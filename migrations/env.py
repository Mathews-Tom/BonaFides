# SPDX-License-Identifier: MIT OR Apache-2.0
"""Only the application migration entrypoint supplies an authorized connection."""

from alembic import context

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("Use python -m bonafides migrate with a migration DSN secret")
context.configure(
    connection=connection, transactional_ddl=True, version_table_schema="bonafides_migrations"
)
with context.begin_transaction():
    context.run_migrations()
