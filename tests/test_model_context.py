# SPDX-License-Identifier: MIT OR Apache-2.0
"""Confidential or employer-owned evidence content never reaches a transmitted model payload.

Each model call site builds its context from owner data and transmits it through the local
model client; these tests capture the exact request bytes at a fixture endpoint.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from threading import Event
from typing import Any, cast
from uuid import UUID, uuid4

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
from test_imports import ImportContext, _parsed
from test_imports import _review as _import_review
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

from bonafides import imports, local_model, model_context, profile_entries, suitability
from bonafides.commands import canonical_json
from bonafides.db import Database
from bonafides.import_parser import ImportParserClient, ParsedImport
from bonafides.worker import Worker
from bonafides.workflows import Claim, assert_fence, claim, heartbeat

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
    assert preview["withheld_evidence"] == []


@pytest.mark.postgres
def test_generation_preview_explains_withheld_evidence_beside_the_exact_projection(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _profile(catalog)
    application = _application(catalog, description_text=SOURCE)
    _write(catalog, {"id": application["posting_id"]}, [_requirement(1, "Linux required.")])
    profile, evidence_id, claim_id = _withheld_source(catalog, "private", False)
    profile = _reclassify(catalog, profile, evidence_id, "private", True)
    _route(monkeypatch, tmp_path)
    preview = _preview(catalog, profile, application)
    assert preview["withheld_evidence"] == [
        {
            "evidence_revision_id": evidence_id,
            "source": "Review",
            "reasons": ["employer_owned"],
            "claim_revision_ids": [claim_id],
        }
    ]
    assert "withheld_evidence" not in preview["model_projection"]
    assert SECRET not in canonical_json(preview)


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
def test_committed_reclassification_before_shared_acquisition_refuses_without_endpoint_bytes(
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
            send_lock = model_context.model_send_lock

            @contextmanager
            def classify_before_shared_acquisition(db: Database, owner: UUID) -> Iterator[None]:
                # The API response proves the classification transaction committed before
                # the final shared-lock acquisition and its fresh-context validation.
                _reclassify(catalog, profile, evidence_id, privacy_class, employer_owned)
                with send_lock(db, owner):
                    yield

            monkeypatch.setattr(
                model_context, "model_send_lock", classify_before_shared_acquisition
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


def _unlinked_evidence(context: SourceContext, label: str) -> tuple[dict[str, Any], dict[str, Any]]:
    current = context.client.get("/api/v1/profile").json()
    response = context.client.post(
        "/api/v1/evidence",
        json={
            "payload": {"text": label, "source": "Synthetic owner-lock fixture"},
            "provenance": {
                "source_type": "work_sample",
                "privacy_class": "private",
                "employer_owned": False,
            },
        },
        headers=context.headers(current["version"]),
    )
    assert response.status_code == 200, response.text
    profile = cast(dict[str, Any], response.json())
    record = next(
        item for item in profile["evidence"] if item["id"] == profile["changed_evidence_id"]
    )
    return profile, record


def _archive_for_restore(context: SourceContext) -> tuple[dict[str, Any], str]:
    historical, record = _unlinked_evidence(context, "Archived before the model context was pinned")
    response = context.client.delete(
        f"/api/v1/evidence/{record['id']}", headers=context.headers(historical["version"])
    )
    assert response.status_code == 200, response.text
    assert record["id"] not in {item["id"] for item in response.json()["evidence"]}
    return cast(dict[str, Any], response.json()), str(historical["current_revision"]["id"])


def _staged_owner_lock_import(
    context: SourceContext, monkeypatch: pytest.MonkeyPatch
) -> tuple[ImportContext, dict[str, Any]]:
    # Reuse the parser transport and review/worker fixtures without bootstrapping a
    # second singleton owner or requiring an external parser process.
    def parse(
        self: ImportParserClient, data: bytes, media_type: str, interrupted: Event | None = None
    ) -> ParsedImport:
        del self
        assert media_type == "text/plain"
        assert interrupted is not None and not interrupted.is_set()
        return _parsed(data.decode("utf-8"))

    monkeypatch.setattr(ImportParserClient, "parse", parse)
    importing = ImportContext(
        context.identity, context.client, context.csrf, context.owner_id, context.runtime
    )
    return importing, importing.staged("Skills\nOwner serialization fixture skill\n")


def _evidence_write_state(context: SourceContext) -> dict[str, list[Any]]:
    """Include immutable history and classification, not just the hydrated projection."""
    with context.runtime.transaction() as conn:
        return {
            table: list(
                conn.execute(
                    text(
                        f"SELECT to_jsonb(record) FROM bonafides.{table} AS record "
                        "WHERE owner_id=:owner ORDER BY to_jsonb(record)::text"
                    ),
                    {"owner": context.owner_id},
                ).scalars()
            )
            for table in (
                "profiles",
                "profile_revisions",
                "profile_revision_claims",
                "profile_revision_evidence",
                "professional_claims",
                "claim_revisions",
                "claim_evidence_links",
                "evidence_records",
                "evidence_revisions",
                "evidence_classification_changes",
                "import_batches",
            )
        }


@pytest.mark.postgres
@pytest.mark.parametrize(
    "stage", ["generate_documents", "regenerate_section", "match_requirements"]
)
@pytest.mark.parametrize("phase", ["metadata", "inference", "postflight"])
def test_inflight_owner_send_refuses_classify_edit_archive_restore_and_import_merge(
    catalog: SourceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stage: str,
    phase: str,
) -> None:
    _profile(catalog)
    application = _application(catalog, description_text=SOURCE)
    view = _write(catalog, {"id": application["posting_id"]}, [_requirement(1, "Linux required.")])
    _, evidence_id, _ = _withheld_source(catalog, "private", False)
    _, record = _unlinked_evidence(catalog, "Evidence not cited by any document claim")
    profile, restore_revision = _archive_for_restore(catalog)
    importing, batch = _staged_owner_lock_import(catalog, monkeypatch)
    entered, released = Event(), Event()

    def block() -> None:
        entered.set()
        assert released.wait(60), "test must release the fixture endpoint"

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
        data["deadline_seconds"] = 90
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
        if stage != "match_requirements":
            assert record["revision"]["id"] not in canonical_json(task.input["context"])
        before = _evidence_write_state(catalog)
        current = catalog.client.get("/api/v1/profile").json()
        writes = (
            (
                "classify",
                "PUT",
                f"/api/v1/evidence/{record['id']}/classification",
                {"privacy_class": "private", "employer_owned": True},
            ),
            (
                "edit",
                "PUT",
                f"/api/v1/evidence/{record['id']}",
                {
                    "payload": {
                        **record["revision"]["payload"],
                        "text": "Edited unlinked evidence after the owner send",
                    },
                    "provenance": {"source_type": "work_sample"},
                },
            ),
            ("archive", "DELETE", f"/api/v1/evidence/{record['id']}", None),
            ("restore", "POST", "/api/v1/profile/restore", {"revision_id": restore_revision}),
            ("import merge", "POST", f"/api/v1/imports/{batch['id']}/merge", _import_review(batch)),
        )
        worker = Worker(catalog.runtime, replace(catalog.identity.settings, mode="production"))
        with ThreadPoolExecutor(max_workers=2) as executor:
            sending = executor.submit(worker._execute, task, Event())
            try:
                assert entered.wait(10), "worker must reach the blocking fixture endpoint"
                # The owner send lock must not retain workflow/owner/profile row locks.
                with catalog.runtime.transaction() as conn:
                    conn.execute(text("SET LOCAL statement_timeout='3s'"))
                    assert heartbeat(conn, task)
                    assert_fence(conn, task)
                for name, method, path, body in writes:
                    rejected = executor.submit(
                        catalog.client.request,
                        method,
                        path,
                        json=body,
                        headers=catalog.headers(current["version"]),
                    ).result(timeout=5)
                    assert rejected.status_code == 409, (name, rejected.text)
                    assert rejected.json()["code"] == "evidence_in_model_use", name
                    assert _evidence_write_state(catalog) == before, name
                assert catalog.client.get("/api/v1/profile").json() == current
                assert not sending.done()
            finally:
                released.set()
            sending.result(timeout=10)
        assert len(requests) == 1
        assert WITHHELD_MARKER.encode() in requests[0]
        assert catalog.workflow(task.id)["last_error_code"] == "invalid_model_output"
        # Even a failed model call releases the owner lock. Each previously refused
        # command must now perform its real write, not merely pass a primitive probe.
        for name, method, path, body in writes:
            current = catalog.client.get("/api/v1/profile").json()
            accepted = catalog.client.request(
                method, path, json=body, headers=catalog.headers(current["version"])
            )
            assert accepted.status_code == (202 if name == "import merge" else 200), (
                name,
                accepted.text,
            )
            if name == "classify":
                classified = next(
                    item for item in accepted.json()["evidence"] if item["id"] == record["id"]
                )
                assert classified["revision"]["provenance"]["employer_owned"] is True
            elif name == "edit":
                edited = next(
                    item for item in accepted.json()["evidence"] if item["id"] == record["id"]
                )
                assert edited["revision"]["id"] != record["revision"]["id"]
            elif name == "archive":
                assert record["id"] not in {item["id"] for item in accepted.json()["evidence"]}
            elif name == "restore":
                restored = next(
                    item for item in accepted.json()["evidence"] if item["id"] == record["id"]
                )
                assert restored["revision"]["id"] == record["revision"]["id"]
        merged = importing.run()
        assert catalog.workflow(merged.id)["state"] == "succeeded"
        assert importing.batch(batch["id"])["state"] == "succeeded"
        assert evidence_id in canonical_json(catalog.client.get("/api/v1/profile").json())


def _matching_fixture_response() -> bytes:
    return response_body(
        message={
            "role": "assistant",
            "content": json.dumps(
                {
                    "schema_version": "1",
                    "stage": "match_requirements",
                    "assessments": [],
                    "summary": "Synthetic owner-lock transport fixture.",
                    "warnings": [],
                }
            ),
        }
    )


def _send_with_owner_lock(
    context: SourceContext, owner: UUID, route: local_model.LocalRoute
) -> None:
    with model_context.model_send_lock(context.runtime, owner):
        local_model.OllamaClient(route).call(
            "match_requirements",
            {"requirements": [], "claims": [], "evidence": []},
            local_model.PROMPT_VERSIONS["match_requirements"],
        )


@pytest.mark.postgres
def test_two_same_owner_shared_sends_both_reach_http_before_either_releases(
    catalog: SourceContext, tmp_path: Path
) -> None:
    entered = [Event(), Event()]
    released = Event()

    def block(index: int) -> None:
        entered[index].set()
        assert released.wait(30), "test must release both fixture endpoints"

    # Provider admission deliberately remains singleton. Exercise the shared-lock
    # contract around real HTTP without changing or bypassing production admission.
    with (
        fixture_server(_matching_fixture_response(), on_inference=lambda: block(0)) as first,
        fixture_server(_matching_fixture_response(), on_inference=lambda: block(1)) as second,
    ):
        routes = []
        for endpoint, _ in (first, second):
            data = route_data(endpoint)
            data["deadline_seconds"] = 60
            routes.append(qualified_route(tmp_path, data))
        with ThreadPoolExecutor(max_workers=2) as executor:
            sends = [
                executor.submit(_send_with_owner_lock, catalog, catalog.owner_id, route)
                for route in routes
            ]
            try:
                assert entered[0].wait(10), "first same-owner send must reach HTTP"
                assert entered[1].wait(10), "shared owner lock must admit the second send"
                assert len(first[1]) == len(second[1]) == 1
                assert all(not sending.done() for sending in sends)
            finally:
                released.set()
            for sending in sends:
                sending.result(timeout=10)


@pytest.mark.postgres
def test_other_owner_evidence_writes_are_unaffected_by_an_active_shared_send(
    catalog: SourceContext, tmp_path: Path
) -> None:
    _profile(catalog)
    profile, record = _unlinked_evidence(catalog, "Evidence belonging to the writing owner")
    entered, released = Event(), Event()

    def block() -> None:
        entered.set()
        assert released.wait(30), "test must release the other owner's fixture endpoint"

    # The installation fixture bootstraps one persisted owner. A different owner's
    # send uses its own UUID namespace without needing a second bootstrap identity.
    sending_owner = uuid4()
    with fixture_server(_matching_fixture_response(), on_inference=block) as (endpoint, requests):
        data = route_data(endpoint)
        data["deadline_seconds"] = 60
        route = qualified_route(tmp_path, data)
        with ThreadPoolExecutor(max_workers=2) as executor:
            sending = executor.submit(_send_with_owner_lock, catalog, sending_owner, route)
            try:
                assert entered.wait(10), "other owner's send must be in flight"
                accepted = executor.submit(
                    catalog.client.put,
                    f"/api/v1/evidence/{record['id']}/classification",
                    json={"privacy_class": "private", "employer_owned": True},
                    headers=catalog.headers(profile["version"]),
                ).result(timeout=5)
                assert accepted.status_code == 200, accepted.text
                classified = next(
                    item for item in accepted.json()["evidence"] if item["id"] == record["id"]
                )
                assert classified["revision"]["provenance"]["employer_owned"] is True
                assert not sending.done()
            finally:
                released.set()
            sending.result(timeout=10)
        assert len(requests) == 1


@pytest.mark.postgres
def test_approved_import_worker_waits_during_owner_send_without_consuming_an_attempt(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _profile(catalog)
    importing, batch = _staged_owner_lock_import(catalog, monkeypatch)
    approved = importing.approve(batch)
    assert approved.status_code == 202, approved.text
    worker = Worker(catalog.runtime, catalog.identity.settings)
    with catalog.runtime.transaction() as conn:
        # Exercise the final available merge attempt, not a workflow with spare retries.
        conn.execute(
            text("""UPDATE bonafides.workflow_runs SET attempt=max_attempts-1
            WHERE owner_id=:owner AND id=:id AND state='pending'"""),
            {"owner": catalog.owner_id, "id": UUID(approved.json()["workflow_id"])},
        )
        task = claim(conn, worker.id)
    assert task is not None and str(task.id) == approved.json()["workflow_id"]
    assert task.attempt == 4
    before = _evidence_write_state(catalog)
    entered, released, retried = Event(), Event(), Event()
    tries = 0

    def observe_lock(conn: Connection, owner: UUID) -> None:
        nonlocal tries
        tries += 1
        if tries >= 2:
            retried.set()
        model_context.lock_evidence_write(conn, owner)

    monkeypatch.setattr(imports, "lock_evidence_write", observe_lock)

    def block() -> None:
        entered.set()
        assert released.wait(30), "test must release the fixture endpoint"

    with fixture_server(_matching_fixture_response(), on_inference=block) as (endpoint, requests):
        data = route_data(endpoint)
        data["deadline_seconds"] = 60
        route = qualified_route(tmp_path, data)
        with ThreadPoolExecutor(max_workers=2) as executor:
            sending = executor.submit(_send_with_owner_lock, catalog, catalog.owner_id, route)
            try:
                assert entered.wait(10), "owner send must reach HTTP before the merge worker"
                merging = executor.submit(worker._execute_active, task, Event())
                assert retried.wait(10), "worker must retry contention within its existing claim"
                assert not merging.done()
                assert _evidence_write_state(catalog) == before
                with catalog.runtime.transaction() as conn:
                    waiting = (
                        conn.execute(
                            text("""SELECT state,attempt,last_error_code,last_heartbeat_at,
                        lease_expires_at>clock_timestamp() AS lease_valid
                        FROM bonafides.workflow_runs WHERE owner_id=:owner AND id=:id"""),
                            {"owner": catalog.owner_id, "id": task.id},
                        )
                        .mappings()
                        .one()
                    )
                assert waiting["state"] == "running"
                assert waiting["attempt"] == task.attempt
                assert waiting["last_error_code"] is None
                assert waiting["last_heartbeat_at"] is not None and waiting["lease_valid"]
                assert not sending.done()
            finally:
                released.set()
            sending.result(timeout=10)
            merging.result(timeout=10)
        assert len(requests) == 1
    finished = catalog.workflow(task.id)
    assert finished["state"] == "succeeded"
    assert finished["attempt"] == task.attempt
    assert importing.batch(batch["id"])["state"] == "succeeded"
    after = _evidence_write_state(catalog)
    for table in ("profile_revisions", "evidence_records", "evidence_revisions"):
        assert len(after[table]) == len(before[table]) + 1
    # A repeated obsolete execution cannot duplicate the approved merge.
    worker._execute_active(task, Event())
    assert _evidence_write_state(catalog) == after


@pytest.mark.postgres
@pytest.mark.parametrize("stop", ["interrupted", "cancelled", "lease", "deadline"])
def test_waiting_import_merge_stops_at_existing_execution_fences(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, stop: str
) -> None:
    _profile(catalog)
    importing, batch = _staged_owner_lock_import(catalog, monkeypatch)
    approved = importing.approve(batch)
    assert approved.status_code == 202, approved.text
    worker = Worker(catalog.runtime, catalog.identity.settings)
    with catalog.runtime.transaction() as conn:
        task = claim(conn, worker.id)
    assert task is not None and str(task.id) == approved.json()["workflow_id"]
    before = _evidence_write_state(catalog)
    interrupted = Event()
    heartbeats = 0

    def stop_wait(conn: Connection, active: Claim) -> bool:
        nonlocal heartbeats
        heartbeats += 1
        if stop == "interrupted":
            interrupted.set()
        else:
            updates = {
                "cancelled": "cancel_requested_at=clock_timestamp()",
                "lease": "lease_expires_at=clock_timestamp()-interval '1 second'",
                "deadline": "deadline_at=clock_timestamp()-interval '1 second'",
            }
            conn.execute(
                text(f"""UPDATE bonafides.workflow_runs SET {updates[stop]}
                WHERE owner_id=:owner AND id=:id"""),
                {"owner": active.owner_id, "id": active.id},
            )
        return heartbeat(conn, active)

    monkeypatch.setattr(imports, "heartbeat", stop_wait)
    with model_context.model_send_lock(catalog.runtime, catalog.owner_id):
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(worker._execute_active, task, interrupted).result(timeout=5)
        assert _evidence_write_state(catalog) == before
        assert heartbeats == 1
        workflow = catalog.workflow(task.id)
        assert workflow["state"] == "running"
        assert workflow["attempt"] == task.attempt
        assert workflow["last_error_code"] is None


@pytest.mark.postgres
@pytest.mark.parametrize("first_lock", ["send", "restore"])
def test_restore_racing_owner_send_is_bounded_and_never_deadlocks(
    catalog: SourceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    first_lock: str,
) -> None:
    _profile(catalog)
    application = _application(catalog, description_text=SOURCE)
    _write(catalog, {"id": application["posting_id"]}, [_requirement(1, "Linux required.")])
    _withheld_source(catalog, "private", False)
    profile, restore_revision = _archive_for_restore(catalog)
    entered, released = Event(), Event()
    writer_locked, release_writer, send_acquiring = Event(), Event(), Event()

    def block() -> None:
        entered.set()
        assert released.wait(30), "test must release the fixture endpoint"

    metadata: list[str] = []
    with fixture_server(
        response_body(),
        on_inference=block,
        metadata_paths=metadata,
    ) as (endpoint, requests):
        data = route_data(endpoint)
        data["deadline_seconds"] = 60
        route = qualified_route(tmp_path, data)
        monkeypatch.setattr(local_model, "load_route", lambda _path: route)
        task = _queue_pair(catalog, _preview(catalog, profile, application))
        worker = Worker(catalog.runtime, replace(catalog.identity.settings, mode="production"))
        before = _evidence_write_state(catalog)
        if first_lock == "restore":
            write_lock = model_context.lock_evidence_write
            shared_lock = model_context.model_send_lock

            def pause_restore(conn: Connection, owner: UUID) -> None:
                write_lock(conn, owner)
                writer_locked.set()
                assert release_writer.wait(20), "test must let the restore transaction commit"

            @contextmanager
            def announce_shared_acquisition(db: Database, owner: UUID) -> Iterator[None]:
                send_acquiring.set()
                with shared_lock(db, owner):
                    yield

            monkeypatch.setattr(profile_entries, "lock_evidence_write", pause_restore)
            monkeypatch.setattr(model_context, "model_send_lock", announce_shared_acquisition)
        with ThreadPoolExecutor(max_workers=2) as executor:
            try:
                if first_lock == "send":
                    sending = executor.submit(worker._execute, task, Event())
                    assert entered.wait(10), "send must hold its shared lock at HTTP"
                restoring = executor.submit(
                    catalog.client.post,
                    "/api/v1/profile/restore",
                    json={"revision_id": restore_revision},
                    headers=catalog.headers(profile["version"]),
                )
                if first_lock == "restore":
                    assert writer_locked.wait(10), "restore must hold the exclusive owner lock"
                    sending = executor.submit(worker._execute, task, Event())
                    assert send_acquiring.wait(10), "send must reach shared-lock acquisition"
                    assert requests == [] and metadata == []
                    release_writer.set()
                restored = restoring.result(timeout=5)
                if first_lock == "send":
                    assert restored.status_code == 409, restored.text
                    assert restored.json()["code"] == "evidence_in_model_use"
                    assert _evidence_write_state(catalog) == before
                else:
                    assert restored.status_code == 200, restored.text
                    assert (
                        restored.json()["current_revision"]["snapshot"]["evidence_revision_ids"]
                        != profile["current_revision"]["snapshot"]["evidence_revision_ids"]
                    )
            finally:
                release_writer.set()
                released.set()
            sending.result(timeout=10)
        if first_lock == "restore":
            assert catalog.workflow(task.id)["last_error_code"] == "stale_document_context"
            assert requests == [] and metadata == []
        else:
            assert len(requests) == 1
            current = catalog.client.get("/api/v1/profile").json()
            restored = catalog.client.post(
                "/api/v1/profile/restore",
                json={"revision_id": restore_revision},
                headers=catalog.headers(current["version"]),
            )
            assert restored.status_code == 200, restored.text
