-- SPDX-License-Identifier: MIT OR Apache-2.0
-- PostgreSQL 18; execute once with psql -X -v ON_ERROR_STOP=1 -f this-file.
-- Requires CREATE on the selected empty database, not SUPERUSER or extensions.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';
CREATE SCHEMA bonafides;
REVOKE ALL ON SCHEMA bonafides FROM PUBLIC;
SET LOCAL search_path = bonafides, pg_catalog;
CREATE TABLE schema_migrations (version integer PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now());
CREATE TYPE workflow_state AS ENUM ('pending','running','waiting_for_approval','retryable_failure','permanent_failure','cancelled','succeeded');
CREATE TYPE application_stage AS ENUM ('preparing','applied','screening','interview','offer','accepted','rejected','withdrawn');
CREATE TABLE owners (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), singleton boolean NOT NULL DEFAULT true UNIQUE CHECK(singleton),
 login_name text NOT NULL UNIQUE CHECK(length(login_name) BETWEEN 1 AND 120), password_hash text NOT NULL,
 version bigint NOT NULL DEFAULT 1 CHECK(version > 0), created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE sessions (
 owner_id uuid NOT NULL REFERENCES owners(id) ON DELETE CASCADE, id uuid NOT NULL DEFAULT gen_random_uuid(),
 token_sha256 text NOT NULL UNIQUE CHECK(token_sha256 ~ '^[0-9a-f]{64}$'), csrf_sha256 text NOT NULL,
 expires_at timestamptz NOT NULL, last_seen_at timestamptz NOT NULL DEFAULT now(), created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(owner_id,id), CHECK(expires_at > created_at)
);
CREATE INDEX sessions_expiry_idx ON sessions(expires_at);
CREATE TABLE owner_settings (
 owner_id uuid PRIMARY KEY REFERENCES owners(id) ON DELETE CASCADE, version bigint NOT NULL DEFAULT 1 CHECK(version > 0),
 privacy_mode text NOT NULL DEFAULT 'local_only' CHECK(privacy_mode IN ('local_only','hosted_opt_in')),
 timezone text NOT NULL DEFAULT 'UTC', settings jsonb NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(settings)='object'),
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE blobs (
 owner_id uuid NOT NULL REFERENCES owners(id) ON DELETE CASCADE, id uuid NOT NULL DEFAULT gen_random_uuid(),
 sha256 text NOT NULL CHECK(sha256 ~ '^[0-9a-f]{64}$'), byte_length bigint NOT NULL CHECK(byte_length BETWEEN 0 AND 1073741824),
 media_type text NOT NULL, storage_key text NOT NULL CHECK(storage_key ~ '^[0-9a-f]{2}/[0-9a-f]{64}$'),
 state text NOT NULL DEFAULT 'ready' CHECK(state IN ('staged','ready','deleting','missing')),
 created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz, PRIMARY KEY(owner_id,id), UNIQUE(owner_id,sha256)
);
CREATE INDEX blobs_gc_idx ON blobs(owner_id,state,expires_at);
CREATE TABLE profiles (
 owner_id uuid NOT NULL REFERENCES owners(id) ON DELETE CASCADE, id uuid NOT NULL DEFAULT gen_random_uuid(),
 version bigint NOT NULL DEFAULT 1 CHECK(version > 0), current_revision_id uuid, created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(owner_id,id), UNIQUE(owner_id)
);
CREATE TABLE profile_revisions (
 owner_id uuid NOT NULL, id uuid NOT NULL DEFAULT gen_random_uuid(), profile_id uuid NOT NULL,
 revision_no bigint NOT NULL CHECK(revision_no > 0), snapshot jsonb NOT NULL CHECK(jsonb_typeof(snapshot)='object'),
 content_sha256 text NOT NULL CHECK(content_sha256 ~ '^[0-9a-f]{64}$'), reason text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(owner_id,id), UNIQUE(owner_id,profile_id,revision_no),
 UNIQUE(owner_id,profile_id,id), FOREIGN KEY(owner_id,profile_id) REFERENCES profiles(owner_id,id) ON DELETE CASCADE
);
ALTER TABLE profiles ADD FOREIGN KEY(owner_id,id,current_revision_id) REFERENCES profile_revisions(owner_id,profile_id,id) DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE professional_claims (
 owner_id uuid NOT NULL, id uuid NOT NULL DEFAULT gen_random_uuid(), profile_id uuid NOT NULL,
 kind text NOT NULL CHECK(kind IN ('identity','experience','education','skill','project','certification','preference','other')),
 version bigint NOT NULL DEFAULT 1 CHECK(version > 0), current_revision_id uuid, archived_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(owner_id,id),
 FOREIGN KEY(owner_id,profile_id) REFERENCES profiles(owner_id,id) ON DELETE CASCADE
);
CREATE TABLE claim_revisions (
 owner_id uuid NOT NULL, id uuid NOT NULL DEFAULT gen_random_uuid(), claim_id uuid NOT NULL,
 revision_no bigint NOT NULL CHECK(revision_no > 0), payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object'),
 content_sha256 text NOT NULL CHECK(content_sha256 ~ '^[0-9a-f]{64}$'), created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(owner_id,id), UNIQUE(owner_id,claim_id,revision_no), UNIQUE(owner_id,claim_id,id),
 FOREIGN KEY(owner_id,claim_id) REFERENCES professional_claims(owner_id,id) ON DELETE CASCADE
);
ALTER TABLE professional_claims ADD FOREIGN KEY(owner_id,id,current_revision_id) REFERENCES claim_revisions(owner_id,claim_id,id) DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE evidence_records (
 owner_id uuid NOT NULL REFERENCES owners(id) ON DELETE CASCADE, id uuid NOT NULL DEFAULT gen_random_uuid(),
 version bigint NOT NULL DEFAULT 1 CHECK(version > 0), current_revision_id uuid, archived_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(owner_id,id)
);
CREATE TABLE evidence_revisions (
 owner_id uuid NOT NULL, id uuid NOT NULL DEFAULT gen_random_uuid(), evidence_id uuid NOT NULL,
 revision_no bigint NOT NULL CHECK(revision_no > 0), payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object'), blob_id uuid,
 content_sha256 text NOT NULL CHECK(content_sha256 ~ '^[0-9a-f]{64}$'), created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(owner_id,id), UNIQUE(owner_id,evidence_id,revision_no), UNIQUE(owner_id,evidence_id,id),
 FOREIGN KEY(owner_id,evidence_id) REFERENCES evidence_records(owner_id,id) ON DELETE CASCADE,
 FOREIGN KEY(owner_id,blob_id) REFERENCES blobs(owner_id,id) ON DELETE RESTRICT
);
ALTER TABLE evidence_records ADD FOREIGN KEY(owner_id,id,current_revision_id) REFERENCES evidence_revisions(owner_id,evidence_id,id) DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE claim_evidence_links (
 owner_id uuid NOT NULL, claim_revision_id uuid NOT NULL, evidence_revision_id uuid NOT NULL,
 support_kind text NOT NULL CHECK(support_kind IN ('direct','user_asserted','context')), excerpt text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(owner_id,claim_revision_id,evidence_revision_id),
 FOREIGN KEY(owner_id,claim_revision_id) REFERENCES claim_revisions(owner_id,id) ON DELETE CASCADE,
 FOREIGN KEY(owner_id,evidence_revision_id) REFERENCES evidence_revisions(owner_id,id) ON DELETE RESTRICT
);
CREATE TABLE profile_revision_claims (
 owner_id uuid NOT NULL, profile_revision_id uuid NOT NULL, claim_revision_id uuid NOT NULL,
 PRIMARY KEY(owner_id,profile_revision_id,claim_revision_id),
 FOREIGN KEY(owner_id,profile_revision_id) REFERENCES profile_revisions(owner_id,id) ON DELETE CASCADE,
 FOREIGN KEY(owner_id,claim_revision_id) REFERENCES claim_revisions(owner_id,id) ON DELETE RESTRICT
);
CREATE TABLE source_configs (
 owner_id uuid NOT NULL REFERENCES owners(id) ON DELETE CASCADE, id uuid NOT NULL DEFAULT gen_random_uuid(),
 source_kind text NOT NULL CHECK(source_kind IN ('manual','greenhouse')), board_token text, enabled boolean NOT NULL DEFAULT false,
 terms_url text, terms_reviewed_at timestamptz, terms_revision text, allowed_host text,
 request_limit_per_minute integer NOT NULL DEFAULT 10 CHECK(request_limit_per_minute BETWEEN 1 AND 60),
 last_success_at timestamptz, last_error_code text, next_allowed_at timestamptz, version bigint NOT NULL DEFAULT 1 CHECK(version > 0),
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(owner_id,id), UNIQUE(owner_id,source_kind,board_token),
 CHECK(NOT enabled OR source_kind='manual' OR (terms_reviewed_at IS NOT NULL AND terms_url IS NOT NULL AND board_token IS NOT NULL AND allowed_host='boards-api.greenhouse.io'))
);
CREATE TABLE job_postings (
 owner_id uuid NOT NULL REFERENCES owners(id) ON DELETE CASCADE, id uuid NOT NULL DEFAULT gen_random_uuid(),
 title text NOT NULL CHECK(length(title) BETWEEN 1 AND 500), company text NOT NULL CHECK(length(company) BETWEEN 1 AND 500),
 location_text text NOT NULL DEFAULT '', description_text text NOT NULL CHECK(length(description_text)<=200000),
 canonical_url text, dedupe_key text NOT NULL, version bigint NOT NULL DEFAULT 1 CHECK(version > 0),
 status text NOT NULL DEFAULT 'open' CHECK(status IN ('open','closed','unknown')), requirements jsonb NOT NULL DEFAULT '[]' CHECK(jsonb_typeof(requirements)='array'),
 requirements_reviewed_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 search_vector tsvector GENERATED ALWAYS AS (to_tsvector('simple'::regconfig,coalesce(title,'') || ' ' || coalesce(company,'') || ' ' || coalesce(location_text,'') || ' ' || coalesce(description_text,''))) STORED,
 PRIMARY KEY(owner_id,id), UNIQUE(owner_id,dedupe_key)
);
CREATE INDEX job_postings_fts_idx ON job_postings USING gin(search_vector);
CREATE INDEX job_postings_list_idx ON job_postings(owner_id,created_at,id);
CREATE TABLE job_source_observations (
 owner_id uuid NOT NULL, id uuid NOT NULL DEFAULT gen_random_uuid(), posting_id uuid NOT NULL, source_config_id uuid NOT NULL,
 external_id text NOT NULL, content_sha256 text NOT NULL CHECK(content_sha256 ~ '^[0-9a-f]{64}$'),
 fetched_at timestamptz NOT NULL DEFAULT now(), source_url text NOT NULL, snapshot jsonb NOT NULL CHECK(jsonb_typeof(snapshot)='object'),
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(owner_id,id), UNIQUE(owner_id,source_config_id,external_id,content_sha256),
 FOREIGN KEY(owner_id,posting_id) REFERENCES job_postings(owner_id,id) ON DELETE CASCADE,
 FOREIGN KEY(owner_id,source_config_id) REFERENCES source_configs(owner_id,id) ON DELETE RESTRICT
);
CREATE INDEX observations_posting_idx ON job_source_observations(owner_id,posting_id,fetched_at DESC);
CREATE TABLE saved_opportunities (
 owner_id uuid NOT NULL, id uuid NOT NULL DEFAULT gen_random_uuid(), posting_id uuid NOT NULL,
 disposition text NOT NULL DEFAULT 'saved' CHECK(disposition IN ('saved','dismissed','archived')), version bigint NOT NULL DEFAULT 1 CHECK(version > 0),
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(owner_id,id), UNIQUE(owner_id,posting_id),
 FOREIGN KEY(owner_id,posting_id) REFERENCES job_postings(owner_id,id) ON DELETE RESTRICT
);
CREATE TABLE applications (
 owner_id uuid NOT NULL, id uuid NOT NULL DEFAULT gen_random_uuid(), opportunity_id uuid NOT NULL,
 current_stage application_stage NOT NULL DEFAULT 'preparing', version bigint NOT NULL DEFAULT 1 CHECK(version > 0),
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(owner_id,id),
 FOREIGN KEY(owner_id,opportunity_id) REFERENCES saved_opportunities(owner_id,id) ON DELETE RESTRICT
);
CREATE INDEX applications_list_idx ON applications(owner_id,created_at,id);
CREATE TABLE application_stage_events (
 owner_id uuid NOT NULL, id uuid NOT NULL DEFAULT gen_random_uuid(), application_id uuid NOT NULL,
 from_stage application_stage, to_stage application_stage NOT NULL, effective_at timestamptz NOT NULL,
 reason text NOT NULL DEFAULT '', created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(owner_id,id),
 FOREIGN KEY(owner_id,application_id) REFERENCES applications(owner_id,id) ON DELETE CASCADE
);
CREATE INDEX stage_events_app_idx ON application_stage_events(owner_id,application_id,effective_at,id);
CREATE TABLE application_notes (
 owner_id uuid NOT NULL, id uuid NOT NULL DEFAULT gen_random_uuid(), application_id uuid NOT NULL,
 body text NOT NULL CHECK(length(body)<=50000), version bigint NOT NULL DEFAULT 1 CHECK(version > 0),
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(owner_id,id),
 FOREIGN KEY(owner_id,application_id) REFERENCES applications(owner_id,id) ON DELETE CASCADE
);
CREATE TABLE application_contacts (
 owner_id uuid NOT NULL, id uuid NOT NULL DEFAULT gen_random_uuid(), application_id uuid NOT NULL,
 display_name text NOT NULL, email text, role_label text, version bigint NOT NULL DEFAULT 1 CHECK(version > 0),
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(owner_id,id),
 FOREIGN KEY(owner_id,application_id) REFERENCES applications(owner_id,id) ON DELETE CASCADE
);
CREATE TABLE application_attachments (
 owner_id uuid NOT NULL, id uuid NOT NULL DEFAULT gen_random_uuid(), application_id uuid NOT NULL, blob_id uuid NOT NULL,
 display_name text NOT NULL CHECK(length(display_name)<=255), created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(owner_id,id),
 FOREIGN KEY(owner_id,application_id) REFERENCES applications(owner_id,id) ON DELETE CASCADE,
 FOREIGN KEY(owner_id,blob_id) REFERENCES blobs(owner_id,id) ON DELETE RESTRICT
);
CREATE TABLE appointments (
 owner_id uuid NOT NULL, id uuid NOT NULL DEFAULT gen_random_uuid(), application_id uuid NOT NULL,
 kind text NOT NULL CHECK(kind IN ('interview','follow_up')), title text NOT NULL, starts_at timestamptz NOT NULL,
 timezone text NOT NULL, local_start timestamp NOT NULL, duration_minutes integer CHECK(duration_minutes BETWEEN 1 AND 1440),
 completed_at timestamptz, version bigint NOT NULL DEFAULT 1 CHECK(version > 0), created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(owner_id,id),
 FOREIGN KEY(owner_id,application_id) REFERENCES applications(owner_id,id) ON DELETE CASCADE
);
CREATE INDEX appointments_due_idx ON appointments(owner_id,starts_at) WHERE completed_at IS NULL;
CREATE TABLE workflow_runs (
 owner_id uuid NOT NULL REFERENCES owners(id) ON DELETE CASCADE, id uuid NOT NULL DEFAULT gen_random_uuid(),
 kind text NOT NULL CHECK(kind IN ('profile_import','discovery','posting_extract','matching','document_generate','pdf_render','portable_export','portable_import','privacy_delete','backup','restore_validate','integration_sync','approved_send')),
 state workflow_state NOT NULL DEFAULT 'pending', input jsonb NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(input)='object'), result jsonb,
 dedupe_key text NOT NULL, request_sha256 text NOT NULL CHECK(request_sha256 ~ '^[0-9a-f]{64}$'),
 version bigint NOT NULL DEFAULT 1 CHECK(version>0), attempt integer NOT NULL DEFAULT 0, max_attempts integer NOT NULL DEFAULT 3 CHECK(max_attempts BETWEEN 1 AND 10),
 next_attempt_at timestamptz NOT NULL DEFAULT now(), lease_holder uuid, lease_expires_at timestamptz, fence bigint NOT NULL DEFAULT 0 CHECK(fence>=0),
 cancel_requested_at timestamptz, deadline_at timestamptz NOT NULL, finished_at timestamptz,
 token_budget bigint NOT NULL DEFAULT 0 CHECK(token_budget>=0), tokens_used bigint NOT NULL DEFAULT 0 CHECK(tokens_used>=0),
 cost_budget_microunits bigint NOT NULL DEFAULT 0 CHECK(cost_budget_microunits>=0), cost_used_microunits bigint NOT NULL DEFAULT 0 CHECK(cost_used_microunits>=0),
 budget_currency text NOT NULL DEFAULT 'USD' CHECK(budget_currency ~ '^[A-Z]{3}$'), last_error_code text,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(owner_id,id), UNIQUE(owner_id,kind,dedupe_key),
 CHECK(attempt BETWEEN 0 AND max_attempts), CHECK(tokens_used<=token_budget), CHECK(cost_used_microunits<=cost_budget_microunits),
 CHECK((lease_holder IS NULL)=(lease_expires_at IS NULL)), CHECK((state='running')=(lease_holder IS NOT NULL)),
 CHECK((state IN ('succeeded','cancelled','permanent_failure'))=(finished_at IS NOT NULL)), CHECK(deadline_at>created_at)
);
CREATE INDEX workflow_claim_idx ON workflow_runs(next_attempt_at,created_at,id) WHERE state IN ('pending','retryable_failure');
CREATE INDEX workflow_expired_idx ON workflow_runs(lease_expires_at) WHERE state='running';
CREATE INDEX workflow_owner_list_idx ON workflow_runs(owner_id,created_at,id);
CREATE TABLE workflow_steps (
 owner_id uuid NOT NULL, id uuid NOT NULL DEFAULT gen_random_uuid(), workflow_run_id uuid NOT NULL,
 step_key text NOT NULL, attempt integer NOT NULL CHECK(attempt>0), fence bigint NOT NULL CHECK(fence>0),
 state text NOT NULL CHECK(state IN ('pending','running','succeeded','failed','waiting_for_approval')),
 request_sha256 text, provider_operation_id text, delivery_state text NOT NULL DEFAULT 'not_attempted' CHECK(delivery_state IN ('not_attempted','known_failed','confirmed','unknown')),
 checkpoint jsonb NOT NULL DEFAULT '{}', error_code text, created_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz,
 PRIMARY KEY(owner_id,id), UNIQUE(owner_id,workflow_run_id,step_key,attempt),
 FOREIGN KEY(owner_id,workflow_run_id) REFERENCES workflow_runs(owner_id,id) ON DELETE CASCADE
);
CREATE TABLE model_runs (
 owner_id uuid NOT NULL, id uuid NOT NULL DEFAULT gen_random_uuid(), workflow_run_id uuid NOT NULL,
 provider_key text NOT NULL, model_id text NOT NULL, model_revision text, task_kind text NOT NULL,
 prompt_version text NOT NULL, input_sha256 text NOT NULL CHECK(input_sha256 ~ '^[0-9a-f]{64}$'), output_sha256 text,
 parameters jsonb NOT NULL CHECK(jsonb_typeof(parameters)='object'), egress_class text NOT NULL CHECK(egress_class IN ('local','hosted_opt_in')),
 input_tokens bigint NOT NULL DEFAULT 0 CHECK(input_tokens>=0), output_tokens bigint NOT NULL DEFAULT 0 CHECK(output_tokens>=0),
 reserved_tokens bigint NOT NULL CHECK(reserved_tokens>=0), reserved_cost_microunits bigint NOT NULL CHECK(reserved_cost_microunits>=0),
 actual_cost_microunits bigint CHECK(actual_cost_microunits>=0), status text NOT NULL CHECK(status IN ('reserved','running','succeeded','failed','unknown_usage')),
 created_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz, PRIMARY KEY(owner_id,id),
 FOREIGN KEY(owner_id,workflow_run_id) REFERENCES workflow_runs(owner_id,id) ON DELETE RESTRICT
);
CREATE INDEX model_runs_workflow_idx ON model_runs(owner_id,workflow_run_id);
CREATE TABLE match_assessments (
 owner_id uuid NOT NULL, id uuid NOT NULL DEFAULT gen_random_uuid(), posting_id uuid NOT NULL, profile_revision_id uuid NOT NULL,
 model_run_id uuid, posting_version bigint NOT NULL, hard_constraints jsonb NOT NULL CHECK(jsonb_typeof(hard_constraints)='array'),
 evidence_coverage jsonb NOT NULL CHECK(jsonb_typeof(evidence_coverage)='array'), explanation text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(owner_id,id),
 FOREIGN KEY(owner_id,posting_id) REFERENCES job_postings(owner_id,id) ON DELETE CASCADE,
 FOREIGN KEY(owner_id,profile_revision_id) REFERENCES profile_revisions(owner_id,id) ON DELETE RESTRICT,
 FOREIGN KEY(owner_id,model_run_id) REFERENCES model_runs(owner_id,id) ON DELETE RESTRICT
);
CREATE TABLE document_drafts (
 owner_id uuid NOT NULL, id uuid NOT NULL DEFAULT gen_random_uuid(), application_id uuid NOT NULL,
 kind text NOT NULL CHECK(kind IN ('resume','cover_letter')), profile_revision_id uuid NOT NULL,
 current_revision_id uuid, version bigint NOT NULL DEFAULT 1 CHECK(version>0), stale_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(owner_id,id), UNIQUE(owner_id,application_id,kind),
 FOREIGN KEY(owner_id,application_id) REFERENCES applications(owner_id,id) ON DELETE CASCADE,
 FOREIGN KEY(owner_id,profile_revision_id) REFERENCES profile_revisions(owner_id,id) ON DELETE RESTRICT
);
CREATE TABLE document_revisions (
 owner_id uuid NOT NULL, id uuid NOT NULL DEFAULT gen_random_uuid(), document_draft_id uuid NOT NULL,
 revision_no bigint NOT NULL CHECK(revision_no>0), profile_revision_id uuid NOT NULL, model_run_id uuid,
 body jsonb NOT NULL CHECK(jsonb_typeof(body)='object'), posting_snapshot jsonb NOT NULL CHECK(jsonb_typeof(posting_snapshot)='object'),
 provenance_snapshot jsonb NOT NULL CHECK(jsonb_typeof(provenance_snapshot)='object'), template_version text NOT NULL,
 content_sha256 text NOT NULL CHECK(content_sha256 ~ '^[0-9a-f]{64}$'), origin text NOT NULL CHECK(origin IN ('generated','manual','restore')),
 reason text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(owner_id,id),
 UNIQUE(owner_id,document_draft_id,revision_no), UNIQUE(owner_id,document_draft_id,id),
 FOREIGN KEY(owner_id,document_draft_id) REFERENCES document_drafts(owner_id,id) ON DELETE CASCADE,
 FOREIGN KEY(owner_id,profile_revision_id) REFERENCES profile_revisions(owner_id,id) ON DELETE RESTRICT,
 FOREIGN KEY(owner_id,model_run_id) REFERENCES model_runs(owner_id,id) ON DELETE RESTRICT
);
ALTER TABLE document_drafts ADD FOREIGN KEY(owner_id,id,current_revision_id) REFERENCES document_revisions(owner_id,document_draft_id,id) DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE document_claim_links (
 owner_id uuid NOT NULL, id uuid NOT NULL DEFAULT gen_random_uuid(), document_revision_id uuid NOT NULL,
 claim_path text NOT NULL, claim_revision_id uuid, evidence_revision_id uuid, excerpt text NOT NULL,
 status text NOT NULL CHECK(status IN ('supported','unsupported','user_asserted')), created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(owner_id,id), UNIQUE(owner_id,document_revision_id,claim_path,claim_revision_id,evidence_revision_id),
 FOREIGN KEY(owner_id,document_revision_id) REFERENCES document_revisions(owner_id,id) ON DELETE CASCADE,
 FOREIGN KEY(owner_id,claim_revision_id) REFERENCES claim_revisions(owner_id,id) ON DELETE RESTRICT,
 FOREIGN KEY(owner_id,evidence_revision_id) REFERENCES evidence_revisions(owner_id,id) ON DELETE RESTRICT,
 CHECK(status<>'supported' OR (claim_revision_id IS NOT NULL AND evidence_revision_id IS NOT NULL))
);
CREATE TABLE document_reviews (
 owner_id uuid NOT NULL, id uuid NOT NULL DEFAULT gen_random_uuid(), document_revision_id uuid NOT NULL,
 decision text NOT NULL CHECK(decision IN ('accepted','changes_requested')), reviewed_content_sha256 text NOT NULL CHECK(reviewed_content_sha256 ~ '^[0-9a-f]{64}$'),
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(owner_id,id), UNIQUE(owner_id,document_revision_id),
 FOREIGN KEY(owner_id,document_revision_id) REFERENCES document_revisions(owner_id,id) ON DELETE CASCADE
);
CREATE TABLE document_artifacts (
 owner_id uuid NOT NULL, id uuid NOT NULL DEFAULT gen_random_uuid(), document_revision_id uuid NOT NULL, blob_id uuid NOT NULL,
 renderer_version text NOT NULL, format text NOT NULL CHECK(format IN ('pdf','docx')), created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(owner_id,id), UNIQUE(owner_id,document_revision_id,renderer_version,format),
 FOREIGN KEY(owner_id,document_revision_id) REFERENCES document_revisions(owner_id,id) ON DELETE CASCADE,
 FOREIGN KEY(owner_id,blob_id) REFERENCES blobs(owner_id,id) ON DELETE RESTRICT
);
CREATE TABLE external_accounts (
 owner_id uuid NOT NULL REFERENCES owners(id) ON DELETE CASCADE, id uuid NOT NULL DEFAULT gen_random_uuid(),
 provider_key text NOT NULL, external_subject_hash text NOT NULL, granted_scopes text[] NOT NULL DEFAULT '{}',
 status text NOT NULL DEFAULT 'disconnected' CHECK(status IN ('connected','revocation_pending','disconnected','reauthorize')),
 capability_flags jsonb NOT NULL DEFAULT '{}', sync_cursor_ciphertext bytea, sync_cursor_key_id text,
 version bigint NOT NULL DEFAULT 1 CHECK(version>0), created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(owner_id,id), UNIQUE(owner_id,provider_key,external_subject_hash)
);
CREATE TABLE encrypted_credentials (
 owner_id uuid NOT NULL REFERENCES owners(id) ON DELETE CASCADE, id uuid NOT NULL DEFAULT gen_random_uuid(), external_account_id uuid,
 purpose text NOT NULL, key_id text NOT NULL, algorithm text NOT NULL CHECK(algorithm='AES-256-GCM'),
 nonce bytea NOT NULL CHECK(octet_length(nonce)=12), ciphertext bytea NOT NULL CHECK(octet_length(ciphertext)>=16),
 aad_version integer NOT NULL DEFAULT 1 CHECK(aad_version=1), version bigint NOT NULL DEFAULT 1 CHECK(version>0),
 created_at timestamptz NOT NULL DEFAULT now(), rotated_at timestamptz, PRIMARY KEY(owner_id,id), UNIQUE(key_id,nonce),
 FOREIGN KEY(owner_id,external_account_id) REFERENCES external_accounts(owner_id,id) ON DELETE CASCADE
);
CREATE TABLE oauth_transactions (
 owner_id uuid NOT NULL REFERENCES owners(id) ON DELETE CASCADE, id uuid NOT NULL DEFAULT gen_random_uuid(),
 state_sha256 text NOT NULL UNIQUE CHECK(state_sha256 ~ '^[0-9a-f]{64}$'), session_id uuid NOT NULL, provider_key text NOT NULL,
 pkce_ciphertext bytea NOT NULL, key_id text NOT NULL, nonce bytea NOT NULL CHECK(octet_length(nonce)=12),
 redirect_uri text NOT NULL, requested_scopes text[] NOT NULL, expires_at timestamptz NOT NULL, consumed_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(owner_id,id),
 FOREIGN KEY(owner_id,session_id) REFERENCES sessions(owner_id,id) ON DELETE CASCADE, CHECK(expires_at>created_at)
);
CREATE TABLE action_approvals (
 owner_id uuid NOT NULL, id uuid NOT NULL DEFAULT gen_random_uuid(), workflow_run_id uuid NOT NULL,
 action_kind text NOT NULL CHECK(action_kind IN ('send_mail','calendar_write','replace_workspace','privacy_delete')),
 payload_sha256 text NOT NULL CHECK(payload_sha256 ~ '^[0-9a-f]{64}$'), recipient_sha256 text CHECK(recipient_sha256 ~ '^[0-9a-f]{64}$'),
 document_revision_id uuid, expires_at timestamptz NOT NULL, approved_at timestamptz, consumed_at timestamptz,
 version bigint NOT NULL DEFAULT 1 CHECK(version>0), created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(owner_id,id),
 FOREIGN KEY(owner_id,workflow_run_id) REFERENCES workflow_runs(owner_id,id) ON DELETE CASCADE,
 FOREIGN KEY(owner_id,document_revision_id) REFERENCES document_revisions(owner_id,id) ON DELETE RESTRICT,
 CHECK(expires_at>created_at), CHECK(consumed_at IS NULL OR approved_at IS NOT NULL),
 CHECK(action_kind<>'send_mail' OR (recipient_sha256 IS NOT NULL AND document_revision_id IS NOT NULL))
);
CREATE TABLE idempotency_records (
 owner_id uuid NOT NULL REFERENCES owners(id) ON DELETE CASCADE, route_key text NOT NULL, key text NOT NULL CHECK(length(key) BETWEEN 16 AND 200),
 request_sha256 text NOT NULL CHECK(request_sha256 ~ '^[0-9a-f]{64}$'), response_status integer CHECK(response_status BETWEEN 200 AND 599),
 response_body jsonb, workflow_run_id uuid, created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL,
 PRIMARY KEY(owner_id,route_key,key), FOREIGN KEY(owner_id,workflow_run_id) REFERENCES workflow_runs(owner_id,id) ON DELETE RESTRICT,
 CHECK(expires_at>created_at)
);
CREATE INDEX idempotency_expiry_idx ON idempotency_records(expires_at);
CREATE TABLE outbox_events (
 owner_id uuid NOT NULL REFERENCES owners(id) ON DELETE CASCADE, id uuid NOT NULL DEFAULT gen_random_uuid(),
 aggregate_type text NOT NULL, aggregate_id uuid NOT NULL, aggregate_version bigint NOT NULL CHECK(aggregate_version>0),
 event_type text NOT NULL, schema_version integer NOT NULL DEFAULT 1 CHECK(schema_version=1), payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object'),
 dedupe_key text NOT NULL, available_at timestamptz NOT NULL DEFAULT now(), attempt integer NOT NULL DEFAULT 0 CHECK(attempt>=0),
 lease_holder uuid, lease_expires_at timestamptz, fence bigint NOT NULL DEFAULT 0 CHECK(fence>=0), delivered_at timestamptz, last_error_code text,
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(owner_id,id), UNIQUE(owner_id,dedupe_key),
 CHECK((lease_holder IS NULL)=(lease_expires_at IS NULL))
);
CREATE INDEX outbox_delivery_idx ON outbox_events(available_at,created_at,id) WHERE delivered_at IS NULL;
CREATE TABLE event_receipts (
 owner_id uuid NOT NULL, consumer_key text NOT NULL, event_id uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(owner_id,consumer_key,event_id), FOREIGN KEY(owner_id,event_id) REFERENCES outbox_events(owner_id,id) ON DELETE CASCADE
);
CREATE TABLE audit_events (
 owner_id uuid NOT NULL REFERENCES owners(id) ON DELETE CASCADE, id uuid NOT NULL DEFAULT gen_random_uuid(),
 action text NOT NULL, actor_kind text NOT NULL CHECK(actor_kind IN ('owner','worker','operator')), actor_id text NOT NULL,
 request_id uuid NOT NULL, entity_type text NOT NULL, entity_id uuid, detail jsonb NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(detail)='object'),
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(owner_id,id)
);
CREATE INDEX audit_list_idx ON audit_events(owner_id,created_at,id);
CREATE TABLE transfer_manifests (
 owner_id uuid NOT NULL, id uuid NOT NULL DEFAULT gen_random_uuid(), workflow_run_id uuid NOT NULL,
 kind text NOT NULL CHECK(kind IN ('portable_export','import_preview','backup','restore_validation','privacy_delete')),
 format_version integer NOT NULL DEFAULT 1 CHECK(format_version>0), blob_id uuid, manifest_sha256 text,
 snapshot_at timestamptz, preview jsonb, expires_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(owner_id,id),
 FOREIGN KEY(owner_id,workflow_run_id) REFERENCES workflow_runs(owner_id,id) ON DELETE RESTRICT,
 FOREIGN KEY(owner_id,blob_id) REFERENCES blobs(owner_id,id) ON DELETE RESTRICT
);
-- Installation maintenance fence is not portable user data.
CREATE TABLE installation_state (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 namespace_id uuid NOT NULL DEFAULT gen_random_uuid() UNIQUE,
 maintenance_active boolean NOT NULL DEFAULT false,
 maintenance_epoch bigint NOT NULL DEFAULT 0 CHECK(maintenance_epoch>=0),
 bootstrap_token_sha256 text CHECK(bootstrap_token_sha256 ~ '^[0-9a-f]{64}$'),
 bootstrap_consumed_at timestamptz
);
INSERT INTO installation_state(singleton) VALUES(true);
ALTER TABLE workflow_runs ADD COLUMN maintenance_epoch bigint NOT NULL DEFAULT 0 CHECK(maintenance_epoch>=0);
CREATE INDEX claim_profile_idx ON professional_claims(owner_id,profile_id);
CREATE INDEX evidence_blob_idx ON evidence_revisions(owner_id,blob_id);
CREATE INDEX links_evidence_idx ON claim_evidence_links(owner_id,evidence_revision_id);
CREATE INDEX snapshot_claim_idx ON profile_revision_claims(owner_id,claim_revision_id);
CREATE INDEX applications_opportunity_idx ON applications(owner_id,opportunity_id);
CREATE INDEX notes_application_idx ON application_notes(owner_id,application_id,created_at,id);
CREATE INDEX contacts_application_idx ON application_contacts(owner_id,application_id,created_at,id);
CREATE INDEX attachments_application_idx ON application_attachments(owner_id,application_id,created_at,id);
CREATE INDEX attachments_blob_idx ON application_attachments(owner_id,blob_id);
CREATE INDEX steps_workflow_idx ON workflow_steps(owner_id,workflow_run_id,created_at,id);
CREATE INDEX assessments_posting_idx ON match_assessments(owner_id,posting_id,created_at,id);
CREATE INDEX assessments_profile_idx ON match_assessments(owner_id,profile_revision_id);
CREATE INDEX drafts_profile_idx ON document_drafts(owner_id,profile_revision_id);
CREATE INDEX doc_revisions_profile_idx ON document_revisions(owner_id,profile_revision_id);
CREATE INDEX doc_revisions_model_idx ON document_revisions(owner_id,model_run_id);
CREATE INDEX doc_links_claim_idx ON document_claim_links(owner_id,claim_revision_id);
CREATE INDEX doc_links_evidence_idx ON document_claim_links(owner_id,evidence_revision_id);
CREATE INDEX doc_artifacts_blob_idx ON document_artifacts(owner_id,blob_id);
CREATE INDEX credentials_account_idx ON encrypted_credentials(owner_id,external_account_id);
CREATE INDEX approvals_workflow_idx ON action_approvals(owner_id,workflow_run_id);
CREATE INDEX transfers_workflow_idx ON transfer_manifests(owner_id,workflow_run_id);
CREATE INDEX transfers_blob_idx ON transfer_manifests(owner_id,blob_id);
-- Revision UPDATE is forbidden, including through the table owner. DELETE is only
-- permitted to the lifecycle service through explicit retention/deletion operations.
CREATE FUNCTION reject_revision_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'immutable record; append a new revision' USING ERRCODE='55000'; END $$;
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['profile_revisions','claim_revisions','evidence_revisions','claim_evidence_links','profile_revision_claims','job_source_observations','application_stage_events','document_revisions','document_claim_links','document_reviews','audit_events'] LOOP
 EXECUTE format('CREATE TRIGGER immutable_update BEFORE UPDATE ON bonafides.%I FOR EACH ROW EXECUTE FUNCTION bonafides.reject_revision_update()',t);
 END LOOP;
END $$;
-- A worker MUST call this in the SAME transaction before any domain/outbox write.
-- Row lock serializes cancellation/reclaim with publishing; never hold it over I/O.
CREATE FUNCTION assert_workflow_fence(p_owner uuid,p_run uuid,p_worker uuid,p_fence bigint)
RETURNS void LANGUAGE plpgsql SET search_path=bonafides,pg_catalog AS $$
DECLARE w workflow_runs;
BEGIN
 -- Acquire maintenance fence BEFORE workflow lock, consistently with API writers.
 PERFORM 1 FROM installation_state WHERE singleton AND NOT maintenance_active FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'installation maintenance active' USING ERRCODE='55000'; END IF;
 SELECT * INTO w FROM workflow_runs WHERE owner_id=p_owner AND id=p_run FOR UPDATE;
 IF NOT FOUND OR w.state<>'running' OR w.lease_holder IS DISTINCT FROM p_worker OR w.fence<>p_fence
 OR w.lease_expires_at<=clock_timestamp() OR w.cancel_requested_at IS NOT NULL OR w.deadline_at<=clock_timestamp()
 THEN RAISE EXCEPTION 'workflow lease fenced, cancelled, or expired' USING ERRCODE='55000'; END IF;
 IF w.maintenance_epoch<>(SELECT maintenance_epoch FROM installation_state WHERE singleton)
 THEN RAISE EXCEPTION 'installation epoch changed' USING ERRCODE='55000'; END IF;
END $$;
REVOKE ALL ON ALL TABLES IN SCHEMA bonafides FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA bonafides FROM PUBLIC;
INSERT INTO schema_migrations(version) VALUES (1);
COMMIT;
