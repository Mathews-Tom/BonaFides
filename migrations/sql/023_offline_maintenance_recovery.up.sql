-- SPDX-License-Identifier: MIT OR Apache-2.0
SET search_path=offerloom,pg_catalog;

ALTER TABLE installation_state ADD COLUMN execution_guard_id uuid;
ALTER TABLE installation_activities ADD COLUMN execution_guard_id uuid;
ALTER TABLE provider_executions ADD COLUMN execution_guard_id uuid;
ALTER TABLE installation_controls ADD COLUMN execution_guard_id uuid;

CREATE FUNCTION bind_execution_guard(p_guard uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,offerloom AS $$
DECLARE i offerloom.installation_state%ROWTYPE;
BEGIN
 SELECT * INTO STRICT i FROM offerloom.installation_state WHERE singleton;
 IF p_guard IS NULL OR (i.execution_guard_id IS NOT NULL AND i.execution_guard_id<>p_guard) THEN
  RAISE EXCEPTION 'execution guard identity mismatch' USING ERRCODE='55000';
 END IF;
 IF i.execution_guard_id IS NULL THEN
  UPDATE offerloom.installation_state SET execution_guard_id=p_guard
  WHERE singleton AND (execution_guard_id IS NULL OR execution_guard_id=p_guard);
  IF NOT FOUND THEN
   RAISE EXCEPTION 'execution guard identity mismatch' USING ERRCODE='55000';
  END IF;
 END IF;
END $$;
REVOKE ALL ON FUNCTION bind_execution_guard(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION bind_execution_guard(uuid) TO offerloom_runtime;

-- Keep one authoritative engine. The old, unguarded entrypoint is no longer a runtime capability.
REVOKE ALL ON FUNCTION begin_installation_control(uuid,uuid,uuid,bigint,uuid) FROM offerloom_runtime;
CREATE FUNCTION begin_guarded_installation_control(
 p_owner uuid,p_workflow uuid,p_holder uuid,p_workflow_fence bigint,p_control_holder uuid,p_guard uuid
) RETURNS SETOF installation_controls LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,offerloom AS $$
DECLARE c offerloom.installation_controls%ROWTYPE;
BEGIN
 PERFORM offerloom.bind_execution_guard(p_guard);
 SELECT * INTO STRICT c FROM offerloom.begin_installation_control(
  p_owner,p_workflow,p_holder,p_workflow_fence,p_control_holder);
 UPDATE offerloom.installation_controls SET execution_guard_id=p_guard WHERE id=c.id;
 RETURN QUERY SELECT * FROM offerloom.installation_controls WHERE id=c.id;
END $$;
REVOKE ALL ON FUNCTION begin_guarded_installation_control(uuid,uuid,uuid,bigint,uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION begin_guarded_installation_control(uuid,uuid,uuid,bigint,uuid,uuid) TO offerloom_runtime;

CREATE TABLE maintenance_recoveries (
 id uuid PRIMARY KEY,
 namespace_id uuid NOT NULL,
 maintenance_epoch bigint NOT NULL,
 control_id uuid REFERENCES installation_controls(id) ON DELETE CASCADE,
 previous_holder uuid,
 previous_fence bigint,
 holder uuid NOT NULL,
 control_fence bigint,
 execution_guard_id uuid NOT NULL,
 parser_nonce text NOT NULL CHECK(parser_nonce ~ '^[0-9a-f]{32}$'),
 renderer_nonce text NOT NULL CHECK(renderer_nonce ~ '^[0-9a-f]{32}$'),
 process_proof text NOT NULL CHECK(process_proof='exclusive-kernel-flock/v1'),
 cleared_activities bigint NOT NULL CHECK(cleared_activities>=0),
 started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 deadline_at timestamptz NOT NULL,
 finished_at timestamptz,
 outcome text CHECK(outcome IN ('ABORTED','PURGE_RESUMED','ADMISSION_RECOVERED','SUPERSEDED')),
 CHECK((outcome IS NULL)=(finished_at IS NULL)),
 CHECK((control_id IS NULL)=(previous_holder IS NULL)),
 CHECK((control_id IS NULL)=(previous_fence IS NULL)),
 CHECK((control_id IS NULL)=(control_fence IS NULL)),
 CHECK(parser_nonce<>renderer_nonce),
 CHECK(deadline_at>started_at AND deadline_at<=started_at+interval '15 minutes')
);
REVOKE ALL ON maintenance_recoveries FROM PUBLIC,offerloom_runtime;
GRANT SELECT ON maintenance_recoveries TO offerloom_runtime;
-- Offline-only ownership ledger for precommit import files. Existing unreferenced
-- CAS objects are never adopted; only fresh keys and exact named partial writes.
CREATE TABLE portable_staged_files (
 control_id uuid NOT NULL REFERENCES installation_controls(id) ON DELETE CASCADE,
 storage_key text NOT NULL,
 staging_name text NOT NULL CHECK(staging_name ~ '^\.staging-[0-9a-f]{32}$'),
 sha256 text NOT NULL CHECK(sha256 ~ '^[0-9a-f]{64}$'),
 byte_length bigint NOT NULL CHECK(byte_length>0 AND byte_length<=67108864),
 PRIMARY KEY(control_id,storage_key),
 CHECK(storage_key=left(sha256,2)||'/'||sha256)
);
REVOKE ALL ON portable_staged_files FROM PUBLIC,offerloom_runtime;
GRANT SELECT ON portable_staged_files TO offerloom_runtime;
CREATE FUNCTION register_portable_staging(
 p_id uuid,p_holder uuid,p_fence bigint,p_sha text,p_length bigint,p_staging text
) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,offerloom AS $$
DECLARE c offerloom.installation_controls%ROWTYPE;
BEGIN
 SELECT * INTO STRICT c FROM offerloom.assert_installation_control(p_id,p_holder,p_fence);
 IF c.phase<>'quiescent' OR c.purpose<>'portable_import'
 OR EXISTS(SELECT 1 FROM offerloom.portable_purges WHERE control_id=c.id) THEN
  RAISE EXCEPTION 'precommit staging authority required' USING ERRCODE='55000';
 END IF;
 INSERT INTO offerloom.portable_staged_files(control_id,storage_key,staging_name,sha256,byte_length)
 VALUES(c.id,left(p_sha,2)||'/'||p_sha,p_staging,p_sha,p_length);
END $$;
REVOKE ALL ON FUNCTION register_portable_staging(uuid,uuid,bigint,text,bigint,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION register_portable_staging(uuid,uuid,bigint,text,bigint,text) TO offerloom_runtime;

CREATE FUNCTION inspect_maintenance_recovery(
 p_namespace uuid,p_epoch bigint,p_control uuid,p_holder uuid,p_fence bigint,p_guard uuid
) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,offerloom AS $$
DECLARE i offerloom.installation_state%ROWTYPE; c offerloom.installation_controls%ROWTYPE;
BEGIN
 SELECT * INTO STRICT i FROM offerloom.installation_state WHERE singleton FOR UPDATE;
 SELECT * INTO c FROM offerloom.installation_controls WHERE phase<>'released' FOR UPDATE;
 IF i.namespace_id IS DISTINCT FROM p_namespace OR i.maintenance_epoch IS DISTINCT FROM p_epoch
 OR i.execution_guard_id IS DISTINCT FROM p_guard OR p_guard IS NULL
 OR c.id IS DISTINCT FROM p_control
 OR (c.id IS NOT NULL AND (NOT i.maintenance_active OR c.holder IS DISTINCT FROM p_holder
  OR c.fence IS DISTINCT FROM p_fence OR c.namespace_id<>i.namespace_id
  OR c.maintenance_epoch<>i.maintenance_epoch OR c.execution_guard_id IS DISTINCT FROM p_guard))
 OR (c.id IS NULL AND (p_holder IS NOT NULL OR p_fence IS NOT NULL
  OR (i.maintenance_active AND NOT EXISTS(
   SELECT 1 FROM offerloom.maintenance_recoveries r WHERE r.control_id IS NULL
   AND r.namespace_id=p_namespace AND r.maintenance_epoch=p_epoch
   AND r.execution_guard_id=p_guard AND r.outcome IS NULL)))) THEN
  RAISE EXCEPTION 'offline recovery identity or legacy control refused' USING ERRCODE='55000';
 END IF;
 IF EXISTS(SELECT 1 FROM offerloom.provider_executions) THEN
  RAISE EXCEPTION 'provider execution remains unknown' USING ERRCODE='55000';
 END IF;
 IF EXISTS(SELECT 1 FROM offerloom.installation_activities WHERE
  execution_guard_id IS DISTINCT FROM p_guard OR namespace_id<>p_namespace
  OR maintenance_epoch>p_epoch) THEN
  RAISE EXCEPTION 'legacy or foreign activity cannot be proven stopped' USING ERRCODE='55000';
 END IF;
END $$;
REVOKE ALL ON FUNCTION inspect_maintenance_recovery(uuid,bigint,uuid,uuid,bigint,uuid)
 FROM PUBLIC,offerloom_runtime;

CREATE FUNCTION begin_maintenance_recovery(
 p_recovery uuid,p_namespace uuid,p_epoch bigint,p_control uuid,p_previous_holder uuid,p_previous_fence bigint,
 p_guard uuid,p_parser text,p_renderer text,p_holder uuid
) RETURNS SETOF installation_controls LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,offerloom AS $$
DECLARE c offerloom.installation_controls%ROWTYPE; v_count bigint; v_deadline timestamptz;
BEGIN
 PERFORM offerloom.inspect_maintenance_recovery(p_namespace,p_epoch,p_control,p_previous_holder,p_previous_fence,p_guard);
 IF p_holder IS NULL OR p_holder=p_previous_holder THEN
  RAISE EXCEPTION 'fresh recovery holder required' USING ERRCODE='55000';
 END IF;
 v_deadline:=clock_timestamp()+interval '14 minutes';
 SELECT count(*) INTO v_count FROM offerloom.installation_activities;
 UPDATE offerloom.maintenance_recoveries SET outcome='SUPERSEDED',finished_at=clock_timestamp()
 WHERE namespace_id=p_namespace AND outcome IS NULL;
 IF p_control IS NOT NULL THEN
  UPDATE offerloom.workflow_runs SET state='running',cancel_requested_at=NULL,
   fence=fence+1,lease_holder=p_holder,lease_expires_at=clock_timestamp()+interval '30 seconds',
   deadline_at=v_deadline,finished_at=NULL,version=version+1,updated_at=clock_timestamp()
  WHERE id=p_control;
  UPDATE offerloom.installation_controls SET holder=p_holder,fence=fence+1,
   workflow_fence=(SELECT fence FROM offerloom.workflow_runs WHERE id=p_control),
   phase='quiescent',settled_at=NULL,deadline_at=v_deadline,
   drain_deadline_at=clock_timestamp()+interval '60 seconds',
   lease_expires_at=clock_timestamp()+interval '30 seconds'
  WHERE id=p_control RETURNING * INTO c;
 END IF;
 INSERT INTO offerloom.maintenance_recoveries(id,namespace_id,maintenance_epoch,control_id,
  previous_holder,previous_fence,holder,control_fence,execution_guard_id,parser_nonce,renderer_nonce,
  process_proof,cleared_activities,deadline_at)
 VALUES(p_recovery,p_namespace,p_epoch,p_control,p_previous_holder,p_previous_fence,p_holder,c.fence,
  p_guard,p_parser,p_renderer,'exclusive-kernel-flock/v1',v_count,v_deadline);
 DELETE FROM offerloom.installation_activities;
 UPDATE offerloom.installation_state SET maintenance_active=true WHERE singleton;
 IF p_control IS NOT NULL THEN RETURN NEXT c; END IF;
END $$;
REVOKE ALL ON FUNCTION begin_maintenance_recovery(uuid,uuid,bigint,uuid,uuid,bigint,uuid,text,text,uuid)
 FROM PUBLIC,offerloom_runtime;

CREATE FUNCTION abort_installation_control(p_id uuid,p_holder uuid,p_fence bigint) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,offerloom AS $$
DECLARE c offerloom.installation_controls%ROWTYPE;
BEGIN
 SELECT * INTO STRICT c FROM offerloom.assert_installation_control(p_id,p_holder,p_fence);
 IF c.phase<>'quiescent' OR EXISTS(SELECT 1 FROM offerloom.portable_purges WHERE control_id=c.id)
 OR NOT EXISTS(SELECT 1 FROM offerloom.maintenance_recoveries WHERE control_id=c.id
  AND holder=p_holder AND control_fence=p_fence AND outcome IS NULL AND deadline_at>clock_timestamp()) THEN
  RAISE EXCEPTION 'precommit abort refused' USING ERRCODE='55000';
 END IF;
 UPDATE offerloom.portable_previews SET candidate_bytes=NULL,cleared_at=clock_timestamp(),clear_reason='cancelled'
 WHERE workflow_run_id=c.id AND cleared_at IS NULL;
 UPDATE offerloom.portable_exports SET state='expired' WHERE workflow_run_id=c.id;
 UPDATE offerloom.installation_controls SET phase='settled',settled_at=clock_timestamp(),
  result='{"outcome":"ABORTED","error_code":"maintenance_aborted"}' WHERE id=c.id;
END $$;
REVOKE ALL ON FUNCTION abort_installation_control(uuid,uuid,bigint) FROM PUBLIC,offerloom_runtime;

-- An aborted precommit workspace replacement must not cancel the old workspace.
CREATE FUNCTION release_aborted_installation_control(p_id uuid,p_holder uuid,p_fence bigint) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,offerloom AS $$
DECLARE c offerloom.installation_controls%ROWTYPE;
BEGIN
 SELECT * INTO STRICT c FROM offerloom.assert_installation_control(p_id,p_holder,p_fence);
 IF c.phase<>'settled' OR c.result->>'outcome' IS DISTINCT FROM 'ABORTED'
 OR EXISTS(SELECT 1 FROM offerloom.portable_purges WHERE control_id=c.id)
 OR EXISTS(SELECT 1 FROM offerloom.provider_executions)
 OR EXISTS(SELECT 1 FROM offerloom.installation_activities) THEN
  RAISE EXCEPTION 'aborted control release refused' USING ERRCODE='55000';
 END IF;
 UPDATE offerloom.workflow_runs SET maintenance_epoch=c.maintenance_epoch,fence=fence+1,
  version=version+1,updated_at=clock_timestamp(),
  state=CASE WHEN state='running' THEN 'retryable_failure'::offerloom.workflow_state ELSE state END,
  lease_holder=NULL,lease_expires_at=NULL,next_attempt_at=clock_timestamp()
 WHERE id<>c.id AND state IN ('pending','retryable_failure','running');
 UPDATE offerloom.workflow_runs SET state='permanent_failure',result=c.result,last_error_code='maintenance_aborted',
  finished_at=clock_timestamp(),lease_holder=NULL,lease_expires_at=NULL,fence=fence+1,
  version=version+1,updated_at=clock_timestamp() WHERE id=c.id;
 UPDATE offerloom.installation_controls SET phase='released',released_at=clock_timestamp() WHERE id=c.id;
 UPDATE offerloom.installation_state SET maintenance_active=false WHERE singleton;
 UPDATE offerloom.maintenance_recoveries SET outcome='ABORTED',finished_at=clock_timestamp()
 WHERE control_id=c.id AND holder=p_holder AND control_fence=p_fence AND outcome IS NULL;
END $$;
REVOKE ALL ON FUNCTION release_aborted_installation_control(uuid,uuid,bigint) FROM PUBLIC,offerloom_runtime;

ALTER FUNCTION release_installation_control(uuid,uuid,bigint) RENAME TO release_installation_control_engine;
REVOKE ALL ON FUNCTION release_installation_control_engine(uuid,uuid,bigint) FROM PUBLIC,offerloom_runtime;
CREATE FUNCTION release_installation_control(p_id uuid,p_holder uuid,p_fence bigint) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,offerloom AS $$
DECLARE c offerloom.installation_controls%ROWTYPE;
BEGIN
 SELECT * INTO STRICT c FROM offerloom.assert_installation_control(p_id,p_holder,p_fence);
 IF c.result->>'outcome'='ABORTED' THEN
  RAISE EXCEPTION 'aborted operation is not success' USING ERRCODE='55000';
 END IF;
 PERFORM offerloom.release_installation_control_engine(p_id,p_holder,p_fence);
 UPDATE offerloom.maintenance_recoveries SET outcome='PURGE_RESUMED',finished_at=clock_timestamp()
 WHERE control_id=p_id AND holder=p_holder AND control_fence=p_fence AND outcome IS NULL;
END $$;
REVOKE ALL ON FUNCTION release_installation_control(uuid,uuid,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION release_installation_control(uuid,uuid,bigint) TO offerloom_runtime;

CREATE FUNCTION release_orphan_recovery(p_id uuid,p_namespace uuid,p_epoch bigint,p_guard uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,offerloom AS $$
DECLARE r offerloom.maintenance_recoveries%ROWTYPE; i offerloom.installation_state%ROWTYPE;
BEGIN
 SELECT * INTO STRICT i FROM offerloom.installation_state WHERE singleton FOR UPDATE;
 SELECT * INTO STRICT r FROM offerloom.maintenance_recoveries WHERE id=p_id FOR UPDATE;
 IF r.control_id IS NOT NULL OR r.outcome IS NOT NULL OR r.deadline_at<=clock_timestamp()
 OR r.namespace_id<>p_namespace OR i.namespace_id<>p_namespace OR NOT i.maintenance_active
 OR r.maintenance_epoch<>p_epoch OR i.maintenance_epoch<>p_epoch
 OR r.execution_guard_id<>p_guard OR i.execution_guard_id<>p_guard
 OR EXISTS(SELECT 1 FROM offerloom.installation_controls WHERE phase<>'released')
 OR EXISTS(SELECT 1 FROM offerloom.provider_executions)
 OR EXISTS(SELECT 1 FROM offerloom.installation_activities) THEN
  RAISE EXCEPTION 'orphan recovery fence lost' USING ERRCODE='55000';
 END IF;
 UPDATE offerloom.installation_state SET maintenance_active=false,maintenance_epoch=maintenance_epoch+1 WHERE singleton;
 UPDATE offerloom.workflow_runs SET maintenance_epoch=p_epoch+1,fence=fence+1,
  version=version+1,updated_at=clock_timestamp(),
  state=CASE WHEN state='running' THEN 'retryable_failure'::offerloom.workflow_state ELSE state END,
  lease_holder=NULL,lease_expires_at=NULL,next_attempt_at=clock_timestamp()
 WHERE state IN ('pending','retryable_failure','running');
 UPDATE offerloom.maintenance_recoveries SET outcome='ADMISSION_RECOVERED',finished_at=clock_timestamp() WHERE id=p_id;
END $$;
REVOKE ALL ON FUNCTION release_orphan_recovery(uuid,uuid,bigint,uuid) FROM PUBLIC,offerloom_runtime;
INSERT INTO schema_migrations(version,source_sha256) VALUES(23,repeat('0',64));
