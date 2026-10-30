-- SPDX-License-Identifier: MIT OR Apache-2.0
SET search_path=bonafides,pg_catalog;

-- A local timeout, lease expiry or dead socket is not proof of remote completion.
-- Only a matching terminal provider response or verified operator recovery clears it.
CREATE TABLE provider_executions (
 id uuid PRIMARY KEY,
 namespace_id uuid NOT NULL,
 maintenance_epoch bigint NOT NULL CHECK(maintenance_epoch>=0),
 participant_id uuid NOT NULL,
 owner_id uuid NOT NULL,
 model_run_id uuid NOT NULL,
 started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(owner_id,model_run_id),
 FOREIGN KEY(owner_id,model_run_id) REFERENCES model_runs(owner_id,id) ON DELETE CASCADE
);
CREATE INDEX provider_executions_namespace_idx
 ON provider_executions(namespace_id,maintenance_epoch);
REVOKE ALL ON provider_executions FROM PUBLIC,bonafides_runtime;
GRANT SELECT,INSERT,DELETE ON provider_executions TO bonafides_runtime;
INSERT INTO schema_migrations(version,source_sha256) VALUES(15,repeat('0',64));
