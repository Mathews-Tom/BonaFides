-- SPDX-License-Identifier: MIT OR Apache-2.0
SET search_path=bonafides,pg_catalog;

CREATE TABLE portable_exports (
 owner_id uuid NOT NULL,
 workflow_run_id uuid NOT NULL,
 namespace_id uuid NOT NULL,
 maintenance_epoch bigint NOT NULL CHECK(maintenance_epoch>0),
 archive_sha256 text NOT NULL CHECK(archive_sha256 ~ '^[0-9a-f]{64}$'),
 manifest_sha256 text NOT NULL CHECK(manifest_sha256 ~ '^[0-9a-f]{64}$'),
 byte_length bigint NOT NULL CHECK(byte_length BETWEEN 1 AND 134217728),
 state text NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','ready','expired')),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 expires_at timestamptz NOT NULL DEFAULT clock_timestamp()+interval '24 hours',
 PRIMARY KEY(owner_id,workflow_run_id),
 FOREIGN KEY(owner_id,workflow_run_id) REFERENCES workflow_runs(owner_id,id) ON DELETE CASCADE,
 CHECK(expires_at>created_at)
);
CREATE INDEX portable_export_expiry_idx ON portable_exports(expires_at,workflow_run_id)
 WHERE state<>'expired';
REVOKE ALL ON portable_exports FROM PUBLIC,bonafides_runtime;
GRANT SELECT,INSERT ON portable_exports TO bonafides_runtime;
GRANT UPDATE(state) ON portable_exports TO bonafides_runtime;

-- Only a positively quiescent, unchanged read-only export may release a failed
-- operation. Destructive failures remain closed for independent recovery.
CREATE FUNCTION fail_portable_export_control(p_id uuid,p_holder uuid,p_fence bigint)
 RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,bonafides AS $$
DECLARE c bonafides.installation_controls%ROWTYPE;
BEGIN
 SELECT * INTO STRICT c FROM bonafides.assert_installation_control(p_id,p_holder,p_fence);
 IF c.phase<>'settled' OR c.purpose<>'portable_export'
 OR c.result IS DISTINCT FROM '{"error_code":"portable_export_failed"}'::jsonb
 OR EXISTS(SELECT 1 FROM bonafides.installation_activities)
 OR EXISTS(SELECT 1 FROM bonafides.provider_executions)
 OR EXISTS(SELECT 1 FROM bonafides.portable_exports e
  WHERE e.owner_id=c.owner_id AND e.workflow_run_id=c.id AND e.state<>'expired') THEN
  RAISE EXCEPTION 'export failure settlement refused' USING ERRCODE='55000';
 END IF;
 UPDATE bonafides.workflow_runs SET maintenance_epoch=c.maintenance_epoch,fence=fence+1,
  version=version+1,updated_at=clock_timestamp()
 WHERE owner_id=c.owner_id AND id<>c.id AND state IN ('pending','retryable_failure');
 UPDATE bonafides.workflow_runs SET state='retryable_failure',maintenance_epoch=c.maintenance_epoch,
  lease_holder=NULL,lease_expires_at=NULL,fence=fence+1,version=version+1,
  next_attempt_at=clock_timestamp(),updated_at=clock_timestamp(),last_error_code='worker_interrupted'
 WHERE owner_id=c.owner_id AND id<>c.id AND state='running';
 UPDATE bonafides.workflow_runs SET state='permanent_failure',result=c.result,
  finished_at=clock_timestamp(),lease_holder=NULL,lease_expires_at=NULL,fence=fence+1,
  version=version+1,updated_at=clock_timestamp(),last_error_code='portable_export_failed'
 WHERE owner_id=c.owner_id AND id=c.id;
 INSERT INTO bonafides.workflow_attempt_events(owner_id,workflow_run_id,attempt,fence,event,error_code)
 SELECT owner_id,id,attempt,fence,'permanent_failure','portable_export_failed'
 FROM bonafides.workflow_runs WHERE owner_id=c.owner_id AND id=c.id;
 UPDATE bonafides.installation_controls SET phase='released',released_at=clock_timestamp() WHERE id=c.id;
 UPDATE bonafides.installation_state SET maintenance_active=false WHERE singleton;
END $$;
REVOKE ALL ON FUNCTION fail_portable_export_control(uuid,uuid,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION fail_portable_export_control(uuid,uuid,bigint) TO bonafides_runtime;
INSERT INTO schema_migrations(version,source_sha256) VALUES(17,repeat('0',64));
