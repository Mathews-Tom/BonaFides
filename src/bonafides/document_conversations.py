# SPDX-License-Identifier: MIT OR Apache-2.0
"""Application conversations with checked, stable-unit publication and exact undo."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from functools import partial
from typing import Annotated, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, Response
from pydantic import Field, ValidationError, field_validator
from sqlalchemy import Connection, text
from sqlalchemy.engine import RowMapping

from bonafides import document_generation as generation
from bonafides import (
    documents,
    letter_frame,
    local_model,
    model_calls,
    model_projections,
    section_regeneration,
)
from bonafides.auth import Owner, require_owner
from bonafides.commands import canonical_json, command
from bonafides.config import Settings
from bonafides.conversation_protocol import ConversationReply, validate_reply
from bonafides.db import Database
from bonafides.problems import Problem
from bonafides.suitability import _route
from bonafides.workflows import Claim, assert_fence, enqueue, get_workflow

STAGE = "refine_documents"
MODE = "document_conversation"


class UnitSelection(documents.StrictModel):
    document_id: Annotated[UUID, Field(strict=False)]
    section_key: documents.Key
    block_key: documents.Key


class ConversationRequest(documents.StrictModel):
    message: str = Field(min_length=1, max_length=2000)
    document_ids: list[Annotated[UUID, Field(strict=False)]] = Field(
        default_factory=list, max_length=2
    )
    units: list[UnitSelection] = Field(default_factory=list, max_length=256)
    context_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

    @field_validator("message", mode="before")
    @classmethod
    def trim_message(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


def _objects(value: object) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], value)


def _key(value: Mapping[str, object]) -> tuple[str, str, str]:
    return str(value["document_id"]), str(value["section_key"]), str(value["block_key"])


def _targets(
    row: Mapping[str, object] | RowMapping, document: UUID, *, eligible: bool = True
) -> list[dict[str, object]]:
    provenance = cast(dict[str, object], row["provenance_snapshot"])
    publications = {
        str(fact["claim_revision_id"])
        for fact in _objects(provenance.get("facts", []))
        if fact.get("category") == "publication"
    }
    body = documents.parse_body(row["body"])
    blocked = {
        issue["path"] for issue in generation.validation_issues(body, provenance, str(row["kind"]))
    }
    return [
        {
            "document_id": str(document),
            "section_key": section.key,
            "block_key": block.block_key,
            "heading": section.heading,
            "previous_sha256": generation.sha(block.model_dump(mode="json")),
            "before": block.model_dump(mode="json"),
        }
        for si, section in enumerate(body.sections)
        for bi, block in enumerate(section.blocks)
        if not eligible
        or (
            block.text.strip()
            and not publications.intersection(map(str, block.claim_revision_ids))
            and section.key != "profile-publications"
            and not (
                row["kind"] == "cover_letter"
                and block.claim_kind == "greeting"
                and letter_frame.role(block.text, cast(dict[str, object], provenance["frame"]))
            )
            and f"/sections/{si}/blocks/{bi}" not in blocked
        )
    ]


def _source(
    conn: Connection, owner: UUID, document: UUID
) -> tuple[RowMapping, dict[str, object], dict[str, object]]:
    row = section_regeneration._target(conn, owner, document)
    body = documents.parse_body(row["body"])
    tone = str(cast(dict[str, object], row["provenance_snapshot"]).get("tone") or "professional")
    row, binding, projection = section_regeneration._source_context(
        conn, owner, document, body.sections[0].key, tone, refinement=True
    )
    # Document revisions, tone and unrelated annotations are deliberately NOT late fences.
    for name in (
        "document_version",
        "revision_id",
        "content_sha256",
        "section_key",
        "tone",
        "original_section",
        "parent_provenance_sha256",
    ):
        binding.pop(name, None)
    provenance = cast(dict[str, object], row["provenance_snapshot"])
    frame = cast(dict[str, object], provenance["frame"])
    if row["kind"] == "cover_letter":
        # Correspondence is independently owner-editable, not a source-context fence.
        frame = {
            key: value
            for key, value in frame.items()
            if key not in letter_frame.LetterParts.model_fields
        }
        binding["template_version"] = str(row["template_version"]).rsplit("-v", 1)[0]
        binding.pop("template_sha256", None)
    binding["frame_sha256"] = generation.sha(frame)
    return row, binding, projection


def _rows(conn: Connection, owner: UUID, application: UUID) -> list[RowMapping]:
    return list(
        conn.execute(
            text("""SELECT t.*,w.state AS workflow_state
        FROM bonafides.document_conversation_turns t LEFT JOIN bonafides.workflow_runs w
        ON w.owner_id=t.owner_id AND w.id=t.workflow_run_id
        WHERE t.owner_id=:owner AND t.application_id=:app ORDER BY t.created_at,t.id"""),
            {"owner": owner, "app": application},
        ).mappings()
    )


def _active(rows: list[RowMapping]) -> str | None:
    return next(
        (
            str(row["workflow_run_id"])
            for row in reversed(rows)
            if row["workflow_state"]
            in {"pending", "running", "retryable_failure", "waiting_for_approval"}
        ),
        None,
    )


def _turn_view(row: Mapping[str, object] | RowMapping) -> dict[str, object]:
    historical = (
        row["historical_workflow_run_id"] is not None or row["historical_model_run_id"] is not None
    )
    reply = cast(dict[str, object] | None, row["reply"])
    return {
        "id": str(row["id"]),
        "application_id": str(row["application_id"]),
        "model_run_id": str(row["model_run_id"]) if row["model_run_id"] else None,
        "request_text": row["request_text"],
        "reply": reply,
        "workflow_run_id": str(row["workflow_run_id"]) if row["workflow_run_id"] else None,
        "workflow_state": row.get("workflow_state"),
        "created_at": str(row["created_at"]),
        "undone_at": str(row["undone_at"]) if row["undone_at"] else None,
        "historical": historical,
        "can_undo": bool(
            reply
            and (reply.get("applied") or reply.get("tones"))
            and row["workflow_run_id"]
            and row["model_run_id"]
            and not row["undone_at"]
            and not historical
        ),
    }


def _turn(conn: Connection, owner: UUID, application: UUID, turn: UUID) -> RowMapping:
    row = (
        conn.execute(
            text("""SELECT t.*,w.state AS workflow_state
        FROM bonafides.document_conversation_turns t LEFT JOIN bonafides.workflow_runs w
        ON w.owner_id=t.owner_id AND w.id=t.workflow_run_id
        WHERE t.owner_id=:owner AND t.application_id=:app AND t.id=:id FOR UPDATE OF t"""),
            {"owner": owner, "app": application, "id": turn},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise Problem(404, "conversation_turn_not_found")
    return row


def context_view(
    conn: Connection, owner: UUID, application: UUID, settings: Settings
) -> dict[str, object]:
    generation.lock_workspace(conn, owner)
    app = generation._application(conn, owner, application)
    ids = conn.execute(
        text("""SELECT id FROM bonafides.document_drafts
        WHERE owner_id=:owner AND application_id=:app AND deleted_at IS NULL
        AND kind IN ('resume','cover_letter')
        AND current_revision_id IS NOT NULL ORDER BY kind,id"""),
        {"owner": owner, "app": application},
    ).scalars()
    summaries: list[dict[str, object]] = []
    for identifier in ids:
        saved = documents._document(conn, owner, identifier, settings)
        revision = cast(dict[str, object], saved["current_revision"])
        summaries.append(
            {
                "document_id": saved["id"],
                "kind": saved["kind"],
                "version": saved["version"],
                "revision_id": revision["id"],
                "revision_no": revision["revision_no"],
                "blocking_unsupported": bool(saved["unsupported_claims"]),
                "reviewed": saved["reviewed"],
                "stale": saved["stale"],
                "units": [
                    {
                        **{key: value for key, value in unit.items() if key != "before"},
                        "text": cast(dict[str, object], unit["before"])["text"],
                    }
                    for unit in _targets(
                        section_regeneration._target(conn, owner, identifier), identifier
                    )
                ],
            }
        )
    pinned = {
        "application_id": str(application),
        "application_version": app["version"],
        "documents": summaries,
        "route_status": generation.route_status(conn, owner, settings, stage="refine_documents"),
    }
    rows = _rows(conn, owner, application)
    return {
        **pinned,
        "context_sha256": generation.sha(pinned),
        "turns": [_turn_view(row) for row in rows],
        "active_workflow_id": _active(rows),
    }


def _local_reply(current: dict[str, object]) -> dict[str, object]:
    saved = cast(list[dict[str, object]], current["documents"])
    descriptions = [
        f"{'Résumé' if item['kind'] == 'resume' else 'Cover letter'}: "
        f"saved revision {item['revision_no']}, version {item['version']}; "
        f"{'blocking unsupported statements' if item['blocking_unsupported'] else 'no blocking unsupported statements'}; "
        f"{'reviewed' if item['reviewed'] else 'review needed'}"
        f"{' (saved context is stale)' if item['stale'] else ''}."
        for item in saved
    ]
    route = cast(dict[str, object], current["route_status"])
    text_reply = " ".join(
        [
            "Saved-context guidance only; I have not interpreted your request or changed any document.",
            *(
                descriptions
                if descriptions
                else ["No saved résumé or cover letter exists for this application yet."]
            ),
            f"The refine_documents route is {'qualified' if route['status'] == 'qualified' else 'not qualified'}.",
            "This conversation does not run that route. No model was called.",
            "To change a saved document now, open its editor and save your changes, or use section regeneration "
            "when its separate route is qualified. Compare revisions and refresh the latest saved version before "
            "replacing local editor text. Review unsupported statements before export.",
        ]
    )
    return {
        "text": text_reply,
        "declined": False,
        "applied": [],
        "skipped": [],
        "tones": [],
        "revisions": [],
    }


def _bump(conn: Connection, owner: UUID, application: UUID) -> None:
    conn.execute(
        text(
            "UPDATE bonafides.applications SET version=version+1 WHERE owner_id=:owner AND id=:app"
        ),
        {"owner": owner, "app": application},
    )


def request_turn(
    conn: Connection, owner: UUID, application: UUID, body: ConversationRequest, settings: Settings
) -> tuple[int, dict[str, object]]:
    if any(
        unit.section_key in letter_frame.PROTECTED_BODY_KEYS
        or unit.block_key in letter_frame.PROTECTED_BODY_KEYS
        for unit in body.units
    ):
        raise Problem(
            422,
            "letter_frame_not_regenerable",
            "Edit sender, date or signature manually; they are not sent to the model.",
        )
    current = context_view(conn, owner, application, settings)
    generation._application(conn, owner, application, active=True)
    if current["active_workflow_id"]:
        raise Problem(409, "conversation_in_progress")
    if body.context_sha256 != current["context_sha256"]:
        raise Problem(409, "conversation_context_changed")
    if len(set(body.document_ids)) != len(body.document_ids):
        raise Problem(422, "duplicate_conversation_document")
    identifier = uuid4()
    frozen: dict[str, object] = {
        "mode": MODE,
        "stage": STAGE,
        "application_id": str(application),
        "turn_id": str(identifier),
        "request_text": body.message,
        "documents": [
            {key: value for key, value in item.items() if key != "units"}
            for item in _objects(current["documents"])
        ],
        "route_status": current["route_status"],
        "context_sha256": current["context_sha256"],
    }
    reply: dict[str, object] | None = None
    workflow: UUID | None = None
    _bump(conn, owner, application)
    if not body.document_ids:
        if body.units:
            raise Problem(422, "conversation_target_not_selected")
        reply = _local_reply(current)
    else:
        route = cast(dict[str, object], current["route_status"])
        if route["status"] != "qualified":
            raise Problem(503, "conversation_route_unavailable")
        available = {item["document_id"] for item in _objects(current["documents"])}
        if any(str(document) not in available for document in body.document_ids):
            raise Problem(422, "conversation_document_not_owned")
        snapshots: list[dict[str, object]] = []
        targets: list[dict[str, object]] = []
        contexts: list[dict[str, object]] = []
        selected = {_key(unit.model_dump(mode="json")) for unit in body.units}
        for document in body.document_ids:
            row, binding, projection = _source(conn, owner, document)
            if row["application_id"] != application:
                raise Problem(422, "conversation_document_not_owned")
            snapshots.append(
                {
                    **binding,
                    "revision_id": str(row["id"]),
                    "body": row["body"],
                    "kind": row["kind"],
                    "provenance_snapshot": row["provenance_snapshot"],
                }
            )
            targets.extend(
                item for item in _targets(row, document) if not selected or _key(item) in selected
            )
            contexts.append({**projection, "document_id": str(document)})
        if selected - {_key(item) for item in targets}:
            raise Problem(409, "conversation_target_missing")
        if not targets or len(targets) > 256:
            raise Problem(422, "conversation_target_limit")
        turns = [
            {
                "request_text": row["request_text"],
                "reply_text": cast(dict[str, object], row["reply"])["text"],
            }
            for row in reversed(_rows(conn, owner, application))
            if row["reply"] is not None and (row["model_run_id"] or row["historical_model_run_id"])
        ][:9]
        client = local_model.OllamaClient(_route(conn, owner, settings, STAGE))
        if client.route.identity() != route["identity"]:
            raise Problem(409, "conversation_context_changed")
        while True:
            try:
                projection = model_projections.refinement_projection(
                    contexts, body.message, list(reversed(turns)), targets
                )
                # Reserve all twelve permitted ASCII-only 120-character correction issues.
                client.prepare(
                    STAGE,
                    projection,
                    local_model.PROMPT_VERSIONS[STAGE],
                    correction=("x" * 120,) * 12,
                )
                break
            except local_model.ModelFailure as exc:
                if not turns or exc.code not in {"model_context_limit", "model_request_limit"}:
                    raise Problem(409, "document_context_outside_model_limits") from exc
                turns.pop()
        frozen.update(
            {
                "documents": snapshots,
                "targets": targets,
                "context": projection,
                "route_identity": route["identity"],
                "prompt_version": local_model.PROMPT_VERSIONS[STAGE],
            }
        )
        workflow = enqueue(conn, owner, "document_generate", frozen)
    conn.execute(
        text("""INSERT INTO bonafides.document_conversation_turns
        (owner_id,id,application_id,request_text,context_snapshot,reply,workflow_run_id)
        VALUES(:owner,:id,:app,:request,CAST(:context AS jsonb),CAST(:reply AS jsonb),:workflow)"""),
        {
            "owner": owner,
            "id": identifier,
            "app": application,
            "request": body.message,
            "context": canonical_json(frozen),
            "reply": canonical_json(reply) if reply else None,
            "workflow": workflow,
        },
    )
    refreshed = context_view(conn, owner, application, settings)
    return (202 if workflow else 201), {
        "application_version": refreshed["application_version"],
        "context_sha256": refreshed["context_sha256"],
        "turn": _turn_view(_turn(conn, owner, application, identifier)),
        "workflow": get_workflow(conn, owner, workflow) if workflow else None,
    }


def check_context(conn: Connection, task: Claim, settings: Settings) -> None:
    generation.lock_workspace(conn, task.owner_id)
    assert_fence(conn, task)
    row = _turn(
        conn,
        task.owner_id,
        UUID(str(task.input["application_id"])),
        UUID(str(task.input["turn_id"])),
    )
    if row["workflow_run_id"] != task.id or row["reply"] is not None:
        raise Problem(409, "conversation_request_changed")
    application = UUID(str(task.input["application_id"]))
    app = generation._application(conn, task.owner_id, application, active=True)
    active = _active(_rows(conn, task.owner_id, application))
    if active is not None and active != str(task.id):
        raise Problem(409, "conversation_in_progress")
    for frozen in _objects(task.input["documents"]):
        if app["version"] != frozen["application_version"]:
            raise Problem(409, "conversation_source_changed")
        try:
            _, current, _ = _source(conn, task.owner_id, UUID(str(frozen["document_id"])))
        except Problem as exc:
            if exc.code == "document_not_found":
                continue
            raise
        if any(current[name] != frozen.get(name) for name in current):
            raise Problem(409, "conversation_source_changed")
    route = generation.route_status(conn, task.owner_id, settings, stage=STAGE)
    if route["status"] != "qualified" or route["identity"] != task.input["route_identity"]:
        raise Problem(409, "conversation_route_changed")


def _replace(
    body: documents.DocumentBody, replacements: dict[tuple[str, str], documents.DocumentBlock]
) -> documents.DocumentBody:
    return documents.DocumentBody(
        title=body.title,
        sections=[
            section.model_copy(
                update={
                    "blocks": [
                        replacements.get((section.key, block.block_key), block)
                        for block in section.blocks
                    ]
                }
            )
            for section in body.sections
        ],
    )


def _checked(
    body: documents.DocumentBody,
    provenance: dict[str, object],
    kind: str,
    replacements: dict[tuple[str, str], documents.DocumentBlock],
) -> documents.DocumentBody:
    try:
        proposed = _replace(body, replacements)
    except ValidationError as exc:
        raise generation._mismatch(exc) from exc
    paths = {
        f"/sections/{si}/blocks/{bi}"
        for si, section in enumerate(proposed.sections)
        for bi, block in enumerate(section.blocks)
        if (section.key, block.block_key) in replacements
    }
    if any(
        issue["path"] in paths for issue in generation.validation_issues(proposed, provenance, kind)
    ):
        raise model_calls.OutputMismatch(["edits: unsupported_document_statement"])
    return proposed


def candidate_validator(
    conn: Connection, task: Claim
) -> Callable[[dict[str, object]], ConversationReply]:
    return partial(validate_reply, frozen=task.input)


def publish_reply(
    conn: Connection, task: Claim, output: ConversationReply, model_run_id: UUID, settings: Settings
) -> dict[str, object]:
    check_context(conn, task, settings)
    candidate = validate_reply(output.model_dump(mode="json"), task.input)
    targets = {_key(item): item for item in _objects(task.input["targets"])}
    frozen_documents = {
        str(item["document_id"]): item for item in _objects(task.input["documents"])
    }
    reply: dict[str, object] = {
        "text": candidate.reply_text,
        "declined": candidate.declined,
        "applied": [],
        "skipped": [],
        "tones": [],
        "revisions": [],
    }
    accepted: dict[str, dict[tuple[str, str], documents.DocumentBlock]] = {}
    rows = {}
    for document in frozen_documents:
        try:
            rows[document] = section_regeneration._target(conn, task.owner_id, UUID(document))
        except Problem as exc:
            if exc.code != "document_not_found":
                raise
    for edit in candidate.edits:
        key = (str(edit.document_id), edit.section_key, edit.block_key)
        if key[0] not in frozen_documents:
            continue
        target = targets.get(key)
        reason = "unknown_target"
        if target is not None:
            current = (
                {_key(item): item for item in _targets(rows[key[0]], edit.document_id)}.get(key)
                if key[0] in rows
                else None
            )
            reason = "target_changed"
            if (
                current is not None
                and current["previous_sha256"] == target["previous_sha256"]
                and current["heading"] == target["heading"]
            ):
                if edit.replacement.model_dump(mode="json") == target["before"]:
                    continue
                accepted.setdefault(key[0], {})[(key[1], key[2])] = edit.replacement
                _objects(reply["applied"]).append(
                    {
                        "document_id": key[0],
                        "section_key": key[1],
                        "block_key": key[2],
                        "heading": target["heading"],
                        "before": target["before"],
                        "after": edit.replacement.model_dump(mode="json"),
                    }
                )
                continue
        _objects(reply["skipped"]).append(
            {"document_id": key[0], "section_key": key[1], "block_key": key[2], "reason": reason}
        )
    prepared = []
    for document, row in rows.items():
        provenance = dict(cast(dict[str, object], row["provenance_snapshot"]))
        replacements = accepted.get(document, {})
        body = _checked(
            documents.parse_body(row["body"]), provenance, str(row["kind"]), replacements
        )
        if (
            candidate.tone is not None
            and provenance.get("tone") != candidate.tone
            and any(key[0] == document for key in targets)
        ):
            # A concurrent tone change is unrelated owner work, not model authority.
            frozen = frozen_documents[document]
            before = cast(dict[str, object], frozen["provenance_snapshot"]).get("tone")
            if provenance.get("tone") == before:
                change: dict[str, object] = {
                    "document_id": document,
                    "before": before,
                    "after": candidate.tone,
                }
                if row["kind"] == "cover_letter":
                    frame = cast(dict[str, object], provenance["frame"])
                    frozen_provenance = cast(dict[str, object], frozen["provenance_snapshot"])
                    previous_tone = str(before or "professional")
                    current_parts = letter_frame.parts(frame, previous_tone, body)
                    original_parts = letter_frame.parts(
                        cast(dict[str, object], frozen_provenance["frame"]),
                        previous_tone,
                        documents.parse_body(frozen["body"]),
                    )
                    if (
                        not letter_frame.is_custom(provenance, body)
                        and current_parts.closing == original_parts.closing
                    ):
                        current_parts = current_parts.model_copy(
                            update={"closing": letter_frame.closing_for(candidate.tone)}
                        )
                    change["letter_before"] = letter_frame.state(
                        documents.parse_body(row["body"]), dict(row)
                    )
                    body = letter_frame.split(body, frame)
                    pinned = letter_frame.pin(dict(row), current_parts)
                    provenance.update(cast(dict[str, object], pinned["provenance_snapshot"]))
                _objects(reply["tones"]).append(change)
                provenance["tone"] = candidate.tone
        source = {
            **row,
            "template_version": provenance["template_version"],
            "provenance_snapshot": provenance,
            "model_run_id": model_run_id,
        }
        for change in _objects(reply["tones"]):
            if change["document_id"] == document and "letter_before" in change:
                change["letter_after"] = letter_frame.state(body, source)
        if replacements or provenance != row["provenance_snapshot"]:
            prepared.append((document, row, source, body))
    for document, row, source, body in prepared:
        revision = generation.append_revision(
            conn,
            task.owner_id,
            UUID(document),
            body,
            source,
            "generated",
            "Conversational refinement",
        )
        _objects(reply["revisions"]).append(
            {
                "document_id": document,
                "before_revision_id": str(row["id"]),
                "after_revision_id": str(revision),
            }
        )
        for change in _objects(reply["tones"]):
            if change["document_id"] == document and "letter_before" in change:
                published = section_regeneration._target(conn, task.owner_id, UUID(document))
                change["after_sha256"] = published["content_sha256"]
    assert_fence(conn, task)
    conn.execute(
        text("""UPDATE bonafides.document_conversation_turns SET reply=CAST(:reply AS jsonb),model_run_id=:run
        WHERE owner_id=:owner AND id=:id AND workflow_run_id=:workflow AND reply IS NULL"""),
        {
            "reply": canonical_json(reply),
            "run": model_run_id,
            "owner": task.owner_id,
            "id": UUID(str(task.input["turn_id"])),
            "workflow": task.id,
        },
    )
    assert_fence(conn, task)
    return {"turn_id": task.input["turn_id"], "reply": reply}


def undo_turn(
    conn: Connection, owner: UUID, application: UUID, identifier: UUID
) -> dict[str, object]:
    generation.lock_workspace(conn, owner)
    generation._application(conn, owner, application, active=True)
    turn = _turn(conn, owner, application, identifier)
    if turn["undone_at"] is not None:
        raise Problem(409, "conversation_already_undone")
    if not _turn_view(turn)["can_undo"]:
        raise Problem(409, "conversation_not_undoable")
    reply = cast(dict[str, object], turn["reply"])
    documents_changed = {str(item["document_id"]) for item in _objects(reply["applied"])}
    documents_changed.update(str(item["document_id"]) for item in _objects(reply["tones"]))
    letters = {
        str(item["document_id"]): item
        for item in _objects(reply["tones"])
        if "letter_before" in item
    }
    pairs = {str(item["document_id"]): item for item in _objects(reply["revisions"])}
    prepared = []
    for document in sorted(documents_changed):
        try:
            row = section_regeneration._target(conn, owner, UUID(document))
        except Problem as exc:
            raise Problem(409, "conversation_undo_conflict") from exc
        if document in letters:
            if (
                row["content_sha256"] != letters[document]["after_sha256"]
                or cast(dict[str, object], row["provenance_snapshot"]).get("tone")
                != letters[document]["after"]
                or letter_frame.state(documents.parse_body(row["body"]), dict(row))
                != letters[document]["letter_after"]
            ):
                raise Problem(409, "conversation_undo_conflict")
            before_row = generation.revision_view(
                conn, owner, UUID(document), UUID(str(pairs[document]["before_revision_id"]))
            )
            source = {
                **row,
                "template_version": before_row["template_version"],
                "provenance_snapshot": before_row["provenance_snapshot"],
            }
            prepared.append((document, documents.parse_body(before_row["body"]), source))
            continue
        body = documents.parse_body(row["body"])
        units = {_key(item): item for item in _targets(row, UUID(document), eligible=False)}
        replacements = {}
        for item in _objects(reply["applied"]):
            if item["document_id"] != document:
                continue
            current = units.get(_key(item))
            if (
                current is None
                or current["before"] != item["after"]
                or current["heading"] != item["heading"]
            ):
                raise Problem(409, "conversation_undo_conflict")
            replacements[(str(item["section_key"]), str(item["block_key"]))] = (
                documents.DocumentBlock.model_validate_json(
                    canonical_json(item["before"]), strict=True
                )
            )
        provenance = dict(cast(dict[str, object], row["provenance_snapshot"]))
        for tone in _objects(reply["tones"]):
            if tone["document_id"] == document:
                if provenance.get("tone") != tone["after"]:
                    raise Problem(409, "conversation_undo_conflict")
                provenance["tone"] = tone["before"]
        source = {**row, "provenance_snapshot": provenance}
        try:
            prepared.append((document, _replace(body, replacements), source))
        except ValidationError as exc:
            raise Problem(409, "conversation_undo_conflict") from exc
    # Validate every affected unit/tone before appending even the first revision.
    for document, body, source in prepared:
        generation.append_revision(
            conn, owner, UUID(document), body, source, "restore", "Undo conversational refinement"
        )
    conn.execute(
        text("""UPDATE bonafides.document_conversation_turns SET undone_at=clock_timestamp()
        WHERE owner_id=:owner AND id=:id AND undone_at IS NULL"""),
        {"owner": owner, "id": identifier},
    )
    _bump(conn, owner, application)
    return {"turn": _turn_view(_turn(conn, owner, application, identifier))}


def install_routes(router: APIRouter, db: Database, settings: Settings) -> None:
    @router.get("/applications/{application_id}/conversation")
    def view(application_id: UUID, owner: Annotated[Owner, Depends(require_owner)]) -> Response:
        with db.transaction() as conn:
            return generation._response(200, context_view(conn, owner.id, application_id, settings))

    @router.post("/applications/{application_id}/conversation")
    def create(
        application_id: UUID,
        body: ConversationRequest,
        request: Request,
        owner: Annotated[Owner, Depends(require_owner)],
    ) -> Response:
        with db.transaction() as conn:
            status, result = command(
                conn,
                owner.id,
                "POST",
                "/applications/{application_id}/conversation",
                {"application_id": str(application_id)},
                body.model_dump(mode="json"),
                request.headers.get("if-match"),
                request.headers.get("idempotency-key"),
                "application",
                application_id,
                lambda connection: request_turn(
                    connection, owner.id, application_id, body, settings
                ),
            )
        headers = {"Cache-Control": "private, no-store"}
        if status == 202:
            headers["Location"] = (
                f"/api/v1/workflows/{cast(dict[str, object], result['workflow'])['id']}"
            )
        return Response(
            canonical_json(result),
            status_code=status,
            media_type="application/json",
            headers=headers,
        )

    @router.post("/applications/{application_id}/conversation/turns/{turn_id}/undo")
    def undo(
        application_id: UUID,
        turn_id: UUID,
        body: documents.StrictModel,
        request: Request,
        owner: Annotated[Owner, Depends(require_owner)],
    ) -> Response:
        with db.transaction() as conn:
            status, result = command(
                conn,
                owner.id,
                "POST",
                "/applications/{application_id}/conversation/turns/{turn_id}/undo",
                {"application_id": str(application_id), "turn_id": str(turn_id)},
                body.model_dump(mode="json"),
                request.headers.get("if-match"),
                request.headers.get("idempotency-key"),
                "application",
                application_id,
                lambda connection: (200, undo_turn(connection, owner.id, application_id, turn_id)),
            )
        return generation._response(status, result)
