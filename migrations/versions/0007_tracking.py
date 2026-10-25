# SPDX-License-Identifier: MIT OR Apache-2.0
"""Add manual tracking metadata without rewriting retained history."""

import hashlib
from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0007_tracking"
down_revision = "0006_suitability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    source = (Path(__file__).resolve().parents[1] / "sql" / "010_tracking.up.sql").read_text(
        encoding="utf-8"
    )
    connection.exec_driver_sql(source, execution_options={"no_parameters": True})
    connection.execute(
        text("UPDATE offerloom.schema_migrations SET source_sha256=:sha WHERE version=10"),
        {"sha": hashlib.sha256(source.encode()).hexdigest()},
    )


def downgrade() -> None:
    raise RuntimeError("Application tracking history requires a verified backup restore")
