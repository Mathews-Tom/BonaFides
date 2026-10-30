# SPDX-License-Identifier: MIT OR Apache-2.0
from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Event
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from pypdf import PdfReader
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError
from test_database import PGContext
from test_database import pg_context as pg_context
from test_identity import identity as identity
from test_import_parser import parser as parser
from test_render_safety import _body
from test_sources import SourceContext
from test_sources import catalog as catalog

from bonafides.commands import canonical_json, lock_installation
from bonafides.execution_guard import ExecutionGuard
from bonafides.import_parser import ImportFailure, ImportParserClient
from bonafides.installation_control import (
    Control,
    InstallationMaintenance,
    assert_control,
    begin_control,
)
from bonafides.problems import Problem
from bonafides.render_service import RenderClient
from bonafides.spool_guard import SpoolBusy
from bonafides.storage import BlobStore
from bonafides.workflows import assert_fence, claim, claim_control, enqueue


@pytest.mark.postgres
@pytest.mark.parametrize("table", ["profile_revisions", "profiles"])
def test_runtime_cannot_erase_unreferenced_history_or_cascade_its_parent(
    pg_context: PGContext, table: str
) -> None:
    leaf_id = uuid4()
    with pg_context.migrator.begin() as conn:
        conn.execute(
            text("""INSERT INTO bonafides.profile_revisions(
            owner_id,id,profile_id,revision_no,snapshot,content_sha256,reason)
            VALUES(:owner,:id,:profile,2,'{}',repeat('0',64),'synthetic unreferenced leaf')"""),
            {"owner": pg_context.owner_id, "id": leaf_id, "profile": pg_context.profile_id},
        )
    target = leaf_id if table == "profile_revisions" else pg_context.profile_id
    with pytest.raises(DBAPIError) as denied, pg_context.runtime.begin() as conn:
        conn.execute(
            text(f"DELETE FROM bonafides.{table} WHERE owner_id=:owner AND id=:id"),
            {"owner": pg_context.owner_id, "id": target},
        )
    assert getattr(denied.value.orig, "sqlstate", None) == "42501"
    with pg_context.migrator.begin() as conn:
        assert (
            conn.execute(
                text("SELECT id FROM bonafides.profile_revisions WHERE owner_id=:owner AND id=:id"),
                {"owner": pg_context.owner_id, "id": leaf_id},
            ).scalar_one()
            == leaf_id
        )


def _queue_control(conn: Connection, owner: UUID, kind: str = "backup") -> UUID:
    identifier = uuid4()
    snapshot = canonical_json({"schema_version": 1})
    conn.execute(
        text("""INSERT INTO bonafides.workflow_runs(
        owner_id,id,kind,input,dedupe_key,request_sha256,deadline_at,maintenance_epoch)
        SELECT :owner,:id,:kind,CAST(:input AS jsonb),:dedupe,
        :sha,
        clock_timestamp()+interval '3 minutes',maintenance_epoch
        FROM bonafides.installation_state WHERE singleton"""),
        {
            "owner": owner,
            "id": identifier,
            "kind": kind,
            "dedupe": str(identifier),
            "input": snapshot,
            "sha": hashlib.sha256(snapshot.encode()).hexdigest(),
        },
    )
    return identifier


def _begin(context: SourceContext) -> Control:
    with context.runtime.transaction() as conn:
        identifier = _queue_control(conn, context.owner_id)
        task = claim_control(conn, uuid4())
        assert task is not None and task.id == identifier
        return begin_control(conn, task)


@pytest.fixture
def renderer_spool(tmp_path: Path) -> Iterator[Path]:
    spool = tmp_path / "maintenance-renderer"
    spool.mkdir(mode=0o700)
    container: str | None = None
    environment = dict(os.environ, BONAFIDES_RENDERER_SPOOL=str(spool))
    if sys.platform == "linux":
        arguments = [sys.executable, "-m", "bonafides.render_service", "serve"]
    else:
        image = os.environ.get("BONAFIDES_TEST_PARSER_IMAGE")
        if not image:
            pytest.fail("Maintenance renderer proof requires the current sealed Linux image.")
        container = "bonafides-maintenance-renderer-" + uuid4().hex
        arguments = [
            "docker",
            "run",
            "--pull=never",
            "--name",
            container,
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
            f"type=bind,src={spool},dst=/spool",
            "--env",
            "BONAFIDES_RENDERER_SPOOL=/spool",
            "--entrypoint",
            "python",
            image,
            "-m",
            "bonafides.render_service",
            "serve",
        ]
    process = subprocess.Popen(
        arguments,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 20
        while not (spool / ".renderer-lock").exists() and process.poll() is None:
            assert time.monotonic() < deadline, "Sealed renderer startup timed out"
            time.sleep(0.05)
        assert process.poll() is None
        yield spool
    finally:
        if container is not None:
            subprocess.run(
                ["docker", "stop", "--time", "5", container], check=True, capture_output=True
            )
        else:
            process.terminate()
        stdout, stderr = process.communicate(timeout=10)
        if container is not None:
            subprocess.run(["docker", "rm", container], check=True, capture_output=True)
        assert stdout == b"" and stderr == b""


@pytest.mark.postgres
def test_ordinary_executor_never_claims_lifecycle_work(pg_context: PGContext) -> None:
    with pg_context.runtime.begin() as conn:
        control_id = _queue_control(conn, pg_context.owner_id)
        ordinary_id = enqueue(conn, pg_context.owner_id, "document_generate", {"scenario": "clean"})
        task = claim(conn, uuid4())
        assert task is not None and task.id == ordinary_id
        assert claim(conn, uuid4()) is None
        control = claim_control(conn, uuid4())
        assert control is not None and control.id == control_id
        assert claim_control(conn, uuid4()) is None


@pytest.mark.postgres
@pytest.mark.parametrize("kind", ["privacy_delete", "portable_import", "restore_validate"])
def test_destructive_control_requires_a_consumed_exact_approval(
    pg_context: PGContext, kind: str, tmp_path: Path
) -> None:
    with pg_context.runtime.begin() as conn:
        _queue_control(conn, pg_context.owner_id, kind)
        task = claim_control(conn, uuid4())
        assert task is not None
    with (
        ExecutionGuard(tmp_path) as guard,
        pytest.raises(DBAPIError),
        pg_context.runtime.begin() as conn,
    ):
        conn.info["execution_guard"] = guard
        begin_control(conn, task)
    with pg_context.runtime.begin() as conn:
        assert lock_installation(conn) >= 0
        assert (
            conn.execute(text("SELECT count(*) FROM bonafides.installation_controls")).scalar_one()
            == 0
        )


@pytest.mark.postgres
@pytest.mark.parametrize("fault", ["holder", "fence", "epoch", "lease", "deadline"])
def test_control_cannot_revive_or_mutate_after_fence_or_deadline_loss(
    catalog: SourceContext, fault: str
) -> None:
    control = _begin(catalog)
    candidate = control
    with catalog.identity.admin.transaction() as conn:
        if fault == "holder":
            candidate = replace(control, holder=uuid4())
        elif fault == "fence":
            candidate = replace(control, fence=control.fence + 1)
        elif fault == "epoch":
            conn.execute(
                text(
                    "UPDATE bonafides.installation_state SET maintenance_epoch=maintenance_epoch+1"
                )
            )
        elif fault == "lease":
            conn.execute(
                text("""UPDATE bonafides.installation_controls
            SET lease_expires_at=clock_timestamp()-interval '1 second'""")
            )
        else:
            conn.execute(
                text("""UPDATE bonafides.installation_controls
            SET deadline_at=statement_timestamp()-interval '1 second',
            drain_deadline_at=statement_timestamp()-interval '1 second'""")
            )
    for operation in ("assert", "heartbeat", "quiesce", "release"):
        with pytest.raises(DBAPIError) as denied, catalog.runtime.transaction() as conn:
            conn.execute(
                text(f"SELECT bonafides.{operation}_installation_control(:id,:holder,:fence)"),
                candidate.parameters,
            )
        assert getattr(denied.value.orig, "sqlstate", None) == "55000"
    assert catalog.client.get("/api/v1/profile").status_code == 503


@pytest.mark.postgres
def test_runtime_cannot_forge_maintenance_state_or_control_receipt(catalog: SourceContext) -> None:
    control = _begin(catalog)
    for query in (
        "UPDATE bonafides.installation_state SET maintenance_active=false",
        "UPDATE bonafides.installation_state SET maintenance_epoch=maintenance_epoch+1",
        "UPDATE bonafides.installation_controls SET phase='quiescent'",
        "DELETE FROM bonafides.installation_controls",
    ):
        with pytest.raises(DBAPIError) as denied, catalog.runtime.transaction() as conn:
            conn.execute(text(query))
        assert getattr(denied.value.orig, "sqlstate", None) == "42501"
    with pytest.raises(DBAPIError), catalog.runtime.transaction() as conn:
        conn.execute(
            text("SELECT bonafides.release_installation_control(:id,:holder,:fence)"),
            control.parameters,
        )
    with catalog.runtime.transaction() as conn:
        assert_control(conn, control, quiescent=False)
        with pytest.raises(Problem) as maintenance:
            lock_installation(conn)
        assert maintenance.value.code == "installation_maintenance"


@pytest.mark.postgres
def test_coordinator_drains_actual_api_and_both_live_sealed_spools_before_release(
    catalog: SourceContext, parser: ImportParserClient, renderer_spool: Path
) -> None:
    entered, handler_release, quiesced, finish = Event(), Event(), Event(), Event()
    payload = b"Synthetic maintenance snapshot boundary.\n"

    def delayed_io() -> dict[str, str]:
        entered.set()
        assert handler_release.wait(15)
        blob = BlobStore(catalog.identity.settings.blob_root).put_bytes(catalog.owner_id, payload)
        return {"sha256": blob.sha256}

    cast(FastAPI, catalog.client.app).get("/api/v1/control-drain-probe")(delayed_io)
    parsed = parser.parse(payload, "text/plain")
    render = RenderClient(renderer_spool)
    pdf = render.render(_body("Synthetic selectable maintenance text"))
    with catalog.runtime.transaction() as conn:
        ordinary_id = enqueue(conn, catalog.owner_id, "document_generate", {"scenario": "clean"})
        ordinary = claim(conn, uuid4())
        assert ordinary is not None and ordinary.id == ordinary_id
    with ThreadPoolExecutor(max_workers=2) as pool:
        request = pool.submit(catalog.client.get, "/api/v1/control-drain-probe")
        try:
            assert entered.wait(10)
            control = _begin(catalog)

            def maintain() -> None:
                coordinator = InstallationMaintenance(
                    catalog.runtime, control, parser.spool, renderer_spool
                )
                with coordinator.quiescent():
                    quiesced.set()
                    assert finish.wait(15)
                    coordinator.finish(
                        {"operation": "synthetic_snapshot_boundary", "verified": True}
                    )

            running = pool.submit(maintain)
            assert not quiesced.wait(0.15), "Unfinished actual API I/O was treated as drained"
            assert catalog.client.get("/api/v1/profile").status_code == 503
            handler_release.set()
            assert request.result(timeout=10).status_code == 200
            assert quiesced.wait(10)
            with catalog.runtime.transaction() as conn:
                assert_control(conn, control)
            # A real controller heartbeat, not the original ordinary lease,
            # must preserve authority during a copy longer than this short lease.
            with catalog.identity.admin.transaction() as conn:
                conn.execute(
                    text("""UPDATE bonafides.installation_controls
                SET lease_expires_at=clock_timestamp()+interval '3 seconds' WHERE id=:id"""),
                    {"id": control.id},
                )
            time.sleep(3.5)
            with pytest.raises(DBAPIError), catalog.runtime.transaction() as conn:
                assert_fence(conn, ordinary)
            with pytest.raises(ImportFailure) as rejected:
                parser.parse(b"Cannot enter while paused", "text/plain")
            assert rejected.value.code == "import_parser_busy"
            with pytest.raises(SpoolBusy):
                render.render(_body("Cannot enter while paused"))
            assert catalog.client.get("/api/v1/profile").status_code == 503
            finish.set()
            running.result(timeout=10)
        finally:
            handler_release.set()
            finish.set()
    assert parser.parse(payload, "text/plain").text == parsed.text == payload.decode()
    after_pdf = render.render(_body("Synthetic selectable maintenance text"))
    before_text = " ".join(
        " ".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages).split()
    )
    after_text = " ".join(
        " ".join(page.extract_text() for page in PdfReader(io.BytesIO(after_pdf)).pages).split()
    )
    assert before_text == after_text == "Resume Evidence Synthetic selectable maintenance text"
    assert catalog.client.get("/api/v1/profile").status_code == 200
    with catalog.runtime.transaction() as conn:
        row = conn.execute(
            text("SELECT state,result FROM bonafides.workflow_runs WHERE id=:id"),
            {"id": control.id},
        ).one()
        assert row.state == "succeeded" and row.result == {
            "operation": "synthetic_snapshot_boundary",
            "verified": True,
        }
        resumed = claim(conn, uuid4())
        assert resumed is not None and resumed.id == ordinary.id and resumed.fence > ordinary.fence


@pytest.mark.postgres
def test_absent_daemon_does_not_turn_empty_spools_into_quiescence(catalog: SourceContext) -> None:
    control = _begin(catalog)
    with catalog.identity.admin.transaction() as conn:
        conn.execute(
            text("""UPDATE bonafides.installation_controls
        SET drain_deadline_at=clock_timestamp()+interval '0.2 seconds' WHERE id=:id"""),
            {"id": control.id},
        )
    settings = catalog.identity.settings
    with (
        pytest.raises(SpoolBusy),
        InstallationMaintenance(
            catalog.runtime, control, settings.import_spool, settings.renderer_spool
        ).quiescent(),
    ):
        pytest.fail("No daemon supplied an idle acknowledgement")
    assert catalog.client.get("/api/v1/profile").status_code == 503
    assert (settings.import_spool / ".maintenance.request").exists()
    with catalog.runtime.transaction() as conn:
        assert (
            conn.execute(
                text("SELECT phase FROM bonafides.installation_controls WHERE id=:id"),
                {"id": control.id},
            ).scalar_one()
            == "draining"
        )


@pytest.mark.postgres
def test_authenticated_status_remains_readonly_while_ordinary_api_is_fenced(
    catalog: SourceContext,
) -> None:
    with catalog.identity.admin.transaction() as conn:
        before = conn.execute(
            text("""UPDATE bonafides.sessions SET last_seen_at=now()-interval '2 minutes'
            WHERE owner_id=:owner RETURNING last_seen_at"""),
            {"owner": catalog.owner_id},
        ).scalar_one()
    control = _begin(catalog)
    for _ in range(2):
        response = catalog.client.get("/api/v1/maintenance")
        assert response.status_code == 200
        status = response.json()
        assert status["maintenance_active"] is True and status["recovery_required"] is False
        assert status["operation"]["workflow_id"] == str(control.id)
        assert status["operation"]["phase"] == "draining"
        assert status["drain"] == {"active_calls": 0, "provider_executions": 0}
        assert status["namespace_id"] == str(control.namespace_id)
        assert status["maintenance_epoch"] == control.maintenance_epoch
        with catalog.runtime.transaction() as conn:
            assert (
                status["workspace_version"]
                == conn.execute(
                    text(
                        "SELECT workspace_version FROM bonafides.owner_settings WHERE owner_id=:owner"
                    ),
                    {"owner": catalog.owner_id},
                ).scalar_one()
            )
        assert response.headers["cache-control"] == "no-store"
        assert catalog.csrf not in response.text and str(control.holder) not in response.text
    assert catalog.client.get("/api/v1/profile").status_code == 503
    assert (
        catalog.client.post("/api/v1/maintenance", json={}, headers=catalog.headers()).status_code
        == 503
    )
    assert catalog.client.get("/api/v1/maintenance/").status_code == 503
    bad_host = catalog.client.get("/api/v1/maintenance", headers={"Host": "attacker.invalid"})
    assert bad_host.status_code == 400 and bad_host.json()["code"] == "host_rejected"
    bad_origin = catalog.client.post(
        "/api/v1/maintenance",
        json={},
        headers={**catalog.headers(), "Origin": "https://attacker.invalid"},
    )
    assert bad_origin.status_code == 403 and bad_origin.json()["code"] == "origin_rejected"
    with catalog.identity.admin.transaction() as conn:
        after = conn.execute(
            text("SELECT last_seen_at FROM bonafides.sessions WHERE owner_id=:owner"),
            {"owner": catalog.owner_id},
        ).scalar_one()
        assert after == before
        assert (
            conn.execute(
                text("SELECT count(*) FROM bonafides.installation_activities")
            ).scalar_one()
            == 0
        )


@pytest.mark.postgres
@pytest.mark.parametrize(
    "invalid", ["missing", "revoked", "auth_epoch", "absolute_expiry", "idle_expiry"]
)
def test_maintenance_status_does_not_bypass_session_authentication(
    catalog: SourceContext, invalid: str
) -> None:
    _begin(catalog)
    with catalog.identity.admin.transaction() as conn:
        if invalid == "missing":
            catalog.client.cookies.clear()
        elif invalid == "revoked":
            conn.execute(text("UPDATE bonafides.sessions SET revoked_at=clock_timestamp()"))
        elif invalid == "auth_epoch":
            conn.execute(text("UPDATE bonafides.owners SET auth_epoch=auth_epoch+1"))
        elif invalid == "absolute_expiry":
            conn.execute(
                text("""UPDATE bonafides.sessions SET created_at=now()-interval '2 days',
            expires_at=now()-interval '1 second'""")
            )
        else:
            conn.execute(
                text("UPDATE bonafides.sessions SET last_seen_at=now()-interval '13 hours'")
            )
    response = catalog.client.get("/api/v1/maintenance")
    assert response.status_code == 401
    assert response.json()["code"] == "authentication_required"
    assert catalog.client.get("/api/v1/profile").status_code == 503


@pytest.mark.postgres
@pytest.mark.parametrize("lost", ["lease", "drain", "namespace", "workflow"])
def test_status_reports_required_recovery_instead_of_inventing_quiescence(
    catalog: SourceContext, lost: str
) -> None:
    control = _begin(catalog)
    with catalog.identity.admin.transaction() as conn:
        if lost == "lease":
            conn.execute(
                text("""UPDATE bonafides.installation_controls
            SET lease_expires_at=clock_timestamp()-interval '1 second'""")
            )
        elif lost == "drain":
            conn.execute(
                text("""UPDATE bonafides.installation_controls
            SET drain_deadline_at=clock_timestamp()-interval '1 second'""")
            )
        elif lost == "namespace":
            conn.execute(
                text("UPDATE bonafides.installation_state SET namespace_id=gen_random_uuid()")
            )
        else:
            conn.execute(
                text("UPDATE bonafides.workflow_runs SET fence=fence+1 WHERE id=:id"),
                {"id": control.id},
            )
    response = catalog.client.get("/api/v1/maintenance")
    assert response.status_code == 200
    status = response.json()
    assert status["maintenance_active"] is True and status["recovery_required"] is True
    assert status["operation"]["phase"] == "draining"
    assert catalog.client.get("/api/v1/profile").status_code == 503
