# SPDX-License-Identifier: MIT OR Apache-2.0
"""`bonafides claims inflation-report <dir>`: the inflation rules on synthetic case sets.

The directory holds ``honest.jsonl`` and ``inflated.jsonl``. Each line is one case: the
evidence text, the claim (literal text, or claim schema v1 fields pinned as their rendering),
a statement and the expected kind (``null`` for an honest case). Every case runs through the
document checker as a factual statement citing its claim, exactly as a document block does.
The report prints counts, the honest false-block rate and the block rate per kind, and exits
non-zero when the false-block rate exceeds the threshold. Files are neither stored nor sent.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from pydantic import ValidationError

from bonafides.claims import StructuredClaim, contains_statement, render_claim
from bonafides.documents import DocumentBlock, DocumentBody, DocumentSection
from bonafides.inflation import CODES, INFLATION_KINDS, InflationKind
from bonafides.statement_check import check_statements

FALSE_BLOCK_LIMIT = 0.05
_CLAIM = uuid5(NAMESPACE_URL, "bonafides-inflation-report/claim")
_EVIDENCE = uuid5(NAMESPACE_URL, "bonafides-inflation-report/evidence")


class CaseError(ValueError):
    """A case line is not a valid inflation case."""


@dataclass(frozen=True)
class Case:
    line: int
    evidence: str
    claim_text: str
    statement: str
    kind: InflationKind | None


def _text(value: object, name: str, line: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CaseError(f"line {line}: {name} must be non-empty text")
    return value


def _case(raw: str, line: int, honest: bool) -> Case:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CaseError(f"line {line}: invalid JSON ({exc.msg})") from exc
    if not isinstance(value, dict) or set(value) != {"evidence", "claim", "statement", "kind"}:
        raise CaseError(f"line {line}: keys must be evidence, claim, statement and kind")
    claim = value["claim"]
    if isinstance(claim, dict):
        try:
            claim_text = render_claim(StructuredClaim.model_validate(claim))
        except ValidationError as exc:
            raise CaseError(f"line {line}: invalid structured claim") from exc
    else:
        claim_text = _text(claim, "claim", line)
    kind = value["kind"]
    if honest and kind is not None:
        raise CaseError(f"line {line}: an honest case has kind null")
    if not honest and kind not in INFLATION_KINDS:
        raise CaseError(f"line {line}: kind must be one of {', '.join(INFLATION_KINDS)}")
    evidence = _text(value["evidence"], "evidence", line)
    if not contains_statement(claim_text, evidence):
        raise CaseError(f"line {line}: the claim is not contained in its evidence")
    return Case(line, evidence, claim_text, _text(value["statement"], "statement", line), kind)


def load(path: Path, honest: bool) -> list[Case]:
    cases = [
        _case(raw, number, honest)
        for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if raw.strip()
    ]
    if not cases:
        raise CaseError(f"{path.name} contains no cases")
    return cases


def codes(case: Case) -> list[str]:
    """Every checker code for the case's statement cited as a factual block of its claim."""
    body = DocumentBody(
        title="Base résumé",
        sections=[
            DocumentSection(
                key="experience",
                heading="Experience",
                blocks=[
                    DocumentBlock(
                        block_key="statement",
                        text=case.statement,
                        claim_kind="factual",
                        claim_revision_ids=[_CLAIM],
                        evidence_revision_ids=[_EVIDENCE],
                    )
                ],
            )
        ],
    )
    fact: dict[str, object] = {
        "claim_revision_id": str(_CLAIM),
        "evidence_revision_id": str(_EVIDENCE),
        "claim_text": case.claim_text,
        "evidence_text": case.evidence,
    }
    return [issue["code"] for issue in check_statements(body, [fact], {}, "base")]


@dataclass(frozen=True)
class InflationReport:
    honest: int
    false_blocks: list[tuple[int, list[str]]]
    inflated: dict[InflationKind, tuple[int, int]]
    misattributed: list[tuple[int, list[str]]]

    @property
    def false_block_rate(self) -> float:
        return len(self.false_blocks) / self.honest

    def lines(self) -> list[str]:
        blocked = len(self.false_blocks)
        # Ceil the false-block share and floor block shares so neither displays as better.
        rate = math.ceil(blocked * 1000 / self.honest) / 10
        output = [f"Honest: {blocked} of {self.honest} blocked (false-block {rate:.1f}%)"]
        output.extend(
            f"honest line {line}: {', '.join(found)}" for line, found in self.false_blocks
        )
        for kind, (hits, total) in self.inflated.items():
            share = math.floor(hits * 1000 / total) / 10 if total else 0.0
            output.append(f"Inflated {kind}: {hits} of {total} blocked ({share:.1f}%)")
        output.extend(
            f"inflated line {line}: not blocked as its kind ({', '.join(found) or 'no blocker'})"
            for line, found in self.misattributed
        )
        verdict = "within" if self.false_block_rate <= FALSE_BLOCK_LIMIT else "exceeds"
        output.append(f"False-block rate {verdict} the {FALSE_BLOCK_LIMIT:.0%} limit")
        return output


def report(honest: list[Case], inflated: list[Case]) -> InflationReport:
    false_blocks = [(case.line, found) for case in honest if (found := codes(case))]
    counts: dict[InflationKind, tuple[int, int]] = {kind: (0, 0) for kind in INFLATION_KINDS}
    misattributed: list[tuple[int, list[str]]] = []
    for case in inflated:
        if case.kind is None:
            raise CaseError(f"line {case.line}: an inflated case names its kind")
        found = codes(case)
        hits, total = counts[case.kind]
        hit = CODES[case.kind] in found
        counts[case.kind] = (hits + hit, total + 1)
        if not hit:
            misattributed.append((case.line, found))
    return InflationReport(len(honest), false_blocks, counts, misattributed)


def run(directory: Path) -> InflationReport:
    return report(
        load(directory / "honest.jsonl", honest=True),
        load(directory / "inflated.jsonl", honest=False),
    )
