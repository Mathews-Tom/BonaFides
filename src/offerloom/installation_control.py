# SPDX-License-Identifier: MIT OR Apache-2.0
"""Dedicated maintenance authority; ordinary workflow fencing is never bypassed."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Thread
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends
from sqlalchemy import Connection, text

from offerloom.auth import Owner, require_readonly_owner
from offerloom.commands import canonical_json
from offerloom.db import Database
from offerloom.execution_guard import ExecutionGuard
from offerloom.problems import Problem
from offerloom.spool_guard import PausedSpool, pause_spool
from offerloom.workflows import Claim

MAINTENANCE_STATUS_PATH = "/api/v1/maintenance"


@dataclass(frozen=True)
class Control:
    id: UUID
    owner_id: UUID
    holder: UUID
    fence: int
    namespace_id: UUID
    maintenance_epoch: int
    purpose: str

    @property
    def parameters(self) -> dict[str, object]:
        return {"id": self.id, "holder": self.holder, "fence": self.fence}


def begin_control(conn: Connection, task: Claim) -> Control:
    """Commit the returned barrier before draining any I/O.

    Destructive producers must consume their exact action approval in this same
    transaction, BEFORE beginning maintenance. SQL independently verifies that
    receipt and derives immutable scope from the stored intent, not arguments.
    """
    guard = conn.info.get("execution_guard")
    identity = guard.verify() if isinstance(guard, ExecutionGuard) and not guard.exclusive else None
    row = (
        conn.execute(
            text("""SELECT * FROM offerloom.begin_guarded_installation_control(
        :owner,:workflow,:holder,:fence,:controller,:guard)"""),
            {
                "owner": task.owner_id,
                "workflow": task.id,
                "holder": task.holder,
                "fence": task.fence,
                "controller": uuid4(),
                "guard": identity,
            },
        )
        .mappings()
        .one()
    )
    return Control(
        row["id"],
        row["owner_id"],
        row["holder"],
        row["fence"],
        row["namespace_id"],
        row["maintenance_epoch"],
        row["purpose"],
    )


def assert_control(conn: Connection, control: Control, *, quiescent: bool = True) -> None:
    row = (
        conn.execute(
            text("SELECT * FROM offerloom.assert_installation_control(:id,:holder,:fence)"),
            control.parameters,
        )
        .mappings()
        .one()
    )
    if quiescent and row["phase"] != "quiescent":
        raise Problem(409, "installation_not_quiescent")


class InstallationMaintenance:
    """One <=60s drain budget, followed by a separate bounded operation deadline.

    A timeout, crash, lost lease or uncompleted context leaves admission closed
    and spool requests in place. There is no elapsed-time recovery or ordinary
    fence override. A separately verified operator recovery must reconcile it.
    """

    def __init__(
        self, db: Database, control: Control, parser_spool: Path, renderer_spool: Path
    ) -> None:
        self.db = db
        self.control = control
        self.parser_spool = parser_spool
        self.renderer_spool = renderer_spool
        self._stop = Event()
        self._lost = Event()
        self._failure: Exception | None = None
        self._paused: tuple[PausedSpool, PausedSpool] | None = None
        self._entered = False
        self._finished = False

    def _check(self) -> None:
        if self._lost.is_set():
            raise Problem(409, "installation_control_lease_lost") from self._failure

    def _heartbeat(self) -> None:
        while not self._stop.wait(2):
            try:
                with self.db.transaction() as conn:
                    conn.execute(
                        text("SELECT offerloom.heartbeat_installation_control(:id,:holder,:fence)"),
                        self.control.parameters,
                    )
            except Exception as error:
                self._failure = error
                self._lost.set()
                return

    def _remaining(self, deadline: float) -> float:
        self._check()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Problem(409, "installation_drain_timeout")
        return remaining

    @contextmanager
    def quiescent(
        self, *, recovered_spools: tuple[PausedSpool, PausedSpool] | None = None
    ) -> Iterator[InstallationMaintenance]:
        if self._entered:
            raise Problem(409, "installation_control_already_entered")
        self._entered = True
        with self.db.transaction() as conn:
            remaining = float(
                conn.execute(
                    text("""SELECT extract(epoch FROM drain_deadline_at-clock_timestamp())
                FROM offerloom.assert_installation_control(:id,:holder,:fence)"""),
                    self.control.parameters,
                ).scalar_one()
            )
        deadline = time.monotonic() + remaining
        self._remaining(deadline)
        pulse = Thread(target=self._heartbeat, name="offerloom-maintenance-heartbeat")
        pulse.start()
        try:
            with ExitStack() as stack:
                if recovered_spools is None:
                    while True:
                        self._remaining(deadline)
                        with self.db.transaction() as conn:
                            assert_control(conn, self.control, quiescent=False)
                            pending = conn.execute(
                                text("""SELECT
                            EXISTS(SELECT 1 FROM offerloom.installation_activities)
                            OR EXISTS(SELECT 1 FROM offerloom.provider_executions)""")
                            ).scalar_one()
                        if not pending:
                            break
                        self._stop.wait(min(0.05, self._remaining(deadline)))
                    parser = stack.enter_context(
                        pause_spool(self.parser_spool, timeout=min(60.0, self._remaining(deadline)))
                    )
                    renderer = stack.enter_context(
                        pause_spool(
                            self.renderer_spool, timeout=min(60.0, self._remaining(deadline))
                        )
                    )
                    self._remaining(deadline)
                    with self.db.transaction() as conn:
                        conn.execute(
                            text(
                                "SELECT offerloom.quiesce_installation_control(:id,:holder,:fence)"
                            ),
                            self.control.parameters,
                        )
                else:
                    guard = self.db.execution_guard
                    if guard is None or not guard.exclusive:
                        raise Problem(409, "offline_recovery_exclusion_required")
                    guard.verify()
                    parser, renderer = recovered_spools
                    with self.db.transaction() as conn:
                        assert_control(conn, self.control)
                self._paused = (parser, renderer)
                yield self
        finally:
            self._paused = None
            self._stop.set()
            pulse.join()

    def finish(self, result: dict[str, object]) -> None:
        """Settle while fenced, resume BOTH exact spool receipts, then admit work."""
        self._release(result, failed_export=False)

    def fail_export(self) -> None:
        """Only an unchanged export with all temporary bytes removed may fail open."""
        if self.control.purpose != "portable_export":
            raise Problem(409, "export_failure_settlement_forbidden")
        self._release({"error_code": "portable_export_failed"}, failed_export=True)

    def _release(self, result: dict[str, object], *, failed_export: bool) -> None:
        self._check()
        if self._paused is None or self._finished:
            raise Problem(409, "installation_control_not_active")
        with self.db.transaction() as conn:
            conn.execute(
                text("""SELECT offerloom.settle_installation_control(
                :id,:holder,:fence,CAST(:result AS jsonb))"""),
                {**self.control.parameters, "result": canonical_json(result)},
            )
        for spool in self._paused:
            self._check()
            spool.resume()
        self._check()
        with self.db.transaction() as conn:
            release = (
                "SELECT offerloom.fail_portable_export_control(:id,:holder,:fence)"
                if failed_export
                else "SELECT offerloom.release_installation_control(:id,:holder,:fence)"
            )
            conn.execute(text(release), self.control.parameters)
        self._finished = True

    def abort(self) -> None:
        """Privileged pre-domain-commit abort; never report destructive success."""
        self._check()
        if self._paused is None or self._finished:
            raise Problem(409, "installation_control_not_active")
        with self.db.transaction() as conn:
            conn.execute(
                text("SELECT offerloom.abort_installation_control(:id,:holder,:fence)"),
                self.control.parameters,
            )
        for spool in self._paused:
            self._check()
            spool.resume()
        with self.db.transaction() as conn:
            conn.execute(
                text("SELECT offerloom.release_aborted_installation_control(:id,:holder,:fence)"),
                self.control.parameters,
            )
        self._finished = True


def build_router(db: Database) -> APIRouter:
    router = APIRouter()

    @router.get(MAINTENANCE_STATUS_PATH)
    def maintenance_status(
        owner: Annotated[Owner, Depends(require_readonly_owner)],
    ) -> dict[str, object]:
        # Neither authentication nor status advances sessions, takes a writer
        # fence, registers executable activity, or opens admission.
        with db.transaction() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            row = (
                conn.execute(
                    text("""SELECT i.namespace_id,i.maintenance_epoch,s.workspace_version,
                i.maintenance_active,c.id,c.purpose,c.phase,c.started_at,
                c.deadline_at,c.drain_deadline_at,
                c.lease_expires_at>clock_timestamp() AS lease_current,
                c.namespace_id=i.namespace_id AND c.maintenance_epoch=i.maintenance_epoch
                AND EXISTS(SELECT 1 FROM offerloom.workflow_runs w
                 WHERE w.owner_id=c.owner_id AND w.id=c.id AND w.state='running'
                 AND w.fence=c.workflow_fence AND w.lease_holder=c.holder
                 AND w.cancel_requested_at IS NULL) AS authority_current,
                c.deadline_at<=clock_timestamp() AS deadline_expired,
                c.drain_deadline_at<=clock_timestamp() AS drain_expired,
                (SELECT count(*) FROM offerloom.installation_activities) AS active_calls,
                (SELECT count(*) FROM offerloom.provider_executions) AS provider_executions
                FROM offerloom.installation_state i
                JOIN offerloom.owner_settings s ON s.owner_id=:owner
                LEFT JOIN offerloom.installation_controls c
                ON c.owner_id=:owner AND c.phase<>'released'
                WHERE i.singleton"""),
                    {"owner": owner.id},
                )
                .mappings()
                .one()
            )
        active = bool(row["maintenance_active"])
        recovery = active and (
            row["id"] is None
            or not row["lease_current"]
            or not row["authority_current"]
            or row["deadline_expired"]
            or row["phase"] == "draining"
            and row["drain_expired"]
        )
        operation: dict[str, object] | None = None
        if row["id"] is not None:
            operation = {
                "workflow_id": str(row["id"]),
                "purpose": row["purpose"],
                "phase": row["phase"],
                "started_at": row["started_at"],
                "deadline_at": row["deadline_at"],
                "drain_deadline_at": row["drain_deadline_at"],
            }
        return {
            "namespace_id": str(row["namespace_id"]),
            "maintenance_epoch": row["maintenance_epoch"],
            "workspace_version": row["workspace_version"],
            "maintenance_active": active,
            "operation": operation,
            "recovery_required": bool(recovery),
            "drain": {
                "active_calls": row["active_calls"],
                "provider_executions": row["provider_executions"],
            },
        }

    return router
