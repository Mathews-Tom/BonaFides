# SPDX-License-Identifier: MIT OR Apache-2.0
"""`bonafides claims inflation-report <dir>`: the claim checker on synthetic case sets.

The directory holds ``honest.jsonl`` and ``inflated.jsonl``. Each line is one case: the
evidence text, the claim (literal text, or claim schema v1 fields pinned as their rendering),
a statement and the expected kind (``null`` for an honest case); optional ``fields`` hold the
claim's reviewed structured-field lines, which make its pinned printable text. Every case runs through the
document checker as a factual statement citing its claim, exactly as a document block does.
A verb promoted or a phrase truncated by a mid-claim cut must block as
``statement_cut_inside_claim``; a longer period, larger figure or wider scope taken
from elsewhere in the evidence must block as not part of its claim. The report prints counts, the honest false-block rate and the block rate per kind,
and exits non-zero when the false-block rate exceeds the threshold. Files are neither stored
nor sent.
Each UTF-8 file is limited to 5 MiB and each line to 64 KiB, including its line ending.
"""

from __future__ import annotations

import io
import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import ValidationError

from bonafides.claims import StructuredClaim, contains_statement, render_claim
from bonafides.documents import DocumentBlock, DocumentBody, DocumentSection
from bonafides.statement_check import PRINTABLE_FIELDS, check_statements, printable_text

InflationKind = Literal[
    "stronger_verb", "longer_tenure", "larger_metric", "wider_scope", "truncated_phrase"
]
INFLATION_KINDS: tuple[InflationKind, ...] = get_args(InflationKind)
# The checker code each kind of strengthening must block with.
EXPECTED_CODES: dict[InflationKind, str] = {
    "stronger_verb": "statement_cut_inside_claim",
    "longer_tenure": "statement_not_in_claim",
    "larger_metric": "statement_not_in_claim",
    "wider_scope": "statement_not_in_claim",
    "truncated_phrase": "statement_cut_inside_claim",
}
FALSE_BLOCK_LIMIT = 0.05
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_LINE_BYTES = 64 * 1024
CLAIM = uuid5(NAMESPACE_URL, "bonafides-inflation-report/claim")
EVIDENCE = uuid5(NAMESPACE_URL, "bonafides-inflation-report/evidence")


class CaseError(ValueError):
    """A case line is not a valid inflation case."""


@dataclass(frozen=True)
class Claim:
    """A case claim with its pinned text, reviewed field lines and the evidence holding it."""

    evidence: str
    text: str
    fields: dict[str, str]
    verb: str | None

    def fact(self, claim_id: UUID, evidence_id: UUID) -> dict[str, object]:
        """The claim as one pinned generation-context fact under the given revision ids."""
        return {
            "claim_revision_id": str(claim_id),
            "evidence_revision_id": str(evidence_id),
            "claim_text": self.text,
            "evidence_text": self.evidence,
            "structured_fields": self.fields,
            "printable_text": printable_text(self.text, self.fields),
            "claim_verb": self.verb,
        }


@dataclass(frozen=True)
class Case:
    line: int
    evidence: str
    claim_text: str
    statement: str
    kind: InflationKind | None
    fields: dict[str, str]
    claim_verb: str | None


def text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CaseError(f"{name} must be non-empty text")
    return value


def claim(value: Mapping[str, object]) -> Claim:
    """Validate a record's ``evidence``, ``claim`` and optional ``fields``; the claim must be
    contained in its evidence."""
    fields = value.get("fields", {})
    if not isinstance(fields, dict) or not set(fields) <= set(PRINTABLE_FIELDS):
        raise CaseError(f"fields must hold only {', '.join(PRINTABLE_FIELDS)}")
    printed = {key: text(item, key) for key, item in fields.items()}
    raw = value["claim"]
    verb: str | None = None
    if isinstance(raw, dict):
        try:
            structured = StructuredClaim.model_validate(raw)
        except ValidationError as exc:
            raise CaseError("invalid structured claim") from exc
        claim_text = render_claim(structured)
        verb = structured.verb
    else:
        claim_text = text(raw, "claim")
    evidence = text(value["evidence"], "evidence")
    if not contains_statement(claim_text, evidence):
        raise CaseError("the claim is not contained in its evidence")
    return Claim(evidence, claim_text, printed, verb)


def parse_case(raw: str, line: int, honest: bool) -> Case:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CaseError(f"invalid JSON ({exc.msg})") from exc
    if not isinstance(value, dict) or not (
        {"evidence", "claim", "statement", "kind"}
        <= set(value)
        <= {"evidence", "claim", "statement", "kind", "fields"}
    ):
        raise CaseError("keys must be evidence, claim, statement, kind and optional fields")
    own = claim(value)
    kind = value["kind"]
    if honest and kind is not None:
        raise CaseError("an honest case has kind null")
    if not honest and kind not in INFLATION_KINDS:
        raise CaseError(f"kind must be one of {', '.join(INFLATION_KINDS)}")
    statement = text(value["statement"], "statement")
    return Case(line, own.evidence, own.text, statement, kind, own.fields, own.verb)


def read_bounded(path: Path) -> bytes:
    """A case file's bytes, refused once it exceeds the file limit."""
    with path.open("rb") as source:
        data = source.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise CaseError(f"{path.name}:1: file exceeds {MAX_FILE_BYTES} bytes")
    return data


def parse_lines[T](name: str, data: bytes, parse: Callable[[str, int], T]) -> list[T]:
    """Parse bounded non-blank UTF-8 lines, attaching the filename and line to input errors."""
    items: list[T] = []
    source = io.BytesIO(data)
    number = 0
    while raw := source.readline(MAX_LINE_BYTES + 1):
        number += 1
        if len(raw) > MAX_LINE_BYTES:
            raise CaseError(f"{name}:{number}: line exceeds {MAX_LINE_BYTES} bytes")
        try:
            decoded = raw.decode("utf-8")
            if decoded.strip():
                items.append(parse(decoded, number))
        except (ValueError, RecursionError) as exc:
            raise CaseError(f"{name}:{number}: {exc}") from exc
    if not items:
        raise CaseError(f"{name}:1: contains no cases")
    return items


def load(path: Path, honest: bool) -> list[Case]:
    """Read bounded case lines and attach their filename and line to input errors."""
    return parse_lines(
        path.name, read_bounded(path), lambda raw, line: parse_case(raw, line, honest)
    )


def block_codes(
    statement: str,
    claim_kind: Literal["factual", "intent"],
    cited: Sequence[tuple[UUID, UUID]],
    facts: Sequence[Mapping[str, object]],
) -> list[str]:
    """Every checker code for one block of a one-section base résumé over ``facts``."""
    body = DocumentBody(
        title="Base résumé",
        sections=[
            DocumentSection(
                key="experience",
                heading="Experience",
                blocks=[
                    DocumentBlock(
                        block_key="statement",
                        text=statement,
                        claim_kind=claim_kind,
                        claim_revision_ids=[claim_id for claim_id, _ in cited],
                        evidence_revision_ids=[evidence_id for _, evidence_id in cited],
                    )
                ],
            )
        ],
    )
    return [issue["code"] for issue in check_statements(body, facts, {}, "base")]


def codes(case: Case) -> list[str]:
    """Every checker code for the case's statement cited as a factual block of its claim."""
    own = Claim(case.evidence, case.claim_text, case.fields, case.claim_verb)
    return block_codes(case.statement, "factual", [(CLAIM, EVIDENCE)], [own.fact(CLAIM, EVIDENCE)])


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
        hit = EXPECTED_CODES[case.kind] in found
        counts[case.kind] = (hits + hit, total + 1)
        if not hit:
            misattributed.append((case.line, found))
    return InflationReport(len(honest), false_blocks, counts, misattributed)


def run(directory: Path) -> InflationReport:
    return report(
        load(directory / "honest.jsonl", honest=True),
        load(directory / "inflated.jsonl", honest=False),
    )
