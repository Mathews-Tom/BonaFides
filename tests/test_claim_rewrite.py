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
UNMAPPABLE = "Saved $90K in synthetic licensing costs"


def _literal(evidence_id: str, statement: str) -> dict[str, object]:
    return {
        "kind": "experience",
        "payload": {"role": "Platform engineer", "text": statement},
        "evidence_links": [
            {
                "evidence_revision_id": evidence_id,
                "support_kind": "direct",
                "excerpt": statement,
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
        evidence = _mutate(
            client,
            profile_owner,
            csrf,
            "POST",
            "/evidence",
            1,
            {"payload": {"text": f"{MAPPABLE}. {UNMAPPABLE}.", "source": "Synthetic review"}},
        )
        evidence_id = _json(evidence)["evidence"][0]["revision"]["id"]
        for version, statement in ((2, MAPPABLE), (3, UNMAPPABLE)):
            saved = _mutate(
                client,
                profile_owner,
                csrf,
                "POST",
                "/claims",
                version,
                _literal(evidence_id, statement),
            )
            assert saved.status_code == 200, saved.text
        before = _json(client.get("/api/v1/profile"))
        assert _summary(before) == [(MAPPABLE, True), (UNMAPPABLE, True)]

        with profile_owner.admin.transaction() as conn:
            assert restructure_free_text_claims(conn, owner_id) == 1
        with profile_owner.admin.transaction() as conn:
            assert restructure_free_text_claims(conn, owner_id) == 0

        after = _json(client.get("/api/v1/profile"))
        assert after["version"] == before["version"] + 1
        assert _summary(after) == [(MAPPABLE, False), (UNMAPPABLE, True)]
        rewritten = after["current_revision"]["claims"][0]["revision"]
        assert "text" not in rewritten["payload"]
        assert rewritten["payload"]["role"] == "Platform engineer"
        assert rewritten["payload"]["claim"]["verb"] == "reduced"
        assert rewritten["evidence_links"][0]["excerpt"] == MAPPABLE
        assert after["current_revision"]["reason"] == RESTRUCTURE_REASON

        historical = _json(
            client.get(f"/api/v1/profile/revisions/{before['current_revision']['id']}")
        )
        assert [item["rendering"] for item in historical["claims"]] == [MAPPABLE, UNMAPPABLE]
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
        assert _summary(_json(restored)) == [(MAPPABLE, True), (UNMAPPABLE, True)]
        assert "text" in _json(restored)["current_revision"]["claims"][0]["revision"]["payload"]


@pytest.mark.parametrize(
    ("statement", "rewritten"),
    [
        ("Reduced batch runtime by 30% across 3 warehouses.", True),
        ("Hired 4 engineers for the platform team", False),
        ("Proposed a new on-call rotation", False),
        ("Prioritized the quarterly roadmap", False),
        ("Spearheaded vendor consolidation", False),
        ("Led migration of 40 services to Kubernetes (2021-2022)", False),
    ],
)
def test_a_free_text_claim_is_rewritten_only_when_its_rendering_is_its_own_text(
    statement: str, rewritten: bool
) -> None:
    assert (_restructurable(statement) is not None) is rewritten


def test_the_frozen_rewrite_imports_no_live_application_module() -> None:
    source = Path(structured_claims_v1.__file__).read_text(encoding="utf-8")
    imported = {
        alias.name if isinstance(node, ast.Import) else str(node.module)
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    }
    assert not any(name.split(".")[0] == "bonafides" for name in imported), imported
