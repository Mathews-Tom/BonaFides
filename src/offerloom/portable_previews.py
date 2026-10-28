# SPDX-License-Identifier: MIT OR Apache-2.0
"""Owner-reviewed destructive plans; uploaded bytes never become client authority."""

from __future__ import annotations

import hashlib
from typing import Annotated, Literal, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Connection, text
from starlette.concurrency import run_in_threadpool

from offerloom.auth import Owner, require_owner
from offerloom.backup_archive import BackupError
from offerloom.backup_configuration import (
    VerifiedCheckpoint,
    recheck_verified_checkpoint,
    verified_checkpoint,
)
from offerloom.backup_generations import credential_keyring_sha256
from offerloom.backup_operator import source_sha256
from offerloom.commands import canonical_json, command, lock_installation
from offerloom.config import ConfigurationError, Settings
from offerloom.db import Database
from offerloom.portable_archive import MAX_ARCHIVE_BYTES, ArchiveError, decode_archive
from offerloom.portable_data import quarantine_records
from offerloom.portable_uploads import (
    UPLOAD_CHUNK_BYTES,
    CandidateUpload,
    retain_candidate,
    temporary_archive,
)
from offerloom.problems import Problem
from offerloom.workflows import cancel_workflow, enqueue, get_workflow

PREVIEW_TTL_SECONDS = 600


class DeletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    scope: Literal["application", "workspace"]
    application_id: Annotated[UUID, Field(strict=False)] | None = None
    application_version: int | None = Field(default=None, ge=1, le=9223372036854775807)

    @model_validator(mode="after")
    def exact_scope(self) -> DeletionRequest:
        if self.scope == "application":
            if self.application_id is None or self.application_version is None:
                raise ValueError("application identity and version required")
        elif self.application_id is not None or self.application_version is not None:
            raise ValueError("workspace scope cannot contain an application")
        return self


class CancelPreview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def _sha(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _bindings(
    conn: Connection, owner: UUID, session: UUID, settings: Settings
) -> dict[str, object]:
    row = (
        conn.execute(
            text("""SELECT i.namespace_id,i.maintenance_epoch,i.auth_epoch AS installation_auth_epoch,
        o.auth_epoch AS owner_auth_epoch,s.workspace_version,
        (SELECT jsonb_object_agg(version::text,source_sha256) FROM offerloom.schema_migrations)
          AS schema_versions
        FROM offerloom.installation_state i CROSS JOIN offerloom.owners o
        JOIN offerloom.owner_settings s ON s.owner_id=o.id WHERE i.singleton AND o.id=:owner"""),
            {"owner": owner},
        )
        .mappings()
        .one()
    )
    return {
        **dict(row),
        "namespace_id": str(row["namespace_id"]),
        "owner_id": str(owner),
        "session_id": str(session),
        "source_sha256": source_sha256(),
        "keyring_sha256": credential_keyring_sha256(
            settings.keyring.active_key_id,
            {
                key: hashlib.sha256(value).hexdigest()
                for key, value in settings.keyring.keys.items()
            },
        ),
    }


def confirmation_phrase(plan: dict[str, object]) -> str:
    if plan["action_kind"] == "replace_workspace":
        return "REPLACE ALL WORKSPACE DATA"
    payload = cast(dict[str, object], plan["payload"])
    if payload["categories"] == ["applications"]:
        return "DELETE APPLICATION " + str(payload["application_id"])
    return "DELETE ALL WORKSPACE DATA"


def approval_preview(
    conn: Connection, owner_id: UUID, workflow_id: UUID, preview_sha256: object
) -> dict[str, object] | None:
    """Legacy review-only intents remain unavailable; real producer plans fail closed."""
    row = (
        conn.execute(
            text("""SELECT w.input,p.id,p.plan,p.preview_sha256,p.cleared_at,
        p.expires_at>clock_timestamp() AS unexpired
        FROM offerloom.workflow_runs w LEFT JOIN offerloom.portable_previews p
          ON p.owner_id=w.owner_id AND p.workflow_run_id=w.id
        WHERE w.owner_id=:owner AND w.id=:workflow"""),
            {"owner": owner_id, "workflow": workflow_id},
        )
        .mappings()
        .one()
    )
    if "preview_id" not in row["input"]:
        return None
    if row["id"] is None or row["cleared_at"] is not None or not row["unexpired"]:
        raise Problem(409, "portable_preview_unavailable")
    plan = cast(dict[str, object], row["plan"])
    if (
        str(row["id"]) != row["input"]["preview_id"]
        or plan.get("id") != str(row["id"])
        or plan.get("workflow_run_id") != str(workflow_id)
        or row["preview_sha256"] != preview_sha256
        or _sha(plan) != preview_sha256
    ):
        raise Problem(409, "portable_preview_changed")
    return plan


def checked_plan(
    conn: Connection, settings: Settings, owner_id: UUID, workflow_id: UUID
) -> dict[str, object]:
    """Executor calls before consuming approval and closing admission, in one TX."""
    approval = (
        conn.execute(
            text("""SELECT action_kind,intent->'payload' AS payload FROM offerloom.action_approvals
        WHERE owner_id=:owner AND workflow_run_id=:workflow AND intent IS NOT NULL"""),
            {"owner": owner_id, "workflow": workflow_id},
        )
        .mappings()
        .one_or_none()
    )
    if approval is None:
        raise Problem(409, "approval_required")
    plan = approval_preview(conn, owner_id, workflow_id, approval["payload"]["preview_sha256"])
    if plan is None or plan["action_kind"] != approval["action_kind"]:
        raise Problem(409, "portable_preview_required")
    binding = cast(dict[str, object], plan["binding"])
    if binding != _bindings(conn, owner_id, UUID(str(binding["session_id"])), settings):
        raise Problem(409, "portable_preview_stale")
    expected_payload = {**cast(dict[str, object], plan["payload"]), "preview_sha256": _sha(plan)}
    if approval["payload"] != expected_payload:
        raise Problem(409, "portable_preview_changed")
    return plan


def expire_previews(conn: Connection, owner_id: UUID | None = None) -> int:
    result = conn.execute(
        text("""UPDATE offerloom.portable_previews SET candidate_bytes=NULL,
        cleared_at=clock_timestamp(),clear_reason='expired'
        WHERE cleared_at IS NULL AND expires_at<=clock_timestamp()
        AND (CAST(:owner AS uuid) IS NULL OR owner_id=CAST(:owner AS uuid))"""),
        {"owner": owner_id},
    )
    return result.rowcount


def _counts(conn: Connection, owner: UUID, application: UUID | None) -> dict[str, int]:
    # These identifiers and predicates are fixed here, never client-controlled SQL.
    if application is not None:
        relations = {
            "applications": ("applications", "id=:application"),
            "notes": ("application_notes", "application_id=:application"),
            "contacts": ("application_contacts", "application_id=:application"),
            "attachments": ("application_attachments", "application_id=:application"),
            "appointments": ("appointments", "application_id=:application"),
            "documents": ("document_drafts", "application_id=:application"),
        }
    else:
        relations = {
            name: (name, "true")
            for name in (
                "professional_claims",
                "evidence_records",
                "source_configs",
                "job_postings",
                "applications",
                "application_notes",
                "application_contacts",
                "application_attachments",
                "appointments",
                "document_drafts",
                "document_revisions",
                "encrypted_credentials",
            )
        }
    return {
        label: int(
            conn.execute(
                text(
                    f"SELECT count(*) FROM offerloom.{table} WHERE owner_id=:owner AND {predicate}"
                ),
                {"owner": owner, "application": application},
            ).scalar_one()
        )
        for label, (table, predicate) in relations.items()
    }


def get_preview(conn: Connection, owner_id: UUID, identifier: UUID) -> dict[str, object]:
    from offerloom.approvals import get_action_approval

    row = (
        conn.execute(
            text("""SELECT id,workflow_run_id,plan,preview_sha256,expires_at,cleared_at,clear_reason
        FROM offerloom.portable_previews WHERE owner_id=:owner AND id=:id"""),
            {"owner": owner_id, "id": identifier},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise Problem(404, "portable_preview_not_found")
    workflow = get_workflow(conn, owner_id, row["workflow_run_id"])
    return {
        "id": str(row["id"]),
        "workflow_run_id": str(row["workflow_run_id"]),
        "version": workflow["version"],
        "workflow": workflow,
        "plan": row["plan"],
        "preview_sha256": row["preview_sha256"],
        "expires_at": row["expires_at"].isoformat(),
        "cleared_at": row["cleared_at"].isoformat() if row["cleared_at"] is not None else None,
        "clear_reason": row["clear_reason"],
        "approval": get_action_approval(conn, owner_id, row["workflow_run_id"]),
    }


def _prepare(
    conn: Connection,
    settings: Settings,
    owner: Owner,
    *,
    payload: CandidateUpload | None,
    deletion: DeletionRequest | None,
    expected_source_owner: UUID | None,
    checkpoint: VerifiedCheckpoint | None,
) -> tuple[int, dict[str, object]]:
    from offerloom.approvals import prepare_action_approval

    expire_previews(conn, owner.id)
    if (
        conn.execute(
            text(
                "SELECT 1 FROM offerloom.portable_previews WHERE owner_id=:owner AND cleared_at IS NULL"
            ),
            {"owner": owner.id},
        ).first()
        is not None
    ):
        raise Problem(409, "portable_preview_pending")
    identifier = uuid4()
    replacing = payload is not None
    action: Literal["replace_workspace", "privacy_delete"] = (
        "replace_workspace" if replacing else "privacy_delete"
    )
    workflow = enqueue(
        conn,
        owner.id,
        "portable_import" if replacing else "privacy_delete",
        {"preview_id": str(identifier)},
    )
    expiry = conn.execute(
        text("SELECT clock_timestamp()+make_interval(secs=>:ttl)"), {"ttl": PREVIEW_TTL_SECONDS}
    ).scalar_one()
    plan: dict[str, object] = {
        "schema_version": 1,
        "id": str(identifier),
        "workflow_run_id": str(workflow),
        "action_kind": action,
        "binding": _bindings(conn, owner.id, owner.session_id, settings),
        "expires_at": expiry.isoformat(),
        "remaining_copies": ["downloaded_exports", "encrypted_backups", "external_copies"],
        "connections_after_operation": "disabled_reauthorization_and_review_required",
    }
    scope: dict[str, object]
    if replacing:
        assert payload is not None and expected_source_owner is not None and checkpoint is not None
        with decode_archive(payload.stream, expected_source_owner=expected_source_owner) as archive:
            quarantine_records(conn, archive)
            recheck_verified_checkpoint(conn, owner.id, checkpoint)
        scope = {"replacement_sha256": payload.sha256}
        plan.update(
            source_owner_id=str(expected_source_owner),
            source_namespace=str(archive.manifest.source_namespace),
            source_schema_version=archive.manifest.source_schema_version,
            manifest_sha256=archive.manifest.manifest_sha256,
            record_counts={key: len(rows) for key, rows in archive.manifest.records.items()},
            blob_count=len(archive.manifest.blob_manifest),
            byte_length=payload.byte_length,
            checkpoint_id=str(checkpoint.generation_id),
            runtime_pins=checkpoint.runtime_pins,
            credential_policy="excluded_from_portable_archive",
        )
        payload.stream.seek(0)
    else:
        assert deletion is not None
        application = deletion.application_id
        if application is not None:
            version = conn.execute(
                text("SELECT version FROM offerloom.applications WHERE owner_id=:owner AND id=:id"),
                {"owner": owner.id, "id": application},
            ).scalar_one_or_none()
            if version is None:
                raise Problem(404, "application_not_found")
            if version != deletion.application_version:
                raise Problem(412, "version_conflict")
        scope = {"categories": ["all_workspace"] if application is None else ["applications"]}
        if application is not None:
            scope.update(
                application_id=str(application), application_version=deletion.application_version
            )
            plan["connections_after_operation"] = "unchanged_except_revoked_owner_sessions"
        plan.update(
            record_counts=_counts(conn, owner.id, application),
            credentials_erased=application is None,
            local_login_retained=True,
        )
    plan["payload"] = scope
    digest = _sha(plan)
    conn.execute(
        text("""INSERT INTO offerloom.portable_previews
        (owner_id,id,workflow_run_id,action_kind,plan,preview_sha256,candidate_bytes,expires_at)
        VALUES(:owner,:id,:workflow,:kind,CAST(:plan AS jsonb),:sha,:bytes,:expiry)"""),
        {
            "owner": owner.id,
            "id": identifier,
            "workflow": workflow,
            "kind": action,
            "plan": canonical_json(plan),
            "sha": digest,
            "bytes": payload.stream.read(UPLOAD_CHUNK_BYTES) if payload is not None else None,
            "expiry": expiry,
        },
    )
    if payload is not None:
        retain_candidate(conn, owner.id, identifier, payload.stream)
    binding = cast(dict[str, object], plan["binding"])
    prepare_action_approval(
        conn,
        owner.id,
        workflow,
        action_kind=action,
        destination={"namespace_id": binding["namespace_id"]},
        payload={**scope, "preview_sha256": digest},
        session_id=owner.session_id,
        ttl_seconds=PREVIEW_TTL_SECONDS,
    )
    return 201, get_preview(conn, owner.id, identifier)


def _checkpoint(db: Database, settings: Settings, owner: UUID) -> VerifiedCheckpoint:
    try:
        return verified_checkpoint(db, settings, owner)
    except ConfigurationError as error:
        raise Problem(503, "backup_configuration_invalid") from error
    except BackupError as error:
        raise Problem(409, str(error)) from error


def build_router(db: Database, settings: Settings) -> APIRouter:
    from offerloom.approvals import require_recent_authentication

    router = APIRouter(prefix="/api/v1/portable-previews")

    @router.get("")
    def state(owner: Annotated[Owner, Depends(require_owner)]) -> dict[str, object]:
        with db.transaction() as conn:
            lock_installation(conn)
            version = conn.execute(
                text(
                    "SELECT workspace_version FROM offerloom.owner_settings WHERE owner_id=:owner"
                ),
                {"owner": owner.id},
            ).scalar_one()
            identifiers = (
                conn.execute(
                    text("""SELECT id FROM offerloom.portable_previews WHERE owner_id=:owner
                ORDER BY created_at DESC,id DESC LIMIT 20"""),
                    {"owner": owner.id},
                )
                .scalars()
                .all()
            )
            return {
                "workspace_version": version,
                "items": [get_preview(conn, owner.id, identifier) for identifier in identifiers],
            }

    @router.get("/{identifier}")
    def inspect(
        identifier: UUID, owner: Annotated[Owner, Depends(require_owner)]
    ) -> dict[str, object]:
        with db.transaction() as conn:
            lock_installation(conn)
            return get_preview(conn, owner.id, identifier)

    @router.post("/deletion")
    def deletion_preview(
        body: DeletionRequest,
        request: Request,
        owner: Annotated[Owner, Depends(require_owner)],
    ) -> JSONResponse:
        with db.transaction() as conn:
            lock_installation(conn)
            require_recent_authentication(conn, owner)
            status, result = command(
                conn,
                owner.id,
                "POST",
                "/portable-previews/deletion",
                {},
                body.model_dump(mode="json"),
                request.headers.get("if-match"),
                request.headers.get("idempotency-key"),
                "workspace",
                owner.id,
                lambda active: _prepare(
                    active,
                    settings,
                    owner,
                    payload=None,
                    deletion=body,
                    expected_source_owner=None,
                    checkpoint=None,
                ),
                workspace_change=False,
            )
        return JSONResponse(result, status_code=status, headers={"ETag": f'"{result["version"]}"'})

    @router.post("/import")
    async def import_preview(
        request: Request,
        owner: Annotated[Owner, Depends(require_owner)],
        source_owner: Annotated[UUID | None, Header(alias="X-Source-Owner")] = None,
    ) -> JSONResponse:
        def preflight() -> VerifiedCheckpoint:
            with db.transaction() as conn:
                lock_installation(conn)
                require_recent_authentication(conn, owner)
            return _checkpoint(db, settings, owner.id)

        checkpoint = await run_in_threadpool(preflight)
        expected_source = owner.id if source_owner is None else source_owner

        def prepare(payload: CandidateUpload) -> JSONResponse:
            try:
                with db.transaction() as conn:
                    lock_installation(conn)
                    require_recent_authentication(conn, owner)
                    status, result = command(
                        conn,
                        owner.id,
                        "POST",
                        "/portable-previews/import",
                        {},
                        {
                            "archive_sha256": payload.sha256,
                            "byte_length": payload.byte_length,
                            "expected_source_owner": str(expected_source),
                        },
                        request.headers.get("if-match"),
                        request.headers.get("idempotency-key"),
                        "workspace",
                        owner.id,
                        lambda active: _prepare(
                            active,
                            settings,
                            owner,
                            payload=payload,
                            deletion=None,
                            expected_source_owner=expected_source,
                            checkpoint=checkpoint,
                        ),
                        workspace_change=False,
                    )
            except ArchiveError as error:
                raise Problem(422, str(error)) from error
            return JSONResponse(
                result, status_code=status, headers={"ETag": f'"{result["version"]}"'}
            )

        with temporary_archive(settings) as stream:
            total = 0
            digest = hashlib.sha256()
            async for chunk in request.stream():
                total += len(chunk)
                if total > MAX_ARCHIVE_BYTES:
                    raise Problem(413, "body_too_large")
                digest.update(chunk)
                await run_in_threadpool(stream.write, chunk)
            if not total:
                raise Problem(413, "body_too_large")
            stream.seek(0)
            return await run_in_threadpool(
                prepare, CandidateUpload(stream, total, digest.hexdigest())
            )

    @router.post("/{identifier}/cancel")
    def cancel(
        identifier: UUID,
        body: CancelPreview,
        request: Request,
        owner: Annotated[Owner, Depends(require_owner)],
    ) -> JSONResponse:
        with db.transaction() as conn:
            lock_installation(conn)
            row = conn.execute(
                text("""SELECT workflow_run_id FROM offerloom.portable_previews
                WHERE owner_id=:owner AND id=:id"""),
                {"owner": owner.id, "id": identifier},
            ).one_or_none()
            if row is None:
                raise Problem(404, "portable_preview_not_found")

            def effect(active: Connection) -> tuple[int, dict[str, object]]:
                cancel_workflow(active, owner.id, row.workflow_run_id)
                return 200, get_preview(active, owner.id, identifier)

            status, result = command(
                conn,
                owner.id,
                "POST",
                "/portable-previews/{identifier}/cancel",
                {"identifier": str(identifier)},
                body.model_dump(),
                request.headers.get("if-match"),
                request.headers.get("idempotency-key"),
                "workflow",
                row.workflow_run_id,
                effect,
                workspace_change=False,
            )
        return JSONResponse(result, status_code=status, headers={"ETag": f'"{result["version"]}"'})

    return router
