# SPDX-License-Identifier: MIT OR Apache-2.0
"""Preserve the local-model reservation singleton through workspace erasure."""

import hashlib
from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0023_erasure_reservation"
down_revision = "0022_credential_rotation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    source = (
        Path(__file__).resolve().parents[1] / "sql" / "026_erasure_reservation.up.sql"
    ).read_text(encoding="utf-8")
    connection.exec_driver_sql(source, execution_options={"no_parameters": True})
    connection.execute(
        text("UPDATE offerloom.schema_migrations SET source_sha256=:sha WHERE version=26"),
        {"sha": hashlib.sha256(source.encode()).hexdigest()},
    )


def downgrade() -> None:
    raise RuntimeError("Erasure reservation downgrade requires a verified installation checkpoint")
