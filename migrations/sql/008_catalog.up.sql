-- SPDX-License-Identifier: MIT OR Apache-2.0
-- OL-008 keeps owner disposition and conservative review separate from identity.
SET LOCAL search_path=offerloom,pg_catalog;
ALTER TABLE saved_opportunities DROP CONSTRAINT saved_opportunities_disposition_check;
ALTER TABLE saved_opportunities ADD CONSTRAINT saved_opportunities_disposition_check
 CHECK(disposition IN ('saved','dismissed','archived','unmarked'));
CREATE INDEX opportunity_disposition_catalog ON saved_opportunities(owner_id,disposition,posting_id);
CREATE INDEX posting_source_catalog ON job_postings(owner_id,source_config_id,created_at DESC,id DESC);
CREATE INDEX posting_status_catalog ON job_postings(owner_id,status,created_at DESC,id DESC);
CREATE INDEX posting_freshness_catalog ON job_postings(owner_id,last_observed_at DESC,id DESC);
-- Exact normalized equality is only a review hint, never a merge key. Hashes bound
-- B-tree entry size for long Unicode locations; queries recheck the full strings.
ALTER TABLE job_postings ADD COLUMN normalized_company text GENERATED ALWAYS AS
 (lower(btrim(regexp_replace(company,'[[:space:]]+',' ','g')))) STORED;
ALTER TABLE job_postings ADD COLUMN normalized_title text GENERATED ALWAYS AS
 (lower(btrim(regexp_replace(title,'[[:space:]]+',' ','g')))) STORED;
ALTER TABLE job_postings ADD COLUMN normalized_location text GENERATED ALWAYS AS
 (lower(btrim(regexp_replace(location_text,'[[:space:]]+',' ','g')))) STORED;
CREATE INDEX posting_duplicate_candidates ON job_postings
 (owner_id,md5(normalized_company),md5(normalized_title),md5(normalized_location),created_at DESC,id DESC);
CREATE TABLE posting_duplicate_decisions (
 owner_id uuid NOT NULL REFERENCES owners(id) ON DELETE CASCADE,
 posting_id uuid NOT NULL, candidate_id uuid NOT NULL,
 decision text NOT NULL CHECK(decision IN ('suspected','distinct')),
 version bigint NOT NULL DEFAULT 1 CHECK(version>0),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(owner_id,posting_id,candidate_id), CHECK(posting_id<candidate_id),
 FOREIGN KEY(owner_id,posting_id) REFERENCES job_postings(owner_id,id) ON DELETE RESTRICT,
 FOREIGN KEY(owner_id,candidate_id) REFERENCES job_postings(owner_id,id) ON DELETE RESTRICT
);
CREATE INDEX duplicate_decision_reverse ON posting_duplicate_decisions(owner_id,candidate_id,posting_id);
ALTER TABLE source_configs ADD COLUMN removed_at timestamptz;
ALTER TABLE source_configs ADD CONSTRAINT source_retired_shape CHECK
 (removed_at IS NULL OR (source_kind='greenhouse' AND NOT enabled));
CREATE OR REPLACE FUNCTION guard_source_identity() RETURNS trigger LANGUAGE plpgsql SET search_path=offerloom,pg_catalog AS $$
BEGIN
 IF ROW(NEW.owner_id,NEW.id,NEW.source_kind,NEW.board_token,NEW.created_at)
 IS DISTINCT FROM ROW(OLD.owner_id,OLD.id,OLD.source_kind,OLD.board_token,OLD.created_at)
 THEN RAISE EXCEPTION 'immutable source identity' USING ERRCODE='55000'; END IF;
 IF OLD.removed_at IS NOT NULL AND ROW(NEW.company,NEW.enabled,NEW.terms_url,NEW.terms_revision,
 NEW.terms_reviewed_at,NEW.allowed_host,NEW.request_limit_per_minute,NEW.current_policy_review_id,NEW.policy_version,NEW.removed_at)
 IS DISTINCT FROM ROW(OLD.company,OLD.enabled,OLD.terms_url,OLD.terms_revision,
 OLD.terms_reviewed_at,OLD.allowed_host,OLD.request_limit_per_minute,OLD.current_policy_review_id,OLD.policy_version,OLD.removed_at)
 THEN RAISE EXCEPTION 'retired source is immutable' USING ERRCODE='55000'; END IF;
 IF OLD.removed_at IS NULL AND NEW.removed_at IS NOT NULL THEN
  IF NEW.source_kind<>'greenhouse' OR NEW.enabled OR NEW.version<=OLD.version OR
   ROW(NEW.company,NEW.terms_url,NEW.terms_revision,NEW.terms_reviewed_at,NEW.allowed_host,
   NEW.request_limit_per_minute,NEW.current_policy_review_id,NEW.policy_version)
   IS DISTINCT FROM ROW(OLD.company,OLD.terms_url,OLD.terms_revision,OLD.terms_reviewed_at,OLD.allowed_host,
   OLD.request_limit_per_minute,OLD.current_policy_review_id,OLD.policy_version)
  THEN RAISE EXCEPTION 'invalid source retirement' USING ERRCODE='55000'; END IF;
 ELSIF ROW(NEW.company,NEW.enabled,NEW.terms_url,NEW.terms_revision,NEW.terms_reviewed_at,NEW.allowed_host,
 NEW.request_limit_per_minute,NEW.current_policy_review_id)
 IS DISTINCT FROM ROW(OLD.company,OLD.enabled,OLD.terms_url,OLD.terms_revision,OLD.terms_reviewed_at,OLD.allowed_host,
 OLD.request_limit_per_minute,OLD.current_policy_review_id)
 AND (NEW.policy_version<=OLD.policy_version OR NEW.version<=OLD.version)
 THEN RAISE EXCEPTION 'source policy version required' USING ERRCODE='55000'; END IF;
 RETURN NEW;
END $$;
GRANT SELECT,INSERT,UPDATE ON posting_duplicate_decisions TO offerloom_runtime;
INSERT INTO schema_migrations(version,source_sha256) VALUES(8,repeat('0',64));
