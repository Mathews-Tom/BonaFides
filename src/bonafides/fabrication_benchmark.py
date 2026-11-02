# SPDX-License-Identifier: MIT OR Apache-2.0
"""`bonafides benchmark run <dir>`: the claim checker and a lexical baseline on temptations.

The dataset directory holds exactly the three files the run reads, each bounded like the
inflation report's case files:

- ``honest.jsonl`` and ``inflated.jsonl`` in the inflation report's case format; inflated
  kinds are the five temptation classes the inflation rules target.
- ``untargeted.jsonl``: temptation classes the inflation rules do not target. Each line has
  ``evidence``, ``claim``, ``statement``, ``kind`` (an untargeted class) and optional
  ``fields``, plus optional ``citation`` (``own`` by default; ``absent`` cites a claim missing
  from the profile; ``uncited`` is a factual block citing nothing; ``connective`` is a
  non-factual block) and optional ``others``: further profile claims, each ``evidence``,
  ``claim`` and optional ``fields``. Every claim must be contained in its evidence.

Each statement runs through the document checker as the only block of a base résumé whose
profile holds the case's claim and its ``others``; a case is blocked by the checker when any
issue is reported. The lexical baseline blocks a statement unless every content word
(lowercased word characters, minus ``STOP_WORDS``) appears among the words of its cited
claim's evidence; a statement citing no profile claim has no evidence words. The baseline is
fixed and never tuned to a dataset.

The report is canonical JSON (sorted keys, integer counts, per-mille rates; block rates floor
and false-block rates ceil, so neither displays as better) and a Markdown table. The dataset
digest is SHA-256 over each read file's relative path, a NUL, its byte length, a NUL and its
bytes, in sorted path order. Files are neither stored nor sent.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypedDict, get_args
from uuid import NAMESPACE_URL, UUID, uuid5

from bonafides.inflation_report import (
    CLAIM,
    EVIDENCE,
    INFLATION_KINDS,
    Case,
    CaseError,
    Claim,
    block_codes,
    claim,
    parse_case,
    parse_lines,
    read_bounded,
    text,
)
from bonafides.statement_check import CHECKER_VERSION

UntargetedClass = Literal[
    "invented_claim",
    "posting_only_skill",
    "cross_claim_borrowing",
    "uncited_statement",
    "paraphrase_inflation",
    "dropped_qualifier",
]
UNTARGETED_CLASSES: tuple[UntargetedClass, ...] = get_args(UntargetedClass)
CLASSES: tuple[str, ...] = (*INFLATION_KINDS, *UNTARGETED_CLASSES)
Citation = Literal["own", "absent", "uncited", "connective"]
# The citations each untargeted class may use; every other class cites its own claim.
CITATIONS: dict[UntargetedClass, frozenset[Citation]] = {
    "invented_claim": frozenset({"absent"}),
    "posting_only_skill": frozenset({"own"}),
    "cross_claim_borrowing": frozenset({"own"}),
    "uncited_statement": frozenset({"uncited", "connective"}),
    "paraphrase_inflation": frozenset({"own"}),
    "dropped_qualifier": frozenset({"own"}),
}
DATASET_FILES = ("honest.jsonl", "inflated.jsonl", "untargeted.jsonl")
BASELINE_VERSION = "lexical-content-words-v1"
STOP_WORDS = frozenset(
    [
        "a",
        "about",
        "across",
        "also",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "being",
        "but",
        "by",
        "did",
        "do",
        "does",
        "during",
        "for",
        "from",
        "had",
        "has",
        "have",
        "i",
        "in",
        "into",
        "is",
        "it",
        "its",
        "me",
        "my",
        "nor",
        "of",
        "on",
        "onto",
        "or",
        "our",
        "over",
        "per",
        "so",
        "than",
        "that",
        "the",
        "then",
        "these",
        "this",
        "those",
        "through",
        "to",
        "under",
        "us",
        "via",
        "was",
        "we",
        "were",
        "when",
        "where",
        "which",
        "while",
        "who",
        "whom",
        "whose",
        "with",
        "within",
        "without",
    ]
)
_WORD = re.compile(r"[^\W_]+")
_ABSENT_CLAIM = uuid5(NAMESPACE_URL, "bonafides-fabrication-benchmark/absent-claim")
_ABSENT_EVIDENCE = uuid5(NAMESPACE_URL, "bonafides-fabrication-benchmark/absent-evidence")
_KEYS = {"evidence", "claim", "statement", "kind", "fields", "citation", "others"}
_OTHER_KEYS = {"evidence", "claim", "fields"}


@dataclass(frozen=True)
class Temptation:
    """One statement as a block, the profile claims around it and its cited evidence."""

    kind: str | None
    statement: str
    citation: Citation
    own: Claim
    others: tuple[Claim, ...]

    def checker_blocks(self) -> bool:
        ids = [(CLAIM, EVIDENCE)]
        ids.extend(_other_ids(index) for index in range(len(self.others)))
        facts = [
            item.fact(claim_id, evidence_id)
            for item, (claim_id, evidence_id) in zip((self.own, *self.others), ids, strict=True)
        ]
        cited = {
            "own": [(CLAIM, EVIDENCE)],
            "absent": [(_ABSENT_CLAIM, _ABSENT_EVIDENCE)],
            "uncited": [],
            "connective": [],
        }[self.citation]
        claim_kind: Literal["factual", "intent"] = (
            "intent" if self.citation == "connective" else "factual"
        )
        return bool(block_codes(self.statement, claim_kind, cited, facts))

    def baseline_blocks(self) -> bool:
        evidence = self.own.evidence if self.citation == "own" else ""
        return not set(content_words(self.statement)) <= set(content_words(evidence))


def _other_ids(index: int) -> tuple[UUID, UUID]:
    prefix = f"bonafides-fabrication-benchmark/other-{index}"
    return uuid5(NAMESPACE_URL, f"{prefix}/claim"), uuid5(NAMESPACE_URL, f"{prefix}/evidence")


def content_words(value: str) -> list[str]:
    """Lowercased word-character runs that are not stop words."""
    return [word for word in _WORD.findall(value.lower()) if word not in STOP_WORDS]


def _from_case(case: Case) -> Temptation:
    own = Claim(case.evidence, case.claim_text, case.fields, case.claim_verb)
    return Temptation(case.kind, case.statement, "own", own, ())


def parse_untargeted(raw: str, _line: int) -> Temptation:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CaseError(f"invalid JSON ({exc.msg})") from exc
    if not isinstance(value, dict) or not (
        {"evidence", "claim", "statement", "kind"} <= set(value) <= _KEYS
    ):
        raise CaseError(
            "keys must be evidence, claim, statement, kind and optional fields, citation, others"
        )
    kind = value["kind"]
    if kind not in UNTARGETED_CLASSES:
        raise CaseError(f"kind must be one of {', '.join(UNTARGETED_CLASSES)}")
    raw_citation = value.get("citation", "own")
    citation = next((item for item in CITATIONS[kind] if item == raw_citation), None)
    if citation is None:
        raise CaseError(f"{kind} cases cite {' or '.join(sorted(CITATIONS[kind]))}")
    others = value.get("others", [])
    if not isinstance(others, list) or not all(
        isinstance(item, dict) and {"evidence", "claim"} <= set(item) <= _OTHER_KEYS
        for item in others
    ):
        raise CaseError("others must be a list of evidence, claim and optional fields")
    if kind == "cross_claim_borrowing" and not others:
        raise CaseError("cross_claim_borrowing cases name the claims they borrow from")
    statement = text(value["statement"], "statement")
    return Temptation(kind, statement, citation, claim(value), tuple(map(claim, others)))


def digest(files: dict[str, bytes]) -> str:
    """SHA-256 over each file's relative path, NUL, byte length, NUL and bytes, sorted by path."""
    hasher = hashlib.sha256()
    for name in sorted(files):
        data = files[name]
        hasher.update(f"{name}\0{len(data)}\0".encode())
        hasher.update(data)
    return hasher.hexdigest()


def _block_per_mille(blocked: int, total: int) -> int:
    return blocked * 1000 // total


def _false_block_per_mille(blocked: int, total: int) -> int:
    return -(-blocked * 1000 // total)


class ClassRow(TypedDict):
    baseline_block_per_mille: int
    baseline_blocked: int
    cases: int
    checker_block_per_mille: int
    checker_blocked: int
    targeted: bool


class HonestRow(TypedDict):
    baseline_blocked: int
    baseline_false_block_per_mille: int
    cases: int
    checker_blocked: int
    checker_false_block_per_mille: int


class Dataset(TypedDict):
    files: list[str]
    sha256: str


class Report(TypedDict):
    baseline: str
    checker: str
    classes: dict[str, ClassRow]
    dataset: Dataset
    honest: HonestRow


def _honest(items: list[Temptation]) -> HonestRow:
    checker = sum(item.checker_blocks() for item in items)
    baseline = sum(item.baseline_blocks() for item in items)
    return {
        "baseline_blocked": baseline,
        "baseline_false_block_per_mille": _false_block_per_mille(baseline, len(items)),
        "cases": len(items),
        "checker_blocked": checker,
        "checker_false_block_per_mille": _false_block_per_mille(checker, len(items)),
    }


def _class(name: str, items: list[Temptation]) -> ClassRow:
    if not items:
        raise CaseError(f"the dataset has no {name} cases")
    checker = sum(item.checker_blocks() for item in items)
    baseline = sum(item.baseline_blocks() for item in items)
    return {
        "baseline_block_per_mille": _block_per_mille(baseline, len(items)),
        "baseline_blocked": baseline,
        "cases": len(items),
        "checker_block_per_mille": _block_per_mille(checker, len(items)),
        "checker_blocked": checker,
        "targeted": name in INFLATION_KINDS,
    }


def run(directory: Path) -> Report:
    """The canonical report for the dataset in ``directory``."""
    files = {name: read_bounded(directory / name) for name in DATASET_FILES}
    honest = [
        _from_case(case)
        for case in parse_lines(
            "honest.jsonl", files["honest.jsonl"], lambda raw, line: parse_case(raw, line, True)
        )
    ]
    temptations = [
        _from_case(case)
        for case in parse_lines(
            "inflated.jsonl",
            files["inflated.jsonl"],
            lambda raw, line: parse_case(raw, line, False),
        )
    ]
    temptations.extend(parse_lines("untargeted.jsonl", files["untargeted.jsonl"], parse_untargeted))
    return {
        "baseline": BASELINE_VERSION,
        "checker": CHECKER_VERSION,
        "classes": {
            name: _class(name, [item for item in temptations if item.kind == name])
            for name in CLASSES
        },
        "dataset": {"files": list(DATASET_FILES), "sha256": digest(files)},
        "honest": _honest(honest),
    }


def canonical_json(report: Report) -> bytes:
    return (json.dumps(report, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode()


def _percent(per_mille: int) -> str:
    return f"{per_mille // 10}.{per_mille % 10}%"


def markdown(report: Report) -> str:
    """The report as a Markdown table, in class order with targeted classes first."""
    dataset = report["dataset"]
    honest = report["honest"]
    lines = [
        "## Fabrication benchmark",
        "",
        f"Dataset SHA-256 `{dataset['sha256']}` over {', '.join(dataset['files'])}; "
        f"checker `{report['checker']}`; baseline `{report['baseline']}`.",
        "",
        "| Set | Targeted by the inflation rules | Cases | Checker blocked | Baseline blocked |",
        "| --- | --- | ---: | ---: | ---: |",
        f"| honest (false blocks) | — | {honest['cases']} | {honest['checker_blocked']} "
        f"({_percent(honest['checker_false_block_per_mille'])}) | {honest['baseline_blocked']} "
        f"({_percent(honest['baseline_false_block_per_mille'])}) |",
    ]
    for name in CLASSES:
        row = report["classes"][name]
        lines.append(
            f"| {name} | {'yes' if row['targeted'] else 'no'} | {row['cases']} | "
            f"{row['checker_blocked']} ({_percent(row['checker_block_per_mille'])}) | "
            f"{row['baseline_blocked']} ({_percent(row['baseline_block_per_mille'])}) |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="bonafides benchmark")
    actions = parser.add_subparsers(dest="action", required=True)
    runner = actions.add_parser(
        "run",
        help="report checker and lexical-baseline block rates per temptation class on a dataset",
    )
    runner.add_argument(
        "directory",
        type=Path,
        help="directory holding honest.jsonl, inflated.jsonl and untargeted.jsonl",
    )
    runner.add_argument("--output", type=Path, help="write the canonical JSON report here")
    arguments = parser.parse_args(argv)
    try:
        report = run(arguments.directory)
    except (OSError, UnicodeDecodeError, CaseError) as exc:
        parser.error(f"cannot read the benchmark dataset in {arguments.directory}: {exc}")
    if arguments.output is not None:
        try:
            arguments.output.write_bytes(canonical_json(report))
        except OSError as exc:
            parser.error(f"cannot write {arguments.output}: {exc}")
    print(markdown(report), end="")
    return 0
