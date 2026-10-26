# SPDX-License-Identifier: MIT OR Apache-2.0
"""Persistent selections must not rewrite pinned documents or authorize external work."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from sqlalchemy import text
from test_document_generation import (
    _base,
    _candidate,
    _claim,
    _document,
    _pair_documents,
    _preview,
    _queue_base,
    _queue_pair,
    _review,
    _save,
    _setup_pair,
)
from test_identity import identity as identity
from test_sources import SourceContext
from test_sources import catalog as catalog
from test_suitability import FixtureModel, _profile, _route

from offerloom import documents, suitability
from offerloom.auth import Owner, require_owner
from offerloom.commands import canonical_json
from offerloom.document_frame import template_digest
from offerloom.storage import BlobStore
from offerloom.worker import Worker
from offerloom.workflows import publish


def _stored(context: SourceContext) -> dict[str, Any]:
    with context.runtime.transaction() as conn:
        return dict(
            conn.execute(
                text("SELECT * FROM offerloom.owner_settings WHERE owner_id=:owner"),
                {"owner": context.owner_id},
            )
            .mappings()
            .one()
        )


def _defaults(context: SourceContext) -> dict[str, Any]:
    response = context.client.get("/api/v1/document-defaults")
    assert response.status_code == 200, response.text
    assert response.headers["etag"] == f'"{response.json()["version"]}"'
    assert "no-store" in response.headers["cache-control"]
    return cast(dict[str, Any], response.json())


@pytest.mark.postgres
def test_partial_defaults_preserve_other_preferences_and_replay_without_rollback(
    catalog: SourceContext,
) -> None:
    unrelated = {"ui": {"theme": "dark"}, "private_owner_note": "unchanged"}
    with catalog.runtime.transaction() as conn:
        conn.execute(
            text(
                "UPDATE offerloom.owner_settings SET settings=CAST(:settings AS jsonb) "
                "WHERE owner_id=:owner"
            ),
            {"owner": catalog.owner_id, "settings": canonical_json(unrelated)},
        )
    before = _stored(catalog)
    original = _defaults(catalog)
    assert _stored(catalog) == before, "Reading unset defaults must not mutate the owner."
    assert original["template_version"] == "offerloom-evidence-v1"
    assert original["tone"] == "professional"

    headers = catalog.headers(original["version"])
    first = catalog.client.patch(
        "/api/v1/document-defaults", json={"tone": "concise"}, headers=headers
    )
    assert first.status_code == 200, first.text
    assert first.json()["tone"] == "concise"
    assert first.json()["template_version"] == original["template_version"]
    assert first.headers["etag"] == f'"{first.json()["version"]}"'
    second = catalog.client.patch(
        "/api/v1/document-defaults",
        json={"template_version": "offerloom-compact-letter-v1"},
        headers=catalog.headers(first.json()["version"]),
    )
    assert second.status_code == 200, second.text
    assert second.json()["tone"] == "concise"
    assert second.json()["template_version"] == "offerloom-compact-letter-v1"
    saved = _stored(catalog)
    assert saved["settings"] == {
        **unrelated,
        "document_defaults": {
            "template_version": "offerloom-compact-letter-v1",
            "tone": "concise",
        },
    }
    assert saved["version"] == before["version"]
    assert saved["privacy_mode"] == before["privacy_mode"]

    replay = catalog.client.patch(
        "/api/v1/document-defaults", json={"tone": "concise"}, headers=headers
    )
    assert replay.status_code == 200 and replay.json() == first.json()
    assert _stored(catalog) == saved, "A late replay must not roll back the latest selections."
    mismatch = catalog.client.patch(
        "/api/v1/document-defaults", json={"tone": "professional"}, headers=headers
    )
    assert mismatch.status_code == 409 and mismatch.json()["code"] == "idempotency_payload_mismatch"
    stale = catalog.client.patch(
        "/api/v1/document-defaults",
        json={"tone": "professional"},
        headers=catalog.headers(first.json()["version"]),
    )
    assert stale.status_code == 412 and stale.json()["code"] == "version_conflict"
    assert _stored(catalog) == saved
    with catalog.identity.client() as reopened:
        reopened.cookies.update(catalog.client.cookies)
        loaded = reopened.get("/api/v1/document-defaults")
        assert loaded.status_code == 200 and loaded.json() == second.json()


@pytest.mark.postgres
@pytest.mark.parametrize(
    "body",
    [
        {},
        {"tone": None},
        {"template_version": None},
        {"tone": "casual"},
        {"tone": 1},
        {"template_version": "offerloom-base-v1"},
        {"template_version": "file:///tmp/owned.html"},
        {"template_version": ""},
        {"language": "fr"},
        {"revision_retention": "one_day"},
        {"task_input_retention_seconds": 172800},
        {"extension_supported": True},
        {"owner_id": str(uuid4()), "tone": "concise"},
        {"tone": "concise", "unknown": True},
    ],
)
def test_invalid_defaults_changes_are_rejected_without_mutation(
    catalog: SourceContext,
    body: dict[str, object],
) -> None:
    before = _stored(catalog)
    rejected = catalog.client.patch(
        "/api/v1/document-defaults", json=body, headers=catalog.headers(before["workspace_version"])
    )
    assert rejected.status_code == 422, rejected.text
    assert _stored(catalog) == before


@pytest.mark.postgres
def test_defaults_require_authentication_origin_csrf_and_write_preconditions(
    catalog: SourceContext,
) -> None:
    path = "/api/v1/document-defaults"
    change = {"tone": "concise"}
    current = _defaults(catalog)
    before = _stored(catalog)
    with catalog.identity.client() as unauthenticated:
        assert unauthenticated.get(path).status_code == 401
        rejected = unauthenticated.patch(
            path, json=change, headers={"Origin": catalog.identity.settings.origin}
        )
        assert rejected.status_code == 401
    for header, value, code in (
        ("Origin", "https://attacker.invalid", "origin_rejected"),
        ("X-CSRF-Token", "not-the-session-token", "csrf_rejected"),
    ):
        headers = {**catalog.headers(current["version"]), header: value}
        rejected = catalog.client.patch(path, json=change, headers=headers)
        assert rejected.status_code == 403 and rejected.json()["code"] == code
    missing_csrf = catalog.headers(current["version"])
    del missing_csrf["X-CSRF-Token"]
    assert catalog.client.patch(path, json=change, headers=missing_csrf).status_code == 403
    missing_origin = catalog.headers(current["version"])
    del missing_origin["Origin"]
    assert catalog.client.patch(path, json=change, headers=missing_origin).status_code == 403
    assert catalog.client.patch(path, json=change, headers=catalog.headers()).status_code == 428
    malformed = {**catalog.headers(current["version"]), "If-Match": str(current["version"])}
    assert catalog.client.patch(path, json=change, headers=malformed).status_code == 400
    no_key = catalog.headers(current["version"])
    del no_key["Idempotency-Key"]
    rejected = catalog.client.patch(path, json=change, headers=no_key)
    assert rejected.status_code == 400 and rejected.json()["code"] == "invalid_idempotency_key"
    assert _stored(catalog) == before


@pytest.mark.postgres
def test_foreign_authenticated_principal_cannot_read_or_change_singleton_owner_defaults(
    catalog: SourceContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _stored(catalog)
    foreign = Owner(uuid4(), "foreign", uuid4(), "unused", datetime.now(UTC), datetime.now(UTC), 1)
    application = cast(FastAPI, catalog.client.app)
    overrides = application.dependency_overrides.copy()
    overrides[require_owner] = lambda: foreign
    monkeypatch.setattr(application, "dependency_overrides", overrides)
    read = catalog.client.get("/api/v1/document-defaults")
    assert read.status_code == 404 and read.json()["code"] == "owner_not_found"
    write = catalog.client.patch(
        "/api/v1/document-defaults",
        json={"tone": "concise"},
        headers=catalog.headers(before["workspace_version"]),
    )
    assert write.status_code == 404 and write.json()["code"] == "owner_not_found"
    assert _stored(catalog) == before


@pytest.mark.postgres
@pytest.mark.parametrize(
    "corrupt",
    [
        None,
        [],
        {},
        {"tone": "concise"},
        {"template_version": "removed-template", "tone": "professional"},
        {"template_version": "offerloom-evidence-v1", "tone": "casual"},
        {"template_version": "offerloom-evidence-v1", "tone": "professional", "extra": True},
    ],
)
def test_corrupted_stored_defaults_fail_explicitly_without_fallback_or_partial_repair(
    catalog: SourceContext,
    corrupt: object,
) -> None:
    with catalog.runtime.transaction() as conn:
        conn.execute(
            text(
                "UPDATE offerloom.owner_settings SET settings=CAST(:settings AS jsonb) "
                "WHERE owner_id=:owner"
            ),
            {"owner": catalog.owner_id, "settings": canonical_json({"document_defaults": corrupt})},
        )
    before = _stored(catalog)
    read = catalog.client.get("/api/v1/document-defaults")
    assert read.status_code == 409 and read.json()["code"] == "invalid_document_defaults"
    write = catalog.client.patch(
        "/api/v1/document-defaults",
        json={"tone": "concise"},
        headers=catalog.headers(before["workspace_version"]),
    )
    assert write.status_code == 409 and write.json()["code"] == "invalid_document_defaults"
    assert _stored(catalog) == before


def _pinned_rows(context: SourceContext) -> dict[str, object]:
    with context.runtime.transaction() as conn:
        return {
            table: (
                conn.execute(
                    text(
                        f"SELECT row_to_json(r) FROM offerloom.{table} r "
                        "WHERE owner_id=:owner ORDER BY id"
                    ),
                    {"owner": context.owner_id},
                )
                .scalars()
                .all()
            )
            for table in (
                "profiles",
                "profile_revisions",
                "document_drafts",
                "document_revisions",
                "document_reviews",
                "document_artifacts",
                "blobs",
                "action_approvals",
                "hosted_egress_consents",
                "source_configs",
            )
        }


@pytest.mark.postgres
def test_defaults_do_not_mutate_saved_pins_artifacts_or_pending_explicit_generation(
    catalog: SourceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    profile, application = _setup_pair(catalog)
    _route(monkeypatch, tmp_path)
    document = _base(catalog, profile)
    reviewed = _review(catalog, document)
    assert reviewed.status_code == 200, reviewed.text
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
    render_task = _claim(catalog, queued.json()["id"])
    # A storage/publication fixture, not a synthetic claim about actual PDF geometry.
    payload = b"%PDF-1.7\nsynthetic immutable artifact fixture\n%%EOF"
    blob = BlobStore(catalog.identity.settings.blob_root).put(catalog.owner_id, payload)
    with catalog.runtime.transaction() as conn:
        prepared = documents.prepare_render(
            conn, catalog.owner_id, UUID(document["id"]), UUID(revision["id"])
        )
        result = publish(
            conn,
            render_task,
            lambda active: documents.publish_artifact(
                active, catalog.owner_id, render_task.id, prepared, blob
            ),
        )
    artifact_id = result["artifact_id"]
    before_document = _document(catalog, document["id"])
    preview = _preview(catalog, profile, application)
    pending = _queue_pair(catalog, preview)
    before_rows = _pinned_rows(catalog)
    defaults = _defaults(catalog)
    changed = catalog.client.patch(
        "/api/v1/document-defaults",
        json={"template_version": "offerloom-compact-letter-v1", "tone": "concise"},
        headers=catalog.headers(defaults["version"]),
    )
    assert changed.status_code == 200, changed.text
    assert _pinned_rows(catalog) == before_rows
    assert _document(catalog, document["id"]) == before_document
    historical = catalog.client.get(
        f"/api/v1/documents/{document['id']}/revisions/{revision['id']}"
    )
    assert historical.status_code == 200 and historical.json() == revision
    downloaded = catalog.client.get(f"/api/v1/artifacts/{artifact_id}")
    assert downloaded.status_code == 200 and downloaded.content == payload
    with catalog.runtime.transaction() as conn:
        assert (
            documents.prepare_render(
                conn, catalog.owner_id, UUID(document["id"]), UUID(revision["id"])
            )
            == prepared
        )
    assert _preview(catalog, profile, application) == preview
    suitability.execute_model(
        catalog.runtime,
        catalog.identity.settings,
        pending,
        Event(),
        FixtureModel(_candidate(preview)),
    )
    for saved in _pair_documents(catalog, application)["items"]:
        current = saved["current_revision"]
        assert current["template_version"] == preview["template_version"]
        assert current["provenance_snapshot"]["tone"] == preview["tone"]
        assert current["provenance_snapshot"]["template_sha256"] == template_digest(
            preview["template_version"]
        )


@pytest.mark.postgres
@pytest.mark.parametrize(
    "template", ["offerloom-evidence-letter-v1", "offerloom-compact-letter-v1"]
)
def test_explicit_letter_generation_pins_selected_template_and_printable_frame(
    catalog: SourceContext,
    template: str,
) -> None:
    profile = _profile(catalog)
    task = _queue_base(catalog, profile, template)
    Worker(catalog.runtime, replace(catalog.identity.settings, mode="production"))._execute(
        task, Event()
    )
    workflow = catalog.workflow(task.id)
    assert workflow["state"] == "succeeded", workflow
    document = _document(catalog, workflow["result"]["document_id"])
    revision = document["current_revision"]
    assert revision["template_version"] == template
    assert revision["provenance_snapshot"]["template_sha256"] == template_digest(template)
    assert revision["provenance_snapshot"]["frame"] == task.input["frame"]
    facts = cast(list[dict[str, object]], task.input["facts"])
    assert [
        block["text"] for section in revision["body"]["sections"] for block in section["blocks"]
    ] == list(dict.fromkeys(fact["printable_text"] for fact in facts))
    assert _review(catalog, document).status_code == 200
    with catalog.runtime.transaction() as conn:
        rendered = documents.prepare_render(
            conn, catalog.owner_id, UUID(document["id"]), UUID(revision["id"])
        )
    assert rendered.template_version == template
    assert rendered.template_sha256 == template_digest(template)
    assert rendered.frame is not None
    assert rendered.frame.model_dump(mode="json") == task.input["frame"]


@pytest.mark.postgres
def test_retained_history_has_no_fifty_revision_cap_and_old_restore_preserves_displaced_state(
    catalog: SourceContext,
) -> None:
    document = _base(catalog, _profile(catalog))
    original = document["current_revision"]
    expected = {original["id"]: original}
    for revision_no in range(2, 53):
        body = {**document["current_revision"]["body"], "title": f"Saved history {revision_no}"}
        saved = _save(catalog, document, body)
        assert saved.status_code == 200, saved.text
        document = saved.json()
        revision = document["current_revision"]
        expected[revision["id"]] = revision
    observed: dict[str, Any] = {}
    cursor = ""
    while True:
        response = catalog.client.get(
            f"/api/v1/documents/{document['id']}/revisions?limit=20{cursor}"
        )
        assert response.status_code == 200, response.text
        page = response.json()
        for item in page["items"]:
            assert item["id"] not in observed
            observed[item["id"]] = item
        if page["next_cursor"] is None:
            break
        cursor = f"&before={page['next_cursor']}"
    assert observed == expected
    assert _defaults(catalog)["revision_retention"] == "until_owner_erasure"
    displaced = document["current_revision"]
    restored = catalog.client.post(
        f"/api/v1/documents/{document['id']}/restore",
        json={"revision_id": original["id"], "reason": "Explicit retained history restore"},
        headers=catalog.headers(document["version"]),
    )
    assert restored.status_code == 200, restored.text
    current = restored.json()["current_revision"]
    assert current["revision_no"] == 53
    assert current["content_sha256"] == original["content_sha256"]
    assert current["provenance_snapshot"] == original["provenance_snapshot"]
    for retained in (original, displaced):
        response = catalog.client.get(
            f"/api/v1/documents/{document['id']}/revisions/{retained['id']}"
        )
        assert response.status_code == 200 and response.json() == retained
