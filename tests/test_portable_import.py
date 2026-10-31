# SPDX-License-Identifier: MIT OR Apache-2.0
from __future__ import annotations

import hashlib
import io
from datetime import timedelta
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from pydantic import JsonValue
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_action_approvals import Prepared, _approve, _owner
from test_document_generation import _base, _document
from test_identity import _login
from test_identity import identity as identity
from test_import_parser import parser as parser
from test_installation_control import renderer_spool as renderer_spool
from test_portable_archive import _encoded
from test_portable_data import _digest, _export, _historical_model, _historical_proposal
from test_portable_erasure import _checkpoint
from test_sources import SourceContext
from test_sources import catalog as catalog
from test_suitability import _profile

from bonafides.approvals import consume_action_approval, prepare_action_approval
from bonafides.commands import canonical_json
from bonafides.import_parser import ImportParserClient
from bonafides.installation_control import InstallationMaintenance, begin_control
from bonafides.portable_archive import ArchiveError, BlobMember, PortableArchive
from bonafides.portable_import import import_controlled_owner_data
from bonafides.storage import BlobStore
from bonafides.workflows import claim_control


def _source(context: SourceContext) -> tuple[PortableArchive, bytes]:
    _base(context, _profile(context))
    original = _export(context)
    source_owner = uuid4()
    for rows in original.manifest.records.values():
        for row in rows:
            if "owner_id" in row:
                row["owner_id"] = str(source_owner)
            if "acknowledged_by_owner_id" in row:
                row["acknowledged_by_owner_id"] = str(source_owner)
    blobs = dict(original.blobs)
    archive = PortableArchive(
        original.manifest.model_copy(update={"source_owner_id": source_owner}), blobs
    )
    observation = _historical_model(archive)
    archive.manifest.records["model_run_provenance"].append(observation)
    archive.manifest.records["document_regeneration_proposals"].append(
        _historical_proposal(archive, cast(dict[str, JsonValue], observation["record"]))
    )
    payload = b"Synthetic portable evidence. Exact private bytes.\n"
    digest = hashlib.sha256(payload).hexdigest()
    blob = BlobMember(
        blob_id=uuid4(),
        sha256=digest,
        byte_length=len(payload),
        media_type="text/plain",
        archive_member=f"blobs/{digest}",
    )
    archive.manifest.records["evidence_revisions"][0]["blob_id"] = str(blob.blob_id)
    archive.manifest.records["evidence_revisions"][0].update(
        {"source_type": "work_sample", "privacy_class": "confidential", "employer_owned": True}
    )
    archive.manifest.blob_manifest.append(blob)
    blobs[digest] = payload
    archive = _digest(archive)
    return archive, _encoded(archive.manifest, archive.blobs)


def _approved_import(context: SourceContext, payload: bytes) -> Prepared:
    identifier = uuid4()
    snapshot = {"synthetic_conformance": "empty isolated import target"}
    with context.runtime.transaction() as conn:
        owner = _owner(conn, context.owner_id)
        namespace = conn.execute(
            text("SELECT namespace_id FROM bonafides.installation_state WHERE singleton")
        ).scalar_one()
        conn.execute(
            text("""INSERT INTO bonafides.workflow_runs
        (owner_id,id,kind,input,dedupe_key,request_sha256,deadline_at,maintenance_epoch)
        SELECT :owner,:id,'portable_import',CAST(:input AS jsonb),:dedupe,:sha,
        clock_timestamp()+interval '5 minutes',maintenance_epoch
        FROM bonafides.installation_state WHERE singleton"""),
            {
                "owner": context.owner_id,
                "id": identifier,
                "dedupe": str(identifier),
                "input": canonical_json(snapshot),
                "sha": hashlib.sha256(canonical_json(snapshot).encode()).hexdigest(),
            },
        )
        destination: dict[str, object] = {"namespace_id": str(namespace)}
        intent: dict[str, object] = {
            "preview_sha256": "2" * 64,
            "replacement_sha256": hashlib.sha256(payload).hexdigest(),
        }
        approval = prepare_action_approval(
            conn,
            context.owner_id,
            identifier,
            action_kind="replace_workspace",
            destination=destination,
            payload=intent,
            session_id=owner.session_id,
        )
    prepared = Prepared(identifier, approval, destination, intent, "replace_workspace")
    _approve(context.runtime.engine, owner, prepared)
    # Only the trusted producer dispatch is seeded. All approval consumption,
    # fencing, actual daemon drain, import and relational/blob publication below
    # are production code. The owner-facing replacement producer is not qualified.
    with context.identity.admin.transaction() as conn:
        conn.execute(
            text("""UPDATE bonafides.workflow_runs SET state='pending',
        version=version+1,last_error_code=NULL WHERE owner_id=:owner AND id=:id"""),
            {"owner": context.owner_id, "id": identifier},
        )
    return prepared


@pytest.mark.postgres
def test_foreign_historical_import_preserves_native_rows_and_bytes_without_authority(
    catalog: SourceContext,
    parser: ImportParserClient,
    renderer_spool: Path,
) -> None:
    archive, payload = _source(catalog)
    changed = _digest(
        PortableArchive(
            archive.manifest.model_copy(
                update={"created_at": archive.manifest.created_at + timedelta(seconds=1)}
            ),
            archive.blobs,
        )
    )
    changed_payload = _encoded(changed.manifest, changed.blobs)
    # The old workspace and replacement share the same physical CAS object.
    # Metadata is erased/reinserted atomically; cleanup must retain its live bytes.
    shared = archive.manifest.blob_manifest[0]
    shared_stored = BlobStore(catalog.identity.settings.blob_root).put_bytes(
        catalog.owner_id, archive.blobs[shared.sha256]
    )
    with catalog.runtime.transaction() as conn:
        conn.execute(
            text("""INSERT INTO bonafides.blobs(owner_id,id,sha256,byte_length,media_type,storage_key)
            VALUES(:owner,:id,:sha,:size,:media,:key)"""),
            {
                "owner": catalog.owner_id,
                "id": shared.blob_id,
                "sha": shared.sha256,
                "size": shared.byte_length,
                "media": shared.media_type,
                "key": shared_stored.storage_key,
            },
        )
    checkpoint_id, runtime_pins = _checkpoint(catalog, parser.spool, renderer_spool)
    prepared = _approved_import(catalog, payload)
    with catalog.runtime.transaction() as conn:
        task = claim_control(conn, uuid4())
        assert task is not None and task.id == prepared.workflow_id
        consume_action_approval(
            conn,
            task,
            action_kind="replace_workspace",
            destination=prepared.destination,
            payload=prepared.payload,
            target_version=cast(int, prepared.approval["target_version"]),
        )
        control = begin_control(conn, task)
    maintenance = InstallationMaintenance(catalog.runtime, control, parser.spool, renderer_spool)
    with maintenance.quiescent():
        with pytest.raises(ArchiveError, match="portable_import_scope_mismatch"):
            import_controlled_owner_data(
                catalog.runtime,
                catalog.identity.settings,
                control,
                changed_payload,
                expected_source_owner=archive.manifest.source_owner_id,
                checkpoint_id=checkpoint_id,
                runtime_pins=runtime_pins,
            )
        with pytest.raises(ArchiveError, match="portable_owner_mismatch"):
            import_controlled_owner_data(
                catalog.runtime,
                catalog.identity.settings,
                control,
                payload,
                expected_source_owner=uuid4(),
                checkpoint_id=checkpoint_id,
                runtime_pins=runtime_pins,
            )
        source = io.BytesIO(payload)
        result = import_controlled_owner_data(
            catalog.runtime,
            catalog.identity.settings,
            control,
            source,
            expected_source_owner=archive.manifest.source_owner_id,
            checkpoint_id=checkpoint_id,
            runtime_pins=runtime_pins,
        )
        assert not source.closed
        with pytest.raises(DBAPIError, match="fresh verified checkpoint"):
            import_controlled_owner_data(
                catalog.runtime,
                catalog.identity.settings,
                control,
                payload,
                expected_source_owner=archive.manifest.source_owner_id,
                checkpoint_id=checkpoint_id,
                runtime_pins=runtime_pins,
            )
        maintenance.finish(result)
    with catalog.runtime.transaction() as conn:
        assert conn.execute(text("SELECT count(*) FROM bonafides.model_runs")).scalar_one() == 0
        assert (
            conn.execute(text("SELECT count(*) FROM bonafides.hosted_egress_consents")).scalar_one()
            == 0
        )
        history = (
            conn.execute(text("SELECT * FROM bonafides.portable_model_history")).mappings().one()
        )
        original = archive.manifest.records["model_run_provenance"][0]
        assert history["owner_id"] == catalog.owner_id
        assert history["source_owner_id"] == archive.manifest.source_owner_id
        assert history["record"] == original["record"]
        proposal = (
            conn.execute(text("SELECT * FROM bonafides.document_regeneration_proposals"))
            .mappings()
            .one()
        )
        assert proposal["model_run_id"] is None and proposal["workflow_run_id"] is None
        assert proposal["historical_model_run_id"] == history["id"]
        current = conn.execute(
            text("SELECT current_revision_id FROM bonafides.profiles")
        ).scalar_one()
        assert current == archive.manifest.current_profile_revision_id
        assert (
            conn.execute(
                text("""SELECT outcome FROM bonafides.portable_purge_items
            WHERE control_id=:id AND kind='cas' AND storage_key=:key"""),
                {"id": control.id, "key": shared_stored.storage_key},
            ).scalar_one()
            == "retained"
        )
    blob = archive.manifest.blob_manifest[0]
    actual = BlobStore(catalog.identity.settings.blob_root).read(
        catalog.owner_id, f"{blob.sha256[:2]}/{blob.sha256}", blob.sha256, blob.byte_length
    )
    assert actual == archive.blobs[blob.sha256]
    assert catalog.client.get("/api/v1/session").status_code == 401
    login = _login(catalog.identity, catalog.client)
    assert login.status_code == 201, login.text
    catalog.csrf = login.json()["csrf_token"]
    document_id = str(archive.manifest.records["document_drafts"][0]["id"])
    response = catalog.client.get(f"/api/v1/documents/{document_id}/regeneration-proposals")
    assert response.status_code == 200, response.text
    stored = response.json()["items"][0]
    assert stored["historical"] is True and stored["applicable"] is False
    document = _document(catalog, document_id)
    rejected = catalog.client.post(
        f"/api/v1/documents/{document_id}/regeneration-proposals/{stored['id']}/apply",
        json={
            "revision_id": document["current_revision"]["id"],
            "content_sha256": document["current_revision"]["content_sha256"],
            "reason": "Cannot revive historical authority",
        },
        headers=catalog.headers(document["version"]),
    )
    assert (
        rejected.status_code == 409
        and rejected.json()["code"] == "historical_regeneration_proposal"
    )
    again = _export(catalog)
    assert (
        again.manifest.records["model_run_provenance"]
        == archive.manifest.records["model_run_provenance"]
    )
    assert again.manifest.records["document_regeneration_proposals"][0]["model_run_id"] == str(
        history["id"]
    )
    assert (
        again.manifest.records["document_regeneration_proposals"][0]["workflow_run_id"]
        == cast(dict[str, JsonValue], original["record"])["workflow_run_id"]
    )
    for family in (
        "profile_revisions",
        "claim_revisions",
        "evidence_revisions",
        "document_revisions",
    ):
        expected = {(row["id"], row["content_sha256"]) for row in archive.manifest.records[family]}
        assert {
            (row["id"], row["content_sha256"]) for row in again.manifest.records[family]
        } == expected
    provenance = ("source_type", "origin", "privacy_class", "employer_owned")
    assert {
        (row["id"], *(row[key] for key in provenance))
        for row in again.manifest.records["evidence_revisions"]
    } == {
        (row["id"], *(row[key] for key in provenance))
        for row in archive.manifest.records["evidence_revisions"]
    }
    assert ("work_sample", "confidential", True) in {
        (row["source_type"], row["privacy_class"], row["employer_owned"])
        for row in again.manifest.records["evidence_revisions"]
    }


@pytest.mark.postgres
def test_import_without_exact_live_control_cannot_publish(catalog: SourceContext) -> None:

    from bonafides.installation_control import Control

    archive, payload = _source(catalog)
    before = _export(catalog).manifest.records
    control = Control(uuid4(), catalog.owner_id, uuid4(), 1, uuid4(), 1, "portable_import")
    with pytest.raises(DBAPIError):
        import_controlled_owner_data(
            catalog.runtime,
            catalog.identity.settings,
            control,
            payload,
            expected_source_owner=archive.manifest.source_owner_id,
            checkpoint_id=uuid4(),
            runtime_pins={},
        )
    assert _export(catalog).manifest.records == before


@pytest.mark.postgres
def test_replacement_publication_failure_rolls_back_erasure_and_preserves_original_bytes(
    catalog: SourceContext, parser: ImportParserClient, renderer_spool: Path
) -> None:
    archive, payload = _source(catalog)
    store = BlobStore(catalog.identity.settings.blob_root)
    original_bytes = b"Original synthetic live bytes must survive SQL rollback"
    original_blob = store.put_bytes(catalog.owner_id, original_bytes)
    with catalog.runtime.transaction() as conn:
        conn.execute(
            text("""INSERT INTO bonafides.blobs(owner_id,sha256,byte_length,media_type,storage_key)
            VALUES(:owner,:sha,:size,'text/plain',:key)"""),
            {
                "owner": catalog.owner_id,
                "sha": original_blob.sha256,
                "size": original_blob.byte_length,
                "key": original_blob.storage_key,
            },
        )
    checkpoint_id, runtime_pins = _checkpoint(catalog, parser.spool, renderer_spool)
    prepared = _approved_import(catalog, payload)
    with catalog.runtime.transaction() as conn:
        task = claim_control(conn, uuid4(), workflow_id=prepared.workflow_id)
        assert task is not None
        consume_action_approval(
            conn,
            task,
            action_kind="replace_workspace",
            destination=prepared.destination,
            payload=prepared.payload,
            target_version=cast(int, prepared.approval["target_version"]),
        )
        control = begin_control(conn, task)

    def live_rows() -> dict[str, object]:
        with catalog.runtime.transaction() as conn:
            return {
                name: conn.execute(
                    text(f"""SELECT coalesce(jsonb_agg(to_jsonb(r) ORDER BY to_jsonb(r)::text),'[]')
                    FROM bonafides.{name} r WHERE owner_id=:owner"""),
                    {"owner": catalog.owner_id},
                ).scalar_one()
                for name in (
                    "profiles",
                    "profile_revisions",
                    "document_drafts",
                    "document_revisions",
                    "blobs",
                    "owner_settings",
                    "sessions",
                )
            }

    maintenance = InstallationMaintenance(catalog.runtime, control, parser.spool, renderer_spool)
    with maintenance.quiescent():
        before = live_rows()
        with catalog.identity.admin.transaction() as conn:
            # A real native trigger faults publication after the eraser ran.
            # LIKE-based quarantine does not copy triggers, so validation passes.
            conn.exec_driver_sql("""CREATE FUNCTION bonafides.test_portable_publication_failure()
            RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
             RAISE EXCEPTION 'synthetic portable publication failure' USING ERRCODE='55000';
            END $$;
            CREATE TRIGGER test_portable_publication_failure BEFORE INSERT ON bonafides.document_revisions
            FOR EACH ROW EXECUTE FUNCTION bonafides.test_portable_publication_failure();""")
        try:
            with pytest.raises(DBAPIError, match="synthetic portable publication failure"):
                import_controlled_owner_data(
                    catalog.runtime,
                    catalog.identity.settings,
                    control,
                    payload,
                    expected_source_owner=archive.manifest.source_owner_id,
                    checkpoint_id=checkpoint_id,
                    runtime_pins=runtime_pins,
                )
            assert live_rows() == before
            assert (
                store.read(
                    catalog.owner_id,
                    original_blob.storage_key,
                    original_blob.sha256,
                    original_blob.byte_length,
                )
                == original_bytes
            )
            with catalog.runtime.transaction() as conn:
                assert (
                    conn.execute(
                        text("SELECT count(*) FROM bonafides.portable_purges")
                    ).scalar_one()
                    == 0
                )
                assert (
                    conn.execute(
                        text("SELECT maintenance_active FROM bonafides.installation_state")
                    ).scalar_one()
                    is True
                )
        finally:
            with catalog.identity.admin.transaction() as conn:
                conn.exec_driver_sql("""DROP TRIGGER test_portable_publication_failure ON bonafides.document_revisions;
                DROP FUNCTION bonafides.test_portable_publication_failure();""")


@pytest.mark.postgres
def test_archive_from_before_structured_claims_rewrites_free_text_claims(
    catalog: SourceContext,
    parser: ImportParserClient,
    renderer_spool: Path,
) -> None:
    statements = (
        "Reduced batch runtime by 30% across 3 warehouses",
        "Hired 4 engineers for the platform team",
        "Proposed a new on-call rotation",
        "Cut batch runtime by 30% across 3 warehouses",
        "Reduced batch runtime by 30% across 3 warehouses.",
        "Implemented a new on-call rotation",
    )
    version = catalog.client.get("/api/v1/profile").json()["version"]
    evidence = catalog.client.post(
        "/api/v1/evidence",
        json={
            "payload": {"text": ". ".join(statements), "source": "Synthetic review"},
            "provenance": {
                "source_type": "document",
                "privacy_class": "private",
                "employer_owned": False,
            },
        },
        headers=catalog.headers(version),
    )
    assert evidence.status_code == 200, evidence.text
    evidence_id = evidence.json()["evidence"][-1]["revision"]["id"]
    version = evidence.json()["version"]
    for statement in statements:
        saved = catalog.client.post(
            "/api/v1/claims",
            json={
                "kind": "experience",
                "payload": {"role": "Platform engineer", "text": statement},
                "evidence_links": [
                    {
                        "evidence_revision_id": evidence_id,
                        "support_kind": "direct",
                        "excerpt": statement,
                    }
                ],
            },
            headers=catalog.headers(version),
        )
        assert saved.status_code == 200, saved.text
        version = saved.json()["version"]
    original = _export(catalog)
    source_owner = uuid4()
    for rows in original.manifest.records.values():
        for row in rows:
            if "owner_id" in row:
                row["owner_id"] = str(source_owner)
    # Archives from before evidence provenance carry no provenance columns.
    for row in original.manifest.records["evidence_revisions"]:
        for key in ("source_type", "origin", "privacy_class", "employer_owned"):
            del row[key]
    archive = _digest(
        PortableArchive(
            original.manifest.model_copy(
                update={"source_owner_id": source_owner, "source_schema_version": 27}
            ),
            original.blobs,
        )
    )
    payload = _encoded(archive.manifest, archive.blobs)
    checkpoint_id, runtime_pins = _checkpoint(catalog, parser.spool, renderer_spool)
    prepared = _approved_import(catalog, payload)
    with catalog.runtime.transaction() as conn:
        task = claim_control(conn, uuid4())
        assert task is not None and task.id == prepared.workflow_id
        consume_action_approval(
            conn,
            task,
            action_kind="replace_workspace",
            destination=prepared.destination,
            payload=prepared.payload,
            target_version=cast(int, prepared.approval["target_version"]),
        )
        control = begin_control(conn, task)
    maintenance = InstallationMaintenance(catalog.runtime, control, parser.spool, renderer_spool)
    with maintenance.quiescent():
        result = import_controlled_owner_data(
            catalog.runtime,
            catalog.identity.settings,
            control,
            payload,
            expected_source_owner=source_owner,
            checkpoint_id=checkpoint_id,
            runtime_pins=runtime_pins,
        )
        maintenance.finish(result)
    assert _login(catalog.identity, catalog.client).status_code == 201
    profile = catalog.client.get("/api/v1/profile").json()
    claims = profile["current_revision"]["claims"]
    assert [(item["rendering"], item["needs_attention"]) for item in claims] == [
        (statements[0], False),
        (statements[1], True),
        (statements[2], True),
        (statements[3], False),
        (statements[4], True),
        (statements[5], False),
    ]
    assert profile["current_revision"]["reason"] == "claims_restructured"
    assert claims[5]["revision"]["payload"]["claim"]["verb"] == "implemented"
    assert claims[5]["revision"]["evidence_links"][0]["excerpt"] == statements[5]
    assert claims[5]["revision"]["evidence_links"][0]["evidence"]["provenance"] == {
        "source_type": "owner_statement",
        "origin": "owner_entry",
        "privacy_class": "private",
        "employer_owned": False,
    }
