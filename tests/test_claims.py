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


def test_grounding_requires_every_literal_part_in_the_excerpt() -> None:
    claim = _claim(
        scope="across four teams",
        metric={"value": "1200", "unit": "count", "measure": "invoices"},
        period={"start": "2021", "end": "2022"},
    )
    excerpt = "Spearheaded the billing migration across four teams for 1,200 invoices in 2021-2022"
    assert grounded(claim, excerpt) is True
    assert grounded(claim, excerpt.replace("four", "five")) is False
    assert grounded(claim, excerpt.replace("1,200", "11,200")) is False
    assert grounded(claim, excerpt.replace("2022", "2023")) is False
    assert grounded(_claim(), "led THE BILLING MIGRATION") is True


@pytest.mark.parametrize(
    ("metric", "quoted", "unquoted"),
    [
        ({"value": "30", "unit": "percent"}, "by 30% overall", "by 30 seconds overall"),
        ({"value": "30", "unit": "percent"}, "by 30 percent", "by 30.5% overall"),
        ({"value": "1.2", "unit": "usd", "magnitude": "million"}, "saving $1.2M", "saving $1.2K"),
        ({"value": "6", "unit": "hours", "per": "week"}, "6 hours per week", "6 hours per day"),
        ({"value": "3", "unit": "times"}, "grew 3x", "grew 3 teams"),
        ({"value": "3", "unit": "count"}, "for 3 clients", "for 3M clients"),
        ({"value": "3", "unit": "count"}, "for 3 monthly clients", "for 3 billion clients"),
        (
            {"value": "6", "unit": "hours", "per": "week"},
            "6 hours weekly",
            "6 hours per day; weekly reporting",
        ),
    ],
)
def test_grounding_requires_the_complete_metric(
    metric: dict[str, object], quoted: str, unquoted: str
) -> None:
    claim = _claim(subject="the migration", metric=metric)
    assert grounded(claim, f"Led the migration {quoted}") is True
    assert grounded(claim, f"Led the migration {unquoted}") is False


def test_grounding_checks_months_when_the_period_supplies_them() -> None:
    claim = _claim(period={"start": "2021-03", "end": "2022-04"})
    assert grounded(claim, "Led the billing migration (Mar 2021 – April 2022)") is True
    assert grounded(claim, "Led the billing migration (2021-03 to 2022-04)") is True
    assert grounded(claim, "Led the billing migration (May 2021 – Dec 2022)") is False
    assert grounded(claim, "Led the billing migration (April 2021 – March 2022)") is False
    assert grounded(claim, "Led the billing migration (3/2021 – 04/2022)") is True
    assert grounded(claim, "Led the billing migration (March 5, 2021 – April 30, 2022)") is True
    assert grounded(claim, "Led the billing migration (5 March 2021 – 30th April 2022)") is True
    assert grounded(claim, "Led the billing migration (2021-03-05 to 2022-04-30)") is True
    assert grounded(claim, "Led the billing migration (3/5/2021 – 4/30/2022)") is True
    assert grounded(claim, "Led the billing migration (April 5, 2021 – March 30, 2022)") is False
    assert grounded(
        _claim(period={"start": "2021", "end": "2022"}), "Led the billing migration 2021-2022"
    )


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
                "excerpt": "Cut batch runtime by 30%",
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
            {"payload": {"text": "Cut batch runtime by 30%", "source": "Synthetic review"}},
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
