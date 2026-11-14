-- SPDX-License-Identifier: MIT OR Apache-2.0
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

ALTER TABLE bonafides.local_model_route_blocks
 DROP CONSTRAINT local_model_route_blocks_stage_check,
 ADD CONSTRAINT local_model_route_blocks_stage_check CHECK (stage IN
 ('extract_posting','match_requirements','generate_documents','regenerate_section','structure_import','refine_documents'));

CREATE TABLE bonafides.document_conversation_turns (
 owner_id uuid NOT NULL REFERENCES bonafides.owners(id) ON DELETE CASCADE,
 id uuid NOT NULL DEFAULT gen_random_uuid(),
 application_id uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 request_text text NOT NULL CHECK (length(request_text) BETWEEN 1 AND 2000 AND request_text=btrim(request_text)),
 context_snapshot jsonb NOT NULL CHECK (jsonb_typeof(context_snapshot)='object'),
 reply jsonb CHECK (jsonb_typeof(reply)='object'),
 workflow_run_id uuid,
 historical_workflow_run_id uuid,
 model_run_id uuid,
 historical_model_run_id uuid,
 undone_at timestamptz,
 PRIMARY KEY (owner_id,id),
 FOREIGN KEY (owner_id,application_id) REFERENCES bonafides.applications(owner_id,id) ON DELETE CASCADE,
 FOREIGN KEY (owner_id,workflow_run_id) REFERENCES bonafides.workflow_runs(owner_id,id) ON DELETE RESTRICT,
 FOREIGN KEY (owner_id,model_run_id,workflow_run_id) REFERENCES bonafides.model_runs(owner_id,id,workflow_run_id) ON DELETE RESTRICT,
 FOREIGN KEY (owner_id,historical_model_run_id) REFERENCES bonafides.portable_model_history(owner_id,id) ON DELETE RESTRICT,
 CHECK ((context_snapshot->>'application_id') IS NOT DISTINCT FROM application_id::text
  AND (context_snapshot->>'turn_id') IS NOT DISTINCT FROM id::text
  AND (context_snapshot->>'request_text') IS NOT DISTINCT FROM request_text
  AND (context_snapshot->>'mode') IS NOT DISTINCT FROM 'document_conversation'
  AND (context_snapshot->>'stage') IS NOT DISTINCT FROM 'refine_documents'),
 CHECK (reply IS NULL OR coalesce(
  jsonb_typeof(reply->'text')='string' AND length(reply->>'text') BETWEEN 1 AND 2000
  AND jsonb_typeof(reply->'declined')='boolean' AND jsonb_typeof(reply->'applied')='array'
  AND jsonb_typeof(reply->'skipped')='array' AND jsonb_typeof(reply->'tones')='array'
  AND jsonb_typeof(reply->'revisions')='array'
  AND (reply->>'declined'='false' OR (reply->'applied'='[]'::jsonb AND reply->'tones'='[]'::jsonb)),false)),
 CHECK (workflow_run_id IS NULL OR historical_workflow_run_id IS NULL),
 CHECK (model_run_id IS NULL OR (workflow_run_id IS NOT NULL AND historical_model_run_id IS NULL AND reply IS NOT NULL)),
 CHECK (historical_model_run_id IS NULL OR (historical_workflow_run_id IS NOT NULL AND model_run_id IS NULL AND reply IS NOT NULL)),
 CHECK (undone_at IS NULL OR (reply IS NOT NULL AND jsonb_array_length(reply->'applied')>0))
);
CREATE INDEX conversation_application_history ON bonafides.document_conversation_turns(owner_id,application_id,created_at,id);
GRANT SELECT,INSERT,UPDATE ON bonafides.document_conversation_turns TO bonafides_runtime;

CREATE FUNCTION bonafides.guard_document_conversation_turn() RETURNS trigger
 LANGUAGE plpgsql SET search_path TO 'pg_catalog','bonafides' AS $$
BEGIN
 IF TG_OP='UPDATE' THEN
  IF ROW(NEW.owner_id,NEW.id,NEW.application_id,NEW.created_at,NEW.request_text,NEW.context_snapshot,
    NEW.workflow_run_id,NEW.historical_workflow_run_id,NEW.historical_model_run_id)
   IS DISTINCT FROM ROW(OLD.owner_id,OLD.id,OLD.application_id,OLD.created_at,OLD.request_text,OLD.context_snapshot,
    OLD.workflow_run_id,OLD.historical_workflow_run_id,OLD.historical_model_run_id)
   OR (OLD.workflow_run_id IS NULL AND
       ROW(NEW.reply,NEW.model_run_id) IS DISTINCT FROM ROW(OLD.reply,OLD.model_run_id))
   OR (OLD.reply IS NOT NULL AND NEW.reply IS DISTINCT FROM OLD.reply)
   OR ((OLD.model_run_id IS NOT NULL OR OLD.reply IS NOT NULL) AND NEW.model_run_id IS DISTINCT FROM OLD.model_run_id)
   OR (OLD.undone_at IS NOT NULL AND NEW.undone_at IS DISTINCT FROM OLD.undone_at)
   OR (NEW.undone_at IS DISTINCT FROM OLD.undone_at AND
       (OLD.reply IS NULL OR OLD.workflow_run_id IS NULL OR OLD.historical_workflow_run_id IS NOT NULL)) THEN
   RAISE EXCEPTION 'immutable conversation history' USING ERRCODE='55000';
  END IF;
 END IF;
 IF TG_OP='INSERT' AND NEW.workflow_run_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM bonafides.workflow_runs w WHERE w.owner_id=NEW.owner_id AND w.id=NEW.workflow_run_id
   AND w.kind='document_generate' AND w.input->>'mode'='document_conversation'
   AND w.input->>'application_id'=NEW.application_id::text AND w.input->>'turn_id'=NEW.id::text) THEN
  RAISE EXCEPTION 'conversation workflow mismatch' USING ERRCODE='23514';
 END IF;
 IF NEW.historical_model_run_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM bonafides.portable_model_history h WHERE h.owner_id=NEW.owner_id AND h.id=NEW.historical_model_run_id
   AND h.record->>'workflow_run_id'=NEW.historical_workflow_run_id::text) THEN
  RAISE EXCEPTION 'conversation model history mismatch' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER conversation_history_guard BEFORE INSERT OR UPDATE ON bonafides.document_conversation_turns
 FOR EACH ROW EXECUTE FUNCTION bonafides.guard_document_conversation_turn();
REVOKE ALL ON FUNCTION bonafides.guard_document_conversation_turn() FROM PUBLIC;

-- Application cascades already remove turns, and existing workflow erasure removes
-- their runnable inputs and runtime models. Preserve that implementation; extend
-- only the uncovered no-edit historical-model references before the cascade.
ALTER FUNCTION bonafides.erase_portable_scope(uuid,uuid,bigint,uuid,jsonb,jsonb)
 RENAME TO erase_portable_scope_records;
REVOKE ALL ON FUNCTION bonafides.erase_portable_scope_records(uuid,uuid,bigint,uuid,jsonb,jsonb) FROM PUBLIC,bonafides_runtime;
CREATE FUNCTION bonafides.erase_portable_scope(p_id uuid,p_holder uuid,p_fence bigint,
 p_checkpoint uuid DEFAULT NULL,p_runtime_pins jsonb DEFAULT '{}'::jsonb,p_orphans jsonb DEFAULT '[]'::jsonb)
 RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'pg_catalog','bonafides' AS $$
DECLARE
 c bonafides.installation_controls%ROWTYPE;
 history_ids uuid[];
 result jsonb;
BEGIN
 SELECT * INTO STRICT c FROM bonafides.assert_installation_control(p_id,p_holder,p_fence);
 SELECT coalesce(array_agg(historical_model_run_id),'{}'::uuid[]) INTO history_ids
 FROM bonafides.document_conversation_turns WHERE owner_id=c.owner_id AND
  (c.purpose='portable_import' OR c.scope->'payload'->'categories'='["all_workspace"]'::jsonb
   OR application_id::text=c.scope->'payload'->>'application_id');
 result:=bonafides.erase_portable_scope_records(p_id,p_holder,p_fence,p_checkpoint,p_runtime_pins,p_orphans);
 DELETE FROM bonafides.portable_model_history h WHERE h.owner_id=c.owner_id AND h.id=ANY(history_ids)
  AND NOT EXISTS(SELECT 1 FROM bonafides.document_conversation_turns t WHERE t.owner_id=c.owner_id AND t.historical_model_run_id=h.id)
  AND NOT EXISTS(SELECT 1 FROM bonafides.document_revisions r WHERE r.owner_id=c.owner_id AND r.historical_model_run_id=h.id)
  AND NOT EXISTS(SELECT 1 FROM bonafides.match_assessments a WHERE a.owner_id=c.owner_id AND a.historical_model_run_id=h.id)
  AND NOT EXISTS(SELECT 1 FROM bonafides.document_regeneration_proposals p WHERE p.owner_id=c.owner_id AND p.historical_model_run_id=h.id);
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION bonafides.erase_portable_scope(uuid,uuid,bigint,uuid,jsonb,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION bonafides.erase_portable_scope(uuid,uuid,bigint,uuid,jsonb,jsonb) TO bonafides_runtime;
