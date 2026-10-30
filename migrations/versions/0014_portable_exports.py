# SPDX-License-Identifier: MIT OR Apache-2.0
"""Track expiring owner exports and safely settle read-only export failures."""

import hashlib
from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0014_portable_exports"
down_revision = "0013_installation_controls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    source = (
        Path(__file__).resolve().parents[1] / "sql" / "017_portable_exports.up.sql"
    ).read_text(encoding="utf-8")
    connection.exec_driver_sql(source, execution_options={"no_parameters": True})
    connection.execute(
        text("UPDATE bonafides.schema_migrations SET source_sha256=:sha WHERE version=17"),
        {"sha": hashlib.sha256(source.encode()).hexdigest()},
    )


def downgrade() -> None:
    raise RuntimeError("Export recovery requires a verified installation checkpoint")
