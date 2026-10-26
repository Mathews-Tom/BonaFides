# SPDX-License-Identifier: MIT OR Apache-2.0
"""Durable, fenced owner exports; temporary copies are not installation backups."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import time
from pathlib import Path
from threading import Event, Thread
from typing import Annotated, Literal, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from offerloom.approvals import consume_action_approval
from offerloom.auth import Owner, require_owner
from offerloom.backup_archive import BackupError
from offerloom.backup_configuration import (
    VerifiedCheckpoint,
    recheck_verified_checkpoint,
    verified_checkpoint,
)
from offerloom.commands import canonical_json, command, lock_installation
from offerloom.config import ConfigurationError, Settings
from offerloom.db import Database
from offerloom.installation_control import InstallationMaintenance, assert_control, begin_control
from offerloom.maintenance import finish_activity, start_cleanup_activity
from offerloom.portable_archive import MAX_ARCHIVE_BYTES, ArchiveError, encode_archive
from offerloom.portable_data import collect_controlled_owner_data
from offerloom.portable_erasure import delete_controlled_owner_data
from offerloom.portable_import import import_controlled_owner_data
from offerloom.portable_previews import checked_plan, expire_previews
from offerloom.problems import Problem
from offerloom.storage import atomic_file, directory, read_file, subdirectory
from offerloom.workflows import (
    Claim,
    assert_fence,
    claim_control,
    enqueue,
    fail,
    get_workflow,
    heartbeat,
)


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def _root(settings: Settings) -> Path:
    return settings.blob_root / "portable_exports"


def _remove(settings: Settings, owner: UUID, identifier: UUID) -> None:
    try:
        with directory(_root(settings)) as root, subdirectory(root, str(owner)) as folder:
            os.unlink(str(identifier) + ".zip", dir_fd=folder)
            os.fsync(folder)
    except FileNotFoundError:
        # An absent expired copy has no remaining bytes to erase.
        return


def expire_exports(db: Database, settings: Settings) -> int:
    """Positive execution registration survives connection loss until all I/O returns."""
    activity = start_cleanup_activity(db, uuid4())
    try:
        return _expire_exports(db, settings)
    finally:
        finish_activity(db, activity)


def _expire_exports(db: Database, settings: Settings) -> int:
    """Hold admission's shared lock through actual deletion; never race a replacement."""
    with db.transaction() as conn:
        lock_installation(conn)
        expire_previews(conn)
        rows = conn.execute(
            text("""SELECT owner_id,workflow_run_id FROM offerloom.portable_exports
            WHERE state<>'expired' AND expires_at<=clock_timestamp()
            ORDER BY expires_at,workflow_run_id LIMIT 100 FOR UPDATE""")
        ).all()
        for owner, identifier in rows:
            _remove(settings, owner, identifier)
            conn.execute(
                text("""UPDATE offerloom.portable_exports SET state='expired'
                WHERE owner_id=:owner AND workflow_run_id=:id"""),
                {"owner": owner, "id": identifier},
            )
        return len(rows)


class LifecycleWorker:
    """A single dedicated controller; never registers itself as an ordinary writer."""

    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.id = uuid4()
        self.stopping = Event()

    def _destructive_preflight(
        self,
        task: Claim,
    ) -> tuple[dict[str, object], VerifiedCheckpoint | None]:
        stopped, lost = Event(), Event()

        def pulse() -> None:
            while not stopped.wait(2):
                try:
                    with self.db.transaction() as conn:
                        if not heartbeat(conn, task):
                            lost.set()
                            return
                except (DBAPIError, Problem):
                    lost.set()
                    return

        thread = Thread(target=pulse, name="offerloom-preview-preflight-heartbeat")
        thread.start()
        try:
            with self.db.transaction() as conn:
                assert_fence(conn, task)
                plan = checked_plan(conn, self.settings, task.owner_id, task.id)
            checkpoint = None
            if task.kind == "portable_import":
                checkpoint = verified_checkpoint(
                    self.db, self.settings, task.owner_id, UUID(str(plan["checkpoint_id"]))
                )
                if checkpoint.runtime_pins != plan["runtime_pins"]:
                    raise Problem(409, "portable_checkpoint_changed")
            if lost.is_set():
                raise Problem(409, "workflow_lease_lost")
            return plan, checkpoint
        finally:
            stopped.set()
            thread.join()

    def run_once(self) -> bool:
        with self.db.transaction() as conn:
            task = claim_control(conn, self.id)
            if task is None:
                return False
        plan: dict[str, object] | None = None
        checkpoint: VerifiedCheckpoint | None = None
        try:
            if task.kind in {"portable_import", "privacy_delete"}:
                plan, checkpoint = self._destructive_preflight(task)
            with self.db.transaction() as conn:
                assert_fence(conn, task)
                if plan is not None:
                    plan = checked_plan(conn, self.settings, task.owner_id, task.id)
                    if checkpoint is not None:
                        recheck_verified_checkpoint(conn, task.owner_id, checkpoint)
                    binding = cast(dict[str, object], plan["binding"])
                    consume_action_approval(
                        conn,
                        task,
                        action_kind=cast(
                            Literal["privacy_delete", "replace_workspace"], plan["action_kind"]
                        ),
                        destination={"namespace_id": binding["namespace_id"]},
                        payload={
                            **cast(dict[str, object], plan["payload"]),
                            "preview_sha256": hashlib.sha256(
                                canonical_json(plan).encode()
                            ).hexdigest(),
                        },
                        target_version=cast(int, binding["workspace_version"]),
                    )
                elif task.kind != "portable_export":
                    fail(conn, task, "unsupported_lifecycle_operation", False)
                    return True
                control = begin_control(conn, task)
        except (BackupError, ConfigurationError, Problem, OSError, ValueError) as error:
            code = error.code if isinstance(error, Problem) else "portable_preflight_failed"
            with self.db.transaction() as conn:
                fail(conn, task, code, False)
            return True
        maintenance = InstallationMaintenance(
            self.db, control, self.settings.import_spool, self.settings.renderer_spool
        )
        with maintenance.quiescent():
            if task.kind in {"portable_import", "privacy_delete"}:
                assert plan is not None
                if task.kind == "portable_import":
                    assert checkpoint is not None
                    with self.db.transaction() as conn:
                        assert_control(conn, control)
                        payload = conn.execute(
                            text("""SELECT candidate_bytes FROM offerloom.portable_previews
                            WHERE owner_id=:owner AND workflow_run_id=:id AND cleared_at IS NULL"""),
                            {"owner": task.owner_id, "id": task.id},
                        ).scalar_one()
                    if payload is None:
                        raise Problem(409, "portable_preview_unavailable")
                    result = import_controlled_owner_data(
                        self.db,
                        self.settings,
                        control,
                        bytes(payload),
                        expected_source_owner=UUID(str(plan["source_owner_id"])),
                        checkpoint_id=checkpoint.generation_id,
                        runtime_pins=checkpoint.runtime_pins,
                    )
                else:
                    result = delete_controlled_owner_data(self.db, self.settings, control)
                maintenance.finish(result)
                return True
            try:
                with (
                    self.db.engine.connect().execution_options(
                        isolation_level="REPEATABLE READ"
                    ) as conn,
                    conn.begin(),
                ):
                    archive = collect_controlled_owner_data(conn, self.settings, control)
                    payload = encode_archive(archive.manifest, archive.blobs)
                digest = hashlib.sha256(payload).hexdigest()
                with self.db.transaction() as conn:
                    assert_control(conn, control)
                    expires = conn.execute(
                        text("""INSERT INTO offerloom.portable_exports
                        (owner_id,workflow_run_id,namespace_id,maintenance_epoch,
                         archive_sha256,manifest_sha256,byte_length)
                        VALUES(:owner,:id,:namespace,:epoch,:sha,:manifest,:length)
                        RETURNING expires_at"""),
                        {
                            "owner": control.owner_id,
                            "id": control.id,
                            "namespace": control.namespace_id,
                            "epoch": control.maintenance_epoch,
                            "sha": digest,
                            "manifest": archive.manifest.manifest_sha256,
                            "length": len(payload),
                        },
                    ).scalar_one()
                with (
                    directory(_root(self.settings), create=True) as root,
                    subdirectory(root, str(control.owner_id), create=True) as folder,
                ):
                    atomic_file(folder, str(control.id) + ".zip", payload, staging_id=control.id)
                with self.db.transaction() as conn:
                    assert_control(conn, control)
                    conn.execute(
                        text("""UPDATE offerloom.portable_exports SET state='ready'
                        WHERE owner_id=:owner AND workflow_run_id=:id AND state='pending'"""),
                        {"owner": control.owner_id, "id": control.id},
                    )
                maintenance.finish(
                    {
                        "export_id": str(control.id),
                        "archive_sha256": digest,
                        "manifest_sha256": archive.manifest.manifest_sha256,
                        "byte_length": len(payload),
                        "expires_at": expires.isoformat(),
                        "record_count": sum(
                            len(rows) for rows in archive.manifest.records.values()
                        ),
                    }
                )
            except (ArchiveError, ValueError, OSError):
                # All local I/O has returned. Remove partial/completed copy before opening
                # admission. A failed removal or lost authority leaves maintenance closed.
                _remove(self.settings, control.owner_id, control.id)
                with self.db.transaction() as conn:
                    assert_control(conn, control)
                    conn.execute(
                        text("""UPDATE offerloom.portable_exports SET state='expired'
                        WHERE owner_id=:owner AND workflow_run_id=:id"""),
                        {"owner": control.owner_id, "id": control.id},
                    )
                maintenance.fail_export()
        return True

    def stop(self, _signum: int, _frame: object) -> None:
        # Do not kill active bounded I/O or fabricate a completed drain.
        self.stopping.set()

    def run(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        last_cleanup = float("-inf")
        while not self.stopping.is_set():
            try:
                if time.monotonic() - last_cleanup >= 60:
                    last_cleanup = time.monotonic()
                    from offerloom.backup_schedule import run_due
                    from offerloom.task_retention import expire_task_inputs

                    # Independent privacy expiry must not depend on optional backup config.
                    expire_task_inputs(self.db)

                    try:
                        scheduled = run_due(self.db, self.settings)
                    except ConfigurationError:
                        print(
                            json.dumps(
                                {
                                    "event": "backup_schedule_failed",
                                    "code": "backup_configuration_invalid",
                                }
                            ),
                            flush=True,
                        )
                    else:
                        if scheduled.get("state") == "failed":
                            print(
                                json.dumps(
                                    {
                                        "event": "backup_schedule_failed",
                                        "code": scheduled["failure_code"],
                                    }
                                ),
                                flush=True,
                            )
                    expire_exports(self.db, self.settings)
                if self.run_once():
                    continue
            except (DBAPIError, Problem, OSError, ValueError) as error:
                code = error.code if isinstance(error, Problem) else "lifecycle_operation_failed"
                print(json.dumps({"event": "lifecycle_unavailable", "code": code}), flush=True)
            self.stopping.wait(2)


def build_router(db: Database, settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/v1/portable-exports")

    @router.get("")
    def list_exports(owner: Annotated[Owner, Depends(require_owner)]) -> dict[str, object]:
        with db.transaction() as conn:
            lock_installation(conn)
            rows = (
                conn.execute(
                    text("""SELECT w.id,w.state,w.last_error_code,w.created_at,
                e.archive_sha256,e.manifest_sha256,e.byte_length,e.expires_at,
                coalesce(e.state='ready' AND e.expires_at>clock_timestamp()
                  AND w.state='succeeded',false) AS downloadable,
                e.state AS copy_state
                FROM offerloom.workflow_runs w LEFT JOIN offerloom.portable_exports e
                ON e.owner_id=w.owner_id AND e.workflow_run_id=w.id
                WHERE w.owner_id=:owner AND w.kind='portable_export'
                ORDER BY w.created_at DESC,w.id DESC LIMIT 20"""),
                    {"owner": owner.id},
                )
                .mappings()
                .all()
            )
            return {
                "items": [
                    {
                        key: str(value)
                        if isinstance(value, UUID)
                        else value.isoformat()
                        if hasattr(value, "isoformat")
                        else value
                        for key, value in row.items()
                    }
                    for row in rows
                ]
            }

    @router.post("")
    def create_export(
        body: ExportRequest, request: Request, owner: Annotated[Owner, Depends(require_owner)]
    ) -> Response:
        with db.transaction() as conn:
            profile = conn.execute(
                text("SELECT id FROM offerloom.profiles WHERE owner_id=:owner"),
                {"owner": owner.id},
            ).scalar_one()

            def effect(active: Connection) -> tuple[int, dict[str, object]]:
                pending = active.execute(
                    text("""SELECT count(*) FROM offerloom.workflow_runs WHERE owner_id=:owner
                    AND kind='portable_export' AND state NOT IN
                    ('succeeded','cancelled','permanent_failure')"""),
                    {"owner": owner.id},
                ).scalar_one()
                copies = active.execute(
                    text("""SELECT count(*) FROM offerloom.portable_exports WHERE owner_id=:owner
                    AND state<>'expired' AND expires_at>clock_timestamp()"""),
                    {"owner": owner.id},
                ).scalar_one()
                if pending or copies >= 2:
                    raise Problem(429, "portable_export_limit")
                identifier = enqueue(active, owner.id, "portable_export", {})
                return 202, get_workflow(active, owner.id, identifier)

            status, result = command(
                conn,
                owner.id,
                "POST",
                "/portable-exports",
                {},
                body.model_dump(mode="json"),
                request.headers.get("if-match"),
                request.headers.get("idempotency-key"),
                "profile",
                profile,
                effect,
                workspace_change=False,
            )
        return Response(
            json.dumps(result),
            status_code=status,
            media_type="application/json",
            headers={"Location": f"/api/v1/workflows/{result['id']}"},
        )

    @router.get("/{export_id}/download")
    def download_export(
        export_id: UUID, owner: Annotated[Owner, Depends(require_owner)]
    ) -> Response:
        with db.transaction() as conn:
            lock_installation(conn)
            row = (
                conn.execute(
                    text("""SELECT e.*, e.expires_at<=clock_timestamp() AS expired
                FROM offerloom.portable_exports e JOIN offerloom.workflow_runs w
                ON w.owner_id=e.owner_id AND w.id=e.workflow_run_id
                WHERE e.owner_id=:owner AND e.workflow_run_id=:id AND w.state='succeeded'"""),
                    {"owner": owner.id, "id": export_id},
                )
                .mappings()
                .first()
            )
            if row is None:
                raise Problem(404, "portable_export_not_found")
            if row["expired"] or row["state"] == "expired":
                raise Problem(410, "portable_export_expired")
            if row["state"] != "ready":
                raise Problem(409, "portable_export_unavailable")
            try:
                with (
                    directory(_root(settings)) as root,
                    subdirectory(root, str(owner.id)) as folder,
                ):
                    payload = read_file(folder, str(export_id) + ".zip", MAX_ARCHIVE_BYTES)
            except (OSError, ValueError) as error:
                raise Problem(409, "portable_export_unavailable") from error
            if (
                len(payload) != row["byte_length"]
                or hashlib.sha256(payload).hexdigest() != row["archive_sha256"]
            ):
                raise Problem(409, "portable_export_integrity_failed")
        return Response(
            payload,
            media_type="application/zip",
            headers={
                "Content-Disposition": 'attachment; filename="offerloom-owner-data.zip"',
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
                "X-Content-SHA256": row["archive_sha256"],
            },
        )

    return router
