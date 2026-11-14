# SPDX-License-Identifier: MIT OR Apache-2.0
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from pydantic import JsonValue
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_claim_usage import _pair
from test_document_generation import _document, _pair_documents, _save
from test_identity import _login
from test_identity import identity as identity
from test_import_parser import parser as parser
from test_installation_control import renderer_spool as renderer_spool
from test_portable_archive import _encoded
from test_portable_data import _digest, _export, _historical_model, _snapshot
from test_portable_erasure import _deletion
from test_portable_workspace import _import
from test_sources import SourceContext
from test_sources import catalog as catalog
from test_suitability import _profile
from test_task_retention import _model

from bonafides.commands import canonical_json
from bonafides.import_parser import ImportParserClient
from bonafides.installation_control import InstallationMaintenance
from bonafides.portable_archive import ArchiveError, PortableArchive
from bonafides.portable_columns import COLUMNS
from bonafides.portable_erasure import delete_controlled_owner_data
from bonafides.profiles import content_hash
from bonafides.workflows import enqueue


def _reply(message: str, *, declined: bool = False) -> dict[str, JsonValue]:
    return {
        "text": message,
        "declined": declined,
        "applied": [],
        "skipped": [],
        "tones": [],
        "revisions": [],
    }


def _domain_rows(context: SourceContext) -> dict[str, object]:
    # Failed replacement deliberately leaves maintenance closed: inspect owner
    # rows directly instead of bypassing the ordinary export admission guard.
    tables = ["model_runs" if name == "model_run_provenance" else name for name in COLUMNS]
    with context.identity.admin.transaction() as conn:
        return {
            table: conn.execute(
                text(f"""SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb)
                FROM bonafides.{table} t WHERE owner_id=:owner"""),
                {"owner": context.owner_id},
            ).scalar_one()
            for table in [*tables, "portable_model_history"]
        }


def _history(
    context: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> PortableArchive:
    _, application = _pair(context, monkeypatch, tmp_path, "valid")
    document = next(
        item
        for item in _pair_documents(context, application)["items"]
        if item["kind"] == "cover_letter"
    )
    before_id = document["current_revision"]["id"]
    body = deepcopy(document["current_revision"]["body"])
    section, before = next(
        (section, block)
        for section in body["sections"]
        for block in section["blocks"]
        if block["claim_kind"] == "intent"
    )
    prior = deepcopy(before)
    before["text"] = "I would welcome discussing how I can contribute to this role."
    replacement = deepcopy(before)
    response = _save(context, document, body)
    assert response.status_code == 200, response.text
    changed = _document(context, document["id"])
    after_id = changed["current_revision"]["id"]
    response = _save(context, changed, document["current_revision"]["body"])
    assert response.status_code == 200, response.text
    path = f"/api/v1/applications/{application['id']}/conversation"
    current = context.client.get(path).json()
    response = context.client.post(
        path,
        headers=context.headers(current["application_version"]),
        json={
            "message": "Summarize the saved context.",
            "context_sha256": current["context_sha256"],
        },
    )
    assert response.status_code == 201, response.text
    archive = _export(context)
    records = archive.manifest.records
    local_turns = records["document_conversation_turns"]
    local_snapshot = cast(dict[str, Any], local_turns[0]["context_snapshot"])
    assert local_snapshot["documents"] and "body" not in local_snapshot["documents"][0]
    assert local_turns[0]["workflow_run_id"] is None and local_turns[0]["model_run_id"] is None
    records["document_conversation_turns"] = []
    revisions = {
        row["id"]: row for row in cast(list[dict[str, Any]], records["document_revisions"])
    }
    observation = _historical_model(archive)
    model = cast(dict[str, JsonValue], observation["record"])
    model["task_kind"] = "refine_documents"
    records["model_run_provenance"].append(observation)
    turn_id, request = str(uuid4()), "Make the closing more direct."
    target: dict[str, JsonValue] = {
        "document_id": document["id"],
        "section_key": section["key"],
        "block_key": prior["block_key"],
        "heading": section["heading"],
        "before": prior,
        "previous_sha256": content_hash(prior),
    }
    snapshot: dict[str, JsonValue] = {
        "mode": "document_conversation",
        "stage": "refine_documents",
        "application_id": application["id"],
        "turn_id": turn_id,
        "request_text": request,
        "documents": [
            {
                "document_id": document["id"],
                "revision_id": before_id,
                "profile_revision_id": revisions[before_id]["profile_revision_id"],
                "body": revisions[before_id]["body"],
                "provenance_snapshot": revisions[before_id]["provenance_snapshot"],
            }
        ],
        "targets": [target],
    }
    records["document_conversation_turns"].append(
        {
            "owner_id": str(context.owner_id),
            "id": turn_id,
            "application_id": application["id"],
            "created_at": revisions[after_id]["created_at"],
            "request_text": request,
            "context_snapshot": snapshot,
            "workflow_run_id": model["workflow_run_id"],
            "model_run_id": model["id"],
            "undone_at": revisions[after_id]["created_at"],
            "reply": {
                **_reply("The closing is more direct."),
                "applied": [
                    {key: value for key, value in target.items() if key != "previous_sha256"}
                    | {"after": replacement}
                ],
                "revisions": [
                    {
                        "document_id": document["id"],
                        "before_revision_id": before_id,
                        "after_revision_id": after_id,
                    }
                ],
            },
        }
    )
    for pending in (False, True):
        observation = _historical_model(archive)
        model = cast(dict[str, JsonValue], observation["record"])
        model["task_kind"] = "refine_documents"
        if not pending:
            records["model_run_provenance"].append(observation)
        identifier = str(uuid4())
        message = "Refine without adding unsupported facts."
        records["document_conversation_turns"].append(
            {
                "owner_id": str(context.owner_id),
                "id": identifier,
                "application_id": application["id"],
                "created_at": revisions[after_id]["created_at"],
                "request_text": message,
                "context_snapshot": {**snapshot, "turn_id": identifier, "request_text": message},
                "reply": None
                if pending
                else _reply("I cannot support that requested claim.", declined=True),
                "workflow_run_id": model["workflow_run_id"],
                "model_run_id": None if pending else model["id"],
                "undone_at": None,
            }
        )
    for index in range(12):
        identifier, message = str(uuid4()), f"Local guidance request {index + 1}"
        records["document_conversation_turns"].append(
            {
                "owner_id": str(context.owner_id),
                "id": identifier,
                "application_id": application["id"],
                "created_at": revisions[after_id]["created_at"],
                "request_text": message,
                "context_snapshot": {
                    "mode": "document_conversation",
                    "stage": "refine_documents",
                    "application_id": application["id"],
                    "turn_id": identifier,
                    "request_text": message,
                },
                "reply": _reply("Select saved documents to refine their wording."),
                "workflow_run_id": None,
                "model_run_id": None,
                "undone_at": None,
            }
        )
    records["document_conversation_turns"].extend(local_turns)
    return _digest(archive)


@pytest.mark.postgres
@pytest.mark.parametrize("workspace", [False, True])
def test_complete_conversation_history_imports_inert_and_reexports_exactly(
    catalog: SourceContext,
    parser: ImportParserClient,
    renderer_spool: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    workspace: bool,
) -> None:
    archive = _history(catalog, monkeypatch, tmp_path)
    source_owner = uuid4()
    for rows in archive.manifest.records.values():
        for row in rows:
            if "owner_id" in row:
                row["owner_id"] = str(source_owner)
            if "acknowledged_by_owner_id" in row:
                row["acknowledged_by_owner_id"] = str(source_owner)
    archive = _digest(
        PortableArchive(
            archive.manifest.model_copy(update={"source_owner_id": source_owner}), archive.blobs
        )
    )
    _import(
        catalog, parser, renderer_spool, _encoded(archive.manifest, archive.blobs), source_owner
    )
    restored = _export(catalog)
    for family, rows in archive.manifest.records.items():
        expected_rows = [
            {
                **row,
                **({"owner_id": str(catalog.owner_id)} if "owner_id" in row else {}),
                **(
                    {"acknowledged_by_owner_id": str(catalog.owner_id)}
                    if "acknowledged_by_owner_id" in row
                    else {}
                ),
            }
            for row in rows
        ]
        if family == "source_configs":
            # Imported terms history must not authorize a new source read.
            expected_rows = [
                {**row, "enabled": False, "current_policy_review_id": None} for row in expected_rows
            ]
        assert sorted(restored.manifest.records[family], key=canonical_json) == sorted(
            expected_rows, key=canonical_json
        ), family
    with catalog.runtime.transaction() as conn:
        stored = (
            conn.execute(
                text("""SELECT workflow_run_id,model_run_id,historical_workflow_run_id,
            historical_model_run_id,undone_at FROM bonafides.document_conversation_turns
            WHERE owner_id=:owner ORDER BY created_at,id"""),
                {"owner": catalog.owner_id},
            )
            .mappings()
            .all()
        )
        assert all(row["workflow_run_id"] is None and row["model_run_id"] is None for row in stored)
        assert any(row["historical_model_run_id"] and row["undone_at"] for row in stored)
    login = _login(catalog.identity, catalog.client)
    assert login.status_code == 201, login.text
    catalog.csrf = login.json()["csrf_token"]
    application_id = archive.manifest.records["applications"][0]["id"]
    path = f"/api/v1/applications/{application_id}"
    current = catalog.client.get(path + "/conversation").json()
    headers = catalog.headers(current["application_version"])
    response = catalog.client.post(
        path + "/conversation",
        headers=headers,
        json={
            "message": "Private local guidance to erase.",
            "context_sha256": current["context_sha256"],
        },
    )
    assert response.status_code == 201, response.text
    application = catalog.client.get(path).json()
    control = _deletion(catalog, None if workspace else application)
    maintenance = InstallationMaintenance(catalog.runtime, control, parser.spool, renderer_spool)
    with maintenance.quiescent():
        result = delete_controlled_owner_data(catalog.runtime, catalog.identity.settings, control)
        maintenance.finish(result)
    with catalog.runtime.transaction() as conn:
        assert not conn.execute(
            text("SELECT EXISTS(SELECT 1 FROM bonafides.document_conversation_turns)")
        ).scalar_one()
        model_ids = [
            row["model_run_id"]
            for row in archive.manifest.records["document_conversation_turns"]
            if row["model_run_id"] is not None
        ]
        assert not conn.execute(
            text("""SELECT EXISTS(SELECT 1 FROM bonafides.portable_model_history
                WHERE id=ANY(CAST(:ids AS uuid[])))"""),
            {"ids": model_ids},
        ).scalar_one()
        assert not conn.execute(
            text(
                "SELECT EXISTS(SELECT 1 FROM bonafides.idempotency_records WHERE owner_id=:owner AND key=:key)"
            ),
            {"owner": catalog.owner_id, "key": headers["Idempotency-Key"]},
        ).scalar_one()
        assert conn.execute(
            text("""SELECT EXISTS(SELECT 1 FROM bonafides.erased_request_keys
                WHERE owner_id=:owner AND key_sha256=encode(sha256(convert_to(:key,'UTF8')),'hex'))"""),
            {"owner": catalog.owner_id, "key": headers["Idempotency-Key"]},
        ).scalar_one()
    login = _login(catalog.identity, catalog.client)
    assert login.status_code == 201, login.text
    headers["X-CSRF-Token"] = login.json()["csrf_token"]
    replay = catalog.client.post(
        path + "/conversation",
        headers=headers,
        json={
            "message": "Private local guidance to erase.",
            "context_sha256": current["context_sha256"],
        },
    )
    assert replay.status_code == 410 and replay.json()["code"] == "data_erased"


@pytest.mark.postgres
@pytest.mark.parametrize(
    "damage",
    [
        "owner",
        "application",
        "revision",
        "profile",
        "claim",
        "model",
        "workflow",
        "request",
        "context",
        "reply",
        "declined",
        "undone",
        "duplicate_revision",
    ],
)
def test_invalid_conversation_references_leave_live_workspace_unchanged(
    catalog: SourceContext,
    parser: ImportParserClient,
    renderer_spool: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    damage: str,
) -> None:
    archive = _history(catalog, monkeypatch, tmp_path)
    turn = archive.manifest.records["document_conversation_turns"][0]
    snapshot = cast(dict[str, Any], turn["context_snapshot"])
    reply = cast(dict[str, Any], turn["reply"])
    identifier = str(uuid4())
    if damage == "owner":
        turn["owner_id"] = identifier
    elif damage == "application":
        turn["application_id"] = snapshot["application_id"] = identifier
    elif damage == "revision":
        reply["revisions"][0]["after_revision_id"] = identifier
    elif damage == "profile":
        snapshot["documents"][0]["profile_revision_id"] = identifier
    elif damage == "claim":
        reply["applied"][0]["before"]["claim_revision_ids"] = [identifier]
    elif damage == "model":
        turn["model_run_id"] = identifier
    elif damage == "workflow":
        turn["workflow_run_id"] = identifier
    elif damage == "request":
        turn["request_text"] = snapshot["request_text"] = " untrimmed "
    elif damage == "context":
        snapshot["turn_id"] = identifier
    elif damage == "reply":
        reply["skipped"] = {}
    elif damage == "declined":
        reply["declined"] = True
    elif damage == "undone":
        reply["applied"] = []
    else:
        reply["revisions"].append(deepcopy(reply["revisions"][0]))
    before = _snapshot(catalog)
    before_records = _domain_rows(catalog)
    with pytest.raises(ArchiveError):
        _import(catalog, parser, renderer_spool, _encoded(_digest(archive).manifest, archive.blobs))
    assert _snapshot(catalog) == before
    assert _domain_rows(catalog) == before_records


@pytest.mark.postgres
def test_conversation_database_rejects_cross_owner_application_and_history_rewrites(
    catalog: SourceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive = _history(catalog, monkeypatch, tmp_path)
    turn = archive.manifest.records["document_conversation_turns"][1]
    insert = text("""INSERT INTO bonafides.document_conversation_turns
        (owner_id,id,application_id,request_text,context_snapshot,workflow_run_id,historical_workflow_run_id)
        VALUES(:owner,:id,:application,:request,CAST(:snapshot AS jsonb),:workflow,:historical)""")
    parameters = {
        "owner": catalog.owner_id,
        "id": turn["id"],
        "application": turn["application_id"],
        "request": turn["request_text"],
        "snapshot": canonical_json(turn["context_snapshot"]),
        "workflow": None,
        "historical": None,
    }
    with pytest.raises(DBAPIError), catalog.runtime.transaction() as conn:
        conn.execute(insert, {**parameters, "owner": uuid4()})
    foreign_application = str(uuid4())
    foreign_snapshot = {
        **cast(dict[str, JsonValue], turn["context_snapshot"]),
        "application_id": foreign_application,
    }
    with pytest.raises(DBAPIError), catalog.runtime.transaction() as conn:
        conn.execute(
            insert,
            {
                **parameters,
                "application": foreign_application,
                "snapshot": canonical_json(foreign_snapshot),
            },
        )
    publish = text("""UPDATE bonafides.document_conversation_turns
        SET reply=CAST(:reply AS jsonb),model_run_id=:model WHERE owner_id=:owner AND id=:id""")
    parameters.update(reply=canonical_json(turn["reply"]), model=None)
    for historical in (None, str(uuid4())):
        with pytest.raises(DBAPIError) as denied, catalog.runtime.transaction() as conn:
            conn.execute(insert, {**parameters, "historical": historical})
            conn.execute(publish, parameters)
        assert getattr(denied.value.orig, "sqlstate", None) == "55000"
    for turn in archive.manifest.records["document_conversation_turns"][:2]:
        parameters.update(
            id=turn["id"],
            request=turn["request_text"],
            snapshot=canonical_json(turn["context_snapshot"]),
            reply=canonical_json(turn["reply"]),
        )
        with catalog.runtime.transaction() as conn:
            workflow = enqueue(
                conn,
                catalog.owner_id,
                "document_generate",
                cast(dict[str, object], turn["context_snapshot"]),
            )
        parameters.update(workflow=workflow, model=_model(catalog, workflow))
        with catalog.runtime.transaction() as conn:
            conn.execute(insert, parameters)
            conn.execute(publish, parameters)
        applied = cast(dict[str, JsonValue], turn["reply"])["applied"]
        if applied:
            with catalog.runtime.transaction() as conn:
                conn.execute(
                    text("""UPDATE bonafides.document_conversation_turns SET undone_at=clock_timestamp()
                    WHERE owner_id=:owner AND id=:id"""),
                    parameters,
                )
        for assignment in (
            "request_text='rewritten'",
            "reply=NULL",
            "model_run_id=NULL",
            "reply=jsonb_set(reply,'{text}','\"changed\"')",
            "context_snapshot='{}'::jsonb",
            "undone_at=NULL" if applied else "undone_at=clock_timestamp()",
        ):
            with pytest.raises(DBAPIError), catalog.runtime.transaction() as conn:
                conn.execute(
                    text(
                        f"UPDATE bonafides.document_conversation_turns SET {assignment} WHERE owner_id=:owner AND id=:id"
                    ),
                    parameters,
                )


@pytest.mark.postgres
@pytest.mark.parametrize("version", [1, 2, 3])
def test_older_workspaces_import_without_conversation_authority(
    catalog: SourceContext,
    parser: ImportParserClient,
    renderer_spool: Path,
    version: int,
) -> None:
    _profile(catalog)
    archive = _export(catalog)
    archive.manifest.records.pop("document_conversation_turns")
    archive = _digest(
        PortableArchive(
            archive.manifest.model_copy(
                update={"format_version": version, "source_schema_version": 1}
            ),
            archive.blobs,
        )
    )
    _import(catalog, parser, renderer_spool, _encoded(archive.manifest, archive.blobs))
    restored = _export(catalog)
    assert restored.manifest.format_version == 4
    assert restored.manifest.records["document_conversation_turns"] == []
    assert (
        restored.manifest.records["profile_revisions"]
        == archive.manifest.records["profile_revisions"]
    )


@pytest.mark.postgres
def test_conversation_insert_checks_reject_malformed_storage(
    catalog: SourceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive = _history(catalog, monkeypatch, tmp_path)
    original = archive.manifest.records["document_conversation_turns"][1]
    failures: list[dict[str, JsonValue]] = [
        {"request_text": ""},
        {"request_text": " padded "},
        {"request_text": "x" * 2001},
        {"context_snapshot": []},
        {"context_snapshot": {}},
        {"reply": {**_reply("Invalid"), "applied": {}}},
        {"reply": {**_reply("Invalid", declined=True), "tones": [{}]}},
        {"workflow_run_id": str(uuid4())},
        {"model_run_id": str(uuid4())},
        {"historical_model_run_id": str(uuid4())},
        {"undone_at": original["created_at"]},
    ]
    for changes in failures:
        row = {**original, "workflow_run_id": None, "model_run_id": None, **changes}
        with pytest.raises(DBAPIError), catalog.runtime.transaction() as conn:
            conn.execute(
                text("""INSERT INTO bonafides.document_conversation_turns
                SELECT * FROM jsonb_populate_record(
                NULL::bonafides.document_conversation_turns,CAST(:row AS jsonb))"""),
                {"row": canonical_json(row)},
            )
