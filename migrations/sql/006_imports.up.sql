-- SPDX-License-Identifier: MIT OR Apache-2.0
-- OL-006 stages bounded private imports independently from canonical profile history.
SET LOCAL search_path=offerloom,pg_catalog;
CREATE TABLE import_batches (
 owner_id uuid NOT NULL REFERENCES owners(id) ON DELETE CASCADE,
 id uuid NOT NULL DEFAULT gen_random_uuid(), workflow_run_id uuid NOT NULL,
 blob_id uuid NOT NULL, media_type text NOT NULL CHECK(media_type IN ('application/pdf','text/plain')),
 byte_size integer NOT NULL CHECK(byte_size BETWEEN 1 AND 10485760),
 profile_version bigint NOT NULL CHECK(profile_version>0),
 source_sha256 text CHECK(source_sha256 ~ '^[0-9a-f]{64}$'),
 parsed_text text CHECK(length(parsed_text)<=80000 AND octet_length(parsed_text)<=262144),
 proposals jsonb NOT NULL DEFAULT '[]' CHECK(jsonb_typeof(proposals)='array' AND jsonb_array_length(proposals)<=100),
 warnings jsonb NOT NULL DEFAULT '[]' CHECK(jsonb_typeof(warnings)='array'),
 review jsonb CHECK(jsonb_typeof(review)='object'), review_expires_at timestamptz, reviewed_at timestamptz,
 merged_profile_revision_id uuid, created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(owner_id,id), UNIQUE(owner_id,workflow_run_id),
 FOREIGN KEY(owner_id,workflow_run_id) REFERENCES workflow_runs(owner_id,id) ON DELETE CASCADE,
 FOREIGN KEY(owner_id,blob_id) REFERENCES blobs(owner_id,id) ON DELETE RESTRICT,
 FOREIGN KEY(owner_id,merged_profile_revision_id) REFERENCES profile_revisions(owner_id,id) ON DELETE RESTRICT,
 CHECK((parsed_text IS NULL)=(source_sha256 IS NULL)),
 CHECK((parsed_text IS NULL)=(review_expires_at IS NULL)),
 CHECK((review IS NULL)=(reviewed_at IS NULL))
);
CREATE INDEX import_owner_list_idx ON import_batches(owner_id,created_at DESC,id);
CREATE INDEX import_review_expiry_idx ON import_batches(review_expires_at) WHERE review IS NULL;
-- Keep bytes, extracted source, and accepted review immutable after each phase.
CREATE FUNCTION guard_import_batch() RETURNS trigger LANGUAGE plpgsql SET search_path=offerloom,pg_catalog AS $$
BEGIN
 IF ROW(NEW.owner_id,NEW.id,NEW.workflow_run_id,NEW.blob_id,NEW.media_type,NEW.byte_size,NEW.created_at)
 IS DISTINCT FROM ROW(OLD.owner_id,OLD.id,OLD.workflow_run_id,OLD.blob_id,OLD.media_type,OLD.byte_size,OLD.created_at)
 OR (OLD.parsed_text IS NOT NULL AND ROW(NEW.parsed_text,NEW.source_sha256,NEW.proposals,NEW.warnings,NEW.review_expires_at)
 IS DISTINCT FROM ROW(OLD.parsed_text,OLD.source_sha256,OLD.proposals,OLD.warnings,OLD.review_expires_at))
 OR (OLD.review IS NOT NULL AND ROW(NEW.review,NEW.reviewed_at,NEW.profile_version)
 IS DISTINCT FROM ROW(OLD.review,OLD.reviewed_at,OLD.profile_version))
 OR (OLD.merged_profile_revision_id IS NOT NULL AND NEW.merged_profile_revision_id IS DISTINCT FROM OLD.merged_profile_revision_id)
 THEN RAISE EXCEPTION 'immutable import phase' USING ERRCODE='55000'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER import_phase_guard BEFORE UPDATE ON import_batches FOR EACH ROW EXECUTE FUNCTION guard_import_batch();
REVOKE ALL ON FUNCTION guard_import_batch() FROM PUBLIC;
GRANT SELECT,INSERT,UPDATE,DELETE ON import_batches TO offerloom_runtime;
INSERT INTO schema_migrations(version,source_sha256) VALUES(6,repeat('0',64));
