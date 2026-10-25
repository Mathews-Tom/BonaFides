# SPDX-License-Identifier: MIT OR Apache-2.0
"""Owner-reviewed requirements, conservative evidence coverage and fenced proposals.

Token checks are only novelty guards, never a proof of semantic entailment. The
owner reviews every published assessment; a model can only propose review data.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from threading import Event
from typing import Annotated, Literal, Protocol, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header
from fastapi.responses import JSONResponse
from pydantic import ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import Connection, text
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import DBAPIError

from offerloom import local_model
from offerloom.auth import Owner, StrictModel, audit, require_owner
from offerloom.commands import append_outbox, canonical_json, command, lock_installation
from offerloom.config import Settings
from offerloom.db import Database
from offerloom.problems import Problem
from offerloom.workflows import Claim, assert_fence, enqueue, publish

POLICY_VERSION = "ol-suitability-policy-v2"
TEMPLATE_VERSION = "ol-suitability-review-v1"
_STOP = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "demonstrate",
        "demonstrated",
        "experience",
        "experienced",
        "for",
        "from",
        "have",
        "in",
        "is",
        "knowledge",
        "of",
        "on",
        "or",
        "professional",
        "proficiency",
        "required",
        "requires",
        "requirement",
        "skills",
        "skill",
        "strong",
        "the",
        "to",
        "using",
        "used",
        "with",
        "work",
        "working",
        "years",
        "year",
        "supported",
        "evidence",
        "this",
        "claim",
        "shows",
        "show",
        "direct",
        "owner",
        "asserted",
        "has",
        "maintained",
        "wrote",
        "queries",
        "ability",
        "preferred",
        "demonstrates",
        "provides",
        "provide",
        "establishes",
        "establish",
        "reviewed",
        "indicates",
        "documented",
        "states",
        "related",
        "relevant",
        "supports",
        "support",
        "it",
        "does",
        "not",
        "no",
        "fully",
        "partially",
        "explicitly",
        "confirms",
        "confirmed",
        "matching",
        "linked",
        "cited",
        "citation",
        "only",
    ]
)
_TECH = frozenset(
    [
        "linux",
        "sql",
        "kubernetes",
        "python",
        "java",
        "javascript",
        "typescript",
        "rust",
        "golang",
        "c++",
        "c#",
        "docker",
        "postgresql",
        "mysql",
        "sqlite",
        "aws",
        "azure",
        "gcp",
        "terraform",
        "ansible",
        "react",
        "angular",
        "windows",
        "unix",
        "scala",
        "ruby",
        "php",
        "redis",
        "kafka",
        "mongodb",
        "spark",
        "hadoop",
        "swift",
        "kotlin",
        "nodejs",
        "git",
        "bash",
    ]
)
_ALIASES = {"k8s": "kubernetes", "postgres": "postgresql", "node.js": "nodejs"}


def normalize(value: str) -> str:
    return unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))


def _sha(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _text_sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _literal(value: str) -> str:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("invalid_unicode") from exc
    if "\x00" in value or not value.strip():
        raise ValueError("invalid_text")
    return value


def _tokens(value: str) -> set[str]:
    return {
        _ALIASES.get(token, token)
        for token in re.findall(r"[\w]+(?:[.+#-][\w+#]+)*", normalize(value).casefold())
    }


def _anchors(value: str) -> set[str]:
    tokens = _tokens(value)
    return (tokens & _TECH) | {token for token in tokens if any(c.isdigit() for c in token)}


def _comparison(value: str | None) -> str:
    return " ".join(normalize(value or "").casefold().split())


class ReviewModel(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SourceSpan(ReviewModel):
    start: int = Field(ge=0, le=200000)
    end: int = Field(ge=1, le=200000)
    quote: str = Field(min_length=1, max_length=2000)


class HardConstraint(ReviewModel):
    kind: Literal["work_authorization", "location", "salary", "other"]
    operator: Literal["manual", "equals", "contains", "minimum", "maximum"]
    required_value: str | None = Field(default=None, max_length=300)
    owner_value: str | None = Field(default=None, max_length=300)
    jurisdiction: str | None = Field(default=None, max_length=200)
    owner_jurisdiction: str | None = Field(default=None, max_length=200)
    currency: str | None = Field(default=None, max_length=30)
    owner_currency: str | None = Field(default=None, max_length=30)
    period: str | None = Field(default=None, max_length=30)
    owner_period: str | None = Field(default=None, max_length=30)
    owner_confirmed: bool = False
    contradictory: bool = False


class Requirement(ReviewModel):
    requirement_key: str = Field(pattern=r"^req-[0-9]{1,3}$")
    text: str = Field(min_length=1, max_length=1000)
    importance: Literal["required", "preferred", "unclear"]
    span: SourceSpan
    hard_constraint: HardConstraint | None = None

    @field_validator("text")
    @classmethod
    def literal_text(cls, value: str) -> str:
        return _literal(value)


class RequirementsWrite(ReviewModel):
    confirmed: Literal[True]
    requirements: list[Requirement] = Field(max_length=100)

    @field_validator("confirmed", mode="before")
    @classmethod
    def explicit_confirmation(cls, value: object) -> object:
        if value is not True:
            raise ValueError("explicit_confirmation_required")
        return value


class Coverage(StrictModel):
    requirement_key: str = Field(pattern=r"^req-[0-9]{1,3}$")
    coverage: Literal["supported", "partial", "not_evidenced", "conflicting"]
    claim_revision_ids: list[UUID] = Field(max_length=100)
    evidence_revision_ids: list[UUID] = Field(max_length=200)
    explanation: str = Field(min_length=1, max_length=1000)

    @field_validator("explanation")
    @classmethod
    def literal_explanation(cls, value: str) -> str:
        return _literal(value)


class CoverageWrite(StrictModel):
    model_config = ConfigDict(extra="forbid")
    profile_revision_id: UUID
    requirements_revision_id: UUID
    confirmed: Literal[True]
    assessments: list[Coverage] = Field(max_length=100)

    @field_validator("confirmed", mode="before")
    @classmethod
    def explicit_confirmation(cls, value: object) -> object:
        if value is not True:
            raise ValueError("explicit_confirmation_required")
        return value


class MatchRequest(StrictModel):
    profile_revision_id: UUID
    requirements_revision_id: UUID


class ExtractRequest(ReviewModel):
    pass


class ProposedRequirement(ReviewModel):
    requirement_key: str = Field(pattern=r"^req-[0-9]{1,3}$")
    text: str = Field(min_length=1, max_length=1000)
    importance: Literal["required", "preferred", "unclear"]
    span: SourceSpan


class ExtractionOutput(ReviewModel):
    schema_version: Literal["1"]
    stage: Literal["extract_posting"]
    title: str | None = Field(max_length=300)
    company: str | None = Field(max_length=300)
    requirements: list[ProposedRequirement] = Field(max_length=100)
    warnings: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(max_length=20)


class ModelCoverage(Coverage):
    claim_revision_ids: list[UUID] = Field(max_length=20)
    evidence_revision_ids: list[UUID] = Field(max_length=20)


class MatchingOutput(ReviewModel):
    schema_version: Literal["1"]
    stage: Literal["match_requirements"]
    assessments: list[ModelCoverage] = Field(max_length=100)
    summary: str = Field(max_length=2000)
    warnings: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(max_length=20)


def _posting(conn: Connection, owner_id: UUID, posting_id: UUID) -> RowMapping:
    row = (
        conn.execute(
            text("SELECT * FROM offerloom.job_postings WHERE owner_id=:owner AND id=:id FOR SHARE"),
            {"owner": owner_id, "id": posting_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise Problem(404, "posting_not_found")
    return row


def _posting_digest(row: RowMapping) -> str:
    # Not posting.version: save/dismiss/archive never change source content.
    return _sha(
        {
            "title": normalize(str(row["title"])),
            "company": normalize(str(row["company"])),
            "location_text": normalize(str(row["location_text"])),
            "source_text": normalize(str(row["description_text"])),
            "canonical_url": row["canonical_url"],
            "source_content_sha256": row["content_sha256"],
        }
    )


def _current_requirements(conn: Connection, owner_id: UUID, posting_id: UUID) -> RowMapping | None:
    return (
        conn.execute(
            text("""SELECT * FROM offerloom.posting_requirement_revisions
        WHERE owner_id=:owner AND posting_id=:posting ORDER BY revision_no DESC LIMIT 1"""),
            {"owner": owner_id, "posting": posting_id},
        )
        .mappings()
        .one_or_none()
    )


def _requirements_view(row: RowMapping, posting: RowMapping) -> dict[str, object]:
    return {
        "id": str(row["id"]),
        "revision_no": row["revision_no"],
        "source_sha256": row["source_sha256"],
        "requirements": row["requirements"],
        "confirmed": row["confirmed"],
        "created_at": row["created_at"].isoformat(),
        "provenance": row["provenance"],
        "stale": row["posting_content_sha256"] != _posting_digest(posting),
    }


def _route_status(conn: Connection, owner_id: UUID, settings: Settings) -> dict[str, object]:
    try:
        route = local_model.load_route(settings.local_model_route_file)
    except local_model.ModelFailure:
        return {"status": "unqualified", "reason": "model_route_invalid", "identity": None}
    if route is None:
        return {"status": "disabled", "reason": "model_route_not_configured", "identity": None}
    qualified = all(
        route.qualified_for(stage) for stage in ("extract_posting", "match_requirements")
    )
    blocked = (
        conn.execute(
            text(
                "SELECT stage FROM offerloom.local_model_route_blocks WHERE owner_id=:owner AND route_sha256=:sha AND stage IN ('extract_posting','match_requirements')"
            ),
            {"owner": owner_id, "sha": _sha(route.identity())},
        ).first()
        is not None
    )
    if blocked:
        return {
            "status": "unqualified",
            "reason": "model_route_failed_live_evaluation",
            "identity": route.identity(),
        }
    return {
        "status": "qualified" if qualified else "unqualified",
        "reason": "operator_qualified_local_route" if qualified else "model_route_not_qualified",
        "identity": route.identity(),
    }


def _route(
    conn: Connection, owner_id: UUID, settings: Settings, stage: str
) -> local_model.LocalRoute:
    try:
        route = local_model.load_route(settings.local_model_route_file)
    except local_model.ModelFailure as exc:
        raise Problem(503, "model_route_unavailable") from exc
    if route is None or not route.qualified_for(stage):
        raise Problem(503, "model_route_unavailable")
    blocked = (
        conn.execute(
            text("""SELECT stage FROM offerloom.local_model_route_blocks
        WHERE owner_id=:owner AND route_sha256=:sha AND stage=:stage"""),
            {"owner": owner_id, "sha": _sha(route.identity()), "stage": stage},
        ).first()
        is not None
    )
    if blocked:
        raise Problem(503, "model_route_unavailable")
    return route


def _settings_version(conn: Connection, owner_id: UUID) -> int:
    return int(
        conn.execute(
            text("SELECT version FROM offerloom.owner_settings WHERE owner_id=:owner"),
            {"owner": owner_id},
        ).scalar_one()
    )


def _policy(conn: Connection, owner_id: UUID, settings: Settings) -> dict[str, object]:
    return {
        "policy_version": POLICY_VERSION,
        "template_version": TEMPLATE_VERSION,
        "prompt_versions": dict(local_model.PROMPT_VERSIONS),
        "model_route": _route_status(conn, owner_id, settings),
    }


def _stale_reasons(
    conn: Connection,
    owner_id: UUID,
    posting: RowMapping,
    requirement: RowMapping | None,
    assessment: RowMapping,
    settings: Settings,
) -> list[str]:
    provenance = cast(dict[str, object], assessment["provenance"])
    reasons: list[str] = []
    current_profile = conn.execute(
        text("SELECT current_revision_id FROM offerloom.profiles WHERE owner_id=:owner"),
        {"owner": owner_id},
    ).scalar_one()
    if current_profile != assessment["profile_revision_id"]:
        reasons.append("profile_changed")
    if requirement is None or requirement["id"] != assessment["requirements_revision_id"]:
        reasons.append("requirements_changed")
    if provenance.get("posting_content_sha256") != _posting_digest(posting):
        reasons.append("posting_content_changed")
    if provenance.get("policy") != _policy(conn, owner_id, settings):
        reasons.append("model_prompt_or_template_changed")
    if provenance.get("settings_version") != _settings_version(conn, owner_id):
        reasons.append("settings_changed")
    return reasons


def _assessment_view(
    conn: Connection,
    owner_id: UUID,
    posting: RowMapping,
    requirement: RowMapping | None,
    row: RowMapping,
    settings: Settings,
) -> dict[str, object]:
    assessments = cast(list[dict[str, object]], row["evidence_coverage"])
    counts = {
        key: sum(item.get("coverage") == key for item in assessments)
        for key in ("supported", "partial", "not_evidenced", "conflicting")
    }
    counts["total"] = len(assessments)
    reasons = _stale_reasons(conn, owner_id, posting, requirement, row, settings)
    return {
        "id": str(row["id"]),
        "profile_revision_id": str(row["profile_revision_id"]),
        "requirements_revision_id": str(row["requirements_revision_id"])
        if row["requirements_revision_id"] is not None
        else None,
        "created_at": row["created_at"].isoformat(),
        "stale": bool(reasons),
        "stale_reasons": reasons,
        "hard_eligibility": row["hard_eligibility"],
        "hard_constraints": row["hard_constraints"],
        "assessments": assessments,
        "evidence_coverage": counts,
        "provenance": row["provenance"],
    }


def get_requirements(
    conn: Connection,
    owner_id: UUID,
    posting_id: UUID,
    settings: Settings,
) -> dict[str, object]:
    posting = _posting(conn, owner_id, posting_id)
    requirement = _current_requirements(conn, owner_id, posting_id)
    latest = (
        conn.execute(
            text("""SELECT * FROM offerloom.match_assessments WHERE owner_id=:owner
        AND posting_id=:posting ORDER BY created_at DESC,id DESC LIMIT 1"""),
            {"owner": owner_id, "posting": posting_id},
        )
        .mappings()
        .one_or_none()
    )
    source = normalize(str(posting["description_text"]))
    return {
        "posting_id": str(posting_id),
        "version": posting["version"],
        "source_text": source,
        "source_sha256": _text_sha(source),
        "current_revision": _requirements_view(requirement, posting)
        if requirement is not None
        else None,
        "model_route": _route_status(conn, owner_id, settings),
        "latest_assessment": _assessment_view(
            conn, owner_id, posting, requirement, latest, settings
        )
        if latest is not None
        else None,
    }


def validate_requirements(requirements: list[Requirement], source: str) -> None:
    keys: set[str] = set()
    for item in requirements:
        if item.requirement_key in keys:
            raise Problem(422, "duplicate_requirement_key")
        keys.add(item.requirement_key)
        span = item.span
        if (
            not 0 <= span.start < span.end <= len(source)
            or source[span.start : span.end] != span.quote
        ):
            raise Problem(422, "invalid_source_span")
        _literal(span.quote)
        if _anchors(item.text) - _anchors(span.quote) or (_tokens(item.text) - _STOP) - _tokens(
            span.quote
        ):
            raise Problem(422, "unsupported_requirement_novelty")
        constraint = item.hard_constraint
        if constraint is not None:
            # Source facts must actually be stated. Owner facts are separate review fields.
            for value in (
                constraint.required_value,
                constraint.jurisdiction,
                constraint.currency,
                constraint.period,
            ):
                if value is not None and _comparison(value):
                    tokens = _tokens(value)
                    stated = (
                        tokens <= _tokens(span.quote)
                        if tokens
                        else _comparison(value) in _comparison(span.quote)
                    )
                    if not stated:
                        raise Problem(422, "constraint_not_in_source")


def _bump(conn: Connection, owner_id: UUID, posting_id: UUID, event: str, item_id: UUID) -> None:
    version = conn.execute(
        text("""UPDATE offerloom.job_postings SET version=version+1,updated_at=clock_timestamp()
        WHERE owner_id=:owner AND id=:id RETURNING version"""),
        {"owner": owner_id, "id": posting_id},
    ).scalar_one()
    append_outbox(
        conn,
        owner_id,
        event,
        "posting",
        posting_id,
        {"posting_id": str(posting_id), "revision_id": str(item_id)},
        int(version),
    )
    audit(conn, owner_id, event)


def save_requirements(
    conn: Connection,
    owner_id: UUID,
    posting_id: UUID,
    body: RequirementsWrite,
    settings: Settings,
) -> dict[str, object]:
    posting = _posting(conn, owner_id, posting_id)
    source = normalize(str(posting["description_text"]))
    validate_requirements(body.requirements, source)
    current = _current_requirements(conn, owner_id, posting_id)
    revision_id = uuid4()
    requirements = [item.model_dump(mode="json") for item in body.requirements]
    provenance = {
        "origin": "owner_reviewed",
        "policy_version": POLICY_VERSION,
        "semantic_review": "owner_required_token_guards_are_not_entailment_proof",
    }
    conn.execute(
        text("""INSERT INTO offerloom.posting_requirement_revisions
        (owner_id,id,posting_id,revision_no,source_text,source_sha256,posting_content_sha256,
        requirements,confirmed,provenance) VALUES(:owner,:id,:posting,:number,:source,:sha,:content,
        CAST(:requirements AS jsonb),true,CAST(:provenance AS jsonb))"""),
        {
            "owner": owner_id,
            "id": revision_id,
            "posting": posting_id,
            "number": int(current["revision_no"]) + 1 if current is not None else 1,
            "source": source,
            "sha": _text_sha(source),
            "content": _posting_digest(posting),
            "requirements": canonical_json(requirements),
            "provenance": canonical_json(provenance),
        },
    )
    conn.execute(
        text("""UPDATE offerloom.job_postings SET requirements=CAST(:requirements AS jsonb),
        requirements_reviewed_at=clock_timestamp() WHERE owner_id=:owner AND id=:posting"""),
        {"owner": owner_id, "posting": posting_id, "requirements": canonical_json(requirements)},
    )
    _bump(conn, owner_id, posting_id, "posting.requirements_reviewed", revision_id)
    return get_requirements(conn, owner_id, posting_id, settings)


@dataclass(frozen=True)
class ProfileProjection:
    claims: dict[UUID, dict[str, object]]
    evidence: dict[UUID, dict[str, object]]
    links: dict[UUID, set[UUID]]


def _profile_projection(conn: Connection, owner_id: UUID, revision_id: UUID) -> ProfileProjection:
    row = (
        conn.execute(
            text("""SELECT p.current_revision_id,r.snapshot FROM offerloom.profiles p
        JOIN offerloom.profile_revisions r ON r.owner_id=p.owner_id AND r.id=:revision
        AND r.profile_id=p.id WHERE p.owner_id=:owner FOR SHARE OF p"""),
            {"owner": owner_id, "revision": revision_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise Problem(422, "invalid_profile_revision")
    if row["current_revision_id"] != revision_id:
        raise Problem(409, "stale_profile_revision")
    snapshot = cast(dict[str, list[str]], row["snapshot"])
    claims: dict[UUID, dict[str, object]] = {}
    evidence: dict[UUID, dict[str, object]] = {}
    links: dict[UUID, set[UUID]] = {}
    for value in snapshot.get("evidence_revision_ids", []):
        identifier = UUID(value)
        found = (
            conn.execute(
                text("""SELECT r.payload FROM offerloom.evidence_revisions r JOIN offerloom.evidence_records e
            ON e.owner_id=r.owner_id AND e.id=r.evidence_id WHERE r.owner_id=:owner AND r.id=:id
            AND e.archived_at IS NULL AND e.current_revision_id=r.id FOR SHARE OF e"""),
                {"owner": owner_id, "id": identifier},
            )
            .mappings()
            .one_or_none()
        )
        if found is not None:
            payload = cast(dict[str, object], found["payload"])
            evidence[identifier] = {"id": str(identifier), "text": str(payload.get("text", ""))}
    for value in snapshot.get("claim_revision_ids", []):
        identifier = UUID(value)
        found = (
            conn.execute(
                text("""SELECT r.payload FROM offerloom.claim_revisions r JOIN offerloom.professional_claims c
            ON c.owner_id=r.owner_id AND c.id=r.claim_id WHERE r.owner_id=:owner AND r.id=:id
            AND c.archived_at IS NULL AND c.current_revision_id=r.id FOR SHARE OF c"""),
                {"owner": owner_id, "id": identifier},
            )
            .mappings()
            .one_or_none()
        )
        if found is None:
            continue
        permitted: set[UUID] = set()
        for link in conn.execute(
            text("""SELECT evidence_revision_id,support_kind FROM offerloom.claim_evidence_links
            WHERE owner_id=:owner AND claim_revision_id=:id"""),
            {"owner": owner_id, "id": identifier},
        ).mappings():
            if link["evidence_revision_id"] in evidence and link["support_kind"] in {
                "direct",
                "user_asserted",
            }:
                permitted.add(link["evidence_revision_id"])
        payload = cast(dict[str, object], found["payload"])
        claims[identifier] = {
            "id": str(identifier),
            "text": str(payload.get("text", "")),
            "evidence_revision_ids": sorted(str(item) for item in permitted),
        }
        links[identifier] = permitted
    return ProfileProjection(claims, evidence, links)


def _reviewed_requirements(
    conn: Connection,
    owner_id: UUID,
    posting: RowMapping,
    revision_id: UUID,
) -> tuple[RowMapping, list[Requirement]]:
    current = _current_requirements(conn, owner_id, posting["id"])
    if current is None or not current["requirements"]:
        raise Problem(409, "requirements_unavailable")
    if current["id"] != revision_id or current["posting_content_sha256"] != _posting_digest(
        posting
    ):
        raise Problem(409, "stale_requirements_revision")
    requirements = [Requirement.model_validate(item) for item in current["requirements"]]
    validate_requirements(requirements, normalize(str(posting["description_text"])))
    return current, requirements


_GAP_EVIDENCE = (
    r"(?:the\s+)?(?:(?:reviewed|available|cited|selected|provided|current)\s+){0,3}"
    r"(?:evidence|support|claims?|citations?)"
)
_GAP_PATTERNS = (
    re.compile(
        r"(?:no|missing|insufficient|absent|unavailable|not enough)\s+"
        + _GAP_EVIDENCE
        + r"\s+(?:for|of|establishes|supports|shows|confirms|demonstrates|documents)\s+"
        r"(?P<topic>.+)"
    ),
    re.compile(
        _GAP_EVIDENCE + r"\s+(?:does not|do not|cannot|can not)\s+"
        r"(?:establish|support|show|confirm|demonstrate|document)\s+(?P<topic>.+)"
    ),
    re.compile(
        _GAP_EVIDENCE + r"\s+(?:is|are)\s+(?:missing|absent|insufficient|unavailable)\s+"
        r"(?:for|of)\s+(?P<topic>.+)"
    ),
    re.compile(
        r"(?P<topic>.+?)\s+(?:(?:is|are|remains?)\s+)?"
        r"(?:not\s+(?:evidenced|established|supported|documented|demonstrated|confirmed|shown)"
        r"|unknown|unclear|unavailable)"
        r"(?:\s+(?:by|in|from)\s+" + _GAP_EVIDENCE + r")?"
    ),
)


def _gap_topic(topic: str, requirement: str) -> bool:
    """A source-derived noun phrase, not an arbitrary clause after a gap prefix."""
    if topic in {
        "this requirement",
        "these requirements",
        "the requirement",
        "the requirements",
        "requirement",
        "requirements",
    }:
        return True
    if re.fullmatch(r"[\w]+(?:[.+#-][\w+#]+)*(?:\s+[\w]+(?:[.+#-][\w+#]+)*)*", topic) is None:
        return False
    words = [_ALIASES.get(word, word) for word in topic.split()]
    if words and words[0] == "the":
        words = words[1:]
    nouns = (_tokens(requirement) - _STOP) | {"experience", "skill", "skills", "proficiency"}
    expecting_noun = True
    for word in words:
        if word in {"and", "or"}:
            if expecting_noun:
                return False
            expecting_noun = True
        elif word in nouns:
            expecting_noun = False
        else:
            return False
    return bool(words) and not expecting_noun


def _requirement_gap(clause: str, requirement: str) -> bool:
    """Recognize complete absence constructions with only a bounded requirement topic."""
    wording = _comparison(clause)
    for pattern in _GAP_PATTERNS:
        match = pattern.fullmatch(wording)
        if match is not None and _gap_topic(match.group("topic"), requirement):
            return True
    return False


def _validate_explanation(
    explanation: str,
    requirement: str,
    claim_text: str,
    evidence_text: str,
) -> None:
    cited_words = _tokens(claim_text) | _tokens(evidence_text)
    cited_anchors = _anchors(claim_text) & _anchors(evidence_text)
    # A gap sentence cannot launder a separate applicant assertion after it.
    for clause in re.split(r"[;!?\n]+|\.(?=\s|$)", explanation):
        if not clause.strip():
            continue
        novel_anchors = _anchors(clause) - cited_anchors
        novel_words = (_tokens(clause) - _STOP) - cited_words
        if (novel_anchors or novel_words) and not _requirement_gap(clause, requirement):
            raise Problem(422, "unsupported_explanation_novelty")


def validate_coverage(
    assessments: Sequence[Coverage],
    requirements: list[Requirement],
    profile: ProfileProjection,
) -> None:
    required = {item.requirement_key: item for item in requirements}
    keys = [item.requirement_key for item in assessments]
    if len(keys) != len(set(keys)) or set(keys) != set(required):
        raise Problem(422, "incomplete_requirement_coverage")
    for item in assessments:
        claims, evidence = set(item.claim_revision_ids), set(item.evidence_revision_ids)
        if len(claims) != len(item.claim_revision_ids) or len(evidence) != len(
            item.evidence_revision_ids
        ):
            raise Problem(422, "duplicate_coverage_citation")
        if not claims <= profile.claims.keys() or not evidence <= profile.evidence.keys():
            raise Problem(422, "invalid_coverage_citation")
        connected: set[UUID] = set().union(*(profile.links[identifier] for identifier in claims))
        if not evidence <= connected:
            raise Problem(422, "unconnected_coverage_citation")
        claim_text = " ".join(str(profile.claims[c]["text"]) for c in claims)
        evidence_text = " ".join(str(profile.evidence[e]["text"]) for e in evidence)
        requirement = required[item.requirement_key].text
        if item.coverage in {"supported", "partial"}:
            if not claims or not evidence or any(not profile.links[c] & evidence for c in claims):
                raise Problem(422, "positive_coverage_requires_connected_evidence")
            # Both the accepted claim and its cited evidence must bear on the requirement.
            anchors = _anchors(requirement)
            relevant = anchors | (_tokens(requirement) - _STOP)
            supported = _tokens(claim_text) & _tokens(evidence_text)
            if not relevant & supported or (
                item.coverage == "supported" and not relevant <= supported
            ):
                raise Problem(422, "unsupported_coverage_novelty")
        _validate_explanation(item.explanation, requirement, claim_text, evidence_text)


def _constraint_state(requirement: Requirement) -> tuple[str, str]:
    item = requirement.hard_constraint
    assert item is not None
    if requirement.importance != "required":
        return "unknown", "not_a_reviewed_mandatory_condition"
    if item.contradictory:
        return "unknown", "contradictory_source_or_owner_facts"
    if not item.owner_confirmed:
        return "unknown", "owner_facts_not_confirmed"
    required, owner = _comparison(item.required_value), _comparison(item.owner_value)
    if not required or not owner or not _tokens(required) or not _tokens(owner):
        return "unknown", "missing_explicit_facts"
    if item.kind == "work_authorization":
        if not _comparison(item.jurisdiction) or not _comparison(item.owner_jurisdiction):
            return "unknown", "missing_explicit_authorization_jurisdiction"
        if _comparison(item.jurisdiction) != _comparison(item.owner_jurisdiction):
            return "unknown", "authorization_jurisdictions_incomparable"
        source = _comparison(requirement.span.quote)
        if not any(
            re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", source)
            for phrase in (
                "work authorization",
                "work authorisation",
                "authorized to work",
                "authorised to work",
                "work permit",
                "right to work",
                "permission to work",
                "eligible to work",
            )
        ):
            return "unknown", "source_does_not_state_work_authorization"
        if _tokens(source) & {"not", "no", "unless", "except", "without"}:
            return "unknown", "authorization_condition_requires_interpretation"
        if not _tokens(owner) & {
            "authorization",
            "authorisation",
            "authorized",
            "authorised",
            "permission",
            "permit",
            "eligible",
            "allowed",
        }:
            return "unknown", "missing_explicit_owner_authorization_assertion"
        if item.operator != "equals":
            return "unknown", "authorization_requires_explicit_comparison_no_legal_inference"
    if item.kind == "salary":
        if (
            not _comparison(item.currency)
            or not _comparison(item.period)
            or _comparison(item.currency) != _comparison(item.owner_currency)
            or _comparison(item.period) != _comparison(item.owner_period)
        ):
            return "unknown", "salary_currency_or_period_incomparable"
        if item.operator not in {"minimum", "maximum", "equals"}:
            return "unknown", "salary_operator_requires_review"
        if any(
            re.fullmatch(r"[0-9]{1,12}(?:\.[0-9]{1,4})?", value) is None
            for value in (required, owner)
        ):
            return "unknown", "salary_not_explicit_decimal"
        left, right = Decimal(owner), Decimal(required)
        met = (
            left >= right
            if item.operator == "minimum"
            else left <= right
            if item.operator == "maximum"
            else left == right
        )
    elif item.operator == "equals":
        met = owner == required
    elif item.operator == "contains" and item.kind != "work_authorization":
        met = _tokens(required) <= _tokens(owner)
    else:
        return "unknown", "manual_or_legal_interpretation_required"
    return (
        ("met", "explicit_owner_comparison_met")
        if met
        else ("not_met", "explicit_owner_comparison_not_met")
    )


def _contradictory_keys(requirements: list[Requirement]) -> set[str]:
    groups: dict[tuple[str, str, str, str, str, int, int, str], list[Requirement]] = {}
    for requirement in requirements:
        item = requirement.hard_constraint
        if item is None or requirement.importance != "required" or item.kind == "other":
            continue
        # `other` has no criterion identity. Typed constraints still share a
        # criterion only when they review the same literal source requirement.
        group = (
            item.kind,
            _comparison(item.jurisdiction),
            _comparison(item.currency),
            _comparison(item.period),
            _comparison(requirement.text),
            requirement.span.start,
            requirement.span.end,
            requirement.span.quote,
        )
        groups.setdefault(group, []).append(requirement)
    conflicting: set[str] = set()
    for entries in groups.values():
        equals: set[str] = set()
        minimums: list[Decimal] = []
        maximums: list[Decimal] = []
        for requirement in entries:
            item = requirement.hard_constraint
            assert item is not None
            value = _comparison(item.required_value)
            if item.operator == "equals" and value:
                equals.add(value)
            if item.kind == "salary" and re.fullmatch(r"[0-9]{1,12}(?:\.[0-9]{1,4})?", value):
                if item.operator == "minimum":
                    minimums.append(Decimal(value))
                elif item.operator == "maximum":
                    maximums.append(Decimal(value))
        if len(equals) > 1 or (minimums and maximums and max(minimums) > min(maximums)):
            conflicting.update(requirement.requirement_key for requirement in entries)
    return conflicting


def evaluate_constraints(requirements: list[Requirement]) -> tuple[str, list[dict[str, object]]]:
    results: list[dict[str, object]] = []
    contradictory = _contradictory_keys(requirements)
    for requirement in requirements:
        if requirement.hard_constraint is None:
            continue
        state, reason = _constraint_state(requirement)
        if requirement.requirement_key in contradictory:
            state, reason = "unknown", "contradictory_reviewed_conditions"
        results.append(
            {"requirement_key": requirement.requirement_key, "state": state, "reason": reason}
        )
    if not results:
        return "unknown", [
            {"requirement_key": None, "state": "unknown", "reason": "no_reviewed_constraints"}
        ]
    states = {item["state"] for item in results}
    return (
        "not_met" if "not_met" in states else "unknown" if "unknown" in states else "met",
        results,
    )


def save_coverage(
    conn: Connection,
    owner_id: UUID,
    posting_id: UUID,
    body: CoverageWrite,
    settings: Settings,
) -> dict[str, object]:
    posting = _posting(conn, owner_id, posting_id)
    _, requirements = _reviewed_requirements(conn, owner_id, posting, body.requirements_revision_id)
    profile = _profile_projection(conn, owner_id, body.profile_revision_id)
    validate_coverage(body.assessments, requirements, profile)
    eligibility, constraints = evaluate_constraints(requirements)
    assessment_id = uuid4()
    provenance = {
        "origin": "owner_reviewed_manual",
        "posting_content_sha256": _posting_digest(posting),
        "policy": _policy(conn, owner_id, settings),
        "settings_version": _settings_version(conn, owner_id),
        "semantic_review": "owner_confirmed_token_guards_are_not_entailment_proof",
        "profile_evidence": {
            "claims": list(profile.claims.values()),
            "evidence": list(profile.evidence.values()),
        },
    }
    conn.execute(
        text("""INSERT INTO offerloom.match_assessments(owner_id,id,posting_id,profile_revision_id,
        requirements_revision_id,posting_version,hard_constraints,hard_eligibility,evidence_coverage,
        explanation,provenance) VALUES(:owner,:id,:posting,:profile,:requirements,:version,
        CAST(:constraints AS jsonb),:eligibility,CAST(:coverage AS jsonb),'Owner-reviewed coverage; not a model result',
        CAST(:provenance AS jsonb))"""),
        {
            "owner": owner_id,
            "id": assessment_id,
            "posting": posting_id,
            "profile": body.profile_revision_id,
            "requirements": body.requirements_revision_id,
            "version": posting["version"],
            "constraints": canonical_json(constraints),
            "eligibility": eligibility,
            "coverage": canonical_json([item.model_dump(mode="json") for item in body.assessments]),
            "provenance": canonical_json(provenance),
        },
    )
    _bump(conn, owner_id, posting_id, "posting.coverage_reviewed", assessment_id)
    return get_requirements(conn, owner_id, posting_id, settings)


def _prepare_workflow(
    conn: Connection,
    owner_id: UUID,
    posting_id: UUID,
    settings: Settings,
    body: MatchRequest | ExtractRequest,
) -> dict[str, object]:
    stage = "match_requirements" if isinstance(body, MatchRequest) else "extract_posting"
    route = _route(conn, owner_id, settings, stage)
    posting = _posting(conn, owner_id, posting_id)
    source = normalize(str(posting["description_text"]))
    if not source.strip():
        raise Problem(409, "requirements_unavailable")
    current = _current_requirements(conn, owner_id, posting_id)
    snapshot: dict[str, object] = {
        "posting_id": str(posting_id),
        "posting_content_sha256": _posting_digest(posting),
        "source_sha256": _text_sha(source),
        "source_text": source,
        "stage": stage,
        "route_identity": route.identity(),
        "prompt_version": local_model.PROMPT_VERSIONS[stage],
        "policy": _policy(conn, owner_id, settings),
        "settings_version": _settings_version(conn, owner_id),
        "requirements_revision_id": str(current["id"]) if current is not None else None,
    }
    if isinstance(body, MatchRequest):
        _, requirements = _reviewed_requirements(
            conn, owner_id, posting, body.requirements_revision_id
        )
        profile = _profile_projection(conn, owner_id, body.profile_revision_id)
        snapshot.update(
            {
                "profile_revision_id": str(body.profile_revision_id),
                "requirements_revision_id": str(body.requirements_revision_id),
                "context": {
                    "requirements": [item.model_dump(mode="json") for item in requirements],
                    "claims": list(profile.claims.values()),
                    "evidence": list(profile.evidence.values()),
                },
            }
        )
    else:
        snapshot["context"] = {"source_text": source, "source_sha256": _text_sha(source)}
    return snapshot


def _check_workflow_context(
    conn: Connection, task: Claim, settings: Settings
) -> ProfileProjection | None:
    if task.kind == "document_generate":
        if task.input.get("mode") == "section_regeneration":
            from offerloom.section_regeneration import check_context
        else:
            from offerloom.document_generation import check_context

        check_context(conn, task, settings)
        return None
    assert_fence(conn, task)
    posting = _posting(conn, task.owner_id, UUID(str(task.input["posting_id"])))
    if task.input.get("posting_content_sha256") != _posting_digest(posting):
        raise Problem(409, "stale_posting_content")
    if task.input.get("policy") != _policy(conn, task.owner_id, settings):
        raise Problem(409, "stale_model_prompt_or_template")
    if task.input.get("settings_version") != _settings_version(conn, task.owner_id):
        raise Problem(409, "stale_settings")
    current = _current_requirements(conn, task.owner_id, posting["id"])
    current_id = str(current["id"]) if current is not None else None
    if task.input.get("requirements_revision_id") != current_id:
        raise Problem(409, "stale_requirements_revision")
    if task.kind == "matching":
        _reviewed_requirements(conn, task.owner_id, posting, UUID(str(current_id)))
        profile = _profile_projection(
            conn, task.owner_id, UUID(str(task.input["profile_revision_id"]))
        )
        context = cast(dict[str, object], task.input["context"])
        if context.get("claims") != list(profile.claims.values()) or context.get(
            "evidence"
        ) != list(profile.evidence.values()):
            raise Problem(409, "stale_profile_evidence")
        return profile
    return None


class ModelClient(Protocol):
    def call(
        self,
        stage: str,
        context: dict[str, object],
        prompt_version: str,
        interrupted: Event | None = None,
    ) -> local_model.ModelResult: ...


def _reserve_call(conn: Connection, task: Claim, settings: Settings, seconds: int) -> UUID | None:
    lock_installation(conn)
    _check_workflow_context(conn, task, settings)
    row = (
        conn.execute(
            text("""SELECT reserved_until<=clock_timestamp() AS available FROM offerloom.local_model_reservations
        WHERE singleton FOR UPDATE"""),
        )
        .mappings()
        .one()
    )
    if not row["available"]:
        return None
    permitted = conn.execute(
        text("""UPDATE offerloom.workflow_runs SET model_network_attempts=model_network_attempts+1
        WHERE owner_id=:owner AND id=:id AND model_network_attempts<2
        AND deadline_at>clock_timestamp()+make_interval(secs=>:seconds) RETURNING id"""),
        {"owner": task.owner_id, "id": task.id, "seconds": seconds},
    ).first()
    if permitted is None:
        raise Problem(409, "model_attempt_or_deadline_exhausted")
    conn.execute(
        text("""UPDATE offerloom.local_model_reservations SET owner_id=:owner,workflow_run_id=:id,
        fence=:fence,reserved_until=clock_timestamp()+make_interval(secs=>:seconds) WHERE singleton"""),
        {"owner": task.owner_id, "id": task.id, "fence": task.fence, "seconds": seconds},
    )
    route = cast(dict[str, object], task.input["route_identity"])
    run_id = uuid4()
    conn.execute(
        text("""UPDATE offerloom.model_runs SET status='unknown_usage',finished_at=clock_timestamp(),
        metadata='{"usage_status":"unknown","error_code":"worker_interrupted"}'::jsonb
        WHERE owner_id=:owner AND workflow_run_id=:id AND status='running'"""),
        {"owner": task.owner_id, "id": task.id},
    )
    configured_route = _route(conn, task.owner_id, settings, str(task.input["stage"]))
    if configured_route.identity() != route:
        raise Problem(409, "stale_model_route")
    conn.execute(
        text("""INSERT INTO offerloom.model_runs(owner_id,id,workflow_run_id,provider_key,model_id,model_revision,
        task_kind,prompt_version,input_sha256,parameters,egress_class,reserved_tokens,reserved_cost_microunits,status,metadata)
        VALUES(:owner,:run,:workflow,'ollama',:model,:digest,:stage,:prompt,:sha,CAST(:parameters AS jsonb),
        'local',:reserved_tokens,0,'running',CAST(:metadata AS jsonb))"""),
        {
            "owner": task.owner_id,
            "run": run_id,
            "workflow": task.id,
            "model": str(route["model"]),
            "digest": route.get("model_sha256"),
            "stage": task.input["stage"],
            "prompt": task.input["prompt_version"],
            "sha": _sha(task.input["context"]),
            "parameters": canonical_json({"temperature": 0, "route_identity": route}),
            "reserved_tokens": configured_route.max_input_tokens
            + configured_route.max_output_tokens,
            "metadata": canonical_json(
                {"usage_status": "unknown", "reservation_deadline_seconds": seconds}
            ),
        },
    )
    return run_id


def _execute_reserved_model(
    db: Database,
    settings: Settings,
    task: Claim,
    interrupted: Event,
    client: ModelClient | None = None,
) -> None:
    """No DB transaction spans network I/O. A durable reservation survives cancellation/crash."""
    stage = str(task.input["stage"])
    with db.transaction() as conn:
        route = _route(conn, task.owner_id, settings, stage)
    if route.identity() != task.input.get("route_identity"):
        raise Problem(409, "stale_model_route")
    seconds = (
        min(route.deadline_seconds, 120) if stage == "extract_posting" else route.deadline_seconds
    )
    run_id: UUID | None = None
    # Wait inside the worker lease, not as a network attempt or an owner-held DB lock.
    while run_id is None:
        if interrupted.is_set():
            raise local_model.ModelFailure("model_interrupted", retryable=False)
        with db.transaction() as conn:
            run_id = _reserve_call(conn, task, settings, seconds)
        if run_id is None and interrupted.wait(0.25):
            raise local_model.ModelFailure("model_interrupted", retryable=False)
    try:
        # Re-read private route immediately before transmission, not just API enqueue.
        with db.transaction() as conn:
            active_route = _route(conn, task.owner_id, settings, stage)
            _check_workflow_context(conn, task, settings)
        if active_route.identity() != task.input.get("route_identity"):
            raise Problem(409, "stale_model_route")
        if interrupted.is_set():
            raise local_model.ModelFailure("model_interrupted", retryable=False)
        result = (client or local_model.OllamaClient(active_route)).call(
            stage,
            cast(dict[str, object], task.input["context"]),
            str(task.input["prompt_version"]),
            interrupted,
        )
        if interrupted.is_set():
            raise local_model.ModelFailure("model_interrupted", retryable=False)
        try:
            if stage == "extract_posting":
                extracted = ExtractionOutput.model_validate(result.output)
                requirements = [
                    Requirement.model_validate(
                        {**item.model_dump(mode="json"), "hard_constraint": None}
                    )
                    for item in extracted.requirements
                ]
                validate_requirements(requirements, str(task.input["source_text"]))
                proposal = extracted.model_dump(mode="json")
                proposal["requirements"] = [item.model_dump(mode="json") for item in requirements]
            elif stage == "generate_documents":
                from offerloom.document_generation import validate_pair

                pair = validate_pair(result.output, task.input)
                proposal = pair.model_dump(mode="json")
            elif stage == "regenerate_section":
                from offerloom.section_regeneration import validate_candidate

                section_candidate = validate_candidate(result.output, task.input)
                proposal = section_candidate.model_dump(mode="json")
            else:
                matching = MatchingOutput.model_validate_json(canonical_json(result.output))
                proposal = matching.model_dump(mode="json")
        except ValidationError as exc:
            raise Problem(422, "invalid_model_output") from exc
        except Problem as exc:
            if exc.code == "noncanonical_request":
                raise Problem(422, "invalid_model_output") from exc
            raise
        with db.transaction() as conn:
            if stage in {"generate_documents", "regenerate_section"}:
                from offerloom.document_generation import lock_workspace

                lock_workspace(conn, task.owner_id)

            def effect(active: Connection) -> dict[str, object]:
                profile = _check_workflow_context(active, task, settings)
                if stage == "match_requirements":
                    assert profile is not None
                    context = cast(dict[str, object], task.input["context"])
                    requirements = [
                        Requirement.model_validate(item)
                        for item in cast(list[object], context["requirements"])
                    ]
                    validate_coverage(matching.assessments, requirements, profile)
                active.execute(
                    text("""UPDATE offerloom.model_runs SET status='succeeded',finished_at=clock_timestamp(),
                    output_sha256=:sha,metadata=CAST(:metadata AS jsonb),usage_status=:usage,
                    input_tokens=:input_tokens,output_tokens=:output_tokens
                    WHERE owner_id=:owner AND id=:id"""),
                    {
                        "owner": task.owner_id,
                        "id": run_id,
                        "sha": _sha(result.output),
                        "metadata": canonical_json(result.metadata),
                        "input_tokens": result.metadata.get("input_tokens"),
                        "output_tokens": result.metadata.get("output_tokens"),
                        "usage": "known"
                        if result.metadata.get("usage_status") == "known"
                        else "unknown",
                    },
                )
                active.execute(
                    text("""UPDATE offerloom.local_model_reservations SET reserved_until=clock_timestamp()
                    WHERE singleton AND owner_id=:owner AND workflow_run_id=:id AND fence=:fence"""),
                    {"owner": task.owner_id, "id": task.id, "fence": task.fence},
                )
                if stage == "generate_documents":
                    from offerloom.document_generation import publish_pair

                    assert run_id is not None
                    return publish_pair(active, task, settings, pair, run_id, result.metadata)
                if stage == "regenerate_section":
                    from offerloom.section_regeneration import publish_proposal

                    assert run_id is not None
                    return publish_proposal(
                        active, task, settings, section_candidate, run_id, result.metadata
                    )
                return {
                    **proposal,
                    "posting_id": task.input["posting_id"],
                    "review_required": True,
                    "source_sha256": task.input["source_sha256"],
                    "profile_revision_id": task.input.get("profile_revision_id"),
                    "requirements_revision_id": task.input.get("requirements_revision_id"),
                    "provenance": {
                        "origin": "untrusted_model_proposal",
                        "model_run_id": str(run_id),
                        "route_identity": task.input["route_identity"],
                        "prompt_version": task.input["prompt_version"],
                        "source_sha256": task.input["source_sha256"],
                        "profile_revision_id": task.input.get("profile_revision_id"),
                        "requirements_revision_id": task.input.get("requirements_revision_id"),
                    },
                }

            publish(conn, task, effect)
    except (local_model.ModelFailure, Problem, DBAPIError) as exc:
        code = "workflow_publication_fenced" if isinstance(exc, DBAPIError) else exc.code
        with db.transaction() as conn:
            conn.execute(
                text("""UPDATE offerloom.model_runs SET status='unknown_usage',finished_at=clock_timestamp(),
                metadata=CAST(:metadata AS jsonb) WHERE owner_id=:owner AND id=:id"""),
                {
                    "owner": task.owner_id,
                    "id": run_id,
                    "metadata": canonical_json({"usage_status": "unknown", "error_code": code}),
                },
            )
            if code in {
                "invalid_model_output",
                "invalid_source_span",
                "duplicate_requirement_key",
                "unsupported_requirement_novelty",
                "constraint_not_in_source",
                "model_invented_owner_constraint",
                "incomplete_requirement_coverage",
                "duplicate_coverage_citation",
                "invalid_coverage_citation",
                "unconnected_coverage_citation",
                "positive_coverage_requires_connected_evidence",
                "unsupported_coverage_novelty",
                "unsupported_explanation_novelty",
                "model_identity_changed",
                "reference_outside_pinned_context",
                "foreign_document_gap",
                "duplicate_document_gap",
                "model_metadata_invalid",
                "model_route_changed",
                "model_usage_limit",
                "model_invalid_json",
                "model_envelope_invalid",
                "model_destination_rejected",
                "model_tls_rejected",
                "model_incomplete",
                "model_cloud_rejected",
            }:
                conn.execute(
                    text("""INSERT INTO offerloom.local_model_route_blocks(owner_id,route_sha256,stage,error_code)
                    VALUES(:owner,:sha,:stage,:code) ON CONFLICT DO NOTHING"""),
                    {
                        "owner": task.owner_id,
                        "sha": _sha(task.input["route_identity"]),
                        "stage": stage,
                        "code": code,
                    },
                )
        raise


def execute_model(
    db: Database,
    settings: Settings,
    task: Claim,
    interrupted: Event,
    client: ModelClient | None = None,
) -> None:
    # A session lock also prevents overlap if a live but delayed client outlasts
    # its durable crash reservation. AUTOCOMMIT holds no server transaction.
    with db.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as lock:
        acquired = False
        try:
            while not acquired:
                if interrupted.is_set():
                    raise local_model.ModelFailure("model_interrupted")
                with db.transaction() as conn:
                    _check_workflow_context(conn, task, settings)
                acquired = bool(
                    lock.execute(text("SELECT pg_try_advisory_lock(741903229)")).scalar_one()
                )
                if not acquired:
                    interrupted.wait(0.25)
            _execute_reserved_model(db, settings, task, interrupted, client)
        finally:
            if acquired:
                lock.execute(text("SELECT pg_advisory_unlock(741903229)"))


def build_router(db: Database, settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.get("/postings/{posting_id}/requirements")
    def requirements(
        posting_id: UUID, owner: Annotated[Owner, Depends(require_owner)]
    ) -> JSONResponse:
        with db.transaction() as conn:
            result = get_requirements(conn, owner.id, posting_id, settings)
        return JSONResponse(result, headers={"ETag": f'"{result["version"]}"'})

    @router.get("/postings/{posting_id}/requirements/history")
    def history(
        posting_id: UUID, owner: Annotated[Owner, Depends(require_owner)]
    ) -> dict[str, object]:
        with db.transaction() as conn:
            posting = _posting(conn, owner.id, posting_id)
            rows = (
                conn.execute(
                    text("""SELECT * FROM offerloom.posting_requirement_revisions WHERE owner_id=:owner
                AND posting_id=:posting ORDER BY revision_no DESC LIMIT 100"""),
                    {"owner": owner.id, "posting": posting_id},
                )
                .mappings()
                .all()
            )
            return {"items": [_requirements_view(row, posting) for row in rows]}

    @router.put("/postings/{posting_id}/requirements")
    def review_requirements(
        posting_id: UUID,
        body: RequirementsWrite,
        owner: Annotated[Owner, Depends(require_owner)],
        if_match: Annotated[str | None, Header()] = None,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        with db.transaction() as conn:
            status, result = command(
                conn,
                owner.id,
                "PUT",
                "/postings/{posting_id}/requirements",
                {"posting_id": str(posting_id)},
                body.model_dump(mode="json"),
                if_match,
                idempotency_key,
                "posting",
                posting_id,
                lambda active: (
                    200,
                    save_requirements(active, owner.id, posting_id, body, settings),
                ),
            )
        return JSONResponse(result, status_code=status, headers={"ETag": f'"{result["version"]}"'})

    @router.put("/postings/{posting_id}/coverage")
    def review_coverage(
        posting_id: UUID,
        body: CoverageWrite,
        owner: Annotated[Owner, Depends(require_owner)],
        if_match: Annotated[str | None, Header()] = None,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        with db.transaction() as conn:
            status, result = command(
                conn,
                owner.id,
                "PUT",
                "/postings/{posting_id}/coverage",
                {"posting_id": str(posting_id)},
                body.model_dump(mode="json"),
                if_match,
                idempotency_key,
                "posting",
                posting_id,
                lambda active: (200, save_coverage(active, owner.id, posting_id, body, settings)),
            )
        return JSONResponse(result, status_code=status, headers={"ETag": f'"{result["version"]}"'})

    def queue(
        posting_id: UUID,
        body: MatchRequest | ExtractRequest,
        owner: Owner,
        if_match: str | None,
        idempotency_key: str | None,
    ) -> JSONResponse:
        matching = isinstance(body, MatchRequest)
        route = "/postings/{posting_id}/match" if matching else "/postings/{posting_id}/extract"
        with db.transaction() as conn:

            def effect(active: Connection) -> tuple[int, dict[str, object]]:
                snapshot = _prepare_workflow(active, owner.id, posting_id, settings, body)
                workflow_id = enqueue(
                    active, owner.id, "matching" if matching else "posting_extract", snapshot
                )
                _bump(active, owner.id, posting_id, "posting.model_proposal_requested", workflow_id)
                return 202, {"workflow_id": str(workflow_id), "posting_id": str(posting_id)}

            status, result = command(
                conn,
                owner.id,
                "POST",
                route,
                {"posting_id": str(posting_id)},
                body.model_dump(mode="json"),
                if_match,
                idempotency_key,
                "posting",
                posting_id,
                effect,
            )
        return JSONResponse(
            result,
            status_code=status,
            headers={"Location": f"/api/v1/workflows/{result['workflow_id']}"},
        )

    @router.post("/postings/{posting_id}/extract")
    def extract(
        posting_id: UUID,
        body: ExtractRequest,
        owner: Annotated[Owner, Depends(require_owner)],
        if_match: Annotated[str | None, Header()] = None,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        return queue(posting_id, body, owner, if_match, idempotency_key)

    @router.post("/postings/{posting_id}/match")
    def match(
        posting_id: UUID,
        body: MatchRequest,
        owner: Annotated[Owner, Depends(require_owner)],
        if_match: Annotated[str | None, Header()] = None,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        return queue(posting_id, body, owner, if_match, idempotency_key)

    return router
