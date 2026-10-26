# SPDX-License-Identifier: MIT OR Apache-2.0
"""Opt-in daily slots around the existing fenced installation capture engine.

An unfinished slot is an operator-recovery latch, never a lease to steal. Local
mount copies establish byte integrity only, not off-host or human key custody.
"""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import Connection, text
from sqlalchemy.exc import SQLAlchemyError

from offerloom.backup_archive import BackupError
from offerloom.backup_generations import verify_ciphertext
from offerloom.backup_operator import CaptureConfiguration, _capture, configuration_sha256
from offerloom.commands import canonical_json, lock_installation
from offerloom.config import Settings
from offerloom.db import Database
from offerloom.execution_guard import ExecutionGuard
from offerloom.maintenance import finish_activity, start_cleanup_activity
from offerloom.problems import Problem
from offerloom.storage import directory

_SCHEDULE_LOCK = 741903224
DAILY_RETENTION = 14


def _storage_sha256(config: CaptureConfiguration) -> str:
    return hashlib.sha256(
        canonical_json(
            {
                "destination": str(config.destination),
                "copy_destination": str(config.schedule.copy_destination)
                if config.schedule is not None and config.schedule.copy_destination is not None
                else None,
            }
        ).encode()
    ).hexdigest()


@contextmanager
def _coordinator(db: Database) -> Iterator[bool]:
    # A session lock spans committed slot admission, real capture and filesystem
    # work. The durable unfinished row still blocks replay if this connection dies.
    with db.engine.connect() as conn:
        acquired = bool(
            conn.execute(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": _SCHEDULE_LOCK}
            ).scalar_one()
        )
        conn.commit()
        try:
            yield acquired
        finally:
            if acquired:
                try:
                    conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _SCHEDULE_LOCK})
                    conn.commit()
                except SQLAlchemyError:
                    # Never return a possibly session-locked connection to the pool.
                    conn.invalidate()


def claim_daily_slot(
    conn: Connection, config: CaptureConfiguration, *, now: datetime | None = None
) -> UUID | None:
    """Claim only the latest due UTC slot, collapsing every missed day into one."""
    schedule = config.schedule
    if schedule is None or not schedule.enabled:
        return None
    installation = (
        conn.execute(
            text("""SELECT namespace_id,maintenance_active
        FROM offerloom.installation_state WHERE singleton FOR SHARE""")
        )
        .mappings()
        .one()
    )
    if installation["namespace_id"] != schedule.namespace_id:
        return None
    if installation["maintenance_active"]:
        return None
    if conn.execute(
        text("""SELECT EXISTS(SELECT 1 FROM offerloom.backup_daily_runs
        WHERE namespace_id=:namespace AND finished_at IS NULL)"""),
        {"namespace": schedule.namespace_id},
    ).scalar_one():
        return None
    clock = now if now is not None else conn.execute(text("SELECT clock_timestamp()")).scalar_one()
    if clock.tzinfo is None:
        raise ValueError("backup_schedule_aware_clock_required")
    clock = clock.astimezone(UTC)
    due = clock.replace(hour=schedule.utc_hour, minute=schedule.utc_minute, second=0, microsecond=0)
    if due > clock:
        due -= timedelta(days=1)
    owner = conn.execute(text("SELECT id FROM offerloom.owners")).scalar_one_or_none()
    if owner is None:
        return None
    identifier = uuid4()
    return conn.execute(
        text("""INSERT INTO offerloom.backup_daily_runs
        (id,owner_id,namespace_id,slot_date,config_sha256,storage_sha256,state)
        VALUES(:id,:owner,:namespace,:slot,:config,:storage,'running')
        ON CONFLICT DO NOTHING RETURNING id"""),
        {
            "id": identifier,
            "owner": owner,
            "namespace": schedule.namespace_id,
            "slot": due.date(),
            "config": configuration_sha256(config),
            "storage": _storage_sha256(config),
        },
    ).scalar_one_or_none()


def copy_ciphertext(config: CaptureConfiguration, record: dict[str, object]) -> None:
    """Publish a second private ciphertext, then independently reopen/hash it."""
    schedule = config.schedule
    if schedule is None or schedule.copy_destination is None:
        raise BackupError("backup_copy_not_configured")
    verify_ciphertext(config.destination, record)
    name = str(record["file_name"])
    length = int(str(record["byte_length"]))
    temporary = ".daily-" + uuid4().hex
    with (
        directory(config.destination) as source,
        directory(schedule.copy_destination, create=True) as target,
    ):
        source_info, target_info = os.fstat(source), os.fstat(target)
        if (source_info.st_dev, source_info.st_ino) == (target_info.st_dev, target_info.st_ino):
            raise BackupError("backup_copy_destination_not_distinct")
        if stat.S_IMODE(target_info.st_mode) & 0o077:
            raise BackupError("backup_copy_destination_not_private")
        output = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=target
        )
        try:
            input_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=source)
            try:
                before = os.fstat(input_fd)
                if not stat.S_ISREG(before.st_mode) or before.st_size != length:
                    raise BackupError("backup_copy_source_changed")
                digest = hashlib.sha256()
                remaining = length
                while remaining:
                    chunk = os.read(input_fd, min(remaining, 1024**2))
                    if not chunk:
                        raise BackupError("backup_copy_source_changed")
                    digest.update(chunk)
                    remaining -= len(chunk)
                    view = memoryview(chunk)
                    while view:
                        written = os.write(output, view)
                        if written <= 0:
                            raise OSError("short_backup_copy_write")
                        view = view[written:]
                after = os.fstat(input_fd)
                if (
                    os.read(input_fd, 1)
                    or digest.hexdigest() != record["cipher_sha256"]
                    or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                    != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                ):
                    raise BackupError("backup_copy_source_changed")
            finally:
                os.close(input_fd)
            os.fsync(output)
            os.close(output)
            output = -1
            # Never replace an existing generation (including a symlink).
            os.link(temporary, name, src_dir_fd=target, dst_dir_fd=target, follow_symlinks=False)
            os.fsync(target)
        finally:
            if output >= 0:
                os.close(output)
            with suppress(FileNotFoundError):
                os.unlink(temporary, dir_fd=target)
            os.fsync(target)
    verify_ciphertext(schedule.copy_destination, record)


def _unlink_ciphertext(path: Path, record: dict[str, object]) -> None:
    verify_ciphertext(path, record)
    with directory(path) as folder:
        os.unlink(str(record["file_name"]), dir_fd=folder)
        os.fsync(folder)


def prune_daily_generations(db: Database, config: CaptureConfiguration, generation_id: UUID) -> int:
    """Only a newly committed, re-read good ciphertext authorizes this retention pass.

    The caller registers cleanup activity around all filesystem work. Installation
    admission's shared SQL lock prevents a snapshot/replacement during deletion.
    """
    schedule = config.schedule
    if schedule is None:
        raise BackupError("backup_schedule_disabled")
    with db.transaction() as conn:
        lock_installation(conn)
        rows = [
            dict(row)
            for row in conn.execute(
                text("""SELECT d.id AS daily_id,
            b.id,b.file_name,b.cipher_sha256,b.byte_length,b.cipher_verified_at,b.copy_verified_at
            FROM offerloom.backup_daily_runs d JOIN offerloom.installation_backups b
            ON b.id=d.generation_id CROSS JOIN offerloom.installation_state i
            WHERE i.singleton AND d.namespace_id=i.namespace_id AND d.namespace_id=:namespace
            AND d.storage_sha256=:storage AND d.created_at IS NOT NULL
            AND d.failure_code IS NULL AND d.state IN ('created','copied')
            AND b.pruned_at IS NULL ORDER BY d.slot_date DESC,d.id DESC FOR UPDATE OF d,b"""),
                {"namespace": schedule.namespace_id, "storage": _storage_sha256(config)},
            ).mappings()
        ]
        if not rows or rows[0]["id"] != generation_id or rows[0]["cipher_verified_at"] is None:
            raise BackupError("backup_retention_new_generation_required")
        newest = rows[0]
        verify_ciphertext(config.destination, newest)
        if schedule.copy_destination is not None:
            if newest["copy_verified_at"] is None:
                raise BackupError("backup_retention_copy_required")
            verify_ciphertext(schedule.copy_destination, newest)
        protected = conn.execute(
            text("""SELECT b.id FROM offerloom.installation_backups b
            WHERE b.namespace_id=:namespace AND b.pruned_at IS NULL
            AND b.restore_verified_at IS NOT NULL
            ORDER BY b.captured_at DESC,b.id DESC LIMIT 1"""),
            {"namespace": schedule.namespace_id},
        ).scalar_one_or_none()
    pruned = 0
    for row in rows[DAILY_RETENTION:]:
        # Fourteen daily ciphertexts do not supersede the last actual native drill.
        if row["id"] == protected:
            continue
        # Commit a conservative unavailable state BEFORE irreversible deletion.
        # A crash can leave extra bytes, never advertise a removed file as usable.
        with db.transaction() as conn:
            lock_installation(conn)
            conn.execute(
                text("""UPDATE offerloom.backup_daily_runs SET state='failed',
                failure_code='backup_schedule_retention_failed'
                WHERE id=:id"""),
                {"id": row["daily_id"]},
            )
        primary_removed = False
        copy_removed = False
        try:
            with db.transaction() as conn:
                lock_installation(conn)
                _unlink_ciphertext(config.destination, row)
                primary_removed = True
                if schedule.copy_destination is not None:
                    _unlink_ciphertext(schedule.copy_destination, row)
                    copy_removed = True
        finally:
            with db.transaction() as conn:
                conn.execute(
                    text("""UPDATE offerloom.installation_backups
                    SET pruned_at=CASE WHEN :primary THEN clock_timestamp() ELSE pruned_at END,
                    copy_pruned_at=CASE WHEN :copied THEN clock_timestamp() ELSE copy_pruned_at END
                    WHERE id=:id"""),
                    {"id": row["id"], "primary": primary_removed, "copied": copy_removed},
                )
        with db.transaction() as conn:
            conn.execute(
                text("""UPDATE offerloom.backup_daily_runs SET state='pruned',
                pruned_at=clock_timestamp(),failure_code=NULL WHERE id=:id"""),
                {"id": row["daily_id"]},
            )
        pruned += 1
    return pruned


def run_due(
    db: Database, settings: Settings, config: CaptureConfiguration | None = None
) -> dict[str, object]:
    """Run one latest-due slot. No retry of a failed/unfinished slot is implicit."""
    if config is None:
        if settings.backup_capture_config_file is None:
            return {"state": "disabled"}
        from offerloom.backup_configuration import capture_configuration

        config = capture_configuration(settings)
    if config.schedule is None or not config.schedule.enabled:
        return {"state": "disabled"}
    with _coordinator(db) as acquired:
        if not acquired:
            return {"state": "busy"}
        with db.transaction() as conn:
            identifier = claim_daily_slot(conn, config)
        if identifier is None:
            return {"state": "not_due_or_blocked"}
        failure = "backup_schedule_capture_failed"
        activity = None
        try:
            result = _capture(config, settings)
            generation = UUID(str(result["generation_id"]))
            with db.transaction() as conn:
                conn.execute(
                    text("""UPDATE offerloom.backup_daily_runs
                    SET state='created',created_at=clock_timestamp(),generation_id=:generation
                    WHERE id=:id AND state='running'"""),
                    {"id": identifier, "generation": generation},
                )
            failure = (
                "backup_schedule_copy_failed"
                if config.schedule.copy_destination is not None
                else "backup_schedule_retention_failed"
            )
            activity = start_cleanup_activity(db, uuid4())
            if config.schedule.copy_destination is not None:
                copy_ciphertext(config, result)
                with db.transaction() as conn:
                    conn.execute(
                        text("""UPDATE offerloom.installation_backups
                        SET copy_verified_at=clock_timestamp() WHERE id=:id"""),
                        {"id": generation},
                    )
                    conn.execute(
                        text("""UPDATE offerloom.backup_daily_runs
                        SET state='copied',copied_at=clock_timestamp() WHERE id=:id"""),
                        {"id": identifier},
                    )
            failure = "backup_schedule_retention_failed"
            pruned = prune_daily_generations(db, config, generation)
            with db.transaction() as conn:
                conn.execute(
                    text("""UPDATE offerloom.backup_daily_runs
                    SET finished_at=clock_timestamp() WHERE id=:id"""),
                    {"id": identifier},
                )
            return {
                "state": "copied" if config.schedule.copy_destination is not None else "created",
                "daily_run_id": str(identifier),
                "generation_id": str(generation),
                "pruned_generations": pruned,
                "offhost_copy": "not_verified",
                "key_custody": "not_attested",
            }
        except (Problem, OSError, ValueError, SQLAlchemyError, subprocess.SubprocessError):
            with db.transaction() as conn:
                conn.execute(
                    text("""UPDATE offerloom.backup_daily_runs SET state='failed',
                    failure_code=:failure,finished_at=clock_timestamp() WHERE id=:id"""),
                    {"id": identifier, "failure": failure},
                )
            return {"state": "failed", "daily_run_id": str(identifier), "failure_code": failure}
        finally:
            if activity is not None:
                finish_activity(db, activity)


def resolve_interrupted(db: Database, config: CaptureConfiguration) -> dict[str, object]:
    """Explicit host operator settlement AFTER the existing maintenance recovery."""
    schedule = config.schedule
    if schedule is None:
        raise BackupError("backup_schedule_disabled")
    guard = db.execution_guard
    if not isinstance(guard, ExecutionGuard) or not guard.exclusive:
        raise BackupError("backup_schedule_offline_guard_required")
    guard.verify()
    if not db.ready():
        raise BackupError("backup_schedule_runtime_identity_mismatch")
    with _coordinator(db) as acquired:
        if not acquired:
            raise BackupError("backup_schedule_running")
        with db.transaction() as conn:
            lock_installation(conn)
            namespace = conn.execute(
                text("SELECT namespace_id FROM offerloom.installation_state WHERE singleton")
            ).scalar_one()
            if (
                namespace != schedule.namespace_id
                or conn.execute(
                    text("""SELECT
                EXISTS(SELECT 1 FROM offerloom.installation_controls WHERE phase<>'released')
                OR EXISTS(SELECT 1 FROM offerloom.installation_activities)
                OR EXISTS(SELECT 1 FROM offerloom.provider_executions)""")
                ).scalar_one()
            ):
                raise BackupError("backup_schedule_recovery_required")
            identifiers = (
                conn.execute(
                    text("""UPDATE offerloom.backup_daily_runs
                SET state='failed',finished_at=clock_timestamp(),failure_code='backup_schedule_interrupted'
                WHERE namespace_id=:namespace AND finished_at IS NULL RETURNING id"""),
                    {"namespace": namespace},
                )
                .scalars()
                .all()
            )
    return {"settled_daily_run_ids": [str(value) for value in identifiers], "retried": False}


def backup_status(
    db: Database, config: CaptureConfiguration | None, owner_id: UUID | None = None
) -> dict[str, object]:
    """Content/path-free status; creation, copy, drill and custody stay distinct."""
    with db.transaction() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        installation = (
            conn.execute(
                text("""SELECT namespace_id,maintenance_active,
            clock_timestamp() AS now FROM offerloom.installation_state WHERE singleton""")
            )
            .mappings()
            .one()
        )
        rows = [
            dict(row)
            for row in conn.execute(
                text("""SELECT b.id,b.captured_at,
            b.byte_length,b.cipher_sha256,b.cipher_verified_at,b.copy_verified_at,
            b.restore_verified_at,b.restore_receipt_sha256,b.pruned_at,b.copy_pruned_at,
            b.namespace_id=:namespace AS current_installation,
            d.state AS daily_state,d.failure_code AS failure_code,d.finished_at AS daily_finished_at,
            CASE WHEN b.pruned_at IS NOT NULL THEN 'pruned'
                 WHEN d.failure_code='backup_schedule_retention_failed' THEN 'retention_uncertain'
                 WHEN b.cipher_verified_at IS NOT NULL THEN 'ciphertext_verified'
                 ELSE 'created_unverified' END AS creation_state,
            CASE WHEN b.copy_pruned_at IS NOT NULL THEN 'pruned'
                 WHEN b.copy_verified_at IS NOT NULL THEN 'ciphertext_verified'
                 WHEN d.failure_code='backup_schedule_copy_failed' THEN 'failed'
                 ELSE 'not_verified' END AS copy_state
            FROM offerloom.installation_backups b LEFT JOIN offerloom.backup_daily_runs d
            ON d.generation_id=b.id WHERE (CAST(:owner AS uuid) IS NULL OR b.owner_id=:owner)
            ORDER BY b.captured_at DESC,b.id DESC LIMIT 30"""),
                {"owner": owner_id, "namespace": installation["namespace_id"]},
            ).mappings()
        ]
        runs = [
            dict(row)
            for row in conn.execute(
                text("""SELECT id,namespace_id,slot_date,state,
            started_at,created_at,copied_at,pruned_at,finished_at,failure_code
            FROM offerloom.backup_daily_runs WHERE (CAST(:owner AS uuid) IS NULL OR owner_id=:owner)
            ORDER BY started_at DESC,id DESC LIMIT 30"""),
                {"owner": owner_id},
            ).mappings()
        ]
        parameters = {"owner": owner_id, "namespace": installation["namespace_id"]}
        verified = (
            conn.execute(
                text("""SELECT captured_at,byte_length
            FROM offerloom.installation_backups WHERE namespace_id=:namespace
            AND (CAST(:owner AS uuid) IS NULL OR owner_id=:owner)
            AND pruned_at IS NULL AND cipher_verified_at IS NOT NULL
            ORDER BY captured_at DESC,id DESC LIMIT 1"""),
                parameters,
            )
            .mappings()
            .one_or_none()
        )
        qualifications = (
            conn.execute(
                text("""SELECT
            max(restore_verified_at) FILTER (WHERE pruned_at IS NULL) AS native_drill,
            max(copy_verified_at) FILTER (WHERE copy_pruned_at IS NULL) AS copied
            FROM offerloom.installation_backups WHERE namespace_id=:namespace
            AND (CAST(:owner AS uuid) IS NULL OR owner_id=:owner)"""),
                parameters,
            )
            .mappings()
            .one()
        )
    now = installation["now"]
    schedule = config.schedule if config is not None else None
    schedule_state = "disabled"
    next_due = None
    latest_due = None
    if schedule is not None and schedule.enabled:
        if schedule.namespace_id != installation["namespace_id"]:
            schedule_state = "restored_namespace_disabled"
        elif any(
            row["finished_at"] is None and row["namespace_id"] == schedule.namespace_id
            for row in runs
        ):
            with _coordinator(db) as available:
                schedule_state = "recovery_required" if available else "running"
        elif installation["maintenance_active"]:
            schedule_state = "maintenance_blocked"
        else:
            schedule_state = "enabled"
            next_due = now.astimezone(UTC).replace(
                hour=schedule.utc_hour, minute=schedule.utc_minute, second=0, microsecond=0
            )
            if next_due <= now:
                next_due += timedelta(days=1)
        latest_due = now.astimezone(UTC).replace(
            hour=schedule.utc_hour, minute=schedule.utc_minute, second=0, microsecond=0
        )
        if latest_due > now:
            latest_due -= timedelta(days=1)
    age = max(0, int((now - verified["captured_at"]).total_seconds())) if verified else None
    alerts = ["backup_key_custody_not_attested", "backup_offhost_not_verified"]
    if age is None:
        alerts.append("backup_never_verified")
    elif age > 48 * 3600:
        alerts.append("backup_age_critical")
    elif age > 26 * 3600:
        alerts.append("backup_age_warning")
    if schedule_state not in {"enabled", "running"}:
        alerts.append("backup_schedule_" + schedule_state)
    current_runs = [row for row in runs if row["namespace_id"] == installation["namespace_id"]]
    failure = next((row for row in current_runs if row["failure_code"] is not None), None)
    if current_runs and current_runs[0]["failure_code"] is not None:
        alerts.append(str(current_runs[0]["failure_code"]))

    def safe(record: dict[str, object]) -> dict[str, object]:
        return {
            key: value.isoformat()
            if hasattr(value, "isoformat")
            else str(value)
            if isinstance(value, UUID)
            else value
            for key, value in record.items()
        }

    return {
        "configuration_present": config is not None,
        "qualified_configured": False,
        "key_custody": "not_attested",
        "offhost_copy": "not_verified",
        "copy_kind": "operator_path_bytes_only"
        if schedule is not None and schedule.copy_destination is not None
        else "not_configured",
        "last_created_age_seconds": age,
        "last_created_byte_length": verified["byte_length"] if verified else None,
        "last_native_drill_at": qualifications["native_drill"].isoformat()
        if qualifications["native_drill"] is not None
        else None,
        "last_copy_verified_at": qualifications["copied"].isoformat()
        if qualifications["copied"] is not None
        else None,
        "last_failure": safe(failure) if failure is not None else None,
        "maintenance_active": bool(installation["maintenance_active"]),
        "alerts": alerts,
        "schedule": {
            "state": schedule_state,
            "enabled": bool(
                schedule is not None
                and schedule.enabled
                and schedule.namespace_id == installation["namespace_id"]
            ),
            "utc_hour": schedule.utc_hour if schedule else None,
            "utc_minute": schedule.utc_minute if schedule else None,
            "display_timezone": schedule.display_timezone if schedule else None,
            "next_due_at": next_due.isoformat() if next_due else None,
            "next_due_local": next_due.astimezone(ZoneInfo(schedule.display_timezone)).isoformat()
            if next_due is not None and schedule is not None
            else None,
            "latest_due_slot_at": latest_due.isoformat() if latest_due else None,
            "catchup_due": bool(
                latest_due is not None
                and schedule is not None
                and schedule.namespace_id == installation["namespace_id"]
                and not any(row["slot_date"] == latest_due.date() for row in current_runs)
            ),
            "retained_daily_generations": DAILY_RETENTION,
            "retention_scope": "current_operator_destinations",
            "native_last_good_retained_in_addition": True,
        },
        "daily_runs": [safe(row) for row in runs],
        "generations": [safe(row) for row in rows],
    }
