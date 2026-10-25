-- SPDX-License-Identifier: MIT OR Apache-2.0
-- OL-009 immutable owner review, evidence coverage and bounded local inference.
SET LOCAL search_path=offerloom,pg_catalog;
-- A changed source invalidates the mutable current projection, never its history.
CREATE FUNCTION invalidate_posting_requirement_projection() RETURNS trigger
LANGUAGE plpgsql SET search_path=offerloom,pg_catalog AS $$
BEGIN
 IF ROW(NEW.title,NEW.company,NEW.location_text,NEW.description_text,NEW.canonical_url,NEW.content_sha256)
 IS DISTINCT FROM ROW(OLD.title,OLD.company,OLD.location_text,OLD.description_text,OLD.canonical_url,OLD.content_sha256)
 THEN NEW.requirements='[]'::jsonb; NEW.requirements_reviewed_at=NULL; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION invalidate_posting_requirement_projection() FROM PUBLIC;
CREATE TRIGGER posting_requirement_source_guard BEFORE UPDATE ON job_postings
 FOR EACH ROW EXECUTE FUNCTION invalidate_posting_requirement_projection();
CREATE TABLE posting_requirement_revisions (
 owner_id uuid NOT NULL, id uuid NOT NULL DEFAULT gen_random_uuid(), posting_id uuid NOT NULL,
 revision_no bigint NOT NULL CHECK(revision_no>0), source_text text NOT NULL,
 source_sha256 text NOT NULL CHECK(source_sha256 ~ '^[0-9a-f]{64}$'),
 posting_content_sha256 text NOT NULL CHECK(posting_content_sha256 ~ '^[0-9a-f]{64}$'),
 requirements jsonb NOT NULL CHECK(jsonb_typeof(requirements)='array'),
 confirmed boolean NOT NULL CHECK(confirmed), provenance jsonb NOT NULL CHECK(jsonb_typeof(provenance)='object'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY(owner_id,id),
 UNIQUE(owner_id,posting_id,revision_no), UNIQUE(owner_id,posting_id,id),
 FOREIGN KEY(owner_id,posting_id) REFERENCES job_postings(owner_id,id) ON DELETE RESTRICT
);
CREATE INDEX requirements_posting_history ON posting_requirement_revisions(owner_id,posting_id,revision_no DESC);
CREATE TRIGGER immutable_update BEFORE UPDATE ON posting_requirement_revisions
 FOR EACH ROW EXECUTE FUNCTION reject_revision_update();
ALTER TABLE match_assessments ADD COLUMN requirements_revision_id uuid;
ALTER TABLE match_assessments ADD COLUMN hard_eligibility text NOT NULL DEFAULT 'unknown'
 CHECK(hard_eligibility IN ('met','not_met','unknown'));
ALTER TABLE match_assessments ADD COLUMN provenance jsonb NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(provenance)='object');
ALTER TABLE match_assessments ADD FOREIGN KEY(owner_id,posting_id,requirements_revision_id)
 REFERENCES posting_requirement_revisions(owner_id,posting_id,id) ON DELETE RESTRICT;
CREATE TRIGGER immutable_update BEFORE UPDATE ON match_assessments
 FOR EACH ROW EXECUTE FUNCTION reject_revision_update();
-- Attempts are transmission reservations, not worker leases. Recovery never resets them.
ALTER TABLE workflow_runs ADD COLUMN model_network_attempts integer NOT NULL DEFAULT 0
 CHECK(model_network_attempts BETWEEN 0 AND 2);
CREATE TABLE local_model_reservations (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 owner_id uuid, workflow_run_id uuid, fence bigint, reserved_until timestamptz NOT NULL DEFAULT '-infinity',
 FOREIGN KEY(owner_id,workflow_run_id) REFERENCES workflow_runs(owner_id,id) ON DELETE SET NULL,
 CHECK((owner_id IS NULL)=(workflow_run_id IS NULL))
);
INSERT INTO local_model_reservations(singleton) VALUES(true);
ALTER TABLE model_runs ADD COLUMN usage_status text NOT NULL DEFAULT 'unknown'
 CHECK(usage_status IN ('known','unknown'));
ALTER TABLE model_runs ADD COLUMN metadata jsonb NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(metadata)='object');
-- Unknown provider usage is NULL, not a fabricated zero-token observation.
ALTER TABLE model_runs ALTER COLUMN input_tokens DROP NOT NULL;
ALTER TABLE model_runs ALTER COLUMN input_tokens DROP DEFAULT;
ALTER TABLE model_runs ALTER COLUMN output_tokens DROP NOT NULL;
ALTER TABLE model_runs ALTER COLUMN output_tokens DROP DEFAULT;
CREATE TABLE local_model_route_blocks (
 owner_id uuid NOT NULL REFERENCES owners(id) ON DELETE CASCADE,
 route_sha256 text NOT NULL CHECK(route_sha256 ~ '^[0-9a-f]{64}$'),
 stage text NOT NULL CHECK(stage IN ('extract_posting','match_requirements')),
 error_code text NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(owner_id,route_sha256,stage)
);
GRANT SELECT,INSERT ON local_model_route_blocks TO offerloom_runtime;
GRANT SELECT,INSERT ON posting_requirement_revisions,match_assessments TO offerloom_runtime;
REVOKE UPDATE,DELETE ON posting_requirement_revisions,match_assessments FROM offerloom_runtime;
GRANT SELECT,UPDATE ON local_model_reservations TO offerloom_runtime;
INSERT INTO schema_migrations(version,source_sha256) VALUES(9,repeat('0',64));
