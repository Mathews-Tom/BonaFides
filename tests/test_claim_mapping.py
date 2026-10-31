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
            "Led migration of 40 services to Kubernetes for the payments team (2021-2022)",
        ),
        ("Increased revenue by 3x (2020).", "Increased revenue by 3x (2020)"),
        ("Contributed to the open-source scheduler", "Contributed to the open-source scheduler"),
        (
            "Improved test coverage by 25% through contract tests",
            "Improved test coverage by 25% through contract tests",
        ),
        ("Increased weekly active users by 12000", "Increased weekly active users by 12000"),
        ("Increased weekly active users by 12,000", "Increased weekly active users by 12,000"),
        (
            "- Delivered $3.4M in new enterprise contracts (2021)",
            "Delivered $3.4M in new enterprise contracts (2021)",
        ),
        (
            "Delivered 99.95% uptime for the core API (2022)",
            "Delivered 99.95% uptime for the core API (2022)",
        ),
        (
            "Increased order throughput by 20% during peak season",
            "Increased order throughput by 20% during peak season",
        ),
        (
            "Improved container images for the production cluster",
            "Improved container images for the production cluster",
        ),
        (
            "Spearheaded vendor consolidation, saving $1.2M annually",
            "Spearheaded vendor consolidation, saving $1.2M annually",
        ),
        ("Grew revenue by 3x in 2020.", "Grew revenue by 3x in 2020"),
        ("Cut cloud spend by 30%", "Cut cloud spend by 30%"),
        ("Led migration for checkout in 2020", "Led migration for checkout in 2020"),
        (
            "Delivered $3.4M in new enterprise contracts in 2021",
            "Delivered $3.4M in new enterprise contracts in 2021",
        ),
        (
            "Shipped 99.95% uptime for the core API (2022)",
            "Shipped 99.95% uptime for the core API (2022)",
        ),
        (
            "Hardened container images for the production cluster",
            "Hardened container images for the production cluster",
        ),
        ("Saved $200K in licensing costs", "Saved $200K in licensing costs"),
        ("Raised $75K for the food bank", "Raised $75K for the food bank"),
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
        "Reduced latency by 30 percent",
        "Led migration (2021-03)",
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
        "Closed",
        "Secured",
        "Won",
        "Prioritized",
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


def test_distinct_source_actions_map_to_their_own_keys() -> None:
    assert _claim_verb("Oversaw the quarterly vendor review") == "oversaw"
    assert _claim_verb("Orchestrated the quarterly vendor review") == "orchestrated"
    assert _claim_verb("Launched the quarterly vendor review") == "launched"


def _claim_verb(line: str) -> str | None:
    claim = map_line(line).claim
    return None if claim is None else claim.verb


def test_cut_keeps_its_own_key_only_before_a_metric() -> None:
    assert _claim_verb("Cut cloud spend by 30%") == "cut"
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


@pytest.mark.parametrize(
    ("line", "flagged"),
    [
        ("Designed and implemented a self-service onboarding flow", "Designed and implemented"),
        ("Led and grew the platform team", "Led and grew"),
        ("Built and ran the API", "Built and ran"),
        ("Built and run the API", "Built and run"),
        ("Led and taught the platform team", "Led and taught"),
        ("Led and took the platform team", "Led and took"),
    ],
)
def test_a_compound_verb_is_flagged_not_folded_into_the_subject(line: str, flagged: str) -> None:
    mapping = map_line(line)
    assert mapping.claim is None
    assert [(part.text, part.reason) for part in mapping.unparsed] == [(flagged, "compound_verb")]


@pytest.mark.parametrize("verb", OWNERSHIP_VERBS)
def test_every_inventory_action_preserves_its_source_words(verb: str) -> None:
    phrase = verb.replace("_", " ").capitalize()
    tail = " by 20%" if verb in {"cut", "slashed", "trimmed"} else ""
    line = f"{phrase} the release{tail}"
    mapping = map_line(line)
    assert mapping.representable, mapping.unparsed
    assert mapping.claim is not None and mapping.claim.verb == verb
    assert mapping.rendering == line
    assert grounded(mapping.claim, line)


@pytest.mark.parametrize(
    ("suffix", "period"),
    [
        ("in 2022", {"start": "2022", "end": "2022", "style": "in_year"}),
        ("(2022)", {"start": "2022", "end": "2022", "style": "parenthetical"}),
        (
            "(2021-2022)",
            {"start": "2021", "end": "2022", "style": "parenthetical", "separator": "hyphen"},
        ),
        (
            "2021–2022",
            {"start": "2021", "end": "2022", "style": "range", "separator": "en_dash"},
        ),
    ],
)
def test_period_form_is_stored_and_reproduced(suffix: str, period: dict[str, str]) -> None:
    line = f"Implemented access control {suffix}"
    mapping = map_line(line)
    assert mapping.representable
    assert mapping.rendering == line
    assert mapping.claim is not None and mapping.claim.period is not None
    assert mapping.claim.period.model_dump(exclude_none=True) == period


@pytest.mark.parametrize(
    ("wording", "key"),
    [
        ("per year", "per"),
        ("a year", "a"),
        ("an year", "an"),
        ("each year", "each"),
        ("every year", "every"),
        ("yearly", "adverbial"),
        ("annually", "annual"),
    ],
)
def test_rate_form_is_stored_and_reproduced(wording: str, key: str) -> None:
    line = f"Led vendor consolidation, saving $200K {wording}"
    mapping = map_line(line)
    assert mapping.representable, mapping.unparsed
    assert mapping.rendering == line
    assert mapping.claim is not None and mapping.claim.metric is not None
    assert mapping.claim.metric.per == "year"
    assert mapping.claim.metric.rate_wording == key


@pytest.mark.parametrize(
    "line", ["Successfully implemented access control", "Hired engineers", "Proposed a migration"]
)
def test_leading_adverbs_and_unlisted_actions_remain_flagged(line: str) -> None:
    mapping = map_line(line)
    assert not mapping.representable
    assert mapping.claim is None
    assert mapping.unparsed[0].reason == "unknown_verb"
