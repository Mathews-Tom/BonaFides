# SPDX-License-Identifier: MIT OR Apache-2.0
"""Actual loopback HTTP and PostgreSQL; no model runtime or weights are invoked."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from threading import Event
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_identity import identity as identity
from test_local_model import (
    fixture_server,
    qualified_route,
    response_body,
    route_data,
)
from test_maintenance import _barrier
from test_sources import SourceContext
from test_sources import catalog as catalog
from test_suitability import _posting, _queue, _view

from bonafides import local_model, suitability
from bonafides.local_model import LocalRoute, ModelFailure, OllamaClient
from bonafides.maintenance import ProviderExecution
from bonafides.problems import Problem
from bonafides.workflows import Claim, assert_fence, reap


def _pending(context: SourceContext) -> list[UUID]:
    with context.runtime.transaction() as conn:
        return list(conn.execute(text("SELECT id FROM bonafides.provider_executions")).scalars())


def _prepare(
    context: SourceContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
    seconds: int = 5,
) -> tuple[Claim, LocalRoute]:
    data = route_data(endpoint)
    data["deadline_seconds"] = seconds
    route = qualified_route(tmp_path, data)
    monkeypatch.setattr(local_model, "load_route", lambda _path: route)
    posting = _posting(context)
    return _queue(context, _view(context, posting["id"])), route


def _execution(context: SourceContext, task: Claim, seconds: int) -> ProviderExecution:
    with context.runtime.transaction() as conn:
        run_id = suitability._reserve_call(conn, task, context.identity.settings, seconds)
    assert run_id is not None
    return ProviderExecution(context.runtime, task, run_id)


@pytest.mark.postgres
def test_production_inference_records_before_transmission_and_acknowledges_terminal_response(
    catalog: SourceContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: list[list[UUID]] = []
    with fixture_server(on_inference=lambda: observed.append(_pending(catalog))) as (
        endpoint,
        requests,
    ):
        task, _route = _prepare(catalog, tmp_path, monkeypatch, endpoint)
        suitability.execute_model(catalog.runtime, catalog.identity.settings, task, Event())
    assert len(requests) == 1
    assert len(observed) == 1 and len(observed[0]) == 1
    assert _pending(catalog) == []
    with catalog.runtime.transaction() as conn:
        model = conn.execute(
            text("SELECT status,output_sha256 FROM bonafides.model_runs WHERE workflow_run_id=:id"),
            {"id": task.id},
        ).one()
    assert model.status == "succeeded" and len(model.output_sha256) == 64


@pytest.mark.postgres
@pytest.mark.parametrize("terminal", ["accepted", "refused", "length_limited"])
def test_terminal_response_drains_even_when_barrier_fences_publication_or_output_is_rejected(
    catalog: SourceContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    terminal: str,
) -> None:
    observed: list[list[UUID]] = []

    def enter_maintenance() -> None:
        observed.append(_pending(catalog))
        _barrier(catalog, True)

    body = (
        response_body(message={"role": "assistant", "refusal": "Synthetic refusal"})
        if terminal == "refused"
        else response_body(done_reason="length")
        if terminal == "length_limited"
        else response_body()
    )
    try:
        with fixture_server(body=body, on_inference=enter_maintenance) as (endpoint, requests):
            task, route = _prepare(catalog, tmp_path, monkeypatch, endpoint)
            execution = _execution(catalog, task, 5)
            if terminal == "accepted":
                result = OllamaClient(route, execution).call(
                    "extract_posting",
                    cast(dict[str, object], task.input["context"]),
                    str(task.input["prompt_version"]),
                )
                assert result.output["stage"] == "extract_posting"
            else:
                with pytest.raises(ModelFailure) as rejected:
                    OllamaClient(route, execution).call(
                        "extract_posting",
                        cast(dict[str, object], task.input["context"]),
                        str(task.input["prompt_version"]),
                    )
                assert rejected.value.code == (
                    "model_refused" if terminal == "refused" else "model_incomplete"
                )
            assert len(requests) == 1
            assert len(observed) == 1 and len(observed[0]) == 1
            assert _pending(catalog) == []
            with catalog.runtime.transaction() as conn, pytest.raises(DBAPIError) as fenced:
                assert_fence(conn, task)
            assert getattr(fenced.value.orig, "sqlstate", None) == "55000"
    finally:
        _barrier(catalog, False)


@pytest.mark.postgres
@pytest.mark.parametrize(
    "body",
    [response_body(done=False), response_body(model="unmatched-model"), b'{"done":'],
)
def test_nonterminal_or_unattributable_response_retains_unknown_provider_execution(
    catalog: SourceContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    body: bytes,
) -> None:
    with fixture_server(body=body) as (endpoint, requests):
        task, route = _prepare(catalog, tmp_path, monkeypatch, endpoint)
        execution = _execution(catalog, task, 5)
        with pytest.raises(ModelFailure):
            OllamaClient(route, execution).call(
                "extract_posting",
                cast(dict[str, object], task.input["context"]),
                str(task.input["prompt_version"]),
            )
    assert len(requests) == 1
    assert execution.registration is not None
    assert _pending(catalog) == [execution.registration.id]


@pytest.mark.postgres
@pytest.mark.parametrize("cancel", [False, True])
def test_local_timeout_or_cancel_cannot_acknowledge_still_running_remote_request(
    catalog: SourceContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cancel: bool,
) -> None:
    entered, release, interrupted = Event(), Event(), Event()

    def running_remote_request() -> None:
        entered.set()
        if cancel:
            interrupted.set()
        if not release.wait(10):
            raise RuntimeError("Synthetic remote request was not released")

    with fixture_server(on_inference=running_remote_request) as (endpoint, requests):
        try:
            task, route = _prepare(catalog, tmp_path, monkeypatch, endpoint, seconds=1)
            execution = _execution(catalog, task, 1)
            with pytest.raises(ModelFailure) as stopped:
                OllamaClient(route, execution).call(
                    "extract_posting",
                    cast(dict[str, object], task.input["context"]),
                    str(task.input["prompt_version"]),
                    interrupted,
                )
            assert stopped.value.code == ("model_interrupted" if cancel else "model_timeout")
            assert entered.is_set() and not release.is_set()
            assert len(requests) == 1
            assert execution.registration is not None
            assert _pending(catalog) == [execution.registration.id]
            # Neither elapsed reservation nor a second local attempt authorizes
            # another inference while the earlier remote execution is unknown.
            with catalog.identity.admin.transaction() as conn:
                conn.execute(
                    text("UPDATE bonafides.local_model_reservations SET reserved_until='-infinity'")
                )
                conn.execute(
                    text(
                        "UPDATE bonafides.provider_executions SET started_at=now()-interval '1 year'"
                    )
                )
            with catalog.runtime.transaction() as conn:
                assert suitability._reserve_call(conn, task, catalog.identity.settings, 1) is None
                attempts = conn.execute(
                    text("SELECT model_network_attempts FROM bonafides.workflow_runs WHERE id=:id"),
                    {"id": task.id},
                ).scalar_one()
            assert attempts == 1
            with catalog.identity.admin.transaction() as conn:
                conn.execute(
                    text(
                        "UPDATE bonafides.workflow_runs SET lease_expires_at=now()-interval '1 second' WHERE id=:id"
                    ),
                    {"id": task.id},
                )
            with catalog.runtime.transaction() as conn:
                reap(conn)
            assert _pending(catalog) == [execution.registration.id]
        finally:
            release.set()
    # The response was not observed by the caller; later remote progress does not
    # retroactively turn the local socket failure into an acknowledgement.
    assert _pending(catalog) == [execution.registration.id]


@pytest.mark.postgres
def test_barrier_refuses_inference_and_changed_epoch_keeps_stale_claim_fenced(
    catalog: SourceContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with fixture_server() as (endpoint, requests):
        task, route = _prepare(catalog, tmp_path, monkeypatch, endpoint)
        execution = _execution(catalog, task, 5)
        _barrier(catalog, True)
        try:
            with pytest.raises(Problem) as rejected:
                OllamaClient(route, execution).call(
                    "extract_posting",
                    cast(dict[str, object], task.input["context"]),
                    str(task.input["prompt_version"]),
                )
            assert rejected.value.code == "installation_maintenance"
            assert requests == [] and _pending(catalog) == []
        finally:
            _barrier(catalog, False)
    # A new valid claim is required after an epoch change. This stale claim
    # cannot create a registration even with maintenance turned off again.
    with pytest.raises(DBAPIError) as stale:
        execution.started()
    assert getattr(stale.value.orig, "sqlstate", None) == "55000"
    assert _pending(catalog) == []


@pytest.mark.postgres
def test_provider_completion_requires_exact_registered_participant(
    catalog: SourceContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with fixture_server() as (endpoint, _requests):
        task, _route = _prepare(catalog, tmp_path, monkeypatch, endpoint)
        execution = _execution(catalog, task, 5)
        execution.started()
        registration = execution.registration
        assert registration is not None
        execution.registration = replace(registration, participant_id=uuid4())
        with pytest.raises(Problem) as rejected:
            execution.completed()
        assert rejected.value.code == "provider_execution_completion_not_registered"
        assert _pending(catalog) == [registration.id]


@pytest.mark.postgres
def test_cancellation_during_durable_admission_cannot_send_inference_bytes(
    catalog: SourceContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    interrupted = Event()

    class CancelAfterAdmission(ProviderExecution):
        def started(self) -> None:
            super().started()
            interrupted.set()

    with fixture_server() as (endpoint, requests):
        task, route = _prepare(catalog, tmp_path, monkeypatch, endpoint)
        reserved = _execution(catalog, task, 5)
        execution = CancelAfterAdmission(catalog.runtime, task, reserved.model_run_id)
        with pytest.raises(ModelFailure) as canceled:
            OllamaClient(route, execution).call(
                "extract_posting",
                cast(dict[str, object], task.input["context"]),
                str(task.input["prompt_version"]),
                interrupted,
            )
        assert canceled.value.code == "model_interrupted"
    assert requests == []
    assert execution.registration is not None
    assert _pending(catalog) == [execution.registration.id]
