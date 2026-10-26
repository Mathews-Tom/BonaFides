# SPDX-License-Identifier: MIT OR Apache-2.0
"""Expire terminal frozen task/model input, never committed owner content.

The fixed policy is 24 hours after workflow ``finished_at``; extensions are not
supported. The lifecycle controller checks once a minute, processes at most 100
workflows per call, and starts a new completed sweep after 24 hours. A partial
batch or failure is retried on the next minute, not reported as whole-backlog
success. Each batch commits independently, so a crash resumes from remaining
unscrubbed inputs. Model prompt bodies live in workflow input; model_runs keeps
hashes, parameters and usage/provenance metadata and is deliberately unchanged.

Only validated, explicitly named top-level resource UUIDs, SHA-256 pins, the
input schema version and immutable source policy version survive. The exact
application/document/revision IDs remain usable by SQL portable scope binding.
Arbitrary strings, nested context/frame/plan/facts and unknown keys are discarded.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from offerloom.commands import canonical_json, lock_installation
from offerloom.db import Database
from offerloom.maintenance import finish_activity, start_cleanup_activity
from offerloom.problems import Problem

RETENTION_SECONDS = 24 * 60 * 60
BATCH_SIZE = 100
_ACTION = "task_input_retention"
_FAILURE_CODE = "task_input_retention_failed"

# No suffix-based allowlist: a task-controlled key can itself contain private text.
_RETAINED_INPUT = """(SELECT coalesce(jsonb_object_agg(e.key,e.value),'{}'::jsonb)
    FROM jsonb_each(w.input) AS e(key,value)
    WHERE (e.key IN (
        'application_id','document_id','revision_id','profile_revision_id',
        'current_profile_revision_id','requirements_revision_id','posting_id',
        'import_id','source_id','policy_review_id','preview_id'
    ) AND jsonb_typeof(e.value)='string'
      AND e.value #>> '{}' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
    OR (e.key IN (
        'profile_sha256','template_sha256','posting_content_sha256','source_sha256',
        'context_sha256','policy_sha256','content_sha256'
    ) AND jsonb_typeof(e.value)='string' AND e.value #>> '{}' ~ '^[0-9a-f]{64}$')
    OR (w.kind='source_refresh' AND e.key='policy_version'
        AND jsonb_typeof(e.value)='number' AND e.value #>> '{}' ~ '^[1-9][0-9]{0,18}$')
    OR (e.key='schema_version' AND e.value='1'::jsonb))"""

_ELIGIBLE = """w.owner_id=:owner
    AND w.state IN ('succeeded','cancelled','permanent_failure')
    AND w.finished_at<=clock_timestamp()-make_interval(secs=>:retention)
    AND NOT EXISTS(SELECT 1 FROM offerloom.workflow_steps s
        WHERE s.owner_id=w.owner_id AND s.workflow_run_id=w.id AND s.delivery_state='unknown')
    AND NOT EXISTS(SELECT 1 FROM offerloom.model_runs m
        JOIN offerloom.provider_executions p ON p.owner_id=m.owner_id AND p.model_run_id=m.id
        WHERE m.owner_id=w.owner_id AND m.workflow_run_id=w.id)
    AND NOT EXISTS(SELECT 1 FROM offerloom.installation_activities a WHERE a.execution_id=w.id)
    AND NOT EXISTS(SELECT 1 FROM offerloom.installation_controls c
        WHERE c.owner_id=w.owner_id AND c.id=w.id AND c.phase<>'released')"""


def _record(
    conn: Connection, owner_id: UUID, result: str, count: int, *, failure: bool = False
) -> None:
    detail: dict[str, object] = {"result": result, "processed_count": count, "policy_version": 1}
    if failure:
        detail["failure_code"] = _FAILURE_CODE
    conn.execute(
        text("""INSERT INTO offerloom.audit_events
        (owner_id,action,actor_kind,actor_id,request_id,entity_type,detail,created_at)
        VALUES(:owner,:action,'worker','lifecycle',:request,'workflow_input',
        CAST(:detail AS jsonb),clock_timestamp())"""),
        {
            "owner": owner_id,
            "action": _ACTION,
            "request": uuid4(),
            "detail": canonical_json(detail),
        },
    )


def _lock_owner(conn: Connection, owner_id: UUID) -> bool:
    lock_installation(conn)
    return (
        conn.execute(
            text("SELECT owner_id FROM offerloom.owner_settings WHERE owner_id=:owner FOR UPDATE"),
            {"owner": owner_id},
        ).scalar_one_or_none()
        is not None
    )


def _expire_owner(db: Database, owner_id: UUID, limit: int) -> tuple[int, bool]:
    with db.transaction() as conn:
        if not _lock_owner(conn, owner_id):
            return 0, False
        # Only a completed sweep suppresses work for a day. Batches and failures
        # stay due, including a failure after an earlier successful sweep.
        latest = (
            conn.execute(
                text("""SELECT detail->>'result' AS result,
            created_at>clock_timestamp()-make_interval(secs=>:retention) AS recent
            FROM offerloom.audit_events WHERE owner_id=:owner AND action=:action
            ORDER BY created_at DESC,id DESC LIMIT 1"""),
                {"owner": owner_id, "action": _ACTION, "retention": RETENTION_SECONDS},
            )
            .mappings()
            .one_or_none()
        )
        if latest is not None and latest["result"] == "succeeded" and latest["recent"]:
            return 0, False
        parameters = {"owner": owner_id, "retention": RETENTION_SECONDS, "limit": limit}
        rows = conn.execute(
            text(f"""SELECT w.id,retained.input FROM offerloom.workflow_runs w
            CROSS JOIN LATERAL (SELECT {_RETAINED_INPUT} AS input) retained
            WHERE {_ELIGIBLE} AND w.input<>retained.input
            ORDER BY w.finished_at,w.id LIMIT :limit FOR UPDATE OF w"""),
            parameters,
        ).all()
        for identifier, retained in rows:
            conn.execute(
                text("""UPDATE offerloom.workflow_runs SET input=CAST(:input AS jsonb),
                version=version+1,updated_at=clock_timestamp() WHERE owner_id=:owner AND id=:id"""),
                {"owner": owner_id, "id": identifier, "input": canonical_json(retained)},
            )
        pending = bool(
            conn.execute(
                text(f"""SELECT EXISTS(SELECT 1 FROM offerloom.workflow_runs w
                WHERE {_ELIGIBLE} AND w.input<>{_RETAINED_INPUT})"""),
                parameters,
            ).scalar_one()
        )
        _record(conn, owner_id, "batch" if pending else "succeeded", len(rows))
        return len(rows), pending


def _record_failure(db: Database, owner_id: UUID) -> None:
    # Separate from the rolled-back batch. Admission and owner locks still apply:
    # an unavailable DB or maintenance barrier must fail visibly to the caller,
    # never be bypassed merely to manufacture a status receipt.
    with db.transaction() as conn:
        if _lock_owner(conn, owner_id):
            _record(conn, owner_id, "failed", 0, failure=True)


def expire_task_inputs(db: Database, *, batch_size: int = BATCH_SIZE) -> dict[str, object]:
    """Run one guarded bounded batch; raise failures after durable safe audit.

    Owner settings use the same installation/owner/workflow lock order as ordinary
    writers. Positive cleanup registration and the shared admission lock prevent
    an installation snapshot or erasure racing a commit-capable retention batch.
    """
    if not 1 <= batch_size <= BATCH_SIZE:
        raise ValueError("task retention batch size out of range")
    activity = start_cleanup_activity(db, uuid4())
    owners: list[UUID] = []
    count = 0
    pending = False
    try:
        with db.transaction() as conn:
            lock_installation(conn)
            owners = list(
                conn.execute(
                    text(
                        "SELECT owner_id FROM offerloom.owner_settings ORDER BY owner_id LIMIT :limit"
                    ),
                    {"limit": BATCH_SIZE},
                ).scalars()
            )
        for owner_id in owners:
            if count == batch_size:
                pending = True
                break
            try:
                processed, remaining = _expire_owner(db, owner_id, batch_size - count)
            except (DBAPIError, Problem, OSError, ValueError):
                _record_failure(db, owner_id)
                raise
            count += processed
            pending = pending or remaining
    finally:
        try:
            finish_activity(db, activity)
        except (DBAPIError, Problem, OSError, ValueError):
            for owner_id in owners:
                _record_failure(db, owner_id)
            raise
    return {"processed_count": count, "has_more": pending}


def task_retention_status(conn: Connection, owner_id: UUID) -> dict[str, object]:
    """Return only owner-scoped timestamps, safe counts/codes and fixed policy."""
    latest = conn.execute(
        text("""SELECT detail FROM offerloom.audit_events WHERE owner_id=:owner AND action=:action
        ORDER BY created_at DESC,id DESC LIMIT 1"""),
        {"owner": owner_id, "action": _ACTION},
    ).scalar_one_or_none()
    times = (
        conn.execute(
            text("""SELECT max(created_at) FILTER(WHERE detail->>'result'='succeeded') AS success,
        max(created_at) FILTER(WHERE detail->>'result'='failed') AS failure
        FROM offerloom.audit_events WHERE owner_id=:owner AND action=:action"""),
            {"owner": owner_id, "action": _ACTION},
        )
        .mappings()
        .one()
    )
    result = latest.get("result") if isinstance(latest, dict) else None
    count = latest.get("processed_count") if isinstance(latest, dict) else None
    success, failure = times["success"], times["failure"]
    return {
        "state": {"batch": "in_progress", "succeeded": "succeeded", "failed": "failed"}.get(
            result if isinstance(result, str) else "", "not_run"
        ),
        "last_success_at": success.isoformat() if isinstance(success, datetime) else None,
        "last_failure_at": failure.isoformat() if isinstance(failure, datetime) else None,
        "failure_code": _FAILURE_CODE if failure is not None else None,
        "processed_count": count if type(count) is int and 0 <= count <= BATCH_SIZE else 0,
        "policy": {
            "terminal_input_retention_seconds": RETENTION_SECONDS,
            "sweep_interval_seconds": RETENTION_SECONDS,
            "batch_retry_interval_seconds": 60,
            "batch_size": BATCH_SIZE,
            "extension_supported": False,
        },
    }
