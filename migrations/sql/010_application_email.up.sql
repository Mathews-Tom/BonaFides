-- SPDX-License-Identifier: MIT OR Apache-2.0
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

ALTER TABLE bonafides.document_drafts
 DROP CONSTRAINT document_drafts_kind_check,
 ADD CONSTRAINT document_drafts_kind_check CHECK (kind IN
 ('resume','cover_letter','application_email'));

ALTER TABLE bonafides.local_model_route_blocks
 DROP CONSTRAINT local_model_route_blocks_stage_check,
 ADD CONSTRAINT local_model_route_blocks_stage_check CHECK (stage IN
 ('extract_posting','match_requirements','generate_documents','regenerate_section','structure_import','refine_documents','write_application_email'));
