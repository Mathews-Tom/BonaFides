# SPDX-License-Identifier: MIT OR Apache-2.0
"""Real-PostgreSQL proposal/CAS boundaries; synthetic local model output only."""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from threading import Event
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_checked_generation import _generate, _run, _stub
from test_document_generation import (
    _base,
    _candidate,
    _claim,
    _document,
    _pair_documents,
    _preview,
    _queue_pair,
    _review,
    _save,
    _setup_pair,
)
from test_identity import identity as identity
from test_sources import SourceContext
from test_sources import catalog as catalog
from test_suitability import FixtureModel, _after_model_send, _profile, _requirement, _route, _write

from bonafides import document_generation as generation
from bonafides import documents, local_model, section_regeneration, suitability
from bonafides.commands import canonical_json
from bonafides.problems import Problem
from bonafides.storage import BlobStore
from bonafides.worker import Worker
from bonafides.workflows import Claim, cancel_workflow, publish, reap


def _setup(
    context: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    profile, application = _setup_pair(context)
    _route(monkeypatch, tmp_path)
    preview = _preview(context, profile, application)
    pair = cast(dict[str, Any], _candidate(preview))
    extra = copy.deepcopy(pair["documents"][0]["sections"][0])
    extra["heading"] = "Experience"
    extra["blocks"][0]["block_key"] = "b-3"
    pair["documents"][0]["sections"].append(extra)
    task = _queue_pair(context, preview)
    suitability.execute_model(
        context.runtime, context.identity.settings, task, Event(), FixtureModel(pair)
    )
    records = _pair_documents(context, application)["items"]
    records.sort(key=lambda item: item["kind"], reverse=True)
    assert records[0]["kind"] == "resume"
    return profile, application, records


def _section_preview(context: SourceContext, document: dict[str, Any]) -> dict[str, Any]:
    response = context.client.get(
        f"/api/v1/documents/{document['id']}/regeneration-context",
        params={
            "section_key": document["current_revision"]["body"]["sections"][0]["key"],
            "tone": "concise",
        },
    )
    assert response.status_code == 200, response.text
    assert "no-store" in response.headers["cache-control"]
    return cast(dict[str, Any], response.json())


def _request_body(preview: dict[str, Any]) -> dict[str, Any]:
    return {
        key: preview[key]
        for key in ("revision_id", "content_sha256", "section_key", "tone", "context_sha256")
    }


def _queue_section(context: SourceContext, preview: dict[str, Any]) -> Claim:
    response = context.client.post(
        f"/api/v1/documents/{preview['document_id']}/regeneration-proposals",
        json=_request_body(preview),
        headers=context.headers(preview["document_version"]),
    )
    assert response.status_code == 202, response.text
    assert response.json()["mode"] == "section_regeneration"
    task = _claim(context, response.json()["id"])
    assert task.input["stage"] == "regenerate_section"
    assert task.input["mode"] == "section_regeneration"
    return task


def _output(preview: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "1",
        "stage": "regenerate_section",
        "blocks": copy.deepcopy(preview["model_projection"]["target"]["blocks"]),
        "warnings": [],
    }


def _proposal(
    context: SourceContext, document: dict[str, Any], *, unsupported: bool = False
) -> tuple[Claim, dict[str, Any]]:
    preview = _section_preview(context, document)
    task = _queue_section(context, preview)
    output = _output(preview)
    if unsupported:
        output["blocks"][0]["text"] = "Led 9999 engineers and invented an unsupported credential."
    suitability.execute_model(
        context.runtime, context.identity.settings, task, Event(), FixtureModel(output)
    )
    workflow = context.workflow(task.id)
    assert workflow["state"] == "succeeded", workflow
    result = workflow["result"]
    assert set(result) == {
        "document_id",
        "proposal_id",
        "target_revision_id",
        "section_key",
        "model_run_id",
    }
    assert "blocks" not in canonical_json(
        result
    ) and "unsupported credential" not in canonical_json(result)
    response = context.client.get(
        f"/api/v1/documents/{document['id']}/regeneration-proposals/{result['proposal_id']}"
    )
    assert response.status_code == 200, response.text
    return task, cast(dict[str, Any], response.json())


def _apply(
    context: SourceContext,
    document: dict[str, Any],
    proposal: dict[str, Any],
    *,
    key: str | None = None,
) -> Any:
    return context.client.post(
        f"/api/v1/documents/{document['id']}/regeneration-proposals/{proposal['id']}/apply",
        json={
            "revision_id": proposal["target_revision_id"],
            "content_sha256": proposal["target_content_sha256"],
            "reason": "Explicit synthetic owner selection",
        },
        headers=context.headers(document["version"], key=key),
    )


@pytest.mark.postgres
def test_claim_checked_proposal_reports_same_blocker_as_applied_revision(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with _stub(monkeypatch, tmp_path, "valid"):
        _, application, task = _generate(catalog)
        workflow = _run(catalog, task)
    assert workflow["state"] == "succeeded", workflow
    document = next(
        item for item in _pair_documents(catalog, application)["items"] if item["kind"] == "resume"
    )
    assert document["current_revision"]["provenance_snapshot"]["checker_version"] == (
        "claim-evidence-v1"
    )
    _route(monkeypatch, tmp_path)
    _, proposal = _proposal(catalog, document, unsupported=True)
    blockers = proposal["validation_issues"]
    assert blockers == [
        {
            "path": "/sections/0/blocks/0",
            "code": "statement_not_in_claim_evidence",
            "statement": "Led 9999 engineers and invented an unsupported credential.",
        }
    ]

    applied = _apply(catalog, document, proposal)
    assert applied.status_code == 200, applied.text
    revision = applied.json()["current_revision"]
    assert revision["validation_issues"] == blockers
    assert revision["provenance_snapshot"]["checker_version"] == "claim-evidence-v1"


@pytest.mark.postgres
def test_proposal_is_immutable_apply_only_one_section_and_retains_old_pdf(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, _, records = _setup(catalog, monkeypatch, tmp_path)
    document = records[0]
    reviewed = _review(catalog, document)
    assert reviewed.status_code == 200
    document = reviewed.json()
    revision = document["current_revision"]
    queued = catalog.client.post(
        f"/api/v1/documents/{document['id']}/renders",
        json={
            "revision_id": revision["id"],
            "content_sha256": revision["content_sha256"],
            "format": "pdf",
        },
        headers=catalog.headers(document["version"]),
    )
    assert queued.status_code == 202, queued.text
    rendering = _claim(catalog, queued.json()["id"])
    payload = b"%PDF-1.7\nexplicit immutable storage/publication fixture\n%%EOF"
    blob = BlobStore(catalog.identity.settings.blob_root).put(catalog.owner_id, payload)
    with catalog.runtime.transaction() as conn:
        prepared = documents.prepare_render(
            conn, catalog.owner_id, UUID(document["id"]), UUID(revision["id"])
        )
        publish(
            conn,
            rendering,
            lambda active: documents.publish_artifact(
                active, catalog.owner_id, rendering.id, prepared, blob
            ),
        )
    artifact = catalog.workflow(rendering.id)["result"]["artifact_id"]
    document = _document(catalog, document["id"])
    with catalog.runtime.transaction() as conn:
        original_row = conn.execute(
            text(
                "SELECT row_to_json(r) FROM bonafides.document_revisions r WHERE owner_id=:owner AND id=:id"
            ),
            {"owner": catalog.owner_id, "id": UUID(revision["id"])},
        ).scalar_one()
    _, proposal = _proposal(catalog, document)
    assert proposal["applicable"] and proposal["validation_issues"] == []
    assert _document(catalog, document["id"]) == document
    assert proposal["original_section"] == revision["body"]["sections"][0]
    assert proposal["proposed_section"]["heading"] == proposal["original_section"]["heading"]
    assert proposal["proposed_section"]["key"] == proposal["original_section"]["key"]
    assert all(
        block["block_key"] == f"proposal-{proposal['id']}-{index}"
        for index, block in enumerate(proposal["proposed_section"]["blocks"])
    )
    key = str(uuid4())
    applied = _apply(catalog, document, proposal, key=key)
    assert applied.status_code == 200, applied.text
    updated = applied.json()
    current = updated["current_revision"]
    assert updated["version"] == document["version"] + 1
    assert current["origin"] == "manual" and not updated["reviewed"]
    assert current["body"]["title"] == revision["body"]["title"]
    assert current["body"]["sections"][0] == proposal["proposed_section"]
    assert current["body"]["sections"][1:] == revision["body"]["sections"][1:]
    assert current["template_version"] == revision["template_version"]
    provenance = copy.deepcopy(current["provenance_snapshot"])
    section = provenance.pop("section_regeneration")
    assert provenance == revision["provenance_snapshot"]
    assert section == {
        "proposal_id": proposal["id"],
        "model_run_id": proposal["model_run"]["id"],
        "parent_revision_id": revision["id"],
        "section_key": proposal["section_key"],
    }
    assert _apply(catalog, document, proposal, key=key).json() == updated
    assert _apply(catalog, updated, proposal).status_code == 412
    history = catalog.client.get(f"/api/v1/documents/{document['id']}/revisions").json()["items"]
    assert [item["id"] for item in history] == [current["id"], revision["id"]]
    assert catalog.client.get(f"/api/v1/artifacts/{artifact}").content == payload
    with catalog.runtime.transaction() as conn:
        assert (
            conn.execute(
                text(
                    "SELECT row_to_json(r) FROM bonafides.document_revisions r WHERE owner_id=:owner AND id=:id"
                ),
                {"owner": catalog.owner_id, "id": UUID(revision["id"])},
            ).scalar_one()
            == original_row
        )
    for statement in (
        "UPDATE bonafides.document_regeneration_proposals SET section_key='changed' WHERE owner_id=:owner AND id=:id",
        "DELETE FROM bonafides.document_regeneration_proposals WHERE owner_id=:owner AND id=:id",
    ):
        with pytest.raises(DBAPIError), catalog.runtime.transaction() as conn:
            conn.execute(text(statement), {"owner": catalog.owner_id, "id": UUID(proposal["id"])})


@pytest.mark.postgres
def test_minimized_original_parent_projection_excludes_frame_other_sections_and_new_facts(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile, _, records = _setup(catalog, monkeypatch, tmp_path)
    document = records[0]
    manual = copy.deepcopy(document["current_revision"]["body"])
    manual["sections"][1]["blocks"][0]["text"] = "UNTARGETED-PRIVATE-SENTINEL"
    saved = _save(catalog, document, manual)
    assert saved.status_code == 200
    document = saved.json()
    expanded = _profile(catalog)
    added_ids = expanded["current_revision"]["snapshot"]["claim_revision_ids"]
    snapshot = copy.deepcopy(profile["current_revision"]["snapshot"])
    snapshot["claim_revision_ids"] = [*snapshot["claim_revision_ids"], *added_ids]
    snapshot["display_name"] = "NEW-PRIVATE-NAME"
    snapshot["contact"] = {"email": "new-private@example.test"}
    changed = catalog.client.put(
        "/api/v1/profile", json=snapshot, headers=catalog.headers(expanded["version"])
    )
    assert changed.status_code == 200, changed.text
    refreshed = _document(catalog, document["id"])
    assert refreshed["current_revision"] == document["current_revision"]
    assert refreshed["version"] > document["version"]
    document = refreshed
    preview = _section_preview(catalog, document)
    wire = preview["model_projection"]
    assert set(wire) == {
        "posting",
        "requirements",
        "claims",
        "evidence",
        "tone",
        "language",
        "target",
    }
    assert set(wire["target"]) == {"kind", "section_key", "heading", "blocks"}
    assert all("block_key" not in block for block in wire["target"]["blocks"])
    serialized = canonical_json(wire)
    for forbidden in (
        "NEW-PRIVATE-NAME",
        "new-private@example.test",
        "UNTARGETED-PRIVATE-SENTINEL",
        str(catalog.owner_id),
        document["id"],
        document["application_id"],
        document["current_revision"]["id"],
        "frame",
        "contact",
        "hard_constraint",
    ):
        assert forbidden not in serialized
    pinned = document["current_revision"]["provenance_snapshot"]["facts"]
    assert {item["id"] for item in wire["claims"]} == {item["claim_revision_id"] for item in pinned}
    assert {item["id"] for item in wire["evidence"]} == {
        item["evidence_revision_id"] for item in pinned
    }
    assert not set(added_ids) & {item["id"] for item in wire["claims"]}
    assert all(identifier not in serialized for identifier in added_ids)
    _, proposal = _proposal(catalog, document)
    updated = _apply(catalog, document, proposal)
    assert updated.status_code == 200, updated.text
    assert (
        updated.json()["current_revision"]["provenance_snapshot"]["frame"]
        == document["current_revision"]["provenance_snapshot"]["frame"]
    )


@pytest.mark.postgres
def test_unsupported_candidate_remains_visible_and_common_review_pdf_guards_block(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, _, records = _setup(catalog, monkeypatch, tmp_path)
    document = records[0]
    _, proposal = _proposal(catalog, document, unsupported=True)
    assert proposal["validation_issues"] and proposal["applicable"]
    assert "9999" in proposal["proposed_section"]["blocks"][0]["text"]
    assert _document(catalog, document["id"]) == document
    applied = _apply(catalog, document, proposal)
    assert applied.status_code == 200, applied.text
    blocked = applied.json()
    assert blocked["unsupported_claims"] and not blocked["reviewed"]
    assert _review(catalog, blocked).status_code == 409
    revision = blocked["current_revision"]
    pdf = catalog.client.post(
        f"/api/v1/documents/{document['id']}/renders",
        json={
            "revision_id": revision["id"],
            "content_sha256": revision["content_sha256"],
            "format": "pdf",
        },
        headers=catalog.headers(blocked["version"]),
    )
    assert pdf.status_code == 409


@pytest.mark.postgres
@pytest.mark.parametrize(
    "failure",
    [
        "foreign_claim",
        "foreign_evidence",
        "extra_heading",
        "extra_key",
        "wrong_stage",
        "empty",
        "duplicate_reference",
    ],
)
def test_malformed_or_unpinned_candidate_quarantines_only_regeneration_stage(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str
) -> None:
    _, _, records = _setup(catalog, monkeypatch, tmp_path)
    document = records[0]
    preview = _section_preview(catalog, document)
    task = _queue_section(catalog, preview)
    candidate = _output(preview)
    if failure == "foreign_claim":
        candidate["blocks"][0]["claim_revision_ids"] = [str(uuid4())]
    elif failure == "foreign_evidence":
        candidate["blocks"][0]["evidence_revision_ids"] = [str(uuid4())]
    elif failure == "extra_heading":
        candidate["heading"] = "Invented heading"
    elif failure == "extra_key":
        candidate["blocks"][0]["block_key"] = "model-controlled"
    elif failure == "wrong_stage":
        candidate["stage"] = "generate_documents"
    elif failure == "empty":
        candidate["blocks"] = []
    else:
        candidate["blocks"][0]["claim_revision_ids"] *= 2
    model = FixtureModel(candidate)
    with pytest.raises(Problem):
        suitability.execute_model(catalog.runtime, catalog.identity.settings, task, Event(), model)
    assert model.calls == 1 and _document(catalog, document["id"]) == document
    with catalog.runtime.transaction() as conn:
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM bonafides.document_regeneration_proposals WHERE owner_id=:owner"
                ),
                {"owner": catalog.owner_id},
            ).scalar_one()
            == 0
        )
        assert conn.execute(
            text("SELECT stage FROM bonafides.local_model_route_blocks WHERE owner_id=:owner"),
            {"owner": catalog.owner_id},
        ).scalars().all() == ["regenerate_section"]
    assert _section_preview(catalog, document)["generation_route"]["status"] == "unqualified"
    with catalog.runtime.transaction() as conn:
        assert (
            generation.route_status(conn, catalog.owner_id, catalog.identity.settings)["status"]
            == "qualified"
        )


@pytest.mark.postgres
@pytest.mark.parametrize(
    "race",
    [
        "manual",
        "cancel",
        "lease",
        "delete",
        "profile",
        "requirements",
        "posting",
        "archive",
        "route",
        "settings",
    ],
)
def test_late_output_is_fenced_by_target_and_original_context_changes(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, race: str
) -> None:
    profile, application, records = _setup(catalog, monkeypatch, tmp_path)
    document = records[0]
    preview = _section_preview(catalog, document)
    task = _queue_section(catalog, preview)
    expected = document["current_revision"]["id"]

    def intervene() -> None:
        nonlocal expected
        if race == "manual":
            saved = _save(catalog, document, document["current_revision"]["body"])
            assert saved.status_code == 200
            expected = saved.json()["current_revision"]["id"]
        elif race == "cancel":
            with catalog.runtime.transaction() as conn:
                cancel_workflow(conn, catalog.owner_id, task.id)
        elif race == "profile":
            saved = catalog.client.put(
                "/api/v1/profile",
                json={**profile["current_revision"]["snapshot"], "contact": {"phone": "555 0199"}},
                headers=catalog.headers(profile["version"]),
            )
            assert saved.status_code == 200
        elif race == "requirements":
            _write(catalog, {"id": application["posting_id"]}, [_requirement(1, "Linux required.")])
        elif race == "route":
            route = _route(monkeypatch, tmp_path)
            changed = replace(
                route,
                _identity_json=canonical_json(
                    {**route.identity(), "fixture_changed": True}
                ).encode(),
            )
            monkeypatch.setattr(local_model, "load_route", lambda _: changed)
        else:
            with catalog.identity.admin.transaction() as conn:
                if race == "lease":
                    conn.execute(
                        text(
                            "UPDATE bonafides.workflow_runs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE owner_id=:owner AND id=:id"
                        ),
                        {"owner": catalog.owner_id, "id": task.id},
                    )
                elif race == "delete":
                    conn.execute(
                        text(
                            "UPDATE bonafides.document_drafts SET deleted_at=clock_timestamp(),version=version+1 WHERE owner_id=:owner AND id=:id"
                        ),
                        {"owner": catalog.owner_id, "id": UUID(document["id"])},
                    )
                elif race == "posting":
                    conn.execute(
                        text(
                            "UPDATE bonafides.job_postings SET description_text=description_text||' Changed context',version=version+1 WHERE owner_id=:owner AND id=:id"
                        ),
                        {"owner": catalog.owner_id, "id": UUID(application["posting_id"])},
                    )
                elif race == "archive":
                    conn.execute(
                        text(
                            "UPDATE bonafides.applications SET archived_at=clock_timestamp(),version=version+1 WHERE owner_id=:owner AND id=:id"
                        ),
                        {"owner": catalog.owner_id, "id": UUID(application["id"])},
                    )
                else:
                    conn.execute(
                        text(
                            "UPDATE bonafides.owner_settings SET version=version+1 WHERE owner_id=:owner"
                        ),
                        {"owner": catalog.owner_id},
                    )

    if race == "profile":
        _after_model_send(monkeypatch, intervene)
    model = FixtureModel(_output(preview), during=None if race == "profile" else intervene)
    with pytest.raises((Problem, DBAPIError)):
        suitability.execute_model(catalog.runtime, catalog.identity.settings, task, Event(), model)
    assert model.calls == 1
    with catalog.runtime.transaction() as conn:
        assert (
            str(
                conn.execute(
                    text(
                        "SELECT current_revision_id FROM bonafides.document_drafts WHERE owner_id=:owner AND id=:id"
                    ),
                    {"owner": catalog.owner_id, "id": UUID(document["id"])},
                ).scalar_one()
            )
            == expected
        )
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM bonafides.document_regeneration_proposals WHERE owner_id=:owner"
                ),
                {"owner": catalog.owner_id},
            ).scalar_one()
            == 0
        )
        assert (
            conn.execute(
                text(
                    "SELECT status FROM bonafides.model_runs WHERE owner_id=:owner AND workflow_run_id=:id"
                ),
                {"owner": catalog.owner_id, "id": task.id},
            ).scalar_one()
            == "unknown_usage"
        )


@pytest.mark.postgres
def test_other_pair_manual_change_does_not_discard_target_only_output(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, _, records = _setup(catalog, monkeypatch, tmp_path)
    document, other = records
    preview = _section_preview(catalog, document)
    task = _queue_section(catalog, preview)

    def intervene() -> None:
        saved = _save(catalog, other, other["current_revision"]["body"])
        assert saved.status_code == 200

    suitability.execute_model(
        catalog.runtime,
        catalog.identity.settings,
        task,
        Event(),
        FixtureModel(_output(preview), during=intervene),
    )
    assert catalog.workflow(task.id)["state"] == "succeeded"
    assert _document(catalog, document["id"]) == document
    proposal = catalog.client.get(
        f"/api/v1/documents/{document['id']}/regeneration-proposals"
    ).json()["items"][0]
    assert proposal["applicable"]
    assert _apply(catalog, document, proposal).status_code == 200


@pytest.mark.postgres
@pytest.mark.parametrize("mutation", ["save", "restore", "withdraw", "requirements", "delete"])
def test_apply_rechecks_identity_and_current_source_eligibility(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mutation: str
) -> None:
    profile, application, records = _setup(catalog, monkeypatch, tmp_path)
    document = records[0]
    _, proposal = _proposal(catalog, document)
    current = document
    expected = 409
    if mutation in {"save", "restore"}:
        saved = _save(catalog, current, current["current_revision"]["body"])
        assert saved.status_code == 200
        current = saved.json()
        if mutation == "restore":
            restored = catalog.client.post(
                f"/api/v1/documents/{document['id']}/restore",
                json={
                    "revision_id": document["current_revision"]["id"],
                    "reason": "Same bytes new identity",
                },
                headers=catalog.headers(current["version"]),
            )
            assert restored.status_code == 200
            current = restored.json()
            assert (
                current["current_revision"]["content_sha256"]
                == document["current_revision"]["content_sha256"]
            )
        expected = 412
    elif mutation == "withdraw":
        snapshot = copy.deepcopy(profile["current_revision"]["snapshot"])
        snapshot["claim_revision_ids"] = []
        snapshot["evidence_revision_ids"] = []
        saved = catalog.client.put(
            "/api/v1/profile", json=snapshot, headers=catalog.headers(profile["version"])
        )
        assert saved.status_code == 200, saved.text
        # Profile invalidation also advances the draft's optimistic version.
        expected = 412
        with catalog.runtime.transaction() as conn:
            generation.lock_workspace(conn, catalog.owner_id)
            with pytest.raises(Problem) as source_refused:
                section_regeneration._source_context(
                    conn,
                    catalog.owner_id,
                    UUID(document["id"]),
                    document["current_revision"]["body"]["sections"][0]["key"],
                    "professional",
                )
            assert source_refused.value.status == 409
    elif mutation == "requirements":
        _write(catalog, {"id": application["posting_id"]}, [_requirement(1, "Linux required.")])
    else:
        # Simulate the already-existing tombstone boundary, not an invented delete API.
        with catalog.identity.admin.transaction() as conn:
            conn.execute(
                text(
                    "UPDATE bonafides.document_drafts SET deleted_at=clock_timestamp(),version=version+1 WHERE owner_id=:owner AND id=:id"
                ),
                {"owner": catalog.owner_id, "id": UUID(document["id"])},
            )
        expected = 404
    response = _apply(catalog, current, proposal)
    assert response.status_code == expected, response.text
    fetched = catalog.client.get(
        f"/api/v1/documents/{document['id']}/regeneration-proposals/{proposal['id']}"
    )
    if mutation == "delete":
        assert fetched.status_code == 404
    else:
        assert fetched.status_code == 200 and not fetched.json()["applicable"]


@pytest.mark.postgres
def test_stored_qualified_proposal_can_be_applied_offline_and_cursor_is_document_scoped(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, _, records = _setup(catalog, monkeypatch, tmp_path)
    document = records[0]
    _, first = _proposal(catalog, document)
    _, second = _proposal(catalog, document)
    page = catalog.client.get(
        f"/api/v1/documents/{document['id']}/regeneration-proposals", params={"limit": 1}
    ).json()
    assert [item["id"] for item in page["items"]] == [second["id"]]
    assert page["next_cursor"] == second["id"]
    older = catalog.client.get(
        f"/api/v1/documents/{document['id']}/regeneration-proposals",
        params={"limit": 1, "before": page["next_cursor"]},
    ).json()
    assert [item["id"] for item in older["items"]] == [first["id"]] and older["next_cursor"] is None
    assert (
        catalog.client.get(
            f"/api/v1/documents/{records[1]['id']}/regeneration-proposals",
            params={"before": first["id"]},
        ).status_code
        == 404
    )
    assert (
        catalog.client.get(
            f"/api/v1/documents/{records[1]['id']}/regeneration-proposals/{first['id']}"
        ).status_code
        == 404
    )
    with catalog.runtime.transaction() as conn:
        lookups: tuple[Callable[[], object], ...] = (
            lambda: section_regeneration._proposal(
                conn, uuid4(), UUID(document["id"]), UUID(first["id"])
            ),
            lambda: section_regeneration._target(conn, uuid4(), UUID(document["id"])),
        )
        for lookup in lookups:
            with pytest.raises(Problem) as refused:
                lookup()
            assert refused.value.status == 404
    monkeypatch.setattr(local_model, "load_route", lambda _: None)
    applied = _apply(catalog, document, second)
    assert applied.status_code == 200, applied.text
    assert applied.json()["current_revision"]["origin"] == "manual"


@pytest.mark.postgres
def test_base_has_no_provider_lookup_and_unqualified_section_stage_cannot_enqueue(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile = _profile(catalog)
    base = _base(catalog, profile)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("base section regeneration attempted provider lookup")

    monkeypatch.setattr(local_model, "load_route", forbidden)
    response = catalog.client.get(
        f"/api/v1/documents/{base['id']}/regeneration-context",
        params={"section_key": base["current_revision"]["body"]["sections"][0]["key"]},
    )
    assert (
        response.status_code == 409
        and response.json()["code"] == "section_regeneration_requires_tailored_document"
    )
    response = catalog.client.post(
        f"/api/v1/documents/{base['id']}/regeneration-proposals",
        json={
            "revision_id": base["current_revision"]["id"],
            "content_sha256": base["current_revision"]["content_sha256"],
            "section_key": base["current_revision"]["body"]["sections"][0]["key"],
            "tone": "concise",
            "context_sha256": "0" * 64,
        },
        headers=catalog.headers(base["version"]),
    )
    assert (
        response.status_code == 409
        and response.json()["code"] == "section_regeneration_requires_tailored_document"
    )
    with catalog.runtime.transaction() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM bonafides.model_runs WHERE owner_id=:owner"),
                {"owner": catalog.owner_id},
            ).scalar_one()
            == 0
        )
    monkeypatch.undo()
    _, _, records = _setup(catalog, monkeypatch, tmp_path)
    route = _route(monkeypatch, tmp_path)
    generation_only = replace(route, _qualified=frozenset({"generate_documents"}))
    monkeypatch.setattr(local_model, "load_route", lambda _: generation_only)
    preview = _section_preview(catalog, records[0])
    assert preview["generation_route"]["status"] == "unqualified"
    response = catalog.client.post(
        f"/api/v1/documents/{records[0]['id']}/regeneration-proposals",
        json=_request_body(preview),
        headers=catalog.headers(records[0]["version"]),
    )
    assert (
        response.status_code == 503 and response.json()["code"] == "regeneration_route_unavailable"
    )


@pytest.mark.postgres
def test_preview_context_admission_rejects_changed_profile_and_stale_document(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile, _, records = _setup(catalog, monkeypatch, tmp_path)
    document = records[0]
    preview = _section_preview(catalog, document)
    changed = catalog.client.put(
        "/api/v1/profile",
        json={**profile["current_revision"]["snapshot"], "contact": {"phone": "555 0101"}},
        headers=catalog.headers(profile["version"]),
    )
    assert changed.status_code == 200
    response = catalog.client.post(
        f"/api/v1/documents/{document['id']}/regeneration-proposals",
        json=_request_body(preview),
        headers=catalog.headers(document["version"]),
    )
    assert response.status_code == 412
    current = _document(catalog, document["id"])
    assert current["current_revision"] == document["current_revision"]
    response = catalog.client.post(
        f"/api/v1/documents/{document['id']}/regeneration-proposals",
        json=_request_body(preview),
        headers=catalog.headers(current["version"]),
    )
    assert response.status_code == 409 and response.json()["code"] == "regeneration_context_changed"
    saved = _save(catalog, current, current["current_revision"]["body"])
    assert saved.status_code == 200
    response = catalog.client.post(
        f"/api/v1/documents/{document['id']}/regeneration-proposals",
        json=_request_body(preview),
        headers=catalog.headers(document["version"]),
    )
    assert response.status_code == 412
    with catalog.runtime.transaction() as conn:
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM bonafides.workflow_runs WHERE owner_id=:owner AND input->>'mode'='section_regeneration'"
                ),
                {"owner": catalog.owner_id},
            ).scalar_one()
            == 0
        )


@pytest.mark.postgres
def test_section_model_attempt_budget_is_cumulative_across_reclaims(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, _, records = _setup(catalog, monkeypatch, tmp_path)
    preview = _section_preview(catalog, records[0])
    task = _queue_section(catalog, preview)
    model = FixtureModel(
        _output(preview), failure=local_model.ModelFailure("model_unavailable", retryable=True)
    )
    for index in range(2):
        with pytest.raises(local_model.ModelFailure):
            suitability.execute_model(
                catalog.runtime, catalog.identity.settings, task, Event(), model
            )
        with catalog.identity.admin.transaction() as conn:
            conn.execute(
                text(
                    "UPDATE bonafides.local_model_reservations SET reserved_until=clock_timestamp()-interval '1 second' WHERE singleton"
                )
            )
            if index == 0:
                conn.execute(
                    text(
                        "UPDATE bonafides.workflow_runs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE owner_id=:owner AND id=:id"
                    ),
                    {"owner": catalog.owner_id, "id": task.id},
                )
        if index == 0:
            with catalog.runtime.transaction() as conn:
                reap(conn)
            with catalog.identity.admin.transaction() as conn:
                conn.execute(
                    text(
                        "UPDATE bonafides.workflow_runs SET next_attempt_at=clock_timestamp() WHERE owner_id=:owner AND id=:id"
                    ),
                    {"owner": catalog.owner_id, "id": task.id},
                )
            task = _claim(catalog, str(task.id))
    with pytest.raises(Problem, match="model_attempt_or_deadline_exhausted"):
        suitability.execute_model(catalog.runtime, catalog.identity.settings, task, Event(), model)
    assert model.calls == 2
    with catalog.runtime.transaction() as conn:
        assert (
            conn.execute(
                text(
                    "SELECT model_network_attempts FROM bonafides.workflow_runs WHERE owner_id=:owner AND id=:id"
                ),
                {"owner": catalog.owner_id, "id": task.id},
            ).scalar_one()
            == 2
        )
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM bonafides.document_regeneration_proposals WHERE owner_id=:owner"
                ),
                {"owner": catalog.owner_id},
            ).scalar_one()
            == 0
        )


@pytest.mark.postgres
def test_worker_dispatches_section_proposal_without_automatic_document_publication(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, _, records = _setup(catalog, monkeypatch, tmp_path)
    document = records[0]
    preview = _section_preview(catalog, document)
    task = _queue_section(catalog, preview)
    model = FixtureModel(_output(preview))
    monkeypatch.setattr(local_model, "OllamaClient", lambda _route, _lifecycle: model)
    Worker(catalog.runtime, catalog.identity.settings)._execute(task, Event())
    assert model.calls == 1 and catalog.workflow(task.id)["state"] == "succeeded"
    assert _document(catalog, document["id"]) == document


@pytest.mark.postgres
def test_same_owner_new_profile_facts_cannot_escape_original_parent_pin(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile, _, records = _setup(catalog, monkeypatch, tmp_path)
    expanded = _profile(catalog)
    snapshot = copy.deepcopy(expanded["current_revision"]["snapshot"])
    new_claim = snapshot["claim_revision_ids"][0]
    snapshot["claim_revision_ids"] = [
        *profile["current_revision"]["snapshot"]["claim_revision_ids"],
        *snapshot["claim_revision_ids"],
    ]
    changed = catalog.client.put(
        "/api/v1/profile", json=snapshot, headers=catalog.headers(expanded["version"])
    )
    assert changed.status_code == 200, changed.text
    preview = _section_preview(catalog, records[0])
    assert new_claim not in canonical_json(preview["model_projection"])
    task = _queue_section(catalog, preview)
    output = _output(preview)
    output["blocks"][0]["claim_revision_ids"] = [new_claim]
    with pytest.raises(Problem) as refused:
        suitability.execute_model(
            catalog.runtime, catalog.identity.settings, task, Event(), FixtureModel(output)
        )
    assert refused.value.code == "reference_outside_pinned_context"
    assert (
        _document(catalog, records[0]["id"])["current_revision"] == records[0]["current_revision"]
    )


@pytest.mark.postgres
def test_pinned_posting_context_refuses_preview_instead_of_rebasing(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, application, records = _setup(catalog, monkeypatch, tmp_path)
    document = records[0]
    old_frame = document["current_revision"]["provenance_snapshot"]["frame"]
    _write(catalog, {"id": application["posting_id"]}, [_requirement(1, "Linux required.")])
    response = catalog.client.get(
        f"/api/v1/documents/{document['id']}/regeneration-context",
        params={"section_key": document["current_revision"]["body"]["sections"][0]["key"]},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "stale_regeneration_posting_context"
    assert (
        _document(catalog, document["id"])["current_revision"]["provenance_snapshot"]["frame"]
        == old_frame
    )
    with catalog.runtime.transaction() as conn:
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM bonafides.workflow_runs WHERE owner_id=:owner AND input->>'mode'='section_regeneration'"
                ),
                {"owner": catalog.owner_id},
            ).scalar_one()
            == 0
        )
