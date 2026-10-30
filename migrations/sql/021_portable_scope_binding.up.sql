-- SPDX-License-Identifier: MIT OR Apache-2.0
SET search_path=bonafides,pg_catalog;

-- Bind work to its top-level typed target, not arbitrary copied professional
-- text. Cached responses are scoped only through named resource-reference keys.
-- Replacing the function preserves all transactional, checkpoint and purge gates.
CREATE OR REPLACE FUNCTION erase_portable_scope(
 p_id uuid,p_holder uuid,p_fence bigint,p_checkpoint uuid DEFAULT NULL,
 p_runtime_pins jsonb DEFAULT '{}',p_orphans jsonb DEFAULT '[]'
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,bonafides AS $$
DECLARE
 c bonafides.installation_controls%ROWTYPE;
 v_application uuid;
 v_workspace boolean;
 v_documents uuid[];
 v_revisions uuid[];
 v_ids uuid[];
 v_workflows uuid[];
 v_models uuid[];
 v_blobs uuid[];
 v_history uuid[];
 v_item jsonb;
 v_table text;
 v_count bigint;
BEGIN
 SELECT * INTO STRICT c FROM bonafides.assert_installation_control(p_id,p_holder,p_fence);
 IF c.phase<>'quiescent' OR c.purpose NOT IN ('privacy_delete','portable_import')
 OR EXISTS(SELECT 1 FROM bonafides.installation_activities)
 OR EXISTS(SELECT 1 FROM bonafides.provider_executions)
 OR EXISTS(SELECT 1 FROM bonafides.portable_purges WHERE control_id=c.id) THEN
  RAISE EXCEPTION 'portable erasure refused' USING ERRCODE='55000';
 END IF;
 v_workspace:=c.purpose='portable_import' OR coalesce(c.scope->'payload'->'categories'='["all_workspace"]'::jsonb,false);
 IF NOT v_workspace THEN
  IF c.scope->'payload'->'categories' IS DISTINCT FROM '["applications"]'::jsonb
   OR c.scope->'payload'->>'application_id' IS NULL THEN
   RAISE EXCEPTION 'unsupported deletion scope' USING ERRCODE='55000';
  END IF;
  v_application:=(c.scope->'payload'->>'application_id')::uuid;
  IF NOT EXISTS(SELECT 1 FROM bonafides.applications WHERE owner_id=c.owner_id
   AND id=v_application AND version=(c.scope->'payload'->>'application_version')::bigint) THEN
   RAISE EXCEPTION 'stale application deletion' USING ERRCODE='55000';
  END IF;
 END IF;
 IF c.purpose='portable_import' THEN
  PERFORM bonafides.assert_portable_checkpoint(p_id,p_holder,p_fence,p_checkpoint,p_runtime_pins);
 END IF;
 IF jsonb_typeof(p_orphans) IS DISTINCT FROM 'array' OR jsonb_array_length(p_orphans)>100000
 OR octet_length(p_orphans::text)>33554432 THEN
  RAISE EXCEPTION 'invalid orphan inventory' USING ERRCODE='55000';
 END IF;
 SET CONSTRAINTS ALL DEFERRED;
 SELECT coalesce(array_agg(id),'{}'::uuid[]) INTO v_documents
 FROM bonafides.document_drafts WHERE owner_id=c.owner_id AND (v_workspace OR application_id=v_application);
 SELECT coalesce(array_agg(id),'{}'::uuid[]) INTO v_revisions
 FROM bonafides.document_revisions WHERE owner_id=c.owner_id AND document_draft_id=ANY(v_documents);
 SELECT coalesce(array_agg(id),'{}'::uuid[]) INTO v_ids FROM (
  SELECT unnest(v_documents||v_revisions) AS id
  UNION SELECT v_application WHERE v_application IS NOT NULL
  UNION SELECT id FROM bonafides.application_notes WHERE owner_id=c.owner_id AND application_id=v_application
  UNION SELECT id FROM bonafides.application_contacts WHERE owner_id=c.owner_id AND application_id=v_application
  UNION SELECT id FROM bonafides.application_attachments WHERE owner_id=c.owner_id AND application_id=v_application
  UNION SELECT id FROM bonafides.application_stage_events WHERE owner_id=c.owner_id AND application_id=v_application
  UNION SELECT id FROM bonafides.appointments WHERE owner_id=c.owner_id AND application_id=v_application
  UNION SELECT id FROM bonafides.document_regeneration_proposals WHERE owner_id=c.owner_id AND document_draft_id=ANY(v_documents)
 ) selected;
 SELECT coalesce(array_agg(id),'{}'::uuid[]) INTO v_workflows FROM bonafides.workflow_runs
 WHERE owner_id=c.owner_id AND id<>c.id AND (v_workspace
  OR input->>'application_id'=v_application::text
  OR input->>'document_id' IN (SELECT id::text FROM unnest(v_documents) AS documents(id))
  OR input->>'revision_id' IN (SELECT id::text FROM unnest(v_revisions) AS revisions(id)));
 SELECT coalesce(array_agg(id),'{}'::uuid[]) INTO v_models FROM bonafides.model_runs
 WHERE owner_id=c.owner_id AND (v_workspace OR workflow_run_id=ANY(v_workflows)
  OR id IN (SELECT model_run_id FROM bonafides.document_revisions WHERE owner_id=c.owner_id AND id=ANY(v_revisions))
  OR id IN (SELECT model_run_id FROM bonafides.document_regeneration_proposals WHERE owner_id=c.owner_id AND document_draft_id=ANY(v_documents)));
 SELECT coalesce(array_agg(id),'{}'::uuid[]) INTO v_history FROM bonafides.portable_model_history
 WHERE owner_id=c.owner_id AND (v_workspace OR
  id IN (SELECT historical_model_run_id FROM bonafides.document_revisions WHERE owner_id=c.owner_id AND id=ANY(v_revisions))
  OR id IN (SELECT historical_model_run_id FROM bonafides.document_regeneration_proposals WHERE owner_id=c.owner_id AND document_draft_id=ANY(v_documents)));
 SELECT coalesce(array_agg(id),'{}'::uuid[]) INTO v_blobs FROM bonafides.blobs WHERE owner_id=c.owner_id AND (v_workspace
  OR id IN (SELECT blob_id FROM bonafides.application_attachments WHERE owner_id=c.owner_id AND application_id=v_application)
  OR id IN (SELECT blob_id FROM bonafides.document_artifacts WHERE owner_id=c.owner_id AND document_revision_id=ANY(v_revisions)));

 INSERT INTO bonafides.portable_purges(control_id,owner_id,scope,application_id)
 VALUES(c.id,c.owner_id,CASE WHEN v_workspace THEN 'workspace' ELSE 'application' END,v_application);
 INSERT INTO bonafides.erased_request_keys(owner_id,route_sha256,key_sha256,request_sha256)
 SELECT owner_id,encode(sha256(convert_to(route_key,'UTF8')),'hex'),encode(sha256(convert_to(key,'UTF8')),'hex'),request_sha256
 FROM bonafides.idempotency_records WHERE owner_id=c.owner_id AND (v_workspace OR workflow_run_id=ANY(v_workflows)
  OR EXISTS(
   SELECT 1 FROM jsonb_path_query(response_body,'$.**') AS node(value)
   CROSS JOIN LATERAL jsonb_each(CASE WHEN jsonb_typeof(node.value)='object'
    THEN node.value ELSE '{}'::jsonb END) AS field
   WHERE field.key IN ('id','application_id','document_id','document_revision_id','revision_id',
    'target_revision_id','workflow_id','workflow_run_id','proposal_id','artifact_id')
    AND field.value IN (SELECT to_jsonb(referenced.id::text) FROM unnest(v_ids||v_workflows) AS referenced(id))))
 ON CONFLICT DO NOTHING;
 DELETE FROM bonafides.idempotency_records r WHERE r.owner_id=c.owner_id AND EXISTS(
  SELECT 1 FROM bonafides.erased_request_keys k WHERE k.owner_id=r.owner_id
   AND k.route_sha256=encode(sha256(convert_to(r.route_key,'UTF8')),'hex')
   AND k.key_sha256=encode(sha256(convert_to(r.key,'UTF8')),'hex'));

 -- The controller itself and prior control receipts remain; raw queued inputs,
 -- results and checkpoints do not. A late ordinary executor has a stale fence.
 UPDATE bonafides.workflow_runs SET input='{"erased":true}',result=NULL,
  state=CASE WHEN state IN ('succeeded','cancelled','permanent_failure') THEN state ELSE 'cancelled'::bonafides.workflow_state END,
  cancel_requested_at=coalesce(cancel_requested_at,clock_timestamp()),
  finished_at=coalesce(finished_at,clock_timestamp()),lease_holder=NULL,lease_expires_at=NULL,
  fence=fence+1,version=version+1,updated_at=clock_timestamp(),last_error_code='data_erased'
 WHERE owner_id=c.owner_id AND id=ANY(v_workflows);
 UPDATE bonafides.workflow_steps SET checkpoint='{}',provider_operation_id=NULL
 WHERE owner_id=c.owner_id AND workflow_run_id=ANY(v_workflows);

 -- Break the consent/approval/document provenance cycle only for model rows
 -- being erased. An unexpected cross-scope dependency fails this transaction.
 UPDATE bonafides.model_runs SET hosted_consent_id=NULL WHERE owner_id=c.owner_id AND id=ANY(v_models)
  AND (v_workspace OR (NOT EXISTS(SELECT 1 FROM bonafides.document_revisions r WHERE r.owner_id=c.owner_id
   AND r.model_run_id=model_runs.id AND NOT r.id=ANY(v_revisions))
  AND NOT EXISTS(SELECT 1 FROM bonafides.match_assessments m WHERE m.owner_id=c.owner_id AND m.model_run_id=model_runs.id)));
 DELETE FROM bonafides.hosted_egress_consents h WHERE h.owner_id=c.owner_id
  AND (v_workspace OR h.workflow_run_id=ANY(v_workflows))
  AND NOT EXISTS(SELECT 1 FROM bonafides.model_runs m WHERE m.owner_id=c.owner_id AND m.hosted_consent_id=h.id);
 DELETE FROM bonafides.action_approvals a WHERE a.owner_id=c.owner_id
  AND (v_workspace OR a.workflow_run_id=ANY(v_workflows) OR a.document_revision_id=ANY(v_revisions))
  AND NOT EXISTS(SELECT 1 FROM bonafides.installation_controls k WHERE k.approval_id=a.id)
  AND NOT EXISTS(SELECT 1 FROM bonafides.hosted_egress_consents h WHERE h.owner_id=c.owner_id AND h.approval_id=a.id);
 DELETE FROM bonafides.document_regeneration_proposals WHERE owner_id=c.owner_id AND document_draft_id=ANY(v_documents);
 DELETE FROM bonafides.document_drafts WHERE owner_id=c.owner_id AND id=ANY(v_documents);
 DELETE FROM bonafides.applications WHERE owner_id=c.owner_id AND (v_workspace OR id=v_application);
 IF v_workspace THEN
  DELETE FROM bonafides.match_assessments WHERE owner_id=c.owner_id;
 END IF;
 DELETE FROM bonafides.model_runs m WHERE m.owner_id=c.owner_id AND m.id=ANY(v_models)
  AND NOT EXISTS(SELECT 1 FROM bonafides.document_revisions r WHERE r.owner_id=c.owner_id AND r.model_run_id=m.id)
  AND NOT EXISTS(SELECT 1 FROM bonafides.match_assessments a WHERE a.owner_id=c.owner_id AND a.model_run_id=m.id)
  AND NOT EXISTS(SELECT 1 FROM bonafides.document_regeneration_proposals p WHERE p.owner_id=c.owner_id AND p.model_run_id=m.id);
 DELETE FROM bonafides.portable_model_history h WHERE h.owner_id=c.owner_id AND h.id=ANY(v_history)
  AND NOT EXISTS(SELECT 1 FROM bonafides.document_revisions r WHERE r.owner_id=c.owner_id AND r.historical_model_run_id=h.id)
  AND NOT EXISTS(SELECT 1 FROM bonafides.match_assessments a WHERE a.owner_id=c.owner_id AND a.historical_model_run_id=h.id)
  AND NOT EXISTS(SELECT 1 FROM bonafides.document_regeneration_proposals p WHERE p.owner_id=c.owner_id AND p.historical_model_run_id=h.id);

 IF v_workspace THEN
  FOREACH v_table IN ARRAY ARRAY['transfer_manifests','import_batches','profile_revision_evidence',
   'profile_revision_claims','claim_evidence_links','professional_claims','profiles','evidence_records',
   'posting_duplicate_decisions','saved_opportunities','posting_requirement_revisions','job_source_observations',
   'job_postings','encrypted_credentials','external_accounts','local_model_reservations','local_model_route_blocks',
   'event_metadata','event_receipts','outbox_events','audit_events'] LOOP
   EXECUTE format('DELETE FROM bonafides.%I WHERE owner_id=$1',v_table) USING c.owner_id;
  END LOOP;
  -- Retain only lifecycle rows referenced by immutable installation receipts.
  DELETE FROM bonafides.workflow_runs w WHERE w.owner_id=c.owner_id AND w.id<>c.id
   AND NOT EXISTS(SELECT 1 FROM bonafides.installation_controls k WHERE k.id=w.id);
  -- Reviews restrict config deletion; the reverse current-review FK is deferred.
  DELETE FROM bonafides.source_policy_reviews WHERE owner_id=c.owner_id;
  DELETE FROM bonafides.source_configs WHERE owner_id=c.owner_id;
  UPDATE bonafides.owner_settings SET version=version+1,workspace_version=workspace_version+1,
   privacy_mode='local_only',timezone='UTC',settings='{}',updated_at=clock_timestamp() WHERE owner_id=c.owner_id;
  IF c.purpose='privacy_delete' THEN
   INSERT INTO bonafides.profiles(owner_id) VALUES(c.owner_id);
  END IF;
 ELSE
  UPDATE bonafides.owner_settings SET workspace_version=workspace_version+1,updated_at=clock_timestamp() WHERE owner_id=c.owner_id;
 END IF;

 WITH removed AS (
  DELETE FROM bonafides.blobs b WHERE b.owner_id=c.owner_id AND b.id=ANY(v_blobs)
   AND NOT EXISTS(SELECT 1 FROM bonafides.evidence_revisions r WHERE r.owner_id=c.owner_id AND r.blob_id=b.id)
   AND NOT EXISTS(SELECT 1 FROM bonafides.application_attachments a WHERE a.owner_id=c.owner_id AND a.blob_id=b.id)
   AND NOT EXISTS(SELECT 1 FROM bonafides.document_artifacts a WHERE a.owner_id=c.owner_id AND a.blob_id=b.id)
   AND NOT EXISTS(SELECT 1 FROM bonafides.import_batches i WHERE i.owner_id=c.owner_id AND i.blob_id=b.id)
   AND NOT EXISTS(SELECT 1 FROM bonafides.transfer_manifests t WHERE t.owner_id=c.owner_id AND t.blob_id=b.id)
  RETURNING storage_key,sha256,byte_length
 ) INSERT INTO bonafides.portable_purge_items(control_id,kind,storage_key,sha256,byte_length)
 SELECT c.id,'cas',storage_key,sha256,byte_length FROM removed ON CONFLICT DO NOTHING;
 -- Portable exports contain the whole owner scope, even for application erasure.
 INSERT INTO bonafides.portable_purge_items(control_id,kind,storage_key,sha256,byte_length)
 SELECT c.id,'export',workflow_run_id::text||'.zip',archive_sha256,byte_length
 FROM bonafides.portable_exports WHERE owner_id=c.owner_id AND state<>'expired'
 ON CONFLICT DO NOTHING;
 FOR v_item IN SELECT value FROM jsonb_array_elements(p_orphans) LOOP
  IF v_item->>'kind' IS NULL OR v_item->>'kind' NOT IN ('cas','cas_staging') THEN
   RAISE EXCEPTION 'invalid orphan scope' USING ERRCODE='55000';
  END IF;
  INSERT INTO bonafides.portable_purge_items(control_id,kind,storage_key,sha256,byte_length)
  SELECT c.id,v_item->>'kind',v_item->>'storage_key',v_item->>'sha256',(v_item->>'byte_length')::bigint
  WHERE v_item->>'kind'='cas_staging' OR NOT EXISTS(
   SELECT 1 FROM bonafides.blobs WHERE owner_id=c.owner_id AND storage_key=v_item->>'storage_key')
  ON CONFLICT DO NOTHING;
 END LOOP;
 DELETE FROM bonafides.oauth_transactions WHERE owner_id=c.owner_id;
 DELETE FROM bonafides.sessions WHERE owner_id=c.owner_id;
 UPDATE bonafides.owners SET auth_epoch=auth_epoch+1 WHERE id=c.owner_id;
 UPDATE bonafides.action_approvals SET revoked_at=clock_timestamp(),version=version+1 WHERE owner_id=c.owner_id AND revoked_at IS NULL;
 UPDATE bonafides.hosted_egress_consents SET revoked_at=clock_timestamp() WHERE owner_id=c.owner_id AND revoked_at IS NULL;
 SELECT count(*) INTO v_count FROM bonafides.portable_purge_items WHERE control_id=c.id;
 INSERT INTO bonafides.audit_events(owner_id,action,actor_kind,actor_id,request_id,entity_type,entity_id,detail)
 VALUES(c.owner_id,'data_erasure_committed','worker',c.holder::text,c.id,'installation_control',c.id,
  jsonb_build_object('scope',CASE WHEN v_workspace THEN 'workspace' ELSE 'application' END,'purge_items',v_count));
 PERFORM bonafides.assert_installation_control(p_id,p_holder,p_fence);
 RETURN jsonb_build_object('scope',CASE WHEN v_workspace THEN 'workspace' ELSE 'application' END,
  'application_id',v_application,'purge_items',v_count,'filesystem_complete',false);
END $$;
REVOKE ALL ON FUNCTION erase_portable_scope(uuid,uuid,bigint,uuid,jsonb,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION erase_portable_scope(uuid,uuid,bigint,uuid,jsonb,jsonb) TO bonafides_runtime;
INSERT INTO schema_migrations(version,source_sha256) VALUES(21,repeat('0',64));
