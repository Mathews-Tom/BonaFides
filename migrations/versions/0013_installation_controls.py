# SPDX-License-Identifier: MIT OR Apache-2.0
"""Give installation maintenance an independently fenced, fail-closed controller."""

import hashlib
from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0013_installation_controls"
down_revision = "0012_provider_executions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    source = (
        Path(__file__).resolve().parents[1] / "sql" / "016_installation_controls.up.sql"
    ).read_text(encoding="utf-8")
    connection.exec_driver_sql(source, execution_options={"no_parameters": True})
    connection.execute(
        text("UPDATE offerloom.schema_migrations SET source_sha256=:sha WHERE version=16"),
        {"sha": hashlib.sha256(source.encode()).hexdigest()},
    )


def downgrade() -> None:
    raise RuntimeError("Installation control requires a verified maintenance recovery")
