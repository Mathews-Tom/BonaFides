# SPDX-License-Identifier: MIT OR Apache-2.0
"""Fence scoped erasure and journal live-byte cleanup before reopening admission."""

import hashlib
from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0017_portable_erasure"
down_revision = "0016_backup_generations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    source = (
        Path(__file__).resolve().parents[1] / "sql" / "020_portable_erasure.up.sql"
    ).read_text(encoding="utf-8")
    connection.exec_driver_sql(source, execution_options={"no_parameters": True})
    connection.execute(
        text("UPDATE offerloom.schema_migrations SET source_sha256=:sha WHERE version=20"),
        {"sha": hashlib.sha256(source.encode()).hexdigest()},
    )


def downgrade() -> None:
    raise RuntimeError("Erasure recovery requires a verified installation checkpoint")
