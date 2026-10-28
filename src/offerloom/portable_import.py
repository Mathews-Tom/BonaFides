# SPDX-License-Identifier: MIT OR Apache-2.0
"""Validate and stage portable bytes before one atomic, checkpointed cutover."""

from __future__ import annotations

import hashlib
import io
from typing import BinaryIO, cast
from uuid import UUID, uuid4

from pydantic import JsonValue
from sqlalchemy import Connection, text

from offerloom.commands import canonical_json
from offerloom.config import Settings
from offerloom.db import Database
from offerloom.installation_control import Control, assert_control
from offerloom.portable_archive import (
    MAX_ARCHIVE_BYTES,
    ArchiveError,
    PortableArchive,
    decode_archive,
    hash_stream,
)
from offerloom.portable_columns import COLUMNS
from offerloom.portable_data import quarantine_records
from offerloom.portable_erasure import assert_checkpoint, erase_scope, orphan_inventory, purge_scope
from offerloom.storage import BlobStore

_HISTORY_RELATIONS = frozenset(
    {"match_assessments", "document_revisions", "document_regeneration_proposals"}
)


def _authorize(conn: Connection, control: Control, digest: str) -> None:
    assert_control(conn, control)
    scope = conn.execute(
        text("""SELECT scope FROM offerloom.installation_controls
        WHERE id=:id AND holder=:holder AND fence=:fence AND owner_id=:owner
        AND namespace_id=:namespace AND maintenance_epoch=:epoch
        AND purpose='portable_import' AND phase='quiescent'"""),
        {
            **control.parameters,
            "owner": control.owner_id,
            "namespace": control.namespace_id,
            "epoch": control.maintenance_epoch,
        },
    ).scalar_one_or_none()
    if scope is None or scope.get("payload", {}).get("replacement_sha256") != digest:
        raise ArchiveError("portable_import_scope_mismatch")


def _empty_target(conn: Connection, owner: UUID) -> None:
    names = [name for name in COLUMNS if name != "model_run_provenance"]
    names.extend(("portable_model_history", "blobs"))
    occupied = conn.execute(
        text(
            "SELECT "
            + " OR ".join(
                f"EXISTS(SELECT 1 FROM offerloom.{name} WHERE owner_id=:owner)" for name in names
            )
        ),
        {"owner": owner},
    ).scalar_one()
    if occupied:
        raise ArchiveError("portable_import_target_not_empty")


def _persist_records(conn: Connection, owner: UUID, archive: PortableArchive) -> None:
    conn.execute(text("SET CONSTRAINTS ALL DEFERRED"))
    for item in archive.manifest.records["model_run_provenance"]:
        record = cast(dict[str, JsonValue], item["record"])
        encoded = canonical_json(record)
        conn.execute(
            text("""INSERT INTO offerloom.portable_model_history
            (owner_id,id,source_namespace,source_owner_id,record,record_sha256)
            VALUES(:owner,:id,:namespace,:source_owner,CAST(:record AS jsonb),:sha)"""),
            {
                "owner": owner,
                "id": UUID(str(record["id"])),
                "namespace": UUID(str(item["source_namespace"])),
                "source_owner": UUID(str(item["source_owner_id"])),
                "record": encoded,
                "sha": hashlib.sha256(encoded.encode()).hexdigest(),
            },
        )
    for blob in archive.manifest.blob_manifest:
        conn.execute(
            text("""INSERT INTO offerloom.blobs
            (owner_id,id,sha256,byte_length,media_type,storage_key)
            VALUES(:owner,:id,:sha,:length,:media,:key)"""),
            {
                "owner": owner,
                "id": blob.blob_id,
                "sha": blob.sha256,
                "length": blob.byte_length,
                "media": blob.media_type,
                "key": f"{blob.sha256[:2]}/{blob.sha256}",
            },
        )
    for name, columns in COLUMNS.items():
        if name == "model_run_provenance":
            continue
        rows: list[dict[str, JsonValue]] = []
        for original in archive.manifest.records[name]:
            row = {**original, "owner_id": str(owner)}
            if "acknowledged_by_owner_id" in row:
                row["acknowledged_by_owner_id"] = str(owner)
            if name == "source_configs":
                # Imported terms history is not a new board-read authorization.
                row["enabled"] = False
                row["current_policy_review_id"] = None
            if name in _HISTORY_RELATIONS:
                row["historical_model_run_id"] = row["model_run_id"]
                row["model_run_id"] = None
                if name == "document_regeneration_proposals":
                    row["workflow_run_id"] = None
            rows.append(row)
        if not rows:
            continue
        fields = list(columns)
        if name in _HISTORY_RELATIONS:
            fields.append("historical_model_run_id")
        selected = ",".join(fields)
        # One bounded statement per family, not one round trip per domain row.
        conn.execute(
            text(f"""INSERT INTO offerloom.{name} ({selected})
            SELECT {selected} FROM jsonb_populate_recordset(NULL::offerloom.{name},CAST(:rows AS jsonb))"""),
            {"rows": canonical_json(rows)},
        )
    conn.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))


def import_controlled_owner_data(
    db: Database,
    settings: Settings,
    control: Control,
    payload: bytes | BinaryIO,
    *,
    expected_source_owner: UUID,
    checkpoint_id: UUID,
    runtime_pins: dict[str, str],
) -> dict[str, object]:
    """Revalidate exact approved bytes; commit all native relationships atomically.

    No live row changes before full archive/quarantine validation. Blob I/O does
    not hold the controller row needed by its independent heartbeat. A lost lease
    leaves only immutable orphan bytes, never a published partial replacement.
    """
    source = io.BytesIO(payload) if isinstance(payload, bytes) else payload
    source.seek(0)
    digest, _ = hash_stream(source, limit=MAX_ARCHIVE_BYTES)
    source.seek(0)
    with db.transaction() as conn:
        _authorize(conn, control, digest)
        assert_checkpoint(conn, control, checkpoint_id, runtime_pins)
    with decode_archive(source, expected_source_owner=expected_source_owner) as archive:
        with db.transaction() as conn:
            quarantine_records(conn, archive)
        inventory = orphan_inventory(db, settings, control)
        store = BlobStore(settings.blob_root)
        for member in archive.manifest.blob_manifest:
            with db.transaction() as conn:
                _authorize(conn, control, digest)
            staging_id = uuid4()
            try:
                store.read(
                    control.owner_id,
                    f"{member.sha256[:2]}/{member.sha256}",
                    member.sha256,
                    member.byte_length,
                )
            except FileNotFoundError:
                # Commit ownership BEFORE the first byte. Offline abort may remove
                # only this operation's fresh CAS object / exact named partial write.
                with db.transaction() as conn:
                    conn.execute(
                        text("""SELECT offerloom.register_portable_staging(
                        :id,:holder,:fence,:sha,:length,:staging)"""),
                        {
                            **control.parameters,
                            "sha": member.sha256,
                            "length": member.byte_length,
                            "staging": f".staging-{staging_id.hex}",
                        },
                    )
            stored = store.put_bytes(
                control.owner_id, archive.blobs[member.sha256], staging_id=staging_id
            )
            if stored.sha256 != member.sha256 or stored.byte_length != member.byte_length:
                raise ArchiveError("portable_import_blob_mismatch")
        with db.transaction() as conn:
            _authorize(conn, control, digest)
            erase_scope(
                conn, control, inventory, checkpoint_id=checkpoint_id, runtime_pins=runtime_pins
            )
            _empty_target(conn, control.owner_id)
            _persist_records(conn, control.owner_id, archive)
            # The final guard also refuses a lease that elapsed during a large insert.
            _authorize(conn, control, digest)
        purge_scope(db, settings, control)
        return {
            "archive_sha256": digest,
            "manifest_sha256": archive.manifest.manifest_sha256,
            "source_namespace": str(archive.manifest.source_namespace),
            "source_owner_id": str(archive.manifest.source_owner_id),
            "record_count": sum(len(rows) for rows in archive.manifest.records.values()),
            "blob_count": len(archive.manifest.blob_manifest),
            "connectors_enabled": False,
            "filesystem_complete": True,
        }
