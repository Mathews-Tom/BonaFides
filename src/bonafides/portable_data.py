# SPDX-License-Identifier: MIT OR Apache-2.0
"""Explicit owner-data release and private, non-live relational validation."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, cast
from uuid import UUID, uuid4

from pydantic import JsonValue, ValidationError
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from bonafides.claims import literal_grounded
from bonafides.commands import canonical_json, lock_installation
from bonafides.config import Settings
from bonafides.db import SCHEMA_VERSION, Database, require_baseline, schema_ledger
from bonafides.document_generation import revision_digest
from bonafides.documents import parse_body
from bonafides.installation_control import Control
from bonafides.portable_archive import (
    EXCLUSIONS,
    MAX_BLOBS,
    MAX_MANIFEST_BYTES,
    MAX_RECORDS,
    WORKSPACE_RECORD_SCHEMA,
    ArchiveError,
    BlobMember,
    Manifest,
    PortableArchive,
    encode_archive,
    manifest_digest,
)
from bonafides.portable_columns import COLUMNS
from bonafides.portable_workspace import FORMAT as WORKSPACE_FORMAT
from bonafides.portable_workspace_v3 import FORMAT_VERSION as WORKSPACE_VERSION
from bonafides.profile_breadth import snapshot_claim_ids
from bonafides.profile_entries import ClaimPayload, ClaimWrite, WritingFactPayload
from bonafides.profile_identity import PortraitPin, validate_portrait
from bonafides.profiles import ProfileSnapshot, content_hash
from bonafides.storage import BlobStore

JsonRow = dict[str, JsonValue]
_BLOB_REFERENCES = ("evidence_revisions", "application_attachments", "document_artifacts")


def _portrait_pins(records: Mapping[str, list[JsonRow]]) -> set[PortraitPin]:
    pins: set[PortraitPin] = set()
    for family, field in (
        ("profile_revisions", "snapshot"),
        ("document_revisions", "provenance_snapshot"),
    ):
        for row in records[family]:
            value = row[field]
            if family == "document_revisions" and isinstance(value, dict):
                value = value.get("frame")
            if isinstance(value, dict) and value.get("portrait") is not None:
                try:
                    pins.add(
                        PortraitPin.model_validate_json(
                            canonical_json(value["portrait"]), strict=True
                        )
                    )
                except ValidationError as exc:
                    raise ArchiveError("invalid_portable_portrait") from exc
    return pins


def _validate_portraits(manifest: Manifest, blobs: Mapping[str, bytes] | None = None) -> None:
    members = {member.blob_id: member for member in manifest.blob_manifest}
    for pin in _portrait_pins(manifest.records):
        member = members.get(pin.blob_id)
        if (
            member is None
            or member.sha256 != pin.sha256
            or member.byte_length != pin.byte_length
            or member.media_type != "image/png"
        ):
            raise ArchiveError("portable_portrait_reference_mismatch")
        if blobs is not None:
            try:
                data = blobs[pin.sha256]
                if len(data) != pin.byte_length or hashlib.sha256(data).hexdigest() != pin.sha256:
                    raise ValueError("portrait_digest_mismatch")
                validate_portrait(data)
            except (KeyError, OSError, ValueError) as exc:
                raise ArchiveError("invalid_portable_portrait") from exc


class _CollectedBlobs(Mapping[str, bytes]):
    def __init__(
        self, store: BlobStore, owner_id: UUID, members: Mapping[str, tuple[str, int]]
    ) -> None:
        self._store = store
        self._owner_id = owner_id
        self._members = members

    def __iter__(self) -> Iterator[str]:
        return iter(self._members)

    def __len__(self) -> int:
        return len(self._members)

    def __getitem__(self, digest: str) -> bytes:
        key, size = self._members[digest]
        try:
            return self._store.read(self._owner_id, key, digest, size)
        except (OSError, ValueError) as exc:
            raise ArchiveError("portable_blob_unavailable") from exc


def _relation(name: str) -> str:
    if name not in COLUMNS:
        raise ArchiveError("unknown_portable_record_type")
    return "model_runs" if name == "model_run_provenance" else name


def collect_owner_data(conn: Connection, settings: Settings, owner_id: UUID) -> PortableArchive:
    """Caller owns a repeatable-read transaction; hold deletion's barrier throughout."""
    if conn.execute(text("SHOW transaction_isolation")).scalar_one() != "repeatable read":
        raise ValueError("portable_export_requires_repeatable_read")
    lock_installation(conn)
    return _collect_owner_data(conn, settings.blob_root, owner_id)


def collect_controlled_owner_data(
    conn: Connection, settings: Settings, control: Control
) -> PortableArchive:
    """The dedicated coordinator, not an ordinary worker, owns this snapshot."""
    if conn.execute(text("SHOW transaction_isolation")).scalar_one() != "repeatable read":
        raise ValueError("portable_export_requires_repeatable_read")
    # Do not hold the control-row UPDATE lock during blob I/O: the independent
    # heartbeat needs that row. Publication rechecks current authority afterward.
    valid = conn.execute(
        text("""SELECT EXISTS(SELECT 1 FROM bonafides.installation_controls c
        JOIN bonafides.installation_state i ON i.singleton
        JOIN bonafides.workflow_runs w ON w.owner_id=c.owner_id AND w.id=c.id
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
    return _collect_owner_data(conn, settings.blob_root, control.owner_id)


def collect_fenced_owner_data(
    conn: Connection, settings: Settings, owner_id: UUID
) -> PortableArchive:
    """Backup capture's snapshot: its caller fences the cut and rechecks authority after.

    A lease timestamp read through a long-lived snapshot never sees heartbeat
    renewals, so this collector deliberately does not test lease freshness.
    """
    if conn.execute(text("SHOW transaction_isolation")).scalar_one() != "repeatable read":
        raise ValueError("portable_export_requires_repeatable_read")
    return _collect_owner_data(conn, settings.blob_root, owner_id)


def restored_owner_manifest(conn: Connection, blob_root: Path, owner_id: UUID) -> Manifest:
    """Records of a natively restored target that is still closed to every writer."""
    return _collect_owner_data(conn, blob_root, owner_id).manifest


def _collect_owner_data(conn: Connection, blob_root: Path, owner_id: UUID) -> PortableArchive:
    namespace = conn.execute(
        text("SELECT namespace_id FROM bonafides.installation_state WHERE singleton")
    ).scalar_one()
    ledger = schema_ledger(conn)
    require_baseline(ledger)
    if max(ledger, default=None) != SCHEMA_VERSION:
        raise ArchiveError("incompatible_portable_schema")
    profile = conn.execute(
        text("SELECT current_revision_id FROM bonafides.profiles WHERE owner_id=:owner"),
        {"owner": owner_id},
    ).scalar_one()
    records: dict[str, list[JsonRow]] = {}
    count, remaining = 0, MAX_MANIFEST_BYTES
    for name, columns in COLUMNS.items():
        relation = _relation(name)
        fields = ",".join(columns)
        if name in {
            "match_assessments",
            "document_revisions",
            "document_regeneration_proposals",
        }:
            fields = ",".join(
                "COALESCE(model_run_id,historical_model_run_id) AS model_run_id"
                if column == "model_run_id"
                else (
                    "COALESCE(workflow_run_id,(SELECT (h.record->>'workflow_run_id')::uuid "
                    "FROM bonafides.portable_model_history h WHERE h.owner_id=:owner "
                    "AND h.id=historical_model_run_id)) AS workflow_run_id"
                    if name == "document_regeneration_proposals" and column == "workflow_run_id"
                    else column
                )
                for column in columns
            )
        order = ",".join(
            column
            for column in columns
            if column == "id" or column.endswith("_id") or column == "sequence"
        )
        query = text(f"""SELECT CASE WHEN octet_length(to_jsonb(r)::text)<=:maximum
            THEN to_jsonb(r) ELSE NULL END FROM (
            SELECT {fields} FROM bonafides.{relation} WHERE owner_id=:owner
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
    result = conn.execute(
        text("""SELECT CASE WHEN octet_length(record::text)<=:maximum
        THEN jsonb_build_object('source_namespace',source_namespace,
         'source_owner_id',source_owner_id,'record',record) ELSE NULL END,record_sha256
        FROM bonafides.portable_model_history WHERE owner_id=:owner
        ORDER BY id LIMIT :limit""").execution_options(stream_results=True),
        {"owner": owner_id, "maximum": remaining, "limit": MAX_RECORDS - count + 1},
    )
    try:
        for value, digest in result:
            if value is None:
                raise ArchiveError("manifest_size_limit")
            historical = cast(JsonRow, value)
            if hashlib.sha256(canonical_json(historical["record"]).encode()).hexdigest() != digest:
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
    blob_ids.update(str(pin.blob_id) for pin in _portrait_pins(records))
    if len(blob_ids) > MAX_BLOBS:
        raise ArchiveError("portable_blob_limit")
    locations: dict[str, tuple[str, int]] = {}
    members: list[BlobMember] = []
    for identifier in sorted(blob_ids):
        blob_row = (
            conn.execute(
                text("""SELECT id,sha256,byte_length,media_type,storage_key,state FROM bonafides.blobs
                WHERE owner_id=:owner AND id=:id"""),
                {"owner": owner_id, "id": UUID(identifier)},
            )
            .mappings()
            .one()
        )
        if blob_row["state"] != "ready":
            raise ArchiveError("portable_blob_unavailable")
        # Blob bytes are read and verified only when encode_archive writes them,
        # while the caller still holds the deletion barrier / controlled snapshot.
        locations[blob_row["sha256"]] = (blob_row["storage_key"], blob_row["byte_length"])
        members.append(
            BlobMember(
                blob_id=blob_row["id"],
                sha256=blob_row["sha256"],
                byte_length=blob_row["byte_length"],
                media_type=blob_row["media_type"],
                archive_member=f"blobs/{blob_row['sha256']}",
            )
        )
    manifest = Manifest(
        format=WORKSPACE_FORMAT,
        format_version=WORKSPACE_VERSION,
        source_schema_version=WORKSPACE_RECORD_SCHEMA,
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
    _validate_portraits(manifest)
    return PortableArchive(manifest, _CollectedBlobs(BlobStore(blob_root), owner_id, locations))


def export_owner_data(
    db: Database, settings: Settings, owner_id: UUID, output: BinaryIO
) -> Manifest:
    with (
        db.engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn,
        conn.begin(),
    ):
        archive = collect_owner_data(conn, settings, owner_id)
        encode_archive(archive.manifest, archive.blobs, output)
        return archive.manifest


def _native_types(conn: Connection, relation: str) -> dict[str, str]:
    rows = conn.execute(
        text("""SELECT a.attname,t.typname FROM pg_catalog.pg_attribute a
        JOIN pg_catalog.pg_type t ON t.oid=a.atttypid
        WHERE a.attrelid=to_regclass(:relation) AND a.attnum>0 AND NOT a.attisdropped"""),
        {"relation": f"bonafides.{relation}"},
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
    kinds = {cast(str, item["id"]): item["kind"] for item in records["professional_claims"]}
    archived_claims = {
        item["id"] for item in records["professional_claims"] if item["archived_at"] is not None
    }
    revisions = {cast(str, item["id"]): item for item in records["claim_revisions"]}
    sources = {cast(str, item["id"]): item["payload"] for item in records["evidence_revisions"]}
    applications = {cast(str, item["id"]) for item in records["applications"]}
    links: dict[str, list[JsonRow]] = {}
    for link in records["claim_evidence_links"]:
        links.setdefault(cast(str, link["claim_revision_id"]), []).append(link)
    for identifier, breadth_revision in revisions.items():
        kind = kinds[cast(str, breadth_revision["claim_id"])]
        if kind not in ("publication", "writing_fact"):
            continue
        own = links.get(identifier, [])
        try:
            checked = ClaimWrite.model_validate_json(
                canonical_json(
                    {
                        "kind": kind,
                        "payload": breadth_revision["payload"],
                        "evidence_links": [
                            {
                                key: link[key]
                                for key in ("evidence_revision_id", "support_kind", "excerpt")
                            }
                            for link in own
                        ],
                    }
                ),
                strict=True,
            )
            payload = cast(ClaimPayload | WritingFactPayload, checked.payload)
            if (
                isinstance(payload, WritingFactPayload)
                and payload.application_id is not None
                and str(payload.application_id) not in applications
                and breadth_revision["claim_id"] not in archived_claims
            ):
                raise ValueError("writing_scope_missing")
            if payload.text.strip() and not any(
                link["support_kind"] in ("direct", "user_asserted")
                and isinstance(source := sources[cast(str, link["evidence_revision_id"])], dict)
                and isinstance(source.get("text"), str)
                and literal_grounded(
                    payload.text, cast(str, link["excerpt"]), cast(str, source["text"])
                )
                for link in own
            ):
                raise ValueError("literal_claim_not_grounded")
        except (ValidationError, ValueError, KeyError) as exc:
            raise ArchiveError("invalid_portable_breadth_claim") from exc
    for row in records["profile_revisions"]:
        try:
            snapshot = ProfileSnapshot.model_validate_json(
                canonical_json(row["snapshot"]), strict=True
            )
        except ValidationError as exc:
            raise ArchiveError("invalid_portable_profile") from exc
        if row["id"] == str(archive.manifest.current_profile_revision_id) and any(
            scope != "personal" and scope not in applications and ids
            for scope, ids in snapshot.writing_fact_revision_ids.items()
        ):
            raise ArchiveError("invalid_portable_current_profile")
        snapshot_claims = {
            UUID(value) for value in snapshot_claim_ids(snapshot.model_dump(mode="json"))
        }
        if not snapshot_claims <= claims or not set(snapshot.evidence_revision_ids) <= evidence:
            raise ArchiveError("portable_snapshot_reference_missing")
        selected_ids = snapshot_claim_ids(snapshot.model_dump(mode="json"))
        if len(set(selected_ids)) != len(selected_ids) or len(
            {revisions[value]["claim_id"] for value in selected_ids}
        ) != len(selected_ids):
            raise ArchiveError("portable_snapshot_membership_mismatch")
        for scope, identifiers in snapshot.writing_fact_revision_ids.items():
            for writing_identifier in identifiers:
                breadth_revision = revisions[str(writing_identifier)]
                payload_value = cast(dict[str, JsonValue], breadth_revision["payload"])
                if (
                    kinds[cast(str, breadth_revision["claim_id"])] != "writing_fact"
                    or str(payload_value.get("application_id") or "personal") != scope
                ):
                    raise ArchiveError("portable_snapshot_membership_mismatch")
        if any(
            kinds[cast(str, revisions[str(identifier)]["claim_id"])] == "writing_fact"
            for identifier in snapshot.claim_revision_ids
        ):
            raise ArchiveError("portable_snapshot_membership_mismatch")
        selected = {
            UUID(cast(str, link["claim_revision_id"]))
            for link in records["profile_revision_claims"]
            if link["profile_revision_id"] == row["id"]
        }
        if selected != snapshot_claims:
            raise ArchiveError("portable_snapshot_membership_mismatch")
    for row in records["document_revisions"]:
        try:
            body = parse_body(row["body"])
            provenance = cast(dict[str, object], row["provenance_snapshot"])
            from bonafides.print_fitting import inventory

            hidden = inventory(body, provenance).omissions
            for item in hidden:
                if (
                    not set(item.block.claim_revision_ids) <= claims
                    or not set(item.block.evidence_revision_ids) <= evidence
                ):
                    raise ArchiveError("portable_snapshot_reference_missing")
            for section in body.sections:
                for block in section.blocks:
                    if (
                        not set(block.claim_revision_ids) <= claims
                        or not set(block.evidence_revision_ids) <= evidence
                    ):
                        raise ArchiveError("portable_snapshot_reference_missing")
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
    bonafides. Model provenance is historical data, never a fabricated ModelRun.
    """
    manifest = archive.manifest
    if manifest.source_schema_version != WORKSPACE_RECORD_SCHEMA or set(manifest.records) != set(
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
                    f"CREATE TEMP TABLE {model_table} (LIKE bonafides.model_runs INCLUDING ALL) ON COMMIT DROP"
                )
            )
            # Historical observations retain every data constraint, not live consent.
            # Dropping this column also drops only its dependent CHECK/index in the clone.
            conn.execute(
                text(f"ALTER TABLE pg_temp.{model_table} DROP COLUMN hosted_consent_id CASCADE")
            )
            conn.execute(
                text(
                    f"CREATE TEMP TABLE {tables['blobs']} (LIKE bonafides.blobs INCLUDING ALL) ON COMMIT DROP"
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
                            NULL::bonafides.model_runs,CAST(:row AS jsonb))"""),
                            {"row": canonical_json(row)},
                        )
                        models[row["id"]] = row
                    continue
                conn.execute(
                    text(
                        f"CREATE TEMP TABLE {tables[name]} (LIKE bonafides.{name} INCLUDING ALL) ON COMMIT DROP"
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
                        SELECT {fields} FROM jsonb_populate_record(NULL::bonafides.{name},CAST(:row AS jsonb))"""),
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
                    {"relation": f"bonafides.{name}"},
                ).mappings()
                for index, edge in enumerate(edges):
                    parent = edge["parent"].split(".")[-1]
                    if parent in {"owners", "workflow_runs"}:
                        continue
                    if parent == "portable_model_history":
                        # Portable records use original model IDs and the separately
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
    _validate_portraits(manifest, archive.blobs)
    referenced = {
        cast(str, row["blob_id"])
        for name in _BLOB_REFERENCES
        for row in manifest.records[name]
        if row["blob_id"] is not None
    }
    referenced.update(str(pin.blob_id) for pin in _portrait_pins(manifest.records))
    if referenced != {str(blob.blob_id) for blob in manifest.blob_manifest}:
        raise ArchiveError("portable_blob_reference_mismatch")
