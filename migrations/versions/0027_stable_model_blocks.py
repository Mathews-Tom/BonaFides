# SPDX-License-Identifier: MIT OR Apache-2.0
"""Preserve permanent live-evaluation blocks across qualification receipt changes."""

import hashlib
from pathlib import Path

from alembic import op
from sqlalchemy import text

from bonafides.frozen_migrations.stable_model_blocks_v1 import preserve_route_blocks

revision = "0027_stable_model_blocks"
down_revision = "0026_evidence_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    source = (
        Path(__file__).resolve().parents[1] / "sql" / "030_stable_model_blocks.up.sql"
    ).read_text(encoding="utf-8")
    connection.exec_driver_sql(source, execution_options={"no_parameters": True})
    preserve_route_blocks(connection)
    connection.execute(
        text("UPDATE bonafides.schema_migrations SET source_sha256=:sha WHERE version=30"),
        {"sha": hashlib.sha256(source.encode()).hexdigest()},
    )


def downgrade() -> None:
    raise RuntimeError("Model block downgrade requires a verified installation checkpoint")
