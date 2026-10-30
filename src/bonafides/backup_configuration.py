# SPDX-License-Identifier: MIT OR Apache-2.0
"""Bind replacement eligibility to protected configuration and actual ciphertext."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import ValidationError
from sqlalchemy import Connection, text

from bonafides.auth import Owner, require_readonly_owner
from bonafides.backup_archive import BackupError, recipient_certificate
from bonafides.backup_generations import credential_keyring_sha256, verify_ciphertext
from bonafides.backup_operator import CaptureConfiguration, configuration_sha256, source_sha256
from bonafides.config import ConfigurationError, Settings, read_secret
from bonafides.db import Database
from bonafides.installation_backup import schema_sources
from bonafides.problems import Problem


@dataclass(frozen=True)
class VerifiedCheckpoint:
    generation_id: UUID
    runtime_pins: dict[str, str]
    schema_versions: dict[str, str]


def capture_configuration(settings: Settings) -> CaptureConfiguration:
    path = settings.backup_capture_config_file
    if path is None:
        raise BackupError("backup_not_configured")
    if not path.is_absolute() or ".." in path.parts:
        raise ConfigurationError("Invalid backup_capture_config_file")
    try:
        return CaptureConfiguration.model_validate_json(read_secret(path, "backup_capture_config"))
    except ValidationError as error:
        raise ConfigurationError("Invalid backup_capture_config") from error


def capture_runtime_pins(config: CaptureConfiguration, settings: Settings) -> dict[str, str]:
    with recipient_certificate(config.recipient) as (_, fingerprint):
        schema_sources(config.migrations / "sql")
    return {
        "image_sha256": config.image_sha256,
        "source_sha256": source_sha256(),
        "config_sha256": configuration_sha256(config),
        "recipient_sha256": fingerprint,
        "keyring_sha256": credential_keyring_sha256(
            settings.keyring.active_key_id,
            {
                key: hashlib.sha256(value).hexdigest()
                for key, value in settings.keyring.keys.items()
            },
        ),
    }


def _candidate(
    conn: Connection,
    owner_id: UUID,
    pins: dict[str, str],
    versions: dict[str, str],
    generation_id: UUID | None,
) -> dict[str, object]:
    from bonafides.commands import canonical_json

    row = (
        conn.execute(
            text("""SELECT b.id,b.file_name,b.cipher_sha256,b.byte_length
        FROM bonafides.installation_backups b
        JOIN bonafides.owners o ON o.id=b.owner_id
        JOIN bonafides.owner_settings s ON s.owner_id=o.id
        CROSS JOIN bonafides.installation_state i
        WHERE i.singleton AND b.owner_id=:owner
        AND (CAST(:id AS uuid) IS NULL OR b.id=CAST(:id AS uuid))
        AND b.namespace_id=i.namespace_id AND b.maintenance_epoch=i.maintenance_epoch
        AND b.workspace_version=s.workspace_version
        AND b.owner_auth_epoch=o.auth_epoch AND b.installation_auth_epoch=i.auth_epoch
        AND b.pruned_at IS NULL
        AND b.restore_verified_at IS NOT NULL AND b.restore_receipt_sha256 IS NOT NULL
        AND b.schema_versions=CAST(:versions AS jsonb)
        AND b.schema_versions=(SELECT jsonb_object_agg(version::text,source_sha256)
                               FROM bonafides.schema_migrations)
        AND to_jsonb(b) @> CAST(:pins AS jsonb)
        ORDER BY b.captured_at DESC,b.id LIMIT 1"""),
            {
                "owner": owner_id,
                "id": generation_id,
                "pins": canonical_json(pins),
                "versions": canonical_json(versions),
            },
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise BackupError("fresh_verified_checkpoint_required")
    return dict(row)


def verified_checkpoint(
    db: Database,
    settings: Settings,
    owner_id: UUID,
    generation_id: UUID | None = None,
) -> VerifiedCheckpoint:
    """Do not hold SQL/controller locks during bounded ciphertext I/O.

    A preview and its executor each call this. The destructive transaction still
    invokes assert_portable_checkpoint after admission changes the epoch.
    """
    config = capture_configuration(settings)
    pins = capture_runtime_pins(config, settings)
    versions = schema_sources(config.migrations / "sql")
    with db.transaction() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        row = _candidate(conn, owner_id, pins, versions, generation_id)
    verify_ciphertext(config.destination, row)
    if capture_configuration(settings) != config or capture_runtime_pins(config, settings) != pins:
        raise BackupError("backup_configuration_changed")
    selected = UUID(str(row["id"]))
    with db.transaction() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        if _candidate(conn, owner_id, pins, versions, selected) != row:
            raise BackupError("backup_checkpoint_changed")
    return VerifiedCheckpoint(
        selected, {**pins, "cipher_sha256": str(row["cipher_sha256"])}, versions
    )


def recheck_verified_checkpoint(
    conn: Connection, owner_id: UUID, checkpoint: VerifiedCheckpoint
) -> None:
    """Close the SQL race after outside-transaction file verification."""
    _candidate(
        conn,
        owner_id,
        checkpoint.runtime_pins,
        checkpoint.schema_versions,
        checkpoint.generation_id,
    )


def build_router(db: Database, settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/v1/installation-backups")

    @router.get("")
    def status(owner: Annotated[Owner, Depends(require_readonly_owner)]) -> dict[str, object]:
        from bonafides.backup_schedule import backup_status

        config = None
        checkpoint = None
        reason = None
        try:
            if settings.backup_capture_config_file is not None:
                config = capture_configuration(settings)
                checkpoint = str(verified_checkpoint(db, settings, owner.id).generation_id)
            else:
                reason = "backup_not_configured"
        except ConfigurationError as error:
            raise Problem(503, "backup_configuration_invalid") from error
        except BackupError as error:
            reason = str(error)
        return {
            **backup_status(db, config, owner.id),
            "eligible_checkpoint_id": checkpoint,
            "eligibility_reason": reason,
        }

    return router
