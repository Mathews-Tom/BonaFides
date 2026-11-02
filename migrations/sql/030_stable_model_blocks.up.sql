-- SPDX-License-Identifier: MIT OR Apache-2.0
SET search_path=bonafides,pg_catalog;

-- Alembic appends capability-bound copies where model-run identities still exist.
-- Application erasure can remove those observations without removing route blocks.
-- Mapped capability keys survive later qualification. Unmapped historical rows
-- apply only while the legacy route identity is unchanged. This pre-release
-- upgrade requires old document receipts to be re-measured; that identity change
-- ends an unmapped block, with the measured run providing replacement evidence.
COMMENT ON COLUMN local_model_route_blocks.route_sha256 IS
 'Capability SHA-256 for new blocks; legacy hashes apply only while route identity is unchanged.';
INSERT INTO schema_migrations(version,source_sha256) VALUES(30,repeat('0',64));
