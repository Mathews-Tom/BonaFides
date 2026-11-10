# SPDX-License-Identifier: MIT OR Apache-2.0
"""Real PostgreSQL/HTTP regressions for local pipeline projections."""

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy import text
from test_identity import identity as identity
from test_sources import SourceContext
from test_sources import catalog as catalog
from test_tracking import _application, _appointment, _history, _stage, _write

from bonafides import pipeline_views


def _insights(context: SourceContext, **query: object) -> dict[str, Any]:
    response = context.client.get("/api/v1/applications/insights", params=cast(Any, query))
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


@pytest.mark.postgres
def test_empty_cohort_has_null_rates_and_52_monday_buckets(catalog: SourceContext) -> None:
    result = _insights(catalog, cohort="30")
    counts = result["cohort_counts"]
    assert counts["application_count"] == counts["no_progression_count"] == 0
    assert all(s["rate"] is None and s["reached_count"] == 0 for s in counts["stages"])
    assert sum(counts["current_by_stage"].values()) == 0
    assert set(counts["current_by_stage"]) == {
        "preparing",
        "applied",
        "screening",
        "interview",
        "offer",
        "accepted",
        "rejected",
        "withdrawn",
    }
    weeks = result["weeks"]
    assert len(weeks) == 52 and result["next"] == {"items": [], "has_more": False}
    assert all(datetime.fromisoformat(w["week_start"]).weekday() == 0 for w in weeks)
    assert all(w["created"] == w["recorded_applied"] == 0 for w in weeks)
    latest = datetime.fromisoformat(weeks[-1]["week_start"]).date()
    assert latest <= datetime.fromisoformat(result["as_of"]).date() < latest + timedelta(days=7)


@pytest.mark.postgres
def test_funnel_corrections_backward_rejection_repeat_archive_and_cohorts(
    catalog: SourceContext,
) -> None:
    backward = _stage(catalog, _application(catalog), "interview", "2099-01-01T00:00:00Z")
    backward = _stage(catalog, backward, "screening")
    _write(catalog, backward, "archive", {"archived": True})
    rejected = _stage(catalog, _application(catalog), "rejected")
    repeat = _write(catalog, rejected, "repeat", {"confirmed": True}, expected=201)
    corrected = _stage(catalog, _application(catalog), "interview")
    corrected = _stage(
        catalog, corrected, "offer", correction_of=corrected["current_stage_event_id"]
    )
    corrected = _stage(
        catalog, corrected, "applied", correction_of=corrected["current_stage_event_id"]
    )
    history = _history(catalog, corrected)
    all_time = _insights(catalog)
    counts = all_time["cohort_counts"]
    assert counts["application_count"] == 4 and counts["archived_application_count"] == 1
    assert counts["no_progression_count"] == counts["inferred_applied_from_rejection"] == 1
    assert [(s["stage"], s["reached_count"], s["rate"]) for s in counts["stages"]] == [
        ("applied", 3, 0.75),
        ("screening", 1, 0.25),
        ("interview", 1, 0.25),
        ("offer", 0, 0),
        ("accepted", 0, 0),
    ]
    assert counts["current_by_stage"] == {
        "preparing": 1,
        "applied": 1,
        "screening": 1,
        "interview": 0,
        "offer": 0,
        "accepted": 0,
        "rejected": 1,
        "withdrawn": 0,
    }
    with catalog.identity.admin.transaction() as conn:
        for app, days in ((backward, 31), (rejected, 91), (repeat, 91)):
            conn.execute(
                text("""UPDATE bonafides.applications SET created_at=now()-:days*interval '1 day'
                WHERE owner_id=:owner AND id=:id"""),
                {"owner": catalog.owner_id, "id": app["id"], "days": days},
            )
    short, medium = _insights(catalog, cohort="30"), _insights(catalog, cohort="90")
    assert short["cohort_counts"]["application_count"] == 1
    assert medium["cohort_counts"]["application_count"] == 2
    assert short["cohort_counts"]["stages"][1]["reached_count"] == 0
    assert short["next"] == medium["next"] == _insights(catalog)["next"]
    assert _history(catalog, corrected) == history


@pytest.mark.postgres
def test_weekly_utc_boundaries_use_creation_and_earliest_valid_effective_clocks(
    catalog: SourceContext,
) -> None:
    with catalog.runtime.transaction() as conn:
        monday = cast(
            datetime,
            conn.execute(
                text("SELECT date_trunc('week',now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'")
            ).scalar_one(),
        )
    before = monday - timedelta(microseconds=1)
    app = _stage(catalog, _application(catalog), "applied", before.isoformat())
    app = _stage(
        catalog, app, "applied", monday.isoformat(), correction_of=app["current_stage_event_id"]
    )
    _stage(catalog, app, "screening", (monday + timedelta(days=2)).isoformat())
    rejected = _stage(catalog, _application(catalog), "rejected", before.isoformat())
    other = _stage(catalog, _application(catalog), "offer", monday.isoformat())
    with catalog.identity.admin.transaction() as conn:
        for created_before in (app, other):
            conn.execute(
                text("""UPDATE bonafides.applications SET created_at=:created
                WHERE owner_id=:owner AND id=:id"""),
                {"owner": catalog.owner_id, "id": created_before["id"], "created": before},
            )
        conn.execute(
            text("""UPDATE bonafides.applications SET created_at=:created
            WHERE owner_id=:owner AND id=:id"""),
            {"owner": catalog.owner_id, "id": rejected["id"], "created": monday},
        )
    weeks = _insights(catalog, cohort="30")["weeks"]
    assert (weeks[-2]["created"], weeks[-2]["recorded_applied"]) == (2, 1)
    assert (weeks[-1]["created"], weeks[-1]["recorded_applied"]) == (1, 2)
    assert weeks[-1]["week_start"] == monday.date().isoformat()
    assert sum(w["recorded_applied"] for w in weeks) == 3


@pytest.mark.postgres
def test_next_priority_dedup_quiet_clock_and_bounded_lookahead(catalog: SourceContext) -> None:
    now = datetime.fromisoformat(_insights(catalog)["as_of"]).astimezone(UTC)
    apps = [_application(catalog, title=f"Synthetic next {i}") for i in range(8)]
    for index, stage in (
        (1, "interview"),
        (2, "offer"),
        (4, "applied"),
        (5, "applied"),
        (6, "interview"),
        (7, "interview"),
    ):
        if index == 4:
            apps[index] = _stage(
                catalog,
                apps[index],
                "preparing",
                (now - timedelta(days=10)).isoformat(),
                correction_of=apps[index]["current_stage_event_id"],
            )
        effective = now + timedelta(days=10) if index == 5 else now - timedelta(days=10)
        apps[index] = _stage(catalog, apps[index], stage, effective.isoformat())
    for index, kind, days in (
        (0, "follow_up", -2),
        (0, "follow_up", -1),
        (0, "interview", 1),
        (1, "interview", 2),
        (5, "follow_up", 9),
        (6, "interview", -2),
    ):
        start = now + timedelta(days=days)
        apps[index] = _write(
            catalog,
            apps[index],
            "appointments",
            _appointment(
                kind=kind,
                timezone="UTC",
                local_start=start.strftime("%Y-%m-%dT%H:%M:00"),
                local_end=(start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:00")
                if kind == "interview"
                else None,
            ),
        )
    next_items = _insights(catalog)["next"]["items"]
    assert [item["application_id"] for item in next_items] == [app["id"] for app in apps]
    assert len({item["application_id"] for item in next_items}) == 8
    assert [item["reason"] for item in next_items] == [
        "Overdue follow-up",
        "Interview within seven days",
        "Review your offer",
        "Continue preparing",
        "No recorded progress recently",
        "Scheduled local work",
        "Past interview needs an update",
        "Interview has no scheduled time",
    ]
    assert apps[4]["id"] not in {
        item["application_id"] for item in _insights(catalog, quiet_days=14)["next"]["items"]
    }
    followups = [ap for ap in apps[0]["appointments"] if ap["kind"] == "follow_up"]
    for appointment, status in zip(followups, ("completed", "cancelled"), strict=True):
        apps[0] = _write(
            catalog,
            apps[0],
            f"appointments/{appointment['id']}/status",
            {"status": status},
        )
    assert _insights(catalog)["next"]["items"][0]["reason"] == "Interview within seven days"
    _write(catalog, apps[0], "archive", {"archived": True})
    _stage(catalog, apps[2], "accepted")
    assert _insights(catalog)["cohort_counts"]["stages"][-1]["reached_count"] == 1
    assert {apps[0]["id"], apps[2]["id"]}.isdisjoint(
        item["application_id"] for item in _insights(catalog)["next"]["items"]
    )
    with catalog.identity.admin.transaction() as conn:
        conn.execute(
            text("""INSERT INTO bonafides.applications(
            owner_id,opportunity_id,attempt_no,current_stage,updated_at)
            SELECT owner_id,opportunity_id,n,'applied',now()-interval '10 days'
            FROM bonafides.applications CROSS JOIN generate_series(2,53) n
            WHERE owner_id=:owner AND id=:id"""),
            {"owner": catalog.owner_id, "id": apps[3]["id"]},
        )
    bounded = _insights(catalog, cohort="30")["next"]
    assert len(bounded["items"]) == len({item["application_id"] for item in bounded["items"]}) == 50
    assert bounded["has_more"] is True
    seeded = [
        item
        for item in bounded["items"]
        if item["application_id"] not in {app["id"] for app in apps}
    ]
    assert seeded and all(item["reason"] == "No recorded progress recently" for item in seeded)


@pytest.mark.postgres
def test_calendar_local_month_dst_all_statuses_and_equal_start_keysets(
    catalog: SourceContext,
) -> None:
    app = _application(catalog)
    instants = [
        "2026-11-01T03:59:00",
        "2026-11-01T04:00:00",
        "2026-11-01T05:30:00",
        "2026-11-01T05:30:00",
        "2026-11-01T06:30:00",
        "2026-12-01T04:59:00",
        "2026-12-01T05:00:00",
    ]
    for index, instant in enumerate(instants):
        app = _write(
            catalog,
            app,
            "appointments",
            _appointment(
                title=f"Slot {index}",
                timezone="UTC",
                local_start=instant,
                kind="follow_up",
                local_end="2026-11-01T04:30:00" if index == 1 else None,
            ),
        )
    appointments = {item["title"]: item for item in app["appointments"]}
    for title, status in (("Slot 2", "completed"), ("Slot 3", "cancelled")):
        app = _write(
            catalog, app, f"appointments/{appointments[title]['id']}/status", {"status": status}
        )
    hidden = _write(
        catalog,
        _application(catalog),
        "appointments",
        _appointment(
            local_start="2026-11-05T12:00:00",
            local_end="2026-11-05T13:00:00",
        ),
    )
    _write(catalog, hidden, "archive", {"archived": True})
    terminal = _write(
        catalog,
        _application(catalog),
        "appointments",
        _appointment(
            local_start="2026-11-06T12:00:00",
            local_end="2026-11-06T13:00:00",
        ),
    )
    _stage(catalog, terminal, "rejected")
    params: dict[str, Any] = {"month": "2026-11", "timezone": "America/New_York", "limit": 2}
    items: list[dict[str, Any]] = []
    while True:
        response = catalog.client.get("/api/v1/applications/calendar", params=params)
        assert response.status_code == 200, response.text
        page = response.json()
        items.extend(page["items"])
        if page["next_cursor"] is None:
            break
        params.update(
            after_start=page["next_cursor"]["starts_at"], after_id=page["next_cursor"]["id"]
        )
    assert len(items) == len({item["appointment_id"] for item in items}) == 6
    assert {item["status"] for item in items} == {"scheduled", "completed", "cancelled"}
    visible_titles = {
        item["appointment_title"] for item in items if item["application_id"] == app["id"]
    }
    assert visible_titles == {f"Slot {i}" for i in range(1, 6)}
    ordered = sorted(items, key=lambda item: (item["starts_at"], item["appointment_id"]))
    assert items == ordered
    assert items[1]["display_start"].endswith("-04:00") and items[3]["display_start"].endswith(
        "-05:00"
    )
    assert items[0]["display_start"] == "2026-11-01T00:00:00-04:00"
    assert items[0]["display_end"] == "2026-11-01T00:30:00-04:00"
    assert items[0]["ends_at"] == "2026-11-01T04:30:00+00:00"
    for item in items:
        if item["application_id"] == app["id"]:
            assert item["starts_at"] == appointments[item["appointment_title"]]["starts_at"]


@pytest.mark.postgres
@pytest.mark.parametrize(
    "path,query",
    [
        ("insights", {"cohort": "60"}),
        ("insights", {"quiet_days": 8}),
        ("calendar", {"month": "2026-13"}),
        ("calendar", {"month": "2026-11", "timezone": "Invalid/Zone"}),
        ("calendar", {"month": "2026-11", "after_start": "2026-11-01T00:00:00Z"}),
    ],
)
def test_projection_inputs_reject_invalid_scopes(
    catalog: SourceContext, path: str, query: dict[str, object]
) -> None:
    assert (
        catalog.client.get(f"/api/v1/applications/{path}", params=cast(Any, query)).status_code
        == 422
    )


@pytest.mark.postgres
def test_exact_cohort_creation_bounds_and_owner_scope(catalog: SourceContext) -> None:
    apps = [_application(catalog) for _ in range(3)]
    with catalog.identity.admin.transaction() as conn:
        for app, offset in zip(
            apps, ("-30 days", "-30 days -1 microsecond", "1 microsecond"), strict=True
        ):
            conn.execute(
                text("""UPDATE bonafides.applications SET created_at=now()+CAST(:offset AS interval)
                WHERE owner_id=:owner AND id=:id"""),
                {"owner": catalog.owner_id, "id": app["id"], "offset": offset},
            )
        scoped = pipeline_views.insights(conn, catalog.owner_id, "30", 7)
        counts = cast(dict[str, Any], scoped["cohort_counts"])
        assert counts["application_count"] == counts["no_progression_count"] == 1
        assert counts["stages"][0]["rate"] == 0
        all_time = pipeline_views.insights(conn, catalog.owner_id, "all", 7)
        assert cast(dict[str, Any], all_time["cohort_counts"])["application_count"] == 3
        foreign = pipeline_views.insights(conn, uuid4(), "all", 7)
        assert cast(dict[str, Any], foreign["cohort_counts"])["application_count"] == 0
        assert foreign["next"] == {"items": [], "has_more": False}
        assert scoped["weeks"] == all_time["weeks"]
