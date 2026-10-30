# SPDX-License-Identifier: MIT OR Apache-2.0
"""Real PostgreSQL/opening boundaries; native disaster recovery is a separate drill."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from test_identity import _login
from test_identity import identity as identity
from test_import_parser import parser as parser
from test_installation_control import _begin
from test_installation_control import renderer_spool as renderer_spool
from test_maintenance_recovery import _configuration, _stop_ordinary
from test_sources import SourceContext
from test_sources import catalog as catalog

from bonafides.backup_archive import BackupError
from bonafides.db import Database
from bonafides.execution_guard import ExecutionGuard
from bonafides.import_parser import ImportParserClient
from bonafides.installation_open import RestoredOpenConfiguration, open_restored_target
from bonafides.installation_restore import _revoke_restored_authority
from bonafides.maintenance_operator import recover
from bonafides.spool_guard import pause_spool


def _restored(
    context: SourceContext, tmp_path: Path, parser_spool: Path, renderer: Path
) -> RestoredOpenConfiguration:
    # Reproduce the real revocation transaction, not a fabricated open receipt.
    # This boundary fixture does not claim native dump/blob recovery evidence.
    _begin(context)
    _stop_ordinary(context)
    operation = uuid4()
    with (
        ExecutionGuard(context.identity.settings.blob_root, exclusive=True, create=False) as guard,
        context.identity.admin.transaction() as conn,
    ):
        conn.info["execution_guard"] = guard
        try:
            _revoke_restored_authority(conn, uuid4(), operation)
        finally:
            conn.info.pop("execution_guard")
    config, _ = _configuration(context, tmp_path, parser_spool, renderer, None)
    return RestoredOpenConfiguration(**config.model_dump(), operation_id=operation)


@pytest.mark.postgres
@pytest.mark.parametrize(
    "changed", ["namespace", "epoch", "operation", "prior_operation", "authority"]
)
def test_open_refuses_wrong_target_receipt_or_unrevoked_authority(
    catalog: SourceContext, tmp_path: Path, changed: str
) -> None:
    config = _restored(
        catalog,
        tmp_path,
        catalog.identity.settings.import_spool,
        catalog.identity.settings.renderer_spool,
    )
    if changed == "namespace":
        config = config.model_copy(update={"namespace_id": uuid4()})
    elif changed == "epoch":
        assert config.maintenance_epoch is not None
        config = config.model_copy(update={"maintenance_epoch": config.maintenance_epoch + 1})
    elif changed == "operation":
        config = config.model_copy(update={"operation_id": uuid4()})
    elif changed == "prior_operation":
        # A genuine historical restore receipt is not authority for a later target.
        namespace = uuid4()
        with (
            ExecutionGuard(
                catalog.identity.settings.blob_root, exclusive=True, create=False
            ) as guard,
            catalog.identity.admin.transaction() as conn,
        ):
            conn.info["execution_guard"] = guard
            try:
                _revoke_restored_authority(conn, namespace, uuid4())
                epoch = conn.execute(
                    text(
                        "SELECT maintenance_epoch FROM bonafides.installation_state WHERE singleton"
                    )
                ).scalar_one()
            finally:
                conn.info.pop("execution_guard")
        config = config.model_copy(update={"namespace_id": namespace, "maintenance_epoch": epoch})
    else:
        with catalog.identity.admin.transaction() as conn:
            assert (
                conn.execute(
                    text(
                        "UPDATE bonafides.sessions SET revoked_at=NULL WHERE revoked_at IS NOT NULL"
                    )
                ).rowcount
                > 0
            )
    with pytest.raises(
        BackupError, match="backup_open_(target_identity|restore_receipt|restored_authority)"
    ):
        open_restored_target(config)
    with catalog.identity.admin.transaction() as conn:
        assert conn.execute(
            text("SELECT maintenance_active FROM bonafides.installation_state WHERE singleton")
        ).scalar_one()
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM bonafides.audit_events WHERE action='restored_admission_opened'"
                )
            ).scalar_one()
            == 0
        )


@pytest.mark.postgres
def test_explicit_open_requires_stopped_actors_and_resumes_revoked_local_owner(
    catalog: SourceContext,
    parser: ImportParserClient,
    renderer_spool: Path,
    tmp_path: Path,
) -> None:
    config = _restored(catalog, tmp_path, parser.spool, renderer_spool)
    runtime_path = tmp_path / "runtime-cannot-open"
    runtime_path.touch(mode=0o600)
    runtime_path.write_text(catalog.identity.settings.database_dsn)
    runtime_config = config.model_copy(update={"migration_dsn_file": runtime_path})
    with pytest.raises(BackupError, match="maintenance_offline_operator_role_required"):
        open_restored_target(runtime_config)
    actor = Database(catalog.identity.settings)
    try:
        with pytest.raises(BackupError, match="backup_open_stop_api_worker"):
            open_restored_target(config)
    finally:
        actor.close()
    result = open_restored_target(config)
    assert result["namespace_id"] == str(config.namespace_id)
    assert result["maintenance_active"] is False and result["promoted"] is False
    assert parser.parse(b"Resumed after explicit restored-target opening", "text/plain").text == (
        "Resumed after explicit restored-target opening"
    )
    with catalog.identity.client() as client:
        login = _login(catalog.identity, client)
        assert login.status_code == 201
        before = client.get("/api/v1/profile").json()
        snapshot = {
            **before["current_revision"]["snapshot"],
            "display_name": "Restored synthetic owner",
        }
        saved = client.put(
            "/api/v1/profile",
            json=snapshot,
            headers={
                "Origin": catalog.identity.settings.origin,
                "X-CSRF-Token": login.json()["csrf_token"],
                "If-Match": f'"{before["version"]}"',
                "Idempotency-Key": uuid4().hex,
            },
        )
        assert saved.status_code == 200
        reloaded = client.get("/api/v1/profile").json()
        assert reloaded["version"] == before["version"] + 1
        assert reloaded["current_revision"]["snapshot"]["display_name"] == (
            "Restored synthetic owner"
        )
    with catalog.identity.admin.transaction() as conn:
        audit = conn.execute(
            text("""SELECT detail FROM bonafides.audit_events
            WHERE action='restored_admission_opened' AND request_id=:operation"""),
            {"operation": config.operation_id},
        ).scalar_one()
        assert audit["namespace_id"] == str(config.namespace_id)
        assert audit["parser_nonce"] != audit["renderer_nonce"]
        assert (
            conn.execute(
                text("SELECT count(*) FROM bonafides.source_configs WHERE enabled")
            ).scalar_one()
            == 0
        )


@pytest.mark.postgres
def test_consumed_restore_receipt_cannot_open_an_interrupted_orphan_recovery(
    catalog: SourceContext,
    parser: ImportParserClient,
    renderer_spool: Path,
    tmp_path: Path,
) -> None:
    config = _restored(catalog, tmp_path, parser.spool, renderer_spool)
    with catalog.identity.admin.transaction() as conn:
        expected_revision = conn.execute(
            text("SELECT current_revision_id FROM bonafides.profiles WHERE owner_id=:owner"),
            {"owner": catalog.owner_id},
        ).scalar_one()
    open_restored_target(config)
    recovery_id = uuid4()
    # Commit the real offline recovery admission with two fresh daemon ACKs,
    # then leave before its release step, retaining the actual pause requests.
    with (
        ExecutionGuard(config.blob_root, exclusive=True, create=False) as guard,
        pause_spool(parser.spool, recovery_guard=guard) as paused_parser,
        pause_spool(renderer_spool, recovery_guard=guard) as paused_renderer,
        catalog.identity.admin.transaction() as conn,
    ):
        conn.info["execution_guard"] = guard
        try:
            conn.execute(
                text("""SELECT * FROM bonafides.begin_maintenance_recovery(
                :recovery,:namespace,:epoch,NULL,NULL,NULL,:guard,:parser,:renderer,:holder)"""),
                {
                    "recovery": recovery_id,
                    "namespace": config.namespace_id,
                    "epoch": config.maintenance_epoch,
                    "guard": guard.identity,
                    "parser": paused_parser.nonce,
                    "renderer": paused_renderer.nonce,
                    "holder": uuid4(),
                },
            ).all()
        finally:
            conn.info.pop("execution_guard")
    with pytest.raises(BackupError, match="backup_open_restore_receipt_required"):
        open_restored_target(config)
    with catalog.identity.admin.transaction() as conn:
        assert conn.execute(
            text("SELECT maintenance_active FROM bonafides.installation_state WHERE singleton")
        ).scalar_one()
        assert (
            conn.execute(
                text("SELECT outcome FROM bonafides.maintenance_recoveries WHERE id=:id"),
                {"id": recovery_id},
            ).scalar_one()
            is None
        )
        assert (
            conn.execute(
                text("""SELECT count(*) FROM bonafides.audit_events
            WHERE action='restored_admission_opened' AND request_id=:operation"""),
                {"operation": config.operation_id},
            ).scalar_one()
            == 1
        )
    result = recover(config)
    assert result["outcome"] == "ADMISSION_RECOVERED"
    with catalog.identity.client() as client:
        assert _login(catalog.identity, client).status_code == 201
        assert client.get("/api/v1/profile").json()["current_revision"]["id"] == str(
            expected_revision
        )
