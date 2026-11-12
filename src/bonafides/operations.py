# SPDX-License-Identifier: MIT OR Apache-2.0
"""Installation-local release preferences; never portable career data."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, cast
from uuid import uuid4

from fastapi import APIRouter, Depends
from pydantic import Field, StrictBool, StrictInt
from sqlalchemy import Connection, text

from bonafides import __version__
from bonafides.auth import Owner, StrictModel, require_owner, require_readonly_owner
from bonafides.commands import lock_installation
from bonafides.db import SCHEMA_VERSION, Database
from bonafides.greenhouse import SourceFailure, _Budget
from bonafides.problems import Problem
from bonafides.release_updates import TIMEOUT, ReleaseClient, version_tuple


class Preference(StrictModel):
    enabled: StrictBool
    version: StrictInt = Field(ge=1)


class Check(StrictModel):
    automatic: StrictBool


class Dismiss(StrictModel):
    version: str = Field(min_length=1, max_length=32)


def _row(conn: Connection, *, lock: bool = False) -> Mapping[str, object]:
    return cast(
        Mapping[str, object],
        conn.execute(
            text(
                "SELECT *,lease_until>clock_timestamp() AS checking FROM bonafides.installation_updates "
                "WHERE singleton" + (" FOR UPDATE" if lock else "")
            )
        )
        .mappings()
        .one(),
    )


def _state(row: Mapping[str, object]) -> dict[str, object]:
    latest = row["latest_version"]
    checked = cast(datetime | None, row["last_checked_at"])
    newer = isinstance(latest, str) and version_tuple(latest) > version_tuple(__version__)
    expired = bool(row["enabled"] and row["lease_id"] is not None and not row["checking"])
    outcome = row["outcome"]
    if outcome in ("newer", "current") and latest is not None:
        outcome = "newer" if newer else "current"
    return {
        "enabled": row["enabled"],
        "version": row["version"],
        "last_checked_at": checked.isoformat() if checked else None,
        "outcome": "checking"
        if row["enabled"] and row["checking"]
        else "unknown"
        if expired
        else outcome,
        "latest_version": latest,
        "latest_url": row["latest_url"],
        "dismissed_version": row["dismissed_version"],
        "notice": bool(row["enabled"] and newer and latest != row["dismissed_version"]),
        "error_code": "source_timeout" if expired else row["error_code"],
    }


def check_updates(db: Database, client: ReleaseClient, automatic: bool) -> dict[str, object]:
    lease = uuid4()
    budget = _Budget(None, seconds=TIMEOUT)
    with db.transaction() as conn:
        lock_installation(conn)
        row = _row(conn, lock=True)
        if not row["enabled"]:
            raise Problem(409, "updates_disabled")
        if row["checking"]:
            raise Problem(409, "updates_check_in_progress")
        today = conn.execute(
            text("SELECT (clock_timestamp() AT TIME ZONE 'UTC')::date")
        ).scalar_one()
        if automatic and row["automatic_day"] == today:
            return _state(row)
        revision = row["version"]
        conn.execute(
            text("""UPDATE bonafides.installation_updates SET
            lease_id=:lease,lease_until=clock_timestamp()+interval '10 seconds',
            last_checked_at=clock_timestamp(),automatic_day=CASE WHEN :automatic THEN :today
            ELSE automatic_day END WHERE singleton"""),
            {"lease": lease, "automatic": automatic, "today": today},
        )
    # Reservation commits before DNS/socket I/O. No connection/owner-settings lock is held.
    try:
        release = client.read(budget=budget)
        outcome = (
            "no_release"
            if release is None
            else (
                "newer"
                if version_tuple(release.version) > version_tuple(__version__)
                else "current"
            )
        )
        error_code = None
    except SourceFailure as error:
        release, outcome, error_code = None, "unknown", error.code
    with db.transaction() as conn:
        lock_installation(conn)
        row = _row(conn, lock=True)
        superseded = (
            not row["enabled"]
            or row["version"] != revision
            or row["lease_id"] != lease
            or not row["checking"]
        )
        if superseded:
            # Retain an invalidated lease until its I/O actually finishes, preventing
            # disable/re-enable from admitting a second overlapping request.
            if row["lease_id"] == lease:
                conn.execute(
                    text("""UPDATE bonafides.installation_updates SET
                    lease_id=NULL,lease_until=NULL,outcome='unknown',
                    error_code=CASE WHEN enabled AND version=:revision THEN 'source_timeout'
                    ELSE error_code END WHERE singleton"""),
                    {"revision": revision},
                )
        else:
            conn.execute(
                text("""UPDATE bonafides.installation_updates SET
                lease_id=NULL,lease_until=NULL,outcome=:outcome,error_code=:error,
                latest_version=CASE WHEN :failed THEN latest_version ELSE :latest END,
                latest_url=CASE WHEN :failed THEN latest_url ELSE :url END WHERE singleton"""),
                {
                    "outcome": outcome,
                    "error": error_code,
                    "failed": error_code is not None,
                    "latest": release.version if release else None,
                    "url": release.url if release else None,
                },
            )
        state = _state(_row(conn))
    if superseded:
        raise Problem(409, "updates_check_superseded")
    return state


def build_router(db: Database) -> APIRouter:
    router = APIRouter(prefix="/api/v1/operations")
    client = ReleaseClient(relay=True)

    @router.get("")
    def health(owner: Annotated[Owner, Depends(require_readonly_owner)]) -> dict[str, object]:
        if not db.ready():
            raise Problem(503, "database_unavailable")
        with db.transaction() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            updates = _state(_row(conn))
        return {
            "version": __version__,
            "schema_version": SCHEMA_VERSION,
            "database": "ready",
            "updates": updates,
        }

    @router.put("/updates")
    def configure(
        body: Preference, owner: Annotated[Owner, Depends(require_owner)]
    ) -> dict[str, object]:
        with db.transaction() as conn:
            lock_installation(conn)
            row = _row(conn, lock=True)
            if row["version"] != body.version:
                raise Problem(409, "updates_version_conflict")
            conn.execute(
                text("""UPDATE bonafides.installation_updates SET enabled=:enabled,
                outcome=CASE WHEN lease_id IS NOT NULL THEN 'unknown' ELSE outcome END,
                error_code=CASE WHEN lease_id IS NOT NULL THEN 'source_cancelled' ELSE error_code END,
                version=version+1 WHERE singleton"""),
                {"enabled": body.enabled},
            )
            return _state(_row(conn))

    @router.post("/updates/check")
    def check(body: Check, owner: Annotated[Owner, Depends(require_owner)]) -> dict[str, object]:
        return check_updates(db, client, body.automatic)

    @router.post("/updates/dismiss")
    def dismiss(
        body: Dismiss, owner: Annotated[Owner, Depends(require_owner)]
    ) -> dict[str, object]:
        with db.transaction() as conn:
            lock_installation(conn)
            row = _row(conn, lock=True)
            if body.version != row["latest_version"]:
                raise Problem(409, "updates_version_conflict")
            conn.execute(
                text(
                    "UPDATE bonafides.installation_updates SET dismissed_version=:version WHERE singleton"
                ),
                {"version": body.version},
            )
            return _state(_row(conn))

    return router
