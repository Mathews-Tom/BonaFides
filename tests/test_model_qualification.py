# SPDX-License-Identifier: MIT OR Apache-2.0
"""Synthetic qualification uses fake calls; no installed model is needed by these tests."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest
from test_local_model import (
    fixture_receipt,
    fixture_server,
    qualified_route,
    response_body,
    route_data,
    route_file,
)

from bonafides import documents, import_structuring, local_model, model_calls
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


@pytest.mark.parametrize("stage", ("generate_documents", "regenerate_section"))
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
        ("generate_documents", "regenerate_section"), receipt
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


@pytest.mark.parametrize("missing_key", [False, True])
def test_missing_section_target_is_rejected_before_inference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    missing_key: bool,
) -> None:
    cases, _ = qualification.load_cases(DATASET, "regenerate_section")
    target = cast(dict[str, object], cases[0]["target"])
    if missing_key:
        del target["section_key"]
    else:
        target["section_key"] = "missing-section"
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "regenerate_section.jsonl").write_text(
        "\n".join(canonical_json(case) for case in cases) + "\n"
    )
    qualified_route(tmp_path, route_data("http://127.0.0.1:11434"))
    route = tmp_path / "route.json"
    original = route.read_bytes()

    def call(self: local_model.OllamaClient, *args: object) -> local_model.ModelResult:
        raise AssertionError("invalid section target must not reach inference")

    monkeypatch.setattr(local_model.OllamaClient, "call", call)
    assert (
        qualification.main(
            [
                "qualify",
                "--route",
                str(route),
                "--stage",
                "regenerate_section",
                "--dataset",
                str(dataset),
                "--reviewed-by",
                "synthetic-only",
            ]
        )
        == 2
    )
    assert "qualification_target_missing" in capsys.readouterr().err
    assert route.read_bytes() == original


def _import_response(items: object) -> bytes:
    output = {"schema_version": "1", "stage": "structure_import", "items": items}
    return response_body(message={"role": "assistant", "content": canonical_json(output)})


def _qualify_import(path: Path) -> int:
    return qualification.main(
        [
            "qualify",
            f"--route={path}",
            "--stage=structure_import",
            f"--dataset={DATASET}",
            "--reviewed-by=synthetic-only",
        ]
    )


def test_import_projection_preserves_literal_units_and_enforces_byte_limit() -> None:
    cases, _ = qualification.load_cases(DATASET, "structure_import")
    case = cases[0]
    units = cast(list[dict[str, object]], copy.deepcopy(case["units"]))
    units[0]["text"] = "é" * 7999
    units[1]["text"] = "é"
    context = import_structuring.projection(str(case["source_sha256"]), units)
    assert context["units"] == units and set(context) == {"source_sha256", "units"}
    units[1]["text"] = "éé"
    with pytest.raises(local_model.ModelFailure, match="import_structuring_input_limit"):
        import_structuring.projection(str(case["source_sha256"]), units)
    with pytest.raises(local_model.ModelFailure, match="import_structuring_projection_unavailable"):
        import_structuring.projection(str(case["source_sha256"]), [])
    units[1]["text"] = "literal"
    units[1]["email"] = "private@example.invalid"
    with pytest.raises(local_model.ModelFailure, match="model_context_invalid"):
        import_structuring.projection(str(case["source_sha256"]), units)
    units = [
        {"source_id": str(UUID(int=index + 1)), "text": "x", "kind": "other"}
        for index in range(101)
    ]
    with pytest.raises(local_model.ModelFailure, match="import_structuring_input_limit"):
        import_structuring.projection("a" * 64, units)


@pytest.mark.parametrize(
    "mutation", ("foreign", "duplicate", "omitted", "kind", "invented_text", "metadata")
)
def test_import_checker_rejects_invention_and_incomplete_classification(mutation: str) -> None:
    cases, _ = qualification.load_cases(DATASET, "structure_import")
    context = qualification.projection(cases[0], "structure_import")
    items = cast(list[dict[str, object]], copy.deepcopy(cases[0]["expected"]))
    result: dict[str, object] = {"schema_version": "1", "stage": "structure_import", "items": items}
    assert import_structuring.checked_units(result, context) == {
        str(item["source_id"]): str(item["kind"]) for item in items
    }
    if mutation == "foreign":
        items[0]["source_id"] = "00000000-0000-4000-8000-000000000000"
    elif mutation == "duplicate":
        items[1]["source_id"] = items[0]["source_id"]
    elif mutation == "omitted":
        items.pop()
    elif mutation == "kind":
        items[0]["kind"] = "invented employer"
    elif mutation == "invented_text":
        items[0]["text"] = "Fabricated a new qualification."
    else:
        result["warnings"] = ["Owner data must never leak through errors."]
    with pytest.raises(model_calls.OutputMismatch) as error:
        import_structuring.checked_units(result, context)
    assert error.value.summary == ["items: invalid_source_classification"]


def test_import_opt_in_preserves_disabled_capability_and_never_carries_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = route_data("http://127.0.0.1:11434")
    old = qualified_route(tmp_path, data)
    with monkeypatch.context() as patch:
        prompts = local_model.PROMPT_VERSIONS.copy()
        prompts.pop("structure_import")
        patch.setattr(local_model, "PROMPT_VERSIONS", prompts)
        unchanged = local_model.load_route(route_file(tmp_path, data))
        assert unchanged is not None and unchanged.identity() == old.identity()
    data["import_structuring"] = False
    disabled = local_model.load_route(route_file(tmp_path, data))
    assert disabled is not None and disabled.identity() == old.identity()
    assert disabled.receipt_details() == old.receipt_details()
    assert disabled._receipts_json == old._receipts_json
    with pytest.raises(local_model.ModelFailure, match="model_route_unavailable"):
        local_model.OllamaClient(disabled, qualification_stage="structure_import")
    data["import_structuring"] = True
    enabled = local_model.load_route(route_file(tmp_path, data))
    assert enabled is not None and enabled.capability_sha256 != old.capability_sha256
    assert not any(enabled.qualified_for(stage) for stage in local_model.PROMPT_VERSIONS)
    receipts = cast(dict[str, object], data["qualification_receipts"])
    receipts["generate_documents"] = fixture_receipt(enabled.capability_sha256)
    enabled = local_model.load_route(route_file(tmp_path, data))
    assert enabled is not None and enabled.qualified_for("generate_documents")
    assert not enabled.qualified_for("structure_import")
    receipts["structure_import"] = fixture_receipt(
        enabled.capability_sha256, stage="extract_posting"
    )
    with pytest.raises(local_model.ModelFailure, match="model_route_invalid"):
        local_model.load_route(route_file(tmp_path, data))
    receipts["structure_import"] = fixture_receipt(
        enabled.capability_sha256, stage="structure_import"
    )
    qualified = local_model.load_route(route_file(tmp_path, data))
    assert qualified is not None and qualified.qualified_for("structure_import")
    assert qualified.qualified_for("generate_documents")
    assert not qualified.qualified_for("regenerate_section")
    data["import_structuring"] = False
    disabled = local_model.load_route(route_file(tmp_path, data))
    assert disabled is not None and not disabled.qualified_for("structure_import")


@pytest.mark.parametrize("flag", (None, 0, 1, "true", [], {}))
def test_import_route_flag_is_strict_boolean(tmp_path: Path, flag: object) -> None:
    data = {**route_data("http://127.0.0.1:11434"), "import_structuring": flag}
    with pytest.raises(local_model.ModelFailure, match="model_route_invalid"):
        local_model.load_route(route_file(tmp_path, data))


@pytest.mark.parametrize("wrong_kind", (False, True))
def test_import_qualification_measures_classification_and_one_correction(
    tmp_path: Path, wrong_kind: bool
) -> None:
    cases, sha = qualification.load_cases(DATASET, "structure_import")
    responses: list[bytes] = []
    for case in cases:
        items = cast(list[dict[str, object]], copy.deepcopy(case["expected"]))
        if wrong_kind:
            for item in items:
                item["kind"] = "other" if item["kind"] == "identity" else "identity"
        responses.extend(
            [response_body(message={"role": "assistant", "content": "{}"}), _import_response(items)]
        )
    with fixture_server(body=responses) as (endpoint, requests):
        route = local_model.load_route(
            route_file(tmp_path, {**route_data(endpoint), "import_structuring": True})
        )
        assert route is not None
        receipt = qualification.qualify(route, "structure_import", cases, sha, "synthetic-only")
        assert len(requests) == 40
        first, corrected = (json.loads(request) for request in requests[:2])
        assert first["format"] == corrected["format"]
        assert len(first["messages"]) == 2 and len(corrected["messages"]) == 3
        assert first["messages"] == corrected["messages"][:2]
    assert receipt["counts"] == {
        "valid_first": 0,
        "valid_after_correction": 20,
        "invalid_after_correction": 0,
        "failed_before_check": 0,
    }
    assert receipt["unit_count"] == 40
    assert receipt["correct_kind_count"] == (0 if wrong_kind else 40)
    assert receipt["correct_kind_rate_per_mille"] == (0 if wrong_kind else 1000)
    assert receipt["status"] == ("failed" if wrong_kind else "passed")
    assert "usable_statement_rate_per_mille" not in receipt


def test_import_measured_cli_uses_real_transport_and_checked_source_ids(tmp_path: Path) -> None:
    cases, _ = qualification.load_cases(DATASET, "structure_import")
    body = _import_response(cases[0]["expected"])
    with fixture_server(body=body) as (endpoint, requests):
        path = route_file(tmp_path, {**route_data(endpoint), "import_structuring": True})
        assert _qualify_import(path) == 1
        route = local_model.load_route(path)
        assert route is not None and not route.qualified_for("structure_import")
        receipt = json.loads(path.read_text())["qualification_receipts"]["structure_import"]
        assert receipt["counts"]["valid_first"] == 1
        assert receipt["counts"]["invalid_after_correction"] == 19
        assert len(requests) == 39


@pytest.mark.parametrize(
    ("valid", "wrong_units", "status"),
    [
        (20, 2, "passed"),
        (20, 3, "failed"),
        (19, 0, "passed"),
        (18, 0, "failed"),
    ],
)
def test_import_cli_measures_both_independent_thresholds(
    tmp_path: Path, valid: int, wrong_units: int, status: str
) -> None:
    cases, _ = qualification.load_cases(DATASET, "structure_import")
    responses: list[bytes] = []
    remaining = wrong_units
    for index, case in enumerate(cases):
        if index >= valid:
            responses.extend([response_body(message={"role": "assistant", "content": "{}"})] * 2)
            continue
        items = cast(list[dict[str, object]], copy.deepcopy(case["expected"]))
        for item in items:
            if remaining:
                item["kind"] = "identity" if item["kind"] != "identity" else "other"
                remaining -= 1
        responses.append(_import_response(items))
    with fixture_server(body=responses) as (endpoint, requests):
        path = route_file(tmp_path, {**route_data(endpoint), "import_structuring": True})
        result = _qualify_import(path)
        assert result == (0 if status == "passed" else 1)
        assert len(requests) == valid + (20 - valid) * 2
        receipt = json.loads(path.read_text())["qualification_receipts"]["structure_import"]
        assert receipt["status"] == status
        assert receipt["valid_rate_per_mille"] == valid * 50
        assert receipt["correct_kind_count"] == valid * 2 - wrong_units
        assert receipt["correct_kind_rate_per_mille"] == (valid * 2 - wrong_units) * 25
        assert receipt == local_model.qualification_receipt(receipt, stage="structure_import")
        route = local_model.load_route(path)
        assert route is not None and route.qualified_for("structure_import") == (status == "passed")
        assert not route.qualified_for("generate_documents")


@pytest.mark.parametrize("count", [19, 21])
def test_import_dataset_requires_exactly_twenty_cases(tmp_path: Path, count: int) -> None:
    cases, _ = qualification.load_cases(DATASET, "structure_import")
    if count == 19:
        cases.pop()
    else:
        cases.append({**cases[0], "id": "extra-case"})
    (tmp_path / "structure_import.jsonl").write_text(
        "\n".join(canonical_json(case) for case in cases) + "\n"
    )
    with pytest.raises(ValueError, match="qualification_case_count_invalid"):
        qualification.load_cases(tmp_path, "structure_import")


def refinement_output(case: dict[str, object]) -> dict[str, object]:
    expected = cast(dict[str, object], case["expected"])
    edits = []
    for target in cast(list[dict[str, object]], case["targets"]):
        before = cast(dict[str, object], target["before"])
        text = str(before["text"])
        edits.append(
            {
                key: target[key]
                for key in ("document_id", "section_key", "block_key", "previous_sha256")
            }
            | {"replacement": {**before, "text": text[:-1] if text.endswith(".") else text + "."}}
        )
    return {
        "schema_version": "1",
        "stage": "refine_documents",
        "reply_text": "Reviewed.",
        "declined": expected["declined"],
        "tone": None,
        "edits": edits if expected["minimum_edits"] else [],
    }


def test_refinement_protocol_and_pinned_projection() -> None:
    from pydantic import ValidationError

    from bonafides.conversation_protocol import ConversationReply
    from bonafides.model_projections import refinement_projection

    cases, _ = qualification.load_cases(DATASET, "refine_documents")
    case = cases[1]
    output = refinement_output(case)
    output["edits"] = [*cast(list[object], output["edits"])] * 2
    reply = ConversationReply.model_validate_json(canonical_json(output))
    assert len(reply.edits) == 2
    for field, value in (("schema_version", "2"), ("declined", True), ("reply_text", " ")):
        with pytest.raises(ValidationError):
            ConversationReply.model_validate_json(canonical_json({**output, field: value}))
    target = cast(list[dict[str, object]], case["targets"])[0]
    replacement = {**cast(dict[str, object], target["before"]), "block_key": "different"}
    edit = {
        key: target[key] for key in ("document_id", "section_key", "block_key", "previous_sha256")
    }
    with pytest.raises(ValidationError):
        ConversationReply.model_validate_json(
            canonical_json({**output, "edits": [{**edit, "replacement": replacement}]})
        )
    turns: list[dict[str, object]] = [
        {"request_text": str(index), "reply_text": "Supported."} for index in range(14)
    ]
    projected = refinement_projection(
        cast(list[dict[str, object]], case["base_contexts"]),
        str(case["request"]),
        turns,
        cast(list[dict[str, object]], case["targets"]),
    )
    assert projected["turns"] == turns[-9:]
    assert len(cast(list[object], projected["base_contexts"])) == 2
    polluted = copy.deepcopy(projected)
    cast(list[dict[str, object]], polluted["base_contexts"])[0]["private_notes"] = "PRIVATE"
    with pytest.raises(local_model.ModelFailure, match="model_context_invalid"):
        local_model.refinement_context(polluted)
    target["previous_sha256"] = "0" * 64
    with pytest.raises(local_model.ModelFailure, match="model_context_invalid"):
        qualification.projection(case, "refine_documents")


@pytest.mark.parametrize("corrected", [False, True])
def test_packaged_refinement_cases_fit_installed_transport_budget(
    tmp_path: Path, corrected: bool
) -> None:
    from test_local_model import fixture_server, response_body

    cases, sha = qualification.load_cases(DATASET, "refine_documents")
    responses = [
        response_body(message={"role": "assistant", "content": canonical_json(output)})
        for case in cases
        for output in (
            ({"schema_version": "1", "stage": "refine_documents"}, refinement_output(case))
            if corrected
            else (refinement_output(case),)
        )
    ]
    with fixture_server(responses) as (endpoint, requests):
        data = route_data(endpoint)
        data.update(
            context_window=32768,
            max_input_tokens=12000,
            max_output_tokens=6000,
            framing_token_overhead=2048,
        )
        route = local_model.load_route(route_file(tmp_path, data))
        assert route is not None
        receipt = qualification.qualify(route, "refine_documents", cases, sha, "synthetic-only")
    assert receipt["status"] == "passed"
    assert receipt["counts"] == {
        "valid_first": 0 if corrected else 20,
        "valid_after_correction": 20 if corrected else 0,
        "invalid_after_correction": 0,
        "failed_before_check": 0,
    }
    assert receipt["usable_statement_rate_per_mille"] == 1000
    assert len(requests) == (40 if corrected else 20)


@pytest.mark.parametrize(
    "mutation",
    [
        "document_id",
        "section_key",
        "block_key",
        "before",
        "duplicate_document",
        "noncanonical_id",
        "nonstring_id",
    ],
)
def test_refinement_dataset_binds_targets_to_frozen_units(tmp_path: Path, mutation: str) -> None:
    cases, _ = qualification.load_cases(DATASET, "refine_documents")
    target = cast(list[dict[str, object]], cases[0]["targets"])[0]
    frozen = cast(list[dict[str, object]], cases[0]["documents"])
    document = next(item for item in frozen if item["document_id"] == target["document_id"])
    if mutation in {"document_id", "section_key"}:
        target[mutation] = str(UUID(int=999)) if mutation == "document_id" else "missing-section"
    elif mutation == "duplicate_document":
        frozen.append(copy.deepcopy(document))
    elif mutation == "nonstring_id":
        document["document_id"] = 123
    elif mutation == "noncanonical_id":
        original_id = target["document_id"]
        document["document_id"] = "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"
        for item in cast(list[dict[str, object]], cases[0]["targets"]):
            if item["document_id"] == original_id:
                item["document_id"] = document["document_id"]
    else:
        body = cast(dict[str, object], document["body"])
        section = next(
            item
            for item in cast(list[dict[str, object]], body["sections"])
            if item["key"] == target["section_key"]
        )
        block = next(
            item
            for item in cast(list[dict[str, object]], section["blocks"])
            if item["block_key"] == target["block_key"]
        )
        block["block_key" if mutation == "block_key" else "text"] = "changed-frozen-unit"
    (tmp_path / "refine_documents.jsonl").write_text(
        "\n".join(canonical_json(case) for case in cases) + "\n"
    )
    with pytest.raises(ValueError, match="qualification_case_invalid"):
        qualification.load_cases(tmp_path, "refine_documents")


@pytest.mark.parametrize("unknown_field", ["document_id", "section_key", "block_key"])
def test_refinement_qualification_checks_changes_refusal_and_single_correction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unknown_field: str
) -> None:
    cases, sha = qualification.load_cases(DATASET, "refine_documents")
    route = local_model.load_route(route_file(tmp_path, route_data("http://127.0.0.1:11434")))
    assert route is not None
    calls = 0

    def call(self: local_model.OllamaClient, *args: object) -> local_model.ModelResult:
        nonlocal calls
        calls += 1
        context = cast(dict[str, object], args[1])
        case = next(item for item in cases if item["request"] == context["request"])
        if args[4] is None:
            return local_model.ModelResult({}, {})
        output = refinement_output(case)
        edits = cast(list[dict[str, object]], output["edits"])
        if not output["declined"]:
            target = cast(list[dict[str, object]], case["targets"])[0]
            unknown = {
                key: target[key]
                for key in ("document_id", "section_key", "block_key", "previous_sha256")
            } | {"replacement": copy.deepcopy(target["before"])}
            unknown[unknown_field] = (
                str(UUID(int=999)) if unknown_field == "document_id" else "unselected"
            )
            replacement = cast(dict[str, object], unknown["replacement"])
            replacement["block_key"] = unknown["block_key"]
            replacement["text"] = "Invented unsupported achievement."
            replacement["claim_revision_ids"] = (
                cast(list[object], replacement["claim_revision_ids"]) * 2
            )
            edits.append(unknown)
        return local_model.ModelResult(output, {})

    monkeypatch.setattr(local_model.OllamaClient, "call", call)
    receipt = qualification.qualify(route, "refine_documents", cases, sha, "synthetic-only")
    assert receipt["status"] == "passed"
    assert receipt["counts"] == {
        "valid_first": 0,
        "valid_after_correction": 20,
        "invalid_after_correction": 0,
        "failed_before_check": 0,
    }
    assert calls == 40
    assert receipt["usable_statement_rate_per_mille"] == 1000
    unchanged = refinement_output(cases[0])
    unchanged["edits"] = []
    with pytest.raises(model_calls.OutputMismatch):
        qualification.checked_refinement(unchanged, cases[0])
    unsafe = refinement_output(cases[0])
    edit = cast(list[dict[str, object]], unsafe["edits"])[0]
    cast(dict[str, object], edit["replacement"])["text"] = "Led a fabricated department."
    with pytest.raises(model_calls.OutputMismatch):
        qualification.checked_refinement(unsafe, cases[0])
    no_change = next(
        case
        for case in cases
        if cast(dict[str, object], case["expected"]) == {"declined": False, "minimum_edits": 0}
    )
    toned = refinement_output(no_change)
    toned["tone"] = "concise"
    with pytest.raises(model_calls.OutputMismatch):
        qualification.checked_refinement(toned, no_change)


def test_regeneration_receipt_cannot_qualify_refinement(tmp_path: Path) -> None:
    data = route_data("http://127.0.0.1:11434")
    route = local_model.load_route(route_file(tmp_path, data))
    assert route is not None
    data["qualification_receipts"] = {
        "regenerate_section": fixture_receipt(route.capability_sha256, stage="regenerate_section")
    }
    route = local_model.load_route(route_file(tmp_path, data))
    assert route is not None and route.qualified_for("regenerate_section")
    assert not route.qualified_for("refine_documents")
    assert route.receipt_details()["refine_documents"]["qualified"] is False
