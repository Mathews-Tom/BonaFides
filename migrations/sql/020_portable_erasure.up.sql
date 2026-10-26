-- SPDX-License-Identifier: MIT OR Apache-2.0
SET search_path=offerloom,pg_catalog;

-- Request bodies and cached responses are erased, not merely hidden. Retaining
-- only these hashes prevents a delayed original request from recreating data.
CREATE TABLE erased_request_keys (
 owner_id uuid NOT NULL REFERENCES owners(id) ON DELETE CASCADE,
 route_sha256 text NOT NULL CHECK(route_sha256 ~ '^[0-9a-f]{64}$'),
 key_sha256 text NOT NULL CHECK(key_sha256 ~ '^[0-9a-f]{64}$'),
 request_sha256 text NOT NULL CHECK(request_sha256 ~ '^[0-9a-f]{64}$'),
 erased_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(owner_id,route_sha256,key_sha256)
);
CREATE TABLE portable_purges (
 control_id uuid PRIMARY KEY REFERENCES installation_controls(id),
 owner_id uuid NOT NULL REFERENCES owners(id) ON DELETE CASCADE,
 scope text NOT NULL CHECK(scope IN ('application','workspace')),
 application_id uuid,
 committed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 completed_at timestamptz,
 CHECK((scope='application')=(application_id IS NOT NULL))
);
CREATE TABLE portable_purge_items (
 control_id uuid NOT NULL REFERENCES portable_purges(control_id) ON DELETE CASCADE,
 kind text NOT NULL CHECK(kind IN ('cas','cas_staging','export','candidate')),
 storage_key text NOT NULL,
 sha256 text NOT NULL CHECK(sha256 ~ '^[0-9a-f]{64}$'),
 byte_length bigint NOT NULL CHECK(byte_length>=0),
 resolved_at timestamptz,
 outcome text CHECK(outcome IN ('removed','retained')),
 PRIMARY KEY(control_id,kind,storage_key),
 CHECK((resolved_at IS NULL)=(outcome IS NULL)),
 CHECK((kind='cas' AND storage_key=left(sha256,2)||'/'||sha256)
  OR (kind='cas_staging' AND storage_key ~ '^[0-9a-f]{2}/\.staging-[0-9a-f]{32}$')
  OR (kind IN ('export','candidate') AND storage_key ~ '^[0-9a-f-]{36}\.zip$'))
);
REVOKE ALL ON erased_request_keys,portable_purges,portable_purge_items FROM PUBLIC,offerloom_runtime;
GRANT SELECT ON erased_request_keys,portable_purges,portable_purge_items TO offerloom_runtime;

CREATE FUNCTION assert_portable_checkpoint(
 p_id uuid,p_holder uuid,p_fence bigint,p_checkpoint uuid,p_runtime_pins jsonb
) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,offerloom AS $$
DECLARE
 c offerloom.installation_controls%ROWTYPE;
 b offerloom.installation_backups%ROWTYPE;
BEGIN
 SELECT * INTO STRICT c FROM offerloom.assert_installation_control(p_id,p_holder,p_fence);
 SELECT * INTO STRICT b FROM offerloom.installation_backups WHERE id=p_checkpoint;
 IF c.phase<>'quiescent' OR c.purpose<>'portable_import'
  OR b.owner_id<>c.owner_id OR b.namespace_id<>c.namespace_id
  OR b.maintenance_epoch<>c.maintenance_epoch-1 OR b.restore_verified_at IS NULL
  OR b.workspace_version<>(SELECT workspace_version FROM offerloom.owner_settings WHERE owner_id=c.owner_id)
  OR b.owner_auth_epoch<>(SELECT auth_epoch FROM offerloom.owners WHERE id=c.owner_id)
  OR b.installation_auth_epoch<>(SELECT auth_epoch FROM offerloom.installation_state WHERE singleton)
  OR b.schema_versions IS DISTINCT FROM (SELECT jsonb_object_agg(version::text,source_sha256) FROM offerloom.schema_migrations)
  OR p_runtime_pins IS DISTINCT FROM jsonb_build_object(
   'image_sha256',b.image_sha256,'source_sha256',b.source_sha256,
   'config_sha256',b.config_sha256,'keyring_sha256',b.keyring_sha256,
   'recipient_sha256',b.recipient_sha256,'cipher_sha256',b.cipher_sha256)
 THEN
  RAISE EXCEPTION 'fresh verified checkpoint required' USING ERRCODE='55000';
 END IF;
END $$;
REVOKE ALL ON FUNCTION assert_portable_checkpoint(uuid,uuid,bigint,uuid,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION assert_portable_checkpoint(uuid,uuid,bigint,uuid,jsonb) TO offerloom_runtime;

CREATE FUNCTION erase_portable_scope(
 p_id uuid,p_holder uuid,p_fence bigint,p_checkpoint uuid DEFAULT NULL,
 p_runtime_pins jsonb DEFAULT '{}',p_orphans jsonb DEFAULT '[]'
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,offerloom AS $$
DECLARE
 c offerloom.installation_controls%ROWTYPE;
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
 SELECT * INTO STRICT c FROM offerloom.assert_installation_control(p_id,p_holder,p_fence);
 IF c.phase<>'quiescent' OR c.purpose NOT IN ('privacy_delete','portable_import')
 OR EXISTS(SELECT 1 FROM offerloom.installation_activities)
 OR EXISTS(SELECT 1 FROM offerloom.provider_executions)
 OR EXISTS(SELECT 1 FROM offerloom.portable_purges WHERE control_id=c.id) THEN
  RAISE EXCEPTION 'portable erasure refused' USING ERRCODE='55000';
 END IF;
 v_workspace:=c.purpose='portable_import' OR coalesce(c.scope->'payload'->'categories'='["all_workspace"]'::jsonb,false);
 IF NOT v_workspace THEN
  IF c.scope->'payload'->'categories' IS DISTINCT FROM '["applications"]'::jsonb
   OR c.scope->'payload'->>'application_id' IS NULL THEN
   RAISE EXCEPTION 'unsupported deletion scope' USING ERRCODE='55000';
  END IF;
  v_application:=(c.scope->'payload'->>'application_id')::uuid;
  IF NOT EXISTS(SELECT 1 FROM offerloom.applications WHERE owner_id=c.owner_id
   AND id=v_application AND version=(c.scope->'payload'->>'application_version')::bigint) THEN
   RAISE EXCEPTION 'stale application deletion' USING ERRCODE='55000';
  END IF;
 END IF;
 IF c.purpose='portable_import' THEN
  PERFORM offerloom.assert_portable_checkpoint(p_id,p_holder,p_fence,p_checkpoint,p_runtime_pins);
 END IF;
 IF jsonb_typeof(p_orphans) IS DISTINCT FROM 'array' OR jsonb_array_length(p_orphans)>100000
 OR octet_length(p_orphans::text)>33554432 THEN
  RAISE EXCEPTION 'invalid orphan inventory' USING ERRCODE='55000';
 END IF;
 SET CONSTRAINTS ALL DEFERRED;
 SELECT coalesce(array_agg(id),'{}'::uuid[]) INTO v_documents
 FROM offerloom.document_drafts WHERE owner_id=c.owner_id AND (v_workspace OR application_id=v_application);
 SELECT coalesce(array_agg(id),'{}'::uuid[]) INTO v_revisions
 FROM offerloom.document_revisions WHERE owner_id=c.owner_id AND document_draft_id=ANY(v_documents);
 SELECT coalesce(array_agg(id),'{}'::uuid[]) INTO v_ids FROM (
  SELECT unnest(v_documents||v_revisions) AS id
  UNION SELECT v_application WHERE v_application IS NOT NULL
  UNION SELECT id FROM offerloom.application_notes WHERE owner_id=c.owner_id AND application_id=v_application
  UNION SELECT id FROM offerloom.application_contacts WHERE owner_id=c.owner_id AND application_id=v_application
  UNION SELECT id FROM offerloom.application_attachments WHERE owner_id=c.owner_id AND application_id=v_application
  UNION SELECT id FROM offerloom.application_stage_events WHERE owner_id=c.owner_id AND application_id=v_application
  UNION SELECT id FROM offerloom.appointments WHERE owner_id=c.owner_id AND application_id=v_application
  UNION SELECT id FROM offerloom.document_regeneration_proposals WHERE owner_id=c.owner_id AND document_draft_id=ANY(v_documents)
 ) selected;
 SELECT coalesce(array_agg(id),'{}'::uuid[]) INTO v_workflows FROM offerloom.workflow_runs
 WHERE owner_id=c.owner_id AND id<>c.id AND (v_workspace OR
  jsonb_path_exists(input,'$.** ? (@ == $ids[*])',jsonb_build_object('ids',to_jsonb(v_ids))));
 SELECT coalesce(array_agg(id),'{}'::uuid[]) INTO v_models FROM offerloom.model_runs
 WHERE owner_id=c.owner_id AND (v_workspace OR workflow_run_id=ANY(v_workflows)
  OR id IN (SELECT model_run_id FROM offerloom.document_revisions WHERE owner_id=c.owner_id AND id=ANY(v_revisions))
  OR id IN (SELECT model_run_id FROM offerloom.document_regeneration_proposals WHERE owner_id=c.owner_id AND document_draft_id=ANY(v_documents)));
 SELECT coalesce(array_agg(id),'{}'::uuid[]) INTO v_history FROM offerloom.portable_model_history
 WHERE owner_id=c.owner_id AND (v_workspace OR
  id IN (SELECT historical_model_run_id FROM offerloom.document_revisions WHERE owner_id=c.owner_id AND id=ANY(v_revisions))
  OR id IN (SELECT historical_model_run_id FROM offerloom.document_regeneration_proposals WHERE owner_id=c.owner_id AND document_draft_id=ANY(v_documents)));
 SELECT coalesce(array_agg(id),'{}'::uuid[]) INTO v_blobs FROM offerloom.blobs WHERE owner_id=c.owner_id AND (v_workspace
  OR id IN (SELECT blob_id FROM offerloom.application_attachments WHERE owner_id=c.owner_id AND application_id=v_application)
  OR id IN (SELECT blob_id FROM offerloom.document_artifacts WHERE owner_id=c.owner_id AND document_revision_id=ANY(v_revisions)));

 INSERT INTO offerloom.portable_purges(control_id,owner_id,scope,application_id)
 VALUES(c.id,c.owner_id,CASE WHEN v_workspace THEN 'workspace' ELSE 'application' END,v_application);
 INSERT INTO offerloom.erased_request_keys(owner_id,route_sha256,key_sha256,request_sha256)
 SELECT owner_id,encode(sha256(convert_to(route_key,'UTF8')),'hex'),encode(sha256(convert_to(key,'UTF8')),'hex'),request_sha256
 FROM offerloom.idempotency_records WHERE owner_id=c.owner_id AND (v_workspace OR workflow_run_id=ANY(v_workflows)
  OR jsonb_path_exists(response_body,'$.** ? (@ == $ids[*])',jsonb_build_object('ids',to_jsonb(v_ids||v_workflows))))
 ON CONFLICT DO NOTHING;
 DELETE FROM offerloom.idempotency_records r WHERE r.owner_id=c.owner_id AND EXISTS(
  SELECT 1 FROM offerloom.erased_request_keys k WHERE k.owner_id=r.owner_id
   AND k.route_sha256=encode(sha256(convert_to(r.route_key,'UTF8')),'hex')
   AND k.key_sha256=encode(sha256(convert_to(r.key,'UTF8')),'hex'));

 -- The controller itself and prior control receipts remain; raw queued inputs,
 -- results and checkpoints do not. A late ordinary executor has a stale fence.
 UPDATE offerloom.workflow_runs SET input='{"erased":true}',result=NULL,
  state=CASE WHEN state IN ('succeeded','cancelled','permanent_failure') THEN state ELSE 'cancelled'::offerloom.workflow_state END,
  cancel_requested_at=coalesce(cancel_requested_at,clock_timestamp()),
  finished_at=coalesce(finished_at,clock_timestamp()),lease_holder=NULL,lease_expires_at=NULL,
  fence=fence+1,version=version+1,updated_at=clock_timestamp(),last_error_code='data_erased'
 WHERE owner_id=c.owner_id AND id=ANY(v_workflows);
 UPDATE offerloom.workflow_steps SET checkpoint='{}',provider_operation_id=NULL
 WHERE owner_id=c.owner_id AND workflow_run_id=ANY(v_workflows);

 -- Break the consent/approval/document provenance cycle only for model rows
 -- being erased. An unexpected cross-scope dependency fails this transaction.
 UPDATE offerloom.model_runs SET hosted_consent_id=NULL WHERE owner_id=c.owner_id AND id=ANY(v_models)
  AND (v_workspace OR (NOT EXISTS(SELECT 1 FROM offerloom.document_revisions r WHERE r.owner_id=c.owner_id
   AND r.model_run_id=model_runs.id AND NOT r.id=ANY(v_revisions))
  AND NOT EXISTS(SELECT 1 FROM offerloom.match_assessments m WHERE m.owner_id=c.owner_id AND m.model_run_id=model_runs.id)));
 DELETE FROM offerloom.hosted_egress_consents h WHERE h.owner_id=c.owner_id
  AND (v_workspace OR h.workflow_run_id=ANY(v_workflows))
  AND NOT EXISTS(SELECT 1 FROM offerloom.model_runs m WHERE m.owner_id=c.owner_id AND m.hosted_consent_id=h.id);
 DELETE FROM offerloom.action_approvals a WHERE a.owner_id=c.owner_id
  AND (v_workspace OR a.workflow_run_id=ANY(v_workflows) OR a.document_revision_id=ANY(v_revisions))
  AND NOT EXISTS(SELECT 1 FROM offerloom.installation_controls k WHERE k.approval_id=a.id)
  AND NOT EXISTS(SELECT 1 FROM offerloom.hosted_egress_consents h WHERE h.owner_id=c.owner_id AND h.approval_id=a.id);
 DELETE FROM offerloom.document_regeneration_proposals WHERE owner_id=c.owner_id AND document_draft_id=ANY(v_documents);
 DELETE FROM offerloom.document_drafts WHERE owner_id=c.owner_id AND id=ANY(v_documents);
 DELETE FROM offerloom.applications WHERE owner_id=c.owner_id AND (v_workspace OR id=v_application);
 IF v_workspace THEN
  DELETE FROM offerloom.match_assessments WHERE owner_id=c.owner_id;
 END IF;
 DELETE FROM offerloom.model_runs m WHERE m.owner_id=c.owner_id AND m.id=ANY(v_models)
  AND NOT EXISTS(SELECT 1 FROM offerloom.document_revisions r WHERE r.owner_id=c.owner_id AND r.model_run_id=m.id)
  AND NOT EXISTS(SELECT 1 FROM offerloom.match_assessments a WHERE a.owner_id=c.owner_id AND a.model_run_id=m.id)
  AND NOT EXISTS(SELECT 1 FROM offerloom.document_regeneration_proposals p WHERE p.owner_id=c.owner_id AND p.model_run_id=m.id);
 DELETE FROM offerloom.portable_model_history h WHERE h.owner_id=c.owner_id AND h.id=ANY(v_history)
  AND NOT EXISTS(SELECT 1 FROM offerloom.document_revisions r WHERE r.owner_id=c.owner_id AND r.historical_model_run_id=h.id)
  AND NOT EXISTS(SELECT 1 FROM offerloom.match_assessments a WHERE a.owner_id=c.owner_id AND a.historical_model_run_id=h.id)
  AND NOT EXISTS(SELECT 1 FROM offerloom.document_regeneration_proposals p WHERE p.owner_id=c.owner_id AND p.historical_model_run_id=h.id);

 IF v_workspace THEN
  FOREACH v_table IN ARRAY ARRAY['transfer_manifests','import_batches','profile_revision_evidence',
   'profile_revision_claims','claim_evidence_links','professional_claims','profiles','evidence_records',
   'posting_duplicate_decisions','saved_opportunities','posting_requirement_revisions','job_source_observations',
   'job_postings','encrypted_credentials','external_accounts','local_model_reservations','local_model_route_blocks',
   'event_metadata','event_receipts','outbox_events','audit_events'] LOOP
   EXECUTE format('DELETE FROM offerloom.%I WHERE owner_id=$1',v_table) USING c.owner_id;
  END LOOP;
  -- Retain only lifecycle rows referenced by immutable installation receipts.
  DELETE FROM offerloom.workflow_runs w WHERE w.owner_id=c.owner_id AND w.id<>c.id
   AND NOT EXISTS(SELECT 1 FROM offerloom.installation_controls k WHERE k.id=w.id);
  -- Reviews restrict config deletion; the reverse current-review FK is deferred.
  DELETE FROM offerloom.source_policy_reviews WHERE owner_id=c.owner_id;
  DELETE FROM offerloom.source_configs WHERE owner_id=c.owner_id;
  UPDATE offerloom.owner_settings SET version=version+1,workspace_version=workspace_version+1,
   privacy_mode='local_only',timezone='UTC',settings='{}',updated_at=clock_timestamp() WHERE owner_id=c.owner_id;
  IF c.purpose='privacy_delete' THEN
   INSERT INTO offerloom.profiles(owner_id) VALUES(c.owner_id);
  END IF;
 ELSE
  UPDATE offerloom.owner_settings SET workspace_version=workspace_version+1,updated_at=clock_timestamp() WHERE owner_id=c.owner_id;
 END IF;

 WITH removed AS (
  DELETE FROM offerloom.blobs b WHERE b.owner_id=c.owner_id AND b.id=ANY(v_blobs)
   AND NOT EXISTS(SELECT 1 FROM offerloom.evidence_revisions r WHERE r.owner_id=c.owner_id AND r.blob_id=b.id)
   AND NOT EXISTS(SELECT 1 FROM offerloom.application_attachments a WHERE a.owner_id=c.owner_id AND a.blob_id=b.id)
   AND NOT EXISTS(SELECT 1 FROM offerloom.document_artifacts a WHERE a.owner_id=c.owner_id AND a.blob_id=b.id)
   AND NOT EXISTS(SELECT 1 FROM offerloom.import_batches i WHERE i.owner_id=c.owner_id AND i.blob_id=b.id)
   AND NOT EXISTS(SELECT 1 FROM offerloom.transfer_manifests t WHERE t.owner_id=c.owner_id AND t.blob_id=b.id)
  RETURNING storage_key,sha256,byte_length
 ) INSERT INTO offerloom.portable_purge_items(control_id,kind,storage_key,sha256,byte_length)
 SELECT c.id,'cas',storage_key,sha256,byte_length FROM removed ON CONFLICT DO NOTHING;
 -- Portable exports contain the whole owner scope, even for application erasure.
 INSERT INTO offerloom.portable_purge_items(control_id,kind,storage_key,sha256,byte_length)
 SELECT c.id,'export',workflow_run_id::text||'.zip',archive_sha256,byte_length
 FROM offerloom.portable_exports WHERE owner_id=c.owner_id AND state<>'expired'
 ON CONFLICT DO NOTHING;
 FOR v_item IN SELECT value FROM jsonb_array_elements(p_orphans) LOOP
  IF v_item->>'kind' IS NULL OR v_item->>'kind' NOT IN ('cas','cas_staging') THEN
   RAISE EXCEPTION 'invalid orphan scope' USING ERRCODE='55000';
  END IF;
  INSERT INTO offerloom.portable_purge_items(control_id,kind,storage_key,sha256,byte_length)
  SELECT c.id,v_item->>'kind',v_item->>'storage_key',v_item->>'sha256',(v_item->>'byte_length')::bigint
  WHERE v_item->>'kind'='cas_staging' OR NOT EXISTS(
   SELECT 1 FROM offerloom.blobs WHERE owner_id=c.owner_id AND storage_key=v_item->>'storage_key')
  ON CONFLICT DO NOTHING;
 END LOOP;
 DELETE FROM offerloom.oauth_transactions WHERE owner_id=c.owner_id;
 DELETE FROM offerloom.sessions WHERE owner_id=c.owner_id;
 UPDATE offerloom.owners SET auth_epoch=auth_epoch+1 WHERE id=c.owner_id;
 UPDATE offerloom.action_approvals SET revoked_at=clock_timestamp(),version=version+1 WHERE owner_id=c.owner_id AND revoked_at IS NULL;
 UPDATE offerloom.hosted_egress_consents SET revoked_at=clock_timestamp() WHERE owner_id=c.owner_id AND revoked_at IS NULL;
 SELECT count(*) INTO v_count FROM offerloom.portable_purge_items WHERE control_id=c.id;
 INSERT INTO offerloom.audit_events(owner_id,action,actor_kind,actor_id,request_id,entity_type,entity_id,detail)
 VALUES(c.owner_id,'data_erasure_committed','worker',c.holder::text,c.id,'installation_control',c.id,
  jsonb_build_object('scope',CASE WHEN v_workspace THEN 'workspace' ELSE 'application' END,'purge_items',v_count));
 PERFORM offerloom.assert_installation_control(p_id,p_holder,p_fence);
 RETURN jsonb_build_object('scope',CASE WHEN v_workspace THEN 'workspace' ELSE 'application' END,
  'application_id',v_application,'purge_items',v_count,'filesystem_complete',false);
END $$;

CREATE FUNCTION resolve_portable_purge_item(
 p_id uuid,p_holder uuid,p_fence bigint,p_kind text,p_key text,p_outcome text
) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,offerloom AS $$
DECLARE c offerloom.installation_controls%ROWTYPE;
 v_live boolean;
BEGIN
 SELECT * INTO STRICT c FROM offerloom.assert_installation_control(p_id,p_holder,p_fence);
 IF c.phase<>'quiescent' OR NOT EXISTS(SELECT 1 FROM offerloom.portable_purges
  WHERE control_id=c.id AND owner_id=c.owner_id AND completed_at IS NULL) THEN
  RAISE EXCEPTION 'purge authority required' USING ERRCODE='55000';
 END IF;
 v_live:=p_kind='cas' AND EXISTS(SELECT 1 FROM offerloom.blobs WHERE owner_id=c.owner_id AND storage_key=p_key);
 IF p_outcome NOT IN ('removed','retained') OR (p_outcome='retained') IS DISTINCT FROM v_live THEN
  RAISE EXCEPTION 'purge disposition refused' USING ERRCODE='55000';
 END IF;
 UPDATE offerloom.portable_purge_items SET resolved_at=clock_timestamp(),outcome=p_outcome
 WHERE control_id=c.id AND kind=p_kind AND storage_key=p_key AND resolved_at IS NULL;
 IF NOT FOUND THEN
  RAISE EXCEPTION 'unknown or settled purge item' USING ERRCODE='55000';
 END IF;
 IF p_kind='export' THEN
  UPDATE offerloom.portable_exports SET state='expired'
  WHERE owner_id=c.owner_id AND workflow_run_id::text||'.zip'=p_key;
 END IF;
END $$;

CREATE FUNCTION complete_portable_purge(p_id uuid,p_holder uuid,p_fence bigint)
 RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,offerloom AS $$
DECLARE c offerloom.installation_controls%ROWTYPE;
BEGIN
 SELECT * INTO STRICT c FROM offerloom.assert_installation_control(p_id,p_holder,p_fence);
 IF c.phase<>'quiescent' OR EXISTS(SELECT 1 FROM offerloom.portable_purge_items WHERE control_id=c.id AND resolved_at IS NULL) THEN
  RAISE EXCEPTION 'live purge incomplete' USING ERRCODE='55000';
 END IF;
 IF EXISTS(SELECT 1 FROM offerloom.portable_purges
  WHERE control_id=c.id AND owner_id=c.owner_id AND completed_at IS NOT NULL) THEN
  RETURN;
 END IF;
 UPDATE offerloom.portable_purges SET completed_at=clock_timestamp()
 WHERE control_id=c.id AND owner_id=c.owner_id AND completed_at IS NULL;
 IF NOT FOUND THEN
  RAISE EXCEPTION 'purge receipt required' USING ERRCODE='55000';
 END IF;
END $$;

-- Database publication is not filesystem completion. Admission stays closed
-- until every item is either fsynced absent or still referenced by live data.
CREATE OR REPLACE FUNCTION settle_installation_control(p_id uuid,p_holder uuid,p_fence bigint,p_result jsonb)
 RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,offerloom AS $$
DECLARE c offerloom.installation_controls%ROWTYPE;
BEGIN
 SELECT * INTO STRICT c FROM offerloom.assert_installation_control(p_id,p_holder,p_fence);
 IF c.phase<>'quiescent' OR jsonb_typeof(p_result) IS DISTINCT FROM 'object'
 OR octet_length(p_result::text)>16384
 OR EXISTS(SELECT 1 FROM offerloom.installation_activities)
 OR EXISTS(SELECT 1 FROM offerloom.provider_executions)
 OR (c.purpose IN ('privacy_delete','portable_import') AND NOT EXISTS(
  SELECT 1 FROM offerloom.portable_purges WHERE control_id=c.id AND completed_at IS NOT NULL)) THEN
  RAISE EXCEPTION 'installation settlement refused' USING ERRCODE='55000';
 END IF;
 UPDATE offerloom.installation_controls SET phase='settled',settled_at=clock_timestamp(),result=p_result WHERE id=c.id;
END $$;
REVOKE ALL ON FUNCTION erase_portable_scope(uuid,uuid,bigint,uuid,jsonb,jsonb),
 resolve_portable_purge_item(uuid,uuid,bigint,text,text,text),complete_portable_purge(uuid,uuid,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION erase_portable_scope(uuid,uuid,bigint,uuid,jsonb,jsonb),
 resolve_portable_purge_item(uuid,uuid,bigint,text,text,text),complete_portable_purge(uuid,uuid,bigint) TO offerloom_runtime;
INSERT INTO schema_migrations(version,source_sha256) VALUES(20,repeat('0',64));
