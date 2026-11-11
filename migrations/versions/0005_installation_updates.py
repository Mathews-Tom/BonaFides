# SPDX-License-Identifier: MIT OR Apache-2.0
"""Installation-local opt-in release cache, excluded from portable career content."""

from __future__ import annotations

import hashlib
from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0005_installation_updates"
down_revision = "0004_import_structuring"
branch_labels = None
depends_on = None


def upgrade() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "sql" / "005_installation_updates.up.sql"
    ).read_bytes()
    connection = op.get_bind()
    connection.exec_driver_sql(source.decode("utf-8"), execution_options={"no_parameters": True})
    connection.execute(
        text("INSERT INTO bonafides.schema_migrations(version,source_sha256) VALUES(5,:sha)"),
        {"sha": hashlib.sha256(source).hexdigest()},
    )


def downgrade() -> None:
    raise RuntimeError("Installation history is durable; restore a verified backup instead")
