# SPDX-License-Identifier: MIT OR Apache-2.0
"""Real Linux/OpenSSL archive boundaries; native PG restoration has its own drill."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

_PROGRAM = r"""
import hashlib,io,json,os,subprocess,sys,tarfile,tempfile
from pathlib import Path
from datetime import UTC,datetime,timedelta
from uuid import uuid4
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from bonafides.backup_archive import BackupFile,BackupManifest,BackupError,create_bundle,recover_bundle
case=sys.argv[1]
with tempfile.TemporaryDirectory() as temporary:
 root=Path(temporary)
 key=rsa.generate_private_key(public_exponent=65537,key_size=3072)
 now=datetime.now(UTC)
 subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'BonaFides archive regression')])
 cert=(x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
       .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1))
       .not_valid_after(now+timedelta(days=1)).sign(key,hashes.SHA256()))
 recipient=root/'recipient.pem';recipient.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
 private=root/'private.pem';private.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()));private.chmod(0o600)
 blobs=root/'source';blobs.mkdir(mode=0o700)
 payload=b'Synthetic exact immutable archive bytes.\n'*8192
 digest=hashlib.sha256(payload).hexdigest()
 owner=uuid4();relative=f'{owner}/{digest[:2]}/{digest}'
 stored=blobs/relative;stored.parent.mkdir(parents=True);stored.write_bytes(payload)
 # Structured transport fixture only: native PostgreSQL recovery is a separate drill.
 evidence_id,revision_id=str(uuid4()),str(uuid4())
 snapshot={
  'evidence_records':[{'owner_id':str(owner),'id':evidence_id,'version':1,
   'current_revision_id':revision_id,'archived_at':None,'created_at':now.isoformat(),
   'privacy_class':'confidential','employer_owned':True}],
  'evidence_revisions':[{'owner_id':str(owner),'id':revision_id,'evidence_id':evidence_id,
   'revision_no':1,'payload':{'text':'Synthetic source','source':'Owner supplied'},
   'blob_id':str(uuid4()),'content_sha256':digest,'created_at':now.isoformat(),
   'source_type':'work_sample','origin':'owner_entry'}],
  'evidence_classification_changes':[
   {'owner_id':str(owner),'evidence_id':evidence_id,'sequence':sequence,
    'privacy_class':privacy,'employer_owned':owned,'changed_at':(now+timedelta(seconds=sequence)).isoformat()}
   for sequence,privacy,owned in ((1,'public',False),(2,'confidential',True))]}
 dump=json.dumps(snapshot,sort_keys=True,separators=(',',':')).encode()
 counts={name:len(rows) for name,rows in snapshot.items()}
 fingerprints={name:hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest()
  for name,rows in snapshot.items()}
 schema={str(n):hashlib.sha256(f'synthetic schema {n}'.encode()).hexdigest() for n in range(1,30)}
 namespace=uuid4()
 def factory(sha,size,fingerprint,files):
  return BackupManifest(format='bonafides-installation',format_version=1,created_at=now,
   source_namespace=namespace,source_owner_id=owner,control_id=uuid4(),installation_auth_epoch=1,
   owner_auth_epoch=1,maintenance_epoch=1,workspace_version=1,image_sha256='2'*64,
   source_sha256='3'*64,config_sha256='4'*64,recipient_sha256=fingerprint,
   credential_keys={'synthetic-key':'5'*64},credential_active_key_id='synthetic-key',
   postgres_version='18.6',dump_tool_version='opaque transport fixture, not native pg_dump',
   schema_versions=schema,table_counts=counts,table_sha256=fingerprints,
   dump_sha256=sha,dump_byte_length=size,files=files)
 def create():
  return create_bundle(dump=io.BytesIO(dump),blob_root=blobs,
   blob_files=[BackupFile(path='blobs/'+relative,sha256=digest,byte_length=len(payload))],
   recipient=recipient,staging_root=root/'online',destination=root/'encrypted',manifest_factory=factory)
 receipt=create()
 original=root/'encrypted'/receipt.file_name
 encoded=original.read_bytes()
 assert hashlib.sha256(encoded).hexdigest()==receipt.sha256
 assert payload[:256] not in encoded and dump[:256] not in encoded
 assert not list((root/'online').iterdir())
 arguments=dict(source=original,trusted_sha256=receipt.sha256,private_key=private,
  staging_root=root/'offline',expected_schema=schema,expected_image_sha256='2'*64)
 if case=='roundtrip':
  with recover_bundle(**arguments) as restored:
   assert restored.dump.read_bytes()==dump
   assert (restored.blobs/relative).read_bytes()==payload
   assert restored.manifest.source_namespace==namespace
   assert restored.manifest.source_owner_id==owner
   assert restored.manifest.schema_versions==schema
   assert json.loads(restored.dump.read_bytes())==snapshot
   assert restored.manifest.table_counts==counts
   assert restored.manifest.table_sha256==fingerprints
   assert len(json.loads(restored.dump.read_bytes())['evidence_classification_changes'])==2
  assert not list((root/'offline').iterdir())
 elif case in ('changed_source','symlink_source','unwritable_destination'):
  if case=='changed_source':
   stored.write_bytes(bytes([payload[0]^1])+payload[1:])
   expected=BackupError
  elif case=='symlink_source':
   stored.unlink();stored.symlink_to(private)
   expected=OSError
  else:
   (root/'encrypted').chmod(0o500)
   expected=PermissionError
  try:
   create()
   raise AssertionError('unsafe new generation published')
  except expected as error:
   if case=='changed_source': assert str(error)=='backup_blob_changed'
  finally:
   (root/'encrypted').chmod(0o700)
  assert original.read_bytes()==encoded
  assert [p.name for p in (root/'encrypted').iterdir()]==[receipt.file_name]
  assert not list((root/'online').iterdir())
 else:
  expected='backup_ciphertext_hash_mismatch'
  if case=='wrong_digest':
   arguments['trusted_sha256']='0'*64
  elif case=='wrong_private_key':
   wrong=root/'wrong.pem'
   wrong.write_bytes(rsa.generate_private_key(public_exponent=65537,key_size=3072).private_bytes(
    serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
   wrong.chmod(0o600);arguments['private_key']=wrong;expected='backup_crypto_failed'
  elif case=='incompatible_schema':
   arguments['expected_schema']={**schema,'30':'7'*64};expected='incompatible_backup_schema'
  elif case=='incompatible_image':
   arguments['expected_image_sha256']='0'*64;expected='incompatible_backup_image'
  elif case in ('tampered_ciphertext','truncated_ciphertext'):
   altered=root/'altered.cms'
   changed=bytearray(encoded)
   if case=='tampered_ciphertext': changed[len(changed)//2]^=1
   else: changed=changed[:len(changed)//2]
   altered.write_bytes(changed)
   arguments['source']=altered
   # Recompute the transport hash so GCM, not just a stale digest, must reject it.
   arguments['trusted_sha256']=hashlib.sha256(changed).hexdigest()
   expected='backup_crypto_failed'
  else:
   plain=root/'authenticated.tar'
   subprocess.run(['openssl','cms','-decrypt','-binary','-inform','DER','-in',str(original),
    '-inkey',str(private),'-out',str(plain)],capture_output=True,check=True,timeout=10)
   members=[]
   with tarfile.open(plain,'r:') as archive:
    for member in archive:
     stream=archive.extractfile(member);assert stream is not None
     members.append((member.name,stream.read(),tarfile.REGTYPE))
   manifest=json.loads(members[0][1])
   if case=='corrupt_blob':
    name,data,kind=members[-1]
    members[-1]=(name,bytes([data[0]^1])+data[1:],kind)
    expected='backup_member_hash_mismatch'
   elif case=='missing_blob':
    members.pop();expected='backup_member_set_mismatch'
   elif case=='traversal':
    manifest['files'][-1]['path']='blobs/../outside'
    members[-1]=('blobs/../outside',members[-1][1],tarfile.REGTYPE)
    expected='invalid_backup_member'
   elif case=='symlink_member':
    members[-1]=(members[-1][0],b'',tarfile.SYMTYPE)
    expected='backup_member_set_mismatch'
   elif case=='duplicate_member':
    members.append(members[-1]);expected='unexpected_backup_member'
   elif case=='corrupt_inner_ciphertext':
    inner=bytearray(members[1][1]);inner[len(inner)//2]^=1
    members[1]=('database.cms',bytes(inner),tarfile.REGTYPE)
    manifest['files'][0]['sha256']=hashlib.sha256(inner).hexdigest()
    expected='backup_crypto_failed'
   elif case=='trailing_content':
    expected='invalid_backup_trailer'
   elif case=='unauthenticated_envelope':
    expected='backup_crypto_format_forbidden'
   else:
    raise AssertionError('unknown case')
   members[0]=('manifest.json',json.dumps(manifest,sort_keys=True,separators=(',',':')).encode(),tarfile.REGTYPE)
   rebuilt=root/'rebuilt.tar'
   with tarfile.open(rebuilt,'w',format=tarfile.USTAR_FORMAT) as archive:
    for name,data,kind in members:
     member=tarfile.TarInfo(name);member.size=len(data);member.type=kind
     if kind==tarfile.SYMTYPE: member.linkname='/outside'
     archive.addfile(member,io.BytesIO(data) if kind==tarfile.REGTYPE else None)
   if case=='trailing_content':
    with rebuilt.open('ab') as output: output.write(b'not an allowed zero tar trailer')
   malicious=root/'malicious.cms'
   algorithm='-aes-256-cbc' if case=='unauthenticated_envelope' else '-aes-256-gcm'
   subprocess.run(['openssl','cms','-encrypt','-binary',algorithm,'-stream','-outform','DER',
    '-recip',str(recipient),'-keyopt','rsa_padding_mode:oaep','-keyopt','rsa_oaep_md:sha256',
    '-in',str(rebuilt),'-out',str(malicious)],capture_output=True,check=True,timeout=10)
   arguments['source']=malicious
   arguments['trusted_sha256']=hashlib.sha256(malicious.read_bytes()).hexdigest()
  reached_consumer=False
  try:
   with recover_bundle(**arguments): reached_consumer=True
  except BackupError as error:
   assert str(error)==expected,(case,str(error),expected)
  else:
   raise AssertionError('unsafe archive reached consumer')
  assert not reached_consumer
  assert not list((root/'offline').iterdir())
  assert original.read_bytes()==encoded
  assert stored.read_bytes()==payload
 print(json.dumps({'case':case,'consumer_bytes_verified':case=='roundtrip',
  'original_generation_preserved':True,'private_staging_empty':True}))
"""


@pytest.mark.parametrize(
    "case",
    [
        "roundtrip",
        "changed_source",
        "symlink_source",
        "unwritable_destination",
        "wrong_digest",
        "wrong_private_key",
        "incompatible_schema",
        "incompatible_image",
        "tampered_ciphertext",
        "truncated_ciphertext",
        "corrupt_blob",
        "missing_blob",
        "traversal",
        "symlink_member",
        "duplicate_member",
        "corrupt_inner_ciphertext",
        "trailing_content",
        "unauthenticated_envelope",
    ],
)
def test_actual_authenticated_archive_boundary(case: str) -> None:
    image = os.environ.get("BONAFIDES_TEST_PARSER_IMAGE")
    if not image:
        pytest.fail("Backup archive tests require the qualified Linux image and Docker.")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--pull=never",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--pids-limit",
            "32",
            "--memory",
            "512m",
            "--cpus",
            "1",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=67108864,mode=1777",
            "--mount",
            f"type=bind,src={root / 'src'},dst=/proof-src,readonly",
            "--env",
            "PYTHONPATH=/proof-src",
            "--entrypoint",
            "python",
            image,
            "-c",
            _PROGRAM,
            case,
        ],
        text=True,
        capture_output=True,
        timeout=40,
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt == {
        "case": case,
        "consumer_bytes_verified": case == "roundtrip",
        "original_generation_preserved": True,
        "private_staging_empty": True,
    }
