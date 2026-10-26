# SPDX-License-Identifier: MIT OR Apache-2.0
"""Real PostgreSQL frozen-input expiry, exclusion and durable status boundaries."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError
from test_document_generation import _base
from test_identity import identity as identity
from test_sources import SourceContext
from test_sources import catalog as catalog
from test_suitability import _profile
from test_tracking import _application

from offerloom import backup_schedule, task_retention
from offerloom.commands import canonical_json
from offerloom.config import ConfigurationError
from offerloom.db import Database
from offerloom.maintenance import (
    Activity,
    ProviderExecution,
    finish_activity,
    start_worker_activity,
)
from offerloom.portable_exports import LifecycleWorker
from offerloom.problems import Problem
from offerloom.storage import BlobStore
from offerloom.task_retention import expire_task_inputs, task_retention_status
from offerloom.workflows import Claim, enqueue

pytestmark = pytest.mark.postgres

PRIVATE = "Synthetic private frozen prompt text, not a resource identifier."


def _seed(
    context: SourceContext,
    state: str = "succeeded",
    *,
    hours: int = 25,
    payload: dict[str, object] | None = None,
    kind: str = "document_generate",
) -> UUID:
    with context.runtime.transaction() as conn:
        identifier = enqueue(conn, context.owner_id, kind, payload or {"source_text": PRIVATE})
    _state(context, identifier, state, hours)
    return identifier


def _state(context: SourceContext, identifier: UUID, state: str, hours: int = 25) -> None:
    with context.identity.admin.transaction() as conn:
        conn.execute(
            text("""UPDATE offerloom.workflow_runs SET state=CAST(:state AS offerloom.workflow_state),
            finished_at=CASE WHEN :state IN ('succeeded','cancelled','permanent_failure')
                THEN clock_timestamp()-make_interval(hours=>:hours) ELSE NULL END,
            lease_holder=CASE WHEN :state='running' THEN :holder ELSE NULL END,
            lease_expires_at=CASE WHEN :state='running' THEN clock_timestamp()+interval '1 hour' ELSE NULL END,
            fence=1,attempt=1 WHERE owner_id=:owner AND id=:id"""),
            {
                "owner": context.owner_id,
                "id": identifier,
                "state": state,
                "hours": hours,
                "holder": uuid4(),
            },
        )


def _row(context: SourceContext, identifier: UUID) -> dict[str, Any]:
    with context.runtime.transaction() as conn:
        return dict(
            conn.execute(
                text("SELECT * FROM offerloom.workflow_runs WHERE owner_id=:owner AND id=:id"),
                {"owner": context.owner_id, "id": identifier},
            )
            .mappings()
            .one()
        )


def _status(context: SourceContext, owner_id: UUID | None = None) -> dict[str, object]:
    with context.runtime.transaction() as conn:
        return task_retention_status(conn, owner_id or context.owner_id)


def _model(context: SourceContext, workflow_id: UUID) -> UUID:
    identifier = uuid4()
    with context.identity.admin.transaction() as conn:
        conn.execute(
            text("""INSERT INTO offerloom.model_runs
            (owner_id,id,workflow_run_id,provider_key,model_id,task_kind,prompt_version,input_sha256,
            output_sha256,parameters,egress_class,reserved_tokens,reserved_cost_microunits,status,metadata)
            VALUES(:owner,:id,:workflow,'ollama','synthetic-model','document_generate','synthetic-v1',
            repeat('a',64),repeat('b',64),CAST(:parameters AS jsonb),'local',0,0,'running',
            '{"usage_status":"unknown","prompt_version":"synthetic-v1"}')"""),
            {
                "owner": context.owner_id,
                "id": identifier,
                "workflow": workflow_id,
                "parameters": canonical_json({"temperature": 0}),
            },
        )
    return identifier


def _claim(context: SourceContext, identifier: UUID) -> Claim:
    row = _row(context, identifier)
    return Claim(context.owner_id, identifier, row["lease_holder"], 1, 1, row["kind"], row["input"])


def test_expired_terminal_inputs_scrub_all_content_and_preserve_committed_data(
    catalog: SourceContext,
) -> None:
    profile = _profile(catalog)
    document = _base(catalog, profile)
    application = _application(catalog)
    revision = document["current_revision"]
    identifiers = {
        "application_id": application["id"],
        "document_id": document["id"],
        "revision_id": revision["id"],
        "profile_revision_id": profile["current_revision"]["id"],
    }
    payload: dict[str, object] = {
        **identifiers,
        "source_text": PRIVATE,
        "context": {"prompt": PRIVATE},
        "frame": {"contact": PRIVATE},
        "plan": {"draft": PRIVATE},
        "facts": [{"evidence_text": PRIVATE}],
        "unknown_future_payload": PRIVATE,
        "profile_sha256": "a" * 64,
        "context_sha256": "b" * 64,
        "import_id": PRIVATE,
        "posting_id": {"text": PRIVATE},
        "source_id": [PRIVATE],
        "policy_review_id": "00000000-0000-0000-0000-000000000000 " + PRIVATE,
        "template_sha256": PRIVATE,
        PRIVATE + "_sha256": "c" * 64,
    }
    identifier = _seed(catalog, payload=payload)
    model = _model(catalog, identifier)
    blob = BlobStore(catalog.identity.settings.blob_root).put_bytes(
        catalog.owner_id, b"saved artifact"
    )
    with catalog.identity.admin.transaction() as conn:
        conn.execute(
            text("""UPDATE offerloom.workflow_runs SET result=CAST(:result AS jsonb)
            WHERE owner_id=:owner AND id=:id"""),
            {"owner": catalog.owner_id, "id": identifier, "result": canonical_json(identifiers)},
        )
        blob_id = uuid4()
        conn.execute(
            text("""INSERT INTO offerloom.blobs(owner_id,id,sha256,byte_length,media_type,storage_key)
            VALUES(:owner,:id,:sha,:length,'application/pdf',:key)"""),
            {
                "owner": catalog.owner_id,
                "id": blob_id,
                "sha": blob.sha256,
                "length": blob.byte_length,
                "key": blob.storage_key,
            },
        )
        conn.execute(
            text("""INSERT INTO offerloom.document_artifacts
            (owner_id,document_revision_id,blob_id,renderer_version,format)
            VALUES(:owner,:revision,:blob,'synthetic-preservation-v1','pdf')"""),
            {"owner": catalog.owner_id, "revision": UUID(revision["id"]), "blob": blob_id},
        )
    tables = (
        "profile_revisions",
        "evidence_revisions",
        "document_revisions",
        "document_artifacts",
        "model_runs",
    )
    before_data: dict[str, object] = {}
    with catalog.runtime.transaction() as conn:
        for table in tables:
            before_data[table] = conn.execute(
                text(
                    f"SELECT jsonb_agg(to_jsonb(t) ORDER BY id) FROM offerloom.{table} t WHERE owner_id=:owner"
                ),
                {"owner": catalog.owner_id},
            ).scalar_one()
    before = _row(catalog, identifier)
    outcome = expire_task_inputs(catalog.runtime)
    assert outcome == {"processed_count": 1, "has_more": False}
    after = _row(catalog, identifier)
    assert after["input"] == {
        **identifiers,
        "schema_version": 1,
        "profile_sha256": "a" * 64,
        "context_sha256": "b" * 64,
    }
    for key in ("request_sha256", "result", "state", "finished_at", "fence", "attempt"):
        assert after[key] == before[key]
    assert after["version"] == before["version"] + 1
    with catalog.runtime.transaction() as conn:
        # These are the exact top-level predicates in migration 021, independently
        # of retention's implementation, not reconstructed from workflow results.
        for key, value in identifiers.items():
            if key in {"application_id", "document_id", "revision_id"}:
                assert (
                    conn.execute(
                        text("""SELECT id FROM offerloom.workflow_runs WHERE owner_id=:owner
                    AND id=:id AND input->>:key=:value"""),
                        {"owner": catalog.owner_id, "id": identifier, "key": key, "value": value},
                    ).scalar_one()
                    == identifier
                )
        for table in tables:
            assert (
                conn.execute(
                    text(
                        f"SELECT jsonb_agg(to_jsonb(t) ORDER BY id) FROM offerloom.{table} t WHERE owner_id=:owner"
                    ),
                    {"owner": catalog.owner_id},
                ).scalar_one()
                == before_data[table]
            )
        assert (
            conn.execute(
                text(
                    "SELECT input_sha256 FROM offerloom.model_runs WHERE owner_id=:owner AND id=:id"
                ),
                {"owner": catalog.owner_id, "id": model},
            ).scalar_one()
            == "a" * 64
        )
    assert (
        BlobStore(catalog.identity.settings.blob_root).read(
            catalog.owner_id, blob.storage_key, blob.sha256, blob.byte_length
        )
        == b"saved artifact"
    )
    assert _status(catalog)["last_success_at"] is not None
    assert expire_task_inputs(catalog.runtime) == {"processed_count": 0, "has_more": False}
    assert _row(catalog, identifier) == after


def test_terminal_cutoff_and_active_retry_waiting_inputs_remain_untouched(
    catalog: SourceContext,
) -> None:
    removable = [_seed(catalog, state) for state in ("succeeded", "cancelled", "permanent_failure")]
    preserved = [
        _seed(catalog, state, hours=1) for state in ("succeeded", "cancelled", "permanent_failure")
    ]
    preserved += [
        _seed(catalog, state)
        for state in ("pending", "running", "retryable_failure", "waiting_for_approval")
    ]
    before = {identifier: _row(catalog, identifier) for identifier in preserved}
    assert expire_task_inputs(catalog.runtime) == {"processed_count": 3, "has_more": False}
    for identifier in removable:
        assert _row(catalog, identifier)["input"] == {"schema_version": 1}
    for identifier in preserved:
        assert _row(catalog, identifier) == before[identifier]


def test_unknown_delivery_provider_live_execution_and_unreleased_control_are_preserved(
    catalog: SourceContext,
) -> None:
    unknown = _seed(catalog, "cancelled")
    provider = _seed(catalog, "running")
    live = _seed(catalog, "running")
    controlled = _seed(catalog, "cancelled", kind="backup")
    safe = _seed(catalog)
    model = _model(catalog, provider)
    execution = ProviderExecution(catalog.runtime, _claim(catalog, provider), model)
    execution.started()
    activity = start_worker_activity(catalog.runtime, uuid4(), _claim(catalog, live))
    _state(catalog, provider, "cancelled")
    _state(catalog, live, "permanent_failure")
    with catalog.identity.admin.transaction() as conn:
        conn.execute(
            text("""INSERT INTO offerloom.workflow_steps
            (owner_id,workflow_run_id,step_key,attempt,fence,state,delivery_state)
            VALUES(:owner,:id,'external_write',1,1,'failed','unknown')"""),
            {"owner": catalog.owner_id, "id": unknown},
        )
        conn.execute(
            text("""INSERT INTO offerloom.installation_controls
            (id,owner_id,holder,fence,workflow_fence,namespace_id,maintenance_epoch,purpose,scope,
            phase,deadline_at,drain_deadline_at,lease_expires_at,settled_at)
            SELECT :id,:owner,:holder,1,1,namespace_id,maintenance_epoch+1,'backup','{}','settled',
            clock_timestamp()-interval '25 hours',clock_timestamp()-interval '26 hours',
            clock_timestamp()-interval '25 hours',clock_timestamp()-interval '25 hours'
            FROM offerloom.installation_state WHERE singleton"""),
            {"owner": catalog.owner_id, "id": controlled, "holder": uuid4()},
        )
    preserved = (unknown, provider, live, controlled)
    before = {identifier: _row(catalog, identifier) for identifier in preserved}
    try:
        assert expire_task_inputs(catalog.runtime) == {"processed_count": 1, "has_more": False}
        assert _row(catalog, safe)["input"] == {"schema_version": 1}
        for identifier in preserved:
            assert _row(catalog, identifier) == before[identifier]
    finally:
        execution.completed()
        finish_activity(catalog.runtime, activity)


def test_bounded_batches_resume_without_premature_whole_sweep_success(
    catalog: SourceContext,
) -> None:
    identifiers = [_seed(catalog) for _ in range(5)]
    first = expire_task_inputs(catalog.runtime, batch_size=2)
    assert first == {"processed_count": 2, "has_more": True}
    status = _status(catalog)
    assert status["state"] == "in_progress" and status["last_success_at"] is None
    assert status["processed_count"] == 2
    assert status["policy"] == {
        "terminal_input_retention_seconds": 86400,
        "sweep_interval_seconds": 86400,
        "batch_retry_interval_seconds": 60,
        "batch_size": 100,
        "extension_supported": False,
    }
    assert (
        sum("source_text" not in _row(catalog, identifier)["input"] for identifier in identifiers)
        == 2
    )
    second = expire_task_inputs(catalog.runtime, batch_size=2)
    assert second == {"processed_count": 2, "has_more": True}
    assert _status(catalog)["last_success_at"] is None
    assert expire_task_inputs(catalog.runtime, batch_size=2) == {
        "processed_count": 1,
        "has_more": False,
    }
    assert _status(catalog)["state"] == "succeeded"
    assert _status(catalog)["last_success_at"] is not None
    for identifier in identifiers:
        assert _row(catalog, identifier)["input"] == {"schema_version": 1}
    final = [_row(catalog, identifier) for identifier in identifiers]
    assert expire_task_inputs(catalog.runtime, batch_size=2)["processed_count"] == 0
    assert [_row(catalog, identifier) for identifier in identifiers] == final


def test_database_cleanup_failure_is_audited_without_text_and_can_resume(
    catalog: SourceContext,
) -> None:
    identifier = _seed(catalog)
    before = _row(catalog, identifier)
    with catalog.identity.admin.transaction() as conn:
        conn.execute(
            text("""CREATE FUNCTION offerloom.synthetic_retention_failure() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Synthetic private frozen prompt text'; END; $$""")
        )
        conn.execute(
            text("""CREATE TRIGGER synthetic_retention_failure BEFORE UPDATE OF input
        ON offerloom.workflow_runs FOR EACH ROW EXECUTE FUNCTION offerloom.synthetic_retention_failure()""")
        )
    try:
        with pytest.raises(DBAPIError):
            expire_task_inputs(catalog.runtime)
        assert _row(catalog, identifier) == before
        status = _status(catalog)
        assert status["state"] == "failed" and status["last_success_at"] is None
        assert status["last_failure_at"] is not None
        assert status["failure_code"] == "task_input_retention_failed"
        assert status["processed_count"] == 0
        with catalog.runtime.transaction() as conn:
            details = (
                conn.execute(
                    text(
                        "SELECT detail FROM offerloom.audit_events WHERE owner_id=:owner AND action='task_input_retention'"
                    ),
                    {"owner": catalog.owner_id},
                )
                .scalars()
                .all()
            )
        assert details == [
            {
                "result": "failed",
                "processed_count": 0,
                "policy_version": 1,
                "failure_code": "task_input_retention_failed",
            }
        ]
        assert "private" not in json.dumps(status).lower()
        assert _status(catalog, uuid4()) == {
            "state": "not_run",
            "last_success_at": None,
            "last_failure_at": None,
            "failure_code": None,
            "processed_count": 0,
            "policy": status["policy"],
        }
        json.dumps(status)
    finally:
        with catalog.identity.admin.transaction() as conn:
            conn.execute(
                text("DROP TRIGGER synthetic_retention_failure ON offerloom.workflow_runs")
            )
            conn.execute(text("DROP FUNCTION offerloom.synthetic_retention_failure()"))
    assert expire_task_inputs(catalog.runtime)["processed_count"] == 1
    resumed = _status(catalog)
    assert resumed["state"] == "succeeded" and resumed["last_success_at"] is not None
    assert resumed["last_failure_at"] == status["last_failure_at"]


def test_admission_and_owner_locks_prevent_snapshot_or_writer_racing_expiry(
    catalog: SourceContext,
) -> None:
    identifier = _seed(catalog)
    entered, release = Event(), Event()

    def pause(
        _conn: Any, _cursor: Any, statement: str, _parameters: Any, _context: Any, _many: bool
    ) -> None:
        if statement.startswith("UPDATE offerloom.workflow_runs SET input="):
            entered.set()
            assert release.wait(10)

    event.listen(catalog.runtime.engine, "before_cursor_execute", pause)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(expire_task_inputs, catalog.runtime)
            try:
                assert entered.wait(10)
                with (
                    pytest.raises(DBAPIError) as fenced,
                    catalog.identity.admin.transaction() as conn,
                ):
                    conn.execute(text("SET LOCAL lock_timeout='200ms'"))
                    conn.execute(
                        text(
                            "UPDATE offerloom.installation_state SET maintenance_active=true WHERE singleton"
                        )
                    )
                assert getattr(fenced.value.orig, "sqlstate", None) == "55P03"
                with (
                    pytest.raises(DBAPIError) as writer,
                    catalog.identity.admin.transaction() as conn,
                ):
                    conn.execute(text("SET LOCAL lock_timeout='200ms'"))
                    conn.execute(
                        text(
                            "SELECT owner_id FROM offerloom.owner_settings WHERE owner_id=:owner FOR UPDATE"
                        ),
                        {"owner": catalog.owner_id},
                    )
                assert getattr(writer.value.orig, "sqlstate", None) == "55P03"
            finally:
                release.set()
            assert future.result(timeout=10)["processed_count"] == 1
    finally:
        event.remove(catalog.runtime.engine, "before_cursor_execute", pause)
    assert _row(catalog, identifier)["input"] == {"schema_version": 1}


def test_optional_backup_configuration_failure_does_not_block_retention(
    catalog: SourceContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identifier = _seed(catalog)
    worker = LifecycleWorker(catalog.runtime, catalog.identity.settings)

    def broken_backup(_db: object, _settings: object) -> dict[str, object]:
        raise ConfigurationError("Synthetic backup configuration unavailable")

    def stop_after_cleanup() -> bool:
        worker.stopping.set()
        return False

    monkeypatch.setattr(backup_schedule, "run_due", broken_backup)
    monkeypatch.setattr("offerloom.portable_exports.signal.signal", lambda *_args: None)
    monkeypatch.setattr(worker, "run_once", stop_after_cleanup)
    worker.run()
    assert _row(catalog, identifier)["input"] == {"schema_version": 1}
    assert _status(catalog)["state"] == "succeeded"


def test_source_refresh_immutable_policy_pins_do_not_break_terminal_sweeps(
    catalog: SourceContext,
) -> None:
    source = catalog.create()
    queued = catalog.refresh(source)
    assert queued.status_code == 202, queued.text
    identifier = UUID(queued.json()["workflow_id"])
    _state(catalog, identifier, "succeeded")
    before = _row(catalog, identifier)
    assert expire_task_inputs(catalog.runtime) == {"processed_count": 0, "has_more": False}
    assert _row(catalog, identifier) == before
    assert _status(catalog)["state"] == "succeeded"


def test_cleanup_acknowledgement_failure_remains_visible(
    catalog: SourceContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identifier = _seed(catalog)
    completed = finish_activity
    outstanding: list[Activity] = []

    def reject_completion(db: Database, activity: Activity) -> None:
        outstanding.append(activity)
        raise Problem(409, "activity_completion_not_registered")

    monkeypatch.setattr(task_retention, "finish_activity", reject_completion)
    try:
        with pytest.raises(Problem) as error:
            expire_task_inputs(catalog.runtime)
        assert error.value.code == "activity_completion_not_registered"
        assert _row(catalog, identifier)["input"] == {"schema_version": 1}
        status = _status(catalog)
        assert status["state"] == "failed"
        assert status["last_failure_at"] is not None
        assert status["failure_code"] == "task_input_retention_failed"
        with catalog.runtime.transaction() as conn:
            assert (
                conn.execute(
                    text("SELECT id FROM offerloom.installation_activities WHERE id=:id"),
                    {"id": outstanding[0].id},
                ).scalar_one()
                == outstanding[0].id
            )
    finally:
        for activity in outstanding:
            completed(catalog.runtime, activity)
