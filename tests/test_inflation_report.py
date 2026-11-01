# SPDX-License-Identifier: MIT OR Apache-2.0
"""`bonafides claims inflation-report`: committed case sets, CLI output and input limits."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from bonafides.claim_coverage import main as claims_main
from bonafides.claims import OWNERSHIP_VERBS, _normalise_grounding, contains_statement
from bonafides.inflation_report import (
    EXPECTED_CODES,
    INFLATION_KINDS,
    MAX_FILE_BYTES,
    MAX_LINE_BYTES,
    load,
    report,
)

ROOT = Path(__file__).parent
SETS = ROOT / "fixtures" / "inflation"
ORIGINAL_FALSE_BLOCKS = [
    (875, ["statement_cut_inside_claim"]),
    (1131, ["statement_cut_inside_claim"]),
]
# Deliberate endpoint measurement cases remain kind=null: none promotes a verb.
ENDPOINT_FALSE_BLOCKS = [(line, ["statement_cut_inside_claim"]) for line in range(1170, 1186)]
INTENTIONAL_FALSE_BLOCKS = [*ORIGINAL_FALSE_BLOCKS, *ENDPOINT_FALSE_BLOCKS]


def _same(statement: str, claim_text: str) -> bool:
    return _normalise_grounding(statement).rstrip(".;") == _normalise_grounding(claim_text).rstrip(
        ".;"
    )


def test_committed_sets_are_contained_in_their_evidence() -> None:
    honest = load(SETS / "honest.jsonl", honest=True)
    inflated = load(SETS / "inflated.jsonl", honest=False)
    for kind in INFLATION_KINDS:
        assert sum(case.kind == kind for case in inflated) >= 10
    # Every statement is wording of its claim's own evidence; a printable text's field lines
    # are reviewed structured fields, so only its claim text is evidence wording.
    for case in [*honest, *inflated]:
        wording = case.claim_text if case.fields else case.statement
        assert contains_statement(wording, case.evidence), case


def test_committed_honest_set_preserves_cut_shapes_with_at_most_half_identical() -> None:
    honest = load(SETS / "honest.jsonl", honest=True)
    assert len(honest) == 1185
    identical = [case for case in honest if _same(case.statement, case.claim_text)]
    printable = [case for case in honest if case.fields]
    prefixes = [
        case
        for case in honest
        if case not in identical
        and case not in printable
        and _normalise_grounding(case.claim_text).startswith(_normalise_grounding(case.statement))
    ]
    assert len(identical) == 314
    assert len(identical) * 2 <= len(honest)
    assert len(printable) == 6
    assert len(prefixes) == 294
    blocked_lines = {line for line, _ in ORIGINAL_FALSE_BLOCKS}
    postverb = [
        case
        for case in honest
        if case not in identical
        and case not in printable
        and case not in prefixes
        and case.line not in blocked_lines
    ]
    assert len(postverb) == 569
    vocabulary = {verb.replace("_", " ") for verb in OWNERSHIP_VERBS}
    full_remainders = 0
    for case in postverb:
        before, separator, after = _normalise_grounding(case.claim_text).partition(
            _normalise_grounding(case.statement)
        )
        assert separator and before.strip() in vocabulary, case
        full_remainders += not after
    assert full_remainders == 297
    assert len(postverb) - full_remainders == 272
    assert [(case.line, case.statement) for case in honest if case.line in blocked_lines] == [
        (875, "the billing migration"),
        (1131, "automated tests for the API"),
    ]
    for case in honest:
        assert case in printable or contains_statement(case.statement, case.claim_text), case


def test_committed_inflated_set_cuts_the_claim_only_to_promote_its_verb() -> None:
    inflated = load(SETS / "inflated.jsonl", honest=False)
    assert {kind: sum(case.kind == kind for case in inflated) for kind in INFLATION_KINDS} == {
        "stronger_verb": 15,
        "longer_tenure": 16,
        "larger_metric": 74,
        "wider_scope": 18,
    }
    assert EXPECTED_CODES["stronger_verb"] == "statement_cut_inside_claim"
    for case in inflated:
        in_claim = contains_statement(case.statement, case.claim_text)
        assert in_claim == (case.kind == "stronger_verb"), case


def test_inflation_report_committed_sets_within_limit_and_every_kind_blocked() -> None:
    result = report(
        load(SETS / "honest.jsonl", honest=True), load(SETS / "inflated.jsonl", honest=False)
    )
    assert result.false_block_rate <= 0.05
    assert result.false_blocks == INTENTIONAL_FALSE_BLOCKS
    assert result.misattributed == []
    assert all(hits == total for hits, total in result.inflated.values())


def test_claims_cli_inflation_report_prints_rates(capsys: pytest.CaptureFixture[str]) -> None:
    assert claims_main(["inflation-report", str(SETS)]) == 0
    output = capsys.readouterr().out.splitlines()
    assert output[0] == "Honest: 18 of 1185 blocked (false-block 1.6%)"
    assert output[1:19] == [
        f"honest line {line}: statement_cut_inside_claim" for line, _ in INTENTIONAL_FALSE_BLOCKS
    ]
    for kind in INFLATION_KINDS:
        assert any(line.startswith(f"Inflated {kind}: ") for line in output)
    assert output[-1] == "False-block rate within the 5% limit"


@pytest.mark.parametrize(
    ("claim", "evidence", "statement", "expected"),
    [
        (
            "Designed and implemented a platform for merchants",
            "Designed and implemented a platform for merchants.",
            "Designed and implemented a platform",
            [],
        ),
        (
            "Reduced checkout latency by 35% across 4 regions",
            "Reduced checkout latency by 35% across 4 regions.",
            "  CHECKOUT\tlatency  ",
            [],
        ),
        (
            {"schema_version": 1, "verb": "contributed_to", "subject": "the open-source scheduler"},
            "Contributed to the open-source scheduler.",
            "the open-source scheduler",
            [],
        ),
        (
            "Assisted the team that led the billing migration",
            "Assisted the team that led the billing migration.",
            "the billing migration",
            ["statement_cut_inside_claim"],
        ),
        (
            "Wrote automated tests for the API",
            "Wrote automated tests for the API.",
            "automated tests for the API",
            ["statement_cut_inside_claim"],
        ),
        (
            "Built custom-built dashboards for finance",
            "Built custom-built dashboards for finance.",
            "Built custom",
            ["statement_cut_inside_claim"],
        ),
        (
            "Custom-built dashboards for finance",
            "Custom-built dashboards for finance.",
            "dashboards for finance",
            ["statement_cut_inside_claim"],
        ),
    ],
    ids=[
        "multi-verb-prefix",
        "normalized-postverb-prefix",
        "structured-pinned-verb-phrase",
        "mid-claim-subject",
        "vocabulary-remainder-guard",
        "cut-inside-hyphen",
        "hyphen-joined-word-is-not-leading-verb",
    ],
)
def test_inflation_report_claim_cut_shapes(
    tmp_path: Path,
    claim: str | dict[str, object],
    evidence: str,
    statement: str,
    expected: list[str],
) -> None:
    _write_sets(
        tmp_path,
        [{"claim": claim, "evidence": evidence, "statement": statement, "kind": None}],
    )
    result = report(load(tmp_path / "honest.jsonl", honest=True), [])
    assert result.false_blocks == ([(1, expected)] if expected else [])


def test_inflation_report_printable_text_case_passes_and_altered_fields_block(
    tmp_path: Path,
) -> None:
    case = {
        "evidence": "Maintained Linux batch runbooks.",
        "claim": {"schema_version": 1, "verb": "maintained", "subject": "Linux batch runbooks"},
        "fields": {"role": "Operations Analyst", "organization": "Juniper Relay"},
        "kind": None,
    }
    lines = [
        {**case, "statement": "Maintained Linux batch runbooks\nOperations Analyst\nJuniper Relay"},
        {**case, "statement": "Maintained Linux batch runbooks\nOperations Director"},
    ]
    path = tmp_path / "honest.jsonl"
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    result = report(load(path, honest=True), [])
    assert result.false_blocks == [(2, ["statement_not_in_claim"])]


@pytest.mark.parametrize(
    "fields",
    [{"title": "Analyst"}, {"role": ""}, {"role": 3}, ["role"]],
    ids=["unknown-field", "empty-value", "non-text-value", "not-an-object"],
)
def test_inflation_report_invalid_fields_are_case_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], fields: object
) -> None:
    case = {
        "evidence": "Led migration.",
        "claim": "Led migration",
        "statement": "Led migration",
        "kind": None,
        "fields": fields,
    }
    _write_sets(tmp_path, [case])
    with pytest.raises(SystemExit) as refused:
        claims_main(["inflation-report", str(tmp_path)])
    assert refused.value.code == 2
    assert "honest.jsonl:1:" in capsys.readouterr().err


def _write_sets(directory: Path, honest: Sequence[Mapping[str, object]]) -> None:
    inflated = {
        "evidence": "Assisted the team that led the billing migration.",
        "claim": "Assisted the team that led the billing migration",
        "statement": "led the billing migration",
        "kind": "stronger_verb",
    }
    (directory / "honest.jsonl").write_text(
        "".join(json.dumps(case) + "\n" for case in honest), encoding="utf-8"
    )
    (directory / "inflated.jsonl").write_text(json.dumps(inflated) + "\n", encoding="utf-8")


def test_claims_cli_inflation_report_false_block_above_limit_exits_non_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    passing = {
        "evidence": "Led the billing migration.",
        "claim": "Led the billing migration",
        "statement": "Led the billing migration",
        "kind": None,
    }
    blocked = {
        "evidence": "Led the billing migration (2021–2023), team 2019–2023.",
        "claim": "Led the billing migration (2021–2023)",
        "statement": "team 2019–2023",
        "kind": None,
    }
    _write_sets(tmp_path, [passing] * 9 + [blocked])
    assert claims_main(["inflation-report", str(tmp_path)]) == 1
    output = capsys.readouterr().out.splitlines()
    assert output[0] == "Honest: 1 of 10 blocked (false-block 10.0%)"
    assert output[1] == "honest line 10: statement_not_in_claim"
    assert output[-1] == "False-block rate exceeds the 5% limit"


def test_claims_cli_inflation_report_uncontained_claim_is_rejected(tmp_path: Path) -> None:
    _write_sets(
        tmp_path,
        [
            {
                "evidence": "Tuned PostgreSQL queries.",
                "claim": "SQL",
                "statement": "Tuned PostgreSQL queries",
                "kind": None,
            }
        ],
    )
    with pytest.raises(SystemExit) as refused:
        claims_main(["inflation-report", str(tmp_path)])
    assert refused.value.code == 2


@pytest.mark.parametrize("filename", ["honest.jsonl", "inflated.jsonl"])
@pytest.mark.parametrize(
    "raw",
    [b"not JSON", b"[" * 10_000 + b"0" + b"]" * 10_000, b"\xff"],
    ids=["malformed-json", "deeply-nested-json", "invalid-utf8"],
)
def test_claims_cli_inflation_report_bad_line_exits_with_location(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], filename: str, raw: bytes
) -> None:
    _write_sets(
        tmp_path,
        [
            {
                "evidence": "Led migration.",
                "claim": "Led migration",
                "statement": "Led migration",
                "kind": None,
            }
        ],
    )
    (tmp_path / filename).write_bytes(b"\n" + raw + b"\n")
    with pytest.raises(SystemExit) as refused:
        claims_main(["inflation-report", str(tmp_path)])
    assert refused.value.code == 2
    output = capsys.readouterr()
    assert f"{filename}:2:" in output.err
    assert "Traceback" not in output.err
    assert output.out == ""


@pytest.mark.parametrize("error", [ValueError("integer exceeds limit"), RecursionError("too deep")])
def test_claims_cli_inflation_report_parser_errors_are_case_errors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    _write_sets(tmp_path, [])
    (tmp_path / "honest.jsonl").write_bytes(b"\n{}\n")

    def reject_json(raw: str) -> object:
        raise error

    monkeypatch.setattr(json, "loads", reject_json)
    with pytest.raises(SystemExit) as refused:
        claims_main(["inflation-report", str(tmp_path)])
    assert refused.value.code == 2
    output = capsys.readouterr()
    assert f"honest.jsonl:2: {error}" in output.err
    assert "Traceback" not in output.err
    assert output.out == ""


@pytest.mark.parametrize("filename", ["honest.jsonl", "inflated.jsonl"])
def test_claims_cli_inflation_report_oversized_file_is_rejected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], filename: str
) -> None:
    _write_sets(
        tmp_path,
        [
            {
                "evidence": "Led migration.",
                "claim": "Led migration",
                "statement": "Led migration",
                "kind": None,
            }
        ],
    )
    (tmp_path / filename).write_bytes(b" " * (MAX_FILE_BYTES + 1))
    with pytest.raises(SystemExit) as refused:
        claims_main(["inflation-report", str(tmp_path)])
    assert refused.value.code == 2
    output = capsys.readouterr()
    assert f"{filename}:1: file exceeds {MAX_FILE_BYTES} bytes" in output.err
    assert "Traceback" not in output.err
    assert output.out == ""


@pytest.mark.parametrize("filename", ["honest.jsonl", "inflated.jsonl"])
@pytest.mark.parametrize(
    "raw",
    [b" " * (MAX_LINE_BYTES + 1), ("\u00e9" * (MAX_LINE_BYTES // 2) + "\n").encode()],
    ids=["ascii", "multibyte-utf8"],
)
def test_claims_cli_inflation_report_oversized_line_is_rejected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], filename: str, raw: bytes
) -> None:
    _write_sets(
        tmp_path,
        [
            {
                "evidence": "Led migration.",
                "claim": "Led migration",
                "statement": "Led migration",
                "kind": None,
            }
        ],
    )
    (tmp_path / filename).write_bytes(b"\n" + raw)
    with pytest.raises(SystemExit) as refused:
        claims_main(["inflation-report", str(tmp_path)])
    assert refused.value.code == 2
    output = capsys.readouterr()
    assert f"{filename}:2: line exceeds {MAX_LINE_BYTES} bytes" in output.err
    assert "Traceback" not in output.err
    assert output.out == ""


@pytest.mark.parametrize("ending", [b"", b"\n", b"\r\n"])
def test_inflation_report_line_at_byte_limit_is_accepted(tmp_path: Path, ending: bytes) -> None:
    raw = json.dumps(
        {
            "evidence": "Led migration.",
            "claim": "Led migration",
            "statement": "Led migration",
            "kind": None,
        }
    ).encode()
    path = tmp_path / "honest.jsonl"
    path.write_bytes(b"\n" + raw + b" " * (MAX_LINE_BYTES - len(raw) - len(ending)) + ending)
    cases = load(path, honest=True)
    assert len(cases) == 1
    assert cases[0].line == 2
    assert cases[0].claim_text == "Led migration"
