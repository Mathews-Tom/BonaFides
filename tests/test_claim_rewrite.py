# SPDX-License-Identifier: MIT OR Apache-2.0
"""Free-text claims are rewritten in place as structured claims; history and restore survive."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from test_identity import IdentityContext, _bootstrap
from test_identity import identity as identity
from test_profiles import _json, _mutate
from test_profiles import profile_owner as profile_owner

from bonafides.frozen_migrations import structured_claims_v1
from bonafides.frozen_migrations.structured_claims_v1 import (
    RESTRUCTURE_REASON,
    _restructurable,
    restructure_free_text_claims,
)

MAPPABLE = "Reduced batch runtime by 30% across 3 warehouses"
UNMAPPABLE = "Successfully saved $90K in synthetic licensing costs"


def _literal(evidence_id: str, statement: str, excerpt: str | None = None) -> dict[str, object]:
    return {
        "kind": "experience",
        "payload": {"role": "Platform engineer", "text": statement},
        "evidence_links": [
            {
                "evidence_revision_id": evidence_id,
                "support_kind": "direct",
                "excerpt": statement if excerpt is None else excerpt,
            }
        ],
    }


def _summary(profile: dict[str, Any]) -> list[tuple[str, bool]]:
    return [
        (item["rendering"], item["needs_attention"])
        for item in profile["current_revision"]["claims"]
    ]


@pytest.mark.postgres
def test_free_text_claims_are_rewritten_in_place_and_restore_brings_back_the_text(
    profile_owner: IdentityContext,
) -> None:
    with profile_owner.client() as client:
        boot = _json(_bootstrap(profile_owner, client))
        csrf, owner_id = str(boot["csrf_token"]), UUID(str(boot["owner_id"]))
        statements = (
            (MAPPABLE, MAPPABLE),
            (UNMAPPABLE, UNMAPPABLE),
            ("Hired 4 engineers for the platform team", "Hired 4 engineers for the platform team"),
            ("Proposed a new on-call rotation", "Proposed a new on-call rotation"),
            (MAPPABLE, "Cut batch runtime by 30% across 3 warehouses"),
            (MAPPABLE + ".", MAPPABLE + "."),
            ("Implemented a new on-call rotation", "Implemented a new on-call rotation"),
        )
        literal_summary = [(statement, True) for statement, _ in statements]
        evidence = _mutate(
            client,
            profile_owner,
            csrf,
            "POST",
            "/evidence",
            1,
            {
                "payload": {
                    "text": ". ".join(excerpt for _, excerpt in statements),
                    "source": "Synthetic review",
                }
            },
        )
        evidence_id = _json(evidence)["evidence"][0]["revision"]["id"]
        for version, (statement, excerpt) in enumerate(statements, start=2):
            saved = _mutate(
                client,
                profile_owner,
                csrf,
                "POST",
                "/claims",
                version,
                _literal(evidence_id, statement, excerpt),
            )
            assert saved.status_code == 200, saved.text
        before = _json(client.get("/api/v1/profile"))
        assert _summary(before) == literal_summary

        with profile_owner.admin.transaction() as conn:
            assert restructure_free_text_claims(conn, owner_id) == 2
        with profile_owner.admin.transaction() as conn:
            assert restructure_free_text_claims(conn, owner_id) == 0

        after = _json(client.get("/api/v1/profile"))
        assert after["version"] == before["version"] + 1
        assert _summary(after) == [
            (statement, index in {1, 2, 3, 4, 5}) for index, (statement, _) in enumerate(statements)
        ]
        assert after["current_revision"]["claims"][1:6] == before["current_revision"]["claims"][1:6]
        implemented = after["current_revision"]["claims"][6]["revision"]
        assert implemented["payload"]["claim"]["verb"] == "implemented"
        assert implemented["evidence_links"][0]["excerpt"] == statements[6][0]
        rewritten = after["current_revision"]["claims"][0]["revision"]
        assert "text" not in rewritten["payload"]
        assert rewritten["payload"]["role"] == "Platform engineer"
        assert rewritten["payload"]["claim"]["verb"] == "reduced"
        assert rewritten["evidence_links"][0]["excerpt"] == MAPPABLE
        assert after["current_revision"]["reason"] == RESTRUCTURE_REASON

        historical = _json(
            client.get(f"/api/v1/profile/revisions/{before['current_revision']['id']}")
        )
        assert [item["rendering"] for item in historical["claims"]] == [
            statement for statement, _ in statements
        ]
        restored = _mutate(
            client,
            profile_owner,
            csrf,
            "POST",
            "/profile/restore",
            after["version"],
            {"revision_id": before["current_revision"]["id"]},
        )
        assert restored.status_code == 200, restored.text
        assert _summary(_json(restored)) == literal_summary
        assert "text" in _json(restored)["current_revision"]["claims"][0]["revision"]["payload"]


@pytest.mark.parametrize(
    ("statement", "rewritten"),
    [
        (MAPPABLE, True),
        (MAPPABLE + ".", False),
        (MAPPABLE + ";", False),
        (" " + MAPPABLE, False),
        (MAPPABLE + " ", False),
        ("- " + MAPPABLE, False),
        ("Hired 4 engineers for the platform team", False),
        ("Proposed a new on-call rotation", False),
        ("Successfully implemented a new on-call rotation", False),
        ("Implemented a new on-call rotation", True),
        ("Prioritized the quarterly roadmap", False),
        ("Spearheaded vendor consolidation", True),
        ("Led migration of 40 services to Kubernetes (2021-2022)", True),
        ("Implemented a new on-call rotation in 2022", True),
        ("Implemented a new on-call rotation 2021—2022", True),
        ("Reduced runtime by 12% each year", True),
        ("Reduced runtime by 12% annually", True),
    ],
)
def test_a_free_text_claim_is_rewritten_only_when_its_rendering_is_its_own_text(
    statement: str, rewritten: bool
) -> None:
    assert (_restructurable(statement) is not None) is rewritten


@pytest.mark.parametrize(
    ("excerpt", "expected"),
    [
        (MAPPABLE, True),
        ("Owner source: " + MAPPABLE + ".", True),
        ("REDUCED BATCH RUNTIME BY 30% ACROSS 3 WAREHOUSES", True),
        ("Reduced  batch runtime\nby 30% across 3 warehouses", True),
        ("Ｒｅｄｕｃｅｄ batch runtime by 30% across 3 warehouses", True),
        ("Cut batch runtime by 30% across 3 warehouses", False),
        ("Led batch runtime by 30% across 3 warehouses", False),
        ("Reduced batch runtime by 30 percent across 3 warehouses", False),
        ("Reduced batch runtime by 30%; across 3 warehouses", False),
        ("Reduced batch runtime by 30% across 3 warehouses_extra", True),
        ("Reduced batch runtime by 30% across 3 warehouseswide", False),
        ("Unreduced batch runtime by 30% across 3 warehouses", False),
    ],
)
def test_frozen_grounding_requires_the_complete_rendering(excerpt: str, expected: bool) -> None:
    claim = _restructurable(MAPPABLE)
    assert claim is not None
    assert structured_claims_v1.grounded(claim, excerpt) is expected


@pytest.mark.parametrize(
    "excerpt",
    [
        "Built system for 30",
        "Built system for 30%",
        "Built system for 30.5",
        "Built system for 30€",
    ],
)
def test_frozen_grounding_preserves_numeric_phrase_boundaries(excerpt: str) -> None:
    claim = structured_claims_v1.StructuredClaim(
        schema_version=1, verb="built", subject="system for 30"
    )
    assert structured_claims_v1.grounded(claim, excerpt) is (excerpt == "Built system for 30")


def test_the_frozen_rewrite_imports_no_live_application_module() -> None:
    source = Path(structured_claims_v1.__file__).read_text(encoding="utf-8")
    imported = {
        alias.name if isinstance(node, ast.Import) else str(node.module)
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    }
    assert not any(name.split(".")[0] == "bonafides" for name in imported), imported


@pytest.mark.parametrize(
    "statement",
    [
        "Implemented a new on-call rotation",
        "Hired 4 engineers for the platform team",
        "Proposed a new on-call rotation",
        "Successfully implemented a new on-call rotation",
        "Led migration of 40 services to Kubernetes (2021-2022)",
        "Led migration of 40 services to Kubernetes (2021–2022)",
        "Implemented a new on-call rotation in 2022",
        "Implemented a new on-call rotation 2021—2022",
        "Reduced runtime by 12% per year",
        "Reduced runtime by 12% a year",
        "Reduced runtime by 12% each year",
        "Reduced runtime by 12% every year",
        "Reduced runtime by 12% yearly",
        "Reduced runtime by 12% annually",
        "Reduced runtime by 12% annually.",
        "Increased 12 service quotas",
    ],
)
def test_release_frozen_mapper_matches_live_schema_rendering_and_grounding(statement: str) -> None:
    from bonafides.claim_mapping import map_line
    from bonafides.claims import grounded, render_claim

    live = map_line(statement)
    frozen = structured_claims_v1.map_line(statement)
    assert frozen.model_dump(mode="json") == live.model_dump(mode="json")
    if live.claim is None:
        return
    assert frozen.claim is not None
    rendering = render_claim(live.claim)
    assert structured_claims_v1.render_claim(frozen.claim) == rendering
    assert (_restructurable(statement) is not None) is (
        live.representable and rendering == statement
    )
    for excerpt in (statement, "Owner source: " + statement, "Unrelated source"):
        assert structured_claims_v1.grounded(frozen.claim, excerpt) == grounded(live.claim, excerpt)


@pytest.mark.parametrize(
    ("statement", "field", "expected"),
    [
        ("Reduced runtime by 12% each year", "metric", {"rate_wording": "each", "per": "year"}),
        ("Reduced runtime by 12% annually", "metric", {"rate_wording": "annual", "per": "year"}),
        ("Reduced runtime by 12% yearly", "metric", {"rate_wording": "adverbial", "per": "year"}),
        (
            "Implemented a new on-call rotation in 2022",
            "period",
            {"start": "2022", "end": "2022", "style": "in_year"},
        ),
        (
            "Implemented a new on-call rotation 2021—2022",
            "period",
            {"start": "2021", "end": "2022", "style": "range", "separator": "em_dash"},
        ),
        (
            "Led migration of 40 services to Kubernetes (2021-2022)",
            "period",
            {"start": "2021", "end": "2022", "separator": "hyphen"},
        ),
    ],
)
def test_frozen_rewrite_retains_exact_period_and_rate_metadata(
    statement: str, field: str, expected: dict[str, str]
) -> None:
    claim = _restructurable(statement)
    assert claim is not None
    payload = claim.model_dump(mode="json")
    assert all(payload[field][key] == value for key, value in expected.items())
    assert structured_claims_v1.render_claim(claim) == statement
    assert structured_claims_v1.grounded(claim, statement)
    assert not structured_claims_v1.grounded(claim, "Implemented something unrelated")


@pytest.mark.parametrize(
    "parts",
    [
        {"metric": {"value": "12", "unit": "count", "rate_wording": "each"}},
        {"metric": {"value": "12", "unit": "count", "per": "month", "rate_wording": "annual"}},
        {"metric": {"value": "12", "unit": "count", "per": "year", "rate_wording": "hourly"}},
        {"period": {"start": "2021", "end": "2022", "style": "in_year"}},
        {"period": {"start": "2022-01", "end": "2022-01", "style": "in_year"}},
        {"period": {"start": "2022", "end": "2022", "separator": "slash"}},
    ],
)
def test_frozen_new_metadata_validation_matches_live(parts: dict[str, object]) -> None:
    from pydantic import ValidationError

    from bonafides.claims import StructuredClaim

    payload = {"schema_version": 1, "verb": "delivered", "subject": "releases", **parts}
    for model in (StructuredClaim, structured_claims_v1.StructuredClaim):
        with pytest.raises(ValidationError):
            model.model_validate(payload)


def test_frozen_release_vocabulary_and_defaults_match_live() -> None:
    from bonafides.claims import StructuredClaim, vocabulary

    assert structured_claims_v1.vocabulary() == vocabulary()
    payload = {
        "schema_version": 1,
        "verb": "delivered",
        "subject": "releases",
        "metric": {"value": "12", "unit": "count", "per": "year"},
        "period": {"start": "2021", "end": "2022"},
    }
    live = StructuredClaim.model_validate(payload)
    frozen = structured_claims_v1.StructuredClaim.model_validate(payload)
    assert frozen.model_dump(mode="json") == live.model_dump(mode="json")
    assert structured_claims_v1.render_claim(frozen) == (
        "Delivered releases, reaching 12 per year (2021–2022)"
    )
