# SPDX-License-Identifier: MIT OR Apache-2.0
"""Privileged kernel-excluded recovery of guarded interrupted actors."""

import hashlib
from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0020_maintenance_recovery"
down_revision = "0019_portable_previews"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    source = (
        Path(__file__).resolve().parents[1] / "sql" / "023_offline_maintenance_recovery.up.sql"
    ).read_text(encoding="utf-8")
    connection.exec_driver_sql(source, execution_options={"no_parameters": True})
    connection.execute(
        text("UPDATE offerloom.schema_migrations SET source_sha256=:sha WHERE version=23"),
        {"sha": hashlib.sha256(source.encode()).hexdigest()},
    )


def downgrade() -> None:
    raise RuntimeError("Offline recovery downgrade requires a verified installation checkpoint")
