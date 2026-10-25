# SPDX-License-Identifier: MIT OR Apache-2.0
"""Real worker execution, PostgreSQL recovery, and bounded local HTTP fixtures."""

from __future__ import annotations

from pathlib import Path
from threading import Event, Thread
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from test_identity import identity as identity
from test_local_model import fixture_server, qualified_route, route_data
from test_sources import SourceContext
from test_sources import catalog as catalog
from test_suitability import _posting, _queue, _view

from offerloom import local_model
from offerloom.worker import Worker
from offerloom.workflows import claim, reap


@pytest.mark.postgres
@pytest.mark.parametrize("recovery", ["resume", "cancel", "deadline", "exhausted"])
def test_real_worker_shutdown_keeps_model_execution_under_database_recovery_authority(
    catalog: SourceContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recovery: str,
) -> None:
    # Exercise run()'s actual shutdown Event path, without changing process handlers.
    monkeypatch.setattr("offerloom.worker.signal.signal", lambda *_args: None)
    worker = Worker(catalog.runtime, catalog.identity.settings)
    release_response = Event()
    worker_finished = Event()
    inference_started = Event()
    cancellation_status: list[int] = []
    workflow_id: UUID | None = None
    callbacks = 0

    def during_inference() -> None:
        nonlocal callbacks
        callbacks += 1
        if callbacks != 1:
            return
        inference_started.set()
        try:
            if recovery == "cancel":
                assert workflow_id is not None
                current = catalog.workflow(workflow_id)
                response = catalog.client.post(
                    f"/api/v1/workflows/{workflow_id}/cancel",
                    json={},
                    headers=catalog.headers(current["version"]),
                )
                cancellation_status.append(response.status_code)
        finally:
            worker.stop()
            # Hold the HTTP response until run() has interrupted its running task.
            release_response.wait(5)

    def bounded_stop() -> None:
        if not worker_finished.wait(5):
            worker.stop()

    with fixture_server(on_inference=during_inference) as (endpoint, requests):
        route = qualified_route(tmp_path, route_data(endpoint))
        monkeypatch.setattr(local_model, "load_route", lambda _path: route)
        posting = _posting(catalog)
        view = _view(catalog, posting["id"])
        queued = catalog.client.post(
            f"/api/v1/postings/{posting['id']}/extract",
            json={},
            headers=catalog.headers(view["version"]),
        )
        assert queued.status_code == 202, queued.text
        workflow_id = UUID(queued.json()["workflow_id"])
        watchdog = Thread(target=bounded_stop)
        watchdog.start()
        try:
            worker.run()
        finally:
            worker_finished.set()
            release_response.set()
            watchdog.join()
        assert inference_started.is_set()
        assert len(requests) == 1
        interrupted = catalog.workflow(workflow_id)
        assert interrupted["state"] == ("cancelled" if recovery == "cancel" else "running")
        assert "requirements" not in interrupted["result"]
        with catalog.runtime.transaction() as conn:
            runs = (
                conn.execute(
                    text("""SELECT status,metadata FROM offerloom.model_runs
                WHERE owner_id=:owner AND workflow_run_id=:id"""),
                    {"owner": catalog.owner_id, "id": workflow_id},
                )
                .mappings()
                .all()
            )
            assert len(runs) == 1
            assert runs[0]["status"] == "unknown_usage"
            assert runs[0]["metadata"]["error_code"] == "model_interrupted"
            assert (
                conn.execute(
                    text("""SELECT model_network_attempts FROM offerloom.workflow_runs
                WHERE owner_id=:owner AND id=:id"""),
                    {"owner": catalog.owner_id, "id": workflow_id},
                ).scalar_one()
                == 1
            )
            reservation = conn.execute(
                text(
                    "SELECT workflow_run_id FROM offerloom.local_model_reservations WHERE singleton"
                )
            ).scalar_one()
            assert reservation == workflow_id
        if recovery == "cancel":
            assert cancellation_status == [200]
        # Advance only persisted clocks/allowance, not production timers or policy.
        with catalog.identity.admin.transaction() as conn:
            conn.execute(
                text("""UPDATE offerloom.workflow_runs
                SET lease_expires_at=clock_timestamp()-interval '1 second'
                WHERE owner_id=:owner AND id=:id AND state='running'"""),
                {"owner": catalog.owner_id, "id": workflow_id},
            )
            conn.execute(
                text("""UPDATE offerloom.local_model_reservations
                SET reserved_until=clock_timestamp()-interval '1 second' WHERE singleton"""),
            )
            if recovery == "deadline":
                conn.execute(
                    text("""UPDATE offerloom.workflow_runs
                    SET created_at=clock_timestamp()-interval '181 seconds',
                    deadline_at=clock_timestamp()-interval '1 second'
                    WHERE owner_id=:owner AND id=:id"""),
                    {"owner": catalog.owner_id, "id": workflow_id},
                )
            if recovery == "exhausted":
                conn.execute(
                    text("""UPDATE offerloom.workflow_runs SET model_network_attempts=2
                    WHERE owner_id=:owner AND id=:id"""),
                    {"owner": catalog.owner_id, "id": workflow_id},
                )
        with catalog.runtime.transaction() as conn:
            assert reap(conn) == (0 if recovery == "cancel" else 1)
        with catalog.identity.admin.transaction() as conn:
            conn.execute(
                text("""UPDATE offerloom.workflow_runs SET next_attempt_at=clock_timestamp()
                WHERE owner_id=:owner AND id=:id"""),
                {"owner": catalog.owner_id, "id": workflow_id},
            )
        with catalog.runtime.transaction() as conn:
            resumed = claim(conn, uuid4())
        if recovery in {"cancel", "deadline"}:
            assert resumed is None
            terminal = catalog.workflow(workflow_id)
            assert terminal["state"] == (
                "cancelled" if recovery == "cancel" else "permanent_failure"
            )
            assert terminal["last_error_code"] == (
                "cancelled" if recovery == "cancel" else "deadline_exceeded"
            )
            assert len(requests) == 1
        else:
            assert resumed is not None and resumed.id == workflow_id and resumed.attempt == 2
            Worker(catalog.runtime, catalog.identity.settings)._execute(resumed, Event())
            final = catalog.workflow(workflow_id)
            if recovery == "resume":
                assert final["state"] == "succeeded"
                assert len(requests) == 2
            else:
                assert final["state"] == "permanent_failure"
                assert final["last_error_code"] == "model_attempt_or_deadline_exhausted"
                assert len(requests) == 1
            with catalog.runtime.transaction() as conn:
                assert (
                    conn.execute(
                        text("""SELECT model_network_attempts FROM offerloom.workflow_runs
                    WHERE owner_id=:owner AND id=:id"""),
                        {"owner": catalog.owner_id, "id": workflow_id},
                    ).scalar_one()
                    == 2
                )


@pytest.mark.postgres
def test_postflight_model_mapping_drift_blocks_worker_publication_and_route_reuse(
    catalog: SourceContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with fixture_server(postflight_tags_digest="c" * 64) as (endpoint, requests):
        route = qualified_route(tmp_path, route_data(endpoint))
        monkeypatch.setattr(local_model, "load_route", lambda _path: route)
        posting = _posting(catalog)
        task = _queue(catalog, _view(catalog, posting["id"]))
        Worker(catalog.runtime, catalog.identity.settings)._execute(task, Event())
        failed = catalog.workflow(task.id)
        assert failed["state"] == "permanent_failure"
        assert failed["last_error_code"] == "model_identity_changed"
        assert "requirements" not in failed["result"]
        assert len(requests) == 1
        with catalog.runtime.transaction() as conn:
            blocked = (
                conn.execute(
                    text("""SELECT stage,error_code FROM offerloom.local_model_route_blocks
                WHERE owner_id=:owner"""),
                    {"owner": catalog.owner_id},
                )
                .mappings()
                .one()
            )
            assert blocked["stage"] == "extract_posting"
            assert blocked["error_code"] == "model_identity_changed"
        view = _view(catalog, posting["id"])
        assert view["current_revision"] is None
        rejected = catalog.client.post(
            f"/api/v1/postings/{posting['id']}/extract",
            json={},
            headers=catalog.headers(view["version"]),
        )
        assert rejected.status_code == 503
        assert len(requests) == 1
