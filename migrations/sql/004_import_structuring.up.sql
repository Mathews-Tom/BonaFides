-- SPDX-License-Identifier: MIT OR Apache-2.0
ALTER TABLE bonafides.import_batches
 ADD COLUMN structuring_mode text NOT NULL DEFAULT 'literal' CHECK (structuring_mode IN ('literal','model')),
 ADD COLUMN structuring_provenance jsonb,
 ADD CONSTRAINT import_structuring_provenance_check CHECK (
 ((structuring_mode='model' AND parsed_text IS NOT NULL) = (structuring_provenance IS NOT NULL))
 AND (structuring_provenance IS NULL OR jsonb_typeof(structuring_provenance)='object'));

CREATE OR REPLACE FUNCTION bonafides.guard_import_batch() RETURNS trigger
 LANGUAGE plpgsql SET search_path TO 'bonafides', 'pg_catalog' AS $$
BEGIN
 IF ROW(NEW.owner_id,NEW.id,NEW.workflow_run_id,NEW.blob_id,NEW.media_type,NEW.byte_size,NEW.created_at,NEW.structuring_mode)
 IS DISTINCT FROM ROW(OLD.owner_id,OLD.id,OLD.workflow_run_id,OLD.blob_id,OLD.media_type,OLD.byte_size,OLD.created_at,OLD.structuring_mode)
 OR (OLD.parsed_text IS NOT NULL AND ROW(NEW.parsed_text,NEW.source_sha256,NEW.proposals,NEW.warnings,NEW.review_expires_at,NEW.structuring_provenance)
 IS DISTINCT FROM ROW(OLD.parsed_text,OLD.source_sha256,OLD.proposals,OLD.warnings,OLD.review_expires_at,OLD.structuring_provenance))
 OR (OLD.review IS NOT NULL AND ROW(NEW.review,NEW.reviewed_at,NEW.profile_version)
 IS DISTINCT FROM ROW(OLD.review,OLD.reviewed_at,OLD.profile_version))
 OR (OLD.merged_profile_revision_id IS NOT NULL AND NEW.merged_profile_revision_id IS DISTINCT FROM OLD.merged_profile_revision_id)
 THEN RAISE EXCEPTION 'immutable import phase' USING ERRCODE='55000'; END IF;
 RETURN NEW;
END $$;

ALTER TABLE bonafides.local_model_route_blocks
 DROP CONSTRAINT local_model_route_blocks_stage_check,
 ADD CONSTRAINT local_model_route_blocks_stage_check CHECK (stage IN
 ('extract_posting','match_requirements','generate_documents','regenerate_section','structure_import'));
