# SPDX-License-Identifier: MIT OR Apache-2.0
"""The DB-free production/qualification loop and projection boundary stay identical."""

from collections.abc import Sequence
from copy import deepcopy
from typing import cast

import pytest

from bonafides import local_model, model_calls, model_projections
from bonafides.problems import Problem


def test_valid_first_attempt_keeps_result_and_skips_reservation() -> None:
    result = local_model.ModelResult({"valid": True}, {"tokens": 12})
    corrections: list[Sequence[str] | None] = []

    def call(correction: Sequence[str] | None) -> local_model.ModelResult:
        corrections.append(correction)
        return result

    def before_correction() -> None:
        pytest.fail("a valid output must not reserve a corrective call")

    checked, returned, corrected = model_calls.checked_call(
        call, lambda output: output["valid"], before_correction
    )
    assert checked is True
    assert returned is result
    assert corrected is False
    assert corrections == [None]


@pytest.mark.parametrize("transport_mismatch", [False, True])
def test_one_correction_uses_only_summary_and_reserves_before_send(
    transport_mismatch: bool,
) -> None:
    events: list[object] = []
    result = local_model.ModelResult({"valid": True}, {"tokens": 12})

    def call(correction: Sequence[str] | None) -> local_model.ModelResult:
        events.append(correction)
        if correction is None:
            if transport_mismatch:
                raise local_model.ModelFailure("model_output_schema_mismatch")
            return local_model.ModelResult({}, {})
        return result

    def validate(output: dict[str, object]) -> object:
        if not output:
            raise model_calls.OutputMismatch(["documents: missing"])
        return output["valid"]

    checked, returned, corrected = model_calls.checked_call(
        call, validate, lambda: events.append("reserved")
    )
    summary = (
        "output: json_or_schema_version_invalid" if transport_mismatch else "documents: missing"
    )
    assert events == [None, "reserved", [summary]]
    assert checked is True and returned is result and corrected is True
    assert returned.metadata == {"tokens": 12}


@pytest.mark.parametrize("transport_mismatch", [False, True])
def test_second_mismatch_fails_without_third_call(transport_mismatch: bool) -> None:
    calls: list[Sequence[str] | None] = []
    reservations: list[bool] = []

    def call(correction: Sequence[str] | None) -> local_model.ModelResult:
        calls.append(correction)
        if transport_mismatch:
            raise local_model.ModelFailure("model_output_schema_mismatch")
        return local_model.ModelResult({}, {})

    def validate(_output: dict[str, object]) -> None:
        raise model_calls.OutputMismatch(["documents: missing"])

    with pytest.raises(Problem) as exc:
        model_calls.checked_call(call, validate, lambda: reservations.append(True))
    assert exc.value.code == "model_output_invalid_after_correction"
    assert len(calls) == 2
    assert reservations == [True]


@pytest.mark.parametrize(
    "code", ["model_incomplete", "model_timeout", "model_refused", "model_identity_changed"]
)
def test_transport_failure_is_not_a_correctable_output(code: str) -> None:
    calls: list[Sequence[str] | None] = []
    failure = local_model.ModelFailure(code, retryable=True)

    def call(correction: Sequence[str] | None) -> local_model.ModelResult:
        calls.append(correction)
        raise failure

    with pytest.raises(local_model.ModelFailure) as exc:
        model_calls.checked_call(call, lambda output: output)
    assert exc.value is failure
    assert calls == [None]


def test_reservation_failure_prevents_corrective_send() -> None:
    calls: list[Sequence[str] | None] = []

    def call(correction: Sequence[str] | None) -> local_model.ModelResult:
        calls.append(correction)
        raise local_model.ModelFailure("model_output_schema_mismatch")

    def before_correction() -> None:
        raise Problem(409, "model_attempt_or_deadline_exhausted")

    with pytest.raises(Problem) as exc:
        model_calls.checked_call(call, lambda output: output, before_correction)
    assert exc.value.code == "model_attempt_or_deadline_exhausted"
    assert calls == [None]


def test_mismatch_summary_has_existing_twelve_error_limit() -> None:
    assert model_calls.OutputMismatch(["output: missing"] * 20).summary == ["output: missing"] * 12


def _projection_data() -> tuple[
    dict[str, object], list[dict[str, object]], list[dict[str, object]], dict[str, object]
]:
    claim = "00000000-0000-4000-8000-000000000001"
    evidence = "00000000-0000-4000-8000-000000000002"
    posting: dict[str, object] = {
        "title": "Synthetic engineer",
        "company": "Synthetic employer",
        "description_text": "Built cafe\u0301 tools.\r\nReviewed changes.\r",
        "private_tracking": "never projected",
    }
    claims: list[dict[str, object]] = [
        {
            "id": claim,
            "text": "Built tools.",
            "structured_fields": {},
            "evidence_revision_ids": [evidence],
        }
    ]
    sources: list[dict[str, object]] = [{"id": evidence, "text": "Built tools."}]
    target: dict[str, object] = {
        "kind": "resume",
        "section_key": "summary",
        "heading": "Summary",
        "blocks": [
            {
                "text": "Built tools.",
                "claim_kind": "factual",
                "claim_revision_ids": [claim],
                "evidence_revision_ids": [evidence],
            }
        ],
    }
    return posting, claims, sources, target


def test_projection_builders_preserve_stage_specific_posting_normalization() -> None:
    posting, claims, evidence, target = _projection_data()
    original = deepcopy((posting, claims, evidence, target))
    generated = model_projections.generation_projection(
        posting, [], claims, evidence, "professional", "en"
    )
    regenerated = model_projections.regeneration_projection(
        posting, [], claims, evidence, "professional", "en", target
    )
    assert generated["posting"] == {
        "title": posting["title"],
        "organization": posting["company"],
        "source_text": "Built caf\u00e9 tools.\nReviewed changes.\n",
    }
    pinned = cast(dict[str, object], regenerated["posting"])
    assert pinned["source_text"] == posting["description_text"]
    assert regenerated["target"] == target
    assert generated["claims"] == regenerated["claims"] == claims
    assert generated["evidence"] == regenerated["evidence"] == evidence
    assert (posting, claims, evidence, target) == original


def test_regeneration_projection_rejects_target_references_outside_pins() -> None:
    posting, claims, evidence, target = _projection_data()
    blocks = cast(list[dict[str, object]], target["blocks"])
    blocks[0]["claim_revision_ids"] = ["00000000-0000-4000-8000-000000000099"]
    with pytest.raises(local_model.ModelFailure) as exc:
        model_projections.regeneration_projection(
            posting, [], claims, evidence, "professional", "en", target
        )
    assert exc.value.code == "model_context_invalid"
