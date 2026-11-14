-- SPDX-License-Identifier: MIT OR Apache-2.0
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- Migration008 left this check unnamed. Resolve only the check on undone_at.
DO $$
DECLARE
 undo_check name;
BEGIN
 SELECT c.conname INTO STRICT undo_check
 FROM pg_catalog.pg_constraint c
 JOIN pg_catalog.pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=ANY(c.conkey)
 WHERE c.conrelid='bonafides.document_conversation_turns'::regclass
  AND c.contype='c' AND a.attname='undone_at';
 EXECUTE format('ALTER TABLE bonafides.document_conversation_turns DROP CONSTRAINT %I',undo_check);
END $$;
ALTER TABLE bonafides.document_conversation_turns
 ADD CONSTRAINT document_conversation_turns_undo_check CHECK (undone_at IS NULL OR
  (reply IS NOT NULL AND (jsonb_array_length(reply->'applied')>0 OR jsonb_array_length(reply->'tones')>0)));
