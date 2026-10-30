# SPDX-License-Identifier: MIT OR Apache-2.0
"""Add durable OL-006 local import review without modifying applied SQL."""

import hashlib
from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0003_imports"
down_revision = "0002_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    source = (Path(__file__).resolve().parents[1] / "sql" / "006_imports.up.sql").read_text(
        encoding="utf-8"
    )
    connection.exec_driver_sql(source, execution_options={"no_parameters": True})
    connection.execute(
        text("UPDATE bonafides.schema_migrations SET source_sha256=:sha WHERE version=6"),
        {"sha": hashlib.sha256(source.encode()).hexdigest()},
    )


def downgrade() -> None:
    raise RuntimeError("Import and profile history are durable; restore a verified backup instead")
