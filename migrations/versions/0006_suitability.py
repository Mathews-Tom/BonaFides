# SPDX-License-Identifier: MIT OR Apache-2.0
"""Add immutable suitability review review without rewriting applied migration sources."""

import hashlib
from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0006_suitability"
down_revision = "0005_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    source = (Path(__file__).resolve().parents[1] / "sql" / "009_suitability.up.sql").read_text(
        encoding="utf-8"
    )
    connection.exec_driver_sql(source, execution_options={"no_parameters": True})
    connection.execute(
        text("UPDATE bonafides.schema_migrations SET source_sha256=:sha WHERE version=9"),
        {"sha": hashlib.sha256(source.encode()).hexdigest()},
    )


def downgrade() -> None:
    raise RuntimeError("Suitability review history requires a verified backup restore")
