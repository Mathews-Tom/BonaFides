-- SPDX-License-Identifier: MIT OR Apache-2.0
SET search_path=offerloom,pg_catalog;

-- Legacy AAD remains readable only for explicit migration/native recovery.
ALTER TABLE encrypted_credentials
 DROP CONSTRAINT encrypted_credentials_aad_version_check,
 ADD COLUMN provider_origin text,
 ADD CONSTRAINT encrypted_credentials_aad_version_check CHECK(aad_version IN (1,2)),
 ADD CONSTRAINT encrypted_credentials_origin_binding CHECK(
  (aad_version=1 AND provider_origin IS NULL) OR
  (aad_version=2 AND provider_origin IS NOT NULL AND length(provider_origin)<=2048
   AND provider_origin ~ '^https://([a-z0-9][a-z0-9.-]*|\[[0-9a-f:]+\])(:[0-9]{1,5})?$'));

-- Persist conservative write usage across resumable rotations and native rebinding.
-- The operator also rejects aliases of the new key's bytes under another ID.
CREATE TABLE credential_key_usage (
 key_id text PRIMARY KEY,
 encryption_count bigint NOT NULL CHECK(encryption_count BETWEEN 0 AND 1048576)
);
INSERT INTO credential_key_usage(key_id,encryption_count)
 SELECT key_id,count(*) FROM encrypted_credentials GROUP BY key_id;
REVOKE ALL ON credential_key_usage FROM PUBLIC,offerloom_runtime;
GRANT SELECT ON credential_key_usage TO offerloom_runtime;
INSERT INTO schema_migrations(version,source_sha256) VALUES(25,repeat('0',64));
