# SPDX-License-Identifier: MIT OR Apache-2.0
from __future__ import annotations

import io
from copy import deepcopy
from typing import cast
from uuid import UUID, uuid4

import pytest
from pydantic import JsonValue
from sqlalchemy import text
from test_document_generation import _base, _document, _save
from test_identity import identity as identity
from test_sources import SourceContext
from test_sources import catalog as catalog
from test_suitability import _profile

from bonafides.commands import canonical_json
from bonafides.portable_archive import (
    ArchiveError,
    PortableArchive,
    decode_archive,
    encode_archive,
    manifest_digest,
)
from bonafides.portable_data import collect_owner_data, export_owner_data, quarantine_records
from bonafides.profiles import content_hash
from bonafides.storage import BlobStore


def _export(context: SourceContext) -> PortableArchive:
    output = io.BytesIO()
    manifest = export_owner_data(
        context.runtime, context.identity.settings, context.owner_id, output
    )
    assert manifest.format_version == 2
    return decode_archive(output, expected_source_owner=context.owner_id)


def _digest(archive: PortableArchive) -> PortableArchive:
    manifest = archive.manifest.model_copy(
        update={"manifest_sha256": manifest_digest(archive.manifest)}
    )
    return PortableArchive(manifest, archive.blobs)


def _snapshot(context: SourceContext) -> object:
    with context.identity.admin.transaction() as conn:
        return conn.execute(
            text("""SELECT jsonb_build_object(
            'profiles',(SELECT jsonb_agg(to_jsonb(p)) FROM bonafides.profiles p),
            'profile_revisions',(SELECT jsonb_agg(to_jsonb(r)) FROM bonafides.profile_revisions r),
            'documents',(SELECT jsonb_agg(to_jsonb(d)) FROM bonafides.document_revisions d),
            'blobs',(SELECT jsonb_agg(to_jsonb(b)) FROM bonafides.blobs b),
            'sessions',(SELECT jsonb_agg(to_jsonb(s)) FROM bonafides.sessions s))""")
        ).scalar_one()


@pytest.mark.postgres
def test_nonempty_document_evidence_and_blob_survive_byte_and_relational_validation(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = _base(catalog, _profile(catalog))
    body = deepcopy(document["current_revision"]["body"])
    factual = next(
        block
        for section in body["sections"]
        for block in section["blocks"]
        if block["claim_kind"] == "factual"
    )
    factual["text"] = "Certified Kubernetes expert with invented qualifications."
    saved = _save(catalog, document, body)
    assert saved.status_code == 200, saved.text
    document = _document(catalog, document["id"])
    assert document["current_revision"]["validation_issues"]
    blocked = catalog.client.post(
        f"/api/v1/documents/{document['id']}/renders",
        json={
            "revision_id": document["current_revision"]["id"],
            "content_sha256": document["current_revision"]["content_sha256"],
            "format": "pdf",
        },
        headers=catalog.headers(document["version"]),
    )
    assert blocked.status_code == 409, blocked.text
    payload = b"Synthetic attached evidence with exact retained bytes.\n"
    stored = BlobStore(catalog.identity.settings.blob_root).put_bytes(catalog.owner_id, payload)
    blob_id, evidence_id, revision_id = uuid4(), uuid4(), uuid4()
    evidence = {"text": "Synthetic attached evidence", "source": "Owner supplied"}
    with catalog.runtime.transaction() as conn:
        conn.execute(
            text("""INSERT INTO bonafides.blobs(owner_id,id,sha256,byte_length,media_type,storage_key)
            VALUES(:owner,:id,:sha,:bytes,'text/plain',:key)"""),
            {
                "owner": catalog.owner_id,
                "id": blob_id,
                "sha": stored.sha256,
                "bytes": stored.byte_length,
                "key": stored.storage_key,
            },
        )
        conn.execute(
            text("""INSERT INTO bonafides.evidence_records(owner_id,id,current_revision_id)
            VALUES(:owner,:id,:revision)"""),
            {"owner": catalog.owner_id, "id": evidence_id, "revision": revision_id},
        )
        conn.execute(
            text("""INSERT INTO bonafides.evidence_revisions(owner_id,id,evidence_id,revision_no,payload,blob_id,content_sha256,
            source_type,origin,privacy_class,employer_owned)
            VALUES(:owner,:id,:evidence,1,CAST(:payload AS jsonb),:blob,:sha,
            'work_sample','owner_entry','confidential',true)"""),
            {
                "owner": catalog.owner_id,
                "id": revision_id,
                "evidence": evidence_id,
                "payload": canonical_json(evidence),
                "blob": blob_id,
                "sha": content_hash(evidence),
            },
        )
    before = _snapshot(catalog)
    reads: list[str] = []
    original_read = BlobStore.read

    def read_blob(
        store: BlobStore, owner_id: UUID, storage_key: str, digest: str, size: int
    ) -> bytes:
        reads.append(digest)
        return original_read(store, owner_id, storage_key, digest, size)

    with monkeypatch.context() as patch:
        patch.setattr(BlobStore, "read", read_blob)
        with (
            catalog.runtime.engine.connect().execution_options(
                isolation_level="REPEATABLE READ"
            ) as conn,
            conn.begin(),
        ):
            collected = collect_owner_data(conn, catalog.identity.settings, catalog.owner_id)
            assert reads == []
            encode_archive(collected.manifest, collected.blobs, io.BytesIO())
            assert reads == [stored.sha256]

            def missing_blob(
                store: BlobStore, owner_id: UUID, storage_key: str, digest: str, size: int
            ) -> bytes:
                raise FileNotFoundError

            patch.setattr(BlobStore, "read", missing_blob)
            with pytest.raises(ArchiveError, match="portable_blob_unavailable"):
                encode_archive(collected.manifest, collected.blobs, io.BytesIO())
    archive = _export(catalog)
    with catalog.runtime.transaction() as conn:
        quarantine_records(conn, archive)
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM pg_tables WHERE schemaname LIKE 'pg_temp_%' AND tablename LIKE 'portable_%'"
                )
            ).scalar_one()
            == 0
        )
    assert _snapshot(catalog) == before
    exported_revision = next(
        row
        for row in archive.manifest.records["document_revisions"]
        if row["id"] == document["current_revision"]["id"]
    )
    assert exported_revision["body"] == document["current_revision"]["body"]
    assert exported_revision["content_sha256"] == document["current_revision"]["content_sha256"]
    assert (
        exported_revision["validation_issues"] == document["current_revision"]["validation_issues"]
    )
    exported_evidence = next(
        row
        for row in archive.manifest.records["evidence_revisions"]
        if row["id"] == str(revision_id)
    )
    assert exported_evidence["blob_id"] == str(blob_id)
    assert archive.blobs[stored.sha256] == payload
    with catalog.identity.admin.transaction() as conn:
        session_hash = conn.execute(
            text("SELECT token_sha256 FROM bonafides.sessions WHERE owner_id=:owner"),
            {"owner": catalog.owner_id},
        ).scalar_one()
    assert session_hash not in canonical_json(archive.manifest.model_dump(mode="json"))
    missing_archive = _digest(
        PortableArchive(archive.manifest.model_copy(update={"blob_manifest": []}), {})
    )
    with catalog.runtime.transaction() as conn, pytest.raises(ArchiveError):
        quarantine_records(conn, missing_archive)
    assert _snapshot(catalog) == before


@pytest.mark.postgres
@pytest.mark.parametrize(
    "damage",
    [
        "owner",
        "current_pointer",
        "version_type",
        "version_check",
        "duplicate_identity",
        "content_hash",
        "unknown_column",
        "unknown_schema",
        "snapshot_reference",
        "snapshot_membership",
    ],
)
def test_invalid_archive_rejects_without_changing_live_rows_or_sessions(
    catalog: SourceContext, damage: str
) -> None:
    _base(catalog, _profile(catalog))
    archive = _export(catalog)
    records = archive.manifest.records
    if damage == "owner":
        records["profile_revisions"][0]["owner_id"] = str(uuid4())
    elif damage == "current_pointer":
        records["profiles"][0]["current_revision_id"] = str(uuid4())
    elif damage == "version_type":
        records["profiles"][0]["version"] = "1"
    elif damage == "version_check":
        records["profiles"][0]["version"] = 0
    elif damage == "duplicate_identity":
        records["claim_revisions"].append(deepcopy(records["claim_revisions"][0]))
    elif damage == "content_hash":
        cast(dict[str, object], records["claim_revisions"][0]["payload"])["text"] = (
            "Invented qualification"
        )
    elif damage == "unknown_column":
        records["profiles"][0]["unknown"] = "forbidden"
    elif damage == "unknown_schema":
        archive = PortableArchive(
            archive.manifest.model_copy(update={"source_schema_version": 999}), archive.blobs
        )
    elif damage == "snapshot_reference":
        row = records["profile_revisions"][-1]
        snapshot = cast(dict[str, object], row["snapshot"])
        snapshot["claim_revision_ids"] = [str(uuid4())]
        row["content_sha256"] = content_hash(snapshot)
    elif damage == "snapshot_membership":
        records["profile_revision_claims"].pop()
    archive = _digest(archive)
    before = _snapshot(catalog)
    with catalog.runtime.transaction() as conn:
        with pytest.raises(ArchiveError):
            quarantine_records(conn, archive)
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM pg_tables WHERE schemaname LIKE 'pg_temp_%' AND tablename LIKE 'portable_%'"
                )
            ).scalar_one()
            == 0
        )
    assert _snapshot(catalog) == before


@pytest.mark.postgres
def test_foreign_source_archive_validates_without_granting_authority_to_its_owner(
    catalog: SourceContext,
) -> None:
    _base(catalog, _profile(catalog))
    archive = _export(catalog)
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
    before = _snapshot(catalog)
    with catalog.runtime.transaction() as conn:
        quarantine_records(conn, archive)
        assert (
            conn.execute(
                text("SELECT count(*) FROM bonafides.owners WHERE id=:id"), {"id": source_owner}
            ).scalar_one()
            == 0
        )
    assert _snapshot(catalog) == before


def _historical_model(archive: PortableArchive) -> dict[str, JsonValue]:
    return {
        "source_namespace": str(archive.manifest.source_namespace),
        "source_owner_id": str(archive.manifest.source_owner_id),
        "record": {
            "owner_id": str(archive.manifest.source_owner_id),
            "id": str(uuid4()),
            "workflow_run_id": str(uuid4()),
            "provider_key": "synthetic_historical_observation",
            "model_id": "synthetic-archived-model",
            "model_revision": None,
            "task_kind": "generate_documents",
            "prompt_version": "synthetic-history-v1",
            "input_sha256": "a" * 64,
            "output_sha256": None,
            "parameters": {"temperature": 0},
            "egress_class": "hosted_opt_in",
            "input_tokens": None,
            "output_tokens": None,
            "reserved_tokens": 100,
            "reserved_cost_microunits": 0,
            "actual_cost_microunits": None,
            "status": "unknown_usage",
            "created_at": "2026-10-25T00:00:00+00:00",
            "finished_at": None,
            "usage_status": "unknown",
            "metadata": {"source": "synthetic historical observation, not execution authority"},
        },
    }


def _historical_proposal(
    archive: PortableArchive, model: dict[str, JsonValue]
) -> dict[str, JsonValue]:
    revision = archive.manifest.records["document_revisions"][0]
    body = cast(dict[str, JsonValue], revision["body"])
    section = cast(list[dict[str, JsonValue]], body["sections"])[0]
    return {
        "owner_id": str(archive.manifest.source_owner_id),
        "id": str(uuid4()),
        "document_draft_id": revision["document_draft_id"],
        "target_revision_id": revision["id"],
        "target_content_sha256": revision["content_sha256"],
        "target_document_version": 1,
        "section_key": section["key"],
        "original_section": deepcopy(section),
        "proposed_section": deepcopy(section),
        "validation_issues": [],
        "model_run_id": model["id"],
        "workflow_run_id": model["workflow_run_id"],
        "model_run": deepcopy(model),
        "context_snapshot": {"profile_revision_id": revision["profile_revision_id"]},
        "created_at": "2026-10-25T00:00:00+00:00",
    }


@pytest.mark.postgres
def test_historical_hosted_model_validates_without_restoring_execution_or_consent(
    catalog: SourceContext,
) -> None:
    _base(catalog, _profile(catalog))
    archive = _export(catalog)
    observation = _historical_model(archive)
    model = cast(dict[str, JsonValue], observation["record"])
    archive.manifest.records["model_run_provenance"].append(observation)
    archive.manifest.records["document_regeneration_proposals"].append(
        _historical_proposal(archive, model)
    )
    archive = _digest(archive)
    before = _snapshot(catalog)
    with catalog.runtime.transaction() as conn:
        counts_before = conn.execute(
            text("""SELECT (SELECT count(*) FROM bonafides.model_runs),
            (SELECT count(*) FROM bonafides.hosted_egress_consents)""")
        ).one()
        quarantine_records(conn, archive)
        assert (
            conn.execute(
                text("""SELECT (SELECT count(*) FROM bonafides.model_runs),
            (SELECT count(*) FROM bonafides.hosted_egress_consents)""")
            ).one()
            == counts_before
        )
    assert _snapshot(catalog) == before


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider_key", None),
        ("model_id", None),
        ("workflow_run_id", "not-a-uuid"),
        ("input_sha256", "invalid"),
        ("output_sha256", "invalid"),
        ("parameters", []),
        ("egress_class", "unapproved"),
        ("input_tokens", -1),
        ("output_tokens", True),
        ("reserved_tokens", -1),
        ("reserved_cost_microunits", -1),
        ("actual_cost_microunits", -1),
        ("status", "unrecognized"),
        ("created_at", None),
        ("finished_at", "invalid-date"),
        ("usage_status", "fabricated"),
        ("metadata", []),
    ],
)
def test_invalid_historical_model_is_rejected_without_live_mutation(
    catalog: SourceContext, field: str, value: JsonValue
) -> None:
    _base(catalog, _profile(catalog))
    archive = _export(catalog)
    observation = _historical_model(archive)
    cast(dict[str, JsonValue], observation["record"])[field] = value
    archive.manifest.records["model_run_provenance"].append(observation)
    before = _snapshot(catalog)
    with catalog.runtime.transaction() as conn, pytest.raises(ArchiveError):
        quarantine_records(conn, _digest(archive))
    assert _snapshot(catalog) == before


@pytest.mark.postgres
@pytest.mark.parametrize("damage", ["absent", "wrong_workflow"])
def test_proposal_requires_matching_historical_model(catalog: SourceContext, damage: str) -> None:
    _base(catalog, _profile(catalog))
    archive = _export(catalog)
    observation = _historical_model(archive)
    model = cast(dict[str, JsonValue], observation["record"])
    proposal = _historical_proposal(archive, model)
    if damage == "wrong_workflow":
        archive.manifest.records["model_run_provenance"].append(observation)
        proposal["workflow_run_id"] = str(uuid4())
    archive.manifest.records["document_regeneration_proposals"].append(proposal)
    before = _snapshot(catalog)
    expected = (
        "portable_model_reference_missing"
        if damage == "absent"
        else "portable_model_reference_mismatch"
    )
    with catalog.runtime.transaction() as conn, pytest.raises(ArchiveError, match=expected):
        quarantine_records(conn, _digest(archive))
    assert _snapshot(catalog) == before
