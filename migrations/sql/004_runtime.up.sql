-- SPDX-License-Identifier: MIT OR Apache-2.0
-- Additive M0 security15/session and workflow14 observability requirements.
SET LOCAL search_path=offerloom,pg_catalog;
ALTER TABLE installation_state ADD COLUMN bootstrap_expires_at timestamptz;
ALTER TABLE installation_state ADD COLUMN auth_epoch bigint NOT NULL DEFAULT 1 CHECK(auth_epoch>0);
ALTER TABLE owners ADD COLUMN auth_epoch bigint NOT NULL DEFAULT 1 CHECK(auth_epoch>0);
ALTER TABLE sessions ADD COLUMN auth_epoch bigint NOT NULL DEFAULT 1 CHECK(auth_epoch>0);
ALTER TABLE sessions ADD COLUMN revoked_at timestamptz;
ALTER TABLE sessions ADD COLUMN authenticated_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE sessions ADD COLUMN csrf_token text NOT NULL DEFAULT '';
CREATE TABLE auth_challenges (
 token_sha256 text PRIMARY KEY CHECK(token_sha256 ~ '^[0-9a-f]{64}$'),
 csrf_sha256 text NOT NULL CHECK(csrf_sha256 ~ '^[0-9a-f]{64}$'), expires_at timestamptz NOT NULL
);
CREATE TABLE auth_throttles (
 scope text PRIMARY KEY, attempts integer NOT NULL DEFAULT 0 CHECK(attempts>=0),
 window_started_at timestamptz NOT NULL DEFAULT now(), blocked_until timestamptz
);
CREATE TABLE worker_heartbeats (
 id uuid PRIMARY KEY, started_at timestamptz NOT NULL DEFAULT now(),
 heartbeat_at timestamptz NOT NULL DEFAULT now(), concurrency integer NOT NULL CHECK(concurrency BETWEEN 1 AND 8)
);
ALTER TABLE workflow_runs ADD COLUMN error_fingerprint text;
ALTER TABLE workflow_runs ADD COLUMN repeated_error_count integer NOT NULL DEFAULT 0 CHECK(repeated_error_count>=0);
CREATE TABLE event_metadata (
 owner_id uuid NOT NULL REFERENCES owners(id) ON DELETE CASCADE,
 event_id uuid NOT NULL, event_type text NOT NULL, aggregate_id uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(owner_id,event_id),
 FOREIGN KEY(owner_id,event_id) REFERENCES outbox_events(owner_id,id)
);
REVOKE ALL ON SCHEMA offerloom FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA offerloom FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA offerloom FROM PUBLIC;
GRANT USAGE ON SCHEMA offerloom TO offerloom_runtime;
GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA offerloom TO offerloom_runtime;
REVOKE INSERT,UPDATE,DELETE ON schema_migrations FROM offerloom_runtime;
REVOKE UPDATE,DELETE ON audit_events FROM offerloom_runtime;
GRANT EXECUTE ON FUNCTION assert_workflow_fence(uuid,uuid,uuid,bigint) TO offerloom_runtime;
INSERT INTO schema_migrations(version) VALUES(4);
