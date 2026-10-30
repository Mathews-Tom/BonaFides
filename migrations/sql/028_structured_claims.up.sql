-- SPDX-License-Identifier: MIT OR Apache-2.0
-- structured claims keeps every table; the migration step rewrites free-text experience and project
-- claims as claim schema v1 revisions and leaves unrepresentable ones as needing attention.
SET LOCAL search_path=bonafides,pg_catalog;
INSERT INTO schema_migrations(version,source_sha256) VALUES(28,repeat('0',64));
