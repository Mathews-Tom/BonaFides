# SPDX-License-Identifier: MIT OR Apache-2.0
"""Synthetic qualification uses fake calls; no installed model is needed by these tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from test_local_model import (
    fixture_receipt,
    fixture_server,
    qualified_route,
    route_data,
    route_file,
)

from bonafides import documents, local_model, model_calls
from bonafides import model_qualification as qualification
from bonafides.commands import canonical_json
from bonafides.config import ConfigurationError, read_secret

DATASET = Path(__file__).parent / "fixtures" / "qualification"


def output(case: dict[str, object], stage: str) -> dict[str, object]:
    claim = cast(list[dict[str, object]], case["claims"])[0]
    block = {"block_key": "b-1", "kind": "factual", "text": claim["text"], "claim_id": claim["id"]}
    if stage == "regenerate_section":
        return {"schema_version": "2", "stage": stage, "blocks": [block], "warnings": []}
    return {
        "schema_version": "2",
        "stage": stage,
        "documents": [
            {
                "kind": kind,
                "sections": [
                    {"heading": "Experience", "blocks": [{**block, "block_key": f"b-{index + 1}"}]}
                ],
            }
            for index, kind in enumerate(("resume", "cover_letter"))
        ],
        "gaps": [],
        "warnings": [],
    }


@pytest.mark.parametrize("stage", qualification.STAGES)
@pytest.mark.parametrize(("valid", "expected"), [(19, "passed"), (18, "failed")])
def test_qualification_counts_threshold_and_checker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    valid: int,
    expected: str,
) -> None:
    cases, sha = qualification.load_cases(DATASET, stage)
    route = local_model.load_route(route_file(tmp_path, route_data("http://127.0.0.1:11434")))
    assert route is not None
    calls = 0

    def call(self: local_model.OllamaClient, *args: object) -> local_model.ModelResult:
        nonlocal calls
        index = calls
        calls += 1
        if index >= valid:
            raise local_model.ModelFailure("model_incomplete")
        context = cast(dict[str, object], args[1])
        case = next(
            case
            for case in cases
            if cast(dict[str, object], case["posting"])["company"]
            == cast(dict[str, object], context["posting"])["organization"]
        )
        return local_model.ModelResult(output(case, stage), {})

    monkeypatch.setattr(local_model.OllamaClient, "call", call)
    receipt = qualification.qualify(route, stage, cases, sha, "synthetic-only")
    assert receipt["status"] == expected
    assert receipt["valid_rate_per_mille"] == valid * 50
    assert receipt["usable_statement_rate_per_mille"] == 1000
    assert receipt["counts"] == {
        "valid_first": valid,
        "valid_after_correction": 0,
        "invalid_after_correction": 0,
        "failed_before_check": 20 - valid,
    }


def test_correction_and_invalid_are_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cases, sha = qualification.load_cases(DATASET, "generate_documents")
    route = local_model.load_route(route_file(tmp_path, route_data("http://127.0.0.1:11434")))
    assert route is not None
    calls = 0

    def call(self: local_model.OllamaClient, *args: object) -> local_model.ModelResult:
        nonlocal calls
        calls += 1
        if args[4] is None or calls > 2:
            return local_model.ModelResult({}, {})
        return local_model.ModelResult(output(cases[0], "generate_documents"), {})

    monkeypatch.setattr(local_model.OllamaClient, "call", call)
    receipt = qualification.qualify(route, "generate_documents", cases, sha, "synthetic-only")
    assert receipt["counts"] == {
        "valid_first": 0,
        "valid_after_correction": 1,
        "invalid_after_correction": 19,
        "failed_before_check": 0,
    }
    assert calls == 40


def test_unsupported_output_is_not_usable() -> None:
    cases, _ = qualification.load_cases(DATASET, "generate_documents")
    case = cases[0]
    plan = {
        "facts": case["facts"],
        "frame": {},
        "context": qualification.projection(case, "generate_documents"),
    }
    candidate = output(case, "generate_documents")
    for document in cast(list[dict[str, object]], candidate["documents"]):
        sections = cast(list[dict[str, object]], document["sections"])
        cast(list[dict[str, object]], sections[0]["blocks"])[0]["text"] = (
            "Invented a quantum cloud platform."
        )
    bodies = qualification.checked_bodies(
        candidate, "generate_documents", plan, cast(dict[str, object], case["target"])
    )
    assert qualification.statement_counts(bodies, plan) == (2, 0)


def test_atomic_write_preserves_other_stage_and_refuses_edits(tmp_path: Path) -> None:
    data = route_data("http://127.0.0.1:11434")
    path = route_file(tmp_path, data)
    route = local_model.load_route(path)
    assert route is not None
    receipt = fixture_receipt(route.capability_sha256)
    qualification.write_receipt(path, read_secret(path, "route"), "regenerate_section", receipt)
    original = read_secret(path, "route")
    qualification.write_receipt(path, original, "generate_documents", receipt)
    assert json.loads(path.read_text())["qualification_receipts"] == dict.fromkeys(
        qualification.STAGES, receipt
    )
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match="qualification_route_changed"):
        qualification.write_receipt(path, original, "generate_documents", receipt)
    assert not list(tmp_path.glob("*.tmp"))
    link = tmp_path / "linked.json"
    link.symlink_to(path)
    with pytest.raises(ConfigurationError):
        qualification.write_receipt(link, read_secret(path, "route"), "generate_documents", receipt)
    assert path.read_text() == canonical_json(json.loads(path.read_text())) + "\n"


def test_dataset_digest_binds_bytes(tmp_path: Path) -> None:
    _, original = qualification.load_cases(DATASET, "generate_documents")
    data = (DATASET / "generate_documents.jsonl").read_bytes()
    (tmp_path / "generate_documents.jsonl").write_bytes(data.replace(b"Workshop", b"Workroom", 1))
    _, changed = qualification.load_cases(tmp_path, "generate_documents")
    assert original != changed


@pytest.mark.parametrize(
    "code",
    [
        "model_cloud_rejected",
        "model_identity_changed",
        "model_metadata_invalid",
        "model_unavailable",
        "model_destination_rejected",
        "model_tls_rejected",
        "model_tls_required",
        "model_prompt_mismatch",
        "model_context_invalid",
        "model_interrupted",
        "future_policy_refusal",
    ],
)
def test_unverifiable_route_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    code: str,
) -> None:
    cases, sha = qualification.load_cases(DATASET, "generate_documents")
    route = local_model.load_route(route_file(tmp_path, route_data("http://127.0.0.1:11434")))
    assert route is not None

    def call(self: local_model.OllamaClient, *args: object) -> local_model.ModelResult:
        raise local_model.ModelFailure(code)

    monkeypatch.setattr(local_model.OllamaClient, "call", call)
    with pytest.raises(local_model.ModelFailure) as failure:
        qualification.qualify(route, "generate_documents", cases, sha, "synthetic-only")
    assert failure.value.code == code


def test_destination_refusal_preserves_passed_receipt(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with fixture_server() as (endpoint, requests):
        data = route_data(endpoint)
        data["approved_ips"] = ["127.0.0.2"]
        qualified_route(tmp_path, data)
        path = tmp_path / "route.json"
        original = path.read_bytes()
        assert (
            qualification.main(
                [
                    "qualify",
                    "--route",
                    str(path),
                    "--stage",
                    "generate_documents",
                    "--dataset",
                    str(DATASET),
                    "--reviewed-by",
                    "synthetic-only",
                ]
            )
            == 2
        )
        assert "model_destination_rejected" in capsys.readouterr().err
        assert path.read_bytes() == original
        assert requests == []


@pytest.mark.parametrize("reviewer", ["", "x" * 301, "invalid\nreviewer", "invalid\x7freviewer"])
def test_invalid_reviewer_is_rejected_before_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    reviewer: str,
) -> None:
    def call(*args: object, **kwargs: object) -> local_model.ModelResult:
        raise AssertionError("invalid reviewer must not reach transport")

    monkeypatch.setattr(local_model.OllamaClient, "call", call)
    assert (
        qualification.main(
            [
                "qualify",
                "--route",
                str(tmp_path / "missing.json"),
                "--stage",
                "generate_documents",
                "--dataset",
                str(DATASET),
                "--reviewed-by",
                reviewer,
            ]
        )
        == 2
    )
    assert "qualification_reviewer_invalid" in capsys.readouterr().err


def test_section_qualification_checks_full_replacement_limits() -> None:
    cases, _ = qualification.load_cases(DATASET, "regenerate_section")
    case = cases[0]
    body = documents.parse_body(case["base_body"])
    other = body.sections[1]
    full = documents.DocumentBody(
        title=body.title,
        sections=[
            body.sections[0],
            other.model_copy(
                update={
                    "blocks": [
                        other.blocks[0].model_copy(update={"block_key": f"existing-{index}"})
                        for index in range(127)
                    ]
                }
            ),
        ],
    )
    candidate = output(case, "regenerate_section")
    blocks = cast(list[dict[str, object]], candidate["blocks"])
    blocks.append({**blocks[0], "block_key": "b-2"})
    plan = {
        "facts": case["facts"],
        "frame": {},
        "context": qualification.projection(case, "regenerate_section"),
    }
    with pytest.raises(model_calls.OutputMismatch) as mismatch:
        qualification.checked_bodies(
            candidate, "regenerate_section", plan, cast(dict[str, object], case["target"]), full
        )
    assert any("document_too_large" in item for item in mismatch.value.summary)
