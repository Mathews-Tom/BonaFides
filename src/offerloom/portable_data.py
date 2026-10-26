# SPDX-License-Identifier: MIT OR Apache-2.0
"""Explicit owner-data release and private, non-live relational validation."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from pydantic import JsonValue, ValidationError
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from offerloom.commands import canonical_json, lock_installation
from offerloom.config import Settings
from offerloom.db import Database
from offerloom.document_generation import revision_digest
from offerloom.documents import parse_body
from offerloom.installation_control import Control
from offerloom.portable_archive import (
    EXCLUSIONS,
    MAX_ARCHIVE_BYTES,
    MAX_MANIFEST_BYTES,
    MAX_RECORDS,
    ArchiveError,
    BlobMember,
    Manifest,
    PortableArchive,
    encode_archive,
    manifest_digest,
)
from offerloom.portable_columns import COLUMNS
from offerloom.profiles import ProfileSnapshot, content_hash
from offerloom.storage import BlobStore

JsonRow = dict[str, JsonValue]
_BLOB_REFERENCES = ("evidence_revisions", "application_attachments", "document_artifacts")
_SOURCE_SCHEMAS = frozenset({13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26})


def _relation(name: str) -> str:
    if name not in COLUMNS:
        raise ArchiveError("unknown_portable_record_type")
    return "model_runs" if name == "model_run_provenance" else name


def collect_owner_data(conn: Connection, settings: Settings, owner_id: UUID) -> PortableArchive:
    """Caller owns a repeatable-read transaction; hold deletion's barrier throughout."""
    if conn.execute(text("SHOW transaction_isolation")).scalar_one() != "repeatable read":
        raise ValueError("portable_export_requires_repeatable_read")
    lock_installation(conn)
    return _collect_owner_data(conn, settings, owner_id)


def collect_controlled_owner_data(
    conn: Connection, settings: Settings, control: Control
) -> PortableArchive:
    """The dedicated coordinator, not an ordinary worker, owns this snapshot."""
    if conn.execute(text("SHOW transaction_isolation")).scalar_one() != "repeatable read":
        raise ValueError("portable_export_requires_repeatable_read")
    # Do not hold the control-row UPDATE lock during blob I/O: the independent
    # heartbeat needs that row. Publication rechecks current authority afterward.
    valid = conn.execute(
        text("""SELECT EXISTS(SELECT 1 FROM offerloom.installation_controls c
        JOIN offerloom.installation_state i ON i.singleton
        JOIN offerloom.workflow_runs w ON w.owner_id=c.owner_id AND w.id=c.id
        WHERE c.id=:id AND c.holder=:holder AND c.fence=:fence
        AND c.owner_id=:owner AND c.namespace_id=:namespace AND c.maintenance_epoch=:epoch
        AND c.phase='quiescent' AND c.lease_expires_at>clock_timestamp()
        AND c.deadline_at>clock_timestamp() AND i.maintenance_active
        AND i.namespace_id=c.namespace_id AND i.maintenance_epoch=c.maintenance_epoch
        AND w.state='running' AND w.fence=c.workflow_fence
        AND w.lease_holder=c.holder AND w.cancel_requested_at IS NULL)"""),
        {
            **control.parameters,
            "owner": control.owner_id,
            "namespace": control.namespace_id,
            "epoch": control.maintenance_epoch,
        },
    ).scalar_one()
    if not valid:
        raise ArchiveError("stale_portable_snapshot_control")
    return _collect_owner_data(conn, settings, control.owner_id)


def _collect_owner_data(conn: Connection, settings: Settings, owner_id: UUID) -> PortableArchive:
    namespace = conn.execute(
        text("SELECT namespace_id FROM offerloom.installation_state WHERE singleton")
    ).scalar_one()
    schema = conn.execute(text("SELECT max(version) FROM offerloom.schema_migrations")).scalar_one()
    if schema not in _SOURCE_SCHEMAS:
        raise ArchiveError("incompatible_portable_schema")
    profile = conn.execute(
        text("SELECT current_revision_id FROM offerloom.profiles WHERE owner_id=:owner"),
        {"owner": owner_id},
    ).scalar_one()
    records: dict[str, list[JsonRow]] = {}
    count, remaining = 0, MAX_MANIFEST_BYTES
    for name, columns in COLUMNS.items():
        relation = _relation(name)
        fields = ",".join(columns)
        if schema >= 18 and name in {
            "match_assessments",
            "document_revisions",
            "document_regeneration_proposals",
        }:
            fields = ",".join(
                "COALESCE(model_run_id,historical_model_run_id) AS model_run_id"
                if column == "model_run_id"
                else (
                    "COALESCE(workflow_run_id,(SELECT (h.record->>'workflow_run_id')::uuid "
                    "FROM offerloom.portable_model_history h WHERE h.owner_id=:owner "
                    "AND h.id=historical_model_run_id)) AS workflow_run_id"
                    if name == "document_regeneration_proposals" and column == "workflow_run_id"
                    else column
                )
                for column in columns
            )
        order = ",".join(column for column in columns if column == "id" or column.endswith("_id"))
        query = text(f"""SELECT CASE WHEN octet_length(to_jsonb(r)::text)<=:maximum
            THEN to_jsonb(r) ELSE NULL END FROM (
            SELECT {fields} FROM offerloom.{relation} WHERE owner_id=:owner
            ORDER BY {order} LIMIT :limit) r""")
        rows: list[JsonRow] = []
        result = conn.execute(
            query.execution_options(stream_results=True),
            {"owner": owner_id, "limit": MAX_RECORDS - count + 1, "maximum": remaining},
        )
        try:
            for (value,) in result:
                if value is None:
                    raise ArchiveError("manifest_size_limit")
                row = cast(JsonRow, value)
                count += 1
                if count > MAX_RECORDS:
                    raise ArchiveError("portable_record_limit")
                if name == "model_run_provenance":
                    row = {
                        "source_namespace": str(namespace),
                        "source_owner_id": str(owner_id),
                        "record": row,
                    }
                remaining -= len(canonical_json(row).encode())
                if remaining < 0:
                    raise ArchiveError("manifest_size_limit")
                rows.append(row)
        finally:
            result.close()
        records[name] = rows
    if schema >= 18:
        result = conn.execute(
            text("""SELECT CASE WHEN octet_length(record::text)<=:maximum
            THEN jsonb_build_object('source_namespace',source_namespace,
             'source_owner_id',source_owner_id,'record',record) ELSE NULL END,record_sha256
            FROM offerloom.portable_model_history WHERE owner_id=:owner
            ORDER BY id LIMIT :limit""").execution_options(stream_results=True),
            {"owner": owner_id, "maximum": remaining, "limit": MAX_RECORDS - count + 1},
        )
        try:
            for value, digest in result:
                if value is None:
                    raise ArchiveError("manifest_size_limit")
                historical = cast(JsonRow, value)
                if (
                    hashlib.sha256(canonical_json(historical["record"]).encode()).hexdigest()
                    != digest
                ):
                    raise ArchiveError("portable_model_history_hash_mismatch")
                count += 1
                remaining -= len(canonical_json(historical).encode())
                if count > MAX_RECORDS:
                    raise ArchiveError("portable_record_limit")
                if remaining < 0:
                    raise ArchiveError("manifest_size_limit")
                records["model_run_provenance"].append(historical)
        finally:
            result.close()
    blob_ids = {
        cast(str, row["blob_id"])
        for name in _BLOB_REFERENCES
        for row in records[name]
        if row["blob_id"] is not None
    }
    blobs: dict[str, bytes] = {}
    members: list[BlobMember] = []
    store = BlobStore(settings.blob_root)
    total = 0
    for identifier in sorted(blob_ids):
        blob_row = (
            conn.execute(
                text("""SELECT id,sha256,byte_length,media_type,storage_key,state FROM offerloom.blobs
                WHERE owner_id=:owner AND id=:id"""),
                {"owner": owner_id, "id": UUID(identifier)},
            )
            .mappings()
            .one()
        )
        if blob_row["state"] != "ready":
            raise ArchiveError("portable_blob_unavailable")
        payload = store.read(
            owner_id, blob_row["storage_key"], blob_row["sha256"], blob_row["byte_length"]
        )
        total += len(payload)
        # Leave room for the manifest and ZIP headers; encode checks exact size.
        if total + MAX_MANIFEST_BYTES > MAX_ARCHIVE_BYTES:
            raise ArchiveError("archive_size_limit")
        members.append(
            BlobMember(
                blob_id=blob_row["id"],
                sha256=blob_row["sha256"],
                byte_length=blob_row["byte_length"],
                media_type=blob_row["media_type"],
                archive_member=f"blobs/{blob_row['sha256']}",
            )
        )
        blobs[blob_row["sha256"]] = payload
    manifest = Manifest(
        format="offerloom-portable",
        format_version=1,
        source_schema_version=schema,
        created_at=datetime.now(UTC),
        source_namespace=namespace,
        source_owner_id=owner_id,
        current_profile_revision_id=profile,
        manifest_sha256="0" * 64,
        records=records,
        blob_manifest=members,
        exclusions=list(EXCLUSIONS),
    )
    manifest = manifest.model_copy(update={"manifest_sha256": manifest_digest(manifest)})
    return PortableArchive(manifest, blobs)


def export_owner_data(db: Database, settings: Settings, owner_id: UUID) -> bytes:
    with (
        db.engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn,
        conn.begin(),
    ):
        archive = collect_owner_data(conn, settings, owner_id)
        return encode_archive(archive.manifest, archive.blobs)


def _native_types(conn: Connection, relation: str) -> dict[str, str]:
    rows = conn.execute(
        text("""SELECT a.attname,t.typname FROM pg_catalog.pg_attribute a
        JOIN pg_catalog.pg_type t ON t.oid=a.atttypid
        WHERE a.attrelid=to_regclass(:relation) AND a.attnum>0 AND NOT a.attisdropped"""),
        {"relation": f"offerloom.{relation}"},
    )
    return {row[0]: row[1] for row in rows}


def _row_shape(row: JsonRow, columns: tuple[str, ...], types: dict[str, str]) -> None:
    if set(row) != set(columns):
        raise ArchiveError("incompatible_portable_columns")
    for name, value in row.items():
        if value is None:
            continue
        kind = types[name]
        valid = (
            (kind in {"int2", "int4", "int8"} and type(value) is int)
            or (kind == "bool" and type(value) is bool)
            or (kind == "jsonb")
            or (kind.startswith("_") and isinstance(value, list))
            or (
                kind not in {"int2", "int4", "int8", "bool", "jsonb"}
                and not kind.startswith("_")
                and isinstance(value, str)
            )
        )
        if not valid:
            raise ArchiveError("invalid_portable_field_type")


def _semantic_hashes(archive: PortableArchive) -> None:
    records = archive.manifest.records
    for name, field in (
        ("profile_revisions", "snapshot"),
        ("claim_revisions", "payload"),
        ("evidence_revisions", "payload"),
    ):
        for row in records[name]:
            if content_hash(row[field]) != row["content_sha256"]:
                raise ArchiveError("portable_content_hash_mismatch")
    claims = {UUID(cast(str, row["id"])) for row in records["claim_revisions"]}
    evidence = {UUID(cast(str, row["id"])) for row in records["evidence_revisions"]}
    for row in records["profile_revisions"]:
        try:
            snapshot = ProfileSnapshot.model_validate_json(
                canonical_json(row["snapshot"]), strict=True
            )
        except ValidationError as exc:
            raise ArchiveError("invalid_portable_profile") from exc
        if (
            not set(snapshot.claim_revision_ids) <= claims
            or not set(snapshot.evidence_revision_ids) <= evidence
        ):
            raise ArchiveError("portable_snapshot_reference_missing")
        selected = {
            UUID(cast(str, link["claim_revision_id"]))
            for link in records["profile_revision_claims"]
            if link["profile_revision_id"] == row["id"]
        }
        if selected != set(snapshot.claim_revision_ids):
            raise ArchiveError("portable_snapshot_membership_mismatch")
    for row in records["document_revisions"]:
        try:
            body = parse_body(row["body"])
            for section in body.sections:
                for block in section.blocks:
                    if (
                        not set(block.claim_revision_ids) <= claims
                        or not set(block.evidence_revision_ids) <= evidence
                    ):
                        raise ArchiveError("portable_snapshot_reference_missing")
            provenance = cast(dict[str, object], row["provenance_snapshot"])
            digest = revision_digest(body, cast(str, row["template_version"]), provenance)
        except ArchiveError:
            raise
        except (ValidationError, ValueError, KeyError, TypeError) as exc:
            raise ArchiveError("invalid_portable_document") from exc
        if digest != row["content_sha256"]:
            raise ArchiveError("portable_content_hash_mismatch")
    documents = {cast(str, row["id"]): row for row in records["document_revisions"]}
    for review in records["document_reviews"]:
        document = documents.get(cast(str, review["document_revision_id"]))
        if document is None or review["reviewed_content_sha256"] != document["content_sha256"]:
            raise ArchiveError("portable_review_hash_mismatch")
    profiles = records["profiles"]
    if len(profiles) != 1 or profiles[0]["current_revision_id"] != str(
        archive.manifest.current_profile_revision_id
    ):
        raise ArchiveError("invalid_portable_current_profile")


def quarantine_records(conn: Connection, archive: PortableArchive) -> None:
    """Check actual SQL types, checks, unique keys and complete FK closure in temp tables.

    No live domain row is inserted, changed or deleted. Temporary clones have no
    triggers or execution authority. Every clone and constraint is dropped by the
    savepoint rollback, including on failure. Runtime needs TEMPORARY, not DDL in
    offerloom. Model provenance is historical data, never a fabricated ModelRun.
    """
    manifest = archive.manifest
    if manifest.source_schema_version not in _SOURCE_SCHEMAS or set(manifest.records) != set(
        COLUMNS
    ):
        raise ArchiveError("incompatible_portable_schema")
    source = str(manifest.source_owner_id)
    prefix = "portable_" + uuid4().hex[:12] + "_"
    tables = {name: prefix + name for name in COLUMNS if name != "model_run_provenance"}
    tables["blobs"] = prefix + "blobs"
    models: dict[str, JsonRow] = {}
    try:
        with conn.begin_nested() as transaction:
            model_table = prefix + "model_provenance"
            conn.execute(
                text(
                    f"CREATE TEMP TABLE {model_table} (LIKE offerloom.model_runs INCLUDING ALL) ON COMMIT DROP"
                )
            )
            # Historical observations retain every data constraint, not live consent.
            # Dropping this column also drops only its dependent CHECK/index in the clone.
            conn.execute(
                text(f"ALTER TABLE pg_temp.{model_table} DROP COLUMN hosted_consent_id CASCADE")
            )
            conn.execute(
                text(
                    f"CREATE TEMP TABLE {tables['blobs']} (LIKE offerloom.blobs INCLUDING ALL) ON COMMIT DROP"
                )
            )
            for blob in manifest.blob_manifest:
                conn.execute(
                    text(f"""INSERT INTO pg_temp.{tables["blobs"]}
                    (owner_id,id,sha256,byte_length,media_type,storage_key)
                    VALUES(:owner,:id,:sha,:bytes,:media,:key)"""),
                    {
                        "owner": manifest.source_owner_id,
                        "id": blob.blob_id,
                        "sha": blob.sha256,
                        "bytes": blob.byte_length,
                        "media": blob.media_type,
                        "key": f"{blob.sha256[:2]}/{blob.sha256}",
                    },
                )
            for name, columns in COLUMNS.items():
                types = _native_types(conn, _relation(name))
                if name == "model_run_provenance":
                    for item in manifest.records[name]:
                        if set(item) != {"source_namespace", "source_owner_id", "record"}:
                            raise ArchiveError("invalid_portable_model_provenance")
                        if not isinstance(item["source_namespace"], str) or not isinstance(
                            item["source_owner_id"], str
                        ):
                            raise ArchiveError("invalid_portable_model_provenance")
                        UUID(item["source_namespace"])
                        UUID(item["source_owner_id"])
                        row = cast(JsonRow, item["record"])
                        _row_shape(row, columns, types)
                        output_hash = row["output_sha256"]
                        if output_hash is not None and (
                            not isinstance(output_hash, str)
                            or len(output_hash) != 64
                            or any(character not in "0123456789abcdef" for character in output_hash)
                        ):
                            raise ArchiveError("invalid_portable_model_hash")
                        if (
                            row["owner_id"] != item["source_owner_id"]
                            or not isinstance(row["id"], str)
                            or row["id"] in models
                        ):
                            raise ArchiveError("invalid_portable_model_provenance")
                        fields = ",".join(columns)
                        conn.execute(
                            text(f"""INSERT INTO pg_temp.{model_table} ({fields})
                            SELECT {fields} FROM jsonb_populate_record(
                            NULL::offerloom.model_runs,CAST(:row AS jsonb))"""),
                            {"row": canonical_json(row)},
                        )
                        models[row["id"]] = row
                    continue
                conn.execute(
                    text(
                        f"CREATE TEMP TABLE {tables[name]} (LIKE offerloom.{name} INCLUDING ALL) ON COMMIT DROP"
                    )
                )
                fields = ",".join(columns)
                for row in manifest.records[name]:
                    _row_shape(row, columns, types)
                    if row["owner_id"] != source or (
                        "acknowledged_by_owner_id" in row
                        and row["acknowledged_by_owner_id"] != source
                    ):
                        raise ArchiveError("portable_owner_mismatch")
                    conn.execute(
                        text(f"""INSERT INTO pg_temp.{tables[name]} ({fields})
                        SELECT {fields} FROM jsonb_populate_record(NULL::offerloom.{name},CAST(:row AS jsonb))"""),
                        {"row": canonical_json(row)},
                    )
            for name in tables:
                edges = conn.execute(
                    text("""SELECT c.confrelid::regclass::text AS parent,
                    ARRAY(SELECT a.attname FROM unnest(c.conkey) WITH ORDINALITY k(num,pos)
                          JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.num ORDER BY k.pos) AS child_columns,
                    ARRAY(SELECT a.attname FROM unnest(c.confkey) WITH ORDINALITY k(num,pos)
                          JOIN pg_attribute a ON a.attrelid=c.confrelid AND a.attnum=k.num ORDER BY k.pos) AS parent_columns
                    FROM pg_constraint c WHERE c.contype='f' AND c.conrelid=to_regclass(:relation)"""),
                    {"relation": f"offerloom.{name}"},
                ).mappings()
                for index, edge in enumerate(edges):
                    parent = edge["parent"].split(".")[-1]
                    if parent in {"owners", "workflow_runs"}:
                        continue
                    if parent == "portable_model_history":
                        # Portable v1 uses original model IDs and the separately
                        # validated historical family, never live history columns.
                        if (
                            name
                            not in {
                                "match_assessments",
                                "document_revisions",
                                "document_regeneration_proposals",
                            }
                            or edge["child_columns"] != ["owner_id", "historical_model_run_id"]
                            or edge["parent_columns"] != ["owner_id", "id"]
                            or conn.execute(
                                text(
                                    f"SELECT EXISTS(SELECT 1 FROM pg_temp.{tables[name]} "
                                    "WHERE historical_model_run_id IS NOT NULL)"
                                )
                            ).scalar_one()
                        ):
                            raise ArchiveError("unknown_portable_dependency")
                        continue
                    if parent == "model_runs":
                        for row in manifest.records[name]:
                            identifier = row.get("model_run_id")
                            if identifier is not None and (
                                not isinstance(identifier, str) or identifier not in models
                            ):
                                raise ArchiveError("portable_model_reference_missing")
                            if (
                                identifier is not None
                                and "workflow_run_id" in row
                                and row["workflow_run_id"] != models[identifier]["workflow_run_id"]
                            ):
                                raise ArchiveError("portable_model_reference_mismatch")
                        continue
                    if parent not in tables:
                        raise ArchiveError("unknown_portable_dependency")
                    # Only catalog-provided identifiers from reviewed tables enter SQL.
                    child_columns = ",".join(edge["child_columns"])
                    parent_columns = ",".join(edge["parent_columns"])
                    conn.execute(
                        text(f"""ALTER TABLE pg_temp.{tables[name]}
                        ADD CONSTRAINT portable_fk_{index} FOREIGN KEY ({child_columns})
                        REFERENCES pg_temp.{tables[parent]}({parent_columns})
                        DEFERRABLE INITIALLY DEFERRED""")
                    )
            conn.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            transaction.rollback()
    except ArchiveError:
        raise
    except (DBAPIError, ValueError, TypeError, KeyError) as exc:
        raise ArchiveError("invalid_portable_records") from exc
    _semantic_hashes(archive)
    referenced = {
        cast(str, row["blob_id"])
        for name in _BLOB_REFERENCES
        for row in manifest.records[name]
        if row["blob_id"] is not None
    }
    if referenced != {str(blob.blob_id) for blob in manifest.blob_manifest}:
        raise ArchiveError("portable_blob_reference_mismatch")
