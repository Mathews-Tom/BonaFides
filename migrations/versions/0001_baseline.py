# SPDX-License-Identifier: MIT OR Apache-2.0
"""The 0.1.0 schema baseline; later migrations append to it and never edit it."""

import hashlib
from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    source = (Path(__file__).resolve().parents[1] / "sql" / "001_baseline.up.sql").read_text(
        encoding="utf-8"
    )
    connection.exec_driver_sql(source, execution_options={"no_parameters": True})
    connection.execute(
        text("INSERT INTO bonafides.schema_migrations(version,source_sha256) VALUES(1,:sha)"),
        {"sha": hashlib.sha256(source.encode()).hexdigest()},
    )
    connection.execute(
        text(
            "REVOKE ALL ON TABLE bonafides_migrations.alembic_version FROM PUBLIC,bonafides_runtime"
        )
    )


def downgrade() -> None:
    raise RuntimeError("Baseline downgrade is destructive; restore a verified backup instead")
