# SPDX-License-Identifier: MIT OR Apache-2.0
"""Real-quota portable roundtrip; synthetic files, no model or board access.

The checkpoint/producer setup follows test_portable_import: it exercises runtime
approval, fencing, erasure, import and filesystem publication, not physical backup
or owner-facing replacement dispatch. Fixtures use only the disposable test DB.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from test_action_approvals import Prepared, _approve, _owner
from test_document_generation import _base
from test_identity import identity as identity
from test_import_parser import parser as parser
from test_installation_control import renderer_spool as renderer_spool
from test_portable_erasure import _checkpoint
from test_sources import SourceContext
from test_sources import catalog as catalog
from test_suitability import _profile
from test_tracking import _application

from bonafides.approvals import consume_action_approval, prepare_action_approval
from bonafides.commands import canonical_json
from bonafides.import_parser import ImportParserClient
from bonafides.installation_control import InstallationMaintenance, begin_control
from bonafides.portable_archive import Manifest, decode_archive
from bonafides.portable_data import collect_owner_data, export_owner_data
from bonafides.portable_import import import_controlled_owner_data
from bonafides.profiles import content_hash
from bonafides.storage import BlobStore, StoredBlob
from bonafides.workflows import claim_control

_MIB = 1024 * 1024
_ATTACHMENT_BYTES = 1024 * _MIB
_IMPORT_BYTES = 64 * _MIB
_PDF_BYTES = 64 * _MIB
# Metadata only: never retain a collection of file payloads in memory.
BlobInventory = dict[UUID, StoredBlob]


def _blob(
    context: SourceContext, inventory: BlobInventory, category: str, index: int, size: int
) -> UUID:
    prefix = f"Synthetic portable {category} {index:06d}\n".encode()
    is_pdf = category in {"pdf", "accepted-original"}
    if is_pdf:
        # Literal text plus comment padding: originals remain small after extraction.
        stream = b"BT /F1 10 Tf 72 720 Td (" + prefix.rstrip(b"\n") + b") Tj ET\n"
        objects = [
            b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
            b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
            b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n",
            b"4 0 obj\n<< /Length "
            + str(len(stream)).encode()
            + b" >>\nstream\n"
            + stream
            + b"endstream\nendobj\n",
            b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
        ]
        prefix = b"%PDF-1.4\n%" + prefix
        offsets = []
        for item in objects:
            offsets.append(len(prefix))
            prefix += item
        xref = b"xref\n0 6\n0000000000 65535 f \n" + b"".join(
            f"{offset:010d} 00000 n \n".encode() for offset in offsets
        )
        trailer = b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n"
        # Decimal width is stable at the real 4MiB/10MiB/64MiB fixture boundaries.
        xref_offset = size - len(xref) - len(trailer) - len(str(size)) - len(b"\n%%EOF\n")
        suffix = xref + trailer + str(xref_offset).encode() + b"\n%%EOF\n"
        padding = size - len(prefix) - len(suffix)
        payload = prefix + b"%" + b"x" * (padding - 2) + b"\n" + suffix
    else:
        payload = prefix + b"x" * (size - len(prefix))
    store = BlobStore(context.identity.settings.blob_root)
    stored = (
        store.put(context.owner_id, payload)
        if is_pdf
        else store.put_bytes(context.owner_id, payload)
    )
    identifier = uuid4()
    with context.runtime.transaction() as conn:
        conn.execute(
            text("""INSERT INTO bonafides.blobs
            (owner_id,id,sha256,byte_length,media_type,storage_key)
            VALUES(:owner,:id,:sha,:size,:media,:key)"""),
            {
                "owner": context.owner_id,
                "id": identifier,
                "sha": stored.sha256,
                "size": stored.byte_length,
                "media": "application/pdf" if is_pdf else "text/plain",
                "key": stored.storage_key,
            },
        )
    inventory[identifier] = stored
    return identifier


def _attachments(context: SourceContext, inventory: BlobInventory, total: int) -> None:
    remaining, index = total, 0
    while remaining:
        application = _application(
            context, canonical_url=f"https://careers.fictional-employer.com/portable-quota/{index}"
        )
        application_remaining = min(100 * _MIB, remaining)
        while application_remaining:
            size = min(10 * _MIB, application_remaining)
            blob_id = _blob(context, inventory, "attachment", index, size)
            with context.runtime.transaction() as conn:
                conn.execute(
                    text("""INSERT INTO bonafides.application_attachments
                    (owner_id,application_id,blob_id,display_name)
                    VALUES(:owner,:application,:blob,:name)"""),
                    {
                        "owner": context.owner_id,
                        "application": UUID(application["id"]),
                        "blob": blob_id,
                        "name": f"synthetic-{index}.txt",
                    },
                )
            remaining -= size
            application_remaining -= size
            index += 1


def _accepted_originals(context: SourceContext, inventory: BlobInventory) -> None:
    remaining, index = _IMPORT_BYTES, 0
    while remaining:
        size = min(10 * _MIB, remaining)
        blob_id = _blob(context, inventory, "accepted-original", index, size)
        workflow_id, import_id, evidence_id, revision_id = (uuid4() for _ in range(4))
        proposal_id = uuid4()
        quote = f"Synthetic portable accepted-original {index:06d}"
        source_sha = hashlib.sha256(quote.encode()).hexdigest()
        proposal = {
            "proposal_id": str(proposal_id),
            "kind": "experience",
            "payload": {"text": quote},
            "source_start": 0,
            "source_end": len(quote),
            "quote": quote,
        }
        review = {
            "mode": "merge",
            "replace_all_confirmed": False,
            "decisions": [
                {
                    "proposal_id": str(proposal_id),
                    "action": "add",
                    "claim_id": None,
                    "kind": "experience",
                    "payload": {"text": quote},
                }
            ],
        }
        evidence = {
            "text": quote,
            "source": "Owner-reviewed local literal import",
            "import_id": str(import_id),
            "proposal_id": str(proposal_id),
            "source_sha256": source_sha,
            "source_start": 0,
            "source_end": len(quote),
            "extraction": "deterministic_literal",
            "assertion": "owner_asserted_not_independently_verified",
        }
        with context.runtime.transaction() as conn:
            conn.execute(
                text("""INSERT INTO bonafides.workflow_runs
                (owner_id,id,kind,state,dedupe_key,request_sha256,deadline_at,finished_at)
                VALUES(:owner,:id,'profile_import','succeeded',:dedupe,:sha,
                clock_timestamp()+interval '5 minutes',clock_timestamp())"""),
                {
                    "owner": context.owner_id,
                    "id": workflow_id,
                    "dedupe": str(workflow_id),
                    "sha": inventory[blob_id].sha256,
                },
            )
            conn.execute(
                text("""INSERT INTO bonafides.import_batches
                (owner_id,id,workflow_run_id,blob_id,media_type,byte_size,profile_version,
                parsed_text,source_sha256,proposals,review,review_expires_at,reviewed_at,
                merged_profile_revision_id)
                SELECT :owner,:id,:workflow,:blob,'application/pdf',:size,version,:source,:sha,
                CAST(:proposals AS jsonb),CAST(:review AS jsonb),clock_timestamp()+interval '1 day',
                clock_timestamp(),current_revision_id FROM bonafides.profiles WHERE owner_id=:owner"""),
                {
                    "owner": context.owner_id,
                    "id": import_id,
                    "workflow": workflow_id,
                    "blob": blob_id,
                    "size": size,
                    "source": quote,
                    "sha": source_sha,
                    "proposals": canonical_json([proposal]),
                    "review": canonical_json(review),
                },
            )
            conn.execute(
                text("""INSERT INTO bonafides.evidence_records(owner_id,id,current_revision_id)
                VALUES(:owner,:id,:revision)"""),
                {"owner": context.owner_id, "id": evidence_id, "revision": revision_id},
            )
            conn.execute(
                text("""INSERT INTO bonafides.evidence_revisions
                (owner_id,id,evidence_id,revision_no,payload,blob_id,content_sha256)
                VALUES(:owner,:id,:evidence,1,CAST(:payload AS jsonb),:blob,:sha)"""),
                {
                    "owner": context.owner_id,
                    "id": revision_id,
                    "evidence": evidence_id,
                    "payload": canonical_json(evidence),
                    "blob": blob_id,
                    "sha": content_hash(evidence),
                },
            )
        remaining -= size
        index += 1


def _approved_import(context: SourceContext, digest: str) -> Prepared:
    identifier = uuid4()
    snapshot = {"synthetic_conformance": "real-quota checkpointed import target"}
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
        intent: dict[str, object] = {"preview_sha256": "2" * 64, "replacement_sha256": digest}
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
    with context.identity.admin.transaction() as conn:
        conn.execute(
            text("""UPDATE bonafides.workflow_runs SET state='pending',version=version+1,
            last_error_code=NULL WHERE owner_id=:owner AND id=:id"""),
            {"owner": context.owner_id, "id": identifier},
        )
    return prepared


def _assert_manifest(manifest: Manifest, inventory: BlobInventory) -> None:
    assert manifest.format_version == 2
    assert {
        member.blob_id: (member.sha256, member.byte_length) for member in manifest.blob_manifest
    } == {
        identifier: (stored.sha256, stored.byte_length) for identifier, stored in inventory.items()
    }
    assert len({stored.sha256 for stored in inventory.values()}) == len(inventory)


@pytest.mark.postgres
def test_export_above_legacy_archive_limit(catalog: SourceContext, tmp_path: Path) -> None:
    inventory: BlobInventory = {}
    _attachments(catalog, inventory, 120 * _MIB)
    with (tmp_path / "above-legacy-limit.zip").open("w+b") as output:
        manifest = export_owner_data(
            catalog.runtime, catalog.identity.settings, catalog.owner_id, output
        )
        _assert_manifest(manifest, inventory)
        assert output.tell() > 112 * _MIB
        output.seek(0)
        decoded = decode_archive(output, expected_source_owner=catalog.owner_id)
        assert decoded.manifest == manifest
        for member in manifest.blob_manifest:
            assert hashlib.sha256(decoded.blobs[member.sha256]).hexdigest() == member.sha256


@pytest.mark.postgres
def test_real_owner_quotas_roundtrip_streamed_archive(
    catalog: SourceContext, parser: ImportParserClient, renderer_spool: Path, tmp_path: Path
) -> None:
    document = _base(catalog, _profile(catalog))
    inventory: BlobInventory = {}
    _attachments(catalog, inventory, _ATTACHMENT_BYTES)
    _accepted_originals(catalog, inventory)
    pdf_id = _blob(catalog, inventory, "pdf", 0, _PDF_BYTES)
    with catalog.runtime.transaction() as conn:
        conn.execute(
            text("""INSERT INTO bonafides.document_artifacts
            (owner_id,document_revision_id,blob_id,renderer_version,format)
            VALUES(:owner,:revision,:blob,'synthetic-quota-pdf-v1','pdf')"""),
            {
                "owner": catalog.owner_id,
                "revision": UUID(document["current_revision"]["id"]),
                "blob": pdf_id,
            },
        )
        totals = conn.execute(
            text("""SELECT sum(b.byte_length),max(b.byte_length),count(*)
            FROM bonafides.application_attachments a JOIN bonafides.blobs b
            ON b.owner_id=a.owner_id AND b.id=a.blob_id WHERE a.owner_id=:owner"""),
            {"owner": catalog.owner_id},
        ).one()
        assert tuple(totals) == (_ATTACHMENT_BYTES, 10 * _MIB, 103)
        per_application = conn.execute(
            text("""SELECT sum(b.byte_length),count(*) FROM bonafides.application_attachments a
            JOIN bonafides.blobs b ON b.owner_id=a.owner_id AND b.id=a.blob_id
            WHERE a.owner_id=:owner GROUP BY a.application_id"""),
            {"owner": catalog.owner_id},
        ).all()
        assert len(per_application) == 11
        assert sorted(int(row[0]) for row in per_application) == [24 * _MIB] + [100 * _MIB] * 10
        assert all(row[1] <= 100 for row in per_application)
        assert tuple(
            conn.execute(
                text("""SELECT sum(byte_size),max(byte_size),count(*) FROM bonafides.import_batches
            WHERE owner_id=:owner"""),
                {"owner": catalog.owner_id},
            ).one()
        ) == (_IMPORT_BYTES, 10 * _MIB, 7)
    assert len(inventory) == 111
    assert sum(stored.byte_length for stored in inventory.values()) == 1152 * _MIB
    archive_path = tmp_path / "real-owner-quotas.zip"
    with archive_path.open("w+b") as output:
        manifest = export_owner_data(
            catalog.runtime, catalog.identity.settings, catalog.owner_id, output
        )
        _assert_manifest(manifest, inventory)
        assert archive_path.stat().st_size > 1152 * _MIB
        output.seek(0)
        digest = hashlib.file_digest(output, "sha256").hexdigest()
    checkpoint_id, runtime_pins = _checkpoint(catalog, parser.spool, renderer_spool)
    prepared = _approved_import(catalog, digest)
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
    maintenance = InstallationMaintenance(catalog.runtime, control, parser.spool, renderer_spool)
    with maintenance.quiescent():
        with archive_path.open("rb") as source:
            result = import_controlled_owner_data(
                catalog.runtime,
                catalog.identity.settings,
                control,
                source,
                expected_source_owner=catalog.owner_id,
                checkpoint_id=checkpoint_id,
                runtime_pins=runtime_pins,
            )
            assert not source.closed
        maintenance.finish(result)
    with (
        catalog.runtime.engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as conn,
        conn.begin(),
    ):
        restored = collect_owner_data(conn, catalog.identity.settings, catalog.owner_id)
        _assert_manifest(restored.manifest, inventory)
        # Compare native relationships and revision/content hashes, not just row counts.
        for family in (
            "profiles",
            "profile_revisions",
            "professional_claims",
            "claim_revisions",
            "evidence_records",
            "evidence_revisions",
            "claim_evidence_links",
            "profile_revision_claims",
            "profile_revision_evidence",
            "saved_opportunities",
            "applications",
            "application_stage_events",
            "application_attachments",
            "document_drafts",
            "document_revisions",
            "document_claim_links",
            "document_artifacts",
        ):
            assert restored.manifest.records[family] == manifest.records[family], family
        assert (
            conn.execute(
                text("SELECT count(*) FROM bonafides.import_batches WHERE owner_id=:owner"),
                {"owner": catalog.owner_id},
            ).scalar_one()
            == 0
        )  # Workflow state is excluded; accepted original evidence is retained.
        actual = conn.execute(
            text(
                "SELECT id,sha256,byte_length,storage_key FROM bonafides.blobs WHERE owner_id=:owner"
            ),
            {"owner": catalog.owner_id},
        ).all()
        assert {row[0]: (row[1], row[2], row[3]) for row in actual} == {
            identifier: (stored.sha256, stored.byte_length, stored.storage_key)
            for identifier, stored in inventory.items()
        }
    owner_root = catalog.identity.settings.blob_root / str(catalog.owner_id)
    actual_files = {
        path.relative_to(owner_root).as_posix() for path in owner_root.rglob("*") if path.is_file()
    }
    assert actual_files == {stored.storage_key for stored in inventory.values()}
    verified_bytes = 0
    for stored in inventory.values():
        path = owner_root / stored.storage_key
        assert path.stat().st_size == stored.byte_length
        with path.open("rb") as source:
            assert hashlib.file_digest(source, "sha256").hexdigest() == stored.sha256
        verified_bytes += stored.byte_length
    assert verified_bytes == 1152 * _MIB
