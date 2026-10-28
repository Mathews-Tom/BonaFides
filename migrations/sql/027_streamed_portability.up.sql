-- SPDX-License-Identifier: MIT OR Apache-2.0
SET search_path=offerloom,pg_catalog;

-- The ceiling covers 10,000 distinct 64 MiB files, a 16 MiB manifest and
-- 4 MiB of bounded ZIP64 structure. It is not an additional owner write quota.
ALTER TABLE portable_exports DROP CONSTRAINT portable_exports_byte_length_check;
ALTER TABLE portable_exports ADD CONSTRAINT portable_exports_byte_length_check
 CHECK(byte_length BETWEEN 1 AND 671109611520);

-- Keep the first real upload chunk in the existing preview row. Additional
-- chunks avoid PostgreSQL's single-bytea limit and preserve atomic cancellation,
-- erasure and native recovery through the existing candidate_bytes=NULL cut.
CREATE TABLE portable_preview_chunks (
 owner_id uuid NOT NULL,
 preview_id uuid NOT NULL,
 chunk_no integer NOT NULL CHECK(chunk_no BETWEEN 1 AND 640019),
 payload bytea NOT NULL CHECK(octet_length(payload) BETWEEN 1 AND 1048576),
 PRIMARY KEY(owner_id,preview_id,chunk_no),
 FOREIGN KEY(owner_id,preview_id) REFERENCES portable_previews(owner_id,id) ON DELETE CASCADE
);
REVOKE ALL ON portable_preview_chunks FROM PUBLIC,offerloom_runtime;
GRANT SELECT,INSERT ON portable_preview_chunks TO offerloom_runtime;

-- Preserve any already-approved v1 candidate exactly while splitting its bytes.
INSERT INTO portable_preview_chunks(owner_id,preview_id,chunk_no,payload)
 SELECT p.owner_id,p.id,n,substring(p.candidate_bytes FROM n*1048576+1 FOR 1048576)
 FROM portable_previews p CROSS JOIN LATERAL
 generate_series(1,(octet_length(p.candidate_bytes)-1)/1048576) AS n
 WHERE p.candidate_bytes IS NOT NULL;
UPDATE portable_previews SET candidate_bytes=substring(candidate_bytes FROM 1 FOR 1048576)
 WHERE octet_length(candidate_bytes)>1048576;
ALTER TABLE portable_previews DROP CONSTRAINT portable_previews_candidate_bytes_check;
ALTER TABLE portable_previews ADD CONSTRAINT portable_previews_candidate_bytes_check
 CHECK(octet_length(candidate_bytes) BETWEEN 1 AND 1048576);

-- Replacement review starts only after every candidate chunk is retained.
-- Existing previews and deletion plans are already finalized.
ALTER TABLE portable_previews ADD COLUMN retained_at timestamptz DEFAULT clock_timestamp();

CREATE FUNCTION finalize_portable_preview(
 p_owner uuid,p_id uuid,p_plan jsonb,p_sha text,p_expiry timestamptz
) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,offerloom AS $$
DECLARE p offerloom.portable_previews%ROWTYPE; ready_at timestamptz;
 candidate_length bigint; chunk_count bigint; expected bigint;
BEGIN
 SELECT * INTO STRICT p FROM offerloom.portable_previews
 WHERE owner_id=p_owner AND id=p_id FOR UPDATE;
 ready_at=clock_timestamp();
 expected=(p.plan->>'byte_length')::bigint;
 SELECT COALESCE(sum(octet_length(payload)),0)+octet_length(p.candidate_bytes),count(*)
 INTO candidate_length,chunk_count FROM offerloom.portable_preview_chunks
 WHERE owner_id=p_owner AND preview_id=p_id;
 IF p.action_kind<>'replace_workspace' OR p.cleared_at IS NOT NULL
 OR p.candidate_bytes IS NULL OR p.retained_at IS NOT NULL
 OR (p_plan-'expires_at') IS DISTINCT FROM (p.plan-'expires_at')
 OR p_expiry IS NULL OR (p_plan->>'expires_at')::timestamptz IS DISTINCT FROM p_expiry
 OR p_expiry<=ready_at OR p_expiry>ready_at+interval '10 minutes'
 OR p_sha IS NULL OR p_sha!~'^[0-9a-f]{64}$'
 OR candidate_length IS DISTINCT FROM expected OR chunk_count<>(expected-1)/1048576
 OR NOT EXISTS(SELECT 1 FROM offerloom.workflow_runs
  WHERE owner_id=p_owner AND id=p.workflow_run_id AND state='pending')
 OR EXISTS(SELECT 1 FROM offerloom.action_approvals
  WHERE owner_id=p_owner AND workflow_run_id=p.workflow_run_id) THEN
  RAISE EXCEPTION 'portable preview finalization refused' USING ERRCODE='23514';
 END IF;
 UPDATE offerloom.portable_previews SET plan=p_plan,preview_sha256=p_sha,
  created_at=ready_at,expires_at=p_expiry,retained_at=ready_at
 WHERE owner_id=p_owner AND id=p_id;
END $$;
REVOKE ALL ON FUNCTION finalize_portable_preview(uuid,uuid,jsonb,text,timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION finalize_portable_preview(uuid,uuid,jsonb,text,timestamptz)
 TO offerloom_runtime;

CREATE FUNCTION guard_portable_preview_chunk() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,offerloom AS $$
DECLARE p offerloom.portable_previews%ROWTYPE; next_chunk integer; expected bigint;
BEGIN
 SELECT * INTO STRICT p FROM offerloom.portable_previews
 WHERE owner_id=NEW.owner_id AND id=NEW.preview_id FOR UPDATE;
 expected=(p.plan->>'byte_length')::bigint;
 SELECT COALESCE(max(chunk_no),0)+1 INTO next_chunk FROM offerloom.portable_preview_chunks
 WHERE owner_id=NEW.owner_id AND preview_id=NEW.preview_id;
 IF p.cleared_at IS NOT NULL OR p.candidate_bytes IS NULL
 OR octet_length(p.candidate_bytes)<>1048576 OR expected NOT BETWEEN 1 AND 671109611520
 OR NEW.chunk_no<>next_chunk
 OR NEW.chunk_no::bigint*1048576+octet_length(NEW.payload)>expected
 OR octet_length(NEW.payload)<>LEAST(1048576,expected-NEW.chunk_no::bigint*1048576) THEN
  RAISE EXCEPTION 'portable candidate chunk refused' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION guard_portable_preview_chunk() FROM PUBLIC,offerloom_runtime;
CREATE TRIGGER portable_preview_chunk_guard BEFORE INSERT ON portable_preview_chunks
 FOR EACH ROW EXECUTE FUNCTION guard_portable_preview_chunk();

CREATE FUNCTION clear_portable_preview_chunks() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,offerloom AS $$
BEGIN
 DELETE FROM offerloom.portable_preview_chunks WHERE owner_id=NEW.owner_id AND preview_id=NEW.id;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION clear_portable_preview_chunks() FROM PUBLIC,offerloom_runtime;
CREATE TRIGGER portable_preview_chunks_clear AFTER UPDATE OF candidate_bytes ON portable_previews
 FOR EACH ROW WHEN (NEW.candidate_bytes IS NULL) EXECUTE FUNCTION clear_portable_preview_chunks();

INSERT INTO schema_migrations(version,source_sha256) VALUES(27,repeat('0',64));
