# SPDX-License-Identifier: MIT OR Apache-2.0
"""Strengthening blockers on generated documents: owner edits and a stub-route generation.

Every strengthened statement here is contained in its claim's own evidence. A cut of the
claim that starts at a stronger verb blocks as a middle cut; a longer period, larger
figure or wider scope taken from elsewhere in the evidence is not part of its claim.
Other contained middle/suffix cuts remain blocked even without promoting a verb.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, cast

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

from tools import stub_model

# A structured claim whose subject names a stronger verb than its own pinned verb.
CLAIM: dict[str, object] = {
    "schema_version": 1,
    "verb": "assisted",
    "subject": "the team that led the Linux runbook migration for the operations team",
    "metric": {"change": "decrease", "measure": "alert noise", "unit": "percent", "value": "30"},
    "period": {"start": "2021", "end": "2022", "separator": "en_dash", "style": "parenthetical"},
}
CLAIM_TEXT = (
    "Assisted the team that led the Linux runbook migration for the operations team, "
    "reducing alert noise by 30% (2021–2022)"
)
EVIDENCE_TEXT = (
    f"{CLAIM_TEXT}. The wider programme ran 2019–2023 and cut alert noise by 45%. "
    "The runbooks were later adopted company-wide."
)
STRENGTHENED = [
    (
        "statement_cut_inside_claim",
        "Led the Linux runbook migration for the operations team, reducing alert noise by 30%",
    ),
    ("statement_not_in_claim", "The wider programme ran 2019–2023"),
    ("statement_not_in_claim", "alert noise by 45%"),
    ("statement_not_in_claim", "The runbooks were later adopted company-wide"),
]
MIDCLAIM = [
    "the Linux runbook migration for the operations team",
    "reducing alert noise by 30% (2021–2022)",
]
HONEST = [
    "Assisted the team that led the Linux runbook migration",
    "the team that led the Linux runbook migration",
    CLAIM_TEXT,
    "Assisted the team",
    "the team",
]


def _setup(context: SourceContext) -> tuple[dict[str, Any], dict[str, Any]]:
    """SYN-CORE plus one structured claim whose evidence holds every strengthened variant."""
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
            "payload": {"claim": CLAIM},
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
def test_owner_edit_into_each_strengthened_variant_names_statement_and_reason(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    document = _resume(catalog, monkeypatch, tmp_path, "valid")
    assert document["current_revision"]["validation_issues"] == []
    key = _claim_block(document)
    for code, statement in [
        *STRENGTHENED,
        *(("statement_cut_inside_claim", text) for text in MIDCLAIM),
    ]:
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
def test_owner_edit_into_allowed_claim_prefix_passes_review_and_render(
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
@pytest.mark.parametrize(
    ("statement", "blocked"),
    [(text, False) for text in HONEST] + [(text, True) for text in MIDCLAIM],
)
def test_generated_pair_claim_prefixes_and_middle_cuts(
    catalog: SourceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    statement: str,
    blocked: bool,
) -> None:
    respond = stub_model.respond

    def cut_response(scenario: str, request: dict[str, object]) -> dict[str, object]:
        envelope = respond(scenario, request)
        message = cast(dict[str, str], envelope["message"])
        output = json.loads(message["content"])
        for document in output["documents"]:
            for section in document["sections"]:
                for block in section["blocks"]:
                    if block["text"] == CLAIM_TEXT:
                        block["text"] = statement
        message["content"] = json.dumps(output)
        return envelope

    monkeypatch.setattr(stub_model, "respond", cut_response)
    document = _resume(catalog, monkeypatch, tmp_path, "valid")
    issues = document["current_revision"]["validation_issues"]
    assert [(issue["code"], issue["statement"]) for issue in issues] == (
        [("statement_cut_inside_claim", statement)] if blocked else []
    )
    assert any(block["text"] == statement for block in _factual_blocks(document))
    assert _review(catalog, document).status_code == (409 if blocked else 200)
    assert _render(catalog, _document(catalog, document["id"])).status_code == (
        409 if blocked else 202
    )


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("verb", "subject", "statement", "blocked"),
    [
        (
            "designed",
            "and implemented a self-service onboarding flow",
            "Designed and implemented",
            True,
        ),
        ("built", "the API for billing team", "Built the API", False),
        ("reduced", "costs by 10 to 15%", "Reduced costs by 10", True),
        ("supported", "10 to 20 clients", "Supported 10", True),
        ("supported", "$10 to $20 budgets", "Supported $10", True),
        ("supported", "10 clients to resolve incidents", "Supported 10 clients", False),
        ("built", "migration planning", "Built migration", True),
        ("built", "migration jointly", "Built migration", True),
        ("built", "QA/release tooling", "Built QA", True),
        ("built", "Node.js tooling", "Built Node", True),
        ("built", "the team's API", "Built the team", True),
        ("built", "the teams' API", "Built the teams", True),
        ("built", "$2 million pipeline", "Built $2", True),
        ("built", "2 per 1000 pipeline", "Built 2", True),
        ("built", "the API for billing team", "the API", False),
        *[
            ("built", f"the API{ending}", "Built the API", False)
            for ending in (
                ", supporting billing",
                "; supporting billing",
                ": supporting billing",
                " (billing)",
                ". Shipped the CLI",
                " - shipped the CLI",
                " – shipped the CLI",
                " — shipped the CLI",
                " by billing",
                " to support billing",
                " on billing",
                " with billing",
                " in billing",
                " across billing",
                " from billing",
                " through billing",
                " during billing",
                " at billing",
                " that supports billing",
            )
        ],
    ],
)
def test_generated_pair_endpoint_policy(
    catalog: SourceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    verb: str,
    subject: str,
    statement: str,
    blocked: bool,
) -> None:
    import test_inflation_documents as fixtures

    claim_text = f"{verb.capitalize()} {subject}"
    monkeypatch.setattr(fixtures, "CLAIM", {"schema_version": 1, "verb": verb, "subject": subject})
    monkeypatch.setattr(fixtures, "CLAIM_TEXT", claim_text)
    monkeypatch.setattr(fixtures, "EVIDENCE_TEXT", claim_text)
    test_generated_pair_claim_prefixes_and_middle_cuts(
        catalog, monkeypatch, tmp_path, statement, blocked
    )


@pytest.mark.postgres
def test_inflated_stub_generation_blocks_export_even_after_review(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    resume = _resume(catalog, monkeypatch, tmp_path, "inflated")
    issues = resume["current_revision"]["validation_issues"]
    statement = (
        "Led the Linux runbook migration for the operations team, "
        "reducing alert noise by 30% (2021–2022)"
    )
    assert [(issue["code"], issue["statement"]) for issue in issues] == [
        ("statement_cut_inside_claim", statement)
    ]
    assert any(block["text"] == statement for block in _factual_blocks(resume))
    refused = _review(catalog, resume)
    assert refused.status_code == 409 and refused.json()["code"] == "unsupported_claims"
    rendered = _render(catalog, _document(catalog, resume["id"]))
    assert rendered.status_code == 409 and rendered.json()["code"] == "unsupported_claims"
