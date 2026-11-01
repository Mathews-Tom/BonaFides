# SPDX-License-Identifier: MIT OR Apache-2.0
"""Deterministic checking of claim-structured generated documents.

A factual statement is supported only when it cites exactly one claim of the pinned
generation context and its text is contained, under the structured-claim containment
rule, in the full text of one of that claim's own linked evidence revisions. The
posting and other claims' evidence are never consulted. Non-factual text must be one
of the declared connective templates, whose only variable slots are the posting title
and organization. Anything else is treated as an unsupported factual statement.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Literal

from bonafides.claims import contains_statement
from bonafides.documents import DocumentBody

CHECKER_VERSION = "claim-evidence-v1"
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
_SLOT = re.compile(r"\{(title|organization)\}")


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


def _factual_issue(
    claim_ids: Sequence[str],
    evidence_ids: Sequence[str],
    text: str,
    facts: Sequence[Mapping[str, object]],
) -> str | None:
    if not claim_ids:
        return "uncited_factual_statement"
    if len(claim_ids) != 1:
        return "statement_cites_multiple_claims"
    own = [fact for fact in facts if str(fact["claim_revision_id"]) == claim_ids[0]]
    if not own:
        return "claim_not_in_profile"
    if set(evidence_ids) != {str(fact["evidence_revision_id"]) for fact in own}:
        return "evidence_not_linked_to_claim"
    if not any(contains_statement(text, str(fact["evidence_text"])) for fact in own):
        return "statement_not_in_claim_evidence"
    return None


def check_statements(
    body: DocumentBody,
    facts: Sequence[Mapping[str, object]],
    frame: Mapping[str, object],
    kind: str,
) -> list[dict[str, str]]:
    """Every blocking issue of a claim-structured document, each naming its statement."""
    declared = connectives(frame)
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
                code = _factual_issue(
                    [str(item) for item in block.claim_revision_ids],
                    [str(item) for item in block.evidence_revision_ids],
                    block.text,
                    facts,
                )
                if code is not None:
                    codes.append(code)
            found.extend({"path": path, "code": code, "statement": block.text} for code in codes)
    return found
