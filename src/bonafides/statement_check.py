# SPDX-License-Identifier: MIT OR Apache-2.0
"""Deterministic checking of claim-structured generated documents.

A factual statement is supported only when it cites exactly one claim of the pinned
generation context, that claim's pinned text is contained, under the structured-claim
containment rule, in the full text of one of the claim's own linked evidence revisions, and
the statement is either exactly the claim's pinned printable text (its claim text followed by
that claim revision's own reviewed structured-field lines), a whole-phrase prefix of its claim
text, or a whole-phrase prefix after the claim's leading verb. Nothing is borrowed from
elsewhere in the evidence, the posting or other claims. A prefix cannot end inside a
hyphenated word. The leading verb is the structured claim's pinned rendered verb, or a
literal claim's leading ownership-vocabulary phrase. Dropping it is unavailable if the
remainder starts with another vocabulary verb, optionally after "and", "&" or a comma.
Prefixes end only at the claim's end or before declared punctuation or boundary words;
qualifiers starting with those words can still be dropped.
Review cannot waive any of these blockers.
Non-factual text must be one of the declared connective templates, whose only variable
slots are the posting title and organization; a base résumé has no connective text.
Anything else is treated as an unsupported factual statement.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Literal

from bonafides.claims import _EN_VERBS, _normalise_grounding, contains_statement

if TYPE_CHECKING:
    from bonafides.documents import DocumentBody

CHECKER_VERSION = "claim-evidence-v4"
CATEGORIES = {
    "experience": "Experience",
    "education": "Education",
    "skill": "Skills",
    "project": "Projects",
    "certification": "Certifications",
    "language": "Languages",
    "other": "Profile evidence",
}
HEADINGS = frozenset({*CATEGORIES.values(), "Application", "Letter", "Interest", "Summary"})
ConnectiveKind = Literal["greeting", "intent"]
# The closed connective set. Slots are limited to {title} and {organization} of the
# posting; a template whose slot value is absent is not available.
CONNECTIVES: dict[ConnectiveKind, tuple[str, ...]] = {
    "greeting": (
        "Dear Hiring team,",
        "Dear {organization} hiring team,",
        "Sincerely,",
        "Kind regards,",
        "Thank you for your consideration.",
        "Thank you for considering my application.",
    ),
    "intent": (
        "I am applying for the {title} role at {organization}.",
        "I am writing to apply for the {title} position at {organization}.",
        "I would welcome the opportunity to discuss this role.",
        "I would welcome a discussion of the role.",
        "I would welcome the opportunity to discuss the {title} role at {organization}.",
        "The following examples from my record are relevant to this role.",
    ),
}
# Reviewed structured fields printed after a claim's text, one line each, in this order.
PRINTABLE_FIELDS = ("role", "organization", "start_date", "end_date", "proficiency")
_SLOT = re.compile(r"\{(title|organization)\}")
# Use the renderer's wording, not a second rendering of the vocabulary keys.
_VERB_WORDING: dict[str, str] = {
    key: _normalise_grounding(wording) for key, wording in _EN_VERBS.items()
}
# Longest phrase first; hyphen-joined words do not hold a vocabulary verb.
_VOCABULARY_VERB = re.compile(
    r"(?:"
    + "|".join(
        re.escape(phrase) for phrase in sorted(_VERB_WORDING.values(), key=len, reverse=True)
    )
    + r")(?![^\W_]|-)"
)
_CONJUNCTION = re.compile(r"^(?:and\b|&|,)\s*")
_CUT_END = re.compile(
    r"\s*(?:[,;:(]|(?:for|by|to|on|with|in|across|from|through|during|at|that)(?![^\W_]|-))"
)


def title_issues(body: DocumentBody, kind: str) -> list[dict[str, str]]:
    expected = (
        "Cover letter" if kind == "cover_letter" else "Base résumé" if kind == "base" else "Résumé"
    )
    return [] if body.title == expected else [{"path": "/title", "code": "unestablished_heading"}]


def heading_issues(index: int, heading: str) -> list[dict[str, str]]:
    if heading in HEADINGS:
        return []
    return [{"path": f"/sections/{index}/heading", "code": "unestablished_heading"}]


def text_issues(path: str, text: str) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    if any((ord(char) < 32 and char not in "\n\t") or 127 <= ord(char) <= 159 for char in text):
        found.append({"path": path, "code": "disallowed_control"})
    if re.search(r"\[(?:add|insert|todo|tbd|fill|replace)\b[^\]]*\]", text, re.IGNORECASE):
        found.append({"path": path, "code": "unresolved_omission_marker"})
    return found


def connectives(frame: Mapping[str, object]) -> dict[str, ConnectiveKind]:
    """Every declared connective rendered with this posting's title and organization."""
    slots = {"title": frame.get("role"), "organization": frame.get("organization")}
    rendered: dict[str, ConnectiveKind] = {}
    for kind, templates in CONNECTIVES.items():
        for template in templates:
            names = _SLOT.findall(template)
            if any(not isinstance(slots[name], str) or not slots[name] for name in names):
                continue
            rendered[_SLOT.sub(lambda match: str(slots[match.group(1)]), template)] = kind
    return rendered


def printable_text(claim_text: str, fields: Mapping[str, str]) -> str:
    """A claim's printable text: its claim text, then its reviewed structured-field lines."""
    return "\n".join([claim_text, *(fields[key] for key in PRINTABLE_FIELDS if key in fields)])


def _factual_issues(
    claim_ids: Sequence[str],
    evidence_ids: Sequence[str],
    text: str,
    facts: Sequence[Mapping[str, object]],
) -> list[str]:
    if not claim_ids:
        return ["uncited_factual_statement"]
    if len(claim_ids) != 1:
        return ["statement_cites_multiple_claims"]
    own = [fact for fact in facts if str(fact["claim_revision_id"]) == claim_ids[0]]
    if not own:
        return ["claim_not_in_profile"]
    if set(evidence_ids) != {str(fact["evidence_revision_id"]) for fact in own}:
        return ["evidence_not_linked_to_claim"]
    claim_text = str(own[0]["claim_text"])
    if not any(contains_statement(claim_text, str(fact["evidence_text"])) for fact in own):
        return ["statement_not_in_claim_evidence"]
    # The pinned printable text appends only the claim revision's own reviewed fields.
    if text in {fact.get("printable_text") for fact in own}:
        return []
    if not contains_statement(text, claim_text):
        return ["statement_not_in_claim"]
    verb = own[0].get("claim_verb")
    if _claim_prefix(text, claim_text, verb if isinstance(verb, str) else None):
        return []
    return ["statement_cut_inside_claim"]


def _whole_prefix(statement: str, text: str) -> bool:
    """An already normalised prefix, with containment's boundaries plus no hyphen cut."""
    currencies = re.escape("".join({char for char in text if unicodedata.category(char) == "Sc"}))
    currency_boundary = rf"|[{currencies}]" if currencies else ""
    return (
        re.match(
            rf"{re.escape(statement)}(?![^\W_]|-|(?<=\d)[%×]|[.,]\d{currency_boundary})",
            text,
        )
        is not None
    )


def _permitted_prefix(statement: str, text: str) -> bool:
    """A whole prefix ending at the claim end or a declared punctuation/word boundary."""
    if not _whole_prefix(statement, text):
        return False
    rest = text[len(statement) :]
    return not rest or _CUT_END.match(rest) is not None


def _claim_prefix(statement: str, claim_text: str, claim_verb: str | None) -> bool:
    """Accept only the claim's prefix or an unambiguous prefix after its leading verb."""
    statement = _normalise_grounding(statement).rstrip(".;")
    claim_text = _normalise_grounding(claim_text).rstrip(".;")
    if _permitted_prefix(statement, claim_text):
        return True
    if claim_verb is None:
        leading = _VOCABULARY_VERB.match(claim_text)
        if leading is None:
            return False
        end = leading.end()
    else:
        wording = _VERB_WORDING.get(claim_verb)
        if wording is None or not _whole_prefix(wording, claim_text):
            return False
        end = len(wording)
    remainder = claim_text[end:].lstrip()
    if _VOCABULARY_VERB.match(_CONJUNCTION.sub("", remainder, count=1)):
        return False
    return _permitted_prefix(statement, remainder)


def check_statements(
    body: DocumentBody,
    facts: Sequence[Mapping[str, object]],
    frame: Mapping[str, object],
    kind: str,
) -> list[dict[str, str]]:
    """Every blocking issue of a claim-structured document, each naming its statement."""
    declared = {} if kind == "base" else connectives(frame)
    found = [{**issue, "statement": body.title} for issue in title_issues(body, kind)]
    for si, section in enumerate(body.sections):
        found.extend(
            {**issue, "statement": section.heading} for issue in heading_issues(si, section.heading)
        )
        for bi, block in enumerate(section.blocks):
            path = f"/sections/{si}/blocks/{bi}"
            codes = [issue["code"] for issue in text_issues(path, block.text)]
            if block.claim_kind != "factual":
                if (
                    block.claim_revision_ids
                    or block.evidence_revision_ids
                    or block.text not in declared
                ):
                    codes.append("connective_not_declared")
            else:
                codes.extend(
                    _factual_issues(
                        [str(item) for item in block.claim_revision_ids],
                        [str(item) for item in block.evidence_revision_ids],
                        block.text,
                        facts,
                    )
                )
            found.extend({"path": path, "code": code, "statement": block.text} for code in codes)
    return found
