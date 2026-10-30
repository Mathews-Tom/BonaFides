# SPDX-License-Identifier: MIT OR Apache-2.0
"""Rewrite free-text experience and project claims in place as structured claims."""

import hashlib
from pathlib import Path
from uuid import UUID

from alembic import op
from sqlalchemy import text

from bonafides.frozen_migrations.structured_claims_v1 import restructure_free_text_claims

revision = "0025_structured_claims"
down_revision = "0024_streamed_portability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    source = (
        Path(__file__).resolve().parents[1] / "sql" / "028_structured_claims.up.sql"
    ).read_text(encoding="utf-8")
    connection.exec_driver_sql(source, execution_options={"no_parameters": True})
    connection.execute(
        text("UPDATE bonafides.schema_migrations SET source_sha256=:sha WHERE version=28"),
        {"sha": hashlib.sha256(source.encode()).hexdigest()},
    )
    owners = connection.execute(
        text(
            "SELECT owner_id FROM bonafides.profiles WHERE current_revision_id IS NOT NULL ORDER BY owner_id"
        )
    ).scalars()
    for owner_id in list(owners):
        restructure_free_text_claims(connection, UUID(str(owner_id)))


def downgrade() -> None:
    raise RuntimeError("Structured claims downgrade requires a verified installation checkpoint")
