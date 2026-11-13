# SPDX-License-Identifier: MIT OR Apache-2.0
"""Retain owner portrait bytes while any profile or document history pins them."""

from __future__ import annotations

import hashlib
from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0007_profile_portrait_lifetime"
down_revision = "0006_profile_breadth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "sql" / "007_profile_portrait_lifetime.up.sql"
    ).read_bytes()
    connection = op.get_bind()
    connection.exec_driver_sql(source.decode("utf-8"), execution_options={"no_parameters": True})
    connection.execute(
        text("INSERT INTO bonafides.schema_migrations(version,source_sha256) VALUES(7,:sha)"),
        {"sha": hashlib.sha256(source).hexdigest()},
    )


def downgrade() -> None:
    raise RuntimeError("Portrait history is durable; restore a verified backup instead")
