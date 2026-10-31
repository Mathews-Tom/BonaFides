# SPDX-License-Identifier: MIT OR Apache-2.0
"""Normalized owner entries; profile-wide preconditions fence every accepted edit."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Annotated, Literal, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Response
from pydantic import Field, HttpUrl, ValidationInfo, field_validator
from sqlalchemy import Connection, text
from sqlalchemy.engine import RowMapping

from bonafides.auth import Owner, StrictModel, require_owner
from bonafides.claims import (
    STRUCTURED_KINDS,
    RenderLanguage,
    StructuredClaim,
    claim_text,
    date_bounds,
    needs_attention,
    render_claim,
    vocabulary,
)
from bonafides.commands import command
from bonafides.db import Database
from bonafides.evidence_provenance import EvidenceProvenance, provenance
from bonafides.problems import Problem
from bonafides.profiles import ProfileSnapshot, _save, canonical, content_hash

ClaimKind = Literal[
    "identity",
    "experience",
    "education",
    "skill",
    "project",
    "certification",
    "preference",
    "other",
    "language",
]


def _covered_days(intervals: list[tuple[int, int]]) -> int:
    total, last_end = 0, 0
    for start, end in sorted(intervals):
        if start <= end:
            total += max(0, end - max(start, last_end + 1) + 1)
            last_end = max(last_end, end)
    return total


def experience_coverage(claims: Sequence[Mapping[str, object]]) -> dict[str, int]:
    """Conservative calendar-day bounds, not verified employment or seniority."""
    minimum: list[tuple[int, int]] = []
    maximum: list[tuple[int, int]] = []
    # An entry approved as one claim per line shares its context; count entries, not lines.
    unbounded: set[object] = set()
    for claim in claims:
        if claim["kind"] != "experience":
            continue
        revision = cast(Mapping[str, object], claim["revision"])
        payload = cast(Mapping[str, object], revision["payload"])
        start, end = payload.get("start_date"), payload.get("end_date")
        if not isinstance(start, str) or not isinstance(end, str):
            context = tuple(payload.get(field) for field in ("organization", "role", "start_date"))
            unbounded.add(context if any(context[:2]) else claim["id"])
            continue
        start_min, start_max = date_bounds(start)
        end_min, end_max = date_bounds(end)
        minimum.append((start_max.toordinal(), end_min.toordinal()))
        maximum.append((start_min.toordinal(), end_max.toordinal()))
    return {
        "minimum_days": _covered_days(minimum),
        "maximum_days": _covered_days(maximum),
        "undated_or_open_entries": len(unbounded),
    }


class ClaimContext(StrictModel):
    organization: str | None = Field(default=None, max_length=200)
    role: str | None = Field(default=None, max_length=200)
    start_date: str | None = Field(default=None, max_length=10)
    end_date: str | None = Field(default=None, max_length=10)
    proficiency: str | None = Field(default=None, max_length=120)
    project_url: str | None = Field(default=None, max_length=2048)

    @field_validator("project_url")
    @classmethod
    def syntactic_project_url(cls, value: str | None) -> str | None:
        if value is not None:
            if (
                any(character.isspace() or ord(character) < 32 for character in value)
                or "\\" in value
            ):
                raise ValueError("invalid_project_url")
            parsed = HttpUrl(value)
            if parsed.username is not None or parsed.password is not None:
                raise ValueError("invalid_project_url")
        return value

    @field_validator("start_date", "end_date")
    @classmethod
    def valid_date(cls, value: str | None) -> str | None:
        if value is not None:
            date_bounds(value)
        return value

    @field_validator("end_date")
    @classmethod
    def ordered_interval(cls, value: str | None, info: ValidationInfo) -> str | None:
        start = info.data.get("start_date")
        if (
            isinstance(start, str)
            and value is not None
            and date_bounds(start)[0] > date_bounds(value)[1]
        ):
            raise ValueError("invalid_interval")
        return value


class ClaimPayload(ClaimContext):
    text: str = Field(min_length=1, max_length=4000)

    @field_validator("text")
    @classmethod
    def nonblank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("blank_text")
        return value


class StructuredClaimPayload(ClaimContext):
    """Claim schema v1: the stored statement is its structure, never a sentence."""

    claim: StructuredClaim


class EvidencePayload(StrictModel):
    text: str = Field(min_length=1, max_length=8000)
    source: str = Field(min_length=1, max_length=500)

    @field_validator("text", "source")
    @classmethod
    def nonblank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("blank_text")
        return value


class EvidenceLink(StrictModel):
    evidence_revision_id: UUID
    support_kind: Literal["direct", "user_asserted", "context"]
    excerpt: str = Field(min_length=1, max_length=4000)


class ClaimWrite(StrictModel):
    kind: ClaimKind
    payload: StructuredClaimPayload | ClaimPayload
    evidence_links: list[EvidenceLink] = Field(min_length=1, max_length=16)

    @field_validator("payload")
    @classmethod
    def structured_kind(
        cls, value: StructuredClaimPayload | ClaimPayload, info: ValidationInfo
    ) -> StructuredClaimPayload | ClaimPayload:
        if isinstance(value, StructuredClaimPayload) and info.data.get("kind") not in (
            STRUCTURED_KINDS
        ):
            raise ValueError("structured_claim_kind")
        return value


class EvidenceWrite(StrictModel):
    payload: EvidencePayload
    provenance: EvidenceProvenance


class RenderRequest(StrictModel):
    claim: StructuredClaim
    language: RenderLanguage = "en"


class RestoreProfile(StrictModel):
    revision_id: UUID


def _revision(row: RowMapping, owner_id: UUID) -> dict[str, object]:
    created_at = cast(datetime, row["created_at"])
    return {
        "id": str(row["id"]),
        "revision_no": row["revision_no"],
        "payload": row["payload"],
        "content_sha256": row["content_sha256"],
        "created_at": created_at.isoformat(),
        "author_owner_id": str(owner_id),
    }


def evidence_revision(conn: Connection, owner_id: UUID, revision_id: UUID) -> dict[str, object]:
    row = (
        conn.execute(
            text("SELECT * FROM bonafides.evidence_revisions WHERE owner_id=:owner AND id=:id"),
            {"owner": owner_id, "id": revision_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise Problem(404, "evidence_revision_not_found")
    return {**_revision(row, owner_id), "provenance": provenance(row)}


def hydrate_claim(
    conn: Connection, owner_id: UUID, revision_id: UUID, *, historical: bool = False
) -> dict[str, object]:
    row = (
        conn.execute(
            text("""SELECT r.*,c.id AS entity_id,c.kind,c.version,c.archived_at,c.current_revision_id
        FROM bonafides.claim_revisions r JOIN bonafides.professional_claims c
        ON c.owner_id=r.owner_id AND c.id=r.claim_id WHERE r.owner_id=:owner AND r.id=:id"""),
            {"owner": owner_id, "id": revision_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise Problem(404, "claim_revision_not_found")
    links = (
        conn.execute(
            text("""SELECT evidence_revision_id,support_kind,excerpt FROM bonafides.claim_evidence_links
        WHERE owner_id=:owner AND claim_revision_id=:id ORDER BY evidence_revision_id"""),
            {"owner": owner_id, "id": revision_id},
        )
        .mappings()
        .all()
    )
    revision = _revision(row, owner_id)
    revision["evidence_links"] = [
        {
            "evidence_revision_id": str(link["evidence_revision_id"]),
            "support_kind": link["support_kind"],
            "excerpt": link["excerpt"],
            "evidence": evidence_revision(conn, owner_id, link["evidence_revision_id"]),
        }
        for link in links
    ]
    payload = cast(dict[str, object], row["payload"])
    result: dict[str, object] = {
        "id": str(row["entity_id"]),
        "kind": row["kind"],
        "revision": revision,
        "rendering": claim_text(payload),
        "needs_attention": needs_attention(str(row["kind"]), payload),
    }
    if not historical:
        result.update(
            {
                "version": row["version"],
                "archived": row["archived_at"] is not None,
                "current_revision_id": str(row["current_revision_id"]),
            }
        )
    return result


def hydrate_claims(
    conn: Connection, owner_id: UUID, ids: list[str], *, historical: bool = False
) -> list[dict[str, object]]:
    return [hydrate_claim(conn, owner_id, UUID(str(value)), historical=historical) for value in ids]


def active_evidence(conn: Connection, owner_id: UUID) -> list[dict[str, object]]:
    rows = (
        conn.execute(
            text(
                "SELECT * FROM bonafides.evidence_records WHERE owner_id=:owner AND archived_at IS NULL ORDER BY created_at,id"
            ),
            {"owner": owner_id},
        )
        .mappings()
        .all()
    )
    return [
        {
            "id": str(row["id"]),
            "version": row["version"],
            "archived": False,
            "current_revision_id": str(row["current_revision_id"]),
            "revision": evidence_revision(conn, owner_id, row["current_revision_id"]),
        }
        for row in rows
    ]


def _snapshot(conn: Connection, owner_id: UUID) -> ProfileSnapshot:
    value = conn.execute(
        text("""SELECT r.snapshot FROM bonafides.profiles p JOIN bonafides.profile_revisions r
        ON r.owner_id=p.owner_id AND r.id=p.current_revision_id WHERE p.owner_id=:owner"""),
        {"owner": owner_id},
    ).scalar_one()
    return ProfileSnapshot.model_validate(value)


def _active_record(
    conn: Connection, owner_id: UUID, entity_id: UUID, kind: Literal["claim", "evidence"]
) -> RowMapping:
    table = "professional_claims" if kind == "claim" else "evidence_records"
    row = (
        conn.execute(
            text(f"SELECT * FROM bonafides.{table} WHERE owner_id=:owner AND id=:id FOR UPDATE"),
            {"owner": owner_id, "id": entity_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise Problem(404, f"{kind}_not_found")
    if row["archived_at"] is not None:
        raise Problem(409, f"{kind}_archived")
    return row


def write_evidence(
    conn: Connection, owner_id: UUID, body: EvidenceWrite, entity_id: UUID | None
) -> dict[str, object]:
    creating = entity_id is None
    if entity_id is None:
        count = conn.execute(
            text(
                "SELECT count(*) FROM bonafides.evidence_records WHERE owner_id=:owner AND archived_at IS NULL"
            ),
            {"owner": owner_id},
        ).scalar_one()
        if count >= 200:
            raise Problem(422, "evidence_limit")
        entity_id = uuid4()
        conn.execute(
            text("INSERT INTO bonafides.evidence_records(owner_id,id) VALUES(:owner,:id)"),
            {"owner": owner_id, "id": entity_id},
        )
    else:
        _active_record(conn, owner_id, entity_id, "evidence")
    revision_id = uuid4()
    payload = body.payload.model_dump(mode="json")
    conn.execute(
        text("""INSERT INTO bonafides.evidence_revisions(owner_id,id,evidence_id,revision_no,payload,content_sha256,
        source_type,origin,privacy_class,employer_owned)
        SELECT :owner,:revision,:id,COALESCE(max(revision_no),0)+1,CAST(:payload AS jsonb),:sha,
        :source_type,'owner_entry',:privacy_class,:employer_owned
        FROM bonafides.evidence_revisions WHERE owner_id=:owner AND evidence_id=:id"""),
        {
            "owner": owner_id,
            "revision": revision_id,
            "id": entity_id,
            "payload": canonical(payload),
            "sha": content_hash(payload),
            **body.provenance.model_dump(),
        },
    )
    conn.execute(
        text(
            "UPDATE bonafides.evidence_records SET current_revision_id=:revision,version=version+:increment WHERE owner_id=:owner AND id=:id"
        ),
        {
            "owner": owner_id,
            "id": entity_id,
            "revision": revision_id,
            "increment": 0 if creating else 1,
        },
    )
    result = _save(conn, owner_id, _snapshot(conn, owner_id), "evidence_saved")
    result["changed_evidence_id"] = str(entity_id)
    return result


def append_claim_revision(
    conn: Connection,
    owner_id: UUID,
    entity_id: UUID,
    payload: dict[str, object],
    evidence_links: Sequence[EvidenceLink],
    *,
    increment: int,
) -> UUID:
    """Append an immutable claim revision with its evidence links and make it current."""
    revision_id = uuid4()
    conn.execute(
        text("""INSERT INTO bonafides.claim_revisions(owner_id,id,claim_id,revision_no,payload,content_sha256)
        SELECT :owner,:revision,:id,COALESCE(max(revision_no),0)+1,CAST(:payload AS jsonb),:sha
        FROM bonafides.claim_revisions WHERE owner_id=:owner AND claim_id=:id"""),
        {
            "owner": owner_id,
            "revision": revision_id,
            "id": entity_id,
            "payload": canonical(payload),
            "sha": content_hash(payload),
        },
    )
    for link in evidence_links:
        conn.execute(
            text("""INSERT INTO bonafides.claim_evidence_links(owner_id,claim_revision_id,evidence_revision_id,support_kind,excerpt)
            VALUES(:owner,:claim,:evidence,:support,:excerpt)"""),
            {
                "owner": owner_id,
                "claim": revision_id,
                "evidence": link.evidence_revision_id,
                "support": link.support_kind,
                "excerpt": link.excerpt,
            },
        )
    conn.execute(
        text(
            "UPDATE bonafides.professional_claims SET current_revision_id=:revision,version=version+:increment WHERE owner_id=:owner AND id=:id"
        ),
        {
            "owner": owner_id,
            "id": entity_id,
            "revision": revision_id,
            "increment": increment,
        },
    )
    return revision_id


def write_claim(
    conn: Connection, owner_id: UUID, body: ClaimWrite, entity_id: UUID | None
) -> dict[str, object]:
    creating = entity_id is None
    snapshot = _snapshot(conn, owner_id)
    evidence_ids = [link.evidence_revision_id for link in body.evidence_links]
    if len(set(evidence_ids)) != len(evidence_ids):
        raise Problem(422, "duplicate_evidence_revision")
    for link in body.evidence_links:
        valid = conn.execute(
            text("""SELECT r.id FROM bonafides.evidence_revisions r JOIN bonafides.evidence_records e
            ON e.owner_id=r.owner_id AND e.id=r.evidence_id WHERE r.owner_id=:owner AND r.id=:id AND e.archived_at IS NULL"""),
            {"owner": owner_id, "id": link.evidence_revision_id},
        ).scalar_one_or_none()
        if valid is None:
            raise Problem(422, "invalid_evidence_revision")
    previous = None
    if entity_id is None:
        count = conn.execute(
            text(
                "SELECT count(*) FROM bonafides.professional_claims WHERE owner_id=:owner AND archived_at IS NULL"
            ),
            {"owner": owner_id},
        ).scalar_one()
        if count >= 100:
            raise Problem(422, "claim_limit")
        entity_id = uuid4()
        conn.execute(
            text(
                "INSERT INTO bonafides.professional_claims(owner_id,id,profile_id,kind) SELECT :owner,:id,id,:kind FROM bonafides.profiles WHERE owner_id=:owner"
            ),
            {"owner": owner_id, "id": entity_id, "kind": body.kind},
        )
    else:
        current = _active_record(conn, owner_id, entity_id, "claim")
        if current["kind"] != body.kind:
            raise Problem(422, "claim_kind_immutable")
        previous = current["current_revision_id"]
    revision_id = append_claim_revision(
        conn,
        owner_id,
        entity_id,
        body.payload.model_dump(mode="json", exclude_none=True),
        body.evidence_links,
        increment=0 if creating else 1,
    )
    ids = [revision_id if item == previous else item for item in snapshot.claim_revision_ids]
    if revision_id not in ids:
        ids.append(revision_id)
    result = _save(
        conn, owner_id, snapshot.model_copy(update={"claim_revision_ids": ids}), "claim_saved"
    )
    result["changed_claim_id"] = str(entity_id)
    return result


def archive_entry(
    conn: Connection, owner_id: UUID, entity_id: UUID, kind: Literal["claim", "evidence"]
) -> dict[str, object]:
    record = _active_record(conn, owner_id, entity_id, kind)
    table = "professional_claims" if kind == "claim" else "evidence_records"
    conn.execute(
        text(
            f"UPDATE bonafides.{table} SET archived_at=clock_timestamp(),version=version+1 WHERE owner_id=:owner AND id=:id"
        ),
        {"owner": owner_id, "id": entity_id},
    )
    snapshot = _snapshot(conn, owner_id)
    if kind == "claim":
        snapshot = snapshot.model_copy(
            update={
                "claim_revision_ids": [
                    item
                    for item in snapshot.claim_revision_ids
                    if item != record["current_revision_id"]
                ]
            }
        )
    result = _save(conn, owner_id, snapshot, f"{kind}_archived")
    result[f"changed_{kind}_id"] = str(entity_id)
    return result


def profile_revision(
    conn: Connection, owner_id: UUID, revision_id: UUID, *, include_claims: bool = True
) -> dict[str, object]:
    row = (
        conn.execute(
            text("SELECT * FROM bonafides.profile_revisions WHERE owner_id=:owner AND id=:id"),
            {"owner": owner_id, "id": revision_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise Problem(404, "profile_revision_not_found")
    result: dict[str, object] = {
        "id": str(row["id"]),
        "revision_no": row["revision_no"],
        "snapshot": row["snapshot"],
        "content_sha256": row["content_sha256"],
        "created_at": row["created_at"].isoformat(),
        "reason": row["reason"],
        "author_owner_id": str(owner_id),
    }
    if include_claims:
        result["claims"] = hydrate_claims(
            conn, owner_id, row["snapshot"].get("claim_revision_ids", []), historical=True
        )
        ids = row["snapshot"].get("evidence_revision_ids", [])
        evidence_rows = (
            conn.execute(
                text(
                    "SELECT id,evidence_id FROM bonafides.evidence_revisions WHERE owner_id=:owner AND id=ANY(CAST(:ids AS uuid[]))"
                ),
                {"owner": owner_id, "ids": ids},
            )
            .mappings()
            .all()
        )
        by_revision = {str(item["id"]): item["evidence_id"] for item in evidence_rows}
        result["evidence"] = [
            {
                "id": str(by_revision[str(item)]),
                "revision": evidence_revision(conn, owner_id, UUID(str(item))),
            }
            for item in ids
        ]
    return result


def restore_profile(conn: Connection, owner_id: UUID, revision_id: UUID) -> dict[str, object]:
    historical = profile_revision(conn, owner_id, revision_id)
    snapshot = ProfileSnapshot.model_validate(historical["snapshot"])
    # Only this explicit command can reactivate archived entries. Ordinary writes cannot.
    selected = snapshot.claim_revision_ids
    conn.execute(
        text("""UPDATE bonafides.professional_claims SET archived_at=clock_timestamp(),version=version+1
        WHERE owner_id=:owner AND archived_at IS NULL
        AND id NOT IN (SELECT claim_id FROM bonafides.claim_revisions WHERE owner_id=:owner AND id=ANY(CAST(:selected AS uuid[])))"""),
        {"owner": owner_id, "selected": selected},
    )
    for claim_revision_id in selected:
        conn.execute(
            text("""UPDATE bonafides.professional_claims c SET archived_at=NULL,current_revision_id=r.id,version=c.version+1
        FROM bonafides.claim_revisions r WHERE r.owner_id=:owner AND r.id=:id AND c.owner_id=r.owner_id AND c.id=r.claim_id"""),
            {"owner": owner_id, "id": claim_revision_id},
        )
    raw_snapshot = cast(dict[str, object], historical["snapshot"])
    evidence_ids = snapshot.evidence_revision_ids
    if "evidence_revision_ids" not in raw_snapshot:
        # Snapshots saved before the evidence editor carry no editor state; preserve their exact pins.
        evidence_ids = list(
            conn.execute(
                text("""SELECT DISTINCT ON(r.evidence_id) r.id
            FROM bonafides.evidence_revisions r JOIN bonafides.claim_evidence_links l
            ON l.owner_id=r.owner_id AND l.evidence_revision_id=r.id
            WHERE l.owner_id=:owner AND l.claim_revision_id=ANY(CAST(:claims AS uuid[]))
            ORDER BY r.evidence_id,r.revision_no DESC"""),
                {"owner": owner_id, "claims": selected},
            ).scalars()
        )
    conn.execute(
        text("""UPDATE bonafides.evidence_records SET archived_at=clock_timestamp(),version=version+1
        WHERE owner_id=:owner AND archived_at IS NULL AND id NOT IN
        (SELECT evidence_id FROM bonafides.evidence_revisions WHERE owner_id=:owner AND id=ANY(CAST(:ids AS uuid[])))"""),
        {"owner": owner_id, "ids": evidence_ids},
    )
    for evidence_id in evidence_ids:
        conn.execute(
            text("""UPDATE bonafides.evidence_records e SET archived_at=NULL,current_revision_id=r.id,version=e.version+1
            FROM bonafides.evidence_revisions r WHERE r.owner_id=:owner AND r.id=:id
            AND e.owner_id=r.owner_id AND e.id=r.evidence_id"""),
            {"owner": owner_id, "id": evidence_id},
        )
    return _save(conn, owner_id, snapshot, "owner_restore", restoring=True)


def build_entry_router(db: Database) -> APIRouter:
    router = APIRouter()

    def mutate(
        owner_id: UUID,
        method: str,
        route: str,
        path: dict[str, str],
        body: dict[str, object],
        if_match: str | None,
        key: str | None,
        response: Response,
        effect: Callable[[Connection], dict[str, object]],
    ) -> dict[str, object]:
        with db.transaction() as conn:
            profile_id = conn.execute(
                text("SELECT id FROM bonafides.profiles WHERE owner_id=:owner"), {"owner": owner_id}
            ).scalar_one()
            status, result = command(
                conn,
                owner_id,
                method,
                route,
                path,
                body,
                if_match,
                key,
                "profile",
                profile_id,
                lambda connection: (200, effect(connection)),
            )
        response.status_code = status
        response.headers["ETag"] = f'"{result["version"]}"'
        return result

    @router.get("/claims/vocabulary")
    def claim_vocabulary(
        owner: Annotated[Owner, Depends(require_owner)], language: RenderLanguage = "en"
    ) -> dict[str, object]:
        return vocabulary(language)

    @router.post("/claims/render")
    def render(
        body: RenderRequest, owner: Annotated[Owner, Depends(require_owner)]
    ) -> dict[str, object]:
        # Side-effect free preview; the stored claim is rendered by the same function on read.
        return {"language": body.language, "rendering": render_claim(body.claim, body.language)}

    @router.get("/claims")
    def claims(owner: Annotated[Owner, Depends(require_owner)]) -> dict[str, object]:
        with db.transaction() as conn:
            ids = (
                conn.execute(
                    text(
                        "SELECT current_revision_id FROM bonafides.professional_claims WHERE owner_id=:owner AND archived_at IS NULL ORDER BY created_at,id"
                    ),
                    {"owner": owner.id},
                )
                .scalars()
                .all()
            )
            return {"items": [hydrate_claim(conn, owner.id, item) for item in ids]}

    @router.get("/evidence")
    def evidence(owner: Annotated[Owner, Depends(require_owner)]) -> dict[str, object]:
        with db.transaction() as conn:
            return {"items": active_evidence(conn, owner.id)}

    @router.get("/profile/revisions")
    def history(
        owner: Annotated[Owner, Depends(require_owner)], before: int | None = None
    ) -> dict[str, object]:
        if before is not None and not 1 <= before <= 9223372036854775807:
            raise Problem(422, "invalid_revision_cursor")
        with db.transaction() as conn:
            ids = (
                conn.execute(
                    text(
                        "SELECT id FROM bonafides.profile_revisions WHERE owner_id=:owner AND (CAST(:before AS bigint) IS NULL OR revision_no<CAST(:before AS bigint)) ORDER BY revision_no DESC LIMIT 101"
                    ),
                    {"owner": owner.id, "before": before},
                )
                .scalars()
                .all()
            )
            items = [
                profile_revision(conn, owner.id, item, include_claims=False) for item in ids[:100]
            ]
            return {
                "items": items,
                "next_cursor": items[-1]["revision_no"] if len(ids) > 100 else None,
            }

    @router.get("/profile/revisions/{revision_id}")
    def read_profile_revision(
        revision_id: UUID, owner: Annotated[Owner, Depends(require_owner)]
    ) -> dict[str, object]:
        with db.transaction() as conn:
            return profile_revision(conn, owner.id, revision_id)

    @router.get("/claims/{entity_id}/revisions/{revision_id}")
    def read_claim_revision(
        entity_id: UUID, revision_id: UUID, owner: Annotated[Owner, Depends(require_owner)]
    ) -> dict[str, object]:
        with db.transaction() as conn:
            result = hydrate_claim(conn, owner.id, revision_id, historical=True)
            if result["id"] != str(entity_id):
                raise Problem(404, "claim_revision_not_found")
            revision = cast(dict[str, object], result["revision"])
            return {**revision, "claim_id": str(entity_id), "kind": result["kind"]}

    @router.get("/evidence/{entity_id}/revisions/{revision_id}")
    def read_evidence_revision(
        entity_id: UUID, revision_id: UUID, owner: Annotated[Owner, Depends(require_owner)]
    ) -> dict[str, object]:
        with db.transaction() as conn:
            match = conn.execute(
                text(
                    "SELECT id FROM bonafides.evidence_revisions WHERE owner_id=:owner AND id=:revision AND evidence_id=:id"
                ),
                {"owner": owner.id, "revision": revision_id, "id": entity_id},
            ).scalar_one_or_none()
            if match is None:
                raise Problem(404, "evidence_revision_not_found")
            return evidence_revision(conn, owner.id, revision_id)

    @router.post("/claims")
    def create_claim(
        body: ClaimWrite,
        response: Response,
        owner: Annotated[Owner, Depends(require_owner)],
        if_match: Annotated[str | None, Header()] = None,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        return mutate(
            owner.id,
            "POST",
            "/api/v1/claims",
            {},
            body.model_dump(mode="json", exclude_none=True),
            if_match,
            idempotency_key,
            response,
            lambda conn: write_claim(conn, owner.id, body, None),
        )

    @router.put("/claims/{entity_id}")
    def update_claim(
        entity_id: UUID,
        body: ClaimWrite,
        response: Response,
        owner: Annotated[Owner, Depends(require_owner)],
        if_match: Annotated[str | None, Header()] = None,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        return mutate(
            owner.id,
            "PUT",
            "/api/v1/claims/{entity_id}",
            {"entity_id": str(entity_id)},
            body.model_dump(mode="json", exclude_none=True),
            if_match,
            idempotency_key,
            response,
            lambda conn: write_claim(conn, owner.id, body, entity_id),
        )

    @router.delete("/claims/{entity_id}")
    def delete_claim(
        entity_id: UUID,
        response: Response,
        owner: Annotated[Owner, Depends(require_owner)],
        if_match: Annotated[str | None, Header()] = None,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        return mutate(
            owner.id,
            "DELETE",
            "/api/v1/claims/{entity_id}",
            {"entity_id": str(entity_id)},
            {},
            if_match,
            idempotency_key,
            response,
            lambda conn: archive_entry(conn, owner.id, entity_id, "claim"),
        )

    @router.post("/evidence")
    def create_evidence(
        body: EvidenceWrite,
        response: Response,
        owner: Annotated[Owner, Depends(require_owner)],
        if_match: Annotated[str | None, Header()] = None,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        return mutate(
            owner.id,
            "POST",
            "/api/v1/evidence",
            {},
            body.model_dump(mode="json"),
            if_match,
            idempotency_key,
            response,
            lambda conn: write_evidence(conn, owner.id, body, None),
        )

    @router.put("/evidence/{entity_id}")
    def update_evidence(
        entity_id: UUID,
        body: EvidenceWrite,
        response: Response,
        owner: Annotated[Owner, Depends(require_owner)],
        if_match: Annotated[str | None, Header()] = None,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        return mutate(
            owner.id,
            "PUT",
            "/api/v1/evidence/{entity_id}",
            {"entity_id": str(entity_id)},
            body.model_dump(mode="json"),
            if_match,
            idempotency_key,
            response,
            lambda conn: write_evidence(conn, owner.id, body, entity_id),
        )

    @router.delete("/evidence/{entity_id}")
    def delete_evidence(
        entity_id: UUID,
        response: Response,
        owner: Annotated[Owner, Depends(require_owner)],
        if_match: Annotated[str | None, Header()] = None,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        return mutate(
            owner.id,
            "DELETE",
            "/api/v1/evidence/{entity_id}",
            {"entity_id": str(entity_id)},
            {},
            if_match,
            idempotency_key,
            response,
            lambda conn: archive_entry(conn, owner.id, entity_id, "evidence"),
        )

    @router.post("/profile/restore")
    def restore(
        body: RestoreProfile,
        response: Response,
        owner: Annotated[Owner, Depends(require_owner)],
        if_match: Annotated[str | None, Header()] = None,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        return mutate(
            owner.id,
            "POST",
            "/api/v1/profile/restore",
            {},
            body.model_dump(mode="json"),
            if_match,
            idempotency_key,
            response,
            lambda conn: restore_profile(conn, owner.id, body.revision_id),
        )

    return router
