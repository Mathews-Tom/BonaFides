# SPDX-License-Identifier: MIT OR Apache-2.0
"""Claim schema v1: language-neutral structured statements and deterministic renderings.

A structured claim stores its parts, never a sentence. Every displayed or printed sentence is
derived by the renderer for one language, so the same claim renders identically everywhere.
"""

from __future__ import annotations

import calendar
import re
import unicodedata
from collections.abc import Callable, Mapping
from datetime import date
from typing import Literal, get_args

from pydantic import Field, ValidationInfo, field_validator, model_validator

from bonafides.auth import StrictModel

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
