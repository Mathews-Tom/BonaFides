# SPDX-License-Identifier: MIT OR Apache-2.0
"""Add OL-005 language claims; preserve all applied migration sources."""

import hashlib
from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0002_profiles"
down_revision = "0001_m0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    source = (Path(__file__).resolve().parents[1] / "sql" / "005_profiles.up.sql").read_text(
        encoding="utf-8"
    )
    connection.exec_driver_sql(source, execution_options={"no_parameters": True})
    connection.execute(
        text("UPDATE offerloom.schema_migrations SET source_sha256=:sha WHERE version=5"),
        {"sha": hashlib.sha256(source.encode()).hexdigest()},
    )


def downgrade() -> None:
    raise RuntimeError("Profile history is durable; restore a verified backup instead")
