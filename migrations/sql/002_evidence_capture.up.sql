-- SPDX-License-Identifier: MIT OR Apache-2.0
ALTER TABLE bonafides.evidence_revisions DROP CONSTRAINT evidence_revisions_origin_check;
ALTER TABLE bonafides.evidence_revisions ADD CONSTRAINT evidence_revisions_origin_check
    CHECK (origin IN ('owner_entry', 'resume_import', 'evidence_capture'));
