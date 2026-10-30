# SPDX-License-Identifier: MIT OR Apache-2.0
"""Claim schema v1: language-neutral structured statements and deterministic renderings.

A structured claim stores its parts, never a sentence. Every displayed or printed sentence is
derived by the renderer for one language, so the same claim renders identically everywhere.
"""

from __future__ import annotations

import calendar
import re
from collections.abc import Callable, Mapping
from datetime import date
from typing import Literal, get_args

from pydantic import Field, ValidationInfo, field_validator, model_validator

from bonafides.auth import StrictModel

# Ordered from strongest to weakest ownership. The order is part of the schema contract.
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


RENDERERS: dict[RenderLanguage, Callable[[StructuredClaim], str]] = {"en": _render_en}


def render_claim(claim: StructuredClaim, language: RenderLanguage = "en") -> str:
    return RENDERERS[language](claim)


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


_EN_MAGNITUDE_PATTERNS: dict[Magnitude, str] = {
    "thousand": r"\s?(?:[kK]|thousand)\b",
    "million": r"\s?(?:[mM]|mn|million)\b",
    "billion": r"\s?(?:[bB]|bn|billion)\b",
}
_EN_ANY_MAGNITUDE = r"\s?(?:[kmb]|mn|bn|thousand|million|billion)\b"
_EN_UNIT_PREFIXES: dict[str, str] = {
    "usd": r"(?:US\$|\$|USD\s?)",
    "eur": r"(?:€|EUR\s?)",
    "gbp": r"(?:£|GBP\s?)",
}
_EN_UNIT_SUFFIXES: dict[MetricUnit, str] = {
    "percent": r"\s?(?:%|percent\b|per cent\b)",
    "times": r"\s?(?:[x×](?![A-Za-z0-9])|times\b|-?fold\b)",
    "points": r"\s?(?:points?|pts|pp)\b",
    "milliseconds": r"\s?(?:ms|milliseconds?)\b",
    "seconds": r"\s?(?:s|secs?|seconds?)\b",
    "minutes": r"\s?(?:mins?|minutes?)\b",
    "hours": r"\s?(?:hrs?|hours?)\b",
    "days": r"\s?days?\b",
    "weeks": r"\s?weeks?\b",
    "months": r"\s?months?\b",
}
_EN_RATE_PATTERNS: dict[MetricRate, str] = {
    "day": r"\b(?:(?:per|a|an|each|every)\s+day|daily)\b",
    "week": r"\b(?:(?:per|a|an|each|every)\s+week|weekly)\b",
    "month": r"\b(?:(?:per|a|an|each|every)\s+month|monthly)\b",
    "quarter": r"\b(?:(?:per|a|an|each|every)\s+quarter|quarterly)\b",
    "year": r"\b(?:(?:per|a|an|each|every)\s+year|yearly|annually)\b",
}


def _metric_quoted(metric: ClaimMetric, excerpt: str) -> bool:
    """The excerpt states this amount with its unit, magnitude and rate, not a longer number.

    An absent magnitude rejects a magnitude suffix ("3" is not "3M"), and the rate must follow
    the amount it qualifies ("6 hours per week", not "6 hours per day; weekly").
    """
    magnitude = (
        _EN_MAGNITUDE_PATTERNS[metric.magnitude]
        if metric.magnitude
        else rf"(?!{_EN_ANY_MAGNITUDE})"
    )
    amount = (
        _EN_UNIT_PREFIXES.get(metric.unit, "")
        + rf"(?<![0-9.]){re.escape(metric.value)}(?![0-9]|\.[0-9])"
        + magnitude
        + _EN_UNIT_SUFFIXES.get(metric.unit, "")
        + (r"\s*" + _EN_RATE_PATTERNS[metric.per] if metric.per else "")
    )
    return re.search(amount, excerpt.replace(",", ""), re.IGNORECASE) is not None


def _date_quoted(value: str, excerpt: str) -> bool:
    """The excerpt names the year and, when the claim supplies one, that month of that year."""
    year, _, month = value.partition("-")
    if not month:
        return re.search(rf"(?<![0-9]){year}(?![0-9])", excerpt) is not None
    index = int(month)
    name = rf"(?:{'|'.join({calendar.month_name[index], calendar.month_abbr[index]})})\.?"
    day = r"[0-9]{1,2}(?:st|nd|rd|th)?"
    # One date expression: "Mar 2021", "March 5, 2021", "5 March 2021", "2021-03(-05)",
    # "3/2021" or "3/5/2021"; the month and the year must belong to the same date.
    together = "|".join(
        (
            rf"\b{name}(?:\s+{day})?,?\s+{year}(?![0-9])",
            rf"\b{day}\s+{name},?\s+{year}(?![0-9])",
            rf"\b{year}-{month}\b",
            rf"\b0?{index}/(?:[0-9]{{1,2}}/)?{year}(?![0-9])",
        )
    )
    return re.search(together, excerpt, re.IGNORECASE) is not None


def grounded(claim: StructuredClaim, excerpt: str) -> bool:
    """Every literal part of a structured claim must appear in its quoted evidence excerpt."""
    folded = excerpt.casefold()
    phrases = [claim.subject, claim.scope, claim.metric.measure if claim.metric else None]
    if any(phrase is not None and phrase.casefold() not in folded for phrase in phrases):
        return False
    if claim.metric is not None and not _metric_quoted(claim.metric, excerpt):
        return False
    if claim.period is not None:
        bounds = {claim.period.start, claim.period.end}
        if not all(_date_quoted(bound, excerpt) for bound in bounds):
            return False
    return True
