# SPDX-License-Identifier: MIT OR Apache-2.0
"""Deterministic mapping of one English résumé line to a claim schema v1 proposal.

The mapper never guesses: a part it cannot place is returned as an unparsed part with a reason,
and a line without a recognized ownership verb or subject yields no claim. A complete proposal
is accepted only when the source quotes its rendered sentence under the strict grounding rule.
"""

from __future__ import annotations

import re
from typing import Literal, cast

from pydantic import ValidationError

from bonafides.auth import StrictModel
from bonafides.claims import (
    ClaimMetric,
    ClaimPeriod,
    Magnitude,
    MetricChange,
    MetricRate,
    MetricUnit,
    OwnershipVerb,
    StructuredClaim,
    grounded,
    render_claim,
)

UnparsedReason = Literal[
    "unknown_verb",
    "missing_subject",
    "additional_metric",
    "multiple_statements",
    "invalid_value",
    "unplaced_scope",
    "unquoted_rendering",
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
    # Recognizing synonyms or metric notation does not make a reworded sentence evidence.
    # Keep the proposal inspectable, but approval falls back to the original literal line.
    if not unparsed and not grounded(claim, line):
        unparsed.append(UnparsedPart(text=statement_text(line), reason="unquoted_rendering"))
    return ClaimMapping(claim=claim, rendering=render_claim(claim), unparsed=unparsed)


__all__ = ["ClaimMapping", "ClaimMetric", "ClaimPeriod", "UnparsedPart", "map_line"]
