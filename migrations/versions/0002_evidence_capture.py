# SPDX-License-Identifier: MIT OR Apache-2.0
"""Permit explicitly approved capture provenance without rewriting history."""

from __future__ import annotations

import hashlib
from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0002_evidence_capture"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "sql" / "002_evidence_capture.up.sql"
    ).read_bytes()
    connection = op.get_bind()
    connection.exec_driver_sql(source.decode("utf-8"), execution_options={"no_parameters": True})
    connection.execute(
        text("INSERT INTO bonafides.schema_migrations(version,source_sha256) VALUES(2,:sha)"),
        {"sha": hashlib.sha256(source).hexdigest()},
    )


def downgrade() -> None:
    raise RuntimeError("Capture provenance is immutable; restore a verified backup instead")
