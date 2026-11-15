# SPDX-License-Identifier: MIT OR Apache-2.0
"""Operator-only email qualification; all inference uses deterministic local fixtures."""

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

from bonafides import documents, local_model, model_calls, model_projections
from bonafides import model_qualification as qualification
from bonafides.commands import canonical_json
from bonafides.fabrication_benchmark import digest
from tools import stub_model

STAGE = "write_application_email"
DATASET = Path(__file__).parent / "fixtures" / "qualification"


def _output(context: dict[str, object], scenario: str = "valid") -> dict[str, object]:
    request: dict[str, object] = {
        "messages": [{"role": "system", "content": "fixture"}, {"content": canonical_json(context)}]
    }
    envelope = stub_model.respond(scenario, request)
    message = cast(dict[str, object], envelope["message"])
    return cast(dict[str, object], json.loads(str(message["content"])))


def _plan(case: dict[str, object]) -> dict[str, object]:
    context = qualification.projection(case, STAGE)
    posting = cast(dict[str, object], context["posting"])
    return {
        "context": context,
        "facts": case["facts"],
        "frame": {"role": posting["title"], "organization": posting["organization"]},
    }


def test_email_dataset_is_distinct_grounded_and_bundled() -> None:
    cases, sha = qualification.load_cases(DATASET, STAGE)
    assert sha == digest({f"{STAGE}.jsonl": (DATASET / f"{STAGE}.jsonl").read_bytes()})
    assert len(cases) == 20 and len({case["id"] for case in cases}) == 20
    assert len({cast(dict[str, object], case["posting"])["title"] for case in cases}) == 10
    assert cases[0]["tone"] == "friendly"
    assert {case["tone"] for case in cases} == {"friendly", "concise", "professional"}
    assert len({canonical_json(qualification.projection(case, STAGE)) for case in cases}) == 20
    for case in cases:
        plan = _plan(case)
        context = cast(dict[str, object], plan["context"])
        assert "Kubernetes" in str(context["posting"])
        assert "Kubernetes" not in canonical_json(context["claims"])
        assert "Kubernetes" not in canonical_json(context["evidence"])
        body = qualification.checked_email(_output(context), plan)
        assert qualification.statement_counts([("cover_letter", body)], plan) == (2, 2)


def test_email_prompt_offers_only_body_connectives() -> None:
    prompt = local_model._SUFFIXES[STAGE]
    for phrase in (
        "Dear Hiring team,",
        "Dear {organization} hiring team,",
        "Sincerely,",
        "Kind regards,",
    ):
        assert phrase not in prompt
    assert "Thank you for your consideration." in prompt


@pytest.mark.parametrize("index", [3, 7, 11, 15])
def test_email_dataset_letter_context_cannot_establish_new_claims(index: int) -> None:
    cases, _ = qualification.load_cases(DATASET, STAGE)
    case = cases[index]
    original = documents.parse_body(case["letter_body"])
    blocks = original.sections[0].blocks
    assert blocks[0].claim_kind == blocks[-1].claim_kind == "greeting"
    assert any(block.claim_kind == "intent" for block in blocks)
    plan = _plan(case)
    context = cast(dict[str, object], plan["context"])
    body_text = str(context["letter_body"])
    assert blocks[0].text not in body_text and blocks[-1].text not in body_text
    unsupported = next(block for block in blocks if "Kubernetes" in block.text)
    assert unsupported.text in body_text
    assert any(block.text in body_text for block in blocks if block.claim_kind == "intent")
    candidate = _output(context)
    reply_blocks = cast(list[dict[str, object]], candidate["blocks"])
    reply_blocks.append({**reply_blocks[0], "block_key": "b-900", "text": unsupported.text})
    with pytest.raises(model_calls.OutputMismatch):
        qualification.checked_email(candidate, plan)


def test_email_projection_excludes_private_fields_and_legacy_correspondence() -> None:
    cases, _ = qualification.load_cases(DATASET, STAGE)
    case = cases[0]
    private = {
        key: f"PRIVATE-{key}"
        for key in (
            "sender",
            "contact",
            "signature",
            "recipient",
            "greeting",
            "closing",
            "correspondence_date",
            "contacts",
            "portrait",
            "private_notes",
        )
    }
    posting = {**cast(dict[str, object], case["posting"]), **private}
    body = documents.parse_body(case["letter_body"])
    original = body.sections[0]
    frame_blocks = [
        documents.DocumentBlock(
            block_key=f"b-{index}",
            text=private[key],
            claim_kind="greeting",
            claim_revision_ids=[],
            evidence_revision_ids=[],
        )
        for index, key in enumerate(("greeting", "closing"), start=98)
    ]
    section = original.model_copy(
        update={"blocks": [frame_blocks[0], *original.blocks, frame_blocks[1]]}
    )
    body = body.model_copy(update={"sections": [section]})
    projection = model_projections.application_email_projection(
        posting,
        cast(list[dict[str, object]], case["requirements"]),
        cast(list[dict[str, object]], case["claims"]),
        cast(list[dict[str, object]], case["evidence"]),
        str(case["tone"]),
        str(case["language"]),
        body,
        frame=dict(private),
    )
    assert set(projection) == {
        "posting",
        "requirements",
        "claims",
        "evidence",
        "tone",
        "language",
        "letter_body",
    }
    assert projection["letter_body"] == "\n\n".join(block.text for block in original.blocks)
    assert not any(secret in canonical_json(projection) for secret in private.values())
    assert set(cast(dict[str, object], projection["posting"])) == {
        "title",
        "organization",
        "source_text",
    }


@pytest.mark.parametrize(
    "private_field",
    ["sender", "contact", "signature", "recipient", "frame", "portrait", "private_notes"],
)
def test_email_wire_rejects_extraneous_private_roots(private_field: str) -> None:
    cases, _ = qualification.load_cases(DATASET, STAGE)
    context = qualification.projection(cases[0], STAGE)
    with pytest.raises(local_model.ModelFailure, match="model_context_invalid"):
        local_model.email_context({**context, private_field: "PRIVATE"})


@pytest.mark.parametrize(
    "mutation",
    [
        "recipient",
        "address",
        "subject_fact",
        "subject_length",
        "empty",
        "duplicate",
        "unknown",
        "unsupported",
        "connective_fact",
        "greeting",
        "closing",
        "evidence_only",
        "blank",
    ],
)
def test_email_checker_rejects_every_unsupported_channel(mutation: str) -> None:
    cases, _ = qualification.load_cases(DATASET, STAGE)
    plan = _plan(cases[0])
    candidate = _output(cast(dict[str, object], plan["context"]))
    blocks = cast(list[dict[str, object]], candidate["blocks"])
    if mutation in {"recipient", "address"}:
        candidate[mutation] = "private@example.invalid"
    elif mutation == "subject_fact":
        candidate["subject"] = "Experienced Kubernetes specialist"
    elif mutation == "subject_length":
        candidate["subject"] = "x" * 121
    elif mutation == "empty":
        candidate["blocks"] = []
    elif mutation == "duplicate":
        blocks.append(dict(blocks[0]))
    elif mutation == "unknown":
        blocks[0]["claim_id"] = str(UUID(int=0))
    elif mutation == "evidence_only":
        blocks[0]["text"] = "The work was recorded in a synthetic project log."
    elif mutation == "blank":
        blocks[0]["text"] = " "
    elif mutation in {"unsupported", "connective_fact"}:
        blocks[0]["text"] = "Experienced with Kubernetes."
        if mutation == "connective_fact":
            blocks[0].update(kind="connective", claim_id=None)
    else:
        blocks[0].update(
            kind="connective",
            claim_id=None,
            text="Dear Hiring team," if mutation == "greeting" else "Kind regards,",
        )
    with pytest.raises(model_calls.OutputMismatch):
        qualification.checked_email(candidate, plan)


def test_email_letter_is_not_an_extra_factual_authority() -> None:
    cases, _ = qualification.load_cases(DATASET, STAGE)
    case = copy.deepcopy(cases[0])
    body = cast(dict[str, object], case["letter_body"])
    sections = cast(list[dict[str, object]], body["sections"])
    blocks = cast(list[dict[str, object]], sections[0]["blocks"])
    blocks[0]["text"] = "Led a Kubernetes rollout for fifty teams."
    plan = _plan(case)
    context = cast(dict[str, object], plan["context"])
    candidate = _output(context)
    cast(list[dict[str, object]], candidate["blocks"])[0]["text"] = blocks[0]["text"]
    with pytest.raises(model_calls.OutputMismatch):
        qualification.checked_email(candidate, plan)


@pytest.mark.parametrize(
    ("scenario", "corrected", "invalid"),
    [
        ("valid", 0, 0),
        ("malformed-once", 20, 0),
        ("posting-only-skill", 0, 20),
        ("absent-claim", 0, 20),
        ("malformed", 0, 20),
    ],
)
def test_email_qualification_uses_shared_single_correction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    corrected: int,
    invalid: int,
) -> None:
    cases, sha = qualification.load_cases(DATASET, STAGE)
    route = local_model.load_route(route_file(tmp_path, route_data("http://127.0.0.1:11434")))
    assert route is not None
    calls = 0

    def call(self: local_model.OllamaClient, *args: object) -> local_model.ModelResult:
        nonlocal calls
        calls += 1
        context = cast(dict[str, object], args[1])
        actual = "valid" if scenario == "malformed-once" and args[4] else scenario
        return local_model.ModelResult(_output(context, actual), {})

    monkeypatch.setattr(local_model.OllamaClient, "call", call)
    receipt = qualification.qualify(route, STAGE, cases, sha, "synthetic-only")
    assert calls == 20 + corrected + invalid
    assert receipt["status"] == ("failed" if invalid else "passed")
    assert receipt["counts"] == {
        "valid_first": 20 - corrected - invalid,
        "valid_after_correction": corrected,
        "invalid_after_correction": invalid,
        "failed_before_check": 0,
    }
    assert receipt["usable_statement_rate_per_mille"] == (0 if invalid else 1000)
    assert receipt["capability_sha256"] == route.capability_for(STAGE)


@pytest.mark.parametrize(("valid", "status"), [(19, "passed"), (18, "failed")])
def test_email_qualification_threshold_includes_precheck_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    valid: int,
    status: str,
) -> None:
    cases, sha = qualification.load_cases(DATASET, STAGE)
    route = local_model.load_route(route_file(tmp_path, route_data("http://127.0.0.1:11434")))
    assert route is not None
    calls = 0

    def call(self: local_model.OllamaClient, *args: object) -> local_model.ModelResult:
        nonlocal calls
        calls += 1
        if calls > valid:
            raise local_model.ModelFailure("model_incomplete")
        return local_model.ModelResult(_output(cast(dict[str, object], args[1])), {})

    monkeypatch.setattr(local_model.OllamaClient, "call", call)
    receipt = qualification.qualify(route, STAGE, cases, sha, "synthetic-only")
    assert receipt["status"] == status and receipt["valid_rate_per_mille"] == valid * 50
    assert cast(dict[str, int], receipt["counts"])["failed_before_check"] == 20 - valid


def test_email_cli_transports_exact_wire_and_preserves_other_receipts(tmp_path: Path) -> None:
    cases, sha = qualification.load_cases(DATASET, STAGE)
    responses = []
    for case in cases:
        output = _output(qualification.projection(case, STAGE))
        responses.extend(
            [
                response_body(message={"role": "assistant", "content": "{}"}),
                response_body(message={"role": "assistant", "content": canonical_json(output)}),
            ]
        )
    with fixture_server(responses) as (endpoint, requests):
        data = route_data(endpoint)
        data["max_output_tokens"] = 6000
        existing = qualified_route(tmp_path, data)
        receipts = cast(dict[str, object], data["qualification_receipts"])
        receipts.pop(STAGE)
        original = copy.deepcopy(receipts)
        path = route_file(tmp_path, data)
        assert (
            qualification.main(
                [
                    "qualify",
                    "--route",
                    str(path),
                    "--stage",
                    STAGE,
                    "--dataset",
                    str(DATASET),
                    "--reviewed-by",
                    "synthetic-only",
                ]
            )
            == 0
        )
        assert len(requests) == 40
        first, correction = (json.loads(request) for request in requests[:2])
        assert json.loads(first["messages"][1]["content"]) == qualification.projection(
            cases[0], STAGE
        )
        assert first["messages"] == correction["messages"][:2]
        assert len(correction["messages"]) == 3 and first["format"] == correction["format"]
        assert first["format"]["additionalProperties"] is False
        assert set(first["format"]["properties"]) == {
            "schema_version",
            "stage",
            "subject",
            "blocks",
        }
        assert first["options"]["num_predict"] == 6000
        published = json.loads(path.read_text())["qualification_receipts"]
        assert {key: value for key, value in published.items() if key != STAGE} == original
        assert published[STAGE]["dataset_sha256"] == sha
        route = local_model.load_route(path)
        assert route is not None and route.qualified_for(STAGE)
        assert route.capability_sha256 == existing.capability_sha256
        assert route.receipt_details()[STAGE]["qualified"] is True


def test_email_capability_is_separate_and_changes_invalidate_only_email(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = route_data("http://127.0.0.1:11434")
    route = qualified_route(tmp_path, data)
    receipts = cast(dict[str, object], data["qualification_receipts"])
    assert route.qualified_for(STAGE)
    assert route.capability_for(STAGE) != route.capability_sha256
    monkeypatch.setitem(local_model._SUFFIXES, STAGE, local_model._SUFFIXES[STAGE] + " Changed.")
    changed = local_model.load_route(route_file(tmp_path, data))
    assert changed is not None and not changed.qualified_for(STAGE)
    assert changed.capability_sha256 == route.capability_sha256
    assert changed.qualified_for("generate_documents") and changed.qualified_for("refine_documents")
    assert changed.receipt_details()[STAGE]["reason"] == "capability_changed"
    receipts[STAGE] = fixture_receipt(changed.capability_sha256, stage=STAGE)
    wrong = local_model.load_route(route_file(tmp_path, data))
    assert wrong is not None and not wrong.qualified_for(STAGE)


def test_email_transport_requires_its_own_stage_receipt(tmp_path: Path) -> None:
    cases, _ = qualification.load_cases(DATASET, STAGE)
    data = route_data("http://127.0.0.1:11434")
    qualified_route(tmp_path, data)
    cast(dict[str, object], data["qualification_receipts"]).pop(STAGE)
    route = local_model.load_route(route_file(tmp_path, data))
    assert route is not None and route.qualified_for("generate_documents")
    with pytest.raises(local_model.ModelFailure, match="model_route_unavailable"):
        local_model.OllamaClient(route).prepare(
            STAGE, qualification.projection(cases[0], STAGE), local_model.PROMPT_VERSIONS[STAGE]
        )
