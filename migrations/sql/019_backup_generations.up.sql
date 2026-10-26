-- SPDX-License-Identifier: MIT OR Apache-2.0
SET search_path=offerloom,pg_catalog;

-- A created ciphertext is not a restore drill. Only the offline migration role
-- may record the latter; ordinary API/worker credentials cannot forge it.
CREATE TABLE installation_backups (
 id uuid PRIMARY KEY REFERENCES installation_controls(id),
 owner_id uuid NOT NULL REFERENCES owners(id),
 namespace_id uuid NOT NULL,
 maintenance_epoch bigint NOT NULL CHECK(maintenance_epoch>0),
 workspace_version bigint NOT NULL CHECK(workspace_version>0),
 installation_auth_epoch bigint NOT NULL CHECK(installation_auth_epoch>0),
 owner_auth_epoch bigint NOT NULL CHECK(owner_auth_epoch>0),
 schema_versions jsonb NOT NULL CHECK(jsonb_typeof(schema_versions)='object'),
 image_sha256 text NOT NULL CHECK(image_sha256 ~ '^[0-9a-f]{64}$'),
 source_sha256 text NOT NULL CHECK(source_sha256 ~ '^[0-9a-f]{64}$'),
 config_sha256 text NOT NULL CHECK(config_sha256 ~ '^[0-9a-f]{64}$'),
 keyring_sha256 text NOT NULL CHECK(keyring_sha256 ~ '^[0-9a-f]{64}$'),
 recipient_sha256 text NOT NULL CHECK(recipient_sha256 ~ '^[0-9a-f]{64}$'),
 file_name text NOT NULL UNIQUE CHECK(file_name ~ '^[0-9a-f-]{36}\.cms$'),
 cipher_sha256 text NOT NULL CHECK(cipher_sha256 ~ '^[0-9a-f]{64}$'),
 manifest_sha256 text NOT NULL CHECK(manifest_sha256 ~ '^[0-9a-f]{64}$'),
 byte_length bigint NOT NULL CHECK(byte_length BETWEEN 1 AND 4294967296),
 captured_at timestamptz NOT NULL,
 registered_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 restore_verified_at timestamptz,
 restore_receipt_sha256 text CHECK(restore_receipt_sha256 ~ '^[0-9a-f]{64}$'),
 restore_target_namespace uuid,
 CHECK((restore_verified_at IS NULL AND restore_receipt_sha256 IS NULL AND restore_target_namespace IS NULL)
  OR (restore_verified_at IS NOT NULL AND restore_receipt_sha256 IS NOT NULL
      AND restore_target_namespace IS NOT NULL AND restore_target_namespace<>namespace_id))
);
REVOKE ALL ON installation_backups FROM PUBLIC,offerloom_runtime;
GRANT SELECT ON installation_backups TO offerloom_runtime;

CREATE FUNCTION register_installation_backup(p_id uuid,p_holder uuid,p_fence bigint,p_record jsonb)
 RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,offerloom AS $$
DECLARE c offerloom.installation_controls%ROWTYPE;
BEGIN
 SELECT * INTO STRICT c FROM offerloom.assert_installation_control(p_id,p_holder,p_fence);
 IF c.phase<>'quiescent' OR c.purpose<>'backup'
 OR jsonb_typeof(p_record) IS DISTINCT FROM 'object'
 OR octet_length(p_record::text)>131072
 OR EXISTS(SELECT 1 FROM offerloom.installation_activities)
 OR EXISTS(SELECT 1 FROM offerloom.provider_executions)
 OR (p_record->>'namespace_id')::uuid IS DISTINCT FROM c.namespace_id
 OR (p_record->>'maintenance_epoch')::bigint IS DISTINCT FROM c.maintenance_epoch
 OR (p_record->>'workspace_version')::bigint IS DISTINCT FROM (
  SELECT workspace_version FROM offerloom.owner_settings WHERE owner_id=c.owner_id)
 OR (p_record->>'installation_auth_epoch')::bigint IS DISTINCT FROM (
  SELECT auth_epoch FROM offerloom.installation_state WHERE singleton)
 OR (p_record->>'owner_auth_epoch')::bigint IS DISTINCT FROM (
  SELECT auth_epoch FROM offerloom.owners WHERE id=c.owner_id)
 OR p_record->'schema_versions' IS DISTINCT FROM (
  SELECT jsonb_object_agg(version::text,source_sha256) FROM offerloom.schema_migrations)
 THEN
  RAISE EXCEPTION 'backup generation registration refused' USING ERRCODE='55000';
 END IF;
 INSERT INTO offerloom.installation_backups(
  id,owner_id,namespace_id,maintenance_epoch,workspace_version,installation_auth_epoch,owner_auth_epoch,
  schema_versions,image_sha256,source_sha256,config_sha256,keyring_sha256,recipient_sha256,
  file_name,cipher_sha256,manifest_sha256,byte_length,captured_at
 ) VALUES(
  c.id,c.owner_id,c.namespace_id,c.maintenance_epoch,(p_record->>'workspace_version')::bigint,
  (p_record->>'installation_auth_epoch')::bigint,(p_record->>'owner_auth_epoch')::bigint,
  p_record->'schema_versions',p_record->>'image_sha256',p_record->>'source_sha256',
  p_record->>'config_sha256',p_record->>'keyring_sha256',p_record->>'recipient_sha256',
  p_record->>'file_name',p_record->>'cipher_sha256',p_record->>'manifest_sha256',
  (p_record->>'byte_length')::bigint,(p_record->>'captured_at')::timestamptz
 );
END $$;
REVOKE ALL ON FUNCTION register_installation_backup(uuid,uuid,bigint,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION register_installation_backup(uuid,uuid,bigint,jsonb) TO offerloom_runtime;
INSERT INTO schema_migrations(version,source_sha256) VALUES(19,repeat('0',64));
