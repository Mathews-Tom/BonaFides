# SPDX-License-Identifier: MIT OR Apache-2.0
"""PostgreSQL durable queue and independently fenced local outbox delivery."""

import hashlib
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal, Protocol, cast
from uuid import UUID, uuid4

from alembic import command as alembic_command
from alembic.config import Config
from fastapi import APIRouter, Depends, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import make_url

from offerloom.auth import Owner, require_owner
from offerloom.commands import append_outbox, canonical_json, command, lock_installation
from offerloom.config import Settings
from offerloom.db import Database
from offerloom.problems import Problem

LEASE_SECONDS = 60


class MigrationConfig(Protocol):
    @property
    def migration_dsn(self) -> str | None: ...


def migrate(settings: MigrationConfig) -> None:
    if not settings.migration_dsn:
        raise RuntimeError("OFFERLOOM_MIGRATION_DSN_FILE is required for migration")
    root = Path.cwd()
    if not (root / "alembic.ini").is_file() or not (root / "migrations" / "sql").is_dir():
        raise RuntimeError(
            "migration requires alembic.ini and migrations/ in the working directory"
        )
    url = make_url(settings.migration_dsn)
    if url.drivername not in {"postgresql", "postgresql+psycopg"}:
        raise RuntimeError("migration requires a PostgreSQL psycopg DSN")
    engine = create_engine(
        url.set(drivername="postgresql+psycopg"),
        hide_parameters=True,
        connect_args={"connect_timeout": 5},
    )
    try:
        with engine.begin() as conn:
            version = int(conn.execute(text("SHOW server_version_num")).scalar_one())
            if not 180000 <= version < 190000:
                raise RuntimeError("PostgreSQL 18 is required")
            conn.execute(text("SET LOCAL lock_timeout='5s'"))
            conn.execute(text("SET LOCAL statement_timeout='60s'"))
            conn.execute(text("SELECT pg_advisory_xact_lock(741903221)"))
            conn.execute(text("CREATE SCHEMA IF NOT EXISTS offerloom_migrations"))
            conn.execute(
                text("REVOKE ALL ON SCHEMA offerloom_migrations FROM PUBLIC,offerloom_runtime")
            )
            config = Config(str(root / "alembic.ini"))
            config.attributes["connection"] = conn
            alembic_command.upgrade(config, "head")
            rows = conn.execute(
                text(
                    "SELECT version,source_sha256 FROM offerloom.schema_migrations ORDER BY version"
                )
            ).all()
            names = (
                "001_initial.up.sql",
                "002_hosted_consent.up.sql",
                "003_base_resume.up.sql",
                "004_runtime.up.sql",
                "005_profiles.up.sql",
                "006_imports.up.sql",
                "007_sources.up.sql",
            )
            if len(rows) != len(names):
                raise RuntimeError("unsupported migration ledger")
            for row, name in zip(rows, names, strict=True):
                expected = hashlib.sha256(
                    (root / "migrations" / "sql" / name).read_bytes()
                ).hexdigest()
                if row.version != int(name[:3]) or row.source_sha256 != expected:
                    raise RuntimeError("migration source checksum mismatch")
    finally:
        engine.dispose()


@dataclass(frozen=True)
class Claim:
    owner_id: UUID
    id: UUID
    holder: UUID
    fence: int
    attempt: int
    kind: str
    input: dict[str, object]


def enqueue(conn: Connection, owner_id: UUID, kind: str, input_snapshot: dict[str, object]) -> UUID:
    if kind not in {"document_generate", "pdf_render", "profile_import", "source_refresh"}:
        raise Problem(422, "unsupported_workflow_kind")
    epoch = lock_installation(conn)
    workflow_id = uuid4()
    pinned = dict(input_snapshot)
    pinned["schema_version"] = 1
    sha = hashlib.sha256(canonical_json(pinned).encode()).hexdigest()
    conn.execute(
        text("""WITH moment AS MATERIALIZED (SELECT clock_timestamp() AS now)
        INSERT INTO offerloom.workflow_runs(owner_id,id,kind,input,dedupe_key,request_sha256,deadline_at,maintenance_epoch,max_attempts,
        source_config_id,source_policy_version,source_policy_review_id,source_policy_sha256,created_at)
        SELECT :owner,:id,:kind,CAST(:input AS jsonb),:dedupe,:sha,now+interval '180 seconds',:epoch,:attempts,
        :source,:policy_version,:policy_review,:policy_sha,now FROM moment"""),
        {
            "owner": owner_id,
            "id": workflow_id,
            "kind": kind,
            "input": canonical_json(pinned),
            "dedupe": str(workflow_id),
            "sha": sha,
            "epoch": epoch,
            "attempts": 4 if kind == "profile_import" else 3,
            "source": pinned.get("source_id") if kind == "source_refresh" else None,
            "policy_version": pinned.get("policy_version") if kind == "source_refresh" else None,
            "policy_review": pinned.get("policy_review_id") if kind == "source_refresh" else None,
            "policy_sha": pinned.get("policy_sha256") if kind == "source_refresh" else None,
        },
    )
    append_outbox(
        conn,
        owner_id,
        "workflow.queued",
        "workflow",
        workflow_id,
        {"workflow_id": str(workflow_id)},
    )
    return workflow_id


def get_workflow(conn: Connection, owner_id: UUID, workflow_id: UUID) -> dict[str, object]:
    row = (
        conn.execute(
            text("""SELECT id,kind,version,state,attempt,max_attempts,next_attempt_at,last_error_code,result,created_at,updated_at,finished_at
        FROM offerloom.workflow_runs WHERE owner_id=:owner AND id=:id"""),
            {"owner": owner_id, "id": workflow_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise Problem(404, "workflow_not_found")
    return {
        key: str(value)
        if isinstance(value, UUID)
        else value.isoformat()
        if isinstance(value, datetime)
        else value
        for key, value in row.items()
    }


def claim(conn: Connection, holder: UUID) -> Claim | None:
    epoch = lock_installation(conn)
    row = (
        conn.execute(
            text("""WITH candidate AS MATERIALIZED (
        SELECT w.owner_id,w.id FROM offerloom.workflow_runs w
        WHERE w.maintenance_epoch=:epoch AND w.state IN ('pending','retryable_failure')
        AND w.next_attempt_at<=clock_timestamp() AND w.deadline_at>clock_timestamp()
        AND w.cancel_requested_at IS NULL AND w.attempt<w.max_attempts
        AND NOT EXISTS(SELECT 1 FROM offerloom.workflow_steps s WHERE s.owner_id=w.owner_id AND s.workflow_run_id=w.id AND s.delivery_state='unknown')
        ORDER BY w.next_attempt_at,w.created_at,w.id FOR UPDATE OF w SKIP LOCKED LIMIT 1)
        UPDATE offerloom.workflow_runs w SET state='running',lease_holder=:holder,
        lease_expires_at=clock_timestamp()+make_interval(secs=>:lease),fence=w.fence+1,
        attempt=w.attempt+1,version=w.version+1,updated_at=clock_timestamp()
        FROM candidate c WHERE w.owner_id=c.owner_id AND w.id=c.id RETURNING w.*"""),
            {"epoch": epoch, "holder": holder, "lease": LEASE_SECONDS},
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    return Claim(
        row["owner_id"], row["id"], holder, row["fence"], row["attempt"], row["kind"], row["input"]
    )


def heartbeat(conn: Connection, task: Claim) -> bool:
    epoch = lock_installation(conn)
    return (
        conn.execute(
            text("""UPDATE offerloom.workflow_runs SET
        lease_expires_at=clock_timestamp()+make_interval(secs=>:lease),updated_at=clock_timestamp()
        WHERE owner_id=:owner AND id=:id AND state='running' AND lease_holder=:holder AND fence=:fence
        AND lease_expires_at>clock_timestamp() AND cancel_requested_at IS NULL
        AND deadline_at>clock_timestamp() AND maintenance_epoch=:epoch RETURNING id"""),
            {
                "owner": task.owner_id,
                "id": task.id,
                "holder": task.holder,
                "fence": task.fence,
                "lease": LEASE_SECONDS,
                "epoch": epoch,
            },
        ).first()
        is not None
    )


def assert_fence(conn: Connection, task: Claim) -> None:
    conn.execute(
        text("SELECT offerloom.assert_workflow_fence(:owner,:id,:holder,:fence)"),
        {"owner": task.owner_id, "id": task.id, "holder": task.holder, "fence": task.fence},
    )


def publish(
    conn: Connection, task: Claim, effect: Callable[[Connection], dict[str, object]]
) -> dict[str, object]:
    lock_installation(conn)
    conn.execute(
        text("SELECT owner_id FROM offerloom.owner_settings WHERE owner_id=:owner FOR UPDATE"),
        {"owner": task.owner_id},
    )
    assert_fence(conn, task)
    result = effect(conn)
    if task.kind == "source_refresh":
        # A bounded board may take time to persist; an expired attempt never commits it.
        assert_fence(conn, task)
    conn.execute(
        text("""INSERT INTO offerloom.workflow_steps(owner_id,workflow_run_id,step_key,attempt,fence,state,request_sha256,checkpoint,completed_at)
        SELECT owner_id,id,'publish',attempt,fence,'succeeded',request_sha256,CAST(:result AS jsonb),clock_timestamp()
        FROM offerloom.workflow_runs WHERE owner_id=:owner AND id=:id"""),
        {"owner": task.owner_id, "id": task.id, "result": canonical_json(result)},
    )
    version = conn.execute(
        text("""UPDATE offerloom.workflow_runs SET state='succeeded',result=CAST(:result AS jsonb),lease_holder=NULL,
        lease_expires_at=NULL,finished_at=clock_timestamp(),version=version+1,updated_at=clock_timestamp(),last_error_code=NULL
        WHERE owner_id=:owner AND id=:id AND fence=:fence RETURNING version"""),
        {
            "owner": task.owner_id,
            "id": task.id,
            "fence": task.fence,
            "result": canonical_json(result),
        },
    ).scalar_one()
    conn.execute(
        text(
            "UPDATE offerloom.owner_settings SET workspace_version=workspace_version+1 WHERE owner_id=:owner"
        ),
        {"owner": task.owner_id},
    )
    append_outbox(
        conn,
        task.owner_id,
        "workflow.succeeded",
        "workflow",
        task.id,
        {"workflow_id": str(task.id)},
        int(version),
    )
    return result


def fail(
    conn: Connection,
    task: Claim,
    code: str,
    transient: bool,
    minimum_retry_delay_seconds: int | None = None,
) -> None:
    if minimum_retry_delay_seconds is not None and minimum_retry_delay_seconds < 0:
        raise ValueError("negative minimum retry delay")
    assert_fence(conn, task)
    row = (
        conn.execute(
            text(
                """SELECT error_fingerprint,repeated_error_count,max_attempts,
                extract(epoch FROM deadline_at-clock_timestamp()) AS remaining_seconds
                FROM offerloom.workflow_runs WHERE owner_id=:owner AND id=:id"""
            ),
            {"owner": task.owner_id, "id": task.id},
        )
        .mappings()
        .one()
    )
    repeated = int(row["repeated_error_count"]) + 1 if row["error_fingerprint"] == code else 1
    poison = code == "benchmark_poison" and repeated >= 2
    retry = transient and not poison and task.attempt < int(row["max_attempts"])
    delay = max(
        random.uniform(0, min(300, 2**task.attempt)),
        minimum_retry_delay_seconds or 0,
    )
    if minimum_retry_delay_seconds is not None and delay >= float(row["remaining_seconds"]):
        retry = False
    if minimum_retry_delay_seconds is not None and not retry:
        # A terminal run has no resend time; the source retains its full provider pause.
        delay = 0.0
    state = "retryable_failure" if retry else "permanent_failure"
    version = conn.execute(
        text("""UPDATE offerloom.workflow_runs SET state=CAST(:state AS offerloom.workflow_state),last_error_code=:code,
        error_fingerprint=:code,repeated_error_count=:count,next_attempt_at=clock_timestamp()+make_interval(secs=>:delay),
        lease_holder=NULL,lease_expires_at=NULL,finished_at=CASE WHEN :retry THEN NULL ELSE clock_timestamp() END,
        version=version+1,updated_at=clock_timestamp() WHERE owner_id=:owner AND id=:id RETURNING version"""),
        {
            "state": state,
            "code": code,
            "count": repeated,
            "delay": delay,
            "retry": retry,
            "owner": task.owner_id,
            "id": task.id,
        },
    ).scalar_one()
    append_outbox(
        conn,
        task.owner_id,
        "workflow." + state,
        "workflow",
        task.id,
        {
            "workflow_id": str(task.id),
            "error_code": code,
            **(
                {
                    "source_id": str(task.input["source_id"]),
                    "policy_version": task.input["policy_version"],
                }
                if task.kind == "source_refresh"
                else {}
            ),
        },
        int(version),
    )


def reap(conn: Connection) -> int:
    epoch = lock_installation(conn)
    rows = (
        conn.execute(
            text("""SELECT w.*,EXISTS(SELECT 1 FROM offerloom.workflow_steps s
        WHERE s.owner_id=w.owner_id AND s.workflow_run_id=w.id AND s.delivery_state='unknown') AS unknown_delivery,
        (w.deadline_at<=clock_timestamp()) AS deadline_passed
        FROM offerloom.workflow_runs w WHERE w.maintenance_epoch=:epoch
        AND w.state IN ('pending','retryable_failure','running','waiting_for_approval')
        AND (w.cancel_requested_at IS NOT NULL OR ((w.state<>'waiting_for_approval' OR w.kind='profile_import') AND w.deadline_at<=clock_timestamp())
        OR (w.state='running' AND w.lease_expires_at<=clock_timestamp()))
        ORDER BY w.created_at,w.id FOR UPDATE OF w SKIP LOCKED LIMIT 100"""),
            {"epoch": epoch},
        )
        .mappings()
        .all()
    )
    for row in rows:
        if row["cancel_requested_at"] is not None:
            state, code = "cancelled", "cancelled"
        elif row["unknown_delivery"]:
            state, code = "waiting_for_approval", "delivery_reconciliation_required"
        elif row["deadline_passed"] or row["attempt"] >= row["max_attempts"]:
            state, code = (
                "permanent_failure",
                "import_review_expired"
                if row["kind"] == "profile_import" and row["state"] == "waiting_for_approval"
                else "deadline_exceeded"
                if row["deadline_passed"]
                else "attempts_exhausted",
            )
        else:
            state, code = "retryable_failure", "worker_interrupted"
        terminal = state in {"cancelled", "permanent_failure"}
        version = conn.execute(
            text("""UPDATE offerloom.workflow_runs SET state=CAST(:state AS offerloom.workflow_state),
            lease_holder=NULL,lease_expires_at=NULL,fence=fence+1,last_error_code=:code,version=version+1,
            next_attempt_at=clock_timestamp()+interval '2 seconds',finished_at=CASE WHEN :terminal THEN clock_timestamp() ELSE NULL END,
            updated_at=clock_timestamp() WHERE owner_id=:owner AND id=:id RETURNING version"""),
            {
                "state": state,
                "code": code,
                "terminal": terminal,
                "owner": row["owner_id"],
                "id": row["id"],
            },
        ).scalar_one()
        append_outbox(
            conn,
            row["owner_id"],
            "workflow." + state,
            "workflow",
            row["id"],
            {"workflow_id": str(row["id"]), "error_code": code},
            int(version),
        )
    return len(rows)


@dataclass(frozen=True)
class OutboxClaim:
    owner_id: UUID
    id: UUID
    holder: UUID
    fence: int


def claim_outbox(conn: Connection, holder: UUID) -> OutboxClaim | None:
    lock_installation(conn)
    row = (
        conn.execute(
            text("""WITH candidate AS MATERIALIZED (
        SELECT owner_id,id FROM offerloom.outbox_events WHERE delivered_at IS NULL
        AND available_at<=clock_timestamp() AND (lease_expires_at IS NULL OR lease_expires_at<=clock_timestamp())
        ORDER BY available_at,created_at,id FOR UPDATE SKIP LOCKED LIMIT 1)
        UPDATE offerloom.outbox_events e SET lease_holder=:holder,lease_expires_at=clock_timestamp()+interval '60 seconds',
        fence=e.fence+1,attempt=e.attempt+1 FROM candidate c WHERE e.owner_id=c.owner_id AND e.id=c.id RETURNING e.owner_id,e.id,e.fence"""),
            {"holder": holder},
        )
        .mappings()
        .first()
    )
    return None if row is None else OutboxClaim(row["owner_id"], row["id"], holder, row["fence"])


def deliver_outbox(conn: Connection, event: OutboxClaim) -> bool:
    lock_installation(conn)
    row = (
        conn.execute(
            text("""SELECT event_type,aggregate_id FROM offerloom.outbox_events WHERE owner_id=:owner AND id=:id
        AND lease_holder=:holder AND fence=:fence AND lease_expires_at>clock_timestamp() AND delivered_at IS NULL FOR UPDATE"""),
            {"owner": event.owner_id, "id": event.id, "holder": event.holder, "fence": event.fence},
        )
        .mappings()
        .first()
    )
    if row is None:
        return False
    inserted = conn.execute(
        text("""INSERT INTO offerloom.event_receipts(owner_id,consumer_key,event_id)
        VALUES(:owner,'local-metadata-v1',:id) ON CONFLICT DO NOTHING RETURNING event_id"""),
        {"owner": event.owner_id, "id": event.id},
    ).first()
    if inserted is not None:
        conn.execute(
            text(
                "INSERT INTO offerloom.event_metadata(owner_id,event_id,event_type,aggregate_id) VALUES(:owner,:id,:type,:aggregate)"
            ),
            {
                "owner": event.owner_id,
                "id": event.id,
                "type": row["event_type"],
                "aggregate": row["aggregate_id"],
            },
        )
    conn.execute(
        text(
            "UPDATE offerloom.outbox_events SET delivered_at=clock_timestamp(),lease_holder=NULL,lease_expires_at=NULL WHERE owner_id=:owner AND id=:id AND fence=:fence"
        ),
        {"owner": event.owner_id, "id": event.id, "fence": event.fence},
    )
    return True


def cancel_workflow(conn: Connection, owner_id: UUID, workflow_id: UUID) -> dict[str, object]:
    lock_installation(conn)
    conn.execute(
        text("SELECT id FROM offerloom.workflow_runs WHERE owner_id=:owner AND id=:id FOR UPDATE"),
        {"owner": owner_id, "id": workflow_id},
    )
    row = get_workflow(conn, owner_id, workflow_id)
    if row["state"] not in {"succeeded", "permanent_failure", "cancelled"}:
        version = conn.execute(
            text("""UPDATE offerloom.workflow_runs SET state='cancelled',cancel_requested_at=clock_timestamp(),
            fence=fence+1,lease_holder=NULL,lease_expires_at=NULL,finished_at=clock_timestamp(),version=version+1,
            last_error_code='cancelled',updated_at=clock_timestamp() WHERE owner_id=:owner AND id=:id RETURNING version"""),
            {"owner": owner_id, "id": workflow_id},
        ).scalar_one()
        append_outbox(
            conn,
            owner_id,
            "workflow.cancelled",
            "workflow",
            workflow_id,
            {"workflow_id": str(workflow_id)},
            int(version),
        )
    return get_workflow(conn, owner_id, workflow_id)


class BenchmarkTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_revision_id: UUID
    scenario: Literal["normal", "slow", "poison", "stage_pause"] = "normal"
    delay_seconds: int = Field(default=0, ge=0, le=120)


class CancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


def build_router(db: Database, settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.post("/benchmarks/tasks")
    def benchmark_task(
        body: BenchmarkTask,
        owner: Annotated[Owner, Depends(require_owner)],
        if_match: Annotated[str | None, Header()] = None,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        if settings.mode != "benchmark":
            raise Problem(404, "not_found")
        with db.transaction() as conn:
            profile = conn.execute(
                text(
                    "SELECT profile_id FROM offerloom.profile_revisions WHERE owner_id=:owner AND id=:id"
                ),
                {"owner": owner.id, "id": body.profile_revision_id},
            ).scalar_one_or_none()
            if profile is None:
                raise Problem(404, "profile_revision_not_found")
            snapshot = cast(dict[str, object], body.model_dump(mode="json"))

            def effect(active: Connection) -> tuple[int, dict[str, object]]:
                workflow_id = enqueue(active, owner.id, "document_generate", snapshot)
                return 202, get_workflow(active, owner.id, workflow_id)

            status, result = command(
                conn,
                owner.id,
                "POST",
                "/benchmarks/tasks",
                {},
                snapshot,
                if_match,
                idempotency_key,
                "profile",
                profile,
                effect,
            )
        return JSONResponse(
            result, status_code=status, headers={"Location": f"/api/v1/workflows/{result['id']}"}
        )

    @router.get("/workflows")
    def list_workflows(owner: Annotated[Owner, Depends(require_owner)]) -> dict[str, object]:
        with db.transaction() as conn:
            ids = (
                conn.execute(
                    text(
                        "SELECT id FROM offerloom.workflow_runs WHERE owner_id=:owner ORDER BY created_at DESC,id LIMIT 100"
                    ),
                    {"owner": owner.id},
                )
                .scalars()
                .all()
            )
            return {"items": [get_workflow(conn, owner.id, identifier) for identifier in ids]}

    @router.get("/workflows/{workflow_id}")
    def workflow(
        workflow_id: UUID, owner: Annotated[Owner, Depends(require_owner)]
    ) -> JSONResponse:
        with db.transaction() as conn:
            result = get_workflow(conn, owner.id, workflow_id)
        return JSONResponse(result, headers={"ETag": f'"{result["version"]}"'})

    @router.post("/workflows/{workflow_id}/cancel")
    def cancel(
        workflow_id: UUID,
        body: CancelRequest,
        owner: Annotated[Owner, Depends(require_owner)],
        if_match: Annotated[str | None, Header()] = None,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        def effect(conn: Connection) -> tuple[int, dict[str, object]]:
            return 200, cancel_workflow(conn, owner.id, workflow_id)

        with db.transaction() as conn:
            status, result = command(
                conn,
                owner.id,
                "POST",
                "/workflows/{workflow_id}/cancel",
                {"workflow_id": str(workflow_id)},
                body.model_dump(mode="json"),
                if_match,
                idempotency_key,
                "workflow",
                workflow_id,
                effect,
            )
        return JSONResponse(result, status_code=status, headers={"ETag": f'"{result["version"]}"'})

    @router.get("/status")
    def status(owner: Annotated[Owner, Depends(require_owner)]) -> dict[str, object]:
        with db.transaction() as conn:
            queue = (
                conn.execute(
                    text("""SELECT count(*) FILTER(WHERE state IN ('pending','retryable_failure')) AS queued,
                count(*) FILTER(WHERE state='running') AS running,count(*) FILTER(WHERE state='permanent_failure') AS failed,
                coalesce(max(extract(epoch FROM clock_timestamp()-created_at)) FILTER(WHERE state IN ('pending','retryable_failure')),0) AS queue_age_seconds,
                coalesce(sum(attempt),0) AS attempts FROM offerloom.workflow_runs WHERE owner_id=:owner"""),
                    {"owner": owner.id},
                )
                .mappings()
                .one()
            )
            workers = (
                conn.execute(
                    text(
                        "SELECT count(*) AS active,extract(epoch FROM clock_timestamp()-max(heartbeat_at)) AS heartbeat_age_seconds FROM offerloom.worker_heartbeats WHERE heartbeat_at>clock_timestamp()-interval '60 seconds'"
                    )
                )
                .mappings()
                .one()
            )
            outbox = (
                conn.execute(
                    text(
                        "SELECT count(*) AS pending,coalesce(max(extract(epoch FROM clock_timestamp()-created_at)),0) AS lag_seconds FROM offerloom.outbox_events WHERE owner_id=:owner AND delivered_at IS NULL"
                    ),
                    {"owner": owner.id},
                )
                .mappings()
                .one()
            )
        return {
            "queue": {key: float(value) for key, value in queue.items()},
            "worker": {
                key: float(value) if value is not None else None for key, value in workers.items()
            },
            "outbox": {key: float(value) for key, value in outbox.items()},
        }

    return router
