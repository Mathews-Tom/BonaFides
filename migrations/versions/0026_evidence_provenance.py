# SPDX-License-Identifier: MIT OR Apache-2.0
"""Record source type, origin, privacy class and employer ownership on evidence revisions."""

import hashlib
from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0026_evidence_provenance"
down_revision = "0025_structured_claims"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    source = (
        Path(__file__).resolve().parents[1] / "sql" / "029_evidence_provenance.up.sql"
    ).read_text(encoding="utf-8")
    connection.exec_driver_sql(source, execution_options={"no_parameters": True})
    connection.execute(
        text("UPDATE bonafides.schema_migrations SET source_sha256=:sha WHERE version=29"),
        {"sha": hashlib.sha256(source.encode()).hexdigest()},
    )


def downgrade() -> None:
    raise RuntimeError("Evidence provenance downgrade requires a verified installation checkpoint")
