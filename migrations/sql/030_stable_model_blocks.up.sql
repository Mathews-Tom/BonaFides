-- SPDX-License-Identifier: MIT OR Apache-2.0
SET search_path=bonafides,pg_catalog;

-- Alembic appends capability-bound copies where model-run identities still exist.
-- Application erasure can remove those observations without removing route blocks.
-- All historical rows remain intact; readers also check the unchanged route's
-- legacy identity hash, preserving unmapped blocks for an unchanged route.
COMMENT ON COLUMN local_model_route_blocks.route_sha256 IS
 'Capability SHA-256 for new blocks; legacy identity hashes remain readable when model-run observations were erased.';
INSERT INTO schema_migrations(version,source_sha256) VALUES(30,repeat('0',64));
