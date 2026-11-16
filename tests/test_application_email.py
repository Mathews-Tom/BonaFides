# SPDX-License-Identifier: MIT OR Apache-2.0
"""Application email API contracts, using only the deterministic local stub."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from test_checked_generation import _render, _run
from test_checked_section_regeneration import _resume
from test_document_generation import _claim, _document, _pair_documents, _save
from test_identity import identity as identity
from test_sources import SourceContext
from test_sources import catalog as catalog
from test_suitability import _after_model_send

from bonafides import document_conversations, document_generation, documents, suitability
from bonafides.db import SCHEMA_VERSION
from bonafides.problems import Problem
from bonafides.workflows import Claim, cancel_workflow
from tools import stub_model


def _queue(
    context: SourceContext, application: dict[str, Any], email: dict[str, Any] | None = None
) -> Claim:
    path = (
        f"/api/v1/documents/{email['id']}/rewrite"
        if email
        else f"/api/v1/applications/{application['id']}/email"
    )
    version = email["version"] if email else _pair_documents(context, application)["version"]
    response = context.client.post(path, json={}, headers=context.headers(version))
    assert response.status_code == 202, response.text
    return _claim(context, response.json()["id"])


def _generate(context: SourceContext, application: dict[str, Any]) -> dict[str, Any]:
    result = _run(context, _queue(context, application))
    assert result["state"] == "succeeded", result
    response = context.client.get(f"/api/v1/applications/{application['id']}/email")
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def _history(context: SourceContext, email: dict[str, Any]) -> list[dict[str, Any]]:
    return cast(
        list[dict[str, Any]],
        context.client.get(f"/api/v1/documents/{email['id']}/revisions").json()["items"],
    )


def _restore(context: SourceContext, email: dict[str, Any], revision: str) -> Any:
    return context.client.post(
        f"/api/v1/documents/{email['id']}/restore",
        json={"revision_id": revision, "reason": "Undo email rewrite"},
        headers=context.headers(email["version"]),
    )


@pytest.mark.postgres
def test_email_generate_edit_rewrite_exact_restore_and_unchanged_tracking(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with _resume(catalog, monkeypatch, tmp_path) as (_, application, _):
        before = catalog.client.get(f"/api/v1/applications/{application['id']}").json()
        email = _generate(catalog, application)
        assert email["kind"] == "application_email" and not email["unsupported_claims"]
        parts = email["current_revision"]["email"]
        assert parts == {
            "recipient": "",
            "subject": f"Application: {email['current_revision']['provenance_snapshot']['frame']['role']}",
        }
        body = copy.deepcopy(email["current_revision"]["body"])
        body["sections"][0]["blocks"].reverse()
        saved = _save(
            catalog,
            email,
            body,
            email={"recipient": "owner-selected@example.org", "subject": "My application"},
        )
        assert saved.status_code == 200, saved.text
        manual = saved.json()
        assert (
            manual["current_revision"]["content_sha256"]
            != email["current_revision"]["content_sha256"]
        )
        assert _save(catalog, email, body).status_code == 412
        task = _queue(catalog, application, manual)
        # Owner edits made while a rewrite is running are captured as its undo source.
        latest = _save(
            catalog,
            manual,
            body,
            email={"recipient": "latest@example.org", "subject": "While running"},
        ).json()
        result = _run(catalog, task)
        assert result["state"] == "succeeded", result
        rewritten = _document(catalog, email["id"])
        assert rewritten["current_revision"]["email"]["recipient"] == "latest@example.org"
        assert result["result"]["restore_revision_id"] == latest["current_revision"]["id"]
        restored = _restore(catalog, rewritten, latest["current_revision"]["id"])
        assert restored.status_code == 200, restored.text
        assert restored.json()["current_revision"]["body"] == latest["current_revision"]["body"]
        assert restored.json()["current_revision"]["email"] == latest["current_revision"]["email"]
        assert len(_history(catalog, email)) == 5
        after = catalog.client.get(f"/api/v1/applications/{application['id']}").json()
        assert after == before
        with catalog.runtime.transaction() as conn:
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM bonafides.document_drafts WHERE kind='application_email'"
                    )
                ).scalar_one()
                == 1
            )
            assert (
                conn.execute(
                    text("SELECT max(version) FROM bonafides.schema_migrations")
                ).scalar_one()
                == SCHEMA_VERSION
                == 10
            )


@pytest.mark.postgres
def test_email_restore_refuses_later_edit_even_with_current_etag(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with _resume(catalog, monkeypatch, tmp_path) as (_, app, _):
        email = _generate(catalog, app)
        assert _run(catalog, _queue(catalog, app, email))["state"] == "succeeded"
        rewritten = _document(catalog, email["id"])
        changed = _save(
            catalog,
            rewritten,
            rewritten["current_revision"]["body"],
            email={"recipient": "", "subject": "Later edit"},
        ).json()
        assert _restore(catalog, changed, email["current_revision"]["id"]).status_code == 409
        assert len(_history(catalog, email)) == 3
        assert _document(catalog, email["id"]) == changed


@pytest.mark.postgres
@pytest.mark.parametrize("scenario", ["absent-claim", "malformed", "truncated", "recipient"])
def test_email_failure_keeps_content_and_never_writes_recipient(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, scenario: str
) -> None:
    with _resume(catalog, monkeypatch, tmp_path) as (stub, app, _):
        email = _generate(catalog, app)
        manual = _save(
            catalog,
            email,
            email["current_revision"]["body"],
            email={"recipient": "manual@example.org", "subject": "Keep this"},
        ).json()
        task = _queue(catalog, app, manual)
        if scenario == "recipient":
            respond = stub_model.respond

            def inject(selected: str, request: dict[str, object]) -> dict[str, object]:
                import json

                envelope = respond(selected, request)
                message = envelope["message"]
                assert isinstance(message, dict)
                output = json.loads(message["content"])
                output["recipient"] = "invented@example.org"
                message["content"] = json.dumps(output)
                return envelope

            monkeypatch.setattr(stub_model, "respond", inject)
        else:
            stub.scenario = scenario
        result = _run(catalog, task)
        assert result["state"] != "succeeded", result
        assert _document(catalog, email["id"]) == manual
        assert len(_history(catalog, email)) == 2
        if scenario != "truncated":
            assert len(stub.requests) == 4  # pair, initial email, rewrite and one correction


@pytest.mark.postgres
@pytest.mark.parametrize("race", ["cancel", "lease", "duplicate"])
def test_email_durable_publication_fences(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, race: str
) -> None:
    with _resume(catalog, monkeypatch, tmp_path) as (_, app, _):
        task = _queue(catalog, app)
        path = f"/api/v1/applications/{app['id']}/email"
        replay = catalog.client.post(
            path, json={}, headers=catalog.headers(_pair_documents(catalog, app)["version"])
        )
        assert replay.status_code == 202 and replay.json()["id"] == str(task.id)
        if race != "duplicate":

            def intervene() -> None:
                if race == "cancel":
                    with catalog.runtime.transaction() as conn:
                        cancel_workflow(conn, catalog.owner_id, task.id)
                else:
                    with catalog.identity.admin.transaction() as conn:
                        conn.execute(
                            text(
                                "UPDATE bonafides.workflow_runs SET fence=fence+1,lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=:id"
                            ),
                            {"id": task.id},
                        )

            _after_model_send(monkeypatch, intervene)
        result = _run(catalog, task)
        if race == "duplicate":
            assert result["state"] == "succeeded", result
            _run(catalog, task)
        else:
            assert result["state"] != "succeeded", result
        with catalog.runtime.transaction() as conn:
            assert conn.execute(
                text(
                    "SELECT count(*) FROM bonafides.document_drafts WHERE kind='application_email'"
                )
            ).scalar_one() == (1 if race == "duplicate" else 0)
            assert conn.execute(
                text(
                    "SELECT count(*) FROM bonafides.document_revisions r JOIN bonafides.document_drafts d ON d.id=r.document_draft_id AND d.owner_id=r.owner_id WHERE d.kind='application_email'"
                )
            ).scalar_one() == (1 if race == "duplicate" else 0)


@pytest.mark.postgres
def test_email_refuses_print_section_frame_and_excludes_pair_conversation_archive(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with _resume(catalog, monkeypatch, tmp_path) as (_, app, _):
        email = _generate(catalog, app)
        assert _render(catalog, email).status_code == 422
        assert (
            _save(
                catalog,
                email,
                email["current_revision"]["body"],
                letter_frame={
                    "recipient": "Someone",
                    "greeting": "Hello",
                    "closing": "Regards",
                    "correspondence_date": "2026-11-16",
                },
            ).status_code
            == 422
        )
        section = catalog.client.get(
            f"/api/v1/documents/{email['id']}/regeneration-context",
            params={"section_key": "email-body"},
        )
        assert section.status_code == 422, section.text
        assert {item["kind"] for item in _pair_documents(catalog, app)["items"]} == {
            "resume",
            "cover_letter",
        }
        with catalog.runtime.transaction() as conn:
            context = document_conversations.context_view(
                conn, catalog.owner_id, UUID(app["id"]), catalog.identity.settings
            )
            assert {item["kind"] for item in cast(list[dict[str, Any]], context["documents"])} == {
                "resume",
                "cover_letter",
            }
            request = document_conversations.ConversationRequest.model_validate_json(
                documents.canonical(
                    {
                        "message": "Rewrite",
                        "document_ids": [email["id"]],
                        "context_sha256": context["context_sha256"],
                    }
                )
            )
            with pytest.raises(Problem) as refused:
                document_conversations.request_turn(
                    conn, catalog.owner_id, UUID(app["id"]), request, catalog.identity.settings
                )
            assert refused.value.status == 422
            with pytest.raises(Problem, match="application_email_not_printable"):
                document_generation.export_eligibility(
                    conn, catalog.owner_id, email["current_revision"]
                )
            with pytest.raises(Problem) as foreign:
                documents._document(conn, uuid4(), UUID(email["id"]))
            assert foreign.value.status == 404
        # Unreviewed printable members still block the archive: email is never a member.
        from test_document_generation import _review

        for item in _pair_documents(catalog, app)["items"]:
            assert _review(catalog, item).status_code == 200
        manifest = catalog.client.get("/api/v1/document-archives/preview")
        assert manifest.status_code == 200, manifest.text
        assert "application_email" not in manifest.text
        assert catalog.client.post(
            f"/api/v1/documents/{email['id']}/send",
            json={},
            headers=catalog.headers(email["version"]),
        ).status_code in {404, 405}
        catalog.client.cookies.clear()
        assert catalog.client.get(f"/api/v1/documents/{email['id']}").status_code == 401
        assert catalog.client.get(f"/api/v1/applications/{app['id']}/email").status_code == 401


@pytest.mark.postgres
def test_email_route_block_does_not_change_suitability(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with _resume(catalog, monkeypatch, tmp_path), catalog.runtime.transaction() as conn:
        before = suitability._policy(conn, catalog.owner_id, catalog.identity.settings)
        route = suitability._route_status(conn, catalog.owner_id, catalog.identity.settings)
        conn.execute(
            text(
                "INSERT INTO bonafides.local_model_route_blocks(owner_id,route_sha256,stage,error_code) VALUES(:owner,:sha,'write_application_email','model_output_invalid_after_correction')"
            ),
            {"owner": catalog.owner_id, "sha": document_generation.sha(route["identity"])},
        )
        assert suitability._policy(conn, catalog.owner_id, catalog.identity.settings) == before
        assert suitability._route_status(conn, catalog.owner_id, catalog.identity.settings) == route
