# SPDX-License-Identifier: MIT OR Apache-2.0
"""Persistent application conversation with deterministic, model-free saved-context guidance."""

from __future__ import annotations

from typing import Annotated, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, Response
from pydantic import Field, field_validator
from sqlalchemy import Connection, text
from sqlalchemy.engine import RowMapping

from bonafides import document_generation as generation
from bonafides import documents
from bonafides.auth import Owner, require_owner
from bonafides.commands import canonical_json, command
from bonafides.config import Settings
from bonafides.db import Database
from bonafides.problems import Problem


class ConversationRequest(documents.StrictModel):
    message: str = Field(min_length=1, max_length=2000)
    context_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

    @field_validator("message", mode="before")
    @classmethod
    def trim_message(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


def _turn_view(row: RowMapping) -> dict[str, object]:
    return {
        "id": str(row["id"]),
        "application_id": str(row["application_id"]),
        "created_at": str(row["created_at"]),
        "request_text": row["request_text"],
        "reply": row["reply"],
        "workflow_run_id": str(row["workflow_run_id"]) if row["workflow_run_id"] else None,
        "model_run_id": str(row["model_run_id"]) if row["model_run_id"] else None,
        "undone_at": str(row["undone_at"]) if row["undone_at"] else None,
        "historical": row["historical_workflow_run_id"] is not None
        or row["historical_model_run_id"] is not None,
    }


def context_view(
    conn: Connection, owner: UUID, application: UUID, settings: Settings
) -> dict[str, object]:
    generation.lock_workspace(conn, owner)
    app = generation._application(conn, owner, application)
    ids = conn.execute(
        text("""SELECT id FROM bonafides.document_drafts
        WHERE owner_id=:owner AND application_id=:app AND deleted_at IS NULL
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
            }
        )
    pinned = {
        "application_id": str(application),
        "application_version": app["version"],
        "documents": summaries,
        "route_status": generation.route_status(conn, owner, settings, stage="refine_documents"),
    }
    rows = conn.execute(
        text("""SELECT * FROM bonafides.document_conversation_turns
        WHERE owner_id=:owner AND application_id=:app ORDER BY created_at,id"""),
        {"owner": owner, "app": application},
    ).mappings()
    return {
        **pinned,
        "context_sha256": generation.sha(pinned),
        "turns": [_turn_view(row) for row in rows],
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


def request_turn(
    conn: Connection, owner: UUID, application: UUID, body: ConversationRequest, settings: Settings
) -> tuple[int, dict[str, object]]:
    current = context_view(conn, owner, application, settings)
    generation._application(conn, owner, application, active=True)
    if body.context_sha256 != current["context_sha256"]:
        raise Problem(409, "conversation_context_changed")
    identifier = uuid4()
    frozen = {
        "mode": "document_conversation",
        "stage": "refine_documents",
        "application_id": str(application),
        "turn_id": str(identifier),
        "request_text": body.message,
        "documents": current["documents"],
        "route_status": current["route_status"],
        "context_sha256": current["context_sha256"],
    }
    row = (
        conn.execute(
            text("""INSERT INTO bonafides.document_conversation_turns
        (owner_id,id,application_id,request_text,context_snapshot,reply)
        VALUES(:owner,:id,:app,:message,CAST(:context AS jsonb),CAST(:reply AS jsonb)) RETURNING *"""),
            {
                "owner": owner,
                "id": identifier,
                "app": application,
                "message": body.message,
                "context": canonical_json(frozen),
                "reply": canonical_json(_local_reply(current)),
            },
        )
        .mappings()
        .one()
    )
    current["application_version"] = conn.execute(
        text(
            "UPDATE bonafides.applications SET version=version+1 WHERE owner_id=:owner AND id=:app RETURNING version"
        ),
        {"owner": owner, "app": application},
    ).scalar_one()
    current.pop("turns")
    current.pop("context_sha256")
    return 201, {
        "turn": _turn_view(row),
        "application_version": current["application_version"],
        "context_sha256": generation.sha(current),
    }


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
        return generation._response(status, result)
