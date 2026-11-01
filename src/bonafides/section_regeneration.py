# SPDX-License-Identifier: MIT OR Apache-2.0
"""Frozen, target-only model proposals; publication never edits a saved document."""

from __future__ import annotations

from typing import Annotated, Literal, cast
from uuid import UUID, uuid5

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import Field, ValidationError, model_validator
from sqlalchemy import Connection, text
from sqlalchemy.engine import RowMapping

from bonafides import document_generation as generation
from bonafides import documents, local_model, model_context, suitability
from bonafides.auth import Owner, require_owner
from bonafides.commands import canonical_json, command
from bonafides.config import Settings
from bonafides.db import Database
from bonafides.document_frame import template_digest
from bonafides.problems import Problem
from bonafides.workflows import Claim, assert_fence, enqueue, get_workflow

STAGE = "regenerate_section"
MODE = "section_regeneration"


class ProposalRequest(documents.StrictModel):
    revision_id: Annotated[UUID, Field(strict=False)]
    content_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    section_key: documents.Key
    tone: Literal["professional", "concise"]
    context_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ApplyRequest(documents.StrictModel):
    revision_id: Annotated[UUID, Field(strict=False)]
    content_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    reason: str = Field(min_length=1, max_length=200)


class Candidate(documents.StrictModel):
    """The pair-generation statement shape for one section's replacement blocks."""

    schema_version: Literal["2"]
    stage: Literal["regenerate_section"]
    blocks: list[generation.CandidateStatement] = Field(min_length=1, max_length=30)
    warnings: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(max_length=20)

    @model_validator(mode="after")
    def unique_keys(self) -> Candidate:
        if len({block.block_key for block in self.blocks}) != len(self.blocks):
            raise ValueError("duplicate_section_block_key")
        return self


def _target(conn: Connection, owner: UUID, document: UUID) -> RowMapping:
    row = (
        conn.execute(
            text("""SELECT r.*,d.kind,d.application_id,d.version AS document_version,
            d.current_revision_id FROM bonafides.document_drafts d
            JOIN bonafides.document_revisions r ON r.owner_id=d.owner_id
            AND r.document_draft_id=d.id AND r.id=d.current_revision_id
            WHERE d.owner_id=:owner AND d.id=:id AND d.deleted_at IS NULL FOR UPDATE OF d"""),
            {"owner": owner, "id": document},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise Problem(404, "document_not_found")
    return row


def _source_context(
    conn: Connection, owner: UUID, document: UUID, section_key: str, tone: str
) -> tuple[RowMapping, dict[str, object], dict[str, object]]:
    """Recheck eligibility, but never rebase historical professional/frame pins."""
    row = _target(conn, owner, document)
    if row["scope"] != "tailored":
        raise Problem(409, "section_regeneration_requires_tailored_document")
    revision = generation.revision_view(conn, owner, document, row["id"])
    provenance = cast(dict[str, object], row["provenance_snapshot"])
    if provenance.get("schema_version") != generation.PROTOCOL:
        raise Problem(409, "stale_regeneration_context")
    body = documents.parse_body(revision["body"])
    selected = next((item for item in body.sections if item.key == section_key), None)
    if selected is None:
        raise Problem(404, "document_section_not_found")
    profile = generation._profile(conn, owner, row["profile_revision_id"])
    facts = generation._fact_data(conn, owner, row["profile_revision_id"])
    # The historical parent, not today's expanded profile, is the complete fact boundary.
    if facts != provenance.get("facts"):
        raise Problem(409, "document_source_changed")
    permitted_claims = {str(fact["claim_revision_id"]) for fact in facts}
    permitted_evidence = {str(fact["evidence_revision_id"]) for fact in facts}
    for block in selected.blocks:
        if {str(value) for value in block.claim_revision_ids} - permitted_claims or {
            str(value) for value in block.evidence_revision_ids
        } - permitted_evidence:
            raise Problem(409, "document_source_changed")
    if template_digest(str(row["template_version"])) != provenance.get("template_sha256"):
        raise Problem(409, "stale_regeneration_context")
    if provenance.get("policy_version") != generation.POLICY:
        raise Problem(409, "stale_regeneration_context")
    app = generation._application(conn, owner, row["application_id"], active=True)
    posting = suitability._posting(conn, owner, app["posting_id"])
    requirements = suitability._current_requirements(conn, owner, posting["id"])
    if (
        requirements is None
        or not requirements["confirmed"]
        or str(requirements["id"]) != provenance.get("requirements_revision_id")
        or suitability._posting_digest(posting) != provenance.get("posting_content_sha256")
    ):
        raise Problem(409, "stale_regeneration_posting_context")
    _, reviewed = suitability._reviewed_requirements(conn, owner, posting, requirements["id"])
    pinned_posting = cast(dict[str, object], row["posting_snapshot"])
    if pinned_posting.get("posting_id") != str(posting["id"]) or pinned_posting.get(
        "content_sha256"
    ) != suitability._posting_digest(posting):
        raise Problem(409, "stale_regeneration_posting_context")
    claims, evidence = model_context.professional_context(conn, owner, facts)
    projection: dict[str, object] = {
        "posting": {
            "title": pinned_posting["title"],
            "organization": pinned_posting["company"],
            "source_text": pinned_posting["source_text"],
        },
        "requirements": [
            {
                "requirement_key": item.requirement_key,
                "text": item.text,
                "importance": item.importance,
                "span": item.span.model_dump(mode="json"),
            }
            for item in reviewed
        ],
        "claims": claims,
        "evidence": evidence,
        "tone": tone,
        "language": provenance.get("language", "en"),
        "target": {
            "kind": row["kind"],
            "section_key": selected.key,
            "heading": selected.heading,
            "blocks": [
                block.model_dump(mode="json", exclude={"block_key"}) for block in selected.blocks
            ],
        },
    }
    try:
        projection = local_model.section_projection(projection)
    except local_model.ModelFailure as exc:
        raise Problem(409, "document_context_outside_model_limits") from exc
    binding: dict[str, object] = {
        "document_id": str(document),
        "document_version": row["document_version"],
        "revision_id": str(row["id"]),
        "content_sha256": row["content_sha256"],
        "section_key": section_key,
        "tone": tone,
        "original_section": selected.model_dump(mode="json"),
        "profile_revision_id": str(row["profile_revision_id"]),
        "current_profile_revision_id": str(profile["current_revision_id"]),
        "profile_version": profile["version"],
        "profile_sha256": profile["content_sha256"],
        "application_id": str(app["id"]),
        "application_version": app["version"],
        "requirements_revision_id": str(requirements["id"]),
        "posting_content_sha256": provenance["posting_content_sha256"],
        "template_version": row["template_version"],
        "template_sha256": provenance["template_sha256"],
        "parent_provenance_sha256": generation.sha(provenance),
        "facts": facts,
        "policy_version": generation.POLICY,
        "privacy_policy_revision": provenance.get("privacy_policy_revision", 1),
        "settings_version": suitability._settings_version(conn, owner),
    }
    return row, binding, projection


def plan(
    conn: Connection,
    owner: UUID,
    document: UUID,
    section_key: str,
    tone: str,
    settings: Settings,
) -> dict[str, object]:
    generation.lock_workspace(conn, owner)
    _, binding, projection = _source_context(conn, owner, document, section_key, tone)
    route = generation.route_status(conn, owner, settings, stage=STAGE)
    frozen = {
        **binding,
        "mode": MODE,
        "stage": STAGE,
        "prompt_version": local_model.PROMPT_VERSIONS[STAGE],
        "context": projection,
        "route_identity": route["identity"],
        "generation_route": route,
    }
    return {**frozen, "context_sha256": generation.sha(frozen)}


def check_context(conn: Connection, task: Claim, settings: Settings) -> None:
    generation.lock_workspace(conn, task.owner_id)
    assert_fence(conn, task)
    current = plan(
        conn,
        task.owner_id,
        UUID(str(task.input["document_id"])),
        str(task.input["section_key"]),
        str(task.input["tone"]),
        settings,
    )
    if current["context_sha256"] != task.input.get("context_sha256"):
        raise Problem(409, "regeneration_context_changed")


def _replacement(
    body: documents.DocumentBody, section_key: str, proposed: documents.DocumentSection
) -> documents.DocumentBody:
    return documents.DocumentBody(
        title=body.title,
        sections=[proposed if section.key == section_key else section for section in body.sections],
    )


def validate_candidate(output: dict[str, object]) -> Candidate:
    """Schema-validate a section; claim references are checked per statement, not here."""
    return generation.parse_candidate(output, Candidate)


def publish_proposal(
    conn: Connection,
    task: Claim,
    settings: Settings,
    candidate: Candidate,
    run: UUID,
    metadata: dict[str, object],
) -> dict[str, object]:
    check_context(conn, task, settings)
    frozen = task.input
    identifier = uuid5(task.id, "section-proposal")
    original = documents.DocumentSection.model_validate_json(
        canonical_json(frozen["original_section"]), strict=True
    )
    row = _target(conn, task.owner_id, UUID(str(frozen["document_id"])))
    provenance = cast(dict[str, object], row["provenance_snapshot"])
    try:
        proposed = documents.DocumentSection(
            key=original.key,
            heading=original.heading,
            blocks=[
                generation.candidate_block(statement, provenance).model_copy(
                    update={"block_key": f"proposal-{identifier}-{index}"}
                )
                for index, statement in enumerate(candidate.blocks)
            ],
        )
        body = _replacement(documents.parse_body(row["body"]), original.key, proposed)
    except ValidationError as exc:
        raise Problem(422, "invalid_model_output") from exc
    validation = generation.validation_issues(body, provenance, str(row["kind"]))
    model_run: dict[str, object] = {
        "id": str(run),
        "route": frozen["route_identity"],
        "prompt_version": frozen["prompt_version"],
        "usage": metadata,
        "warnings": candidate.warnings,
    }
    conn.execute(
        text("""INSERT INTO bonafides.document_regeneration_proposals
        (owner_id,id,document_draft_id,target_revision_id,target_content_sha256,
        target_document_version,section_key,original_section,proposed_section,validation_issues,
        model_run_id,workflow_run_id,model_run,context_snapshot)
        VALUES(:owner,:id,:document,:revision,:sha,:version,:section,CAST(:original AS jsonb),
        CAST(:proposed AS jsonb),CAST(:issues AS jsonb),:run,:workflow,CAST(:model AS jsonb),CAST(:context AS jsonb))"""),
        {
            "owner": task.owner_id,
            "id": identifier,
            "document": frozen["document_id"],
            "revision": frozen["revision_id"],
            "sha": frozen["content_sha256"],
            "version": frozen["document_version"],
            "section": original.key,
            "original": canonical_json(original.model_dump(mode="json")),
            "proposed": canonical_json(proposed.model_dump(mode="json")),
            "issues": canonical_json(validation),
            "run": run,
            "workflow": task.id,
            "model": canonical_json(model_run),
            "context": canonical_json(frozen),
        },
    )
    return {
        "document_id": frozen["document_id"],
        "proposal_id": str(identifier),
        "target_revision_id": frozen["revision_id"],
        "section_key": original.key,
        "model_run_id": str(run),
    }


def _proposal(conn: Connection, owner: UUID, document: UUID, proposal: UUID) -> RowMapping:
    row = (
        conn.execute(
            text("""SELECT p.* FROM bonafides.document_regeneration_proposals p
            JOIN bonafides.document_drafts d ON d.owner_id=p.owner_id AND d.id=p.document_draft_id
            WHERE p.owner_id=:owner AND p.document_draft_id=:document AND p.id=:id
            AND d.deleted_at IS NULL"""),
            {"owner": owner, "document": document, "id": proposal},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise Problem(404, "regeneration_proposal_not_found")
    return row


def _same_target(target: RowMapping, proposal: RowMapping) -> bool:
    return bool(
        target["id"] == proposal["target_revision_id"]
        and target["document_version"] == proposal["target_document_version"]
        and target["content_sha256"] == proposal["target_content_sha256"]
    )


def proposal_view(conn: Connection, owner: UUID, row: RowMapping) -> dict[str, object]:
    applicable = False
    if row["historical_model_run_id"] is None:
        try:
            target, _, _ = _source_context(
                conn,
                owner,
                row["document_draft_id"],
                str(row["section_key"]),
                str(row["context_snapshot"]["tone"]),
            )
            applicable = _same_target(target, row)
        except Problem as exc:
            if exc.status not in {404, 409, 422}:
                raise
    return {
        "id": str(row["id"]),
        "document_id": str(row["document_draft_id"]),
        "target_revision_id": str(row["target_revision_id"]),
        "target_content_sha256": row["target_content_sha256"],
        "target_document_version": row["target_document_version"],
        "section_key": row["section_key"],
        "original_section": row["original_section"],
        "proposed_section": row["proposed_section"],
        "validation_issues": row["validation_issues"],
        # The target revision's pinned facts, which publication required to equal the
        # proposal's own, so each proposed statement shows its claim.
        "facts": conn.execute(
            text("""SELECT provenance_snapshot->'facts' FROM bonafides.document_revisions
            WHERE owner_id=:owner AND id=:id"""),
            {"owner": owner, "id": row["target_revision_id"]},
        ).scalar_one(),
        "model_run": row["model_run"],
        "created_at": row["created_at"].isoformat(),
        "applicable": applicable,
        "historical": row["historical_model_run_id"] is not None,
    }


def install_routes(router: APIRouter, db: Database, settings: Settings) -> None:
    @router.get("/documents/{document_id}/regeneration-context")
    def preview(
        document_id: UUID,
        owner: Annotated[Owner, Depends(require_owner)],
        section_key: documents.Key,
        tone: Literal["professional", "concise"] = "professional",
    ) -> Response:
        with db.transaction() as conn:
            frozen = plan(conn, owner.id, document_id, section_key, tone, settings)
            return generation._response(
                200,
                {
                    **{
                        key: frozen[key]
                        for key in (
                            "document_id",
                            "document_version",
                            "revision_id",
                            "content_sha256",
                            "section_key",
                            "tone",
                            "context_sha256",
                            "generation_route",
                        )
                    },
                    "model_projection": frozen["context"],
                },
            )

    @router.post("/documents/{document_id}/regeneration-proposals")
    def request_proposal(
        document_id: UUID,
        body: ProposalRequest,
        request: Request,
        owner: Annotated[Owner, Depends(require_owner)],
    ) -> Response:
        def effect(conn: Connection) -> tuple[int, dict[str, object]]:
            frozen = plan(conn, owner.id, document_id, body.section_key, body.tone, settings)
            if (
                frozen["revision_id"] != str(body.revision_id)
                or frozen["content_sha256"] != body.content_sha256
            ):
                raise Problem(412, "version_conflict")
            if frozen["context_sha256"] != body.context_sha256:
                raise Problem(409, "regeneration_context_changed")
            if cast(dict[str, object], frozen["generation_route"])["status"] != "qualified":
                raise Problem(503, "regeneration_route_unavailable")
            workflow = enqueue(conn, owner.id, "document_generate", frozen)
            return 202, get_workflow(conn, owner.id, workflow)

        with db.transaction() as conn:
            generation.lock_workspace(conn, owner.id)
            _target(conn, owner.id, document_id)
            status, result = command(
                conn,
                owner.id,
                "POST",
                "/documents/{document_id}/regeneration-proposals",
                {"document_id": str(document_id)},
                body.model_dump(mode="json"),
                request.headers.get("if-match"),
                request.headers.get("idempotency-key"),
                "document",
                document_id,
                effect,
            )
        return generation._response(status, result)

    @router.get("/documents/{document_id}/regeneration-proposals")
    def list_proposals(
        document_id: UUID,
        owner: Annotated[Owner, Depends(require_owner)],
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
        before: UUID | None = None,
    ) -> Response:
        with db.transaction() as conn:
            generation.lock_workspace(conn, owner.id)
            _target(conn, owner.id, document_id)
            cursor = _proposal(conn, owner.id, document_id, before) if before else None
            rows = (
                conn.execute(
                    text("""SELECT * FROM bonafides.document_regeneration_proposals
                WHERE owner_id=:owner AND document_draft_id=:document
                AND (CAST(:before AS uuid) IS NULL OR (created_at,id)<(:created,CAST(:before AS uuid)))
                ORDER BY created_at DESC,id DESC LIMIT :limit"""),
                    {
                        "owner": owner.id,
                        "document": document_id,
                        "before": before,
                        "created": cursor["created_at"] if cursor else None,
                        "limit": limit + 1,
                    },
                )
                .mappings()
                .all()
            )
            return generation._response(
                200,
                {
                    "items": [proposal_view(conn, owner.id, row) for row in rows[:limit]],
                    "next_cursor": str(rows[limit - 1]["id"]) if len(rows) > limit else None,
                },
            )

    @router.get("/documents/{document_id}/regeneration-proposals/{proposal_id}")
    def get_proposal(
        document_id: UUID,
        proposal_id: UUID,
        owner: Annotated[Owner, Depends(require_owner)],
    ) -> Response:
        with db.transaction() as conn:
            generation.lock_workspace(conn, owner.id)
            return generation._response(
                200,
                proposal_view(conn, owner.id, _proposal(conn, owner.id, document_id, proposal_id)),
            )

    @router.post("/documents/{document_id}/regeneration-proposals/{proposal_id}/apply")
    def apply_proposal(
        document_id: UUID,
        proposal_id: UUID,
        body: ApplyRequest,
        request: Request,
        owner: Annotated[Owner, Depends(require_owner)],
    ) -> Response:
        def effect(conn: Connection) -> tuple[int, dict[str, object]]:
            proposal = _proposal(conn, owner.id, document_id, proposal_id)
            if proposal["historical_model_run_id"] is not None:
                raise Problem(409, "historical_regeneration_proposal")
            target = _target(conn, owner.id, document_id)
            if (
                not _same_target(target, proposal)
                or str(target["id"]) != str(body.revision_id)
                or target["content_sha256"] != body.content_sha256
            ):
                raise Problem(412, "version_conflict")
            _source_context(
                conn,
                owner.id,
                document_id,
                str(proposal["section_key"]),
                str(proposal["context_snapshot"]["tone"]),
            )
            proposed = documents.DocumentSection.model_validate_json(
                canonical_json(proposal["proposed_section"]), strict=True
            )
            content = _replacement(
                documents.parse_body(target["body"]), str(proposal["section_key"]), proposed
            )
            source = dict(target)
            provenance = dict(cast(dict[str, object], target["provenance_snapshot"]))
            provenance["section_regeneration"] = {
                "proposal_id": str(proposal_id),
                "model_run_id": str(proposal["model_run_id"]),
                "parent_revision_id": str(target["id"]),
                "section_key": proposal["section_key"],
            }
            source["provenance_snapshot"] = provenance
            generation.append_revision(
                conn,
                owner.id,
                document_id,
                content,
                source,
                "manual",
                f"Owner-applied section regeneration proposal: {body.reason}",
            )
            return 200, documents._document(conn, owner.id, document_id, settings)

        with db.transaction() as conn:
            generation.lock_workspace(conn, owner.id)
            _target(conn, owner.id, document_id)
            status, result = command(
                conn,
                owner.id,
                "POST",
                "/documents/{document_id}/regeneration-proposals/{proposal_id}/apply",
                {"document_id": str(document_id), "proposal_id": str(proposal_id)},
                body.model_dump(mode="json"),
                request.headers.get("if-match"),
                request.headers.get("idempotency-key"),
                "document",
                document_id,
                effect,
            )
        return generation._response(status, result)
