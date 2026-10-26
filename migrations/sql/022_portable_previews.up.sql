-- SPDX-License-Identifier: MIT OR Apache-2.0
SET search_path=offerloom,pg_catalog;

-- One live preview per owner bounds retained uploads independently of HTTP limits.
-- Candidate bytes are operational state, not portable owner-export records.
CREATE TABLE portable_previews (
 owner_id uuid NOT NULL REFERENCES owners(id) ON DELETE CASCADE,
 id uuid NOT NULL DEFAULT gen_random_uuid(),
 workflow_run_id uuid NOT NULL,
 action_kind text NOT NULL CHECK(action_kind IN ('replace_workspace','privacy_delete')),
 plan jsonb NOT NULL CHECK(jsonb_typeof(plan)='object' AND octet_length(plan::text)<=262144),
 preview_sha256 text NOT NULL CHECK(preview_sha256 ~ '^[0-9a-f]{64}$'),
 candidate_bytes bytea CHECK(octet_length(candidate_bytes)<=134217728),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 expires_at timestamptz NOT NULL,
 cleared_at timestamptz,
 clear_reason text CHECK(clear_reason IN ('expired','cancelled','erased','installation_restored')),
 PRIMARY KEY(owner_id,id),
 UNIQUE(owner_id,workflow_run_id),
 FOREIGN KEY(owner_id,workflow_run_id) REFERENCES workflow_runs(owner_id,id) ON DELETE CASCADE,
 CHECK(expires_at>created_at AND expires_at<=created_at+interval '10 minutes'),
 CHECK((cleared_at IS NULL)=(clear_reason IS NULL)),
 CHECK((action_kind='replace_workspace' AND cleared_at IS NULL)=(candidate_bytes IS NOT NULL))
);
CREATE UNIQUE INDEX portable_previews_one_live_owner ON portable_previews(owner_id)
 WHERE cleared_at IS NULL;
REVOKE ALL ON portable_previews FROM PUBLIC,offerloom_runtime;
GRANT SELECT,INSERT ON portable_previews TO offerloom_runtime;
GRANT UPDATE(candidate_bytes,cleared_at,clear_reason) ON portable_previews TO offerloom_runtime;

-- Every candidate contains the whole workspace. Even application-only erasure
-- must remove all retained candidates in the SAME transaction as domain erasure.
-- Only the existing privileged erasure function can create this journal row.
CREATE FUNCTION clear_erased_portable_previews() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,offerloom AS $$
BEGIN
 UPDATE offerloom.portable_previews SET candidate_bytes=NULL,
  cleared_at=clock_timestamp(),clear_reason='erased'
 WHERE owner_id=NEW.owner_id AND cleared_at IS NULL;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION clear_erased_portable_previews() FROM PUBLIC,offerloom_runtime;
CREATE TRIGGER portable_erasure_clears_previews AFTER INSERT ON portable_purges
 FOR EACH ROW EXECUTE FUNCTION clear_erased_portable_previews();
INSERT INTO schema_migrations(version,source_sha256) VALUES(22,repeat('0',64));
