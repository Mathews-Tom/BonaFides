# SPDX-License-Identifier: MIT OR Apache-2.0
"""Real PostgreSQL18 invariants; never substitute SQLite or mock SQL outcomes."""

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from bonafides.commands import command
from bonafides.problems import Problem
from bonafides.workflows import migrate


@dataclass(frozen=True)
class MigrationSecret:
    migration_dsn: str | None


@dataclass(frozen=True)
class PGContext:
    migrator: Engine
    runtime: Engine
    owner_id: UUID
    profile_id: UUID
    revision_id: UUID


def _secret(name: str) -> str | None:
    direct = os.environ.get(name)
    file = os.environ.get(name + "_FILE")
    value = Path(file).read_text().strip() if file else direct
    if value:
        return value.replace("postgresql://", "postgresql+psycopg://", 1)
    return None


@pytest.fixture
def pg_context() -> Iterator[PGContext]:
    migration_dsn = _secret("BONAFIDES_TEST_MIGRATION_DSN")
    runtime_dsn = _secret("BONAFIDES_TEST_RUNTIME_DSN") or _secret("BONAFIDES_TEST_DSN")
    if not migration_dsn or not runtime_dsn:
        pytest.fail(
            "disposable PostgreSQL18 migration/runtime DSNs not configured; integration gate cannot skip"
        )
    for dsn in (migration_dsn, runtime_dsn):
        name = make_url(dsn).database or ""
        if not (name.startswith("test_") or name.endswith("_test")):
            pytest.fail("refusing invariant tests outside a named disposable test database")
    migrate(MigrationSecret(migration_dsn))
    migrator = create_engine(migration_dsn, hide_parameters=True)
    runtime = create_engine(runtime_dsn, hide_parameters=True)
    owner_id, profile_id, revision_id = uuid4(), uuid4(), uuid4()
    with migrator.begin() as conn:
        if conn.execute(text("SELECT count(*) FROM bonafides.owners")).scalar_one():
            pytest.fail("PostgreSQL invariant tests require an empty disposable owner database")
        conn.execute(
            text("UPDATE bonafides.installation_state SET execution_guard_id=NULL WHERE singleton")
        )
        conn.execute(
            text(
                "INSERT INTO bonafides.owners(id,login_name,password_hash) VALUES(:id,'foundation-invariant-test','not-a-login-hash')"
            ),
            {"id": owner_id},
        )
        conn.execute(
            text("INSERT INTO bonafides.owner_settings(owner_id) VALUES(:owner)"),
            {"owner": owner_id},
        )
        conn.execute(
            text("INSERT INTO bonafides.profiles(owner_id,id) VALUES(:owner,:id)"),
            {"owner": owner_id, "id": profile_id},
        )
        conn.execute(
            text("""INSERT INTO bonafides.profile_revisions(owner_id,id,profile_id,revision_no,snapshot,content_sha256,reason)
            VALUES(:owner,:id,:profile,1,'{}',repeat('0',64),'synthetic test')"""),
            {"owner": owner_id, "id": revision_id, "profile": profile_id},
        )
        conn.execute(
            text(
                "UPDATE bonafides.profiles SET current_revision_id=:revision WHERE owner_id=:owner"
            ),
            {"owner": owner_id, "revision": revision_id},
        )
    try:
        yield PGContext(migrator, runtime, owner_id, profile_id, revision_id)
    finally:
        with migrator.begin() as conn:
            conn.execute(
                text("""UPDATE bonafides.installation_state SET maintenance_active=false
                WHERE singleton AND EXISTS(
                SELECT 1 FROM bonafides.installation_controls WHERE owner_id=:owner)"""),
                {"owner": owner_id},
            )
            conn.execute(
                text("DELETE FROM bonafides.portable_purges WHERE owner_id=:owner"),
                {"owner": owner_id},
            )
            conn.execute(
                text("DELETE FROM bonafides.installation_backups WHERE owner_id=:owner"),
                {"owner": owner_id},
            )
            conn.execute(
                text("DELETE FROM bonafides.installation_controls WHERE owner_id=:owner"),
                {"owner": owner_id},
            )
            conn.execute(text("DELETE FROM bonafides.owners WHERE id=:owner"), {"owner": owner_id})
        runtime.dispose()
        migrator.dispose()


@pytest.mark.postgres
def test_migration_repeat_and_checksum_ledger(pg_context: PGContext) -> None:
    query = text("SELECT version,source_sha256 FROM bonafides.schema_migrations ORDER BY version")
    with pg_context.runtime.begin() as conn:
        before = conn.execute(query).all()
    config = MigrationSecret(str(pg_context.migrator.url.render_as_string(hide_password=False)))
    migrate(config)
    with pg_context.runtime.begin() as conn:
        assert conn.execute(query).all() == before
    version, original_hash = before[0]
    try:
        with pg_context.migrator.begin() as conn:
            conn.execute(
                text(
                    "UPDATE bonafides.schema_migrations SET source_sha256=:hash WHERE version=:version"
                ),
                {"hash": "f" * 64, "version": version},
            )
        with pytest.raises(RuntimeError, match="migration source checksum mismatch"):
            migrate(config)
    finally:
        with pg_context.migrator.begin() as conn:
            conn.execute(
                text(
                    "UPDATE bonafides.schema_migrations SET source_sha256=:hash WHERE version=:version"
                ),
                {"hash": original_hash, "version": version},
            )


@pytest.mark.postgres
def test_owner_foreign_key_and_current_pointer(pg_context: PGContext) -> None:
    with pytest.raises(DBAPIError), pg_context.runtime.begin() as conn:
        conn.execute(
            text("""INSERT INTO bonafides.profile_revisions(owner_id,profile_id,revision_no,snapshot,content_sha256,reason)
                VALUES(:owner,:profile,2,'{}',repeat('1',64),'cross-owner')"""),
            {"owner": uuid4(), "profile": pg_context.profile_id},
        )
    with pytest.raises(DBAPIError), pg_context.runtime.begin() as conn:
        conn.execute(
            text(
                "UPDATE bonafides.profiles SET current_revision_id=:revision WHERE owner_id=:owner"
            ),
            {"owner": pg_context.owner_id, "revision": uuid4()},
        )
    with pg_context.runtime.begin() as conn:
        assert (
            conn.execute(
                text("SELECT current_revision_id FROM bonafides.profiles WHERE owner_id=:owner"),
                {"owner": pg_context.owner_id},
            ).scalar_one()
            == pg_context.revision_id
        )


@pytest.mark.postgres
def test_immutable_revisions_and_runtime_ddl_denied(pg_context: PGContext) -> None:
    for statement in (
        "UPDATE bonafides.profile_revisions SET reason='mutated' WHERE owner_id=:owner",
        "CREATE TABLE bonafides.forbidden_runtime_ddl(id integer)",
        "UPDATE bonafides.audit_events SET action='mutated' WHERE owner_id=:owner",
        "DELETE FROM bonafides.audit_events WHERE owner_id=:owner",
        "UPDATE bonafides.schema_migrations SET source_sha256=repeat('f',64)",
    ):
        with pytest.raises(DBAPIError), pg_context.runtime.begin() as conn:
            conn.execute(text(statement), {"owner": pg_context.owner_id})


@pytest.mark.postgres
def test_idempotency_replays_before_stale_version(pg_context: PGContext) -> None:
    calls = 0

    def effect(conn: Connection) -> tuple[int, dict[str, object]]:
        nonlocal calls
        calls += 1
        conn.execute(
            text("UPDATE bonafides.profiles SET version=version+1 WHERE owner_id=:owner"),
            {"owner": pg_context.owner_id},
        )
        return 202, {"id": "persisted-response", "version": 2}

    def execute(body: dict[str, object], match: str = '"1"') -> tuple[int, dict[str, object]]:
        with pg_context.runtime.begin() as conn:
            return command(
                conn,
                pg_context.owner_id,
                "POST",
                "/proof",
                {"profile": str(pg_context.profile_id)},
                body,
                match,
                "same-request-key-0001",
                "profile",
                pg_context.profile_id,
                effect,
            )

    first = execute({"fixture": "SYN-CORE"})
    assert execute({"fixture": "SYN-CORE"}) == first
    assert calls == 1
    with pytest.raises(Problem) as mismatch:
        execute({"fixture": "different"})
    assert mismatch.value.status == 409
    with pytest.raises(Problem) as stale, pg_context.runtime.begin() as conn:
        command(
            conn,
            pg_context.owner_id,
            "POST",
            "/proof",
            {},
            {},
            '"1"',
            "another-request-key-1",
            "profile",
            pg_context.profile_id,
            effect,
        )
    assert stale.value.status == 412
