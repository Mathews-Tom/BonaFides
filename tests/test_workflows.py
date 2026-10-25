# SPDX-License-Identifier: MIT OR Apache-2.0
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError
from test_database import PGContext
from test_database import pg_context as pg_context

from offerloom.commands import append_outbox, canonical_json, require_if_match
from offerloom.problems import Problem
from offerloom.workflows import (
    Claim,
    cancel_workflow,
    claim,
    claim_outbox,
    deliver_outbox,
    enqueue,
    fail,
    get_workflow,
    heartbeat,
    publish,
    reap,
)


def test_strict_quoted_if_match() -> None:
    assert require_if_match('"1"') == 1
    for value in (None, "1", 'W/"1"', '"01"', '"0"', '"1", "2"', '"9223372036854775808"'):
        with pytest.raises(Problem):
            require_if_match(value)


def test_normalized_canonical_request() -> None:
    assert canonical_json({"z": [True, None], "a": "Mira"}) == '{"a":"Mira","z":[true,null]}'
    assert canonical_json({"\ue000": 1, "\U00010000": 2}) == '{"\U00010000":2,"\ue000":1}'
    with pytest.raises(Problem):
        canonical_json({"not_in_m0": float("nan")})


def _enqueue(pg: PGContext) -> UUID:
    with pg.runtime.begin() as conn:
        return enqueue(
            conn,
            pg.owner_id,
            "document_generate",
            {"profile_revision_id": str(pg.revision_id), "scenario": "normal"},
        )


def _claim(pg: PGContext) -> Claim:
    with pg.runtime.begin() as conn:
        task = claim(conn, uuid4())
    assert task is not None
    return task


def _expire(pg: PGContext, task: Claim) -> None:
    with pg.migrator.begin() as conn:
        conn.execute(
            text(
                "UPDATE offerloom.workflow_runs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE owner_id=:owner AND id=:id"
            ),
            {"owner": task.owner_id, "id": task.id},
        )
    with pg.runtime.begin() as conn:
        assert reap(conn) == 1
    with pg.migrator.begin() as conn:
        conn.execute(
            text(
                "UPDATE offerloom.workflow_runs SET next_attempt_at=clock_timestamp() WHERE owner_id=:owner AND id=:id"
            ),
            {"owner": task.owner_id, "id": task.id},
        )


@pytest.mark.postgres
def test_two_concurrent_claims_skip_locked(pg_context: PGContext) -> None:
    one, two = _enqueue(pg_context), _enqueue(pg_context)
    with pg_context.runtime.begin() as first:
        first_claim = claim(first, uuid4())
        assert first_claim is not None
        with pg_context.runtime.begin() as second:
            second_claim = claim(second, uuid4())
            assert second_claim is not None
            assert {first_claim.id, second_claim.id} == {one, two}
            assert second_claim.id != first_claim.id
    with pg_context.runtime.begin() as conn:
        assert claim(conn, uuid4()) is None


@pytest.mark.postgres
def test_stale_fence_and_heartbeat_rejected_after_reclaim(pg_context: PGContext) -> None:
    _enqueue(pg_context)
    old = _claim(pg_context)
    _expire(pg_context, old)
    current = _claim(pg_context)
    assert current.id == old.id and current.fence > old.fence and current.attempt == 2
    with pg_context.runtime.begin() as conn:
        assert not heartbeat(conn, old)
        assert heartbeat(conn, current)
    with pytest.raises(DBAPIError), pg_context.runtime.begin() as conn:
        publish(conn, old, lambda active: {"forbidden": "late"})
    with pg_context.runtime.begin() as conn:
        publish(conn, current, lambda active: {"accepted": "current"})
    with pg_context.runtime.begin() as conn:
        assert get_workflow(conn, current.owner_id, current.id)["result"] == {"accepted": "current"}
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM offerloom.workflow_steps WHERE owner_id=:owner AND workflow_run_id=:id AND step_key='publish'"
                ),
                {"owner": current.owner_id, "id": current.id},
            ).scalar_one()
            == 1
        )


@pytest.mark.postgres
def test_domain_effect_outbox_atomic_rollback(pg_context: PGContext) -> None:
    _enqueue(pg_context)
    task = _claim(pg_context)

    def effect(conn: Connection) -> dict[str, object]:
        conn.execute(
            text(
                "UPDATE offerloom.owner_settings SET settings=CAST(:settings AS jsonb) WHERE owner_id=:owner"
            ),
            {"owner": task.owner_id, "settings": '{"must_rollback":true}'},
        )
        append_outbox(
            conn, task.owner_id, "rollback.proof", "workflow", task.id, {"synthetic": True}
        )
        raise RuntimeError("injected transaction failure")

    with pytest.raises(RuntimeError), pg_context.runtime.begin() as conn:
        publish(conn, task, effect)
    with pg_context.runtime.begin() as conn:
        assert (
            conn.execute(
                text("SELECT settings FROM offerloom.owner_settings WHERE owner_id=:owner"),
                {"owner": task.owner_id},
            ).scalar_one()
            == {}
        )
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM offerloom.outbox_events WHERE owner_id=:owner AND event_type='workflow.succeeded'"
                ),
                {"owner": task.owner_id},
            ).scalar_one()
            == 0
        )
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM offerloom.outbox_events WHERE owner_id=:owner AND event_type='rollback.proof'"
                ),
                {"owner": task.owner_id},
            ).scalar_one()
            == 0
        )
        assert get_workflow(conn, task.owner_id, task.id)["state"] == "running"


@pytest.mark.postgres
def test_cancellation_revokes_publication_and_preserves_prior_result(pg_context: PGContext) -> None:
    workflow_id = _enqueue(pg_context)
    task = _claim(pg_context)
    with pg_context.migrator.begin() as conn:
        conn.execute(
            text(
                'UPDATE offerloom.workflow_runs SET result=\'{"checkpoint":"preserved"}\' WHERE owner_id=:owner AND id=:id'
            ),
            {"owner": task.owner_id, "id": task.id},
        )
    with pg_context.runtime.begin() as conn:
        cancelled = cancel_workflow(conn, task.owner_id, workflow_id)
        assert cancelled["state"] == "cancelled"
        assert cancelled["result"] == {"checkpoint": "preserved"}
    with pytest.raises(DBAPIError), pg_context.runtime.begin() as conn:
        publish(conn, task, lambda active: {"forbidden": True})
    with pg_context.runtime.begin() as conn:
        assert claim(conn, uuid4()) is None


@pytest.mark.postgres
def test_deterministic_poison_is_visible_and_not_retried_forever(pg_context: PGContext) -> None:
    workflow_id = _enqueue(pg_context)
    first = _claim(pg_context)
    with pg_context.runtime.begin() as conn:
        fail(conn, first, "benchmark_poison", True)
    with pg_context.migrator.begin() as conn:
        conn.execute(
            text(
                "UPDATE offerloom.workflow_runs SET next_attempt_at=clock_timestamp() WHERE owner_id=:owner AND id=:id"
            ),
            {"owner": first.owner_id, "id": first.id},
        )
    second = _claim(pg_context)
    with pg_context.runtime.begin() as conn:
        fail(conn, second, "benchmark_poison", True)
    with pg_context.runtime.begin() as conn:
        result = get_workflow(conn, first.owner_id, workflow_id)
        assert result["state"] == "permanent_failure"
        assert result["last_error_code"] == "benchmark_poison"
        assert result["attempt"] == 2
        assert claim(conn, uuid4()) is None


@pytest.mark.postgres
def test_outbox_independent_fence_and_duplicate_consumer_receipt(pg_context: PGContext) -> None:
    _enqueue(pg_context)
    with pg_context.runtime.begin() as conn:
        first = claim_outbox(conn, uuid4())
    assert first is not None
    with pg_context.migrator.begin() as conn:
        conn.execute(
            text(
                "UPDATE offerloom.outbox_events SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE owner_id=:owner AND id=:id"
            ),
            {"owner": first.owner_id, "id": first.id},
        )
    with pg_context.runtime.begin() as conn:
        second = claim_outbox(conn, uuid4())
    assert second is not None and second.id == first.id and second.fence > first.fence
    with pg_context.runtime.begin() as conn:
        assert not deliver_outbox(conn, first)
        assert deliver_outbox(conn, second)
        assert not deliver_outbox(conn, second)
    # Simulate an acknowledgment loss: the same event is made deliverable again.
    with pg_context.migrator.begin() as conn:
        conn.execute(
            text(
                "UPDATE offerloom.outbox_events SET delivered_at=NULL WHERE owner_id=:owner AND id=:id"
            ),
            {"owner": first.owner_id, "id": first.id},
        )
    with pg_context.runtime.begin() as conn:
        duplicate = claim_outbox(conn, uuid4())
    assert duplicate is not None
    with pg_context.runtime.begin() as conn:
        assert deliver_outbox(conn, duplicate)
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM offerloom.event_receipts WHERE owner_id=:owner AND event_id=:id"
                ),
                {"owner": first.owner_id, "id": first.id},
            ).scalar_one()
            == 1
        )
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM offerloom.event_metadata WHERE owner_id=:owner AND event_id=:id"
                ),
                {"owner": first.owner_id, "id": first.id},
            ).scalar_one()
            == 1
        )


@pytest.mark.postgres
def test_failure_preserves_prior_result_and_attempt_cap(pg_context: PGContext) -> None:
    workflow_id = _enqueue(pg_context)
    for expected_attempt in range(1, 4):
        current = _claim(pg_context)
        assert current.attempt == expected_attempt
        with pg_context.migrator.begin() as conn:
            conn.execute(
                text(
                    'UPDATE offerloom.workflow_runs SET result=\'{"checkpoint":"preserved"}\' WHERE owner_id=:owner AND id=:id'
                ),
                {"owner": current.owner_id, "id": current.id},
            )
        with pg_context.runtime.begin() as conn:
            fail(conn, current, "temporary_local_unavailable", True)
        with pg_context.migrator.begin() as conn:
            conn.execute(
                text(
                    "UPDATE offerloom.workflow_runs SET next_attempt_at=clock_timestamp() WHERE owner_id=:owner AND id=:id"
                ),
                {"owner": current.owner_id, "id": current.id},
            )
    with pg_context.runtime.begin() as conn:
        result = get_workflow(conn, pg_context.owner_id, workflow_id)
        assert result["state"] == "permanent_failure"
        assert result["attempt"] == 3
        assert result["result"] == {"checkpoint": "preserved"}
        assert claim(conn, uuid4()) is None


@pytest.mark.postgres
def test_deadline_reaper_revokes_claim(pg_context: PGContext) -> None:
    _enqueue(pg_context)
    current = _claim(pg_context)
    # Keep the reviewed deadline>created_at constraint while advancing the run's
    # synthetic timestamps to a fully elapsed execution segment.
    with pg_context.migrator.begin() as conn:
        conn.execute(
            text("""UPDATE offerloom.workflow_runs SET
            created_at=clock_timestamp()-interval '181 seconds',
            deadline_at=clock_timestamp()-interval '1 second'
            WHERE owner_id=:owner AND id=:id"""),
            {"owner": current.owner_id, "id": current.id},
        )
    with pg_context.runtime.begin() as conn:
        assert not heartbeat(conn, current)
        assert reap(conn) == 1
        result = get_workflow(conn, current.owner_id, current.id)
        assert result["state"] == "permanent_failure"
        assert result["last_error_code"] == "deadline_exceeded"
    with pytest.raises(DBAPIError), pg_context.runtime.begin() as conn:
        publish(conn, current, lambda active: {"forbidden": True})
