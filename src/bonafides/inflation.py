# SPDX-License-Identifier: MIT OR Apache-2.0
"""Deterministic rules that block a statement strengthening the claim it cites.

Containment alone admits text cut from the claim's evidence that drops a qualifier or an
ownership cue. These rules compare a contained statement with its claim's own pinned text,
read by the same parsers the claim mapper uses, and name each kind of strengthening:

- stronger verb: the statement's leading ownership verb ranks above the claim's verb in the
  ordered ownership vocabulary (a lower position is stronger);
- longer tenure: a period the statement states is not within a period the claim states
  (a bare year spans the whole year, so it is longer than any month range inside it);
- larger metric: a figure the statement states has no claim figure of the same unit,
  magnitude and rate at least as large (dropping a rate is not a change of rate);
- wider scope: the statement's widest scope term ranks above the claim's widest one on the
  declared ordered scope vocabulary.

A structured claim's pinned text is the deterministic rendering of its fields, so reading it
recovers exactly its verb, period, metric and scope; a literal claim is read the same way.
A rule whose part the statement or the claim does not state does not fire, and a statement
that reproduces its claim never strengthens it. Nothing here matches synonyms or meaning.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal, cast, get_args

from bonafides.claim_mapping import (
    _ADVERB_RATES,
    _AMOUNT,
    _MAGNITUDES,
    _METRIC_ONLY,
    _NUMBER,
    _VERB_PHRASES,
    _duration,
    _verb,
    statement_text,
)
from bonafides.claims import (
    OWNERSHIP_VERBS,
    Magnitude,
    MetricRate,
    MetricUnit,
    _normalise_grounding,
    date_bounds,
)

InflationKind = Literal["stronger_verb", "longer_tenure", "larger_metric", "wider_scope"]
INFLATION_KINDS: tuple[InflationKind, ...] = get_args(InflationKind)
CODES: dict[InflationKind, str] = {kind: f"statement_{kind}" for kind in INFLATION_KINDS}

# Ordered from narrowest to widest. Extended only with honest-set evidence.
SCOPE_LEVELS: tuple[tuple[str, ...], ...] = (
    ("personal", "individual"),
    ("team", "teams", "squad", "squads"),
    ("cross-team", "cross-functional", "department-wide", "division-wide", "across teams"),
    (
        "organization-wide",
        "organisation-wide",
        "org-wide",
        "company-wide",
        "across the company",
        "across the organization",
        "across the organisation",
        "enterprise-wide",
    ),
    ("cross-company", "industry-wide", "globally", "worldwide"),
)
_SCOPE_TERMS = {term: level for level, terms in enumerate(SCOPE_LEVELS) for term in terms}
_SCOPE = re.compile(
    r"(?<![^\W_])(?:"
    + "|".join(re.escape(term) for term in sorted(_SCOPE_TERMS, key=lambda item: -len(item)))
    + r")(?![^\W_])"
)
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_MONTH_NAME = (
    r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?"
    r"|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?"
    r"|dec(?:ember)?)\b\.?"
)
_DATE = (
    rf"(?:(?P<{{0}}name>{_MONTH_NAME})\s+|(?P<{{0}}slash>0?[1-9]|1[0-2])/)?"
    r"(?P<{0}year>(?:19|20)[0-9]{{2}})(?:-(?P<{0}month>0[1-9]|1[0-2]))?"
)
_SPAN = re.compile(
    rf"(?<![\w./$€£-]){_DATE.format('start_')}"
    rf"(?:\s*(?:[-–—]|\bto\b|\buntil\b|\bthrough\b)\s*"
    rf"(?:{_DATE.format('end_')}|(?P<open>present|current|now)\b))?"
    r"(?![\w%×]|[.,][0-9])",
    re.IGNORECASE,
)
_FIGURE = re.compile(
    rf"(?<![\w.,/-])(?P<currency>[$€£])?(?P<number>{_NUMBER})"
    r"\s*(?P<magnitude>thousand|million|billion|[kmb])?"
    r"\s*(?P<unit>%|percent\b|[x×](?!\w)|points?\b|pts\b|ms\b"
    r"|milliseconds?\b|seconds?\b|minutes?\b|hours?\b|days?\b|weeks?\b|months?\b)?"
    r"(?![\w]|[.,][0-9])",
    re.IGNORECASE,
)
_CLAUSE = re.compile(r"(\band\b|&|;|,)(?![0-9])", re.IGNORECASE)
_LEADING_ADVERBS = re.compile(r"^(?:[a-z]+ly\s+)+", re.IGNORECASE)
_ATTACHED_RATE = re.compile(
    r"(?:\b(?:per|a|an|each|every)\s+|/)\s*(?P<unit>day|week|month|quarter|year)\b"
    r"|\b(?P<adverb>daily|weekly|monthly|quarterly|yearly|annually)\b",
    re.IGNORECASE,
)
_FACTORS = {
    None: Decimal(1),
    "thousand": Decimal(1000),
    "million": Decimal(1000000),
    "billion": Decimal(1000000000),
}


@dataclass(frozen=True)
class Figure:
    value: Decimal
    unit: MetricUnit
    magnitude: Magnitude | None
    per: MetricRate | None


@dataclass(frozen=True)
class Reading:
    """The parts of one text these rules compare; ``None`` or empty means not stated."""

    verb_rank: int | None
    periods: tuple[tuple[date, date], ...]
    figures: tuple[Figure, ...]
    scope_rank: int | None


def _rank(phrase: str, rest: str) -> int | None:
    # "Cut the 2.0 release" is not an outcome claim: these verbs count only with an amount.
    if phrase in _METRIC_ONLY and _AMOUNT.search(rest) is None:
        return None
    return OWNERSHIP_VERBS.index(_VERB_PHRASES[phrase][0])


def _verb_rank(text: str) -> int | None:
    """Read only clause-initial verbs, never verbs inside relative clauses."""
    ranks: list[int] = []
    relative = False
    for index, clause in enumerate(_CLAUSE.split(statement_text(text))):
        if index % 2:
            if clause == ";":
                relative = False
            continue
        if not relative:
            initial = _LEADING_ADVERBS.sub("", clause.strip())
            found = _verb(initial)
            if found is not None and (rank := _rank(found[0], found[3])) is not None:
                ranks.append(rank)
        relative = relative or bool(re.search(r"\b(?:that|who|which|whose)\b", clause, re.I))
    return min(ranks) if ranks else None


def _bound(match: re.Match[str], prefix: str, upper: bool) -> date:
    year = match[f"{prefix}year"]
    named, slash, numeric = match[f"{prefix}name"], match[f"{prefix}slash"], match[f"{prefix}month"]
    month = (
        _MONTHS.index(named[:3].casefold()) + 1
        if named
        else int(slash)
        if slash
        else int(numeric)
        if numeric
        else None
    )
    bounds = date_bounds(year if month is None else f"{year}-{month:02d}")
    return bounds[1] if upper else bounds[0]


def _periods(text: str) -> tuple[list[tuple[date, date]], list[tuple[int, int]]]:
    periods: list[tuple[date, date]] = []
    spans: list[tuple[int, int]] = []
    for match in _SPAN.finditer(text):
        prefix = text[: match.start()]
        contextual = (
            match["start_name"]
            or match["start_slash"]
            or match["start_month"]
            or match["end_year"]
            or match["open"]
            or re.search(r"\b(?:in|since|from|during|until|through)\s*$", prefix, re.I)
            or (prefix.rstrip().endswith("(") and text[match.end() :].lstrip().startswith(")"))
        )
        if not contextual:
            continue
        start = _bound(match, "start_", False)
        if match["open"] or (
            not match["end_year"] and re.search(r"\b(?:since|from)\s*$", prefix, re.I)
        ):
            end = date.max
        else:
            end = _bound(match, "end_" if match["end_year"] else "start_", True)
        if start <= end:
            periods.append((start, end))
            spans.append(match.span())
    return periods, spans


def _rate(tail: str) -> MetricRate | None:
    # A noun may separate the amount from its rate ("500 tickets per week").
    clause = re.split(r"[;,.]|\band\b|&", tail, maxsplit=1, flags=re.I)[0]
    rate = _ATTACHED_RATE.search(clause)
    if rate is None:
        return None
    if rate["adverb"]:
        return _ADVERB_RATES[rate["adverb"].casefold()]
    return cast(MetricRate, rate["unit"].casefold())


def _figures(text: str, masked: list[tuple[int, int]]) -> list[Figure]:
    figures: list[Figure] = []
    for match in _FIGURE.finditer(text):
        if any(match.start() < end and start < match.end() for start, end in masked):
            continue
        currency, word = match["currency"], (match["unit"] or "").casefold()
        unit: MetricUnit
        if currency:
            unit = cast(MetricUnit, {"$": "usd", "€": "eur", "£": "gbp"}[currency])
        elif word in ("%", "percent"):
            unit = "percent"
        elif word in ("x", "×"):
            unit = "times"
        elif word in ("point", "points", "pts"):
            unit = "points"
        elif word:
            unit = "milliseconds" if word == "ms" else _duration(word)
        else:
            unit = "count"
        magnitude = _MAGNITUDES[match["magnitude"].casefold()] if match["magnitude"] else None
        figures.append(
            Figure(
                Decimal(match["number"].replace(",", "")),
                unit,
                magnitude,
                _rate(text[match.end() :]),
            )
        )
    return figures


def _scope_rank(text: str) -> int | None:
    normalised = _normalise_grounding(text)
    levels = [_SCOPE_TERMS[match.group(0)] for match in _SCOPE.finditer(normalised)]
    if re.search(r"\bacross\s+[0-9]+\s+teams\b", normalised):
        levels.append(2)
    return max(levels) if levels else None


def read(text: str) -> Reading:
    periods, spans = _periods(text)
    return Reading(
        _verb_rank(text), tuple(periods), tuple(_figures(text, spans)), _scope_rank(text)
    )


def _within_claim(figure: Figure, claimed: tuple[Figure, ...]) -> bool:
    return any(
        figure.unit == item.unit
        and figure.per in (None, item.per)
        and figure.value * _FACTORS[figure.magnitude] <= item.value * _FACTORS[item.magnitude]
        for item in claimed
    )


def inflation_kinds(
    statement: str,
    claim_text: str,
    *,
    claim_dates: tuple[str | None, str | None] | None = None,
) -> list[InflationKind]:
    """Each kind by which a contained statement strengthens its claim's pinned text."""
    said, claimed = read(statement), read(claim_text)
    if not claimed.periods and claim_dates is not None and claim_dates[0]:
        low = date_bounds(claim_dates[0])[0]
        high = date_bounds(claim_dates[1])[1] if claim_dates[1] else date.max
        claimed = Reading(claimed.verb_rank, ((low, high),), claimed.figures, claimed.scope_rank)
    kinds: list[InflationKind] = []
    if (
        said.verb_rank is not None
        and claimed.verb_rank is not None
        and said.verb_rank < claimed.verb_rank
    ):
        kinds.append("stronger_verb")
    if claimed.periods and any(
        not any(low <= start and end <= high for low, high in claimed.periods)
        for start, end in said.periods
    ):
        kinds.append("longer_tenure")
    if claimed.figures and any(
        not _within_claim(figure, claimed.figures) for figure in said.figures
    ):
        kinds.append("larger_metric")
    if (
        said.scope_rank is not None
        and claimed.scope_rank is not None
        and said.scope_rank > claimed.scope_rank
    ):
        kinds.append("wider_scope")
    return kinds


__all__ = ["CODES", "INFLATION_KINDS", "SCOPE_LEVELS", "Figure", "Reading", "inflation_kinds"]
