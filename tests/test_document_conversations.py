# SPDX-License-Identifier: MIT OR Apache-2.0
"""Real PostgreSQL/API local conversation boundaries with synthetic owner data."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from test_document_generation import _save
from test_identity import identity as identity
from test_section_regeneration import _setup
from test_sources import SourceContext
from test_sources import catalog as catalog
from test_tracking import _application, _get, _write

from bonafides import document_generation as generation
from bonafides.document_conversations import _local_reply, context_view
from bonafides.portable_previews import _counts as deletion_counts
from bonafides.problems import Problem


def _view(context: SourceContext, application: dict[str, Any]) -> dict[str, Any]:
    response = context.client.get(f"/api/v1/applications/{application['id']}/conversation")
    assert response.status_code == 200, response.text
    assert "no-store" in response.headers["cache-control"]
    return cast(dict[str, Any], response.json())


def _counts(context: SourceContext) -> list[object]:
    with context.runtime.transaction() as conn:
        return list(
            conn.execute(
                text("""SELECT
            (SELECT count(*) FROM bonafides.workflow_runs),
            (SELECT count(*) FROM bonafides.model_runs),
            (SELECT count(*) FROM bonafides.document_revisions),
            (SELECT count(*) FROM bonafides.document_conversation_turns),
            (SELECT count(*) FROM bonafides.idempotency_records),
            (SELECT array_agg(version ORDER BY id) FROM bonafides.applications),
            (SELECT array_agg(workspace_version ORDER BY owner_id) FROM bonafides.owner_settings)
            """)
            ).one()
        )


@pytest.mark.postgres
def test_local_guidance_persists_reloads_without_model_work_or_edits(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, application, documents = _setup(catalog, monkeypatch, tmp_path)
    application = _write(
        catalog, _get(catalog, application), "notes", {"body": "SYNTHETIC PRIVATE NOTE"}
    )
    application = _write(
        catalog,
        application,
        "contacts",
        {"display_name": "SYNTHETIC PRIVATE CONTACT", "email": None, "role_label": None},
    )
    current = _view(catalog, application)
    before = _counts(catalog)
    path = f"/api/v1/applications/{application['id']}/conversation"
    body = {
        "message": "  Synthetic request about my documents.  ",
        "context_sha256": current["context_sha256"],
    }
    headers = catalog.headers(current["application_version"])
    response = catalog.client.post(path, json=body, headers=headers)
    assert response.status_code == 201, response.text
    turn = response.json()["turn"]
    assert turn["request_text"] == body["message"].strip()
    assert turn["workflow_run_id"] is turn["model_run_id"] is turn["undone_at"] is None
    assert not turn["historical"]
    reply = turn["reply"]
    assert not reply["declined"] and all(
        reply[name] == [] for name in ("applied", "skipped", "tones", "revisions")
    )
    for expected in (
        "Résumé: saved revision",
        "Cover letter: saved revision",
        "no blocking unsupported statements",
        "No model was called",
        "editor",
        "section regeneration",
        "Compare",
        "latest",
        "not interpreted",
    ):
        assert expected in reply["text"]
    assert len(reply["text"]) <= 2000
    assert catalog.client.post(path, json=body, headers=headers).json() == response.json()
    reloaded = _view(catalog, application)
    assert reloaded["turns"] == [turn]
    assert reloaded["application_version"] == current["application_version"] + 1
    assert response.json()["application_version"] == reloaded["application_version"]
    assert response.json()["context_sha256"] == reloaded["context_sha256"]
    second = catalog.client.post(
        path,
        json={"message": "x" * 2000, "context_sha256": response.json()["context_sha256"]},
        headers=catalog.headers(response.json()["application_version"]),
    )
    assert second.status_code == 201, second.text
    assert second.json()["turn"]["reply"] == reply
    assert [item["id"] for item in _view(catalog, application)["turns"]] == [
        turn["id"],
        second.json()["turn"]["id"],
    ]
    assert _counts(catalog)[:3] == before[:3]
    for document in documents:
        assert (
            catalog.client.get(f"/api/v1/documents/{document['id']}").json()["current_revision"][
                "id"
            ]
            == document["current_revision"]["id"]
        )
    with catalog.runtime.transaction() as conn:
        frozen = conn.execute(
            text("SELECT context_snapshot FROM bonafides.document_conversation_turns WHERE id=:id"),
            {"id": UUID(turn["id"])},
        ).scalar_one()
    assert set(frozen["documents"][0]) == {
        "document_id",
        "kind",
        "version",
        "revision_id",
        "revision_no",
        "blocking_unsupported",
        "reviewed",
        "stale",
    }
    assert "SYNTHETIC PRIVATE" not in str(current) + reply["text"] + str(frozen)
    previous = _view(catalog, application)
    changed = copy.deepcopy(documents[0]["current_revision"]["body"])
    changed["sections"][0]["blocks"][0]["text"] = "Unsupported synthetic invention."
    assert _save(catalog, documents[0], changed).status_code == 200
    before = _counts(catalog)
    stale = catalog.client.post(
        path,
        json={**body, "context_sha256": previous["context_sha256"]},
        headers=catalog.headers(previous["application_version"]),
    )
    assert stale.status_code == 409 and stale.json()["code"] == "conversation_context_changed"
    assert _counts(catalog) == before
    fresh = _view(catalog, application)
    assert fresh["documents"][1]["blocking_unsupported"]
    with catalog.runtime.transaction() as conn:
        counts = deletion_counts(conn, catalog.owner_id, UUID(application["id"]))
        assert counts["conversation_turns"] == 2
        assert deletion_counts(conn, catalog.owner_id, None)["document_conversation_turns"] == 2
    _write(catalog, _get(catalog, application), "archive", {"archived": True})
    before = _counts(catalog)
    assert _view(catalog, application)["turns"] == fresh["turns"]
    assert _counts(catalog) == before


@pytest.mark.postgres
def test_refusals_auth_and_owner_scope_write_nothing(catalog: SourceContext) -> None:
    application = _application(catalog)
    current = _view(catalog, application)
    path = f"/api/v1/applications/{application['id']}/conversation"
    body = {
        "message": "Synthetic local guidance request",
        "context_sha256": current["context_sha256"],
    }
    before = _counts(catalog)
    for changes, headers, status in (
        ({"message": " \n\t"}, catalog.headers(application["version"]), 422),
        ({"message": "x" * 2001}, catalog.headers(application["version"]), 422),
        ({"context_sha256": "0" * 64}, catalog.headers(application["version"]), 409),
        ({}, catalog.headers(application["version"] + 1), 412),
        ({}, catalog.headers(), 428),
        ({}, {"Origin": catalog.identity.settings.origin}, 403),
    ):
        refused = catalog.client.post(path, json={**body, **changes}, headers=headers)
        assert refused.status_code == status, refused.text
        assert _counts(catalog) == before
    with catalog.identity.client() as anonymous:
        assert anonymous.get(path).status_code == 401
    unknown = f"/api/v1/applications/{uuid4()}/conversation"
    assert catalog.client.get(unknown).status_code == 404
    assert catalog.client.post(unknown, json=body, headers=catalog.headers(1)).status_code == 404
    with pytest.raises(Problem) as foreign, catalog.runtime.transaction() as conn:
        context_view(conn, catalog.owner_id, uuid4(), catalog.identity.settings)
    assert foreign.value.status == 404
    with pytest.raises(Problem) as foreign_owner, catalog.runtime.transaction() as conn:
        generation._application(conn, uuid4(), UUID(application["id"]), active=True)
    assert foreign_owner.value.status == 404
    assert _counts(catalog) == before
    _write(catalog, application, "archive", {"archived": True})
    before = _counts(catalog)
    assert catalog.client.get(path).status_code == 200
    archived = _get(catalog, application)
    assert (
        catalog.client.post(
            path, json=body, headers=catalog.headers(archived["version"])
        ).status_code
        == 409
    )
    assert _counts(catalog) == before


@pytest.mark.parametrize("qualified", [False, True])
def test_local_guidance_is_truthful_and_bounded(qualified: bool) -> None:
    reply = _local_reply(
        {"documents": [], "route_status": {"status": "qualified" if qualified else "disabled"}}
    )
    assert "No saved résumé or cover letter" in str(reply["text"])
    assert f"route is {'qualified' if qualified else 'not qualified'}" in str(reply["text"])
    assert "No model was called" in str(reply["text"])
