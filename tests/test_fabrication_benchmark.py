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
    CaseExport,
    Temptation,
    content_words,
    digest,
    export_cases,
    leaderboard,
    load_snapshot,
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


def _comparison_dataset(directory: Path) -> Path:
    rows: dict[str, list[dict[str, object]]] = {
        "honest.jsonl": [{**HONEST, "kind": None} for _ in range(3)],
        "inflated.jsonl": [{**HONEST, "kind": kind} for kind in INFLATION_KINDS for _ in range(3)],
        "untargeted.jsonl": [
            {
                **HONEST,
                "kind": kind,
                "citation": (
                    "absent"
                    if kind == "invented_claim"
                    else "uncited"
                    if kind == "uncited_statement"
                    else "own"
                ),
                "fields": {},
                "others": [{"evidence": "Built service.", "claim": "Built service", "fields": {}}],
            }
            for kind in UNTARGETED_CLASSES
            for _ in range(3)
        ],
    }
    for name, cases in rows.items():
        (directory / name).write_text(
            "".join(json.dumps(case) + "\n" for case in cases), encoding="utf-8"
        )
    return directory


def _recording(
    exported: CaseExport,
    *,
    kind: str = "local_model",
    name: str = "qwen2.5:7b-instruct",
    capability: str = "a" * 64,
    provenance: str = "synthetic",
    rejected: frozenset[str] = frozenset(),
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "dataset_sha256": exported["dataset"]["sha256"],
        "kind": kind,
        "name": name,
        "capability_sha256": capability,
        "provenance": provenance,
        "predictions": [
            {
                "case_id": case["case_id"],
                "verdict": "reject" if case["case_id"] in rejected else "accept",
            }
            for case in exported["cases"]
        ],
    }


def test_case_export_preserves_context_physical_lines_and_digest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _comparison_dataset(tmp_path)
    honest = tmp_path / "honest.jsonl"
    honest.write_bytes(b"\n" + honest.read_bytes().replace(b"\n", b"\n\n"))
    outputs = []
    for index in range(2):
        target = tmp_path / f"cases-{index}.json"
        assert cli_main(["benchmark", "cases", str(tmp_path), "--output", str(target)]) == 0
        stdout = capsys.readouterr().out
        assert target.read_bytes() == stdout.encode()
        outputs.append(stdout)
    assert outputs[0] == outputs[1]
    exported = json.loads(outputs[0])
    assert exported["schema_version"] == 1
    assert exported["dataset"] == run(tmp_path)["dataset"]
    assert [case["case_id"] for case in exported["cases"][:3]] == [
        "honest.jsonl:2",
        "honest.jsonl:4",
        "honest.jsonl:6",
    ]
    originals = {
        f"{name}:{line}": json.loads(raw)
        for name in DATASET_FILES
        for line, raw in enumerate((tmp_path / name).read_text().splitlines(), 1)
        if raw.strip()
    }
    assert {case["case_id"]: case["case"] for case in exported["cases"]} == originals
    assert {case["category"] for case in exported["cases"]} == {"honest", *CLASSES}


def test_leaderboard_cli_compares_each_recording_deterministically_without_authority(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _comparison_dataset(tmp_path)
    exported = export_cases(load_snapshot(tmp_path))
    original = run(tmp_path)
    # One false rejection out of three rounds up; one rejection in each class rounds down.
    rejected = frozenset(
        case["case_id"] for index, case in enumerate(exported["cases"]) if index % 3 == 0
    )
    recordings = [
        _recording(exported, kind="advisory_judge", name="Checker", capability="c" * 64),
        _recording(exported, capability="b" * 64, rejected=rejected),
        _recording(exported, capability="a" * 64, provenance="owner_recorded"),
    ]
    paths = []
    for index, recording in enumerate(recordings):
        path = tmp_path / f"recording-{index}.json"
        path.write_text(json.dumps(recording), encoding="utf-8")
        paths.append(path)
    outputs = []
    for iteration, ordered in enumerate((paths, list(reversed(paths)))):
        if iteration:
            for path in paths:
                reordered = json.loads(path.read_text())
                reordered["predictions"].reverse()
                path.write_text(json.dumps(reordered), encoding="utf-8")
        target = tmp_path / "comparison.json"
        arguments = ["benchmark", "leaderboard", str(tmp_path), "--output", str(target)]
        for path in ordered:
            arguments.extend(["--result", str(path)])
        assert cli_main(arguments) == 0
        outputs.append((target.read_bytes(), capsys.readouterr().out))
    assert outputs[0] == outputs[1]
    report = json.loads(outputs[0][0])
    assert report["dataset"] == original["dataset"]
    assert report["authority"] == "comparison_only"
    assert report["recorded_results"] == "owner_supplied_unverified"
    assert report["capability_hashes"] == "claimed_not_verified_or_qualification"
    rows = report["comparators"]
    assert [row["kind"] for row in rows] == [
        "checker",
        "lexical_baseline",
        "advisory_judge",
        "local_model",
        "local_model",
    ]
    assert [row["capability_sha256"] for row in rows[2:]] == ["c" * 64, "a" * 64, "b" * 64]
    measured = rows[-1]
    assert measured["honest"] == {"cases": 3, "rejected": 1, "reject_per_mille": 334}
    assert set(measured["classes"]) == set(CLASSES)
    assert all(
        metric == {"cases": 3, "rejected": 1, "reject_per_mille": 333}
        for metric in measured["classes"].values()
    )
    expected_counts = [
        (
            original["honest"]["checker_blocked"],
            {name: metric["checker_blocked"] for name, metric in original["classes"].items()},
        ),
        (
            original["honest"]["baseline_blocked"],
            {name: metric["baseline_blocked"] for name, metric in original["classes"].items()},
        ),
    ]
    for row, (honest_count, class_counts) in zip(rows[:2], expected_counts, strict=True):
        assert row["honest"]["rejected"] == honest_count
        for category in CLASSES:
            assert row["classes"][category]["rejected"] == class_counts[category]
    assert rows[2]["checker_disagreements"]["accept_checker_reject"] > 0
    assert rows[2]["source"] == "owner_supplied_unverified"
    assert rows[2]["provenance"] == "synthetic"
    assert rows[3]["provenance"] == "owner_recorded"
    assert report["dataset"]["sha256"] in outputs[0][1]
    assert run(tmp_path) == original


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_digest",
        "missing",
        "duplicate",
        "foreign",
        "extra_prediction_key",
        "wrong_verdict",
        "bool_version",
        "unknown_key",
        "bad_kind",
        "verified_provenance",
        "uppercase_hash",
        "bad_name",
        "duplicate_key",
        "nested_duplicate_key",
        "malformed",
        "oversized",
        "nonfinite",
        "wrong_predictions_type",
        "deeply_nested",
    ],
)
def test_leaderboard_refuses_invalid_recording_before_publishing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], mutation: str
) -> None:
    _comparison_dataset(tmp_path)
    exported = export_cases(load_snapshot(tmp_path))
    recording = _recording(exported)
    predictions = [{"case_id": case["case_id"], "verdict": "accept"} for case in exported["cases"]]
    recording["predictions"] = predictions
    if mutation == "wrong_digest":
        recording["dataset_sha256"] = "0" * 64
    elif mutation == "missing":
        predictions.pop()
    elif mutation == "duplicate":
        predictions.append(predictions[0])
    elif mutation == "foreign":
        predictions[0]["case_id"] = "honest.jsonl:99999"
    elif mutation == "extra_prediction_key":
        predictions[0]["qualified"] = "yes"
    elif mutation == "wrong_verdict":
        predictions[0]["verdict"] = "qualified"
    elif mutation == "bool_version":
        recording["schema_version"] = True
    elif mutation == "unknown_key":
        recording["qualified"] = True
    elif mutation == "bad_kind":
        recording["kind"] = "qualified_model"
    elif mutation == "verified_provenance":
        recording["provenance"] = "verified"
    elif mutation == "uppercase_hash":
        recording["capability_sha256"] = "A" * 64
    elif mutation == "bad_name":
        recording["name"] = "Safe\n| forged table |"
    elif mutation == "wrong_predictions_type":
        recording["predictions"] = {}
    raw = json.dumps(recording)
    if mutation == "duplicate_key":
        raw = raw.replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1')
    elif mutation == "nested_duplicate_key":
        raw = raw.replace('"verdict": "accept"', '"verdict": "reject", "verdict": "accept"', 1)
    elif mutation == "malformed":
        raw = raw[:-1]
    elif mutation == "oversized":
        raw = " " * (5 * 1024 * 1024 + 1)
    elif mutation == "nonfinite":
        raw = raw.replace('"schema_version": 1', '"schema_version": NaN')
    elif mutation == "deeply_nested":
        raw = "[" * 1500 + "0" + "]" * 1500
    result = tmp_path / "result.json"
    result.write_text(raw, encoding="utf-8")
    output = tmp_path / "report.json"
    output.write_bytes(b"previous valid report\n")
    with pytest.raises(SystemExit) as refused:
        cli_main(
            [
                "benchmark",
                "leaderboard",
                str(tmp_path),
                "--result",
                str(result),
                "--output",
                str(output),
            ]
        )
    assert refused.value.code == 2
    assert capsys.readouterr().out == ""
    assert output.read_bytes() == b"previous valid report\n"


def test_leaderboard_rejects_duplicate_identity_but_not_duplicate_display_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _comparison_dataset(tmp_path)
    snapshot = load_snapshot(tmp_path)
    exported = export_cases(snapshot)
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(json.dumps(_recording(exported)), encoding="utf-8")
    second.write_text(json.dumps(_recording(exported, name="Renamed")), encoding="utf-8")
    with pytest.raises(SystemExit) as refused:
        cli_main(
            [
                "benchmark",
                "leaderboard",
                str(tmp_path),
                "--result",
                str(first),
                "--result",
                str(second),
            ]
        )
    assert refused.value.code == 2
    assert capsys.readouterr().out == ""
    second.write_text(json.dumps(_recording(exported, capability="b" * 64)), encoding="utf-8")
    assert len(leaderboard(snapshot, [first, second])["comparators"]) == 4
    # A judge may claim the same hash as a model; kind is part of the comparator identity.
    second.write_text(json.dumps(_recording(exported, kind="advisory_judge")), encoding="utf-8")
    assert len(leaderboard(snapshot, [first, second])["comparators"]) == 4


def test_changed_corpus_cannot_reuse_recording(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _comparison_dataset(tmp_path)
    recording = _recording(export_cases(load_snapshot(tmp_path)))
    result = tmp_path / "recording.json"
    result.write_text(json.dumps(recording), encoding="utf-8")
    honest = tmp_path / "honest.jsonl"
    honest.write_bytes(honest.read_bytes() + b"\n")
    with pytest.raises(SystemExit) as refused:
        cli_main(["benchmark", "leaderboard", str(tmp_path), "--result", str(result)])
    assert refused.value.code == 2
    assert capsys.readouterr().out == ""


def test_leaderboard_requires_recordings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as refused:
        cli_main(["benchmark", "leaderboard", str(tmp_path)])
    assert refused.value.code == 2
    assert capsys.readouterr().out == ""
