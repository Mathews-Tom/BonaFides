# SPDX-License-Identifier: MIT OR Apache-2.0
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event
from typing import cast
from uuid import uuid4

import pytest
from fastapi import FastAPI
from sqlalchemy import text
from test_identity import identity as identity
from test_sources import SourceContext
from test_sources import catalog as catalog

from offerloom.commands import lock_installation
from offerloom.maintenance import finish_activity, start_api_activity
from offerloom.problems import Problem
from offerloom.storage import BlobStore
from offerloom.worker import Worker
from offerloom.workflows import claim, enqueue, reap


def _barrier(context: SourceContext, active: bool) -> None:
    with context.identity.admin.transaction() as conn:
        conn.execute(
            text("""UPDATE offerloom.installation_state SET maintenance_active=:active,
            maintenance_epoch=maintenance_epoch+1 WHERE singleton"""),
            {"active": active},
        )


def _active(context: SourceContext) -> list[tuple[object, ...]]:
    with context.runtime.transaction() as conn:
        return [
            tuple(row)
            for row in conn.execute(
                text(
                    "SELECT id,kind,participant_id,execution_id FROM offerloom.installation_activities"
                )
            )
        ]


@pytest.mark.postgres
def test_api_barrier_waits_for_actual_handler_io_and_blocks_new_admission(
    catalog: SourceContext,
) -> None:
    entered, release = Event(), Event()
    payload = b"Synthetic activity lifetime proof.\n"

    def blocked_handler() -> dict[str, str]:
        entered.set()
        if not release.wait(15):
            raise RuntimeError("Synthetic handler release was not received")
        blob = BlobStore(catalog.identity.settings.blob_root).put_bytes(catalog.owner_id, payload)
        return {"sha256": blob.sha256}

    cast(FastAPI, catalog.client.app).get("/api/v1/activity-lifetime-probe")(blocked_handler)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(catalog.client.get, "/api/v1/activity-lifetime-probe")
        try:
            assert entered.wait(10)
            registrations = _active(catalog)
            assert len(registrations) == 1 and registrations[0][1] == "api"
            _barrier(catalog, True)
            denied = catalog.client.get("/api/v1/profile")
            assert denied.status_code == 503
            assert denied.json()["code"] == "installation_maintenance"
            assert denied.headers["cache-control"] == "no-store"
            assert denied.headers["x-content-type-options"] == "nosniff"
            assert _active(catalog) == registrations
            assert not future.done()
            with catalog.runtime.transaction() as conn, pytest.raises(Problem) as error:
                lock_installation(conn)
            assert error.value.code == "installation_maintenance"
        finally:
            release.set()
            try:
                response = future.result(timeout=10)
            finally:
                _barrier(catalog, False)
    assert response.status_code == 200
    assert _active(catalog) == []
    store = BlobStore(catalog.identity.settings.blob_root)
    digest = response.json()["sha256"]
    assert store.read(catalog.owner_id, f"{digest[:2]}/{digest}", digest, len(payload)) == payload


@pytest.mark.postgres
def test_worker_lease_and_maintenance_do_not_acknowledge_running_execution(
    catalog: SourceContext,
) -> None:
    worker = Worker(catalog.runtime, catalog.identity.settings)
    interrupted = Event()
    with catalog.runtime.transaction() as conn:
        workflow_id = enqueue(
            conn, catalog.owner_id, "document_generate", {"scenario": "slow", "delay_seconds": 120}
        )
        task = claim(conn, worker.id)
    assert task is not None and task.id == workflow_id
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(worker._execute, task, interrupted)
        try:
            deadline = time.monotonic() + 10
            registrations = _active(catalog)
            while not registrations and time.monotonic() < deadline:
                time.sleep(0.01)
                registrations = _active(catalog)
            assert len(registrations) == 1
            with catalog.identity.admin.transaction() as conn:
                conn.execute(
                    text("""UPDATE offerloom.workflow_runs
                    SET lease_expires_at=clock_timestamp()-interval '1 second'
                    WHERE owner_id=:owner AND id=:id"""),
                    {"owner": catalog.owner_id, "id": workflow_id},
                )
            with catalog.runtime.transaction() as conn:
                reap(conn)
            assert registrations[0][1:] == ("worker", worker.id, workflow_id)
            _barrier(catalog, True)
            assert _active(catalog) == registrations
            assert not future.done()
            interrupted.set()
            future.result(timeout=10)
            assert _active(catalog) == []
            with catalog.runtime.transaction() as conn:
                state = conn.execute(
                    text(
                        "SELECT state FROM offerloom.workflow_runs WHERE owner_id=:owner AND id=:id"
                    ),
                    {"owner": catalog.owner_id, "id": workflow_id},
                ).scalar_one()
            assert state == "retryable_failure"
        finally:
            interrupted.set()
            try:
                future.result(timeout=10)
            finally:
                _barrier(catalog, False)


@pytest.mark.postgres
def test_registration_requires_exact_positive_completion_and_no_time_based_cleanup(
    catalog: SourceContext,
) -> None:
    registration = start_api_activity(catalog.runtime, uuid4(), uuid4())
    before = _active(catalog)
    try:
        with catalog.identity.admin.transaction() as conn:
            conn.execute(
                text("""UPDATE offerloom.installation_activities
                SET started_at=clock_timestamp()-interval '1 day' WHERE id=:id"""),
                {"id": registration.id},
            )
        _barrier(catalog, True)
        with pytest.raises(Problem) as wrong_participant:
            finish_activity(catalog.runtime, replace(registration, participant_id=uuid4()))
        assert wrong_participant.value.code == "activity_completion_not_registered"
        assert _active(catalog) == before
        with pytest.raises(Problem) as denied:
            start_api_activity(catalog.runtime, uuid4(), uuid4())
        assert denied.value.code == "installation_maintenance"
        assert _active(catalog) == before
        finish_activity(catalog.runtime, registration)
        assert _active(catalog) == []
    finally:
        with catalog.identity.admin.transaction() as conn:
            conn.execute(
                text("DELETE FROM offerloom.installation_activities WHERE id=:id"),
                {"id": registration.id},
            )
        _barrier(catalog, False)
