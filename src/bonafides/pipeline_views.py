# SPDX-License-Identifier: MIT OR Apache-2.0
"""Read-only local pipeline projections; callers supply a repeatable-read transaction."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Literal, cast
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import Connection, text

PROGRESSION_STAGES = ("applied", "screening", "interview", "offer", "accepted")
_CURRENT_STAGES = ("preparing", *PROGRESSION_STAGES, "rejected", "withdrawn")
_HISTORY = """WITH valid AS (
    SELECT e.owner_id,e.application_id,e.to_stage,e.effective_at
    FROM bonafides.application_stage_events e
    WHERE e.owner_id=:owner AND NOT EXISTS (
        SELECT 1 FROM bonafides.application_stage_events replacement
        WHERE replacement.owner_id=e.owner_id AND replacement.application_id=e.application_id
          AND replacement.correction_of=e.id)
), progress AS (
    SELECT owner_id,application_id,
      COALESCE(max(array_position(CAST(:stages AS text[]),to_stage::text)),0) AS peak,
      bool_or(to_stage='rejected') AS rejected,
      min(effective_at) FILTER (WHERE to_stage::text=ANY(CAST(:stages AS text[]))
        OR to_stage='rejected') AS first_applied,
      max(effective_at) AS latest_status
    FROM valid GROUP BY owner_id,application_id
) """
_LABELS = """JOIN bonafides.saved_opportunities o
    ON o.owner_id=a.owner_id AND o.id=a.opportunity_id
    JOIN bonafides.job_postings p ON p.owner_id=o.owner_id AND p.id=o.posting_id"""


def insights(
    conn: Connection, owner_id: UUID, cohort: Literal["all", "30", "90"], quiet_days: int
) -> dict[str, object]:
    now = cast(datetime, conn.execute(text("SELECT now()")).scalar_one()).astimezone(UTC)
    params = {
        "owner": owner_id,
        "stages": list(PROGRESSION_STAGES),
        "now": now,
        "days": None if cohort == "all" else int(cohort),
        "quiet": quiet_days,
    }
    counts = {stage: 0 for stage in _CURRENT_STAGES}
    furthest = [0] * len(PROGRESSION_STAGES)
    total = archived = inferred = 0
    for row in conn.execute(
        text(
            _HISTORY
            + """
        SELECT a.current_stage,COALESCE(g.peak,0) AS peak,COALESCE(g.rejected,false) AS rejected,
          count(*) AS n,count(*) FILTER (WHERE a.archived_at IS NOT NULL) AS archived
        FROM bonafides.applications a LEFT JOIN progress g
          ON g.owner_id=a.owner_id AND g.application_id=a.id
        WHERE a.owner_id=:owner AND (CAST(:days AS integer) IS NULL OR
          a.created_at BETWEEN :now-CAST(:days AS integer)*interval '1 day' AND :now)
        GROUP BY a.current_stage,g.peak,g.rejected"""
        ),
        params,
    ).mappings():
        n, peak = int(row["n"]), int(row["peak"])
        total += n
        archived += int(row["archived"])
        counts[row["current_stage"]] += n
        if peak == 0 and row["rejected"]:
            inferred += n
            peak = 1
        if peak:
            furthest[peak - 1] += n
    stages: list[dict[str, object]] = []
    reached = sum(furthest)
    no_progression = total - reached
    for stage, count in zip(PROGRESSION_STAGES, furthest, strict=True):
        stages.append(
            {
                "stage": stage,
                "reached_count": reached,
                "rate": reached / total if total else None,
            }
        )
        reached -= count
    weeks = [
        dict(row)
        for row in conn.execute(
            text(
                _HISTORY
                + """, weeks AS (
        SELECT generate_series(date_trunc('week',CAST(:now AS timestamptz) AT TIME ZONE 'UTC')
          -interval '51 weeks',date_trunc('week',CAST(:now AS timestamptz) AT TIME ZONE 'UTC'),
          interval '1 week') AS start
    ), activity AS (
        SELECT created_at AS occurred,'created' AS metric FROM bonafides.applications WHERE owner_id=:owner
        UNION ALL SELECT first_applied,'applied' FROM progress WHERE first_applied IS NOT NULL
    ) SELECT w.start::date::text AS week_start,
      count(*) FILTER (WHERE metric='created') AS created,
      count(*) FILTER (WHERE metric='applied') AS recorded_applied
    FROM weeks w LEFT JOIN activity t ON date_trunc('week',t.occurred AT TIME ZONE 'UTC')=w.start
    GROUP BY w.start ORDER BY w.start"""
            ),
            params,
        ).mappings()
    ]
    rows = (
        conn.execute(
            text(
                _HISTORY
                + """, candidates AS (
        SELECT a.id AS application_id,p.title,p.company,c.priority,c.reason,c.starts_at
        FROM bonafides.applications a """
                + _LABELS
                + """
        LEFT JOIN progress g ON g.owner_id=a.owner_id AND g.application_id=a.id
        CROSS JOIN LATERAL (
            SELECT CASE WHEN ap.kind='follow_up' AND ap.starts_at<:now THEN 0
              WHEN ap.kind='interview' AND ap.starts_at BETWEEN :now AND :now+interval '7 days' THEN 1
              WHEN ap.starts_at>=:now THEN 5 ELSE 6 END AS priority,
              CASE WHEN ap.kind='follow_up' AND ap.starts_at<:now THEN 'Overdue follow-up'
                WHEN ap.kind='interview' AND ap.starts_at<=:now+interval '7 days'
                  AND ap.starts_at>=:now THEN 'Interview within seven days'
                WHEN ap.starts_at>=:now THEN 'Scheduled local work'
                ELSE 'Past interview needs an update' END AS reason,ap.starts_at
            FROM bonafides.appointments ap
            WHERE ap.owner_id=a.owner_id AND ap.application_id=a.id AND ap.status='scheduled'
            UNION ALL
            SELECT CASE a.current_stage WHEN 'offer' THEN 2 WHEN 'preparing' THEN 3
              WHEN 'interview' THEN 7 ELSE 4 END,
              CASE a.current_stage WHEN 'offer' THEN 'Review your offer'
                WHEN 'preparing' THEN 'Continue preparing'
                WHEN 'interview' THEN 'Interview has no scheduled time'
                ELSE 'No recorded progress recently' END,NULL::timestamptz
            WHERE a.current_stage IN ('offer','preparing')
              OR (a.current_stage='interview' AND NOT EXISTS (
                SELECT 1 FROM bonafides.appointments ap WHERE ap.owner_id=a.owner_id
                  AND ap.application_id=a.id AND ap.kind='interview' AND ap.status='scheduled'))
              OR (a.current_stage IN ('applied','screening')
                AND COALESCE(g.latest_status,a.updated_at)<=:now-:quiet*interval '1 day')
            ORDER BY priority,starts_at NULLS LAST LIMIT 1
        ) c WHERE a.owner_id=:owner AND a.archived_at IS NULL
    ) SELECT * FROM candidates ORDER BY priority,starts_at NULLS LAST,application_id LIMIT 51"""
            ),
            params,
        )
        .mappings()
        .all()
    )
    next_items = [
        {
            "application_id": str(row["application_id"]),
            "title": row["title"],
            "company": row["company"],
            "reason": row["reason"],
            "starts_at": row["starts_at"].isoformat() if row["starts_at"] else None,
        }
        for row in rows[:50]
    ]
    return {
        "as_of": now.isoformat(),
        "cohort": cohort,
        "quiet_days": quiet_days,
        "cohort_counts": {
            "application_count": total,
            "archived_application_count": archived,
            "current_by_stage": counts,
            "no_progression_count": no_progression,
            "inferred_applied_from_rejection": inferred,
            "stages": stages,
        },
        "weeks": weeks,
        "next": {"items": next_items, "has_more": len(rows) > 50},
    }


def calendar(
    conn: Connection,
    owner_id: UUID,
    month: date,
    zone: ZoneInfo,
    *,
    after_start: datetime | None = None,
    after_id: UUID | None = None,
    limit: int = 100,
) -> dict[str, object]:
    now = cast(datetime, conn.execute(text("SELECT now()")).scalar_one()).astimezone(UTC)
    following = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    rows = (
        conn.execute(
            text(
                """SELECT a.id AS application_id,p.title,p.company,ap.id,
        ap.title AS appointment_title,ap.kind,ap.status,ap.starts_at,ap.ends_at
        FROM bonafides.appointments ap JOIN bonafides.applications a
          ON a.owner_id=ap.owner_id AND a.id=ap.application_id """
                + _LABELS
                + """
        WHERE ap.owner_id=:owner AND a.archived_at IS NULL
          AND ap.starts_at>=:start AND ap.starts_at<:end
          AND (CAST(:after_start AS timestamptz) IS NULL OR
            (ap.starts_at,ap.id)>(CAST(:after_start AS timestamptz),CAST(:after_id AS uuid)))
        ORDER BY ap.starts_at,ap.id LIMIT :limit"""
            ),
            {
                "owner": owner_id,
                "start": datetime.combine(month, time.min, zone),
                "end": datetime.combine(following, time.min, zone),
                "after_start": after_start,
                "after_id": after_id,
                "limit": limit + 1,
            },
        )
        .mappings()
        .all()
    )
    items = [
        {
            "application_id": str(row["application_id"]),
            "title": row["title"],
            "company": row["company"],
            "appointment_id": str(row["id"]),
            "appointment_title": row["appointment_title"],
            "kind": row["kind"],
            "status": row["status"],
            "starts_at": row["starts_at"].astimezone(UTC).isoformat(),
            "ends_at": row["ends_at"].astimezone(UTC).isoformat() if row["ends_at"] else None,
            "display_start": row["starts_at"].astimezone(zone).isoformat(),
            "display_end": row["ends_at"].astimezone(zone).isoformat() if row["ends_at"] else None,
        }
        for row in rows[:limit]
    ]
    cursor = (
        {"starts_at": items[-1]["starts_at"], "id": items[-1]["appointment_id"]}
        if len(rows) > limit
        else None
    )
    return {
        "month": month.isoformat()[:7],
        "timezone": zone.key,
        "as_of": now.isoformat(),
        "items": items,
        "next_cursor": cursor,
    }
