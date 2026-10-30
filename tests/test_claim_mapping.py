# SPDX-License-Identifier: MIT OR Apache-2.0
"""Résumé lines map deterministically to claim proposals; unplaced parts are flagged."""

from __future__ import annotations

import pytest

from bonafides.claim_mapping import map_line
from bonafides.claims import grounded


@pytest.mark.parametrize(
    ("line", "rendering"),
    [
        (
            "- Reduced checkout latency by 35% across 4 regions",
            "Reduced checkout latency by 35% across 4 regions",
        ),
        (
            "• Spearheaded vendor consolidation, saving $1.2M annually",
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
        ("Grew revenue by 3x in 2020.", "Increased revenue by 3x (2020)"),
        ("Contributed to the open-source scheduler", "Contributed to the open-source scheduler"),
        (
            "Improved test coverage by 25% through contract tests",
            "Improved test coverage by 25% through contract tests",
        ),
        ("Increased weekly active users by 12,000", "Increased weekly active users by 12,000"),
    ],
)
def test_representable_lines_map_to_grounded_claims(line: str, rendering: str) -> None:
    mapping = map_line(line)
    assert mapping.representable, mapping.unparsed
    assert mapping.rendering == rendering
    assert mapping.claim is not None and grounded(mapping.claim, line)
    assert map_line(line) == mapping


@pytest.mark.parametrize(
    ("line", "unparsed"),
    [
        ("Saved $200K in licensing costs", [("Saved", "unknown_verb")]),
        (
            "Designed onboarding flow; cut churn by 10%",
            [("cut churn by 10%", "multiple_statements")],
        ),
        (
            "Automated invoice matching, cutting errors by 40% and increasing throughput by 2x",
            [("and increasing throughput by 2x", "additional_metric")],
        ),
        ("Led", [("Led", "missing_subject")]),
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
