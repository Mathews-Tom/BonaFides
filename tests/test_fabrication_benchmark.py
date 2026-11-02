# SPDX-License-Identifier: MIT OR Apache-2.0
"""`bonafides benchmark run`: baseline, digest, determinism and the committed dataset."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from bonafides.cli import main as cli_main
from bonafides.fabrication_benchmark import (
    CLASSES,
    DATASET_FILES,
    UNTARGETED_CLASSES,
    Temptation,
    content_words,
    digest,
    parse_untargeted,
    run,
)
from bonafides.inflation_report import INFLATION_KINDS, parse_lines

DATASET = Path(__file__).parent / "fixtures" / "fabrication"
HONEST = {"evidence": "Led migration.", "claim": "Led migration", "statement": "Led migration"}


def _temptation(statement: str, evidence: str, citation: str = "own") -> Temptation:
    return parse_untargeted(
        json.dumps(
            {
                "claim": "Led the billing migration",
                "evidence": evidence,
                "kind": "invented_claim" if citation == "absent" else "posting_only_skill",
                "statement": statement,
                "citation": citation,
            }
        ),
        1,
    )


def _dataset(directory: Path, untargeted_rows: list[dict[str, object]]) -> Path:
    for name in ("honest.jsonl", "inflated.jsonl"):
        shutil.copyfile(DATASET / name, directory / name)
    (directory / "untargeted.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in untargeted_rows), encoding="utf-8"
    )
    return directory


def test_content_words_lowercases_and_drops_stop_words() -> None:
    assert content_words("Led THE migration of 40 services_to AWS, with $1.2M") == [
        "led",
        "migration",
        "40",
        "services",
        "aws",
        "1",
        "2m",
    ]


def test_baseline_passes_statement_whose_content_words_are_all_in_evidence() -> None:
    temptation = _temptation(
        "led the billing migration for the team",
        "Supported the team that led the billing migration.",
    )
    assert temptation.baseline_blocks() is False
    assert temptation.checker_blocks() is True


def test_baseline_blocks_statement_with_a_content_word_missing_from_evidence() -> None:
    temptation = _temptation("Led the payments migration", "Led the billing migration.")
    assert temptation.baseline_blocks() is True


def test_baseline_blocks_statement_citing_absent_claim_even_with_its_wording() -> None:
    temptation = _temptation(
        "Led the billing migration", "Led the billing migration.", citation="absent"
    )
    assert temptation.baseline_blocks() is True
    assert temptation.checker_blocks() is True


def test_digest_covers_each_byte_and_each_path() -> None:
    files = {"honest.jsonl": b"a\n", "untargeted.jsonl": b"b\n"}
    original = digest(files)
    assert digest(dict(reversed(files.items()))) == original
    assert digest({**files, "honest.jsonl": b"c\n"}) != original
    assert digest({"honest.jsonl": b"a\nb\n", "untargeted.jsonl": b""}) != original
    assert digest({"inflated.jsonl": b"a\n", "untargeted.jsonl": b"b\n"}) != original


def test_committed_dataset_reports_every_class_with_at_least_ten_cases() -> None:
    report = run(DATASET)
    assert list(report["dataset"]["files"]) == list(DATASET_FILES)
    assert set(report["classes"]) == set(CLASSES)
    assert all(row["cases"] >= 10 for row in report["classes"].values())
    assert report["honest"]["cases"] == 1175
    for name in INFLATION_KINDS:
        assert report["classes"][name]["targeted"] is True
        assert report["classes"][name]["checker_block_per_mille"] == 1000
    untargeted_names = [name for name, row in report["classes"].items() if not row["targeted"]]
    assert untargeted_names == list(UNTARGETED_CLASSES)
    # The accepted limitation: a dropped trailing qualifier at a boundary word passes.
    assert report["classes"]["dropped_qualifier"]["checker_blocked"] == 0


def test_benchmark_cli_twice_is_byte_identical(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    outputs: list[tuple[bytes, str]] = []
    for index in range(2):
        target = tmp_path / f"report-{index}.json"
        assert cli_main(["benchmark", "run", str(DATASET), "--output", str(target)]) == 0
        outputs.append((target.read_bytes(), capsys.readouterr().out))
    assert outputs[0] == outputs[1]
    data, markdown = outputs[0]
    report = json.loads(data)
    assert data == (json.dumps(report, sort_keys=True, indent=2) + "\n").encode()
    assert report["dataset"]["sha256"] in markdown
    assert "| dropped_qualifier | no | 12 | 0 (0.0%) |" in markdown
    assert "| honest (false blocks) | — | 1175 |" in markdown


def test_benchmark_cli_one_changed_dataset_byte_changes_the_digest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    original = tmp_path / "original"
    changed = tmp_path / "changed"
    shutil.copytree(DATASET, original)
    shutil.copytree(DATASET, changed)
    data = bytearray((changed / "honest.jsonl").read_bytes())
    index = data.index(b"checkout latency")
    data[index] = ord("C")
    (changed / "honest.jsonl").write_bytes(bytes(data))
    reports = []
    for directory in (original, changed):
        target = directory / "report.json"
        assert cli_main(["benchmark", "run", str(directory), "--output", str(target)]) == 0
        reports.append(json.loads(target.read_bytes()))
    capsys.readouterr()
    assert reports[0]["dataset"]["sha256"] != reports[1]["dataset"]["sha256"]
    assert reports[0]["classes"] == reports[1]["classes"]


def test_benchmark_digest_ignores_files_the_run_does_not_read(tmp_path: Path) -> None:
    shutil.copytree(DATASET, tmp_path / "copy")
    (tmp_path / "copy" / "README.md").write_text("changed\n", encoding="utf-8")
    assert run(tmp_path / "copy")["dataset"] == run(DATASET)["dataset"]


def test_cross_claim_borrowing_case_blocks_on_another_profile_claims_wording() -> None:
    rows = parse_lines(
        "untargeted.jsonl",
        json.dumps(
            {
                "claim": "Led the billing migration",
                "evidence": "Led the billing migration. Reduced costs by 20%.",
                "kind": "cross_claim_borrowing",
                "statement": "Led the billing migration and reduced costs by 20%",
                "others": [
                    {
                        "claim": "Reduced costs by 20%",
                        "evidence": "Led the billing migration. Reduced costs by 20%.",
                    }
                ],
            }
        ).encode(),
        parse_untargeted,
    )
    assert rows[0].checker_blocks() is True
    assert rows[0].baseline_blocks() is False


@pytest.mark.parametrize("citation", ["uncited", "connective"])
def test_uncited_statement_blocks_even_when_it_repeats_the_claim(citation: str) -> None:
    case = parse_untargeted(
        json.dumps(
            {
                **HONEST,
                "kind": "uncited_statement",
                "citation": citation,
            }
        ),
        1,
    )
    assert case.checker_blocks() is True
    assert case.baseline_blocks() is True


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ({**HONEST, "kind": "stronger_verb"}, "kind must be one of invented_claim"),
        ({**HONEST, "kind": "invented_claim"}, "invented_claim cases cite absent"),
        (
            {**HONEST, "kind": "posting_only_skill", "citation": "absent"},
            "posting_only_skill cases cite own",
        ),
        ({**HONEST, "kind": "cross_claim_borrowing"}, "name the claims they borrow from"),
        (
            {**HONEST, "kind": "dropped_qualifier", "citation": ["own"]},
            "dropped_qualifier cases cite own",
        ),
        (
            {
                **HONEST,
                "kind": "cross_claim_borrowing",
                "others": [{"claim": "Cut costs", "evidence": "Led migration."}],
            },
            "the claim is not contained in its evidence",
        ),
        (
            {**HONEST, "kind": "dropped_qualifier", "others": [{"claim": "Cut costs"}]},
            "others must be a list of evidence, claim and optional fields",
        ),
        ({**HONEST, "kind": "dropped_qualifier", "extra": 1}, "keys must be evidence"),
    ],
)
def test_benchmark_cli_invalid_untargeted_case_exits_two_with_location(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    row: dict[str, object],
    message: str,
) -> None:
    _dataset(tmp_path, [row])
    with pytest.raises(SystemExit) as refused:
        cli_main(["benchmark", "run", str(tmp_path)])
    assert refused.value.code == 2
    output = capsys.readouterr()
    assert "untargeted.jsonl:1: " in output.err
    assert message in output.err
    assert "Traceback" not in output.err
    assert output.out == ""


def test_benchmark_cli_class_without_cases_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _dataset(tmp_path, [{**HONEST, "kind": "dropped_qualifier"}])
    with pytest.raises(SystemExit) as refused:
        cli_main(["benchmark", "run", str(tmp_path)])
    assert refused.value.code == 2
    assert "the dataset has no invented_claim cases" in capsys.readouterr().err


def test_benchmark_cli_missing_dataset_file_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    shutil.copyfile(DATASET / "honest.jsonl", tmp_path / "honest.jsonl")
    with pytest.raises(SystemExit) as refused:
        cli_main(["benchmark", "run", str(tmp_path)])
    assert refused.value.code == 2
    output = capsys.readouterr()
    assert "cannot read the benchmark dataset" in output.err
    assert output.out == ""
