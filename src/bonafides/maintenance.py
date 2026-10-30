# SPDX-License-Identifier: MIT OR Apache-2.0
"""Durable admission of actual executing code, independent of workflow leases."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import Connection, text

from bonafides.commands import lock_installation
from bonafides.db import Database
from bonafides.execution_guard import ExecutionGuard
from bonafides.problems import Problem
from bonafides.workflows import Claim, assert_fence


@dataclass(frozen=True)
class Activity:
    id: UUID
    namespace_id: UUID
    maintenance_epoch: int
    participant_id: UUID


def _register(conn: Connection, kind: str, participant_id: UUID, execution_id: UUID) -> Activity:
    guard = conn.info.get("execution_guard")
    if not isinstance(guard, ExecutionGuard) or guard.exclusive:
        raise Problem(409, "guarded_runtime_required")
    identity = guard.verify()
    conn.execute(text("SELECT bonafides.bind_execution_guard(:guard)"), {"guard": identity})
    epoch = lock_installation(conn)
    namespace = conn.execute(
        text("SELECT namespace_id FROM bonafides.installation_state WHERE singleton")
    ).scalar_one()
    activity = Activity(uuid4(), namespace, epoch, participant_id)
    conn.execute(
        text("""INSERT INTO bonafides.installation_activities
        (id,namespace_id,maintenance_epoch,participant_id,kind,execution_id,execution_guard_id)
        VALUES(:id,:namespace,:epoch,:participant,:kind,:execution,:guard)"""),
        {
            "id": activity.id,
            "namespace": namespace,
            "epoch": epoch,
            "participant": participant_id,
            "kind": kind,
            "execution": execution_id,
            "guard": identity,
        },
    )
    return activity


def start_api_activity(db: Database, participant_id: UUID, request_id: UUID) -> Activity:
    with db.transaction() as conn:
        return _register(conn, "api", participant_id, request_id)


def start_worker_activity(db: Database, participant_id: UUID, task: Claim) -> Activity:
    with db.transaction() as conn:
        activity = _register(conn, "worker", participant_id, task.id)
        assert_fence(conn, task)
        return activity


def start_cleanup_activity(db: Database, participant_id: UUID) -> Activity:
    """Register actual background cleanup, not the maintenance controller itself."""
    with db.transaction() as conn:
        return _register(conn, "worker", participant_id, uuid4())


def finish_activity(db: Database, activity: Activity) -> None:
    """Delete only after the entire executing call returns, including its I/O.

    Removal is the durable, positive completion acknowledgement. A crashed call
    leaves its registration behind and blocks maintenance until independently
    verified operator recovery; elapsed time is not proof of completion.
    """
    with db.transaction() as conn:
        completed = conn.execute(
            text("""DELETE FROM bonafides.installation_activities
            WHERE id=:id AND namespace_id=:namespace AND maintenance_epoch=:epoch
              AND participant_id=:participant RETURNING id"""),
            {
                "id": activity.id,
                "namespace": activity.namespace_id,
                "epoch": activity.maintenance_epoch,
                "participant": activity.participant_id,
            },
        ).scalar_one_or_none()
        if completed is None:
            raise Problem(409, "activity_completion_not_registered")


class ProviderExecution:
    """Keep uncertain remote execution alive after local cancellation or death.

    Only the transport's complete, matching terminal envelope acknowledges remote
    completion. Lease expiry, socket close and output publication do not.
    """

    def __init__(self, db: Database, task: Claim, model_run_id: UUID) -> None:
        self.db = db
        self.task = task
        self.model_run_id = model_run_id
        self.registration: Activity | None = None

    def started(self) -> None:
        if self.registration is not None:
            raise Problem(409, "provider_execution_already_started")
        with self.db.transaction() as conn:
            guard = conn.info.get("execution_guard")
            if not isinstance(guard, ExecutionGuard) or guard.exclusive:
                raise Problem(409, "guarded_runtime_required")
            identity = guard.verify()
            conn.execute(text("SELECT bonafides.bind_execution_guard(:guard)"), {"guard": identity})
            epoch = lock_installation(conn)
            assert_fence(conn, self.task)
            namespace = conn.execute(
                text("SELECT namespace_id FROM bonafides.installation_state WHERE singleton")
            ).scalar_one()
            registration = Activity(uuid4(), namespace, epoch, uuid4())
            inserted = conn.execute(
                text("""INSERT INTO bonafides.provider_executions
                (id,namespace_id,maintenance_epoch,participant_id,owner_id,model_run_id,execution_guard_id)
                SELECT :id,:namespace,:epoch,:participant,owner_id,id,:guard
                FROM bonafides.model_runs
                WHERE owner_id=:owner AND id=:model AND workflow_run_id=:workflow
                  AND status='running' RETURNING id"""),
                {
                    "id": registration.id,
                    "namespace": namespace,
                    "epoch": epoch,
                    "participant": registration.participant_id,
                    "owner": self.task.owner_id,
                    "model": self.model_run_id,
                    "workflow": self.task.id,
                    "guard": identity,
                },
            ).scalar_one_or_none()
            if inserted is None:
                raise Problem(409, "provider_execution_model_mismatch")
        self.registration = registration

    def completed(self) -> None:
        registration = self.registration
        if registration is None:
            raise Problem(409, "provider_execution_not_started")
        with self.db.transaction() as conn:
            removed = conn.execute(
                text("""DELETE FROM bonafides.provider_executions
                WHERE id=:id AND namespace_id=:namespace AND maintenance_epoch=:epoch
                  AND participant_id=:participant AND owner_id=:owner AND model_run_id=:model
                RETURNING id"""),
                {
                    "id": registration.id,
                    "namespace": registration.namespace_id,
                    "epoch": registration.maintenance_epoch,
                    "participant": registration.participant_id,
                    "owner": self.task.owner_id,
                    "model": self.model_run_id,
                },
            ).scalar_one_or_none()
            if removed is None:
                raise Problem(409, "provider_execution_completion_not_registered")
