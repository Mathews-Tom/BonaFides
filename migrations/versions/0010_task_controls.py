# SPDX-License-Identifier: MIT OR Apache-2.0
"""Add durable task audit and exact single-action approval bindings."""

import hashlib
from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0010_task_controls"
down_revision = "0009_section_regeneration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    source = (Path(__file__).resolve().parents[1] / "sql" / "013_task_controls.up.sql").read_text(
        encoding="utf-8"
    )
    connection.exec_driver_sql(source, execution_options={"no_parameters": True})
    connection.execute(
        text("UPDATE bonafides.schema_migrations SET source_sha256=:sha WHERE version=13"),
        {"sha": hashlib.sha256(source.encode()).hexdigest()},
    )


def downgrade() -> None:
    raise RuntimeError("Task and approval history requires a verified backup restore")
