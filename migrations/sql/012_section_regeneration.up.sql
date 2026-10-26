-- SPDX-License-Identifier: MIT OR Apache-2.0
SET search_path=offerloom,pg_catalog;

-- New stage qualification is independent; no existing receipt is promoted.
ALTER TABLE local_model_route_blocks DROP CONSTRAINT local_model_route_blocks_stage_check;
ALTER TABLE local_model_route_blocks ADD CONSTRAINT local_model_route_blocks_stage_check
 CHECK(stage IN ('extract_posting','match_requirements','generate_documents','regenerate_section'));
ALTER TABLE model_runs ADD CONSTRAINT model_runs_owner_id_workflow_unique
 UNIQUE(owner_id,id,workflow_run_id);
CREATE TABLE document_regeneration_proposals (
 owner_id uuid NOT NULL,
 id uuid NOT NULL,
 document_draft_id uuid NOT NULL,
 target_revision_id uuid NOT NULL,
 target_content_sha256 text NOT NULL CHECK(target_content_sha256 ~ '^[0-9a-f]{64}$'),
 target_document_version bigint NOT NULL CHECK(target_document_version>0),
 section_key text NOT NULL CHECK(section_key ~ '^[a-zA-Z0-9_-]{1,80}$'),
 original_section jsonb NOT NULL CHECK(jsonb_typeof(original_section)='object'),
 proposed_section jsonb NOT NULL CHECK(jsonb_typeof(proposed_section)='object'),
 validation_issues jsonb NOT NULL CHECK(jsonb_typeof(validation_issues)='array'),
 model_run_id uuid NOT NULL,
 workflow_run_id uuid NOT NULL,
 model_run jsonb NOT NULL CHECK(jsonb_typeof(model_run)='object'),
 context_snapshot jsonb NOT NULL CHECK(jsonb_typeof(context_snapshot)='object'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(owner_id,id),
 UNIQUE(owner_id,workflow_run_id),
 FOREIGN KEY(owner_id,document_draft_id) REFERENCES document_drafts(owner_id,id) ON DELETE RESTRICT,
 FOREIGN KEY(owner_id,document_draft_id,target_revision_id)
  REFERENCES document_revisions(owner_id,document_draft_id,id) ON DELETE RESTRICT,
 FOREIGN KEY(owner_id,workflow_run_id) REFERENCES workflow_runs(owner_id,id) ON DELETE RESTRICT,
 FOREIGN KEY(owner_id,model_run_id,workflow_run_id)
  REFERENCES model_runs(owner_id,id,workflow_run_id) ON DELETE RESTRICT
);
CREATE INDEX document_regeneration_proposal_history_idx
 ON document_regeneration_proposals(owner_id,document_draft_id,created_at DESC,id DESC);
REVOKE ALL ON document_regeneration_proposals FROM PUBLIC,offerloom_runtime;
GRANT SELECT,INSERT ON document_regeneration_proposals TO offerloom_runtime;
INSERT INTO schema_migrations(version,source_sha256) VALUES(12,repeat('0',64));
