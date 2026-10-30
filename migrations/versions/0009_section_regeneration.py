# SPDX-License-Identifier: MIT OR Apache-2.0
"""Add immutable target-bound section regeneration proposals."""

import hashlib
from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0009_section_regeneration"
down_revision = "0008_documents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    source = (
        Path(__file__).resolve().parents[1] / "sql" / "012_section_regeneration.up.sql"
    ).read_text(encoding="utf-8")
    connection.exec_driver_sql(source, execution_options={"no_parameters": True})
    connection.execute(
        text("UPDATE bonafides.schema_migrations SET source_sha256=:sha WHERE version=12"),
        {"sha": hashlib.sha256(source.encode()).hexdigest()},
    )


def downgrade() -> None:
    raise RuntimeError("Document proposal history requires a verified backup restore")
