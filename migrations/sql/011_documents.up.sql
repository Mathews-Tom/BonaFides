-- SPDX-License-Identifier: MIT OR Apache-2.0
SET search_path=offerloom,pg_catalog;

-- Existing revision bytes, v1 hashes, reviews and artifacts are not rewritten.
ALTER TABLE document_revisions ADD COLUMN validation_issues jsonb NOT NULL DEFAULT '[]'
 CHECK(jsonb_typeof(validation_issues)='array');
ALTER TABLE document_reviews ADD COLUMN reviewed_block_keys jsonb
 CHECK(reviewed_block_keys IS NULL OR jsonb_typeof(reviewed_block_keys)='array');
ALTER TABLE document_drafts ADD COLUMN deleted_at timestamptz;
CREATE INDEX document_revision_history_idx ON document_revisions(owner_id,document_draft_id,revision_no DESC);
ALTER TABLE local_model_route_blocks DROP CONSTRAINT local_model_route_blocks_stage_check;
ALTER TABLE local_model_route_blocks ADD CONSTRAINT local_model_route_blocks_stage_check
 CHECK(stage IN ('extract_posting','match_requirements','generate_documents'));
-- Runtime may append revisions/reviews but cannot rewrite historical statements.
REVOKE UPDATE,DELETE ON document_revisions,document_claim_links,document_reviews FROM offerloom_runtime;
INSERT INTO schema_migrations(version,source_sha256) VALUES(11,repeat('0',64));
