# SPDX-License-Identifier: MIT OR Apache-2.0
"""Confidential or employer-owned evidence content never reaches a transmitted model payload.

Each model call site builds its context from owner data and transmits it through the local
model client; these tests capture the exact request bytes at a fixture endpoint.
"""

from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Event
from typing import Any, cast
from uuid import UUID

import pytest
from sqlalchemy import Connection, text
from test_document_generation import (
    _candidate,
    _claim,
    _pair_documents,
    _preview,
    _queue_pair,
    _review,
)
from test_identity import identity as identity
from test_installation_control import renderer_spool as renderer_spool
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

from bonafides import local_model, model_context, suitability
from bonafides.commands import canonical_json
from bonafides.worker import Worker
from bonafides.workflows import assert_fence, heartbeat

CLAIM = "Kept the synthetic payroll batch inside its nightly window"
WITHHELD_MARKER = "SECRET-EMPLOYER-ROADMAP-7Q"


def _withheld_source(
    context: SourceContext, privacy_class: str = "confidential", employer_owned: bool = True
) -> tuple[dict[str, Any], str, str]:
    """Add evidence the owner withholds from models and one claim that cites it."""
    current = context.client.get("/api/v1/profile").json()
    evidence = context.client.post(
        "/api/v1/evidence",
        json={
            "payload": {
                "text": f"{CLAIM}. Internal codename {WITHHELD_MARKER}.",
                "source": "Review",
            },
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


def _reclassify(
    context: SourceContext,
    profile: dict[str, Any],
    evidence_id: str,
    privacy_class: str,
    employer_owned: bool,
) -> dict[str, Any]:
    record = next(item for item in profile["evidence"] if item["revision"]["id"] == evidence_id)
    response = context.client.put(
        f"/api/v1/evidence/{record['id']}/classification",
        json={"privacy_class": privacy_class, "employer_owned": employer_owned},
        headers=context.headers(profile["version"]),
    )
    assert response.status_code == 200, response.text
    updated = cast(dict[str, Any], response.json())
    current = next(item for item in updated["evidence"] if item["id"] == record["id"])
    assert current["revision"]["id"] == evidence_id
    assert current["revision"]["payload"] == record["revision"]["payload"]
    assert current["revision"]["content_sha256"] == record["revision"]["content_sha256"]
    assert current["revision"]["provenance"]["source_type"] == "work_sample"
    assert current["revision"]["provenance"]["privacy_class"] == privacy_class
    assert current["revision"]["provenance"]["employer_owned"] is employer_owned
    assert updated["current_revision"]["snapshot"] == profile["current_revision"]["snapshot"]
    assert updated["current_revision"]["id"] != profile["current_revision"]["id"]
    before_claims = profile["current_revision"]["claims"]
    after_claims = updated["current_revision"]["claims"]
    assert [item["id"] for item in after_claims] == [item["id"] for item in before_claims]
    for before, after in zip(before_claims, after_claims, strict=True):
        # Hydrated source classification is live; the claim and its exact pins are not.
        assert {
            key: value for key, value in after["revision"].items() if key != "evidence_links"
        } == {key: value for key, value in before["revision"].items() if key != "evidence_links"}
        assert [
            {key: value for key, value in link.items() if key != "evidence"}
            for link in after["revision"]["evidence_links"]
        ] == [
            {key: value for key, value in link.items() if key != "evidence"}
            for link in before["revision"]["evidence_links"]
        ]
    return updated


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
    assert WITHHELD_MARKER.encode() not in raw
    sent = json.loads(raw)
    return cast(dict[str, Any], json.loads(sent["messages"][1]["content"]))


def _assert_withheld(sent: dict[str, Any], evidence_id: str, claim_id: str) -> None:
    assert next(item for item in sent["evidence"] if item["id"] == evidence_id) == {
        "id": evidence_id,
        "withheld": True,
    }
    claim = next(item for item in sent["claims"] if item["id"] == claim_id)
    assert claim["text"] == CLAIM and evidence_id in claim["evidence_revision_ids"]
    assert WITHHELD_MARKER not in canonical_json(sent)


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
    profile, evidence_id, claim_id = _withheld_source(catalog, "private", False)
    _route(monkeypatch, tmp_path)
    before = _preview(catalog, profile, application)
    assert {"id": evidence_id, "text": f"{CLAIM}. Internal codename {WITHHELD_MARKER}."} in before[
        "model_projection"
    ]["evidence"]
    profile = _reclassify(catalog, profile, evidence_id, privacy_class, employer_owned)
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
def test_document_generation_content_edit_without_repin_is_unavailable(
    catalog: SourceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _profile(catalog)
    application = _application(catalog, description_text=SOURCE)
    _write(catalog, {"id": application["posting_id"]}, [_requirement(1, "Linux required.")])
    profile, evidence_id, _ = _withheld_source(catalog, "private", False)
    record = next(item for item in profile["evidence"] if item["revision"]["id"] == evidence_id)
    _route(monkeypatch, tmp_path)
    _preview(catalog, profile, application)
    payload = copy.deepcopy(record["revision"]["payload"])
    payload["text"] = f"{payload['text']} Changed source text."
    updated = catalog.client.put(
        f"/api/v1/evidence/{record['id']}",
        json={
            "payload": payload,
            "provenance": {"source_type": "work_sample"},
        },
        headers=catalog.headers(profile["version"]),
    )
    assert updated.status_code == 200, updated.text
    current = next(item for item in updated.json()["evidence"] if item["id"] == record["id"])
    assert current["revision"]["id"] != evidence_id
    assert current["revision"]["content_sha256"] != record["revision"]["content_sha256"]
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


@pytest.mark.postgres
@pytest.mark.parametrize("privacy_class", ["private", "public"])
def test_private_and_public_evidence_content_still_reaches_document_generation(
    catalog: SourceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    privacy_class: str,
) -> None:
    _profile(catalog)
    application = _application(catalog, description_text=SOURCE)
    _write(catalog, {"id": application["posting_id"]}, [_requirement(1, "Linux required.")])
    profile, evidence_id, _ = _withheld_source(catalog, privacy_class, False)
    _route(monkeypatch, tmp_path)
    preview = _preview(catalog, profile, application)
    assert {"id": evidence_id, "text": f"{CLAIM}. Internal codename {WITHHELD_MARKER}."} in preview[
        "model_projection"
    ]["evidence"]


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("privacy_class", "employer_owned"), [("confidential", False), ("private", True)]
)
def test_section_regeneration_sends_the_claim_without_withheld_evidence_content(
    catalog: SourceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    privacy_class: str,
    employer_owned: bool,
) -> None:
    _profile(catalog)
    application = _application(catalog, description_text=SOURCE)
    _write(catalog, {"id": application["posting_id"]}, [_requirement(1, "Linux required.")])
    profile, evidence_id, claim_id = _withheld_source(catalog, "private", False)
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
    profile = _reclassify(catalog, profile, evidence_id, privacy_class, employer_owned)
    _assert_withheld(
        _preview(catalog, profile, application)["model_projection"], evidence_id, claim_id
    )
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
@pytest.mark.parametrize(
    ("privacy_class", "employer_owned"), [("confidential", False), ("private", True)]
)
def test_requirement_matching_sends_the_claim_without_withheld_evidence_content(
    catalog: SourceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    privacy_class: str,
    employer_owned: bool,
) -> None:
    _profile(catalog)
    profile, evidence_id, claim_id = _withheld_source(catalog, "private", False)
    application = _application(catalog, description_text=SOURCE)
    view = _write(catalog, {"id": application["posting_id"]}, [_requirement(1, "Linux required.")])
    _route(monkeypatch, tmp_path)
    before = _preview(catalog, profile, application)
    assert {"id": evidence_id, "text": f"{CLAIM}. Internal codename {WITHHELD_MARKER}."} in before[
        "model_projection"
    ]["evidence"]
    profile = _reclassify(catalog, profile, evidence_id, privacy_class, employer_owned)
    _assert_withheld(
        _preview(catalog, profile, application)["model_projection"], evidence_id, claim_id
    )
    task = _queue(catalog, view, profile)
    context = cast(dict[str, Any], task.input["context"])
    _assert_withheld(context, evidence_id, claim_id)
    assert WITHHELD_MARKER not in canonical_json(task.input)
    _assert_withheld(_transmitted(tmp_path, "match_requirements", context), evidence_id, claim_id)
    with catalog.runtime.transaction() as conn:
        projection = suitability._profile_projection(
            conn, catalog.owner_id, UUID(str(task.input["profile_revision_id"]))
        )
    # The local checker keeps reading the complete evidence the claim cites.
    assert WITHHELD_MARKER in str(projection.evidence[next(iter(projection.withheld))]["text"])


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("stage", "error"),
    [
        ("generate_documents", "stale_document_context"),
        ("regenerate_section", "regeneration_context_changed"),
        ("match_requirements", "stale_profile_revision"),
    ],
)
@pytest.mark.parametrize(
    ("privacy_class", "employer_owned"), [("confidential", False), ("private", True)]
)
@pytest.mark.parametrize("after_reservation", [False, True])
def test_reclassification_after_queue_refuses_before_transmission(
    catalog: SourceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stage: str,
    error: str,
    privacy_class: str,
    employer_owned: bool,
    after_reservation: bool,
) -> None:
    _profile(catalog)
    application = _application(catalog, description_text=SOURCE)
    view = _write(catalog, {"id": application["posting_id"]}, [_requirement(1, "Linux required.")])
    profile, evidence_id, _ = _withheld_source(catalog, "private", False)
    metadata: list[str] = []
    with fixture_server(response_body(), metadata_paths=metadata) as (endpoint, requests):
        route = qualified_route(tmp_path, route_data(endpoint))
        monkeypatch.setattr(local_model, "load_route", lambda _path: route)
        preview = _preview(catalog, profile, application)
        if stage == "match_requirements":
            task = _queue(catalog, view, profile)
        elif stage == "regenerate_section":
            suitability.execute_model(
                catalog.runtime,
                catalog.identity.settings,
                _queue_pair(catalog, preview),
                Event(),
                FixtureModel(_candidate(preview)),
            )
            resume = next(
                item
                for item in _pair_documents(catalog, application)["items"]
                if item["kind"] == "resume"
            )
            task = _queue_section(catalog, _section_preview(catalog, resume))
        else:
            task = _queue_pair(catalog, preview)
        assert WITHHELD_MARKER in canonical_json(task.input["context"])
        if after_reservation:
            lock_evidence = model_context.lock_transmitted_evidence

            def classify_before_final_check(conn: Connection, owner: UUID, context: Any) -> None:
                _reclassify(catalog, profile, evidence_id, privacy_class, employer_owned)
                lock_evidence(conn, owner, context)

            monkeypatch.setattr(
                model_context, "lock_transmitted_evidence", classify_before_final_check
            )
        else:
            _reclassify(catalog, profile, evidence_id, privacy_class, employer_owned)
        Worker(catalog.runtime, replace(catalog.identity.settings, mode="production"))._execute(
            task, Event()
        )
        failed = catalog.workflow(task.id)
        assert failed["state"] == "permanent_failure", failed
        assert failed["last_error_code"] == error, failed
        assert requests == []
        assert metadata == []


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("privacy_class", "employer_owned"), [("confidential", False), ("private", True)]
)
def test_document_generated_after_reclassification_can_be_reviewed_and_exported(
    catalog: SourceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    renderer_spool: Path,
    privacy_class: str,
    employer_owned: bool,
) -> None:
    _profile(catalog)
    application = _application(catalog, description_text=SOURCE)
    _write(catalog, {"id": application["posting_id"]}, [_requirement(1, "Linux required.")])
    profile, evidence_id, claim_id = _withheld_source(catalog, "private", False)
    profile = _reclassify(catalog, profile, evidence_id, privacy_class, employer_owned)
    _route(monkeypatch, tmp_path)
    preview = _preview(catalog, profile, application)
    # Make the generated document cite the reclassified record, not an unrelated fixture claim.
    candidate_preview = copy.deepcopy(preview)
    candidate_preview["model_projection"]["claims"] = [
        item for item in preview["model_projection"]["claims"] if item["id"] == claim_id
    ]
    candidate = _candidate(candidate_preview)
    worker = Worker(
        catalog.runtime,
        replace(catalog.identity.settings, mode="production", renderer_spool=renderer_spool),
    )
    encoded = response_body(message={"role": "assistant", "content": json.dumps(candidate)})
    with fixture_server(encoded) as (endpoint, requests):
        route = qualified_route(tmp_path, route_data(endpoint))
        monkeypatch.setattr(local_model, "load_route", lambda _path: route)
        preview = _preview(catalog, profile, application)
        task = _queue_pair(catalog, preview)
        worker._execute(task, Event())
        result = catalog.workflow(task.id)
        assert result["state"] == "succeeded", result
        assert len(requests) == 1
        assert WITHHELD_MARKER.encode() not in requests[0]
        sent = json.loads(json.loads(requests[0])["messages"][1]["content"])
        _assert_withheld(sent, evidence_id, claim_id)
    for document in _pair_documents(catalog, application)["items"]:
        reviewed = _review(catalog, document)
        assert reviewed.status_code == 200, reviewed.text
        current = reviewed.json()
        revision = current["current_revision"]
        queued = catalog.client.post(
            f"/api/v1/documents/{document['id']}/renders",
            json={
                "revision_id": revision["id"],
                "content_sha256": revision["content_sha256"],
                "format": "pdf",
            },
            headers=catalog.headers(current["version"]),
        )
        assert queued.status_code == 202, queued.text
        render_task = _claim(catalog, queued.json()["id"])
        worker._execute(render_task, Event())
        rendered = catalog.workflow(render_task.id)
        assert rendered["state"] == "succeeded", rendered
        artifact = catalog.client.get(f"/api/v1/artifacts/{rendered['result']['artifact_id']}")
        assert artifact.status_code == 200, artifact.text
        assert artifact.content.startswith(b"%PDF-")


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
        "evidence": [{"id": "e", "withheld": True, "text": WITHHELD_MARKER}],
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


@pytest.mark.postgres
@pytest.mark.parametrize(
    "stage", ["generate_documents", "regenerate_section", "match_requirements"]
)
@pytest.mark.parametrize("phase", ["metadata", "inference", "postflight"])
def test_inflight_send_refuses_reclassification_only_for_transmitted_records(
    catalog: SourceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stage: str,
    phase: str,
) -> None:
    _profile(catalog)
    application = _application(catalog, description_text=SOURCE)
    view = _write(catalog, {"id": application["posting_id"]}, [_requirement(1, "Linux required.")])
    profile, evidence_id, _ = _withheld_source(catalog, "private", False)
    entered, released = Event(), Event()

    def block() -> None:
        entered.set()
        assert released.wait(20), "test must release the fixture endpoint"

    response = response_body(
        message={
            "role": "assistant",
            "content": json.dumps({"schema_version": "1", "stage": stage}),
        }
    )
    with fixture_server(
        response,
        on_preflight=block if phase == "metadata" else None,
        on_inference=block if phase == "inference" else None,
        on_postflight=block if phase == "postflight" else None,
    ) as (endpoint, requests):
        data = route_data(endpoint)
        data["deadline_seconds"] = 30
        route = qualified_route(tmp_path, data)
        monkeypatch.setattr(local_model, "load_route", lambda _path: route)
        preview = _preview(catalog, profile, application)
        if stage == "match_requirements":
            task = _queue(catalog, view, profile)
        elif stage == "regenerate_section":
            suitability.execute_model(
                catalog.runtime,
                catalog.identity.settings,
                _queue_pair(catalog, preview),
                Event(),
                FixtureModel(_candidate(preview)),
            )
            resume = next(
                item
                for item in _pair_documents(catalog, application)["items"]
                if item["kind"] == "resume"
            )
            task = _queue_section(catalog, _section_preview(catalog, resume))
        else:
            task = _queue_pair(catalog, preview)
        assert WITHHELD_MARKER in canonical_json(task.input["context"])
        record = next(item for item in profile["evidence"] if item["revision"]["id"] == evidence_id)
        worker = Worker(catalog.runtime, replace(catalog.identity.settings, mode="production"))
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(worker._execute, task, Event())
            try:
                assert entered.wait(10), "worker must reach the blocking fixture endpoint"
                # A retained workflow row lock would break both lease renewal and
                # provider admission (which already ran before the chat request).
                with catalog.runtime.transaction() as conn:
                    assert heartbeat(conn, task)
                    assert_fence(conn, task)
                current = catalog.client.get("/api/v1/profile").json()
                rejected = catalog.client.put(
                    f"/api/v1/evidence/{record['id']}/classification",
                    json={"privacy_class": "private", "employer_owned": True},
                    headers=catalog.headers(current["version"]),
                )
                assert rejected.status_code == 409, rejected.text
                assert rejected.json()["code"] == "evidence_in_model_use"
                assert catalog.client.get("/api/v1/profile").json() == current
                with catalog.runtime.transaction() as conn:
                    stored = conn.execute(
                        text("""SELECT e.employer_owned,
                        (SELECT count(*) FROM bonafides.evidence_classification_changes c
                        WHERE c.owner_id=e.owner_id AND c.evidence_id=e.id) AS history
                        FROM bonafides.evidence_records e WHERE e.owner_id=:owner AND e.id=:id"""),
                        {"owner": catalog.owner_id, "id": UUID(record["id"])},
                    ).one()
                    assert not stored.employer_owned and stored.history == 1
                # This newly created record is outside the frozen context, even for
                # matching (which includes all evidence in its pinned profile).
                extra = catalog.client.post(
                    "/api/v1/evidence",
                    json={
                        "payload": {"text": "Unrelated synthetic note", "source": "Review"},
                        "provenance": {
                            "source_type": "work_sample",
                            "privacy_class": "private",
                            "employer_owned": False,
                        },
                    },
                    headers=catalog.headers(current["version"]),
                )
                assert extra.status_code == 200, extra.text
                unrelated = extra.json()["changed_evidence_id"]
                extra_record = next(
                    item for item in extra.json()["evidence"] if item["id"] == unrelated
                )
                assert extra_record["revision"]["id"] not in canonical_json(task.input["context"])
                _reclassify(catalog, extra.json(), extra_record["revision"]["id"], "private", True)
                assert not future.done()
            finally:
                released.set()
            future.result(timeout=10)
        assert len(requests) == 1
        assert WITHHELD_MARKER.encode() in requests[0]
        assert catalog.workflow(task.id)["last_error_code"] == "invalid_model_output"
        # Failure also releases send locks; the original classification can now commit.
        current = catalog.client.get("/api/v1/profile").json()
        _reclassify(catalog, current, evidence_id, "private", True)
