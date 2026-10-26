# SPDX-License-Identifier: MIT OR Apache-2.0
"""Local operator entrypoints; online capture never loads offline recovery keys."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from offerloom.backup_archive import BackupError, Digest, recipient_certificate, recover_bundle
from offerloom.backup_generations import verify_generation
from offerloom.commands import canonical_json
from offerloom.config import MigrationSettings, Settings, read_secret
from offerloom.crypto import Keyring
from offerloom.db import Database
from offerloom.execution_guard import ExecutionGuard
from offerloom.installation_backup import capture_installation, schema_sources
from offerloom.installation_control import InstallationMaintenance, begin_control
from offerloom.installation_restore import restore_installation
from offerloom.storage import directory
from offerloom.workflows import claim_control, enqueue


class _Configuration(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    @model_validator(mode="after")
    def absolute_paths(self) -> Self:
        if any(
            isinstance(value, Path) and (not value.is_absolute() or ".." in value.parts)
            for value in self.__dict__.values()
        ):
            raise ValueError("Backup configuration requires absolute normalized paths")
        return self


class CaptureConfiguration(_Configuration):
    recipient: Path
    destination: Path
    staging_root: Path
    migrations: Path
    image_sha256: Digest


class RecoveryConfiguration(_Configuration):
    archive: Path
    trusted_sha256: Digest
    private_key: Path
    credential_keyring_file: Path
    target_migration_dsn_file: Path
    target_blob_root: Path
    staging_root: Path
    migrations: Path
    image_sha256: Digest
    source_migration_dsn_file: Path | None = None
    source_blob_root: Path | None = None


def source_sha256() -> str:
    root = Path(__file__).resolve().parent
    sources: dict[str, str] = {}
    for path in sorted(root.rglob("*.py")):
        if path.is_symlink() or not path.is_file():
            raise BackupError("backup_source_identity_invalid")
        sources[path.relative_to(root.parent).as_posix()] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    return hashlib.sha256(canonical_json(sources).encode()).hexdigest()


def configuration_sha256(config: CaptureConfiguration) -> str:
    return hashlib.sha256(canonical_json(config.model_dump(mode="json")).encode()).hexdigest()


def _migration_database(path: Path) -> Database:
    config = MigrationSettings(read_secret(path, "backup_migration_dsn"))
    if config.migration_dsn is None:
        raise BackupError("backup_migration_dsn_required")
    db = Database(config.migration_dsn)
    try:
        with db.transaction() as conn:
            if conn.execute(text("SELECT current_user")).scalar_one() != "offerloom_migrator":
                raise BackupError("backup_verification_operator_role_required")
    except Exception:
        db.close()
        raise
    return db


def _capture(config: CaptureConfiguration) -> dict[str, object]:
    settings = Settings.load()
    with recipient_certificate(config.recipient):
        schema_sources(config.migrations / "sql")
    with directory(config.staging_root, create=True) as staging:
        if stat.S_IMODE(os.fstat(staging).st_mode) & 0o077:
            raise BackupError("backup_staging_not_private")
    source_sha = source_sha256()
    config_sha = configuration_sha256(config)
    db = Database(settings)
    try:
        with db.transaction() as conn:
            owner = conn.execute(text("SELECT id FROM offerloom.owners")).scalar_one_or_none()
            if owner is None:
                raise BackupError("backup_owner_required")
            # Enqueue, claim this exact job, and commit admission's barrier in one
            # transaction. A running lifecycle service cannot steal this command.
            identifier = enqueue(conn, owner, "backup", {"config_sha256": config_sha})
            task = claim_control(conn, uuid4(), workflow_id=identifier)
            if task is None:
                raise BackupError("backup_claim_unavailable")
            control = begin_control(conn, task)
        maintenance = InstallationMaintenance(
            db, control, settings.import_spool, settings.renderer_spool
        )
        with maintenance.quiescent():
            receipt = capture_installation(
                db,
                settings,
                control,
                sql_root=config.migrations / "sql",
                recipient=config.recipient,
                staging_root=config.staging_root,
                destination=config.destination,
                image_sha256=config.image_sha256,
                source_sha256=source_sha,
                config_sha256=config_sha,
            )
            result: dict[str, object] = {
                "generation_id": str(control.id),
                "file_name": receipt.file_name,
                "cipher_sha256": receipt.sha256,
                "byte_length": receipt.byte_length,
                "restore_verified": False,
            }
            maintenance.finish(result)
            return result
    finally:
        db.close()


def _status(config: CaptureConfiguration) -> dict[str, object]:
    with recipient_certificate(config.recipient) as (_, fingerprint):
        schema_sources(config.migrations / "sql")
    db = Database(Settings.load())
    try:
        with db.transaction() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            rows = (
                conn.execute(
                    text("""SELECT b.id,b.cipher_sha256,b.byte_length,b.captured_at,b.restore_verified_at,
                b.restore_receipt_sha256,b.namespace_id=i.namespace_id AS current_installation
                FROM offerloom.installation_backups b CROSS JOIN offerloom.installation_state i
                WHERE i.singleton AND b.config_sha256=:config
                ORDER BY b.captured_at DESC,b.id LIMIT 30"""),
                    {"config": configuration_sha256(config)},
                )
                .mappings()
                .all()
            )
        return {
            "configuration_present": True,
            "recipient_sha256": fingerprint,
            "key_custody": "not_attested",
            "offhost_copy": "not_verified",
            "generations": [dict(row) for row in rows],
        }
    finally:
        db.close()


def _recover_impl(
    command: str,
    config: RecoveryConfiguration,
    source_guard: ExecutionGuard | None,
) -> dict[str, object]:
    source = (
        _migration_database(config.source_migration_dsn_file)
        if command == "backup-verify" and config.source_migration_dsn_file is not None
        else None
    )
    try:
        if source is not None:
            if source_guard is None:
                raise BackupError("backup_verification_source_guard_required")
            source.execution_guard = source_guard
            with source.transaction() as conn:
                if (
                    conn.execute(
                        text(
                            "SELECT execution_guard_id FROM offerloom.installation_state WHERE singleton"
                        )
                    ).scalar_one()
                    != source_guard.identity
                ):
                    raise BackupError("backup_verification_source_guard_mismatch")
        keyring = Keyring.parse(
            read_secret(config.credential_keyring_file, "backup_credential_keyring")
        )
        target = _migration_database(config.target_migration_dsn_file)
        try:
            with recover_bundle(
                source=config.archive,
                trusted_sha256=config.trusted_sha256,
                private_key=config.private_key,
                staging_root=config.staging_root,
                expected_schema=schema_sources(config.migrations / "sql"),
                expected_image_sha256=config.image_sha256,
                execution_fds=(source_guard.fd,) if source_guard is not None else (),
            ) as bundle:
                receipt = restore_installation(
                    target,
                    bundle,
                    keyring,
                    migrations=config.migrations,
                    blob_root=config.target_blob_root,
                    expected_source_sha256=source_sha256(),
                )
                result: dict[str, object] = asdict(receipt)
                result["cipher_sha256"] = config.trusted_sha256
                result["source_generation_verified"] = False
                if source is not None:
                    with source.transaction() as conn:
                        result["restore_receipt_sha256"] = verify_generation(
                            conn, bundle.manifest, config.trusted_sha256, receipt
                        )
                    result["source_generation_verified"] = True
                return result
        finally:
            target.close()
    finally:
        if source is not None:
            source.close()


def _recover(command: str, config: RecoveryConfiguration) -> dict[str, object]:
    if command == "backup-verify":
        if config.source_migration_dsn_file is None or config.source_blob_root is None:
            raise BackupError("backup_verification_source_database_and_blob_root_required")
        with ExecutionGuard(config.source_blob_root, create=False) as guard:
            return _recover_impl(command, config, guard)
    return _recover_impl(command, config, None)


def run(command: str, path: Path) -> int:
    """A protected config file carries paths; no secret is accepted in argv."""
    try:
        encoded = read_secret(path, "backup_configuration")
        if command in {"backup-create", "backup-status"}:
            capture = CaptureConfiguration.model_validate_json(encoded)
            result = _capture(capture) if command == "backup-create" else _status(capture)
        elif command in {"backup-restore", "backup-verify"}:
            recovery = RecoveryConfiguration.model_validate_json(encoded)
            result = _recover(command, recovery)
        else:
            raise BackupError("unsupported_backup_command")
        print(json.dumps(result, default=str, sort_keys=True))
        return 0
    except (OSError, subprocess.SubprocessError, SQLAlchemyError) as error:
        # Driver/child exception messages can contain credentials or private rows.
        raise BackupError("backup_operator_failed") from error
