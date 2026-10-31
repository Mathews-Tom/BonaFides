# SPDX-License-Identifier: MIT OR Apache-2.0
"""Release-fixed claim schema v1 mapping, rendering and grounding for schema 28.

This final version is fixed for release. The schema 28 upgrade and imports of portable
archives written before schema 28 use this standalone copy. Later application changes
must never change this frozen schema, vocabulary, mapper, renderer or grounding behavior.
No live application modules may be imported here.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import re
import unicodedata
from collections.abc import Callable, Mapping
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


# Ordered from strongest to weakest ownership. The order is part of the schema contract.
OwnershipVerb = Literal[
    "led",  # Explicit leadership; no generic achievement is placed here.
    "spearheaded",  # Explicit leadership; no generic achievement is placed here.
    "headed",  # Explicit leadership; no generic achievement is placed here.
    "directed",  # Explicit leadership; no generic achievement is placed here.
    "owned",  # Explicit responsibility, below leadership.
    "took_ownership_of",  # Explicit responsibility, below leadership.
    "responsible_for",  # Explicit responsibility, below leadership.
    "was_responsible_for",  # Explicit responsibility, below leadership.
    "managed",  # Supervision or oversight, not creation or leadership inference.
    "supervised",  # Supervision or oversight, not creation or leadership inference.
    "oversaw",  # Supervision or oversight, not creation or leadership inference.
    "initiated",  # Starting something, not necessarily owning its later execution.
    "founded",  # Starting something, not necessarily owning its later execution.
    "established",  # Starting something, not necessarily owning its later execution.
    "introduced",  # Starting something, not necessarily owning its later execution.
    "pioneered",  # Starting something, not necessarily owning its later execution.
    "designed",  # Design or planning, without asserting delivery.
    "architected",  # Design or planning, without asserting delivery.
    "planned",  # Design or planning, without asserting delivery.
    "built",  # Hands-on implementation; prototypes and integration do not assert project leadership.
    "developed",  # Hands-on implementation; prototypes and integration do not assert project leadership.
    "implemented",  # Hands-on implementation; prototypes and integration do not assert project leadership.
    "created",  # Hands-on implementation; prototypes and integration do not assert project leadership.
    "engineered",  # Hands-on implementation; prototypes and integration do not assert project leadership.
    "coded",  # Hands-on implementation; prototypes and integration do not assert project leadership.
    "programmed",  # Hands-on implementation; prototypes and integration do not assert project leadership.
    "automated",  # Hands-on implementation; prototypes and integration do not assert project leadership.
    "integrated",  # Hands-on implementation; prototypes and integration do not assert project leadership.
    "migrated",  # Hands-on implementation; prototypes and integration do not assert project leadership.
    "prototyped",  # Hands-on implementation; prototypes and integration do not assert project leadership.
    "delivered",  # Executing or releasing work, without asserting its design or ownership.
    "shipped",  # Executing or releasing work, without asserting its design or ownership.
    "launched",  # Executing or releasing work, without asserting its design or ownership.
    "completed",  # Executing or releasing work, without asserting its design or ownership.
    "executed",  # Executing or releasing work, without asserting its design or ownership.
    "released",  # Executing or releasing work, without asserting its design or ownership.
    "rolled_out",  # Executing or releasing work, without asserting its design or ownership.
    "deployed",  # Executing or releasing work, without asserting its design or ownership.
    "increased",  # A stated outcome/change, without asserting who led the work.
    "grew",  # A stated outcome/change, without asserting who led the work.
    "raised",  # A stated outcome/change, without asserting who led the work.
    "boosted",  # A stated outcome/change, without asserting who led the work.
    "expanded",  # A stated outcome/change, without asserting who led the work.
    "scaled",  # A stated outcome/change, without asserting who led the work.
    "generated",  # A stated outcome/change, without asserting who led the work.
    "achieved",  # A stated outcome/change, without asserting who led the work.
    "reduced",  # A stated outcome/change, without asserting who led the work.
    "cut",  # A stated outcome/change, without asserting who led the work.
    "decreased",  # A stated outcome/change, without asserting who led the work.
    "lowered",  # A stated outcome/change, without asserting who led the work.
    "shortened",  # A stated outcome/change, without asserting who led the work.
    "minimized",  # A stated outcome/change, without asserting who led the work.
    "slashed",  # A stated outcome/change, without asserting who led the work.
    "trimmed",  # A stated outcome/change, without asserting who led the work.
    "saved",  # A stated outcome/change, without asserting who led the work.
    "improved",  # A stated outcome/change, without asserting who led the work.
    "optimized",  # A stated outcome/change, without asserting who led the work.
    "enhanced",  # A stated outcome/change, without asserting who led the work.
    "streamlined",  # A stated outcome/change, without asserting who led the work.
    "modernized",  # A stated outcome/change, without asserting who led the work.
    "stabilized",  # A stated outcome/change, without asserting who led the work.
    "strengthened",  # A stated outcome/change, without asserting who led the work.
    "hardened",  # A stated outcome/change, without asserting who led the work.
    "accelerated",  # A stated outcome/change, without asserting who led the work.
    "resolved",  # A stated outcome/change, without asserting who led the work.
    "fixed",  # A stated outcome/change, without asserting who led the work.
    "wrote",  # Creating or communicating an artifact, not managing the subject.
    "authored",  # Creating or communicating an artifact, not managing the subject.
    "documented",  # Creating or communicating an artifact, not managing the subject.
    "drafted",  # Creating or communicating an artifact, not managing the subject.
    "produced",  # Creating or communicating an artifact, not managing the subject.
    "prepared",  # Creating or communicating an artifact, not managing the subject.
    "published",  # Creating or communicating an artifact, not managing the subject.
    "presented",  # Creating or communicating an artifact, not managing the subject.
    "maintained",  # Operating or improving an existing system, without ownership inference.
    "administered",  # Operating or improving an existing system, without ownership inference.
    "operated",  # Operating or improving an existing system, without ownership inference.
    "configured",  # Operating or improving an existing system, without ownership inference.
    "monitored",  # Operating or improving an existing system, without ownership inference.
    "upgraded",  # Operating or improving an existing system, without ownership inference.
    "refactored",  # Operating or improving an existing system, without ownership inference.
    "mentored",  # Teaching or developing others, not managing them.
    "coached",  # Teaching or developing others, not managing them.
    "trained",  # Teaching or developing others, not managing them.
    "analyzed",  # Investigation or a bounded performed activity, without delivery inference.
    "assessed",  # Investigation or a bounded performed activity, without delivery inference.
    "audited",  # Investigation or a bounded performed activity, without delivery inference.
    "evaluated",  # Investigation or a bounded performed activity, without delivery inference.
    "investigated",  # Investigation or a bounded performed activity, without delivery inference.
    "researched",  # Investigation or a bounded performed activity, without delivery inference.
    "reviewed",  # Investigation or a bounded performed activity, without delivery inference.
    "measured",  # Investigation or a bounded performed activity, without delivery inference.
    "inspected",  # Investigation or a bounded performed activity, without delivery inference.
    "conducted",  # Investigation or a bounded performed activity, without delivery inference.
    "identified",  # Investigation or a bounded performed activity, without delivery inference.
    "defined",  # Investigation or a bounded performed activity, without delivery inference.
    "coordinated",  # Coordination, negotiation or joint work, not unilateral leadership.
    "orchestrated",  # Coordination, negotiation or joint work, not unilateral leadership.
    "organized",  # Coordination, negotiation or joint work, not unilateral leadership.
    "facilitated",  # Coordination, negotiation or joint work, not unilateral leadership.
    "scheduled",  # Coordination, negotiation or joint work, not unilateral leadership.
    "negotiated",  # Coordination, negotiation or joint work, not unilateral leadership.
    "partnered",  # Coordination, negotiation or joint work, not unilateral leadership.
    "collaborated",  # Coordination, negotiation or joint work, not unilateral leadership.
    "contributed",  # A contribution or participation only.
    "contributed_to",  # A contribution or participation only.
    "worked_on",  # A contribution or participation only.
    "participated_in",  # A contribution or participation only.
    "took_part_in",  # A contribution or participation only.
    "supported",  # Service/support activity only.
    "handled",  # Service/support activity only.
    "processed",  # Service/support activity only.
    "answered",  # Service/support activity only.
    "responded",  # Service/support activity only.
    "assisted_with",  # Assistance only; never inflated into an outcome or ownership claim.
    "assisted_in",  # Assistance only; never inflated into an outcome or ownership claim.
    "helped",  # Assistance only; never inflated into an outcome or ownership claim.
    "helped_to",  # Assistance only; never inflated into an outcome or ownership claim.
    "assisted",  # Assistance only; never inflated into an outcome or ownership claim.
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
MetricChange = Literal["increase", "decrease", "improvement", "saving", "quantity"]
Magnitude = Literal["thousand", "million", "billion"]
MetricRate = Literal["day", "week", "month", "quarter", "year"]
RateWording = Literal["per", "a", "an", "each", "every", "adverbial", "annual"]
PeriodStyle = Literal["parenthetical", "in_year", "range"]
PeriodSeparator = Literal["hyphen", "en_dash", "em_dash"]
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
    rate_wording: RateWording | None = None
    measure: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("measure")
    @classmethod
    def single_line_measure(cls, value: str | None) -> str | None:
        return None if value is None else _phrase(value)

    @model_validator(mode="after")
    def quantity_without_measure(self) -> ClaimMetric:
        # A quantity counts the subject itself, so a separate measure would be ambiguous.
        if self.change == "quantity" and self.measure is not None:
            raise ValueError("quantity_with_measure")
        if self.rate_wording is not None and self.per is None:
            raise ValueError("rate_wording_without_rate")
        if self.rate_wording == "annual" and self.per != "year":
            raise ValueError("annual_wording_requires_year")
        return self


class ClaimPeriod(StrictModel):
    start: str = Field(pattern=r"^[0-9]{4}(?:-[0-9]{2})?$")
    end: str = Field(pattern=r"^[0-9]{4}(?:-[0-9]{2})?$")
    style: PeriodStyle | None = None
    separator: PeriodSeparator | None = None

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

    @model_validator(mode="after")
    def coherent_style(self) -> ClaimPeriod:
        if self.style == "in_year" and (self.start != self.end or len(self.start) != 4):
            raise ValueError("in_year_requires_one_year")
        return self


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
            and _METRIC_POLARITY.get(expected) is not None
            and _METRIC_POLARITY.get(metric.change) == -_METRIC_POLARITY[expected]
            and (metric.measure is None or metric.measure.casefold() == self.subject.casefold())
        ):
            raise ValueError("metric_direction_contradicts_verb")
        return self


_EN_VERBS: dict[OwnershipVerb, str] = {
    verb: verb.replace("_", " ").capitalize() for verb in OWNERSHIP_VERBS
}
_EN_CURRENCY: dict[str, str] = {"usd": "$", "eur": "€", "gbp": "£"}
_EN_MAGNITUDE: dict[Magnitude, str] = {"thousand": "K", "million": "M", "billion": "B"}
_EN_MONTHS = tuple(calendar.month_abbr)
_VERB_DIRECTION: dict[OwnershipVerb, MetricChange] = {
    "increased": "increase",
    "grew": "increase",
    "raised": "increase",
    "boosted": "increase",
    "expanded": "increase",
    "scaled": "increase",
    "generated": "increase",
    "accelerated": "increase",
    "reduced": "decrease",
    "cut": "decrease",
    "decreased": "decrease",
    "lowered": "decrease",
    "shortened": "decrease",
    "minimized": "decrease",
    "slashed": "decrease",
    "trimmed": "decrease",
    "saved": "saving",
    "improved": "improvement",
    "optimized": "improvement",
    "enhanced": "improvement",
    "streamlined": "improvement",
    "modernized": "improvement",
    "stabilized": "improvement",
    "strengthened": "improvement",
    "hardened": "improvement",
}
_METRIC_POLARITY: dict[MetricChange | None, int] = {"increase": 1, "decrease": -1, "saving": -1}
_EN_RATE_ADVERBS: dict[MetricRate, str] = {
    "day": "daily",
    "week": "weekly",
    "month": "monthly",
    "quarter": "quarterly",
    "year": "yearly",
}
_EN_PERIOD_SEPARATORS: dict[PeriodSeparator, str] = {
    "hyphen": "-",
    "en_dash": "–",
    "em_dash": "—",
}
_EN_PARTICIPLES: dict[MetricChange, str] = {
    "increase": "increasing",
    "decrease": "reducing",
    "improvement": "improving",
}


def _en_rate(metric: ClaimMetric) -> str:
    if metric.per is None:
        return ""
    wording = metric.rate_wording or "per"
    if wording == "annual":
        return " annually"
    if wording == "adverbial":
        return " " + _EN_RATE_ADVERBS[metric.per]
    return f" {wording} {metric.per}"


def _en_amount(metric: ClaimMetric) -> str:
    number = metric.value + (_EN_MAGNITUDE[metric.magnitude] if metric.magnitude else "")
    rate = _en_rate(metric)
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
    leading = metric is not None and metric.change == "quantity"
    words = [_EN_VERBS[claim.verb]]
    if leading and metric is not None:
        words.append(_en_amount(metric))
    words.append(claim.subject)
    if inline and metric is not None:
        words.append(f"by {_en_amount(metric)}")
    if claim.scope:
        words.append(claim.scope)
    sentence = " ".join(words)
    if metric is not None and not inline and not leading:
        amount = _en_amount(metric)
        if metric.change in _EN_PARTICIPLES:
            sentence += f", {_EN_PARTICIPLES[metric.change]} {metric.measure or 'it'} by {amount}"
        else:
            lead = "saving" if metric.change == "saving" else "reaching"
            sentence += f", {lead} {amount}" + (f" {metric.measure}" if metric.measure else "")
    if claim.period is not None:
        period = claim.period
        start, end = _en_date(period.start), _en_date(period.end)
        if period.style == "in_year":
            sentence += f" in {start}"
        else:
            separator = _EN_PERIOD_SEPARATORS[period.separator or "en_dash"]
            dates = start if start == end else f"{start}{separator}{end}"
            sentence += f" {dates}" if period.style == "range" else f" ({dates})"
    return sentence


RENDERERS: dict[RenderLanguage, Callable[[StructuredClaim], str]] = {"en": _render_en}


def render_claim(claim: StructuredClaim, language: RenderLanguage = "en") -> str:
    return RENDERERS[language](claim)


def vocabulary(language: RenderLanguage = "en") -> dict[str, object]:
    """Editor choices; verbs keep their ownership order and carry the language's wording."""
    labels = {"en": _EN_VERBS}[language]
    return {
        "language": language,
        "verbs": [{"key": verb, "label": labels[verb]} for verb in OWNERSHIP_VERBS],
        "units": list(METRIC_UNITS),
        "changes": list(get_args(MetricChange)),
        "magnitudes": list(get_args(Magnitude)),
        "rates": list(get_args(MetricRate)),
        "rate_wordings": list(get_args(RateWording)),
        "period_styles": list(get_args(PeriodStyle)),
        "period_separators": list(get_args(PeriodSeparator)),
    }


def structured_claim(payload: Mapping[str, object]) -> StructuredClaim | None:
    value = payload.get("claim")
    return None if value is None else StructuredClaim.model_validate(value)


def claim_text(payload: Mapping[str, object], language: RenderLanguage = "en") -> str:
    """The displayed statement: a rendering for structured claims, literal text otherwise."""
    claim = structured_claim(payload)
    if claim is not None:
        return render_claim(claim, language)
    value = payload.get("text")
    if not isinstance(value, str):
        raise ValueError("claim_without_statement")
    return value


def needs_attention(kind: str, payload: Mapping[str, object]) -> bool:
    """Experience and project statements still stored as free text await restructuring."""
    return kind in STRUCTURED_KINDS and payload.get("claim") is None


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
    """Require the complete English rendering verbatim within the evidence excerpt.

    Both texts use NFKC, casefolding, collapsed whitespace and ASCII quotes/dashes.
    A trailing full stop or semicolon on the rendering is ignored; wording is not.
    Phrase boundaries exclude matches within longer words or numeric amounts.
    This is literal containment, not a semantic assessment of the surrounding text.
    """
    rendering = _normalise_grounding(render_claim(claim, "en")).rstrip(".;")
    quoted = _normalise_grounding(excerpt)
    # Currency symbols present in the excerpt are escaped, never treated as regex syntax.
    currencies = re.escape("".join({char for char in quoted if unicodedata.category(char) == "Sc"}))
    currency_boundary = rf"|[{currencies}]" if currencies else ""
    pattern = (
        rf"(?<![^\W_]){re.escape(rendering)}"
        rf"(?![^\W_]|(?<=\d)[%×]|[.,]\d{currency_boundary})"
    )
    return re.search(pattern, quoted) is not None


UnparsedReason = Literal[
    "unknown_verb",
    "missing_subject",
    "additional_metric",
    "multiple_statements",
    "invalid_value",
    "unplaced_scope",
    "unquoted_rendering",
    "compound_verb",
]

# Source actions are vocabulary identities, never synonyms for another ownership key.
_VERB_PHRASES: dict[str, tuple[OwnershipVerb, MetricChange | None]] = {
    verb.replace("_", " "): (verb, _VERB_DIRECTION.get(verb)) for verb in OWNERSHIP_VERBS
}
# Verbs that also mean "removed" or "shortened in scope" map only when a metric follows them
# ("Cut costs by 20%", never "Cut the 2.0 release").
_METRIC_ONLY = frozenset({"cut", "slashed", "trimmed"})
_COMPOUND = re.compile(r"(?:and|&)\s+(?P<word>[A-Za-z]+)\b", re.IGNORECASE)
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
    r"\s*\((?P<start>[0-9]{4}(?:-[0-9]{2})?)(?:\s*(?P<separator>[-–—])\s*(?P<end>[0-9]{4}(?:-[0-9]{2})?))?\)$"
    r"|\s+in\s+(?P<year>[0-9]{4})$"
    r"|\s+(?P<range_start>[0-9]{4}(?:-[0-9]{2})?)\s*(?P<range_separator>[-–—])\s*(?P<range_end>[0-9]{4}(?:-[0-9]{2})?)$"
)
_SCOPE = re.compile(r"\s+(?=(?:across|for|within|spanning|serving)\s)", re.IGNORECASE)
_TAIL = re.compile(
    r"^(?:across|for|within|spanning|serving|through|via|using|with|in|at|over|on|by|while|from"
    r"|during|after|before|without)\s",
    re.IGNORECASE,
)
_RATE = re.compile(
    r"^(?:(?P<wording>per|a|an|each|every)\s+(?P<unit>day|week|month|quarter|year)"
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


def _quantity(body: str) -> tuple[re.Match[str], str] | None:
    """An amount opening the object counts the subject itself ("$3.4M in new contracts")."""
    found = _leading_amount(body)
    if found is None or found[0]["duration"] or not found[1]:
        return None
    return found


def _verb(line: str) -> tuple[str, OwnershipVerb, MetricChange | None, str] | None:
    folded = line.casefold()
    for phrase in _PHRASES:
        if folded.startswith(phrase) and (len(line) == len(phrase) or line[len(phrase)] == " "):
            verb, change = _VERB_PHRASES[phrase]
            return phrase, verb, change, line[len(phrase) :].strip()
    return None


def _verb_word(word: str) -> bool:
    folded = word.casefold()
    return (
        folded in {"ran", "run", "took", "taken", "made", "gave", "given", "taught", "brought"}
        or folded.endswith("ed")
        or any(phrase.split()[0] == folded for phrase in _VERB_PHRASES)
    )


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
    # "Designed and implemented X" names two ownership acts; neither is folded into the subject.
    compound = _COMPOUND.match(body)
    if compound is not None and _verb_word(compound["word"]):
        return ClaimMapping(
            claim=None,
            rendering=None,
            unparsed=[
                UnparsedPart(
                    text=statement[: len(phrase)] + " " + compound[0], reason="compound_verb"
                ),
                *unparsed,
            ],
        )
    fields: dict[str, object] = {"schema_version": 1, "verb": verb}
    period = _PERIOD.search(body)
    if period is not None:
        start = period["start"] or period["year"] or period["range_start"]
        end = period["end"] or period["range_end"] or start
        style = (
            "in_year" if period["year"] else "range" if period["range_start"] else "parenthetical"
        )
        period_fields = {"start": start, "end": end, "style": style}
        separator = period["separator"] or period["range_separator"]
        if separator:
            period_fields["separator"] = {"-": "hyphen", "–": "en_dash", "—": "em_dash"}[separator]
        fields["period"] = period_fields
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
    elif (quantity := _quantity(body)) is not None:
        metric = {"change": "quantity"}
        amount, body = (quantity[0], ""), quantity[1]
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
            adverb = (rate["adverb"] or "").casefold()
            metric["rate_wording"] = (
                "annual"
                if adverb == "annually"
                else "adverbial"
                if adverb
                else rate["wording"].casefold()
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
    # Identity mapping and form metadata never waive complete-rendering quotation.
    # Unsupported wording remains inspectable, with literal approval needing attention.
    if not unparsed and not grounded(claim, line):
        unparsed.append(UnparsedPart(text=statement_text(line), reason="unquoted_rendering"))
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
