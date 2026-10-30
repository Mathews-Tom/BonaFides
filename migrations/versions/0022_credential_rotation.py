# SPDX-License-Identifier: MIT OR Apache-2.0
"""Explicit credential-key rotation and origin-bound AAD version two."""

import hashlib
from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0022_credential_rotation"
down_revision = "0021_daily_backups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    source = (
        Path(__file__).resolve().parents[1] / "sql" / "025_credential_rotation.up.sql"
    ).read_text(encoding="utf-8")
    connection.exec_driver_sql(source, execution_options={"no_parameters": True})
    connection.execute(
        text("UPDATE bonafides.schema_migrations SET source_sha256=:sha WHERE version=25"),
        {"sha": hashlib.sha256(source.encode()).hexdigest()},
    )


def downgrade() -> None:
    raise RuntimeError("Credential rotation downgrade requires a verified installation checkpoint")
