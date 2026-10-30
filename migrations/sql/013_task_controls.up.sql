-- SPDX-License-Identifier: MIT OR Apache-2.0
SET search_path=bonafides,pg_catalog;

-- A lease start is not a heartbeat; old runs have no invented observation.
ALTER TABLE workflow_runs ADD COLUMN last_heartbeat_at timestamptz;
CREATE TABLE workflow_attempt_events (
 owner_id uuid NOT NULL,
 id uuid NOT NULL DEFAULT gen_random_uuid(),
 workflow_run_id uuid NOT NULL,
 attempt integer NOT NULL CHECK(attempt>=0),
 fence bigint NOT NULL CHECK(fence>=0),
 event text NOT NULL CHECK(event IN (
  'claimed','retry_requested','retryable_failure','permanent_failure','cancelled',
  'succeeded','interrupted','waiting_for_approval','approved','approval_expired'
 )),
 error_code text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(owner_id,id),
 FOREIGN KEY(owner_id,workflow_run_id) REFERENCES workflow_runs(owner_id,id) ON DELETE CASCADE
);
CREATE INDEX workflow_attempt_history_idx
 ON workflow_attempt_events(owner_id,workflow_run_id,created_at DESC,id DESC);
CREATE TRIGGER immutable_update BEFORE UPDATE ON workflow_attempt_events
 FOR EACH ROW EXECUTE FUNCTION reject_revision_update();
REVOKE ALL ON workflow_attempt_events FROM PUBLIC,bonafides_runtime;
GRANT SELECT,INSERT ON workflow_attempt_events TO bonafides_runtime;

-- Legacy unbound rows retain their historical bytes but cannot authorize execution.
ALTER TABLE action_approvals DROP CONSTRAINT action_approvals_action_kind_check;
ALTER TABLE action_approvals ADD CONSTRAINT action_approvals_action_kind_check
 CHECK(action_kind IN ('send_mail','calendar_write','hosted_egress','replace_workspace','privacy_delete'));
ALTER TABLE action_approvals ADD COLUMN intent jsonb CHECK(intent IS NULL OR jsonb_typeof(intent)='object');
ALTER TABLE action_approvals ADD COLUMN destination_sha256 text
 CHECK(destination_sha256 IS NULL OR destination_sha256 ~ '^[0-9a-f]{64}$');
ALTER TABLE action_approvals ADD COLUMN target_version bigint CHECK(target_version>0);
ALTER TABLE action_approvals ADD COLUMN approved_session_id uuid;
ALTER TABLE action_approvals ADD COLUMN approved_auth_epoch bigint CHECK(approved_auth_epoch>0);
ALTER TABLE action_approvals ADD COLUMN revoked_at timestamptz;
ALTER TABLE action_approvals ADD CONSTRAINT action_approvals_exact_binding_check
 CHECK(intent IS NULL OR (destination_sha256 IS NOT NULL AND target_version IS NOT NULL));
ALTER TABLE action_approvals ADD CONSTRAINT action_approvals_approval_session_check
 CHECK((approved_session_id IS NULL)=(approved_auth_epoch IS NULL));
ALTER TABLE action_approvals ADD CONSTRAINT action_approvals_exact_session_check
 CHECK(intent IS NULL OR approved_at IS NULL OR approved_session_id IS NOT NULL);
CREATE UNIQUE INDEX action_approvals_exact_workflow_idx
 ON action_approvals(owner_id,workflow_run_id) WHERE intent IS NOT NULL;
ALTER TABLE action_approvals ADD CONSTRAINT action_approvals_workflow_identity_unique
 UNIQUE(owner_id,id,workflow_run_id);

CREATE FUNCTION protect_action_approval_binding() RETURNS trigger
 LANGUAGE plpgsql SET search_path=bonafides,pg_catalog AS $$
BEGIN
 IF ROW(NEW.owner_id,NEW.id,NEW.workflow_run_id,NEW.action_kind,NEW.payload_sha256,
        NEW.recipient_sha256,NEW.document_revision_id,NEW.expires_at,NEW.created_at,
        NEW.intent,NEW.destination_sha256,NEW.target_version)
  IS DISTINCT FROM
    ROW(OLD.owner_id,OLD.id,OLD.workflow_run_id,OLD.action_kind,OLD.payload_sha256,
        OLD.recipient_sha256,OLD.document_revision_id,OLD.expires_at,OLD.created_at,
        OLD.intent,OLD.destination_sha256,OLD.target_version)
 THEN
  RAISE EXCEPTION 'immutable action intent' USING ERRCODE='55000';
 END IF;
 IF OLD.approved_at IS NOT NULL AND
    ROW(NEW.approved_at,NEW.approved_session_id,NEW.approved_auth_epoch)
     IS DISTINCT FROM ROW(OLD.approved_at,OLD.approved_session_id,OLD.approved_auth_epoch)
 THEN
  RAISE EXCEPTION 'immutable action approval' USING ERRCODE='55000';
 END IF;
 IF (OLD.consumed_at IS NOT NULL AND NEW.consumed_at IS DISTINCT FROM OLD.consumed_at)
 OR (OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at)
 THEN
  RAISE EXCEPTION 'settled action cannot be revived' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER immutable_action_binding BEFORE UPDATE ON action_approvals
 FOR EACH ROW EXECUTE FUNCTION protect_action_approval_binding();
REVOKE ALL ON FUNCTION protect_action_approval_binding() FROM PUBLIC;
REVOKE UPDATE,DELETE ON action_approvals FROM bonafides_runtime;
GRANT UPDATE(approved_at,consumed_at,revoked_at,approved_session_id,approved_auth_epoch,version)
 ON action_approvals TO bonafides_runtime;

CREATE TABLE approval_audit_events (
 owner_id uuid NOT NULL,
 id uuid NOT NULL DEFAULT gen_random_uuid(),
 approval_id uuid NOT NULL,
 workflow_run_id uuid NOT NULL,
 event text NOT NULL CHECK(event IN ('prepared','approved','denied','consumed','revoked','expired')),
 error_code text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(owner_id,id),
 FOREIGN KEY(owner_id,approval_id,workflow_run_id)
  REFERENCES action_approvals(owner_id,id,workflow_run_id) ON DELETE CASCADE,
 FOREIGN KEY(owner_id,workflow_run_id) REFERENCES workflow_runs(owner_id,id) ON DELETE CASCADE
);
CREATE INDEX approval_audit_history_idx
 ON approval_audit_events(owner_id,workflow_run_id,created_at DESC,id DESC);
CREATE TRIGGER immutable_update BEFORE UPDATE ON approval_audit_events
 FOR EACH ROW EXECUTE FUNCTION reject_revision_update();
REVOKE ALL ON approval_audit_events FROM PUBLIC,bonafides_runtime;
GRANT SELECT,INSERT ON approval_audit_events TO bonafides_runtime;

ALTER TABLE hosted_egress_consents ADD COLUMN approval_id uuid;
ALTER TABLE hosted_egress_consents ADD COLUMN transmission_count integer NOT NULL DEFAULT 0
 CHECK(transmission_count BETWEEN 0 AND 2);
ALTER TABLE hosted_egress_consents ADD CONSTRAINT hosted_consent_approval_binding
 FOREIGN KEY(owner_id,approval_id,workflow_run_id)
 REFERENCES action_approvals(owner_id,id,workflow_run_id)
 ON DELETE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;
CREATE FUNCTION protect_hosted_consent_binding() RETURNS trigger
 LANGUAGE plpgsql SET search_path=bonafides,pg_catalog AS $$
BEGIN
 IF ROW(NEW.owner_id,NEW.id,NEW.workflow_run_id,NEW.provider_key,NEW.model_id,NEW.purpose,
        NEW.input_scope_sha256,NEW.settings_version,NEW.acknowledged_at,NEW.expires_at,
        NEW.created_at,NEW.approval_id)
  IS DISTINCT FROM
    ROW(OLD.owner_id,OLD.id,OLD.workflow_run_id,OLD.provider_key,OLD.model_id,OLD.purpose,
        OLD.input_scope_sha256,OLD.settings_version,OLD.acknowledged_at,OLD.expires_at,
        OLD.created_at,OLD.approval_id)
 THEN
  RAISE EXCEPTION 'immutable hosted consent' USING ERRCODE='55000';
 END IF;
 IF OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at
 THEN
  RAISE EXCEPTION 'revoked hosted consent cannot be revived' USING ERRCODE='55000';
 END IF;
 IF NEW.transmission_count NOT IN (OLD.transmission_count,OLD.transmission_count+1)
 THEN
  RAISE EXCEPTION 'hosted transmission count must advance one attempt' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER immutable_hosted_binding BEFORE UPDATE ON hosted_egress_consents
 FOR EACH ROW EXECUTE FUNCTION protect_hosted_consent_binding();
REVOKE ALL ON FUNCTION protect_hosted_consent_binding() FROM PUBLIC;
REVOKE UPDATE,DELETE ON hosted_egress_consents FROM bonafides_runtime;
GRANT UPDATE(revoked_at,transmission_count) ON hosted_egress_consents TO bonafides_runtime;
INSERT INTO schema_migrations(version,source_sha256) VALUES(13,repeat('0',64));
