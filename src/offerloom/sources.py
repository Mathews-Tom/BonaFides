# SPDX-License-Identifier: MIT OR Apache-2.0
"""Manual postings and owner-reviewed, policy-pinned Greenhouse board refreshes."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
from datetime import datetime
from threading import Event
from typing import Annotated, Literal, Protocol, cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import Field, field_validator, model_validator
from sqlalchemy import Connection, text
from sqlalchemy.engine import RowMapping

from offerloom import catalog
from offerloom.auth import Owner, StrictModel, require_owner
from offerloom.commands import append_outbox, canonical_json, command, lock_installation
from offerloom.config import Settings
from offerloom.db import Database
from offerloom.greenhouse import (
    BoardRead,
    SourceFailure,
    SourceGatewayClient,
    safe_reference_url,
    validate_board_token,
)
from offerloom.problems import Problem, problem_response
from offerloom.workflows import Claim, assert_fence, cancel_workflow, enqueue, fail, publish

ALLOWED_HOST = "boards-api.greenhouse.io"
MAX_SOURCES = 100
_TRANSIENT_ERRORS = {
    "source_dns_failed",
    "source_timeout",
    "source_network_failed",
    "source_http_error",
    "source_rate_limited",
    "source_gateway_busy",
    "source_gateway_unavailable",
}


class SourceClient(Protocol):
    def read(self, board_token: str, interrupted: Event | None = None) -> BoardRead: ...


def _literal(value: str) -> str:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("invalid Unicode text") from exc
    if "\x00" in value:
        raise ValueError("NUL is not permitted")
    return value


class ManualPosting(StrictModel):
    title: str = Field(strict=True, min_length=1, max_length=500)
    company: str = Field(strict=True, min_length=1, max_length=500)
    location_text: str = Field(strict=True, default="", max_length=2000)
    description_text: str = Field(strict=True, min_length=1, max_length=200000)
    canonical_url: str | None = Field(default=None, max_length=2048, strict=True)

    @field_validator("title", "company", "description_text")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not _literal(value).strip():
            raise ValueError("text must not be blank")
        return value

    @field_validator("location_text")
    @classmethod
    def literal_location(cls, value: str) -> str:
        return _literal(value)

    @field_validator("canonical_url")
    @classmethod
    def reference(cls, value: str | None) -> str | None:
        try:
            result = safe_reference_url(value)
            if value and result is None:
                raise ValueError("invalid reference URL")
            return result
        except (SourceFailure, ValueError) as exc:
            raise ValueError("invalid reference URL") from exc


class SourceWrite(StrictModel):
    source_kind: Literal["greenhouse"] = "greenhouse"
    board_token: str = Field(strict=True, min_length=1, max_length=120)
    company: str = Field(strict=True, min_length=1, max_length=500)
    terms_url: str | None = Field(default=None, strict=True, max_length=2048)
    terms_revision: str | None = Field(default=None, strict=True, max_length=500)
    terms_review_acknowledged: bool = Field(default=False, strict=True)
    enabled: bool = Field(default=False, strict=True)
    request_limit_per_minute: int = Field(default=10, strict=True, ge=1, le=60)

    @field_validator("board_token")
    @classmethod
    def token(cls, value: str) -> str:
        try:
            return validate_board_token(value)
        except (SourceFailure, ValueError) as exc:
            raise ValueError("invalid board token") from exc

    @field_validator("company")
    @classmethod
    def employer(cls, value: str) -> str:
        if not _literal(value).strip():
            raise ValueError("company must not be blank")
        return value

    @field_validator("terms_revision")
    @classmethod
    def revision(cls, value: str | None) -> str | None:
        if value is not None and not _literal(value).strip():
            raise ValueError("terms revision must not be blank")
        return value

    @field_validator("terms_url")
    @classmethod
    def terms_reference(cls, value: str | None) -> str | None:
        try:
            result = safe_reference_url(value)
            if value and result is None:
                raise ValueError("invalid terms URL")
            return result
        except (SourceFailure, ValueError) as exc:
            raise ValueError("invalid terms URL") from exc

    @model_validator(mode="after")
    def reviewed(self) -> SourceWrite:
        if self.terms_review_acknowledged and (
            self.terms_url is None or self.terms_revision is None
        ):
            raise ValueError("board review requires terms URL and revision")
        if self.enabled and not self.terms_review_acknowledged:
            raise ValueError("enabling requires explicit board review acknowledgement")
        return self


class RefreshRequest(StrictModel):
    pass


class SourceRateLimited(Problem):
    def __init__(self, seconds: int) -> None:
        super().__init__(429, "source_rate_limited")
        self.retry_after_seconds = max(1, seconds)


def _wire(row: RowMapping | dict[str, object]) -> dict[str, object]:
    return {
        key: str(value)
        if isinstance(value, UUID)
        else value.isoformat()
        if isinstance(value, datetime)
        else value
        for key, value in row.items()
    }


def _source_row(
    conn: Connection, owner_id: UUID, source_id: UUID, *, lock: bool = False
) -> RowMapping:
    row = (
        conn.execute(
            text(
                """SELECT s.*,r.policy_sha256,
        greatest(0,extract(epoch FROM s.next_allowed_at-clock_timestamp())) AS wait_seconds,
        extract(epoch FROM timestamptz '9999-12-30 00:00:00+00'-clock_timestamp()) AS maximum_pause_seconds
        FROM offerloom.source_configs s LEFT JOIN offerloom.source_policy_reviews r
        ON r.owner_id=s.owner_id AND r.id=s.current_policy_review_id
        WHERE s.owner_id=:owner AND s.id=:id"""
                + (" FOR UPDATE OF s" if lock else "")
            ),
            {"owner": owner_id, "id": source_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise Problem(404, "source_not_found")
    return row


def get_source(conn: Connection, owner_id: UUID, source_id: UUID) -> dict[str, object]:
    row = _source_row(conn, owner_id, source_id)
    fields = (
        "id",
        "version",
        "source_kind",
        "board_token",
        "company",
        "enabled",
        "terms_url",
        "terms_revision",
        "terms_reviewed_at",
        "policy_version",
        "allowed_host",
        "request_limit_per_minute",
        "last_success_at",
        "last_error_code",
        "next_allowed_at",
        "created_at",
        "rate_limit_blocked",
        "removed_at",
    )
    result = _wire({key: row[key] for key in fields})
    result["health"] = (
        "retired"
        if row["removed_at"] is not None
        else "disabled"
        if not row["enabled"]
        else "rate_limited"
        if row["rate_limit_blocked"]
        or (row["last_error_code"] == "source_rate_limited" and row["wait_seconds"] > 0)
        else "degraded"
        if row["last_error_code"] is not None
        else "healthy"
        if row["last_success_at"] is not None
        else "unknown"
    )
    result["quota"] = {"known": False, "limit": None}
    result["retry_after_seconds"] = row["rate_limit_retry_after_seconds"]
    return result


def _metadata(
    conn: Connection,
    owner_id: UUID,
    entity_id: UUID,
    entity: str,
    action: str,
    version: int,
    detail: dict[str, object],
    *,
    worker_id: UUID | None = None,
) -> None:
    append_outbox(conn, owner_id, action, entity, entity_id, detail, version)
    conn.execute(
        text("""INSERT INTO offerloom.audit_events
        (owner_id,action,actor_kind,actor_id,request_id,entity_type,entity_id,detail)
        VALUES(:owner,:action,:actor_kind,:actor,:request,:entity,:id,CAST(:detail AS jsonb))"""),
        {
            "owner": owner_id,
            "action": action,
            "actor_kind": "worker" if worker_id else "owner",
            "actor": str(worker_id or owner_id),
            "request": uuid4(),
            "entity": entity,
            "id": entity_id,
            "detail": canonical_json(detail),
        },
    )


def _policy_hash(source_id: UUID, policy_version: int, body: SourceWrite) -> str:
    return hashlib.sha256(
        canonical_json(
            {
                "source_id": str(source_id),
                "policy_version": policy_version,
                "board_token": body.board_token,
                "company": body.company,
                "terms_url": body.terms_url,
                "terms_revision": body.terms_revision,
                "allowed_host": ALLOWED_HOST,
                "request_limit_per_minute": body.request_limit_per_minute,
            }
        ).encode()
    ).hexdigest()


def save_source(
    conn: Connection,
    owner_id: UUID,
    source_id: UUID,
    body: SourceWrite,
    *,
    creating: bool,
) -> dict[str, object]:
    if creating:
        count = conn.execute(
            text(
                "SELECT count(*) FROM offerloom.source_configs WHERE owner_id=:owner AND source_kind='greenhouse'"
            ),
            {"owner": owner_id},
        ).scalar_one()
        if count >= MAX_SOURCES - 1:
            raise Problem(409, "source_limit_reached")
        duplicate = conn.execute(
            text(
                "SELECT id FROM offerloom.source_configs WHERE owner_id=:owner AND source_kind='greenhouse' AND board_token=:token"
            ),
            {"owner": owner_id, "token": body.board_token},
        ).first()
        if duplicate is not None:
            raise Problem(409, "source_already_exists")
        policy_version = 1
        version = 1
    else:
        previous = _source_row(conn, owner_id, source_id, lock=True)
        if previous["removed_at"] is not None:
            raise Problem(409, "source_removed")
        if previous["source_kind"] != "greenhouse":
            raise Problem(409, "manual_source_not_configurable")
        if body.board_token != previous["board_token"]:
            raise Problem(409, "source_board_immutable")
        policy_version = int(previous["policy_version"]) + 1
        version = int(previous["version"]) + 1
        # command() already holds these workflow locks before the source lock.
        ids = (
            conn.execute(
                text("""SELECT id FROM offerloom.workflow_runs WHERE owner_id=:owner AND source_config_id=:id
        AND state IN ('pending','running','retryable_failure','waiting_for_approval') ORDER BY id"""),
                {"owner": owner_id, "id": source_id},
            )
            .scalars()
            .all()
        )
        for workflow_id in ids:
            cancel_workflow(conn, owner_id, workflow_id)
    review_id = None
    reviewed_at = None
    if body.terms_review_acknowledged:
        review_id = uuid4()
        reviewed_at = conn.execute(
            text("""INSERT INTO offerloom.source_policy_reviews
        (owner_id,id,source_config_id,policy_version,board_token,company,terms_url,terms_revision,
         allowed_host,request_limit_per_minute,policy_sha256,acknowledged_by_owner_id)
        VALUES(:owner,:review,:source,:policy,:token,:company,:terms,:revision,:host,:rate,:sha,:owner)
        RETURNING reviewed_at"""),
            {
                "owner": owner_id,
                "review": review_id,
                "source": source_id,
                "policy": policy_version,
                "token": body.board_token,
                "company": body.company,
                "terms": body.terms_url,
                "revision": body.terms_revision,
                "host": ALLOWED_HOST,
                "rate": body.request_limit_per_minute,
                "sha": _policy_hash(source_id, policy_version, body),
            },
        ).scalar_one()
    statement = """INSERT INTO offerloom.source_configs
    (owner_id,id,source_kind,board_token,company,enabled,terms_url,terms_revision,terms_reviewed_at,
     allowed_host,request_limit_per_minute,policy_version,current_policy_review_id,version)
    VALUES(:owner,:id,'greenhouse',:token,:company,:enabled,:terms,:revision,:reviewed,:host,:rate,:policy,:review,:version)"""
    if not creating:
        statement = """UPDATE offerloom.source_configs SET company=:company,enabled=:enabled,
        terms_url=:terms,terms_revision=:revision,terms_reviewed_at=:reviewed,allowed_host=:host,
        request_limit_per_minute=:rate,policy_version=:policy,current_policy_review_id=:review,version=:version
        WHERE owner_id=:owner AND id=:id"""
    conn.execute(
        text(statement),
        {
            "owner": owner_id,
            "id": source_id,
            "token": body.board_token,
            "company": body.company,
            "enabled": body.enabled,
            "terms": body.terms_url,
            "revision": body.terms_revision,
            "reviewed": reviewed_at,
            "host": ALLOWED_HOST,
            "rate": body.request_limit_per_minute,
            "policy": policy_version,
            "review": review_id,
            "version": version,
        },
    )
    _metadata(
        conn,
        owner_id,
        source_id,
        "source",
        "source.created" if creating else "source.updated",
        version,
        {"source_id": str(source_id), "policy_version": policy_version, "enabled": body.enabled},
    )
    return get_source(conn, owner_id, source_id)


def retire_source(conn: Connection, owner_id: UUID, source_id: UUID) -> dict[str, object]:
    row = _source_row(conn, owner_id, source_id, lock=True)
    if row["source_kind"] != "greenhouse":
        raise Problem(409, "manual_source_not_removable")
    if row["removed_at"] is not None:
        raise Problem(409, "source_removed")
    # command() locked active workflows before this source, matching worker publication.
    workflow_ids = (
        conn.execute(
            text("""SELECT id FROM offerloom.workflow_runs WHERE owner_id=:owner AND source_config_id=:id
        AND state IN ('pending','running','retryable_failure','waiting_for_approval') ORDER BY id"""),
            {"owner": owner_id, "id": source_id},
        )
        .scalars()
        .all()
    )
    for workflow_id in workflow_ids:
        cancel_workflow(conn, owner_id, workflow_id)
    version = conn.execute(
        text("""UPDATE offerloom.source_configs SET enabled=false,removed_at=clock_timestamp(),
        version=version+1 WHERE owner_id=:owner AND id=:id RETURNING version"""),
        {"owner": owner_id, "id": source_id},
    ).scalar_one()
    _metadata(
        conn,
        owner_id,
        source_id,
        "source",
        "source.retired",
        int(version),
        {"source_id": str(source_id)},
    )
    return get_source(conn, owner_id, source_id)


def _manual_source(conn: Connection, owner_id: UUID) -> UUID:
    existing = conn.execute(
        text(
            "SELECT id FROM offerloom.source_configs WHERE owner_id=:owner AND source_kind='manual' ORDER BY created_at,id LIMIT 1"
        ),
        {"owner": owner_id},
    ).scalar_one_or_none()
    if existing is not None:
        return cast(UUID, existing)
    source_id = uuid5(NAMESPACE_URL, f"offerloom:owner:{owner_id}:manual-source")
    conn.execute(
        text("""INSERT INTO offerloom.source_configs(owner_id,id,source_kind,company,enabled,last_success_at)
    VALUES(:owner,:id,'manual','Manual postings',true,clock_timestamp())"""),
        {"owner": owner_id, "id": source_id},
    )
    return source_id


def create_manual(
    conn: Connection, owner_id: UUID, posting_id: UUID, body: ManualPosting
) -> dict[str, object]:
    source_id = _manual_source(conn, owner_id)
    snapshot = body.model_dump(mode="json")
    sha = hashlib.sha256(canonical_json(snapshot).encode()).hexdigest()
    _save_job(
        conn,
        owner_id,
        source_id,
        str(posting_id),
        posting_id,
        body.title,
        body.company,
        body.location_text,
        body.description_text,
        body.canonical_url,
        sha,
        snapshot,
        "manual:paste",
        1,
        None,
    )
    conn.execute(
        text("""UPDATE offerloom.source_configs SET last_success_at=clock_timestamp(),
        last_error_code=NULL,version=version+1 WHERE owner_id=:owner AND id=:id"""),
        {"owner": owner_id, "id": source_id},
    )
    _metadata(
        conn,
        owner_id,
        posting_id,
        "posting",
        "posting.created",
        1,
        {"posting_id": str(posting_id), "source_id": str(source_id)},
    )
    return catalog.get_posting(conn, owner_id, posting_id)


def _save_job(
    conn: Connection,
    owner_id: UUID,
    source_id: UUID,
    external_id: str,
    posting_id: UUID,
    title: str,
    company: str,
    location: str,
    description: str,
    canonical_url: str | None,
    sha: str,
    snapshot: dict[str, object],
    source_url: str,
    policy_version: int,
    review_id: UUID | None,
) -> str:
    old = (
        conn.execute(
            text(
                "SELECT id,content_sha256,company FROM offerloom.job_postings WHERE owner_id=:owner AND source_config_id=:source AND external_id=:external FOR UPDATE"
            ),
            {"owner": owner_id, "source": source_id, "external": external_id},
        )
        .mappings()
        .one_or_none()
    )
    if old is None:
        conn.execute(
            text("""INSERT INTO offerloom.job_postings
        (owner_id,id,title,company,location_text,description_text,canonical_url,dedupe_key,source_config_id,external_id,content_sha256,last_observed_at)
        VALUES(:owner,:id,:title,:company,:location,:description,:url,:dedupe,:source,:external,:sha,clock_timestamp())"""),
            {
                "owner": owner_id,
                "id": posting_id,
                "title": title,
                "company": company,
                "location": location,
                "description": description,
                "url": canonical_url,
                "dedupe": f"source:{source_id}:{external_id}",
                "source": source_id,
                "external": external_id,
                "sha": sha,
            },
        )
        outcome = "new"
    else:
        posting_id = old["id"]
        changed = old["content_sha256"] != sha or old["company"] != company
        if changed:
            conn.execute(
                text("""UPDATE offerloom.job_postings SET title=:title,company=:company,location_text=:location,
            description_text=:description,canonical_url=:url,content_sha256=:sha,last_observed_at=clock_timestamp(),
            version=version+1,updated_at=clock_timestamp() WHERE owner_id=:owner AND id=:id"""),
                {
                    "owner": owner_id,
                    "id": posting_id,
                    "title": title,
                    "company": company,
                    "location": location,
                    "description": description,
                    "url": canonical_url,
                    "sha": sha,
                },
            )
        else:
            conn.execute(
                text(
                    "UPDATE offerloom.job_postings SET last_observed_at=clock_timestamp() WHERE owner_id=:owner AND id=:id"
                ),
                {"owner": owner_id, "id": posting_id},
            )
        outcome = "changed" if changed else "unchanged"
    conn.execute(
        text("""INSERT INTO offerloom.job_source_observations
    (owner_id,posting_id,source_config_id,external_id,content_sha256,source_url,snapshot,policy_version,policy_review_id)
    VALUES(:owner,:posting,:source,:external,:sha,:url,CAST(:snapshot AS jsonb),:policy,:review)
    ON CONFLICT(owner_id,source_config_id,external_id,policy_version,content_sha256) DO NOTHING"""),
        {
            "owner": owner_id,
            "posting": posting_id,
            "source": source_id,
            "external": external_id,
            "sha": sha,
            "url": source_url,
            "snapshot": canonical_json(snapshot),
            "policy": policy_version,
            "review": review_id,
        },
    )
    return outcome


def _encode_cursor(row: RowMapping) -> str:
    return (
        base64.urlsafe_b64encode(
            canonical_json(
                {"created_at": cast(datetime, row["created_at"]).isoformat(), "id": str(row["id"])}
            ).encode()
        )
        .decode()
        .rstrip("=")
    )


def _decode_cursor(value: str | None) -> tuple[datetime | None, UUID | None]:
    if value is None:
        return None, None
    try:
        if len(value) > 512:
            raise ValueError("cursor length")
        data = json.loads(
            base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        )
        if (
            not isinstance(data, dict)
            or set(data) != {"created_at", "id"}
            or not isinstance(data["created_at"], str)
            or not isinstance(data["id"], str)
        ):
            raise ValueError("cursor shape")
        timestamp = datetime.fromisoformat(data["created_at"])
        if timestamp.tzinfo is None:
            raise ValueError("cursor timezone")
        return timestamp, UUID(data["id"])
    except (ValueError, TypeError, KeyError, binascii.Error, UnicodeError) as exc:
        raise Problem(400, "invalid_cursor") from exc


def observations(
    conn: Connection,
    owner_id: UUID,
    posting_id: UUID,
    cursor: str | None = None,
    limit: int = 20,
    observation_id: UUID | None = None,
) -> dict[str, object]:
    catalog.get_posting(conn, owner_id, posting_id)
    timestamp, identifier = _decode_cursor(cursor)
    rows = (
        conn.execute(
            text(
                """SELECT o.id,o.source_config_id,s.source_kind,s.board_token,o.external_id,
    o.content_sha256,o.fetched_at,o.created_at,o.policy_version"""
                + (",o.source_url,o.snapshot" if observation_id else "")
                + """
    FROM offerloom.job_source_observations o JOIN offerloom.source_configs s ON s.owner_id=o.owner_id AND s.id=o.source_config_id
    WHERE o.owner_id=:owner AND o.posting_id=:posting
    AND (CAST(:observation AS uuid) IS NULL OR o.id=CAST(:observation AS uuid))
    AND (CAST(:time AS timestamptz) IS NULL OR (o.created_at,o.id)<(CAST(:time AS timestamptz),CAST(:id AS uuid)))
    ORDER BY o.created_at DESC,o.id DESC LIMIT :limit"""
            ),
            {
                "owner": owner_id,
                "posting": posting_id,
                "observation": observation_id,
                "time": timestamp,
                "id": identifier,
                "limit": 1 if observation_id else limit + 1,
            },
        )
        .mappings()
        .all()
    )
    if observation_id:
        if not rows:
            raise Problem(404, "observation_not_found")
        return _wire(rows[0])
    return {
        "items": [_wire(row) for row in rows[:limit]],
        "next_cursor": _encode_cursor(rows[limit - 1]) if len(rows) > limit else None,
    }


def request_refresh(conn: Connection, owner_id: UUID, source_id: UUID) -> dict[str, object]:
    row = _source_row(conn, owner_id, source_id, lock=True)
    if row["removed_at"] is not None:
        raise Problem(409, "source_removed")
    if (
        row["source_kind"] != "greenhouse"
        or not row["enabled"]
        or row["current_policy_review_id"] is None
    ):
        raise Problem(409, "source_not_reviewed_enabled")
    if row["rate_limit_blocked"]:
        raise SourceRateLimited(int(row["rate_limit_retry_after_seconds"]))
    if row["wait_seconds"] > 0:
        raise SourceRateLimited(math.ceil(row["wait_seconds"]))
    active = conn.execute(
        text("""SELECT id FROM offerloom.workflow_runs WHERE owner_id=:owner AND source_config_id=:id
    AND state IN ('pending','running','retryable_failure','waiting_for_approval')"""),
        {"owner": owner_id, "id": source_id},
    ).first()
    if active is not None:
        raise Problem(409, "source_refresh_active")
    workflow_id = enqueue(
        conn,
        owner_id,
        "source_refresh",
        {
            "source_id": str(source_id),
            "policy_version": int(row["policy_version"]),
            "policy_review_id": str(row["current_policy_review_id"]),
            "policy_sha256": row["policy_sha256"],
        },
    )
    return {"workflow_id": str(workflow_id), "source_id": str(source_id)}


def _lock_task_source(conn: Connection, task: Claim) -> RowMapping:
    lock_installation(conn)
    conn.execute(
        text("SELECT owner_id FROM offerloom.owner_settings WHERE owner_id=:owner FOR UPDATE"),
        {"owner": task.owner_id},
    )
    assert_fence(conn, task)
    row = _source_row(conn, task.owner_id, UUID(str(task.input["source_id"])), lock=True)
    if (
        row["removed_at"] is not None
        or not row["enabled"]
        or row["source_kind"] != "greenhouse"
        or row["current_policy_review_id"] is None
        or row["policy_version"] != task.input["policy_version"]
        or str(row["current_policy_review_id"]) != task.input["policy_review_id"]
        or row["policy_sha256"] != task.input["policy_sha256"]
    ):
        raise Problem(409, "source_policy_changed")
    return row


def _reserve(conn: Connection, task: Claim) -> str:
    row = _lock_task_source(conn, task)
    if row["rate_limit_blocked"]:
        raise SourceRateLimited(int(row["rate_limit_retry_after_seconds"]))
    if row["wait_seconds"] > 0:
        raise SourceRateLimited(math.ceil(row["wait_seconds"]))
    conn.execute(
        text("""UPDATE offerloom.source_configs SET
    next_allowed_at=clock_timestamp()+make_interval(secs=>60.0/request_limit_per_minute)
    WHERE owner_id=:owner AND id=:id"""),
        {"owner": task.owner_id, "id": row["id"]},
    )
    return str(row["board_token"])


def _publish_read(conn: Connection, task: Claim, board: BoardRead) -> dict[str, object]:
    row = _lock_task_source(conn, task)
    counts = {"new": 0, "changed": 0, "unchanged": 0}
    for job in board.jobs:
        outcome = _save_job(
            conn,
            task.owner_id,
            row["id"],
            job.external_id,
            uuid4(),
            job.title,
            row["company"],
            job.location_text,
            job.description_text,
            job.canonical_url,
            job.source_content_sha256,
            job.snapshot,
            f"https://{ALLOWED_HOST}/v1/boards/{row['board_token']}/jobs?content=true",
            int(row["policy_version"]),
            row["current_policy_review_id"],
        )
        counts[outcome] += 1
    refreshed = (
        conn.execute(
            text("""UPDATE offerloom.source_configs
    SET last_success_at=clock_timestamp(),last_error_code=NULL,version=version+1
    WHERE owner_id=:owner AND id=:id RETURNING last_success_at,version"""),
            {"owner": task.owner_id, "id": row["id"]},
        )
        .mappings()
        .one()
    )
    result: dict[str, object] = {
        "source_id": str(row["id"]),
        "policy_version": row["policy_version"],
        "fetched_at": refreshed["last_success_at"].isoformat(),
        "fetch_elapsed_ms": board.fetch_elapsed_ms,
        "posting_count": len(board.jobs),
        "new_posting_count": counts["new"],
        "changed_posting_count": counts["changed"],
        "unchanged_posting_count": counts["unchanged"],
        "excluded_prospect_count": board.excluded_prospect_count,
        "freshness": "current",
    }
    _metadata(
        conn,
        task.owner_id,
        row["id"],
        "source",
        "source.refreshed",
        refreshed["version"],
        {**result, "workflow_id": str(task.id)},
        worker_id=task.holder,
    )
    return result


def _record_source_failure(
    db: Database, task: Claim, error: SourceFailure | SourceRateLimited
) -> None:
    seconds = error.retry_after_seconds
    if error.code == "source_rate_limited":
        seconds = max(1, seconds if seconds is not None else 60)
    if isinstance(error, SourceFailure) and seconds is not None:
        # An already-consumed request can impose a pause after cancellation or review.
        # Persist only a monotonic scheduling restriction, never stale domain output.
        with db.transaction() as conn:
            conn.execute(
                text("SELECT singleton FROM offerloom.installation_state WHERE singleton FOR SHARE")
            ).scalar_one()
            conn.execute(
                text(
                    "SELECT owner_id FROM offerloom.owner_settings WHERE owner_id=:owner FOR UPDATE"
                ),
                {"owner": task.owner_id},
            ).scalar_one()
            row = _source_row(conn, task.owner_id, UUID(str(task.input["source_id"])), lock=True)
            if row["removed_at"] is not None:
                raise Problem(409, "source_removed")
            blocked = bool(row["rate_limit_blocked"]) or (
                seconds >= int(row["maximum_pause_seconds"])
            )
            persisted_seconds = (
                max(int(row["rate_limit_retry_after_seconds"] or 0), seconds)
                if blocked
                else seconds
            )
            conn.execute(
                text("""UPDATE offerloom.source_configs SET
                rate_limit_blocked=:blocked,rate_limit_retry_after_seconds=:raw_seconds,
                next_allowed_at=CASE WHEN :blocked THEN NULL ELSE greatest(
                coalesce(next_allowed_at,clock_timestamp()),
                clock_timestamp()+make_interval(secs=>CAST(:seconds AS double precision))) END
                WHERE owner_id=:owner AND id=:id"""),
                {
                    "owner": task.owner_id,
                    "id": row["id"],
                    "blocked": blocked,
                    "raw_seconds": str(persisted_seconds),
                    "seconds": None if blocked else seconds,
                },
            )
    with db.transaction() as conn:
        row = _lock_task_source(conn, task)
        source_version = conn.execute(
            text("""UPDATE offerloom.source_configs SET last_error_code=:code,version=version+1
            WHERE owner_id=:owner AND id=:id RETURNING version"""),
            {"owner": task.owner_id, "id": row["id"], "code": error.code},
        ).scalar_one()
        minimum = (
            int(row["rate_limit_retry_after_seconds"])
            if row["rate_limit_blocked"]
            else math.ceil(row["wait_seconds"])
        )
        result: dict[str, object] = {
            "source_id": str(row["id"]),
            "policy_version": row["policy_version"],
            "error_code": error.code,
        }
        conn.execute(
            text(
                "UPDATE offerloom.workflow_runs SET result=CAST(:result AS jsonb) WHERE owner_id=:owner AND id=:id"
            ),
            {"owner": task.owner_id, "id": task.id, "result": canonical_json(result)},
        )
        _metadata(
            conn,
            task.owner_id,
            row["id"],
            "source",
            "source.refresh_failed",
            source_version,
            {**result, "workflow_id": str(task.id)},
            worker_id=task.holder,
        )
        fail(conn, task, error.code, error.code in _TRANSIENT_ERRORS, minimum)


def execute_refresh(
    db: Database, task: Claim, interrupted: Event, client: SourceClient | None = None
) -> None:
    """Only the explicit programmatic test seam can replace the fixed production relay."""
    try:
        if interrupted.is_set():
            return
        with db.transaction() as conn:
            board_token = _reserve(conn, task)
        board = (client if client is not None else SourceGatewayClient()).read(
            board_token, interrupted
        )
        if interrupted.is_set():
            return
        with db.transaction() as conn:
            publish(conn, task, lambda active: _publish_read(active, task, board))
    except SourceRateLimited as exc:
        _record_source_failure(db, task, exc)
    except SourceFailure as exc:
        if exc.code != "source_cancelled":
            _record_source_failure(db, task, exc)


def build_router(db: Database, settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.post("/postings")
    def manual_posting(
        body: ManualPosting,
        owner: Annotated[Owner, Depends(require_owner)],
        idempotency_key: Annotated[str | None, Header()] = None,
        if_match: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        posting_id = uuid4()
        with db.transaction() as conn:
            status, result = command(
                conn,
                owner.id,
                "POST",
                "/postings",
                {},
                body.model_dump(mode="json"),
                if_match,
                idempotency_key,
                "posting",
                posting_id,
                lambda active: (201, create_manual(active, owner.id, posting_id, body)),
                creation_kind="posting",
            )
        return JSONResponse(result, status_code=status, headers={"ETag": f'"{result["version"]}"'})

    @router.get("/postings")
    def posting_list(
        owner: Annotated[Owner, Depends(require_owner)],
        query: Annotated[catalog.CatalogQuery, Query()],
    ) -> dict[str, object]:
        with db.transaction() as conn:
            return catalog.list_postings(conn, owner.id, settings.keyring, query)

    @router.get("/postings/{posting_id}")
    def posting(posting_id: UUID, owner: Annotated[Owner, Depends(require_owner)]) -> JSONResponse:
        with db.transaction() as conn:
            result = catalog.get_posting(conn, owner.id, posting_id)
        return JSONResponse(result, headers={"ETag": f'"{result["version"]}"'})

    @router.put("/postings/{posting_id}/disposition")
    def posting_disposition(
        posting_id: UUID,
        body: catalog.DispositionWrite,
        owner: Annotated[Owner, Depends(require_owner)],
        if_match: Annotated[str | None, Header()] = None,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        def effect(conn: Connection) -> tuple[int, dict[str, object]]:
            result = catalog.set_disposition(conn, owner.id, posting_id, body.disposition)
            _metadata(
                conn,
                owner.id,
                posting_id,
                "posting",
                "opportunity.disposition_changed",
                int(cast(int, result["version"])),
                {"posting_id": str(posting_id), "disposition": body.disposition},
            )
            return 200, result

        with db.transaction() as conn:
            status, result = command(
                conn,
                owner.id,
                "PUT",
                "/postings/{posting_id}/disposition",
                {"posting_id": str(posting_id)},
                body.model_dump(mode="json"),
                if_match,
                idempotency_key,
                "posting",
                posting_id,
                effect,
            )
        return JSONResponse(result, status_code=status, headers={"ETag": f'"{result["version"]}"'})

    @router.get("/postings/{posting_id}/duplicates")
    def duplicate_list(
        posting_id: UUID,
        owner: Annotated[Owner, Depends(require_owner)],
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> dict[str, object]:
        with db.transaction() as conn:
            return catalog.list_duplicates(
                conn, owner.id, posting_id, settings.keyring, cursor, limit
            )

    @router.post("/postings/{posting_id}/duplicates/{candidate_id}/decision")
    def duplicate_decision(
        posting_id: UUID,
        candidate_id: UUID,
        body: catalog.DuplicateWrite,
        owner: Annotated[Owner, Depends(require_owner)],
        if_match: Annotated[str | None, Header()] = None,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        def effect(conn: Connection) -> tuple[int, dict[str, object]]:
            result = catalog.decide_duplicate(
                conn, owner.id, posting_id, candidate_id, body.decision
            )
            _metadata(
                conn,
                owner.id,
                posting_id,
                "posting",
                "posting.duplicate_reviewed",
                int(cast(int, result["version"])),
                {
                    "posting_id": str(posting_id),
                    "candidate_id": str(candidate_id),
                    "decision": body.decision,
                },
            )
            return 200, result

        with db.transaction() as conn:
            status, result = command(
                conn,
                owner.id,
                "POST",
                "/postings/{posting_id}/duplicates/{candidate_id}/decision",
                {"posting_id": str(posting_id), "candidate_id": str(candidate_id)},
                body.model_dump(mode="json"),
                if_match,
                idempotency_key,
                "posting",
                posting_id,
                effect,
            )
        return JSONResponse(result, status_code=status, headers={"ETag": f'"{result["version"]}"'})

    @router.get("/postings/{posting_id}/observations")
    def observation_list(
        posting_id: UUID,
        owner: Annotated[Owner, Depends(require_owner)],
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> dict[str, object]:
        with db.transaction() as conn:
            return observations(conn, owner.id, posting_id, cursor, limit)

    @router.get("/postings/{posting_id}/observations/{observation_id}")
    def observation_detail(
        posting_id: UUID, observation_id: UUID, owner: Annotated[Owner, Depends(require_owner)]
    ) -> dict[str, object]:
        with db.transaction() as conn:
            return observations(conn, owner.id, posting_id, observation_id=observation_id)

    @router.get("/sources")
    def source_list(owner: Annotated[Owner, Depends(require_owner)]) -> dict[str, object]:
        with db.transaction() as conn:
            ids = (
                conn.execute(
                    text(
                        "SELECT id FROM offerloom.source_configs WHERE owner_id=:owner ORDER BY created_at DESC,id DESC LIMIT 100"
                    ),
                    {"owner": owner.id},
                )
                .scalars()
                .all()
            )
            return {"items": [get_source(conn, owner.id, source_id) for source_id in ids]}

    @router.post("/sources")
    def create_source(
        body: SourceWrite,
        owner: Annotated[Owner, Depends(require_owner)],
        idempotency_key: Annotated[str | None, Header()] = None,
        if_match: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        source_id = uuid4()
        with db.transaction() as conn:
            status, result = command(
                conn,
                owner.id,
                "POST",
                "/sources",
                {},
                body.model_dump(mode="json"),
                if_match,
                idempotency_key,
                "source",
                source_id,
                lambda active: (201, save_source(active, owner.id, source_id, body, creating=True)),
                creation_kind="source",
            )
        return JSONResponse(result, status_code=status, headers={"ETag": f'"{result["version"]}"'})

    @router.get("/sources/{source_id}")
    def source(source_id: UUID, owner: Annotated[Owner, Depends(require_owner)]) -> JSONResponse:
        with db.transaction() as conn:
            result = get_source(conn, owner.id, source_id)
        return JSONResponse(result, headers={"ETag": f'"{result["version"]}"'})

    @router.put("/sources/{source_id}")
    def update_source(
        source_id: UUID,
        body: SourceWrite,
        owner: Annotated[Owner, Depends(require_owner)],
        if_match: Annotated[str | None, Header()] = None,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        with db.transaction() as conn:
            status, result = command(
                conn,
                owner.id,
                "PUT",
                "/sources/{source_id}",
                {"source_id": str(source_id)},
                body.model_dump(mode="json"),
                if_match,
                idempotency_key,
                "source",
                source_id,
                lambda active: (
                    200,
                    save_source(active, owner.id, source_id, body, creating=False),
                ),
            )
        return JSONResponse(result, status_code=status, headers={"ETag": f'"{result["version"]}"'})

    @router.delete("/sources/{source_id}")
    def remove_source(
        source_id: UUID,
        owner: Annotated[Owner, Depends(require_owner)],
        if_match: Annotated[str | None, Header()] = None,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        with db.transaction() as conn:
            status, result = command(
                conn,
                owner.id,
                "DELETE",
                "/sources/{source_id}",
                {"source_id": str(source_id)},
                {},
                if_match,
                idempotency_key,
                "source",
                source_id,
                lambda active: (200, retire_source(active, owner.id, source_id)),
            )
        return JSONResponse(result, status_code=status, headers={"ETag": f'"{result["version"]}"'})

    @router.post("/sources/{source_id}/refresh")
    def refresh(
        source_id: UUID,
        body: RefreshRequest,
        request: Request,
        owner: Annotated[Owner, Depends(require_owner)],
        if_match: Annotated[str | None, Header()] = None,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        try:
            with db.transaction() as conn:
                status, result = command(
                    conn,
                    owner.id,
                    "POST",
                    "/sources/{source_id}/refresh",
                    {"source_id": str(source_id)},
                    body.model_dump(mode="json"),
                    if_match,
                    idempotency_key,
                    "source",
                    source_id,
                    lambda active: (202, request_refresh(active, owner.id, source_id)),
                )
        except SourceRateLimited as exc:
            response = problem_response(request, exc)
            response.headers["Retry-After"] = str(exc.retry_after_seconds)
            return response
        return JSONResponse(result, status_code=status)

    return router
