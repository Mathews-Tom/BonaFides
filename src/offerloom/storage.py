# SPDX-License-Identifier: MIT OR Apache-2.0
"""Private, bounded, descriptor-relative content-addressed storage.

Bytes are durable before metadata publication. Failed SQL commits deliberately leave
unreferenced immutable objects: deleting them here could race another reference.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

MAX_PDF_BYTES = 64 * 1024 * 1024
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class StoredBlob:
    sha256: str
    byte_length: int
    storage_key: str


@contextmanager
def directory(path: Path, *, create: bool = False) -> Iterator[int]:
    """Open every component without following even an intermediate symlink."""
    absolute = path.absolute()
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in absolute.parts[1:]:
            if component in (".", ".."):
                raise ValueError("invalid_storage_root")
            if create:
                try:
                    os.mkdir(component, 0o700, dir_fd=fd)
                    os.fsync(fd)
                except FileExistsError:
                    pass
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


@contextmanager
def subdirectory(parent: int, name: str, *, create: bool = False) -> Iterator[int]:
    if not name or "/" in name or name in (".", ".."):
        raise ValueError("invalid_storage_key")
    if create:
        try:
            os.mkdir(name, 0o700, dir_fd=parent)
            os.fsync(parent)
        except FileExistsError:
            pass
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        yield fd
    finally:
        os.close(fd)


def read_file(fd: int, name: str, limit: int) -> bytes:
    if not name or "/" in name or name in (".", ".."):
        raise ValueError("invalid_storage_key")
    source = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    try:
        info = os.fstat(source)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("invalid_stored_file")
        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(source, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        result = b"".join(chunks)
        if len(result) > limit:
            raise ValueError("stored_file_too_large")
        return result
    finally:
        os.close(source)


def atomic_file(fd: int, name: str, data: bytes) -> None:
    if "/" in name or name in (".", ".."):
        raise ValueError("invalid_storage_key")
    temporary = f".staging-{uuid4().hex}"
    target = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd
    )
    try:
        view = memoryview(data)
        while view:
            written = os.write(target, view)
            if written <= 0:
                raise OSError("short_storage_write")
            view = view[written:]
        os.fsync(target)
        os.close(target)
        target = -1
        # rename replaces a symlink itself; it never follows its target.
        os.rename(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
    finally:
        if target >= 0:
            os.close(target)
        with suppress(FileNotFoundError):
            os.unlink(temporary, dir_fd=fd)


class BlobStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def put(self, owner_id: UUID, data: bytes) -> StoredBlob:
        if not data.startswith(b"%PDF-") or not 0 < len(data) <= MAX_PDF_BYTES:
            raise ValueError("invalid_pdf_output")
        return self.put_bytes(owner_id, data)

    def put_bytes(self, owner_id: UUID, data: bytes) -> StoredBlob:
        if not 0 < len(data) <= MAX_PDF_BYTES:
            raise ValueError("invalid_blob_size")
        digest = hashlib.sha256(data).hexdigest()
        with (
            directory(self.root, create=True) as root,
            subdirectory(root, str(owner_id), create=True) as owner,
            subdirectory(owner, digest[:2], create=True) as bucket,
        ):
            try:
                existing = read_file(bucket, digest, MAX_PDF_BYTES)
            except FileNotFoundError:
                atomic_file(bucket, digest, data)
            else:
                if hashlib.sha256(existing).hexdigest() != digest:
                    raise ValueError("blob_integrity_failed")
        return StoredBlob(digest, len(data), f"{digest[:2]}/{digest}")

    def read(self, owner_id: UUID, storage_key: str, digest: str, size: int) -> bytes:
        if not _DIGEST.fullmatch(digest) or storage_key != f"{digest[:2]}/{digest}":
            raise ValueError("invalid_storage_key")
        if not 0 < size <= MAX_PDF_BYTES:
            raise ValueError("invalid_blob_size")
        with (
            directory(self.root) as root,
            subdirectory(root, str(owner_id)) as owner,
            subdirectory(owner, digest[:2]) as bucket,
        ):
            data = read_file(bucket, digest, MAX_PDF_BYTES)
        if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("blob_integrity_failed")
        return data
