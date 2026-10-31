# SPDX-License-Identifier: MIT OR Apache-2.0
"""Confidential or employer-owned evidence content never reaches a transmitted model payload.

Each model call site builds its context from owner data and transmits it through the local
model client; these tests capture the exact request bytes at a fixture endpoint.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from threading import Event
from typing import Any, cast
from uuid import UUID

import pytest
from test_document_generation import _candidate, _pair_documents, _preview, _queue_pair
from test_identity import identity as identity
from test_local_model import fixture_server, qualified_route, response_body, route_data
from test_section_regeneration import _queue_section, _section_preview
from test_sources import SourceContext
from test_sources import catalog as catalog
from test_suitability import (
    SOURCE,
    FixtureModel,
    _posting,
    _profile,
    _queue,
    _requirement,
    _route,
    _write,
)
from test_tracking import _application

from bonafides import local_model, suitability
from bonafides.commands import canonical_json

CLAIM = "Kept the synthetic payroll batch inside its nightly window"
SECRET = "SECRET-EMPLOYER-ROADMAP-7Q"


def _withheld_source(
    context: SourceContext, privacy_class: str = "confidential", employer_owned: bool = True
) -> tuple[dict[str, Any], str, str]:
    """Add evidence the owner withholds from models and one claim that cites it."""
    current = context.client.get("/api/v1/profile").json()
    evidence = context.client.post(
        "/api/v1/evidence",
        json={
            "payload": {"text": f"{CLAIM}. Internal codename {SECRET}.", "source": "Review"},
            "provenance": {
                "source_type": "work_sample",
                "privacy_class": privacy_class,
                "employer_owned": employer_owned,
            },
        },
        headers=context.headers(current["version"]),
    )
    assert evidence.status_code == 200, evidence.text
    evidence_id = evidence.json()["evidence"][-1]["revision"]["id"]
    claimed = context.client.post(
        "/api/v1/claims",
        json={
            "kind": "skill",
            "payload": {"text": CLAIM},
            "evidence_links": [
                {
                    "evidence_revision_id": evidence_id,
                    "support_kind": "user_asserted",
                    "excerpt": CLAIM,
                }
            ],
        },
        headers=context.headers(evidence.json()["version"]),
    )
    assert claimed.status_code == 200, claimed.text
    profile = cast(dict[str, Any], claimed.json())
    claim = next(
        item
        for item in profile["current_revision"]["claims"]
        if item["id"] == profile["changed_claim_id"]
    )
    return profile, evidence_id, str(claim["revision"]["id"])


def _transmitted(tmp_path: Path, stage: str, context: dict[str, object]) -> dict[str, Any]:
    """Send one context through the local model client and return the exact sent context."""
    with fixture_server(response_body()) as (endpoint, requests):
        client = local_model.OllamaClient(qualified_route(tmp_path, route_data(endpoint)))
        if stage == "extract_posting":
            client.call(stage, context, local_model.PROMPT_VERSIONS[stage])
        else:
            # The fixture answer is a posting extraction; only the transmitted request matters.
            with pytest.raises(local_model.ModelFailure):
                client.call(stage, context, local_model.PROMPT_VERSIONS[stage])
        assert len(requests) == 1
        raw = requests[0]
    assert SECRET.encode() not in raw
    sent = json.loads(raw)
    return cast(dict[str, Any], json.loads(sent["messages"][1]["content"]))


def _assert_withheld(sent: dict[str, Any], evidence_id: str, claim_id: str) -> None:
    assert {"id": evidence_id, "withheld": True} in sent["evidence"]
    claim = next(item for item in sent["claims"] if item["id"] == claim_id)
    assert claim["text"] == CLAIM and evidence_id in claim["evidence_revision_ids"]
    assert SECRET not in canonical_json(sent)


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("privacy_class", "employer_owned"),
    [("confidential", False), ("private", True), ("public", True), ("confidential", True)],
)
def test_document_generation_sends_the_claim_without_withheld_evidence_content(
    catalog: SourceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    privacy_class: str,
    employer_owned: bool,
) -> None:
    _profile(catalog)
    application = _application(catalog, description_text=SOURCE)
    _write(catalog, {"id": application["posting_id"]}, [_requirement(1, "Linux required.")])
    profile, evidence_id, claim_id = _withheld_source(catalog, privacy_class, employer_owned)
    _route(monkeypatch, tmp_path)
    preview = _preview(catalog, profile, application)
    _assert_withheld(preview["model_projection"], evidence_id, claim_id)
    task = _queue_pair(catalog, preview)
    assert task.input["context"] == preview["model_projection"]
    sent = _transmitted(
        tmp_path, "generate_documents", cast(dict[str, object], task.input["context"])
    )
    assert sent == preview["model_projection"]
    _assert_withheld(sent, evidence_id, claim_id)


@pytest.mark.postgres
@pytest.mark.parametrize("changed_text", [False, True])
def test_document_generation_reclassified_pinned_evidence_requires_unchanged_content(
    catalog: SourceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    changed_text: bool,
) -> None:
    _profile(catalog)
    application = _application(catalog, description_text=SOURCE)
    _write(catalog, {"id": application["posting_id"]}, [_requirement(1, "Linux required.")])
    profile, evidence_id, claim_id = _withheld_source(catalog, "private", False)
    record = next(item for item in profile["evidence"] if item["revision"]["id"] == evidence_id)
    _route(monkeypatch, tmp_path)
    before = _preview(catalog, profile, application)
    assert {"id": evidence_id, "text": f"{CLAIM}. Internal codename {SECRET}."} in before[
        "model_projection"
    ]["evidence"]

    payload = copy.deepcopy(record["revision"]["payload"])
    if changed_text:
        payload["text"] = f"{payload['text']} Changed source text."
    updated = catalog.client.put(
        f"/api/v1/evidence/{record['id']}",
        json={
            "payload": payload,
            "provenance": {
                "source_type": "work_sample",
                "privacy_class": "private",
                "employer_owned": True,
            },
        },
        headers=catalog.headers(profile["version"]),
    )
    assert updated.status_code == 200, updated.text
    current = next(item for item in updated.json()["evidence"] if item["id"] == record["id"])
    assert current["revision"]["id"] != evidence_id
    assert current["revision"]["provenance"]["employer_owned"] is True
    assert (current["revision"]["content_sha256"] != record["revision"]["content_sha256"]) is (
        changed_text
    )

    if changed_text:
        refused = catalog.client.get(
            f"/api/v1/applications/{application['id']}/generation-context",
            params={
                "profile_revision_id": profile["current_revision"]["id"],
                "template_version": "bonafides-evidence-v1",
                "tone": "professional",
                "language": "en",
            },
        )
        assert refused.status_code == 409, refused.text
        assert refused.json()["code"] == "profile_evidence_unavailable"
        return

    # Keep the original profile/claim citation pinned to the earlier private revision.
    preview = _preview(catalog, profile, application)
    _assert_withheld(preview["model_projection"], evidence_id, claim_id)
    assert preview["model_projection"]["evidence"] != before["model_projection"]["evidence"]
    task = _queue_pair(catalog, preview)
    assert task.input["context"] == preview["model_projection"]
    sent = _transmitted(
        tmp_path, "generate_documents", cast(dict[str, object], task.input["context"])
    )
    assert sent == preview["model_projection"]
    _assert_withheld(sent, evidence_id, claim_id)


@pytest.mark.postgres
def test_private_and_public_evidence_content_still_reaches_document_generation(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _profile(catalog)
    application = _application(catalog, description_text=SOURCE)
    _write(catalog, {"id": application["posting_id"]}, [_requirement(1, "Linux required.")])
    profile, evidence_id, _ = _withheld_source(catalog, "private", False)
    _route(monkeypatch, tmp_path)
    preview = _preview(catalog, profile, application)
    assert {"id": evidence_id, "text": f"{CLAIM}. Internal codename {SECRET}."} in preview[
        "model_projection"
    ]["evidence"]


@pytest.mark.postgres
def test_section_regeneration_sends_the_claim_without_withheld_evidence_content(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _profile(catalog)
    application = _application(catalog, description_text=SOURCE)
    _write(catalog, {"id": application["posting_id"]}, [_requirement(1, "Linux required.")])
    profile, evidence_id, claim_id = _withheld_source(catalog)
    _route(monkeypatch, tmp_path)
    preview = _preview(catalog, profile, application)
    pair = cast(dict[str, Any], _candidate(preview))
    extra = copy.deepcopy(pair["documents"][0]["sections"][0])
    extra["heading"] = "Experience"
    extra["blocks"][0]["block_key"] = "b-3"
    pair["documents"][0]["sections"].append(extra)
    suitability.execute_model(
        catalog.runtime,
        catalog.identity.settings,
        _queue_pair(catalog, preview),
        Event(),
        FixtureModel(pair),
    )
    records = _pair_documents(catalog, application)["items"]
    resume = next(item for item in records if item["kind"] == "resume")
    section = _section_preview(catalog, resume)
    _assert_withheld(section["model_projection"], evidence_id, claim_id)
    task = _queue_section(catalog, section)
    assert task.input["context"] == section["model_projection"]
    sent = _transmitted(
        tmp_path, "regenerate_section", cast(dict[str, object], task.input["context"])
    )
    assert sent == section["model_projection"]
    _assert_withheld(sent, evidence_id, claim_id)


@pytest.mark.postgres
def test_requirement_matching_sends_the_claim_without_withheld_evidence_content(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _profile(catalog)
    profile, evidence_id, claim_id = _withheld_source(catalog)
    view = _write(catalog, _posting(catalog), [_requirement(1, "Linux required.")])
    _route(monkeypatch, tmp_path)
    task = _queue(catalog, view, profile)
    context = cast(dict[str, Any], task.input["context"])
    _assert_withheld(context, evidence_id, claim_id)
    assert SECRET not in canonical_json(task.input)
    _assert_withheld(_transmitted(tmp_path, "match_requirements", context), evidence_id, claim_id)
    with catalog.runtime.transaction() as conn:
        projection = suitability._profile_projection(
            conn, catalog.owner_id, UUID(str(task.input["profile_revision_id"]))
        )
    # The local checker keeps reading the complete evidence the claim cites.
    assert SECRET in str(projection.evidence[next(iter(projection.withheld))]["text"])


@pytest.mark.postgres
def test_posting_extraction_sends_only_the_posting(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _profile(catalog)
    _withheld_source(catalog)
    view = _write(catalog, _posting(catalog), [_requirement(1, "Linux required.")])
    _route(monkeypatch, tmp_path)
    task = _queue(catalog, view)
    sent = _transmitted(tmp_path, "extract_posting", cast(dict[str, object], task.input["context"]))
    assert set(sent) == {"source_text", "source_sha256"} and CLAIM not in canonical_json(sent)


def test_transmit_point_refuses_withheld_evidence_that_carries_content(tmp_path: Path) -> None:
    context: dict[str, object] = {
        "requirements": [],
        "claims": [],
        "evidence": [{"id": "e", "withheld": True, "text": SECRET}],
    }
    with fixture_server(response_body()) as (endpoint, requests):
        client = local_model.OllamaClient(qualified_route(tmp_path, route_data(endpoint)))
        with pytest.raises(local_model.ModelFailure, match="model_context_invalid"):
            client.call(
                "match_requirements", context, local_model.PROMPT_VERSIONS["match_requirements"]
            )
        context["evidence"] = [{"id": "e", "withheld": False}]
        with pytest.raises(local_model.ModelFailure, match="model_context_invalid"):
            client.call(
                "match_requirements", context, local_model.PROMPT_VERSIONS["match_requirements"]
            )
        assert requests == []
