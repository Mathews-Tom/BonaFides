# SPDX-License-Identifier: MIT OR Apache-2.0
"""Frozen claim schema v1 mapping, rendering and grounding for the structured-claims upgrade.

The schema 28 upgrade and the import of portable archives written before schema 28 rewrite
free-text experience and project claims with this copy. It must never change and must not
import live application modules: later changes to the mapper, renderer, grounding or profile
code must not alter what an upgrade from an earlier schema produces.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import re
import unicodedata
from datetime import date
from typing import Literal, cast, get_args
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)
from sqlalchemy import Connection, text
from sqlalchemy.engine import RowMapping


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


OwnershipVerb = Literal[
    "led",
    "owned",
    "managed",
    "initiated",
    "designed",
    "built",
    "delivered",
    "increased",
    "reduced",
    "improved",
    "wrote",
    "maintained",
    "mentored",
    "analyzed",
    "coordinated",
    "contributed",
    "supported",
    "assisted",
]
OWNERSHIP_VERBS: tuple[OwnershipVerb, ...] = get_args(OwnershipVerb)
MetricUnit = Literal[
    "percent",
    "times",
    "points",
    "count",
    "usd",
    "eur",
    "gbp",
    "milliseconds",
    "seconds",
    "minutes",
    "hours",
    "days",
    "weeks",
    "months",
]
METRIC_UNITS: tuple[MetricUnit, ...] = get_args(MetricUnit)
MetricChange = Literal["increase", "decrease", "improvement", "saving"]
Magnitude = Literal["thousand", "million", "billion"]
MetricRate = Literal["day", "week", "month", "quarter", "year"]
RenderLanguage = Literal["en"]
RENDER_LANGUAGES: tuple[RenderLanguage, ...] = get_args(RenderLanguage)
STRUCTURED_KINDS = frozenset({"experience", "project"})


def date_bounds(value: str) -> tuple[date, date]:
    if re.fullmatch(r"[0-9]{4}(?:-[0-9]{2}(?:-[0-9]{2})?)?", value) is None:
        raise ValueError("invalid_date")
    parts = [int(part) for part in value.split("-")]
    year = parts[0]
    month = parts[1] if len(parts) > 1 else 1
    day = parts[2] if len(parts) > 2 else 1
    lower = date(year, month, day)
    upper_month = month if len(parts) > 1 else 12
    upper_day = day if len(parts) > 2 else calendar.monthrange(year, upper_month)[1]
    return lower, date(year, upper_month, upper_day)


def _phrase(value: str) -> str:
    if not value.strip() or value != value.strip() or any(c in value for c in "\r\n\t"):
        raise ValueError("invalid_claim_phrase")
    return value


class ClaimMetric(StrictModel):
    value: str = Field(pattern=r"^[0-9]{1,12}(?:\.[0-9]{1,4})?$")
    unit: MetricUnit
    magnitude: Magnitude | None = None
    change: MetricChange | None = None
    per: MetricRate | None = None
    measure: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("measure")
    @classmethod
    def single_line_measure(cls, value: str | None) -> str | None:
        return None if value is None else _phrase(value)


class ClaimPeriod(StrictModel):
    start: str = Field(pattern=r"^[0-9]{4}(?:-[0-9]{2})?$")
    end: str = Field(pattern=r"^[0-9]{4}(?:-[0-9]{2})?$")

    @field_validator("start", "end")
    @classmethod
    def valid_date(cls, value: str) -> str:
        date_bounds(value)
        return value

    @field_validator("end")
    @classmethod
    def ordered(cls, value: str, info: ValidationInfo) -> str:
        start = info.data.get("start")
        if isinstance(start, str) and date_bounds(start)[0] > date_bounds(value)[1]:
            raise ValueError("invalid_interval")
        return value


class StructuredClaim(StrictModel):
    schema_version: Literal[1]
    verb: OwnershipVerb
    subject: str = Field(min_length=1, max_length=300)
    scope: str | None = Field(default=None, min_length=1, max_length=200)
    metric: ClaimMetric | None = None
    period: ClaimPeriod | None = None

    @field_validator("subject", "scope")
    @classmethod
    def single_line(cls, value: str | None) -> str | None:
        return None if value is None else _phrase(value)

    @model_validator(mode="after")
    def metric_direction_matches_verb(self) -> StructuredClaim:
        # "Reduced latency, increasing it by 30%" contradicts itself; a distinct measure does not.
        expected = _VERB_DIRECTION.get(self.verb)
        metric = self.metric
        if (
            metric is not None
            and {expected, metric.change} == {"increase", "decrease"}
            and (metric.measure is None or metric.measure.casefold() == self.subject.casefold())
        ):
            raise ValueError("metric_direction_contradicts_verb")
        return self


_EN_VERBS: dict[OwnershipVerb, str] = {
    "led": "Led",
    "owned": "Owned",
    "managed": "Managed",
    "initiated": "Initiated",
    "designed": "Designed",
    "built": "Built",
    "delivered": "Delivered",
    "increased": "Increased",
    "reduced": "Reduced",
    "improved": "Improved",
    "wrote": "Wrote",
    "maintained": "Maintained",
    "mentored": "Mentored",
    "analyzed": "Analyzed",
    "coordinated": "Coordinated",
    "contributed": "Contributed to",
    "supported": "Supported",
    "assisted": "Assisted with",
}
_EN_CURRENCY: dict[str, str] = {"usd": "$", "eur": "€", "gbp": "£"}
_EN_MAGNITUDE: dict[Magnitude, str] = {"thousand": "K", "million": "M", "billion": "B"}
_EN_MONTHS = tuple(calendar.month_abbr)
_VERB_DIRECTION: dict[OwnershipVerb, MetricChange] = {
    "increased": "increase",
    "reduced": "decrease",
    "improved": "improvement",
}
_EN_PARTICIPLES: dict[MetricChange, str] = {
    "increase": "increasing",
    "decrease": "reducing",
    "improvement": "improving",
}


def _en_amount(metric: ClaimMetric) -> str:
    number = metric.value + (_EN_MAGNITUDE[metric.magnitude] if metric.magnitude else "")
    rate = f" per {metric.per}" if metric.per else ""
    if metric.unit in _EN_CURRENCY:
        return _EN_CURRENCY[metric.unit] + number + rate
    if metric.unit == "percent":
        return number + "%" + rate
    if metric.unit == "times":
        return number + "x" + rate
    if metric.unit == "count":
        return number + rate
    noun = metric.unit if metric.value != "1" or metric.magnitude else metric.unit[:-1]
    return f"{number} {noun}{rate}"


def _en_date(value: str) -> str:
    year, _, month = value.partition("-")
    return f"{_EN_MONTHS[int(month)]} {year}" if month else year


def _render_en(claim: StructuredClaim) -> str:
    metric = claim.metric
    inline = (
        metric is not None
        and metric.measure is None
        and metric.change is not None
        and _VERB_DIRECTION.get(claim.verb) == metric.change
    )
    words = [_EN_VERBS[claim.verb], claim.subject]
    if inline and metric is not None:
        words.append(f"by {_en_amount(metric)}")
    if claim.scope:
        words.append(claim.scope)
    sentence = " ".join(words)
    if metric is not None and not inline:
        amount = _en_amount(metric)
        if metric.change in _EN_PARTICIPLES:
            sentence += f", {_EN_PARTICIPLES[metric.change]} {metric.measure or 'it'} by {amount}"
        else:
            lead = "saving" if metric.change == "saving" else "reaching"
            sentence += f", {lead} {amount}" + (f" {metric.measure}" if metric.measure else "")
    if claim.period is not None:
        start, end = _en_date(claim.period.start), _en_date(claim.period.end)
        sentence += f" ({start})" if start == end else f" ({start}–{end})"
    return sentence


_GROUNDING_PUNCTUATION = str.maketrans(
    {
        **dict.fromkeys("‘’‚‛", "'"),
        **dict.fromkeys("“”„‟", '"'),
        **dict.fromkeys("‐‑‒–—―−", "-"),
    }
)


def _normalise_grounding(text: str) -> str:
    return " ".join(
        unicodedata.normalize("NFKC", text).casefold().translate(_GROUNDING_PUNCTUATION).split()
    )


def grounded(claim: StructuredClaim, excerpt: str) -> bool:
    """Require the complete rendering as a whole phrase under frozen literal normalization."""
    rendering = _normalise_grounding(render_claim(claim)).rstrip(".;")
    quoted = _normalise_grounding(excerpt)
    currencies = re.escape("".join({char for char in quoted if unicodedata.category(char) == "Sc"}))
    currency_boundary = rf"|[{currencies}]" if currencies else ""
    pattern = (
        rf"(?<![^\W_]){re.escape(rendering)}"
        rf"(?![^\W_]|(?<=\d)[%×]|[.,]\d{currency_boundary})"
    )
    return re.search(pattern, quoted) is not None


def render_claim(claim: StructuredClaim) -> str:
    return _render_en(claim)


UnparsedReason = Literal[
    "unknown_verb",
    "missing_subject",
    "additional_metric",
    "multiple_statements",
    "invalid_value",
    "unplaced_scope",
]

# Declared normalization: case-insensitive whole-word prefixes, longest phrase first.
_VERB_PHRASES: dict[str, tuple[OwnershipVerb, MetricChange | None]] = {
    # A phrase maps only to a vocabulary verb with the same meaning at the same or a lower
    # ownership rank; a verb with another meaning stays unknown so the line needs attention.
    "led": ("led", None),
    "spearheaded": ("led", None),
    "headed": ("led", None),
    "directed": ("led", None),
    "owned": ("owned", None),
    "took ownership of": ("owned", None),
    "responsible for": ("owned", None),
    "was responsible for": ("owned", None),
    "managed": ("managed", None),
    "supervised": ("managed", None),
    "oversaw": ("managed", None),
    "mentored": ("mentored", None),
    "coached": ("mentored", None),
    "maintained": ("maintained", None),
    "administered": ("maintained", None),
    "operated": ("maintained", None),
    "wrote": ("wrote", None),
    "authored": ("wrote", None),
    "documented": ("wrote", None),
    "drafted": ("wrote", None),
    "initiated": ("initiated", None),
    "founded": ("initiated", None),
    "established": ("initiated", None),
    "introduced": ("initiated", None),
    "pioneered": ("initiated", None),
    "designed": ("designed", None),
    "architected": ("designed", None),
    "built": ("built", None),
    "developed": ("built", None),
    "implemented": ("built", None),
    "created": ("built", None),
    "engineered": ("built", None),
    "coded": ("built", None),
    "programmed": ("built", None),
    "delivered": ("delivered", None),
    "shipped": ("delivered", None),
    "launched": ("delivered", None),
    "completed": ("delivered", None),
    "executed": ("delivered", None),
    "released": ("delivered", None),
    "rolled out": ("delivered", None),
    "increased": ("increased", "increase"),
    "grew": ("increased", "increase"),
    "raised": ("increased", "increase"),
    "boosted": ("increased", "increase"),
    "expanded": ("increased", "increase"),
    "reduced": ("reduced", "decrease"),
    "cut": ("reduced", "decrease"),
    "decreased": ("reduced", "decrease"),
    "lowered": ("reduced", "decrease"),
    "shortened": ("reduced", "decrease"),
    "minimized": ("reduced", "decrease"),
    "slashed": ("reduced", "decrease"),
    "trimmed": ("reduced", "decrease"),
    "improved": ("improved", "improvement"),
    "optimized": ("improved", "improvement"),
    "enhanced": ("improved", "improvement"),
    "streamlined": ("improved", "improvement"),
    "modernized": ("improved", "improvement"),
    "stabilized": ("improved", "improvement"),
    "strengthened": ("improved", "improvement"),
    "analyzed": ("analyzed", None),
    "assessed": ("analyzed", None),
    "audited": ("analyzed", None),
    "evaluated": ("analyzed", None),
    "investigated": ("analyzed", None),
    "researched": ("analyzed", None),
    "reviewed": ("analyzed", None),
    "measured": ("analyzed", None),
    "coordinated": ("coordinated", None),
    "orchestrated": ("coordinated", None),
    "organized": ("coordinated", None),
    "facilitated": ("coordinated", None),
    "scheduled": ("coordinated", None),
    "contributed to": ("contributed", None),
    "contributed": ("contributed", None),
    "worked on": ("contributed", None),
    "participated in": ("contributed", None),
    "took part in": ("contributed", None),
    "supported": ("supported", None),
    "handled": ("supported", None),
    "assisted with": ("assisted", None),
    "assisted in": ("assisted", None),
    "assisted": ("assisted", None),
    "helped": ("assisted", None),
    "helped to": ("assisted", None),
}
# Verbs that also mean "removed" or "shortened in scope" map only when a metric follows them
# ("Cut costs by 20%", never "Cut the 2.0 release").
_METRIC_ONLY = frozenset({"cut", "slashed", "trimmed"})
_PHRASES = sorted(_VERB_PHRASES, key=lambda phrase: (-len(phrase), phrase))
_BULLET = re.compile(r"^\s*(?:[-*•·●▪◦‣]\s+)?")
_NUMBER = r"[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]{1,4})?|[0-9]{1,12}(?:\.[0-9]{1,4})?"
_AMOUNT = re.compile(
    rf"(?P<currency>[$€£])(?P<cash>{_NUMBER})\s?(?P<magnitude>[kKmMbB]\b|thousand\b|million\b|billion\b)?"
    rf"|(?P<percent>{_NUMBER})\s?(?:%|percent\b)"
    rf"|(?P<times>{_NUMBER})\s?[x×](?![A-Za-z0-9])"
    rf"|(?P<points>{_NUMBER})\s(?:points?|pts)\b"
    rf"|(?P<duration>{_NUMBER})\s?(?P<unit>ms|milliseconds?|seconds?|minutes?|hours?|days?|weeks?|months?)\b"
)
_PARTICIPLES: dict[str, MetricChange] = {
    "reducing": "decrease",
    "cutting": "decrease",
    "lowering": "decrease",
    "decreasing": "decrease",
    "shortening": "decrease",
    "increasing": "increase",
    "growing": "increase",
    "raising": "increase",
    "boosting": "increase",
    "improving": "improvement",
    "accelerating": "increase",
}
_PARTICIPLE = re.compile(
    r",?\s+(?P<word>" + "|".join(_PARTICIPLES) + r")\s+(?P<measure>.+?)\s+by\s+(?P<amount>.+)$",
    re.IGNORECASE,
)
_SAVING = re.compile(r",?\s+saving\s+(?P<amount>.+)$", re.IGNORECASE)
_BY = re.compile(r"\s+by\s+(?P<amount>.+)$", re.IGNORECASE)
_PERIOD = re.compile(
    r"\s*\((?P<start>[0-9]{4}(?:-[0-9]{2})?)(?:\s*[-–—]\s*(?P<end>[0-9]{4}(?:-[0-9]{2})?))?\)$"
    r"|\s+in\s+(?P<year>[0-9]{4})$"
)
_SCOPE = re.compile(r"\s+(?=(?:across|for|within|spanning|serving)\s)", re.IGNORECASE)
_TAIL = re.compile(
    r"^(?:across|for|within|spanning|serving|through|via|using|with|in|at|over|on|by|while|from)\s",
    re.IGNORECASE,
)
_RATE = re.compile(
    r"^(?:(?:per|a|an|each|every)\s+(?P<unit>day|week|month|quarter|year)"
    r"|(?P<adverb>daily|weekly|monthly|quarterly|yearly|annually))(?P<rest>.*)$",
    re.IGNORECASE,
)
_ADVERB_RATES: dict[str, MetricRate] = {
    "daily": "day",
    "weekly": "week",
    "monthly": "month",
    "quarterly": "quarter",
    "yearly": "year",
    "annually": "year",
}
_UNITS: dict[str, MetricUnit] = {"$": "usd", "€": "eur", "£": "gbp"}
_MAGNITUDES: dict[str, Magnitude] = {
    "k": "thousand",
    "thousand": "thousand",
    "m": "million",
    "million": "million",
    "b": "billion",
    "billion": "billion",
}


class UnparsedPart(StrictModel):
    text: str
    reason: UnparsedReason


class ClaimMapping(StrictModel):
    claim: StructuredClaim | None
    rendering: str | None
    unparsed: list[UnparsedPart]

    @property
    def representable(self) -> bool:
        return self.claim is not None and not self.unparsed


def _metric(match: re.Match[str]) -> tuple[str, MetricUnit, Magnitude | None]:
    groups = match.groupdict()
    if groups["currency"]:
        magnitude = groups["magnitude"]
        return (
            groups["cash"],
            _UNITS[groups["currency"]],
            _MAGNITUDES[magnitude.casefold()] if magnitude else None,
        )
    if groups["percent"]:
        return groups["percent"], "percent", None
    if groups["times"]:
        return groups["times"], "times", None
    if groups["points"]:
        return groups["points"], "points", None
    word = groups["unit"].casefold()
    unit: MetricUnit = "milliseconds" if word == "ms" else _duration(word)
    return groups["duration"], unit, None


def _duration(word: str) -> MetricUnit:
    plural = word if word.endswith("s") else word + "s"
    units: dict[str, MetricUnit] = {
        "milliseconds": "milliseconds",
        "seconds": "seconds",
        "minutes": "minutes",
        "hours": "hours",
        "days": "days",
        "weeks": "weeks",
        "months": "months",
    }
    return units[plural]


def _leading_amount(value: str) -> tuple[re.Match[str], str] | None:
    match = _AMOUNT.match(value)
    return None if match is None else (match, value[match.end() :].strip())


def _verb(line: str) -> tuple[str, OwnershipVerb, MetricChange | None, str] | None:
    folded = line.casefold()
    for phrase in _PHRASES:
        if folded.startswith(phrase) and (len(line) == len(phrase) or line[len(phrase)] == " "):
            verb, change = _VERB_PHRASES[phrase]
            return phrase, verb, change, line[len(phrase) :].strip()
    return None


def _unknown_verb(line: str, statement: str, unparsed: list[UnparsedPart]) -> ClaimMapping:
    first = statement.split(" ", 1)[0] if statement else line.strip()
    return ClaimMapping(
        claim=None,
        rendering=None,
        unparsed=[UnparsedPart(text=first or line, reason="unknown_verb"), *unparsed],
    )


def statement_text(line: str) -> str:
    """The line without its bullet marker or surrounding whitespace, still a verbatim slice."""
    return _BULLET.sub("", line, count=1).strip()


def map_line(line: str) -> ClaimMapping:
    """Map one line; unplaced parts are reported, never dropped or invented."""
    unparsed: list[UnparsedPart] = []
    statement = statement_text(line).rstrip(".;").strip()
    head, separator, rest = statement.partition(";")
    if separator and rest.strip():
        unparsed.append(UnparsedPart(text=rest.strip(), reason="multiple_statements"))
    statement = head.strip()
    found = _verb(statement)
    if found is None:
        return _unknown_verb(line, statement, unparsed)
    phrase, verb, verb_change, body = found
    fields: dict[str, object] = {"schema_version": 1, "verb": verb}
    period = _PERIOD.search(body)
    if period is not None:
        start = period["start"] or period["year"]
        fields["period"] = {"start": start, "end": period["end"] or start}
        body = body[: period.start()].strip()
    metric: dict[str, object] = {}
    amount: tuple[re.Match[str], str] | None = None
    participle = _PARTICIPLE.search(body)
    saving = _SAVING.search(body)
    by = _BY.search(body)
    if participle is not None and (amount := _leading_amount(participle["amount"])):
        metric = {"change": _PARTICIPLES[participle["word"].casefold()]}
        metric["measure"] = participle["measure"].strip()
        body = body[: participle.start()].strip()
    elif saving is not None and (amount := _leading_amount(saving["amount"])):
        metric = {"change": "saving"}
        body = body[: saving.start()].strip()
    elif by is not None and verb_change is not None and (amount := _leading_amount(by["amount"])):
        metric = {"change": verb_change}
        body = body[: by.start()].strip()
    tail = ""
    if amount is not None:
        match, tail = amount
        rate = _RATE.match(tail)
        if rate is not None:
            unit_word = rate["unit"] or ""
            metric["per"] = (
                _ADVERB_RATES[rate["adverb"].casefold()]
                if rate["adverb"]
                else cast(MetricRate, unit_word.casefold())
            )
            tail = rate["rest"].strip()
        if metric["change"] == "saving" and tail and not _TAIL.match(tail):
            metric["measure"], tail = tail, ""
        value, unit, magnitude = _metric(match)
        metric.update(value=value.replace(",", ""), unit=unit)
        if magnitude is not None:
            metric["magnitude"] = magnitude
        fields["metric"] = metric
    if phrase in _METRIC_ONLY and amount is None:
        return _unknown_verb(line, statement, unparsed)
    scope = ""
    if tail:
        if _TAIL.match(tail):
            scope = tail
        else:
            unparsed.append(UnparsedPart(text=tail, reason="additional_metric"))
    parts = _SCOPE.split(body.rstrip(","), maxsplit=1)
    subject = parts[0].strip().rstrip(",")
    if len(parts) == 2:
        joined = f"{parts[1].strip()} {scope}".strip()
        # A scope is a verbatim slice; a tail separated from it by the metric stays unplaced.
        if scope and joined not in statement:
            unparsed.append(UnparsedPart(text=scope, reason="unplaced_scope"))
            joined = parts[1].strip()
        scope = joined
    if scope:
        fields["scope"] = scope.rstrip(",")
    for leftover in _AMOUNT.finditer(f"{subject} {scope}"):
        if leftover.group(0) and not leftover.groupdict().get("duration"):
            unparsed.append(UnparsedPart(text=leftover.group(0), reason="additional_metric"))
    if not subject:
        unparsed.append(UnparsedPart(text=statement, reason="missing_subject"))
        return ClaimMapping(claim=None, rendering=None, unparsed=unparsed)
    fields["subject"] = subject
    try:
        claim = StructuredClaim.model_validate(fields)
    except ValidationError:
        unparsed.append(UnparsedPart(text=statement, reason="invalid_value"))
        return ClaimMapping(claim=None, rendering=None, unparsed=unparsed)
    return ClaimMapping(claim=claim, rendering=render_claim(claim), unparsed=unparsed)


RESTRUCTURE_REASON = "claims_restructured"
# The schema whose upgrade introduced claim schema v1; earlier data holds only free text.
STRUCTURED_CLAIMS_SCHEMA = 28


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _restructurable(statement: str) -> StructuredClaim | None:
    """A claim whose rendering reproduces its own statement, so no word changes unapproved."""
    mapping = map_line(statement)
    if mapping.claim is None or not mapping.representable:
        return None
    if render_claim(mapping.claim) != statement:
        return None
    return mapping.claim


def _links(conn: Connection, owner_id: UUID, revision_id: UUID) -> list[RowMapping] | None:
    """The revision's evidence links, or None when any linked evidence is archived."""
    rows = (
        conn.execute(
            text("""SELECT l.evidence_revision_id,l.support_kind,l.excerpt,e.archived_at
            FROM bonafides.claim_evidence_links l
            JOIN bonafides.evidence_revisions r ON r.owner_id=l.owner_id AND r.id=l.evidence_revision_id
            JOIN bonafides.evidence_records e ON e.owner_id=r.owner_id AND e.id=r.evidence_id
            WHERE l.owner_id=:owner AND l.claim_revision_id=:revision
            ORDER BY l.created_at,l.evidence_revision_id"""),
            {"owner": owner_id, "revision": revision_id},
        )
        .mappings()
        .all()
    )
    if not rows or any(row["archived_at"] is not None for row in rows):
        return None
    return list(rows)


def _append_claim_revision(
    conn: Connection,
    owner_id: UUID,
    claim_id: UUID,
    payload: dict[str, object],
    links: list[RowMapping],
) -> UUID:
    revision_id = uuid4()
    conn.execute(
        text("""INSERT INTO bonafides.claim_revisions(owner_id,id,claim_id,revision_no,payload,content_sha256)
        SELECT :owner,:revision,:id,COALESCE(max(revision_no),0)+1,CAST(:payload AS jsonb),:sha
        FROM bonafides.claim_revisions WHERE owner_id=:owner AND claim_id=:id"""),
        {
            "owner": owner_id,
            "revision": revision_id,
            "id": claim_id,
            "payload": _canonical(payload),
            "sha": _sha256(payload),
        },
    )
    for link in links:
        conn.execute(
            text("""INSERT INTO bonafides.claim_evidence_links(owner_id,claim_revision_id,evidence_revision_id,support_kind,excerpt)
            VALUES(:owner,:claim,:evidence,:support,:excerpt)"""),
            {
                "owner": owner_id,
                "claim": revision_id,
                "evidence": link["evidence_revision_id"],
                "support": link["support_kind"],
                "excerpt": link["excerpt"],
            },
        )
    conn.execute(
        text("""UPDATE bonafides.professional_claims SET current_revision_id=:revision,
        version=version+1 WHERE owner_id=:owner AND id=:id"""),
        {"owner": owner_id, "id": claim_id, "revision": revision_id},
    )
    return revision_id


def _save_profile_revision(
    conn: Connection, owner_id: UUID, profile: RowMapping, claim_ids: list[str]
) -> None:
    """Record one profile revision exactly as the schema 28 profile save does."""
    evidence_ids = [
        str(identifier)
        for identifier in conn.execute(
            text("""SELECT current_revision_id FROM bonafides.evidence_records
            WHERE owner_id=:owner AND archived_at IS NULL ORDER BY created_at,id"""),
            {"owner": owner_id},
        ).scalars()
    ]
    stored = cast(dict[str, object], profile["snapshot"])
    snapshot: dict[str, object] = {
        "display_name": stored.get("display_name", ""),
        "headline": stored.get("headline", ""),
        "description": stored.get("description", ""),
        "claim_revision_ids": claim_ids,
        "evidence_revision_ids": evidence_ids,
        "contact": stored.get("contact"),
    }
    revision_id = uuid4()
    conn.execute(
        text("""INSERT INTO bonafides.profile_revisions(owner_id,id,profile_id,revision_no,snapshot,content_sha256,reason)
        SELECT :owner,:revision,:profile,COALESCE(max(revision_no),0)+1,CAST(:snapshot AS jsonb),:sha,:reason
        FROM bonafides.profile_revisions WHERE owner_id=:owner AND profile_id=:profile"""),
        {
            "owner": owner_id,
            "revision": revision_id,
            "profile": profile["id"],
            "snapshot": _canonical(snapshot),
            "sha": _sha256(snapshot),
            "reason": RESTRUCTURE_REASON,
        },
    )
    for claim_id in claim_ids:
        conn.execute(
            text("""INSERT INTO bonafides.profile_revision_claims(owner_id,profile_revision_id,claim_revision_id)
            VALUES(:owner,:revision,:claim)"""),
            {"owner": owner_id, "revision": revision_id, "claim": UUID(claim_id)},
        )
    conn.execute(
        text("""INSERT INTO bonafides.profile_revision_evidence(owner_id,profile_revision_id,evidence_revision_id)
        SELECT owner_id,:revision,current_revision_id FROM bonafides.evidence_records
        WHERE owner_id=:owner AND archived_at IS NULL"""),
        {"owner": owner_id, "revision": revision_id},
    )
    conn.execute(
        text("""UPDATE bonafides.profiles SET current_revision_id=:revision,version=version+1
        WHERE owner_id=:owner AND id=:profile"""),
        {"owner": owner_id, "revision": revision_id, "profile": profile["id"]},
    )
    conn.execute(
        text("""UPDATE bonafides.document_drafts SET stale_at=COALESCE(stale_at,clock_timestamp()),
        version=version+1 WHERE owner_id=:owner AND profile_revision_id<>:revision AND stale_at IS NULL"""),
        {"owner": owner_id, "revision": revision_id},
    )
    version = int(profile["version"]) + 1
    conn.execute(
        text("""INSERT INTO bonafides.outbox_events
        (owner_id,id,aggregate_type,aggregate_id,aggregate_version,event_type,payload,dedupe_key)
        VALUES (:owner,:id,'profile',:entity,:version,'profile.revision_created',CAST(:payload AS jsonb),:dedupe)
        ON CONFLICT(owner_id,dedupe_key) DO UPDATE SET dedupe_key=EXCLUDED.dedupe_key"""),
        {
            "owner": owner_id,
            "id": uuid4(),
            "entity": profile["id"],
            "version": version,
            "payload": _canonical({"reason": RESTRUCTURE_REASON, "revision_id": str(revision_id)}),
            "dedupe": f"profile:{profile['id']}:{version}:profile.revision_created",
        },
    )
    conn.execute(
        text("""INSERT INTO bonafides.audit_events(owner_id,action,actor_kind,actor_id,request_id,entity_type)
        VALUES(:owner,'profile_saved','owner',:actor_id,:request,'identity')"""),
        {"owner": owner_id, "actor_id": str(owner_id), "request": uuid4()},
    )


def restructure_free_text_claims(conn: Connection, owner_id: UUID) -> int:
    """Rewrite each free-text claim of the current profile that renders back to its own text.

    Every other claim stays free text and needs attention. Earlier revisions are untouched, so
    revision history and restore keep working. Returns the number of rewritten claims.
    """
    profile = (
        conn.execute(
            text("""SELECT p.id,p.version,r.snapshot FROM bonafides.profiles p
            JOIN bonafides.profile_revisions r ON r.owner_id=p.owner_id AND r.id=p.current_revision_id
            WHERE p.owner_id=:owner FOR UPDATE OF p"""),
            {"owner": owner_id},
        )
        .mappings()
        .one()
    )
    claim_ids = [
        str(item) for item in cast(dict[str, list[str]], profile["snapshot"])["claim_revision_ids"]
    ]
    rows = (
        conn.execute(
            text("""SELECT c.id AS claim_id,r.id AS revision_id,r.payload
            FROM bonafides.professional_claims c JOIN bonafides.claim_revisions r
            ON r.owner_id=c.owner_id AND r.id=c.current_revision_id
            WHERE c.owner_id=:owner AND c.archived_at IS NULL AND c.kind IN ('experience','project')
            AND r.id=ANY(CAST(:ids AS uuid[])) AND r.payload->'claim' IS NULL
            ORDER BY c.created_at,c.id"""),
            {"owner": owner_id, "ids": claim_ids},
        )
        .mappings()
        .all()
    )
    replaced: dict[str, str] = {}
    for row in rows:
        payload = {key: value for key, value in dict(row["payload"]).items() if value is not None}
        statement = payload.pop("text", None)
        if not isinstance(statement, str):
            raise ValueError("claim_without_statement")
        claim = _restructurable(statement)
        links = _links(conn, owner_id, row["revision_id"])
        if claim is None or links is None:
            continue
        if not all(grounded(claim, str(link["excerpt"])) for link in links):
            continue
        structured = {**payload, "claim": claim.model_dump(mode="json", exclude_none=True)}
        replaced[str(row["revision_id"])] = str(
            _append_claim_revision(conn, owner_id, row["claim_id"], structured, links)
        )
    if replaced:
        _save_profile_revision(
            conn, owner_id, profile, [replaced.get(item, item) for item in claim_ids]
        )
    return len(replaced)
