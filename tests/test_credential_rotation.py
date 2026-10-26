# SPDX-License-Identifier: MIT OR Apache-2.0
"""Real PostgreSQL/envelopes and real process interruption, without provider APIs."""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from cryptography.exceptions import InvalidTag
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from test_database import PGContext
from test_database import pg_context as pg_context

from offerloom.auth import recover_password
from offerloom.backup_archive import BackupError, BackupFile, BackupManifest
from offerloom.cli import main
from offerloom.credential_rotation import (
    CredentialBinding,
    CredentialRotationConfiguration,
    rotate_credentials,
)
from offerloom.crypto import Envelope, EnvelopeContext, Keyring, decrypt, encrypt
from offerloom.db import Database
from offerloom.execution_guard import LOCK_NAME, ExecutionGuard
from offerloom.installation_restore import _escrow, _rebind_credentials, _revoke_restored_authority


@dataclass(frozen=True)
class RotationCase:
    pg: PGContext
    config: CredentialRotationConfiguration
    config_file: Path
    keyring: Keyring
    old_keyring: Keyring
    plaintexts: dict[UUID, bytes]
    old_envelopes: dict[UUID, Envelope]
    old_contexts: dict[UUID, EnvelopeContext]
    account_id: UUID


def _keyfile(path: Path, keys: Keyring) -> None:
    path.write_text(
        json.dumps(
            {
                "active_key_id": keys.active_key_id,
                "keys": {key: base64.b64encode(value).decode() for key, value in keys.keys.items()},
            }
        )
    )
    path.chmod(0o600)


def _configuration_file(case: RotationCase) -> None:
    case.config_file.write_text(case.config.model_dump_json())
    case.config_file.chmod(0o600)


@pytest.fixture
def rotation_case(pg_context: PGContext, tmp_path: Path) -> Iterator[RotationCase]:
    blob = tmp_path / "blobs"
    blob.mkdir(mode=0o700)
    with ExecutionGuard(blob) as guard, pg_context.migrator.begin() as conn:
        conn.execute(
            text(
                "UPDATE offerloom.installation_state SET execution_guard_id=:guard WHERE singleton"
            ),
            {"guard": guard.identity},
        )
        state = conn.execute(
            text(
                "SELECT namespace_id,maintenance_epoch FROM offerloom.installation_state WHERE singleton"
            )
        ).one()
    old_id, new_id = "old-" + uuid4().hex, "new-" + uuid4().hex
    old = Keyring(old_id, {old_id: bytes(range(32))})
    keys = Keyring(new_id, {**old.keys, new_id: bytes(reversed(range(32)))})
    dsn_file, key_file = tmp_path / "migrator", tmp_path / "keyring"
    dsn_file.write_text(pg_context.migrator.url.render_as_string(hide_password=False))
    dsn_file.chmod(0o600)
    _keyfile(key_file, keys)
    account = uuid4()
    bindings: list[CredentialBinding] = []
    plaintexts: dict[UUID, bytes] = {}
    envelopes: dict[UUID, Envelope] = {}
    contexts: dict[UUID, EnvelopeContext] = {}
    with pg_context.migrator.begin() as conn:
        conn.execute(
            text("""INSERT INTO offerloom.external_accounts
            (owner_id,id,provider_key,external_subject_hash,status)
            VALUES(:owner,:id,'synthetic-provider',:subject,'disconnected')"""),
            {"owner": pg_context.owner_id, "id": account, "subject": uuid4().hex},
        )
        for index, purpose in enumerate(("access_token", "refresh_token", "client_secret")):
            record = UUID(int=index + 1)
            plaintext = f"synthetic-{purpose}-not-a-live-provider-secret".encode()
            context = EnvelopeContext(state.namespace_id, pg_context.owner_id, purpose, record)
            envelope = encrypt(old, context, plaintext)
            conn.execute(
                text("""INSERT INTO offerloom.encrypted_credentials
                (owner_id,id,external_account_id,purpose,key_id,algorithm,nonce,ciphertext)
                VALUES(:owner,:id,:account,:purpose,:key,'AES-256-GCM',:nonce,:ciphertext)"""),
                {
                    "owner": pg_context.owner_id,
                    "id": record,
                    "account": account,
                    "purpose": purpose,
                    "key": envelope.key_id,
                    "nonce": envelope.nonce,
                    "ciphertext": envelope.ciphertext,
                },
            )
            bindings.append(
                CredentialBinding(
                    owner_id=pg_context.owner_id,
                    credential_id=record,
                    external_account_id=account,
                    provider_key="synthetic-provider",
                    purpose=purpose,
                    provider_origin="https://provider.example",
                )
            )
            plaintexts[record], envelopes[record], contexts[record] = plaintext, envelope, context
    config = CredentialRotationConfiguration(
        migration_dsn_file=dsn_file,
        blob_root=blob,
        credential_keyring_file=key_file,
        namespace_id=state.namespace_id,
        maintenance_epoch=state.maintenance_epoch,
        previous_active_key_id=old_id,
        new_active_key_id=new_id,
        credential_bindings=bindings,
        batch_size=1,
    )
    case = RotationCase(
        pg_context,
        config,
        tmp_path / "rotation.json",
        keys,
        old,
        plaintexts,
        envelopes,
        contexts,
        account,
    )
    _configuration_file(case)
    try:
        yield case
    finally:
        with pg_context.migrator.begin() as conn:
            conn.execute(
                text(
                    "UPDATE offerloom.installation_state SET maintenance_active=false WHERE singleton"
                )
            )
            conn.execute(
                text("DELETE FROM offerloom.credential_key_usage WHERE key_id IN (:old,:new)"),
                {"old": old_id, "new": new_id},
            )


def _rows(case: RotationCase) -> list[RowMapping]:
    with case.pg.migrator.begin() as conn:
        return list(
            conn.execute(
                text(
                    "SELECT * FROM offerloom.encrypted_credentials WHERE owner_id=:owner ORDER BY id"
                ),
                {"owner": case.pg.owner_id},
            ).mappings()
        )


def _assert_rotated(case: RotationCase) -> None:
    rows = _rows(case)
    assert len(rows) == len(case.plaintexts) == 3
    for row in rows:
        assert row["key_id"] == case.keyring.active_key_id
        assert row["aad_version"] == 2 and row["provider_origin"] == "https://provider.example"
        assert row["version"] == 2 and row["rotated_at"] is not None
        envelope = Envelope(row["key_id"], bytes(row["nonce"]), bytes(row["ciphertext"]), 2)
        assert envelope.nonce != case.old_envelopes[row["id"]].nonce
        assert envelope.ciphertext != case.old_envelopes[row["id"]].ciphertext
        context = EnvelopeContext(
            case.config.namespace_id,
            row["owner_id"],
            row["purpose"],
            row["id"],
            2,
            row["provider_origin"],
        )
        assert decrypt(case.keyring, context, envelope) == case.plaintexts[row["id"]]
        with pytest.raises(KeyError):
            decrypt(case.old_keyring, context, envelope)
    with case.pg.migrator.begin() as conn:
        assert conn.execute(
            text("SELECT status FROM offerloom.external_accounts WHERE id=:id"),
            {"id": case.account_id},
        ).scalar_one() in ("disconnected", "reauthorize")


@pytest.mark.postgres
@pytest.mark.parametrize("status", ["disconnected", "reauthorize"])
def test_rotation_reencrypts_every_class_with_fresh_nonce_and_preserves_lifecycle(
    rotation_case: RotationCase, status: str
) -> None:
    case = rotation_case
    original_keyfile = case.config.credential_keyring_file.read_bytes()
    with case.pg.migrator.begin() as conn:
        conn.execute(
            text("UPDATE offerloom.external_accounts SET status=:status WHERE id=:id"),
            {"status": status, "id": case.account_id},
        )
    receipt = rotate_credentials(case.config)
    assert (receipt.verified, receipt.rotated, receipt.already_current, receipt.batches) == (
        3,
        3,
        0,
        3,
    )
    assert receipt.old_backup_keys_required and not receipt.accounts_reconnected
    assert not receipt.provider_tokens_revoked
    _assert_rotated(case)
    assert case.config.credential_keyring_file.read_bytes() == original_keyfile
    second = rotate_credentials(case.config)
    assert (second.verified, second.rotated, second.already_current, second.batches) == (3, 0, 3, 0)
    _assert_rotated(case)
    with case.pg.migrator.begin() as conn:
        details = (
            conn.execute(
                text(
                    "SELECT detail FROM offerloom.audit_events WHERE action='credential_key_rotation'"
                )
            )
            .scalars()
            .all()
        )
    assert len(details) == 7
    assert all(set(detail) == {"status", "verified", "rotated"} for detail in details)
    assert sum(detail["status"] == "completed" for detail in details) == 2


@pytest.mark.postgres
def test_password_and_session_revocation_are_independent_of_credential_rotation(
    rotation_case: RotationCase,
) -> None:
    case = rotation_case
    db = Database(case.pg.migrator.url.render_as_string(hide_password=False))
    try:
        with db.transaction() as conn:
            session = conn.execute(
                text("""INSERT INTO offerloom.sessions
                (owner_id,token_sha256,csrf_sha256,expires_at)
                VALUES(:owner,repeat('b',64),repeat('c',64),now()+interval '1 day') RETURNING id"""),
                {"owner": case.pg.owner_id},
            ).scalar_one()
        old_rows = _rows(case)
        recover_password(db, "Synthetic independent password rotation 724!")
        assert _rows(case) == old_rows
        with db.transaction() as conn:
            assert conn.execute(
                text("SELECT revoked_at IS NOT NULL FROM offerloom.sessions WHERE id=:id"),
                {"id": session},
            ).scalar_one()
            owner_before = conn.execute(
                text("SELECT password_hash,auth_epoch,version FROM offerloom.owners")
            ).one()
            fresh = conn.execute(
                text("""INSERT INTO offerloom.sessions
                (owner_id,token_sha256,csrf_sha256,expires_at,auth_epoch)
                SELECT id,repeat('d',64),repeat('e',64),now()+interval '1 day',auth_epoch
                FROM offerloom.owners RETURNING id""")
            ).scalar_one()
        rotate_credentials(case.config)
        _assert_rotated(case)
        with db.transaction() as conn:
            assert (
                conn.execute(
                    text("SELECT password_hash,auth_epoch,version FROM offerloom.owners")
                ).one()
                == owner_before
            )
            assert conn.execute(
                text("SELECT revoked_at IS NULL FROM offerloom.sessions WHERE id=:id"),
                {"id": fresh},
            ).scalar_one()
    finally:
        db.close()


@pytest.mark.postgres
@pytest.mark.parametrize("damage", ["missing_old_key", "tampered_tag", "same_key_alias"])
def test_preflight_authenticates_all_rows_and_rejects_bad_keys_without_partial_rewrite(
    rotation_case: RotationCase, damage: str
) -> None:
    case = rotation_case
    if damage == "missing_old_key":
        _keyfile(
            case.config.credential_keyring_file,
            Keyring(
                case.keyring.active_key_id,
                {case.keyring.active_key_id: case.keyring.keys[case.keyring.active_key_id]},
            ),
        )
    elif damage == "same_key_alias":
        _keyfile(
            case.config.credential_keyring_file,
            Keyring(
                case.keyring.active_key_id,
                {
                    **case.old_keyring.keys,
                    case.keyring.active_key_id: case.old_keyring.keys[
                        case.old_keyring.active_key_id
                    ],
                },
            ),
        )
    else:
        last = _rows(case)[-1]
        ciphertext = bytearray(last["ciphertext"])
        ciphertext[-1] ^= 1
        with case.pg.migrator.begin() as conn:
            conn.execute(
                text("UPDATE offerloom.encrypted_credentials SET ciphertext=:cipher WHERE id=:id"),
                {"cipher": bytes(ciphertext), "id": last["id"]},
            )
    before = _rows(case)
    with pytest.raises(
        BackupError, match="credential_rotation_(authentication|independent_new_active)"
    ):
        rotate_credentials(case.config)
    assert _rows(case) == before
    with case.pg.migrator.begin() as conn:
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM offerloom.audit_events WHERE action='credential_key_rotation'"
                )
            ).scalar_one()
            == 0
        )


@pytest.mark.postgres
@pytest.mark.parametrize("change", ["config_origin", "stored_origin", "purpose", "record"])
def test_origin_bound_rows_cannot_be_silently_rebound_or_substituted(
    rotation_case: RotationCase, change: str
) -> None:
    case = rotation_case
    rotate_credentials(case.config)
    config = case.config
    if change == "config_origin":
        bindings = [
            item.model_copy(update={"provider_origin": "https://other.example"})
            for item in config.credential_bindings
        ]
        config = config.model_copy(update={"credential_bindings": bindings})
    else:
        row = _rows(case)[0]
        with case.pg.migrator.begin() as conn:
            if change == "stored_origin":
                conn.execute(
                    text(
                        "UPDATE offerloom.encrypted_credentials SET provider_origin='https://other.example' WHERE id=:id"
                    ),
                    {"id": row["id"]},
                )
                bindings = [
                    item.model_copy(update={"provider_origin": "https://other.example"})
                    if item.credential_id == row["id"]
                    else item
                    for item in config.credential_bindings
                ]
                config = config.model_copy(update={"credential_bindings": bindings})
            elif change == "purpose":
                conn.execute(
                    text(
                        "UPDATE offerloom.encrypted_credentials SET purpose='wrong-purpose' WHERE id=:id"
                    ),
                    {"id": row["id"]},
                )
                config = config.model_copy(
                    update={
                        "credential_bindings": [
                            item.model_copy(update={"purpose": "wrong-purpose"})
                            if item.credential_id == row["id"]
                            else item
                            for item in config.credential_bindings
                        ]
                    }
                )
            else:
                other = _rows(case)[1]
                # Preserve the unique (key_id,nonce) invariant while moving an
                # authentic envelope to another record: authentication must fail.
                conn.execute(
                    text("DELETE FROM offerloom.encrypted_credentials WHERE id=:id"),
                    {"id": other["id"]},
                )
                conn.execute(
                    text(
                        "UPDATE offerloom.encrypted_credentials SET nonce=:nonce,ciphertext=:cipher WHERE id=:id"
                    ),
                    {"nonce": other["nonce"], "cipher": other["ciphertext"], "id": row["id"]},
                )
                config = config.model_copy(
                    update={
                        "credential_bindings": [
                            item
                            for item in config.credential_bindings
                            if item.credential_id != other["id"]
                        ]
                    }
                )
    before = _rows(case)
    with pytest.raises(
        BackupError, match="credential_rotation_(binding_mismatch|authentication_failed)"
    ):
        rotate_credentials(config)
    assert _rows(case) == before


@pytest.mark.postgres
@pytest.mark.parametrize("changed", ["namespace", "epoch", "guard", "runtime_role"])
def test_rotation_requires_exact_installation_guard_epoch_and_migrator(
    rotation_case: RotationCase, changed: str, tmp_path: Path
) -> None:
    case, config = rotation_case, rotation_case.config
    if changed == "namespace":
        config = config.model_copy(update={"namespace_id": uuid4()})
    elif changed == "epoch":
        config = config.model_copy(update={"maintenance_epoch": config.maintenance_epoch + 1})
    elif changed == "guard":
        with case.pg.migrator.begin() as conn:
            conn.execute(
                text(
                    "UPDATE offerloom.installation_state SET execution_guard_id=:id WHERE singleton"
                ),
                {"id": uuid4()},
            )
    else:
        path = tmp_path / "runtime-dsn"
        path.write_text(case.pg.runtime.url.render_as_string(hide_password=False))
        path.chmod(0o600)
        config = config.model_copy(update={"migration_dsn_file": path})
    before = _rows(case)
    with pytest.raises(BackupError, match="credential_rotation_(installation_identity|migrator)"):
        rotate_credentials(config)
    assert _rows(case) == before


@pytest.mark.postgres
def test_rotation_cannot_replace_missing_canonical_lock_or_run_alongside_live_actor(
    rotation_case: RotationCase,
) -> None:
    case = rotation_case
    with (
        ExecutionGuard(case.config.blob_root, create=False),
        pytest.raises(BackupError, match="stop_all_runtime_actors"),
    ):
        rotate_credentials(case.config)
    lock = case.config.blob_root / LOCK_NAME
    lock.unlink()
    with pytest.raises(FileNotFoundError):
        rotate_credentials(case.config)
    assert not lock.exists()
    assert all(row["key_id"] == case.old_keyring.active_key_id for row in _rows(case))


@pytest.mark.postgres
@pytest.mark.parametrize("kind", ["work", "control", "activity", "account", "cursor", "oauth"])
def test_rotation_refuses_active_work_or_uncontracted_envelope_classes(
    rotation_case: RotationCase, kind: str
) -> None:
    case = rotation_case
    activity = uuid4()
    with case.pg.migrator.begin() as conn:
        if kind == "work":
            conn.execute(
                text("""INSERT INTO offerloom.workflow_runs
                (owner_id,kind,input,dedupe_key,request_sha256,deadline_at)
                VALUES(:owner,'backup','{}',:dedupe,repeat('0',64),now()+interval '1 hour')"""),
                {"owner": case.pg.owner_id, "dedupe": uuid4().hex},
            )
        elif kind == "control":
            control = uuid4()
            conn.execute(
                text("""INSERT INTO offerloom.workflow_runs
                (owner_id,id,kind,input,dedupe_key,request_sha256,deadline_at,state,finished_at)
                VALUES(:owner,:id,'backup','{}',:dedupe,repeat('0',64),now()+interval '1 hour',
                'succeeded',clock_timestamp())"""),
                {"owner": case.pg.owner_id, "id": control, "dedupe": uuid4().hex},
            )
            conn.execute(
                text("""INSERT INTO offerloom.installation_controls
                (id,owner_id,holder,fence,workflow_fence,namespace_id,maintenance_epoch,
                 purpose,scope,deadline_at,drain_deadline_at,lease_expires_at)
                VALUES(:id,:owner,:holder,1,1,:namespace,:epoch,'backup','{}',
                now()+interval '1 hour',now()+interval '1 minute',now()+interval '30 seconds')"""),
                {
                    "id": control,
                    "owner": case.pg.owner_id,
                    "holder": uuid4(),
                    "namespace": case.config.namespace_id,
                    "epoch": max(1, case.config.maintenance_epoch),
                },
            )
        elif kind == "activity":
            conn.execute(
                text("""INSERT INTO offerloom.installation_activities
                (id,namespace_id,maintenance_epoch,participant_id,kind,execution_id)
                VALUES(:id,:namespace,:epoch,:participant,'api',:execution)"""),
                {
                    "id": activity,
                    "namespace": case.config.namespace_id,
                    "epoch": case.config.maintenance_epoch,
                    "participant": uuid4(),
                    "execution": uuid4(),
                },
            )
        elif kind == "account":
            conn.execute(
                text("UPDATE offerloom.external_accounts SET status='connected' WHERE id=:id"),
                {"id": case.account_id},
            )
        elif kind == "cursor":
            conn.execute(
                text(
                    "UPDATE offerloom.external_accounts SET sync_cursor_ciphertext=:cipher WHERE id=:id"
                ),
                {"cipher": b"uncontracted synthetic cursor", "id": case.account_id},
            )
        else:
            session = conn.execute(
                text("""INSERT INTO offerloom.sessions(owner_id,token_sha256,csrf_sha256,expires_at)
                VALUES(:owner,repeat('f',64),repeat('a',64),now()+interval '1 day') RETURNING id"""),
                {"owner": case.pg.owner_id},
            ).scalar_one()
            conn.execute(
                text("""INSERT INTO offerloom.oauth_transactions
                (owner_id,state_sha256,session_id,provider_key,pkce_ciphertext,key_id,nonce,
                 redirect_uri,requested_scopes,expires_at)
                VALUES(:owner,repeat('1',64),:session,'synthetic-provider',:cipher,:key,:nonce,
                'https://provider.example/callback','{}',now()+interval '1 hour')"""),
                {
                    "owner": case.pg.owner_id,
                    "session": session,
                    "cipher": b"opaque unsupported OAuth",
                    "key": case.old_keyring.active_key_id,
                    "nonce": bytes(12),
                },
            )
    before = _rows(case)
    try:
        with pytest.raises(
            BackupError, match="credential_rotation_(active_work|unsupported_envelope)"
        ):
            rotate_credentials(case.config)
        assert _rows(case) == before
    finally:
        with case.pg.migrator.begin() as conn:
            conn.execute(
                text("DELETE FROM offerloom.installation_activities WHERE id=:id"), {"id": activity}
            )


@pytest.mark.postgres
def test_real_killed_process_resumes_only_unrotated_key_ids(rotation_case: RotationCase) -> None:
    case = rotation_case
    # A real PG trigger blocks the second batch, after the first batch commits.
    # There is no test hook or mocked interruption in the production operator.
    lock_key = 619027735
    blocker = case.pg.migrator.connect()
    child: subprocess.Popen[bytes] | None = None
    try:
        blocker.execute(text("SELECT pg_advisory_lock(:key)"), {"key": lock_key})
        blocker.commit()
        with case.pg.migrator.begin() as conn:
            conn.exec_driver_sql(
                f"""CREATE FUNCTION offerloom.test_pause_rotation()
            RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
            IF NEW.id='00000000-0000-0000-0000-000000000002'::uuid THEN
              PERFORM pg_advisory_xact_lock({lock_key});
            END IF; RETURN NEW; END $$;
            CREATE TRIGGER test_pause_rotation BEFORE UPDATE ON offerloom.encrypted_credentials
            FOR EACH ROW EXECUTE FUNCTION offerloom.test_pause_rotation();""",
                execution_options={"no_parameters": True},
            )
        child = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "offerloom.cli",
                "credential-rotate",
                "--config",
                str(case.config_file),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            with case.pg.migrator.begin() as conn:
                committed = conn.execute(
                    text("SELECT count(*) FROM offerloom.encrypted_credentials WHERE key_id=:key"),
                    {"key": case.keyring.active_key_id},
                ).scalar_one()
            if committed == 1:
                break
            if child.poll() is not None:
                pytest.fail("Rotation child exited before the real committed batch boundary")
            time.sleep(0.02)
        else:
            pytest.fail("Rotation did not reach the real partial-progress boundary")
        child.kill()
        stdout, stderr = child.communicate(timeout=10)
        assert child.returncode is not None and child.returncode < 0
        assert stdout == stderr == b""
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            child.communicate(timeout=10)
        blocker.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key})
        blocker.close()
        with case.pg.migrator.begin() as conn:
            conn.exec_driver_sql(
                """DROP TRIGGER IF EXISTS test_pause_rotation ON offerloom.encrypted_credentials;
            DROP FUNCTION IF EXISTS offerloom.test_pause_rotation();""",
                execution_options={"no_parameters": True},
            )
    assert [row["key_id"] for row in _rows(case)].count(case.keyring.active_key_id) == 1
    receipt = rotate_credentials(case.config)
    assert (receipt.verified, receipt.rotated, receipt.already_current, receipt.batches) == (
        3,
        2,
        1,
        2,
    )
    _assert_rotated(case)


def _manifest(case: RotationCase, keyring: Keyring) -> BackupManifest:
    return BackupManifest(
        format="offerloom-installation",
        format_version=1,
        created_at=datetime.now(UTC),
        source_namespace=case.config.namespace_id,
        source_owner_id=case.pg.owner_id,
        control_id=uuid4(),
        installation_auth_epoch=1,
        owner_auth_epoch=1,
        maintenance_epoch=max(1, case.config.maintenance_epoch),
        workspace_version=1,
        image_sha256="1" * 64,
        source_sha256="2" * 64,
        config_sha256="3" * 64,
        recipient_sha256="4" * 64,
        credential_active_key_id=keyring.active_key_id,
        credential_keys={
            key: hashlib.sha256(value).hexdigest() for key, value in keyring.keys.items()
        },
        postgres_version="18.6",
        dump_tool_version="native rebinding boundary, not a full archive drill",
        schema_versions={"25": "5" * 64},
        table_counts={"encrypted_credentials": 3},
        table_sha256={"encrypted_credentials": "6" * 64},
        dump_sha256="7" * 64,
        dump_byte_length=1,
        files=[BackupFile(path="database.dump", sha256="7" * 64, byte_length=1)],
    )


@pytest.mark.postgres
@pytest.mark.parametrize("rotated", [False, True])
def test_native_rebinding_preserves_legacy_or_origin_bound_version_and_retained_escrow(
    rotation_case: RotationCase, rotated: bool
) -> None:
    case = rotation_case
    if rotated:
        rotate_credentials(case.config)
    keys = case.keyring if rotated else case.old_keyring
    manifest = _manifest(case, keys)
    _escrow(manifest, keys)
    for record, envelope in case.old_envelopes.items():
        assert (
            decrypt(case.old_keyring, case.old_contexts[record], envelope)
            == case.plaintexts[record]
        )
    with pytest.raises(BackupError, match="keyring_mismatch"):
        _escrow(
            _manifest(case, case.old_keyring),
            Keyring(
                case.keyring.active_key_id,
                {case.keyring.active_key_id: case.keyring.keys[case.keyring.active_key_id]},
            ),
        )
    before = _rows(case)
    namespace, operation = uuid4(), uuid4()
    with (
        ExecutionGuard(case.config.blob_root, exclusive=True, create=False) as guard,
        case.pg.migrator.begin() as conn,
    ):
        usage_before = conn.execute(
            text(
                "SELECT coalesce(sum(encryption_count),0) FROM offerloom.credential_key_usage WHERE key_id=:key"
            ),
            {"key": keys.active_key_id},
        ).scalar_one()
        # Old rows inserted after migration are first accounted as retained
        # envelopes, exactly as SQL025 seeds existing installations.
        expected_base = max(usage_before, len(before))
        conn.info["execution_guard"] = guard
        try:
            assert _rebind_credentials(conn, manifest, keys, namespace) == 3
            _revoke_restored_authority(conn, namespace, operation)
        finally:
            conn.info.pop("execution_guard")
        usage_after = conn.execute(
            text("SELECT encryption_count FROM offerloom.credential_key_usage WHERE key_id=:key"),
            {"key": keys.active_key_id},
        ).scalar_one()
        assert usage_after == expected_base + 3
    after = _rows(case)
    for old, row in zip(before, after, strict=True):
        assert row["key_id"] == old["key_id"]
        assert row["aad_version"] == old["aad_version"] == (2 if rotated else 1)
        assert row["provider_origin"] == old["provider_origin"]
        assert row["nonce"] != old["nonce"] and row["version"] == old["version"] + 1
        context = EnvelopeContext(
            namespace,
            row["owner_id"],
            row["purpose"],
            row["id"],
            row["aad_version"],
            row["provider_origin"],
        )
        envelope = Envelope(
            row["key_id"], bytes(row["nonce"]), bytes(row["ciphertext"]), row["aad_version"]
        )
        assert decrypt(keys, context, envelope) == case.plaintexts[row["id"]]
        original_context = EnvelopeContext(
            case.config.namespace_id,
            row["owner_id"],
            row["purpose"],
            row["id"],
            row["aad_version"],
            row["provider_origin"],
        )
        with pytest.raises(InvalidTag):
            decrypt(keys, original_context, envelope)
    with case.pg.migrator.begin() as conn:
        assert (
            conn.execute(
                text("SELECT status FROM offerloom.external_accounts WHERE id=:id"),
                {"id": case.account_id},
            ).scalar_one()
            == "disconnected"
        )
        assert conn.execute(
            text("SELECT maintenance_active FROM offerloom.installation_state WHERE singleton")
        ).scalar_one()


@pytest.mark.postgres
def test_cli_uses_only_protected_config_and_reports_safe_counts(
    rotation_case: RotationCase, capsys: pytest.CaptureFixture[str]
) -> None:
    case = rotation_case
    assert main(["credential-rotate", "--config", str(case.config_file)]) == 0
    captured = capsys.readouterr()
    receipt = json.loads(captured.out)
    assert receipt["status"] == "completed" and receipt["rotated"] == 3
    assert captured.err == ""
    assert "synthetic" not in captured.out and "provider.example" not in captured.out
    case.config_file.chmod(0o644)
    assert main(["credential-rotate", "--config", str(case.config_file)]) == 2
    captured = capsys.readouterr()
    assert "permissions/type" in captured.err and "synthetic" not in captured.err
    assert main(["credential-rotate"]) == 2
    assert "configuration_required" in capsys.readouterr().err


def test_rotation_config_rejects_secret_fields_relative_paths_and_duplicate_bindings(
    tmp_path: Path,
) -> None:
    value = {
        "migration_dsn_file": str(tmp_path / "dsn"),
        "blob_root": str(tmp_path / "blobs"),
        "credential_keyring_file": str(tmp_path / "keys"),
        "namespace_id": str(uuid4()),
        "maintenance_epoch": 0,
        "previous_active_key_id": "old",
        "new_active_key_id": "new",
        "credential_bindings": [],
    }
    for change in (
        {"token": "never-accepted"},
        {"credential_keyring_file": "relative"},
        {"new_active_key_id": "old"},
    ):
        with pytest.raises(ValidationError):
            CredentialRotationConfiguration.model_validate_json(json.dumps({**value, **change}))
    binding = {
        "owner_id": str(uuid4()),
        "credential_id": str(uuid4()),
        "external_account_id": None,
        "provider_key": None,
        "purpose": "provider_token",
        "provider_origin": "https://provider.example",
    }
    with pytest.raises(ValidationError):
        CredentialRotationConfiguration.model_validate_json(
            json.dumps({**value, "credential_bindings": [binding, binding]})
        )


@pytest.mark.postgres
@pytest.mark.parametrize("failure", ["missing_binding", "wrong_schema", "exhausted_key"])
def test_rotation_rejects_incomplete_claims_schema_or_exhausted_key(
    rotation_case: RotationCase, failure: str
) -> None:
    case, config = rotation_case, rotation_case.config
    if failure == "missing_binding":
        config = config.model_copy(update={"credential_bindings": config.credential_bindings[:-1]})
    else:
        with case.pg.migrator.begin() as conn:
            if failure == "wrong_schema":
                conn.execute(
                    text(
                        "INSERT INTO offerloom.schema_migrations(version,source_sha256) VALUES(999,repeat('0',64))"
                    )
                )
            else:
                conn.execute(
                    text("""INSERT INTO offerloom.credential_key_usage(key_id,encryption_count)
                    VALUES(:key,1048576)"""),
                    {"key": case.keyring.active_key_id},
                )
    before = _rows(case)
    try:
        with pytest.raises(
            BackupError,
            match="credential_rotation_(exact_bindings|current_schema|key_usage_exhausted)",
        ):
            rotate_credentials(config)
        assert _rows(case) == before
    finally:
        if failure == "wrong_schema":
            with case.pg.migrator.begin() as conn:
                conn.execute(text("DELETE FROM offerloom.schema_migrations WHERE version=999"))


@pytest.mark.postgres
def test_real_concurrent_version_change_refuses_stale_batch_and_resumes_from_fresh_rows(
    rotation_case: RotationCase,
) -> None:
    case = rotation_case
    lock_key = 619027736
    blocker = case.pg.migrator.connect()
    child: subprocess.Popen[bytes] | None = None
    try:
        blocker.execute(text("SELECT pg_advisory_lock(:key)"), {"key": lock_key})
        blocker.commit()
        with case.pg.migrator.begin() as conn:
            conn.exec_driver_sql(
                f"""CREATE FUNCTION offerloom.test_version_pause()
            RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
            IF coalesce(current_setting('offerloom.test_mutator',true),'')<>'yes'
            AND EXISTS(SELECT 1 FROM offerloom.encrypted_credentials WHERE key_id='{case.keyring.active_key_id}')
            THEN PERFORM pg_advisory_xact_lock({lock_key}); END IF;
            RETURN NULL; END $$;
            CREATE TRIGGER test_version_pause BEFORE UPDATE ON offerloom.encrypted_credentials
            FOR EACH STATEMENT EXECUTE FUNCTION offerloom.test_version_pause();""",
                execution_options={"no_parameters": True},
            )
        child = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "offerloom.cli",
                "credential-rotate",
                "--config",
                str(case.config_file),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            with case.pg.migrator.begin() as conn:
                committed = conn.execute(
                    text("SELECT count(*) FROM offerloom.encrypted_credentials WHERE key_id=:key"),
                    {"key": case.keyring.active_key_id},
                ).scalar_one()
            if committed == 1:
                break
            if child.poll() is not None:
                pytest.fail("Version-check child exited before a real batch committed")
            time.sleep(0.02)
        else:
            pytest.fail("Version-check child did not reach the committed batch boundary")
        with case.pg.migrator.begin() as conn:
            conn.execute(text("SET LOCAL offerloom.test_mutator='yes'"))
            conn.execute(
                text("""UPDATE offerloom.encrypted_credentials SET version=version+1
                WHERE id='00000000-0000-0000-0000-000000000002'""")
            )
        blocker.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key})
        blocker.commit()
        stdout, stderr = child.communicate(timeout=30)
        assert child.returncode == 2 and stdout == b""
        assert b"credential_rotation_version_conflict" in stderr
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            child.communicate(timeout=10)
        blocker.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key})
        blocker.close()
        with case.pg.migrator.begin() as conn:
            conn.exec_driver_sql(
                """DROP TRIGGER IF EXISTS test_version_pause ON offerloom.encrypted_credentials;
            DROP FUNCTION IF EXISTS offerloom.test_version_pause();""",
                execution_options={"no_parameters": True},
            )
    receipt = rotate_credentials(case.config)
    assert (receipt.rotated, receipt.already_current) == (2, 1)
    for row in _rows(case):
        assert row["version"] == (3 if row["id"] == UUID(int=2) else 2)
        assert row["key_id"] == case.keyring.active_key_id
        assert (
            decrypt(
                case.keyring,
                EnvelopeContext(
                    case.config.namespace_id,
                    row["owner_id"],
                    row["purpose"],
                    row["id"],
                    2,
                    row["provider_origin"],
                ),
                Envelope(row["key_id"], bytes(row["nonce"]), bytes(row["ciphertext"]), 2),
            )
            == case.plaintexts[row["id"]]
        )
