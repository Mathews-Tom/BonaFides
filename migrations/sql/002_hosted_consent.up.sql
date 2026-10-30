-- SPDX-License-Identifier: MIT OR Apache-2.0
-- Run after 001_initial.up.sql as the same ordinary migration/schema owner.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';
SET LOCAL search_path=bonafides,pg_catalog;
CREATE TABLE hosted_egress_consents (
 owner_id uuid NOT NULL REFERENCES owners(id) ON DELETE CASCADE,
 id uuid NOT NULL DEFAULT gen_random_uuid(), workflow_run_id uuid NOT NULL,
 provider_key text NOT NULL, model_id text NOT NULL,
 purpose text NOT NULL CHECK(purpose IN ('profile_import','posting_extract','matching','document_generate')),
 input_scope_sha256 text NOT NULL CHECK(input_scope_sha256 ~ '^[0-9a-f]{64}$'),
 settings_version bigint NOT NULL CHECK(settings_version>0),
 acknowledged_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL,
 revoked_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(owner_id,id), UNIQUE(owner_id,workflow_run_id),
 FOREIGN KEY(owner_id,workflow_run_id) REFERENCES workflow_runs(owner_id,id) ON DELETE RESTRICT,
 CHECK(expires_at>acknowledged_at)
);
ALTER TABLE model_runs ADD COLUMN hosted_consent_id uuid;
ALTER TABLE model_runs ADD FOREIGN KEY(owner_id,hosted_consent_id) REFERENCES hosted_egress_consents(owner_id,id) ON DELETE RESTRICT;
ALTER TABLE model_runs ADD CHECK(egress_class<>'hosted_opt_in' OR hosted_consent_id IS NOT NULL);
CREATE INDEX model_runs_consent_idx ON model_runs(owner_id,hosted_consent_id);
ALTER TABLE applications ADD COLUMN archived_at timestamptz;
CREATE INDEX applications_active_idx ON applications(owner_id,created_at,id) WHERE archived_at IS NULL;
ALTER TABLE owner_settings ADD COLUMN workspace_version bigint NOT NULL DEFAULT 1 CHECK(workspace_version>0);
REVOKE ALL ON hosted_egress_consents FROM PUBLIC;
INSERT INTO schema_migrations(version) VALUES(2);
COMMIT;
