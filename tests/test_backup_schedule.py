# SPDX-License-Identifier: MIT OR Apache-2.0
"""Real SQL slots and real filesystem lifecycle boundaries, not recovery proof."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from test_database import PGContext
from test_database import pg_context as pg_context

from offerloom import backup_schedule
from offerloom.backup_archive import BackupError
from offerloom.backup_generations import verify_ciphertext
from offerloom.backup_operator import CaptureConfiguration, DailyScheduleConfiguration
from offerloom.backup_schedule import (
    backup_status,
    claim_daily_slot,
    copy_ciphertext,
    prune_daily_generations,
    resolve_interrupted,
    run_due,
)
from offerloom.config import Settings
from offerloom.crypto import Keyring
from offerloom.db import Database
from offerloom.execution_guard import ExecutionGuard
from offerloom.installation_control import begin_control
from offerloom.workflows import claim_control, enqueue


@pytest.fixture
def environment(
    pg_context: PGContext, tmp_path: Path
) -> Iterator[tuple[Database, Settings, CaptureConfiguration]]:
    root = tmp_path.resolve()
    for name in ("blobs", "renderer", "parser", "encrypted", "copy"):
        (root / name).mkdir(mode=0o700)
    settings = Settings(
        database_dsn=pg_context.runtime.url.render_as_string(hide_password=False),
        keyring=Keyring("synthetic", {"synthetic": b"k" * 32}),
        origin="https://localhost",
        blob_root=root / "blobs",
        renderer_spool=root / "renderer",
        import_spool=root / "parser",
        mode="benchmark",
    )
    db = Database(settings)
    with db.transaction() as conn:
        namespace = conn.execute(
            text("SELECT namespace_id FROM offerloom.installation_state WHERE singleton")
        ).scalar_one()
    config = CaptureConfiguration(
        recipient=root / "public.pem",
        destination=root / "encrypted",
        staging_root=root / "staging",
        migrations=Path(__file__).resolve().parents[1] / "migrations",
        image_sha256="1" * 64,
        schedule=DailyScheduleConfiguration(
            enabled=True,
            namespace_id=namespace,
            utc_hour=0,
            display_timezone="Asia/Kolkata",
            copy_destination=root / "copy",
        ),
    )
    try:
        yield db, settings, config
    finally:
        db.close()


def _seed_created(
    context: PGContext, db: Database, config: CaptureConfiguration, day: datetime
) -> dict[str, object]:
    """Synthetic ciphertext-ledger fixture only; never attests a native drill."""
    with db.transaction() as conn:
        identifier = enqueue(conn, context.owner_id, "backup", {})
        task = claim_control(conn, uuid4(), workflow_id=identifier)
        assert task is not None
        control = begin_control(conn, task)
    payload = b"synthetic lifecycle bytes " + identifier.bytes
    name = str(identifier) + ".cms"
    (config.destination / name).write_bytes(payload)
    with context.migrator.begin() as conn:
        conn.execute(
            text("""INSERT INTO offerloom.installation_backups(
            id,owner_id,namespace_id,maintenance_epoch,workspace_version,
            installation_auth_epoch,owner_auth_epoch,schema_versions,image_sha256,
            source_sha256,config_sha256,keyring_sha256,recipient_sha256,file_name,
            cipher_sha256,manifest_sha256,byte_length,captured_at,cipher_verified_at)
            SELECT :id,:owner,:namespace,:epoch,s.workspace_version,i.auth_epoch,o.auth_epoch,
            (SELECT jsonb_object_agg(version::text,source_sha256) FROM offerloom.schema_migrations),
            repeat('0',64),repeat('0',64),repeat('0',64),repeat('0',64),repeat('0',64),
            :name,:sha,repeat('0',64),:length,:day,:day
            FROM offerloom.owner_settings s JOIN offerloom.owners o ON o.id=s.owner_id
            CROSS JOIN offerloom.installation_state i WHERE s.owner_id=:owner AND i.singleton"""),
            {
                "id": identifier,
                "owner": context.owner_id,
                "namespace": control.namespace_id,
                "epoch": control.maintenance_epoch,
                "name": name,
                "sha": hashlib.sha256(payload).hexdigest(),
                "length": len(payload),
                "day": day,
            },
        )
        conn.execute(
            text("""UPDATE offerloom.installation_controls SET phase='released',
            settled_at=clock_timestamp(),released_at=clock_timestamp() WHERE id=:id"""),
            {"id": identifier},
        )
        conn.execute(
            text("UPDATE offerloom.installation_state SET maintenance_active=false WHERE singleton")
        )
        conn.execute(
            text("""UPDATE offerloom.workflow_runs SET state='succeeded',
            lease_holder=NULL,lease_expires_at=NULL,finished_at=clock_timestamp()
            WHERE id=:id"""),
            {"id": identifier},
        )
    with db.transaction() as conn:
        slot = claim_daily_slot(conn, config, now=day)
        assert slot is not None
        conn.execute(
            text("""UPDATE offerloom.backup_daily_runs SET state='created',
            generation_id=:generation,created_at=:day,finished_at=:day WHERE id=:id"""),
            {"id": slot, "generation": identifier, "day": day},
        )
    return {
        "generation_id": str(identifier),
        "file_name": name,
        "cipher_sha256": hashlib.sha256(payload).hexdigest(),
        "byte_length": len(payload),
    }


@pytest.mark.postgres
def test_daily_slot_concurrent_catchup_is_once_and_not_per_missed_day(
    environment: tuple[Database, Settings, CaptureConfiguration],
) -> None:
    db, _, config = environment
    barrier = Barrier(6)
    now = datetime(2035, 4, 25, 10, tzinfo=UTC)

    def claim() -> UUID | None:
        barrier.wait()
        with db.transaction() as conn:
            return claim_daily_slot(conn, config, now=now)

    with ThreadPoolExecutor(max_workers=6) as pool:
        claims = list(pool.map(lambda _: claim(), range(6)))
    assert sum(value is not None for value in claims) == 1
    with db.transaction() as conn:
        assert (
            conn.execute(text("SELECT slot_date FROM offerloom.backup_daily_runs")).scalar_one()
            == now.date()
        )
        assert claim_daily_slot(conn, config, now=now + timedelta(days=1)) is None
        conn.execute(
            text("""UPDATE offerloom.backup_daily_runs SET state='failed',
            finished_at=clock_timestamp(),failure_code='backup_schedule_capture_failed'""")
        )
        assert claim_daily_slot(conn, config, now=now) is None
        assert claim_daily_slot(conn, config, now=now + timedelta(days=50)) is not None
        assert (
            conn.execute(text("SELECT count(*) FROM offerloom.backup_daily_runs")).scalar_one() == 2
        )


@pytest.mark.postgres
def test_absent_or_restored_namespace_configuration_never_reactivates(
    environment: tuple[Database, Settings, CaptureConfiguration],
) -> None:
    db, settings, config = environment
    assert run_due(db, settings) == {"state": "disabled"}
    assert config.schedule is not None
    restored = config.model_copy(
        update={"schedule": config.schedule.model_copy(update={"namespace_id": uuid4()})}
    )
    with db.transaction() as conn:
        assert claim_daily_slot(conn, restored) is None
        assert claim_daily_slot(conn, config.model_copy(update={"schedule": None})) is None
    status = backup_status(db, restored)
    schedule = status["schedule"]
    assert isinstance(schedule, dict)
    assert schedule["state"] == "restored_namespace_disabled"
    assert not status["qualified_configured"]
    encoded = str(status)
    assert str(config.destination) not in encoded
    assert str(settings.blob_root) not in encoded
    assert status["key_custody"] == "not_attested"
    assert status["offhost_copy"] == "not_verified"


@pytest.mark.postgres
def test_unfinished_slot_requires_explicit_settlement_without_automatic_retry(
    environment: tuple[Database, Settings, CaptureConfiguration], monkeypatch: pytest.MonkeyPatch
) -> None:
    db, settings, config = environment
    with db.transaction() as conn:
        identifier = claim_daily_slot(conn, config)
    assert identifier is not None

    def forbidden(*_args: object) -> dict[str, object]:
        raise AssertionError("uncertain slot was automatically retried")

    monkeypatch.setattr(backup_schedule, "_capture", forbidden)
    assert run_due(db, settings, config)["state"] == "not_due_or_blocked"
    schedule = backup_status(db, config)["schedule"]
    assert isinstance(schedule, dict)
    assert schedule["state"] == "recovery_required"
    with pytest.raises(BackupError, match="offline_guard_required"):
        resolve_interrupted(db, config)
    assert db.execution_guard is not None
    db.execution_guard.close()
    db.execution_guard = None
    with ExecutionGuard(settings.blob_root, exclusive=True, create=False) as guard:
        db.execution_guard = guard
        try:
            assert resolve_interrupted(db, config)["settled_daily_run_ids"] == [str(identifier)]
        finally:
            db.execution_guard = None
    db.execution_guard = ExecutionGuard(settings.blob_root, create=False)
    assert run_due(db, settings, config)["state"] == "not_due_or_blocked"


@pytest.mark.postgres
def test_copy_independent_verification_and_failure_preserve_last_good(
    pg_context: PGContext,
    environment: tuple[Database, Settings, CaptureConfiguration],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, settings, config = environment
    record = _seed_created(pg_context, db, config, datetime.now(UTC) - timedelta(days=2))
    copy_ciphertext(config, record)
    assert config.schedule is not None and config.schedule.copy_destination is not None
    copied = config.schedule.copy_destination / str(record["file_name"])
    assert copied.read_bytes() == (config.destination / str(record["file_name"])).read_bytes()
    copied.write_bytes(b"corrupt")
    with pytest.raises(BackupError):
        verify_ciphertext(config.schedule.copy_destination, record)
    with pytest.raises(BackupError):
        prune_daily_generations(db, config, UUID(str(record["generation_id"])))
    assert (config.destination / str(record["file_name"])).exists()

    def fail_capture(*_args: object) -> dict[str, object]:
        raise BackupError("deliberate_test_failure")

    def forbidden_prune(*_args: object) -> int:
        raise AssertionError("failed capture must not prune")

    monkeypatch.setattr(backup_schedule, "_capture", fail_capture)
    monkeypatch.setattr(backup_schedule, "prune_daily_generations", forbidden_prune)
    result = run_due(db, settings, config)
    assert result["failure_code"] == "backup_schedule_capture_failed"
    assert (config.destination / str(record["file_name"])).exists()
    status = backup_status(db, config)
    failure = status["last_failure"]
    assert isinstance(failure, dict)
    assert failure["failure_code"] == "backup_schedule_capture_failed"
    assert "deliberate_test_failure" not in str(status)


@pytest.mark.postgres
def test_retention_requires_new_exact_bytes_then_keeps_fourteen(
    pg_context: PGContext, environment: tuple[Database, Settings, CaptureConfiguration]
) -> None:
    db, _, original = environment
    assert original.schedule is not None
    config = original.model_copy(
        update={"schedule": original.schedule.model_copy(update={"copy_destination": None})}
    )
    records = [
        _seed_created(pg_context, db, config, datetime.now(UTC) - timedelta(days=16 - index))
        for index in range(15)
    ]
    newest = records[-1]
    path = config.destination / str(newest["file_name"])
    valid = path.read_bytes()
    path.write_bytes(b"x" * len(valid))
    with pytest.raises(BackupError, match="cipher_mismatch"):
        prune_daily_generations(db, config, UUID(str(newest["generation_id"])))
    assert all((config.destination / str(record["file_name"])).exists() for record in records)
    path.write_bytes(valid)
    # Migrator-only policy fixture: ciphertext-only days cannot replace a drill.
    with pg_context.migrator.begin() as conn:
        conn.execute(
            text("""UPDATE offerloom.installation_backups
            SET restore_verified_at=clock_timestamp(),restore_receipt_sha256=repeat('0',64),
            restore_target_namespace=:target WHERE id=:id"""),
            {"target": uuid4(), "id": UUID(str(records[0]["generation_id"]))},
        )
    assert prune_daily_generations(db, config, UUID(str(newest["generation_id"]))) == 0
    assert all((config.destination / str(record["file_name"])).exists() for record in records)
    # Only a newer native-drill fixture releases the protected prior last-good.
    with pg_context.migrator.begin() as conn:
        conn.execute(
            text("""UPDATE offerloom.installation_backups
            SET restore_verified_at=clock_timestamp(),restore_receipt_sha256=repeat('0',64),
            restore_target_namespace=:target WHERE id=:id"""),
            {"target": uuid4(), "id": UUID(str(newest["generation_id"]))},
        )
    assert prune_daily_generations(db, config, UUID(str(newest["generation_id"]))) == 1
    assert not (config.destination / str(records[0]["file_name"])).exists()
    assert all((config.destination / str(record["file_name"])).exists() for record in records[1:])
    with db.transaction() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM offerloom.backup_daily_runs WHERE state='pruned'")
            ).scalar_one()
            == 1
        )
        assert (
            conn.execute(
                text("SELECT count(*) FROM offerloom.installation_backups WHERE pruned_at IS NULL")
            ).scalar_one()
            == 14
        )


@pytest.mark.postgres
def test_failed_copy_never_runs_retention(
    pg_context: PGContext,
    environment: tuple[Database, Settings, CaptureConfiguration],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, settings, config = environment
    record = _seed_created(pg_context, db, config, datetime.now(UTC) - timedelta(days=1))
    monkeypatch.setattr(backup_schedule, "_capture", lambda *_args: record)

    def fail_copy(*_args: object) -> None:
        raise OSError("synthetic private path must never reach status")

    def forbidden_prune(*_args: object) -> int:
        raise AssertionError("failed copy must not prune")

    monkeypatch.setattr(backup_schedule, "copy_ciphertext", fail_copy)
    monkeypatch.setattr(backup_schedule, "prune_daily_generations", forbidden_prune)
    result = run_due(db, settings, config)
    assert result["failure_code"] == "backup_schedule_copy_failed"
    assert (config.destination / str(record["file_name"])).exists()
    status = backup_status(db, config)
    assert status["last_created_byte_length"] == record["byte_length"]
    assert "synthetic private path" not in str(status)


@pytest.mark.postgres
def test_exact_file_reader_rejects_symlink_truncation_and_extra_bytes(
    environment: tuple[Database, Settings, CaptureConfiguration],
) -> None:
    _, _, config = environment
    name = str(uuid4()) + ".cms"
    payload = b"exact synthetic ciphertext"
    record = {
        "file_name": name,
        "byte_length": len(payload),
        "cipher_sha256": hashlib.sha256(payload).hexdigest(),
    }
    real = config.destination / "unrelated"
    real.write_bytes(payload)
    target = config.destination / name
    target.symlink_to(real)
    with pytest.raises(BackupError):
        verify_ciphertext(config.destination, record)
    target.unlink()
    for invalid in (payload[:-1], payload + b"x", b"x" * len(payload)):
        target.write_bytes(invalid)
        with pytest.raises(BackupError):
            copy_ciphertext(config, record)
    assert config.schedule is not None and config.schedule.copy_destination is not None
    assert not (config.schedule.copy_destination / name).exists()


@pytest.mark.postgres
def test_interrupted_copy_removes_partial_without_replacing_prior_generation(
    environment: tuple[Database, Settings, CaptureConfiguration],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, config = environment
    assert config.schedule is not None and config.schedule.copy_destination is not None
    records = []
    for payload in (b"prior complete synthetic ciphertext", b"new synthetic ciphertext" * 100):
        record = {
            "file_name": str(uuid4()) + ".cms",
            "byte_length": len(payload),
            "cipher_sha256": hashlib.sha256(payload).hexdigest(),
        }
        (config.destination / str(record["file_name"])).write_bytes(payload)
        records.append(record)
    copy_ciphertext(config, records[0])
    prior = config.schedule.copy_destination / str(records[0]["file_name"])
    prior_bytes = prior.read_bytes()
    original_write = os.write
    partial_sizes = []

    def interrupted(fd: int, content: bytes | memoryview) -> int:
        original_write(fd, content[:17])
        partial_sizes.append(os.fstat(fd).st_size)
        raise OSError("synthetic interruption after actual partial write")

    with monkeypatch.context() as patch:
        patch.setattr(os, "write", interrupted)
        with pytest.raises(OSError, match="synthetic interruption"):
            copy_ciphertext(config, records[1])
    assert partial_sizes == [17]
    assert prior.read_bytes() == prior_bytes
    assert {path.name for path in config.schedule.copy_destination.iterdir()} == {prior.name}
    verify_ciphertext(config.destination, records[1])
