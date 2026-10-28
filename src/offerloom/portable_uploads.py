# SPDX-License-Identifier: MIT OR Apache-2.0
"""Private transient streams and bounded PostgreSQL portable-preview chunks."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from typing import BinaryIO, cast
from uuid import UUID, uuid4

from sqlalchemy import Connection, text

from offerloom.config import Settings
from offerloom.portable_archive import MAX_ARCHIVE_BYTES, ArchiveError
from offerloom.storage import directory

UPLOAD_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class CandidateUpload:
    stream: BinaryIO
    byte_length: int
    sha256: str


@contextmanager
def temporary_archive(settings: Settings) -> Iterator[BinaryIO]:
    # An unlinked file on the data volume, not the service's small tmpfs.
    # No pathname survives normal exit or process death; admission owns its I/O.
    with directory(settings.blob_root) as root:
        name = f".portable-upload-{uuid4().hex}"
        descriptor = os.open(
            name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=root
        )
        try:
            with os.fdopen(descriptor, "w+b") as stream:
                os.unlink(name, dir_fd=root)
                yield cast(BinaryIO, stream)
        finally:
            with suppress(FileNotFoundError):
                os.unlink(name, dir_fd=root)


def retain_candidate(conn: Connection, owner: UUID, preview: UUID, stream: BinaryIO) -> None:
    """The preview row already owns the first chunk; retain the remainder in order."""
    stream.seek(UPLOAD_CHUNK_BYTES)
    number = 1
    while chunk := stream.read(UPLOAD_CHUNK_BYTES):
        conn.execute(
            text("""INSERT INTO offerloom.portable_preview_chunks
            (owner_id,preview_id,chunk_no,payload) VALUES(:owner,:preview,:number,:payload)"""),
            {"owner": owner, "preview": preview, "number": number, "payload": chunk},
        )
        number += 1


def load_candidate(conn: Connection, owner: UUID, workflow: UUID, stream: BinaryIO) -> None:
    """Reconstruct one exact retained upload without materializing it in memory."""
    row = (
        conn.execute(
            text("""SELECT id,candidate_bytes,(plan->>'byte_length')::bigint AS byte_length
        FROM offerloom.portable_previews WHERE owner_id=:owner AND workflow_run_id=:workflow
        AND cleared_at IS NULL"""),
            {"owner": owner, "workflow": workflow},
        )
        .mappings()
        .one_or_none()
    )
    if row is None or row["candidate_bytes"] is None:
        raise ArchiveError("portable_preview_unavailable")
    first = bytes(row["candidate_bytes"])
    total = len(first)
    expected = row["byte_length"]
    if not 0 < expected <= MAX_ARCHIVE_BYTES or total > expected:
        raise ArchiveError("portable_candidate_size_mismatch")
    stream.write(first)
    number = 1
    rows = conn.execute(
        text("""SELECT chunk_no,payload FROM offerloom.portable_preview_chunks
        WHERE owner_id=:owner AND preview_id=:preview ORDER BY chunk_no""").execution_options(
            stream_results=True, yield_per=1
        ),
        {"owner": owner, "preview": row["id"]},
    )
    try:
        for chunk_no, payload in rows:
            if chunk_no != number or total % UPLOAD_CHUNK_BYTES:
                raise ArchiveError("portable_candidate_chunk_mismatch")
            chunk = bytes(payload)
            total += len(chunk)
            if not 0 < len(chunk) <= UPLOAD_CHUNK_BYTES or total > expected:
                raise ArchiveError("portable_candidate_size_mismatch")
            stream.write(chunk)
            number += 1
    finally:
        rows.close()
    if total != expected:
        raise ArchiveError("portable_candidate_size_mismatch")
    stream.seek(0)
