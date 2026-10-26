# SPDX-License-Identifier: MIT OR Apache-2.0
"""Immutable capture pins and migration-role-only native recovery qualification."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from sqlalchemy import Connection, text

from offerloom.backup_archive import BackupError, BackupManifest, BundleReceipt
from offerloom.commands import canonical_json
from offerloom.installation_control import Control

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
