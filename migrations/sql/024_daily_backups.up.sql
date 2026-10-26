-- SPDX-License-Identifier: MIT OR Apache-2.0
SET search_path=offerloom,pg_catalog;

-- Byte verification and copy receipts never confer native recovery authority.
ALTER TABLE installation_backups
 ADD COLUMN cipher_verified_at timestamptz,
 ADD COLUMN copy_verified_at timestamptz,
 ADD COLUMN pruned_at timestamptz,
 ADD COLUMN copy_pruned_at timestamptz;
GRANT UPDATE(cipher_verified_at,copy_verified_at,pruned_at,copy_pruned_at)
 ON installation_backups TO offerloom_runtime;

CREATE TABLE backup_daily_runs (
 id uuid PRIMARY KEY,
 owner_id uuid NOT NULL REFERENCES owners(id) ON DELETE CASCADE,
 namespace_id uuid NOT NULL,
 slot_date date NOT NULL,
 config_sha256 text NOT NULL CHECK(config_sha256 ~ '^[0-9a-f]{64}$'),
 storage_sha256 text NOT NULL CHECK(storage_sha256 ~ '^[0-9a-f]{64}$'),
 generation_id uuid REFERENCES installation_backups(id) ON DELETE SET NULL,
 state text NOT NULL CHECK(state IN ('running','created','copied','failed','pruned','disabled')),
 started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 created_at timestamptz,
 copied_at timestamptz,
 pruned_at timestamptz,
 finished_at timestamptz,
 failure_code text CHECK(failure_code IN (
  'backup_schedule_capture_failed','backup_schedule_copy_failed',
  'backup_schedule_retention_failed','backup_schedule_interrupted',
  'backup_schedule_restored_disabled')),
 UNIQUE(namespace_id,slot_date),
 CHECK(state NOT IN ('failed','pruned','disabled') OR finished_at IS NOT NULL),
 CHECK(state NOT IN ('created','copied','pruned') OR created_at IS NOT NULL),
 CHECK(state<>'copied' OR copied_at IS NOT NULL)
);
CREATE INDEX backup_daily_runs_retention ON backup_daily_runs(namespace_id,storage_sha256,slot_date DESC);
CREATE UNIQUE INDEX backup_daily_runs_active ON backup_daily_runs(namespace_id)
 WHERE finished_at IS NULL;
REVOKE ALL ON backup_daily_runs FROM PUBLIC,offerloom_runtime;
GRANT SELECT,INSERT,UPDATE ON backup_daily_runs TO offerloom_runtime;
INSERT INTO schema_migrations(version,source_sha256) VALUES(24,repeat('0',64));
