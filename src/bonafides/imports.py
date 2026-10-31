# SPDX-License-Identifier: MIT OR Apache-2.0
"""Local literal import proposals and explicitly reviewed, fenced profile publication."""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from threading import Event
from typing import Annotated, Literal, cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import Field
from sqlalchemy import Connection, text
from sqlalchemy.engine import RowMapping
from starlette.concurrency import run_in_threadpool

from bonafides.auth import Owner, StrictModel, require_owner
from bonafides.claim_mapping import UnparsedPart, map_line, statement_text
from bonafides.claims import STRUCTURED_KINDS, StructuredClaim
from bonafides.commands import append_outbox, canonical_json, command, lock_installation
from bonafides.config import Settings
from bonafides.db import Database
from bonafides.import_parser import (
    MAX_TEXT_BYTES,
    MAX_TEXT_SCALARS,
    MAX_UPLOAD_BYTES,
    ImportFailure,
    ImportParserClient,
    ParsedImport,
)
from bonafides.problems import Problem
from bonafides.profile_entries import ClaimKind, ClaimPayload, StructuredClaimPayload, _snapshot
from bonafides.profiles import MAX_HEADLINE_LENGTH, _save, content_hash
from bonafides.storage import BlobStore
from bonafides.workflows import Claim, append_attempt_event, assert_fence, enqueue, publish

MAX_BATCHES = 100
MAX_IMPORT_BYTES = 64 * 1024 * 1024
MAX_ACTIVE_IMPORTS = 4
MAX_PROPOSALS = 100
MAX_IMPORT_SOURCES = 1000

_HEADINGS: dict[str, ClaimKind] = {
    "experience": "experience",
    "work experience": "experience",
    "professional experience": "experience",
    "employment": "experience",
    "employment history": "experience",
    "work history": "experience",
    "education": "education",
    "academic background": "education",
    "skills": "skill",
    "technical skills": "skill",
    "core skills": "skill",
    "competencies": "skill",
    "projects": "project",
    "selected projects": "project",
    "certifications": "certification",
    "certificates": "certification",
    "licenses": "certification",
    "languages": "language",
    "language skills": "language",
    "preferences": "preference",
    "summary": "other",
    "professional summary": "other",
    "profile": "other",
    "objective": "other",
    "achievements": "other",
    "awards": "other",
    "publications": "other",
    "volunteering": "other",
    "interests": "other",
}
_PRIVATE_HEADINGS = {"contact", "contact information", "personal details", "references", "referees"}
_CONTACT = re.compile(
    r"[\w.+-]+@[\w.-]+\.[\w-]+|(?:https?://|www\.)\S+|"
    r"(?:linkedin\.com|github\.com)/|"
    r"^(?:e-?mail|phone|mobile|telephone|address|website|linkedin|github|reference|referee|date of birth)\s*:|"
    r"^references?\s+(?:available|upon request)|"
    r"(?:\(\d{2,4}\)|\b\d{3})[- .]\d{3}[- .]\d{4}\b|"
    r"^\d{1,6}\s+[\w .'-]+\s(?:street|st|avenue|ave|road|rd|lane|ln|drive|dr|boulevard|blvd)\b",
    re.IGNORECASE,
)
_PHONE = re.compile(r"^\+?[\d ()+.-]{7,}$")
_DATE_LINE = re.compile(
    r"\d{4}(?:-\d{2}(?:-\d{2})?)?(?:\s*[-–—]\s*(?:\d{4}(?:-\d{2}(?:-\d{2})?)?|present|current))?\Z",
    re.IGNORECASE,
)


class ImportProposal(StrictModel):
    proposal_id: UUID
    kind: ClaimKind
    payload: ClaimPayload
    source_start: int = Field(ge=0)
    source_end: int = Field(gt=0)
    quote: str = Field(min_length=1, max_length=4000)


class ImportSource(StrictModel):
    source_id: UUID
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1, max_length=4000)


IdentityField = Literal["display_name", "headline"]


class ClaimLineProposal(StrictModel):
    """One statement line proposed as a structured claim; unplaced parts are flagged."""

    source_id: UUID
    quote: str
    claim: StructuredClaim | None
    rendering: str | None
    unparsed: list[UnparsedPart]


class ImportEntry(StrictModel):
    entry_id: UUID
    kind: ClaimKind | None
    identity_field: IdentityField | None = None
    payload: ClaimPayload
    sources: list[ImportSource] = Field(min_length=1, max_length=MAX_IMPORT_SOURCES)
    issues: list[str] = Field(default_factory=list)
    claim_proposals: list[ClaimLineProposal] = Field(default_factory=list)


class ImportComparison(StrictModel):
    status: Literal["new", "unchanged", "conflict", "needs_attention"]
    target_claim_id: UUID | None = None
    candidate_claim_ids: list[UUID] = Field(default_factory=list)


class ImportPreviewEntry(ImportEntry):
    comparison: ImportComparison


class _SavedClaim(StrictModel):
    claim_id: UUID
    kind: ClaimKind
    payload: StructuredClaimPayload | ClaimPayload


class ImportDecision(StrictModel):
    source_ids: list[UUID] = Field(min_length=1, max_length=MAX_IMPORT_SOURCES)
    action: Literal["skip", "add", "replace", "unchanged"]
    claim_id: UUID | None = None
    kind: ClaimKind | None
    identity_field: IdentityField | None = None
    payload: ClaimPayload


class ImportReview(StrictModel):
    mode: Literal["merge", "replace_all"]
    replace_all_confirmed: bool = Field(strict=True)
    decisions: list[ImportDecision] = Field(min_length=1, max_length=MAX_IMPORT_SOURCES)


def extract_proposals(parsed: ParsedImport) -> tuple[list[ImportProposal], list[str]]:
    """One verbatim non-contact line per proposal; no inference, deduplication or OCR."""
    if (
        not parsed.text.strip()
        or len(parsed.text) > MAX_TEXT_SCALARS
        or len(parsed.text.encode("utf-8")) > MAX_TEXT_BYTES
        or hashlib.sha256(parsed.text.encode("utf-8")).hexdigest() != parsed.source_sha256
    ):
        raise Problem(422, "invalid_import_source")
    warnings = list(
        dict.fromkeys(
            [
                *parsed.warnings,
                "deterministic_literal_extraction",
                "evidence_is_owner_asserted_not_independently_verified",
            ]
        )
    )
    result: list[ImportProposal] = []
    kind: ClaimKind = "other"
    private_section = False
    offset = 0
    for line in parsed.text.splitlines(keepends=True):
        literal = line.strip()
        start = offset + len(line) - len(line.lstrip())
        offset += len(line)
        if not literal:
            continue
        heading = literal.strip("# \t:").casefold()
        if heading in _PRIVATE_HEADINGS:
            private_section = True
            if "contact_details_excluded" not in warnings:
                warnings.append("contact_details_excluded")
            continue
        if heading in _HEADINGS:
            kind, private_section = _HEADINGS[heading], False
            continue
        if (
            private_section
            or _CONTACT.search(literal)
            or (
                _PHONE.fullmatch(literal)
                and not _DATE_LINE.fullmatch(literal)
                and sum(character.isdigit() for character in literal) >= 7
            )
        ):
            if "contact_details_excluded" not in warnings:
                warnings.append("contact_details_excluded")
            continue
        # Unknown header-like lines are retained literally rather than assigned a guessed category.
        if literal.endswith(":"):
            kind = "other"
        if kind == "other" and "ambiguous_section_kept_as_other" not in warnings:
            warnings.append("ambiguous_section_kept_as_other")
        if len(literal) > 4000:
            raise Problem(422, "import_line_too_long")
        if len(result) >= MAX_PROPOSALS:
            raise Problem(422, "import_proposal_limit")
        result.append(
            ImportProposal(
                proposal_id=uuid5(
                    NAMESPACE_URL, f"bonafides:{parsed.source_sha256}:{start}:{offset}"
                ),
                kind=kind,
                payload=ClaimPayload(text=literal),
                source_start=start,
                source_end=start + len(literal),
                quote=literal,
            )
        )
    if not result:
        raise Problem(422, "import_no_proposals")
    return result, warnings


_REVIEW_HEADINGS: dict[str, ClaimKind | IdentityField] = {
    **_HEADINGS,
    "core competencies": "skill",
    "technical competencies": "skill",
    "areas of expertise": "skill",
    "key skills": "skill",
    "professional skills": "skill",
    "technical proficiencies": "skill",
    "certifications & training": "certification",
    "summary": "headline",
    "professional summary": "headline",
    "profile": "headline",
    "objective": "headline",
    "notes": "other",
    "additional notes": "other",
}
_SKILL_SEPARATOR = re.compile(r"[|;,•·●]|\s+/\s+")
_BULLET = re.compile(r"^\s*[-*•·●]\s+")
_DATE_RANGE = re.compile(
    r"(?P<start>[0-9]{4}(?:-[0-9]{2}(?:-[0-9]{2})?)?|[0-9]{2}/[0-9]{4})\s*[-–—]\s*"
    r"(?P<end>[0-9]{4}(?:-[0-9]{2}(?:-[0-9]{2})?)?|[0-9]{2}/[0-9]{4}|present|current)\Z",
    re.IGNORECASE,
)


def project_entries(
    source: str, source_sha256: str, proposals: list[ImportProposal]
) -> list[ImportEntry]:
    """Read-only, bounded projection of immutable literal fragments, never a reparse."""
    if (
        len(source) > MAX_TEXT_SCALARS
        or len(source.encode("utf-8")) > MAX_TEXT_BYTES
        or hashlib.sha256(source.encode("utf-8")).hexdigest() != source_sha256
    ):
        raise Problem(422, "invalid_import_source")
    entries: list[ImportEntry] = []
    section: ClaimKind | IdentityField | None = None
    section_seen = 0
    previous_end = 0
    for index, proposal in enumerate(proposals):
        start, end = proposal.source_start, proposal.source_end
        if (
            start < previous_end
            or end <= start
            or end > len(source)
            or source[start:end] != proposal.quote
        ):
            raise Problem(422, "invalid_import_source")
        gap = source[previous_end:start]
        section_changed = False
        for line in gap.splitlines():
            heading = line.strip("# \t:").casefold()
            if heading in _REVIEW_HEADINGS:
                section, section_changed, section_seen = _REVIEW_HEADINGS[heading], True, 0
        heading = proposal.quote.strip("# \t:").casefold()
        # Newly recognized structural headings can exist in older raw fragment JSON.
        # Keep that JSON untouched, exactly as with headings excluded by extraction.
        if heading in _REVIEW_HEADINGS:
            section = _REVIEW_HEADINGS[heading]
            section_seen = 0
            previous_end = end
            continue
        if proposal.quote.endswith(":"):
            section = None
        kind: ClaimKind | None = (
            cast(ClaimKind, section) if section not in (None, "headline", "other") else None
        )
        if section is None and proposal.kind != "other":
            kind = proposal.kind
        if (
            kind == "project"
            and not _BULLET.match(proposal.quote)
            and not proposal.quote.casefold().startswith("project:")
            and index + 1 < len(proposals)
            and _BULLET.match(proposals[index + 1].quote)
            and re.search(r"\s[–—-]\s", proposals[index + 1].quote)
        ):
            kind = None
        identity_field: IdentityField | None = None
        if index == 0 and section is None and _name_like(proposal.quote):
            kind, identity_field = "identity", "display_name"
        elif (
            section == "headline"
            and section_seen == 0
            and len(proposal.quote) <= MAX_HEADLINE_LENGTH
        ):
            # Only a single literal line in a bounded summary is a clear headline.
            following = proposals[index + 1] if index + 1 < len(proposals) else None
            boundary = source[end : following.source_start] if following else "\n\n"
            if following is None or any(
                line.strip("# \t:").casefold() in _REVIEW_HEADINGS for line in boundary.splitlines()
            ):
                kind, identity_field = "identity", "headline"
        spans: list[tuple[int, int]] = [(start, end)]
        if kind == "skill" and (
            _SKILL_SEPARATOR.search(proposal.quote) or _BULLET.match(proposal.quote)
        ):
            spans = []
            bullet = _BULLET.match(proposal.quote)
            offset = bullet.end() if bullet else 0
            label_end = proposal.quote.find(":", offset) if bullet else -1
            if 0 < label_end - offset <= 120:
                offset = label_end + 1
            for separator in [*_SKILL_SEPARATOR.finditer(proposal.quote, offset), None]:
                stop = separator.start() if separator else len(proposal.quote)
                fragment = proposal.quote[offset:stop]
                if fragment.strip():
                    left = start + offset + len(fragment) - len(fragment.lstrip())
                    spans.append((left, left + len(fragment.strip())))
                offset = separator.end() if separator else stop
            if not spans:
                spans = [(start, end)]
        for left, right in spans:
            literal = source[left:right]
            part = ImportSource(
                source_id=uuid5(NAMESPACE_URL, f"bonafides:source:{source_sha256}:{left}:{right}"),
                start=left,
                end=right,
                quote=literal,
            )
            payload = ClaimPayload(text=literal)
            if kind in {"experience", "education", "project"}:
                payload = _literal_fields(literal, kind)
            issues = ["needs_placement"] if kind is None else []
            if kind in {"experience", "education", "project"} and (
                _DATE_RANGE.fullmatch(literal.split("|")[-1].strip()) is not None
                and payload.start_date is None
            ):
                issues.append("invalid_extracted_fields")
            entry = ImportEntry(
                entry_id=uuid5(NAMESPACE_URL, f"bonafides:entry:{part.source_id}"),
                kind=kind,
                identity_field=identity_field,
                payload=payload,
                sources=[part],
                issues=issues,
            )
            group_continuation = bool(
                entries
                and kind in {"experience", "education", "project"}
                and entries[-1].kind == kind
                and not section_changed
                and not re.search(r"\n[ \t]*\n", gap)
                and _can_group(
                    entries[-1],
                    literal,
                    (
                        proposals[index + 1].quote
                        if index + 1 < len(proposals)
                        and not re.search(
                            r"\n[ \t]*\n", source[end : proposals[index + 1].source_start]
                        )
                        else None
                    ),
                )
            )
            if group_continuation and len(entries[-1].payload.text) + len(literal) + 1 > 4000:
                entries[-1].issues.append("source_group_too_long")
                entry.issues.append("source_group_too_long")
                entries.append(entry)
            elif group_continuation:
                previous = entries[-1]
                combined = previous.payload.text + "\n" + literal
                previous.sources.append(part)
                fields = previous.payload.model_dump()
                fields.update(
                    {
                        key: value
                        for key, value in payload.model_dump().items()
                        if value is not None and key != "text"
                    }
                )
                fields["text"] = combined
                try:
                    previous.payload = ClaimPayload.model_validate(fields)
                except ValueError:
                    previous.payload = ClaimPayload(text=combined)
                    previous.issues.append("invalid_extracted_fields")
                previous.issues.extend(entry.issues)
                previous.entry_id = uuid5(
                    NAMESPACE_URL,
                    "bonafides:entry:" + ":".join(str(item.source_id) for item in previous.sources),
                )
            else:
                entries.append(entry)
        section_seen += 1
        previous_end = end
    if sum(len(entry.sources) for entry in entries) > MAX_IMPORT_SOURCES:
        raise Problem(422, "import_source_limit")
    for entry in entries:
        if entry.kind in STRUCTURED_KINDS:
            entry.claim_proposals = _claim_proposals(entry.sources)
    return entries


def _claim_proposals(sources: list[ImportSource]) -> list[ClaimLineProposal]:
    """Every line is proposed, headings included: approval saves exactly these lines.

    A role, employer, date or project heading has no ownership verb, so it stays literal text
    that needs attention rather than being dropped as context the fields may not capture.
    """
    proposals: list[ClaimLineProposal] = []
    for part in sources:
        mapping = map_line(part.quote)
        proposals.append(
            ClaimLineProposal(
                source_id=part.source_id,
                quote=part.quote,
                claim=mapping.claim,
                rendering=mapping.rendering,
                unparsed=mapping.unparsed,
            )
        )
    return proposals


def _name_like(value: str) -> bool:
    words = value.split()
    return (
        len(value) <= 120
        and 2 <= len(words) <= 5
        and all(
            word[0].isupper()
            and all(character.isalpha() or character in "'’-." for character in word)
            for word in words
        )
    )


def _literal_fields(value: str, kind: ClaimKind) -> ClaimPayload:
    fields: dict[str, str] = {"text": value}
    parts = [part.strip() for part in _BULLET.sub("", value, count=1).split("|")]
    date_text = parts[-1]
    dates = _DATE_RANGE.fullmatch(date_text)
    if dates:
        fields["start_date"] = dates["start"]
        if dates["end"].casefold() not in {"present", "current"}:
            fields["end_date"] = dates["end"]
        for field in ("start_date", "end_date"):
            date = fields.get(field)
            if date is not None and "/" in date:
                month, year = date.split("/")
                fields[field] = f"{year}-{month}"
    if len(parts) in {2, 3} and kind in {"experience", "education"}:
        labels = parts[:-1] if dates else parts
        if len(labels) == 2 and all(0 < len(label) <= 200 for label in labels):
            fields.update(role=labels[0], organization=labels[1])
    # Invalid or ambiguous dates remain literal; never normalize precision or invent a date.
    try:
        return ClaimPayload.model_validate(fields)
    except ValueError:
        return ClaimPayload(text=value)


def _can_group(entry: ImportEntry, literal: str, following: str | None) -> bool:
    """Only attach an explicit date or bullet continuation, not adjacent jobs."""
    if literal.casefold().startswith("project:"):
        return False
    if _BULLET.match(literal) and (
        entry.kind == "project"
        and re.search(r"\s[–—-]\s", literal)
        or entry.kind == "education"
        and "|" in literal
    ):
        return False
    if entry.kind == "project" and entry.payload.text.casefold().startswith("project:"):
        return True
    if _BULLET.match(literal):
        return entry.payload.start_date is not None or entry.kind == "project"
    if _DATE_RANGE.fullmatch(literal):
        return entry.payload.start_date is None and len(entry.sources) <= 2
    if following is not None and _DATE_RANGE.fullmatch(following):
        return entry.payload.start_date is None and len(entry.sources) == 1
    return (
        entry.payload.start_date is not None
        and "|" not in literal
        and literal.endswith((".", "!", "?"))
    )


def _saved_claims(conn: Connection, owner_id: UUID, ids: list[UUID]) -> list[_SavedClaim]:
    rows = conn.execute(
        text("""SELECT c.id,c.kind,r.payload FROM bonafides.professional_claims c
        JOIN bonafides.claim_revisions r ON r.owner_id=c.owner_id AND r.id=c.current_revision_id
        WHERE c.owner_id=:owner AND c.archived_at IS NULL
        AND r.id=ANY(CAST(:ids AS uuid[])) ORDER BY c.created_at,c.id"""),
        {"owner": owner_id, "ids": ids},
    ).mappings()
    return [
        _SavedClaim(claim_id=row["id"], kind=row["kind"], payload=row["payload"]) for row in rows
    ]


def _same_entity(
    kind: ClaimKind, left: ClaimPayload, right: StructuredClaimPayload | ClaimPayload
) -> bool:
    if kind in {"experience", "education"}:
        return all(
            getattr(left, field) and getattr(left, field) == getattr(right, field)
            for field in ("organization", "role", "start_date")
        )
    if kind == "project":
        return bool(left.project_url and left.project_url == right.project_url)
    return isinstance(right, ClaimPayload) and bool(left.text and left.text == right.text)


def _compare_entry(
    entry: ImportEntry, saved: list[_SavedClaim], identity: dict[str, str]
) -> ImportComparison:
    if entry.issues or entry.kind is None or len(entry.payload.text) > 4000:
        return ImportComparison(status="needs_attention")
    if entry.kind == "identity":
        field = entry.identity_field
        if field is None or len(entry.payload.text) > (120 if field == "display_name" else 300):
            return ImportComparison(status="needs_attention")
        current = identity[field]
        return ImportComparison(
            status="new"
            if not current
            else "unchanged"
            if current == entry.payload.text
            else "conflict"
        )
    same_kind = [claim for claim in saved if claim.kind == entry.kind]
    split = _saved_statements(entry, entry.kind, entry.payload, same_kind)
    if split:
        return ImportComparison(
            status="unchanged", target_claim_id=split[0], candidate_claim_ids=split
        )
    exact = [claim.claim_id for claim in same_kind if claim.payload == entry.payload]
    if len(exact) == 1:
        return ImportComparison(
            status="unchanged", target_claim_id=exact[0], candidate_claim_ids=exact
        )
    candidates = exact or [
        claim.claim_id
        for claim in same_kind
        if _same_entity(entry.kind, entry.payload, claim.payload)
    ]
    if len(candidates) > 1:
        return ImportComparison(status="needs_attention", candidate_claim_ids=candidates)
    if candidates:
        return ImportComparison(
            status="conflict", target_claim_id=candidates[0], candidate_claim_ids=candidates
        )
    return ImportComparison(status="new")


def get_batch(conn: Connection, owner_id: UUID, import_id: UUID) -> dict[str, object]:
    row = (
        conn.execute(
            text("""SELECT b.*,w.state,w.version,w.last_error_code FROM bonafides.import_batches b
        JOIN bonafides.workflow_runs w ON w.owner_id=b.owner_id AND w.id=b.workflow_run_id
        WHERE b.owner_id=:owner AND b.id=:id"""),
            {"owner": owner_id, "id": import_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise Problem(404, "import_not_found")
    comparison_version = conn.execute(
        text("SELECT version FROM bonafides.profiles WHERE owner_id=:owner FOR SHARE"),
        {"owner": owner_id},
    ).scalar_one()
    snapshot = _snapshot(conn, owner_id)
    saved = _saved_claims(conn, owner_id, snapshot.claim_revision_ids)
    identity = {"display_name": snapshot.display_name, "headline": snapshot.headline}
    return {
        "id": str(row["id"]),
        "workflow_id": str(row["workflow_run_id"]),
        "state": row["state"],
        "version": int(row["version"]),
        "media_type": row["media_type"],
        "byte_size": int(row["byte_size"]),
        "created_at": row["created_at"].isoformat(),
        "source_sha256": row["source_sha256"],
        "text": row["parsed_text"],
        "entries": [
            ImportPreviewEntry(
                **entry.model_dump(),
                comparison=_compare_entry(entry, saved, identity),
            ).model_dump(mode="json")
            for entry in (
                project_entries(
                    row["parsed_text"],
                    row["source_sha256"],
                    [ImportProposal.model_validate(value) for value in row["proposals"]],
                )
                if row["parsed_text"] is not None
                else []
            )
        ],
        "warnings": row["warnings"],
        "profile_version": int(row["profile_version"]),
        "comparison_profile_version": int(comparison_version),
        "error_code": row["last_error_code"],
    }


def _batch_for_task(conn: Connection, task: Claim) -> RowMapping:
    row = (
        conn.execute(
            text("""SELECT b.*,o.storage_key,o.sha256,o.byte_length FROM bonafides.import_batches b
        JOIN bonafides.blobs o ON o.owner_id=b.owner_id AND o.id=b.blob_id
        WHERE b.owner_id=:owner AND b.id=:id AND b.workflow_run_id=:workflow"""),
            {"owner": task.owner_id, "id": UUID(str(task.input["import_id"])), "workflow": task.id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise Problem(404, "import_not_found")
    return row


def _locked_target(
    conn: Connection,
    owner_id: UUID,
    claim_id: UUID,
    kind: ClaimKind | None,
    current_ids: set[UUID],
) -> RowMapping:
    row = (
        conn.execute(
            text("""SELECT c.* FROM bonafides.professional_claims c JOIN bonafides.profiles p
        ON p.owner_id=c.owner_id AND p.id=c.profile_id
        WHERE c.owner_id=:owner AND c.id=:id FOR UPDATE OF c"""),
            {"owner": owner_id, "id": claim_id},
        )
        .mappings()
        .one_or_none()
    )
    if (
        row is None
        or row["archived_at"] is not None
        or row["kind"] != kind
        or row["kind"] == "identity"
        or row["current_revision_id"] not in current_ids
    ):
        raise Problem(422, "import_target_invalid")
    return row


def _validate_review(
    conn: Connection,
    owner_id: UUID,
    batch: RowMapping,
    review: ImportReview,
) -> tuple[dict[UUID, RowMapping], set[UUID]]:
    entries = project_entries(
        batch["parsed_text"],
        batch["source_sha256"],
        [ImportProposal.model_validate(value) for value in batch["proposals"]],
    )
    sources = {part.source_id: part for entry in entries for part in entry.sources}
    by_sources = {frozenset(part.source_id for part in entry.sources): entry for entry in entries}
    received = [source_id for item in review.decisions for source_id in item.source_ids]
    if len(set(received)) != len(received) or set(received) != set(sources):
        raise Problem(422, "invalid_import_decisions")
    accepted = [item for item in review.decisions if item.action != "skip"]
    if not accepted:
        raise Problem(422, "import_no_accepted_proposals")
    if review.mode == "replace_all" and not review.replace_all_confirmed:
        raise Problem(422, "import_replace_confirmation_required")
    replacements: dict[UUID, RowMapping] = {}
    unchanged_targets: set[UUID] = set()
    snapshot = _snapshot(conn, owner_id)
    current_ids = set(snapshot.claim_revision_ids)
    saved = _saved_claims(conn, owner_id, snapshot.claim_revision_ids)
    identity_fields: set[IdentityField] = set()
    for decision in review.decisions:
        if decision.identity_field is not None and decision.kind != "identity":
            raise Problem(422, "import_destination_invalid")
        if decision.action == "skip":
            if decision.claim_id is not None:
                raise Problem(422, "import_target_invalid")
            continue
        if decision.kind is None:
            raise Problem(422, "import_destination_required")
        original = "\n".join(
            part.quote
            for part in sorted(
                (sources[source_id] for source_id in decision.source_ids),
                key=lambda part: part.start,
            )
        )
        if len(original) > 4000:
            raise Problem(422, "import_source_too_long")
        if decision.kind == "identity":
            field = decision.identity_field
            if field is None or field in identity_fields or decision.claim_id is not None:
                raise Problem(422, "import_identity_invalid")
            if any(
                value is not None
                for key, value in decision.payload.model_dump().items()
                if key != "text"
            ):
                raise Problem(422, "import_identity_invalid")
            limit = 120 if field == "display_name" else MAX_HEADLINE_LENGTH
            if len(decision.payload.text) > limit:
                raise Problem(422, "import_identity_invalid")
            populated = bool(getattr(snapshot, field))
            if decision.action == "unchanged":
                if decision.payload.text != getattr(snapshot, field):
                    raise Problem(422, "import_unchanged_invalid")
            elif decision.action != ("replace" if populated else "add"):
                raise Problem(422, "import_identity_overwrite_required")
            identity_fields.add(field)
            continue
        if decision.action not in {"replace", "unchanged"}:
            if decision.claim_id is not None:
                raise Problem(422, "import_target_invalid")
            continue
        if decision.claim_id is None:
            raise Problem(422, "import_target_invalid")
        targets = [decision.claim_id]
        if decision.action == "unchanged":
            exact = [
                claim.claim_id
                for claim in saved
                if claim.kind == decision.kind and claim.payload == decision.payload
            ]
            # An entry approved earlier as one claim per line is unchanged as that whole set.
            split = _saved_statements(
                by_sources.get(frozenset(decision.source_ids)),
                decision.kind,
                decision.payload,
                saved,
            )
            if exact != [decision.claim_id]:
                targets = split
            if targets[:1] != [decision.claim_id]:
                raise Problem(422, "import_unchanged_invalid")
        for target in targets:
            if target in replacements:
                if decision.action == "unchanged" and target in unchanged_targets:
                    continue
                raise Problem(422, "import_target_invalid")
            replacements[target] = _locked_target(
                conn, owner_id, target, decision.kind, current_ids
            )
            if decision.action == "unchanged":
                unchanged_targets.add(target)
    claim_count = conn.execute(
        text("""SELECT count(*) FROM bonafides.professional_claims
        WHERE owner_id=:owner AND archived_at IS NULL
        AND (:replace_all=false OR kind='identity' OR id=ANY(CAST(:targets AS uuid[])))"""),
        {
            "owner": owner_id,
            "replace_all": review.mode == "replace_all",
            "targets": list(replacements),
        },
    ).scalar_one()
    evidence_count = conn.execute(
        text("""SELECT count(*) FROM bonafides.evidence_records e
        WHERE e.owner_id=:owner AND e.archived_at IS NULL AND (:replace_all=false OR EXISTS(
        SELECT 1 FROM bonafides.claim_evidence_links l JOIN bonafides.professional_claims c
        ON c.owner_id=l.owner_id AND c.current_revision_id=l.claim_revision_id
        JOIN bonafides.evidence_revisions r ON r.owner_id=l.owner_id AND r.id=l.evidence_revision_id
        WHERE c.owner_id=e.owner_id AND c.archived_at IS NULL
        AND (c.kind='identity' OR c.id=ANY(CAST(:unchanged AS uuid[]))) AND r.evidence_id=e.id)
        OR EXISTS(SELECT 1 FROM bonafides.evidence_revisions r WHERE r.owner_id=e.owner_id
        AND r.id=e.current_revision_id AND r.payload->>'identity_field' IN ('display_name','headline')))"""),
        {
            "owner": owner_id,
            "replace_all": review.mode == "replace_all",
            "unchanged": list(unchanged_targets),
        },
    ).scalar_one()
    # An unedited entry approved as one claim per line adds one claim per line.
    added = sum(
        max(
            1,
            len(
                _statement_claims(
                    by_sources.get(frozenset(item.source_ids)), item.kind, item.payload
                )
            ),
        )
        for item in accepted
        if item.action == "add" and item.kind != "identity"
    )
    if claim_count + added > 100:
        raise Problem(422, "claim_limit")
    if evidence_count + sum(item.action != "unchanged" for item in accepted) > 200:
        raise Problem(422, "evidence_limit")
    return replacements, unchanged_targets


def stage_parsed(
    conn: Connection,
    task: Claim,
    parsed: ParsedImport,
    *,
    elapsed_ms: int | None = None,
) -> None:
    lock_installation(conn)
    conn.execute(
        text("SELECT owner_id FROM bonafides.owner_settings WHERE owner_id=:owner FOR UPDATE"),
        {"owner": task.owner_id},
    )
    assert_fence(conn, task)
    proposals, warnings = extract_proposals(parsed)
    project_entries(parsed.text, parsed.source_sha256, proposals)
    batch = _batch_for_task(conn, task)
    if batch["parsed_text"] is not None:
        raise Problem(409, "import_already_staged")
    expiry = conn.execute(text("SELECT clock_timestamp()+interval '7 days'")).scalar_one()
    conn.execute(
        text("""UPDATE bonafides.import_batches SET parsed_text=:source,source_sha256=:sha,
        proposals=CAST(:proposals AS jsonb),warnings=CAST(:warnings AS jsonb),review_expires_at=:expiry
        WHERE owner_id=:owner AND id=:id"""),
        {
            "owner": task.owner_id,
            "id": batch["id"],
            "source": parsed.text,
            "sha": parsed.source_sha256,
            "proposals": canonical_json([item.model_dump(mode="json") for item in proposals]),
            "warnings": canonical_json(warnings),
            "expiry": expiry,
        },
    )
    version = conn.execute(
        text("""UPDATE bonafides.workflow_runs SET state='waiting_for_approval',
        lease_holder=NULL,lease_expires_at=NULL,deadline_at=:expiry,version=version+1,
        updated_at=clock_timestamp(),last_error_code=NULL WHERE owner_id=:owner AND id=:id RETURNING version"""),
        {"owner": task.owner_id, "id": task.id, "expiry": expiry},
    ).scalar_one()
    append_attempt_event(conn, task.owner_id, task.id, "waiting_for_approval")
    append_outbox(
        conn,
        task.owner_id,
        "workflow.waiting_for_approval",
        "workflow",
        task.id,
        {
            "workflow_id": str(task.id),
            "stage": "import_parsed",
            "input_bytes": int(batch["byte_size"]),
            "parse_elapsed_ms": elapsed_ms,
            "proposal_count": len(proposals),
        },
        int(version),
    )


@dataclass(frozen=True)
class _ClaimInsert:
    profile_id: UUID
    kind: ClaimKind | None
    claim_id: UUID
    adding: bool


def _statement_claims(
    entry: ImportEntry | None, kind: ClaimKind | None, payload: ClaimPayload
) -> list[tuple[dict[str, object], str]]:
    """An unedited experience or project entry becomes one claim per line.

    Representable lines become structured claims; headings and lines with unparsed parts stay
    literal and therefore need attention. The entry fields are copied to every claim as context.
    """
    if (
        entry is None
        or not entry.claim_proposals
        or kind != entry.kind
        or payload.text != entry.payload.text
    ):
        return []
    context = payload.model_dump(mode="json", exclude_none=True, exclude={"text"})
    statements: list[tuple[dict[str, object], str]] = []
    for proposal in entry.claim_proposals:
        # The bullet marker is layout, not part of the statement or its quoted excerpt.
        quote = statement_text(proposal.quote)
        statement: StructuredClaimPayload | ClaimPayload
        if proposal.claim is not None and not proposal.unparsed:
            statement = StructuredClaimPayload.model_validate({**context, "claim": proposal.claim})
        else:
            statement = ClaimPayload.model_validate({**context, "text": quote})
        statements.append((statement.model_dump(mode="json", exclude_none=True), quote))
    return statements


def _saved_statements(
    entry: ImportEntry | None,
    kind: ClaimKind | None,
    payload: ClaimPayload,
    saved: list[_SavedClaim],
) -> list[UUID]:
    """The saved claims an earlier approval of this unedited entry produced, one per line.

    Empty unless every line's claim is still saved with exactly the payload it would get now.
    """
    found: list[UUID] = []
    for statement, _ in _statement_claims(entry, kind, payload):
        match = next(
            (
                claim.claim_id
                for claim in saved
                if claim.kind == kind
                and claim.claim_id not in found
                and claim.payload.model_dump(mode="json", exclude_none=True) == statement
            ),
            None,
        )
        if match is None:
            return []
        found.append(match)
    return found


def _insert_claim(
    conn: Connection,
    owner_id: UUID,
    target: _ClaimInsert,
    payload: dict[str, object],
    link: tuple[UUID, str],
) -> UUID:
    claim_revision_id = uuid4()
    if target.adding:
        conn.execute(
            text("""INSERT INTO bonafides.professional_claims(owner_id,id,profile_id,kind)
            VALUES(:owner,:id,:profile,:kind)"""),
            {
                "owner": owner_id,
                "id": target.claim_id,
                "profile": target.profile_id,
                "kind": target.kind,
            },
        )
    conn.execute(
        text("""INSERT INTO bonafides.claim_revisions(owner_id,id,claim_id,revision_no,payload,content_sha256)
        SELECT :owner,:id,:claim,COALESCE(max(revision_no),0)+1,CAST(:payload AS jsonb),:sha
        FROM bonafides.claim_revisions WHERE owner_id=:owner AND claim_id=:claim"""),
        {
            "owner": owner_id,
            "id": claim_revision_id,
            "claim": target.claim_id,
            "payload": canonical_json(payload),
            "sha": content_hash(payload),
        },
    )
    conn.execute(
        text("""INSERT INTO bonafides.claim_evidence_links
        (owner_id,claim_revision_id,evidence_revision_id,support_kind,excerpt)
        VALUES(:owner,:claim,:evidence,'user_asserted',:quote)"""),
        {"owner": owner_id, "claim": claim_revision_id, "evidence": link[0], "quote": link[1]},
    )
    conn.execute(
        text("""UPDATE bonafides.professional_claims SET current_revision_id=:revision,
        version=version+:increment WHERE owner_id=:owner AND id=:id"""),
        {
            "owner": owner_id,
            "id": target.claim_id,
            "revision": claim_revision_id,
            "increment": 0 if target.adding else 1,
        },
    )
    return claim_revision_id


def merge_reviewed(conn: Connection, task: Claim) -> dict[str, object]:
    """Called only inside publish(): one locked transaction, at most one profile revision."""
    batch = _batch_for_task(conn, task)
    if batch["review"] is None:
        raise Problem(409, "import_review_required")
    if batch["merged_profile_revision_id"] is not None:
        raise Problem(409, "import_already_merged")
    if conn.execute(
        text("SELECT :expiry<=clock_timestamp()"), {"expiry": batch["review_expires_at"]}
    ).scalar_one():
        raise Problem(409, "import_review_expired")
    profile = (
        conn.execute(
            text("""SELECT id,version,current_revision_id FROM bonafides.profiles
            WHERE owner_id=:owner FOR UPDATE"""),
            {"owner": task.owner_id},
        )
        .mappings()
        .one()
    )
    if int(profile["version"]) != int(batch["profile_version"]):
        raise Problem(412, "profile_version_conflict")
    review = ImportReview.model_validate(batch["review"])
    replacements, unchanged_targets = _validate_review(conn, task.owner_id, batch, review)
    projected = project_entries(
        batch["parsed_text"],
        batch["source_sha256"],
        [ImportProposal.model_validate(value) for value in batch["proposals"]],
    )
    sources = {part.source_id: part for entry in projected for part in entry.sources}
    entries = {frozenset(part.source_id for part in entry.sources): entry for entry in projected}
    snapshot = _snapshot(conn, task.owner_id)
    claim_ids = list(snapshot.claim_revision_ids)
    identity_updates: dict[str, object] = {}
    if review.mode == "replace_all":
        conn.execute(
            text("""UPDATE bonafides.professional_claims SET archived_at=clock_timestamp(),version=version+1
            WHERE owner_id=:owner AND archived_at IS NULL AND kind<>'identity'
            AND NOT(id=ANY(CAST(:targets AS uuid[])))"""),
            {"owner": task.owner_id, "targets": list(replacements)},
        )
        active = list(
            conn.execute(
                text("""SELECT current_revision_id FROM bonafides.professional_claims
            WHERE owner_id=:owner AND archived_at IS NULL ORDER BY created_at,id"""),
                {"owner": task.owner_id},
            ).scalars()
        )
        # Kept claims keep their profile order; claims saved in one transaction share a
        # creation time, so the identifier order would reshuffle an unchanged entry's lines.
        claim_ids = [value for value in claim_ids if value in active]
        claim_ids += [value for value in active if value not in claim_ids]
        # Preserve sources of unchanged targets and identity; archive genuinely displaced sources.
        conn.execute(
            text("""UPDATE bonafides.evidence_records e SET archived_at=clock_timestamp(),version=version+1
            WHERE e.owner_id=:owner AND e.archived_at IS NULL AND NOT EXISTS(
            SELECT 1 FROM bonafides.claim_evidence_links l JOIN bonafides.professional_claims c
            ON c.owner_id=l.owner_id AND c.current_revision_id=l.claim_revision_id
            JOIN bonafides.evidence_revisions r ON r.owner_id=l.owner_id AND r.id=l.evidence_revision_id
            WHERE c.owner_id=e.owner_id AND c.archived_at IS NULL
            AND (c.kind='identity' OR c.id=ANY(CAST(:unchanged AS uuid[]))) AND r.evidence_id=e.id)
            AND NOT EXISTS(SELECT 1 FROM bonafides.evidence_revisions r WHERE r.owner_id=e.owner_id
            AND r.id=e.current_revision_id AND r.payload->>'identity_field' IN ('display_name','headline'))"""),
            {
                "owner": task.owner_id,
                "unchanged": list(unchanged_targets),
            },
        )
    for decision in review.decisions:
        if decision.action in {"skip", "unchanged"}:
            continue
        parts = sorted(
            (sources[source_id] for source_id in decision.source_ids), key=lambda part: part.start
        )
        original = "\n".join(part.quote for part in parts)
        evidence_id, evidence_revision_id = uuid4(), uuid4()
        source_payload: dict[str, object] = {
            "text": original,
            "source": "Owner-reviewed local literal import",
            "import_id": str(batch["id"]),
            "sources": [part.model_dump(mode="json") for part in parts],
            "source_sha256": batch["source_sha256"],
            "extraction": "deterministic_literal",
            "assertion": "owner_asserted_not_independently_verified",
        }
        if decision.kind == "identity":
            source_payload["identity_field"] = decision.identity_field
        conn.execute(
            text(
                "INSERT INTO bonafides.evidence_records(owner_id,id,current_revision_id) VALUES(:owner,:id,:revision)"
            ),
            {"owner": task.owner_id, "id": evidence_id, "revision": evidence_revision_id},
        )
        conn.execute(
            # A literal résumé line is a private document the owner imported and reviewed.
            text("""INSERT INTO bonafides.evidence_revisions
            (owner_id,id,evidence_id,revision_no,payload,blob_id,content_sha256,
            source_type,origin,privacy_class,employer_owned)
            VALUES(:owner,:id,:evidence,1,CAST(:payload AS jsonb),:blob,:sha,
            'document','resume_import','private',false)"""),
            {
                "owner": task.owner_id,
                "id": evidence_revision_id,
                "evidence": evidence_id,
                "payload": canonical_json(source_payload),
                "blob": batch["blob_id"],
                "sha": content_hash(source_payload),
            },
        )
        if decision.kind == "identity":
            identity_updates[str(decision.identity_field)] = decision.payload.text
            continue
        if decision.action == "replace":
            if decision.claim_id is None:
                raise Problem(422, "import_target_invalid")
            claim_id = decision.claim_id
        else:
            claim_id = uuid4()
        statements = _statement_claims(
            entries.get(frozenset(decision.source_ids)), decision.kind, decision.payload
        )
        if decision.action == "add" and statements:
            for statement, excerpt in statements:
                claim_ids.append(
                    _insert_claim(
                        conn,
                        task.owner_id,
                        _ClaimInsert(profile["id"], decision.kind, uuid4(), True),
                        statement,
                        (evidence_revision_id, excerpt),
                    )
                )
            continue
        previous = (
            replacements[claim_id]["current_revision_id"] if claim_id in replacements else None
        )
        claim_revision_id = _insert_claim(
            conn,
            task.owner_id,
            _ClaimInsert(profile["id"], decision.kind, claim_id, decision.action == "add"),
            decision.payload.model_dump(mode="json", exclude_none=True),
            (evidence_revision_id, original),
        )
        claim_ids = [claim_revision_id if value == previous else value for value in claim_ids]
        if claim_revision_id not in claim_ids:
            claim_ids.append(claim_revision_id)
    if len(claim_ids) > 100:
        raise Problem(422, "claim_limit")
    # Recheck the cancellation/lease/deadline after constructing effects and before publication.
    # Any refusal rolls the complete transaction back, including all immutable rows above.
    assert_fence(conn, task)
    if review.mode == "merge" and all(
        item.action in {"skip", "unchanged"} for item in review.decisions
    ):
        revision_id = str(profile["current_revision_id"])
        profile_version = int(profile["version"])
    else:
        result = _save(
            conn,
            task.owner_id,
            snapshot.model_copy(update={"claim_revision_ids": claim_ids, **identity_updates}),
            "import_reviewed_merge",
        )
        revision = cast(dict[str, object], result["current_revision"])
        revision_id = str(revision["id"])
        profile_version = int(str(result["version"]))
    if conn.execute(
        text("SELECT :expiry<=clock_timestamp()"), {"expiry": batch["review_expires_at"]}
    ).scalar_one():
        raise Problem(409, "import_review_expired")
    assert_fence(conn, task)
    conn.execute(
        text("""UPDATE bonafides.import_batches SET merged_profile_revision_id=:revision
        WHERE owner_id=:owner AND id=:id"""),
        {"owner": task.owner_id, "id": batch["id"], "revision": UUID(revision_id)},
    )
    return {
        "import_id": str(batch["id"]),
        "profile_revision_id": revision_id,
        "profile_version": profile_version,
    }


def execute_import(db: Database, settings: Settings, task: Claim, interrupted: Event) -> None:
    with db.transaction() as conn:
        assert_fence(conn, task)
        batch = _batch_for_task(conn, task)
    if interrupted.is_set():
        raise ImportFailure("import_interrupted")
    if batch["review"] is not None:
        with db.transaction() as conn:
            # Match owner commands before taking the workflow/profile locks.
            lock_installation(conn)
            conn.execute(
                text(
                    "SELECT owner_id FROM bonafides.owner_settings WHERE owner_id=:owner FOR UPDATE"
                ),
                {"owner": task.owner_id},
            )
            publish(conn, task, lambda active: merge_reviewed(active, task))
        return
    # Three extraction attempts leave one of the fixed four total attempts for merge.
    # Approval never resets attempts, provider counters or the total cap.
    if task.attempt > 3:
        raise Problem(422, "import_parse_attempts_exhausted")
    try:
        data = BlobStore(settings.blob_root).read(
            task.owner_id, batch["storage_key"], batch["sha256"], int(batch["byte_length"])
        )
    except (OSError, ValueError) as exc:
        raise Problem(503, "import_original_unavailable") from exc
    started = time.monotonic()
    parsed = ImportParserClient(settings.import_spool).parse(data, batch["media_type"], interrupted)
    if interrupted.is_set():
        raise ImportFailure("import_interrupted")
    with db.transaction() as conn:
        stage_parsed(conn, task, parsed, elapsed_ms=int((time.monotonic() - started) * 1000))


def build_router(db: Database, settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    def upload(
        owner_id: UUID,
        data: bytes,
        media_type: str,
        if_match: str | None,
        idempotency_key: str | None,
    ) -> tuple[int, dict[str, object]]:
        if not data:
            raise Problem(422, "empty_import")
        if len(data) > MAX_UPLOAD_BYTES:
            raise Problem(413, "import_too_large")
        digest = hashlib.sha256(data).hexdigest()
        with db.transaction() as conn:
            profile_id = conn.execute(
                text("SELECT id FROM bonafides.profiles WHERE owner_id=:owner"), {"owner": owner_id}
            ).scalar_one()

            def effect(active: Connection) -> tuple[int, dict[str, object]]:
                counts = (
                    active.execute(
                        text("""SELECT count(*) AS batches,COALESCE(sum(b.byte_size),0) AS bytes,
                    count(*) FILTER(WHERE w.state IN ('pending','running','waiting_for_approval','retryable_failure')) AS active
                    FROM bonafides.import_batches b JOIN bonafides.workflow_runs w
                    ON w.owner_id=b.owner_id AND w.id=b.workflow_run_id WHERE b.owner_id=:owner"""),
                        {"owner": owner_id},
                    )
                    .mappings()
                    .one()
                )
                if (
                    counts["batches"] >= MAX_BATCHES
                    or counts["bytes"] + len(data) > MAX_IMPORT_BYTES
                    or counts["active"] >= MAX_ACTIVE_IMPORTS
                ):
                    raise Problem(422, "import_quota_exceeded")
                try:
                    blob = BlobStore(settings.blob_root).put_bytes(owner_id, data)
                except (OSError, ValueError) as exc:
                    raise Problem(503, "import_storage_unavailable") from exc
                blob_id = active.execute(
                    text("""INSERT INTO bonafides.blobs
                    (owner_id,id,sha256,byte_length,media_type,storage_key) VALUES(:owner,:id,:sha,:size,:media,:key)
                    ON CONFLICT(owner_id,sha256) DO UPDATE SET sha256=EXCLUDED.sha256 RETURNING id"""),
                    {
                        "owner": owner_id,
                        "id": uuid4(),
                        "sha": blob.sha256,
                        "size": blob.byte_length,
                        "media": media_type,
                        "key": blob.storage_key,
                    },
                ).scalar_one()
                import_id = uuid4()
                workflow_id = enqueue(
                    active, owner_id, "profile_import", {"import_id": str(import_id)}
                )
                active.execute(
                    text("""INSERT INTO bonafides.import_batches
                    (owner_id,id,workflow_run_id,blob_id,media_type,byte_size,profile_version)
                    SELECT :owner,:id,:workflow,:blob,:media,:size,version FROM bonafides.profiles WHERE owner_id=:owner"""),
                    {
                        "owner": owner_id,
                        "id": import_id,
                        "workflow": workflow_id,
                        "blob": blob_id,
                        "media": media_type,
                        "size": len(data),
                    },
                )
                return 202, {"import_id": str(import_id), "workflow_id": str(workflow_id)}

            return command(
                conn,
                owner_id,
                "POST",
                "/imports",
                {},
                {"source_sha256": digest, "byte_size": len(data), "media_type": media_type},
                if_match,
                idempotency_key,
                "profile",
                profile_id,
                effect,
            )

    @router.post("/imports")
    async def create_import(
        request: Request,
        owner: Annotated[Owner, Depends(require_owner)],
        if_match: Annotated[str | None, Header()] = None,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        parts = [
            part.strip().lower() for part in request.headers.get("content-type", "").split(";")
        ]
        media_type = parts[0]
        if media_type not in {"application/pdf", "text/plain"} or (
            len(parts) > 1
            and (
                media_type != "text/plain"
                or parts[1:] not in [["charset=utf-8"], ['charset="utf-8"']]
            )
        ):
            raise Problem(415, "unsupported_import_media")
        data = await request.body()
        status, result = await run_in_threadpool(
            upload, owner.id, data, media_type, if_match, idempotency_key
        )
        return JSONResponse(
            result,
            status_code=status,
            headers={"Location": f"/api/v1/imports/{result['import_id']}"},
        )

    @router.get("/imports")
    def list_imports(owner: Annotated[Owner, Depends(require_owner)]) -> dict[str, object]:
        with db.transaction() as conn:
            rows = conn.execute(
                text("""SELECT b.id,b.media_type,b.created_at,w.state
                    FROM bonafides.import_batches b JOIN bonafides.workflow_runs w
                    ON w.owner_id=b.owner_id AND w.id=b.workflow_run_id
                    WHERE b.owner_id=:owner ORDER BY b.created_at DESC,b.id LIMIT 100"""),
                {"owner": owner.id},
            ).mappings()
            return {
                "items": [
                    {
                        "id": str(row["id"]),
                        "media_type": row["media_type"],
                        "created_at": row["created_at"].isoformat(),
                        "state": row["state"],
                    }
                    for row in rows
                ]
            }

    @router.get("/imports/{import_id}")
    def detail(import_id: UUID, owner: Annotated[Owner, Depends(require_owner)]) -> JSONResponse:
        with db.transaction() as conn:
            result = get_batch(conn, owner.id, import_id)
        return JSONResponse(result, headers={"ETag": f'"{result["version"]}"'})

    @router.post("/imports/{import_id}/merge")
    def merge(
        import_id: UUID,
        body: ImportReview,
        owner: Annotated[Owner, Depends(require_owner)],
        if_match: Annotated[str | None, Header()] = None,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        with db.transaction() as conn:
            profile_id = conn.execute(
                text("SELECT id FROM bonafides.profiles WHERE owner_id=:owner"), {"owner": owner.id}
            ).scalar_one()

            def effect(active: Connection) -> tuple[int, dict[str, object]]:
                batch = (
                    active.execute(
                        text("""SELECT b.*,w.state,w.cancel_requested_at,w.maintenance_epoch
                    FROM bonafides.import_batches b JOIN bonafides.workflow_runs w
                    ON w.owner_id=b.owner_id AND w.id=b.workflow_run_id
                    WHERE b.owner_id=:owner AND b.id=:id FOR UPDATE OF w,b"""),
                        {"owner": owner.id, "id": import_id},
                    )
                    .mappings()
                    .one_or_none()
                )
                if batch is None:
                    raise Problem(404, "import_not_found")
                if (
                    batch["state"] != "waiting_for_approval"
                    or batch["cancel_requested_at"] is not None
                    or batch["review"] is not None
                ):
                    raise Problem(409, "import_not_waiting")
                if active.execute(
                    text("SELECT :expiry<=clock_timestamp()"),
                    {"expiry": batch["review_expires_at"]},
                ).scalar_one():
                    raise Problem(409, "import_review_expired")
                if int(batch["maintenance_epoch"]) != lock_installation(active):
                    raise Problem(409, "import_epoch_changed")
                _validate_review(active, owner.id, batch, body)
                active.execute(
                    text("""UPDATE bonafides.import_batches SET review=CAST(:review AS jsonb),
                    reviewed_at=clock_timestamp(),profile_version=(SELECT version FROM bonafides.profiles WHERE owner_id=:owner)
                    WHERE owner_id=:owner AND id=:id"""),
                    {
                        "owner": owner.id,
                        "id": import_id,
                        "review": canonical_json(body.model_dump(mode="json")),
                    },
                )
                version = active.execute(
                    text("""UPDATE bonafides.workflow_runs SET state='pending',
                    deadline_at=clock_timestamp()+interval '60 seconds',next_attempt_at=clock_timestamp(),
                    version=version+1,updated_at=clock_timestamp(),last_error_code=NULL
                    WHERE owner_id=:owner AND id=:workflow RETURNING version"""),
                    {"owner": owner.id, "workflow": batch["workflow_run_id"]},
                ).scalar_one()
                append_attempt_event(active, owner.id, batch["workflow_run_id"], "approved")
                append_outbox(
                    active,
                    owner.id,
                    "workflow.queued",
                    "workflow",
                    batch["workflow_run_id"],
                    {"workflow_id": str(batch["workflow_run_id"])},
                    int(version),
                )
                return 202, {
                    "import_id": str(import_id),
                    "workflow_id": str(batch["workflow_run_id"]),
                }

            status, result = command(
                conn,
                owner.id,
                "POST",
                "/imports/{import_id}/merge",
                {"import_id": str(import_id)},
                body.model_dump(mode="json"),
                if_match,
                idempotency_key,
                "profile",
                profile_id,
                effect,
            )
        return JSONResponse(result, status_code=status)

    return router
