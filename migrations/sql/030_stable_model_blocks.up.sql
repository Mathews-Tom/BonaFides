-- SPDX-License-Identifier: MIT OR Apache-2.0
SET search_path=bonafides,pg_catalog;

-- Alembic appends capability-bound copies from recorded model-run identities.
-- Historical receipt-bound rows remain intact; qualification never clears blocks.
COMMENT ON COLUMN local_model_route_blocks.route_sha256 IS
 'Capability SHA-256 for new blocks; historical receipt-bound rows are retained.';
INSERT INTO schema_migrations(version,source_sha256) VALUES(30,repeat('0',64));
