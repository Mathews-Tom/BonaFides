# SPDX-License-Identifier: MIT OR Apache-2.0
"""Section regeneration on the claim schema, the one checker for every document, and the
per-route valid-claim-JSON counts; driven through the deterministic stub model only."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Event
from typing import Any, cast

import pytest
from test_checked_generation import (
    WRITES,
    _archive_evidence,
    _edit_claim,
    _evidence_write,
    _generate,
    _render,
    _run,
    _stub,
)
from test_document_generation import _base, _document, _pair_documents, _review
from test_identity import identity as identity
from test_section_regeneration import _apply, _queue_section, _section_preview
from test_sources import SourceContext
from test_sources import catalog as catalog
from test_suitability import _after_model_send

from tools.stub_model import ABSENT_CLAIM_ID, StubModel

STUB_STAGES = ("generate_documents", "regenerate_section")


@contextmanager
def _resume(
    context: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[tuple[StubModel, dict[str, Any], dict[str, Any]]]:
    """A valid stub pair; yields the stub (one call spent) and the generated résumé."""
    with _stub(monkeypatch, tmp_path, "valid") as stub:
        _, application, task = _generate(context)
        assert _run(context, task)["state"] == "succeeded"
        resume = next(
            item
            for item in _pair_documents(context, application)["items"]
            if item["kind"] == "resume"
        )
        yield stub, application, resume


def _regenerate(context: SourceContext, document: dict[str, Any]) -> dict[str, Any]:
    return _run(context, _queue_section(context, _section_preview(context, document)))


def _proposal(
    context: SourceContext, document: dict[str, Any], workflow: dict[str, Any]
) -> dict[str, Any]:
    assert workflow["state"] == "succeeded", workflow
    response = context.client.get(
        f"/api/v1/documents/{document['id']}/regeneration-proposals/"
        f"{workflow['result']['proposal_id']}"
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def _proposal_count(context: SourceContext, document: dict[str, Any]) -> int:
    response = context.client.get(f"/api/v1/documents/{document['id']}/regeneration-proposals")
    assert response.status_code == 200, response.text
    return len(response.json()["items"])


def _rates(context: SourceContext, application: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return cast(
        dict[str, dict[str, Any]], _pair_documents(context, application)["claim_json_rates"]
    )


def _counts(
    valid_first: int = 0,
    corrected: int = 0,
    invalid: int = 0,
    failed: int = 0,
    rate: int | None = None,
) -> dict[str, Any]:
    return {
        "valid_first": valid_first,
        "valid_after_correction": corrected,
        "invalid_after_correction": invalid,
        "failed_before_check": failed,
        "rate_percent": rate,
    }


@pytest.mark.postgres
def test_valid_stub_section_links_every_statement_and_applied_revision_exports(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with _resume(catalog, monkeypatch, tmp_path) as (stub, application, resume):
        proposal = _proposal(catalog, resume, _regenerate(catalog, resume))
    assert len(stub.requests) == 2
    assert proposal["validation_issues"] == []
    pinned = {fact["claim_revision_id"] for fact in proposal["facts"]}
    assert pinned == {
        fact["claim_revision_id"]
        for fact in resume["current_revision"]["provenance_snapshot"]["facts"]
    }
    blocks = proposal["proposed_section"]["blocks"]
    assert blocks and all(
        block["claim_kind"] == "factual"
        and len(block["claim_revision_ids"]) == 1
        and block["claim_revision_ids"][0] in pinned
        for block in blocks
    )
    applied = _apply(catalog, resume, proposal)
    assert applied.status_code == 200, applied.text
    revision = applied.json()["current_revision"]
    assert revision["validation_issues"] == []
    assert revision["provenance_snapshot"]["checker_version"] == "claim-evidence-v1"
    updated = _document(catalog, resume["id"])
    assert _review(catalog, updated).status_code == 200
    assert _render(catalog, _document(catalog, resume["id"])).status_code == 202
    assert _rates(catalog, application) == {
        "generate_documents": _counts(valid_first=1, rate=100),
        "regenerate_section": _counts(valid_first=1, rate=100),
    }


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("scenario", "code"),
    [
        ("absent-claim", "claim_not_in_profile"),
        ("posting-only-skill", "statement_not_in_claim_evidence"),
    ],
)
def test_fabricated_section_statement_blocks_proposal_and_applied_revision(
    catalog: SourceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    scenario: str,
    code: str,
) -> None:
    with _resume(catalog, monkeypatch, tmp_path) as (stub, _, resume):
        stub.scenario = scenario
        proposal = _proposal(catalog, resume, _regenerate(catalog, resume))
    issues = proposal["validation_issues"]
    assert [issue["code"] for issue in issues] == [code]
    blocked = next(
        block
        for block in proposal["proposed_section"]["blocks"]
        if block["text"] == issues[0]["statement"]
    )
    if scenario == "absent-claim":
        assert blocked["claim_revision_ids"] == [ABSENT_CLAIM_ID]
    else:
        assert blocked["text"].startswith("Hands-on experience with ")
    # Applying keeps the blocker on the new revision; review cannot waive it.
    applied = _apply(catalog, resume, proposal)
    assert applied.status_code == 200, applied.text
    assert applied.json()["current_revision"]["validation_issues"] == issues
    updated = _document(catalog, resume["id"])
    refused = _review(catalog, updated)
    assert refused.status_code == 409 and refused.json()["code"] == "unsupported_claims"
    rendered = _render(catalog, updated)
    assert rendered.status_code == 409 and rendered.json()["code"] == "unsupported_claims"


@pytest.mark.postgres
def test_malformed_once_section_is_corrected_once_on_the_same_projection(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with _resume(catalog, monkeypatch, tmp_path) as (stub, application, resume):
        stub.scenario = "malformed-once"
        proposal = _proposal(catalog, resume, _regenerate(catalog, resume))
    _, first, second = stub.requests
    assert second["messages"][:2] == first["messages"]
    assert "blocks: too_short" in second["messages"][2]["content"]
    assert proposal["validation_issues"] == []
    assert proposal["model_run"]["usage"]["corrected_model_run_id"]
    assert _rates(catalog, application)["regenerate_section"] == _counts(corrected=1, rate=100)


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("scenario", "calls", "code", "counts"),
    [
        ("malformed", 2, "model_output_invalid_after_correction", _counts(invalid=1, rate=0)),
        ("truncated", 1, "model_incomplete", _counts(failed=1)),
    ],
)
def test_failed_section_task_creates_no_proposal_and_keeps_the_document(
    catalog: SourceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    scenario: str,
    calls: int,
    code: str,
    counts: dict[str, Any],
) -> None:
    with _resume(catalog, monkeypatch, tmp_path) as (stub, application, resume):
        stub.scenario = scenario
        workflow = _regenerate(catalog, resume)
    assert len(stub.requests) == 1 + calls
    assert workflow["state"] == "permanent_failure", workflow
    assert workflow["last_error_code"] == code
    assert _proposal_count(catalog, resume) == 0
    assert _document(catalog, resume["id"]) == resume
    assert _rates(catalog, application) == {
        "generate_documents": _counts(valid_first=1, rate=100),
        "regenerate_section": counts,
    }


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("scenario", "held"), [("valid", 2), ("malformed-once", 3)], ids=["send", "correction"]
)
def test_owner_send_lock_refuses_evidence_writers_during_section_send_and_correction(
    catalog: SourceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    scenario: str,
    held: int,
) -> None:
    entered, released = Event(), Event()

    def hold_correction(number: int) -> None:
        if number == held:
            entered.set()
            assert released.wait(30), "test must release the corrective call"

    with _resume(catalog, monkeypatch, tmp_path) as (stub, _, resume):
        stub.scenario = scenario
        stub.on_chat = hold_correction
        task = _queue_section(catalog, _section_preview(catalog, resume))
        with ThreadPoolExecutor(max_workers=1) as executor:
            running = executor.submit(_run, catalog, task)
            try:
                assert entered.wait(15), "worker must reach the held call"
                refused = _evidence_write(catalog)
                assert refused.status_code == 409
                assert refused.json()["code"] == "evidence_in_model_use"
            finally:
                released.set()
            workflow = running.result(timeout=30)
    assert workflow["state"] == "succeeded", workflow
    assert _evidence_write(catalog).status_code == 200


@pytest.mark.postgres
@pytest.mark.parametrize("write", sorted(WRITES))
def test_link_change_between_model_response_and_publish_withdraws_the_section_task(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, write: str
) -> None:
    with _resume(catalog, monkeypatch, tmp_path) as (stub, _, resume):
        task = _queue_section(catalog, _section_preview(catalog, resume))
        _after_model_send(monkeypatch, lambda: WRITES[write](catalog).raise_for_status())
        workflow = _run(catalog, task)
    assert len(stub.requests) == 2
    assert workflow["state"] == "permanent_failure", workflow
    assert workflow["last_error_code"] in {
        "regeneration_context_changed",
        "document_source_changed",
        "profile_fact_withdrawn",
        "profile_evidence_unavailable",
    }
    assert _proposal_count(catalog, resume) == 0
    assert _document(catalog, resume["id"])["current_revision"] == resume["current_revision"]


@pytest.mark.postgres
@pytest.mark.parametrize("write", sorted({*WRITES, "evidence_archive"}))
def test_link_change_between_publish_and_apply_refuses_the_proposal(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, write: str
) -> None:
    with _resume(catalog, monkeypatch, tmp_path) as (_, _, resume):
        proposal = _proposal(catalog, resume, _regenerate(catalog, resume))
    changes: dict[str, Callable[[SourceContext], Any]] = {
        **WRITES,
        "evidence_archive": _archive_evidence,
    }
    assert changes[write](catalog).status_code == 200
    # The write marks the pinned document stale, so its identity no longer matches the
    # proposal's target and the existing apply guard refuses before any source recheck.
    refused = _apply(catalog, _document(catalog, resume["id"]), proposal)
    assert refused.status_code == 412, refused.text
    assert refused.json()["code"] == "version_conflict"
    reopened = _proposal(
        catalog, resume, {"state": "succeeded", "result": {"proposal_id": proposal["id"]}}
    )
    assert reopened["applicable"] is False
    assert _document(catalog, resume["id"])["current_revision"] == resume["current_revision"]


@pytest.mark.postgres
def test_link_change_after_apply_follows_document_export_rules(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with _resume(catalog, monkeypatch, tmp_path) as (_, _, resume):
        proposal = _proposal(catalog, resume, _regenerate(catalog, resume))
    assert _apply(catalog, resume, proposal).status_code == 200
    applied = _document(catalog, resume["id"])
    assert _review(catalog, applied).status_code == 200
    assert _edit_claim(catalog).status_code == 200
    refused = _render(catalog, _document(catalog, resume["id"]))
    assert refused.status_code == 409 and refused.json()["code"] == "document_source_withdrawn"


EVIDENCE_TEXT = (
    "Operations Analyst, Juniper Relay (02/2022 to 05/2025)\nMaintained Linux batch runbooks."
)


def _profile_with_fields(context: SourceContext) -> dict[str, Any]:
    """A profile whose one experience claim carries role, organization and dates."""
    current = context.client.get("/api/v1/profile").json()
    evidence = context.client.post(
        "/api/v1/evidence",
        json={
            "payload": {"text": EVIDENCE_TEXT, "source": "Synthetic employment record"},
            "provenance": {
                "source_type": "owner_statement",
                "privacy_class": "private",
                "employer_owned": False,
            },
        },
        headers=context.headers(current["version"]),
    )
    assert evidence.status_code == 200, evidence.text
    saved = evidence.json()
    claim = context.client.post(
        "/api/v1/claims",
        json={
            "kind": "experience",
            "payload": {
                "text": "Maintained Linux batch runbooks",
                "role": "Operations Analyst",
                "organization": "Juniper Relay",
                "start_date": "2022-02",
                "end_date": "2025-05",
            },
            "evidence_links": [
                {
                    "evidence_revision_id": saved["evidence"][0]["revision"]["id"],
                    "support_kind": "user_asserted",
                    "excerpt": "Maintained Linux batch runbooks",
                }
            ],
        },
        headers=context.headers(saved["version"]),
    )
    assert claim.status_code == 200, claim.text
    return cast(dict[str, Any], claim.json())


@pytest.mark.postgres
def test_owner_base_resume_with_structured_fields_passes_the_claim_checker(
    catalog: SourceContext,
) -> None:
    document = _base(catalog, _profile_with_fields(catalog))
    revision = document["current_revision"]
    assert revision["provenance_snapshot"]["checker_version"] == "claim-evidence-v1"
    texts = [
        block["text"] for section in revision["body"]["sections"] for block in section["blocks"]
    ]
    assert texts == [
        "Maintained Linux batch runbooks\nOperations Analyst\nJuniper Relay\n2022-02\n2025-05"
    ]
    assert revision["validation_issues"] == [] and document["unsupported_claims"] == []
    assert _review(catalog, document).status_code == 200
    assert _render(catalog, _document(catalog, document["id"])).status_code == 202


@pytest.mark.parametrize("stage", STUB_STAGES)
def test_stub_route_carries_a_fixture_only_receipt_for_each_document_stage(
    tmp_path: Path, stage: str
) -> None:
    from bonafides import local_model
    from tools.stub_model import FIXTURE_REVIEWER
    from tools.stub_model import route as write_route

    path = tmp_path / "route.json"
    write_route("http://127.0.0.1:11434", "127.0.0.1", path, 30)
    route = local_model.load_route(path)
    assert route is not None and route.qualified_for(stage)
    receipts = json.loads(path.read_text(encoding="utf-8"))["qualification_receipts"]
    assert receipts[stage]["reviewed_by"] == FIXTURE_REVIEWER
