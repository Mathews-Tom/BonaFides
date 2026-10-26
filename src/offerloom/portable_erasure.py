# SPDX-License-Identifier: MIT OR Apache-2.0
"""Scoped SQL erasure and journaled filesystem completion under a closed barrier.

SQL erasure and replacement publication share their caller's transaction. Files
are removed afterward; a crash or filesystem failure leaves admission closed and
an exact remaining journal. No heartbeat row lock is held through filesystem I/O.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from contextlib import ExitStack
from pathlib import Path
from typing import Literal, Protocol, TypedDict, cast
from uuid import UUID

from sqlalchemy import Connection, text

from offerloom.commands import canonical_json
from offerloom.config import Settings
from offerloom.db import Database
from offerloom.installation_control import Control, assert_control
from offerloom.portable_archive import MAX_ARCHIVE_BYTES, ArchiveError
from offerloom.profiles import create_empty_profile
from offerloom.storage import MAX_PDF_BYTES, directory, subdirectory

_MAX_ITEMS = 100000
_BUCKET = re.compile(r"[0-9a-f]{2}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_STAGING = re.compile(r"\.staging-[0-9a-f]{32}\Z")
_COPY = re.compile(r"[0-9a-f-]{36}\.zip\Z")


class PurgeStorage(Protocol):
    @property
    def blob_root(self) -> Path: ...


class PurgeFile(TypedDict):
    kind: Literal["cas", "cas_staging", "export", "candidate"]
    storage_key: str
    sha256: str
    byte_length: int


def _digest_file(folder: int, name: str, limit: int) -> tuple[str, int]:
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ArchiveError("purge_file_invalid")
        digest = hashlib.sha256()
        size = 0
        while chunk := os.read(descriptor, 1024 * 1024):
            size += len(chunk)
            if size > limit:
                raise ArchiveError("purge_file_too_large")
            digest.update(chunk)
        after = os.fstat(descriptor)
        if size != before.st_size or (before.st_size, before.st_mtime_ns) != (
            after.st_size,
            after.st_mtime_ns,
        ):
            raise ArchiveError("purge_file_changed")
        return digest.hexdigest(), size
    finally:
        os.close(descriptor)


def orphan_inventory(db: Database, settings: Settings, control: Control) -> list[PurgeFile]:
    """Include interrupted CAS writes; never collect a surviving live reference."""
    with db.transaction() as conn:
        assert_control(conn, control)
        live = set(
            conn.execute(
                text("SELECT storage_key FROM offerloom.blobs WHERE owner_id=:owner"),
                {"owner": control.owner_id},
            ).scalars()
        )
    result: list[PurgeFile] = []
    with ExitStack() as stack:
        root = stack.enter_context(directory(settings.blob_root, create=True))
        try:
            owner = stack.enter_context(subdirectory(root, str(control.owner_id)))
        except FileNotFoundError:
            return result
        buckets = os.listdir(owner)
        if len(buckets) > 256:
            raise ArchiveError("purge_inventory_invalid")
        scanned = 0
        for bucket_name in sorted(buckets):
            if not _BUCKET.fullmatch(bucket_name):
                raise ArchiveError("purge_inventory_invalid")
            with subdirectory(owner, bucket_name) as bucket:
                names = os.listdir(bucket)
                scanned += len(names)
                if scanned > _MAX_ITEMS:
                    raise ArchiveError("purge_inventory_too_large")
                for name in sorted(names):
                    key = f"{bucket_name}/{name}"
                    staging = _STAGING.fullmatch(name) is not None
                    if not staging and (not _DIGEST.fullmatch(name) or name[:2] != bucket_name):
                        raise ArchiveError("purge_inventory_invalid")
                    if key in live:
                        continue
                    digest, size = _digest_file(bucket, name, MAX_PDF_BYTES)
                    if not staging and digest != name:
                        raise ArchiveError("purge_file_hash_mismatch")
                    result.append(
                        {
                            "kind": "cas_staging" if staging else "cas",
                            "storage_key": key,
                            "sha256": digest,
                            "byte_length": size,
                        }
                    )
    return result


def assert_checkpoint(
    conn: Connection, control: Control, checkpoint_id: UUID, runtime_pins: dict[str, str]
) -> None:
    conn.execute(
        text("""SELECT offerloom.assert_portable_checkpoint(
        :id,:holder,:fence,:checkpoint,CAST(:pins AS jsonb))"""),
        {
            **control.parameters,
            "checkpoint": checkpoint_id,
            "pins": canonical_json(runtime_pins),
        },
    )


def erase_scope(
    conn: Connection,
    control: Control,
    orphans: list[PurgeFile],
    *,
    checkpoint_id: UUID | None = None,
    runtime_pins: dict[str, str] | None = None,
) -> dict[str, object]:
    """Call in the SAME transaction as any replacement inserts, never before it."""
    result = cast(
        dict[str, object],
        conn.execute(
            text("""SELECT offerloom.erase_portable_scope(
            :id,:holder,:fence,:checkpoint,CAST(:pins AS jsonb),CAST(:orphans AS jsonb))"""),
            {
                **control.parameters,
                "checkpoint": checkpoint_id,
                "pins": canonical_json(runtime_pins),
                "orphans": canonical_json(orphans),
            },
        ).scalar_one(),
    )
    if control.purpose == "privacy_delete" and result["scope"] == "workspace":
        # Initialize the SQL erasure's empty profile before this transaction commits.
        profile_id = conn.execute(
            text("""SELECT id FROM offerloom.profiles
            WHERE owner_id=:owner AND current_revision_id IS NULL"""),
            {"owner": control.owner_id},
        ).scalar_one()
        create_empty_profile(conn, control.owner_id, profile_id=profile_id)
    return result


def _remove_file(settings: PurgeStorage, owner_id: UUID, item: PurgeFile) -> None:
    kind, key = item["kind"], item["storage_key"]
    if kind in ("cas", "cas_staging"):
        parts = key.split("/")
        if len(parts) != 2 or not _BUCKET.fullmatch(parts[0]):
            raise ArchiveError("purge_file_invalid")
        if kind == "cas" and key != f"{item['sha256'][:2]}/{item['sha256']}":
            raise ArchiveError("purge_file_invalid")
        if kind == "cas_staging" and not _STAGING.fullmatch(parts[1]):
            raise ArchiveError("purge_file_invalid")
        root_path = settings.blob_root
        components = (str(owner_id), parts[0])
        name = parts[1]
        limit = MAX_PDF_BYTES
    else:
        if kind not in ("export", "candidate") or not _COPY.fullmatch(key):
            raise ArchiveError("purge_file_invalid")
        root_path = settings.blob_root
        components = (
            "portable_exports" if kind == "export" else "portable_candidates",
            str(owner_id),
        )
        name = key
        limit = MAX_ARCHIVE_BYTES
    with ExitStack() as stack:
        folder = stack.enter_context(directory(root_path))
        for component in components:
            try:
                child = stack.enter_context(subdirectory(folder, component))
            except FileNotFoundError:
                os.fsync(folder)
                return
            folder = child
        try:
            digest, size = _digest_file(folder, name, limit)
        except FileNotFoundError:
            os.fsync(folder)
            return
        if digest != item["sha256"] or size != item["byte_length"]:
            raise ArchiveError("purge_file_hash_mismatch")
        os.unlink(name, dir_fd=folder)
        os.fsync(folder)


def purge_scope(db: Database, settings: PurgeStorage, control: Control) -> int:
    """Resume only the current fenced controller's exact remaining journal."""
    with db.transaction() as conn:
        assert_control(conn, control)
        items = (
            conn.execute(
                text("""SELECT kind,storage_key,sha256,byte_length FROM offerloom.portable_purge_items
            WHERE control_id=:id AND resolved_at IS NULL ORDER BY kind,storage_key"""),
                {"id": control.id},
            )
            .mappings()
            .all()
        )
    for row in items:
        item = cast(PurgeFile, dict(row))
        with db.transaction() as conn:
            assert_control(conn, control)
            retained = (
                item["kind"] == "cas"
                and conn.execute(
                    text("""SELECT EXISTS(SELECT 1 FROM offerloom.blobs
                WHERE owner_id=:owner AND storage_key=:key)"""),
                    {"owner": control.owner_id, "key": item["storage_key"]},
                ).scalar_one()
            )
        if not retained:
            _remove_file(settings, control.owner_id, item)
        with db.transaction() as conn:
            conn.execute(
                text("""SELECT offerloom.resolve_portable_purge_item(
                :id,:holder,:fence,:kind,:key,:outcome)"""),
                {
                    **control.parameters,
                    "kind": item["kind"],
                    "key": item["storage_key"],
                    "outcome": "retained" if retained else "removed",
                },
            )
    with db.transaction() as conn:
        conn.execute(
            text("SELECT offerloom.complete_portable_purge(:id,:holder,:fence)"),
            control.parameters,
        )
    return len(items)


def delete_controlled_owner_data(
    db: Database, settings: Settings, control: Control
) -> dict[str, object]:
    inventory = orphan_inventory(db, settings, control)
    with db.transaction() as conn:
        result = erase_scope(conn, control, inventory)
    purge_scope(db, settings, control)
    return {**result, "filesystem_complete": True}


def abort_staged_files(db: Database, settings: PurgeStorage, control: Control) -> None:
    """Remove only positively owned precommit files, retaining all old live CAS."""
    guard = db.execution_guard
    if guard is None or not guard.exclusive:
        raise ArchiveError("offline_recovery_exclusion_required")
    guard.verify()
    with db.transaction() as conn:
        assert_control(conn, control)
        if conn.execute(
            text("SELECT EXISTS(SELECT 1 FROM offerloom.portable_purges WHERE control_id=:id)"),
            {"id": control.id},
        ).scalar_one():
            raise ArchiveError("committed_purge_must_resume")
        rows = (
            conn.execute(
                text("""SELECT s.storage_key,s.staging_name,s.sha256,s.byte_length,
            EXISTS(SELECT 1 FROM offerloom.blobs b WHERE b.owner_id=:owner
             AND b.storage_key=s.storage_key) AS live
            FROM offerloom.portable_staged_files s WHERE control_id=:id"""),
                {"id": control.id, "owner": control.owner_id},
            )
            .mappings()
            .all()
        )
        export = (
            conn.execute(
                text("""SELECT archive_sha256,byte_length FROM offerloom.portable_exports
            WHERE owner_id=:owner AND workflow_run_id=:id"""),
                {"id": control.id, "owner": control.owner_id},
            )
            .mappings()
            .one_or_none()
        )
    for row in rows:
        if not row["live"]:
            _remove_file(
                settings,
                control.owner_id,
                {
                    "kind": "cas",
                    "storage_key": row["storage_key"],
                    "sha256": row["sha256"],
                    "byte_length": row["byte_length"],
                },
            )
        _remove_owned_partial(
            settings.blob_root,
            (str(control.owner_id), row["sha256"][:2]),
            row["staging_name"],
            row["byte_length"],
        )
    if export is not None:
        _remove_file(
            settings,
            control.owner_id,
            {
                "kind": "export",
                "storage_key": str(control.id) + ".zip",
                "sha256": export["archive_sha256"],
                "byte_length": export["byte_length"],
            },
        )
        _remove_owned_partial(
            settings.blob_root,
            ("portable_exports", str(control.owner_id)),
            ".staging-" + control.id.hex,
            export["byte_length"],
        )


def _remove_owned_partial(root: Path, components: tuple[str, ...], name: str, limit: int) -> None:
    if not _STAGING.fullmatch(name):
        raise ArchiveError("purge_file_invalid")
    with ExitStack() as stack:
        folder = stack.enter_context(directory(root))
        for component in components:
            try:
                folder = stack.enter_context(subdirectory(folder, component))
            except FileNotFoundError:
                return
        try:
            info = os.stat(name, dir_fd=folder, follow_symlinks=False)
        except FileNotFoundError:
            return
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
            raise ArchiveError("purge_file_invalid")
        os.unlink(name, dir_fd=folder)
        os.fsync(folder)
