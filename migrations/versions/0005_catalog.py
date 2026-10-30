# SPDX-License-Identifier: MIT OR Apache-2.0
"""Add job catalog catalog state without rewriting applied source history."""

import hashlib
from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0005_catalog"
down_revision = "0004_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    source = (Path(__file__).resolve().parents[1] / "sql" / "008_catalog.up.sql").read_text(
        encoding="utf-8"
    )
    connection.exec_driver_sql(source, execution_options={"no_parameters": True})
    connection.execute(
        text("UPDATE bonafides.schema_migrations SET source_sha256=:sha WHERE version=8"),
        {"sha": hashlib.sha256(source.encode()).hexdigest()},
    )


def downgrade() -> None:
    raise RuntimeError("Catalog decisions and source history require a verified backup restore")
