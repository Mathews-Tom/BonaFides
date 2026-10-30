-- SPDX-License-Identifier: MIT OR Apache-2.0
-- evidence-linked profiles adds an independently editable language section without changing old revisions.
SET LOCAL search_path=bonafides,pg_catalog;
ALTER TABLE professional_claims DROP CONSTRAINT professional_claims_kind_check;
ALTER TABLE professional_claims ADD CONSTRAINT professional_claims_kind_check
 CHECK(kind IN ('identity','experience','education','skill','project','certification','preference','other','language'));
CREATE TABLE profile_revision_evidence (
 owner_id uuid NOT NULL, profile_revision_id uuid NOT NULL, evidence_revision_id uuid NOT NULL,
 PRIMARY KEY(owner_id,profile_revision_id,evidence_revision_id),
 FOREIGN KEY(owner_id,profile_revision_id) REFERENCES profile_revisions(owner_id,id) ON DELETE CASCADE,
 FOREIGN KEY(owner_id,evidence_revision_id) REFERENCES evidence_revisions(owner_id,id) ON DELETE RESTRICT
);
CREATE TRIGGER immutable_update BEFORE UPDATE ON profile_revision_evidence
 FOR EACH ROW EXECUTE FUNCTION reject_revision_update();
GRANT SELECT,INSERT,DELETE ON profile_revision_evidence TO bonafides_runtime;
INSERT INTO schema_migrations(version,source_sha256) VALUES(5,repeat('0',64));
