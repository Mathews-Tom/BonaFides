# SPDX-License-Identifier: MIT OR Apache-2.0
"""Offline native recovery: empty target only, original escrow, no promotion.

Call only inside recover_bundle's authenticated private quarantine. The target
must be a separate network-isolated deployment with no running API or workers.
This module neither starts services nor opens maintenance or enables egress.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from cryptography.exceptions import InvalidTag
from sqlalchemy import Connection, text

from bonafides.backup_archive import BackupError, BackupManifest, RecoveredBundle
from bonafides.crypto import Envelope, EnvelopeContext, Keyring, decrypt, encrypt
from bonafides.db import Database
from bonafides.execution_guard import LOCK_NAME, ExecutionGuard
from bonafides.installation_backup import (
    database_schema,
    postgres_environment,
    referenced_files,
    schema_sources,
    table_integrity,
)
from bonafides.storage import directory, subdirectory


@dataclass(frozen=True)
class NativeRecoveryReceipt:
    operation_id: UUID
    source_namespace: UUID
    target_namespace: UUID
    schema_version: int
    tables_verified: int
    files_verified: int
    credentials_verified: int
    maintenance_active: bool = True
    promoted: bool = False


def _escrow(manifest: BackupManifest, keyring: Keyring) -> None:
    if manifest.credential_active_key_id != keyring.active_key_id or any(
        key not in keyring.keys or hashlib.sha256(keyring.keys[key]).hexdigest() != digest
        for key, digest in manifest.credential_keys.items()
    ):
        raise BackupError("restore_credential_keyring_mismatch")


def _empty_target(conn: Connection, blob_root: Path) -> None:
    role = conn.execute(text("SELECT current_user")).scalar_one()
    if role != "bonafides_migrator":
        raise BackupError("restore_migration_role_required")
    if not conn.execute(text("SHOW server_version_num")).scalar_one().startswith("18"):
        raise BackupError("restore_postgres_version_incompatible")
    if conn.execute(
        text("""SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_namespace
        WHERE nspname NOT IN ('public','pg_catalog','information_schema')
        AND nspname NOT LIKE 'pg\\_%' ESCAPE '\\')
        OR EXISTS(SELECT 1 FROM pg_catalog.pg_depend d
        JOIN pg_catalog.pg_namespace n ON n.oid=d.refobjid
        WHERE d.refclassid='pg_catalog.pg_namespace'::pg_catalog.regclass
        AND n.nspname='public')""")
    ).scalar_one():
        raise BackupError("restore_database_not_empty")
    with directory(blob_root, create=True) as root:
        if set(os.listdir(root)) - {LOCK_NAME}:
            raise BackupError("restore_blob_target_not_empty")


def _copy_blobs(bundle: RecoveredBundle, blob_root: Path) -> None:
    with directory(blob_root) as root:
        for entry in bundle.manifest.files[1:]:
            parts = entry.path.split("/")
            if parts[0] != "blobs" or any(part in ("", ".", "..") for part in parts):
                raise BackupError("invalid_backup_member")
            relative = parts[1:]
            with ExitStack() as stack:
                parent = root
                for component in relative[:-1]:
                    parent = stack.enter_context(subdirectory(parent, component, create=True))
                fd = os.open(
                    relative[-1],
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=parent,
                )
                with (
                    os.fdopen(fd, "wb") as output,
                    bundle.blobs.joinpath(*relative).open("rb") as source,
                ):
                    digest = hashlib.sha256()
                    size = 0
                    while chunk := source.read(min(1024**2, entry.byte_length - size + 1)):
                        size += len(chunk)
                        if size > entry.byte_length:
                            raise BackupError("restore_blob_hash_mismatch")
                        digest.update(chunk)
                        output.write(chunk)
                    if (digest.hexdigest(), size) != (entry.sha256, entry.byte_length):
                        raise BackupError("restore_blob_hash_mismatch")
                    output.flush()
                    os.fsync(output.fileno())
                os.fsync(parent)
        os.fsync(root)


def _rebind_credentials(
    conn: Connection, manifest: BackupManifest, keyring: Keyring, namespace: UUID
) -> int:
    # Uncontracted connector/OAuth envelopes cannot be silently discarded or
    # advertised as recovered. Their data contract must precede integration.
    if conn.execute(
        text("""SELECT EXISTS(SELECT 1 FROM bonafides.external_accounts
        WHERE sync_cursor_ciphertext IS NOT NULL OR sync_cursor_key_id IS NOT NULL)
        OR EXISTS(SELECT 1 FROM bonafides.oauth_transactions)""")
    ).scalar_one():
        raise BackupError("restore_unsupported_credential_envelope")
    verified = 0
    rows = (
        conn.execute(
            text("""SELECT owner_id,id,purpose,key_id,nonce,ciphertext,aad_version,
        provider_origin,version FROM bonafides.encrypted_credentials ORDER BY owner_id,id""")
        )
        .mappings()
        .all()
    )
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["key_id"]] = counts.get(row["key_id"], 0) + 1
    for key_id, count in counts.items():
        conn.execute(
            text("""INSERT INTO bonafides.credential_key_usage(key_id,encryption_count)
            VALUES(:key,:count) ON CONFLICT(key_id) DO UPDATE SET encryption_count=greatest(
            bonafides.credential_key_usage.encryption_count,EXCLUDED.encryption_count)"""),
            {"key": key_id, "count": count},
        )
        if (
            conn.execute(
                text("""UPDATE bonafides.credential_key_usage SET encryption_count=encryption_count+:count
            WHERE key_id=:key AND encryption_count<=1048576-:count RETURNING key_id"""),
                {"key": key_id, "count": count},
            ).scalar_one_or_none()
            is None
        ):
            raise BackupError("restore_credential_key_usage_exhausted")
    for row in rows:
        original = Envelope(
            row["key_id"], bytes(row["nonce"]), bytes(row["ciphertext"]), row["aad_version"]
        )
        old_context = EnvelopeContext(
            manifest.source_namespace,
            row["owner_id"],
            row["purpose"],
            row["id"],
            row["aad_version"],
            row["provider_origin"],
        )
        new_context = EnvelopeContext(
            namespace,
            row["owner_id"],
            row["purpose"],
            row["id"],
            row["aad_version"],
            row["provider_origin"],
        )
        try:
            plaintext = decrypt(keyring, old_context, original)
            # Preserve each envelope's key ID; this is AAD namespace rebinding,
            # not an implicit credential-key rotation or reconnection.
            rebound = encrypt(Keyring(row["key_id"], keyring.keys), new_context, plaintext)
            if decrypt(keyring, new_context, rebound) != plaintext:
                raise BackupError("restore_credential_rebinding_failed")
            del plaintext
        except (KeyError, ValueError, InvalidTag) as error:
            raise BackupError("restore_credential_authentication_failed") from error
        conn.execute(
            text("""UPDATE bonafides.encrypted_credentials
            SET nonce=:nonce,ciphertext=:cipher,version=version+1,rotated_at=clock_timestamp()
            WHERE owner_id=:owner AND id=:id AND version=:version"""),
            {
                "nonce": rebound.nonce,
                "cipher": rebound.ciphertext,
                "owner": row["owner_id"],
                "id": row["id"],
                "version": row["version"],
            },
        )
        verified += 1
    return verified


def _revoke_restored_authority(conn: Connection, namespace: UUID, operation: UUID) -> None:
    # Product-only pg_dump excludes database ACLs needed by portable quarantine.
    conn.exec_driver_sql(
        """DO $$ BEGIN
    EXECUTE format('GRANT TEMPORARY ON DATABASE %I TO bonafides_runtime',current_database());
    IF NOT has_database_privilege('bonafides_runtime',current_database(),'TEMPORARY')
    THEN RAISE EXCEPTION 'restore runtime temporary privilege required' USING ERRCODE='42501';
    END IF;
    END $$;""",
        execution_options={"no_parameters": True},
    )
    conn.execute(
        text("""UPDATE bonafides.installation_state
    SET namespace_id=:namespace,maintenance_active=true,maintenance_epoch=maintenance_epoch+1,
    auth_epoch=auth_epoch+1,bootstrap_token_sha256=NULL,bootstrap_consumed_at=clock_timestamp(),
    execution_guard_id=:guard
    WHERE singleton"""),
        {"namespace": namespace, "guard": conn.info["execution_guard"].identity},
    )
    conn.execute(text("UPDATE bonafides.owners SET auth_epoch=auth_epoch+1"))
    conn.execute(
        text("UPDATE bonafides.sessions SET revoked_at=clock_timestamp() WHERE revoked_at IS NULL")
    )
    conn.execute(text("DELETE FROM bonafides.auth_challenges"))
    conn.execute(
        text("""WITH revoked AS (
      UPDATE bonafides.action_approvals
      SET revoked_at=clock_timestamp(),version=version+1 WHERE revoked_at IS NULL
      RETURNING owner_id,id,workflow_run_id,intent)
    INSERT INTO bonafides.approval_audit_events
      (owner_id,approval_id,workflow_run_id,event,error_code)
    SELECT owner_id,id,workflow_run_id,'revoked','installation_restored'
    FROM revoked WHERE intent IS NOT NULL""")
    )
    conn.execute(
        text(
            "UPDATE bonafides.hosted_egress_consents SET revoked_at=clock_timestamp() WHERE revoked_at IS NULL"
        )
    )
    conn.execute(text("DELETE FROM bonafides.oauth_transactions"))
    conn.execute(
        text("""UPDATE bonafides.portable_previews
    SET candidate_bytes=NULL,cleared_at=clock_timestamp(),clear_reason='installation_restored'
    WHERE cleared_at IS NULL""")
    )
    conn.execute(
        text("""UPDATE bonafides.workflow_runs
    SET state='cancelled',lease_holder=NULL,lease_expires_at=NULL,fence=fence+1,
    finished_at=clock_timestamp(),version=version+1,updated_at=clock_timestamp(),
    last_error_code='installation_restored'
    WHERE state NOT IN ('succeeded','cancelled','permanent_failure')""")
    )
    conn.execute(
        text("""UPDATE bonafides.installation_controls
    SET phase='released',settled_at=coalesce(settled_at,clock_timestamp()),
    released_at=clock_timestamp(),result=jsonb_build_object('error_code','installation_restored')
    WHERE phase<>'released'""")
    )
    conn.execute(text("DELETE FROM bonafides.worker_heartbeats"))
    conn.execute(
        text("""UPDATE bonafides.source_configs SET enabled=false,
    current_policy_review_id=NULL,terms_reviewed_at=NULL,
    policy_version=policy_version+1,version=version+1 WHERE enabled""")
    )
    conn.execute(
        text("""UPDATE bonafides.external_accounts SET status='reauthorize',version=version+1
    WHERE status IN ('connected','revocation_pending')""")
    )
    conn.execute(
        text("UPDATE bonafides.owner_settings SET privacy_mode='local_only',version=version+1")
    )
    conn.execute(
        text("""UPDATE bonafides.backup_daily_runs
    SET state='disabled',finished_at=clock_timestamp(),
    failure_code='backup_schedule_restored_disabled'
    WHERE finished_at IS NULL AND state IN ('running','created','copied')""")
    )
    conn.execute(
        text("""INSERT INTO bonafides.audit_events
    (owner_id,action,actor_kind,actor_id,request_id,entity_type,detail)
    SELECT o.id,'installation_restored','operator','host-local-recovery',:operation,
    'installation',jsonb_build_object('maintenance_active',true,'promoted',false,
    'namespace_id',i.namespace_id,'maintenance_epoch',i.maintenance_epoch,
    'execution_guard_id',i.execution_guard_id)
    FROM bonafides.owners o CROSS JOIN bonafides.installation_state i WHERE i.singleton"""),
        {"operation": operation},
    )


def _restore_installation(
    target: Database,
    bundle: RecoveredBundle,
    keyring: Keyring,
    *,
    migrations: Path,
    blob_root: Path,
    expected_source_sha256: str,
) -> NativeRecoveryReceipt:
    """Restore and validate an empty isolated target; leave admission CLOSED.

    Native pg_restore is transactional and uses the separately supplied DDL role.
    Exact row/relationship fingerprints and blob set are verified before required
    authority invalidation. A fresh namespace plus authenticated envelope rebinding
    prevents a restored instance from sharing the live installation identity.
    Failed targets are retained for diagnosis; the caller must choose a fresh one.
    """
    manifest = bundle.manifest
    expected_schema = schema_sources(migrations / "sql")
    if (
        manifest.schema_versions != expected_schema
        or manifest.source_sha256 != expected_source_sha256
    ):
        raise BackupError("restore_source_identity_mismatch")
    _escrow(manifest, keyring)
    operation = uuid4()
    namespace = uuid4()
    with target.transaction() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(741903221)"))
        _empty_target(conn, blob_root)
        with bundle.dump.open("rb") as source:
            result = subprocess.run(
                [
                    "pg_restore",
                    "--exit-on-error",
                    "--single-transaction",
                    "--no-owner",
                    "--role=bonafides_migrator",
                    "--dbname",
                    str(target.engine.url.database),
                ],
                stdin=source,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=postgres_environment(target.engine.url.render_as_string(hide_password=False)),
                pass_fds=(conn.info["execution_guard"].fd,),
                timeout=300,
            )
        if result.returncode != 0:
            raise BackupError("restore_native_database_failed")
        if database_schema(conn) != expected_schema:
            raise BackupError("restore_native_schema_mismatch")
        counts, fingerprints = table_integrity(conn)
        if counts != manifest.table_counts or fingerprints != manifest.table_sha256:
            raise BackupError("restore_native_rows_mismatch")
        if not conn.execute(
            text("""SELECT EXISTS(SELECT 1 FROM bonafides.installation_state i
            JOIN bonafides.owners o ON o.id=:owner
            JOIN bonafides.owner_settings s ON s.owner_id=o.id
            JOIN bonafides.installation_controls c ON c.id=:control AND c.owner_id=o.id
            WHERE i.singleton AND i.maintenance_active AND i.namespace_id=:namespace
            AND i.maintenance_epoch=:epoch AND i.auth_epoch=:installation_auth
            AND o.auth_epoch=:owner_auth AND s.workspace_version=:workspace
            AND c.namespace_id=i.namespace_id AND c.maintenance_epoch=i.maintenance_epoch
            AND c.purpose='backup' AND c.phase='quiescent')"""),
            {
                "owner": manifest.source_owner_id,
                "control": manifest.control_id,
                "namespace": manifest.source_namespace,
                "epoch": manifest.maintenance_epoch,
                "installation_auth": manifest.installation_auth_epoch,
                "owner_auth": manifest.owner_auth_epoch,
                "workspace": manifest.workspace_version,
            },
        ).scalar_one():
            raise BackupError("restore_native_cut_mismatch")
        if referenced_files(conn) != manifest.files[1:]:
            raise BackupError("restore_native_blob_set_mismatch")
        if conn.execute(
            text("""SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_constraint c
            JOIN pg_catalog.pg_namespace n ON n.oid=c.connamespace
            WHERE n.nspname='bonafides' AND NOT c.convalidated)""")
        ).scalar_one():
            raise BackupError("restore_native_constraints_unvalidated")
        _copy_blobs(bundle, blob_root)
        credentials = _rebind_credentials(conn, manifest, keyring, namespace)
        # Only the independently verified exact public source ledger permits a
        # standard Alembic stamp. Never copy a guessed/fabricated private ledger.
        conn.execute(text("CREATE SCHEMA bonafides_migrations"))
        conn.execute(
            text("REVOKE ALL ON SCHEMA bonafides_migrations FROM PUBLIC,bonafides_runtime")
        )
        config = Config()
        config.set_main_option("script_location", str(migrations))
        config.attributes["connection"] = conn
        head = ScriptDirectory.from_config(config).get_current_head()
        if head is None:
            raise BackupError("restore_migration_head_missing")
        command.stamp(config, head)
        _revoke_restored_authority(conn, namespace, operation)
        if conn.execute(
            text("""SELECT has_table_privilege('bonafides_runtime','bonafides.profile_revisions','DELETE')
            OR has_schema_privilege('bonafides_runtime','bonafides_migrations','USAGE')""")
        ).scalar_one():
            raise BackupError("restore_runtime_privileges_unsafe")
    return NativeRecoveryReceipt(
        operation,
        manifest.source_namespace,
        namespace,
        max(map(int, expected_schema)),
        len(counts),
        len(manifest.files) - 1,
        credentials,
    )


def restore_installation(
    target: Database,
    bundle: RecoveredBundle,
    keyring: Keyring,
    *,
    migrations: Path,
    blob_root: Path,
    expected_source_sha256: str,
) -> NativeRecoveryReceipt:
    # The isolated target gets its own canonical exclusion identity. Native
    # restore never adopts or opens a source namespace's maintenance barrier.
    if target.execution_guard is not None:
        raise BackupError("restore_offline_target_required")
    with ExecutionGuard(blob_root, exclusive=True) as guard:
        target.execution_guard = guard
        try:
            return _restore_installation(
                target,
                bundle,
                keyring,
                migrations=migrations,
                blob_root=blob_root,
                expected_source_sha256=expected_source_sha256,
            )
        finally:
            target.execution_guard = None
