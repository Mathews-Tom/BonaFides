# SPDX-License-Identifier: MIT OR Apache-2.0
"""Offline credential migration; never connects accounts or revokes provider tokens.

The operator atomically deploys a read-only keyring before this command, retaining
old read keys and separately retaining old-backup escrow. This command never
writes key files, changes passwords/sessions, enables a provider, or makes egress.
"""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Self
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidTag
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import Connection, text
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import SQLAlchemyError

from offerloom.backup_archive import BackupError
from offerloom.commands import canonical_json
from offerloom.config import ConfigurationError, MigrationSettings, read_secret
from offerloom.crypto import (
    Envelope,
    EnvelopeContext,
    Keyring,
    canonical_provider_origin,
    decrypt,
    encrypt,
)
from offerloom.db import SCHEMA_VERSION, Database
from offerloom.execution_guard import ExecutionGuard


class CredentialBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    owner_id: UUID
    credential_id: UUID
    external_account_id: UUID | None
    provider_key: str | None = Field(max_length=128)
    purpose: str = Field(min_length=1, max_length=128)
    provider_origin: str = Field(max_length=2048)

    @model_validator(mode="after")
    def exact_origin_and_account(self) -> Self:
        canonical_provider_origin(self.provider_origin)
        if (self.external_account_id is None) != (self.provider_key is None):
            raise ValueError("Credential binding requires the exact account and provider")
        return self


class CredentialRotationConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    migration_dsn_file: Path
    blob_root: Path
    credential_keyring_file: Path
    namespace_id: UUID
    maintenance_epoch: int = Field(ge=0)
    previous_active_key_id: str = Field(min_length=1, max_length=64)
    new_active_key_id: str = Field(min_length=1, max_length=64)
    credential_bindings: list[CredentialBinding]
    batch_size: int = Field(default=100, ge=1, le=500)

    @model_validator(mode="after")
    def protected_paths_and_unique_bindings(self) -> Self:
        for path in (self.migration_dsn_file, self.blob_root, self.credential_keyring_file):
            if not path.is_absolute() or ".." in path.parts:
                raise ValueError("Credential rotation paths must be absolute and normalized")
        identities = {(item.owner_id, item.credential_id) for item in self.credential_bindings}
        if len(identities) != len(self.credential_bindings):
            raise ValueError("Credential rotation requires unique exact bindings")
        if self.previous_active_key_id == self.new_active_key_id:
            raise ValueError("Credential rotation requires an independent new key ID")
        return self


@dataclass(frozen=True)
class CredentialRotationReceipt:
    verified: int
    rotated: int
    already_current: int
    batches: int
    status: str = "completed"
    accounts_reconnected: bool = False
    provider_tokens_revoked: bool = False
    old_backup_keys_required: bool = True


def _check_installation(
    conn: Connection, config: CredentialRotationConfiguration, guard: ExecutionGuard
) -> None:
    if conn.execute(text("SELECT current_user")).scalar_one() != "offerloom_migrator":
        raise BackupError("credential_rotation_migrator_required")
    if conn.execute(
        text("SELECT max(version) FROM offerloom.schema_migrations")
    ).scalar_one() != SCHEMA_VERSION or not conn.execute(
        text("SHOW server_version_num")
    ).scalar_one().startswith("18"):
        raise BackupError("credential_rotation_current_schema_required")
    state = conn.execute(
        text("""SELECT namespace_id,maintenance_epoch,execution_guard_id
        FROM offerloom.installation_state WHERE singleton FOR UPDATE""")
    ).one()
    if (
        state.namespace_id != config.namespace_id
        or state.maintenance_epoch != config.maintenance_epoch
        or state.execution_guard_id != guard.verify()
    ):
        raise BackupError("credential_rotation_installation_identity_mismatch")
    if conn.execute(
        text("""SELECT
        EXISTS(SELECT 1 FROM offerloom.installation_controls WHERE phase<>'released')
        OR EXISTS(SELECT 1 FROM offerloom.installation_activities)
        OR EXISTS(SELECT 1 FROM offerloom.provider_executions)
        OR EXISTS(SELECT 1 FROM offerloom.workflow_runs
          WHERE state NOT IN ('succeeded','cancelled','permanent_failure')
          OR lease_holder IS NOT NULL)
        OR EXISTS(SELECT 1 FROM offerloom.outbox_events WHERE lease_holder IS NOT NULL)
        OR EXISTS(SELECT 1 FROM offerloom.backup_daily_runs WHERE finished_at IS NULL)
        OR EXISTS(SELECT 1 FROM offerloom.external_accounts
          WHERE status IN ('connected','revocation_pending'))""")
    ).scalar_one():
        raise BackupError("credential_rotation_active_work_control_or_account")
    if conn.execute(
        text("""SELECT EXISTS(SELECT 1 FROM offerloom.oauth_transactions)
        OR EXISTS(SELECT 1 FROM offerloom.external_accounts
          WHERE sync_cursor_ciphertext IS NOT NULL OR sync_cursor_key_id IS NOT NULL)""")
    ).scalar_one():
        raise BackupError("credential_rotation_unsupported_envelope_class")


def _rows(conn: Connection) -> Sequence[RowMapping]:
    return (
        conn.execute(
            text("""SELECT c.owner_id,c.id,c.external_account_id,c.purpose,c.key_id,
        c.nonce,c.ciphertext,c.aad_version,c.version,c.provider_origin,a.provider_key
        FROM offerloom.encrypted_credentials c LEFT JOIN offerloom.external_accounts a
        ON a.owner_id=c.owner_id AND a.id=c.external_account_id ORDER BY c.owner_id,c.id""")
        )
        .mappings()
        .all()
    )


def _context(namespace: UUID, row: RowMapping, *, origin: str | None = None) -> EnvelopeContext:
    return EnvelopeContext(
        namespace,
        row["owner_id"],
        row["purpose"],
        row["id"],
        2 if origin is not None else row["aad_version"],
        origin if origin is not None else row["provider_origin"],
    )


def _plaintext(namespace: UUID, row: RowMapping, keyring: Keyring) -> bytes:
    try:
        return decrypt(
            keyring,
            _context(namespace, row),
            Envelope(
                row["key_id"], bytes(row["nonce"]), bytes(row["ciphertext"]), row["aad_version"]
            ),
        )
    except (KeyError, ValueError, InvalidTag) as error:
        raise BackupError("credential_rotation_authentication_failed") from error


def _bindings(
    rows: Sequence[RowMapping], config: CredentialRotationConfiguration
) -> dict[tuple[UUID, UUID], CredentialBinding]:
    bindings = {(item.owner_id, item.credential_id): item for item in config.credential_bindings}
    if set(bindings) != {(row["owner_id"], row["id"]) for row in rows}:
        raise BackupError("credential_rotation_exact_bindings_required")
    for row in rows:
        binding = bindings[(row["owner_id"], row["id"])]
        if (
            binding.external_account_id != row["external_account_id"]
            or binding.provider_key != row["provider_key"]
            or binding.purpose != row["purpose"]
            or (row["aad_version"] == 2 and binding.provider_origin != row["provider_origin"])
        ):
            raise BackupError("credential_rotation_binding_mismatch")
    return bindings


def _reserve(conn: Connection, key_id: str, count: int) -> None:
    conn.execute(
        text("""INSERT INTO offerloom.credential_key_usage(key_id,encryption_count)
        SELECT :key,count(*) FROM offerloom.encrypted_credentials WHERE key_id=:key
        ON CONFLICT(key_id) DO UPDATE SET encryption_count=greatest(
          offerloom.credential_key_usage.encryption_count,EXCLUDED.encryption_count)"""),
        {"key": key_id},
    )
    if (
        conn.execute(
            text("""UPDATE offerloom.credential_key_usage SET encryption_count=encryption_count+:count
        WHERE key_id=:key AND encryption_count<=1048576-:count RETURNING key_id"""),
            {"key": key_id, "count": count},
        ).scalar_one_or_none()
        is None
    ):
        raise BackupError("credential_rotation_key_usage_exhausted")


def _audit(conn: Connection, operation: UUID, status: str, verified: int, rotated: int) -> None:
    conn.execute(
        text("""INSERT INTO offerloom.audit_events
        (owner_id,action,actor_kind,actor_id,request_id,entity_type,detail)
        SELECT id,'credential_key_rotation','operator','host-local-rotation',:operation,
        'installation',CAST(:detail AS jsonb) FROM offerloom.owners"""),
        {
            "operation": operation,
            "detail": canonical_json({"status": status, "verified": verified, "rotated": rotated}),
        },
    )


def _rotate(
    db: Database, config: CredentialRotationConfiguration, keyring: Keyring, guard: ExecutionGuard
) -> CredentialRotationReceipt:
    operation = uuid4()
    with db.transaction() as conn:
        _check_installation(conn, config, guard)
        rows = _rows(conn)
        bindings = _bindings(rows, config)
        # Authenticate ALL claimed classes before the first committed rewrite.
        # Do not hold credential plaintexts between rows or transactions.
        for row in rows:
            _plaintext(config.namespace_id, row, keyring)
        pending = [
            row for row in rows if row["key_id"] != keyring.active_key_id or row["aad_version"] != 2
        ]
        _reserve(conn, keyring.active_key_id, 1 + len(pending))
        _audit(conn, operation, "started", len(rows), 0)
    rotated, batches = 0, 0
    try:
        for offset in range(0, len(pending), config.batch_size):
            batch = pending[offset : offset + config.batch_size]
            with db.transaction() as conn:
                _check_installation(conn, config, guard)
                for row in batch:
                    origin = bindings[(row["owner_id"], row["id"])].provider_origin
                    plaintext = _plaintext(config.namespace_id, row, keyring)
                    context = _context(config.namespace_id, row, origin=origin)
                    envelope = encrypt(keyring, context, plaintext)
                    if decrypt(keyring, context, envelope) != plaintext:
                        raise BackupError("credential_rotation_verification_failed")
                    del plaintext
                    changed = conn.execute(
                        text("""UPDATE offerloom.encrypted_credentials SET key_id=:new_key,
                        nonce=:nonce,ciphertext=:ciphertext,aad_version=2,provider_origin=:origin,
                        version=version+1,rotated_at=clock_timestamp()
                        WHERE owner_id=:owner AND id=:id AND version=:version AND key_id=:old_key
                        RETURNING id"""),
                        {
                            "new_key": envelope.key_id,
                            "nonce": envelope.nonce,
                            "ciphertext": envelope.ciphertext,
                            "origin": origin,
                            "owner": row["owner_id"],
                            "id": row["id"],
                            "version": row["version"],
                            "old_key": row["key_id"],
                        },
                    ).scalar_one_or_none()
                    if changed is None:
                        raise BackupError("credential_rotation_version_conflict")
                _audit(conn, operation, "batch_completed", len(rows), rotated + len(batch))
            rotated += len(batch)
            batches += 1
        with db.transaction() as conn:
            _check_installation(conn, config, guard)
            final = _rows(conn)
            _bindings(final, config)
            if len(final) != len(rows) or any(
                row["key_id"] != keyring.active_key_id or row["aad_version"] != 2 for row in final
            ):
                raise BackupError("credential_rotation_incomplete")
            for row in final:
                _plaintext(config.namespace_id, row, keyring)
            _audit(conn, operation, "completed", len(final), rotated)
    except Exception:
        with db.transaction() as conn:
            _audit(conn, operation, "failed", len(rows), rotated)
        raise
    return CredentialRotationReceipt(len(rows), rotated, len(rows) - len(pending), batches)


def rotate_credentials(config: CredentialRotationConfiguration) -> CredentialRotationReceipt:
    try:
        guard = ExecutionGuard(config.blob_root, exclusive=True, create=False)
    except BlockingIOError as error:
        raise BackupError("credential_rotation_stop_all_runtime_actors") from error
    with guard:
        keyring = Keyring.parse(read_secret(config.credential_keyring_file, "credential_keyring"))
        new_key = keyring.keys.get(config.new_active_key_id)
        if (
            keyring.active_key_id != config.new_active_key_id
            or config.previous_active_key_id not in keyring.keys
            or new_key is None
            or any(
                key == new_key
                for key_id, key in keyring.keys.items()
                if key_id != config.new_active_key_id
            )
        ):
            raise BackupError("credential_rotation_independent_new_active_key_required")
        sentinel_context = EnvelopeContext(
            config.namespace_id,
            UUID(int=0),
            "credential_rotation_sentinel",
            uuid4(),
            2,
            "https://sentinel.invalid",
        )
        sentinel = secrets.token_bytes(32)
        if (
            decrypt(keyring, sentinel_context, encrypt(keyring, sentinel_context, sentinel))
            != sentinel
        ):
            raise BackupError("credential_rotation_sentinel_failed")
        migration = MigrationSettings(
            read_secret(config.migration_dsn_file, "credential_migration_dsn")
        )
        if migration.migration_dsn is None:
            raise BackupError("credential_rotation_migration_dsn_required")
        db = Database(migration.migration_dsn)
        db.execution_guard = guard
        try:
            return _rotate(db, config, keyring, guard)
        finally:
            db.close()


def run(path: Path) -> int:
    try:
        config = CredentialRotationConfiguration.model_validate_json(
            read_secret(path, "credential_rotation_configuration")
        )
        receipt = rotate_credentials(config)
    except ValidationError as error:
        raise ConfigurationError("Invalid credential_rotation_configuration fields") from error
    except SQLAlchemyError as error:
        raise BackupError("credential_rotation_database_failed") from error
    except OSError as error:
        raise BackupError("credential_rotation_protected_files_unavailable") from error
    print(canonical_json(asdict(receipt)))
    return 0
