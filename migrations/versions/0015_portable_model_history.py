# SPDX-License-Identifier: MIT OR Apache-2.0
"""Keep imported model provenance separate from invocation authority."""

import hashlib
from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0015_portable_model_history"
down_revision = "0014_portable_exports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    source = (
        Path(__file__).resolve().parents[1] / "sql" / "018_portable_model_history.up.sql"
    ).read_text(encoding="utf-8")
    connection.exec_driver_sql(source, execution_options={"no_parameters": True})
    connection.execute(
        text("UPDATE bonafides.schema_migrations SET source_sha256=:sha WHERE version=18"),
        {"sha": hashlib.sha256(source.encode()).hexdigest()},
    )


def downgrade() -> None:
    raise RuntimeError("Portable history recovery requires a verified installation checkpoint")
