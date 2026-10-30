# SPDX-License-Identifier: MIT OR Apache-2.0
"""Add job sources sources without rewriting applied SQL or observation history."""

import hashlib
from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0004_sources"
down_revision = "0003_imports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    source = (Path(__file__).resolve().parents[1] / "sql" / "007_sources.up.sql").read_text(
        encoding="utf-8"
    )
    connection.exec_driver_sql(source, execution_options={"no_parameters": True})
    connection.execute(
        text("UPDATE bonafides.schema_migrations SET source_sha256=:sha WHERE version=7"),
        {"sha": hashlib.sha256(source.encode()).hexdigest()},
    )


def downgrade() -> None:
    raise RuntimeError("Source reviews and observation history require a verified backup restore")
