-- SPDX-License-Identifier: MIT OR Apache-2.0
SET search_path=offerloom,pg_catalog;

-- An entry remains until its executing request/thread positively acknowledges
-- completion. No heartbeat, lease expiry or process restart removes an entry.
CREATE TABLE installation_activities (
 id uuid PRIMARY KEY,
 namespace_id uuid NOT NULL,
 maintenance_epoch bigint NOT NULL CHECK(maintenance_epoch>=0),
 participant_id uuid NOT NULL,
 kind text NOT NULL CHECK(kind IN ('api','worker')),
 execution_id uuid NOT NULL,
 started_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX installation_activities_namespace_idx
 ON installation_activities(namespace_id,maintenance_epoch);
REVOKE ALL ON installation_activities FROM PUBLIC,offerloom_runtime;
GRANT SELECT,INSERT,DELETE ON installation_activities TO offerloom_runtime;

-- Quarantine creates only private, transaction-local clones. The migrator is
-- the installation database owner; no schema CREATE or domain DELETE is added.
DO $$ BEGIN
 EXECUTE format('GRANT TEMPORARY ON DATABASE %I TO offerloom_runtime',current_database());
END $$;
INSERT INTO schema_migrations(version,source_sha256) VALUES(14,repeat('0',64));
