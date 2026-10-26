# SPDX-License-Identifier: MIT OR Apache-2.0
"""Reviewed SQL 001–003 plus additive M0 runtime security contract."""

import hashlib
from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0001_m0"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    directory = Path(__file__).resolve().parents[1] / "sql"
    for version, name in enumerate(
        (
            "001_initial.up.sql",
            "002_hosted_consent.up.sql",
            "003_base_resume.up.sql",
            "004_runtime.up.sql",
        ),
        1,
    ):
        source = (directory / name).read_text(encoding="utf-8")
        # The reviewed source is retained byte-for-byte. Alembic owns the transaction.
        sql = "\n".join(
            line for line in source.splitlines() if line.strip() not in {"BEGIN;", "COMMIT;"}
        )
        connection.exec_driver_sql(sql, execution_options={"no_parameters": True})
        if version == 1:
            connection.execute(
                text(
                    "ALTER TABLE offerloom.schema_migrations ADD COLUMN source_sha256 text CHECK(source_sha256 ~ '^[0-9a-f]{64}$')"
                )
            )
        connection.execute(
            text(
                "UPDATE offerloom.schema_migrations SET source_sha256=:sha WHERE version=:version"
            ),
            {"sha": hashlib.sha256(source.encode()).hexdigest(), "version": version},
        )
    connection.execute(
        text("ALTER TABLE offerloom.schema_migrations ALTER COLUMN source_sha256 SET NOT NULL")
    )
    connection.execute(
        text(
            "REVOKE ALL ON TABLE offerloom_migrations.alembic_version FROM PUBLIC,offerloom_runtime"
        )
    )


def downgrade() -> None:
    raise RuntimeError("M0 downgrade is destructive; restore an explicitly verified backup instead")
