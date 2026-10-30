# SPDX-License-Identifier: MIT OR Apache-2.0
"""Stream portable archives and retain replacement candidates in bounded chunks."""

import hashlib
from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0024_streamed_portability"
down_revision = "0023_erasure_reservation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    source = (
        Path(__file__).resolve().parents[1] / "sql" / "027_streamed_portability.up.sql"
    ).read_text(encoding="utf-8")
    connection.exec_driver_sql(source, execution_options={"no_parameters": True})
    connection.execute(
        text("UPDATE bonafides.schema_migrations SET source_sha256=:sha WHERE version=27"),
        {"sha": hashlib.sha256(source.encode()).hexdigest()},
    )


def downgrade() -> None:
    raise RuntimeError("Streamed portability downgrade requires a verified installation checkpoint")
