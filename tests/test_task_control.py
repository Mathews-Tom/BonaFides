# SPDX-License-Identifier: MIT OR Apache-2.0
"""Durable task control against PostgreSQL and the authenticated owner API."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError
from test_database import PGContext
from test_database import pg_context as pg_context
from test_identity import identity as identity
from test_imports import ImportContext
from test_imports import importing as importing
from test_sources import SourceContext
from test_sources import catalog as catalog
from test_suitability import _profile
from test_workflows import _claim, _enqueue

from offerloom import profile_entries, workflows
from offerloom.commands import canonical_json
from offerloom.crypto import Keyring
from offerloom.problems import Problem
from offerloom.workflows import (
    Claim,
    claim,
    enqueue,
    fail,
    get_workflow,
    heartbeat,
    list_workflows,
    publish,
    reap,
    retry_workflow,
    workflow_history,
)


@pytest.fixture
def task_model_context(pg_context: PGContext) -> Iterator[PGContext]:
    try:
        yield pg_context
    finally:
        with pg_context.migrator.begin() as conn:
            conn.execute(
                text("""UPDATE offerloom.local_model_reservations SET owner_id=NULL,
                workflow_run_id=NULL,fence=NULL,reserved_until='-infinity' WHERE owner_id=:owner"""),
                {"owner": pg_context.owner_id},
            )
            conn.execute(
                text("DELETE FROM offerloom.model_runs WHERE owner_id=:owner"),
                {"owner": pg_context.owner_id},
            )


def _due(pg: PGContext, task: Claim) -> None:
    with pg.migrator.begin() as conn:
        conn.execute(
            text(
                "UPDATE offerloom.workflow_runs SET next_attempt_at=clock_timestamp()-interval '1 second' WHERE owner_id=:owner AND id=:id"
            ),
            {"owner": task.owner_id, "id": task.id},
        )


def _unknown(pg: PGContext, task: Claim) -> None:
    with pg.runtime.begin() as conn:
        conn.execute(
            text("""INSERT INTO offerloom.workflow_steps
            (owner_id,workflow_run_id,step_key,attempt,fence,state,delivery_state,error_code,checkpoint)
            VALUES(:owner,:id,'external_delivery',:attempt,:fence,'waiting_for_approval','unknown',
            'delivery_reconciliation_required','{"private_source":"must never appear"}')"""),
            {"owner": task.owner_id, "id": task.id, "attempt": task.attempt, "fence": task.fence},
        )


def _queue(context: SourceContext) -> UUID:
    with context.runtime.transaction() as conn:
        revision = conn.execute(
            text("SELECT current_revision_id FROM offerloom.profiles WHERE owner_id=:owner"),
            {"owner": context.owner_id},
        ).scalar_one()
        return enqueue(
            conn,
            context.owner_id,
            "document_generate",
            {"profile_revision_id": str(revision), "scenario": "normal"},
        )


def _retryable(context: SourceContext) -> dict[str, Any]:
    identifier = _queue(context)
    with context.runtime.transaction() as conn:
        task = claim(conn, uuid4())
        assert task is not None and task.id == identifier
        fail(conn, task, "temporary_local_unavailable", True)
    with context.identity.admin.transaction() as conn:
        conn.execute(
            text(
                "UPDATE offerloom.workflow_runs SET next_attempt_at=clock_timestamp()-interval '1 second' WHERE owner_id=:owner AND id=:id"
            ),
            {"owner": context.owner_id, "id": identifier},
        )
    return context.workflow(identifier)


@pytest.mark.postgres
def test_attempt_ledger_records_actual_claims_and_control_without_reset(
    pg_context: PGContext,
) -> None:
    workflow = _enqueue(pg_context)
    first = _claim(pg_context)
    with pg_context.runtime.begin() as conn:
        fail(conn, first, "temporary_local_unavailable", True)
    _due(pg_context, first)
    with pg_context.runtime.begin() as conn:
        before = conn.execute(
            text(
                "SELECT attempt,max_attempts,deadline_at,next_attempt_at,model_network_attempts,token_budget,tokens_used,cost_budget_microunits,cost_used_microunits,error_fingerprint,repeated_error_count FROM offerloom.workflow_runs WHERE owner_id=:owner AND id=:id"
            ),
            {"owner": first.owner_id, "id": workflow},
        ).one()
        result = retry_workflow(conn, first.owner_id, workflow)
        after = conn.execute(
            text(
                "SELECT attempt,max_attempts,deadline_at,next_attempt_at,model_network_attempts,token_budget,tokens_used,cost_budget_microunits,cost_used_microunits,error_fingerprint,repeated_error_count FROM offerloom.workflow_runs WHERE owner_id=:owner AND id=:id"
            ),
            {"owner": first.owner_id, "id": workflow},
        ).one()
        assert after == before
        assert result["state"] == "pending" and result["attempt"] == 1
        canonical_json(result)
    second = _claim(pg_context)
    assert second.attempt == 2 and second.fence > first.fence
    with pg_context.runtime.begin() as conn:
        assert get_workflow(conn, second.owner_id, workflow)["last_heartbeat_at"] is None
        assert heartbeat(conn, second)
        assert get_workflow(conn, second.owner_id, workflow)["last_heartbeat_at"] is not None
        publish(conn, second, lambda active: {"checkpoint": "completed"})
        events = cast(
            list[dict[str, Any]],
            workflow_history(conn, second.owner_id, workflow, events=True)["items"],
        )
        assert [event["event"] for event in reversed(events)] == [
            "claimed",
            "retryable_failure",
            "retry_requested",
            "claimed",
            "succeeded",
        ]
        assert [event["attempt"] for event in reversed(events)] == [1, 1, 1, 2, 2]
        assert all(
            set(event) == {"id", "attempt", "fence", "event", "error_code", "created_at"}
            for event in events
        )
    with pytest.raises(DBAPIError), pg_context.runtime.begin() as conn:
        conn.execute(
            text(
                "UPDATE offerloom.workflow_attempt_events SET event='cancelled' WHERE owner_id=:owner"
            ),
            {"owner": first.owner_id},
        )
    with pytest.raises(DBAPIError), pg_context.runtime.begin() as conn:
        conn.execute(
            text("DELETE FROM offerloom.workflow_attempt_events WHERE owner_id=:owner"),
            {"owner": first.owner_id},
        )


@pytest.mark.postgres
def test_claim_and_event_rollback_together(pg_context: PGContext) -> None:
    workflow = _enqueue(pg_context)
    with pytest.raises(RuntimeError, match="rollback"), pg_context.runtime.begin() as conn:
        task = claim(conn, uuid4())
        assert task is not None
        raise RuntimeError("rollback")
    with pg_context.runtime.begin() as conn:
        assert get_workflow(conn, pg_context.owner_id, workflow)["attempt"] == 0
        assert workflow_history(conn, pg_context.owner_id, workflow, events=True)["items"] == []
    assert _claim(pg_context).attempt == 1


@pytest.mark.postgres
def test_unknown_delivery_never_becomes_resendable_after_failure_or_timeout(
    pg_context: PGContext,
) -> None:
    _enqueue(pg_context)
    task = _claim(pg_context)
    _unknown(pg_context, task)
    effects: list[str] = []

    def forbidden_effect(_conn: Connection) -> dict[str, object]:
        effects.append("must_not_run")
        return {}

    with pytest.raises(Problem) as blocked, pg_context.runtime.begin() as conn:
        publish(conn, task, forbidden_effect)
    assert blocked.value.code == "delivery_reconciliation_required" and effects == []
    with pg_context.runtime.begin() as conn:
        fail(conn, task, "provider_timeout", False)
        result = get_workflow(conn, task.owner_id, task.id)
        assert result["state"] == "waiting_for_approval"
        assert result["delivery_state"] == "unknown"
        assert not result["retryable"]
        assert result["retry_block_reason"] == "delivery_reconciliation_required"
    with pg_context.migrator.begin() as conn:
        conn.execute(
            text("""UPDATE offerloom.workflow_runs SET created_at=clock_timestamp()-interval '181 seconds',
            deadline_at=clock_timestamp()-interval '1 second' WHERE owner_id=:owner AND id=:id"""),
            {"owner": task.owner_id, "id": task.id},
        )
    with pg_context.runtime.begin() as conn:
        assert reap(conn) == 0
        assert claim(conn, uuid4()) is None
        assert get_workflow(conn, task.owner_id, task.id)["state"] == "waiting_for_approval"
    with pytest.raises(Problem) as retry, pg_context.runtime.begin() as conn:
        retry_workflow(conn, task.owner_id, task.id)
    assert retry.value.code == "delivery_reconciliation_required"


@pytest.mark.postgres
@pytest.mark.parametrize("initial", ["running", "pending", "waiting_for_approval"])
def test_unknown_delivery_reaper_fences_worker_without_erasing_checkpoint(
    pg_context: PGContext, initial: str
) -> None:
    _enqueue(pg_context)
    task = _claim(pg_context)
    _unknown(pg_context, task)
    if initial != "running":
        with pg_context.migrator.begin() as conn:
            conn.execute(
                text("""UPDATE offerloom.workflow_runs SET state=CAST(:state AS offerloom.workflow_state),
                lease_holder=NULL,lease_expires_at=NULL WHERE owner_id=:owner AND id=:id"""),
                {"state": initial, "owner": task.owner_id, "id": task.id},
            )
    with pg_context.runtime.begin() as conn:
        assert reap(conn) == 1
        result = get_workflow(conn, task.owner_id, task.id)
        assert result["state"] == "waiting_for_approval" and result["finished_at"] is None
        assert claim(conn, uuid4()) is None
        assert reap(conn) == 0
        history = workflow_history(conn, task.owner_id, task.id)
        assert "must never appear" not in json.dumps(history)
        assert cast(list[dict[str, Any]], history["items"])[0]["delivery_state"] == "unknown"
    with pytest.raises(DBAPIError), pg_context.runtime.begin() as conn:
        publish(conn, task, lambda active: {"late": True})


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("state='succeeded',finished_at=clock_timestamp()", "workflow_not_retryable"),
        ("state='permanent_failure',finished_at=clock_timestamp()", "workflow_not_retryable"),
        (
            "state='cancelled',finished_at=clock_timestamp(),cancel_requested_at=clock_timestamp()",
            "workflow_not_retryable",
        ),
        ("next_attempt_at=clock_timestamp()+interval '60 seconds'", "retry_not_due"),
        ("attempt=max_attempts", "attempts_exhausted"),
        ("maintenance_epoch=maintenance_epoch+1", "workflow_namespace_changed"),
        ("token_budget=10,tokens_used=10", "workflow_budget_exhausted"),
        ("cost_budget_microunits=10,cost_used_microunits=10", "workflow_budget_exhausted"),
        ("kind='posting_extract',model_network_attempts=2", "model_attempts_exhausted"),
        ("kind='approved_send'", "workflow_not_replay_safe"),
        (
            "created_at=clock_timestamp()-interval '181 seconds',deadline_at=clock_timestamp()-interval '1 second'",
            "deadline_exceeded",
        ),
    ],
)
def test_retry_guards_preserve_run(pg_context: PGContext, mutation: str, reason: str) -> None:
    _enqueue(pg_context)
    task = _claim(pg_context)
    with pg_context.runtime.begin() as conn:
        fail(conn, task, "temporary_local_unavailable", True)
    _due(pg_context, task)
    with pg_context.migrator.begin() as conn:
        conn.execute(
            text(f"UPDATE offerloom.workflow_runs SET {mutation} WHERE owner_id=:owner AND id=:id"),
            {"owner": task.owner_id, "id": task.id},
        )
        before = conn.execute(
            text(
                "SELECT to_jsonb(w) FROM offerloom.workflow_runs w WHERE owner_id=:owner AND id=:id"
            ),
            {"owner": task.owner_id, "id": task.id},
        ).scalar_one()
    with pytest.raises(Problem) as blocked, pg_context.runtime.begin() as conn:
        retry_workflow(conn, task.owner_id, task.id)
    assert blocked.value.code == reason
    with pg_context.runtime.begin() as conn:
        assert (
            conn.execute(
                text(
                    "SELECT to_jsonb(w) FROM offerloom.workflow_runs w WHERE owner_id=:owner AND id=:id"
                ),
                {"owner": task.owner_id, "id": task.id},
            ).scalar_one()
            == before
        )
        assert all(
            item["event"] != "retry_requested"
            for item in cast(
                list[dict[str, Any]],
                workflow_history(conn, task.owner_id, task.id, events=True)["items"],
            )
        )


@pytest.mark.postgres
def test_usage_distinguishes_unattempted_known_zero_and_unknown(
    task_model_context: PGContext,
) -> None:
    pg_context = task_model_context
    workflow = _enqueue(pg_context)
    with pg_context.runtime.begin() as conn:
        result = get_workflow(conn, pg_context.owner_id, workflow)
        usage = cast(dict[str, Any], result["usage"])
        assert usage["status"] == "not_applicable" and usage["model_runs"] == 0
        assert usage["input_tokens"] is None and usage["cost_used_microunits"] is None
        assert isinstance(result["queue_age_seconds"], int)
        conn.execute(
            text("""INSERT INTO offerloom.model_runs(owner_id,workflow_run_id,provider_key,model_id,task_kind,
            prompt_version,input_sha256,parameters,egress_class,reserved_tokens,reserved_cost_microunits,
            status,usage_status,input_tokens,output_tokens,actual_cost_microunits,finished_at)
            VALUES(:owner,:id,'synthetic','synthetic','synthetic','synthetic',repeat('0',64),'{}','local',100,0,
            'succeeded','known',0,0,0,clock_timestamp())"""),
            {"owner": pg_context.owner_id, "id": workflow},
        )
        usage = cast(dict[str, Any], get_workflow(conn, pg_context.owner_id, workflow)["usage"])
        assert (
            usage["status"] == "known"
            and usage["input_tokens"] == 0
            and usage["cost_used_microunits"] == 0
        )
        conn.execute(
            text("""UPDATE offerloom.model_runs SET usage_status='unknown',input_tokens=NULL,
            output_tokens=NULL,actual_cost_microunits=NULL,status='unknown_usage' WHERE owner_id=:owner AND workflow_run_id=:id"""),
            {"owner": pg_context.owner_id, "id": workflow},
        )
        usage = cast(dict[str, Any], get_workflow(conn, pg_context.owner_id, workflow)["usage"])
        assert usage["status"] == "unknown"
        assert (
            usage["input_tokens"] is None
            and usage["output_tokens"] is None
            and usage["cost_used_microunits"] is None
        )
        assert usage["tokens_used"] == 0 and usage["token_budget"] == 0


@pytest.mark.postgres
def test_history_pages_are_bounded_parent_scoped_and_payload_free(pg_context: PGContext) -> None:
    workflow = _enqueue(pg_context)
    other = _enqueue(pg_context)
    task = _claim(pg_context)
    assert task.id == workflow
    with pg_context.runtime.begin() as conn:
        for index in range(35):
            conn.execute(
                text("""INSERT INTO offerloom.workflow_steps(owner_id,workflow_run_id,step_key,attempt,fence,state,checkpoint,error_code)
                VALUES(:owner,:id,:key,:attempt,:fence,'succeeded','{"secret":"private checkpoint"}',:error)"""),
                {
                    "owner": task.owner_id,
                    "id": task.id,
                    "key": f"checkpoint_{index}",
                    "attempt": task.attempt,
                    "fence": task.fence,
                    "error": "raw secret /Users/private-path",
                },
            )
        first = workflow_history(conn, task.owner_id, task.id)
        second = workflow_history(
            conn, task.owner_id, task.id, before=UUID(cast(str, first["next_cursor"]))
        )
        one, two = (
            cast(list[dict[str, Any]], first["items"]),
            cast(list[dict[str, Any]], second["items"]),
        )
        assert len(one) == 30 and len(two) == 5 and second["next_cursor"] is None
        assert len({item["id"] for item in one + two}) == 35
        serialized = json.dumps([first, second])
        assert "private checkpoint" not in serialized and "/Users/" not in serialized
        assert all(item["error_code"] == "workflow_error" for item in one + two)
    with pytest.raises(Problem) as wrong_parent, pg_context.runtime.begin() as conn:
        workflow_history(conn, task.owner_id, other, before=UUID(one[0]["id"]))
    assert wrong_parent.value.code == "invalid_cursor"
    with pytest.raises(Problem) as wrong_owner, pg_context.runtime.begin() as conn:
        workflow_history(conn, uuid4(), task.id, events=True)
    assert wrong_owner.value.code == "workflow_not_found"
    for limit in (0, 101):
        with pytest.raises(Problem), pg_context.runtime.begin() as conn:
            workflow_history(conn, task.owner_id, task.id, limit=limit)


@pytest.mark.postgres
def test_workflow_cursor_binds_owner_state_and_creation_window(pg_context: PGContext) -> None:
    keyring = Keyring("synthetic", {"synthetic": b"k" * 32})
    ids = [_enqueue(pg_context) for _ in range(4)]
    with pg_context.runtime.begin() as conn:
        first = list_workflows(conn, pg_context.owner_id, keyring, state="pending", limit=2)
    cursor = cast(str, first["next_cursor"])
    late = _enqueue(pg_context)
    with pg_context.runtime.begin() as conn:
        second = list_workflows(
            conn, pg_context.owner_id, keyring, state="pending", limit=2, cursor=cursor
        )
        combined = cast(list[dict[str, Any]], first["items"]) + cast(
            list[dict[str, Any]], second["items"]
        )
        assert {item["id"] for item in combined} == {str(item) for item in ids}
        assert str(late) not in {item["id"] for item in combined}
        assert second["next_cursor"] is None
        for owner, state, invalid in (
            (uuid4(), "pending", cursor),
            (pg_context.owner_id, "running", cursor),
            (pg_context.owner_id, "pending", cursor + "x"),
        ):
            with pytest.raises(Problem) as blocked:
                list_workflows(conn, owner, keyring, state=cast(Any, state), cursor=invalid)
            assert blocked.value.code == "invalid_cursor"


@pytest.mark.postgres
def test_retry_http_security_preconditions_exact_replay_and_control_version(
    catalog: SourceContext,
) -> None:
    workflow = _retryable(catalog)
    route = f"/api/v1/workflows/{workflow['id']}/retry"
    headers = catalog.headers(workflow["version"])
    with catalog.identity.client() as anonymous:
        assert anonymous.post(route, json={}, headers=headers).status_code == 401
    assert (
        catalog.client.post(
            route, json={}, headers={**headers, "Origin": "https://wrong.invalid"}
        ).status_code
        == 403
    )
    missing_csrf = {key: value for key, value in headers.items() if key != "X-CSRF-Token"}
    assert catalog.client.post(route, json={}, headers=missing_csrf).status_code == 403
    assert catalog.client.post(route, json={}, headers=catalog.headers()).status_code == 428
    no_key = {key: value for key, value in headers.items() if key != "Idempotency-Key"}
    assert catalog.client.post(route, json={}, headers=no_key).status_code == 400
    assert catalog.client.post(route, json={"reset": True}, headers=headers).status_code == 422
    with catalog.runtime.transaction() as conn:
        workspace = conn.execute(
            text("SELECT workspace_version FROM offerloom.owner_settings WHERE owner_id=:owner"),
            {"owner": catalog.owner_id},
        ).scalar_one()
    success = catalog.client.post(route, json={}, headers=headers)
    assert success.status_code == 200, success.text
    assert success.json()["state"] == "pending" and success.json()["attempt"] == workflow["attempt"]
    assert success.headers["etag"] == f'"{success.json()["version"]}"'
    replay = catalog.client.post(route, json={}, headers=headers)
    assert replay.status_code == 200 and replay.json() == success.json()
    assert (
        catalog.client.post(
            route, json={}, headers=catalog.headers(workflow["version"])
        ).status_code
        == 412
    )
    assert (
        catalog.client.post(
            route, json={}, headers=catalog.headers(success.json()["version"])
        ).status_code
        == 409
    )
    with catalog.runtime.transaction() as conn:
        assert (
            conn.execute(
                text(
                    "SELECT workspace_version FROM offerloom.owner_settings WHERE owner_id=:owner"
                ),
                {"owner": catalog.owner_id},
            ).scalar_one()
            == workspace
        )
        count = conn.execute(
            text(
                "SELECT count(*) FROM offerloom.workflow_attempt_events WHERE owner_id=:owner AND workflow_run_id=:id AND event='retry_requested'"
            ),
            {"owner": catalog.owner_id, "id": UUID(workflow["id"])},
        ).scalar_one()
        assert count == 1


@pytest.mark.postgres
def test_task_http_pagination_bounds_absence_and_owner_scope(catalog: SourceContext) -> None:
    ids = [_queue(catalog) for _ in range(3)]
    first = catalog.client.get("/api/v1/workflows?limit=2&state=pending")
    assert (
        first.status_code == 200 and len(first.json()["items"]) == 2 and first.json()["next_cursor"]
    )
    second = catalog.client.get(
        "/api/v1/workflows",
        params={"limit": 2, "state": "pending", "cursor": first.json()["next_cursor"]},
    )
    assert second.status_code == 200 and len(second.json()["items"]) == 1
    assert {item["id"] for item in first.json()["items"] + second.json()["items"]} == {
        str(item) for item in ids
    }
    for path in (
        "/api/v1/workflows?limit=0",
        "/api/v1/workflows?limit=101",
        "/api/v1/workflows?state=made_up",
        f"/api/v1/workflows/{ids[0]}/steps?limit=101",
        f"/api/v1/workflows/{ids[0]}/attempt-events?limit=0",
    ):
        assert catalog.client.get(path).status_code == 422
    detail = catalog.workflow(ids[0])
    assert detail["approval"] is None and detail["steps"] == {"items": [], "next_cursor": None}
    assert detail["attempt_events"] == {"items": [], "next_cursor": None}
    assert "input" not in detail
    assert catalog.client.get(f"/api/v1/workflows/{ids[0]}/approval").status_code == 404
    absent = uuid4()
    assert catalog.client.get(f"/api/v1/workflows/{absent}").status_code == 404
    assert catalog.client.get(f"/api/v1/workflows/{absent}/steps").status_code == 404
    assert (
        catalog.client.post(
            f"/api/v1/workflows/{absent}/retry", json={}, headers=catalog.headers(1)
        ).status_code
        == 404
    )


@pytest.mark.postgres
def test_concurrent_retry_commands_serialize_and_cancellation_is_local_only(
    catalog: SourceContext,
) -> None:
    workflow = _retryable(catalog)
    route = f"/api/v1/workflows/{workflow['id']}"
    retry_headers = catalog.headers(workflow["version"])
    cancel_headers = catalog.headers(workflow["version"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        retry = pool.submit(catalog.client.post, route + "/retry", json={}, headers=retry_headers)
        cancel = pool.submit(
            catalog.client.post, route + "/cancel", json={}, headers=cancel_headers
        )
        responses = [retry.result(), cancel.result()]
    assert sorted(response.status_code for response in responses) == [200, 412]
    current = catalog.workflow(workflow["id"])
    if current["state"] != "cancelled":
        cancelled = catalog.client.post(
            route + "/cancel", json={}, headers=catalog.headers(current["version"])
        )
        assert cancelled.status_code == 200, cancelled.text
        current = cancelled.json()
    assert current["cancellation"] == {
        "requested": True,
        "local_fence_settled": True,
        "provider_stopped": None,
        "completed_effects_preserved": True,
        "reason": "local_publication_revoked_provider_may_continue",
    }
    with catalog.runtime.transaction() as conn:
        assert claim(conn, uuid4()) is None


@pytest.mark.postgres
def test_retry_cannot_bypass_source_revocation_or_retry_after(catalog: SourceContext) -> None:
    source = catalog.create()
    response = catalog.refresh(source)
    assert response.status_code == 202, response.text
    identifier = UUID(response.json()["workflow_id"])
    with catalog.runtime.transaction() as conn:
        task = claim(conn, uuid4())
        assert task is not None and task.id == identifier
        fail(conn, task, "source_rate_limited", True, minimum_retry_delay_seconds=60)
    delayed = catalog.workflow(identifier)
    refused = catalog.client.post(
        f"/api/v1/workflows/{identifier}/retry",
        json={},
        headers=catalog.headers(delayed["version"]),
    )
    assert refused.status_code == 409 and refused.json()["code"] == "retry_not_due"
    with catalog.identity.admin.transaction() as conn:
        conn.execute(
            text(
                "UPDATE offerloom.workflow_runs SET next_attempt_at=clock_timestamp()-interval '1 second' WHERE owner_id=:owner AND id=:id"
            ),
            {"owner": catalog.owner_id, "id": identifier},
        )
        conn.execute(
            text(
                "UPDATE offerloom.source_configs SET enabled=false,current_policy_review_id=NULL,terms_reviewed_at=NULL,version=version+1,policy_version=policy_version+1 WHERE owner_id=:owner AND id=:id"
            ),
            {"owner": catalog.owner_id, "id": UUID(source["id"])},
        )
    revoked = catalog.workflow(identifier)
    assert not revoked["retryable"] and revoked["retry_block_reason"] == "source_unavailable"
    refused = catalog.client.post(
        f"/api/v1/workflows/{identifier}/retry",
        json={},
        headers=catalog.headers(revoked["version"]),
    )
    assert refused.status_code == 409 and refused.json()["code"] == "source_unavailable"


@pytest.mark.postgres
def test_retry_retains_model_reservation_and_all_cumulative_accounting(
    task_model_context: PGContext,
) -> None:
    pg = task_model_context
    _enqueue(pg)
    task = _claim(pg)
    with pg.runtime.begin() as conn:
        fail(conn, task, "temporary_local_unavailable", True)
    _due(pg, task)
    with pg.migrator.begin() as conn:
        conn.execute(
            text("""UPDATE offerloom.workflow_runs SET token_budget=100,tokens_used=30,
            cost_budget_microunits=10,cost_used_microunits=3,model_network_attempts=1
            WHERE owner_id=:owner AND id=:id"""),
            {"owner": task.owner_id, "id": task.id},
        )
        conn.execute(
            text("""UPDATE offerloom.local_model_reservations SET owner_id=:owner,workflow_run_id=:id,
            fence=:fence,reserved_until=clock_timestamp()+interval '2 minutes' WHERE singleton"""),
            {"owner": task.owner_id, "id": task.id, "fence": task.fence},
        )
        conn.execute(
            text("""INSERT INTO offerloom.model_runs(owner_id,workflow_run_id,provider_key,model_id,task_kind,
            prompt_version,input_sha256,parameters,egress_class,reserved_tokens,reserved_cost_microunits,status)
            VALUES(:owner,:id,'synthetic','synthetic','synthetic','synthetic',repeat('0',64),'{}','local',30,3,'running')"""),
            {"owner": task.owner_id, "id": task.id},
        )
    with pg.runtime.begin() as conn:
        reservation = conn.execute(
            text("SELECT to_jsonb(r) FROM offerloom.local_model_reservations r WHERE singleton")
        ).scalar_one()
        runs = conn.execute(
            text("SELECT to_jsonb(r) FROM offerloom.model_runs r WHERE owner_id=:owner"),
            {"owner": task.owner_id},
        ).all()
        original = get_workflow(conn, task.owner_id, task.id)
        updated = retry_workflow(conn, task.owner_id, task.id)
        assert updated["attempt"] == original["attempt"]
        assert (
            updated["deadline_at"] == original["deadline_at"]
            and updated["next_attempt_at"] == original["next_attempt_at"]
        )
        assert updated["usage"] == original["usage"]
        assert (
            conn.execute(
                text("SELECT to_jsonb(r) FROM offerloom.local_model_reservations r WHERE singleton")
            ).scalar_one()
            == reservation
        )
        assert (
            conn.execute(
                text("SELECT to_jsonb(r) FROM offerloom.model_runs r WHERE owner_id=:owner"),
                {"owner": task.owner_id},
            ).all()
            == runs
        )
        assert cast(dict[str, Any], updated["usage"])["cost_used_microunits"] is None


@pytest.mark.postgres
def test_attempt_events_paginate_actual_interruption_and_reclaim_checkpoints(
    pg_context: PGContext,
) -> None:
    identifier = _enqueue(pg_context)
    with pg_context.migrator.begin() as conn:
        conn.execute(
            text(
                "UPDATE offerloom.workflow_runs SET max_attempts=10 WHERE owner_id=:owner AND id=:id"
            ),
            {"owner": pg_context.owner_id, "id": identifier},
        )
    for attempt in range(1, 10):
        task = _claim(pg_context)
        assert task.attempt == attempt
        with pg_context.migrator.begin() as conn:
            conn.execute(
                text(
                    "UPDATE offerloom.workflow_runs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE owner_id=:owner AND id=:id"
                ),
                {"owner": task.owner_id, "id": task.id},
            )
        with pg_context.runtime.begin() as conn:
            assert reap(conn) == 1
        _due(pg_context, task)
        with pg_context.runtime.begin() as conn:
            retry_workflow(conn, task.owner_id, task.id)
    final = _claim(pg_context)
    assert final.attempt == 10
    with pg_context.runtime.begin() as conn:
        publish(conn, final, lambda active: {"completed": True})
        first = workflow_history(conn, final.owner_id, identifier, events=True)
        second = workflow_history(
            conn,
            final.owner_id,
            identifier,
            events=True,
            before=UUID(cast(str, first["next_cursor"])),
        )
        events = cast(list[dict[str, Any]], first["items"]) + cast(
            list[dict[str, Any]], second["items"]
        )
        assert (
            len(cast(list[dict[str, Any]], first["items"])) == 30
            and len(events) == 38
            and second["next_cursor"] is None
        )
        assert len({event["id"] for event in events}) == 38
        assert sum(event["event"] == "claimed" for event in events) == 10
        assert sum(event["event"] == "interrupted" for event in events) == 9
        other = _enqueue(pg_context)
    with pytest.raises(Problem) as wrong_parent, pg_context.runtime.begin() as conn:
        workflow_history(conn, final.owner_id, other, events=True, before=UUID(events[0]["id"]))
    assert wrong_parent.value.code == "invalid_cursor"


@pytest.mark.postgres
def test_import_review_expiry_still_terminalizes_without_reopening(pg_context: PGContext) -> None:
    with pg_context.runtime.begin() as conn:
        identifier = enqueue(
            conn, pg_context.owner_id, "profile_import", {"import_id": str(uuid4())}
        )
    task = _claim(pg_context)
    with pg_context.migrator.begin() as conn:
        conn.execute(
            text("""UPDATE offerloom.workflow_runs SET state='waiting_for_approval',lease_holder=NULL,
            lease_expires_at=NULL,created_at=clock_timestamp()-interval '181 seconds',
            deadline_at=clock_timestamp()-interval '1 second'
            WHERE owner_id=:owner AND id=:id"""),
            {"owner": task.owner_id, "id": identifier},
        )
    with pg_context.runtime.begin() as conn:
        assert reap(conn) == 1
        result = get_workflow(conn, task.owner_id, identifier)
        assert (
            result["state"] == "permanent_failure"
            and result["last_error_code"] == "import_review_expired"
        )
        assert result["finished_at"] is not None and claim(conn, uuid4()) is None


@pytest.mark.postgres
def test_retry_cannot_restore_superseded_profile_source(pg_context: PGContext) -> None:
    _enqueue(pg_context)
    task = _claim(pg_context)
    with pg_context.runtime.begin() as conn:
        fail(conn, task, "temporary_local_unavailable", True)
    _due(pg_context, task)
    replacement = uuid4()
    with pg_context.migrator.begin() as conn:
        conn.execute(
            text("""INSERT INTO offerloom.profile_revisions(owner_id,id,profile_id,revision_no,snapshot,content_sha256,reason)
            VALUES(:owner,:revision,:profile,2,'{}',repeat('1',64),'synthetic changed source')"""),
            {"owner": task.owner_id, "revision": replacement, "profile": pg_context.profile_id},
        )
        conn.execute(
            text(
                "UPDATE offerloom.profiles SET current_revision_id=:revision WHERE owner_id=:owner AND id=:profile"
            ),
            {"owner": task.owner_id, "revision": replacement, "profile": pg_context.profile_id},
        )
    with pytest.raises(Problem) as withdrawn, pg_context.runtime.begin() as conn:
        retry_workflow(conn, task.owner_id, task.id)
    assert withdrawn.value.code == "source_unavailable"


@pytest.mark.postgres
@pytest.mark.parametrize(
    "field,value",
    [
        ("token_budget", 9007199254740992),
        ("cost_budget_microunits", 9007199254740992),
        ("version", 9007199254740992),
        ("result", {"nested": {"count": 9007199254740992}}),
        ("result", {"fraction": 0.5}),
    ],
)
def test_noncanonical_persisted_numbers_refuse_without_clamping_or_retry_mutation(
    pg_context: PGContext,
    field: str,
    value: object,
) -> None:
    _enqueue(pg_context)
    task = _claim(pg_context)
    with pg_context.runtime.begin() as conn:
        fail(conn, task, "temporary_local_unavailable", True)
    _due(pg_context, task)
    assert field in {"token_budget", "cost_budget_microunits", "version", "result"}
    expression = "CAST(:value AS jsonb)" if field == "result" else ":value"
    with pg_context.migrator.begin() as conn:
        conn.execute(
            text(
                f"UPDATE offerloom.workflow_runs SET {field}={expression} WHERE owner_id=:owner AND id=:id"
            ),
            {
                "owner": task.owner_id,
                "id": task.id,
                "value": json.dumps(value) if field == "result" else value,
            },
        )
    with pytest.raises(Problem) as summary, pg_context.runtime.begin() as conn:
        get_workflow(conn, task.owner_id, task.id)
    assert summary.value.code == "workflow_number_not_exact"
    with pytest.raises(Problem) as retry, pg_context.runtime.begin() as conn:
        retry_workflow(conn, task.owner_id, task.id)
    assert retry.value.code == "workflow_number_not_exact"
    with pg_context.runtime.begin() as conn:
        assert (
            conn.execute(
                text("SELECT state FROM offerloom.workflow_runs WHERE owner_id=:owner AND id=:id"),
                {"owner": task.owner_id, "id": task.id},
            ).scalar_one()
            == "retryable_failure"
        )
        assert (
            conn.execute(
                text("""SELECT count(*) FROM offerloom.workflow_attempt_events
            WHERE owner_id=:owner AND workflow_run_id=:id AND event='retry_requested'"""),
                {"owner": task.owner_id, "id": task.id},
            ).scalar_one()
            == 0
        )


@pytest.mark.postgres
def test_document_retry_preserves_separate_historical_and_current_profile_pins(
    pg_context: PGContext,
) -> None:
    _enqueue(pg_context)
    task = _claim(pg_context)
    with pg_context.runtime.begin() as conn:
        fail(conn, task, "temporary_local_unavailable", True)
    _due(pg_context, task)
    replacement = uuid4()
    with pg_context.migrator.begin() as conn:
        conn.execute(
            text("""INSERT INTO offerloom.profile_revisions(owner_id,id,profile_id,revision_no,snapshot,content_sha256,reason)
            VALUES(:owner,:revision,:profile,2,'{}',repeat('1',64),'synthetic retained historical facts')"""),
            {"owner": task.owner_id, "revision": replacement, "profile": pg_context.profile_id},
        )
        conn.execute(
            text(
                "UPDATE offerloom.profiles SET current_revision_id=:revision WHERE owner_id=:owner AND id=:profile"
            ),
            {"owner": task.owner_id, "revision": replacement, "profile": pg_context.profile_id},
        )
        conn.execute(
            text("""UPDATE offerloom.workflow_runs SET input=input || jsonb_build_object('current_profile_revision_id',CAST(:revision AS text))
            WHERE owner_id=:owner AND id=:id"""),
            {"owner": task.owner_id, "id": task.id, "revision": replacement},
        )
    with pg_context.runtime.begin() as conn:
        result = retry_workflow(conn, task.owner_id, task.id)
        assert result["state"] == "pending"
        snapshot = conn.execute(
            text("SELECT input FROM offerloom.workflow_runs WHERE owner_id=:owner AND id=:id"),
            {"owner": task.owner_id, "id": task.id},
        ).scalar_one()
        assert snapshot["profile_revision_id"] == str(pg_context.revision_id)
        assert snapshot["current_profile_revision_id"] == str(replacement)


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("kind='posting_extract',model_network_attempts=2", "model_attempts_exhausted"),
        ("token_budget=12,tokens_used=12", "workflow_budget_exhausted"),
        ("cost_budget_microunits=9,cost_used_microunits=9", "workflow_budget_exhausted"),
    ],
)
def test_reaper_dead_letters_exhausted_cumulative_allowance_without_another_claim(
    pg_context: PGContext,
    mutation: str,
    reason: str,
) -> None:
    _enqueue(pg_context)
    task = _claim(pg_context)
    with pg_context.runtime.begin() as conn:
        fail(conn, task, "temporary_local_unavailable", True)
    _due(pg_context, task)
    with pg_context.migrator.begin() as conn:
        conn.execute(
            text(f"UPDATE offerloom.workflow_runs SET {mutation} WHERE owner_id=:owner AND id=:id"),
            {"owner": task.owner_id, "id": task.id},
        )
    with pg_context.runtime.begin() as conn:
        assert claim(conn, uuid4()) is None
        assert reap(conn) == 1
        result = get_workflow(conn, task.owner_id, task.id)
        assert result["state"] == "permanent_failure" and result["last_error_code"] == reason
        assert result["attempt"] == 1 and result["finished_at"] is not None
        events = cast(
            list[dict[str, Any]],
            workflow_history(conn, task.owner_id, task.id, events=True)["items"],
        )
        assert sum(event["event"] == "claimed" for event in events) == 1
        assert events[0]["event"] == "permanent_failure" and events[0]["error_code"] == reason


@pytest.mark.postgres
def test_reviewed_import_continuation_remains_retryable_without_renewing_allowance(
    importing: ImportContext,
) -> None:
    # The existing fixture isolates parser transport; every queue/review/merge is real SQL.
    batch = importing.staged()
    identifier = UUID(batch["workflow_id"])
    detail = importing.client.get(f"/api/v1/workflows/{identifier}").json()
    assert detail["state"] == "waiting_for_approval" and detail["approval"] is None
    approved = importing.approve(batch)
    assert approved.status_code == 202, approved.text
    with importing.runtime.transaction() as conn:
        task = claim(conn, uuid4())
        assert task is not None and task.id == identifier and task.attempt == 2
        fail(conn, task, "temporary_local_unavailable", True)
    with importing.identity.admin.transaction() as conn:
        conn.execute(
            text(
                "UPDATE offerloom.workflow_runs SET next_attempt_at=clock_timestamp()-interval '1 second' WHERE owner_id=:owner AND id=:id"
            ),
            {"owner": importing.owner_id, "id": identifier},
        )
    original = importing.client.get(f"/api/v1/workflows/{identifier}").json()
    retry = importing.client.post(
        f"/api/v1/workflows/{identifier}/retry",
        json={},
        headers=importing.headers(original["version"]),
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()["deadline_at"] == original["deadline_at"]
    assert retry.json()["attempt"] == original["attempt"] and retry.json()["max_attempts"] == 4
    completed = importing.run()
    assert completed.id == identifier and completed.attempt == 3
    assert importing.batch(batch["id"])["state"] == "succeeded"
    detail = importing.client.get(f"/api/v1/workflows/{identifier}").json()
    events = [item["event"] for item in reversed(detail["attempt_events"]["items"])]
    assert events == [
        "claimed",
        "waiting_for_approval",
        "approved",
        "claimed",
        "retryable_failure",
        "retry_requested",
        "claimed",
        "succeeded",
    ]


@pytest.mark.postgres
def test_task_detail_polling_serializes_before_profile_fact_edits(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = _profile(catalog)
    selected = profile["current_revision"]["claims"][0]
    original_revision = selected["revision"]
    original_text = original_revision["payload"]["text"]
    replacement_text = original_text + " Synthetic reviewed correction."
    body = {
        "kind": selected["kind"],
        "payload": {**original_revision["payload"], "text": replacement_text},
        "evidence_links": [
            {key: link[key] for key in ("evidence_revision_id", "support_kind", "excerpt")}
            for link in original_revision["evidence_links"]
        ],
    }
    task = _retryable(catalog)
    identifier = UUID(task["id"])
    editor_ready, reader_ready, release = Event(), Event(), Event()
    backend_pids = [0, 0]
    original_write = profile_entries.write_claim
    original_get = workflows.get_workflow

    def pause_editor(
        conn: Connection,
        owner_id: UUID,
        value: profile_entries.ClaimWrite,
        entity_id: UUID | None,
    ) -> dict[str, object]:
        conn.execute(text("SET LOCAL lock_timeout='15s'"))
        conn.execute(text("SET LOCAL statement_timeout='20s'"))
        backend_pids[0] = int(conn.execute(text("SELECT pg_backend_pid()")).scalar_one())
        editor_ready.set()
        assert release.wait(15), "test did not release the profile editor"
        return original_write(conn, owner_id, value, entity_id)

    def observe_reader(conn: Connection, owner_id: UUID, workflow_id: UUID) -> dict[str, object]:
        if workflow_id == identifier:
            conn.execute(text("SET LOCAL lock_timeout='15s'"))
            conn.execute(text("SET LOCAL statement_timeout='20s'"))
            backend_pids[1] = int(conn.execute(text("SELECT pg_backend_pid()")).scalar_one())
            reader_ready.set()
        return original_get(conn, owner_id, workflow_id)

    def request(
        method: str,
        path: str,
        value: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response | str:
        try:
            return cast(
                httpx.Response, catalog.client.request(method, path, json=value, headers=headers)
            )
        except DBAPIError as exc:
            return str(getattr(exc.orig, "sqlstate", "database_error"))

    monkeypatch.setattr(profile_entries, "write_claim", pause_editor)
    monkeypatch.setattr(workflows, "get_workflow", observe_reader)
    with ThreadPoolExecutor(max_workers=2) as pool:
        editor = pool.submit(
            request,
            "PUT",
            f"/api/v1/claims/{selected['id']}",
            body,
            catalog.headers(profile["version"]),
        )
        try:
            assert editor_ready.wait(10), "profile edit did not reach its owner-locked effect"
            reader = pool.submit(request, "GET", f"/api/v1/workflows/{identifier}")
            assert reader_ready.wait(10), "task detail request did not start"
            blocked = False
            deadline = time.monotonic() + 10
            while not blocked and time.monotonic() < deadline:
                with catalog.identity.admin.transaction() as conn:
                    blocked = bool(
                        conn.execute(
                            text("SELECT :editor=ANY(pg_blocking_pids(:reader))"),
                            {"editor": backend_pids[0], "reader": backend_pids[1]},
                        ).scalar_one()
                    )
                if not blocked:
                    release.wait(0.01)
            assert blocked, "task detail neither serialized behind nor waited on the profile edit"
        finally:
            release.set()
        edited, polled = editor.result(timeout=20), reader.result(timeout=20)
    assert isinstance(edited, httpx.Response), f"profile edit database error: {edited}"
    assert isinstance(polled, httpx.Response), f"task detail database error: {polled}"
    assert edited.status_code == 200, edited.text
    assert polled.status_code == 200, polled.text
    current = catalog.client.get("/api/v1/profile").json()
    current_claim = next(
        item for item in current["current_revision"]["claims"] if item["id"] == selected["id"]
    )
    assert current_claim["revision"]["payload"]["text"] == replacement_text
    assert current_claim["revision"]["id"] != original_revision["id"]
    historical = catalog.client.get(
        f"/api/v1/claims/{selected['id']}/revisions/{original_revision['id']}"
    )
    assert historical.status_code == 200 and historical.json()["payload"]["text"] == original_text
    result = polled.json()
    assert result["state"] == "retryable_failure" and result["attempt"] == 1
    assert not result["retryable"] and result["retry_block_reason"] == "source_unavailable"
    assert [item["event"] for item in reversed(result["attempt_events"]["items"])] == [
        "claimed",
        "retryable_failure",
    ]
