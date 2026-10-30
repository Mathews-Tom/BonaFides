# SPDX-License-Identifier: MIT OR Apache-2.0
"""Real worker execution, PostgreSQL recovery, and bounded local HTTP fixtures."""

from __future__ import annotations

import time
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

from bonafides import local_model
from bonafides.problems import Problem
from bonafides.worker import Worker
from bonafides.workflows import claim, reap


@pytest.mark.postgres
@pytest.mark.parametrize("recovery", ["resume", "cancel", "deadline", "exhausted"])
def test_real_worker_shutdown_keeps_model_execution_under_database_recovery_authority(
    catalog: SourceContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recovery: str,
) -> None:
    # Exercise run()'s actual shutdown Event path, without changing process handlers.
    monkeypatch.setattr("bonafides.worker.signal.signal", lambda *_args: None)
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
            release_response.wait()

    def bounded_stop() -> None:
        if not inference_started.wait(30) or not worker_finished.wait(10):
            worker.stop()

    with fixture_server(on_inference=during_inference) as (endpoint, requests):
        # This case tests shutdown, not the generic fixture's two-second timeout.
        data = route_data(endpoint)
        data["deadline_seconds"] = 30
        route = qualified_route(tmp_path, data)
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
                    text("""SELECT status,metadata FROM bonafides.model_runs
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
                    text("""SELECT model_network_attempts FROM bonafides.workflow_runs
                WHERE owner_id=:owner AND id=:id"""),
                    {"owner": catalog.owner_id, "id": workflow_id},
                ).scalar_one()
                == 1
            )
            reservation = conn.execute(
                text(
                    "SELECT workflow_run_id FROM bonafides.local_model_reservations WHERE singleton"
                )
            ).scalar_one()
            assert reservation == workflow_id
        if recovery == "cancel":
            assert cancellation_status == [200]
        # Advance only persisted clocks/allowance, not production timers or policy.
        with catalog.identity.admin.transaction() as conn:
            conn.execute(
                text("""UPDATE bonafides.workflow_runs
                SET lease_expires_at=clock_timestamp()-interval '1 second'
                WHERE owner_id=:owner AND id=:id AND state='running'"""),
                {"owner": catalog.owner_id, "id": workflow_id},
            )
            conn.execute(
                text("""UPDATE bonafides.local_model_reservations
                SET reserved_until=clock_timestamp()-interval '1 second' WHERE singleton"""),
            )
            if recovery == "deadline":
                conn.execute(
                    text("""UPDATE bonafides.workflow_runs
                    SET created_at=clock_timestamp()-interval '181 seconds',
                    deadline_at=clock_timestamp()-interval '1 second'
                    WHERE owner_id=:owner AND id=:id"""),
                    {"owner": catalog.owner_id, "id": workflow_id},
                )
            if recovery == "exhausted":
                conn.execute(
                    text("""UPDATE bonafides.workflow_runs SET model_network_attempts=2
                    WHERE owner_id=:owner AND id=:id"""),
                    {"owner": catalog.owner_id, "id": workflow_id},
                )
        with catalog.runtime.transaction() as conn:
            assert reap(conn) == (0 if recovery == "cancel" else 1)
        with catalog.identity.admin.transaction() as conn:
            conn.execute(
                text("""UPDATE bonafides.workflow_runs SET next_attempt_at=clock_timestamp()
                WHERE owner_id=:owner AND id=:id"""),
                {"owner": catalog.owner_id, "id": workflow_id},
            )
        with catalog.runtime.transaction() as conn:
            resumed = claim(conn, uuid4())
        if recovery in {"cancel", "deadline", "exhausted"}:
            assert resumed is None
            terminal = catalog.workflow(workflow_id)
            assert terminal["state"] == (
                "cancelled" if recovery == "cancel" else "permanent_failure"
            )
            expected_error = {
                "cancel": "cancelled",
                "deadline": "deadline_exceeded",
                "exhausted": "model_attempts_exhausted",
            }[recovery]
            assert terminal["last_error_code"] == expected_error
            assert terminal["attempt"] == 1 and terminal["finished_at"] is not None
            assert len(requests) == 1
        else:
            assert resumed is not None and resumed.id == workflow_id and resumed.attempt == 2
            Worker(catalog.runtime, catalog.identity.settings)._execute(resumed, Event())
            final = catalog.workflow(workflow_id)
            assert "requirements" not in final["result"]
            assert len(requests) == 1
            with catalog.runtime.transaction() as conn:
                assert (
                    conn.execute(
                        text("""SELECT count(*) FROM bonafides.provider_executions e
                    JOIN bonafides.model_runs m ON m.owner_id=e.owner_id AND m.id=e.model_run_id
                    WHERE m.owner_id=:owner AND m.workflow_run_id=:workflow
                      AND m.status='unknown_usage'"""),
                        {"owner": catalog.owner_id, "workflow": workflow_id},
                    ).scalar_one()
                    == 1
                )
        with catalog.runtime.transaction() as conn:
            assert conn.execute(
                text("""SELECT model_network_attempts FROM bonafides.workflow_runs
                WHERE owner_id=:owner AND id=:id"""),
                {"owner": catalog.owner_id, "id": workflow_id},
            ).scalar_one() == (2 if recovery == "exhausted" else 1)


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
                    text("""SELECT stage,error_code FROM bonafides.local_model_route_blocks
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


@pytest.mark.postgres
@pytest.mark.parametrize("owner_cancel", [False, True])
def test_live_worker_maintenance_drains_terminal_response_without_overriding_owner_cancel(
    catalog: SourceContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owner_cancel: bool,
) -> None:
    from test_maintenance import _barrier

    monkeypatch.setattr("bonafides.worker.signal.signal", lambda *_args: None)
    worker = Worker(catalog.runtime, catalog.identity.settings)
    monkeypatch.setattr(worker, "HEARTBEAT_SECONDS", 0)
    inference_started, release_response, maintenance_seen = Event(), Event(), Event()
    failures: list[Exception] = []
    original_pulse = worker._pulse
    maintenance_pulses = 0

    def observe_pulse() -> None:
        nonlocal maintenance_pulses
        try:
            original_pulse()
        except Problem as exc:
            if exc.code == "installation_maintenance":
                maintenance_pulses += 1
                if maintenance_pulses >= 2:
                    maintenance_seen.set()
            raise

    monkeypatch.setattr(worker, "_pulse", observe_pulse)

    def hold_response() -> None:
        inference_started.set()
        if not release_response.wait(20):
            raise RuntimeError("Synthetic inference response release timed out")

    with fixture_server(on_inference=hold_response) as (endpoint, requests):
        data = route_data(endpoint)
        data["deadline_seconds"] = 20
        route = qualified_route(tmp_path, data)
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

        def executions() -> tuple[int, int]:
            with catalog.runtime.transaction() as conn:
                row = conn.execute(
                    text("""SELECT
                    (SELECT count(*) FROM bonafides.provider_executions WHERE owner_id=:owner),
                    (SELECT count(*) FROM bonafides.installation_activities
                     WHERE kind='worker' AND execution_id=:workflow)"""),
                    {"owner": catalog.owner_id, "workflow": workflow_id},
                ).one()
                return int(row[0]), int(row[1])

        def controller() -> None:
            try:
                assert inference_started.wait(10)
                if owner_cancel:
                    current = catalog.workflow(workflow_id)
                    response = catalog.client.post(
                        f"/api/v1/workflows/{workflow_id}/cancel",
                        json={},
                        headers=catalog.headers(current["version"]),
                    )
                    assert response.status_code == 200, response.text
                _barrier(catalog, True)
                assert maintenance_seen.wait(10)
                assert executions()[0] == 1
                if not owner_cancel:
                    release_response.set()
                deadline = time.monotonic() + 10
                while executions()[1] and time.monotonic() < deadline:
                    time.sleep(0.01)
                provider, activity = executions()
                assert activity == 0, "Actual worker I/O did not drain"
                assert provider == int(owner_cancel), (
                    "Maintenance must read the terminal response; explicit cancellation "
                    "must retain uncertain remote execution"
                )
            except Exception as exc:
                failures.append(exc)
            finally:
                release_response.set()
                _barrier(catalog, False)
                worker.stop()

        supervisor = Thread(target=controller)
        supervisor.start()
        try:
            worker.run()
        finally:
            release_response.set()
            worker.stop()
            supervisor.join(timeout=30)
        assert not supervisor.is_alive()
        if failures:
            raise failures[0]
        assert len(requests) == 1
        assert _view(catalog, posting["id"])["current_revision"] is None
