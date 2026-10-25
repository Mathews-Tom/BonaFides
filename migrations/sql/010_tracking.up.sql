-- SPDX-License-Identifier: MIT OR Apache-2.0
SET search_path=offerloom,pg_catalog;

ALTER TABLE applications ADD COLUMN attempt_no bigint;
WITH numbered AS (
 SELECT owner_id,id,row_number() OVER(PARTITION BY owner_id,opportunity_id ORDER BY created_at,id) AS n
 FROM applications
)
UPDATE applications a SET attempt_no=n.n FROM numbered n WHERE a.owner_id=n.owner_id AND a.id=n.id;
ALTER TABLE applications ALTER COLUMN attempt_no SET NOT NULL;
ALTER TABLE applications ALTER COLUMN attempt_no SET DEFAULT 1;
ALTER TABLE applications ADD CHECK(attempt_no>0);
ALTER TABLE applications ADD UNIQUE(owner_id,opportunity_id,attempt_no);
ALTER TABLE applications ADD COLUMN current_stage_event_id uuid;

ALTER TABLE application_stage_events ADD COLUMN sequence bigint;
ALTER TABLE application_stage_events ADD COLUMN correction_of uuid;
ALTER TABLE application_stage_events ADD COLUMN affects_current_stage boolean NOT NULL DEFAULT true;
-- Only metadata is backfilled. Existing semantic event bytes are never rewritten.
ALTER TABLE application_stage_events DISABLE TRIGGER immutable_update;
WITH numbered AS (
 SELECT owner_id,id,row_number() OVER(PARTITION BY owner_id,application_id ORDER BY created_at,id) AS n
 FROM application_stage_events
)
UPDATE application_stage_events e SET sequence=n.n FROM numbered n WHERE e.owner_id=n.owner_id AND e.id=n.id;
ALTER TABLE application_stage_events ENABLE TRIGGER immutable_update;
ALTER TABLE application_stage_events ALTER COLUMN sequence SET NOT NULL;
ALTER TABLE application_stage_events ADD CHECK(sequence>0);
ALTER TABLE application_stage_events ADD CHECK(correction_of IS NULL OR correction_of<>id);
ALTER TABLE application_stage_events ADD UNIQUE(owner_id,application_id,sequence);
ALTER TABLE application_stage_events ADD UNIQUE(owner_id,application_id,id);
ALTER TABLE application_stage_events ADD UNIQUE(owner_id,application_id,correction_of);
ALTER TABLE application_stage_events ADD FOREIGN KEY(owner_id,application_id,correction_of)
 REFERENCES application_stage_events(owner_id,application_id,id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE applications ADD FOREIGN KEY(owner_id,id,current_stage_event_id)
 REFERENCES application_stage_events(owner_id,application_id,id) DEFERRABLE INITIALLY DEFERRED;
WITH latest AS (
 SELECT DISTINCT ON(owner_id,application_id) owner_id,application_id,id,to_stage
 FROM application_stage_events ORDER BY owner_id,application_id,sequence DESC
)
UPDATE applications a SET current_stage_event_id=e.id FROM latest e
 WHERE a.owner_id=e.owner_id AND a.id=e.application_id AND a.current_stage=e.to_stage;

ALTER TABLE appointments ADD COLUMN ends_at timestamptz;
ALTER TABLE appointments ADD COLUMN local_end timestamp;
ALTER TABLE appointments ADD COLUMN display_timezone text;
ALTER TABLE appointments ADD COLUMN status text NOT NULL DEFAULT 'scheduled' CHECK(status IN ('scheduled','completed','cancelled'));
ALTER TABLE appointments ADD COLUMN cancelled_at timestamptz;
UPDATE appointments SET status=CASE WHEN completed_at IS NULL THEN 'scheduled' ELSE 'completed' END,
 display_timezone=timezone,
 ends_at=CASE WHEN duration_minutes IS NOT NULL THEN starts_at+duration_minutes*interval '1 minute' END;
UPDATE appointments SET local_end=ends_at AT TIME ZONE timezone
 WHERE ends_at IS NOT NULL AND timezone IN (SELECT name FROM pg_timezone_names);
ALTER TABLE appointments ALTER COLUMN display_timezone SET NOT NULL;
ALTER TABLE appointments ADD CHECK((status='completed')=(completed_at IS NOT NULL));
ALTER TABLE appointments ADD CHECK((status='cancelled')=(cancelled_at IS NOT NULL));
ALTER TABLE appointments ADD CHECK(ends_at IS NULL OR ends_at>starts_at);
CREATE INDEX tracking_attention_idx ON appointments(owner_id,starts_at,id) WHERE status='scheduled';
CREATE INDEX tracking_notes_parent_idx ON application_notes(owner_id,application_id,created_at,id);
CREATE INDEX tracking_contacts_parent_idx ON application_contacts(owner_id,application_id,created_at,id);
CREATE INDEX tracking_attachments_parent_idx ON application_attachments(owner_id,application_id,created_at,id);
CREATE INDEX tracking_appointments_parent_idx ON appointments(owner_id,application_id,starts_at,id);
INSERT INTO schema_migrations(version,source_sha256) VALUES(10,repeat('0',64));
