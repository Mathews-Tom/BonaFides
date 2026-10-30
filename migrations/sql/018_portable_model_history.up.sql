-- SPDX-License-Identifier: MIT OR Apache-2.0
SET search_path=bonafides,pg_catalog;

-- Imported observations are deliberately not model_runs or executable tasks.
CREATE TABLE portable_model_history (
 owner_id uuid NOT NULL,
 id uuid NOT NULL,
 source_namespace uuid NOT NULL,
 source_owner_id uuid NOT NULL,
 record jsonb NOT NULL CHECK(jsonb_typeof(record)='object'),
 record_sha256 text NOT NULL CHECK(record_sha256 ~ '^[0-9a-f]{64}$'),
 imported_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(owner_id,id),
 FOREIGN KEY(owner_id) REFERENCES owners(id) ON DELETE CASCADE,
 CHECK(record->>'id'=id::text),
 CHECK(record->>'owner_id'=source_owner_id::text),
 CHECK(record ? 'workflow_run_id' AND jsonb_typeof(record->'workflow_run_id')='string')
);
CREATE TRIGGER immutable_update BEFORE UPDATE ON portable_model_history
 FOR EACH ROW EXECUTE FUNCTION reject_revision_update();
REVOKE ALL ON portable_model_history FROM PUBLIC,bonafides_runtime;
GRANT SELECT,INSERT ON portable_model_history TO bonafides_runtime;

ALTER TABLE match_assessments ADD COLUMN historical_model_run_id uuid;
ALTER TABLE match_assessments ADD FOREIGN KEY(owner_id,historical_model_run_id)
 REFERENCES portable_model_history(owner_id,id) ON DELETE RESTRICT;
ALTER TABLE match_assessments ADD CHECK(model_run_id IS NULL OR historical_model_run_id IS NULL);
CREATE INDEX match_assessment_historical_model_idx ON match_assessments(owner_id,historical_model_run_id)
 WHERE historical_model_run_id IS NOT NULL;

ALTER TABLE document_revisions ADD COLUMN historical_model_run_id uuid;
ALTER TABLE document_revisions ADD FOREIGN KEY(owner_id,historical_model_run_id)
 REFERENCES portable_model_history(owner_id,id) ON DELETE RESTRICT;
ALTER TABLE document_revisions ADD CHECK(model_run_id IS NULL OR historical_model_run_id IS NULL);
ALTER TABLE document_revisions ADD CHECK(scope<>'base' OR historical_model_run_id IS NULL);
CREATE INDEX document_revision_historical_model_idx ON document_revisions(owner_id,historical_model_run_id)
 WHERE historical_model_run_id IS NOT NULL;

ALTER TABLE document_regeneration_proposals ALTER COLUMN model_run_id DROP NOT NULL;
ALTER TABLE document_regeneration_proposals ALTER COLUMN workflow_run_id DROP NOT NULL;
ALTER TABLE document_regeneration_proposals ADD COLUMN historical_model_run_id uuid;
ALTER TABLE document_regeneration_proposals ADD FOREIGN KEY(owner_id,historical_model_run_id)
 REFERENCES portable_model_history(owner_id,id) ON DELETE RESTRICT;
ALTER TABLE document_regeneration_proposals ADD CHECK(
 (model_run_id IS NOT NULL AND workflow_run_id IS NOT NULL AND historical_model_run_id IS NULL)
 OR (model_run_id IS NULL AND workflow_run_id IS NULL AND historical_model_run_id IS NOT NULL)
);
CREATE INDEX regeneration_proposal_historical_model_idx ON document_regeneration_proposals(owner_id,historical_model_run_id)
 WHERE historical_model_run_id IS NOT NULL;
INSERT INTO schema_migrations(version,source_sha256) VALUES(18,repeat('0',64));
