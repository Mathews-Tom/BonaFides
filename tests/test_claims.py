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
    within_a_year = _claim(period={"start": "2021-03", "end": "2021-05"})
    assert grounded(within_a_year, "Led the billing migration on 3/5/2021") is False
    assert grounded(within_a_year, "Led the billing migration 3/5/2021 – 5/20/2021") is True
    assert grounded(within_a_year, "Led the billing migration 2021-03-05 – 2021-05-20") is True
    assert grounded(within_a_year, "Led the billing migration 2021-03-2021-05") is True
    assert grounded(within_a_year, "Led the billing migration 2021-03-05-2021-05-20") is True
    assert grounded(within_a_year, "Led the billing migration 2021-03-05") is False
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


@pytest.mark.parametrize(
    ("verb", "excerpt", "expected"),
    [
        ("led", "Led the migration", True),
        ("led", "Spearheaded the migration", True),
        ("led", "Assisted with the migration", False),
        ("led", "Contributed to the migration", False),
        ("managed", "Led the migration", True),
        ("assisted", "Helped with the migration", True),
        ("assisted", "Led the migration", True),
        ("built", "Designed the migration", True),
        ("designed", "Built the migration", False),
    ],
)
def test_grounding_requires_the_claimed_or_a_stronger_verb(
    verb: str, excerpt: str, expected: bool
) -> None:
    assert grounded(_claim(verb=verb, subject="the migration"), excerpt) is expected


@pytest.mark.parametrize(
    ("excerpt", "expected"),
    [
        ("Reduced error rate by 30%", True),
        ("Cut error rate by 30%", True),
        ("Reduced error rate by 30%, increasing throughput by 2x", True),
        ("Reduced the dashboards; error rate increased by 30%", False),
        ("Reduced alerts, error rate increased by 30%", False),
        ("Reduced alerts while error rate rose by 30%", False),
        ("Did not reduce error rate by 30%", False),
        ("Reduced error rate, though not by 30%", False),
        ("Reduced error rate by 30% but it didn't last", False),
    ],
)
def test_grounding_requires_the_claimed_metric_direction(excerpt: str, expected: bool) -> None:
    claim = _claim(
        verb="reduced",
        subject="error rate",
        metric={"value": "30", "unit": "percent", "change": "decrease"},
    )
    assert grounded(claim, excerpt) is expected


def test_grounding_rejects_a_contrary_saving_or_improvement() -> None:
    saving = _claim(
        subject="the migration",
        metric={"value": "1.2", "unit": "usd", "magnitude": "million", "change": "saving"},
    )
    assert grounded(saving, "Led the migration, saving $1.2M") is True
    assert grounded(saving, "Led the migration, which lost $1.2M") is False
    improvement = _claim(
        verb="improved",
        subject="test coverage",
        metric={"value": "25", "unit": "percent", "change": "improvement"},
    )
    assert grounded(improvement, "Improved test coverage by 25%") is True
    assert grounded(improvement, "Improved test coverage, then it degraded by 25%") is False


def test_grounding_takes_every_part_from_one_clause() -> None:
    reduction = _claim(
        verb="reduced",
        subject="the billing migration",
        metric={"value": "30", "unit": "percent", "change": "decrease"},
    )
    excerpt = "Led the billing migration. Separately, cloud spend fell 30% in 2022."
    assert grounded(reduction, excerpt) is False
    assert grounded(_claim(period={"start": "2022", "end": "2022"}), excerpt) is False
    assert grounded(_claim(period={"start": "2022", "end": "2022"}), excerpt.replace(".", ",", 1))
    assert grounded(_claim(), "Assisted the team; led the billing migration") is True
    assert grounded(_claim(), "Assisted with the billing migration;\nled the rollout") is False
    money = _claim(metric={"value": "1.2", "unit": "usd", "magnitude": "million"})
    assert grounded(money, "Led the billing migration for $1.2M. Done.") is True


@pytest.mark.parametrize(
    ("value", "excerpt", "expected"),
    [
        ("30", "Reduced incidents by 30", True),
        ("30", "Reduced incidents by 30 in total", True),
        ("30", "Reduced incidents by 30%", False),
        ("30", "Reduced incidents by 30 percent", False),
        ("30", "Reduced incidents by 30 hours", False),
        ("30", "Reduced incidents by 30x", False),
        ("3", "Reduced incidents by 3 in Q3", True),
        ("3", "Reduced incidents in Q3", False),
        ("3", "Reduced incidents on 3/5/2021", False),
        ("5", "Reduced incidents on 3/5/2021", False),
        ("3", "Reduced incidents by 3.5", False),
    ],
)
def test_grounding_counts_only_a_bare_number(value: str, excerpt: str, expected: bool) -> None:
    claim = _claim(
        verb="reduced",
        subject="incidents",
        metric={"value": value, "unit": "count", "change": "decrease"},
    )
    assert grounded(claim, excerpt) is expected


@pytest.mark.parametrize(
    ("excerpt", "expected"),
    [
        ("Led the migration for $30", True),
        ("Led the migration for US$30", True),
        ("Led the migration for USD 30", True),
        ("Led the migration for A$30", False),
        ("Led the migration for C$30", False),
        ("Led the migration for AUS$30", False),
    ],
)
def test_grounding_reads_only_us_dollar_prefixes_as_usd(excerpt: str, expected: bool) -> None:
    claim = _claim(subject="the migration", metric={"value": "30", "unit": "usd"})
    assert grounded(claim, excerpt) is expected
