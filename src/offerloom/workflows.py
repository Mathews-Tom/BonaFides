# SPDX-License-Identifier: MIT OR Apache-2.0
"""PostgreSQL durable queue and independently fenced local outbox delivery."""

import base64
import binascii
import hashlib
import hmac
import json
import random
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal, Protocol, cast
from uuid import UUID, uuid4

from alembic import command as alembic_command
from alembic.config import Config
from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import RowMapping, make_url

from offerloom.auth import Owner, require_owner
from offerloom.commands import append_outbox, canonical_json, command, lock_installation
from offerloom.config import Settings
from offerloom.crypto import Keyring
from offerloom.db import Database
from offerloom.problems import Problem

LEASE_SECONDS = 60
WorkflowState = Literal[
    "pending",
    "running",
    "waiting_for_approval",
    "retryable_failure",
    "permanent_failure",
    "succeeded",
    "cancelled",
]
_CURSOR_PURPOSE = b"offerloom/workflows/keyset/v1"
_REPLAY_SAFE_KINDS = frozenset(
    {
        "document_generate",
        "pdf_render",
        "profile_import",
        "source_refresh",
        "posting_extract",
        "matching",
    }
)
_ATTEMPT_EVENTS = frozenset(
    {
        "claimed",
        "retry_requested",
        "retryable_failure",
        "permanent_failure",
        "cancelled",
        "succeeded",
        "interrupted",
        "waiting_for_approval",
        "approved",
        "approval_expired",
    }
)


def _safe_code(value: object) -> str | None:
    if value is None:
        return None
    return (
        value
        if isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,99}", value)
        else "workflow_error"
    )


def append_attempt_event(
    conn: Connection,
    owner_id: UUID,
    workflow_id: UUID,
    event: str,
    error_code: str | None = None,
) -> None:
    """Record an actual checkpoint in the caller's fenced transaction."""
    if event not in _ATTEMPT_EVENTS:
        raise ValueError("unsupported workflow attempt event")
    conn.execute(
        text("""INSERT INTO offerloom.workflow_attempt_events
        (owner_id,workflow_run_id,attempt,fence,event,error_code)
        SELECT owner_id,id,attempt,fence,:event,:code FROM offerloom.workflow_runs
        WHERE owner_id=:owner AND id=:id"""),
        {"owner": owner_id, "id": workflow_id, "event": event, "code": _safe_code(error_code)},
    )


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
                "008_catalog.up.sql",
                "009_suitability.up.sql",
                "010_tracking.up.sql",
                "011_documents.up.sql",
                "012_section_regeneration.up.sql",
                "013_task_controls.up.sql",
                "014_activity_admission.up.sql",
                "015_provider_executions.up.sql",
                "016_installation_controls.up.sql",
                "017_portable_exports.up.sql",
                "018_portable_model_history.up.sql",
                "019_backup_generations.up.sql",
                "020_portable_erasure.up.sql",
                "021_portable_scope_binding.up.sql",
                "022_portable_previews.up.sql",
                "023_offline_maintenance_recovery.up.sql",
                "024_daily_backups.up.sql",
                "025_credential_rotation.up.sql",
                "026_erasure_reservation.up.sql",
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
    if kind not in {
        "document_generate",
        "pdf_render",
        "profile_import",
        "source_refresh",
        "posting_extract",
        "matching",
        "portable_export",
        "portable_import",
        "privacy_delete",
        "backup",
    }:
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
        SELECT :owner,:id,:kind,CAST(:input AS jsonb),:dedupe,:sha,now+make_interval(secs=>:deadline),:epoch,:attempts,
        :source,:policy_version,:policy_review,:policy_sha,now FROM moment"""),
        {
            "owner": owner_id,
            "id": workflow_id,
            "kind": kind,
            "input": canonical_json(pinned),
            "dedupe": str(workflow_id),
            "sha": sha,
            "epoch": epoch,
            "attempts": 1
            if kind in {"portable_import", "privacy_delete"}
            else 2
            if kind in {"posting_extract", "matching"}
            or pinned.get("mode") in {"tailored_ai", "section_regeneration"}
            else 4
            if kind == "profile_import"
            else 3,
            "deadline": 900
            if kind == "backup"
            else 480
            if kind in {"posting_extract", "matching"}
            or pinned.get("mode") in {"tailored_ai", "section_regeneration"}
            else 180,
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


def source_block_reason(conn: Connection, row: Mapping[str, object]) -> str | None:
    snapshot = cast(dict[str, object], row["input"])
    owner_id = row["owner_id"]
    if row["kind"] == "source_refresh":
        source = (
            conn.execute(
                text("""SELECT s.enabled,s.removed_at,s.policy_version,s.current_policy_review_id,r.policy_sha256,
            s.rate_limit_blocked,s.next_allowed_at>clock_timestamp() AS paused FROM offerloom.source_configs s
            LEFT JOIN offerloom.source_policy_reviews r ON r.owner_id=s.owner_id AND r.id=s.current_policy_review_id
            WHERE s.owner_id=:owner AND s.id=:id"""),
                {"owner": owner_id, "id": row["source_config_id"]},
            )
            .mappings()
            .first()
        )
        if source is None or source["removed_at"] is not None or not source["enabled"]:
            return "source_unavailable"
        if (
            source["policy_version"] != row["source_policy_version"]
            or source["current_policy_review_id"] != row["source_policy_review_id"]
            or source["policy_sha256"] != row["source_policy_sha256"]
        ):
            return "source_policy_changed"
        if source["rate_limit_blocked"] or source["paused"]:
            return "source_retry_after"
    checks = {
        "posting_id": """SELECT id FROM offerloom.job_postings
            WHERE owner_id=:owner AND id=:id AND status<>'closed'""",
        "profile_revision_id": """SELECT r.id FROM offerloom.profile_revisions r
            JOIN offerloom.profiles p ON p.owner_id=r.owner_id AND p.id=r.profile_id
            WHERE r.owner_id=:owner AND r.id=:id""",
        "current_profile_revision_id": """SELECT id FROM offerloom.profiles
            WHERE owner_id=:owner AND current_revision_id=:id""",
        "application_id": """SELECT id FROM offerloom.applications
            WHERE owner_id=:owner AND id=:id AND archived_at IS NULL AND current_stage<>'withdrawn'""",
        "document_id": """SELECT id FROM offerloom.document_drafts
            WHERE owner_id=:owner AND id=:id AND deleted_at IS NULL""",
        "import_id": """SELECT id FROM offerloom.import_batches
            WHERE owner_id=:owner AND id=:id AND merged_profile_revision_id IS NULL
            AND (review_expires_at IS NULL OR review_expires_at>clock_timestamp())""",
    }
    if "current_profile_revision_id" not in snapshot:
        checks["profile_revision_id"] += " AND p.current_revision_id=r.id"
    for key, sql in checks.items():
        if key not in snapshot:
            continue
        try:
            identifier = UUID(str(snapshot[key]))
        except ValueError:
            return "source_unavailable"
        if conn.execute(text(sql), {"owner": owner_id, "id": identifier}).first() is None:
            return "source_unavailable"
    if row["kind"] == "profile_import" and "import_id" in snapshot:
        batch = (
            conn.execute(
                text("""SELECT review FROM offerloom.import_batches
            WHERE owner_id=:owner AND id=:id AND workflow_run_id=:workflow"""),
                {"owner": owner_id, "id": UUID(str(snapshot["import_id"])), "workflow": row["id"]},
            )
            .mappings()
            .first()
        )
        if batch is None:
            return "source_unavailable"
        if batch["review"] is None and cast(int, row["attempt"]) >= 3:
            return "import_parse_attempts_exhausted"
    # An archived/edited evidence item cannot be resurrected by queue controls.
    if "profile_revision_id" in snapshot:
        withdrawn = conn.execute(
            text("""SELECT EXISTS(
            SELECT 1 FROM offerloom.profile_revision_claims l
            JOIN offerloom.claim_revisions r ON r.owner_id=l.owner_id AND r.id=l.claim_revision_id
            JOIN offerloom.professional_claims c ON c.owner_id=r.owner_id AND c.id=r.claim_id
            WHERE l.owner_id=:owner AND l.profile_revision_id=:revision
            AND (c.archived_at IS NOT NULL OR c.current_revision_id<>r.id))
            OR EXISTS(
            SELECT 1 FROM offerloom.profile_revision_claims l
            JOIN offerloom.claim_evidence_links x ON x.owner_id=l.owner_id AND x.claim_revision_id=l.claim_revision_id
            JOIN offerloom.evidence_revisions r ON r.owner_id=x.owner_id AND r.id=x.evidence_revision_id
            JOIN offerloom.evidence_records e ON e.owner_id=r.owner_id AND e.id=r.evidence_id
            WHERE l.owner_id=:owner AND l.profile_revision_id=:revision
            AND (e.archived_at IS NOT NULL OR e.current_revision_id<>r.id))"""),
            {"owner": owner_id, "revision": UUID(str(snapshot["profile_revision_id"]))},
        ).scalar_one()
        if withdrawn:
            return "source_withdrawn"
    if row["kind"] == "document_generate" and "profile_revision_id" in snapshot:
        from offerloom.document_generation import _fact_data

        try:
            _fact_data(conn, cast(UUID, owner_id), UUID(str(snapshot["profile_revision_id"])))
        except Problem:
            return "source_withdrawn"
    if row["kind"] == "pdf_render":
        from offerloom.documents import prepare_render

        try:
            prepared = prepare_render(
                conn,
                cast(UUID, owner_id),
                UUID(str(snapshot["document_id"])),
                UUID(str(snapshot["revision_id"])),
            )
            if prepared.content_sha256 != snapshot.get("content_sha256"):
                return "source_changed"
        except (Problem, KeyError, ValueError):
            return "source_unavailable"
    return None


def _retry_block_reason(conn: Connection, row: RowMapping) -> str | None:
    if row["unknown_delivery"]:
        return "delivery_reconciliation_required"
    if row["state"] != "retryable_failure" or row["finished_at"] is not None:
        return "workflow_not_retryable"
    if row["kind"] not in _REPLAY_SAFE_KINDS:
        return "workflow_not_replay_safe"
    if row["cancel_requested_at"] is not None:
        return "workflow_cancelled"
    if row["maintenance_epoch"] != row["current_epoch"]:
        return "workflow_namespace_changed"
    if row["attempt"] >= row["max_attempts"]:
        return "attempts_exhausted"
    if row["deadline_passed"]:
        return "deadline_exceeded"
    if not row["retry_due"]:
        return "retry_not_due"
    snapshot = cast(dict[str, object], row["input"])
    model_task = row["kind"] in {"posting_extract", "matching"} or snapshot.get("mode") in {
        "tailored_ai",
        "section_regeneration",
    }
    if model_task and row["model_network_attempts"] >= 2:
        return "model_attempts_exhausted"
    if (row["token_budget"] > 0 and row["tokens_used"] >= row["token_budget"]) or (
        row["cost_budget_microunits"] > 0
        and row["cost_used_microunits"] >= row["cost_budget_microunits"]
    ):
        return "workflow_budget_exhausted"
    return source_block_reason(conn, cast(Mapping[str, object], row))


def _require_safe_numbers(value: object) -> None:
    if (type(value) is int and abs(value) > 9007199254740991) or isinstance(value, float):
        raise Problem(409, "workflow_number_not_exact")
    if isinstance(value, dict):
        for item in value.values():
            _require_safe_numbers(item)
    elif isinstance(value, list):
        for item in value:
            _require_safe_numbers(item)


def _wire(values: Mapping[str, object]) -> dict[str, object]:
    result = {
        key: str(value)
        if isinstance(value, UUID)
        else value.isoformat()
        if isinstance(value, datetime)
        else value
        for key, value in values.items()
    }
    _require_safe_numbers(result)
    return result


def get_workflow(conn: Connection, owner_id: UUID, workflow_id: UUID) -> dict[str, object]:
    # Retry eligibility can lock profile facts or prepare a PDF; serialize with writers first.
    lock_installation(conn)
    conn.execute(
        text("SELECT owner_id FROM offerloom.owner_settings WHERE owner_id=:owner FOR UPDATE"),
        {"owner": owner_id},
    )
    row = (
        conn.execute(
            text("""SELECT w.*,i.maintenance_epoch AS current_epoch,
        w.deadline_at<=clock_timestamp() AS deadline_passed,
        w.next_attempt_at<=clock_timestamp() AS retry_due,
        CASE WHEN w.state IN ('pending','retryable_failure')
        THEN greatest(0,floor(extract(epoch FROM clock_timestamp()-w.created_at)))::bigint
        ELSE NULL END AS queue_age_seconds,
        EXISTS(SELECT 1 FROM offerloom.workflow_steps s WHERE s.owner_id=w.owner_id
        AND s.workflow_run_id=w.id AND s.delivery_state='unknown') AS unknown_delivery,
        (SELECT s.delivery_state FROM offerloom.workflow_steps s WHERE s.owner_id=w.owner_id
        AND s.workflow_run_id=w.id AND s.delivery_state<>'not_attempted'
        ORDER BY s.created_at DESC,s.id DESC LIMIT 1) AS latest_delivery
        FROM offerloom.workflow_runs w CROSS JOIN offerloom.installation_state i
        WHERE w.owner_id=:owner AND w.id=:id AND i.singleton"""),
            {"owner": owner_id, "id": workflow_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise Problem(404, "workflow_not_found")
    result = _wire(
        {
            key: row[key]
            for key in (
                "id",
                "kind",
                "version",
                "state",
                "attempt",
                "max_attempts",
                "next_attempt_at",
                "result",
                "created_at",
                "updated_at",
                "finished_at",
                "deadline_at",
                "cancel_requested_at",
                "lease_expires_at",
                "last_heartbeat_at",
                "queue_age_seconds",
            )
        }
    )
    result["last_error_code"] = _safe_code(row["last_error_code"])
    result["remaining_attempts"] = max(0, row["max_attempts"] - row["attempt"])
    result["cancelable"] = row["state"] not in {"succeeded", "permanent_failure", "cancelled"}
    reason = _retry_block_reason(conn, row)
    result["retryable"] = reason is None
    result["retry_block_reason"] = reason
    result["delivery_state"] = (
        "unknown" if row["unknown_delivery"] else row["latest_delivery"] or "not_attempted"
    )
    usage = (
        conn.execute(
            text("""SELECT count(*) AS model_runs,
        count(*) FILTER(WHERE usage_status<>'known' OR input_tokens IS NULL OR output_tokens IS NULL
            OR status IN ('reserved','running','unknown_usage')) AS unknown_usage,
        count(*) FILTER(WHERE actual_cost_microunits IS NULL) AS unknown_cost,
        sum(input_tokens) AS input_tokens,sum(output_tokens) AS output_tokens,
        sum(actual_cost_microunits) AS actual_cost
        FROM offerloom.model_runs WHERE owner_id=:owner AND workflow_run_id=:id"""),
            {"owner": owner_id, "id": workflow_id},
        )
        .mappings()
        .one()
    )
    count = int(usage["model_runs"])
    known = count > 0 and usage["unknown_usage"] == 0
    snapshot = row["input"]
    model_task = row["kind"] in {"posting_extract", "matching"} or snapshot.get("mode") in {
        "tailored_ai",
        "section_regeneration",
    }
    result["usage"] = {
        "status": "not_applicable"
        if count == 0 and row["model_network_attempts"] == 0
        else "known"
        if known
        else "unknown",
        "model_runs": count,
        "inference_post_attempts": row["model_network_attempts"],
        "inference_post_limit": 2 if model_task else None,
        "input_tokens": int(usage["input_tokens"]) if known else None,
        "output_tokens": int(usage["output_tokens"]) if known else None,
        "token_budget": row["token_budget"],
        "tokens_used": row["tokens_used"],
        "cost_budget_microunits": row["cost_budget_microunits"],
        "cost_used_microunits": int(usage["actual_cost"])
        if count and usage["unknown_cost"] == 0
        else None,
        "currency": row["budget_currency"],
    }
    settled = row["state"] == "cancelled"
    result["cancellation"] = {
        "requested": row["cancel_requested_at"] is not None,
        "local_fence_settled": settled,
        "provider_stopped": None,
        "completed_effects_preserved": True,
        "reason": "local_publication_revoked_provider_may_continue" if settled else None,
    }
    if row["kind"] == "document_generate" and row["input"].get("mode") in {
        "deterministic_base",
        "tailored_ai",
        "section_regeneration",
    }:
        result["mode"] = row["input"]["mode"]
    if row["kind"] in {"posting_extract", "matching"}:
        snapshot = row["input"]
        pins = {
            key: snapshot.get(key)
            for key in (
                "posting_id",
                "profile_revision_id",
                "requirements_revision_id",
                "source_sha256",
            )
        }
        result["result"] = {**pins, **(row["result"] or {})}
    _require_safe_numbers(result)
    return result


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    if _b64(decoded) != value:
        raise ValueError("noncanonical cursor")
    return decoded


def _workflow_window(
    conn: Connection,
    owner_id: UUID,
    keyring: Keyring,
    state: str | None,
    cursor: str | None,
) -> tuple[datetime, datetime | None, UUID | None]:
    if cursor is None:
        return conn.execute(text("SELECT clock_timestamp()")).scalar_one(), None, None
    try:
        encoded, signature = cursor.split(".")
        payload = _unb64(encoded)
        data = json.loads(payload)
        if not isinstance(data, dict) or set(data) != {
            "v",
            "kid",
            "scope",
            "owner",
            "filters",
            "as_of",
            "created_at",
            "id",
        }:
            raise ValueError("cursor shape")
        if (
            type(data["v"]) is not int
            or data["v"] != 1
            or not all(isinstance(data[key], str) for key in data if key != "v")
        ):
            raise ValueError("cursor types")
        key = hmac.digest(keyring.keys[data["kid"]], _CURSOR_PURPOSE, "sha256")
        if not hmac.compare_digest(hmac.digest(key, payload, "sha256"), _unb64(signature)):
            raise ValueError("cursor signature")
        if (
            data["scope"] != "workflows"
            or data["owner"] != str(owner_id)
            or data["filters"]
            != hashlib.sha256(canonical_json({"state": state}).encode()).hexdigest()
        ):
            raise ValueError("cursor context")
        as_of = datetime.fromisoformat(data["as_of"])
        timestamp = datetime.fromisoformat(data["created_at"])
        if as_of.tzinfo is None or timestamp.tzinfo is None or timestamp > as_of:
            raise ValueError("cursor timestamp")
        return as_of, timestamp, UUID(data["id"])
    except (ValueError, TypeError, KeyError, binascii.Error, UnicodeError, RecursionError) as exc:
        raise Problem(400, "invalid_cursor") from exc


def list_workflows(
    conn: Connection,
    owner_id: UUID,
    keyring: Keyring,
    *,
    limit: int = 30,
    state: WorkflowState | None = None,
    cursor: str | None = None,
) -> dict[str, object]:
    if not 1 <= limit <= 100 or (cursor is not None and len(cursor) > 2048):
        raise Problem(422, "invalid_workflow_page")
    as_of, timestamp, identifier = _workflow_window(conn, owner_id, keyring, state, cursor)
    predicates = ["owner_id=:owner", "created_at<=:as_of"]
    params: dict[str, object] = {"owner": owner_id, "as_of": as_of, "limit": limit + 1}
    if state is not None:
        predicates.append("state=CAST(:state AS offerloom.workflow_state)")
        params["state"] = state
    if timestamp is not None:
        predicates.append("(created_at,id)<(:timestamp,:id)")
        params.update(timestamp=timestamp, id=identifier)
    rows = (
        conn.execute(
            text(f"""SELECT id,created_at FROM offerloom.workflow_runs
        WHERE {" AND ".join(predicates)} ORDER BY created_at DESC,id DESC LIMIT :limit"""),
            params,
        )
        .mappings()
        .all()
    )
    selected = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        last = selected[-1]
        payload = canonical_json(
            {
                "v": 1,
                "kid": keyring.active_key_id,
                "scope": "workflows",
                "owner": str(owner_id),
                "filters": hashlib.sha256(canonical_json({"state": state}).encode()).hexdigest(),
                "as_of": as_of.isoformat(),
                "created_at": last["created_at"].isoformat(),
                "id": str(last["id"]),
            }
        ).encode()
        key = hmac.digest(keyring.keys[keyring.active_key_id], _CURSOR_PURPOSE, "sha256")
        next_cursor = _b64(payload) + "." + _b64(hmac.digest(key, payload, "sha256"))
    return {
        "items": [get_workflow(conn, owner_id, row["id"]) for row in selected],
        "next_cursor": next_cursor,
    }


def workflow_history(
    conn: Connection,
    owner_id: UUID,
    workflow_id: UUID,
    *,
    events: bool = False,
    limit: int = 30,
    before: UUID | None = None,
) -> dict[str, object]:
    if not 1 <= limit <= 100:
        raise Problem(422, "invalid_workflow_page")
    if (
        conn.execute(
            text("SELECT id FROM offerloom.workflow_runs WHERE owner_id=:owner AND id=:id"),
            {"owner": owner_id, "id": workflow_id},
        ).first()
        is None
    ):
        raise Problem(404, "workflow_not_found")
    table = "workflow_attempt_events" if events else "workflow_steps"
    columns = (
        "id,attempt,fence,event,error_code,created_at"
        if events
        else ("id,step_key,attempt,fence,state,delivery_state,error_code,created_at,completed_at")
    )
    predicate = ""
    params: dict[str, object] = {"owner": owner_id, "workflow": workflow_id, "limit": limit + 1}
    if before is not None:
        anchor = (
            conn.execute(
                text(f"""SELECT created_at,id FROM offerloom.{table}
            WHERE owner_id=:owner AND workflow_run_id=:workflow AND id=:before"""),
                {**params, "before": before},
            )
            .mappings()
            .first()
        )
        if anchor is None:
            raise Problem(400, "invalid_cursor")
        predicate = "AND (created_at,id)<(:timestamp,:before)"
        params.update(timestamp=anchor["created_at"], before=before)
    rows = (
        conn.execute(
            text(f"""SELECT {columns} FROM offerloom.{table} WHERE owner_id=:owner
        AND workflow_run_id=:workflow {predicate} ORDER BY created_at DESC,id DESC LIMIT :limit"""),
            params,
        )
        .mappings()
        .all()
    )
    items = []
    for row in rows[:limit]:
        item = _wire(cast(Mapping[str, object], row))
        item["error_code"] = _safe_code(row["error_code"])
        items.append(item)
    return {
        "items": items,
        "next_cursor": str(rows[limit - 1]["id"]) if len(rows) > limit else None,
    }


def retry_workflow(conn: Connection, owner_id: UUID, workflow_id: UUID) -> dict[str, object]:
    lock_installation(conn)
    conn.execute(
        text("SELECT owner_id FROM offerloom.owner_settings WHERE owner_id=:owner FOR UPDATE"),
        {"owner": owner_id},
    )
    row = (
        conn.execute(
            text("""SELECT w.*,i.maintenance_epoch AS current_epoch,
        w.deadline_at<=clock_timestamp() AS deadline_passed,w.next_attempt_at<=clock_timestamp() AS retry_due,
        EXISTS(SELECT 1 FROM offerloom.workflow_steps s WHERE s.owner_id=w.owner_id
        AND s.workflow_run_id=w.id AND s.delivery_state='unknown') AS unknown_delivery
        FROM offerloom.workflow_runs w CROSS JOIN offerloom.installation_state i
        WHERE w.owner_id=:owner AND w.id=:id AND i.singleton FOR UPDATE OF w"""),
            {"owner": owner_id, "id": workflow_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise Problem(404, "workflow_not_found")
    reason = _retry_block_reason(conn, row)
    if reason is not None:
        raise Problem(409, reason)
    # This is only a queue-control checkpoint. The real next claim owns the attempt.
    version = conn.execute(
        text("""UPDATE offerloom.workflow_runs SET state='pending',version=version+1,
        updated_at=clock_timestamp() WHERE owner_id=:owner AND id=:id RETURNING version"""),
        {"owner": owner_id, "id": workflow_id},
    ).scalar_one()
    append_attempt_event(conn, owner_id, workflow_id, "retry_requested")
    append_outbox(
        conn,
        owner_id,
        "workflow.retry_requested",
        "workflow",
        workflow_id,
        {"workflow_id": str(workflow_id)},
        int(version),
    )
    return get_workflow(conn, owner_id, workflow_id)


def _claim(
    conn: Connection, holder: UUID, kinds: tuple[str, ...], workflow_id: UUID | None = None
) -> Claim | None:
    epoch = lock_installation(conn)
    row = (
        conn.execute(
            text("""WITH candidate AS MATERIALIZED (
        SELECT w.owner_id,w.id FROM offerloom.workflow_runs w
        WHERE w.maintenance_epoch=:epoch AND w.state IN ('pending','retryable_failure')
        AND w.kind=ANY(:kinds)
        AND (CAST(:workflow AS uuid) IS NULL OR w.id=CAST(:workflow AS uuid))
        AND w.next_attempt_at<=clock_timestamp() AND w.deadline_at>clock_timestamp()
        AND w.cancel_requested_at IS NULL AND w.attempt<w.max_attempts
        AND (w.token_budget=0 OR w.tokens_used<w.token_budget)
        AND (w.cost_budget_microunits=0 OR w.cost_used_microunits<w.cost_budget_microunits)
        AND (NOT(w.kind IN ('posting_extract','matching') OR coalesce(w.input->>'mode','') IN ('tailored_ai','section_regeneration'))
             OR w.model_network_attempts<2)
        AND NOT EXISTS(SELECT 1 FROM offerloom.workflow_steps s WHERE s.owner_id=w.owner_id AND s.workflow_run_id=w.id AND s.delivery_state='unknown')
        ORDER BY w.next_attempt_at,w.created_at,w.id FOR UPDATE OF w SKIP LOCKED LIMIT 1)
        UPDATE offerloom.workflow_runs w SET state='running',lease_holder=:holder,
        lease_expires_at=clock_timestamp()+make_interval(secs=>:lease),fence=w.fence+1,
        attempt=w.attempt+1,version=w.version+1,updated_at=clock_timestamp(),last_heartbeat_at=NULL
        FROM candidate c WHERE w.owner_id=c.owner_id AND w.id=c.id RETURNING w.*"""),
            {
                "epoch": epoch,
                "holder": holder,
                "lease": LEASE_SECONDS,
                "kinds": list(kinds),
                "workflow": workflow_id,
            },
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    append_attempt_event(conn, row["owner_id"], row["id"], "claimed")
    return Claim(
        row["owner_id"], row["id"], holder, row["fence"], row["attempt"], row["kind"], row["input"]
    )


def claim(conn: Connection, holder: UUID) -> Claim | None:
    return _claim(
        conn,
        holder,
        (
            "document_generate",
            "pdf_render",
            "profile_import",
            "source_refresh",
            "posting_extract",
            "matching",
        ),
    )


def claim_control(
    conn: Connection, holder: UUID, *, workflow_id: UUID | None = None
) -> Claim | None:
    """A separate executor owns lifecycle work; it never registers as an ordinary activity."""
    return _claim(
        conn,
        holder,
        ("portable_export", "portable_import", "privacy_delete", "backup", "restore_validate"),
        workflow_id,
    )


def heartbeat(conn: Connection, task: Claim) -> bool:
    epoch = lock_installation(conn)
    return (
        conn.execute(
            text("""UPDATE offerloom.workflow_runs SET
        lease_expires_at=clock_timestamp()+make_interval(secs=>:lease),
        last_heartbeat_at=clock_timestamp(),updated_at=clock_timestamp()
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
    if conn.execute(
        text("""SELECT EXISTS(SELECT 1 FROM offerloom.workflow_steps
        WHERE owner_id=:owner AND workflow_run_id=:id AND delivery_state='unknown')"""),
        {"owner": task.owner_id, "id": task.id},
    ).scalar_one():
        raise Problem(409, "delivery_reconciliation_required")
    result = effect(conn)
    if task.kind in {"source_refresh", "posting_extract", "matching"}:
        # Recheck independently after bounded domain persistence and validation.
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
    append_attempt_event(conn, task.owner_id, task.id, "succeeded")
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
    lock_installation(conn)
    conn.execute(
        text("SELECT owner_id FROM offerloom.owner_settings WHERE owner_id=:owner FOR UPDATE"),
        {"owner": task.owner_id},
    )
    code = _safe_code(code) or "workflow_error"
    assert_fence(conn, task)
    row = (
        conn.execute(
            text(
                """SELECT error_fingerprint,repeated_error_count,max_attempts,
                extract(epoch FROM deadline_at-clock_timestamp()) AS remaining_seconds,
                (token_budget>0 AND tokens_used>=token_budget)
                OR (cost_budget_microunits>0 AND cost_used_microunits>=cost_budget_microunits)
                OR ((kind IN ('posting_extract','matching') OR coalesce(input->>'mode','') IN ('tailored_ai','section_regeneration'))
                    AND model_network_attempts>=2) AS budgets_exhausted,
                EXISTS(SELECT 1 FROM offerloom.workflow_steps s WHERE s.owner_id=w.owner_id
                AND s.workflow_run_id=w.id AND s.delivery_state='unknown') AS unknown_delivery
                FROM offerloom.workflow_runs w WHERE owner_id=:owner AND id=:id"""
            ),
            {"owner": task.owner_id, "id": task.id},
        )
        .mappings()
        .one()
    )
    repeated = int(row["repeated_error_count"]) + 1 if row["error_fingerprint"] == code else 1
    poison = code == "benchmark_poison" and repeated >= 2
    retry = (
        transient
        and not poison
        and task.kind in _REPLAY_SAFE_KINDS
        and task.attempt < int(row["max_attempts"])
        and not row["budgets_exhausted"]
    )
    delay = max(
        random.uniform(0, min(300, 2**task.attempt)),
        minimum_retry_delay_seconds or 0,
    )
    if delay >= float(row["remaining_seconds"]):
        retry = False
    if minimum_retry_delay_seconds is not None and not retry:
        # A terminal run has no resend time; the source retains its full provider pause.
        delay = 0.0
    state = "retryable_failure" if retry else "permanent_failure"
    if row["unknown_delivery"]:
        state, code = "waiting_for_approval", "delivery_reconciliation_required"
        retry = True
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
    append_attempt_event(conn, task.owner_id, task.id, state, code)
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
    from offerloom.approvals import expire_action_approvals

    epoch = lock_installation(conn)
    # Match control/domain lock ordering; skip owners whose command is in flight.
    owners = (
        conn.execute(
            text("""SELECT owner_id FROM offerloom.owner_settings ORDER BY owner_id
        FOR UPDATE SKIP LOCKED LIMIT 100"""),
        )
        .scalars()
        .all()
    )
    if not owners:
        return 0
    rows = (
        conn.execute(
            text("""SELECT w.*,EXISTS(SELECT 1 FROM offerloom.workflow_steps s
        WHERE s.owner_id=w.owner_id AND s.workflow_run_id=w.id AND s.delivery_state='unknown') AS unknown_delivery,
        w.deadline_at<=clock_timestamp() AS deadline_passed,
        ((w.kind IN ('posting_extract','matching') OR coalesce(w.input->>'mode','') IN ('tailored_ai','section_regeneration'))
            AND w.model_network_attempts>=2) AS model_exhausted,
        ((w.token_budget>0 AND w.tokens_used>=w.token_budget)
            OR (w.cost_budget_microunits>0 AND w.cost_used_microunits>=w.cost_budget_microunits)) AS budget_exhausted
        FROM offerloom.workflow_runs w WHERE w.maintenance_epoch=:epoch
        AND w.owner_id=ANY(CAST(:owners AS uuid[]))
        AND w.state IN ('pending','retryable_failure','running','waiting_for_approval')
        AND (w.cancel_requested_at IS NOT NULL
        OR ((w.state<>'waiting_for_approval' OR w.kind='profile_import') AND w.deadline_at<=clock_timestamp()
            AND (w.state<>'waiting_for_approval' OR w.last_error_code IS DISTINCT FROM 'delivery_reconciliation_required'))
        OR (w.state='running' AND w.lease_expires_at<=clock_timestamp())
        OR (w.state IN ('pending','retryable_failure') AND (
            w.attempt>=w.max_attempts OR (w.token_budget>0 AND w.tokens_used>=w.token_budget)
            OR (w.cost_budget_microunits>0 AND w.cost_used_microunits>=w.cost_budget_microunits)
            OR ((w.kind IN ('posting_extract','matching') OR coalesce(w.input->>'mode','') IN ('tailored_ai','section_regeneration'))
                AND w.model_network_attempts>=2)))
        OR ((w.state<>'waiting_for_approval' OR w.last_error_code IS DISTINCT FROM 'delivery_reconciliation_required')
            AND EXISTS(SELECT 1 FROM offerloom.workflow_steps s
            WHERE s.owner_id=w.owner_id AND s.workflow_run_id=w.id AND s.delivery_state='unknown'))
        OR (EXISTS(SELECT 1 FROM offerloom.action_approvals a WHERE a.owner_id=w.owner_id
            AND a.workflow_run_id=w.id AND a.intent IS NOT NULL AND a.revoked_at IS NULL
            AND a.consumed_at IS NULL AND a.expires_at<=clock_timestamp())
            AND NOT EXISTS(SELECT 1 FROM offerloom.workflow_steps s WHERE s.owner_id=w.owner_id
                AND s.workflow_run_id=w.id AND s.delivery_state='unknown')))
        ORDER BY w.created_at,w.id FOR UPDATE OF w SKIP LOCKED LIMIT 100"""),
            {"epoch": epoch, "owners": list(owners)},
        )
        .mappings()
        .all()
    )
    for row in rows:
        expired = False
        if not row["unknown_delivery"]:
            expired = expire_action_approvals(conn, row["owner_id"], row["id"])
        if row["cancel_requested_at"] is not None:
            state, code = "cancelled", "cancelled"
        elif row["unknown_delivery"]:
            state, code = "waiting_for_approval", "delivery_reconciliation_required"
        elif expired:
            state, code = "permanent_failure", "approval_expired"
        elif row["deadline_passed"] or row["attempt"] >= row["max_attempts"]:
            state, code = (
                "permanent_failure",
                "import_review_expired"
                if row["kind"] == "profile_import" and row["state"] == "waiting_for_approval"
                else "deadline_exceeded"
                if row["deadline_passed"]
                else "attempts_exhausted",
            )
        elif row["model_exhausted"] or row["budget_exhausted"]:
            state, code = (
                "permanent_failure",
                (
                    "model_attempts_exhausted"
                    if row["model_exhausted"]
                    else "workflow_budget_exhausted"
                ),
            )
        else:
            state, code = "retryable_failure", "worker_interrupted"
        terminal = state in {"cancelled", "permanent_failure"}
        version = conn.execute(
            text("""UPDATE offerloom.workflow_runs SET state=CAST(:state AS offerloom.workflow_state),
            lease_holder=NULL,lease_expires_at=NULL,fence=fence+1,last_error_code=:code,version=version+1,
            next_attempt_at=greatest(next_attempt_at,clock_timestamp()+interval '2 seconds'),
            finished_at=CASE WHEN :terminal THEN clock_timestamp() ELSE NULL END,
            updated_at=clock_timestamp() WHERE owner_id=:owner AND id=:id RETURNING version"""),
            {
                "state": state,
                "code": code,
                "terminal": terminal,
                "owner": row["owner_id"],
                "id": row["id"],
            },
        ).scalar_one()
        if row["state"] == "running":
            append_attempt_event(conn, row["owner_id"], row["id"], "interrupted", code)
        append_attempt_event(conn, row["owner_id"], row["id"], state, code)
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
        text("SELECT owner_id FROM offerloom.owner_settings WHERE owner_id=:owner FOR UPDATE"),
        {"owner": owner_id},
    )
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
        append_attempt_event(conn, owner_id, workflow_id, "cancelled", "cancelled")
        append_outbox(
            conn,
            owner_id,
            "workflow.cancelled",
            "workflow",
            workflow_id,
            {"workflow_id": str(workflow_id)},
            int(version),
        )
    conn.execute(
        text("""UPDATE offerloom.portable_previews SET candidate_bytes=NULL,
        cleared_at=clock_timestamp(),clear_reason='cancelled'
        WHERE owner_id=:owner AND workflow_run_id=:id AND cleared_at IS NULL"""),
        {"owner": owner_id, "id": workflow_id},
    )
    return get_workflow(conn, owner_id, workflow_id)


class BenchmarkTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_revision_id: UUID
    scenario: Literal["normal", "slow", "poison", "stage_pause"] = "normal"
    delay_seconds: int = Field(default=0, ge=0, le=120)


class CancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RetryRequest(BaseModel):
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
    def workflow_list(
        owner: Annotated[Owner, Depends(require_owner)],
        limit: Annotated[int, Query(ge=1, le=100)] = 30,
        state: WorkflowState | None = None,
        cursor: Annotated[str | None, Query(max_length=2048)] = None,
    ) -> dict[str, object]:
        with db.transaction() as conn:
            return list_workflows(
                conn, owner.id, settings.keyring, limit=limit, state=state, cursor=cursor
            )

    @router.get("/workflows/{workflow_id}")
    def workflow(
        workflow_id: UUID, owner: Annotated[Owner, Depends(require_owner)]
    ) -> JSONResponse:
        with db.transaction() as conn:
            result = get_workflow(conn, owner.id, workflow_id)
            from offerloom.approvals import get_action_approval

            result["steps"] = workflow_history(conn, owner.id, workflow_id)
            result["attempt_events"] = workflow_history(conn, owner.id, workflow_id, events=True)
            result["approval"] = get_action_approval(conn, owner.id, workflow_id, optional=True)
        return JSONResponse(result, headers={"ETag": f'"{result["version"]}"'})

    @router.get("/workflows/{workflow_id}/steps")
    def steps(
        workflow_id: UUID,
        owner: Annotated[Owner, Depends(require_owner)],
        limit: Annotated[int, Query(ge=1, le=100)] = 30,
        before: UUID | None = None,
    ) -> dict[str, object]:
        with db.transaction() as conn:
            return workflow_history(conn, owner.id, workflow_id, limit=limit, before=before)

    @router.get("/workflows/{workflow_id}/attempt-events")
    def attempt_events(
        workflow_id: UUID,
        owner: Annotated[Owner, Depends(require_owner)],
        limit: Annotated[int, Query(ge=1, le=100)] = 30,
        before: UUID | None = None,
    ) -> dict[str, object]:
        with db.transaction() as conn:
            return workflow_history(
                conn, owner.id, workflow_id, events=True, limit=limit, before=before
            )

    @router.post("/workflows/{workflow_id}/retry")
    def retry(
        workflow_id: UUID,
        body: RetryRequest,
        owner: Annotated[Owner, Depends(require_owner)],
        if_match: Annotated[str | None, Header()] = None,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        def effect(conn: Connection) -> tuple[int, dict[str, object]]:
            return 200, retry_workflow(conn, owner.id, workflow_id)

        with db.transaction() as conn:
            status, result = command(
                conn,
                owner.id,
                "POST",
                "/workflows/{workflow_id}/retry",
                {"workflow_id": str(workflow_id)},
                body.model_dump(mode="json"),
                if_match,
                idempotency_key,
                "workflow",
                workflow_id,
                effect,
                workspace_change=False,
            )
        return JSONResponse(result, status_code=status, headers={"ETag": f'"{result["version"]}"'})

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
                workspace_change=False,
            )
        return JSONResponse(result, status_code=status, headers={"ETag": f'"{result["version"]}"'})

    @router.get("/status")
    def status(owner: Annotated[Owner, Depends(require_owner)]) -> dict[str, object]:
        from offerloom.task_retention import task_retention_status

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
            retention = task_retention_status(conn, owner.id)
        return {
            "queue": {key: float(value) for key, value in queue.items()},
            "worker": {
                key: float(value) if value is not None else None for key, value in workers.items()
            },
            "outbox": {key: float(value) for key, value in outbox.items()},
            "task_input_retention": retention,
        }

    return router
