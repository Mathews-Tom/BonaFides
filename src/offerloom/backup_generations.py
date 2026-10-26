# SPDX-License-Identifier: MIT OR Apache-2.0
"""Immutable capture pins and migration-role-only native recovery qualification."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Connection, text

from offerloom.backup_archive import MAX_BUNDLE_BYTES, BackupError, BackupManifest, BundleReceipt
from offerloom.commands import canonical_json
from offerloom.installation_control import Control
from offerloom.storage import directory

if TYPE_CHECKING:
    from offerloom.installation_restore import NativeRecoveryReceipt


def credential_keyring_sha256(active_key_id: str, keys: dict[str, str]) -> str:
    return hashlib.sha256(
        canonical_json({"active_key_id": active_key_id, "keys": keys}).encode()
    ).hexdigest()


def _pins(manifest: BackupManifest) -> dict[str, object]:
    return {
        "namespace_id": str(manifest.source_namespace),
        "maintenance_epoch": manifest.maintenance_epoch,
        "workspace_version": manifest.workspace_version,
        "installation_auth_epoch": manifest.installation_auth_epoch,
        "owner_auth_epoch": manifest.owner_auth_epoch,
        "schema_versions": manifest.schema_versions,
        "image_sha256": manifest.image_sha256,
        "source_sha256": manifest.source_sha256,
        "config_sha256": manifest.config_sha256,
        "recipient_sha256": manifest.recipient_sha256,
        "keyring_sha256": credential_keyring_sha256(
            manifest.credential_active_key_id, manifest.credential_keys
        ),
    }


def register_generation(
    conn: Connection, control: Control, manifest: BackupManifest, receipt: BundleReceipt
) -> None:
    if (manifest.control_id, manifest.source_owner_id) != (control.id, control.owner_id):
        raise BackupError("backup_generation_control_mismatch")
    record = {
        **_pins(manifest),
        "file_name": receipt.file_name,
        "cipher_sha256": receipt.sha256,
        "manifest_sha256": receipt.manifest_sha256,
        "byte_length": receipt.byte_length,
        "captured_at": manifest.created_at.isoformat(),
    }
    conn.execute(
        text(
            "SELECT offerloom.register_installation_backup(:id,:holder,:fence,CAST(:record AS jsonb))"
        ),
        {**control.parameters, "record": canonical_json(record)},
    )


def verify_generation(
    conn: Connection,
    manifest: BackupManifest,
    cipher_sha256: str,
    receipt: NativeRecoveryReceipt,
) -> str:
    """Call only after actual full native recovery, never after archive inspection."""
    if conn.execute(text("SELECT current_user")).scalar_one() != "offerloom_migrator":
        raise BackupError("backup_verification_operator_role_required")
    if (
        receipt.source_namespace != manifest.source_namespace
        or receipt.target_namespace == manifest.source_namespace
        or not receipt.maintenance_active
        or receipt.promoted
        or receipt.tables_verified != len(manifest.table_counts)
        or receipt.files_verified != len(manifest.files) - 1
        or receipt.credentials_verified != manifest.table_counts["encrypted_credentials"]
    ):
        raise BackupError("backup_verification_receipt_mismatch")
    receipt_sha = hashlib.sha256(
        canonical_json(
            {
                "operation_id": str(receipt.operation_id),
                "source_namespace": str(receipt.source_namespace),
                "target_namespace": str(receipt.target_namespace),
                "schema_version": receipt.schema_version,
                "tables_verified": receipt.tables_verified,
                "files_verified": receipt.files_verified,
                "credentials_verified": receipt.credentials_verified,
                "maintenance_active": receipt.maintenance_active,
                "promoted": receipt.promoted,
                "cipher_sha256": cipher_sha256,
            }
        ).encode()
    ).hexdigest()
    record = _pins(manifest)
    record["manifest_sha256"] = hashlib.sha256(
        canonical_json(manifest.model_dump(mode="json")).encode()
    ).hexdigest()
    record["cipher_sha256"] = cipher_sha256
    updated = conn.execute(
        text("""UPDATE offerloom.installation_backups b
        SET restore_verified_at=clock_timestamp(),restore_receipt_sha256=:receipt,
        restore_target_namespace=:target
        WHERE b.id=:id AND b.owner_id=:owner
        AND to_jsonb(b) @> CAST(:pins AS jsonb)
        RETURNING b.id"""),
        {
            "id": manifest.control_id,
            "owner": manifest.source_owner_id,
            "receipt": receipt_sha,
            "target": receipt.target_namespace,
            "pins": canonical_json(record),
        },
    ).scalar_one_or_none()
    if updated is None:
        raise BackupError("backup_generation_identity_mismatch")
    return receipt_sha


def verify_ciphertext(destination: Path, record: dict[str, object]) -> None:
    """Independently reopen and hash the exact complete, stable regular file."""
    name = str(record["file_name"])
    try:
        if name != str(UUID(name.removesuffix(".cms"))) + ".cms":
            raise BackupError("backup_checkpoint_file_invalid")
        length = int(str(record["byte_length"]))
        if not 0 < length <= MAX_BUNDLE_BYTES:
            raise BackupError("backup_checkpoint_size_invalid")
        with directory(destination) as folder:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder)
            try:
                before = os.fstat(fd)
                if not stat.S_ISREG(before.st_mode) or before.st_size != length:
                    raise BackupError("backup_checkpoint_size_mismatch")
                digest = hashlib.sha256()
                remaining = length
                while remaining:
                    chunk = os.read(fd, min(remaining, 1024**2))
                    if not chunk:
                        raise BackupError("backup_checkpoint_size_mismatch")
                    digest.update(chunk)
                    remaining -= len(chunk)
                after = os.fstat(fd)
                if (
                    os.read(fd, 1)
                    or (
                        before.st_dev,
                        before.st_ino,
                        before.st_size,
                        before.st_mtime_ns,
                        before.st_ctime_ns,
                    )
                    != (
                        after.st_dev,
                        after.st_ino,
                        after.st_size,
                        after.st_mtime_ns,
                        after.st_ctime_ns,
                    )
                    or digest.hexdigest() != record["cipher_sha256"]
                ):
                    raise BackupError("backup_checkpoint_cipher_mismatch")
            finally:
                os.close(fd)
    except (OSError, ValueError) as error:
        if isinstance(error, BackupError):
            raise
        raise BackupError("backup_checkpoint_file_unavailable") from error
