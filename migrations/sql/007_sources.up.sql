-- SPDX-License-Identifier: MIT OR Apache-2.0
-- OL-007 retains source policy evidence and attributable posting observations.
SET LOCAL search_path=bonafides,pg_catalog;
ALTER TABLE source_configs ADD COLUMN company text NOT NULL DEFAULT 'Unspecified employer'
 CHECK(length(company) BETWEEN 1 AND 500);
ALTER TABLE source_configs ADD COLUMN policy_version bigint NOT NULL DEFAULT 1 CHECK(policy_version>0);
ALTER TABLE source_configs ADD COLUMN current_policy_review_id uuid;
-- An unrepresentable Retry-After remains blocked, never shortened to a timestamp.
ALTER TABLE source_configs ADD COLUMN rate_limit_blocked boolean NOT NULL DEFAULT false;
ALTER TABLE source_configs ADD COLUMN rate_limit_retry_after_seconds text
 CHECK(length(rate_limit_retry_after_seconds) BETWEEN 1 AND 4096
 AND rate_limit_retry_after_seconds ~ '^[0-9]+$');
-- Historical configurations were not reviewed under the board-specific OL-007 policy.
UPDATE source_configs SET enabled=false WHERE source_kind='greenhouse';
ALTER TABLE source_configs ADD CONSTRAINT source_board_shape CHECK(
 (source_kind='manual' AND board_token IS NULL) OR
 (source_kind='greenhouse' AND board_token IS NOT NULL AND board_token ~ '^[A-Za-z0-9_-]{1,120}$'));
CREATE TABLE source_policy_reviews (
 owner_id uuid NOT NULL, id uuid NOT NULL DEFAULT gen_random_uuid(), source_config_id uuid NOT NULL,
 policy_version bigint NOT NULL CHECK(policy_version>0), board_token text NOT NULL CHECK(board_token ~ '^[A-Za-z0-9_-]{1,120}$'),
 company text NOT NULL CHECK(length(company) BETWEEN 1 AND 500),
 terms_url text NOT NULL CHECK(length(terms_url) BETWEEN 1 AND 2048),
 terms_revision text NOT NULL CHECK(length(terms_revision) BETWEEN 1 AND 500),
 allowed_host text NOT NULL CHECK(allowed_host='boards-api.greenhouse.io'),
 request_limit_per_minute integer NOT NULL CHECK(request_limit_per_minute BETWEEN 1 AND 60),
 policy_sha256 text NOT NULL CHECK(policy_sha256 ~ '^[0-9a-f]{64}$'),
 acknowledged_by_owner_id uuid NOT NULL, reviewed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(owner_id,id), UNIQUE(owner_id,source_config_id,policy_version),
 UNIQUE(owner_id,source_config_id,policy_version,id),
 UNIQUE(owner_id,source_config_id,policy_version,id,policy_sha256),
 FOREIGN KEY(owner_id,source_config_id) REFERENCES source_configs(owner_id,id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
 FOREIGN KEY(acknowledged_by_owner_id) REFERENCES owners(id) ON DELETE RESTRICT,
 CHECK(acknowledged_by_owner_id=owner_id)
);
ALTER TABLE source_configs ADD CONSTRAINT source_current_review_fk
 FOREIGN KEY(owner_id,id,policy_version,current_policy_review_id)
 REFERENCES source_policy_reviews(owner_id,source_config_id,policy_version,id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE source_configs ADD CONSTRAINT source_enabled_review CHECK(
 source_kind<>'greenhouse' OR NOT enabled OR
 (current_policy_review_id IS NOT NULL AND terms_reviewed_at IS NOT NULL AND terms_url IS NOT NULL
 AND terms_revision IS NOT NULL AND allowed_host='boards-api.greenhouse.io'));
CREATE FUNCTION guard_source_identity() RETURNS trigger LANGUAGE plpgsql SET search_path=bonafides,pg_catalog AS $$
BEGIN
 IF ROW(NEW.owner_id,NEW.id,NEW.source_kind,NEW.board_token,NEW.created_at)
 IS DISTINCT FROM ROW(OLD.owner_id,OLD.id,OLD.source_kind,OLD.board_token,OLD.created_at)
 THEN RAISE EXCEPTION 'immutable source identity' USING ERRCODE='55000'; END IF;
 IF ROW(NEW.company,NEW.enabled,NEW.terms_url,NEW.terms_revision,NEW.terms_reviewed_at,NEW.allowed_host,
 NEW.request_limit_per_minute,NEW.current_policy_review_id)
 IS DISTINCT FROM ROW(OLD.company,OLD.enabled,OLD.terms_url,OLD.terms_revision,OLD.terms_reviewed_at,OLD.allowed_host,
 OLD.request_limit_per_minute,OLD.current_policy_review_id)
 AND (NEW.policy_version<=OLD.policy_version OR NEW.version<=OLD.version)
 THEN RAISE EXCEPTION 'source policy version required' USING ERRCODE='55000'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER source_identity_guard BEFORE UPDATE ON source_configs FOR EACH ROW EXECUTE FUNCTION guard_source_identity();
CREATE FUNCTION check_source_review() RETURNS trigger LANGUAGE plpgsql SET search_path=bonafides,pg_catalog AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM source_configs s WHERE s.owner_id=NEW.owner_id AND s.id=NEW.id
 AND s.source_kind='greenhouse' AND s.current_policy_review_id IS NOT NULL
 AND NOT EXISTS(SELECT 1 FROM source_policy_reviews r WHERE r.owner_id=s.owner_id AND r.id=s.current_policy_review_id
 AND r.source_config_id=s.id AND r.policy_version=s.policy_version AND r.board_token=s.board_token
 AND r.company=s.company AND r.terms_url=s.terms_url AND r.terms_revision=s.terms_revision
 AND r.allowed_host=s.allowed_host AND r.request_limit_per_minute=s.request_limit_per_minute
 AND r.reviewed_at=s.terms_reviewed_at))
 THEN RAISE EXCEPTION 'source review mismatch' USING ERRCODE='55000'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER source_review_guard AFTER INSERT OR UPDATE ON source_configs
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_source_review();
CREATE TRIGGER source_review_immutable BEFORE UPDATE ON source_policy_reviews FOR EACH ROW EXECUTE FUNCTION reject_revision_update();
ALTER TABLE job_postings ADD COLUMN source_config_id uuid;
ALTER TABLE job_postings ADD COLUMN external_id text;
ALTER TABLE job_postings ADD COLUMN content_sha256 text CHECK(content_sha256 ~ '^[0-9a-f]{64}$');
ALTER TABLE job_postings ADD COLUMN last_observed_at timestamptz;
ALTER TABLE job_postings ADD FOREIGN KEY(owner_id,source_config_id) REFERENCES source_configs(owner_id,id) ON DELETE RESTRICT;
ALTER TABLE job_postings ADD CONSTRAINT posting_source_shape CHECK(
 (source_config_id IS NULL AND external_id IS NULL AND content_sha256 IS NULL AND last_observed_at IS NULL) OR
 (source_config_id IS NOT NULL AND external_id IS NOT NULL AND length(external_id) BETWEEN 1 AND 200
 AND content_sha256 IS NOT NULL AND last_observed_at IS NOT NULL));
CREATE UNIQUE INDEX posting_source_identity ON job_postings(owner_id,source_config_id,external_id);
CREATE FUNCTION guard_posting_source() RETURNS trigger LANGUAGE plpgsql SET search_path=bonafides,pg_catalog AS $$
BEGIN
 IF ROW(NEW.owner_id,NEW.id,NEW.source_config_id,NEW.external_id,NEW.created_at)
 IS DISTINCT FROM ROW(OLD.owner_id,OLD.id,OLD.source_config_id,OLD.external_id,OLD.created_at)
 THEN RAISE EXCEPTION 'immutable posting attribution' USING ERRCODE='55000'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER posting_source_guard BEFORE UPDATE ON job_postings FOR EACH ROW EXECUTE FUNCTION guard_posting_source();
ALTER TABLE job_source_observations ADD COLUMN policy_version bigint NOT NULL DEFAULT 1 CHECK(policy_version>0);
ALTER TABLE job_source_observations ADD COLUMN policy_review_id uuid;
-- Same-content replay preserves identity within a policy; a new review is attributable history.
DO $$ DECLARE c record; BEGIN
 FOR c IN SELECT conname FROM pg_constraint WHERE conrelid='job_source_observations'::regclass AND contype='u' LOOP
  EXECUTE format('ALTER TABLE job_source_observations DROP CONSTRAINT %I',c.conname);
 END LOOP;
END $$;
ALTER TABLE job_source_observations ADD UNIQUE(owner_id,source_config_id,external_id,policy_version,content_sha256);
ALTER TABLE job_source_observations ADD FOREIGN KEY(owner_id,source_config_id,policy_version,policy_review_id)
 REFERENCES source_policy_reviews(owner_id,source_config_id,policy_version,id) ON DELETE RESTRICT;
CREATE INDEX observation_history_cursor ON job_source_observations(owner_id,posting_id,created_at DESC,id DESC);
CREATE FUNCTION check_observation_source() RETURNS trigger LANGUAGE plpgsql SET search_path=bonafides,pg_catalog AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM job_postings p JOIN source_configs s
 ON s.owner_id=p.owner_id AND s.id=p.source_config_id
 WHERE p.owner_id=NEW.owner_id AND p.id=NEW.posting_id AND p.source_config_id=NEW.source_config_id
 AND p.external_id=NEW.external_id AND (s.source_kind='manual' OR NEW.policy_review_id IS NOT NULL))
 THEN RAISE EXCEPTION 'observation attribution mismatch' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER observation_source_guard BEFORE INSERT ON job_source_observations FOR EACH ROW EXECUTE FUNCTION check_observation_source();
ALTER TABLE workflow_runs DROP CONSTRAINT workflow_runs_kind_check;
ALTER TABLE workflow_runs ADD CONSTRAINT workflow_runs_kind_check CHECK(kind IN
 ('profile_import','discovery','posting_extract','matching','document_generate','pdf_render','portable_export',
 'portable_import','privacy_delete','backup','restore_validate','integration_sync','approved_send','source_refresh'));
ALTER TABLE workflow_runs ADD COLUMN source_config_id uuid;
ALTER TABLE workflow_runs ADD COLUMN source_policy_version bigint;
ALTER TABLE workflow_runs ADD COLUMN source_policy_review_id uuid;
ALTER TABLE workflow_runs ADD COLUMN source_policy_sha256 text;
ALTER TABLE workflow_runs ADD FOREIGN KEY(owner_id,source_config_id) REFERENCES source_configs(owner_id,id) ON DELETE RESTRICT;
ALTER TABLE workflow_runs ADD FOREIGN KEY(owner_id,source_config_id,source_policy_version,source_policy_review_id)
 REFERENCES source_policy_reviews(owner_id,source_config_id,policy_version,id) ON DELETE RESTRICT;
ALTER TABLE workflow_runs ADD CONSTRAINT source_workflow_shape CHECK(
 (kind<>'source_refresh' AND source_config_id IS NULL AND source_policy_version IS NULL
 AND source_policy_review_id IS NULL AND source_policy_sha256 IS NULL) OR
 (kind='source_refresh' AND source_config_id IS NOT NULL AND source_policy_version IS NOT NULL AND source_policy_version>0
 AND source_policy_review_id IS NOT NULL AND source_policy_sha256 IS NOT NULL
 AND source_policy_sha256 ~ '^[0-9a-f]{64}$' AND max_attempts=3
 AND deadline_at<=created_at+interval '180 seconds' AND token_budget=0 AND cost_budget_microunits=0));
CREATE UNIQUE INDEX one_active_source_refresh ON workflow_runs(owner_id,source_config_id)
 WHERE kind='source_refresh' AND state IN ('pending','running','retryable_failure','waiting_for_approval');
CREATE FUNCTION guard_source_workflow_pin() RETURNS trigger LANGUAGE plpgsql SET search_path=bonafides,pg_catalog AS $$
BEGIN
 IF OLD.kind='source_refresh' AND ROW(NEW.kind,NEW.input,NEW.source_config_id,NEW.source_policy_version,
 NEW.source_policy_review_id,NEW.source_policy_sha256,NEW.max_attempts,NEW.deadline_at,NEW.created_at)
 IS DISTINCT FROM ROW(OLD.kind,OLD.input,OLD.source_config_id,OLD.source_policy_version,
 OLD.source_policy_review_id,OLD.source_policy_sha256,OLD.max_attempts,OLD.deadline_at,OLD.created_at)
 THEN RAISE EXCEPTION 'immutable source workflow policy' USING ERRCODE='55000'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER source_workflow_pin_guard BEFORE UPDATE ON workflow_runs FOR EACH ROW EXECUTE FUNCTION guard_source_workflow_pin();
ALTER TABLE workflow_runs ADD FOREIGN KEY(owner_id,source_config_id,source_policy_version,source_policy_review_id,source_policy_sha256)
 REFERENCES source_policy_reviews(owner_id,source_config_id,policy_version,id,policy_sha256) ON DELETE RESTRICT;
REVOKE ALL ON FUNCTION guard_posting_source(),check_observation_source() FROM PUBLIC;
REVOKE ALL ON FUNCTION guard_source_identity(),check_source_review(),guard_source_workflow_pin() FROM PUBLIC;
GRANT SELECT,INSERT ON source_policy_reviews TO bonafides_runtime;
REVOKE UPDATE,DELETE ON source_policy_reviews FROM bonafides_runtime;
REVOKE DELETE ON source_configs,job_postings,job_source_observations FROM bonafides_runtime;
INSERT INTO schema_migrations(version,source_sha256) VALUES(7,repeat('0',64));
