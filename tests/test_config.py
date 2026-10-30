# SPDX-License-Identifier: MIT OR Apache-2.0
from __future__ import annotations

import base64
import json
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.exceptions import InvalidTag
from hypothesis import given
from hypothesis import strategies as st

from bonafides.config import ConfigurationError, MigrationSettings, Settings, read_secret
from bonafides.crypto import (
    EnvelopeContext,
    Keyring,
    canonical_provider_origin,
    decrypt,
    decrypt_connected_credential,
    encrypt,
)


def keyring() -> Keyring:
    return Keyring.parse(
        json.dumps(
            {
                "active_key_id": "test-1",
                "keys": {"test-1": base64.b64encode(bytes(range(32))).decode()},
            }
        )
    )


def environment(tmp_path: Path) -> dict[str, str]:
    dsn = tmp_path / "dsn"
    dsn.write_text("postgresql://owner:private-password@localhost/bonafides_test")
    dsn.chmod(0o600)
    keys = tmp_path / "keys"
    keys.write_text(
        json.dumps(
            {
                "active_key_id": "test-1",
                "keys": {"test-1": base64.b64encode(bytes(range(32))).decode()},
            }
        )
    )
    keys.chmod(0o600)
    blobs, spool, import_spool = (
        tmp_path / "blobs",
        tmp_path / "spool",
        tmp_path / "import-spool",
    )
    for directory in (blobs, spool, import_spool):
        directory.mkdir()
    return {
        "BONAFIDES_DATABASE_DSN_FILE": str(dsn),
        "BONAFIDES_KEYRING_FILE": str(keys),
        "BONAFIDES_ORIGIN": "http://localhost:8000",
        "BONAFIDES_BLOB_ROOT": str(blobs),
        "BONAFIDES_RENDERER_SPOOL": str(spool),
        "BONAFIDES_IMPORT_SPOOL": str(import_spool),
    }


def test_settings_load_only_explicit_secret_files(tmp_path: Path) -> None:
    env = environment(tmp_path)
    settings = Settings.from_env(env)
    assert settings.mode == "production"
    assert settings.database_dsn.startswith("postgresql+psycopg://")
    assert "private-password" not in repr(settings)
    assert "private-password" not in repr(settings.keyring)
    assert settings.renderer_spool != settings.blob_root
    assert settings.migration_dsn is None
    assert settings.local_model_route_file is None


def test_local_model_route_is_explicit_and_not_auto_provisioned(tmp_path: Path) -> None:
    env = environment(tmp_path)
    path = tmp_path / "operator-owned-route.json"
    env["BONAFIDES_LOCAL_MODEL_ROUTE_FILE"] = str(path)
    settings = Settings.from_env(env)
    assert settings.local_model_route_file == path
    assert not path.exists()
    env["BONAFIDES_LOCAL_MODEL_ROUTE_FILE"] = ""
    assert Settings.from_env(env).local_model_route_file is None


@pytest.mark.parametrize(
    "field",
    ["DATABASE_DSN_FILE", "KEYRING_FILE", "ORIGIN", "BLOB_ROOT", "RENDERER_SPOOL", "IMPORT_SPOOL"],
)
def test_missing_required_configuration_names_only(tmp_path: Path, field: str) -> None:
    env = environment(tmp_path)
    del env[f"BONAFIDES_{field}"]
    with pytest.raises(ConfigurationError) as error:
        Settings.from_env(env)
    assert "private-password" not in str(error.value)


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.com",
        "http://localhost:8000/",
        "https://user:pass@example.com",
        "https://example.com?x=1",
        "https://example.com#fragment",
        "https://example.com:0",
        "null",
        "https://example.com\n",
    ],
)
def test_origin_is_exact_secure_or_loopback(tmp_path: Path, origin: str) -> None:
    env = environment(tmp_path)
    env["BONAFIDES_ORIGIN"] = origin
    with pytest.raises(ConfigurationError):
        Settings.from_env(env)


@pytest.mark.parametrize(
    "dsn",
    [
        "sqlite:///local.db",
        "postgresql://owner:password@localhost",
        "postgresql://owner@localhost/database",
        "postgresql://owner:password@localhost/database?options=-csearch_path=evil",
    ],
)
def test_invalid_database_configuration_redacted(tmp_path: Path, dsn: str) -> None:
    env = environment(tmp_path)
    Path(env["BONAFIDES_DATABASE_DSN_FILE"]).write_text(dsn)
    with pytest.raises(ConfigurationError) as error:
        Settings.from_env(env)
    assert dsn not in str(error.value)


def test_rejects_world_readable_secret_and_symlink(tmp_path: Path) -> None:
    secret = tmp_path / "secret"
    secret.write_text("sensitive")
    secret.chmod(0o644)
    with pytest.raises(ConfigurationError):
        read_secret(secret, "keyring")
    secret.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(secret)
    with pytest.raises(ConfigurationError):
        read_secret(link, "keyring")


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("RENDERER_SPOOL", "BLOB_ROOT"),
        ("IMPORT_SPOOL", "BLOB_ROOT"),
        ("IMPORT_SPOOL", "RENDERER_SPOOL"),
    ],
)
def test_rejects_shared_private_roots(tmp_path: Path, left: str, right: str) -> None:
    env = environment(tmp_path)
    env[f"BONAFIDES_{left}"] = env[f"BONAFIDES_{right}"]
    with pytest.raises(ConfigurationError):
        Settings.from_env(env)


def test_migration_configuration_needs_only_migration_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = tmp_path / "migration"
    secret.write_text("postgresql://migrator:secret@localhost/bonafides_test")
    secret.chmod(0o600)
    monkeypatch.setenv("BONAFIDES_MIGRATION_DSN_FILE", str(secret))
    assert MigrationSettings.load().migration_dsn is not None


@pytest.mark.parametrize(
    "value",
    [
        "{}",
        '{"active_key_id":"missing","keys":{}}',
        '{"active_key_id":"a","keys":{"a":"invalid!"}}',
        '{"active_key_id":"a","keys":{"a":"c2hvcnQ="}}',
    ],
)
def test_invalid_keyring_fails_closed(value: str) -> None:
    with pytest.raises(ValueError, match="Invalid keyring"):
        Keyring.parse(value)


@given(st.binary(max_size=4096))
def test_aead_roundtrip_and_fresh_nonce(plaintext: bytes) -> None:
    keys = keyring()
    context = EnvelopeContext(uuid4(), uuid4(), "credential", uuid4())
    first, second = encrypt(keys, context, plaintext), encrypt(keys, context, plaintext)
    assert len(first.nonce) == 12
    assert len(first.ciphertext) == len(plaintext) + 16
    assert first.nonce != second.nonce
    assert decrypt(keys, context, first) == plaintext


@pytest.mark.parametrize("field", ["namespace_id", "owner_id", "record_id", "purpose"])
def test_aead_rejects_every_wrong_context_field(field: str) -> None:
    keys = keyring()
    context = EnvelopeContext(uuid4(), uuid4(), "credential", uuid4())
    encrypted = encrypt(keys, context, b"synthetic secret")
    wrong = {
        "namespace_id": replace(context, namespace_id=uuid4()),
        "owner_id": replace(context, owner_id=uuid4()),
        "record_id": replace(context, record_id=uuid4()),
        "purpose": replace(context, purpose="other"),
    }[field]
    with pytest.raises(InvalidTag):
        decrypt(keys, wrong, encrypted)


def test_aead_rejects_wrong_key_ciphertext_tag_and_nonce() -> None:
    keys = keyring()
    context = EnvelopeContext(uuid4(), uuid4(), "credential", uuid4())
    encrypted = encrypt(keys, context, b"synthetic secret")
    different_keys = Keyring("test-1", {"test-1": bytes(reversed(range(32)))})
    with pytest.raises(InvalidTag):
        decrypt(different_keys, context, encrypted)
    for position in (0, len(encrypted.ciphertext) - 1):
        corrupted = bytearray(encrypted.ciphertext)
        corrupted[position] ^= 1
        with pytest.raises(InvalidTag):
            decrypt(keys, context, replace(encrypted, ciphertext=bytes(corrupted)))
    nonce = bytearray(encrypted.nonce)
    nonce[0] ^= 1
    with pytest.raises(InvalidTag):
        decrypt(keys, context, replace(encrypted, nonce=bytes(nonce)))
    for bad in (
        replace(encrypted, nonce=b"short"),
        replace(encrypted, ciphertext=b"short"),
        replace(encrypted, version=2),
    ):
        with pytest.raises(ValueError):
            decrypt(keys, context, bad)
    with pytest.raises(KeyError):
        decrypt(keys, context, replace(encrypted, key_id="missing"))
    with pytest.raises(ValueError):
        decrypt(keys, replace(context, version=2), encrypted)


def test_legacy_aad_bytes_remain_exact_for_native_backup_recovery() -> None:
    namespace, owner, record = uuid4(), uuid4(), uuid4()
    context = EnvelopeContext(namespace, owner, "provider_token", record)
    assert (
        context.aad()
        == json.dumps(
            {
                "algorithm": "AES-256-GCM",
                "namespace_id": str(namespace),
                "owner_id": str(owner),
                "purpose": "provider_token",
                "record_id": str(record),
                "version": 1,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )


@pytest.mark.parametrize(
    "origin",
    [
        "http://provider.example",
        "https://Provider.example",
        "https://provider.example/",
        "https://provider.example:443",
        "https://provider.example:0",
        "https://provider.example:65536",
        "https://provider.example.",
        "https://provider.example?next=x",
        "https://provider.example#fragment",
        "https://user:password@provider.example",
        "https://provider.example\n",
        "https://provider.example\\other",
        "https://-provider.example",
        "https://provider..example",
    ],
)
def test_credential_origin_requires_exact_canonical_https(origin: str) -> None:
    with pytest.raises(ValueError, match="canonical provider origin"):
        canonical_provider_origin(origin)


@pytest.mark.parametrize(
    "origin", ["https://provider.example", "https://provider.example:8443", "https://[::1]"]
)
def test_credential_origin_accepts_only_canonical_destination(origin: str) -> None:
    assert canonical_provider_origin(origin) == origin


@pytest.mark.parametrize(
    "field", ["namespace_id", "owner_id", "record_id", "purpose", "provider_origin"]
)
def test_origin_bound_v2_rejects_every_substituted_context(field: str) -> None:
    context = EnvelopeContext(
        uuid4(), uuid4(), "provider_token", uuid4(), 2, "https://provider.example"
    )
    envelope = encrypt(keyring(), context, b"synthetic provider credential")
    assert envelope.version == 2
    assert decrypt(keyring(), context, envelope) == b"synthetic provider credential"
    if field == "namespace_id":
        substituted = replace(context, namespace_id=uuid4())
    elif field == "owner_id":
        substituted = replace(context, owner_id=uuid4())
    elif field == "record_id":
        substituted = replace(context, record_id=uuid4())
    elif field == "purpose":
        substituted = replace(context, purpose="other-purpose")
    else:
        substituted = replace(context, provider_origin="https://other.example")
    with pytest.raises(InvalidTag):
        decrypt(keyring(), substituted, envelope)


@pytest.mark.parametrize("status", ["disconnected", "reauthorize", "revocation_pending"])
def test_retained_disconnected_credentials_never_authorize_live_reads(status: str) -> None:
    context = EnvelopeContext(
        uuid4(), uuid4(), "provider_token", uuid4(), 2, "https://provider.example"
    )
    envelope = encrypt(keyring(), context, b"synthetic retained token")
    with pytest.raises(ValueError, match="connection and provider origin"):
        decrypt_connected_credential(
            keyring(),
            context,
            envelope,
            configured_provider_origin="https://provider.example",
            account_status=status,
        )
    assert decrypt(keyring(), context, envelope) == b"synthetic retained token"


def test_live_credential_read_requires_v2_and_same_configured_provider() -> None:
    legacy = EnvelopeContext(uuid4(), uuid4(), "provider_token", uuid4())
    with pytest.raises(ValueError, match="connection and provider origin"):
        decrypt_connected_credential(
            keyring(),
            legacy,
            encrypt(keyring(), legacy, b"synthetic token"),
            configured_provider_origin="https://provider.example",
            account_status="connected",
        )
    context = replace(legacy, version=2, provider_origin="https://provider.example")
    envelope = encrypt(keyring(), context, b"synthetic token")
    with pytest.raises(ValueError, match="connection and provider origin"):
        decrypt_connected_credential(
            keyring(),
            context,
            envelope,
            configured_provider_origin="https://other.example",
            account_status="connected",
        )
    assert (
        decrypt_connected_credential(
            keyring(),
            context,
            envelope,
            configured_provider_origin="https://provider.example",
            account_status="connected",
        )
        == b"synthetic token"
    )
