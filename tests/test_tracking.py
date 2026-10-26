# SPDX-License-Identifier: MIT OR Apache-2.0
"""Deterministic real-PG/HTTP consumer boundaries for manual tracking, never models."""

from __future__ import annotations

import asyncio
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from starlette.types import Message, Scope
from test_catalog import _disposition, _manual
from test_identity import identity as identity
from test_sources import SourceContext
from test_sources import catalog as catalog
from test_suitability import _profile

from offerloom import suitability, tracking
from offerloom.commands import canonical_json
from offerloom.problems import Problem
from offerloom.storage import BlobStore


def _application(context: SourceContext, **changes: object) -> dict[str, Any]:
    posting = _disposition(context, _manual(context, **changes), "saved")
    response = context.client.post(
        "/api/v1/applications", json={"posting_id": posting["id"]}, headers=context.headers()
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def _get(context: SourceContext, application: dict[str, Any]) -> dict[str, Any]:
    response = context.client.get(f"/api/v1/applications/{application['id']}")
    assert response.status_code == 200, response.text
    assert response.headers["etag"] == f'"{response.json()["version"]}"'
    return cast(dict[str, Any], response.json())


def _write(
    context: SourceContext,
    application: dict[str, Any],
    suffix: str,
    body: dict[str, object],
    *,
    method: str = "POST",
    expected: int = 200,
) -> dict[str, Any]:
    response = context.client.request(
        method,
        f"/api/v1/applications/{application['id']}/{suffix}",
        json=body,
        headers=context.headers(application["version"]),
    )
    assert response.status_code == expected, response.text
    return cast(dict[str, Any], response.json())


def _stage(
    context: SourceContext,
    application: dict[str, Any],
    stage: str,
    effective: str = "2026-10-01T12:00:00Z",
    **changes: object,
) -> dict[str, Any]:
    return _write(
        context,
        application,
        "stage",
        {
            "to_stage": stage,
            "effective_at": effective,
            "reason": "Synthetic manual event",
            **changes,
        },
    )


def _history(
    context: SourceContext, application: dict[str, Any], **query: object
) -> dict[str, Any]:
    response = context.client.get(
        f"/api/v1/applications/{application['id']}/stage-events", params=cast(Any, query)
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def _counts(context: SourceContext, month: int) -> dict[str, Any]:
    response = context.client.get(
        "/api/v1/applications/counts",
        params={
            "period_start": f"2026-{month:02d}-01T00:00:00Z",
            "period_end": f"2026-{month + 1:02d}-01T00:00:00Z",
        },
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def _appointment(**changes: object) -> dict[str, object]:
    return {
        "kind": "interview",
        "title": "Synthetic local interview",
        "local_start": "2026-10-28T23:30:00",
        "local_end": "2026-10-29T00:30:00",
        "timezone": "America/New_York",
        **changes,
    }


def _upload(
    context: SourceContext,
    application: dict[str, Any],
    name: str,
    data: bytes,
    *,
    expected: int = 200,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    response = context.client.post(
        f"/api/v1/applications/{application['id']}/attachments",
        params={"display_name": name},
        content=data,
        headers={
            **context.headers(application["version"]),
            "Content-Type": "application/octet-stream",
            **(headers or {}),
        },
    )
    assert response.status_code == expected, response.text
    return cast(httpx.Response, response)


@pytest.mark.postgres
def test_saved_only_default_creation_replay_repeat_and_independence(catalog: SourceContext) -> None:
    posting = _manual(catalog)
    path, body = "/api/v1/applications", {"posting_id": posting["id"]}
    assert catalog.client.post(path, json=body, headers=catalog.headers()).status_code == 409
    _disposition(catalog, posting, "saved")
    assert catalog.client.post(path, json=body, headers=catalog.headers(1)).status_code == 400
    key = uuid4().hex
    response = catalog.client.post(path, json=body, headers=catalog.headers(key=key))
    assert response.status_code == 201, response.text
    first = response.json()
    assert first["created"] and first["attempt_no"] == 1 and first["current_stage"] == "preparing"
    assert (
        first["notes"] == first["contacts"] == first["appointments"] == first["attachments"] == []
    )
    assert catalog.client.post(path, json=body, headers=catalog.headers(key=key)).json() == first
    first = _stage(catalog, first, "rejected")
    existing = catalog.client.post(path, json=body, headers=catalog.headers())
    assert existing.status_code == 200 and existing.json()["created"] is False
    assert (
        existing.json()["version"] == first["version"]
        and existing.json()["current_stage"] == "rejected"
    )
    assert len(_history(catalog, first)["items"]) == 2
    assert (
        _write(catalog, first, "repeat", {"confirmed": False}, expected=422)["code"]
        == "invalid_request"
    )
    repeat = _write(catalog, first, "repeat", {"confirmed": True}, expected=201)
    assert (
        repeat["id"] != first["id"]
        and repeat["attempt_no"] == 2
        and repeat["current_stage"] == "preparing"
    )
    retained = _get(catalog, first)
    assert retained["version"] == first["version"] + 1 and retained["current_stage"] == "rejected"
    assert (
        _write(catalog, retained, "repeat", {"confirmed": True}, expected=409)["code"]
        == "newer_attempt_exists"
    )
    assert (
        catalog.client.post(path, json=body, headers=catalog.headers()).json()["id"] == repeat["id"]
    )
    posting = catalog.client.get(f"/api/v1/postings/{posting['id']}").json()
    _disposition(catalog, posting, "dismissed")
    assert catalog.client.post(path, json=body, headers=catalog.headers()).status_code == 409
    assert _get(catalog, repeat)["id"] == repeat["id"]
    with catalog.runtime.transaction() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM offerloom.workflow_runs WHERE owner_id=:owner"),
                {"owner": catalog.owner_id},
            ).scalar_one()
            == 0
        )
        assert (
            conn.execute(
                text("SELECT count(*) FROM offerloom.model_runs WHERE owner_id=:owner"),
                {"owner": catalog.owner_id},
            ).scalar_one()
            == 0
        )
        assert (
            conn.execute(
                text("SELECT count(*) FROM offerloom.external_accounts WHERE owner_id=:owner"),
                {"owner": catalog.owner_id},
            ).scalar_one()
            == 0
        )


@pytest.mark.postgres
def test_create_and_aggregate_version_races(catalog: SourceContext) -> None:
    posting = _disposition(catalog, _manual(catalog), "saved")
    barrier = Barrier(2)

    def create() -> httpx.Response:
        barrier.wait()
        return cast(
            httpx.Response,
            catalog.client.post(
                "/api/v1/applications",
                json={"posting_id": posting["id"]},
                headers=catalog.headers(),
            ),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: create(), range(2)))
    assert sorted(response.status_code for response in responses) == [200, 201]
    app = responses[0].json()
    assert {response.json()["id"] for response in responses} == {app["id"]}
    assert len(_history(catalog, app)["items"]) == 1
    barrier = Barrier(2)

    def competing(kind: str) -> httpx.Response:
        barrier.wait()
        body = (
            {"body": "Synthetic race note"}
            if kind == "notes"
            else {
                "to_stage": "applied",
                "effective_at": "2026-10-01T00:00:00Z",
                "reason": "Synthetic race",
            }
        )
        return cast(
            httpx.Response,
            catalog.client.post(
                f"/api/v1/applications/{app['id']}/{kind}",
                json=body,
                headers=catalog.headers(app["version"]),
            ),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(competing, ["notes", "stage"]))
    assert sorted(response.status_code for response in results) == [200, 412]
    current = _get(catalog, app)
    assert current["version"] == app["version"] + 1
    assert len(current["notes"]) + len(_history(catalog, current)["items"]) == 2
    barrier = Barrier(2)

    def repeat() -> httpx.Response:
        barrier.wait()
        return cast(
            httpx.Response,
            catalog.client.post(
                f"/api/v1/applications/{current['id']}/repeat",
                json={"confirmed": True},
                headers=catalog.headers(current["version"]),
            ),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        repeats = list(pool.map(lambda _: repeat(), range(2)))
    assert sorted(response.status_code for response in repeats) == [201, 412]
    listing = catalog.client.get("/api/v1/applications").json()
    assert sorted(item["attempt_no"] for item in listing["items"]) == [1, 2]


@pytest.mark.postgres
def test_arbitrary_immutable_historical_corrections_fix_distinct_period_counts(
    catalog: SourceContext,
) -> None:
    app = _application(catalog)
    app = _stage(catalog, app, "applied", "2026-09-15T00:00:00Z")
    applied = _history(catalog, app)["items"][0]
    app = _stage(catalog, app, "interview", "2026-10-04T00:00:00Z")
    before = _history(catalog, app)["items"]
    pointer = app["current_stage_event_id"]
    assert _counts(catalog, 9)["stage_reached_in_period"]["applied"] == 1
    app = _stage(catalog, app, "applied", "2026-10-02T00:00:00Z", correction_of=applied["id"])
    assert app["current_stage"] == "interview" and app["current_stage_event_id"] == pointer
    events = _history(catalog, app)["items"]
    assert events[1:] == before
    assert (
        events[0]["correction_of"] == applied["id"] and events[0]["affects_current_stage"] is False
    )
    assert events[0]["from_stage"] == applied["from_stage"]
    assert _counts(catalog, 9)["stage_reached_in_period"]["applied"] == 0
    assert _counts(catalog, 10)["stage_reached_in_period"]["applied"] == 1
    assert (
        _stage(catalog, app, "screening")["current_stage"] == "screening"
    )  # explicit reverse move
    app = _get(catalog, app)
    assert (
        _write(
            catalog,
            app,
            "stage",
            {
                "to_stage": "applied",
                "effective_at": "2026-09-20T00:00:00Z",
                "reason": "Duplicate correction",
                "correction_of": applied["id"],
            },
            expected=409,
        )["code"]
        == "invalid_correction_target"
    )
    replacement = events[0]
    app = _stage(catalog, app, "applied", "2026-10-03T00:00:00Z", correction_of=replacement["id"])
    assert app["current_stage"] == "screening"
    app = _stage(catalog, app, "applied", "2026-10-05T00:00:00Z")
    app = _stage(catalog, app, "interview", "2026-10-06T00:00:00Z")
    assert (
        _counts(catalog, 10)["stage_reached_in_period"]["applied"] == 1
    )  # distinct, not event cardinality
    current_event = app["current_stage_event_id"]
    app = _stage(catalog, app, "offer", "2026-10-07T00:00:00Z", correction_of=current_event)
    assert app["current_stage"] == "offer"
    assert _history(catalog, app)["items"][0]["affects_current_stage"] is True
    with pytest.raises(DBAPIError), catalog.runtime.transaction() as conn:
        conn.execute(
            text(
                "UPDATE offerloom.application_stage_events SET reason='forbidden' WHERE owner_id=:owner AND id=:id"
            ),
            {"owner": catalog.owner_id, "id": UUID(applied["id"])},
        )


@pytest.mark.postgres
def test_archive_reactivate_counts_history_and_attention_are_independent(
    catalog: SourceContext,
) -> None:
    app = _stage(catalog, _application(catalog), "rejected")
    # Reopen explicitly; outcomes remain in immutable history.
    app = _stage(catalog, app, "interview")
    due = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:00")
    app = _write(
        catalog,
        app,
        "appointments",
        _appointment(kind="follow_up", local_start=due, local_end=None, timezone="UTC"),
    )
    app = _write(catalog, app, "notes", {"body": "Retained synthetic note"})
    app = _write(
        catalog,
        app,
        "contacts",
        {"display_name": "Fictional contact", "email": None, "role_label": None},
    )
    events = _history(catalog, app)["items"]
    assert (
        catalog.client.get("/api/v1/applications/attention").json()["items"][0]["application_id"]
        == app["id"]
    )
    original = app
    app = _write(catalog, app, "archive", {"archived": True})
    assert app["archived_at"] is not None and app["current_stage"] == "interview"
    for child in ("notes", "contacts", "appointments", "attachments"):
        assert app[child] == original[child]
    assert _history(catalog, app)["items"] == events
    assert catalog.client.get("/api/v1/applications").json()["items"] == []
    assert (
        catalog.client.get("/api/v1/applications", params={"archived": "archived"}).json()["items"][
            0
        ]["id"]
        == app["id"]
    )
    assert catalog.client.get("/api/v1/applications/attention").json()["items"] == []
    totals = _counts(catalog, 10)
    assert (
        totals["current_by_stage"]["interview"] == 0
        and totals["archived_by_stage"]["interview"] == 1
    )
    assert totals["stage_reached_in_period"]["rejected"] == 1
    app = _write(catalog, app, "archive", {"archived": False})
    assert app["archived_at"] is None and app["current_stage"] == "interview"
    assert _counts(catalog, 10)["current_by_stage"]["interview"] == 1
    assert _history(catalog, app)["items"] == events


@pytest.mark.postgres
def test_signed_owner_filter_bound_keysets_equal_timestamps_and_literal_search(
    catalog: SourceContext,
) -> None:
    apps = [
        _application(
            catalog,
            title=f"Synthetic 100%_Role {index}",
            canonical_url=f"https://careers.fictional-employer.com/role/{index}",
        )
        for index in range(4)
    ]
    with catalog.identity.admin.transaction() as conn:
        conn.execute(
            text(
                "UPDATE offerloom.applications SET created_at='2026-01-02T00:00:00Z' WHERE owner_id=:owner"
            ),
            {"owner": catalog.owner_id},
        )
    response = catalog.client.get("/api/v1/applications", params={"limit": 2, "q": "%_"})
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["query_elapsed_ms"] >= 0 and page["next_cursor"]
    assert all("notes" not in item and "contacts" not in item for item in page["items"])
    cursor = page["next_cursor"]
    later = _application(
        catalog,
        title="Synthetic 100%_Role later",
        canonical_url="https://careers.fictional-employer.com/later",
    )
    second = catalog.client.get(
        "/api/v1/applications", params={"limit": 1, "q": "%_", "cursor": cursor}
    )
    assert second.status_code == 200, second.text
    third = catalog.client.get(
        "/api/v1/applications",
        params={"limit": 2, "q": "%_", "cursor": second.json()["next_cursor"]},
    )
    ids = [item["id"] for item in page["items"] + second.json()["items"] + third.json()["items"]]
    assert set(ids) == {app["id"] for app in apps} and len(ids) == len(set(ids))
    assert later["id"] not in ids and third.json()["next_cursor"] is None
    for changes in ({"q": "other"}, {"stage": "applied"}, {"archived": "all"}):
        assert (
            catalog.client.get(
                "/api/v1/applications", params={"cursor": cursor, "q": "%_", **changes}
            ).status_code
            == 400
        )
    assert (
        catalog.client.get(
            "/api/v1/applications", params={"cursor": cursor[:-1] + "!", "q": "%_"}
        ).status_code
        == 400
    )
    posting_cursor = catalog.client.get("/api/v1/postings", params={"limit": 1}).json()[
        "next_cursor"
    ]
    assert (
        catalog.client.get("/api/v1/applications", params={"cursor": posting_cursor}).status_code
        == 400
    )
    with catalog.runtime.transaction() as conn:
        with pytest.raises(Problem) as error:
            tracking.list_applications(
                conn,
                uuid4(),
                catalog.identity.settings.keyring,
                tracking.ApplicationQuery(cursor=cursor, q="%_"),
            )
        assert error.value.code == "invalid_cursor"
    assert (
        catalog.client.get("/api/v1/applications", params={"q": "notpresent%_"}).json()["items"]
        == []
    )


@pytest.mark.postgres
def test_history_paginates_beyond_child_quotas_and_next_stage_is_monotonic(
    catalog: SourceContext,
) -> None:
    app = _application(catalog)
    with catalog.runtime.transaction() as conn:
        conn.execute(
            text("""INSERT INTO offerloom.application_stage_events(owner_id,application_id,to_stage,effective_at,sequence)
            SELECT :owner,:app,'applied',clock_timestamp(),n FROM generate_series(2,225) n"""),
            {"owner": catalog.owner_id, "app": UUID(app["id"])},
        )
    sequences: list[int] = []
    before = None
    while True:
        page = _history(
            catalog, app, **({"before": before} if before is not None else {}), limit=50
        )
        sequences.extend(item["sequence"] for item in page["items"])
        before = page["next_cursor"]
        if before is None:
            break
    assert sequences == list(range(225, 0, -1))
    app = _stage(catalog, app, "interview")
    assert _history(catalog, app, limit=1)["items"][0]["sequence"] == 226


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("start", "end", "offset", "end_offset", "expected", "duration"),
    [
        ("2026-11-01T01:30:00", "2026-11-01T02:30:00", None, None, "ambiguous_local_time", None),
        ("2026-11-01T01:30:00", "2026-11-01T02:30:00", -240, -300, None, 120),
        ("2026-11-01T01:30:00", "2026-11-01T02:30:00", -300, -300, None, 60),
        ("2026-11-01T00:30:00", "2026-11-01T01:30:00", -240, None, "ambiguous_local_time", None),
        ("2026-11-01T00:30:00", "2026-11-01T01:30:00", -240, -240, None, 60),
        ("2026-11-01T00:30:00", "2026-11-01T01:30:00", -240, -300, None, 120),
        (
            "2026-11-01T01:30:00",
            "2026-11-01T01:45:00",
            -300,
            -240,
            "invalid_appointment_interval",
            None,
        ),
        ("2026-03-08T02:30:00", "2026-03-08T03:30:00", None, None, "nonexistent_local_time", None),
        ("2026-03-08T01:30:00", "2026-03-08T02:30:00", None, None, "nonexistent_local_time", None),
        ("2026-03-08T01:30:00", "2026-03-08T03:30:00", -300, -240, None, 60),
        ("2026-10-28T23:30:00", "2026-10-29T00:30:00", -240, -240, None, 60),
        ("2026-10-28T23:30:00", "2026-10-29T00:30:00", -300, None, "invalid_utc_offset", None),
        ("2026-10-28T23:30:00", "2026-10-29T00:30:00", None, -300, "invalid_utc_offset", None),
        (
            "2026-10-28T23:30:00",
            "2026-10-28T23:30:00",
            None,
            None,
            "invalid_appointment_interval",
            None,
        ),
        (
            "2026-10-28T23:30:00",
            "2026-10-29T23:31:00",
            None,
            None,
            "invalid_appointment_interval",
            None,
        ),
        (
            "2026-10-28T23:30:00",
            "2026-10-29T00:30:01",
            None,
            None,
            "invalid_appointment_interval",
            None,
        ),
    ],
)
def test_real_http_dst_resolves_both_endpoints(
    catalog: SourceContext,
    start: str,
    end: str,
    offset: int | None,
    end_offset: int | None,
    expected: str | None,
    duration: int | None,
) -> None:
    app = _application(catalog)
    payload = _appointment(
        local_start=start,
        local_end=end,
        utc_offset_minutes=offset,
        end_utc_offset_minutes=end_offset,
    )
    response = catalog.client.post(
        f"/api/v1/applications/{app['id']}/appointments",
        json=payload,
        headers=catalog.headers(app["version"]),
    )
    if expected is not None:
        assert response.status_code == 422 and response.json()["code"] == expected, response.text
        assert _get(catalog, app)["version"] == app["version"]
    else:
        assert response.status_code == 200, response.text
        assert duration is not None
        appointment = response.json()["appointments"][0]
        assert appointment["duration_minutes"] == duration
        assert appointment["local_start"] == start and appointment["local_end"] == end
        assert appointment["timezone"] == appointment["display_timezone"] == "America/New_York"
        assert (
            datetime.fromisoformat(appointment["ends_at"])
            - datetime.fromisoformat(appointment["starts_at"])
        ).total_seconds() == duration * 60


@pytest.mark.postgres
def test_schedule_edit_display_zone_and_explicit_action_states(catalog: SourceContext) -> None:
    app = _application(catalog)
    assert (
        _write(catalog, app, "appointments", _appointment(local_end=None), expected=422)["code"]
        == "interview_end_required"
    )
    assert (
        _write(
            catalog, app, "appointments", _appointment(timezone="Invalid/Nowhere"), expected=422
        )["code"]
        == "invalid_timezone"
    )
    app = _write(catalog, app, "appointments", _appointment())
    original = app["appointments"][0]
    app = _write(
        catalog, app, f"appointments/{original['id']}/timezone", {"timezone": "Asia/Kolkata"}
    )
    shown = app["appointments"][0]
    for name in (
        "starts_at",
        "ends_at",
        "timezone",
        "local_start",
        "local_end",
        "duration_minutes",
    ):
        assert shown[name] == original[name]
    assert shown["display_start"].endswith("+05:30") and shown["display_timezone"] == "Asia/Kolkata"
    app = _write(
        catalog,
        app,
        f"appointments/{original['id']}",
        _appointment(
            title="Edited synthetic interview",
            local_start="2026-10-29T09:30:00",
            local_end="2026-10-29T10:00:00",
        ),
        method="PUT",
    )
    edited = app["appointments"][0]
    assert edited["duration_minutes"] == 30 and edited["display_timezone"] == "Asia/Kolkata"
    app = _write(catalog, app, f"appointments/{original['id']}/status", {"status": "completed"})
    assert (
        app["appointments"][0]["completed_at"] is not None
        and app["appointments"][0]["cancelled_at"] is None
    )
    app = _write(catalog, app, f"appointments/{original['id']}/status", {"status": "cancelled"})
    assert (
        app["appointments"][0]["completed_at"] is None
        and app["appointments"][0]["cancelled_at"] is not None
    )
    app = _write(catalog, app, f"appointments/{original['id']}/status", {"status": "scheduled"})
    assert (
        app["appointments"][0]["status"] == "scheduled"
        and app["appointments"][0]["cancelled_at"] is None
    )


@pytest.mark.postgres
@pytest.mark.parametrize("closed_stage", ["rejected", "withdrawn"])
def test_closing_cancels_pending_except_explicit_distinct_followup(
    catalog: SourceContext, closed_stage: str
) -> None:
    app = _application(catalog)
    for payload in (
        _appointment(),
        _appointment(kind="follow_up", title="Retained synthetic followup", local_end=None),
        _appointment(kind="follow_up", title="Cancelled synthetic followup", local_end=None),
    ):
        app = _write(catalog, app, "appointments", payload)
    appointments = {item["title"]: item for item in app["appointments"]}
    interview = appointments["Synthetic local interview"]
    retained = appointments["Retained synthetic followup"]
    assert (
        _write(
            catalog,
            app,
            "stage",
            {
                "to_stage": closed_stage,
                "effective_at": "2026-10-25T00:00:00Z",
                "reason": "Synthetic close",
                "retain_follow_up_ids": [interview["id"]],
            },
            expected=422,
        )["code"]
        == "invalid_retained_follow_up"
    )
    app = _stage(catalog, app, closed_stage, retain_follow_up_ids=[retained["id"]])
    states = {item["id"]: item["status"] for item in app["appointments"]}
    assert states[retained["id"]] == "scheduled" and states[interview["id"]] == "cancelled"
    assert list(states.values()).count("cancelled") == 2
    assert (
        _write(
            catalog,
            app,
            f"appointments/{interview['id']}/status",
            {"status": "scheduled"},
            expected=409,
        )["code"]
        == "reopen_application_required"
    )
    assert (
        _write(catalog, app, "appointments", _appointment(), expected=409)["code"]
        == "reopen_application_required"
    )
    assert (
        _write(
            catalog,
            app,
            f"appointments/{retained['id']}",
            _appointment(kind="follow_up", local_end=None),
            method="PUT",
            expected=409,
        )["code"]
        == "reopen_application_required"
    )
    initial = _history(catalog, app)["items"][-1]
    # A historical-only rejection correction must not cancel the explicitly retained follow-up.
    app = _stage(catalog, app, "rejected", correction_of=initial["id"])
    assert (
        next(item for item in app["appointments"] if item["id"] == retained["id"])["status"]
        == "scheduled"
    )
    correction = _history(catalog, app)["items"][0]
    assert (
        _write(
            catalog,
            app,
            "stage",
            {
                "to_stage": "rejected",
                "effective_at": "2026-10-01T00:00:00Z",
                "reason": "Historical only",
                "correction_of": correction["id"],
                "retain_follow_up_ids": [retained["id"]],
            },
            expected=422,
        )["code"]
        == "retention_requires_closing_stage"
    )
    app = _stage(catalog, app, "interview")
    app = _write(catalog, app, f"appointments/{interview['id']}/status", {"status": "scheduled"})
    assert (
        next(item for item in app["appointments"] if item["id"] == interview["id"])["status"]
        == "scheduled"
    )


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("name", "data", "code"),
    [
        ("../secret.txt", b"literal", "invalid_attachment_filename"),
        ("a\\secret.txt", b"literal", "invalid_attachment_filename"),
        ("evil\u202eexe.txt", b"literal", "invalid_attachment_filename"),
        ("line\nname.txt", b"literal", "invalid_attachment_filename"),
        ("run.EXE.txt", b"literal", "attachment_type_rejected"),
        ("run.sh", b"echo synthetic", "attachment_type_rejected"),
        ("safe.txt", b"MZsynthetic", "attachment_type_rejected"),
        ("safe.txt", b"\x7fELFsynthetic", "attachment_type_rejected"),
        ("safe.txt", b"PK\x03\x04synthetic", "attachment_type_rejected"),
        ("safe.txt", b"#!synthetic", "attachment_type_rejected"),
        ("safe.txt", b"\x1f\x8bsynthetic", "attachment_type_rejected"),
        ("safe.txt", b"a" * 257 + b"ustar", "attachment_type_rejected"),
        ("safe.txt", b"\xff\x80", "unsupported_attachment_content"),
        ("safe.txt", b"a\x00b", "unsupported_attachment_content"),
        ("empty.txt", b"", "empty_attachment"),
    ],
)
def test_hostile_attachment_rejection_leaves_aggregate_unchanged(
    catalog: SourceContext, name: str, data: bytes, code: str
) -> None:
    app = _application(catalog)
    response = _upload(
        catalog,
        app,
        name,
        data,
        expected=422 if code in {"invalid_attachment_filename", "empty_attachment"} else 415,
    )
    assert response.json()["code"] == code
    assert (
        _get(catalog, app)["version"] == app["version"] and _get(catalog, app)["attachments"] == []
    )


@pytest.mark.postgres
def test_attachment_download_is_inert_parent_owner_bound_and_hash_checked(
    catalog: SourceContext,
) -> None:
    app = _application(catalog)
    content = '<svg onload="alert(1)">Fictional \u03bb</svg>'.encode()
    response = _upload(catalog, app, 'Fictional \u03bb "literal".svg', content)
    app = response.json()
    attachment = app["attachments"][0]
    assert attachment["sha256"] == hashlib.sha256(content).hexdigest() and attachment[
        "byte_length"
    ] == len(content)
    path = f"/api/v1/applications/{app['id']}/attachments/{attachment['id']}/download"
    downloaded = catalog.client.get(path)
    assert (
        downloaded.content == content
        and downloaded.headers["content-type"] == "application/octet-stream"
    )
    assert downloaded.headers["content-disposition"].startswith(
        "attachment; filename=\"attachment.bin\"; filename*=UTF-8''"
    )
    assert (
        "%CE%BB" in downloaded.headers["content-disposition"]
        and "%22" in downloaded.headers["content-disposition"]
    )
    assert (
        downloaded.headers["x-content-type-options"] == "nosniff"
        and downloaded.headers["cache-control"] == "no-store"
    )
    other = _application(catalog, canonical_url="https://careers.fictional-employer.com/other")
    assert catalog.client.get(path.replace(app["id"], other["id"])).status_code == 404
    with catalog.identity.client() as anonymous:
        assert anonymous.get(path).status_code == 401
    with catalog.runtime.transaction() as conn:
        with pytest.raises(Problem) as error:
            tracking.get_application(conn, uuid4(), UUID(app["id"]))
        assert error.value.status == 404
    # One digest may already have PDF metadata from another legitimate consumer.
    pdf = b"%PDF-1.7\nSynthetic retained PDF\n%%EOF"
    blob = BlobStore(catalog.identity.settings.blob_root).put_bytes(catalog.owner_id, pdf)
    with catalog.runtime.transaction() as conn:
        conn.execute(
            text(
                "INSERT INTO offerloom.blobs(owner_id,sha256,byte_length,media_type,storage_key) VALUES(:owner,:sha,:size,'application/pdf',:key)"
            ),
            {
                "owner": catalog.owner_id,
                "sha": blob.sha256,
                "size": blob.byte_length,
                "key": blob.storage_key,
            },
        )
    app = _upload(catalog, app, "retained.pdf", pdf).json()
    with catalog.runtime.transaction() as conn:
        assert (
            conn.execute(
                text(
                    "SELECT media_type FROM offerloom.blobs WHERE owner_id=:owner AND sha256=:sha"
                ),
                {"owner": catalog.owner_id, "sha": blob.sha256},
            ).scalar_one()
            == "application/pdf"
        )
    pdf_attachment = next(item for item in app["attachments"] if item["sha256"] == blob.sha256)
    assert (
        catalog.client.get(
            f"/api/v1/applications/{app['id']}/attachments/{pdf_attachment['id']}/download"
        ).headers["content-type"]
        == "application/octet-stream"
    )
    digest = attachment["sha256"]
    stored = catalog.identity.settings.blob_root / str(catalog.owner_id) / digest[:2] / digest
    stored.write_bytes(b"corrupted")
    assert catalog.client.get(path).status_code == 410
    stored.unlink()
    stored.symlink_to(catalog.identity.settings.blob_root / "outside-synthetic")
    assert catalog.client.get(path).status_code == 410
    stored.unlink()
    stored.write_bytes(content)
    owner_directory = catalog.identity.settings.blob_root / str(catalog.owner_id)
    moved = owner_directory.with_name(str(catalog.owner_id) + "-synthetic")
    owner_directory.rename(moved)
    try:
        owner_directory.symlink_to(moved, target_is_directory=True)
        assert catalog.client.get(path).status_code == 410
        _upload(catalog, app, "synthetic-new.txt", b"Second synthetic body", expected=503)
    finally:
        owner_directory.unlink()
        moved.rename(owner_directory)


@pytest.mark.postgres
def test_actual_stream_bound_and_exact_upload_route_media_gating(catalog: SourceContext) -> None:
    app = _application(catalog)
    _upload(catalog, app, "huge.txt", b"x" * (tracking.MAX_ATTACHMENT_BYTES + 1), expected=413)
    headers = {**catalog.headers(app["version"]), "Content-Type": "application/octet-stream"}
    for path, method in (
        (f"/api/v1/applications/{app['id']}/attachments/extra", "POST"),
        (f"/api/v1/applications/{app['id']}/notes/attachments", "POST"),
        (f"/api/v1/applications/{app['id']}/attachments", "PUT"),
        ("/api/v1/applications/not-a-uuid/attachments", "POST"),
        ("/api/v1/imports", "POST"),
    ):
        assert (
            catalog.client.request(method, path, content=b"literal", headers=headers).status_code
            == 415
        )

    # Invoke the actual ASGI stack with dishonest Content-Length and multiple chunks.
    # TestClient's encoder may coalesce streams, so the transport boundary is explicit.
    async def chunked_request() -> list[Message]:
        messages: list[Message] = []
        chunks = iter(
            [
                {
                    "type": "http.request",
                    "body": b"a" * tracking.MAX_ATTACHMENT_BYTES,
                    "more_body": True,
                },
                {"type": "http.request", "body": b"b", "more_body": False},
            ]
        )

        async def receive() -> Message:
            return cast(Message, next(chunks))

        async def send(message: Message) -> None:
            messages.append(message)

        scope: Scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": f"/api/v1/applications/{app['id']}/attachments",
            "raw_path": f"/api/v1/applications/{app['id']}/attachments".encode(),
            "root_path": "",
            "query_string": b"display_name=chunked.txt",
            "server": ("localhost", 443),
            "client": ("127.0.0.1", 1),
            "headers": [
                (b"host", b"localhost"),
                (b"origin", b"https://localhost"),
                (b"content-type", b"application/octet-stream"),
                (b"content-length", b"1"),
            ],
        }
        await catalog.client.app(scope, receive, send)
        return messages

    messages = asyncio.run(chunked_request())
    assert (
        next(message for message in messages if message["type"] == "http.response.start")["status"]
        == 413
    )
    assert _get(catalog, app)["version"] == app["version"]


@pytest.mark.postgres
def test_notes_contacts_private_projection_bounds_and_child_parent_relationships(
    catalog: SourceContext, caplog: pytest.LogCaptureFixture
) -> None:
    app = _application(catalog)
    profile = _profile(catalog)
    marker = "PRIVATE_SYNTHETIC_BODY_" + uuid4().hex
    app = _write(catalog, app, "notes", {"body": marker + "<script>literal</script>"})
    note = app["notes"][0]
    app = _write(catalog, app, f"notes/{note['id']}", {"body": marker + " revised"}, method="PUT")
    assert app["notes"][0]["version"] == 2
    app = _write(
        catalog,
        app,
        "contacts",
        {"display_name": marker, "email": None, "role_label": "Synthetic role"},
    )
    contact = app["contacts"][0]
    assert contact["email"] is None
    app = _write(
        catalog,
        app,
        f"contacts/{contact['id']}",
        {"display_name": marker, "email": "fictional@example.com", "role_label": None},
        method="PUT",
    )
    assert app["contacts"][0]["version"] == 2
    other = _application(catalog, canonical_url="https://careers.fictional-employer.com/another")
    assert (
        _write(
            catalog,
            other,
            f"notes/{note['id']}",
            {"body": "wrong parent"},
            method="PUT",
            expected=404,
        )["code"]
        == "note_not_found"
    )
    assert (
        _write(
            catalog,
            other,
            f"contacts/{contact['id']}",
            {"display_name": "wrong parent"},
            method="PUT",
            expected=404,
        )["code"]
        == "contact_not_found"
    )
    assert (
        _write(catalog, app, "notes", {"body": "a" * 50001}, expected=422)["code"]
        == "invalid_request"
    )
    assert (
        _write(catalog, app, "notes", {"body": "bad\x00body"}, expected=422)["code"]
        == "invalid_request"
    )
    for email in (
        "no-address",
        "a\n@example.com",
        "a@example..com",
        "a@-invalid.com",
        "a b@example.com",
    ):
        assert (
            _write(
                catalog,
                app,
                "contacts",
                {"display_name": "Synthetic", "email": email},
                expected=422,
            )["code"]
            == "invalid_request"
        )
    assert marker not in caplog.text
    with catalog.runtime.transaction() as conn:
        projection = suitability._profile_projection(
            conn, catalog.owner_id, UUID(profile["current_revision"]["id"])
        )
        assert projection.claims and projection.evidence
        assert marker not in canonical_json(
            {
                "claims": list(projection.claims.values()),
                "evidence": list(projection.evidence.values()),
            }
        )
        outbox = (
            conn.execute(
                text("SELECT payload FROM offerloom.outbox_events WHERE owner_id=:owner"),
                {"owner": catalog.owner_id},
            )
            .scalars()
            .all()
        )
        assert marker not in json.dumps(outbox)
        audit = (
            conn.execute(
                text("SELECT detail FROM offerloom.audit_events WHERE owner_id=:owner"),
                {"owner": catalog.owner_id},
            )
            .scalars()
            .all()
        )
        assert marker not in json.dumps(audit)
        conn.execute(
            text("""INSERT INTO offerloom.application_notes(owner_id,application_id,body)
            SELECT :owner,:app,'Synthetic quota' FROM generate_series(1,99)"""),
            {"owner": catalog.owner_id, "app": UUID(app["id"])},
        )
        conn.execute(
            text("""INSERT INTO offerloom.application_contacts(owner_id,application_id,display_name)
            SELECT :owner,:app,'Synthetic quota' FROM generate_series(1,99)"""),
            {"owner": catalog.owner_id, "app": UUID(app["id"])},
        )
    assert (
        _write(catalog, app, "notes", {"body": "over quota"}, expected=422)["code"]
        == "tracking_quota_exceeded"
    )
    assert (
        _write(catalog, app, "contacts", {"display_name": "over quota"}, expected=422)["code"]
        == "tracking_quota_exceeded"
    )
    retained = _get(catalog, app)
    assert len(retained["notes"]) == len(retained["contacts"]) == 100


@pytest.mark.postgres
def test_auth_csrf_cas_idempotency_and_validation_do_not_echo_private_inputs(
    catalog: SourceContext,
) -> None:
    app = _application(catalog)
    path = f"/api/v1/applications/{app['id']}/notes"
    body = {"body": "Synthetic private note"}
    with catalog.identity.client() as anonymous:
        for route in (
            "/api/v1/applications",
            "/api/v1/applications/counts",
            "/api/v1/applications/attention",
            f"/api/v1/applications/{app['id']}",
            f"/api/v1/applications/{app['id']}/stage-events",
        ):
            assert anonymous.get(route).status_code == 401
        assert (
            anonymous.post(path, json=body, headers=catalog.headers(app["version"])).status_code
            == 401
        )
    for headers, expected in (
        ({**catalog.headers(app["version"]), "Origin": "https://foreign.invalid"}, 403),
        ({**catalog.headers(app["version"]), "X-CSRF-Token": "wrong"}, 403),
        (catalog.headers(), 428),
        ({**catalog.headers(app["version"]), "If-Match": "1"}, 400),
        (catalog.headers(app["version"] + 1), 412),
        ({**catalog.headers(app["version"]), "Idempotency-Key": "short"}, 400),
    ):
        assert catalog.client.post(path, json=body, headers=headers).status_code == expected
    key = uuid4().hex
    headers = catalog.headers(app["version"], key)
    saved = catalog.client.post(path, json=body, headers=headers)
    assert saved.status_code == 200
    assert catalog.client.post(path, json=body, headers=headers).json() == saved.json()
    assert catalog.client.post(path, json={"body": "changed"}, headers=headers).status_code == 409
    invalid = catalog.client.post(
        path,
        json={"body": {"private-field": "DO_NOT_ECHO_SYNTHETIC"}},
        headers=catalog.headers(saved.json()["version"]),
    )
    assert (
        invalid.status_code == 422
        and "DO_NOT_ECHO_SYNTHETIC" not in invalid.text
        and "private-field" not in invalid.text
    )
    assert catalog.client.get("/api/v1/applications", params={"limit": 101}).status_code == 422
    assert catalog.client.get("/api/v1/applications", params={"archived": "yes"}).status_code == 422
    assert (
        catalog.client.get(
            "/api/v1/applications/counts", params={"period_start": "2026-10-01T00:00:00Z"}
        ).status_code
        == 422
    )
    assert (
        catalog.client.get(
            "/api/v1/applications/counts",
            params={"period_start": "2026-11-01T00:00:00Z", "period_end": "2026-10-01T00:00:00Z"},
        ).status_code
        == 422
    )
    assert catalog.client.get(f"/api/v1/applications/{uuid4()}").status_code == 404
    assert catalog.client.get(f"/api/v1/applications/{uuid4()}/stage-events").status_code == 404
    moved = catalog.client.post(
        f"/api/v1/applications/{app['id']}/stage",
        json={
            "to_stage": "applied",
            "effective_at": "2026-10-01T00:00:00Z",
            "reason": "Synthetic latency",
        },
        headers=catalog.headers(saved.json()["version"]),
    )
    assert moved.status_code == 200 and float(moved.headers["server-timing"].split("dur=")[1]) >= 0


@pytest.mark.postgres
def test_child_corrections_and_appointment_writes_are_parent_and_owner_bound(
    catalog: SourceContext,
) -> None:
    app = _application(catalog)
    other = _application(
        catalog, canonical_url="https://careers.fictional-employer.com/child-boundary"
    )
    app = _write(catalog, app, "appointments", _appointment())
    appointment = app["appointments"][0]
    assert (
        _write(
            catalog,
            other,
            f"appointments/{appointment['id']}",
            _appointment(),
            method="PUT",
            expected=404,
        )["code"]
        == "appointment_not_found"
    )
    assert (
        _write(
            catalog,
            other,
            f"appointments/{appointment['id']}/timezone",
            {"timezone": "UTC"},
            expected=404,
        )["code"]
        == "appointment_not_found"
    )
    assert (
        _write(
            catalog,
            other,
            f"appointments/{appointment['id']}/status",
            {"status": "completed"},
            expected=404,
        )["code"]
        == "appointment_not_found"
    )
    foreign_event = _history(catalog, other)["items"][0]
    for identifier in (foreign_event["id"], str(uuid4())):
        assert (
            _write(
                catalog,
                app,
                "stage",
                {
                    "to_stage": "applied",
                    "effective_at": "2026-10-01T00:00:00Z",
                    "reason": "Wrong synthetic target",
                    "correction_of": identifier,
                },
                expected=409,
            )["code"]
            == "invalid_correction_target"
        )
    for correction in (
        {"reason": ""},
        {"retain_follow_up_ids": [appointment["id"], appointment["id"]]},
    ):
        assert (
            _write(
                catalog,
                app,
                "stage",
                {
                    "to_stage": "rejected",
                    "effective_at": "2026-10-01T00:00:00Z",
                    "reason": "Synthetic",
                    "correction_of": app["current_stage_event_id"],
                    **correction,
                },
                expected=422,
            )["code"]
            == "invalid_request"
        )
    with catalog.runtime.transaction() as conn:
        assert (
            tracking.list_applications(
                conn, uuid4(), catalog.identity.settings.keyring, tracking.ApplicationQuery()
            )["items"]
            == []
        )
        assert (
            sum(
                cast(
                    dict[str, int],
                    tracking.counts(conn, uuid4(), tracking.PeriodQuery())["current_by_stage"],
                ).values()
            )
            == 0
        )
        assert tracking.attention(conn, uuid4(), 20)["items"] == []
        with pytest.raises(Problem):
            tracking.stage_history(conn, uuid4(), UUID(app["id"]), None, 50)
    assert _get(catalog, app)["version"] == app["version"]
    assert _get(catalog, other)["version"] == other["version"]


@pytest.mark.postgres
def test_attention_due_upcoming_excludes_completed_cancelled_and_far_future(
    catalog: SourceContext,
) -> None:
    app = _application(catalog)
    now = datetime.now(UTC)
    schedules = [
        ("due followup", "follow_up", now - timedelta(days=2)),
        ("upcoming interview", "interview", now + timedelta(days=2)),
        ("future followup", "follow_up", now + timedelta(days=2)),
        ("distant interview", "interview", now + timedelta(days=8)),
        ("completed followup", "follow_up", now - timedelta(days=1)),
        ("cancelled followup", "follow_up", now - timedelta(days=1)),
    ]
    for title, kind, start in schedules:
        app = _write(
            catalog,
            app,
            "appointments",
            _appointment(
                title=title,
                kind=kind,
                timezone="UTC",
                local_start=start.strftime("%Y-%m-%dT%H:%M:00"),
                local_end=(start + timedelta(minutes=60)).strftime("%Y-%m-%dT%H:%M:00")
                if kind == "interview"
                else None,
            ),
        )
    by_title = {item["title"]: item for item in app["appointments"]}
    for title, status in (("completed followup", "completed"), ("cancelled followup", "cancelled")):
        app = _write(
            catalog, app, f"appointments/{by_title[title]['id']}/status", {"status": status}
        )
    response = catalog.client.get("/api/v1/applications/attention")
    assert response.status_code == 200, response.text
    attention = response.json()
    assert attention["query_elapsed_ms"] >= 0
    assert [item["appointment"]["title"] for item in attention["items"]] == [
        "due followup",
        "upcoming interview",
    ]
    assert all(
        set(item) == {"application_id", "title", "company", "appointment"}
        for item in attention["items"]
    )
    assert (
        len(
            catalog.client.get("/api/v1/applications/attention", params={"limit": 1}).json()[
                "items"
            ]
        )
        == 1
    )
    assert _get(catalog, app)["next_action"]["title"] == "due followup"


@pytest.mark.postgres
def test_appointment_quota_rejects_addition_without_hiding_retained_rows(
    catalog: SourceContext,
) -> None:
    app = _application(catalog)
    with catalog.runtime.transaction() as conn:
        conn.execute(
            text("""INSERT INTO offerloom.appointments(owner_id,application_id,kind,title,starts_at,local_start,timezone,display_timezone)
            SELECT :owner,:app,'follow_up','Synthetic retained quota',timestamptz '2026-10-01 00:00:00+00',
            timestamp '2026-10-01 00:00:00','UTC','UTC' FROM generate_series(1,200)"""),
            {"owner": catalog.owner_id, "app": UUID(app["id"])},
        )
    assert (
        _write(
            catalog,
            app,
            "appointments",
            _appointment(kind="follow_up", local_end=None),
            expected=422,
        )["code"]
        == "tracking_quota_exceeded"
    )
    retained = _get(catalog, app)
    assert len(retained["appointments"]) == 200 and retained["version"] == app["version"]
    child = retained["appointments"][0]
    updated = _write(
        catalog, retained, f"appointments/{child['id']}/status", {"status": "completed"}
    )
    assert len(updated["appointments"]) == 200


@pytest.mark.postgres
@pytest.mark.parametrize("quota", ["count", "application_bytes", "owner_bytes"])
def test_attachment_reference_count_and_byte_quotas_use_retained_real_blobs(
    catalog: SourceContext, quota: str
) -> None:
    app = _application(catalog)
    size = (
        1 if quota == "count" else 10 * 1024 * 1024 if quota == "application_bytes" else 1024 * 1024
    )
    app = _upload(catalog, app, "synthetic-quota.txt", b"x" * size).json()
    with catalog.runtime.transaction() as conn:
        blob_id = conn.execute(
            text(
                "SELECT blob_id FROM offerloom.application_attachments WHERE owner_id=:owner AND id=:id"
            ),
            {"owner": catalog.owner_id, "id": UUID(app["attachment_id"])},
        ).scalar_one()
    targets = [(app, 99 if quota == "count" else 9 if quota == "application_bytes" else 99)]
    if quota == "owner_bytes":
        for index in range(10):
            target = _application(
                catalog, canonical_url=f"https://careers.fictional-employer.com/quota/{index}"
            )
            targets.append((target, 100 if index < 9 else 24))
    with catalog.runtime.transaction() as conn:
        for target, extra in targets:
            conn.execute(
                text("""INSERT INTO offerloom.application_attachments(owner_id,application_id,blob_id,display_name)
                SELECT :owner,:app,:blob,'Synthetic retained reference.txt' FROM generate_series(1,:n)"""),
                {"owner": catalog.owner_id, "app": UUID(target["id"]), "blob": blob_id, "n": extra},
            )
    destination = targets[-1][0]
    response = _upload(catalog, destination, "one-more.txt", b"y", expected=422)
    assert response.json()["code"] == (
        "tracking_quota_exceeded" if quota == "count" else "attachment_quota_exceeded"
    )
    assert _get(catalog, destination)["version"] == destination["version"]
    assert len(_get(catalog, app)["attachments"]) == (
        100 if quota in {"count", "owner_bytes"} else 10
    )


@pytest.mark.postgres
def test_attachment_replay_csrf_cas_and_changed_payload_do_not_duplicate_bytes(
    catalog: SourceContext,
) -> None:
    app = _application(catalog)
    path = f"/api/v1/applications/{app['id']}/attachments"
    payload = b"Synthetic immutable attachment"
    key = uuid4().hex
    headers = {**catalog.headers(app["version"], key), "Content-Type": "application/octet-stream"}
    params = {"display_name": "synthetic.txt"}
    first = catalog.client.post(path, params=params, content=payload, headers=headers)
    assert first.status_code == 200, first.text
    assert (
        catalog.client.post(path, params=params, content=payload, headers=headers).json()
        == first.json()
    )
    assert (
        catalog.client.post(
            path, params={"display_name": "changed.txt"}, content=payload, headers=headers
        ).status_code
        == 409
    )
    assert (
        catalog.client.post(
            path, params=params, content=payload + b"changed", headers=headers
        ).status_code
        == 409
    )
    assert (
        _upload(catalog, app, "synthetic.txt", payload, expected=412).json()["code"]
        == "version_conflict"
    )
    for overrides in ({"Origin": "https://foreign.invalid"}, {"X-CSRF-Token": "wrong"}):
        _upload(catalog, first.json(), "synthetic.txt", payload, headers=overrides, expected=403)
    with catalog.runtime.transaction() as conn:
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM offerloom.application_attachments WHERE owner_id=:owner"
                ),
                {"owner": catalog.owner_id},
            ).scalar_one()
            == 1
        )
        assert (
            conn.execute(
                text("SELECT count(*) FROM offerloom.blobs WHERE owner_id=:owner"),
                {"owner": catalog.owner_id},
            ).scalar_one()
            == 1
        )


@pytest.mark.postgres
def test_unicode_scalar_note_limit_and_literal_text_survive_reload(catalog: SourceContext) -> None:
    app = _application(catalog)
    literal = "\U0001f9f5" * 49970 + "<script>literal text</script>"
    assert len(literal) <= 50000
    app = _write(catalog, app, "notes", {"body": literal})
    assert _get(catalog, app)["notes"][0]["body"] == literal
    # JSON can represent an unpaired surrogate, but PostgreSQL UTF-8 cannot.
    response = catalog.client.post(
        f"/api/v1/applications/{app['id']}/notes",
        content=json.dumps({"body": "\ud800"}),
        headers={**catalog.headers(app["version"]), "Content-Type": "application/json"},
    )
    assert response.status_code == 422 and "\\ud800" not in response.text
