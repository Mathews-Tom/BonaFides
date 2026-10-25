# SPDX-License-Identifier: MIT OR Apache-2.0
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.engine import make_url

from offerloom.config import Settings


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
                "options": "-c search_path=offerloom,pg_catalog -c statement_timeout=15000 -c lock_timeout=5000",
            },
        )

    @contextmanager
    def transaction(self) -> Iterator[Connection]:
        with self.engine.begin() as conn:
            yield conn

    def ready(self) -> bool:
        with self.engine.connect() as conn:
            version = conn.execute(
                text("SELECT max(version) FROM offerloom.schema_migrations")
            ).scalar_one()
            return bool(
                version == 12
                and conn.execute(text("SHOW server_version_num")).scalar_one()[:2] == "18"
            )

    def close(self) -> None:
        self.engine.dispose()
