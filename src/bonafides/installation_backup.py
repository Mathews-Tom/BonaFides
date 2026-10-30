# SPDX-License-Identifier: MIT OR Apache-2.0
"""Native PostgreSQL snapshot capture under the installation controller fence.

The runtime role exports only the product schema. Migration authority and private
key escrow belong to the separate offline recovery process, never this producer.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from threading import Timer

from sqlalchemy import Connection, text
from sqlalchemy.engine import make_url

from bonafides.backup_archive import (
    MAX_FILES,
    BackupError,
    BackupFile,
    BackupManifest,
    BundleReceipt,
    create_bundle,
)
from bonafides.backup_generations import register_generation
from bonafides.config import Settings
from bonafides.db import SCHEMA_VERSION, Database
from bonafides.installation_control import Control, assert_control
from bonafides.storage import MAX_PDF_BYTES, directory, read_file

_MIGRATION_LOCK = 741903221
_TABLE = re.compile(r"[a-z][a-z0-9_]{0,62}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def schema_sources(sql_root: Path) -> dict[str, str]:
    """Exact reviewed SQL files; the private Alembic ledger is not a runtime read."""
    names = sorted(path.name for path in sql_root.glob("*.up.sql"))
    if len(names) != SCHEMA_VERSION:
        raise BackupError("backup_migration_source_set_mismatch")
    expected: dict[str, str] = {}
    with directory(sql_root) as root:
        for version, name in enumerate(names, 1):
            if not re.fullmatch(rf"{version:03d}_[a-z0-9_]+\.up\.sql", name):
                raise BackupError("backup_migration_source_set_mismatch")
            source = read_file(root, name, 2 * 1024**2)
            expected[str(version)] = hashlib.sha256(source).hexdigest()
    return expected


def database_schema(conn: Connection) -> dict[str, str]:
    return {
        str(row.version): row.source_sha256
        for row in conn.execute(
            text("SELECT version,source_sha256 FROM bonafides.schema_migrations ORDER BY version")
        )
    }


def table_integrity(conn: Connection) -> tuple[dict[str, int], dict[str, str]]:
    """Content fingerprints stay inside the encrypted manifest, never logs."""
    conn.execute(text("SET LOCAL timezone='UTC'"))
    conn.execute(text("SET LOCAL extra_float_digits=3"))
    tables = (
        conn.execute(
            text(
                "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname='bonafides' ORDER BY tablename"
            )
        )
        .scalars()
        .all()
    )
    if not 0 < len(tables) <= 1000 or any(_TABLE.fullmatch(name) is None for name in tables):
        raise BackupError("backup_table_set_invalid")
    counts: dict[str, int] = {}
    fingerprints: dict[str, str] = {}
    for name in tables:
        digest = hashlib.sha256()
        count = 0
        rows = conn.execute(
            text(
                f'SELECT to_jsonb(t)::text COLLATE "C" AS record '
                f"FROM bonafides.{name} t ORDER BY record"
            ).execution_options(stream_results=True, yield_per=100)
        )
        for row in rows:
            encoded = row.record.encode("utf-8")
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
            count += 1
        counts[name] = count
        fingerprints[name] = digest.hexdigest()
    return counts, fingerprints


def referenced_files(conn: Connection) -> list[BackupFile]:
    """Include every ready immutable object and every still-retained export copy."""
    files: dict[str, BackupFile] = {}
    for row in conn.execute(
        text("""SELECT owner_id,storage_key,sha256,byte_length FROM bonafides.blobs
        WHERE state='ready' ORDER BY owner_id,storage_key LIMIT :limit"""),
        {"limit": MAX_FILES + 1},
    ).mappings():
        digest = row["sha256"]
        if (
            _DIGEST.fullmatch(digest) is None
            or row["storage_key"] != f"{digest[:2]}/{digest}"
            or not 0 < row["byte_length"] <= MAX_PDF_BYTES
        ):
            raise BackupError("backup_blob_reference_invalid")
        path = f"blobs/{row['owner_id']}/{row['storage_key']}"
        entry = BackupFile(path=path, sha256=digest, byte_length=row["byte_length"])
        if path in files and files[path] != entry:
            raise BackupError("backup_blob_reference_conflict")
        files[path] = entry
    for row in conn.execute(
        text("""SELECT owner_id,workflow_run_id,archive_sha256,byte_length
        FROM bonafides.portable_exports WHERE state='ready'
        ORDER BY owner_id,workflow_run_id LIMIT :limit"""),
        {"limit": MAX_FILES + 1},
    ).mappings():
        path = f"blobs/portable_exports/{row['owner_id']}/{row['workflow_run_id']}.zip"
        files[path] = BackupFile(
            path=path, sha256=row["archive_sha256"], byte_length=row["byte_length"]
        )
    if len(files) >= MAX_FILES:
        raise BackupError("backup_file_count_limit")
    return [files[name] for name in sorted(files)]


def postgres_environment(dsn: str) -> dict[str, str]:
    """No DSN/password in argv, errors or ambient inherited libpq configuration."""
    url = make_url(dsn)
    if (
        not url.host
        or not url.username
        or not url.password
        or not url.database
        or re.fullmatch(r"[a-zA-Z0-9_][a-zA-Z0-9_-]{0,62}", url.database) is None
    ):
        raise BackupError("backup_database_configuration_invalid")
    return {
        "PATH": os.environ["PATH"],
        "LANG": "C.UTF-8",
        "PGHOST": url.host,
        "PGPORT": str(url.port or 5432),
        "PGUSER": url.username,
        "PGPASSWORD": url.password,
        "PGDATABASE": url.database,
        "PGSSLMODE": str(url.query.get("sslmode", "prefer")),
        "PGCONNECT_TIMEOUT": "5",
    }


class _CheckedDump(io.BufferedReader):
    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        if not isinstance(process.stdout, io.BufferedReader):
            raise BackupError("backup_dump_pipe_unavailable")
        super().__init__(process.stdout)
        self.process = process

    def read(self, size: int | None = -1) -> bytes:
        value = super().read(size)
        if not value and self.process.wait() != 0:
            raise BackupError("backup_database_dump_failed")
        return value


def capture_installation(
    db: Database,
    settings: Settings,
    control: Control,
    *,
    sql_root: Path,
    recipient: Path,
    staging_root: Path,
    destination: Path,
    image_sha256: str,
    source_sha256: str,
    config_sha256: str,
) -> BundleReceipt:
    """Return a created generation, not a restore/off-host/custody verification.

    The caller holds InstallationMaintenance.quiescent, including both live sealed
    daemon acknowledgements. A shared migration lock and exported PostgreSQL
    snapshot bind manifest rows and pg_dump to one exact database cut. Controller
    heartbeat runs independently; its row lock is never held across this I/O.
    """
    if control.purpose != "backup":
        raise BackupError("backup_control_required")
    if make_url(settings.database_dsn).username != "bonafides_runtime":
        raise BackupError("backup_runtime_role_required")
    guard = db.execution_guard
    if guard is None or guard.exclusive:
        raise BackupError("backup_guarded_actor_required")
    guard.verify()
    with db.transaction() as conn:
        assert_control(conn, control)
    expected_schema = schema_sources(sql_root)
    version = subprocess.run(
        ["pg_dump", "--version"],
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
        pass_fds=(guard.fd,),
    ).stdout.strip()
    if re.fullmatch(r"pg_dump \(PostgreSQL\) 18\.[0-9]+", version) is None:
        raise BackupError("backup_dump_tool_incompatible")
    with (
        db.engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn,
        conn.begin(),
    ):
        conn.execute(text("SET TRANSACTION READ ONLY"))
        conn.execute(text("SELECT pg_advisory_xact_lock_shared(:key)"), {"key": _MIGRATION_LOCK})
        if database_schema(conn) != expected_schema:
            raise BackupError("backup_migration_identity_mismatch")
        snapshot = conn.execute(text("SELECT pg_export_snapshot()")).scalar_one()
        if re.fullmatch(r"[0-9A-Fa-f-]{1,100}", snapshot) is None:
            raise BackupError("backup_snapshot_identity_invalid")
        cut = (
            conn.execute(
                text("""SELECT i.namespace_id,i.maintenance_epoch,
            i.auth_epoch AS installation_auth_epoch,o.auth_epoch AS owner_auth_epoch,
            s.workspace_version FROM bonafides.installation_state i
            JOIN bonafides.owners o ON o.id=:owner
            JOIN bonafides.owner_settings s ON s.owner_id=o.id WHERE i.singleton"""),
                {"owner": control.owner_id},
            )
            .mappings()
            .one()
        )
        if (
            cut["namespace_id"] != control.namespace_id
            or cut["maintenance_epoch"] != control.maintenance_epoch
        ):
            raise BackupError("backup_cut_fence_mismatch")
        counts, fingerprints = table_integrity(conn)
        if counts.get("installation_activities") != 0 or counts.get("provider_executions") != 0:
            raise BackupError("backup_writers_not_drained")
        blobs = referenced_files(conn)
        server_version = conn.execute(text("SHOW server_version")).scalar_one().split()[0]
        now = datetime.now(UTC)

        captured: BackupManifest | None = None

        def manifest(
            sha: str, size: int, fingerprint: str, files: list[BackupFile]
        ) -> BackupManifest:
            nonlocal captured
            captured = BackupManifest(
                format="bonafides-installation",
                format_version=1,
                created_at=now,
                source_namespace=cut["namespace_id"],
                source_owner_id=control.owner_id,
                control_id=control.id,
                installation_auth_epoch=cut["installation_auth_epoch"],
                owner_auth_epoch=cut["owner_auth_epoch"],
                maintenance_epoch=cut["maintenance_epoch"],
                workspace_version=cut["workspace_version"],
                image_sha256=image_sha256,
                source_sha256=source_sha256,
                config_sha256=config_sha256,
                recipient_sha256=fingerprint,
                credential_keys={
                    key: hashlib.sha256(value).hexdigest()
                    for key, value in settings.keyring.keys.items()
                },
                credential_active_key_id=settings.keyring.active_key_id,
                postgres_version=server_version,
                dump_tool_version=version,
                schema_versions=expected_schema,
                table_counts=counts,
                table_sha256=fingerprints,
                dump_sha256=sha,
                dump_byte_length=size,
                files=files,
            )
            return captured

        process = subprocess.Popen(
            [
                "pg_dump",
                "--format=custom",
                "--no-owner",
                "--schema=bonafides",
                "--strict-names",
                "--lock-wait-timeout=5000",
                f"--snapshot={snapshot}",
            ],
            env=postgres_environment(settings.database_dsn),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            pass_fds=(guard.fd,),
        )
        deadline = Timer(300, process.kill)
        deadline.daemon = True
        deadline.start()
        try:
            with _CheckedDump(process) as dump:
                receipt = create_bundle(
                    dump=dump,
                    blob_root=settings.blob_root,
                    blob_files=blobs,
                    recipient=recipient,
                    staging_root=staging_root,
                    destination=destination,
                    manifest_factory=manifest,
                    execution_fds=(guard.fd,),
                )
        finally:
            deadline.cancel()
            deadline.join()
            if process.poll() is None:
                process.kill()
            process.wait()
    registration_attempted = False
    try:
        with db.transaction() as conn:
            assert_control(conn, control)
            if captured is None:
                raise BackupError("backup_manifest_missing")
            registration_attempted = True
            register_generation(conn, control, captured, receipt)
    except Exception:
        # Once registration is attempted, a lost commit acknowledgement is
        # ambiguous. Retain the ciphertext for operator reconciliation.
        if not registration_attempted:
            with directory(destination) as folder:
                os.unlink(receipt.file_name, dir_fd=folder)
                os.fsync(folder)
        raise
    return receipt
