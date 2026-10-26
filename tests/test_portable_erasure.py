# SPDX-License-Identifier: MIT OR Apache-2.0
"""Real runtime-role erasure, filesystem failure/recovery and replay boundaries.

The checkpoint helper seeds offline-attested SQL metadata to isolate policy tests;
it is NOT a physical backup or restore drill. Actual native CLI qualification is
retained separately. Destructive producer dispatch is explicit fixture setup, not
claimed HTTP/UI acceptance of the owner-facing producer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_action_approvals import Prepared, _approve, _owner
from test_document_generation import _base
from test_identity import _login
from test_identity import identity as identity
from test_import_parser import parser as parser
from test_installation_control import _queue_control
from test_installation_control import renderer_spool as renderer_spool
from test_sources import SourceContext
from test_sources import catalog as catalog
from test_suitability import _posting, _profile, _queue, _route, _view
from test_tracking import _application

from offerloom import suitability
from offerloom.approvals import consume_action_approval, prepare_action_approval
from offerloom.import_parser import ImportParserClient
from offerloom.installation_control import Control, InstallationMaintenance, begin_control
from offerloom.portable_erasure import delete_controlled_owner_data, purge_scope
from offerloom.storage import BlobStore, StoredBlob
from offerloom.workflows import assert_fence, claim, claim_control, enqueue


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
                text("""INSERT INTO offerloom.installation_backups(
                id,owner_id,namespace_id,maintenance_epoch,workspace_version,
                installation_auth_epoch,owner_auth_epoch,schema_versions,
                image_sha256,source_sha256,config_sha256,keyring_sha256,recipient_sha256,
                file_name,cipher_sha256,manifest_sha256,byte_length,captured_at,
                restore_verified_at,restore_receipt_sha256,restore_target_namespace)
                SELECT :id,:owner,:namespace,:epoch,s.workspace_version,i.auth_epoch,o.auth_epoch,
                (SELECT jsonb_object_agg(version::text,source_sha256) FROM offerloom.schema_migrations),
                repeat('0',64),repeat('0',64),repeat('0',64),repeat('0',64),repeat('0',64),
                :file,repeat('0',64),repeat('0',64),1,clock_timestamp(),
                clock_timestamp(),repeat('0',64),:target
                FROM offerloom.owner_settings s JOIN offerloom.owners o ON o.id=s.owner_id
                CROSS JOIN offerloom.installation_state i WHERE s.owner_id=:owner AND i.singleton"""),
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
            text("SELECT namespace_id FROM offerloom.installation_state WHERE singleton")
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
            text("""UPDATE offerloom.workflow_runs SET state='pending',version=version+1,
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
            text("""INSERT INTO offerloom.blobs(owner_id,sha256,byte_length,media_type,storage_key)
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
            text("""INSERT INTO offerloom.application_attachments(owner_id,application_id,blob_id,display_name)
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
    orphan = BlobStore(catalog.identity.settings.blob_root).put_bytes(
        catalog.owner_id, b"Unpublished interrupted synthetic bytes"
    )
    with catalog.runtime.transaction() as conn:
        request_key = conn.execute(
            text("""SELECT key FROM offerloom.idempotency_records
            WHERE owner_id=:owner AND response_status=201 AND response_body->>'id'=:id"""),
            {"owner": catalog.owner_id, "id": first["id"]},
        ).scalar_one()
        posting_id = conn.execute(
            text("""SELECT o.posting_id FROM offerloom.applications a JOIN offerloom.saved_opportunities o
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
                text("""INSERT INTO offerloom.idempotency_records(
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
        assert conn.execute(text("SELECT id FROM offerloom.applications")).scalars().all() == [
            UUID(second["id"])
        ]
        assert conn.execute(text("SELECT count(*) FROM offerloom.job_postings")).scalar_one() == 2
        assert conn.execute(text("SELECT id FROM offerloom.document_drafts")).scalar_one() == UUID(
            base["id"]
        )
        assert (
            conn.execute(
                text("SELECT state::text FROM offerloom.workflow_runs WHERE id=:id"),
                {"id": unrelated},
            ).scalar_one()
            == "pending"
        )
        assert (
            conn.execute(
                text("SELECT state::text FROM offerloom.workflow_runs WHERE id=:id"),
                {"id": related},
            ).scalar_one()
            == "cancelled"
        )
        if collision == "cached_response":
            assert conn.execute(
                text("""SELECT response_body FROM offerloom.idempotency_records
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
    replay = catalog.client.post(
        "/api/v1/applications",
        json={"posting_id": str(posting_id)},
        headers=catalog.headers(key=request_key),
    )
    assert replay.status_code == 410 and replay.json()["code"] == "data_erased"
    with catalog.runtime.transaction() as conn:
        assert conn.execute(text("SELECT id FROM offerloom.applications")).scalars().all() == [
            UUID(second["id"])
        ]


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
                    conn.execute(text("SELECT count(*) FROM offerloom.applications")).scalar_one()
                    == 0
                )
                assert (
                    conn.execute(
                        text("SELECT maintenance_active FROM offerloom.installation_state")
                    ).scalar_one()
                    is True
                )
                assert (
                    conn.execute(
                        text("SELECT completed_at FROM offerloom.portable_purges")
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
                text("SELECT maintenance_active FROM offerloom.installation_state")
            ).scalar_one()
            is False
        )
        assert (
            conn.execute(text("SELECT outcome FROM offerloom.portable_purge_items")).scalar_one()
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
    catalog.create(enabled=False)
    application = _application(catalog)
    stored = _attachment(catalog, application, b"Synthetic workspace-private bytes")
    with catalog.runtime.transaction() as conn:
        previous_workflow = enqueue(conn, catalog.owner_id, "posting_extract", {})
        conn.execute(
            text("""UPDATE offerloom.local_model_reservations SET owner_id=:owner,
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
            assert conn.execute(text(f"SELECT count(*) FROM offerloom.{name}")).scalar_one() == 0
        assert (
            conn.execute(
                text("SELECT count(*) FROM offerloom.profile_revisions WHERE profile_id=:profile"),
                {"profile": UUID(previous_profile["id"])},
            ).scalar_one()
            == 0
        )
        assert (
            conn.execute(text("SELECT id FROM offerloom.owners")).scalar_one() == catalog.owner_id
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
    assert empty["current_revision"]["snapshot"] == {
        "display_name": "",
        "headline": "",
        "claim_revision_ids": [],
        "evidence_revision_ids": [],
    }
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
