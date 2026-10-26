# SPDX-License-Identifier: MIT OR Apache-2.0
"""Retain uncertain remote inference until positive completion."""

import hashlib
from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0012_provider_executions"
down_revision = "0011_activity_admission"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    source = (
        Path(__file__).resolve().parents[1] / "sql" / "015_provider_executions.up.sql"
    ).read_text(encoding="utf-8")
    connection.exec_driver_sql(source, execution_options={"no_parameters": True})
    connection.execute(
        text("UPDATE offerloom.schema_migrations SET source_sha256=:sha WHERE version=15"),
        {"sha": hashlib.sha256(source.encode()).hexdigest()},
    )


def downgrade() -> None:
    raise RuntimeError("Provider execution requires a verified maintenance recovery")
