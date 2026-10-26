# SPDX-License-Identifier: MIT OR Apache-2.0
"""Checkpoint eligibility policy and actual file checks, NOT native recovery proof.

Only the migrator fixture attests the SQL recovery fields. The independent native
capture/restore drill remains the evidence for the real operator attestation.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from sqlalchemy import text
from test_identity import IdentityContext, _bootstrap, _login
from test_identity import identity as identity

from offerloom.api import create_app
from offerloom.backup_archive import BackupError
from offerloom.backup_configuration import capture_runtime_pins, verified_checkpoint
from offerloom.backup_operator import CaptureConfiguration
from offerloom.commands import canonical_json
from offerloom.config import Settings
from offerloom.db import Database
from offerloom.installation_control import begin_control
from offerloom.workflows import claim_control, enqueue


def _configuration(context: IdentityContext, root: Path) -> tuple[Settings, CaptureConfiguration]:
    root = root.resolve()
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Synthetic checkpoint policy")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    recipient = root / "recipient.pem"
    recipient.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    destination = root / "encrypted"
    destination.mkdir(mode=0o700)
    config = CaptureConfiguration(
        recipient=recipient,
        destination=destination,
        staging_root=root / "staging",
        migrations=Path(__file__).resolve().parents[1] / "migrations",
        image_sha256="1" * 64,
    )
    path = root / "capture.json"
    path.write_text(config.model_dump_json())
    path.chmod(0o600)
    return replace(context.settings, backup_capture_config_file=path), config


def _attested_policy_fixture(
    context: IdentityContext, settings: Settings, config: CaptureConfiguration
) -> tuple[UUID, UUID, Path]:
    """A fake archive with real bytes isolates trust binding, not cryptography."""
    db = Database(settings)
    try:
        with db.transaction() as conn:
            owner = conn.execute(text("SELECT id FROM offerloom.owners")).scalar_one()
            identifier = enqueue(conn, owner, "backup", {})
            task = claim_control(conn, uuid4(), workflow_id=identifier)
            assert task is not None
            control = begin_control(conn, task)
        pins = capture_runtime_pins(config, settings)
        payload = b"Synthetic checkpoint-policy bytes; NOT an installation archive."
        archive = config.destination / (str(uuid4()) + ".cms")
        archive.write_bytes(payload)
        with context.admin.transaction() as conn:
            conn.execute(
                text("""INSERT INTO offerloom.installation_backups(
                id,owner_id,namespace_id,maintenance_epoch,workspace_version,
                installation_auth_epoch,owner_auth_epoch,schema_versions,
                image_sha256,source_sha256,config_sha256,keyring_sha256,recipient_sha256,
                file_name,cipher_sha256,manifest_sha256,byte_length,captured_at,
                restore_verified_at,restore_receipt_sha256,restore_target_namespace)
                SELECT :id,:owner,:namespace,:epoch,s.workspace_version,i.auth_epoch,o.auth_epoch,
                (SELECT jsonb_object_agg(version::text,source_sha256) FROM offerloom.schema_migrations),
                :image,:source,:config,:keyring,:recipient,:file,:cipher,repeat('0',64),:length,
                clock_timestamp(),clock_timestamp(),repeat('0',64),:target
                FROM offerloom.owner_settings s JOIN offerloom.owners o ON o.id=s.owner_id
                CROSS JOIN offerloom.installation_state i WHERE s.owner_id=:owner AND i.singleton"""),
                {
                    "id": identifier,
                    "owner": owner,
                    "namespace": control.namespace_id,
                    "epoch": control.maintenance_epoch,
                    "image": pins["image_sha256"],
                    "source": pins["source_sha256"],
                    "config": pins["config_sha256"],
                    "keyring": pins["keyring_sha256"],
                    "recipient": pins["recipient_sha256"],
                    "file": archive.name,
                    "cipher": hashlib.sha256(payload).hexdigest(),
                    "length": len(payload),
                    "target": uuid4(),
                },
            )
            # This is deliberately only a selection fixture, not an observed
            # maintenance drain, finished backup or native restoration.
            conn.execute(text("UPDATE offerloom.installation_state SET maintenance_active=false"))
        return owner, identifier, archive
    finally:
        db.close()


@pytest.mark.postgres
def test_checkpoint_refuses_stale_owner_workspace_runtime_and_unverified_ledger(
    identity: IdentityContext, tmp_path: Path
) -> None:
    with identity.client() as client:
        assert _bootstrap(identity, client).status_code == 201
    settings, config = _configuration(identity, tmp_path)
    owner, identifier, archive = _attested_policy_fixture(identity, settings, config)
    db = Database(settings)
    try:
        result = verified_checkpoint(db, settings, owner)
        assert result.generation_id == identifier
        assert (
            result.runtime_pins["cipher_sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
        )
        for column, expression in (
            ("image_sha256", "repeat('2',64)"),
            ("source_sha256", "repeat('2',64)"),
            ("config_sha256", "repeat('2',64)"),
            ("keyring_sha256", "repeat('2',64)"),
            ("recipient_sha256", "repeat('2',64)"),
            ("namespace_id", "gen_random_uuid()"),
            ("workspace_version", "workspace_version+1"),
            ("maintenance_epoch", "maintenance_epoch+1"),
            ("owner_auth_epoch", "owner_auth_epoch+1"),
            ("installation_auth_epoch", "installation_auth_epoch+1"),
            ("schema_versions", "'{}'::jsonb"),
        ):
            with identity.admin.transaction() as conn:
                original = conn.execute(
                    text(f"SELECT {column} FROM offerloom.installation_backups WHERE id=:id"),
                    {"id": identifier},
                ).scalar_one()
                conn.execute(
                    text(
                        f"UPDATE offerloom.installation_backups SET {column}={expression} WHERE id=:id"
                    ),
                    {"id": identifier},
                )
            try:
                with pytest.raises(BackupError, match="^fresh_verified_checkpoint_required$"):
                    verified_checkpoint(db, settings, owner, identifier)
            finally:
                with identity.admin.transaction() as conn:
                    conn.execute(
                        text(
                            f"UPDATE offerloom.installation_backups SET {column}=:value WHERE id=:id"
                        )
                        if column != "schema_versions"
                        else text(
                            "UPDATE offerloom.installation_backups SET schema_versions=CAST(:value AS jsonb) WHERE id=:id"
                        ),
                        {
                            "id": identifier,
                            "value": canonical_json(original)
                            if column == "schema_versions"
                            else original,
                        },
                    )
        with identity.admin.transaction() as conn:
            conn.execute(
                text("""UPDATE offerloom.installation_backups SET restore_verified_at=NULL,
                restore_receipt_sha256=NULL,restore_target_namespace=NULL WHERE id=:id"""),
                {"id": identifier},
            )
        with pytest.raises(BackupError, match="^fresh_verified_checkpoint_required$"):
            verified_checkpoint(db, settings, owner)
    finally:
        db.close()


@pytest.mark.postgres
def test_actual_checkpoint_ciphertext_and_config_are_required(
    identity: IdentityContext, tmp_path: Path
) -> None:
    with identity.client() as client:
        assert _bootstrap(identity, client).status_code == 201
    settings, config = _configuration(identity, tmp_path)
    owner, identifier, archive = _attested_policy_fixture(identity, settings, config)
    db = Database(settings)
    original = archive.read_bytes()
    try:
        archive.write_bytes(bytes([original[0] ^ 1]) + original[1:])
        with pytest.raises(BackupError, match="^backup_checkpoint_cipher_mismatch$"):
            verified_checkpoint(db, settings, owner, identifier)
        archive.write_bytes(original[:-1])
        with pytest.raises(BackupError, match="^backup_checkpoint_size_mismatch$"):
            verified_checkpoint(db, settings, owner, identifier)
        archive.unlink()
        archive.symlink_to(config.recipient)
        with pytest.raises(BackupError, match="^backup_checkpoint_file_unavailable$"):
            verified_checkpoint(db, settings, owner, identifier)
        archive.unlink()
        with pytest.raises(BackupError, match="^backup_checkpoint_file_unavailable$"):
            verified_checkpoint(db, settings, owner, identifier)
        archive.write_bytes(original)
        assert settings.backup_capture_config_file is not None
        changed = config.model_copy(update={"image_sha256": "3" * 64})
        settings.backup_capture_config_file.write_text(changed.model_dump_json())
        with pytest.raises(BackupError, match="^fresh_verified_checkpoint_required$"):
            verified_checkpoint(db, settings, owner, identifier)
    finally:
        db.close()


@pytest.mark.postgres
def test_status_requires_owner_and_distinguishes_missing_from_invalid_configuration(
    identity: IdentityContext, tmp_path: Path
) -> None:
    from fastapi.testclient import TestClient

    with identity.client() as client:
        assert client.get("/api/v1/installation-backups").status_code == 401
        assert _bootstrap(identity, client).status_code == 201
        assert _login(identity, client).status_code == 201
        status = client.get("/api/v1/installation-backups")
        assert status.status_code == 200
        assert status.json()["eligible_checkpoint_id"] is None
        assert status.json()["eligibility_reason"] == "backup_not_configured"
        settings, config = _configuration(identity, tmp_path)
        assert settings.backup_capture_config_file is not None
        settings.backup_capture_config_file.write_text(
            json.dumps({"private_marker": "do-not-disclose"})
        )
        with TestClient(create_app(settings), base_url=settings.origin) as configured:
            configured.cookies.update(client.cookies)
            rejected = configured.get("/api/v1/installation-backups")
            assert rejected.status_code == 503
            assert rejected.json()["code"] == "backup_configuration_invalid"
            assert "do-not-disclose" not in rejected.text
            assert str(config.recipient) not in rejected.text
