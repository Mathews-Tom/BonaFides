# SPDX-License-Identifier: MIT OR Apache-2.0
"""Namespace-bound daily backup slots and distinct ciphertext/copy receipts."""

import hashlib
from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0021_daily_backups"
down_revision = "0020_maintenance_recovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    source = (Path(__file__).resolve().parents[1] / "sql" / "024_daily_backups.up.sql").read_text(
        encoding="utf-8"
    )
    connection.exec_driver_sql(source, execution_options={"no_parameters": True})
    connection.execute(
        text("UPDATE bonafides.schema_migrations SET source_sha256=:sha WHERE version=24"),
        {"sha": hashlib.sha256(source.encode()).hexdigest()},
    )


def downgrade() -> None:
    raise RuntimeError("Daily backup downgrade requires a verified installation checkpoint")
