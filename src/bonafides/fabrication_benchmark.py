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


class ExportedCase(TypedDict):
    case_id: str
    category: str
    case: dict[str, object]


class CaseExport(TypedDict):
    schema_version: int
    dataset: Dataset
    cases: list[ExportedCase]


@dataclass(frozen=True)
class SnapshotCase:
    case_id: str
    category: str
    context: dict[str, object]
    temptation: Temptation


@dataclass(frozen=True)
class Snapshot:
    """One bounded read and validation of the corpus, shared by all benchmark commands."""

    dataset: Dataset
    cases: tuple[SnapshotCase, ...]


def load_snapshot(directory: Path) -> Snapshot:
    files = {name: read_bounded(directory / name) for name in DATASET_FILES}
    cases: list[SnapshotCase] = []
    for name in DATASET_FILES:

        def parse(raw: str, line: int, filename: str = name) -> SnapshotCase:
            item = (
                parse_untargeted(raw, line)
                if filename == "untargeted.jsonl"
                else _from_case(parse_case(raw, line, filename == "honest.jsonl"))
            )
            # Retain the original JSON object, including structured claims and other claims,
            # rather than reconstructing a lossy model prompt from the checker projection.
            return SnapshotCase(f"{filename}:{line}", item.kind or "honest", json.loads(raw), item)

        cases.extend(parse_lines(name, files[name], parse))
    categories = {case.category for case in cases}
    for category in CLASSES:
        if category not in categories:
            raise CaseError(f"the dataset has no {category} cases")
    return Snapshot({"files": list(DATASET_FILES), "sha256": digest(files)}, tuple(cases))


def export_cases(snapshot: Snapshot) -> CaseExport:
    """Full validated context for off-tool recordings; IDs use physical JSONL lines."""
    return {
        "schema_version": 1,
        "dataset": snapshot.dataset,
        "cases": [
            {"case_id": case.case_id, "category": case.category, "case": case.context}
            for case in snapshot.cases
        ],
    }


def run(directory: Path) -> Report:
    """The canonical report for the dataset in ``directory``."""
    snapshot = load_snapshot(directory)
    honest = [case.temptation for case in snapshot.cases if case.category == "honest"]
    temptations = [case.temptation for case in snapshot.cases if case.category != "honest"]
    return {
        "baseline": BASELINE_VERSION,
        "checker": CHECKER_VERSION,
        "classes": {
            name: _class(name, [item for item in temptations if item.kind == name])
            for name in CLASSES
        },
        "dataset": snapshot.dataset,
        "honest": _honest(honest),
    }


def canonical_json(report: object) -> bytes:
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


class RejectionRow(TypedDict):
    cases: int
    rejected: int
    reject_per_mille: int


class Disagreements(TypedDict):
    accept_checker_reject: int
    reject_checker_accept: int


class ComparatorRow(TypedDict):
    kind: str
    name: str
    version: str | None
    capability_sha256: str | None
    provenance: str
    source: str
    honest: RejectionRow
    classes: dict[str, RejectionRow]
    checker_disagreements: Disagreements


class Leaderboard(TypedDict):
    schema_version: int
    dataset: Dataset
    authority: str
    recorded_results: str
    capability_hashes: str
    comparators: list[ComparatorRow]


@dataclass(frozen=True)
class RecordedResult:
    kind: str
    name: str
    capability_sha256: str
    provenance: str
    rejected: frozenset[str]


_RESULT_KEYS = {
    "schema_version",
    "dataset_sha256",
    "kind",
    "name",
    "capability_sha256",
    "provenance",
    "predictions",
}
_SHA256 = re.compile(r"[0-9a-f]{64}")
_DISPLAY_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._():/+-]{0,79}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise CaseError(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def _invalid_constant(value: str) -> object:
    raise CaseError(f"invalid JSON constant {value}")


def read_recorded_result(path: Path, snapshot: Snapshot) -> RecordedResult:
    """Read a bounded, untrusted recording, never a qualification receipt."""
    try:
        value = json.loads(
            read_bounded(path).decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        )
        if not isinstance(value, dict) or set(value) != _RESULT_KEYS:
            raise CaseError("recorded result must have exactly the documented result keys")
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise CaseError("schema_version must be integer 1")
        if value["dataset_sha256"] != snapshot.dataset["sha256"]:
            raise CaseError("dataset_sha256 does not match the exact corpus bytes")
        kind = value["kind"]
        if not isinstance(kind, str) or kind not in ("advisory_judge", "local_model"):
            raise CaseError("kind must be advisory_judge or local_model")
        name = value["name"]
        if not isinstance(name, str) or _DISPLAY_NAME.fullmatch(name) is None:
            raise CaseError(
                "name must be 1–80 safe ASCII display characters, starting alphanumeric"
            )
        capability = value["capability_sha256"]
        if not isinstance(capability, str) or _SHA256.fullmatch(capability) is None:
            raise CaseError("capability_sha256 must be 64 lowercase hexadecimal characters")
        provenance = value["provenance"]
        if not isinstance(provenance, str) or provenance not in ("synthetic", "owner_recorded"):
            raise CaseError("provenance must be synthetic or owner_recorded")
        predictions = value["predictions"]
        if not isinstance(predictions, list):
            raise CaseError("predictions must be a list")
        expected = {case.case_id for case in snapshot.cases}
        seen: set[str] = set()
        rejected: set[str] = set()
        for prediction in predictions:
            if not isinstance(prediction, dict) or set(prediction) != {"case_id", "verdict"}:
                raise CaseError("each prediction must have exactly case_id and verdict")
            case_id = prediction["case_id"]
            if not isinstance(case_id, str) or case_id not in expected:
                raise CaseError("prediction has a foreign or invalid case_id")
            if case_id in seen:
                raise CaseError(f"duplicate prediction for {case_id}")
            verdict = prediction["verdict"]
            if not isinstance(verdict, str) or verdict not in ("accept", "reject"):
                raise CaseError("verdict must be accept or reject")
            seen.add(case_id)
            if verdict == "reject":
                rejected.add(case_id)
        if seen != expected:
            raise CaseError(f"predictions are missing {len(expected - seen)} cases")
        return RecordedResult(kind, name, capability, provenance, frozenset(rejected))
    except (ValueError, RecursionError) as exc:
        raise CaseError(f"{path.name}: {exc}") from exc


def _rejections(category: str, ids: list[str], rejected: frozenset[str]) -> RejectionRow:
    count = sum(case_id in rejected for case_id in ids)
    rate = _false_block_per_mille if category == "honest" else _block_per_mille
    return {"cases": len(ids), "rejected": count, "reject_per_mille": rate(count, len(ids))}


def leaderboard(snapshot: Snapshot, result_paths: list[Path]) -> Leaderboard:
    """A same-corpus comparison only; supplied identities/provenance are unverified claims."""
    if not result_paths:
        raise CaseError("at least one recorded result is required")
    results = [read_recorded_result(path, snapshot) for path in result_paths]
    identities = [(result.kind, result.capability_sha256) for result in results]
    if len(set(identities)) != len(identities):
        raise CaseError("duplicate comparator identity (kind, capability_sha256)")
    results.sort(key=lambda result: (result.kind != "advisory_judge", result.capability_sha256))
    checker = frozenset(case.case_id for case in snapshot.cases if case.temptation.checker_blocks())
    baseline = frozenset(
        case.case_id for case in snapshot.cases if case.temptation.baseline_blocks()
    )
    groups = {
        category: [case.case_id for case in snapshot.cases if case.category == category]
        for category in ("honest", *CLASSES)
    }
    rows: list[ComparatorRow] = []

    def append(
        kind: str,
        name: str,
        version: str | None,
        capability: str | None,
        provenance: str,
        source: str,
        rejected: frozenset[str],
    ) -> None:
        rows.append(
            {
                "kind": kind,
                "name": name,
                "version": version,
                "capability_sha256": capability,
                "provenance": provenance,
                "source": source,
                "honest": _rejections("honest", groups["honest"], rejected),
                "classes": {
                    category: _rejections(category, groups[category], rejected)
                    for category in CLASSES
                },
                "checker_disagreements": {
                    "accept_checker_reject": len(checker - rejected),
                    "reject_checker_accept": len(rejected - checker),
                },
            }
        )

    append("checker", "Checker", CHECKER_VERSION, None, "builtin", "builtin", checker)
    append(
        "lexical_baseline",
        "Lexical baseline",
        BASELINE_VERSION,
        None,
        "builtin",
        "builtin",
        baseline,
    )
    for result in results:
        append(
            result.kind,
            result.name,
            None,
            result.capability_sha256,
            result.provenance,
            "owner_supplied_unverified",
            result.rejected,
        )
    return {
        "schema_version": 1,
        "dataset": snapshot.dataset,
        "authority": "comparison_only",
        "recorded_results": "owner_supplied_unverified",
        "capability_hashes": "claimed_not_verified_or_qualification",
        "comparators": rows,
    }


def leaderboard_markdown(report: Leaderboard) -> str:
    lines = [
        "## Offline fabrication comparison",
        "",
        f"Dataset SHA-256 `{report['dataset']['sha256']}`.",
        "",
        "Comparison only: recorded results are owner-supplied and unverified. Synthetic results "
        "are not real-model measurements. Capability hashes are claimed identities, not verified "
        "identities or qualification. Advisory judges never change any gate or checker result.",
        "Honest rejections are false rejections (rates rounded up); other rejection rates round "
        "down. There is no weighted ranking. The checker still misses dropped qualifiers.",
        "",
    ]
    for row in report["comparators"]:
        lines.extend(
            [
                f"### {row['kind']}: {row['name']}",
                "",
                f"Source: {row['source']}; provenance: {row['provenance']}.",
                (
                    f"Claimed capability SHA-256: `{row['capability_sha256']}`."
                    if row["capability_sha256"] is not None
                    else f"Version: `{row['version']}`."
                ),
                "",
                "| Set | Cases | Rejected | Rate |",
                "| --- | ---: | ---: | ---: |",
            ]
        )
        for category in ("honest", *CLASSES):
            metric = row["honest"] if category == "honest" else row["classes"][category]
            label = "honest (false rejections)" if category == "honest" else category
            lines.append(
                f"| {label} | {metric['cases']} | {metric['rejected']} | "
                f"{_percent(metric['reject_per_mille'])} |"
            )
        disagreement = row["checker_disagreements"]
        lines.extend(
            [
                "",
                "Checker disagreements: "
                f"{disagreement['accept_checker_reject']} accepted where checker rejected; "
                f"{disagreement['reject_checker_accept']} rejected where checker accepted.",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="bonafides benchmark")
    actions = parser.add_subparsers(dest="action", required=True)
    runner = actions.add_parser(
        "run",
        help="report checker and lexical-baseline block rates per temptation class on a dataset",
    )
    exporter = actions.add_parser(
        "cases", help="export full validated case context as canonical JSON"
    )
    comparison = actions.add_parser(
        "leaderboard", help="compare owner-supplied unverified recordings on the exact corpus"
    )
    comparison.add_argument("--result", type=Path, action="append", required=True)
    for action in (runner, exporter, comparison):
        action.add_argument(
            "directory",
            type=Path,
            help="directory holding honest.jsonl, inflated.jsonl and untargeted.jsonl",
        )
        action.add_argument("--output", type=Path, help="write the canonical JSON report here")
    arguments = parser.parse_args(argv)
    payload: object
    try:
        if arguments.action == "run":
            report = run(arguments.directory)
            payload = report
            rendered = markdown(report)
        elif arguments.action == "cases":
            payload = export_cases(load_snapshot(arguments.directory))
            rendered = canonical_json(payload).decode("ascii")
        else:
            comparison_report = leaderboard(load_snapshot(arguments.directory), arguments.result)
            payload = comparison_report
            rendered = leaderboard_markdown(comparison_report)
        data = canonical_json(payload)
    except (OSError, UnicodeDecodeError, CaseError) as exc:
        subject = "benchmark dataset" if arguments.action == "run" else "benchmark inputs"
        parser.error(f"cannot read the {subject} in {arguments.directory}: {exc}")
    if arguments.output is not None:
        try:
            arguments.output.write_bytes(data)
        except OSError as exc:
            parser.error(f"cannot write {arguments.output}: {exc}")
    print(rendered, end="")
    return 0
