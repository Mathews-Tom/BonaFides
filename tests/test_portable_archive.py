# SPDX-License-Identifier: MIT OR Apache-2.0
from __future__ import annotations

import hashlib
import io
import json
import stat
import struct
import zipfile
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
    manifest_digest,
)


def sample() -> tuple[Manifest, dict[str, bytes]]:
    payload = b"Synthetic private attachment\n"
    digest = hashlib.sha256(payload).hexdigest()
    manifest = Manifest(
        format="offerloom-portable",
        format_version=1,
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
    decoded = decode_archive(
        encode_archive(manifest, blobs), expected_source_owner=manifest.source_owner_id
    )
    assert decoded.manifest == manifest
    assert decoded.blobs == blobs
    assert decoded.manifest.records["document_revisions"][0]["validation_issues"] == ["unsupported"]


def test_source_owner_requires_exact_independent_identity() -> None:
    manifest, blobs = sample()
    data = encode_archive(manifest, blobs)
    with pytest.raises(ArchiveError, match="portable_owner_mismatch"):
        decode_archive(data, expected_source_owner=uuid4())
    assert decode_archive(data, expected_source_owner=manifest.source_owner_id).blobs == blobs


@pytest.mark.parametrize(
    "name", ["../outside", "/absolute", "blobs/../../escape", "blobs\\escape", "extra.json"]
)
def test_unlisted_and_traversing_members_are_rejected_without_extraction(name: str) -> None:
    manifest, blobs = sample()
    data = repack(encode_archive(manifest, blobs), extra_name=name)
    with pytest.raises(ArchiveError, match="unsafe_archive_member"):
        decode_archive(data, expected_source_owner=manifest.source_owner_id)


@pytest.mark.parametrize("mode", [stat.S_IFLNK, stat.S_IFIFO, stat.S_IFDIR, stat.S_IFSOCK])
def test_nonregular_member_types_are_rejected(mode: int) -> None:
    manifest, blobs = sample()
    data = repack(encode_archive(manifest, blobs), mode=mode)
    with pytest.raises(ArchiveError, match="unsafe_archive_member"):
        decode_archive(data, expected_source_owner=manifest.source_owner_id)


def test_compressed_zip_bomb_is_rejected_before_decompression() -> None:
    manifest, blobs = sample()
    data = repack(encode_archive(manifest, blobs), compression=zipfile.ZIP_DEFLATED)
    with pytest.raises(ArchiveError, match="unsafe_archive_member"):
        decode_archive(data, expected_source_owner=manifest.source_owner_id)


@pytest.mark.parametrize(
    "field,value",
    [
        ("format_version", 2),
        ("format_version", True),
        ("format", "another-product"),
        ("secret", "not-allowed"),
    ],
)
def test_incompatible_and_unknown_manifest_fields_fail(field: str, value: object) -> None:
    manifest, blobs = sample()
    wire = manifest.model_dump(mode="json")
    wire[field] = value
    data = repack(encode_archive(manifest, blobs), manifest=canonical_json(wire).encode())
    with pytest.raises(ArchiveError):
        decode_archive(data, expected_source_owner=manifest.source_owner_id)


def test_hash_mismatch_and_same_size_blob_corruption_fail() -> None:
    manifest, blobs = sample()
    data = encode_archive(manifest, blobs)
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
            repack(encode_archive(manifest, blobs), manifest=duplicate),
            expected_source_owner=manifest.source_owner_id,
        )


def test_missing_or_duplicate_member_is_rejected() -> None:
    manifest, blobs = sample()
    data = encode_archive(manifest, blobs)
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
    data = bytearray(encode_archive(manifest, blobs))
    # Central-directory uncompressed size is checked before opening any member.
    central = data.index(b"PK\x01\x02")
    struct.pack_into("<L", data, central + 24, MAX_BLOB_BYTES + 1)
    struct.pack_into("<L", data, central + 20, MAX_BLOB_BYTES + 1)
    with pytest.raises(ArchiveError, match="archive_member_size_limit"):
        decode_archive(bytes(data), expected_source_owner=manifest.source_owner_id)
    data = bytearray(encode_archive(manifest, blobs))
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
            repack(encode_archive(manifest, blobs), manifest=raw),
            expected_source_owner=manifest.source_owner_id,
        )


def test_directory_cannot_hide_extra_entries_behind_a_forged_small_count() -> None:
    manifest, blobs = sample()
    data = bytearray(encode_archive(manifest, blobs))
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
            repack(encode_archive(manifest, blobs), manifest=raw),
            expected_source_owner=manifest.source_owner_id,
        )
