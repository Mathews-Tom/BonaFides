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
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from types import TracebackType
from typing import IO, Annotated, BinaryIO, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, ValidationError

from offerloom.commands import canonical_json
from offerloom.problems import Problem

MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_BLOB_BYTES = 64 * 1024 * 1024
MAX_BLOBS = 10_000
MAX_RECORDS = 100_000
_MAX_ZIP_OVERHEAD = 4 * 1024 * 1024
MAX_ARCHIVE_BYTES = MAX_BLOBS * MAX_BLOB_BYTES + MAX_MANIFEST_BYTES + _MAX_ZIP_OVERHEAD
_CHUNK_BYTES = 1024 * 1024
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
    format_version: Literal[1, 2]
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
    blobs: Mapping[str, bytes]

    def close(self) -> None:
        """Release ZIP metadata, without closing a caller-provided input file."""
        if isinstance(self.blobs, _ArchiveBlobs):
            self.blobs.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


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


def hash_stream(source: IO[bytes], *, limit: int) -> tuple[str, int]:
    """Hash from the current position to EOF using bounded reads, without closing."""
    digest = hashlib.sha256()
    length = 0
    while chunk := source.read(min(_CHUNK_BYTES, limit - length + 1)):
        length += len(chunk)
        if length > limit:
            raise ArchiveError("archive_size_limit")
        digest.update(chunk)
    return digest.hexdigest(), length


def _read_at(source: BinaryIO, offset: int, size: int) -> bytes:
    source.seek(offset)
    data = source.read(size)
    if len(data) != size:
        raise ArchiveError("invalid_portable_directory")
    return data


def _zip64_extra(extra: bytes, fields: int) -> tuple[int, ...]:
    if not fields:
        if extra:
            raise ArchiveError("unsafe_archive_member")
        return ()
    if len(extra) != 4 + fields * 8 or struct.unpack_from("<2H", extra) != (1, fields * 8):
        raise ArchiveError("unsafe_archive_member")
    return struct.unpack_from(f"<{fields}Q", extra, 4)


def _central_directory(source: BinaryIO) -> tuple[int, int]:
    # Bound both the count and metadata BEFORE ZipFile allocates entry objects.
    # Only ordinary single-disk ZIP and its fixed-size ZIP64 trailer are accepted.
    source.seek(0, io.SEEK_END)
    length = source.tell()
    if not 22 <= length <= MAX_ARCHIVE_BYTES:
        raise ArchiveError("invalid_portable_archive")
    end = _read_at(source, length - 22, 22)
    if end[:4] != b"PK\x05\x06":
        raise ArchiveError("invalid_portable_archive")
    _, disk, directory_disk, disk_count, count, size, offset, comment = struct.unpack(
        "<4s4H2LH", end
    )
    if disk or directory_disk or disk_count != count or comment:
        raise ArchiveError("invalid_portable_directory")
    directory_end = length - 22
    locator = _read_at(source, length - 42, 20) if length >= 42 else b""
    if locator[:4] == b"PK\x06\x07":
        _, zip64_disk, zip64_offset, disks = struct.unpack("<4sLQL", locator)
        if zip64_disk or disks != 1 or zip64_offset + 56 != length - 42:
            raise ArchiveError("invalid_portable_directory")
        record = _read_at(source, zip64_offset, 56)
        (
            signature,
            record_size,
            _,
            required_version,
            zip64_disk,
            zip64_directory_disk,
            zip64_disk_count,
            zip64_count,
            zip64_size,
            zip64_directory_offset,
        ) = struct.unpack("<4sQ2H2L4Q", record)
        if (
            signature != b"PK\x06\x06"
            or record_size != 44
            or required_version != 45
            or zip64_disk
            or zip64_directory_disk
            or zip64_disk_count != zip64_count
            or count != min(zip64_count, 0xFFFF)
            or size != min(zip64_size, 0xFFFFFFFF)
            or offset != min(zip64_directory_offset, 0xFFFFFFFF)
        ):
            raise ArchiveError("invalid_portable_directory")
        count, size, offset = zip64_count, zip64_size, zip64_directory_offset
        directory_end = zip64_offset
    elif count == 0xFFFF or size == 0xFFFFFFFF or offset == 0xFFFFFFFF:
        raise ArchiveError("invalid_portable_directory")
    if (
        not 1 <= count <= MAX_BLOBS + 1
        or size > _MAX_ZIP_OVERHEAD
        or offset + size != directory_end
        or _read_at(source, 0, 4) != b"PK\x03\x04"
    ):
        raise ArchiveError("invalid_portable_directory")
    cursor = offset
    for _ in range(count):
        if cursor + 46 > directory_end:
            raise ArchiveError("invalid_portable_directory")
        header = _read_at(source, cursor, 46)
        if header[:4] != b"PK\x01\x02":
            raise ArchiveError("invalid_portable_directory")
        name_bytes, extra_bytes, comment_bytes, member_disk = struct.unpack_from("<4H", header, 28)
        next_cursor = cursor + 46 + name_bytes + extra_bytes + comment_bytes
        if next_cursor > directory_end or member_disk:
            raise ArchiveError("invalid_portable_directory")
        compressed, uncompressed = struct.unpack_from("<2L", header, 20)
        member_offset = struct.unpack_from("<L", header, 42)[0]
        fields = sum(value == 0xFFFFFFFF for value in (uncompressed, compressed, member_offset))
        _zip64_extra(_read_at(source, cursor + 46 + name_bytes, extra_bytes), fields)
        cursor = next_cursor
    if cursor != directory_end:
        raise ArchiveError("invalid_portable_directory")
    return count, offset


def _local_headers(source: BinaryIO, entries: list[zipfile.ZipInfo], directory: int) -> None:
    cursor = 0
    for entry in sorted(entries, key=lambda item: item.header_offset):
        if entry.header_offset != cursor or cursor + 30 > directory:
            raise ArchiveError("invalid_portable_directory")
        header = _read_at(source, cursor, 30)
        (
            signature,
            version,
            flags,
            compression,
            _,
            _,
            crc,
            compressed,
            uncompressed,
            name_length,
            extra_length,
        ) = struct.unpack("<4s5H3L2H", header)
        if (
            signature != b"PK\x03\x04"
            or version not in (10, 20, 45)
            or version != entry.extract_version
            or flags != entry.flag_bits
            or compression != entry.compress_type
            or crc != entry.CRC
            or cursor + 30 + name_length + extra_length > directory
            or _read_at(source, cursor + 30, name_length) != entry.filename.encode("ascii")
        ):
            raise ArchiveError("unsafe_archive_member")
        values = iter(
            _zip64_extra(
                _read_at(source, cursor + 30 + name_length, extra_length),
                int(uncompressed == 0xFFFFFFFF) + int(compressed == 0xFFFFFFFF),
            )
        )
        if uncompressed == 0xFFFFFFFF:
            uncompressed = next(values)
        if compressed == 0xFFFFFFFF:
            compressed = next(values)
        if uncompressed != entry.file_size or compressed != entry.compress_size:
            raise ArchiveError("unsafe_archive_member")
        cursor += 30 + name_length + extra_length + compressed
        if cursor > directory:
            raise ArchiveError("invalid_portable_directory")
    if cursor != directory:
        raise ArchiveError("invalid_portable_directory")


class _ArchiveBlobs(Mapping[str, bytes]):
    def __init__(self, archive: zipfile.ZipFile, manifest: Manifest) -> None:
        self._archive = archive
        self._members = {blob.sha256: blob for blob in manifest.blob_manifest}

    def __iter__(self) -> Iterator[str]:
        return iter(self._members)

    def __len__(self) -> int:
        return len(self._members)

    def __getitem__(self, digest: str) -> bytes:
        member = self._members[digest]
        try:
            payload = self._archive.read(member.archive_member)
        except (zipfile.BadZipFile, OSError, ValueError, EOFError) as exc:
            raise ArchiveError("invalid_portable_archive") from exc
        if len(payload) != member.byte_length:
            raise ArchiveError("blob_size_mismatch")
        if hashlib.sha256(payload).hexdigest() != digest:
            raise ArchiveError("blob_hash_mismatch")
        return payload

    def close(self) -> None:
        self._archive.close()


def decode_archive(data: bytes | BinaryIO, *, expected_source_owner: UUID) -> PortableArchive:
    """Validate every byte before returning a non-caching blob mapping.

    The caller must keep a supplied seekable binary file open and unchanged for
    the archive's lifetime. Closing the archive never closes that supplied file.
    Small byte inputs remain supported for existing independent consumers.
    Relational/semantic quarantine is required in addition to byte validation.
    """
    source = io.BytesIO(data) if isinstance(data, bytes) else data
    archive: zipfile.ZipFile | None = None
    try:
        count, directory = _central_directory(source)
        archive = zipfile.ZipFile(source)
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if len(entries) != count:
            raise ArchiveError("invalid_portable_directory")
        if len(names) != len(set(names)) or "manifest.json" not in names:
            raise ArchiveError("duplicate_or_missing_archive_member")
        for entry in entries:
            mode = stat.S_IFMT(entry.external_attr >> 16)
            if (
                entry.compress_type != zipfile.ZIP_STORED
                or entry.compress_size != entry.file_size
                or entry.flag_bits & ~0x800
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
        _local_headers(source, entries, directory)
        manifest = _manifest(archive.read("manifest.json"), expected_source_owner)
        expected = {"manifest.json", *(blob.archive_member for blob in manifest.blob_manifest)}
        if set(names) != expected:
            raise ArchiveError("archive_member_set_mismatch")
        for blob in manifest.blob_manifest:
            entry = archive.getinfo(blob.archive_member)
            if entry.file_size != blob.byte_length:
                raise ArchiveError("blob_size_mismatch")
            with archive.open(entry) as member:
                digest, length = hash_stream(member, limit=MAX_BLOB_BYTES)
            if length != blob.byte_length:
                raise ArchiveError("blob_size_mismatch")
            if digest != blob.sha256:
                raise ArchiveError("blob_hash_mismatch")
        return PortableArchive(manifest, _ArchiveBlobs(archive, manifest))
    except ArchiveError:
        if archive is not None:
            archive.close()
        raise
    except (
        zipfile.BadZipFile,
        NotImplementedError,
        OverflowError,
        OSError,
        ValueError,
        EOFError,
    ) as exc:
        if archive is not None:
            archive.close()
        raise ArchiveError("invalid_portable_archive") from exc


def encode_archive(manifest: Manifest, blobs: Mapping[str, bytes], output: BinaryIO) -> None:
    """Stream stored ZIP members to a seekable output; never omit a missing blob."""
    data = canonical_json(manifest.model_dump(mode="json")).encode()
    _manifest(data, manifest.source_owner_id)
    expected = {blob.sha256 for blob in manifest.blob_manifest}
    if set(blobs) != expected:
        raise ArchiveError("archive_member_set_mismatch")
    if not output.seekable():
        raise ArchiveError("portable_output_not_seekable")
    output.seek(0)
    output.truncate(0)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        info = _member_info("manifest.json", len(data))
        with archive.open(info, "w", force_zip64=manifest.format_version == 2) as member:
            member.write(data)
        for blob in manifest.blob_manifest:
            try:
                payload = blobs[blob.sha256]
            except KeyError as exc:
                raise ArchiveError("archive_member_set_mismatch") from exc
            if (
                len(payload) != blob.byte_length
                or hashlib.sha256(payload).hexdigest() != blob.sha256
            ):
                raise ArchiveError("blob_hash_mismatch")
            info = _member_info(blob.archive_member, blob.byte_length)
            with archive.open(info, "w", force_zip64=manifest.format_version == 2) as member:
                member.write(payload)
            del payload
    if output.tell() > MAX_ARCHIVE_BYTES:
        raise ArchiveError("archive_size_limit")


def _member_info(name: str, size: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o600) << 16
    info.file_size = size
    return info
