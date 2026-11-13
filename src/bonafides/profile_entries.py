# SPDX-License-Identifier: MIT OR Apache-2.0
"""Normalized owner entries; profile-wide preconditions fence every accepted edit."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Annotated, Literal, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Response
from pydantic import Field, HttpUrl, ValidationInfo, field_validator, model_validator
from sqlalchemy import Connection, text
from sqlalchemy.engine import RowMapping

from bonafides.auth import Owner, StrictModel, require_owner
from bonafides.claims import (
    STRUCTURED_KINDS,
    RenderLanguage,
    StructuredClaim,
    claim_text,
    date_bounds,
    literal_grounded,
    needs_attention,
    render_claim,
    vocabulary,
)
from bonafides.commands import command
from bonafides.db import Database
from bonafides.evidence_provenance import (
    EvidenceClassification,
    EvidenceProvenance,
    InitialEvidenceProvenance,
    provenance,
)
from bonafides.model_context import lock_evidence_write
from bonafides.problems import Problem
from bonafides.profile_breadth import citation_text, normalize_citation_url, snapshot_claim_ids
from bonafides.profiles import (
    ProfileSnapshot,
    _save,
    canonical,
    content_hash,
    validate_writing_scope,
)

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
    "publication",
    "writing_fact",
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
    citation_markup: str | None = Field(
        default=None, max_length=3000, exclude_if=lambda value: value is None
    )
    citation_url: str | None = Field(
        default=None, max_length=500, exclude_if=lambda value: value is None
    )
    include_in_resume: bool | None = Field(default=None, exclude_if=lambda value: value is None)

    @field_validator("citation_url")
    @classmethod
    def safe_citation_url(cls, value: str | None) -> str | None:
        return normalize_citation_url(value) if value is not None else None

    @field_validator("text")
    @classmethod
    def nonblank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("blank_text")
        return value


class StructuredClaimPayload(ClaimContext):
    """Claim schema v1: the stored statement is its structure, never a sentence."""

    claim: StructuredClaim


class WritingFactPayload(StrictModel):
    text: str = Field(default="", max_length=1000)
    application_id: UUID | None = None


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
    payload: StructuredClaimPayload | ClaimPayload | WritingFactPayload
    evidence_links: list[EvidenceLink] = Field(default_factory=list, max_length=16)

    @field_validator("payload", mode="before")
    @classmethod
    def typed_payload(cls, value: object, info: ValidationInfo) -> object:
        if isinstance(value, dict):
            if info.data.get("kind") == "writing_fact":
                return WritingFactPayload.model_validate(value)
            if "claim" in value:
                return StructuredClaimPayload.model_validate(value)
            return ClaimPayload.model_validate(value)
        return value

    @model_validator(mode="after")
    def valid_entry(self) -> ClaimWrite:
        payload = self.payload
        if isinstance(payload, WritingFactPayload) != (self.kind == "writing_fact"):
            raise ValueError("invalid_writing_payload")
        if isinstance(payload, StructuredClaimPayload) and self.kind not in STRUCTURED_KINDS:
            raise ValueError("structured_claim_kind")
        if not self.evidence_links and not (
            isinstance(payload, WritingFactPayload) and not payload.text.strip()
        ):
            raise ValueError("evidence_required")
        if isinstance(payload, ClaimPayload):
            if self.kind == "publication":
                if len(payload.text) > 3000:
                    raise ValueError("citation_length")
                if (
                    payload.citation_markup is not None
                    and citation_text(payload.citation_markup) != payload.text
                ):
                    raise ValueError("citation_text_mismatch")
            elif payload.model_fields_set & {
                "citation_markup",
                "citation_url",
                "include_in_resume",
            }:
                raise ValueError("publication_metadata")
        return self


class EvidenceWrite(StrictModel):
    payload: EvidencePayload
    provenance: EvidenceProvenance


class EvidenceCreate(StrictModel):
    payload: EvidencePayload
    provenance: InitialEvidenceProvenance


class RenderRequest(StrictModel):
    claim: StructuredClaim
    language: RenderLanguage = "en"


class RestoreProfile(StrictModel):
    revision_id: UUID


SectionKind = Literal["experience", "project", "education", "publication"]


class SectionOrder(StrictModel):
    revision_ids: list[UUID] = Field(max_length=100)
    mode: Literal["manual", "date"]


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
            text("""SELECT r.*,e.privacy_class,e.employer_owned
            FROM bonafides.evidence_revisions r JOIN bonafides.evidence_records e
            ON e.owner_id=r.owner_id AND e.id=r.evidence_id
            WHERE r.owner_id=:owner AND r.id=:id"""),
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
            text("""SELECT l.evidence_revision_id,l.support_kind,l.excerpt,r.payload
        FROM bonafides.claim_evidence_links l JOIN bonafides.evidence_revisions r
        ON r.owner_id=l.owner_id AND r.id=l.evidence_revision_id
        WHERE l.owner_id=:owner AND l.claim_revision_id=:id ORDER BY l.evidence_revision_id"""),
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
    grounding_issue = (
        "literal_claim_not_grounded"
        if payload.get("claim") is None
        and claim_text(payload).strip()
        and not any(
            link["support_kind"] in ("direct", "user_asserted")
            and literal_grounded(
                claim_text(payload), link["excerpt"], link["payload"].get("text", "")
            )
            for link in links
        )
        else None
    )
    result: dict[str, object] = {
        "id": str(row["entity_id"]),
        "kind": row["kind"],
        "revision": revision,
        "rendering": claim_text(payload),
        "grounding_issue": grounding_issue,
        "needs_attention": needs_attention(str(row["kind"]), payload)
        or grounding_issue is not None,
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
    conn: Connection,
    owner_id: UUID,
    body: EvidenceWrite | EvidenceCreate,
    entity_id: UUID | None,
    *,
    origin: Literal["owner_entry", "evidence_capture"] = "owner_entry",
) -> dict[str, object]:
    if origin == "evidence_capture" and entity_id is not None:
        raise ValueError("captured origin is only valid for new evidence")
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
            text("""INSERT INTO bonafides.evidence_records(owner_id,id,privacy_class,employer_owned)
            VALUES(:owner,:id,:privacy_class,:employer_owned)"""),
            {
                "owner": owner_id,
                "id": entity_id,
                **cast(EvidenceCreate, body).provenance.model_dump(),
            },
        )
        append_classification(
            conn,
            owner_id,
            entity_id,
            EvidenceClassification.model_validate(
                cast(EvidenceCreate, body).provenance.model_dump(exclude={"source_type"})
            ),
        )
    else:
        _active_record(conn, owner_id, entity_id, "evidence")
    revision_id = uuid4()
    payload = body.payload.model_dump(mode="json")
    conn.execute(
        text("""INSERT INTO bonafides.evidence_revisions(owner_id,id,evidence_id,revision_no,payload,content_sha256,
        source_type,origin)
        SELECT :owner,:revision,:id,COALESCE(max(revision_no),0)+1,CAST(:payload AS jsonb),:sha,
        :source_type,:origin
        FROM bonafides.evidence_revisions WHERE owner_id=:owner AND evidence_id=:id"""),
        {
            "owner": owner_id,
            "revision": revision_id,
            "id": entity_id,
            "payload": canonical(payload),
            "sha": content_hash(payload),
            "origin": origin,
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


def append_classification(
    conn: Connection, owner_id: UUID, entity_id: UUID, body: EvidenceClassification
) -> None:
    # Callers hold the owner evidence advisory lock and record/profile write locks.
    conn.execute(
        text("""INSERT INTO bonafides.evidence_classification_changes
        (owner_id,evidence_id,sequence,privacy_class,employer_owned)
        SELECT :owner,:id,COALESCE(max(sequence),0)+1,:privacy_class,:employer_owned
        FROM bonafides.evidence_classification_changes
        WHERE owner_id=:owner AND evidence_id=:id"""),
        {"owner": owner_id, "id": entity_id, **body.model_dump()},
    )


def classify_evidence(
    conn: Connection, owner_id: UUID, entity_id: UUID, body: EvidenceClassification
) -> dict[str, object]:
    _active_record(conn, owner_id, entity_id, "evidence")
    conn.execute(
        text("""UPDATE bonafides.evidence_records
        SET privacy_class=:privacy_class,employer_owned=:employer_owned,version=version+1
        WHERE owner_id=:owner AND id=:id"""),
        {"owner": owner_id, "id": entity_id, **body.model_dump()},
    )
    append_classification(conn, owner_id, entity_id, body)
    result = _save(conn, owner_id, _snapshot(conn, owner_id), "evidence_classified")
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


def ensure_claim_capacity(conn: Connection, owner_id: UUID, kind: str, scope: str) -> None:
    count = conn.execute(
        text("""SELECT count(*) FROM bonafides.professional_claims c
        JOIN bonafides.claim_revisions r ON r.owner_id=c.owner_id AND r.id=c.current_revision_id
        WHERE c.owner_id=:owner AND c.archived_at IS NULL AND
        CASE WHEN :writing THEN c.kind='writing_fact' AND COALESCE(r.payload->>'application_id','personal')=:scope
        ELSE c.kind<>'writing_fact' END"""),
        {"owner": owner_id, "writing": kind == "writing_fact", "scope": scope},
    ).scalar_one()
    if count >= (50 if kind == "writing_fact" else 100):
        raise Problem(422, "writing_fact_limit" if kind == "writing_fact" else "claim_limit")


def section_ids(
    conn: Connection, owner_id: UUID, snapshot: ProfileSnapshot, kind: str
) -> list[UUID]:
    members = set(
        conn.execute(
            text("""SELECT r.id FROM bonafides.claim_revisions r
        JOIN bonafides.professional_claims c ON c.owner_id=r.owner_id AND c.id=r.claim_id
        WHERE r.owner_id=:owner AND r.id=ANY(CAST(:ids AS uuid[])) AND c.kind=:kind"""),
            {"owner": owner_id, "ids": snapshot.claim_revision_ids, "kind": kind},
        ).scalars()
    )
    return [item for item in snapshot.claim_revision_ids if item in members]


def apply_date_order(
    conn: Connection, owner_id: UUID, snapshot: ProfileSnapshot, kind: str
) -> None:
    if (
        snapshot.ordering_modes.get(cast(Literal["experience", "project", "education"], kind))
        != "date"
    ):
        return
    members = section_ids(conn, owner_id, snapshot, kind)
    dates = dict(
        conn.execute(
            text("""SELECT id,payload->>'start_date' FROM bonafides.claim_revisions
        WHERE owner_id=:owner AND id=ANY(CAST(:ids AS uuid[]))"""),
            {"owner": owner_id, "ids": members},
        )
        .tuples()
        .all()
    )
    ordered = iter(
        sorted(
            members, key=lambda item: -date_bounds(dates[item])[0].toordinal() if dates[item] else 0
        )
    )
    member_set = set(members)
    snapshot.claim_revision_ids = [
        next(ordered) if item in member_set else item for item in snapshot.claim_revision_ids
    ]


def order_section(
    conn: Connection, owner_id: UUID, kind: SectionKind, body: SectionOrder
) -> dict[str, object]:
    snapshot = _snapshot(conn, owner_id)
    members = section_ids(conn, owner_id, snapshot, kind)
    if len(body.revision_ids) != len(set(body.revision_ids)) or set(body.revision_ids) != set(
        members
    ):
        raise Problem(
            409, "section_membership_changed", "Reload the profile before ordering this section."
        )
    if kind == "publication" and body.mode != "manual":
        raise Problem(422, "publication_order_manual")
    replacements = iter(body.revision_ids if body.mode == "manual" else members)
    member_set = set(members)
    snapshot.claim_revision_ids = [
        next(replacements) if item in member_set else item for item in snapshot.claim_revision_ids
    ]
    if kind != "publication":
        snapshot.ordering_modes[kind] = body.mode
        apply_date_order(conn, owner_id, snapshot, kind)
    return _save(conn, owner_id, snapshot, "section_ordered")


def restore_claim(conn: Connection, owner_id: UUID, entity_id: UUID) -> dict[str, object]:
    record = (
        conn.execute(
            text("""SELECT c.*,r.payload FROM bonafides.professional_claims c
        JOIN bonafides.claim_revisions r ON r.owner_id=c.owner_id AND r.id=c.current_revision_id
        WHERE c.owner_id=:owner AND c.id=:id FOR UPDATE OF c"""),
            {"owner": owner_id, "id": entity_id},
        )
        .mappings()
        .one_or_none()
    )
    if record is None:
        raise Problem(404, "claim_not_found")
    if record["archived_at"] is None:
        raise Problem(409, "claim_not_archived")
    kind, revision_id = str(record["kind"]), record["current_revision_id"]
    scope = str(record["payload"].get("application_id") or "personal")
    if kind == "writing_fact":
        validate_writing_scope(conn, owner_id, scope, active=True)
    ensure_claim_capacity(conn, owner_id, kind, scope)
    snapshot = _snapshot(conn, owner_id)
    historical = conn.execute(
        text("""SELECT p.snapshot FROM bonafides.profile_revisions p
        JOIN bonafides.profile_revision_claims l ON l.owner_id=p.owner_id AND l.profile_revision_id=p.id
        JOIN bonafides.claim_revisions r ON r.owner_id=l.owner_id AND r.id=l.claim_revision_id
        WHERE p.owner_id=:owner AND r.claim_id=:id ORDER BY p.revision_no DESC LIMIT 1"""),
        {"owner": owner_id, "id": entity_id},
    ).scalar_one_or_none()
    ids = list(
        snapshot.writing_fact_revision_ids.get(scope, [])
        if kind == "writing_fact"
        else snapshot.claim_revision_ids
    )
    old = (
        ProfileSnapshot.model_validate(historical) if historical is not None else ProfileSnapshot()
    )
    former = (
        old.writing_fact_revision_ids.get(scope, [])
        if kind == "writing_fact"
        else old.claim_revision_ids
    )
    # Match neighbors by claim identity, not revision: later edits must not erase position.
    identities = dict(
        conn.execute(
            text("""SELECT id,claim_id FROM bonafides.claim_revisions
        WHERE owner_id=:owner AND id=ANY(CAST(:ids AS uuid[]))"""),
            {"owner": owner_id, "ids": list(set(ids + former))},
        )
        .tuples()
        .all()
    )
    positions = {identities[item]: index for index, item in enumerate(ids)}
    pivot = next(
        (index for index, item in enumerate(former) if identities[item] == entity_id), len(former)
    )
    after = next(
        (
            positions[identities[item]]
            for item in former[pivot + 1 :]
            if identities[item] in positions
        ),
        None,
    )
    before = next(
        (
            positions[identities[item]]
            for item in reversed(former[:pivot])
            if identities[item] in positions
        ),
        None,
    )
    ids.insert(
        after if after is not None else before + 1 if before is not None else len(ids), revision_id
    )
    conn.execute(
        text("""UPDATE bonafides.professional_claims SET archived_at=NULL,version=version+1
        WHERE owner_id=:owner AND id=:id"""),
        {"owner": owner_id, "id": entity_id},
    )
    if kind == "writing_fact":
        snapshot.writing_fact_revision_ids = {**snapshot.writing_fact_revision_ids, scope: ids}
    else:
        snapshot.claim_revision_ids = ids
        apply_date_order(conn, owner_id, snapshot, kind)
    result = _save(conn, owner_id, snapshot, "claim_restored")
    result["changed_claim_id"] = str(entity_id)
    return result


def write_claim(
    conn: Connection, owner_id: UUID, body: ClaimWrite, entity_id: UUID | None
) -> dict[str, object]:
    creating = entity_id is None
    snapshot = _snapshot(conn, owner_id)
    evidence_ids = [link.evidence_revision_id for link in body.evidence_links]
    if len(set(evidence_ids)) != len(evidence_ids):
        raise Problem(422, "duplicate_evidence_revision")
    grounded_literal = False
    for link in body.evidence_links:
        valid = conn.execute(
            text("""SELECT r.payload FROM bonafides.evidence_revisions r JOIN bonafides.evidence_records e
            ON e.owner_id=r.owner_id AND e.id=r.evidence_id WHERE r.owner_id=:owner AND r.id=:id AND e.archived_at IS NULL"""),
            {"owner": owner_id, "id": link.evidence_revision_id},
        ).scalar_one_or_none()
        if valid is None:
            raise Problem(422, "invalid_evidence_revision")
        if isinstance(body.payload, (ClaimPayload, WritingFactPayload)) and link.support_kind in (
            "direct",
            "user_asserted",
        ):
            grounded_literal = grounded_literal or literal_grounded(
                body.payload.text, link.excerpt, valid.get("text", "")
            )
    if (
        isinstance(body.payload, (ClaimPayload, WritingFactPayload))
        and body.payload.text.strip()
        and not grounded_literal
    ):
        raise Problem(
            422,
            "literal_claim_not_grounded",
            f"Literal entry is not grounded in its source: {body.payload.text}",
        )
    previous = None
    scope = "personal"
    if isinstance(body.payload, WritingFactPayload):
        scope = str(body.payload.application_id or "personal")
        validate_writing_scope(conn, owner_id, scope, active=True)
    if entity_id is None:
        ensure_claim_capacity(conn, owner_id, body.kind, scope)
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
        if body.kind == "writing_fact":
            old_payload = conn.execute(
                text(
                    "SELECT payload FROM bonafides.claim_revisions WHERE owner_id=:owner AND id=:id"
                ),
                {"owner": owner_id, "id": previous},
            ).scalar_one()
            if str(old_payload.get("application_id") or "personal") != scope:
                raise Problem(422, "writing_scope_immutable")
    payload = body.payload.model_dump(mode="json", exclude_none=True)
    if body.kind != "publication":
        for field in ("citation_markup", "citation_url", "include_in_resume"):
            payload.pop(field, None)
    revision_id = append_claim_revision(
        conn,
        owner_id,
        entity_id,
        payload,
        body.evidence_links,
        increment=0 if creating else 1,
    )
    ids = (
        snapshot.writing_fact_revision_ids.get(scope, [])
        if body.kind == "writing_fact"
        else snapshot.claim_revision_ids
    )
    ids = [revision_id if item == previous else item for item in ids]
    if revision_id not in ids:
        dated_kind = cast(Literal["experience", "project", "education"], body.kind)
        manual_dated = (
            body.kind in ("experience", "project", "education")
            and snapshot.ordering_modes.get(dated_kind, "manual") == "manual"
        )
        ids.insert(0 if body.kind == "publication" or manual_dated else len(ids), revision_id)
    if body.kind == "writing_fact":
        snapshot.writing_fact_revision_ids = {**snapshot.writing_fact_revision_ids, scope: ids}
    else:
        if body.kind in ("experience", "project", "education"):
            dated_kind = cast(Literal["experience", "project", "education"], body.kind)
            existing = section_ids(conn, owner_id, snapshot, dated_kind)
            if not existing and dated_kind not in snapshot.ordering_modes:
                snapshot.ordering_modes = {**snapshot.ordering_modes, dated_kind: "date"}
        snapshot.claim_revision_ids = ids
        apply_date_order(conn, owner_id, snapshot, body.kind)
    result = _save(conn, owner_id, snapshot, "claim_saved")
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
        snapshot.writing_fact_revision_ids = {
            scope: [item for item in ids if item != record["current_revision_id"]]
            for scope, ids in snapshot.writing_fact_revision_ids.items()
        }
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
            conn, owner_id, snapshot_claim_ids(row["snapshot"]), historical=True
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
    applications = {
        str(item)
        for item in conn.execute(
            text("SELECT id FROM bonafides.applications WHERE owner_id=:owner"),
            {"owner": owner_id},
        ).scalars()
    }
    snapshot.writing_fact_revision_ids = {
        scope: ids
        for scope, ids in snapshot.writing_fact_revision_ids.items()
        if scope == "personal" or scope in applications
    }
    # Only this explicit command can reactivate archived entries. Ordinary writes cannot.
    selected = snapshot_claim_ids(snapshot.model_dump(mode="json"))
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
            lock_evidence_write(conn, owner_id)
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

    @router.post("/profile/sections/{kind}/order")
    def reorder(
        kind: SectionKind,
        body: SectionOrder,
        response: Response,
        owner: Annotated[Owner, Depends(require_owner)],
        if_match: Annotated[str | None, Header()] = None,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        return mutate(
            owner.id,
            "POST",
            "/api/v1/profile/sections/{kind}/order",
            {"kind": kind},
            body.model_dump(mode="json"),
            if_match,
            idempotency_key,
            response,
            lambda conn: order_section(conn, owner.id, kind, body),
        )

    @router.post("/claims/{entity_id}/restore")
    def restore_entry(
        entity_id: UUID,
        response: Response,
        owner: Annotated[Owner, Depends(require_owner)],
        if_match: Annotated[str | None, Header()] = None,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        return mutate(
            owner.id,
            "POST",
            "/api/v1/claims/{entity_id}/restore",
            {"entity_id": str(entity_id)},
            {},
            if_match,
            idempotency_key,
            response,
            lambda conn: restore_claim(conn, owner.id, entity_id),
        )

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
        body: EvidenceCreate,
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

    @router.put("/evidence/{entity_id}/classification")
    def update_evidence_classification(
        entity_id: UUID,
        body: EvidenceClassification,
        response: Response,
        owner: Annotated[Owner, Depends(require_owner)],
        if_match: Annotated[str | None, Header()] = None,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        return mutate(
            owner.id,
            "PUT",
            "/api/v1/evidence/{entity_id}/classification",
            {"entity_id": str(entity_id)},
            body.model_dump(mode="json"),
            if_match,
            idempotency_key,
            response,
            lambda conn: classify_evidence(conn, owner.id, entity_id, body),
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
