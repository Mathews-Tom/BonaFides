-- SPDX-License-Identifier: MIT OR Apache-2.0
SET search_path=bonafides,pg_catalog;

-- Content provenance is immutable; classification belongs to the evidence record.
-- Legacy evidence remains private and not employer-owned.
ALTER TABLE evidence_revisions
 ADD COLUMN source_type text NOT NULL DEFAULT 'owner_statement',
 ADD COLUMN origin text NOT NULL DEFAULT 'owner_entry';
ALTER TABLE evidence_revisions
 ALTER COLUMN source_type TYPE text USING CASE WHEN payload ? 'import_id' THEN 'document' ELSE source_type END,
 ALTER COLUMN origin TYPE text USING CASE WHEN payload ? 'import_id' THEN 'resume_import' ELSE origin END;
ALTER TABLE evidence_revisions
 ALTER COLUMN source_type DROP DEFAULT,
 ALTER COLUMN origin DROP DEFAULT,
 ADD CONSTRAINT evidence_revisions_source_type_check
  CHECK(source_type IN ('owner_statement','document','work_sample','web_page','credential','other')),
 ADD CONSTRAINT evidence_revisions_origin_check CHECK(origin IN ('owner_entry','resume_import'));

ALTER TABLE evidence_records
 ADD COLUMN privacy_class text NOT NULL DEFAULT 'private'
  CHECK(privacy_class IN ('public','private','confidential')),
 ADD COLUMN employer_owned boolean NOT NULL DEFAULT false;
ALTER TABLE evidence_records
 ALTER COLUMN privacy_class DROP DEFAULT,
 ALTER COLUMN employer_owned DROP DEFAULT;

CREATE TABLE evidence_classification_changes (
 owner_id uuid NOT NULL,
 evidence_id uuid NOT NULL,
 sequence integer NOT NULL CHECK(sequence > 0),
 privacy_class text NOT NULL CHECK(privacy_class IN ('public','private','confidential')),
 employer_owned boolean NOT NULL,
 changed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(owner_id,evidence_id,sequence),
 FOREIGN KEY(owner_id,evidence_id) REFERENCES evidence_records(owner_id,id) ON DELETE CASCADE
);
INSERT INTO evidence_classification_changes(owner_id,evidence_id,sequence,privacy_class,employer_owned,changed_at)
 SELECT owner_id,id,1,privacy_class,employer_owned,created_at FROM evidence_records;
CREATE TRIGGER immutable_update BEFORE UPDATE ON evidence_classification_changes
 FOR EACH ROW EXECUTE FUNCTION reject_revision_update();
REVOKE ALL ON evidence_classification_changes FROM PUBLIC,bonafides_runtime;
GRANT SELECT,INSERT ON evidence_classification_changes TO bonafides_runtime;

INSERT INTO schema_migrations(version,source_sha256) VALUES(29,repeat('0',64));
