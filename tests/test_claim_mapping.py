# SPDX-License-Identifier: MIT OR Apache-2.0
"""Résumé lines map deterministically to claim proposals; unplaced parts are flagged."""

from __future__ import annotations

import pytest

from bonafides.claim_mapping import _PARTICIPLES, _VERB_PHRASES, map_line
from bonafides.claims import OWNERSHIP_VERBS, grounded


@pytest.mark.parametrize(
    ("line", "rendering"),
    [
        (
            "- Reduced checkout latency by 35% across 4 regions",
            "Reduced checkout latency by 35% across 4 regions",
        ),
        (
            "• Led vendor consolidation, saving $1.2M per year",
            "Led vendor consolidation, saving $1.2M per year",
        ),
        (
            "Built an internal reporting dashboard, reducing manual reconciliation by 6 hours per week",
            "Built an internal reporting dashboard, reducing manual reconciliation by 6 hours per week",
        ),
        (
            "Led migration of 40 services to Kubernetes for the payments team (2021-2022)",
            "Led migration of 40 services to Kubernetes for the payments team (2021–2022)",
        ),
        ("Increased revenue by 3x (2020).", "Increased revenue by 3x (2020)"),
        ("Contributed to the open-source scheduler", "Contributed to the open-source scheduler"),
        (
            "Improved test coverage by 25% through contract tests",
            "Improved test coverage by 25% through contract tests",
        ),
        ("Increased weekly active users by 12,000", "Increased weekly active users by 12,000"),
        (
            "- Delivered $3.4M in new enterprise contracts in 2021",
            "Delivered $3.4M in new enterprise contracts (2021)",
        ),
        (
            "Shipped 99.95% uptime for the core API (2022)",
            "Delivered 99.95% uptime for the core API (2022)",
        ),
        (
            "Increased order throughput by 20% during peak season",
            "Increased order throughput by 20% during peak season",
        ),
        (
            "Hardened container images for the production cluster",
            "Improved container images for the production cluster",
        ),
    ],
)
def test_representable_lines_map_to_grounded_claims(line: str, rendering: str) -> None:
    mapping = map_line(line)
    assert mapping.representable, mapping.unparsed
    assert mapping.rendering == rendering
    assert mapping.claim is not None and grounded(mapping.claim, line)
    assert map_line(line) == mapping


@pytest.mark.parametrize(
    "line",
    [
        "Spearheaded vendor consolidation, saving $1.2M annually",
        "Grew revenue by 3x in 2020.",
        "Cut cloud spend by 30%",
        "Reduced latency by 30 percent",
        "Led migration for checkout in 2020",
    ],
)
def test_reworded_proposals_need_attention_instead_of_claiming_grounding(line: str) -> None:
    mapping = map_line(line)
    assert mapping.claim is not None
    assert mapping.rendering is not None
    assert not mapping.representable
    assert not grounded(mapping.claim, line)
    assert [(part.text, part.reason) for part in mapping.unparsed] == [(line, "unquoted_rendering")]


@pytest.mark.parametrize(
    ("line", "unparsed"),
    [
        ("Saved $200K in licensing costs", [("Saved", "unknown_verb")]),
        (
            "Designed onboarding flow; cut churn by 10%",
            [("cut churn by 10%", "multiple_statements")],
        ),
        (
            "Built invoice matching, cutting errors by 40% and increasing throughput by 2x",
            [("and increasing throughput by 2x", "additional_metric")],
        ),
        ("Led", [("Led", "missing_subject")]),
        ("Managed a $2M annual marketing budget", [("$2M", "additional_metric")]),
        ("Hired 5 engineers for the platform team", [("Hired", "unknown_verb")]),
        ("Raised $75K for the food bank", [("$75K", "additional_metric")]),
    ],
)
def test_unplaced_parts_are_flagged_not_dropped(line: str, unparsed: list[tuple[str, str]]) -> None:
    mapping = map_line(line)
    assert not mapping.representable
    assert [(part.text, part.reason) for part in mapping.unparsed] == unparsed


def test_partial_mapping_keeps_the_claim_beside_its_flags() -> None:
    mapping = map_line("Designed onboarding flow; cut churn by 10%")
    assert mapping.claim is not None and mapping.claim.verb == "designed"
    assert mapping.rendering == "Designed onboarding flow"


@pytest.mark.parametrize(
    "phrase",
    [
        "Proposed",
        "Hired",
        "Ran",
        "Championed",
        "Drove",
        "Presented",
        "Trained",
        "Negotiated",
        "Closed",
        "Secured",
        "Won",
        "Fixed",
        "Deployed",
        "Scaled",
        "Prioritized",
        "Answered",
        "Automated",
        "Published",
        "Planned",
    ],
)
def test_a_verb_with_another_meaning_stays_unknown(phrase: str) -> None:
    mapping = map_line(f"{phrase} the quarterly vendor review")
    assert mapping.claim is None
    assert [(part.text, part.reason) for part in mapping.unparsed] == [(phrase, "unknown_verb")]


@pytest.mark.parametrize("phrase", sorted(_VERB_PHRASES))
def test_vocabulary_verbs_keep_their_meaning_without_grounding_synonyms(phrase: str) -> None:
    line = f"{phrase.capitalize()} the quarterly vendor review by 20%"
    mapping = map_line(line)
    assert mapping.claim is not None, phrase
    if phrase in OWNERSHIP_VERBS:
        assert mapping.claim.verb == phrase
    if grounded(mapping.claim, line):
        assert not any(part.reason == "unquoted_rendering" for part in mapping.unparsed)
    else:
        assert not mapping.representable, phrase
    assert grounded(mapping.claim, mapping.rendering or ""), phrase


@pytest.mark.parametrize("participle", sorted(_PARTICIPLES))
def test_every_participle_obeys_literal_grounding(participle: str) -> None:
    line = f"Built a dashboard, {participle} review speed by 20%"
    mapping = map_line(line)
    assert mapping.claim is not None
    assert mapping.representable == (participle in {"reducing", "increasing", "improving"})
    assert grounded(mapping.claim, line) == mapping.representable
    if not mapping.representable:
        assert [(part.text, part.reason) for part in mapping.unparsed] == [
            (line, "unquoted_rendering")
        ]


def test_oversaw_and_orchestrated_map_at_or_below_their_meaning() -> None:
    assert _claim_verb("Oversaw the quarterly vendor review") == "managed"
    assert _claim_verb("Orchestrated the quarterly vendor review") == "coordinated"
    assert _claim_verb("Launched the quarterly vendor review") == "delivered"


def _claim_verb(line: str) -> str | None:
    claim = map_line(line).claim
    return None if claim is None else claim.verb


def test_cut_maps_to_reduced_only_before_a_metric() -> None:
    assert _claim_verb("Cut cloud spend by 30%") == "reduced"
    release = map_line("Cut the 2.0 release")
    assert release.claim is None
    assert [(part.text, part.reason) for part in release.unparsed] == [("Cut", "unknown_verb")]


def test_a_scope_split_by_the_metric_is_flagged_not_joined() -> None:
    line = "Reduced latency across 12 services by 40% for checkout"
    mapping = map_line(line)
    assert mapping.claim is not None and mapping.claim.scope == "across 12 services"
    assert [(part.text, part.reason) for part in mapping.unparsed] == [
        ("for checkout", "unplaced_scope")
    ]
    assert not mapping.representable
    assert not grounded(mapping.claim, line)
