-- SPDX-License-Identifier: MIT OR Apache-2.0
SET search_path=bonafides,pg_catalog;

-- Every evidence revision records its source type, origin, privacy class and whether its
-- employer owns the content. Existing revisions receive explicit values: literal résumé
-- imports (their payload names the import) are documents from a résumé import, every other
-- revision is an owner statement entered by the owner, and all stay private and not
-- employer-owned so they reach model context exactly as before. The column rewrite assigns
-- these values without updating any immutable revision row.
ALTER TABLE evidence_revisions
 ADD COLUMN source_type text NOT NULL DEFAULT 'owner_statement',
 ADD COLUMN origin text NOT NULL DEFAULT 'owner_entry',
 ADD COLUMN privacy_class text NOT NULL DEFAULT 'private',
 ADD COLUMN employer_owned boolean NOT NULL DEFAULT false;
ALTER TABLE evidence_revisions
 ALTER COLUMN source_type TYPE text USING CASE WHEN payload ? 'import_id' THEN 'document' ELSE source_type END,
 ALTER COLUMN origin TYPE text USING CASE WHEN payload ? 'import_id' THEN 'resume_import' ELSE origin END;
-- New revisions state their provenance and no default can classify them silently.
ALTER TABLE evidence_revisions
 ALTER COLUMN source_type DROP DEFAULT,
 ALTER COLUMN origin DROP DEFAULT,
 ALTER COLUMN privacy_class DROP DEFAULT,
 ALTER COLUMN employer_owned DROP DEFAULT,
 ADD CONSTRAINT evidence_revisions_source_type_check
  CHECK(source_type IN ('owner_statement','document','work_sample','web_page','credential','other')),
 ADD CONSTRAINT evidence_revisions_origin_check CHECK(origin IN ('owner_entry','resume_import')),
 ADD CONSTRAINT evidence_revisions_privacy_class_check
  CHECK(privacy_class IN ('public','private','confidential'));

INSERT INTO schema_migrations(version,source_sha256) VALUES(29,repeat('0',64));
