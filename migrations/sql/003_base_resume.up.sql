-- SPDX-License-Identifier: MIT OR Apache-2.0
-- Run after 001 + 002, using the ordinary migration/schema owner. No extensions.
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='60s';
SET LOCAL search_path=bonafides,pg_catalog;
ALTER TABLE document_drafts ADD COLUMN scope text NOT NULL DEFAULT 'tailored';
ALTER TABLE document_drafts ALTER COLUMN application_id DROP NOT NULL;
ALTER TABLE document_drafts ADD CONSTRAINT document_drafts_scope_check CHECK(scope IN ('base','tailored'));
ALTER TABLE document_drafts ADD CONSTRAINT document_drafts_scope_parent_check
 CHECK((scope='base' AND application_id IS NULL AND kind='resume') OR (scope='tailored' AND application_id IS NOT NULL));
ALTER TABLE document_drafts ADD CONSTRAINT document_drafts_scope_identity_unique UNIQUE(owner_id,id,scope);
CREATE UNIQUE INDEX one_base_resume_per_owner_idx ON document_drafts(owner_id) WHERE scope='base';
ALTER TABLE document_revisions ADD COLUMN scope text NOT NULL DEFAULT 'tailored';
ALTER TABLE document_revisions ALTER COLUMN posting_snapshot DROP NOT NULL;
ALTER TABLE document_revisions ADD CONSTRAINT document_revisions_scope_fk FOREIGN KEY(owner_id,document_draft_id,scope)
 REFERENCES document_drafts(owner_id,id,scope) ON DELETE CASCADE;
ALTER TABLE document_revisions ADD CONSTRAINT document_revisions_scope_check CHECK(scope IN ('base','tailored'));
ALTER TABLE document_revisions ADD CONSTRAINT document_revisions_base_context_check
 CHECK((scope='base' AND posting_snapshot IS NULL AND model_run_id IS NULL AND origin IN ('deterministic','manual','restore'))
 OR (scope='tailored' AND posting_snapshot IS NOT NULL));
-- Replace only the original origin enum-like check, without relying on an
-- automatically generated constraint name. Existing values remain accepted.
DO $$ DECLARE c record; BEGIN
 FOR c IN SELECT conname FROM pg_constraint WHERE conrelid='bonafides.document_revisions'::regclass
 AND contype='c' AND pg_get_constraintdef(oid) LIKE '%origin%'
 AND conname<>'document_revisions_base_context_check' LOOP
 EXECUTE format('ALTER TABLE bonafides.document_revisions DROP CONSTRAINT %I',c.conname);
 END LOOP;
END $$;
ALTER TABLE document_revisions ADD CONSTRAINT document_revisions_origin_check CHECK(origin IN ('generated','deterministic','manual','restore'));
INSERT INTO schema_migrations(version) VALUES(3);
COMMIT;
