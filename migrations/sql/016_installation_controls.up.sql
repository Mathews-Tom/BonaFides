-- SPDX-License-Identifier: MIT OR Apache-2.0
SET search_path=offerloom,pg_catalog;

-- No runtime parent DELETE may bypass immutable-history privileges by CASCADE.
-- Ordinary removal is archival; physical removal belongs to exact lifecycle controls.
REVOKE DELETE ON ALL TABLES IN SCHEMA offerloom FROM offerloom_runtime;
GRANT DELETE ON auth_challenges,auth_throttles,oauth_transactions,worker_heartbeats,
 installation_activities,provider_executions TO offerloom_runtime;
REVOKE INSERT,UPDATE ON installation_state FROM offerloom_runtime;
GRANT UPDATE(bootstrap_token_sha256,bootstrap_expires_at,bootstrap_consumed_at)
 ON installation_state TO offerloom_runtime;

CREATE TABLE installation_controls (
 id uuid PRIMARY KEY,
 owner_id uuid NOT NULL REFERENCES owners(id),
 holder uuid NOT NULL,
 fence bigint NOT NULL CHECK(fence>0),
 workflow_fence bigint NOT NULL CHECK(workflow_fence>0),
 namespace_id uuid NOT NULL,
 maintenance_epoch bigint NOT NULL CHECK(maintenance_epoch>0),
 purpose text NOT NULL CHECK(purpose IN ('portable_export','portable_import','privacy_delete','backup','restore_validate')),
 approval_id uuid,
 scope jsonb NOT NULL CHECK(jsonb_typeof(scope)='object'),
 phase text NOT NULL DEFAULT 'draining' CHECK(phase IN ('draining','quiescent','settled','released')),
 deadline_at timestamptz NOT NULL,
 drain_deadline_at timestamptz NOT NULL,
 lease_expires_at timestamptz NOT NULL,
 started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 settled_at timestamptz,
 released_at timestamptz,
 result jsonb CHECK(result IS NULL OR jsonb_typeof(result)='object'),
 FOREIGN KEY(owner_id,id) REFERENCES workflow_runs(owner_id,id),
 FOREIGN KEY(owner_id,approval_id,id) REFERENCES action_approvals(owner_id,id,workflow_run_id),
 CHECK(drain_deadline_at<=deadline_at),
 CHECK((purpose IN ('portable_import','privacy_delete','restore_validate'))=(approval_id IS NOT NULL)),
 CHECK((phase IN ('settled','released'))=(settled_at IS NOT NULL)),
 CHECK((phase='released')=(released_at IS NOT NULL))
);
CREATE UNIQUE INDEX installation_one_active_control_idx ON installation_controls((true))
 WHERE phase<>'released';
REVOKE ALL ON installation_controls FROM PUBLIC,offerloom_runtime;
GRANT SELECT ON installation_controls TO offerloom_runtime;

CREATE FUNCTION begin_installation_control(
 p_owner uuid,p_workflow uuid,p_holder uuid,p_workflow_fence bigint,p_control_holder uuid
) RETURNS SETOF installation_controls LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,offerloom AS $$
DECLARE
 i offerloom.installation_state%ROWTYPE;
 w offerloom.workflow_runs%ROWTYPE;
 a offerloom.action_approvals%ROWTYPE;
 v_deadline timestamptz;
BEGIN
 SELECT * INTO STRICT i FROM offerloom.installation_state WHERE singleton FOR UPDATE;
 PERFORM offerloom.assert_workflow_fence(p_owner,p_workflow,p_holder,p_workflow_fence);
 SELECT * INTO STRICT w FROM offerloom.workflow_runs
 WHERE owner_id=p_owner AND id=p_workflow FOR UPDATE;
 IF w.kind NOT IN ('portable_export','portable_import','privacy_delete','backup','restore_validate')
 OR p_control_holder=p_holder THEN
  RAISE EXCEPTION 'invalid installation control' USING ERRCODE='55000';
 END IF;
 IF w.kind IN ('portable_import','privacy_delete','restore_validate') THEN
  SELECT * INTO STRICT a FROM offerloom.action_approvals
  WHERE owner_id=p_owner AND workflow_run_id=p_workflow AND intent IS NOT NULL FOR UPDATE;
  IF a.approved_at IS NULL OR a.consumed_at IS NULL OR a.revoked_at IS NOT NULL
  OR a.expires_at<=clock_timestamp()
  OR a.action_kind<>(CASE WHEN w.kind='privacy_delete' THEN 'privacy_delete' ELSE 'replace_workspace' END)
  OR (a.intent->>'namespace_id')::uuid IS DISTINCT FROM i.namespace_id
  OR (a.intent->>'maintenance_epoch')::bigint IS DISTINCT FROM i.maintenance_epoch
  OR a.target_version IS DISTINCT FROM (
   SELECT workspace_version FROM offerloom.owner_settings WHERE owner_id=p_owner
  ) THEN
   RAISE EXCEPTION 'unapproved installation control' USING ERRCODE='55000';
  END IF;
 END IF;
 v_deadline:=least(w.deadline_at,clock_timestamp()+interval '1 hour');
 UPDATE offerloom.installation_state SET maintenance_active=true,
  maintenance_epoch=maintenance_epoch+1 WHERE singleton;
 UPDATE offerloom.workflow_runs SET fence=fence+1,lease_holder=p_control_holder,
  lease_expires_at=least(v_deadline,clock_timestamp()+interval '30 seconds'),
  version=version+1,updated_at=clock_timestamp() WHERE owner_id=p_owner AND id=p_workflow;
 INSERT INTO offerloom.installation_controls(
  id,owner_id,holder,fence,workflow_fence,namespace_id,maintenance_epoch,purpose,
  approval_id,scope,deadline_at,drain_deadline_at,lease_expires_at
 ) VALUES(
  p_workflow,p_owner,p_control_holder,1,p_workflow_fence+1,i.namespace_id,i.maintenance_epoch+1,w.kind,
  a.id,CASE WHEN a.id IS NULL THEN w.input ELSE a.intent END,v_deadline,
  least(v_deadline,clock_timestamp()+interval '60 seconds'),
  least(v_deadline,clock_timestamp()+interval '30 seconds')
 );
 RETURN QUERY SELECT * FROM offerloom.installation_controls WHERE id=p_workflow;
END $$;

CREATE FUNCTION assert_installation_control(p_id uuid,p_holder uuid,p_fence bigint)
 RETURNS SETOF installation_controls LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,offerloom AS $$
DECLARE
 i offerloom.installation_state%ROWTYPE;
 c offerloom.installation_controls%ROWTYPE;
BEGIN
 SELECT * INTO STRICT i FROM offerloom.installation_state WHERE singleton FOR SHARE;
 SELECT * INTO STRICT c FROM offerloom.installation_controls WHERE id=p_id FOR UPDATE;
 IF NOT i.maintenance_active OR i.namespace_id<>c.namespace_id
 OR i.maintenance_epoch<>c.maintenance_epoch OR c.holder<>p_holder OR c.fence<>p_fence
 OR c.phase='released' OR c.deadline_at<=clock_timestamp()
 OR c.lease_expires_at<=clock_timestamp()
 OR NOT EXISTS(SELECT 1 FROM offerloom.workflow_runs w WHERE w.owner_id=c.owner_id
  AND w.id=c.id AND w.state='running' AND w.fence=c.workflow_fence
  AND w.lease_holder=c.holder AND w.cancel_requested_at IS NULL) THEN
  RAISE EXCEPTION 'stale installation control' USING ERRCODE='55000';
 END IF;
 RETURN NEXT c;
END $$;

CREATE FUNCTION heartbeat_installation_control(p_id uuid,p_holder uuid,p_fence bigint)
 RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,offerloom AS $$
DECLARE c offerloom.installation_controls%ROWTYPE;
BEGIN
 SELECT * INTO STRICT c FROM offerloom.assert_installation_control(p_id,p_holder,p_fence);
 UPDATE offerloom.installation_controls SET
  lease_expires_at=least(deadline_at,clock_timestamp()+interval '30 seconds') WHERE id=c.id;
 UPDATE offerloom.workflow_runs SET
  lease_expires_at=least(c.deadline_at,clock_timestamp()+interval '30 seconds'),
  last_heartbeat_at=clock_timestamp() WHERE owner_id=c.owner_id AND id=c.id;
END $$;

CREATE FUNCTION quiesce_installation_control(p_id uuid,p_holder uuid,p_fence bigint)
 RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,offerloom AS $$
DECLARE c offerloom.installation_controls%ROWTYPE;
BEGIN
 SELECT * INTO STRICT c FROM offerloom.assert_installation_control(p_id,p_holder,p_fence);
 IF c.phase<>'draining' OR c.drain_deadline_at<=clock_timestamp()
 OR EXISTS(SELECT 1 FROM offerloom.installation_activities)
 OR EXISTS(SELECT 1 FROM offerloom.provider_executions) THEN
  RAISE EXCEPTION 'installation not quiescent' USING ERRCODE='55000';
 END IF;
 UPDATE offerloom.installation_controls SET phase='quiescent' WHERE id=c.id;
END $$;

-- Settlement commits while admission is STILL closed. Only after both spool
-- pause receipts have been explicitly resumed may the controller release admission.
CREATE FUNCTION settle_installation_control(p_id uuid,p_holder uuid,p_fence bigint,p_result jsonb)
 RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,offerloom AS $$
DECLARE c offerloom.installation_controls%ROWTYPE;
BEGIN
 SELECT * INTO STRICT c FROM offerloom.assert_installation_control(p_id,p_holder,p_fence);
 IF c.phase<>'quiescent' OR jsonb_typeof(p_result) IS DISTINCT FROM 'object'
 OR octet_length(p_result::text)>16384
 OR EXISTS(SELECT 1 FROM offerloom.installation_activities)
 OR EXISTS(SELECT 1 FROM offerloom.provider_executions) THEN
  RAISE EXCEPTION 'installation settlement refused' USING ERRCODE='55000';
 END IF;
 UPDATE offerloom.installation_controls SET phase='settled',settled_at=clock_timestamp(),result=p_result
 WHERE id=c.id;
END $$;

CREATE FUNCTION release_installation_control(p_id uuid,p_holder uuid,p_fence bigint)
 RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,offerloom AS $$
DECLARE c offerloom.installation_controls%ROWTYPE;
BEGIN
 SELECT * INTO STRICT c FROM offerloom.assert_installation_control(p_id,p_holder,p_fence);
 IF c.phase<>'settled' THEN
  RAISE EXCEPTION 'installation settlement required' USING ERRCODE='55000';
 END IF;
 IF c.purpose IN ('portable_import','restore_validate')
 OR (c.purpose='privacy_delete' AND c.scope->'payload'->'categories' ? 'all_workspace') THEN
  UPDATE offerloom.workflow_runs SET state='cancelled',cancel_requested_at=coalesce(cancel_requested_at,clock_timestamp()),
   finished_at=clock_timestamp(),lease_holder=NULL,lease_expires_at=NULL,fence=fence+1,
   version=version+1,updated_at=clock_timestamp(),last_error_code='workspace_maintenance'
  WHERE owner_id=c.owner_id AND id<>c.id AND state NOT IN ('succeeded','cancelled','permanent_failure');
 ELSE
  -- An unchanged snapshot invalidates old executors, not queued owner intent.
  UPDATE offerloom.workflow_runs SET maintenance_epoch=c.maintenance_epoch,fence=fence+1,
   version=version+1,updated_at=clock_timestamp()
  WHERE owner_id=c.owner_id AND id<>c.id AND state IN ('pending','retryable_failure');
  UPDATE offerloom.workflow_runs SET state='retryable_failure',maintenance_epoch=c.maintenance_epoch,
   lease_holder=NULL,lease_expires_at=NULL,fence=fence+1,version=version+1,
   next_attempt_at=clock_timestamp(),updated_at=clock_timestamp(),last_error_code='worker_interrupted'
  WHERE owner_id=c.owner_id AND id<>c.id AND state='running';
 END IF;
 UPDATE offerloom.workflow_runs SET state='succeeded',result=c.result,finished_at=clock_timestamp(),
  lease_holder=NULL,lease_expires_at=NULL,fence=fence+1,version=version+1,updated_at=clock_timestamp(),
  last_error_code=NULL WHERE owner_id=c.owner_id AND id=c.id;
 INSERT INTO offerloom.workflow_attempt_events(owner_id,workflow_run_id,attempt,fence,event)
 SELECT owner_id,id,attempt,fence,'succeeded' FROM offerloom.workflow_runs WHERE owner_id=c.owner_id AND id=c.id;
 UPDATE offerloom.installation_controls SET phase='released',released_at=clock_timestamp() WHERE id=c.id;
 UPDATE offerloom.installation_state SET maintenance_active=false WHERE singleton;
END $$;

REVOKE ALL ON FUNCTION begin_installation_control(uuid,uuid,uuid,bigint,uuid),
 assert_installation_control(uuid,uuid,bigint),heartbeat_installation_control(uuid,uuid,bigint),
 quiesce_installation_control(uuid,uuid,bigint),settle_installation_control(uuid,uuid,bigint,jsonb),
 release_installation_control(uuid,uuid,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION begin_installation_control(uuid,uuid,uuid,bigint,uuid),
 assert_installation_control(uuid,uuid,bigint),heartbeat_installation_control(uuid,uuid,bigint),
 quiesce_installation_control(uuid,uuid,bigint),settle_installation_control(uuid,uuid,bigint,jsonb),
 release_installation_control(uuid,uuid,bigint) TO offerloom_runtime;
INSERT INTO schema_migrations(version,source_sha256) VALUES(16,repeat('0',64));
