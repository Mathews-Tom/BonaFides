# SPDX-License-Identifier: MIT OR Apache-2.0
"""Real owner HTTP -> approval -> fenced SQL/filesystem execution.

Checkpoint metadata in these policy tests is migrator-attested fixture data,
NOT a native backup or restore drill. Separate physical recovery evidence is
required before declaring installation-recovery qualification.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any, BinaryIO
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError, OperationalError
from test_backup_configuration import _configuration
from test_document_generation import _base
from test_identity import _login
from test_identity import identity as identity
from test_import_parser import parser as parser
from test_installation_control import renderer_spool as renderer_spool
from test_portable_erasure import _attachment, _checkpoint
from test_portable_exports import _worker
from test_sources import SourceContext
from test_sources import catalog as catalog
from test_suitability import _profile
from test_tracking import _application

from bonafides.api import create_app
from bonafides.approvals import get_action_approval
from bonafides.backup_configuration import capture_runtime_pins
from bonafides.commands import lock_installation
from bonafides.config import Settings
from bonafides.import_parser import ImportParserClient
from bonafides.portable_data import export_owner_data, quarantine_records
from bonafides.portable_exports import LifecycleWorker
from bonafides.portable_previews import expire_previews
from bonafides.portable_uploads import load_candidate, retain_candidate
from bonafides.problems import Problem
from bonafides.storage import BlobStore
from bonafides.workflows import Claim, claim_control, reap


def _version(context: SourceContext) -> int:
    response = context.client.get("/api/v1/portable-previews")
    assert response.status_code == 200, response.text
    return int(response.json()["workspace_version"])


def _approve(context: SourceContext, preview: dict[str, Any], phrase: str | None) -> httpx.Response:
    approval = preview["approval"]
    http_client: httpx.Client = context.client
    return http_client.post(
        f"/api/v1/workflows/{preview['workflow_run_id']}/approval/approve",
        json={
            "payload_sha256": approval["payload_sha256"],
            "destination_sha256": approval["destination_sha256"],
            "revision_id": approval["revision_id"],
            "target_version": approval["target_version"],
            "confirmation_phrase": phrase,
            "confirmed": True,
        },
        headers=context.headers(preview["version"]),
    )


def _approved_application_deletion(
    context: SourceContext,
) -> tuple[dict[str, Any], dict[str, Any]]:
    selected = _application(context, title="Synthetic admission contention target")
    response = context.client.post(
        "/api/v1/portable-previews/deletion",
        json={
            "scope": "application",
            "application_id": selected["id"],
            "application_version": selected["version"],
        },
        headers=context.headers(_version(context)),
    )
    assert response.status_code == 201, response.text
    preview = response.json()
    approved = _approve(context, preview, preview["approval"]["confirmation_phrase"])
    assert approved.status_code == 200 and approved.json()["state"] == "pending", approved.text
    return selected, preview


@pytest.mark.postgres
def test_unavailable_admission_does_not_consume_the_sole_destructive_attempt(
    catalog: SourceContext, parser: ImportParserClient, renderer_spool: Path
) -> None:
    selected, preview = _approved_application_deletion(catalog)
    identifier = UUID(preview["workflow_run_id"])
    worker = _worker(catalog, parser, renderer_spool)
    with catalog.runtime.transaction() as reader:
        lock_installation(reader)
        for _ in range(2):
            assert worker.run_once() is False
            with catalog.runtime.transaction() as conn:
                row = (
                    conn.execute(
                        text("""SELECT state,attempt,max_attempts,lease_holder,lease_expires_at
                    FROM bonafides.workflow_runs WHERE id=:id"""),
                        {"id": identifier},
                    )
                    .mappings()
                    .one()
                )
                assert dict(row) == {
                    "state": "pending",
                    "attempt": 0,
                    "max_attempts": 1,
                    "lease_holder": None,
                    "lease_expires_at": None,
                }
                assert reap(conn) == 0
                assert conn.execute(
                    text("""SELECT consumed_at IS NULL FROM bonafides.action_approvals
                    WHERE workflow_run_id=:id"""),
                    {"id": identifier},
                ).scalar_one()
                assert (
                    conn.execute(
                        text("SELECT count(*) FROM bonafides.installation_controls")
                    ).scalar_one()
                    == 0
                )
                assert conn.execute(
                    text("SELECT EXISTS(SELECT 1 FROM bonafides.applications WHERE id=:id)"),
                    {"id": UUID(selected["id"])},
                ).scalar_one()
                assert (
                    conn.execute(
                        text("""SELECT count(*) FROM bonafides.workflow_attempt_events
                    WHERE workflow_run_id=:id AND event='claimed'"""),
                        {"id": identifier},
                    ).scalar_one()
                    == 0
                )
        assert get_action_approval(reader, catalog.owner_id, identifier) is not None
        assert worker.run_once() is False
    assert worker.run_once()
    with catalog.runtime.transaction() as conn:
        row = (
            conn.execute(
                text("SELECT state,attempt,max_attempts FROM bonafides.workflow_runs WHERE id=:id"),
                {"id": identifier},
            )
            .mappings()
            .one()
        )
        assert dict(row) == {"state": "succeeded", "attempt": 1, "max_attempts": 1}
        assert conn.execute(
            text("""SELECT consumed_at IS NOT NULL FROM bonafides.action_approvals
            WHERE workflow_run_id=:id"""),
            {"id": identifier},
        ).scalar_one()
        assert (
            conn.execute(
                text("SELECT count(*) FROM bonafides.applications WHERE id=:id"),
                {"id": UUID(selected["id"])},
            ).scalar_one()
            == 0
        )


@pytest.mark.postgres
@pytest.mark.parametrize("phase", ["preflight", "admission"])
@pytest.mark.parametrize("database_error", [False, True])
def test_admission_preserves_explicit_preflight_failure_and_database_errors(
    catalog: SourceContext,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    database_error: bool,
) -> None:
    from bonafides import portable_exports

    selected, preview = _approved_application_deletion(catalog)
    identifier = UUID(preview["workflow_run_id"])
    worker = LifecycleWorker(catalog.runtime, catalog.identity.settings)

    def reject(conn: Connection, task: Claim) -> None:
        if database_error:
            conn.execute(text("SET LOCAL statement_timeout='1ms'"))
            conn.execute(text("SELECT pg_sleep(1)"))
        raise Problem(409, "portable_preview_stale")

    if phase == "preflight":

        def reject_preflight(task: Claim) -> tuple[dict[str, object], None]:
            with catalog.runtime.transaction() as conn:
                reject(conn, task)
            raise AssertionError("preflight unexpectedly returned")

        monkeypatch.setattr(worker, "_destructive_preflight", reject_preflight)
    else:
        monkeypatch.setattr(portable_exports, "begin_control", reject)
    if database_error:
        with pytest.raises(OperationalError) as unavailable:
            worker.run_once()
        assert getattr(unavailable.value.orig, "sqlstate", None) == "57014"
    else:
        assert worker.run_once()
    with catalog.runtime.transaction() as conn:
        row = (
            conn.execute(
                text("""SELECT state,attempt,last_error_code,lease_holder
            FROM bonafides.workflow_runs WHERE id=:id"""),
                {"id": identifier},
            )
            .mappings()
            .one()
        )
        assert dict(row) == {
            "state": "pending" if database_error else "permanent_failure",
            "attempt": 0 if database_error else 1,
            "last_error_code": None if database_error else "portable_preview_stale",
            "lease_holder": None,
        }
        assert conn.execute(
            text("""SELECT consumed_at IS NULL FROM bonafides.action_approvals
            WHERE workflow_run_id=:id"""),
            {"id": identifier},
        ).scalar_one()
        assert (
            conn.execute(text("SELECT count(*) FROM bonafides.installation_controls")).scalar_one()
            == 0
        )
        assert conn.execute(
            text("SELECT EXISTS(SELECT 1 FROM bonafides.applications WHERE id=:id)"),
            {"id": UUID(selected["id"])},
        ).scalar_one()


@pytest.mark.postgres
def test_admission_does_not_steal_a_candidate_claimed_during_preflight(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, preview = _approved_application_deletion(catalog)
    identifier = UUID(preview["workflow_run_id"])
    worker = LifecycleWorker(catalog.runtime, catalog.identity.settings)
    preflight = worker._destructive_preflight
    other_holder = uuid4()

    def claimed_elsewhere(task: Claim) -> tuple[dict[str, object], None]:
        plan, checkpoint = preflight(task)
        assert checkpoint is None
        with catalog.runtime.transaction() as conn:
            other = claim_control(conn, other_holder, workflow_id=task.id)
            assert other is not None
        return plan, None

    monkeypatch.setattr(worker, "_destructive_preflight", claimed_elsewhere)
    assert worker.run_once() is False
    with catalog.runtime.transaction() as conn:
        row = (
            conn.execute(
                text("""SELECT state,attempt,max_attempts,lease_holder
            FROM bonafides.workflow_runs WHERE id=:id"""),
                {"id": identifier},
            )
            .mappings()
            .one()
        )
        assert dict(row) == {
            "state": "running",
            "attempt": 1,
            "max_attempts": 1,
            "lease_holder": other_holder,
        }
        assert conn.execute(
            text("""SELECT consumed_at IS NULL FROM bonafides.action_approvals
            WHERE workflow_run_id=:id"""),
            {"id": identifier},
        ).scalar_one()
        assert (
            conn.execute(text("SELECT count(*) FROM bonafides.installation_controls")).scalar_one()
            == 0
        )


@pytest.mark.postgres
@pytest.mark.parametrize("scope", ["application", "workspace"])
def test_typed_owner_preview_dispatches_exact_scope_and_revokes_old_sessions(
    catalog: SourceContext, parser: ImportParserClient, renderer_spool: Path, scope: str
) -> None:
    _base(catalog, _profile(catalog))
    selected = _application(catalog, title="Synthetic selected erasure")
    neighbor = _application(catalog, title="Synthetic retained neighbor")
    shared = _attachment(catalog, selected, b"Shared surviving owner bytes")
    _attachment(catalog, neighbor, b"Shared surviving owner bytes")
    private = _attachment(catalog, selected, b"Private selected erasure bytes")
    body: dict[str, object] = {"scope": scope}
    if scope == "application":
        body.update(application_id=selected["id"], application_version=selected["version"])
    version, key = _version(catalog), uuid4().hex
    response = catalog.client.post(
        "/api/v1/portable-previews/deletion", json=body, headers=catalog.headers(version, key)
    )
    assert response.status_code == 201, response.text
    preview = response.json()
    assert preview["plan"]["payload"]["categories"] == (
        ["applications"] if scope == "application" else ["all_workspace"]
    )
    replay = catalog.client.post(
        "/api/v1/portable-previews/deletion", json=body, headers=catalog.headers(version, key)
    )
    assert replay.status_code == 201 and replay.json()["id"] == preview["id"]
    refused = _approve(catalog, preview, "delete everything")
    assert refused.status_code == 422 and refused.json()["code"] == "confirmation_phrase_mismatch"
    with catalog.runtime.transaction() as conn:
        assert conn.execute(text("SELECT count(*) FROM bonafides.applications")).scalar_one() == 2
        assert (
            conn.execute(
                text("SELECT state FROM bonafides.workflow_runs WHERE id=:id"),
                {"id": UUID(preview["workflow_run_id"])},
            ).scalar_one()
            == "waiting_for_approval"
        )
    approved = _approve(catalog, preview, preview["approval"]["confirmation_phrase"])
    assert approved.status_code == 200 and approved.json()["state"] == "pending", approved.text
    assert _worker(catalog, parser, renderer_spool).run_once()
    with catalog.runtime.transaction() as conn:
        remaining = conn.execute(text("SELECT id FROM bonafides.applications")).scalars().all()
        assert remaining == ([UUID(neighbor["id"])] if scope == "application" else [])
        result = (
            conn.execute(
                text("SELECT state,result FROM bonafides.workflow_runs WHERE id=:id"),
                {"id": UUID(preview["workflow_run_id"])},
            )
            .mappings()
            .one()
        )
        assert result["state"] == "succeeded" and result["result"]["filesystem_complete"] is True
        assert (
            conn.execute(
                text("SELECT maintenance_active FROM bonafides.installation_state")
            ).scalar_one()
            is False
        )
        assert (
            conn.execute(
                text("SELECT clear_reason FROM bonafides.portable_previews WHERE id=:id"),
                {"id": UUID(preview["id"])},
            ).scalar_one()
            == "erased"
        )
    root = catalog.identity.settings.blob_root / str(catalog.owner_id)
    assert not (root / private.storage_key).exists()
    if scope == "application":
        assert (
            BlobStore(catalog.identity.settings.blob_root).read(
                catalog.owner_id, shared.storage_key, shared.sha256, shared.byte_length
            )
            == b"Shared surviving owner bytes"
        )
    else:
        assert not (root / shared.storage_key).exists()
    assert catalog.client.get("/api/v1/session").status_code == 401
    assert _login(catalog.identity, catalog.client).status_code == 201
    if scope == "application":
        assert catalog.client.get(f"/api/v1/applications/{neighbor['id']}").status_code == 200
    assert catalog.client.get(f"/api/v1/applications/{selected['id']}").status_code == 404


@pytest.mark.postgres
def test_preview_replay_requires_recent_authentication_and_stale_workspace_cannot_approve(
    catalog: SourceContext,
) -> None:
    version, key = _version(catalog), uuid4().hex
    body = {"scope": "workspace"}
    response = catalog.client.post(
        "/api/v1/portable-previews/deletion", json=body, headers=catalog.headers(version, key)
    )
    assert response.status_code == 201, response.text
    preview = response.json()
    with catalog.identity.admin.transaction() as conn:
        conn.execute(
            text(
                "UPDATE bonafides.sessions SET authenticated_at=clock_timestamp()-interval '6 minutes'"
            )
        )
    replay = catalog.client.post(
        "/api/v1/portable-previews/deletion", json=body, headers=catalog.headers(version, key)
    )
    assert replay.status_code == 403 and replay.json()["code"] == "reauthentication_required"
    assert _login(catalog.identity, catalog.client).status_code == 201
    catalog.csrf = catalog.client.get("/api/v1/session").json()["csrf_token"]
    # A different session cannot approve the original exact-bound intent.
    assert _approve(catalog, preview, preview["approval"]["confirmation_phrase"]).status_code == 409
    cancelled = catalog.client.post(
        f"/api/v1/portable-previews/{preview['id']}/cancel",
        json={},
        headers=catalog.headers(preview["version"]),
    )
    assert cancelled.status_code == 200, cancelled.text
    fresh = catalog.client.post(
        "/api/v1/portable-previews/deletion", json=body, headers=catalog.headers(_version(catalog))
    )
    assert fresh.status_code == 201, fresh.text
    with catalog.identity.admin.transaction() as conn:
        conn.execute(
            text("UPDATE bonafides.owner_settings SET workspace_version=workspace_version+1")
        )
    stale = _approve(catalog, fresh.json(), fresh.json()["approval"]["confirmation_phrase"])
    assert stale.status_code == 409 and stale.json()["code"] == "portable_preview_stale"
    with catalog.runtime.transaction() as conn:
        assert (
            conn.execute(text("SELECT count(*) FROM bonafides.installation_controls")).scalar_one()
            == 0
        )
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM bonafides.action_approvals WHERE approved_at IS NOT NULL"
                )
            ).scalar_one()
            == 0
        )


def _eligible_policy_checkpoint(
    context: SourceContext,
    parser: ImportParserClient,
    renderer: Path,
    root: Path,
) -> Settings:
    """Real completed drain, but migrator-attested fake backup bytes: policy ONLY."""
    identifier, _ = _checkpoint(context, parser.spool, renderer)
    settings, config = _configuration(context.identity, root)
    pins = capture_runtime_pins(config, settings)
    payload = b"Synthetic preview checkpoint POLICY fixture, not a native archive."
    archive = config.destination / (str(uuid4()) + ".cms")
    archive.write_bytes(payload)
    with context.identity.admin.transaction() as conn:
        conn.execute(
            text("""UPDATE bonafides.installation_backups SET image_sha256=:image,
            source_sha256=:source,config_sha256=:config,keyring_sha256=:keyring,
            recipient_sha256=:recipient,file_name=:file,cipher_sha256=:cipher,byte_length=:length
            WHERE id=:id"""),
            {
                "id": identifier,
                "image": pins["image_sha256"],
                "source": pins["source_sha256"],
                "config": pins["config_sha256"],
                "keyring": pins["keyring_sha256"],
                "recipient": pins["recipient_sha256"],
                "file": archive.name,
                "cipher": hashlib.sha256(payload).hexdigest(),
                "length": len(payload),
            },
        )
    return settings


@pytest.mark.postgres
def test_long_archive_validation_does_not_expire_the_new_preview(
    catalog: SourceContext,
    parser: ImportParserClient,
    renderer_spool: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import Connection

    from bonafides import portable_previews

    output = io.BytesIO()
    export_owner_data(catalog.runtime, catalog.identity.settings, catalog.owner_id, output)
    settings = _eligible_policy_checkpoint(catalog, parser, renderer_spool, tmp_path)
    validated = False
    execute = Connection.execute

    def validation(conn: Connection, archive: Any) -> None:
        nonlocal validated
        quarantine_records(conn, archive)
        validated = True

    def clock(
        conn: Connection, statement: Any, parameters: Any = None, *args: Any, **kwargs: Any
    ) -> Any:
        if str(statement) == "SELECT clock_timestamp()+make_interval(secs=>:ttl)":
            # Model a validation begun 601 seconds ago, completed at the real
            # database clock. No sleep or timing-sensitive database mutation.
            instant = datetime.now(UTC) - timedelta(seconds=0 if validated else 601)
            return execute(
                conn,
                text("SELECT CAST(:instant AS timestamptz)+make_interval(secs=>:ttl)"),
                {"instant": instant, "ttl": parameters["ttl"]},
            )
        return execute(conn, statement, parameters, *args, **kwargs)

    monkeypatch.setattr(portable_previews, "quarantine_records", validation)
    monkeypatch.setattr(Connection, "execute", clock)
    with TestClient(create_app(settings), base_url=settings.origin) as client:
        assert _login(catalog.identity, client).status_code == 201
        context = replace(
            catalog, client=client, csrf=client.get("/api/v1/session").json()["csrf_token"]
        )
        response = client.post(
            "/api/v1/portable-previews/import",
            content=output.getvalue(),
            headers={**context.headers(_version(context)), "Content-Type": "application/zip"},
        )
        assert response.status_code == 201, response.text
        expires = datetime.fromisoformat(response.json()["plan"]["expires_at"])
        assert expires > datetime.now(UTC) + timedelta(seconds=590)


@pytest.mark.postgres
def test_candidate_retention_cannot_consume_the_reviewed_preview_lifetime(
    catalog: SourceContext,
    parser: ImportParserClient,
    renderer_spool: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bonafides import portable_previews

    original = _application(catalog, title="Synthetic retained replacement")
    _attachment(catalog, original, b"Synthetic retained bytes\n" * 60_000)
    output = io.BytesIO()
    export_owner_data(catalog.runtime, catalog.identity.settings, catalog.owner_id, output)
    settings = _eligible_policy_checkpoint(catalog, parser, renderer_spool, tmp_path)
    finalize = text("""SELECT bonafides.finalize_portable_preview(
        owner_id,id,plan,preview_sha256,expires_at)
        FROM bonafides.portable_previews WHERE owner_id=:owner AND id=:id""")

    def slow_retention(conn: Connection, owner: UUID, identifier: UUID, stream: BinaryIO) -> None:
        # An incomplete candidate cannot become an approvable replacement.
        with pytest.raises(DBAPIError, match="finalization refused"), conn.begin_nested():
            conn.execute(finalize, {"owner": owner, "id": identifier})
        retain_candidate(conn, owner, identifier, stream)
        # Let PostgreSQL's actual provisional deadline pass, without a guessed
        # sleep or a long archive. The final reviewed lifetime remains 600s.
        conn.execute(
            text("""SELECT pg_sleep(GREATEST(0,
            extract(epoch FROM expires_at-clock_timestamp())+0.01))
            FROM bonafides.portable_previews WHERE owner_id=:owner AND id=:id"""),
            {"owner": owner, "id": identifier},
        )
        monkeypatch.setattr(portable_previews, "PREVIEW_TTL_SECONDS", 600)

    monkeypatch.setattr(portable_previews, "PREVIEW_TTL_SECONDS", 2)
    monkeypatch.setattr(portable_previews, "retain_candidate", slow_retention)
    with TestClient(create_app(settings), base_url=settings.origin) as client:
        assert _login(catalog.identity, client).status_code == 201
        context = replace(
            catalog, client=client, csrf=client.get("/api/v1/session").json()["csrf_token"]
        )
        response = client.post(
            "/api/v1/portable-previews/import",
            content=output.getvalue(),
            headers={**context.headers(_version(context)), "Content-Type": "application/zip"},
        )
        assert response.status_code == 201, response.text
        preview = response.json()
        approved = _approve(context, preview, preview["approval"]["confirmation_phrase"])
        assert approved.status_code == 200, approved.text
        # Even this narrow initializer cannot mutate a reviewed/approved plan.
        with (
            pytest.raises(DBAPIError, match="finalization refused"),
            catalog.runtime.transaction() as conn,
        ):
            conn.execute(finalize, {"owner": context.owner_id, "id": UUID(str(preview["id"]))})


@pytest.mark.postgres
@pytest.mark.parametrize("operation", ["dispatch", "cancel", "generic_cancel", "expire"])
def test_uploaded_nonempty_import_is_bound_and_candidate_bytes_are_released(
    catalog: SourceContext,
    parser: ImportParserClient,
    renderer_spool: Path,
    tmp_path: Path,
    operation: str,
) -> None:
    _base(catalog, _profile(catalog))
    original = _application(catalog, title="Synthetic archive application")
    original_bytes = b"Original archive attachment bytes\n" * 70_000
    attachment = _attachment(catalog, original, original_bytes)
    output = io.BytesIO()
    export_owner_data(catalog.runtime, catalog.identity.settings, catalog.owner_id, output)
    archive = output.getvalue()
    added_later = _application(catalog, title="Synthetic replaced live application")
    settings = _eligible_policy_checkpoint(catalog, parser, renderer_spool, tmp_path)
    with TestClient(create_app(settings), base_url=settings.origin) as client:
        assert _login(catalog.identity, client).status_code == 201
        context = replace(
            catalog, client=client, csrf=client.get("/api/v1/session").json()["csrf_token"]
        )
        headers = {**context.headers(_version(context)), "Content-Type": "application/zip"}
        wrong_owner = client.post(
            "/api/v1/portable-previews/import",
            content=archive,
            headers={**headers, "X-Source-Owner": str(uuid4())},
        )
        assert (
            wrong_owner.status_code == 422
            and wrong_owner.json()["code"] == "portable_owner_mismatch"
        )
        response = client.post("/api/v1/portable-previews/import", content=archive, headers=headers)
        assert response.status_code == 201, response.text
        preview = response.json()
        assert (
            preview["plan"]["payload"]["replacement_sha256"] == hashlib.sha256(archive).hexdigest()
        )
        assert preview["plan"]["source_owner_id"] == str(catalog.owner_id)
        with catalog.runtime.transaction() as conn:
            restored = io.BytesIO()
            load_candidate(conn, catalog.owner_id, UUID(preview["workflow_run_id"]), restored)
            assert restored.getvalue() == archive
        with pytest.raises(DBAPIError), catalog.runtime.transaction() as conn:
            conn.execute(text("UPDATE bonafides.portable_previews SET plan='{}'"))
        if operation == "dispatch":
            approved = _approve(context, preview, preview["approval"]["confirmation_phrase"])
            assert approved.status_code == 200, approved.text
            worker = LifecycleWorker(
                catalog.runtime,
                replace(settings, import_spool=parser.spool, renderer_spool=renderer_spool),
            )
            assert worker.run_once()
            assert client.get("/api/v1/session").status_code == 401
            with catalog.runtime.transaction() as conn:
                assert conn.execute(
                    text("SELECT id FROM bonafides.applications")
                ).scalars().all() == [UUID(original["id"])]
                assert (
                    conn.execute(
                        text("SELECT maintenance_active FROM bonafides.installation_state")
                    ).scalar_one()
                    is False
                )
            assert (
                BlobStore(settings.blob_root).read(
                    catalog.owner_id,
                    attachment.storage_key,
                    attachment.sha256,
                    attachment.byte_length,
                )
                == original_bytes
            )
            reason = "erased"
        elif operation in {"cancel", "generic_cancel"}:
            path = (
                f"/api/v1/portable-previews/{preview['id']}/cancel"
                if operation == "cancel"
                else f"/api/v1/workflows/{preview['workflow_run_id']}/cancel"
            )
            result = client.post(
                path,
                json={},
                headers=context.headers(preview["version"]),
            )
            assert result.status_code == 200, result.text
            reason = "cancelled"
        else:
            with catalog.identity.admin.transaction() as conn:
                conn.execute(
                    text(
                        "UPDATE bonafides.portable_previews SET expires_at=created_at+interval '1 microsecond'"
                    )
                )
            with catalog.runtime.transaction() as conn:
                assert expire_previews(conn, catalog.owner_id) == 1
            reason = "expired"
        with catalog.runtime.transaction() as conn:
            row = (
                conn.execute(
                    text(
                        "SELECT candidate_bytes,clear_reason FROM bonafides.portable_previews WHERE id=:id"
                    ),
                    {"id": UUID(preview["id"])},
                )
                .mappings()
                .one()
            )
            assert row["candidate_bytes"] is None and row["clear_reason"] == reason
            assert (
                conn.execute(
                    text("SELECT count(*) FROM bonafides.portable_preview_chunks")
                ).scalar_one()
                == 0
            )
            if operation != "dispatch":
                assert set(
                    conn.execute(text("SELECT id FROM bonafides.applications")).scalars()
                ) == {UUID(original["id"]), UUID(added_later["id"])}


@pytest.mark.postgres
def test_upload_backpressure_precedes_buffering_and_disconnect_releases_slot(
    catalog: SourceContext,
    parser: ImportParserClient,
    renderer_spool: Path,
    tmp_path: Path,
) -> None:
    settings = _eligible_policy_checkpoint(catalog, parser, renderer_spool, tmp_path)

    async def exercise() -> None:
        app = create_app(settings)
        entered = asyncio.Event()
        stalled = asyncio.Event()

        async def upload() -> AsyncIterator[bytes]:
            yield b"An unfinished owner upload"
            entered.set()
            await stalled.wait()

        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url=catalog.identity.settings.origin,
                cookies=catalog.client.cookies,
            ) as client:
                headers = {**catalog.headers(_version(catalog)), "Content-Type": "application/zip"}
                first = asyncio.create_task(
                    client.post(
                        "/api/v1/portable-previews/import", content=upload(), headers=headers
                    )
                )
                await asyncio.wait_for(entered.wait(), timeout=5)
                second = await client.post(
                    "/api/v1/portable-previews/import", content=b"Another upload", headers=headers
                )
                assert second.status_code == 429
                assert second.json()["code"] == "portable_upload_in_progress"
                assert second.headers["cache-control"] == "no-store"
                assert second.headers["content-security-policy"].startswith("default-src 'none'")
                first.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await first
                admitted = await client.post(
                    "/api/v1/portable-previews/import", content=b"Invalid ZIP", headers=headers
                )
                assert (
                    admitted.status_code == 422
                    and admitted.json()["code"] == "invalid_portable_archive"
                )
        finally:
            app.state.db.close()

    asyncio.run(exercise())
    with catalog.runtime.transaction() as conn:
        assert (
            conn.execute(text("SELECT count(*) FROM bonafides.portable_previews")).scalar_one() == 0
        )
        assert (
            conn.execute(
                text("SELECT count(*) FROM bonafides.installation_activities")
            ).scalar_one()
            == 0
        )
