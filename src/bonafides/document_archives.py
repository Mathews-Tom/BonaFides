# SPDX-License-Identifier: MIT OR Apache-2.0
"""All-current reviewed document ZIPs, kept only in a fenced private temporary spool."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import time
import unicodedata
import zipfile
from threading import Event
from typing import Annotated, cast
from uuid import UUID, uuid4, uuid5

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from bonafides.auth import Owner, require_owner
from bonafides.commands import canonical_json, command, lock_installation
from bonafides.config import Settings
from bonafides.db import Database
from bonafides.document_frame import template_digest
from bonafides.document_generation import lock_workspace
from bonafides.documents import PreparedRender, prepare_render
from bonafides.maintenance import finish_activity, start_cleanup_activity
from bonafides.problems import Problem
from bonafides.profile_identity import portrait_bytes
from bonafides.render_service import RenderClient
from bonafides.storage import atomic_stream, directory, read_file, subdirectory
from bonafides.suitability import _posting_digest
from bonafides.workflows import Claim, assert_fence, enqueue, get_workflow, publish

MAX_DOCUMENTS = 100
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
EXPIRES_IN_SECONDS = 86400


class ArchiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    manifest_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


def safe_component(value: str, fallback: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    cleaned = (
        "".join(
            "_" if character in '/\\<>:"|?*' else character
            for character in normalized
            if not unicodedata.category(character).startswith("C")
        )
        .strip()
        .rstrip(". ")
    )
    cleaned = cleaned.encode("utf-8")[:80].decode("utf-8", errors="ignore").rstrip(". ")
    if not cleaned or cleaned in {".", ".."}:
        cleaned = fallback
    if re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", cleaned):
        cleaned = "_" + cleaned
    return cleaned


def manifest(conn: Connection, owner: UUID) -> tuple[dict[str, object], list[PreparedRender]]:
    """Freeze membership before eligibility: a broken live member never disappears."""
    lock_workspace(conn, owner)
    namespace = conn.execute(
        text("SELECT namespace_id FROM bonafides.installation_state WHERE singleton")
    ).scalar_one()
    profile_revision = conn.execute(
        text("SELECT current_revision_id FROM bonafides.profiles WHERE owner_id=:owner"),
        {"owner": owner},
    ).scalar_one()
    rows = (
        conn.execute(
            text("""SELECT d.id,d.application_id,d.kind,d.current_revision_id,p.company,p.title,
        p.location_text,p.description_text,p.canonical_url,p.content_sha256,
        (SELECT r.id FROM bonafides.posting_requirement_revisions r
         WHERE r.owner_id=d.owner_id AND r.posting_id=p.id
         ORDER BY r.revision_no DESC LIMIT 1) AS requirements_revision_id,
        (SELECT to_jsonb(r) FROM bonafides.document_reviews r
         WHERE r.owner_id=d.owner_id AND r.document_revision_id=d.current_revision_id) AS review
        FROM bonafides.document_drafts d
        LEFT JOIN bonafides.applications a ON a.owner_id=d.owner_id AND a.id=d.application_id
        LEFT JOIN bonafides.saved_opportunities o ON o.owner_id=a.owner_id AND o.id=a.opportunity_id
        LEFT JOIN bonafides.job_postings p ON p.owner_id=o.owner_id AND p.id=o.posting_id
        WHERE d.owner_id=:owner AND d.scope='tailored' AND d.kind IN ('resume','cover_letter')
        AND d.deleted_at IS NULL ORDER BY d.application_id,d.kind,d.id LIMIT 101"""),
            {"owner": owner},
        )
        .mappings()
        .all()
    )
    if not rows:
        raise Problem(409, "document_archive_empty")
    if len(rows) > MAX_DOCUMENTS:
        raise Problem(409, "document_archive_too_many_documents")
    items: list[dict[str, object]] = []
    renders: list[PreparedRender] = []
    companies: dict[str, str] = {}
    used_directories: set[str] = set()
    used_paths: set[str] = set()
    for row in rows:
        identifier = str(row["id"])
        if row["current_revision_id"] is None or row["company"] is None or row["title"] is None:
            raise Problem(409, "document_archive_source_changed", identifier)
        try:
            prepared = prepare_render(conn, owner, row["id"], row["current_revision_id"])
            template_sha = prepared.template_sha256 or template_digest(prepared.template_version)
        except Problem as error:
            raise Problem(error.status, error.code, identifier) from error
        except OSError as error:
            raise Problem(409, "document_template_unavailable", identifier) from error
        except ValueError as error:
            if str(error) != "unsupported_document_template":
                raise
            raise Problem(409, "document_template_unavailable", identifier) from error
        company, role = str(row["company"]), str(row["title"])
        if company not in companies:
            base = safe_component(company, "Company")
            folder = base
            suffix = 1
            while folder.casefold() in used_directories:
                suffix += 1
                folder = f"{base}-{suffix}"
            companies[company] = folder
            used_directories.add(folder.casefold())
        filename = f"{safe_component(role, 'Role')}-{row['kind']}-{row['application_id']}"
        path = f"{companies[company]}/{filename}.pdf"
        if path.casefold() in used_paths:
            path = f"{companies[company]}/{filename}-{identifier}.pdf"
        used_paths.add(path.casefold())
        items.append(
            {
                "document_id": identifier,
                "revision_id": str(prepared.revision_id),
                "content_sha256": prepared.content_sha256,
                "template_version": prepared.template_version,
                "template_sha256": template_sha,
                "render_sha256": hashlib.sha256(
                    canonical_json(
                        {
                            "body": prepared.body.model_dump(mode="json"),
                            "frame": prepared.frame.model_dump(mode="json")
                            if prepared.frame
                            else None,
                        }
                    ).encode()
                ).hexdigest(),
                "review_sha256": hashlib.sha256(canonical_json(row["review"]).encode()).hexdigest(),
                "application_id": str(row["application_id"]),
                "posting_content_sha256": _posting_digest(row),
                "requirements_revision_id": str(row["requirements_revision_id"]),
                "company": company,
                "role": role,
                "kind": row["kind"],
                "path": path,
            }
        )
        renders.append(prepared)
    return {
        "namespace_id": str(namespace),
        "profile_revision_id": str(profile_revision),
        "documents": items,
    }, renders


def digest_manifest(value: dict[str, object]) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def check_manifest(
    conn: Connection, owner: UUID, snapshot: dict[str, object]
) -> list[PreparedRender]:
    current, renders = manifest(conn, owner)
    if current != snapshot.get("manifest") or digest_manifest(current) != snapshot.get(
        "manifest_sha256"
    ):
        raise Problem(409, "document_archive_source_changed")
    return renders


def _name(task: Claim) -> str:
    return f"{task.id}.{task.fence}.zip"


def _remove(settings: Settings, owner: UUID, name: str) -> None:
    try:
        with (
            directory(settings.blob_root / "document_archives") as root,
            subdirectory(root, str(owner)) as folder,
        ):
            os.unlink(name, dir_fd=folder)
            os.fsync(folder)
    except FileNotFoundError:
        pass


def _progress(conn: Connection, task: Claim, completed: int, total: int) -> None:
    lock_workspace(conn, task.owner_id)
    assert_fence(conn, task)
    conn.execute(
        text("""INSERT INTO bonafides.workflow_steps
        (owner_id,workflow_run_id,step_key,attempt,fence,state,checkpoint,completed_at)
        VALUES(:owner,:id,:key,:attempt,:fence,'succeeded',CAST(:checkpoint AS jsonb),clock_timestamp())"""),
        {
            "owner": task.owner_id,
            "id": task.id,
            "key": f"archive_progress_{completed}",
            "attempt": task.attempt,
            "fence": task.fence,
            "checkpoint": canonical_json(
                {"mode": "document_archive", "completed": completed, "total": total}
            ),
        },
    )


def execute_archive(db: Database, settings: Settings, task: Claim, interrupted: Event) -> None:
    published = False
    publication_started = False
    name = _name(task)
    try:
        with db.transaction() as conn:
            renders = check_manifest(conn, task.owner_id, task.input)
            assert_fence(conn, task)
            remaining = conn.execute(
                text(
                    "SELECT extract(epoch FROM deadline_at-clock_timestamp()) FROM bonafides.workflow_runs WHERE owner_id=:owner AND id=:id"
                ),
                {"owner": task.owner_id, "id": task.id},
            ).scalar_one()
            _progress(conn, task, 0, len(renders))
        deadline = time.monotonic() + min(600.0, float(remaining))
        snapshot = cast(dict[str, object], task.input["manifest"])
        items = cast(list[dict[str, object]], snapshot["documents"])
        with (
            directory(settings.blob_root / "document_archives", create=True) as root,
            subdirectory(root, str(task.owner_id), create=True) as folder,
            atomic_stream(folder, name, staging_id=uuid5(task.id, str(task.fence))) as stream,
        ):
            pdf_bytes = 0
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for index, (item, prepared) in enumerate(zip(items, renders, strict=True)):
                    if interrupted.is_set():
                        raise Problem(409, "workflow_interrupted")
                    budget = deadline - time.monotonic()
                    if budget <= 0:
                        raise Problem(409, "deadline_exceeded")
                    with db.transaction() as conn:
                        lock_workspace(conn, task.owner_id)
                        assert_fence(conn, task)
                        portrait_png = (
                            portrait_bytes(conn, settings, task.owner_id, prepared.frame.portrait)
                            if prepared.frame is not None and prepared.frame.portrait is not None
                            else None
                        )
                    pdf = RenderClient(settings.renderer_spool, timeout=min(40.0, budget)).render(
                        prepared.body,
                        prepared.template_version,
                        prepared.frame,
                        prepared.template_sha256,
                        portrait_png=portrait_png,
                    )
                    pdf_bytes += len(pdf)
                    if pdf_bytes > MAX_ARCHIVE_BYTES:
                        raise Problem(409, "document_archive_too_large")
                    archive.writestr(str(item["path"]), pdf)
                    del pdf
                    if pdf_bytes + stream.tell() > MAX_ARCHIVE_BYTES:
                        raise Problem(409, "document_archive_too_large")
                    if interrupted.is_set() or time.monotonic() >= deadline:
                        raise Problem(
                            409,
                            "workflow_interrupted" if interrupted.is_set() else "deadline_exceeded",
                        )
                    with db.transaction() as conn:
                        _progress(conn, task, index + 1, len(renders))
            length = stream.tell()
            if pdf_bytes + length > MAX_ARCHIVE_BYTES:
                raise Problem(409, "document_archive_too_large")
            stream.seek(0)
            digest = hashlib.sha256()
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
            sha = digest.hexdigest()
            with db.transaction() as conn:
                check_manifest(conn, task.owner_id, task.input)
                assert_fence(conn, task)
        publication_started = True
        with db.transaction() as conn:

            def effect(active: Connection) -> dict[str, object]:
                check_manifest(active, task.owner_id, task.input)
                assert_fence(active, task)
                expires = active.execute(
                    text("SELECT clock_timestamp()+interval '24 hours'")
                ).scalar_one()
                return {
                    "artifact_type": "document_archive",
                    "archive_document_count": len(renders),
                    "archive_expires_at": expires.isoformat(),
                    "archive_sha256": sha,
                    "archive_byte_length": length,
                    "archive_filename": name,
                    "namespace_id": snapshot["namespace_id"],
                }

            publish(conn, task, effect)
        published = True
    finally:
        if not published:
            if not publication_started:
                _remove(settings, task.owner_id, name)
            else:
                try:
                    with db.transaction() as conn:
                        # Serialize with publication, erasure and the orphan sweeper.
                        # A failed COMMIT acknowledgement is not proof of rollback.
                        lock_installation(conn)
                        conn.execute(
                            text(
                                "SELECT owner_id FROM bonafides.owner_settings WHERE owner_id=:owner FOR UPDATE"
                            ),
                            {"owner": task.owner_id},
                        ).all()
                        committed = conn.execute(
                            text("""SELECT EXISTS(SELECT 1 FROM bonafides.workflow_runs
                            WHERE owner_id=:owner AND id=:id AND fence=:fence
                            AND state='succeeded' AND result->>'artifact_type'='document_archive'
                            AND result->>'archive_filename'=:name)"""),
                            {
                                "owner": task.owner_id,
                                "id": task.id,
                                "fence": task.fence,
                                "name": name,
                            },
                        ).scalar_one()
                        if not committed:
                            _remove(settings, task.owner_id, name)
                except DBAPIError:
                    # Keep uncertain private bytes for the existing lifecycle sweep.
                    # Privacy erasure still removes them synchronously under its lock.
                    logging.getLogger(__name__).exception(
                        "Document archive publication reconciliation failed; cleanup deferred"
                    )


def expire_archives(db: Database, settings: Settings) -> int:
    """Delete expired, erased, obsolete-fence and crash-orphan files, never shared blobs."""
    activity = start_cleanup_activity(db, uuid4())
    removed = 0
    try:
        with db.transaction() as conn:
            lock_installation(conn)
            # Workers enter/progress/publish under these same owner locks. A sweep
            # cannot mistake a just-starting worker's stage for a crash orphan.
            conn.execute(
                text("SELECT owner_id FROM bonafides.owner_settings ORDER BY owner_id FOR UPDATE")
            ).all()
            rows = (
                conn.execute(
                    text("""SELECT w.owner_id,w.id,w.fence,w.state,w.result,
            w.lease_expires_at>clock_timestamp() AND w.deadline_at>clock_timestamp()
            AND w.cancel_requested_at IS NULL AND w.maintenance_epoch=i.maintenance_epoch AS active,
            w.result->>'namespace_id'=i.namespace_id::text
            AND (w.result->>'archive_expires_at')::timestamptz>clock_timestamp() AS ready
            FROM bonafides.workflow_runs w CROSS JOIN bonafides.installation_state i
            WHERE i.singleton AND w.kind='pdf_render' AND
            (w.input->>'mode'='document_archive' OR w.result->>'artifact_type'='document_archive')""")
                )
                .mappings()
                .all()
            )
            keep: dict[str, set[str]] = {}
            for row in rows:
                names = keep.setdefault(str(row["owner_id"]), set())
                if row["state"] == "running" and row["active"]:
                    names.add(f".staging-{uuid5(row['id'], str(row['fence'])).hex}")
                    names.add(f"{row['id']}.{row['fence']}.zip")
                if row["state"] == "succeeded" and row["ready"]:
                    names.add(str(row["result"].get("archive_filename")))
            try:
                with directory(settings.blob_root / "document_archives") as root:
                    for owner in os.listdir(root):
                        try:
                            UUID(owner)
                        except ValueError:
                            continue
                        with subdirectory(root, owner) as folder:
                            for name in os.listdir(folder):
                                if name not in keep.get(owner, set()):
                                    os.unlink(name, dir_fd=folder)
                                    removed += 1
                            os.fsync(folder)
            except FileNotFoundError:
                pass
        return removed
    finally:
        finish_activity(db, activity)


def build_router(db: Database, settings: Settings) -> APIRouter:
    router = APIRouter()

    @router.get("/document-archives/preview")
    def preview(owner: Annotated[Owner, Depends(require_owner)]) -> Response:
        with db.transaction() as conn:
            snapshot, renders = manifest(conn, owner.id)
            version = conn.execute(
                text(
                    "SELECT workspace_version FROM bonafides.owner_settings WHERE owner_id=:owner"
                ),
                {"owner": owner.id},
            ).scalar_one()
        result = {
            "workspace_version": version,
            "manifest_sha256": digest_manifest(snapshot),
            "document_count": len(renders),
            "expires_in_seconds": EXPIRES_IN_SECONDS,
            "max_documents": MAX_DOCUMENTS,
            "max_archive_bytes": MAX_ARCHIVE_BYTES,
        }
        return Response(
            canonical_json(result),
            media_type="application/json",
            headers={"Cache-Control": "private, no-store"},
        )

    @router.post("/document-archives")
    def create(
        body: ArchiveRequest, request: Request, owner: Annotated[Owner, Depends(require_owner)]
    ) -> Response:
        def effect(conn: Connection) -> tuple[int, dict[str, object]]:
            snapshot, _ = manifest(conn, owner.id)
            digest = digest_manifest(snapshot)
            if digest != body.manifest_sha256:
                raise Problem(409, "document_archive_source_changed")
            identifier = enqueue(
                conn,
                owner.id,
                "pdf_render",
                {
                    "schema_version": 1,
                    "mode": "document_archive",
                    "manifest": snapshot,
                    "manifest_sha256": digest,
                },
            )
            return 202, get_workflow(conn, owner.id, identifier)

        with db.transaction() as conn:
            status, result = command(
                conn,
                owner.id,
                "POST",
                "/document-archives",
                {},
                body.model_dump(mode="json"),
                request.headers.get("if-match"),
                request.headers.get("idempotency-key"),
                "workspace",
                owner.id,
                effect,
            )
        if status == 410:
            raise Problem(410, "data_erased")
        return Response(
            canonical_json(result),
            status_code=status,
            media_type="application/json",
            headers={
                "Location": f"/api/v1/workflows/{result['id']}",
                "Cache-Control": "private, no-store",
            },
        )

    @router.get("/document-archives/{workflow_id}/download")
    def download(workflow_id: UUID, owner: Annotated[Owner, Depends(require_owner)]) -> Response:
        with db.transaction() as conn:
            lock_workspace(conn, owner.id)
            row = (
                conn.execute(
                    text("""SELECT w.*,i.namespace_id AS current_namespace,
            (w.result->>'archive_expires_at')::timestamptz<=clock_timestamp() AS expired
            FROM bonafides.workflow_runs w CROSS JOIN bonafides.installation_state i
            WHERE i.singleton AND w.owner_id=:owner AND w.id=:id AND w.kind='pdf_render'
            AND w.result->>'artifact_type'='document_archive' AND w.state='succeeded'"""),
                    {"owner": owner.id, "id": workflow_id},
                )
                .mappings()
                .first()
            )
            if row is None:
                raise Problem(404, "document_archive_not_found")
            result = row["result"]
            if row["expired"]:
                raise Problem(410, "document_archive_expired")
            if result.get("namespace_id") != str(row["current_namespace"]):
                raise Problem(410, "document_archive_unavailable")
            check_manifest(conn, owner.id, row["input"])
            filename = f"{workflow_id}.{row['fence']}.zip"
            if filename != result.get("archive_filename"):
                raise Problem(409, "document_archive_integrity_failed")
            try:
                with (
                    directory(settings.blob_root / "document_archives") as root,
                    subdirectory(root, str(owner.id)) as folder,
                ):
                    data = read_file(folder, filename, MAX_ARCHIVE_BYTES)
            except (OSError, ValueError) as error:
                raise Problem(410, "document_archive_unavailable") from error
            if len(data) != result.get("archive_byte_length") or hashlib.sha256(
                data
            ).hexdigest() != result.get("archive_sha256"):
                raise Problem(409, "document_archive_integrity_failed")
            expired = conn.execute(
                text("SELECT CAST(:expires AS timestamptz)<=clock_timestamp()"),
                {"expires": result["archive_expires_at"]},
            ).scalar_one()
            if expired:
                raise Problem(410, "document_archive_expired")
        return Response(
            data,
            media_type="application/zip",
            headers={
                "Content-Disposition": 'attachment; filename="bonafides-reviewed-documents.zip"',
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    return router
