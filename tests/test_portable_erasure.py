# SPDX-License-Identifier: MIT OR Apache-2.0
"""Real runtime-role erasure, filesystem failure/recovery and replay boundaries.

The checkpoint helper seeds offline-attested SQL metadata to isolate policy tests;
it is NOT a physical backup or restore drill. Actual native CLI qualification is
retained separately. Destructive producer dispatch is explicit fixture setup, not
claimed HTTP/UI acceptance of the owner-facing producer.
"""

from __future__ import annotations

import base64
from dataclasses import replace
from pathlib import Path
from threading import Event
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_action_approvals import Prepared, _approve, _owner
from test_document_generation import _base, _document, _queue_base
from test_identity import _login
from test_identity import identity as identity
from test_import_parser import parser as parser
from test_installation_control import _queue_control
from test_installation_control import renderer_spool as renderer_spool
from test_portable_data import _export
from test_profile_identity import png
from test_sources import SourceContext
from test_sources import catalog as catalog
from test_suitability import _posting, _profile, _queue, _route, _view
from test_tracking import _application

from bonafides import suitability
from bonafides.approvals import consume_action_approval, prepare_action_approval
from bonafides.commands import command
from bonafides.import_parser import ImportParserClient
from bonafides.installation_control import Control, InstallationMaintenance, begin_control
from bonafides.portable_erasure import delete_controlled_owner_data, purge_scope
from bonafides.profile_identity import PortraitPin, portrait_bytes
from bonafides.storage import BlobStore, StoredBlob
from bonafides.worker import Worker
from bonafides.workflows import assert_fence, claim, claim_control, enqueue


def _checkpoint(
    context: SourceContext, parser_spool: Path, renderer_spool: Path
) -> tuple[UUID, dict[str, str]]:
    """SQL policy fixture, NOT evidence that a ciphertext was created or restored."""
    with context.runtime.transaction() as conn:
        identifier = enqueue(conn, context.owner_id, "backup", {})
        task = claim_control(conn, uuid4(), workflow_id=identifier)
        assert task is not None
        control = begin_control(conn, task)
    maintenance = InstallationMaintenance(context.runtime, control, parser_spool, renderer_spool)
    with maintenance.quiescent():
        with context.identity.admin.transaction() as conn:
            conn.execute(
                text("""INSERT INTO bonafides.installation_backups(
                id,owner_id,namespace_id,maintenance_epoch,workspace_version,
                installation_auth_epoch,owner_auth_epoch,schema_versions,
                image_sha256,source_sha256,config_sha256,keyring_sha256,recipient_sha256,
                file_name,cipher_sha256,manifest_sha256,byte_length,captured_at,
                restore_verified_at,restore_receipt_sha256,restore_target_namespace)
                SELECT :id,:owner,:namespace,:epoch,s.workspace_version,i.auth_epoch,o.auth_epoch,
                (SELECT jsonb_object_agg(version::text,source_sha256) FROM bonafides.schema_migrations),
                repeat('0',64),repeat('0',64),repeat('0',64),repeat('0',64),repeat('0',64),
                :file,repeat('0',64),repeat('0',64),1,clock_timestamp(),
                clock_timestamp(),repeat('0',64),:target
                FROM bonafides.owner_settings s JOIN bonafides.owners o ON o.id=s.owner_id
                CROSS JOIN bonafides.installation_state i WHERE s.owner_id=:owner AND i.singleton"""),
                {
                    "id": identifier,
                    "owner": context.owner_id,
                    "namespace": control.namespace_id,
                    "epoch": control.maintenance_epoch,
                    "file": str(uuid4()) + ".cms",
                    "target": uuid4(),
                },
            )
        maintenance.finish({"operation": "checkpoint_policy_fixture_not_physical_recovery"})
    return identifier, {
        key: "0" * 64
        for key in (
            "image_sha256",
            "source_sha256",
            "config_sha256",
            "keyring_sha256",
            "recipient_sha256",
            "cipher_sha256",
        )
    }


def _deletion(context: SourceContext, application: dict[str, Any] | None = None) -> Control:
    with context.runtime.transaction() as conn:
        owner = _owner(conn, context.owner_id)
        identifier = _queue_control(conn, context.owner_id, "privacy_delete")
        namespace = conn.execute(
            text("SELECT namespace_id FROM bonafides.installation_state WHERE singleton")
        ).scalar_one()
        payload: dict[str, object] = {
            "preview_sha256": "2" * 64,
            "categories": ["all_workspace"] if application is None else ["applications"],
        }
        if application is not None:
            payload.update(
                application_id=application["id"], application_version=application["version"]
            )
        destination: dict[str, object] = {"namespace_id": str(namespace)}
        approval = prepare_action_approval(
            conn,
            context.owner_id,
            identifier,
            action_kind="privacy_delete",
            destination=destination,
            payload=payload,
            session_id=owner.session_id,
        )
    prepared = Prepared(identifier, approval, destination, payload, "privacy_delete")
    _approve(context.runtime.engine, owner, prepared)
    # Only producer dispatch is seeded; consumption, fencing, daemon drain and
    # actual SQL/filesystem execution below use production code and runtime role.
    with context.identity.admin.transaction() as conn:
        conn.execute(
            text("""UPDATE bonafides.workflow_runs SET state='pending',version=version+1,
            last_error_code=NULL WHERE owner_id=:owner AND id=:id"""),
            {"owner": context.owner_id, "id": identifier},
        )
    with context.runtime.transaction() as conn:
        task = claim_control(conn, uuid4(), workflow_id=identifier)
        assert task is not None
        consume_action_approval(
            conn,
            task,
            action_kind="privacy_delete",
            destination=destination,
            payload=payload,
            target_version=cast(int, approval["target_version"]),
        )
        return begin_control(conn, task)


def _attachment(context: SourceContext, application: dict[str, Any], data: bytes) -> StoredBlob:
    stored = BlobStore(context.identity.settings.blob_root).put_bytes(context.owner_id, data)
    with context.runtime.transaction() as conn:
        blob_id = conn.execute(
            text("""INSERT INTO bonafides.blobs(owner_id,sha256,byte_length,media_type,storage_key)
            VALUES(:owner,:sha,:size,'text/plain',:key)
            ON CONFLICT(owner_id,sha256) DO UPDATE SET sha256=EXCLUDED.sha256 RETURNING id"""),
            {
                "owner": context.owner_id,
                "sha": stored.sha256,
                "size": stored.byte_length,
                "key": stored.storage_key,
            },
        ).scalar_one()
        conn.execute(
            text("""INSERT INTO bonafides.application_attachments(owner_id,application_id,blob_id,display_name)
            VALUES(:owner,:application,:blob,'synthetic-private.txt')"""),
            {"owner": context.owner_id, "application": UUID(application["id"]), "blob": blob_id},
        )
    return stored


@pytest.mark.postgres
@pytest.mark.parametrize("collision", ["workflow", "cached_response"])
def test_application_erasure_preserves_neighbors_and_shared_bytes_and_blocks_old_intent(
    catalog: SourceContext, parser: ImportParserClient, renderer_spool: Path, collision: str
) -> None:
    base = _base(catalog, _profile(catalog))
    first = _application(catalog, title="Synthetic erasure target")
    second = _application(catalog, title="Synthetic retained neighbor")
    shared = _attachment(catalog, first, b"Shared retained synthetic bytes")
    _attachment(catalog, second, b"Shared retained synthetic bytes")
    private = _attachment(catalog, first, b"Private erased synthetic bytes")
    profile = catalog.client.get("/api/v1/profile").json()
    source = profile["evidence"][0]["revision"]
    response = catalog.client.post(
        "/api/v1/claims",
        json={
            "kind": "writing_fact",
            "payload": {"text": source["payload"]["text"][:1000], "application_id": first["id"]},
            "evidence_links": [
                {
                    "evidence_revision_id": source["id"],
                    "support_kind": "user_asserted",
                    "excerpt": source["payload"]["text"][:1000],
                }
            ],
        },
        headers=catalog.headers(profile["version"]),
    )
    assert response.status_code == 200, response.text
    historical = response.json()["current_revision"]
    orphan = BlobStore(catalog.identity.settings.blob_root).put_bytes(
        catalog.owner_id, b"Unpublished interrupted synthetic bytes"
    )
    with catalog.runtime.transaction() as conn:
        request_key = conn.execute(
            text("""SELECT key FROM bonafides.idempotency_records
            WHERE owner_id=:owner AND response_status=201 AND response_body->>'id'=:id"""),
            {"owner": catalog.owner_id, "id": first["id"]},
        ).scalar_one()
        posting_id = conn.execute(
            text("""SELECT o.posting_id FROM bonafides.applications a JOIN bonafides.saved_opportunities o
            ON o.owner_id=a.owner_id AND o.id=a.opportunity_id WHERE a.owner_id=:owner AND a.id=:id"""),
            {"owner": catalog.owner_id, "id": UUID(first["id"])},
        ).scalar_one()
        related = enqueue(
            conn, catalog.owner_id, "document_generate", {"application_id": first["id"]}
        )
        unrelated_input: dict[str, object] = {"application_id": second["id"]}
        if collision == "workflow":
            unrelated_input["posting"] = {"description_text": first["id"]}
        unrelated = enqueue(conn, catalog.owner_id, "document_generate", unrelated_input)
        if collision == "cached_response":
            conn.execute(
                text("""INSERT INTO bonafides.idempotency_records(
                owner_id,route_key,key,request_sha256,response_status,response_body,expires_at)
                VALUES(:owner,'/synthetic/retained','retained-cache-key',repeat('4',64),200,
                jsonb_build_object('id',CAST(:id AS text),'note',CAST(:note AS text)),
                clock_timestamp()+interval '31 days')"""),
                {"owner": catalog.owner_id, "id": second["id"], "note": first["id"]},
            )
        old = claim(conn, uuid4())
        assert old is not None and old.id == related
    control = _deletion(catalog, first)
    maintenance = InstallationMaintenance(catalog.runtime, control, parser.spool, renderer_spool)
    with maintenance.quiescent():
        result = delete_controlled_owner_data(catalog.runtime, catalog.identity.settings, control)
        with pytest.raises(DBAPIError), catalog.runtime.transaction() as conn:
            assert_fence(conn, old)
        maintenance.finish(result)
    with catalog.runtime.transaction() as conn:
        assert conn.execute(text("SELECT id FROM bonafides.applications")).scalars().all() == [
            UUID(second["id"])
        ]
        assert conn.execute(text("SELECT count(*) FROM bonafides.job_postings")).scalar_one() == 2
        assert conn.execute(text("SELECT id FROM bonafides.document_drafts")).scalar_one() == UUID(
            base["id"]
        )
        assert (
            conn.execute(
                text("SELECT state::text FROM bonafides.workflow_runs WHERE id=:id"),
                {"id": unrelated},
            ).scalar_one()
            == "pending"
        )
        assert (
            conn.execute(
                text("SELECT state::text FROM bonafides.workflow_runs WHERE id=:id"),
                {"id": related},
            ).scalar_one()
            == "cancelled"
        )
        if collision == "cached_response":
            assert conn.execute(
                text("""SELECT response_body FROM bonafides.idempotency_records
                WHERE owner_id=:owner AND route_key='/synthetic/retained'"""),
                {"owner": catalog.owner_id},
            ).scalar_one() == {"id": second["id"], "note": first["id"]}
    store = BlobStore(catalog.identity.settings.blob_root)
    assert (
        store.read(catalog.owner_id, shared.storage_key, shared.sha256, shared.byte_length)
        == b"Shared retained synthetic bytes"
    )
    for removed in (private, orphan):
        assert not (
            catalog.identity.settings.blob_root / str(catalog.owner_id) / removed.storage_key
        ).exists()
    assert catalog.client.get("/api/v1/session").status_code == 401
    login = _login(catalog.identity, catalog.client)
    assert login.status_code == 201, login.text
    catalog.csrf = login.json()["csrf_token"]
    profile = catalog.client.get("/api/v1/profile").json()
    assert first["id"] not in profile["current_revision"]["snapshot"]["writing_fact_revision_ids"]
    restored = catalog.client.post(
        "/api/v1/profile/restore",
        json={"revision_id": historical["id"]},
        headers=catalog.headers(profile["version"]),
    )
    assert restored.status_code == 200, restored.text
    assert (
        first["id"]
        not in restored.json()["current_revision"]["snapshot"]["writing_fact_revision_ids"]
    )
    history = catalog.client.get(f"/api/v1/profile/revisions/{historical['id']}").json()
    assert history["snapshot"] == historical["snapshot"]
    with catalog.runtime.transaction() as conn:
        assert (
            conn.execute(
                text("""SELECT count(*) FROM bonafides.professional_claims c
            JOIN bonafides.claim_revisions r ON r.owner_id=c.owner_id AND r.id=c.current_revision_id
            WHERE c.kind='writing_fact' AND r.payload->>'application_id'=:id
            AND c.archived_at IS NULL"""),
                {"id": first["id"]},
            ).scalar_one()
            == 0
        )
    replay = catalog.client.post(
        "/api/v1/applications",
        json={"posting_id": str(posting_id)},
        headers=catalog.headers(key=request_key),
    )
    assert replay.status_code == 410 and replay.json()["code"] == "data_erased"
    with catalog.runtime.transaction() as conn:
        assert conn.execute(text("SELECT id FROM bonafides.applications")).scalars().all() == [
            UUID(second["id"])
        ]


@pytest.mark.postgres
def test_shared_portrait_survives_application_erasure_until_workspace_history_is_erased(
    catalog: SourceContext, parser: ImportParserClient, renderer_spool: Path
) -> None:
    current = _profile(catalog)
    image = png()
    response = catalog.client.put(
        "/api/v1/profile",
        json={
            **current["current_revision"]["snapshot"],
            "portrait_upload": base64.b64encode(image).decode("ascii"),
        },
        headers=catalog.headers(current["version"]),
    )
    assert response.status_code == 200, response.text
    saved = response.json()
    historical_profile = saved["current_revision"]
    pin = historical_profile["snapshot"]["portrait"]
    task = _queue_base(catalog, saved, "bonafides-evidence-v3")
    Worker(catalog.runtime, replace(catalog.identity.settings, mode="production"))._execute(
        task, Event()
    )
    workflow = catalog.workflow(task.id)
    assert workflow["state"] == "succeeded", workflow
    document = _document(catalog, workflow["result"]["document_id"])
    historical_document = document["current_revision"]
    assert historical_document["provenance_snapshot"]["frame"]["portrait"] == pin
    removed = catalog.client.put(
        "/api/v1/profile",
        json={**historical_profile["snapshot"], "portrait": None},
        headers=catalog.headers(saved["version"]),
    )
    assert removed.status_code == 200, removed.text
    application = _application(catalog, title="Synthetic shared portrait attachment")
    stored = _attachment(catalog, application, image)
    assert stored.sha256 == pin["sha256"]
    control = _deletion(catalog, application)
    maintenance = InstallationMaintenance(catalog.runtime, control, parser.spool, renderer_spool)
    with maintenance.quiescent():
        result = delete_controlled_owner_data(catalog.runtime, catalog.identity.settings, control)
        maintenance.finish(result)
    login = _login(catalog.identity, catalog.client)
    assert login.status_code == 201, login.text
    catalog.csrf = login.json()["csrf_token"]
    history = catalog.client.get(f"/api/v1/profile/revisions/{historical_profile['id']}")
    assert history.status_code == 200, history.text
    assert history.json()["snapshot"] == historical_profile["snapshot"]
    assert history.json()["content_sha256"] == historical_profile["content_sha256"]
    retained_document = _document(catalog, document["id"])["current_revision"]
    assert retained_document == historical_document
    portrait = catalog.client.get(f"/api/v1/profile/portrait/{pin['blob_id']}")
    assert portrait.status_code == 200 and portrait.content == image
    document_pin = PortraitPin.model_validate(
        retained_document["provenance_snapshot"]["frame"]["portrait"]
    )
    with catalog.runtime.transaction() as conn:
        assert conn.execute(text("SELECT count(*) FROM bonafides.applications")).scalar_one() == 0
        assert (
            portrait_bytes(conn, catalog.identity.settings, catalog.owner_id, document_pin) == image
        )
    archive = _export(catalog)
    assert archive.blobs[pin["sha256"]] == image
    for family, identifier, snapshot_key, snapshot in (
        ("profile_revisions", historical_profile["id"], "snapshot", historical_profile["snapshot"]),
        (
            "document_revisions",
            historical_document["id"],
            "provenance_snapshot",
            historical_document["provenance_snapshot"],
        ),
    ):
        row = next(row for row in archive.manifest.records[family] if row["id"] == identifier)
        assert row[snapshot_key] == snapshot
    # The new retention guards must not turn history into an unerasable CAS leak.
    control = _deletion(catalog)
    maintenance = InstallationMaintenance(catalog.runtime, control, parser.spool, renderer_spool)
    with maintenance.quiescent():
        result = delete_controlled_owner_data(catalog.runtime, catalog.identity.settings, control)
        maintenance.finish(result)
    with catalog.runtime.transaction() as conn:
        for family in ("blobs", "document_revisions"):
            assert conn.execute(text(f"SELECT count(*) FROM bonafides.{family}")).scalar_one() == 0
        assert (
            conn.execute(
                text("SELECT count(*) FROM bonafides.profile_revisions WHERE profile_id=:profile"),
                {"profile": UUID(saved["id"])},
            ).scalar_one()
            == 0
        )
    assert not (
        catalog.identity.settings.blob_root / str(catalog.owner_id) / stored.storage_key
    ).exists()


@pytest.mark.postgres
def test_unwritable_live_purge_stays_closed_then_resumes_exact_remaining_bytes(
    catalog: SourceContext, parser: ImportParserClient, renderer_spool: Path
) -> None:
    application = _application(catalog)
    stored = _attachment(catalog, application, b"Synthetic purge must actually unlink this")
    folder = catalog.identity.settings.blob_root / str(catalog.owner_id) / stored.sha256[:2]
    path = folder / stored.sha256
    control = _deletion(catalog, application)
    maintenance = InstallationMaintenance(catalog.runtime, control, parser.spool, renderer_spool)
    with maintenance.quiescent():
        folder.chmod(0o500)
        try:
            with pytest.raises(PermissionError):
                delete_controlled_owner_data(catalog.runtime, catalog.identity.settings, control)
            assert path.read_bytes() == b"Synthetic purge must actually unlink this"
            with catalog.runtime.transaction() as conn:
                assert (
                    conn.execute(text("SELECT count(*) FROM bonafides.applications")).scalar_one()
                    == 0
                )
                assert (
                    conn.execute(
                        text("SELECT maintenance_active FROM bonafides.installation_state")
                    ).scalar_one()
                    is True
                )
                assert (
                    conn.execute(
                        text("SELECT completed_at FROM bonafides.portable_purges")
                    ).scalar_one()
                    is None
                )
            with pytest.raises(DBAPIError) as blocked:
                maintenance.finish({"filesystem_complete": False})
            assert getattr(blocked.value.orig, "sqlstate", None) == "55000"
        finally:
            folder.chmod(0o700)
        assert purge_scope(catalog.runtime, catalog.identity.settings, control) == 1
        assert not path.exists()
        maintenance.finish({"filesystem_complete": True, "scope": "application"})
    with catalog.runtime.transaction() as conn:
        assert (
            conn.execute(
                text("SELECT maintenance_active FROM bonafides.installation_state")
            ).scalar_one()
            is False
        )
        assert (
            conn.execute(text("SELECT outcome FROM bonafides.portable_purge_items")).scalar_one()
            == "removed"
        )


@pytest.mark.postgres
def test_workspace_erasure_removes_reviewed_source_and_profile_history_but_keeps_login(
    catalog: SourceContext,
    parser: ImportParserClient,
    renderer_spool: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _base(catalog, _profile(catalog))
    previous_profile = catalog.client.get("/api/v1/profile").json()
    described = catalog.client.put(
        "/api/v1/profile",
        json={
            **previous_profile["current_revision"]["snapshot"],
            "description": "Synthetic private introduction before erasure.",
        },
        headers=catalog.headers(previous_profile["version"]),
    )
    assert described.status_code == 200, described.text
    previous_profile = described.json()
    assert (
        previous_profile["current_revision"]["snapshot"]["description"]
        == "Synthetic private introduction before erasure."
    )
    catalog.create(enabled=False)
    application = _application(catalog)
    stored = _attachment(catalog, application, b"Synthetic workspace-private bytes")
    with catalog.runtime.transaction() as conn:
        previous_workflow = enqueue(conn, catalog.owner_id, "posting_extract", {})
        conn.execute(
            text("""UPDATE bonafides.local_model_reservations SET owner_id=:owner,
            workflow_run_id=:workflow,fence=1,reserved_until=clock_timestamp()
            WHERE singleton"""),
            {"owner": catalog.owner_id, "workflow": previous_workflow},
        )
    control = _deletion(catalog)
    maintenance = InstallationMaintenance(catalog.runtime, control, parser.spool, renderer_spool)
    with maintenance.quiescent():
        result = delete_controlled_owner_data(catalog.runtime, catalog.identity.settings, control)
        maintenance.finish(result)
    with catalog.runtime.transaction() as conn:
        for name in (
            "professional_claims",
            "evidence_records",
            "applications",
            "document_drafts",
            "job_postings",
            "source_configs",
            "source_policy_reviews",
            "blobs",
        ):
            assert conn.execute(text(f"SELECT count(*) FROM bonafides.{name}")).scalar_one() == 0
        assert (
            conn.execute(
                text("SELECT count(*) FROM bonafides.profile_revisions WHERE profile_id=:profile"),
                {"profile": UUID(previous_profile["id"])},
            ).scalar_one()
            == 0
        )
        assert (
            conn.execute(text("SELECT id FROM bonafides.owners")).scalar_one() == catalog.owner_id
        )
    assert not (
        catalog.identity.settings.blob_root / str(catalog.owner_id) / stored.storage_key
    ).exists()
    assert catalog.client.get("/api/v1/session").status_code == 401
    login = _login(catalog.identity, catalog.client)
    assert login.status_code == 201, login.text
    catalog.csrf = login.json()["csrf_token"]
    reopened = catalog.client.get("/api/v1/profile")
    assert reopened.status_code == 200, reopened.text
    empty = reopened.json()
    assert empty["id"] != previous_profile["id"]
    snapshot = empty["current_revision"]["snapshot"]
    assert snapshot["display_name"] == ""
    assert snapshot["headline"] == ""
    assert snapshot["claim_revision_ids"] == []
    assert snapshot["evidence_revision_ids"] == []
    assert not any(snapshot.values())
    saved = catalog.client.put(
        "/api/v1/profile",
        json={
            **empty["current_revision"]["snapshot"],
            "display_name": "Synthetic owner after erasure",
        },
        headers=catalog.headers(empty["version"]),
    )
    assert saved.status_code == 200, saved.text
    assert (
        catalog.client.get("/api/v1/profile").json()["current_revision"]["snapshot"]["display_name"]
        == "Synthetic owner after erasure"
    )
    _route(monkeypatch, tmp_path)
    posting = _posting(catalog)
    task = _queue(catalog, _view(catalog, posting["id"]))
    with catalog.runtime.transaction() as conn:
        assert suitability._reserve_call(conn, task, catalog.identity.settings, 2) is not None


@pytest.mark.postgres
@pytest.mark.parametrize("workspace", [False, True])
def test_bulk_archive_erasure_scrubs_metadata_and_waits_for_private_copy_removal(
    catalog: SourceContext, parser: ImportParserClient, renderer_spool: Path, workspace: bool
) -> None:
    application = _application(catalog, title="Synthetic archive erasure target")
    neighbor = _application(catalog, title="Synthetic retained archive neighbor")
    retained = _attachment(catalog, neighbor, b"Unrelated shared storage remains intact")
    # Erasure-policy fixture, not evidence of rendering: the archive includes
    # nested application references and private labels, unlike a single PDF task.
    with catalog.runtime.transaction() as conn:
        archive_id = enqueue(
            conn,
            catalog.owner_id,
            "pdf_render",
            {
                "mode": "document_archive",
                "manifest": {
                    "documents": [
                        {"application_id": application["id"], "company": "Private erased company"}
                    ]
                },
            },
        )
        conn.execute(
            text("""UPDATE bonafides.workflow_runs SET state='succeeded',fence=1,attempt=1,
            finished_at=clock_timestamp(),result=jsonb_build_object(
            'artifact_type','document_archive','archive_filename',CAST(:file AS text))
            WHERE owner_id=:owner AND id=:id"""),
            {"owner": catalog.owner_id, "id": archive_id, "file": f"{archive_id}.1.zip"},
        )
        pending_id = enqueue(
            conn,
            catalog.owner_id,
            "pdf_render",
            {
                "mode": "document_archive",
                "manifest": {"documents": [{"application_id": application["id"]}]},
            },
        )
        conn.execute(
            text("""INSERT INTO bonafides.workflow_steps
            (owner_id,workflow_run_id,step_key,attempt,fence,state,checkpoint,completed_at)
            VALUES(:owner,:id,'publish',1,1,'succeeded',
            '{"company":"Private erased company"}',clock_timestamp())"""),
            {"owner": catalog.owner_id, "id": archive_id},
        )
        request_version = conn.execute(
            text("SELECT workspace_version FROM bonafides.owner_settings WHERE owner_id=:owner"),
            {"owner": catalog.owner_id},
        ).scalar_one()
        command(
            conn,
            catalog.owner_id,
            "POST",
            "/document-archives",
            {},
            {"manifest_sha256": "a" * 64},
            f'"{request_version}"',
            "synthetic-archive-replay",
            "workspace",
            catalog.owner_id,
            lambda active: (202, {"id": str(archive_id)}),
        )
    folder = catalog.identity.settings.blob_root / "document_archives" / str(catalog.owner_id)
    folder.mkdir(parents=True)
    raw = b"Synthetic private archive bytes requiring synchronous erasure"
    path = folder / f"{archive_id}.1.zip"
    path.write_bytes(raw)
    staged = folder / (".staging-" + uuid4().hex)
    staged.write_bytes(b"Interrupted private archive")
    control = _deletion(catalog, None if workspace else application)
    maintenance = InstallationMaintenance(catalog.runtime, control, parser.spool, renderer_spool)
    with maintenance.quiescent():
        folder.chmod(0o500)
        try:
            with pytest.raises(PermissionError):
                delete_controlled_owner_data(catalog.runtime, catalog.identity.settings, control)
            assert path.read_bytes() == raw
            with catalog.runtime.transaction() as conn:
                assert (
                    conn.execute(
                        text(
                            "SELECT completed_at FROM bonafides.portable_purges WHERE control_id=:id"
                        ),
                        {"id": control.id},
                    ).scalar_one()
                    is None
                )
                if not workspace:
                    erased = (
                        conn.execute(
                            text(
                                "SELECT input,result,last_error_code FROM bonafides.workflow_runs WHERE id=:id"
                            ),
                            {"id": archive_id},
                        )
                        .mappings()
                        .one()
                    )
                    assert erased["input"] == {"erased": True}
                    assert erased["result"] is None and erased["last_error_code"] == "data_erased"
                    pending = (
                        conn.execute(
                            text(
                                "SELECT state::text,input,last_error_code FROM bonafides.workflow_runs WHERE id=:id"
                            ),
                            {"id": pending_id},
                        )
                        .mappings()
                        .one()
                    )
                    assert pending == {
                        "state": "cancelled",
                        "input": {"erased": True},
                        "last_error_code": "data_erased",
                    }
                    assert (
                        conn.execute(
                            text(
                                "SELECT checkpoint FROM bonafides.workflow_steps WHERE workflow_run_id=:id"
                            ),
                            {"id": archive_id},
                        ).scalar_one()
                        == {}
                    )
                    replay = (
                        conn.execute(
                            text(
                                "SELECT response_status,response_body FROM bonafides.idempotency_records WHERE key='synthetic-archive-replay'"
                            )
                        )
                        .mappings()
                        .one()
                    )
                    assert replay["response_status"] == 410
                    assert replay["response_body"]["code"] == "data_erased"
            with pytest.raises(DBAPIError):
                maintenance.finish({"filesystem_complete": False})
        finally:
            folder.chmod(0o700)
        purge_scope(catalog.runtime, catalog.identity.settings, control)
        assert not path.exists() and not staged.exists()
        maintenance.finish({"filesystem_complete": True})
    login = _login(catalog.identity, catalog.client)
    assert login.status_code == 201, login.text
    catalog.csrf = login.json()["csrf_token"]
    replayed = catalog.client.post(
        "/api/v1/document-archives",
        json={"manifest_sha256": "a" * 64},
        headers=catalog.headers(request_version, key="synthetic-archive-replay"),
    )
    assert replayed.status_code == 410, replayed.text
    assert replayed.json()["code"] == "data_erased"
    assert catalog.client.get(f"/api/v1/document-archives/{archive_id}/download").status_code in (
        404,
        410,
    )
    if not workspace:
        assert (
            catalog.identity.settings.blob_root / str(catalog.owner_id) / retained.storage_key
        ).read_bytes() == b"Unrelated shared storage remains intact"
