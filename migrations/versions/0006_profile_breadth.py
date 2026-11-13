# SPDX-License-Identifier: MIT OR Apache-2.0
"""Source-backed publications and scoped writing facts; existing pins stay unchanged."""

from __future__ import annotations

import hashlib
from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0006_profile_breadth"
down_revision = "0005_installation_updates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "sql" / "006_profile_breadth.up.sql"
    ).read_bytes()
    connection = op.get_bind()
    connection.exec_driver_sql(source.decode("utf-8"), execution_options={"no_parameters": True})
    connection.execute(
        text("INSERT INTO bonafides.schema_migrations(version,source_sha256) VALUES(6,:sha)"),
        {"sha": hashlib.sha256(source).hexdigest()},
    )


def downgrade() -> None:
    raise RuntimeError("Profile history is durable; restore a verified backup instead")
