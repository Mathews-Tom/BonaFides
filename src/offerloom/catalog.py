# SPDX-License-Identifier: MIT OR Apache-2.0
"""Owner-scoped indexed catalog, authenticated keysets and independent review state."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from datetime import UTC, datetime
from time import perf_counter_ns
from typing import Literal, cast
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, field_validator
from sqlalchemy import Connection, TextClause, text
from sqlalchemy.engine import RowMapping

from offerloom.auth import StrictModel
from offerloom.commands import canonical_json
from offerloom.crypto import Keyring
from offerloom.problems import Problem

Disposition = Literal["saved", "dismissed", "archived", "unmarked"]
DuplicateDecision = Literal["suspected", "distinct"]
_CURSOR_PURPOSE = b"offerloom/catalog/keyset/v1"
_SUMMARY = """p.id,p.version,p.title,p.company,p.location_text,p.status,p.created_at,p.updated_at,
p.source_config_id,s.source_kind,p.external_id,p.last_observed_at,
nullif(o.disposition,'unmarked') AS disposition"""
_JOINS = """LEFT JOIN offerloom.source_configs s ON s.owner_id=p.owner_id AND s.id=p.source_config_id
LEFT JOIN offerloom.saved_opportunities o ON o.owner_id=p.owner_id AND o.posting_id=p.id"""


class CatalogQuery(StrictModel):
    cursor: str | None = Field(default=None, max_length=2048)
    limit: int = Field(default=20, ge=1, le=100)
    q: str | None = Field(default=None, max_length=300)
    location: str | None = Field(default=None, max_length=2000)
    source_id: UUID | None = None
    status: Literal["open", "closed", "unknown"] | None = None
    disposition: Disposition | None = None
    observed_after: AwareDatetime | None = None

    @field_validator("q", "location")
    @classmethod
    def literal_text(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                value.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ValueError("invalid Unicode text") from exc
            if "\x00" in value:
                raise ValueError("NUL is not permitted")
        return value if value else None

    @field_validator("observed_after")
    @classmethod
    def utc_recency(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None

    def identity(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"cursor", "limit"})


class DispositionWrite(StrictModel):
    disposition: Disposition


class DuplicateWrite(StrictModel):
    decision: DuplicateDecision


def _wire(row: RowMapping) -> dict[str, object]:
    return {
        key: str(value)
        if isinstance(value, UUID)
        else value.isoformat()
        if isinstance(value, datetime)
        else value
        for key, value in row.items()
    }


def get_posting(conn: Connection, owner_id: UUID, posting_id: UUID) -> dict[str, object]:
    row = (
        conn.execute(
            text(
                f"""SELECT {_SUMMARY},p.description_text,p.canonical_url,p.content_sha256
            FROM offerloom.job_postings p {_JOINS} WHERE p.owner_id=:owner AND p.id=:id"""
            ),
            {"owner": owner_id, "id": posting_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise Problem(404, "posting_not_found")
    return _wire(row)


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    result = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    if _b64(result) != value:
        raise ValueError("noncanonical cursor encoding")
    return result


def _signing_key(keyring: Keyring, key_id: str) -> bytes:
    # Derive a purpose-specific MAC key; encryption keys are never used directly.
    return hmac.digest(keyring.keys[key_id], _CURSOR_PURPOSE, "sha256")


def _filter_hash(filters: dict[str, object]) -> str:
    return hashlib.sha256(canonical_json(filters).encode()).hexdigest()


def _encode_cursor(
    keyring: Keyring,
    owner_id: UUID,
    scope: str,
    filters: dict[str, object],
    as_of: datetime,
    row: RowMapping,
) -> str:
    payload = canonical_json(
        {
            "v": 1,
            "kid": keyring.active_key_id,
            "owner": str(owner_id),
            "scope": scope,
            "filters": _filter_hash(filters),
            "as_of": as_of.isoformat(),
            "created_at": cast(datetime, row["created_at"]).isoformat(),
            "id": str(row["id"]),
        }
    ).encode()
    return (
        _b64(payload)
        + "."
        + _b64(hmac.digest(_signing_key(keyring, keyring.active_key_id), payload, "sha256"))
    )


def _window(
    conn: Connection,
    keyring: Keyring,
    owner_id: UUID,
    scope: str,
    filters: dict[str, object],
    cursor: str | None,
) -> tuple[datetime, datetime | None, UUID | None]:
    if cursor is None:
        return conn.execute(text("SELECT clock_timestamp()")).scalar_one(), None, None
    try:
        if len(cursor) > 2048:
            raise ValueError("cursor length")
        encoded, signature = cursor.split(".")
        payload = _unb64(encoded)
        data = json.loads(payload)
        if not isinstance(data, dict) or set(data) != {
            "v",
            "kid",
            "owner",
            "scope",
            "filters",
            "as_of",
            "created_at",
            "id",
        }:
            raise ValueError("cursor shape")
        if (
            type(data["v"]) is not int
            or data["v"] != 1
            or not all(isinstance(data[name], str) for name in data if name != "v")
        ):
            raise ValueError("cursor types")
        expected = hmac.digest(_signing_key(keyring, data["kid"]), payload, "sha256")
        if not hmac.compare_digest(expected, _unb64(signature)):
            raise ValueError("cursor signature")
        if (
            data["owner"] != str(owner_id)
            or data["scope"] != scope
            or data["filters"] != _filter_hash(filters)
        ):
            raise ValueError("cursor context")
        as_of = datetime.fromisoformat(data["as_of"])
        timestamp = datetime.fromisoformat(data["created_at"])
        if as_of.tzinfo is None or timestamp.tzinfo is None or timestamp > as_of:
            raise ValueError("cursor time")
        return as_of, timestamp, UUID(data["id"])
    except (ValueError, TypeError, KeyError, binascii.Error, UnicodeError, RecursionError) as exc:
        raise Problem(400, "invalid_cursor") from exc


def catalog_statement(
    owner_id: UUID,
    query: CatalogQuery,
    as_of: datetime,
    timestamp: datetime | None,
    identifier: UUID | None,
) -> tuple[TextClause, dict[str, object]]:
    """The actual production statement, also inspectable with PostgreSQL EXPLAIN."""
    predicates = ["p.owner_id=:owner", "p.created_at<=:as_of"]
    parameters: dict[str, object] = {"owner": owner_id, "as_of": as_of, "limit": query.limit + 1}
    if timestamp is not None:
        predicates.append("(p.created_at,p.id)<(:time,:id)")
        parameters.update(time=timestamp, id=identifier)
    if query.q is not None:
        predicates.append("p.search_vector @@ websearch_to_tsquery('simple',:q)")
        parameters["q"] = query.q
    if query.location is not None:
        # strpos is literal: %, _ and backslash do not acquire LIKE semantics.
        predicates.append("strpos(lower(p.location_text),lower(:location))>0")
        parameters["location"] = query.location
    if query.source_id is not None:
        predicates.append("p.source_config_id=:source")
        parameters["source"] = query.source_id
    if query.status is not None:
        predicates.append("p.status=:status")
        parameters["status"] = query.status
    if query.disposition is not None:
        predicates.append(
            "coalesce(o.disposition,'unmarked')=:disposition"
            if query.disposition == "unmarked"
            else "o.disposition=:disposition"
        )
        parameters["disposition"] = query.disposition
    if query.observed_after is not None:
        predicates.append("p.last_observed_at>=:observed_after")
        parameters["observed_after"] = query.observed_after
    statement = text(
        f"SELECT {_SUMMARY} FROM offerloom.job_postings p {_JOINS} WHERE "
        + " AND ".join(predicates)
        + " ORDER BY p.created_at DESC,p.id DESC LIMIT :limit"
    )
    return statement, parameters


def list_postings(
    conn: Connection, owner_id: UUID, keyring: Keyring, query: CatalogQuery
) -> dict[str, object]:
    started = perf_counter_ns()
    filters = query.identity()
    as_of, timestamp, identifier = _window(
        conn, keyring, owner_id, "catalog", filters, query.cursor
    )
    statement, parameters = catalog_statement(owner_id, query, as_of, timestamp, identifier)
    rows = conn.execute(statement, parameters).mappings().all()
    return {
        "items": [_wire(row) for row in rows[: query.limit]],
        "next_cursor": _encode_cursor(
            keyring, owner_id, "catalog", filters, as_of, rows[query.limit - 1]
        )
        if len(rows) > query.limit
        else None,
        "as_of": as_of.isoformat(),
        "query_elapsed_ms": round((perf_counter_ns() - started) / 1_000_000, 3),
    }


def set_disposition(
    conn: Connection, owner_id: UUID, posting_id: UUID, disposition: Disposition
) -> dict[str, object]:
    conn.execute(
        text("""INSERT INTO offerloom.saved_opportunities(owner_id,id,posting_id,disposition)
        VALUES(:owner,:id,:posting,:disposition) ON CONFLICT(owner_id,posting_id)
        DO UPDATE SET disposition=EXCLUDED.disposition,version=saved_opportunities.version+1"""),
        {"owner": owner_id, "id": uuid4(), "posting": posting_id, "disposition": disposition},
    )
    conn.execute(
        text("""UPDATE offerloom.job_postings SET version=version+1,updated_at=clock_timestamp()
        WHERE owner_id=:owner AND id=:id"""),
        {"owner": owner_id, "id": posting_id},
    )
    return get_posting(conn, owner_id, posting_id)


_DUPLICATE_IDS = """WITH anchor AS (
 SELECT owner_id,id,source_config_id,normalized_company,normalized_title,normalized_location
 FROM offerloom.job_postings WHERE owner_id=:owner AND id=:posting
), candidates AS (
 SELECT p.id FROM offerloom.job_postings p JOIN anchor a ON p.owner_id=a.owner_id
 AND md5(p.normalized_company)=md5(a.normalized_company)
 AND md5(p.normalized_title)=md5(a.normalized_title)
 AND md5(p.normalized_location)=md5(a.normalized_location)
 AND p.normalized_company=a.normalized_company AND p.normalized_title=a.normalized_title
 AND p.normalized_location=a.normalized_location
 WHERE p.id<>a.id AND p.source_config_id IS DISTINCT FROM a.source_config_id
 AND a.normalized_company<>'' AND a.normalized_title<>''
 UNION SELECT candidate_id FROM offerloom.posting_duplicate_decisions WHERE owner_id=:owner AND posting_id=:posting
 UNION SELECT posting_id FROM offerloom.posting_duplicate_decisions WHERE owner_id=:owner AND candidate_id=:posting
) """


def list_duplicates(
    conn: Connection,
    owner_id: UUID,
    posting_id: UUID,
    keyring: Keyring,
    cursor: str | None,
    limit: int,
) -> dict[str, object]:
    get_posting(conn, owner_id, posting_id)
    filters: dict[str, object] = {"posting_id": str(posting_id)}
    as_of, timestamp, identifier = _window(conn, keyring, owner_id, "duplicates", filters, cursor)
    rows = (
        conn.execute(
            text(
                _DUPLICATE_IDS
                + f"""SELECT {_SUMMARY},coalesce(d.decision,'suspected') AS decision
            FROM candidates c JOIN offerloom.job_postings p ON p.owner_id=:owner AND p.id=c.id
            {_JOINS} LEFT JOIN offerloom.posting_duplicate_decisions d
            ON d.owner_id=:owner AND d.posting_id=least(:posting,p.id) AND d.candidate_id=greatest(:posting,p.id)
            WHERE p.created_at<=:as_of
            AND (CAST(:time AS timestamptz) IS NULL OR (p.created_at,p.id)<(CAST(:time AS timestamptz),CAST(:id AS uuid)))
            ORDER BY p.created_at DESC,p.id DESC LIMIT :limit"""
            ),
            {
                "owner": owner_id,
                "posting": posting_id,
                "as_of": as_of,
                "time": timestamp,
                "id": identifier,
                "limit": limit + 1,
            },
        )
        .mappings()
        .all()
    )
    return {
        "items": [_wire(row) for row in rows[:limit]],
        "next_cursor": _encode_cursor(
            keyring, owner_id, "duplicates", filters, as_of, rows[limit - 1]
        )
        if len(rows) > limit
        else None,
    }


def decide_duplicate(
    conn: Connection,
    owner_id: UUID,
    posting_id: UUID,
    candidate_id: UUID,
    decision: DuplicateDecision,
) -> dict[str, object]:
    eligible = conn.execute(
        text(_DUPLICATE_IDS + "SELECT id FROM candidates WHERE id=:candidate"),
        {"owner": owner_id, "posting": posting_id, "candidate": candidate_id},
    ).first()
    if eligible is None:
        raise Problem(404, "duplicate_candidate_not_found")
    left, right = sorted((posting_id, candidate_id))
    conn.execute(
        text("""INSERT INTO offerloom.posting_duplicate_decisions(owner_id,posting_id,candidate_id,decision)
        VALUES(:owner,:posting,:candidate,:decision) ON CONFLICT(owner_id,posting_id,candidate_id)
        DO UPDATE SET decision=EXCLUDED.decision,version=posting_duplicate_decisions.version+1,
        updated_at=clock_timestamp()"""),
        {"owner": owner_id, "posting": left, "candidate": right, "decision": decision},
    )
    # The canonical review belongs to both aggregates, regardless of the URL anchor.
    # command() already serializes the owner's writers before checking either ETag.
    conn.execute(
        text("""UPDATE offerloom.job_postings SET version=version+1,updated_at=clock_timestamp()
        WHERE owner_id=:owner AND id IN (:posting,:candidate)"""),
        {"owner": owner_id, "posting": left, "candidate": right},
    )
    return get_posting(conn, owner_id, posting_id)
