# SPDX-License-Identifier: MIT OR Apache-2.0
"""Real PostgreSQL/API local conversation boundaries with synthetic owner data."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from threading import Event
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_document_generation import _claim, _document, _save
from test_identity import identity as identity
from test_import_parser import parser as parser
from test_installation_control import renderer_spool as renderer_spool
from test_local_model import route_data
from test_section_regeneration import _setup
from test_sources import SourceContext
from test_sources import catalog as catalog
from test_suitability import SOURCE, FixtureModel
from test_tracking import _application, _get, _write

from bonafides import document_generation as generation
from bonafides import local_model, suitability
from bonafides.document_conversations import _local_reply, context_view
from bonafides.document_frame import PrintableFrame, template_digest
from bonafides.documents import parse_body
from bonafides.import_parser import ImportParserClient
from bonafides.letter_frame import closing_for, parts
from bonafides.portable_previews import _counts as deletion_counts
from bonafides.problems import Problem
from bonafides.render_service import render_html
from bonafides.workflows import Claim, cancel_workflow, fail
from tools.stub_model import respond


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
            (SELECT array_agg(workspace_version ORDER BY owner_id) FROM bonafides.owner_settings),
            (SELECT count(*) FROM bonafides.outbox_events)
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


BEFORE = "I would welcome a discussion of the role."
AFTER = "I would welcome the opportunity to discuss this role."


def _post(
    context: SourceContext, application: dict[str, Any], documents: list[str], **extra: object
) -> Any:
    view = _view(context, application)
    return context.client.post(
        f"/api/v1/applications/{application['id']}/conversation",
        json={
            "message": "Please make these passages more professional.",
            "document_ids": documents,
            "units": [],
            "context_sha256": view["context_sha256"],
            **extra,
        },
        headers=context.headers(view["application_version"]),
    )


def _prepared(
    context: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, before: str = BEFORE
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    data = route_data("http://127.0.0.1:1")
    data.update(max_input_tokens=12000, deadline_seconds=30)
    monkeypatch.setattr("test_suitability.route_data", lambda _endpoint: data)
    _, application, documents = _setup(context, monkeypatch, tmp_path)
    saved = []
    for document in documents:
        body = copy.deepcopy(document["current_revision"]["body"])
        body["sections"][0]["blocks"].append(
            {
                "block_key": "conversation-prose",
                "text": before,
                "claim_kind": "intent",
                "claim_revision_ids": [],
                "evidence_revision_ids": [],
            }
        )
        response = _save(context, document, body)
        assert response.status_code == 200, response.text
        assert not response.json()["stale"]
        saved.append(response.json())
    return application, saved


def _queue(
    context: SourceContext, application: dict[str, Any], documents: list[dict[str, Any]]
) -> Claim:
    response = _post(context, application, [item["id"] for item in documents])
    assert response.status_code == 202, response.text
    assert response.headers["location"] == f"/api/v1/workflows/{response.json()['workflow']['id']}"
    return _claim(context, response.json()["workflow"]["id"])


def _output(task: Claim, after: str = AFTER) -> dict[str, Any]:
    return {
        "schema_version": "1",
        "stage": "refine_documents",
        "reply_text": "Updated the selected wording.",
        "declined": False,
        "tone": "concise",
        "edits": [
            {
                key: target[key]
                for key in ("document_id", "section_key", "block_key", "previous_sha256")
            }
            | {"replacement": {**target["before"], "text": after}}
            for target in cast(list[dict[str, Any]], task.input["targets"])
            if target["block_key"] == "conversation-prose"
        ],
    }


def _payload(
    context: SourceContext, projection: dict[str, object], corrective: bool = False
) -> bytes:
    route = local_model.load_route(context.identity.settings.local_model_route_file)
    assert route is not None
    return local_model.OllamaClient(route).prepare(
        "refine_documents",
        projection,
        local_model.PROMPT_VERSIONS["refine_documents"],
        correction=("x" * 120,) * 12 if corrective else None,
    )[0]


def _execute(
    context: SourceContext, task: Claim, output: dict[str, Any], during: Any = None
) -> None:
    model = FixtureModel(output, during=during)
    suitability.execute_model(context.runtime, context.identity.settings, task, Event(), model)


def _undo(context: SourceContext, application: dict[str, Any], turn: dict[str, Any]) -> Any:
    view = _view(context, application)
    return context.client.post(
        f"/api/v1/applications/{application['id']}/conversation/turns/{turn['id']}/undo",
        json={},
        headers=context.headers(view["application_version"]),
    )


@pytest.mark.postgres
@pytest.mark.parametrize("change", ["target", "unrelated", "removed", "moved", "annotations"])
def test_partial_publication_preserves_intervening_owner_work(
    catalog: SourceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    change: str,
    parser: ImportParserClient,
    renderer_spool: Path,
) -> None:
    application, documents = _prepared(catalog, monkeypatch, tmp_path)
    task = _queue(catalog, application, documents)
    output = _output(task)
    # Duplicate stable identities collapse, and foreign identities cannot redirect an edit.
    output["edits"].append(copy.deepcopy(output["edits"][0]))
    output["edits"].append(
        {
            **copy.deepcopy(output["edits"][0]),
            "block_key": "unknown",
            "replacement": {**output["edits"][0]["replacement"], "block_key": "unknown"},
        }
    )
    foreign = str(uuid4())
    output["edits"].append({**copy.deepcopy(output["edits"][0]), "document_id": foreign})

    def intervene() -> None:
        body = copy.deepcopy(documents[0]["current_revision"]["body"])
        if change in {"removed", "moved"}:
            block = body["sections"][0]["blocks"].pop()
            if change == "moved":
                body["sections"][1]["blocks"].append(block)
        elif change == "annotations":
            body["sections"][0]["blocks"][-1]["claim_revision_ids"] = [str(uuid4())]
        else:
            block = body["sections"][0]["blocks"][-1 if change == "target" else 0]
            block["text"] = "Owner's intervening text is not model authority."
        saved = _save(catalog, documents[0], body)
        assert saved.status_code == 200, saved.text

    _execute(catalog, task, output, intervene)
    turn = _view(catalog, application)["turns"][-1]
    reply = turn["reply"]
    assert len(reply["applied"]) == (2 if change == "unrelated" else 1)
    assert {item["reason"] for item in reply["skipped"]} == (
        {"unknown_target"} if change == "unrelated" else {"unknown_target", "target_changed"}
    )
    assert foreign not in str(reply)
    current = _document(catalog, documents[0]["id"])["current_revision"]["body"]
    if change in {"target", "unrelated"}:
        assert (
            current["sections"][0]["blocks"][-1 if change == "target" else 0]["text"]
            == "Owner's intervening text is not model authority."
        )
    elif change == "annotations":
        assert current["sections"][0]["blocks"][-1]["claim_revision_ids"]
        assert current["sections"][0]["blocks"][-1]["text"] == BEFORE
        assert _document(catalog, documents[0]["id"])["unsupported_claims"]
    else:
        assert all(
            block["block_key"] != "conversation-prose" for block in current["sections"][0]["blocks"]
        )
        if change == "moved":
            assert current["sections"][1]["blocks"][-1]["text"] == BEFORE
    assert (
        _document(catalog, documents[1]["id"])["current_revision"]["body"]["sections"][0]["blocks"][
            -1
        ]["text"]
        == AFTER
    )
    if change == "target":
        from test_portable_archive import _encoded
        from test_portable_data import _export
        from test_portable_workspace import _import

        archive = _export(catalog)
        _import(catalog, parser, renderer_spool, _encoded(archive.manifest, archive.blobs))
        archive.manifest.records["source_configs"] = [
            {**row, "enabled": False, "current_policy_review_id": None}
            for row in archive.manifest.records["source_configs"]
        ]
        assert _export(catalog).manifest.records == archive.manifest.records


@pytest.mark.postgres
@pytest.mark.parametrize("conflict", ["text", "removed", "tone", "size", None])
def test_reply_bound_cross_document_undo_is_atomic_and_not_repeatable(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, conflict: str | None
) -> None:
    application, documents = _prepared(catalog, monkeypatch, tmp_path, AFTER)
    task = _queue(catalog, application, documents)
    _execute(catalog, task, _output(task, BEFORE))
    turn = _view(catalog, application)["turns"][-1]
    if conflict == "tone":
        from bonafides.documents import parse_body
        from bonafides.section_regeneration import _target

        with catalog.runtime.transaction() as conn:
            owner, identifier = catalog.owner_id, UUID(documents[0]["id"])
            row = _target(conn, owner, identifier)
            source = dict(row)
            source["provenance_snapshot"] = {**row["provenance_snapshot"], "tone": "professional"}
            body = parse_body(row["body"])
            generation.append_revision(conn, owner, identifier, body, source, "manual", "Tone edit")
    elif conflict:
        current = _document(catalog, documents[0]["id"])
        body = copy.deepcopy(current["current_revision"]["body"])
        blocks = body["sections"][0]["blocks"]
        if conflict == "size":
            remaining = 100_000 - sum(
                len(block["text"]) for section in body["sections"] for block in section["blocks"]
            )
            prose = blocks[-1]
            blocks.extend(
                {**prose, "block_key": f"pad-{offset}", "text": "x" * min(4000, remaining - offset)}
                for offset in range(0, remaining, 4000)
            )
        elif conflict == "removed":
            blocks.pop()
        else:
            blocks[-1]["text"] = "Later owner change."
        assert _save(catalog, current, body).status_code == 200
    before = [_document(catalog, item["id"]) for item in documents]
    counts = _counts(catalog)
    response = _undo(catalog, application, turn)
    if conflict:
        assert response.status_code == 409
        assert response.json()["code"] == "conversation_undo_conflict"
        assert [_document(catalog, item["id"]) for item in documents] == before
        assert _view(catalog, application)["turns"][-1]["undone_at"] is None
        assert _counts(catalog) == counts
    else:
        assert response.status_code == 200, response.text
        assert response.json()["turn"]["undone_at"] is not None
        for original in documents:
            restored = _document(catalog, original["id"])
            assert restored["current_revision"]["body"] == original["current_revision"]["body"]
            assert (
                restored["current_revision"]["provenance_snapshot"]["tone"]
                == original["current_revision"]["provenance_snapshot"]["tone"]
            )
        repeated = _undo(catalog, application, turn)
        assert repeated.status_code == 409
        assert repeated.json()["code"] == "conversation_already_undone"


@pytest.mark.postgres
@pytest.mark.parametrize("boundary", ["unsupported", "cancel", "lease", "settings"])
def test_failed_candidate_or_lost_authority_publishes_neither_reply_nor_revision(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, boundary: str
) -> None:
    application, documents = _prepared(catalog, monkeypatch, tmp_path)
    task = _queue(catalog, application, documents)
    output = _output(task)
    if boundary == "unsupported":
        output["edits"][0]["replacement"]["text"] = "Invented an unsupported qualification."

    def intervene() -> None:
        if boundary == "cancel":
            with catalog.runtime.transaction() as conn:
                cancel_workflow(conn, catalog.owner_id, task.id)
        elif boundary in {"lease", "settings"}:
            with catalog.identity.admin.transaction() as conn:
                sql = (
                    "UPDATE bonafides.workflow_runs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE owner_id=:owner AND id=:id"
                    if boundary == "lease"
                    else "UPDATE bonafides.owner_settings SET version=version+1 WHERE owner_id=:owner"
                )
                conn.execute(text(sql), {"owner": catalog.owner_id, "id": task.id})

    with pytest.raises((Problem, DBAPIError)):
        _execute(catalog, task, output, intervene)
    expected = [
        {**item, "stale": True, "stale_reason": "generation_privacy_settings_changed"}
        if boundary == "settings"
        else item
        for item in documents
    ]
    assert [_document(catalog, item["id"]) for item in documents] == expected
    assert _view(catalog, application)["turns"][-1]["reply"] is None
    if boundary in {"cancel", "lease"}:
        from bonafides.workflows import reap

        with catalog.runtime.transaction() as conn:
            reap(conn)
        if boundary == "lease":
            assert (
                _post(catalog, application, [item["id"] for item in documents]).status_code == 409
            )
            with catalog.runtime.transaction() as conn:
                cancel_workflow(conn, catalog.owner_id, task.id)
        assert _post(catalog, application, [item["id"] for item in documents]).status_code == 202


@pytest.mark.postgres
def test_local_guidance_scope_admission_and_recent_context(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    application, documents = _prepared(catalog, monkeypatch, tmp_path)
    before = _counts(catalog)[:3]
    for index in range(11):
        response = _post(
            catalog, application, [], message=f"  Local guidance {index} " + "x" * 1800 + "  "
        )
        assert response.status_code == 201, response.text
        assert response.json()["workflow"] is None
        assert response.json()["turn"]["request_text"] == f"Local guidance {index} " + "x" * 1800
        assert response.json()["turn"]["reply"]["applied"] == []
    assert _counts(catalog)[:3] == before

    with monkeypatch.context() as disabled_route:
        disabled_route.setattr(local_model, "load_route", lambda _path: None)
        counts = _counts(catalog)
        refused = _post(catalog, application, [documents[0]["id"]])
        assert refused.status_code == 503
        assert refused.json()["code"] == "conversation_route_unavailable"
        assert _counts(catalog) == counts
    assert _post(catalog, application, [], message="   ").status_code == 422
    assert _post(catalog, application, [], context_sha256="0" * 64).status_code == 409
    foreign = _post(catalog, application, [str(UUID(int=1))])
    assert foreign.status_code == 422
    assert foreign.json()["code"] == "conversation_document_not_owned"
    missing = {"document_id": documents[0]["id"], "section_key": "missing", "block_key": "missing"}
    selected = _post(catalog, application, [documents[0]["id"]], units=[missing])
    assert selected.status_code == 409 and selected.json()["code"] == "conversation_target_missing"
    task = _queue(catalog, application, documents)
    projection = cast(dict[str, Any], task.input["context"])
    assert projection["turns"] == []
    assert projection["request"] == "Please make these passages more professional."
    blocked = _post(catalog, application, [])
    assert blocked.status_code == 409 and blocked.json()["code"] == "conversation_in_progress"
    for index in range(11):
        output = _output(task)
        output.update(reply_text=f"Model reply {index} " + "x" * 1000, edits=[], tone=None)
        _execute(catalog, task, output)
        task = _queue(catalog, application, documents)
    projection = cast(dict[str, Any], task.input["context"])
    history = projection["turns"]
    expected = [f"Model reply {index} " + "x" * 1000 for index in range(11 - len(history), 11)]
    assert 0 < len(history) < 9 and [turn["reply_text"] for turn in history] == expected
    payload = _payload(catalog, projection)
    assert json.loads(json.loads(payload)["messages"][1]["content"]) == projection
    assert len(_payload(catalog, projection, corrective=True)) + 512 <= 12000
    older = {**history[0], "reply_text": f"Model reply {10 - len(history)} " + "x" * 1000}
    extended = {**projection, "turns": [older, *history]}
    assert len(_payload(catalog, extended)) + 512 <= 12000
    with pytest.raises(local_model.ModelFailure, match="model_context_limit"):
        _payload(catalog, extended, corrective=True)


@pytest.mark.postgres
@pytest.mark.parametrize("kind", ["edit", "declined", "no_edit", "tone"])
def test_explicit_scope_and_truthful_replies(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str
) -> None:
    application, documents = _prepared(catalog, monkeypatch, tmp_path)
    view = _view(catalog, application)
    selected_document = next(
        item for item in view["documents"] if item["document_id"] == documents[0]["id"]
    )
    unit = next(
        item for item in selected_document["units"] if item["block_key"] == "conversation-prose"
    )
    response = _post(
        catalog,
        application,
        [item["id"] for item in documents],
        units=[{name: unit[name] for name in ("document_id", "section_key", "block_key")}],
    )
    assert response.status_code == 202, response.text
    task = _claim(catalog, response.json()["workflow"]["id"])
    assert len(cast(list[object], task.input["targets"])) == 1
    output = _output(task)
    output.update({"declined": kind == "declined", "tone": "concise" if kind == "tone" else None})
    if kind != "edit":
        output["edits"] = []
    _execute(catalog, task, output)
    turn = _view(catalog, application)["turns"][-1]
    assert turn["reply"]["declined"] == (kind == "declined")
    assert len(turn["reply"]["applied"]) == (1 if kind == "edit" else 0)
    assert bool(turn["can_undo"]) == (kind in {"edit", "tone"})
    assert _document(catalog, documents[1]["id"]) == documents[1]
    if kind in {"edit", "tone"}:
        assert _undo(catalog, application, turn).status_code == 200
        restored = _document(catalog, documents[0]["id"])["current_revision"]
        assert restored["body"] == documents[0]["current_revision"]["body"]
        assert (
            restored["provenance_snapshot"]["tone"]
            == documents[0]["current_revision"]["provenance_snapshot"]["tone"]
        )
    else:
        assert [_document(catalog, item["id"]) for item in documents] == documents
        refused = _undo(catalog, application, turn)
        assert refused.status_code == 409 and refused.json()["code"] == "conversation_not_undoable"


@pytest.mark.postgres
def test_unknown_owned_resource_identifiers_are_denied_without_writes(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    application, _ = _prepared(catalog, monkeypatch, tmp_path)

    before = _counts(catalog)
    missing = str(UUID(int=1))
    root = "/api/v1/applications"
    assert catalog.client.get(f"{root}/{missing}/conversation").status_code == 404
    denied = catalog.client.post(
        f"{root}/{missing}/conversation",
        json={
            "message": "Request without ownership",
            "document_ids": [],
            "units": [],
            "context_sha256": "0" * 64,
        },
        headers=catalog.headers(1),
    )
    assert denied.status_code == 404
    assert _post(catalog, application, [missing]).status_code == 422
    denied = _undo(catalog, application, {"id": missing})
    assert denied.status_code == 404
    assert _counts(catalog) == before


@pytest.mark.postgres
@pytest.mark.parametrize("unfinished", ["retryable_failure", "waiting_for_approval"])
def test_unfinished_turn_blocks_admission_and_stale_automatic_retry(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, unfinished: str
) -> None:
    application, documents = _prepared(catalog, monkeypatch, tmp_path)
    old = _queue(catalog, application, documents)
    with catalog.runtime.transaction() as conn:
        fail(conn, old, "model_unavailable", transient=True)

    def set_state(state: str) -> None:
        with catalog.identity.admin.transaction() as conn:
            conn.execute(
                text("""UPDATE bonafides.workflow_runs SET state=CAST(:state AS bonafides.workflow_state),
                finished_at=CASE WHEN :state='cancelled' THEN clock_timestamp() ELSE NULL END,
                next_attempt_at=clock_timestamp()-interval '1 minute' WHERE owner_id=:owner AND id=:id"""),
                {"state": state, "owner": catalog.owner_id, "id": old.id},
            )

    set_state(unfinished)
    before = _counts(catalog)
    refused = _post(catalog, application, [item["id"] for item in documents])
    assert refused.status_code == 409 and refused.json()["code"] == "conversation_in_progress"
    assert _counts(catalog) == before
    # Recreate overlapping legacy turns without bypassing ordinary admission today.
    set_state("cancelled")
    newer = _queue(catalog, application, documents)
    set_state("retryable_failure")
    before_workflow = catalog.workflow(old.id)
    response = catalog.client.post(
        f"/api/v1/workflows/{old.id}/retry",
        json={},
        headers=catalog.headers(before_workflow["version"]),
    )
    assert response.status_code == 409 and response.json()["code"] == "conversation_in_progress"
    assert catalog.workflow(old.id)["version"] == before_workflow["version"]
    assert catalog.workflow(old.id)["state"] == "retryable_failure"
    retried = _claim(catalog, str(old.id))
    assert retried.input == old.input
    model = FixtureModel(_output(retried))
    with pytest.raises(Problem, match="conversation_in_progress"):
        suitability.execute_model(
            catalog.runtime, catalog.identity.settings, retried, Event(), model
        )
    assert model.calls == 0
    _execute(catalog, newer, _output(newer))
    turns = _view(catalog, application)["turns"]
    assert turns[0]["reply"] is None and len(turns[-1]["reply"]["applied"]) == 2


@pytest.mark.postgres
@pytest.mark.parametrize("coordinated", [False, True])
def test_large_saved_scope_refuses_oversized_wire_but_allows_selected_units(
    catalog: SourceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    coordinated: bool,
) -> None:
    data = route_data("http://127.0.0.1:1")
    data.update(context_window=32768, max_output_tokens=6000, framing_token_overhead=2048)
    monkeypatch.setattr("test_document_conversations.route_data", lambda _endpoint: data)
    monkeypatch.setattr(
        "test_document_generation.SOURCE", SOURCE + " Professional role context." * 55
    )
    application, documents = _prepared(catalog, monkeypatch, tmp_path)
    for index, additional in enumerate((6, 8) if coordinated else (30,)):
        body = copy.deepcopy(documents[index]["current_revision"]["body"])
        if coordinated:
            additional -= sum(len(section["blocks"]) for section in body["sections"])
        prose = body["sections"][0]["blocks"][-1]
        body["sections"][0]["blocks"].extend(
            {**prose, "block_key": f"additional-prose-{number}"} for number in range(additional)
        )
        saved = _save(catalog, documents[index], body)
        assert saved.status_code == 200, saved.text
        documents[index] = saved.json()
    ids = [document["id"] for document in documents]
    before = _counts(catalog)
    refused = _post(catalog, application, ids)
    assert refused.status_code == 409
    assert refused.json()["code"] == "document_context_outside_model_limits"
    assert _counts(catalog) == before
    for index in range(11):
        local = _post(catalog, application, [], message=f"Local guidance {index}")
        assert local.status_code == 201 and local.json()["workflow"] is None
    view = _view(catalog, application)
    if coordinated:
        assert sum(len(document["units"]) for document in view["documents"]) == 14
    selected_ids = ids if coordinated else ids[:1]
    selected = [
        {key: unit[key] for key in ("document_id", "section_key", "block_key")}
        for document in view["documents"]
        if document["document_id"] in selected_ids
        for unit in [item for item in document["units"] if "prose" in item["block_key"]][
            : 3 if coordinated and document["kind"] == "resume" else 1
        ]
    ]
    response = _post(catalog, application, selected_ids, units=selected)
    assert response.status_code == 202, response.text
    task = _claim(catalog, response.json()["workflow"]["id"])
    context = cast(dict[str, Any], task.input["context"])
    assert len(context["targets"]) == (4 if coordinated else 1) and context["turns"] == []
    assert 2500 <= len(json.dumps(context["shared"], separators=(",", ":"))) <= 2700
    assert context["documents"] == [{"document_id": document} for document in selected_ids]
    assert len(_payload(catalog, context, corrective=True)) + 2048 <= 12000
    output = _output(task)
    if coordinated:
        result = respond("valid", {"messages": [{"content": ""}, {"content": json.dumps(context)}]})
        output = json.loads(cast(dict[str, Any], result["message"])["content"])
    _execute(catalog, task, output)
    reply = _view(catalog, application)["turns"][-1]["reply"]
    assert len(reply["applied"]) == (4 if coordinated else 1) and not reply["skipped"]
    if not coordinated:
        published = _document(catalog, documents[0]["id"])["current_revision"]["body"]
        blocks = {
            block["block_key"]: block
            for section in published["sections"]
            for block in section["blocks"]
        }
        assert blocks["conversation-prose"]["text"] == AFTER
        assert all(blocks[f"additional-prose-{index}"]["text"] == BEFORE for index in range(30))


@pytest.mark.postgres
def test_actual_refinement_projection_excludes_private_and_withheld_content(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from test_document_generation import _setup_pair
    from test_model_context import WITHHELD_MARKER, _withheld_source

    def prepare(context: SourceContext) -> Any:
        _, application = _setup_pair(context)
        profile, _, _ = _withheld_source(context)
        saved = context.client.put(
            "/api/v1/profile",
            json={
                **profile["current_revision"]["snapshot"],
                "display_name": "SYNTHETIC PRIVATE NAME",
                "contact": {"email": "synthetic-private@example.test"},
            },
            headers=context.headers(profile["version"]),
        )
        assert saved.status_code == 200, saved.text
        return saved.json(), application

    monkeypatch.setattr("test_section_regeneration._setup_pair", prepare)
    application, documents = _prepared(catalog, monkeypatch, tmp_path)
    note: dict[str, object] = {"body": "SYNTHETIC PRIVATE NOTE"}
    contact: dict[str, object] = {"display_name": "SYNTHETIC PRIVATE CONTACT"}
    application = _write(catalog, _get(catalog, application), "notes", note)
    _write(catalog, application, "contacts", contact)
    task = _queue(catalog, application, documents)
    payload = _payload(catalog, cast(dict[str, object], task.input["context"]))
    captured: list[dict[str, object]] = []
    call = FixtureModel.call

    def capture(
        model: FixtureModel, stage: str, context: dict[str, object], *args: Any, **kwargs: Any
    ) -> Any:
        captured.append(context)
        assert _payload(catalog, context) == payload
        return call(model, stage, context, *args, **kwargs)

    monkeypatch.setattr(FixtureModel, "call", capture)
    _execute(catalog, task, _output(task))
    assert len(captured) == 1
    wire = payload.decode()
    for private in (
        "SYNTHETIC PRIVATE",
        "synthetic-private@example.test",
        WITHHELD_MARKER,
        '"frame"',
        '"contact"',
        '"portrait"',
    ):
        assert private not in wire
    shared = cast(dict[str, Any], captured[0]["shared"])
    sources = [shared | part for part in cast(list[dict[str, Any]], captured[0]["documents"])]
    assert any(
        item == {"id": item["id"], "withheld": True}
        for source in sources
        for item in source["evidence"]
    )


def _frame_save(
    context: SourceContext, document: dict[str, Any], status: int = 200, /, **changes: str
) -> dict[str, Any]:
    revision = document["current_revision"]
    frame = parts(
        revision["provenance_snapshot"]["frame"],
        revision["provenance_snapshot"]["tone"],
        parse_body(revision["body"]),
    ).model_dump(mode="json")
    response = _save(context, document, revision["body"], letter_frame={**frame, **changes})
    assert response.status_code == status, response.text
    return cast(dict[str, Any], response.json())


def _legacy_letter(
    context: SourceContext, letter: dict[str, Any], tone: str, *, framed: bool = True
) -> dict[str, Any]:
    from bonafides.section_regeneration import _target

    with context.runtime.transaction() as conn:
        row = _target(conn, context.owner_id, UUID(letter["id"]))
        provenance = copy.deepcopy(row["provenance_snapshot"])
        for key in ("greeting", "closing"):
            provenance["frame"].pop(key, None)
        provenance.pop("letter_closing_edited", None)
        provenance["frame"]["recipient"] = "Hiring team"
        template = str(row["template_version"]).replace("-v4", "-v3")
        provenance.update(
            tone=tone, template_version=template, template_sha256=template_digest(template)
        )
        if not framed:
            template = "bonafides-base-v1"
            provenance.update(schema_version=1, template_version=template)
            for key in ("frame", "template_sha256"):
                provenance.pop(key)
        body = copy.deepcopy(row["body"])
        blocks = body["sections"][0]["blocks"]
        blocks[:] = [block for block in blocks if block["block_key"] != "thanks"]
        for key, text in (
            ("salutation", "Dear Hiring team,"),
            ("signoff", "Sincerely,"),
            ("thanks", "Thank you for your consideration."),
        ):
            blocks.append(
                {
                    "block_key": key,
                    "text": text,
                    "claim_kind": "greeting",
                    "claim_revision_ids": [],
                    "evidence_revision_ids": [],
                }
            )
        generation.append_revision(
            conn,
            context.owner_id,
            UUID(letter["id"]),
            parse_body(body),
            {**row, "template_version": template, "provenance_snapshot": provenance},
            "manual",
            "Pre-frame letter fixture",
        )
    return _document(context, letter["id"])


@pytest.mark.postgres
@pytest.mark.parametrize("tone", ["friendly", "concise", "professional"])
@pytest.mark.parametrize(
    "owner_edit", [None, "before", "during", "recipient", "after", "date_after"]
)
def test_letter_closing_follows_tone_with_exact_undo_and_owner_protection(
    catalog: SourceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tone: str,
    owner_edit: str | None,
    parser: ImportParserClient,
    renderer_spool: Path,
) -> None:
    def html(revision: dict[str, Any]) -> str:
        return render_html(
            parse_body(revision["body"]),
            revision["template_version"],
            PrintableFrame.model_validate(revision["provenance_snapshot"]["frame"]),
        )

    application, documents = _prepared(catalog, monkeypatch, tmp_path)
    letter = _legacy_letter(
        catalog,
        next(item for item in documents if item["kind"] == "cover_letter"),
        "concise" if tone == "professional" else "professional",
    )
    if owner_edit == "before":
        letter = _frame_save(catalog, letter, closing="With appreciation,")
    original = letter["current_revision"]
    expected = original
    task = _queue(catalog, application, [letter])
    payload = _payload(catalog, cast(dict[str, object], task.input["context"]))
    for private in (
        '"correspondence_date"',
        '"display_name"',
        '"contact"',
        '"signature"',
        original["provenance_snapshot"]["frame"]["correspondence_date"],
    ):
        assert private.encode() not in payload

    def intervene() -> None:
        nonlocal expected
        change = (
            {"recipient": "Owner's recipient"}
            if owner_edit == "recipient"
            else {"closing": "With appreciation,"}
        )
        expected = _frame_save(catalog, letter, **change)["current_revision"]

    _execute(
        catalog,
        task,
        {**_output(task), "edits": [], "tone": tone},
        intervene if owner_edit in {"during", "recipient"} else None,
    )
    turn = _view(catalog, application)["turns"][-1]
    saved = _document(catalog, letter["id"])
    provenance = saved["current_revision"]["provenance_snapshot"]
    assert provenance["tone"] == tone
    assert provenance["frame"]["closing"] == (
        "With appreciation," if owner_edit in {"before", "during"} else closing_for(tone)
    )
    assert html(saved["current_revision"]).count("Dear Hiring team,") == 1
    assert "Sincerely," not in html(saved["current_revision"])
    assert turn["reply"]["tones"][0]["letter_before"]["body"] == expected["body"]
    if owner_edit == "recipient":
        assert provenance["frame"]["recipient"] == "Owner's recipient"
    if owner_edit == "before" and tone == "friendly":
        before = (_counts(catalog), saved, _view(catalog, application))
        for wording in ("Kind regards,", "Hi team,", "Dear Hiring team,"):
            body = copy.deepcopy(saved["current_revision"]["body"])
            body["sections"][0]["blocks"][-1]["text"] = wording
            refused = _save(catalog, saved, body)
            assert refused.status_code == 422
            assert refused.json()["code"] == "letter_body_use_greeting_closing_fields"
            assert _counts(catalog) == before[0]
            assert _document(catalog, letter["id"]) == before[1]
            assert _view(catalog, application) == before[2]
        for part in ("greeting", "closing"):
            refused = _frame_save(
                catalog, saved, 422, **{part: "Thank you for your consideration."}
            )
            assert refused["code"] == "letter_body_use_greeting_closing_fields"
        saved = _frame_save(catalog, saved)
        later = _queue(catalog, application, [saved])
        _execute(catalog, later, {**_output(later), "edits": [], "tone": "concise"})
        later_turn = _view(catalog, application)["turns"][-1]
        before = (_counts(catalog), _document(catalog, letter["id"]), _view(catalog, application))
        refused = _undo(catalog, application, turn)
        assert refused.status_code == 409 and refused.json()["code"] == "conversation_undo_conflict"
        assert _counts(catalog) == before[0]
        assert _document(catalog, letter["id"]) == before[1]
        assert _view(catalog, application) == before[2]
        assert _undo(catalog, application, later_turn).status_code == 200
        restored = _document(catalog, letter["id"])["current_revision"]
        assert restored["provenance_snapshot"]["tone"] == "friendly"
    if owner_edit in {"after", "date_after"}:
        change = (
            {"correspondence_date": "2024-02-29"}
            if owner_edit == "date_after"
            else {"closing": "With appreciation,"}
        )
        _frame_save(catalog, saved, **change)
    undone = _undo(catalog, application, turn)
    if owner_edit in {"after", "date_after"}:
        assert undone.status_code == 409 and undone.json()["code"] == "conversation_undo_conflict"
    else:
        assert undone.status_code == 200, undone.text
        restored = _document(catalog, letter["id"])["current_revision"]
        for field in ("body", "provenance_snapshot", "template_version", "content_sha256"):
            assert restored[field] == expected[field]
        assert html(restored) == html(expected)
        if owner_edit is None and tone == "friendly":
            from test_portable_archive import _encoded
            from test_portable_data import _export
            from test_portable_workspace import _import

            from bonafides.conversation_protocol import validate_reply
            from bonafides.model_calls import OutputMismatch

            frozen = cast(dict[str, Any], copy.deepcopy(task.input))
            frozen["documents"][0].update(
                body=saved["current_revision"]["body"],
                provenance_snapshot=saved["current_revision"]["provenance_snapshot"],
            )
            target = next(item for item in frozen["targets"] if item["block_key"] == "thanks")
            edit = {
                key: target[key]
                for key in ("document_id", "section_key", "block_key", "previous_sha256")
            }
            edit["replacement"] = {**target["before"], "text": "Dear Hiring team,"}
            with pytest.raises(OutputMismatch) as refused:
                validate_reply({**_output(task), "edits": [edit]}, frozen)
            assert refused.value.summary == ["edits: letter_correspondence_in_body"]
            archive = _export(catalog)
            _import(catalog, parser, renderer_spool, _encoded(archive.manifest, archive.blobs))
            archive.manifest.records["source_configs"] = [
                {**row, "enabled": False, "current_policy_review_id": None}
                for row in archive.manifest.records["source_configs"]
            ]
            assert _export(catalog).manifest.records == archive.manifest.records


@pytest.mark.postgres
@pytest.mark.parametrize("part", ["sender", "date", "signature"])
def test_protected_letter_refinement_refuses_before_work(
    catalog: SourceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    part: str,
) -> None:
    application, documents = _prepared(catalog, monkeypatch, tmp_path)
    before = _counts(catalog)
    response = _post(
        catalog,
        application,
        [item["id"] for item in documents],
        units=[{"document_id": documents[0]["id"], "section_key": part, "block_key": part}],
    )
    assert response.status_code == 422 and response.json()["code"] == "letter_frame_not_regenerable"
    assert _counts(catalog) == before


@pytest.mark.postgres
@pytest.mark.parametrize(
    "message",
    [
        "Shorten the Experience bullets but keep each end date",
        "Mention I can start at a later date",
        "Keep my résumé up-to-date with the Rust skills",
    ],
)
def test_free_text_date_mentions_do_not_refuse(
    catalog: SourceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    message: str,
) -> None:
    application, documents = _prepared(catalog, monkeypatch, tmp_path)
    letter = _legacy_letter(
        catalog, next(item for item in documents if item["kind"] == "cover_letter"), "professional"
    )
    response = _post(catalog, application, [letter["id"]], message=message)
    assert response.status_code == 202, response.text
