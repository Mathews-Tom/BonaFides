# SPDX-License-Identifier: MIT OR Apache-2.0
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.engine import make_url

from bonafides.config import Settings
from bonafides.execution_guard import ExecutionGuard

SCHEMA_VERSION = 1
# Checksum of migrations/sql/001_baseline.up.sql; every supported ledger starts with it.
BASELINE_SHA256 = "d59f2522f5e4be27f5ad367c23bc3b0979af4c9d48831249b36697df8992d3d8"
PRE_BASELINE_GUIDANCE = (
    "This data predates the 0.1.0 schema baseline and is never upgraded in place. "
    "Run the last pre-baseline BonaFides build (commit 771776e) against it, export a "
    "bonafides-workspace/1 archive, then import that archive into a fresh install."
)


class PreBaselineDatabase(RuntimeError):
    """The database ledger was written by the migration chain the baseline replaced."""

    code = "pre_baseline_database"

    def __init__(self) -> None:
        super().__init__(f"{self.code}: {PRE_BASELINE_GUIDANCE}")


def schema_ledger(conn: Connection) -> dict[int, str]:
    return {
        row.version: row.source_sha256
        for row in conn.execute(
            text("SELECT version,source_sha256 FROM bonafides.schema_migrations")
        )
    }


def require_baseline(ledger: dict[int, str]) -> None:
    """Refuse every non-empty ledger other than this baseline's single row."""
    if ledger and ledger != {SCHEMA_VERSION: BASELINE_SHA256}:
        raise PreBaselineDatabase


class Database:
    def __init__(self, settings: Settings | str) -> None:
        dsn = settings.database_dsn if isinstance(settings, Settings) else settings
        url = make_url(dsn)
        if url.drivername not in {"postgresql", "postgresql+psycopg"}:
            raise ValueError("Database requires PostgreSQL with psycopg")
        self.engine: Engine = create_engine(
            url.set(drivername="postgresql+psycopg"),
            pool_size=5,
            max_overflow=5,
            pool_timeout=5,
            pool_pre_ping=True,
            hide_parameters=True,
            connect_args={
                "connect_timeout": 5,
                "options": "-c search_path=bonafides,pg_catalog -c statement_timeout=15000 -c lock_timeout=5000",
            },
        )
        try:
            self.execution_guard = (
                ExecutionGuard(settings.blob_root) if isinstance(settings, Settings) else None
            )
        except BaseException:
            self.engine.dispose()
            raise

    @contextmanager
    def transaction(self) -> Iterator[Connection]:
        with self.engine.begin() as conn:
            conn.info["execution_guard"] = self.execution_guard
            if self.execution_guard is not None:
                self.execution_guard.verify()
            yield conn

    def ready(self) -> bool:
        with self.engine.connect() as conn:
            ledger = schema_ledger(conn)
            require_baseline(ledger)
            if max(ledger, default=None) != SCHEMA_VERSION:
                return False
            if self.execution_guard is not None:
                identity = self.execution_guard.verify()
                stored = conn.execute(
                    text(
                        "SELECT execution_guard_id FROM bonafides.installation_state WHERE singleton"
                    )
                ).scalar_one()
                if stored is not None and stored != identity:
                    return False
            return bool(conn.execute(text("SHOW server_version_num")).scalar_one()[:2] == "18")

    def close(self) -> None:
        try:
            self.engine.dispose()
        finally:
            if self.execution_guard is not None:
                self.execution_guard.close()
