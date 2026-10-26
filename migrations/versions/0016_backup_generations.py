# SPDX-License-Identifier: MIT OR Apache-2.0
"""Separate created installation backups from operator-verified recovery."""

import hashlib
from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0016_backup_generations"
down_revision = "0015_portable_model_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    source = (
        Path(__file__).resolve().parents[1] / "sql" / "019_backup_generations.up.sql"
    ).read_text(encoding="utf-8")
    connection.exec_driver_sql(source, execution_options={"no_parameters": True})
    connection.execute(
        text("UPDATE offerloom.schema_migrations SET source_sha256=:sha WHERE version=19"),
        {"sha": hashlib.sha256(source.encode()).hexdigest()},
    )


def downgrade() -> None:
    raise RuntimeError("Backup history recovery requires a verified installation checkpoint")
