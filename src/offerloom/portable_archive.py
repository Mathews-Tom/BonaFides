# SPDX-License-Identifier: MIT OR Apache-2.0
"""Versioned owner-data archives, never installation backups or executable input.

ZIP_STORED is intentional: bounds apply before allocating a decompressor and the
manifest names every permitted member. No archive path is ever extracted.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import struct
import zipfile
from dataclasses import dataclass
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, ValidationError

from offerloom.commands import canonical_json
from offerloom.problems import Problem

MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_BLOB_BYTES = 64 * 1024 * 1024
MAX_BLOBS = 10_000
MAX_RECORDS = 100_000
EXCLUSIONS = (
    "credentials",
    "installation_keys",
    "oauth",
    "provider_mail_cache",
    "raw_workflow_inputs",
    "sessions",
)
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
_MEMBER = re.compile(r"blobs/[0-9a-f]{64}\Z")


class ArchiveError(ValueError):
    """A content-free, stable reason suitable for an owner-facing failure."""


class ArchiveModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class BlobMember(ArchiveModel):
    blob_id: UUID
    sha256: Digest
    byte_length: Annotated[int, Field(ge=1, le=MAX_BLOB_BYTES)]
    media_type: Annotated[str, Field(min_length=1, max_length=200)]
    archive_member: Annotated[str, Field(pattern=r"^blobs/[0-9a-f]{64}$")]


class Manifest(ArchiveModel):
    format: Literal["offerloom-portable"]
    format_version: Literal[1]
    source_schema_version: Annotated[int, Field(ge=13)]
    created_at: AwareDatetime
    source_namespace: UUID
    source_owner_id: UUID
    current_profile_revision_id: UUID
    manifest_sha256: Digest
    records: dict[str, list[dict[str, JsonValue]]]
    blob_manifest: Annotated[list[BlobMember], Field(max_length=MAX_BLOBS)]
    exclusions: list[str]


@dataclass(frozen=True)
class PortableArchive:
    manifest: Manifest
    blobs: dict[str, bytes]


def manifest_digest(manifest: Manifest) -> str:
    value = manifest.model_dump(mode="json", exclude={"manifest_sha256"})
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _pairs(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise ArchiveError("duplicate_json_key")
        result[key] = value
    return result


def _constant(value: str) -> None:
    del value
    raise ArchiveError("nonfinite_json_number")


def _manifest(data: bytes, expected_source_owner: UUID) -> Manifest:
    if not 0 < len(data) <= MAX_MANIFEST_BYTES:
        raise ArchiveError("manifest_size_limit")
    try:
        # Reject duplicate keys before Pydantic sees a lossy last-key-wins object.
        raw = json.loads(data, object_pairs_hook=_pairs, parse_constant=_constant)
        if not isinstance(raw, dict) or type(raw.get("format_version")) is not int:
            raise ArchiveError("incompatible_portable_format")
        manifest = Manifest.model_validate_json(canonical_json(raw), strict=True)
    except ArchiveError:
        raise
    except (ValidationError, Problem, ValueError, UnicodeError, RecursionError) as exc:
        raise ArchiveError("invalid_portable_manifest") from exc
    if manifest.source_owner_id != expected_source_owner:
        raise ArchiveError("portable_owner_mismatch")
    if manifest.exclusions != list(EXCLUSIONS):
        raise ArchiveError("invalid_portable_exclusions")
    if sum(len(rows) for rows in manifest.records.values()) > MAX_RECORDS:
        raise ArchiveError("portable_record_limit")
    if manifest_digest(manifest) != manifest.manifest_sha256:
        raise ArchiveError("manifest_hash_mismatch")
    identifiers: set[UUID] = set()
    digests: set[str] = set()
    for blob in manifest.blob_manifest:
        if blob.blob_id in identifiers or blob.sha256 in digests:
            raise ArchiveError("duplicate_blob_identity")
        identifiers.add(blob.blob_id)
        digests.add(blob.sha256)
        if blob.archive_member != f"blobs/{blob.sha256}":
            raise ArchiveError("invalid_blob_member")
    return manifest


def _central_directory(data: bytes) -> None:
    # Offerloom emits neither ZIP64, archive comments nor multi-disk archives.
    # Bound the central-directory count BEFORE ZipFile allocates entry objects.
    if not 22 <= len(data) <= MAX_ARCHIVE_BYTES or data[-22:-18] != b"PK\x05\x06":
        raise ArchiveError("invalid_portable_archive")
    _, disk, directory_disk, disk_count, count, size, offset, comment = struct.unpack(
        "<4s4H2LH", data[-22:]
    )
    if (
        disk != 0
        or directory_disk != 0
        or disk_count != count
        or not 1 <= count <= MAX_BLOBS + 1
        or comment != 0
        or offset + size != len(data) - 22
        or not data.startswith(b"PK\x03\x04")
    ):
        raise ArchiveError("invalid_portable_directory")
    cursor = offset
    end = offset + size
    for _ in range(count):
        if cursor + 46 > end or data[cursor : cursor + 4] != b"PK\x01\x02":
            raise ArchiveError("invalid_portable_directory")
        name_bytes, extra_bytes, comment_bytes = struct.unpack_from("<3H", data, cursor + 28)
        cursor += 46 + name_bytes + extra_bytes + comment_bytes
        if cursor > end:
            raise ArchiveError("invalid_portable_directory")
    if cursor != end:
        raise ArchiveError("invalid_portable_directory")


def decode_archive(data: bytes, *, expected_source_owner: UUID) -> PortableArchive:
    """Validate bytes and exact source identity, without filesystem or DB writes.

    A cross-installation import passes the separately reviewed source-owner UUID;
    matching the destination's current owner is not an implicit import permission.
    Relational/semantic quarantine is required in addition to this byte validation.
    """
    _central_directory(data)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if len(names) != len(set(names)) or "manifest.json" not in names:
                raise ArchiveError("duplicate_or_missing_archive_member")
            if min(entry.header_offset for entry in entries) != 0:
                raise ArchiveError("invalid_portable_directory")
            total = 0
            for entry in entries:
                mode = stat.S_IFMT(entry.external_attr >> 16)
                if (
                    entry.compress_type != zipfile.ZIP_STORED
                    or entry.compress_size != entry.file_size
                    or entry.flag_bits & ~0x800
                    or entry.extra
                    or entry.comment
                    or entry.filename != entry.orig_filename
                    or entry.is_dir()
                    or mode not in (0, stat.S_IFREG)
                    or (entry.filename != "manifest.json" and not _MEMBER.fullmatch(entry.filename))
                ):
                    raise ArchiveError("unsafe_archive_member")
                limit = MAX_MANIFEST_BYTES if entry.filename == "manifest.json" else MAX_BLOB_BYTES
                if not 0 < entry.file_size <= limit:
                    raise ArchiveError("archive_member_size_limit")
                total += entry.file_size
                if total > MAX_ARCHIVE_BYTES:
                    raise ArchiveError("archive_size_limit")
            manifest = _manifest(archive.read("manifest.json"), expected_source_owner)
            expected = {"manifest.json", *(blob.archive_member for blob in manifest.blob_manifest)}
            if set(names) != expected:
                raise ArchiveError("archive_member_set_mismatch")
            blobs: dict[str, bytes] = {}
            for blob in manifest.blob_manifest:
                entry = archive.getinfo(blob.archive_member)
                if entry.file_size != blob.byte_length:
                    raise ArchiveError("blob_size_mismatch")
                payload = archive.read(entry)
                if hashlib.sha256(payload).hexdigest() != blob.sha256:
                    raise ArchiveError("blob_hash_mismatch")
                blobs[blob.sha256] = payload
    except (zipfile.BadZipFile, NotImplementedError, OverflowError, EOFError) as exc:
        raise ArchiveError("invalid_portable_archive") from exc
    return PortableArchive(manifest, blobs)


def encode_archive(manifest: Manifest, blobs: dict[str, bytes]) -> bytes:
    """Emit an independently decodable archive; never omit a missing blob."""
    data = canonical_json(manifest.model_dump(mode="json")).encode()
    _manifest(data, manifest.source_owner_id)
    expected = {blob.sha256 for blob in manifest.blob_manifest}
    if set(blobs) != expected:
        raise ArchiveError("archive_member_set_mismatch")
    if len(data) + sum(len(payload) for payload in blobs.values()) > MAX_ARCHIVE_BYTES:
        raise ArchiveError("archive_size_limit")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED, allowZip64=False) as archive:
        members = [("manifest.json", data)]
        for blob in manifest.blob_manifest:
            payload = blobs[blob.sha256]
            if (
                len(payload) != blob.byte_length
                or hashlib.sha256(payload).hexdigest() != blob.sha256
            ):
                raise ArchiveError("blob_hash_mismatch")
            members.append((blob.archive_member, payload))
        for name, payload in members:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            archive.writestr(info, payload)
    result = output.getvalue()
    if len(result) > MAX_ARCHIVE_BYTES:
        raise ArchiveError("archive_size_limit")
    return result
