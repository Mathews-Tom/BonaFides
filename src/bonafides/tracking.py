# SPDX-License-Identifier: MIT OR Apache-2.0
"""Owner-authorized manual tracking. Nothing here sends or invokes a model.

Child quotas: 100 notes, contacts and attachments; 200 appointments per attempt.
Attachments: 10 MiB each, 100 MiB per attempt, 1 GiB per owner (reference bytes).
Only PDF or UTF-8 literal text is retained, always downloaded as opaque bytes.
Signature/extension rejection is conservative admission, not malware certification.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from time import perf_counter_ns
from typing import Annotated, Literal, cast
from urllib.parse import quote
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import AwareDatetime, Field, field_validator, model_validator
from sqlalchemy import Connection, text
from sqlalchemy.engine import RowMapping
from starlette.concurrency import run_in_threadpool

from bonafides.auth import Owner, StrictModel, require_owner
from bonafides.commands import append_outbox, canonical_json, command
from bonafides.config import Settings
from bonafides.crypto import Keyring
from bonafides.db import Database
from bonafides.problems import Problem
from bonafides.storage import BlobStore

Stage = Literal[
    "preparing", "applied", "screening", "interview", "offer", "accepted", "rejected", "withdrawn"
]
STAGES = (
    "preparing",
    "applied",
    "screening",
    "interview",
    "offer",
    "accepted",
    "rejected",
    "withdrawn",
)
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_APPLICATION_ATTACHMENT_BYTES = 100 * 1024 * 1024
MAX_OWNER_ATTACHMENT_BYTES = 1024 * 1024 * 1024
_CURSOR_PURPOSE = b"bonafides/tracking/keyset/v1"
_SUMMARY = """a.id,a.version,a.attempt_no,o.posting_id,a.opportunity_id,p.title,p.company,
a.current_stage,a.archived_at,a.created_at,a.updated_at"""
_JOINS = """JOIN bonafides.saved_opportunities o ON o.owner_id=a.owner_id AND o.id=a.opportunity_id
JOIN bonafides.job_postings p ON p.owner_id=o.owner_id AND p.id=o.posting_id"""
OwnerDependency = Annotated[Owner, Depends(require_owner)]
MatchHeader = Annotated[str | None, Header()]
KeyHeader = Annotated[str | None, Header()]


def _literal(value: str) -> str:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("invalid Unicode") from exc
    if "\x00" in value:
        raise ValueError("NUL is not permitted")
    return value


class ApplicationCreate(StrictModel):
    posting_id: UUID


class RepeatWrite(StrictModel):
    confirmed: Literal[True]

    @field_validator("confirmed", mode="before")
    @classmethod
    def explicit_confirmation(cls, value: object) -> object:
        if value is not True:
            raise ValueError("explicit confirmation required")
        return value


class ArchiveWrite(StrictModel):
    archived: bool


class StageWrite(StrictModel):
    to_stage: Stage
    effective_at: AwareDatetime
    reason: str = Field(max_length=5000)
    correction_of: UUID | None = None
    retain_follow_up_ids: list[UUID] = Field(default_factory=list, max_length=200)

    @field_validator("reason")
    @classmethod
    def valid_reason(cls, value: str) -> str:
        return _literal(value)

    @model_validator(mode="after")
    def correction_reason(self) -> StageWrite:
        if self.correction_of is not None and not self.reason.strip():
            raise ValueError("corrections require a reason")
        if len(set(self.retain_follow_up_ids)) != len(self.retain_follow_up_ids):
            raise ValueError("retained actions must be distinct")
        return self


class NoteWrite(StrictModel):
    body: str = Field(max_length=50000)

    @field_validator("body")
    @classmethod
    def literal_body(cls, value: str) -> str:
        return _literal(value)


class ContactWrite(StrictModel):
    display_name: str = Field(min_length=1, max_length=500)
    email: str | None = Field(default=None, max_length=320)
    role_label: str | None = Field(default=None, max_length=200)

    @field_validator("display_name", "role_label")
    @classmethod
    def literal_label(cls, value: str | None) -> str | None:
        return _literal(value) if value is not None else None

    @field_validator("email")
    @classmethod
    def address(cls, value: str | None) -> str | None:
        if value is None:
            return None
        _literal(value)
        if any(unicodedata.category(character).startswith("C") for character in value):
            raise ValueError("invalid email")
        if value.count("@") != 1:
            raise ValueError("invalid email")
        local, domain = value.rsplit("@", 1)
        try:
            ascii_domain = domain.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError("invalid email") from exc
        if (
            len(local) > 64
            or re.fullmatch(
                r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*", local
            )
            is None
            or len(ascii_domain) > 253
            or "." not in ascii_domain
            or any(
                re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) is None
                for label in ascii_domain.split(".")
            )
        ):
            raise ValueError("invalid email")
        return value


class ApplicationQuery(StrictModel):
    q: str | None = Field(default=None, max_length=300)
    stage: Stage | None = None
    archived: Literal["active", "archived", "all"] = "active"
    limit: int = Field(default=20, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=2048)

    @field_validator("q")
    @classmethod
    def literal_search(cls, value: str | None) -> str | None:
        return _literal(value) if value else None

    def identity(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"cursor", "limit"})


class PeriodQuery(StrictModel):
    period_start: AwareDatetime | None = None
    period_end: AwareDatetime | None = None

    @model_validator(mode="after")
    def valid_period(self) -> PeriodQuery:
        if (self.period_start is None) != (self.period_end is None):
            raise ValueError("provide both period endpoints")
        if (
            self.period_start is not None
            and self.period_end is not None
            and self.period_start >= self.period_end
        ):
            raise ValueError("period must increase")
        return self


def _zone(value: str) -> ZoneInfo:
    try:
        if not value or len(value) > 200:
            raise ValueError("invalid timezone")
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise Problem(422, "invalid_timezone") from exc


class TimezoneWrite(StrictModel):
    timezone: str = Field(min_length=1, max_length=200)


class AppointmentStatusWrite(StrictModel):
    status: Literal["scheduled", "completed", "cancelled"]


class AppointmentWrite(TimezoneWrite):
    kind: Literal["interview", "follow_up"]
    title: str = Field(min_length=1, max_length=500)
    local_start: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")
    utc_offset_minutes: int | None = Field(default=None, ge=-840, le=840, strict=True)
    local_end: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")
    end_utc_offset_minutes: int | None = Field(default=None, ge=-840, le=840, strict=True)

    @field_validator("title")
    @classmethod
    def literal_title(cls, value: str) -> str:
        return _literal(value)


def resolve_local(value: str, zone: ZoneInfo, offset: int | None) -> datetime:
    try:
        local = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S")
        candidates: dict[datetime, int] = {}
        for fold in (0, 1):
            aware = local.replace(tzinfo=zone, fold=fold)
            instant = aware.astimezone(UTC)
            if instant.astimezone(zone).replace(tzinfo=None) == local:
                delta = aware.utcoffset()
                assert delta is not None
                if delta.total_seconds() % 60:
                    raise Problem(422, "unsupported_timezone_offset")
                candidates[instant] = int(delta.total_seconds() // 60)
    except (ValueError, OverflowError) as exc:
        raise Problem(422, "invalid_local_time") from exc
    if not candidates:
        raise Problem(422, "nonexistent_local_time")
    if offset is not None:
        matches = [
            instant
            for instant, candidate_offset in candidates.items()
            if candidate_offset == offset
        ]
        if len(matches) != 1:
            raise Problem(422, "invalid_utc_offset")
        return matches[0]
    if len(candidates) != 1:
        raise Problem(422, "ambiguous_local_time")
    return next(iter(candidates))


def appointment_interval(body: AppointmentWrite) -> dict[str, object]:
    zone = _zone(body.timezone)
    start = resolve_local(body.local_start, zone, body.utc_offset_minutes)
    if body.kind == "interview" and body.local_end is None:
        raise Problem(422, "interview_end_required")
    if body.local_end is None and body.end_utc_offset_minutes is not None:
        raise Problem(422, "end_offset_without_end")
    end = (
        resolve_local(body.local_end, zone, body.end_utc_offset_minutes)
        if body.local_end is not None
        else None
    )
    duration: int | None = None
    if end is not None:
        seconds = (end - start).total_seconds()
        if not 60 <= seconds <= 1440 * 60 or seconds % 60:
            raise Problem(422, "invalid_appointment_interval")
        duration = int(seconds // 60)
    return {
        "kind": body.kind,
        "title": body.title,
        "starts_at": start,
        "ends_at": end,
        "local_start": start.astimezone(zone).replace(tzinfo=None),
        "local_end": end.astimezone(zone).replace(tzinfo=None) if end is not None else None,
        "timezone": body.timezone,
        "duration_minutes": duration,
    }


def _wire(row: RowMapping) -> dict[str, object]:
    return {
        key: str(value)
        if isinstance(value, UUID)
        else value.isoformat()
        if isinstance(value, datetime)
        else value
        for key, value in row.items()
    }


def _appointment(row: RowMapping) -> dict[str, object]:
    zone = _zone(row["display_timezone"])
    result = _wire(row)
    result["display_start"] = row["starts_at"].astimezone(zone).isoformat()
    result["display_end"] = (
        row["ends_at"].astimezone(zone).isoformat() if row["ends_at"] is not None else None
    )
    return result


_APPOINTMENT_COLUMNS = """id,version,kind,title,starts_at,ends_at,local_start,local_end,timezone,
display_timezone,duration_minutes,status,completed_at,cancelled_at,created_at"""


def _next_action(
    conn: Connection, owner_id: UUID, application_id: UUID
) -> dict[str, object] | None:
    row = (
        conn.execute(
            text(f"""SELECT {_APPOINTMENT_COLUMNS} FROM bonafides.appointments
        WHERE owner_id=:owner AND application_id=:app AND status='scheduled' ORDER BY starts_at,id LIMIT 1"""),
            {"owner": owner_id, "app": application_id},
        )
        .mappings()
        .one_or_none()
    )
    return _appointment(row) if row is not None else None


def get_application(conn: Connection, owner_id: UUID, application_id: UUID) -> dict[str, object]:
    row = (
        conn.execute(
            text(
                f"SELECT {_SUMMARY},a.current_stage_event_id FROM bonafides.applications a {_JOINS} WHERE a.owner_id=:owner AND a.id=:id"
            ),
            {"owner": owner_id, "id": application_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise Problem(404, "application_not_found")
    result = _wire(row)
    params = {"owner": owner_id, "app": application_id}
    result["notes"] = [
        _wire(item)
        for item in conn.execute(
            text(
                "SELECT id,version,body,created_at,updated_at FROM bonafides.application_notes WHERE owner_id=:owner AND application_id=:app ORDER BY created_at,id"
            ),
            params,
        ).mappings()
    ]
    result["contacts"] = [
        _wire(item)
        for item in conn.execute(
            text(
                "SELECT id,version,display_name,email,role_label,created_at FROM bonafides.application_contacts WHERE owner_id=:owner AND application_id=:app ORDER BY created_at,id"
            ),
            params,
        ).mappings()
    ]
    result["attachments"] = [
        _wire(item)
        for item in conn.execute(
            text("""SELECT x.id,x.display_name,b.byte_length,b.sha256,'application/octet-stream' AS media_type,x.created_at
        FROM bonafides.application_attachments x JOIN bonafides.blobs b ON b.owner_id=x.owner_id AND b.id=x.blob_id
        WHERE x.owner_id=:owner AND x.application_id=:app ORDER BY x.created_at,x.id"""),
            params,
        ).mappings()
    ]
    result["appointments"] = [
        _appointment(item)
        for item in conn.execute(
            text(
                f"SELECT {_APPOINTMENT_COLUMNS} FROM bonafides.appointments WHERE owner_id=:owner AND application_id=:app ORDER BY starts_at,id"
            ),
            params,
        ).mappings()
    ]
    result["next_action"] = _next_action(conn, owner_id, application_id)
    return result


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    result = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    if _b64(result) != value:
        raise ValueError("noncanonical encoding")
    return result


def _cursor_key(keyring: Keyring, key_id: str) -> bytes:
    return hmac.digest(keyring.keys[key_id], _CURSOR_PURPOSE, "sha256")


def _window(
    conn: Connection, keyring: Keyring, owner_id: UUID, query: ApplicationQuery
) -> tuple[datetime, datetime | None, UUID | None]:
    if query.cursor is None:
        return conn.execute(text("SELECT clock_timestamp()")).scalar_one(), None, None
    try:
        encoded, signature = query.cursor.split(".")
        payload = _unb64(encoded)
        data = json.loads(payload)
        if not isinstance(data, dict) or set(data) != {
            "v",
            "kid",
            "scope",
            "owner",
            "filters",
            "as_of",
            "created_at",
            "id",
        }:
            raise ValueError("cursor shape")
        if (
            type(data["v"]) is not int
            or data["v"] != 1
            or not all(isinstance(data[key], str) for key in data if key != "v")
        ):
            raise ValueError("cursor types")
        if not hmac.compare_digest(
            hmac.digest(_cursor_key(keyring, data["kid"]), payload, "sha256"), _unb64(signature)
        ):
            raise ValueError("cursor signature")
        filters = hashlib.sha256(canonical_json(query.identity()).encode()).hexdigest()
        if (
            data["owner"] != str(owner_id)
            or data["scope"] != "applications"
            or data["filters"] != filters
        ):
            raise ValueError("cursor context")
        as_of, timestamp = (
            datetime.fromisoformat(data["as_of"]),
            datetime.fromisoformat(data["created_at"]),
        )
        if as_of.tzinfo is None or timestamp.tzinfo is None or timestamp > as_of:
            raise ValueError("cursor timestamp")
        return as_of, timestamp, UUID(data["id"])
    except (ValueError, TypeError, KeyError, binascii.Error, UnicodeError, RecursionError) as exc:
        raise Problem(400, "invalid_cursor") from exc


def list_applications(
    conn: Connection, owner_id: UUID, keyring: Keyring, query: ApplicationQuery
) -> dict[str, object]:
    started = perf_counter_ns()
    as_of, timestamp, identifier = _window(conn, keyring, owner_id, query)
    predicates = ["a.owner_id=:owner", "a.created_at<=:as_of"]
    params: dict[str, object] = {"owner": owner_id, "as_of": as_of, "limit": query.limit + 1}
    if timestamp is not None:
        predicates.append("(a.created_at,a.id)<(:timestamp,:id)")
        params.update(timestamp=timestamp, id=identifier)
    if query.q is not None:
        predicates.append(
            "(strpos(lower(p.title),lower(:q))>0 OR strpos(lower(p.company),lower(:q))>0)"
        )
        params["q"] = query.q
    if query.stage is not None:
        predicates.append("a.current_stage=:stage")
        params["stage"] = query.stage
    if query.archived != "all":
        predicates.append(
            "a.archived_at IS NULL" if query.archived == "active" else "a.archived_at IS NOT NULL"
        )
    rows = (
        conn.execute(
            text(f"""SELECT {_SUMMARY} FROM bonafides.applications a {_JOINS}
        WHERE {" AND ".join(predicates)} ORDER BY a.created_at DESC,a.id DESC LIMIT :limit"""),
            params,
        )
        .mappings()
        .all()
    )
    selected = rows[: query.limit]
    items = []
    for row in selected:
        summary = _wire(row)
        summary["next_action"] = _next_action(conn, owner_id, row["id"])
        items.append(summary)
    cursor = None
    if len(rows) > query.limit:
        last = selected[-1]
        payload = canonical_json(
            {
                "v": 1,
                "kid": keyring.active_key_id,
                "scope": "applications",
                "owner": str(owner_id),
                "filters": hashlib.sha256(canonical_json(query.identity()).encode()).hexdigest(),
                "as_of": as_of.isoformat(),
                "created_at": last["created_at"].isoformat(),
                "id": str(last["id"]),
            }
        ).encode()
        cursor = (
            _b64(payload)
            + "."
            + _b64(hmac.digest(_cursor_key(keyring, keyring.active_key_id), payload, "sha256"))
        )
    return {
        "items": items,
        "next_cursor": cursor,
        "as_of": as_of.isoformat(),
        "query_elapsed_ms": (perf_counter_ns() - started) / 1_000_000,
    }


def counts(conn: Connection, owner_id: UUID, query: PeriodQuery) -> dict[str, object]:
    started = perf_counter_ns()
    now = conn.execute(text("SELECT clock_timestamp()")).scalar_one().astimezone(UTC)
    start = query.period_start or now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = query.period_end or (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    params = {"owner": owner_id, "start": start, "end": end}
    current = dict.fromkeys(STAGES, 0)
    archived = dict.fromkeys(STAGES, 0)
    for row in conn.execute(
        text("""SELECT current_stage,archived_at IS NOT NULL AS archived,count(*) AS n
        FROM bonafides.applications WHERE owner_id=:owner GROUP BY current_stage,archived_at IS NOT NULL"""),
        params,
    ).mappings():
        (archived if row["archived"] else current)[row["current_stage"]] = int(row["n"])
    reached = dict.fromkeys(STAGES, 0)
    for row in conn.execute(
        text("""SELECT e.to_stage,count(DISTINCT e.application_id) AS n
        FROM bonafides.application_stage_events e WHERE e.owner_id=:owner AND e.effective_at>=:start AND e.effective_at<:end
        AND NOT EXISTS(SELECT 1 FROM bonafides.application_stage_events replacement
          WHERE replacement.owner_id=e.owner_id AND replacement.application_id=e.application_id AND replacement.correction_of=e.id)
        GROUP BY e.to_stage"""),
        params,
    ).mappings():
        reached[row["to_stage"]] = int(row["n"])
    created = conn.execute(
        text(
            "SELECT count(*) FROM bonafides.applications WHERE owner_id=:owner AND created_at>=:start AND created_at<:end"
        ),
        params,
    ).scalar_one()
    return {
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "current_by_stage": current,
        "archived_by_stage": archived,
        "created_in_period": int(created),
        "stage_reached_in_period": reached,
        "query_elapsed_ms": (perf_counter_ns() - started) / 1_000_000,
    }


def attention(conn: Connection, owner_id: UUID, limit: int) -> dict[str, object]:
    started = perf_counter_ns()
    as_of = conn.execute(text("SELECT clock_timestamp()")).scalar_one()
    rows = conn.execute(
        text("""SELECT p.title AS application_title,p.company,x.*
        FROM bonafides.appointments x JOIN bonafides.applications a ON a.owner_id=x.owner_id AND a.id=x.application_id
        JOIN bonafides.saved_opportunities o ON o.owner_id=a.owner_id AND o.id=a.opportunity_id
        JOIN bonafides.job_postings p ON p.owner_id=o.owner_id AND p.id=o.posting_id
        WHERE x.owner_id=:owner AND a.archived_at IS NULL AND x.status='scheduled'
        AND (x.starts_at<=:now OR (x.kind='interview' AND x.starts_at<:until)) ORDER BY x.starts_at,x.id LIMIT :limit"""),
        {"owner": owner_id, "now": as_of, "until": as_of + timedelta(days=7), "limit": limit},
    ).mappings()
    items = []
    for row in rows:
        appointment_row = cast(
            RowMapping, {key: row[key] for key in _APPOINTMENT_COLUMNS.replace("\n", "").split(",")}
        )
        items.append(
            {
                "application_id": str(row["application_id"]),
                "title": row["application_title"],
                "company": row["company"],
                "appointment": _appointment(appointment_row),
            }
        )
    return {
        "items": items,
        "as_of": as_of.isoformat(),
        "query_elapsed_ms": (perf_counter_ns() - started) / 1_000_000,
    }


def _initial(conn: Connection, owner_id: UUID, opportunity_id: UUID, attempt_no: int) -> UUID:
    application_id, event_id = uuid4(), uuid4()
    conn.execute(
        text("""INSERT INTO bonafides.applications(owner_id,id,opportunity_id,attempt_no,current_stage_event_id)
        VALUES(:owner,:id,:opportunity,:attempt,:event)"""),
        {
            "owner": owner_id,
            "id": application_id,
            "opportunity": opportunity_id,
            "attempt": attempt_no,
            "event": event_id,
        },
    )
    conn.execute(
        text("""INSERT INTO bonafides.application_stage_events(owner_id,id,application_id,to_stage,effective_at,sequence)
        VALUES(:owner,:id,:app,'preparing',clock_timestamp(),1)"""),
        {"owner": owner_id, "id": event_id, "app": application_id},
    )
    return application_id


def create_application(
    conn: Connection, owner_id: UUID, posting_id: UUID
) -> tuple[int, dict[str, object]]:
    opportunity_id = conn.execute(
        text(
            "SELECT id FROM bonafides.saved_opportunities WHERE owner_id=:owner AND posting_id=:posting AND disposition='saved' FOR UPDATE"
        ),
        {"owner": owner_id, "posting": posting_id},
    ).scalar_one_or_none()
    if opportunity_id is None:
        raise Problem(409, "saved_opportunity_required")
    existing = conn.execute(
        text(
            "SELECT id FROM bonafides.applications WHERE owner_id=:owner AND opportunity_id=:opportunity ORDER BY attempt_no DESC LIMIT 1"
        ),
        {"owner": owner_id, "opportunity": opportunity_id},
    ).scalar_one_or_none()
    created = existing is None
    application_id = _initial(conn, owner_id, opportunity_id, 1) if existing is None else existing
    return (201 if created else 200), {
        **get_application(conn, owner_id, application_id),
        "created": created,
    }


def _bump(conn: Connection, owner_id: UUID, application_id: UUID) -> int:
    return int(
        conn.execute(
            text(
                "UPDATE bonafides.applications SET version=version+1,updated_at=clock_timestamp() WHERE owner_id=:owner AND id=:app RETURNING version"
            ),
            {"owner": owner_id, "app": application_id},
        ).scalar_one()
    )


def change_stage(conn: Connection, owner_id: UUID, application_id: UUID, body: StageWrite) -> None:
    params: dict[str, object] = {"owner": owner_id, "app": application_id}
    app = (
        conn.execute(
            text(
                "SELECT current_stage,current_stage_event_id FROM bonafides.applications WHERE owner_id=:owner AND id=:app"
            ),
            params,
        )
        .mappings()
        .one()
    )
    projection = True
    from_stage = app["current_stage"]
    if body.correction_of is not None:
        target = (
            conn.execute(
                text("""SELECT e.* FROM bonafides.application_stage_events e
            WHERE e.owner_id=:owner AND e.application_id=:app AND e.id=:target
            AND NOT EXISTS(SELECT 1 FROM bonafides.application_stage_events r WHERE r.owner_id=e.owner_id AND r.application_id=e.application_id AND r.correction_of=e.id)"""),
                {**params, "target": body.correction_of},
            )
            .mappings()
            .one_or_none()
        )
        if target is None:
            raise Problem(409, "invalid_correction_target")
        from_stage = target["from_stage"]
        projection = target["id"] == app["current_stage_event_id"]
    elif body.to_stage == app["current_stage"]:
        raise Problem(409, "stage_unchanged")
    closing = projection and body.to_stage in {"rejected", "withdrawn"}
    if body.retain_follow_up_ids and not closing:
        raise Problem(422, "retention_requires_closing_stage")
    for identifier in body.retain_follow_up_ids:
        valid = conn.execute(
            text(
                "SELECT id FROM bonafides.appointments WHERE owner_id=:owner AND application_id=:app AND id=:id AND kind='follow_up' AND status='scheduled'"
            ),
            {**params, "id": identifier},
        ).scalar_one_or_none()
        if valid is None:
            raise Problem(422, "invalid_retained_follow_up")
    sequence = conn.execute(
        text(
            "SELECT coalesce(max(sequence),0)+1 FROM bonafides.application_stage_events WHERE owner_id=:owner AND application_id=:app"
        ),
        params,
    ).scalar_one()
    event_id = uuid4()
    conn.execute(
        text("""INSERT INTO bonafides.application_stage_events
        (owner_id,id,application_id,from_stage,to_stage,effective_at,reason,correction_of,sequence,affects_current_stage)
        VALUES(:owner,:id,:app,:from_stage,:to_stage,:effective,:reason,:correction,:sequence,:projection)"""),
        {
            **params,
            "id": event_id,
            "from_stage": from_stage,
            "to_stage": body.to_stage,
            "effective": body.effective_at,
            "reason": body.reason,
            "correction": body.correction_of,
            "sequence": sequence,
            "projection": projection,
        },
    )
    if projection:
        conn.execute(
            text(
                "UPDATE bonafides.applications SET current_stage=:stage,current_stage_event_id=:event WHERE owner_id=:owner AND id=:app"
            ),
            {**params, "stage": body.to_stage, "event": event_id},
        )
    if closing:
        conn.execute(
            text("""UPDATE bonafides.appointments SET status='cancelled',cancelled_at=clock_timestamp(),version=version+1
            WHERE owner_id=:owner AND application_id=:app AND status='scheduled' AND NOT(id=ANY(CAST(:retained AS uuid[])))"""),
            {**params, "retained": body.retain_follow_up_ids},
        )


def stage_history(
    conn: Connection, owner_id: UUID, application_id: UUID, before: int | None, limit: int
) -> dict[str, object]:
    if (
        conn.execute(
            text("SELECT id FROM bonafides.applications WHERE owner_id=:owner AND id=:app"),
            {"owner": owner_id, "app": application_id},
        ).first()
        is None
    ):
        raise Problem(404, "application_not_found")
    rows = (
        conn.execute(
            text("""SELECT id,from_stage,to_stage,effective_at,created_at AS recorded_at,reason,correction_of,sequence,affects_current_stage
        FROM bonafides.application_stage_events WHERE owner_id=:owner AND application_id=:app
        AND (CAST(:before AS bigint) IS NULL OR sequence<:before) ORDER BY sequence DESC LIMIT :limit"""),
            {"owner": owner_id, "app": application_id, "before": before, "limit": limit + 1},
        )
        .mappings()
        .all()
    )
    selected = rows[:limit]
    return {
        "items": [_wire(row) for row in selected],
        "next_cursor": selected[-1]["sequence"] if len(rows) > limit else None,
    }


def _quota(
    conn: Connection, owner_id: UUID, application_id: UUID, table: str, maximum: int
) -> None:
    # All call sites supply static table names; never request-controlled identifiers.
    count = conn.execute(
        text(
            f"SELECT count(*) FROM bonafides.{table} WHERE owner_id=:owner AND application_id=:app"
        ),
        {"owner": owner_id, "app": application_id},
    ).scalar_one()
    if count >= maximum:
        raise Problem(422, "tracking_quota_exceeded")


def write_note(
    conn: Connection, owner_id: UUID, application_id: UUID, note_id: UUID | None, body: NoteWrite
) -> None:
    params = {"owner": owner_id, "app": application_id, "body": body.body, "id": note_id or uuid4()}
    if note_id is None:
        _quota(conn, owner_id, application_id, "application_notes", 100)
        conn.execute(
            text(
                "INSERT INTO bonafides.application_notes(owner_id,id,application_id,body) VALUES(:owner,:id,:app,:body)"
            ),
            params,
        )
    elif (
        conn.execute(
            text(
                "UPDATE bonafides.application_notes SET body=:body,version=version+1,updated_at=clock_timestamp() WHERE owner_id=:owner AND application_id=:app AND id=:id RETURNING id"
            ),
            params,
        ).first()
        is None
    ):
        raise Problem(404, "note_not_found")


def write_contact(
    conn: Connection,
    owner_id: UUID,
    application_id: UUID,
    contact_id: UUID | None,
    body: ContactWrite,
) -> None:
    params = {
        "owner": owner_id,
        "app": application_id,
        "id": contact_id or uuid4(),
        **body.model_dump(),
    }
    if contact_id is None:
        _quota(conn, owner_id, application_id, "application_contacts", 100)
        conn.execute(
            text(
                "INSERT INTO bonafides.application_contacts(owner_id,id,application_id,display_name,email,role_label) VALUES(:owner,:id,:app,:display_name,:email,:role_label)"
            ),
            params,
        )
    elif (
        conn.execute(
            text(
                "UPDATE bonafides.application_contacts SET display_name=:display_name,email=:email,role_label=:role_label,version=version+1 WHERE owner_id=:owner AND application_id=:app AND id=:id RETURNING id"
            ),
            params,
        ).first()
        is None
    ):
        raise Problem(404, "contact_not_found")


def _child_appointment(
    conn: Connection, owner_id: UUID, application_id: UUID, identifier: UUID
) -> RowMapping:
    row = (
        conn.execute(
            text(
                "SELECT * FROM bonafides.appointments WHERE owner_id=:owner AND application_id=:app AND id=:id"
            ),
            {"owner": owner_id, "app": application_id, "id": identifier},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise Problem(404, "appointment_not_found")
    return row


def _closed(conn: Connection, owner_id: UUID, application_id: UUID) -> bool:
    stage = conn.execute(
        text("SELECT current_stage FROM bonafides.applications WHERE owner_id=:owner AND id=:app"),
        {"owner": owner_id, "app": application_id},
    ).scalar_one()
    return stage in {"rejected", "withdrawn"}


def write_appointment(
    conn: Connection,
    owner_id: UUID,
    application_id: UUID,
    identifier: UUID | None,
    body: AppointmentWrite,
) -> None:
    values = appointment_interval(body)
    params = {"owner": owner_id, "app": application_id, "id": identifier or uuid4(), **values}
    if identifier is None:
        if _closed(conn, owner_id, application_id):
            raise Problem(409, "reopen_application_required")
        _quota(conn, owner_id, application_id, "appointments", 200)
        conn.execute(
            text("""INSERT INTO bonafides.appointments
            (owner_id,id,application_id,kind,title,starts_at,ends_at,local_start,local_end,timezone,display_timezone,duration_minutes)
            VALUES(:owner,:id,:app,:kind,:title,:starts_at,:ends_at,:local_start,:local_end,:timezone,:timezone,:duration_minutes)"""),
            params,
        )
    else:
        existing = _child_appointment(conn, owner_id, application_id, identifier)
        if existing["status"] == "scheduled" and _closed(conn, owner_id, application_id):
            raise Problem(409, "reopen_application_required")
        conn.execute(
            text("""UPDATE bonafides.appointments SET kind=:kind,title=:title,starts_at=:starts_at,ends_at=:ends_at,
            local_start=:local_start,local_end=:local_end,timezone=:timezone,duration_minutes=:duration_minutes,version=version+1
            WHERE owner_id=:owner AND application_id=:app AND id=:id"""),
            params,
        )


def appointment_status(
    conn: Connection,
    owner_id: UUID,
    application_id: UUID,
    identifier: UUID,
    body: AppointmentStatusWrite,
) -> None:
    existing = _child_appointment(conn, owner_id, application_id, identifier)
    if (
        body.status == "scheduled"
        and existing["status"] != "scheduled"
        and _closed(conn, owner_id, application_id)
    ):
        raise Problem(409, "reopen_application_required")
    conn.execute(
        text("""UPDATE bonafides.appointments SET status=:status,version=version+1,
        completed_at=CASE WHEN :status='completed' THEN coalesce(completed_at,clock_timestamp()) ELSE NULL END,
        cancelled_at=CASE WHEN :status='cancelled' THEN coalesce(cancelled_at,clock_timestamp()) ELSE NULL END
        WHERE owner_id=:owner AND application_id=:app AND id=:id"""),
        {"owner": owner_id, "app": application_id, "id": identifier, "status": body.status},
    )


_DENIED_EXTENSIONS = frozenset(
    (
        "exe",
        "dll",
        "com",
        "scr",
        "cpl",
        "pif",
        "msi",
        "msp",
        "msix",
        "msixbundle",
        "bat",
        "cmd",
        "ps1",
        "sh",
        "bash",
        "zsh",
        "fish",
        "py",
        "pyc",
        "pyo",
        "pl",
        "pm",
        "rb",
        "php",
        "phtml",
        "phar",
        "cgi",
        "hta",
        "reg",
        "gadget",
        "vb",
        "vba",
        "js",
        "mjs",
        "cjs",
        "vbs",
        "vbe",
        "wsf",
        "wsh",
        "jse",
        "jar",
        "class",
        "apk",
        "app",
        "application",
        "appref-ms",
        "crx",
        "xpi",
        "dmg",
        "pkg",
        "deb",
        "rpm",
        "so",
        "dylib",
        "bin",
        "elf",
        "macho",
        "o",
        "a",
        "ar",
        "wasm",
        "zip",
        "zipx",
        "7z",
        "rar",
        "tar",
        "gz",
        "tgz",
        "bz2",
        "tbz",
        "xz",
        "txz",
        "zst",
        "z",
        "lz",
        "lzma",
        "lzo",
        "lz4",
        "br",
        "ace",
        "arj",
        "cpio",
        "lzh",
        "iso",
        "cab",
        "vhd",
        "vhdx",
        "vmdk",
        "docm",
        "xlsm",
        "pptm",
        "docx",
        "xlsx",
        "pptx",
        "epub",
        "odt",
        "ods",
        "odp",
        "lnk",
        "desktop",
        "command",
        "workflow",
        "scpt",
        "applescript",
    )
)
_DENIED_SIGNATURES = (
    b"MZ",
    b"\x7fELF",
    b"#!",
    b"PK\x03\x04",
    b"PK\x05\x06",
    b"PK\x07\x08",
    b"Rar!\x1a\x07",
    b"7z\xbc\xaf\x27\x1c",
    b"\x1f\x8b",
    b"\x1f\x9d",
    b"BZh",
    b"\xfd7zXZ\x00",
    b"\x28\xb5\x2f\xfd",
    b"\x04\x22\x4d\x18",
    b"MSCF",
    b"\x00asm",
    b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca",
    b"\xca\xfe\xba\xbf",
    b"\xbf\xba\xfe\xca",
    b"\xfe\xed\xfa\xce",
    b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
    b"\xcf\xfa\xed\xfe",
    b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
    b"!<arch>\n",
    b"070701",
    b"070702",
    b"070707",
)


def validate_attachment(display_name: str, data: bytes) -> None:
    try:
        _literal(display_name)
    except ValueError as exc:
        raise Problem(422, "invalid_attachment_filename") from exc
    if (
        not 1 <= len(display_name) <= 255
        or len(display_name.encode("utf-8")) > 1024
        or display_name != display_name.strip()
        or display_name in {".", ".."}
        or any(
            character in "/\\:\u2028\u2029" or unicodedata.category(character).startswith("C")
            for character in display_name
        )
        or display_name.endswith(".")
    ):
        raise Problem(422, "invalid_attachment_filename")
    if any(part.lower() in _DENIED_EXTENSIONS for part in display_name.split(".")[1:]):
        raise Problem(415, "attachment_type_rejected")
    if not data:
        raise Problem(422, "empty_attachment")
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise Problem(413, "attachment_too_large")
    if (
        data.startswith(_DENIED_SIGNATURES)
        or data[257:262] == b"ustar"
        or data[32769:32774] == b"CD001"
    ):
        raise Problem(415, "attachment_type_rejected")
    if not data.startswith(b"%PDF-"):
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise Problem(415, "unsupported_attachment_content") from exc
        if "\x00" in content or any(
            unicodedata.category(character) == "Cc" and character not in "\t\n\r"
            for character in content
        ):
            raise Problem(415, "unsupported_attachment_content")


def add_attachment(
    conn: Connection,
    settings: Settings,
    owner_id: UUID,
    application_id: UUID,
    display_name: str,
    data: bytes,
) -> UUID:
    _quota(conn, owner_id, application_id, "application_attachments", 100)
    sizes = (
        conn.execute(
            text("""SELECT coalesce(sum(b.byte_length),0) AS owner_bytes,
        coalesce(sum(b.byte_length) FILTER(WHERE x.application_id=:app),0) AS app_bytes
        FROM bonafides.application_attachments x JOIN bonafides.blobs b ON b.owner_id=x.owner_id AND b.id=x.blob_id WHERE x.owner_id=:owner"""),
            {"owner": owner_id, "app": application_id},
        )
        .mappings()
        .one()
    )
    if (
        sizes["owner_bytes"] + len(data) > MAX_OWNER_ATTACHMENT_BYTES
        or sizes["app_bytes"] + len(data) > MAX_APPLICATION_ATTACHMENT_BYTES
    ):
        raise Problem(422, "attachment_quota_exceeded")
    try:
        blob = BlobStore(settings.blob_root).put_bytes(owner_id, data)
    except (OSError, ValueError) as exc:
        raise Problem(503, "attachment_storage_unavailable") from exc
    conn.execute(
        text("""INSERT INTO bonafides.blobs(owner_id,id,sha256,byte_length,media_type,storage_key)
        VALUES(:owner,:id,:sha,:size,'application/octet-stream',:key) ON CONFLICT(owner_id,sha256) DO NOTHING"""),
        {
            "owner": owner_id,
            "id": uuid4(),
            "sha": blob.sha256,
            "size": blob.byte_length,
            "key": blob.storage_key,
        },
    )
    existing = (
        conn.execute(
            text(
                "SELECT id,byte_length,storage_key,state FROM bonafides.blobs WHERE owner_id=:owner AND sha256=:sha"
            ),
            {"owner": owner_id, "sha": blob.sha256},
        )
        .mappings()
        .one()
    )
    if (
        existing["state"] != "ready"
        or existing["byte_length"] != blob.byte_length
        or existing["storage_key"] != blob.storage_key
    ):
        raise Problem(409, "attachment_blob_unavailable")
    identifier = uuid4()
    conn.execute(
        text(
            "INSERT INTO bonafides.application_attachments(owner_id,id,application_id,blob_id,display_name) VALUES(:owner,:id,:app,:blob,:name)"
        ),
        {
            "owner": owner_id,
            "id": identifier,
            "app": application_id,
            "blob": existing["id"],
            "name": display_name,
        },
    )
    return identifier


def _response(
    result: dict[str, object], status: int = 200, headers: dict[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(
        result, status_code=status, headers={"ETag": f'"{result["version"]}"', **(headers or {})}
    )


def _mutate(
    db: Database,
    owner: Owner,
    application_id: UUID,
    method: str,
    route: str,
    body: StrictModel,
    if_match: str | None,
    idempotency_key: str | None,
    effect: Callable[[Connection], None],
    child_path: dict[str, str] | None = None,
    *,
    stage_timing: bool = False,
) -> JSONResponse:
    started = perf_counter_ns()

    def apply(conn: Connection) -> tuple[int, dict[str, object]]:
        effect(conn)
        version = _bump(conn, owner.id, application_id)
        append_outbox(
            conn,
            owner.id,
            "application.tracking_changed",
            "application",
            application_id,
            {"application_id": str(application_id)},
            version,
        )
        return 200, get_application(conn, owner.id, application_id)

    with db.transaction() as conn:
        status, result = command(
            conn,
            owner.id,
            method,
            route,
            {"application_id": str(application_id), **(child_path or {})},
            body.model_dump(mode="json"),
            if_match,
            idempotency_key,
            "application",
            application_id,
            apply,
        )
    headers = (
        {"Server-Timing": f"stage;dur={(perf_counter_ns() - started) / 1_000_000:.3f}"}
        if stage_timing
        else None
    )
    return _response(result, status, headers)


def build_router(db: Database, settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.post("/applications")
    def create(
        body: ApplicationCreate,
        owner: OwnerDependency,
        if_match: MatchHeader = None,
        idempotency_key: KeyHeader = None,
    ) -> JSONResponse:
        with db.transaction() as conn:
            status, result = command(
                conn,
                owner.id,
                "POST",
                "/applications",
                {},
                body.model_dump(mode="json"),
                if_match,
                idempotency_key,
                "application",
                uuid4(),
                lambda active: create_application(active, owner.id, body.posting_id),
                creation_kind="application",
            )
        return _response(result, status)

    @router.get("/applications")
    def listing(
        owner: OwnerDependency, query: Annotated[ApplicationQuery, Query()]
    ) -> dict[str, object]:
        with db.transaction() as conn:
            return list_applications(conn, owner.id, settings.keyring, query)

    @router.get("/applications/counts")
    def application_counts(
        owner: OwnerDependency, query: Annotated[PeriodQuery, Query()]
    ) -> dict[str, object]:
        with db.transaction() as conn:
            return counts(conn, owner.id, query)

    @router.get("/applications/attention")
    def application_attention(
        owner: OwnerDependency, limit: Annotated[int, Query(ge=1, le=100)] = 20
    ) -> dict[str, object]:
        with db.transaction() as conn:
            return attention(conn, owner.id, limit)

    @router.get("/applications/{application_id}")
    def detail(application_id: UUID, owner: OwnerDependency) -> JSONResponse:
        with db.transaction() as conn:
            return _response(get_application(conn, owner.id, application_id))

    @router.post("/applications/{application_id}/repeat")
    def repeat(
        application_id: UUID,
        body: RepeatWrite,
        owner: OwnerDependency,
        if_match: MatchHeader = None,
        idempotency_key: KeyHeader = None,
    ) -> JSONResponse:
        def effect(conn: Connection) -> tuple[int, dict[str, object]]:
            app = (
                conn.execute(
                    text(
                        "SELECT opportunity_id,attempt_no FROM bonafides.applications WHERE owner_id=:owner AND id=:id"
                    ),
                    {"owner": owner.id, "id": application_id},
                )
                .mappings()
                .one()
            )
            latest = conn.execute(
                text(
                    "SELECT max(attempt_no) FROM bonafides.applications WHERE owner_id=:owner AND opportunity_id=:opportunity"
                ),
                {"owner": owner.id, "opportunity": app["opportunity_id"]},
            ).scalar_one()
            if latest != app["attempt_no"]:
                raise Problem(409, "newer_attempt_exists")
            identifier = _initial(conn, owner.id, app["opportunity_id"], int(latest) + 1)
            _bump(conn, owner.id, application_id)
            return 201, {**get_application(conn, owner.id, identifier), "created": True}

        with db.transaction() as conn:
            status, result = command(
                conn,
                owner.id,
                "POST",
                "/applications/{application_id}/repeat",
                {"application_id": str(application_id)},
                body.model_dump(mode="json"),
                if_match,
                idempotency_key,
                "application",
                application_id,
                effect,
            )
        return _response(result, status)

    @router.post("/applications/{application_id}/archive")
    def archive(
        application_id: UUID,
        body: ArchiveWrite,
        owner: OwnerDependency,
        if_match: MatchHeader = None,
        idempotency_key: KeyHeader = None,
    ) -> JSONResponse:
        def effect(conn: Connection) -> None:
            conn.execute(
                text(
                    "UPDATE bonafides.applications SET archived_at=CASE WHEN :archived THEN coalesce(archived_at,clock_timestamp()) ELSE NULL END WHERE owner_id=:owner AND id=:id"
                ),
                {"owner": owner.id, "id": application_id, "archived": body.archived},
            )

        return _mutate(
            db,
            owner,
            application_id,
            "POST",
            "/applications/{application_id}/archive",
            body,
            if_match,
            idempotency_key,
            effect,
        )

    @router.post("/applications/{application_id}/stage")
    def stage(
        application_id: UUID,
        body: StageWrite,
        owner: OwnerDependency,
        if_match: MatchHeader = None,
        idempotency_key: KeyHeader = None,
    ) -> JSONResponse:
        return _mutate(
            db,
            owner,
            application_id,
            "POST",
            "/applications/{application_id}/stage",
            body,
            if_match,
            idempotency_key,
            lambda conn: change_stage(conn, owner.id, application_id, body),
            stage_timing=True,
        )

    @router.get("/applications/{application_id}/stage-events")
    def history(
        application_id: UUID,
        owner: OwnerDependency,
        before: Annotated[int | None, Query(ge=1)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> dict[str, object]:
        with db.transaction() as conn:
            return stage_history(conn, owner.id, application_id, before, limit)

    @router.post("/applications/{application_id}/notes")
    def note_create(
        application_id: UUID,
        body: NoteWrite,
        owner: OwnerDependency,
        if_match: MatchHeader = None,
        idempotency_key: KeyHeader = None,
    ) -> JSONResponse:
        return _mutate(
            db,
            owner,
            application_id,
            "POST",
            "/applications/{application_id}/notes",
            body,
            if_match,
            idempotency_key,
            lambda conn: write_note(conn, owner.id, application_id, None, body),
        )

    @router.put("/applications/{application_id}/notes/{note_id}")
    def note_edit(
        application_id: UUID,
        note_id: UUID,
        body: NoteWrite,
        owner: OwnerDependency,
        if_match: MatchHeader = None,
        idempotency_key: KeyHeader = None,
    ) -> JSONResponse:
        return _mutate(
            db,
            owner,
            application_id,
            "PUT",
            "/applications/{application_id}/notes/{note_id}",
            body,
            if_match,
            idempotency_key,
            lambda conn: write_note(conn, owner.id, application_id, note_id, body),
            {"note_id": str(note_id)},
        )

    @router.post("/applications/{application_id}/contacts")
    def contact_create(
        application_id: UUID,
        body: ContactWrite,
        owner: OwnerDependency,
        if_match: MatchHeader = None,
        idempotency_key: KeyHeader = None,
    ) -> JSONResponse:
        return _mutate(
            db,
            owner,
            application_id,
            "POST",
            "/applications/{application_id}/contacts",
            body,
            if_match,
            idempotency_key,
            lambda conn: write_contact(conn, owner.id, application_id, None, body),
        )

    @router.put("/applications/{application_id}/contacts/{contact_id}")
    def contact_edit(
        application_id: UUID,
        contact_id: UUID,
        body: ContactWrite,
        owner: OwnerDependency,
        if_match: MatchHeader = None,
        idempotency_key: KeyHeader = None,
    ) -> JSONResponse:
        return _mutate(
            db,
            owner,
            application_id,
            "PUT",
            "/applications/{application_id}/contacts/{contact_id}",
            body,
            if_match,
            idempotency_key,
            lambda conn: write_contact(conn, owner.id, application_id, contact_id, body),
            {"contact_id": str(contact_id)},
        )

    @router.post("/applications/{application_id}/appointments")
    def appointment_create(
        application_id: UUID,
        body: AppointmentWrite,
        owner: OwnerDependency,
        if_match: MatchHeader = None,
        idempotency_key: KeyHeader = None,
    ) -> JSONResponse:
        return _mutate(
            db,
            owner,
            application_id,
            "POST",
            "/applications/{application_id}/appointments",
            body,
            if_match,
            idempotency_key,
            lambda conn: write_appointment(conn, owner.id, application_id, None, body),
        )

    @router.put("/applications/{application_id}/appointments/{appointment_id}")
    def appointment_edit(
        application_id: UUID,
        appointment_id: UUID,
        body: AppointmentWrite,
        owner: OwnerDependency,
        if_match: MatchHeader = None,
        idempotency_key: KeyHeader = None,
    ) -> JSONResponse:
        return _mutate(
            db,
            owner,
            application_id,
            "PUT",
            "/applications/{application_id}/appointments/{appointment_id}",
            body,
            if_match,
            idempotency_key,
            lambda conn: write_appointment(conn, owner.id, application_id, appointment_id, body),
            {"appointment_id": str(appointment_id)},
        )

    @router.post("/applications/{application_id}/appointments/{appointment_id}/timezone")
    def display_timezone(
        application_id: UUID,
        appointment_id: UUID,
        body: TimezoneWrite,
        owner: OwnerDependency,
        if_match: MatchHeader = None,
        idempotency_key: KeyHeader = None,
    ) -> JSONResponse:
        def effect(conn: Connection) -> None:
            _zone(body.timezone)
            _child_appointment(conn, owner.id, application_id, appointment_id)
            conn.execute(
                text(
                    "UPDATE bonafides.appointments SET display_timezone=:zone,version=version+1 WHERE owner_id=:owner AND application_id=:app AND id=:id"
                ),
                {
                    "owner": owner.id,
                    "app": application_id,
                    "id": appointment_id,
                    "zone": body.timezone,
                },
            )

        return _mutate(
            db,
            owner,
            application_id,
            "POST",
            "/applications/{application_id}/appointments/{appointment_id}/timezone",
            body,
            if_match,
            idempotency_key,
            effect,
            {"appointment_id": str(appointment_id)},
        )

    @router.post("/applications/{application_id}/appointments/{appointment_id}/status")
    def status_write(
        application_id: UUID,
        appointment_id: UUID,
        body: AppointmentStatusWrite,
        owner: OwnerDependency,
        if_match: MatchHeader = None,
        idempotency_key: KeyHeader = None,
    ) -> JSONResponse:
        return _mutate(
            db,
            owner,
            application_id,
            "POST",
            "/applications/{application_id}/appointments/{appointment_id}/status",
            body,
            if_match,
            idempotency_key,
            lambda conn: appointment_status(conn, owner.id, application_id, appointment_id, body),
            {"appointment_id": str(appointment_id)},
        )

    def upload(
        owner: Owner,
        application_id: UUID,
        display_name: str,
        data: bytes,
        if_match: str | None,
        idempotency_key: str | None,
    ) -> JSONResponse:
        validate_attachment(display_name, data)

        def effect(conn: Connection) -> tuple[int, dict[str, object]]:
            identifier = add_attachment(
                conn, settings, owner.id, application_id, display_name, data
            )
            version = _bump(conn, owner.id, application_id)
            append_outbox(
                conn,
                owner.id,
                "application.attachment_added",
                "application",
                application_id,
                {"attachment_id": str(identifier)},
                version,
            )
            return 200, {
                **get_application(conn, owner.id, application_id),
                "attachment_id": str(identifier),
            }

        with db.transaction() as conn:
            status, result = command(
                conn,
                owner.id,
                "POST",
                "/applications/{application_id}/attachments",
                {"application_id": str(application_id)},
                {
                    "display_name": display_name,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "byte_length": len(data),
                },
                if_match,
                idempotency_key,
                "application",
                application_id,
                effect,
            )
        return _response(result, status)

    @router.post("/applications/{application_id}/attachments")
    async def attachment_upload(
        application_id: UUID,
        request: Request,
        owner: OwnerDependency,
        display_name: Annotated[str, Query(max_length=255)],
        if_match: MatchHeader = None,
        idempotency_key: KeyHeader = None,
    ) -> JSONResponse:
        if request.headers.get("content-type", "").strip().lower() != "application/octet-stream":
            raise Problem(415, "unsupported_attachment_media")
        data = await request.body()
        return await run_in_threadpool(
            upload, owner, application_id, display_name, data, if_match, idempotency_key
        )

    @router.get("/applications/{application_id}/attachments/{attachment_id}/download")
    def attachment_download(
        application_id: UUID, attachment_id: UUID, owner: OwnerDependency
    ) -> Response:
        with db.transaction() as conn:
            row = (
                conn.execute(
                    text("""SELECT x.display_name,b.sha256,b.byte_length,b.storage_key FROM bonafides.application_attachments x
                JOIN bonafides.applications a ON a.owner_id=x.owner_id AND a.id=x.application_id
                JOIN bonafides.blobs b ON b.owner_id=x.owner_id AND b.id=x.blob_id
                WHERE x.owner_id=:owner AND x.application_id=:app AND x.id=:id AND b.state='ready'"""),
                    {"owner": owner.id, "app": application_id, "id": attachment_id},
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise Problem(404, "attachment_not_found")
        try:
            data = BlobStore(settings.blob_root).read(
                owner.id, row["storage_key"], row["sha256"], row["byte_length"]
            )
        except (OSError, ValueError) as exc:
            raise Problem(410, "attachment_gone") from exc
        return Response(
            data,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": "attachment; filename=\"attachment.bin\"; filename*=UTF-8''"
                + quote(row["display_name"], safe=""),
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "no-store",
            },
        )

    return router
