# SPDX-License-Identifier: MIT OR Apache-2.0
"""`bonafides claims inflation-report`: committed case sets, CLI output and input limits."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from bonafides.claim_coverage import main as claims_main
from bonafides.claims import _normalise_grounding, contains_statement
from bonafides.inflation_report import (
    INFLATION_KINDS,
    MAX_FILE_BYTES,
    MAX_LINE_BYTES,
    load,
    report,
)

ROOT = Path(__file__).parent
SETS = ROOT / "fixtures" / "inflation"


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


def test_committed_honest_set_is_claim_cuts_with_at_most_half_identical() -> None:
    honest = load(SETS / "honest.jsonl", honest=True)
    cuts = [case for case in honest if not _same(case.statement, case.claim_text)]
    assert len(cuts) >= 100
    assert len(cuts) * 2 >= len(honest)
    printable = [case for case in honest if case.fields]
    assert printable
    for case in honest:
        assert case in printable or contains_statement(case.statement, case.claim_text), case


def test_committed_inflated_set_cuts_the_claim_only_to_promote_its_verb() -> None:
    for case in load(SETS / "inflated.jsonl", honest=False):
        in_claim = contains_statement(case.statement, case.claim_text)
        assert in_claim == (case.kind == "stronger_verb"), case


def test_inflation_report_committed_sets_within_limit_and_every_kind_blocked() -> None:
    result = report(
        load(SETS / "honest.jsonl", honest=True), load(SETS / "inflated.jsonl", honest=False)
    )
    assert result.false_block_rate <= 0.05
    assert result.misattributed == []
    assert all(hits == total for hits, total in result.inflated.values())


def test_claims_cli_inflation_report_prints_rates(capsys: pytest.CaptureFixture[str]) -> None:
    assert claims_main(["inflation-report", str(SETS)]) == 0
    output = capsys.readouterr().out.splitlines()
    assert output[0].startswith("Honest: ") and "false-block" in output[0]
    for kind in INFLATION_KINDS:
        assert any(line.startswith(f"Inflated {kind}: ") for line in output)
    assert output[-1] == "False-block rate within the 5% limit"


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
