# SPDX-License-Identifier: MIT OR Apache-2.0
"""Allow exact undo of tone-only document conversation replies."""

from __future__ import annotations

import hashlib
from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0009_conversation_tone_undo"
down_revision = "0008_document_conversations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "sql" / "009_conversation_tone_undo.up.sql"
    ).read_bytes()
    connection = op.get_bind()
    connection.exec_driver_sql(source.decode("utf-8"), execution_options={"no_parameters": True})
    connection.execute(
        text("INSERT INTO bonafides.schema_migrations(version,source_sha256) VALUES(9,:sha)"),
        {"sha": hashlib.sha256(source).hexdigest()},
    )


def downgrade() -> None:
    raise RuntimeError("Conversation undo history is durable; restore a verified backup instead")
