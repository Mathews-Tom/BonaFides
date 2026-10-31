# SPDX-License-Identifier: MIT OR Apache-2.0
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.engine import make_url

from bonafides.config import Settings
from bonafides.execution_guard import ExecutionGuard

SCHEMA_VERSION = 28


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
            version = conn.execute(
                text("SELECT max(version) FROM bonafides.schema_migrations")
            ).scalar_one()
            if version != SCHEMA_VERSION:
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
