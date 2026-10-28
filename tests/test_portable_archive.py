# SPDX-License-Identifier: MIT OR Apache-2.0
from __future__ import annotations

import hashlib
import io
import json
import stat
import struct
import zipfile
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from offerloom.commands import canonical_json
from offerloom.portable_archive import (
    EXCLUSIONS,
    MAX_BLOB_BYTES,
    ArchiveError,
    BlobMember,
    Manifest,
    decode_archive,
    encode_archive,
    hash_stream,
    manifest_digest,
)


def sample() -> tuple[Manifest, dict[str, bytes]]:
    payload = b"Synthetic private attachment\n"
    digest = hashlib.sha256(payload).hexdigest()
    manifest = Manifest(
        format="offerloom-portable",
        format_version=2,
        source_schema_version=13,
        created_at=datetime(2026, 10, 25, tzinfo=UTC),
        source_namespace=uuid4(),
        source_owner_id=uuid4(),
        current_profile_revision_id=uuid4(),
        manifest_sha256="0" * 64,
        records={
            "document_revisions": [
                {
                    "body": {"text": "Unsupported Kubernetes claim"},
                    "validation_issues": ["unsupported"],
                }
            ]
        },
        blob_manifest=[
            BlobMember(
                blob_id=uuid4(),
                sha256=digest,
                byte_length=len(payload),
                media_type="text/plain",
                archive_member=f"blobs/{digest}",
            )
        ],
        exclusions=list(EXCLUSIONS),
    )
    return manifest.model_copy(update={"manifest_sha256": manifest_digest(manifest)}), {
        digest: payload
    }


def _encoded(manifest: Manifest, blobs: Mapping[str, bytes]) -> bytes:
    output = io.BytesIO()
    encode_archive(manifest, blobs, output)
    return output.getvalue()


def repack(
    data: bytes,
    *,
    manifest: bytes | None = None,
    extra_name: str | None = None,
    mode: int = stat.S_IFREG,
    compression: int = zipfile.ZIP_STORED,
    corrupt_blob: bool = False,
) -> bytes:
    target = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(target, "w") as destination:
        for old in source.infolist():
            value = source.read(old)
            if old.filename == "manifest.json" and manifest is not None:
                value = manifest
            elif old.filename.startswith("blobs/") and corrupt_blob:
                value = b"!" * len(value)
            info = zipfile.ZipInfo(old.filename)
            info.compress_type = compression
            info.create_system = 3
            info.external_attr = (mode | 0o600) << 16
            destination.writestr(info, value)
        if extra_name is not None:
            destination.writestr(extra_name, b"unlisted private bytes")
    return target.getvalue()


def test_private_archive_preserves_unsupported_draft_and_exact_attachment() -> None:
    manifest, blobs = sample()
    output = io.BytesIO()
    encode_archive(manifest, blobs, output)
    assert output.tell() > 0
    with decode_archive(output, expected_source_owner=manifest.source_owner_id) as decoded:
        assert decoded.manifest == manifest
        assert not isinstance(decoded.blobs, dict)
        assert decoded.blobs == blobs
        assert decoded.manifest.records["document_revisions"][0]["validation_issues"] == [
            "unsupported"
        ]
    assert not output.closed
    # V2 writes ZIP64 local size fields even for a small fixture.
    assert struct.unpack_from("<2L", output.getvalue(), 18) == (0xFFFFFFFF, 0xFFFFFFFF)


def test_source_owner_requires_exact_independent_identity() -> None:
    manifest, blobs = sample()
    data = _encoded(manifest, blobs)
    with pytest.raises(ArchiveError, match="portable_owner_mismatch"):
        decode_archive(data, expected_source_owner=uuid4())
    assert decode_archive(data, expected_source_owner=manifest.source_owner_id).blobs == blobs


@pytest.mark.parametrize(
    "name", ["../outside", "/absolute", "blobs/../../escape", "blobs\\escape", "extra.json"]
)
def test_unlisted_and_traversing_members_are_rejected_without_extraction(name: str) -> None:
    manifest, blobs = sample()
    data = repack(_encoded(manifest, blobs), extra_name=name)
    with pytest.raises(ArchiveError, match="unsafe_archive_member"):
        decode_archive(data, expected_source_owner=manifest.source_owner_id)


@pytest.mark.parametrize("mode", [stat.S_IFLNK, stat.S_IFIFO, stat.S_IFDIR, stat.S_IFSOCK])
def test_nonregular_member_types_are_rejected(mode: int) -> None:
    manifest, blobs = sample()
    data = repack(_encoded(manifest, blobs), mode=mode)
    with pytest.raises(ArchiveError, match="unsafe_archive_member"):
        decode_archive(data, expected_source_owner=manifest.source_owner_id)


def test_compressed_zip_bomb_is_rejected_before_decompression() -> None:
    manifest, blobs = sample()
    data = repack(_encoded(manifest, blobs), compression=zipfile.ZIP_DEFLATED)
    with pytest.raises(ArchiveError, match="unsafe_archive_member"):
        decode_archive(data, expected_source_owner=manifest.source_owner_id)


@pytest.mark.parametrize(
    "field,value",
    [
        ("format_version", 3),
        ("format_version", True),
        ("format", "another-product"),
        ("secret", "not-allowed"),
    ],
)
def test_incompatible_and_unknown_manifest_fields_fail(field: str, value: object) -> None:
    manifest, blobs = sample()
    wire = manifest.model_dump(mode="json")
    wire[field] = value
    data = repack(_encoded(manifest, blobs), manifest=canonical_json(wire).encode())
    with pytest.raises(ArchiveError):
        decode_archive(data, expected_source_owner=manifest.source_owner_id)


def test_hash_mismatch_and_same_size_blob_corruption_fail() -> None:
    manifest, blobs = sample()
    data = _encoded(manifest, blobs)
    wire = manifest.model_dump(mode="json")
    wire["records"]["document_revisions"][0]["body"]["text"] = "Altered draft"
    with pytest.raises(ArchiveError, match="manifest_hash_mismatch"):
        decode_archive(
            repack(data, manifest=canonical_json(wire).encode()),
            expected_source_owner=manifest.source_owner_id,
        )
    with pytest.raises(ArchiveError, match="blob_hash_mismatch"):
        decode_archive(
            repack(data, corrupt_blob=True), expected_source_owner=manifest.source_owner_id
        )


def test_duplicate_json_keys_do_not_use_last_key_wins() -> None:
    manifest, blobs = sample()
    raw = canonical_json(manifest.model_dump(mode="json"))
    duplicate = ('{"format_version":2,' + raw[1:]).encode()
    with pytest.raises(ArchiveError, match="duplicate_json_key"):
        decode_archive(
            repack(_encoded(manifest, blobs), manifest=duplicate),
            expected_source_owner=manifest.source_owner_id,
        )


def test_missing_or_duplicate_member_is_rejected() -> None:
    manifest, blobs = sample()
    data = _encoded(manifest, blobs)
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w") as output:
        output.writestr("manifest.json", canonical_json(manifest.model_dump(mode="json")))
    with pytest.raises(ArchiveError, match="archive_member_set_mismatch"):
        decode_archive(target.getvalue(), expected_source_owner=manifest.source_owner_id)
    with pytest.warns(UserWarning, match="Duplicate name"):
        duplicate = repack(data, extra_name="manifest.json")
    with pytest.raises(ArchiveError, match="duplicate_or_missing_archive_member"):
        decode_archive(duplicate, expected_source_owner=manifest.source_owner_id)


def test_declared_oversize_member_and_unbounded_directory_fail_before_read() -> None:
    manifest, blobs = sample()
    data = bytearray(_encoded(manifest, blobs))
    # Central-directory uncompressed size is checked before opening any member.
    central = data.index(b"PK\x01\x02")
    struct.pack_into("<L", data, central + 24, MAX_BLOB_BYTES + 1)
    struct.pack_into("<L", data, central + 20, MAX_BLOB_BYTES + 1)
    with pytest.raises(ArchiveError, match="archive_member_size_limit"):
        decode_archive(bytes(data), expected_source_owner=manifest.source_owner_id)
    data = bytearray(_encoded(manifest, blobs))
    struct.pack_into("<H", data, len(data) - 12, 65_535)
    with pytest.raises(ArchiveError, match="invalid_portable_directory"):
        decode_archive(bytes(data), expected_source_owner=manifest.source_owner_id)


def test_invalid_nonfinite_json_is_rejected() -> None:
    manifest, blobs = sample()
    wire = manifest.model_dump(mode="json")
    wire["records"]["document_revisions"][0]["body"]["text"] = float("nan")
    raw = json.dumps(wire).encode()
    with pytest.raises(ArchiveError, match="nonfinite_json_number"):
        decode_archive(
            repack(_encoded(manifest, blobs), manifest=raw),
            expected_source_owner=manifest.source_owner_id,
        )


def test_directory_cannot_hide_extra_entries_behind_a_forged_small_count() -> None:
    manifest, blobs = sample()
    data = bytearray(_encoded(manifest, blobs))
    struct.pack_into("<HH", data, len(data) - 14, 1, 1)
    with pytest.raises(ArchiveError, match="invalid_portable_directory"):
        decode_archive(bytes(data), expected_source_owner=manifest.source_owner_id)


def test_noncanonical_integer_is_a_bounded_archive_error() -> None:
    manifest, blobs = sample()
    wire = manifest.model_dump(mode="json")
    wire["records"]["document_revisions"][0]["untrusted_number"] = 2**100
    raw = json.dumps(wire).encode()
    with pytest.raises(ArchiveError, match="invalid_portable_manifest"):
        decode_archive(
            repack(_encoded(manifest, blobs), manifest=raw),
            expected_source_owner=manifest.source_owner_id,
        )


def test_independently_constructed_v1_archive_remains_readable() -> None:
    manifest, blobs = sample()
    legacy = manifest.model_copy(update={"format_version": 1})
    legacy = legacy.model_copy(update={"manifest_sha256": manifest_digest(legacy)})
    output = io.BytesIO()
    # Deliberately do not use encode_archive: these are legacy ZIP32 wire bytes.
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED, allowZip64=False) as archive:
        archive.writestr("manifest.json", canonical_json(legacy.model_dump(mode="json")))
        for blob in legacy.blob_manifest:
            archive.writestr(blob.archive_member, blobs[blob.sha256])
    with decode_archive(output.getvalue(), expected_source_owner=legacy.source_owner_id) as decoded:
        assert decoded.manifest == legacy
        assert dict(decoded.blobs) == blobs
        assert decoded.manifest.records == manifest.records


def test_decoder_hashes_all_blobs_before_returning_non_caching_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest, blobs = sample()
    data = _encoded(manifest, blobs)
    reads: list[str] = []
    original = zipfile.ZipFile.read

    def read(
        archive: zipfile.ZipFile, name: str | zipfile.ZipInfo, pwd: bytes | None = None
    ) -> bytes:
        reads.append(name.filename if isinstance(name, zipfile.ZipInfo) else name)
        return original(archive, name, pwd)

    monkeypatch.setattr(zipfile.ZipFile, "read", read)
    with decode_archive(data, expected_source_owner=manifest.source_owner_id) as decoded:
        assert reads == ["manifest.json"]
        digest = manifest.blob_manifest[0].sha256
        assert decoded.blobs[digest] == blobs[digest]
        assert decoded.blobs[digest] == blobs[digest]
        assert reads == ["manifest.json", f"blobs/{digest}", f"blobs/{digest}"]
    # Hash validation is eager even when no mapping item will ever be requested.
    with pytest.raises(ArchiveError, match="blob_hash_mismatch"):
        decode_archive(
            repack(data, corrupt_blob=True), expected_source_owner=manifest.source_owner_id
        )


def test_encoder_reads_lazy_blob_once_and_never_silently_omits_a_missing_file() -> None:
    manifest, blobs = sample()
    accesses: list[str] = []

    class LazyBlobs(Mapping[str, bytes]):
        def __iter__(self) -> Iterator[str]:
            return iter(blobs)

        def __len__(self) -> int:
            return len(blobs)

        def __getitem__(self, digest: str) -> bytes:
            accesses.append(digest)
            return blobs[digest]

    output = io.BytesIO()
    encode_archive(manifest, LazyBlobs(), output)
    assert accesses == list(blobs)
    assert decode_archive(output, expected_source_owner=manifest.source_owner_id).blobs == blobs

    class MissingFile(LazyBlobs):
        def __getitem__(self, digest: str) -> bytes:
            raise KeyError(digest)

    with pytest.raises(ArchiveError, match="archive_member_set_mismatch"):
        encode_archive(manifest, MissingFile(), io.BytesIO())


def _zip64_directory(data: bytes) -> bytes:
    end = data[-22:]
    _, _, _, _, count, size, offset, _ = struct.unpack("<4s4H2LH", end)
    record_offset = len(data) - 22
    record = struct.pack("<4sQ2H2L4Q", b"PK\x06\x06", 44, 45, 45, 0, 0, count, count, size, offset)
    locator = struct.pack("<4sLQL", b"PK\x06\x07", 0, record_offset, 1)
    return data[:-22] + record + locator + end


def test_zip64_end_directory_is_validated_before_zipfile_allocates_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest, blobs = sample()
    data = _zip64_directory(_encoded(manifest, blobs))
    with decode_archive(data, expected_source_owner=manifest.source_owner_id) as decoded:
        assert decoded.blobs == blobs
    forged = bytearray(data)
    record_offset = len(data) - 22 - 20 - 56
    struct.pack_into("<2Q", forged, record_offset + 24, 10_002, 10_002)
    struct.pack_into("<2H", forged, len(forged) - 14, 10_002, 10_002)

    def forbidden_zipfile(*args: object, **kwargs: object) -> None:
        raise AssertionError("unbounded directory reached ZipFile")

    monkeypatch.setattr(zipfile, "ZipFile", forbidden_zipfile)
    with pytest.raises(ArchiveError, match="invalid_portable_directory"):
        decode_archive(bytes(forged), expected_source_owner=manifest.source_owner_id)


@pytest.mark.parametrize("field", ["zip64_length", "local_size", "local_flags", "local_name"])
def test_zip64_local_header_cannot_disagree_with_central_directory(field: str) -> None:
    manifest, blobs = sample()
    data = bytearray(_encoded(manifest, blobs))
    name_length = struct.unpack_from("<H", data, 26)[0]
    extra_offset = 30 + name_length
    if field == "zip64_length":
        struct.pack_into("<H", data, extra_offset + 2, 8)
    elif field == "local_size":
        struct.pack_into("<Q", data, extra_offset + 4, 1)
    elif field == "local_flags":
        struct.pack_into("<H", data, 6, 1)
    else:
        data[30] = ord("x")
    with pytest.raises(ArchiveError, match="unsafe_archive_member"):
        decode_archive(bytes(data), expected_source_owner=manifest.source_owner_id)


def test_stream_hash_never_requests_unbounded_bytes_and_enforces_its_limit() -> None:
    payload = b"x" * (3 * 1024 * 1024 + 1)

    class BoundedReader(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            assert size is not None and 0 < size <= 1024 * 1024
            return super().read(size)

    source = BoundedReader(payload)
    assert hash_stream(source, limit=len(payload)) == (
        hashlib.sha256(payload).hexdigest(),
        len(payload),
    )
    assert not source.closed
    source.seek(0)
    with pytest.raises(ArchiveError, match="archive_size_limit"):
        hash_stream(source, limit=len(payload) - 1)
