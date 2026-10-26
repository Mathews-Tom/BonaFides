# SPDX-License-Identifier: MIT OR Apache-2.0
"""Actual process death, PostgreSQL publication, sealed daemon and CLI recovery."""

from __future__ import annotations

import hashlib
import json
import os
import select
import subprocess
import sys
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from psycopg.errors import InsufficientPrivilege
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_identity import identity as identity
from test_import_parser import parser as parser
from test_installation_control import _begin
from test_installation_control import renderer_spool as renderer_spool
from test_portable_data import _export
from test_portable_erasure import _attachment, _deletion
from test_portable_import import _approved_import
from test_sources import SourceContext
from test_sources import catalog as catalog
from test_tracking import _application

from offerloom.approvals import consume_action_approval
from offerloom.db import Database
from offerloom.execution_guard import LOCK_NAME, ExecutionGuard
from offerloom.import_parser import ImportParserClient
from offerloom.installation_backup import table_integrity
from offerloom.installation_control import Control, begin_control
from offerloom.installation_restore import _revoke_restored_authority
from offerloom.maintenance import start_api_activity
from offerloom.maintenance_operator import MaintenanceRecoveryConfiguration, inspect, recover
from offerloom.portable_data import quarantine_records
from offerloom.problems import Problem
from offerloom.storage import BlobStore
from offerloom.workflows import claim_control

_CHILD = r'''
import base64, hashlib, json, sys
from pathlib import Path
from uuid import UUID, uuid4
from sqlalchemy import text
from offerloom.config import Settings
from offerloom.crypto import Keyring
from offerloom.db import Database
from offerloom.installation_control import Control, InstallationMaintenance
from offerloom.maintenance import start_api_activity
from offerloom.portable_erasure import erase_scope, orphan_inventory, purge_scope
from offerloom.storage import BlobStore

config = json.loads(Path(sys.argv[1]).read_text())
settings = Settings(
    database_dsn=config["runtime_dsn"], origin="https://localhost",
    keyring=Keyring.parse(json.dumps({"active_key_id":"synthetic", "keys":{
        "synthetic":base64.b64encode(bytes(32)).decode()}})),
    blob_root=Path(config["blob_root"]), import_spool=Path(config["parser_spool"]),
    renderer_spool=Path(config["renderer_spool"]), mode="benchmark",
)
db = Database(settings)
if config["mode"] == "activity":
    activity = start_api_activity(db, uuid4(), uuid4())
    print("READY", flush=True)
    sys.stdin.buffer.read(1)
    raise RuntimeError("test must kill actual actor")
c = config["control"]
control = Control(UUID(c["id"]),UUID(c["owner_id"]),UUID(c["holder"]),c["fence"],
                  UUID(c["namespace_id"]),c["maintenance_epoch"],c["purpose"])
maintenance = InstallationMaintenance(db, control, settings.import_spool, settings.renderer_spool)
with maintenance.quiescent():
    if config["mode"] == "quiescent":
        print("READY", flush=True)
        sys.stdin.buffer.read(1)
        raise RuntimeError("test must kill provider-free quiescent controller")
    if config["mode"] == "staged_import":
        for index, value in enumerate(config["staged"]):
            data = value.encode()
            digest, staging = hashlib.sha256(data).hexdigest(), uuid4()
            with db.transaction() as conn:
                conn.execute(text("""SELECT offerloom.register_portable_staging(
                    :id,:holder,:fence,:sha,:length,:staging)"""),
                    {**control.parameters, "sha":digest, "length":len(data),
                     "staging":".staging-"+staging.hex})
            if index == 0:
                BlobStore(settings.blob_root).put_bytes(control.owner_id,data,staging_id=staging)
            else:
                folder = settings.blob_root / str(control.owner_id) / digest[:2]
                folder.mkdir(parents=True,exist_ok=True)
                (folder / (".staging-"+staging.hex)).write_bytes(data[:4])
        print("READY", flush=True)
        sys.stdin.buffer.read(1)
        raise RuntimeError("test must kill staged import before domain publication")
    inventory = orphan_inventory(db, settings, control)
    if config["mode"] == "precommit":
        with db.transaction() as conn:
            erase_scope(conn, control, inventory)
            print("READY", flush=True)
            sys.stdin.buffer.read(1)
            raise RuntimeError("test must kill open domain transaction")
    with db.transaction() as conn:
        erase_scope(conn, control, inventory)
    if config["mode"] == "partial":
        # One real unlink occurs before the second item encounters a real
        # filesystem type mismatch. No injected forwarding/mocked purge engine.
        path = Path(config["break_path"])
        payload = path.read_bytes()
        path.unlink()
        path.mkdir()
        try:
            purge_scope(db, settings, control)
        except (ValueError, OSError):
            pass
        else:
            raise RuntimeError("expected actual filesystem integrity refusal")
    print("READY", flush=True)
    sys.stdin.buffer.read(1)
    raise RuntimeError("test must kill committed controller")
'''


def _private(path: Path, value: object) -> Path:
    with os.fdopen(
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600), "w"
    ) as stream:
        json.dump(value, stream)
    return path


def _configuration(
    context: SourceContext, tmp_path: Path, parser: Path, renderer: Path, control: Control | None
) -> tuple[MaintenanceRecoveryConfiguration, Path]:
    dsn_file = tmp_path / "operator-dsn"
    with os.fdopen(
        os.open(dsn_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600), "w"
    ) as stream:
        stream.write(context.identity.settings.migration_dsn or "")
    with context.identity.admin.transaction() as conn:
        state = conn.execute(
            text(
                "SELECT namespace_id,maintenance_epoch FROM offerloom.installation_state WHERE singleton"
            )
        ).one()
    config = MaintenanceRecoveryConfiguration(
        migration_dsn_file=dsn_file,
        blob_root=context.identity.settings.blob_root,
        parser_spool=parser,
        renderer_spool=renderer,
        namespace_id=state.namespace_id,
        maintenance_epoch=state.maintenance_epoch,
        control_id=control.id if control else None,
        control_holder=control.holder if control else None,
        control_fence=control.fence if control else None,
    )
    return config, _private(tmp_path / "recovery.json", config.model_dump(mode="json"))


def _stop_ordinary(context: SourceContext) -> None:
    context.runtime.close()
    cast(FastAPI, context.client.app).state.db.close()


def _actor(
    context: SourceContext,
    tmp_path: Path,
    parser: Path,
    renderer: Path,
    control: Control | None,
    mode: str,
    **extra: object,
) -> subprocess.Popen[bytes]:
    value = {
        "runtime_dsn": context.identity.settings.database_dsn,
        "blob_root": str(context.identity.settings.blob_root),
        "parser_spool": str(parser),
        "renderer_spool": str(renderer),
        "mode": mode,
        "control": {
            "id": str(control.id),
            "owner_id": str(control.owner_id),
            "holder": str(control.holder),
            "fence": control.fence,
            "namespace_id": str(control.namespace_id),
            "maintenance_epoch": control.maintenance_epoch,
            "purpose": control.purpose,
        }
        if control
        else None,
        **extra,
    }
    child = subprocess.Popen(
        [sys.executable, "-c", _CHILD, str(_private(tmp_path / "actor.json", value))],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert child.stdout is not None
    if not select.select([child.stdout], [], [], 20)[0] or child.stdout.readline() != b"READY\n":
        child.kill()
        _stdout, stderr = child.communicate(timeout=10)
        pytest.fail("Actual actor did not reach crash boundary: " + stderr.decode())
    return child


def _kill(child: subprocess.Popen[bytes]) -> None:
    child.kill()
    child.communicate(timeout=10)
    assert child.returncode is not None and child.returncode < 0


def _cli(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, "-m", "offerloom.cli", "maintenance-recover", "--config", str(path)],
        capture_output=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stderr.decode()
    return cast(dict[str, Any], json.loads(result.stdout))


@pytest.mark.postgres
def test_live_actor_refuses_then_real_death_recovers_orphan_with_both_sealed_daemons(
    catalog: SourceContext,
    parser: ImportParserClient,
    renderer_spool: Path,
    tmp_path: Path,
) -> None:
    config, path = _configuration(catalog, tmp_path, parser.spool, renderer_spool, None)
    _stop_ordinary(catalog)
    child = _actor(catalog, tmp_path, parser.spool, renderer_spool, None, "activity")
    try:
        with pytest.raises(ValueError, match="maintenance_live_actor"):
            recover(config)
        with catalog.identity.admin.transaction() as conn:
            assert (
                conn.execute(
                    text("SELECT count(*) FROM offerloom.installation_activities")
                ).scalar_one()
                == 1
            )
            assert not conn.execute(
                text("SELECT maintenance_active FROM offerloom.installation_state WHERE singleton")
            ).scalar_one()
    finally:
        _kill(child)
    result = _cli(path)
    assert result["outcome"] == "ADMISSION_RECOVERED" and result["maintenance_active"] is False
    assert (
        parser.parse(b"Actual parser resumes after orphan recovery", "text/plain").text
        == "Actual parser resumes after orphan recovery"
    )
    with catalog.identity.admin.transaction() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM offerloom.installation_activities")
            ).scalar_one()
            == 0
        )
        receipt = conn.execute(
            text(
                "SELECT process_proof,parser_nonce,renderer_nonce,cleared_activities,outcome FROM offerloom.maintenance_recoveries"
            )
        ).one()
        assert receipt.process_proof == "exclusive-kernel-flock/v1"
        assert receipt.parser_nonce != receipt.renderer_nonce
        assert receipt.cleared_activities == 1 and receipt.outcome == "ADMISSION_RECOVERED"


@pytest.mark.postgres
@pytest.mark.parametrize("workspace", [False, True])
@pytest.mark.parametrize("mode", ["precommit", "committed"])
def test_killed_destructive_actor_aborts_rollback_or_resumes_exact_published_scope(
    catalog: SourceContext,
    parser: ImportParserClient,
    renderer_spool: Path,
    tmp_path: Path,
    workspace: bool,
    mode: str,
) -> None:
    application = _application(catalog)
    data = b"Synthetic old attachment must survive rollback, not committed erasure."
    stored = _attachment(catalog, application, data)
    control = _deletion(catalog, None if workspace else application)
    config, path = _configuration(catalog, tmp_path, parser.spool, renderer_spool, control)
    _stop_ordinary(catalog)
    child = _actor(catalog, tmp_path, parser.spool, renderer_spool, control, mode)
    _kill(child)
    with catalog.identity.admin.transaction() as conn:
        committed = conn.execute(
            text("SELECT EXISTS(SELECT 1 FROM offerloom.portable_purges WHERE control_id=:id)"),
            {"id": control.id},
        ).scalar_one()
        assert committed is (mode == "committed")
    result = _cli(path)
    assert result["outcome"] == ("ABORTED" if mode == "precommit" else "PURGE_RESUMED")
    with catalog.identity.admin.transaction() as conn:
        exists = conn.execute(
            text(
                "SELECT EXISTS(SELECT 1 FROM offerloom.applications WHERE owner_id=:owner AND id=:id)"
            ),
            {"owner": catalog.owner_id, "id": application["id"]},
        ).scalar_one()
        assert exists is (mode == "precommit")
        state, receipt = conn.execute(
            text("SELECT state,result FROM offerloom.workflow_runs WHERE id=:id"),
            {"id": control.id},
        ).one()
        assert state == ("permanent_failure" if mode == "precommit" else "succeeded")
        assert receipt["outcome"] == result["outcome"]
        assert not conn.execute(
            text("SELECT maintenance_active FROM offerloom.installation_state WHERE singleton")
        ).scalar_one()
    blob_path = config.blob_root / str(catalog.owner_id) / stored.storage_key
    if mode == "precommit":
        assert blob_path.read_bytes() == data
    else:
        assert not blob_path.exists()
    assert not (parser.spool / ".maintenance.request").exists()
    assert not (renderer_spool / ".maintenance.request").exists()


@pytest.mark.postgres
@pytest.mark.parametrize(
    "fault",
    [
        "legacy_activity",
        "legacy_control",
        "guard_replaced",
        "namespace",
        "epoch",
        "fence",
        "holder",
        "unknown_provider",
    ],
)
def test_offline_recovery_refuses_unproven_or_foreign_authority_without_clearing(
    catalog: SourceContext,
    tmp_path: Path,
    fault: str,
    request: pytest.FixtureRequest,
) -> None:
    control = _begin(catalog)
    config, _path = _configuration(
        catalog,
        tmp_path,
        catalog.identity.settings.import_spool,
        catalog.identity.settings.renderer_spool,
        control,
    )
    with catalog.identity.admin.transaction() as conn:
        if fault == "legacy_activity":
            activity_id = uuid4()

            def remove_owned_activity() -> None:
                with catalog.identity.admin.transaction() as cleanup:
                    cleanup.execute(
                        text("DELETE FROM offerloom.installation_activities WHERE id=:id"),
                        {"id": activity_id},
                    )

            request.addfinalizer(remove_owned_activity)
            conn.execute(
                text("""INSERT INTO offerloom.installation_activities
                (id,namespace_id,maintenance_epoch,participant_id,kind,execution_id)
                VALUES(:id,:namespace,:epoch,:participant,'api',:execution)"""),
                {
                    "id": activity_id,
                    "namespace": control.namespace_id,
                    "epoch": control.maintenance_epoch,
                    "participant": uuid4(),
                    "execution": uuid4(),
                },
            )
        elif fault == "legacy_control":
            conn.execute(
                text(
                    "UPDATE offerloom.installation_controls SET execution_guard_id=NULL WHERE id=:id"
                ),
                {"id": control.id},
            )
        elif fault == "unknown_provider":
            model = conn.execute(
                text("""INSERT INTO offerloom.model_runs(owner_id,workflow_run_id,provider_key,model_id,task_kind,
                prompt_version,input_sha256,parameters,egress_class,reserved_tokens,reserved_cost_microunits,status)
                VALUES(:owner,:id,'synthetic','synthetic','synthetic','synthetic',repeat('0',64),'{}','local',30,3,'running') RETURNING id"""),
                {"owner": control.owner_id, "id": control.id},
            ).scalar_one()
            conn.execute(
                text("""INSERT INTO offerloom.provider_executions
                (id,namespace_id,maintenance_epoch,participant_id,owner_id,model_run_id,execution_guard_id)
                SELECT :id,namespace_id,maintenance_epoch,:participant,:owner,:model,execution_guard_id
                FROM offerloom.installation_state WHERE singleton"""),
                {"id": uuid4(), "participant": uuid4(), "owner": control.owner_id, "model": model},
            )
    _stop_ordinary(catalog)
    if fault == "guard_replaced":
        # Replacing this disposable test lock must not rebind the persisted proof.
        (config.blob_root / LOCK_NAME).unlink()
        with ExecutionGuard(config.blob_root):
            pass
    elif fault in {"namespace", "epoch", "fence", "holder"}:
        changes: dict[str, dict[str, UUID | int]] = {
            "namespace": {"namespace_id": uuid4()},
            "epoch": {"maintenance_epoch": cast(int, config.maintenance_epoch) + 1},
            "fence": {"control_fence": control.fence + 1},
            "holder": {"control_holder": uuid4()},
        }
        config = config.model_copy(update=changes[fault])
    with pytest.raises(DBAPIError):
        recover(config)
    with catalog.identity.admin.transaction() as conn:
        assert (
            conn.execute(
                text("SELECT phase FROM offerloom.installation_controls WHERE id=:id"),
                {"id": control.id},
            ).scalar_one()
            == "draining"
        )
        assert conn.execute(
            text("SELECT maintenance_active FROM offerloom.installation_state WHERE singleton")
        ).scalar_one()
        assert (
            conn.execute(text("SELECT count(*) FROM offerloom.maintenance_recoveries")).scalar_one()
            == 0
        )
    assert not (config.parser_spool / ".maintenance.request").exists()
    assert not (config.renderer_spool / ".maintenance.request").exists()


@pytest.mark.postgres
def test_runtime_cannot_invoke_old_begin_or_privileged_recovery_and_inspection_is_readonly(
    catalog: SourceContext,
    tmp_path: Path,
) -> None:
    with catalog.runtime.transaction() as conn:
        assert not conn.execute(
            text("""SELECT has_function_privilege(current_user,
            'offerloom.begin_installation_control(uuid,uuid,uuid,bigint,uuid)','EXECUTE')
            OR has_function_privilege(current_user,
            'offerloom.begin_maintenance_recovery(uuid,uuid,bigint,uuid,uuid,bigint,uuid,text,text,uuid)','EXECUTE')
            OR has_table_privilege(current_user,'offerloom.maintenance_recoveries','INSERT')""")
        ).scalar_one()
        counts, fingerprints = table_integrity(conn)
        assert counts["maintenance_recoveries"] == 0
        assert fingerprints["maintenance_recoveries"] == hashlib.sha256(b"").hexdigest()
    for statement in (
        "INSERT INTO offerloom.maintenance_recoveries DEFAULT VALUES",
        "UPDATE offerloom.maintenance_recoveries SET outcome='SUPERSEDED' WHERE false",
        "DELETE FROM offerloom.maintenance_recoveries WHERE false",
    ):
        with pytest.raises(DBAPIError) as denied, catalog.runtime.transaction() as conn:
            conn.execute(text(statement))
        assert isinstance(denied.value.orig, InsufficientPrivilege)
    config, _path = _configuration(
        catalog, tmp_path, tmp_path / "parser", tmp_path / "renderer", None
    )
    before = (config.blob_root / LOCK_NAME).read_bytes()
    status = inspect(config)
    assert status["maintenance_active"] is False and status["control"] is None
    assert (config.blob_root / LOCK_NAME).read_bytes() == before
    unguarded = type(catalog.runtime)(catalog.identity.settings.database_dsn)
    try:
        with pytest.raises(Problem, match="guarded_runtime_required"):
            start_api_activity(unguarded, uuid4(), uuid4())
    finally:
        unguarded.close()


@pytest.mark.postgres
def test_partial_committed_purge_refuses_corrupt_remaining_file_then_resumes_without_repeating_domain_commit(
    catalog: SourceContext,
    parser: ImportParserClient,
    renderer_spool: Path,
    tmp_path: Path,
) -> None:
    application = _application(catalog)
    payloads = [
        b"Synthetic first independently journaled file",
        b"Synthetic second independently journaled file",
    ]
    stored = [_attachment(catalog, application, value) for value in payloads]
    broken = max(zip(stored, payloads, strict=True), key=lambda pair: pair[0].storage_key)
    control = _deletion(catalog, application)
    config, path = _configuration(catalog, tmp_path, parser.spool, renderer_spool, control)
    broken_path = config.blob_root / str(catalog.owner_id) / broken[0].storage_key
    _stop_ordinary(catalog)
    child = _actor(
        catalog,
        tmp_path,
        parser.spool,
        renderer_spool,
        control,
        "partial",
        break_path=str(broken_path),
    )
    _kill(child)
    with catalog.identity.admin.transaction() as conn:
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM offerloom.portable_purge_items WHERE control_id=:id AND resolved_at IS NOT NULL"
                ),
                {"id": control.id},
            ).scalar_one()
            == 1
        )
        version = conn.execute(
            text("SELECT workspace_version FROM offerloom.owner_settings WHERE owner_id=:owner"),
            {"owner": catalog.owner_id},
        ).scalar_one()
    failed = subprocess.run(
        [sys.executable, "-m", "offerloom.cli", "maintenance-recover", "--config", str(path)],
        capture_output=True,
        timeout=90,
    )
    assert failed.returncode == 2 and b"file_integrity" in failed.stderr
    with catalog.identity.admin.transaction() as conn:
        assert conn.execute(
            text("SELECT maintenance_active FROM offerloom.installation_state WHERE singleton")
        ).scalar_one()
    # Repair only the synthetic fixture's corrupt remaining object, then require
    # the NEW exact adopted holder/fence (the first operator's old tuple is stale).
    broken_path.rmdir()
    BlobStore(config.blob_root).put_bytes(catalog.owner_id, broken[1])
    current = cast(dict[str, Any], inspect(config)["control"])
    fresh = config.model_copy(
        update={"control_holder": UUID(current["holder"]), "control_fence": current["fence"]}
    )
    _private(path, fresh.model_dump(mode="json"))
    assert _cli(path)["outcome"] == "PURGE_RESUMED"
    with catalog.identity.admin.transaction() as conn:
        assert (
            conn.execute(
                text(
                    "SELECT workspace_version FROM offerloom.owner_settings WHERE owner_id=:owner"
                ),
                {"owner": catalog.owner_id},
            ).scalar_one()
            == version
        )
        assert (
            conn.execute(
                text("SELECT count(*) FROM offerloom.portable_purges WHERE control_id=:id"),
                {"id": control.id},
            ).scalar_one()
            == 1
        )
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM offerloom.portable_purge_items WHERE control_id=:id AND resolved_at IS NULL"
                ),
                {"id": control.id},
            ).scalar_one()
            == 0
        )


@pytest.mark.postgres
def test_precommit_import_abort_removes_only_owned_new_cas_and_partial_staging(
    catalog: SourceContext,
    parser: ImportParserClient,
    renderer_spool: Path,
    tmp_path: Path,
) -> None:
    application = _application(catalog)
    live = _attachment(catalog, application, b"Synthetic live old workspace attachment")
    store = BlobStore(catalog.identity.settings.blob_root)
    unrelated = store.put_bytes(
        catalog.owner_id, b"Synthetic preexisting unreferenced orphan must remain"
    )
    prepared = _approved_import(
        catalog, b"Synthetic approved replacement, not yet decoded or committed"
    )
    with catalog.runtime.transaction() as conn:
        task = claim_control(conn, uuid4(), workflow_id=prepared.workflow_id)
        assert task is not None
        consume_action_approval(
            conn,
            task,
            action_kind="replace_workspace",
            destination=prepared.destination,
            payload=prepared.payload,
            target_version=cast(int, prepared.approval["target_version"]),
        )
        control = begin_control(conn, task)
    config, path = _configuration(catalog, tmp_path, parser.spool, renderer_spool, control)
    staged = [
        "Synthetic fresh staged CAS owned by interrupted import",
        "Synthetic partial write owned by interrupted import",
    ]
    _stop_ordinary(catalog)
    child = _actor(
        catalog, tmp_path, parser.spool, renderer_spool, control, "staged_import", staged=staged
    )
    _kill(child)
    with catalog.identity.admin.transaction() as conn:
        files = conn.execute(
            text(
                "SELECT storage_key,staging_name FROM offerloom.portable_staged_files WHERE control_id=:id"
            ),
            {"id": control.id},
        ).all()
        assert len(files) == 2
        assert not conn.execute(
            text("SELECT EXISTS(SELECT 1 FROM offerloom.portable_purges WHERE control_id=:id)"),
            {"id": control.id},
        ).scalar_one()
    assert _cli(path)["outcome"] == "ABORTED"
    for key, staging in files:
        assert not (config.blob_root / str(catalog.owner_id) / key).exists()
        assert not (config.blob_root / str(catalog.owner_id) / key[:2] / staging).exists()
    assert (
        store.read(catalog.owner_id, live.storage_key, live.sha256, live.byte_length)
        == b"Synthetic live old workspace attachment"
    )
    assert (
        store.read(catalog.owner_id, unrelated.storage_key, unrelated.sha256, unrelated.byte_length)
        == b"Synthetic preexisting unreferenced orphan must remain"
    )
    with catalog.identity.admin.transaction() as conn:
        assert conn.execute(
            text("SELECT EXISTS(SELECT 1 FROM offerloom.applications WHERE id=:id)"),
            {"id": application["id"]},
        ).scalar_one()


@pytest.mark.postgres
def test_expired_provider_free_crash_uses_positive_exclusion_not_lease_clearance(
    catalog: SourceContext,
    parser: ImportParserClient,
    renderer_spool: Path,
    tmp_path: Path,
) -> None:
    control = _begin(catalog)
    _config, path = _configuration(catalog, tmp_path, parser.spool, renderer_spool, control)
    _stop_ordinary(catalog)
    child = _actor(catalog, tmp_path, parser.spool, renderer_spool, control, "quiescent")
    _kill(child)
    with catalog.identity.admin.transaction() as conn:
        conn.execute(
            text("""UPDATE offerloom.installation_controls
            SET deadline_at=clock_timestamp()-interval '1 second',
            drain_deadline_at=clock_timestamp()-interval '2 seconds',
            lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=:id"""),
            {"id": control.id},
        )
    assert _cli(path)["outcome"] == "ABORTED"


@pytest.mark.postgres
def test_closed_native_target_without_current_namespace_control_is_never_promoted(
    catalog: SourceContext,
    tmp_path: Path,
) -> None:
    control = _begin(catalog)
    # Reproduce native restore's closed, newly namespaced, authority-revoked
    # target state; this fixture is not evidence of a native pg_restore drill.
    with catalog.identity.admin.transaction() as conn:
        conn.execute(
            text("""UPDATE offerloom.installation_controls
            SET phase='released',settled_at=clock_timestamp(),released_at=clock_timestamp(),
            result='{\"error_code\":\"installation_restored\"}' WHERE id=:id"""),
            {"id": control.id},
        )
        conn.execute(
            text("""UPDATE offerloom.installation_state SET namespace_id=:namespace,
            maintenance_epoch=maintenance_epoch+1,maintenance_active=true WHERE singleton"""),
            {"namespace": uuid4()},
        )
    config, _path = _configuration(
        catalog,
        tmp_path,
        catalog.identity.settings.import_spool,
        catalog.identity.settings.renderer_spool,
        None,
    )
    _stop_ordinary(catalog)
    with pytest.raises(DBAPIError):
        recover(config)
    with catalog.identity.admin.transaction() as conn:
        assert conn.execute(
            text("SELECT maintenance_active FROM offerloom.installation_state WHERE singleton")
        ).scalar_one()


@pytest.mark.postgres
def test_native_revocation_disables_manual_and_reviewed_sources_without_losing_history(
    catalog: SourceContext,
) -> None:
    application = _application(catalog)
    reviewed = catalog.create()
    with catalog.identity.admin.transaction() as conn:
        before = conn.execute(
            text("""SELECT id,source_kind,version,policy_version,current_policy_review_id
            FROM offerloom.source_configs
            WHERE owner_id=:owner AND enabled ORDER BY id"""),
            {"owner": catalog.owner_id},
        ).all()
    assert {row.source_kind for row in before} == {"manual", "greenhouse"}
    _begin(catalog)
    _stop_ordinary(catalog)
    with ExecutionGuard(catalog.identity.settings.blob_root, exclusive=True, create=False) as guard:
        with catalog.identity.admin.transaction() as conn:
            conn.info["execution_guard"] = guard
            try:
                _revoke_restored_authority(conn, uuid4(), uuid4())
            finally:
                conn.info.pop("execution_guard")
        with catalog.identity.admin.transaction() as conn:
            for original in before:
                current = conn.execute(
                    text("""SELECT enabled,version,policy_version,current_policy_review_id,
                    terms_reviewed_at FROM offerloom.source_configs WHERE id=:id"""),
                    {"id": original.id},
                ).one()
                assert not current.enabled
                assert current.version == original.version + 1
                assert current.policy_version == original.policy_version + 1
                assert current.current_policy_review_id is None
                assert current.terms_reviewed_at is None
            assert conn.execute(
                text("SELECT id FROM offerloom.source_policy_reviews WHERE source_config_id=:id"),
                {"id": UUID(reviewed["id"])},
            ).scalar_one() == next(
                row.current_policy_review_id for row in before if row.id == UUID(reviewed["id"])
            )
            assert conn.execute(
                text("""SELECT o.posting_id FROM offerloom.applications a
                JOIN offerloom.saved_opportunities o
                ON o.owner_id=a.owner_id AND o.id=a.opportunity_id WHERE a.id=:id"""),
                {"id": UUID(application["id"])},
            ).scalar_one() == UUID(application["posting_id"])
            assert conn.execute(
                text("SELECT maintenance_active FROM offerloom.installation_state WHERE singleton")
            ).scalar_one()


@pytest.mark.postgres
def test_restored_runtime_can_quarantine_its_nonempty_export_without_domain_writes(
    catalog: SourceContext, request: pytest.FixtureRequest
) -> None:
    _application(catalog)
    archive = _export(catalog)
    _begin(catalog)

    def restore_temporary_privilege() -> None:
        with catalog.identity.admin.transaction() as conn:
            conn.exec_driver_sql(
                """DO $$ BEGIN
            EXECUTE format('GRANT TEMPORARY ON DATABASE %I TO offerloom_runtime',current_database());
            END $$;""",
                execution_options={"no_parameters": True},
            )

    request.addfinalizer(restore_temporary_privilege)
    with catalog.identity.admin.transaction() as conn:
        conn.exec_driver_sql(
            """DO $$ BEGIN
        EXECUTE format('REVOKE TEMPORARY ON DATABASE %I FROM offerloom_runtime',current_database());
        END $$;""",
            execution_options={"no_parameters": True},
        )
    _stop_ordinary(catalog)
    with ExecutionGuard(catalog.identity.settings.blob_root, exclusive=True, create=False) as guard:
        with catalog.identity.admin.transaction() as conn:
            conn.info["execution_guard"] = guard
            try:
                _revoke_restored_authority(conn, uuid4(), uuid4())
            finally:
                conn.info.pop("execution_guard")
        with catalog.identity.admin.transaction() as conn:
            before = table_integrity(conn)
        # Offline role inspection, not an admitted API or target promotion.
        runtime = Database(catalog.identity.settings.database_dsn)
        try:
            with runtime.transaction() as conn:
                quarantine_records(conn, archive)
        finally:
            runtime.close()
        with catalog.identity.admin.transaction() as conn:
            assert table_integrity(conn) == before
