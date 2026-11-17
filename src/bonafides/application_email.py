# SPDX-License-Identifier: MIT OR Apache-2.0
"""Checked application emails, published through the existing fenced document task."""

from __future__ import annotations

from typing import Annotated, cast
from uuid import UUID, uuid5

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import Connection, text
from sqlalchemy.engine import RowMapping

from bonafides import document_generation as generation
from bonafides import documents, local_model, model_projections
from bonafides.auth import Owner, require_owner
from bonafides.commands import command
from bonafides.config import Settings
from bonafides.db import Database
from bonafides.languages import LABELS
from bonafides.problems import Problem
from bonafides.workflows import Claim, assert_fence, enqueue, get_workflow

STAGE = "write_application_email"
KIND = "application_email"


def target(conn: Connection, owner: UUID, application: UUID) -> RowMapping | None:
    return (
        conn.execute(
            text("""SELECT d.id,d.version,d.deleted_at,d.current_revision_id,r.provenance_snapshot
        FROM bonafides.document_drafts d LEFT JOIN bonafides.document_revisions r
        ON r.owner_id=d.owner_id AND r.id=d.current_revision_id
        WHERE d.owner_id=:owner AND d.application_id=:application AND d.kind='application_email'
        FOR UPDATE OF d"""),
            {"owner": owner, "application": application},
        )
        .mappings()
        .one_or_none()
    )


def plan(conn: Connection, owner: UUID, application: UUID, settings: Settings) -> dict[str, object]:
    generation.lock_workspace(conn, owner)
    generation._application(conn, owner, application, active=True)
    letter = (
        conn.execute(
            text("""SELECT r.* FROM bonafides.document_drafts d JOIN bonafides.document_revisions r
        ON r.owner_id=d.owner_id AND r.id=d.current_revision_id
        WHERE d.owner_id=:owner AND d.application_id=:application AND d.kind='cover_letter'
        AND d.deleted_at IS NULL FOR UPDATE OF d"""),
            {"owner": owner, "application": application},
        )
        .mappings()
        .one_or_none()
    )
    if letter is None:
        raise Problem(409, "application_email_requires_letter")
    provenance = cast(dict[str, object], letter["provenance_snapshot"])
    language = generation.provenance_language(provenance)
    frozen = generation.generation_plan(
        conn,
        owner,
        application,
        letter["profile_revision_id"],
        str(letter["template_version"]),
        str(provenance.get("tone", "professional")),
        language,
        settings,
        active=True,
    )
    context = cast(dict[str, object], frozen["context"])
    posting = cast(dict[str, object], frozen["posting_snapshot"])
    frozen["context"] = model_projections.application_email_projection(
        {
            "title": posting["title"],
            "company": posting["company"],
            "description_text": posting["source_text"],
        },
        cast(list[dict[str, object]], context["requirements"]),
        cast(list[dict[str, object]], context["claims"]),
        cast(list[dict[str, object]], context["evidence"]),
        str(frozen["tone"]),
        str(frozen["language"]),
        documents.parse_body(letter["body"]),
        frame=cast(dict[str, object], provenance.get("frame", {})),
    )
    route = generation.route_status(conn, owner, settings, stage=STAGE)
    frozen.update(
        mode=KIND,
        stage=STAGE,
        prompt_version=local_model.PROMPT_VERSIONS[STAGE],
        route_identity=route["identity"],
        generation_route=route,
    )
    del frozen["context_sha256"]
    frozen["context_sha256"] = generation.sha(frozen)
    return frozen


def check_context(conn: Connection, task: Claim, settings: Settings) -> None:
    generation.lock_workspace(conn, task.owner_id)
    assert_fence(conn, task)
    current = plan(conn, task.owner_id, UUID(str(task.input["application_id"])), settings)
    generation._retain_calendar_fields(current, task.input)
    del current["context_sha256"]
    if generation.sha(current) != task.input["context_sha256"]:
        raise Problem(409, "stale_document_context")


def publish_email(
    conn: Connection,
    task: Claim,
    settings: Settings,
    body: documents.DocumentBody,
    run: UUID,
    metadata: dict[str, object],
) -> dict[str, object]:
    check_context(conn, task, settings)
    application = UUID(str(task.input["application_id"]))
    current = target(conn, task.owner_id, application)
    if current is not None and current["deleted_at"] is not None:
        raise Problem(409, "document_deleted")
    draft = current["id"] if current else uuid5(task.id, KIND)
    if current is None:
        conn.execute(
            text("""INSERT INTO bonafides.document_drafts(owner_id,id,kind,scope,profile_revision_id,application_id)
            VALUES(:owner,:id,'application_email','tailored',:profile,:application)"""),
            {
                "owner": task.owner_id,
                "id": draft,
                "profile": task.input["profile_revision_id"],
                "application": application,
            },
        )
    provenance = generation._provenance(task.input, task.id, run, metadata)
    old = cast(dict[str, object], current["provenance_snapshot"]) if current else {}
    parts = cast(dict[str, object], old.get("email", {}))
    provenance["email"] = generation.EmailParts(
        recipient=str(parts.get("recipient", "")),
        subject=(
            f"{LABELS[generation.provenance_language(task.input)]['Application']}: "
            f"{cast(dict[str, object], task.input['posting_snapshot'])['title']}"
        ),
    ).model_dump()
    previous = str(current["current_revision_id"]) if current else None
    provenance["email_restore_revision_id"] = previous
    source = {
        "kind": KIND,
        "scope": "tailored",
        "profile_revision_id": task.input["profile_revision_id"],
        "template_version": task.input["template_version"],
        "provenance_snapshot": provenance,
        "posting_snapshot": task.input["posting_snapshot"],
        "model_run_id": run,
    }
    revision = generation.append_revision(
        conn,
        task.owner_id,
        draft,
        body.model_copy(update={"title": "Application email"}),
        source,
        "generated",
        "Owner-requested application email",
        revision_id=uuid5(task.id, "email-revision"),
    )
    return {
        "document_id": str(draft),
        "revision_id": str(revision),
        "restore_revision_id": previous,
    }


def install_routes(router: APIRouter, db: Database, settings: Settings) -> None:
    @router.get("/applications/{application_id}/email")
    def read_email(
        application_id: UUID, owner: Annotated[Owner, Depends(require_owner)]
    ) -> Response:
        with db.transaction() as conn:
            generation.lock_workspace(conn, owner.id)
            generation._application(conn, owner.id, application_id)
            current = target(conn, owner.id, application_id)
            if current is None:
                raise Problem(404, "application_email_not_found")
            return generation._response(
                200, documents._document(conn, owner.id, current["id"], settings)
            )

    def queue(conn: Connection, owner: UUID, application: UUID) -> tuple[int, dict[str, object]]:
        generation.lock_workspace(conn, owner)
        generation._application(conn, owner, application, active=True)
        active = conn.execute(
            text("""SELECT id FROM bonafides.workflow_runs WHERE owner_id=:owner AND kind='document_generate'
            AND input->>'mode'='application_email' AND input->>'application_id'=:application
            AND state IN ('pending','running','retryable_failure') AND cancel_requested_at IS NULL
            ORDER BY created_at LIMIT 1"""),
            {"owner": owner, "application": str(application)},
        ).scalar_one_or_none()
        if active is not None:
            return 202, get_workflow(conn, owner, active)
        frozen = plan(conn, owner, application, settings)
        if cast(dict[str, object], frozen["generation_route"])["status"] != "qualified":
            raise Problem(503, "generation_route_unavailable")
        return 202, get_workflow(conn, owner, enqueue(conn, owner, "document_generate", frozen))

    @router.post("/applications/{application_id}/email")
    def generate(
        application_id: UUID, request: Request, owner: Annotated[Owner, Depends(require_owner)]
    ) -> Response:
        def effect(conn: Connection) -> tuple[int, dict[str, object]]:
            if target(conn, owner.id, application_id) is not None:
                raise Problem(409, "application_email_exists")
            return queue(conn, owner.id, application_id)

        with db.transaction() as conn:
            status, result = command(
                conn,
                owner.id,
                "POST",
                "/applications/{application_id}/email",
                {"application_id": str(application_id)},
                {},
                request.headers.get("if-match"),
                request.headers.get("idempotency-key"),
                "application",
                application_id,
                effect,
            )
        return generation._response(status, result)

    @router.post("/documents/{document_id}/rewrite")
    def rewrite(
        document_id: UUID, request: Request, owner: Annotated[Owner, Depends(require_owner)]
    ) -> Response:
        def effect(conn: Connection) -> tuple[int, dict[str, object]]:
            record = documents._document(conn, owner.id, document_id, settings)
            if record["kind"] != KIND:
                raise Problem(422, "rewrite_requires_application_email")
            return queue(conn, owner.id, UUID(str(record["application_id"])))

        with db.transaction() as conn:
            status, result = command(
                conn,
                owner.id,
                "POST",
                "/documents/{document_id}/rewrite",
                {"document_id": str(document_id)},
                {},
                request.headers.get("if-match"),
                request.headers.get("idempotency-key"),
                "document",
                document_id,
                effect,
            )
        return generation._response(status, result)
