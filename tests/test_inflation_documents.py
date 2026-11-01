# SPDX-License-Identifier: MIT OR Apache-2.0
"""Inflation blockers on generated documents: owner edits and a stub-route generation.

Every strengthened statement here is contained in its claim's own evidence, so the blocker
comes from the inflation rules, never from containment. The stub route is fixture-only.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
from test_checked_generation import _factual_blocks, _render, _run, _stub
from test_document_generation import (
    _document,
    _pair_documents,
    _preview,
    _queue_pair,
    _review,
    _save,
)
from test_identity import identity as identity
from test_sources import SourceContext
from test_sources import catalog as catalog
from test_suitability import SOURCE, _profile, _requirement, _write
from test_tracking import _application

CLAIM_TEXT = (
    "Assisted the team that led the Linux runbook migration for the operations team "
    "(2021–2022), reducing alert noise by 30%"
)
EVIDENCE_TEXT = (
    f"{CLAIM_TEXT}. The wider programme ran 2019–2023 and cut alert noise by 45%. "
    "The runbooks were later adopted company-wide."
)
STRENGTHENED = [
    (
        "statement_stronger_verb",
        "Led the Linux runbook migration for the operations team (2021–2022), "
        "reducing alert noise by 30%",
    ),
    ("statement_longer_tenure", "The wider programme ran 2019–2023"),
    ("statement_larger_metric", "alert noise by 45%"),
    ("statement_wider_scope", "The runbooks were later adopted company-wide"),
]
HONEST = [
    "Assisted the team that led the Linux runbook migration",
    "the Linux runbook migration for the operations team (2021–2022)",
    "reducing alert noise by 30%",
]


def _setup(context: SourceContext) -> tuple[dict[str, Any], dict[str, Any]]:
    """SYN-CORE plus one literal claim whose evidence holds every strengthened variant."""
    profile = _profile(context)
    evidence = context.client.post(
        "/api/v1/evidence",
        json={
            "payload": {"text": EVIDENCE_TEXT, "source": "Synthetic programme record"},
            "provenance": {
                "source_type": "owner_statement",
                "privacy_class": "private",
                "employer_owned": False,
            },
        },
        headers=context.headers(profile["version"]),
    )
    assert evidence.status_code == 200, evidence.text
    saved = evidence.json()
    revision = next(
        item["revision"]["id"]
        for item in saved["evidence"]
        if item["revision"]["payload"]["text"] == EVIDENCE_TEXT
    )
    claim = context.client.post(
        "/api/v1/claims",
        json={
            "kind": "experience",
            "payload": {"text": CLAIM_TEXT},
            "evidence_links": [
                {
                    "evidence_revision_id": revision,
                    "support_kind": "user_asserted",
                    "excerpt": CLAIM_TEXT,
                }
            ],
        },
        headers=context.headers(saved["version"]),
    )
    assert claim.status_code == 200, claim.text
    application = _application(context, description_text=SOURCE)
    _write(context, {"id": application["posting_id"]}, [_requirement(1, "Linux required.")])
    return claim.json(), application


def _resume(
    context: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, scenario: str
) -> dict[str, Any]:
    profile, application = _setup(context)
    with _stub(monkeypatch, tmp_path, scenario):
        workflow = _run(context, _queue_pair(context, _preview(context, profile, application)))
    assert workflow["state"] == "succeeded", workflow
    return next(
        item for item in _pair_documents(context, application)["items"] if item["kind"] == "resume"
    )


def _claim_block(document: dict[str, Any]) -> str:
    return str(
        next(
            block["block_key"] for block in _factual_blocks(document) if block["text"] == CLAIM_TEXT
        )
    )


def _edited(document: dict[str, Any], key: str, text: str) -> dict[str, Any]:
    """The saved body with the claim's own generated block reworded by the owner."""
    body = copy.deepcopy(document["current_revision"]["body"])
    blocks = [block for section in body["sections"] for block in section["blocks"]]
    next(block for block in blocks if block["block_key"] == key)["text"] = text
    return dict(body)


@pytest.mark.postgres
def test_owner_edit_into_each_strengthened_variant_names_statement_and_kind(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    document = _resume(catalog, monkeypatch, tmp_path, "valid")
    assert document["current_revision"]["validation_issues"] == []
    key = _claim_block(document)
    for code, statement in STRENGTHENED:
        saved = _save(catalog, document, _edited(document, key, statement))
        assert saved.status_code == 200, saved.text
        document = saved.json()
        issues = document["current_revision"]["validation_issues"]
        assert [(issue["code"], issue["statement"]) for issue in issues] == [(code, statement)]
        assert document["unsupported_claims"] == [issues[0]["path"]]
        refused = _review(catalog, document)
        assert refused.status_code == 409 and refused.json()["code"] == "unsupported_claims"
        rendered = _render(catalog, _document(catalog, document["id"]))
        assert rendered.status_code == 409 and rendered.json()["code"] == "unsupported_claims"
        document = _document(catalog, document["id"])
        reverted = _save(catalog, document, _edited(document, key, CLAIM_TEXT))
        assert reverted.status_code == 200, reverted.text
        document = reverted.json()
        assert document["current_revision"]["validation_issues"] == []


@pytest.mark.postgres
def test_owner_edit_into_honest_rephrasing_passes_review_and_render(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    document = _resume(catalog, monkeypatch, tmp_path, "valid")
    key = _claim_block(document)
    for statement in HONEST:
        saved = _save(catalog, document, _edited(document, key, statement))
        assert saved.status_code == 200, saved.text
        document = saved.json()
        assert document["current_revision"]["validation_issues"] == [], statement
        reverted = _save(catalog, document, _edited(document, key, CLAIM_TEXT))
        assert reverted.status_code == 200, reverted.text
        document = reverted.json()
    saved = _save(catalog, document, _edited(document, key, HONEST[0]))
    document = saved.json()
    assert _review(catalog, document).status_code == 200
    assert _render(catalog, _document(catalog, document["id"])).status_code == 202


@pytest.mark.postgres
def test_inflated_stub_generation_blocks_export_even_after_review(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    resume = _resume(catalog, monkeypatch, tmp_path, "inflated")
    issues = resume["current_revision"]["validation_issues"]
    statement = (
        "Led the Linux runbook migration for the operations team (2021–2022), "
        "reducing alert noise by 30%"
    )
    assert [(issue["code"], issue["statement"]) for issue in issues] == [
        ("statement_stronger_verb", statement)
    ]
    assert any(block["text"] == statement for block in _factual_blocks(resume))
    refused = _review(catalog, resume)
    assert refused.status_code == 409 and refused.json()["code"] == "unsupported_claims"
    rendered = _render(catalog, _document(catalog, resume["id"]))
    assert rendered.status_code == 409 and rendered.json()["code"] == "unsupported_claims"
