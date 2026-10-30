# SPDX-License-Identifier: MIT OR Apache-2.0
"""Claim schema v1 renders deterministically and stores structure rather than sentences."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError
from test_identity import IdentityContext, _bootstrap
from test_identity import identity as identity
from test_profiles import _json, _mutate
from test_profiles import profile_owner as profile_owner

from bonafides.claims import (
    OWNERSHIP_VERBS,
    StructuredClaim,
    claim_text,
    grounded,
    needs_attention,
    render_claim,
)


def _claim(**fields: object) -> StructuredClaim:
    return StructuredClaim.model_validate(
        {"schema_version": 1, "verb": "led", "subject": "the billing migration", **fields}
    )


def test_ownership_vocabulary_is_ordered_strongest_first() -> None:
    assert OWNERSHIP_VERBS[0] == "led"
    assert OWNERSHIP_VERBS.index("built") < OWNERSHIP_VERBS.index("contributed")
    assert OWNERSHIP_VERBS[-1] == "assisted"
    assert len(set(OWNERSHIP_VERBS)) == len(OWNERSHIP_VERBS)


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({}, "Led the billing migration"),
        ({"scope": "across four teams"}, "Led the billing migration across four teams"),
        (
            {
                "verb": "reduced",
                "subject": "checkout latency",
                "metric": {"value": "35", "unit": "percent", "change": "decrease"},
            },
            "Reduced checkout latency by 35%",
        ),
        (
            {"metric": {"value": "40", "unit": "percent", "change": "decrease", "measure": "cost"}},
            "Led the billing migration, reducing cost by 40%",
        ),
        (
            {"metric": {"value": "2", "unit": "times", "change": "increase"}},
            "Led the billing migration, increasing it by 2x",
        ),
        (
            {
                "metric": {
                    "value": "1.2",
                    "unit": "usd",
                    "magnitude": "million",
                    "change": "saving",
                    "measure": "annually",
                }
            },
            "Led the billing migration, saving $1.2M annually",
        ),
        (
            {"metric": {"value": "1", "unit": "hours", "measure": "of weekly toil"}},
            "Led the billing migration, reaching 1 hour of weekly toil",
        ),
        (
            {"verb": "contributed", "period": {"start": "2021-03", "end": "2022"}},
            "Contributed to the billing migration (Mar 2021–2022)",
        ),
        ({"period": {"start": "2020", "end": "2020"}}, "Led the billing migration (2020)"),
        (
            {
                "verb": "improved",
                "subject": "test coverage",
                "metric": {"value": "25", "unit": "percent", "change": "improvement"},
            },
            "Improved test coverage by 25%",
        ),
        (
            {
                "metric": {
                    "value": "6",
                    "unit": "hours",
                    "per": "week",
                    "change": "decrease",
                    "measure": "manual reconciliation",
                }
            },
            "Led the billing migration, reducing manual reconciliation by 6 hours per week",
        ),
    ],
)
def test_english_rendering_is_deterministic(fields: dict[str, object], expected: str) -> None:
    claim = _claim(**fields)
    assert render_claim(claim, "en") == expected
    assert render_claim(_claim(**fields), "en") == expected
    assert grounded(claim, expected) is True
    assert grounded(claim, f"Review notes: {expected}. Confirmed by the owner.") is True


@pytest.mark.parametrize(
    "fields",
    [
        {"verb": "spearheaded"},
        {"subject": " padded"},
        {"subject": "two\nlines"},
        {"metric": {"value": "1,200", "unit": "count"}},
        {"metric": {"value": "5", "unit": "furlongs"}},
        {"period": {"start": "2022", "end": "2021"}},
        {"period": {"start": "2022-13", "end": "2023"}},
        {"sentence": "Led the billing migration"},
    ],
)
def test_structured_claim_rejects_values_outside_the_schema(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _claim(**fields)


def test_claim_text_renders_structure_and_keeps_literal_text() -> None:
    structured = {"claim": _claim().model_dump(mode="json", exclude_none=True)}
    assert claim_text(structured) == "Led the billing migration"
    assert claim_text({"text": "Maintained Linux services"}) == "Maintained Linux services"
    with pytest.raises(ValueError, match="claim_without_statement"):
        claim_text({"role": "Engineer"})
    assert needs_attention("experience", {"text": "Did things"}) is True
    assert needs_attention("experience", structured) is False
    assert needs_attention("skill", {"text": "SQL"}) is False


@pytest.mark.parametrize(
    ("fields", "excerpt"),
    [
        ({}, "  LED\tTHE\nBILLING   MIGRATION  "),
        ({}, "Ｌｅｄ ｔｈｅ ｂｉｌｌｉｎｇ ｍｉｇｒａｔｉｏｎ"),
        ({"subject": "the Straße migration"}, "Led the STRASSE migration"),
        (
            {"period": {"start": "2021-03", "end": "2022-04"}},
            "led the billing migration (mar 2021-apr 2022)",
        ),
        (
            {"subject": "the owner’s “self‑service” migration"},
            'Led the owner\'s "self-service" migration',
        ),
        (
            {"subject": 'the owner\'s "self-service" migration'},
            "Led the owner’s “self—service” migration",
        ),
    ],
)
def test_grounding_normalises_only_presentation(fields: dict[str, object], excerpt: str) -> None:
    assert grounded(_claim(**fields), excerpt) is True


@pytest.mark.parametrize("punctuation", [".", ";"])
def test_grounding_ignores_trailing_rendering_punctuation(punctuation: str) -> None:
    claim = _claim(subject=f"the billing migration{punctuation}")
    assert grounded(claim, "Led the billing migration") is True


@pytest.mark.parametrize("subject", ["the migration at Acme Inc.", "the U.S. migration"])
def test_abbreviations_ground_against_their_own_rendering(subject: str) -> None:
    claim = _claim(subject=subject)
    assert grounded(claim, render_claim(claim, "en")) is True


@pytest.mark.parametrize(
    ("fields", "excerpt"),
    [
        ({"subject": "the migration"}, "Assisted with the migration led by Alice"),
        ({"subject": "the migration"}, "Did not lead the migration"),
        ({"subject": "the migration"}, "Spearheaded the migration"),
        ({"verb": "managed", "subject": "the migration"}, "Led the migration"),
        ({"verb": "assisted", "subject": "the migration"}, "Helped with the migration"),
        ({}, "Led a billing migration"),
        ({}, "Led the migration for billing"),
        ({}, "Assisted with the billing migration;\nled the rollout"),
        (
            {
                "verb": "reduced",
                "subject": "batch runtime",
                "metric": {"value": "30", "unit": "percent", "change": "decrease"},
            },
            "Cut batch runtime by 30%",
        ),
        (
            {
                "verb": "reduced",
                "subject": "error rate",
                "metric": {"value": "30", "unit": "percent", "change": "decrease"},
            },
            "Reduced alerts, error rate increased by 30%",
        ),
        (
            {
                "verb": "reduced",
                "subject": "error rate",
                "metric": {"value": "30", "unit": "percent", "change": "decrease"},
            },
            "Did not reduce error rate by 30%",
        ),
        (
            {"metric": {"value": "1200", "unit": "count", "measure": "invoices"}},
            "Led the billing migration, reaching 11200 invoices",
        ),
        (
            {"metric": {"value": "1200", "unit": "count", "measure": "invoices"}},
            "Led the billing migration, reaching 1,200 invoices",
        ),
        (
            {"metric": {"value": "3", "unit": "count", "measure": "clients"}},
            "Led the billing migration, reaching 3M clients",
        ),
        (
            {"metric": {"value": "6", "unit": "hours", "per": "week"}},
            "Led the billing migration, reaching 6 hours per day",
        ),
        (
            {"period": {"start": "2021-03", "end": "2022-04"}},
            "Led the billing migration (May 2021–Dec 2022)",
        ),
        (
            {"period": {"start": "2021-03", "end": "2022-04"}},
            "Led the billing migration (Apr 2021–Mar 2022)",
        ),
        (
            {"period": {"start": "2021-03", "end": "2022-04"}},
            "Led the billing migration (2021-03 to 2022-04)",
        ),
        (
            {"period": {"start": "2022", "end": "2022"}},
            "Led the billing migration. Separately, cloud spend fell 30% in 2022.",
        ),
        (
            {"scope": "across four teams"},
            "Led the billing migration across five teams",
        ),
        ({}, ""),
    ],
)
def test_grounding_rejects_wording_and_factual_differences(
    fields: dict[str, object], excerpt: str
) -> None:
    assert grounded(_claim(**fields), excerpt) is False


@pytest.mark.parametrize(
    ("fields", "valid"),
    [
        (
            {"verb": "reduced", "metric": {"value": "30", "unit": "percent", "change": "increase"}},
            False,
        ),
        (
            {
                "verb": "reduced",
                "metric": {
                    "value": "30",
                    "unit": "percent",
                    "change": "increase",
                    "measure": "the billing migration",
                },
            },
            False,
        ),
        (
            {
                "verb": "reduced",
                "metric": {
                    "value": "30",
                    "unit": "percent",
                    "change": "increase",
                    "measure": "revenue",
                },
            },
            True,
        ),
        (
            {"verb": "increased", "metric": {"value": "2", "unit": "times", "change": "increase"}},
            True,
        ),
    ],
)
def test_metric_direction_must_not_contradict_the_verb(
    fields: dict[str, object], valid: bool
) -> None:
    if valid:
        _claim(**fields)
    else:
        with pytest.raises(ValidationError, match="metric_direction_contradicts_verb"):
            _claim(**fields)


def _structured_body(evidence_id: str, kind: str = "experience") -> dict[str, object]:
    return {
        "kind": kind,
        "payload": {
            "role": "Platform engineer",
            "organization": "Synthetic Logistics",
            "claim": {
                "schema_version": 1,
                "verb": "reduced",
                "subject": "batch runtime",
                "metric": {"value": "30", "unit": "percent", "change": "decrease"},
            },
        },
        "evidence_links": [
            {
                "evidence_revision_id": evidence_id,
                "support_kind": "user_asserted",
                "excerpt": "Reduced batch runtime by 30%",
            }
        ],
    }


@pytest.mark.postgres
def test_structured_claim_is_saved_without_a_sentence_and_rendered_on_read(
    profile_owner: IdentityContext,
) -> None:
    with profile_owner.client() as client:
        csrf = str(_json(_bootstrap(profile_owner, client))["csrf_token"])
        evidence = _mutate(
            client,
            profile_owner,
            csrf,
            "POST",
            "/evidence",
            1,
            {"payload": {"text": "Reduced batch runtime by 30%", "source": "Synthetic review"}},
        )
        evidence_id = _json(evidence)["evidence"][0]["revision"]["id"]
        saved = _mutate(
            client, profile_owner, csrf, "POST", "/claims", 2, _structured_body(evidence_id)
        )
        assert saved.status_code == 200, saved.text
        claim: dict[str, Any] = _json(saved)["current_revision"]["claims"][0]
        assert claim["rendering"] == "Reduced batch runtime by 30%"
        assert claim["needs_attention"] is False
        assert "text" not in claim["revision"]["payload"]
        history = _json(
            client.get(f"/api/v1/profile/revisions/{_json(saved)['current_revision']['id']}")
        )
        assert history["claims"][0]["rendering"] == "Reduced batch runtime by 30%"
        refused = _mutate(
            client,
            profile_owner,
            csrf,
            "POST",
            "/claims",
            3,
            _structured_body(evidence_id, "skill"),
        )
        assert refused.status_code == 422
        assert _json(client.get("/api/v1/profile"))["version"] == 3
