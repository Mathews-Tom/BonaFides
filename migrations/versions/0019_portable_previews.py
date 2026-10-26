# SPDX-License-Identifier: MIT OR Apache-2.0
"""Persist bounded, exact-bound owner previews and erase uploaded candidates."""

import hashlib
from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0019_portable_previews"
down_revision = "0018_portable_scope_binding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    source = (
        Path(__file__).resolve().parents[1] / "sql" / "022_portable_previews.up.sql"
    ).read_text(encoding="utf-8")
    connection.exec_driver_sql(source, execution_options={"no_parameters": True})
    connection.execute(
        text("UPDATE offerloom.schema_migrations SET source_sha256=:sha WHERE version=22"),
        {"sha": hashlib.sha256(source.encode()).hexdigest()},
    )


def downgrade() -> None:
    raise RuntimeError("Preview recovery requires a verified installation checkpoint")
