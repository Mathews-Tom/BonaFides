# SPDX-License-Identifier: MIT OR Apache-2.0
from __future__ import annotations

import base64
import hashlib
import io
import json
import subprocess
import sys
import time
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4
from zipfile import ZipFile

import pytest
from sqlalchemy import text
from test_document_generation import _base, _document, _save
from test_identity import identity as identity
from test_import_parser import parser as parser
from test_installation_control import renderer_spool as renderer_spool
from test_sources import SourceContext
from test_sources import catalog as catalog
from test_suitability import _profile

from offerloom.commands import canonical_json
from offerloom.import_parser import ImportParserClient
from offerloom.portable_exports import LifecycleWorker, expire_exports
from offerloom.profiles import content_hash
from offerloom.storage import BlobStore


def _queue(context: SourceContext, key: str | None = None) -> dict[str, object]:
    profile = context.client.get("/api/v1/profile").json()
    result = context.client.post(
        "/api/v1/portable-exports", json={}, headers=context.headers(profile["version"], key)
    )
    assert result.status_code == 202, result.text
    return dict(result.json())


def _worker(context: SourceContext, parser: ImportParserClient, renderer: Path) -> LifecycleWorker:
    return LifecycleWorker(
        context.runtime,
        replace(context.identity.settings, import_spool=parser.spool, renderer_spool=renderer),
    )


@pytest.mark.postgres
def test_owner_can_download_nonempty_blocked_document_and_exact_evidence_bytes(
    catalog: SourceContext, parser: ImportParserClient, renderer_spool: Path
) -> None:
    document = _base(catalog, _profile(catalog))
    body = deepcopy(document["current_revision"]["body"])
    factual = next(
        block
        for section in body["sections"]
        for block in section["blocks"]
        if block["claim_kind"] == "factual"
    )
    factual["text"] = "Certified Kubernetes expert with invented qualifications."
    assert _save(catalog, document, body).status_code == 200
    document = _document(catalog, document["id"])
    assert document["current_revision"]["validation_issues"]
    evidence = {"text": "Original synthetic portable evidence", "source": "Owner supplied"}
    payload = b"Synthetic private evidence, byte-for-byte.\n"
    stored = BlobStore(catalog.identity.settings.blob_root).put_bytes(catalog.owner_id, payload)
    blob_id, evidence_id, revision_id = uuid4(), uuid4(), uuid4()
    with catalog.runtime.transaction() as conn:
        conn.execute(
            text("""INSERT INTO offerloom.blobs(owner_id,id,sha256,byte_length,media_type,storage_key)
            VALUES(:owner,:id,:sha,:length,'text/plain',:key)"""),
            {
                "owner": catalog.owner_id,
                "id": blob_id,
                "sha": stored.sha256,
                "length": stored.byte_length,
                "key": stored.storage_key,
            },
        )
        conn.execute(
            text("""INSERT INTO offerloom.evidence_records(owner_id,id,current_revision_id)
            VALUES(:owner,:id,:revision)"""),
            {"owner": catalog.owner_id, "id": evidence_id, "revision": revision_id},
        )
        conn.execute(
            text("""INSERT INTO offerloom.evidence_revisions
            (owner_id,id,evidence_id,revision_no,payload,blob_id,content_sha256)
            VALUES(:owner,:id,:evidence,1,CAST(:payload AS jsonb),:blob,:sha)"""),
            {
                "owner": catalog.owner_id,
                "id": revision_id,
                "evidence": evidence_id,
                "payload": canonical_json(evidence),
                "blob": blob_id,
                "sha": content_hash(evidence),
            },
        )
    key = uuid4().hex
    task = _queue(catalog, key)
    replay = _queue(catalog, key)
    assert replay["id"] == task["id"]
    assert catalog.client.get(f"/api/v1/portable-exports/{task['id']}/download").status_code == 404
    assert _worker(catalog, parser, renderer_spool).run_once()
    result = catalog.client.get(f"/api/v1/workflows/{task['id']}").json()
    assert result["state"] == "succeeded", result
    downloaded = catalog.client.get(f"/api/v1/portable-exports/{task['id']}/download")
    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"] == "application/zip"
    assert "no-store" in downloaded.headers["cache-control"].split(",")
    assert hashlib.sha256(downloaded.content).hexdigest() == result["result"]["archive_sha256"]
    # Independent ZIP/JSON reader and direct SQL, not the exporter or its decoder.
    with ZipFile(io.BytesIO(downloaded.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        records = manifest["records"]
        revision = next(
            row
            for row in records["document_revisions"]
            if row["id"] == document["current_revision"]["id"]
        )
        with catalog.runtime.transaction() as conn:
            original = (
                conn.execute(
                    text("""SELECT body,validation_issues,content_sha256 FROM offerloom.document_revisions
                WHERE owner_id=:owner AND id=:id"""),
                    {"owner": catalog.owner_id, "id": UUID(revision["id"])},
                )
                .mappings()
                .one()
            )
            assert revision["body"] == original["body"]
            assert revision["validation_issues"] == original["validation_issues"]
            assert revision["content_sha256"] == original["content_sha256"]
        blob = next(row for row in manifest["blob_manifest"] if row["blob_id"] == str(blob_id))
        assert archive.read(blob["archive_member"]) == payload
        assert hashlib.sha256(archive.read(blob["archive_member"])).hexdigest() == stored.sha256
        evidence_revision = next(
            row for row in records["evidence_revisions"] if row["id"] == str(revision_id)
        )
        assert evidence_revision["evidence_id"] == str(evidence_id)
        assert evidence_revision["blob_id"] == str(blob_id)
        assert (
            not {"sessions", "action_approvals", "portable_exports", "credentials"} & records.keys()
        )
    assert catalog.client.get("/api/v1/profile").status_code == 200
    listed = catalog.client.get("/api/v1/portable-exports").json()["items"]
    assert listed[0]["id"] == task["id"] and listed[0]["downloadable"] is True


@pytest.mark.postgres
def test_corruption_owner_binding_and_actual_expiry(
    catalog: SourceContext, parser: ImportParserClient, renderer_spool: Path
) -> None:
    task = _queue(catalog)
    worker = _worker(catalog, parser, renderer_spool)
    assert worker.run_once()
    path = (
        catalog.identity.settings.blob_root
        / "portable_exports"
        / str(catalog.owner_id)
        / (str(task["id"]) + ".zip")
    )
    path.write_bytes(b"not the original archive")
    download = f"/api/v1/portable-exports/{task['id']}/download"
    failed = catalog.client.get(download)
    assert failed.status_code == 409 and failed.json()["code"] == "portable_export_integrity_failed"
    assert catalog.client.get(f"/api/v1/portable-exports/{uuid4()}/download").status_code == 404
    with catalog.identity.admin.transaction() as conn:
        conn.execute(
            text("""UPDATE offerloom.portable_exports SET created_at=clock_timestamp()-interval '2 days',
            expires_at=clock_timestamp()-interval '1 day' WHERE owner_id=:owner AND workflow_run_id=:id"""),
            {"owner": catalog.owner_id, "id": UUID(str(task["id"]))},
        )
    assert catalog.client.get(download).status_code == 410
    assert path.exists(), "Metadata expiry is not proof of physical removal"
    assert expire_exports(catalog.runtime, worker.settings) == 1
    assert not path.exists()
    assert catalog.client.get(download).status_code == 410
    listed = catalog.client.get("/api/v1/portable-exports").json()["items"][0]
    assert listed["copy_state"] == "expired" and listed["downloadable"] is False


@pytest.mark.postgres
def test_missing_live_blob_fails_export_without_false_success_or_leaving_writers_closed(
    catalog: SourceContext, parser: ImportParserClient, renderer_spool: Path
) -> None:
    payload = b"Actual synthetic source blob removed before the export starts."
    stored = BlobStore(catalog.identity.settings.blob_root).put_bytes(catalog.owner_id, payload)
    blob_id, evidence_id, revision_id = uuid4(), uuid4(), uuid4()
    evidence = {"text": "Synthetic missing original", "source": "Owner supplied"}
    parameters = {
        "owner": catalog.owner_id,
        "blob": blob_id,
        "evidence": evidence_id,
        "revision": revision_id,
        "sha": stored.sha256,
        "length": stored.byte_length,
        "key": stored.storage_key,
        "payload": canonical_json(evidence),
        "content": content_hash(evidence),
    }
    with catalog.runtime.transaction() as conn:
        conn.execute(
            text("""INSERT INTO offerloom.blobs
            (owner_id,id,sha256,byte_length,media_type,storage_key)
            VALUES(:owner,:blob,:sha,:length,'text/plain',:key)"""),
            parameters,
        )
        conn.execute(
            text("""INSERT INTO offerloom.evidence_records
            (owner_id,id,current_revision_id) VALUES(:owner,:evidence,:revision)"""),
            parameters,
        )
        conn.execute(
            text("""INSERT INTO offerloom.evidence_revisions
            (owner_id,id,evidence_id,revision_no,payload,blob_id,content_sha256)
            VALUES(:owner,:revision,:evidence,1,CAST(:payload AS jsonb),:blob,:content)"""),
            parameters,
        )
    (catalog.identity.settings.blob_root / str(catalog.owner_id) / stored.storage_key).unlink()
    task = _queue(catalog)
    assert _worker(catalog, parser, renderer_spool).run_once()
    failed = catalog.client.get(f"/api/v1/workflows/{task['id']}").json()
    assert failed["state"] == "permanent_failure"
    assert failed["last_error_code"] == "portable_export_failed"
    assert catalog.client.get(f"/api/v1/portable-exports/{task['id']}/download").status_code == 404
    status = catalog.client.get("/api/v1/maintenance").json()
    assert status["maintenance_active"] is False
    assert catalog.client.get("/api/v1/profile").status_code == 200
    listed = catalog.client.get("/api/v1/portable-exports").json()["items"][0]
    assert listed["downloadable"] is False


@pytest.mark.postgres
def test_export_api_requires_owner_csrf_precondition_and_bounded_single_pending(
    catalog: SourceContext,
) -> None:
    path = "/api/v1/portable-exports"
    profile = catalog.client.get("/api/v1/profile").json()
    missing_csrf = catalog.client.post(
        path, json={}, headers={"Origin": catalog.identity.settings.origin}
    )
    assert missing_csrf.status_code == 403
    assert catalog.client.post(path, json={}, headers=catalog.headers()).status_code == 428
    stale = catalog.client.post(path, json={}, headers=catalog.headers(profile["version"] + 1))
    assert stale.status_code == 412
    invalid = catalog.client.post(
        path, json={"owner_id": str(uuid4())}, headers=catalog.headers(profile["version"])
    )
    assert invalid.status_code == 422
    task = _queue(catalog)
    duplicate = catalog.client.post(path, json={}, headers=catalog.headers(profile["version"]))
    assert duplicate.status_code == 429
    cookies = dict(catalog.client.cookies)
    catalog.client.cookies.clear()
    try:
        assert catalog.client.get(path).status_code == 401
        assert catalog.client.get(f"{path}/{task['id']}/download").status_code == 401
    finally:
        catalog.client.cookies.update(cookies)


@pytest.mark.postgres
def test_invalid_backup_configuration_does_not_stall_export_or_expired_copy_cleanup(
    catalog: SourceContext,
    parser: ImportParserClient,
    renderer_spool: Path,
    tmp_path: Path,
) -> None:
    document = _base(catalog, _profile(catalog))
    expired = _queue(catalog)
    assert _worker(catalog, parser, renderer_spool).run_once()
    expired_path = (
        catalog.identity.settings.blob_root
        / "portable_exports"
        / str(catalog.owner_id)
        / (str(expired["id"]) + ".zip")
    )
    with catalog.identity.admin.transaction() as conn:
        conn.execute(
            text("""UPDATE offerloom.portable_exports
            SET created_at=clock_timestamp()-interval '2 days',
                expires_at=clock_timestamp()-interval '1 day'
            WHERE owner_id=:owner AND workflow_run_id=:id"""),
            {"owner": catalog.owner_id, "id": UUID(str(expired["id"]))},
        )
    assert expired_path.exists()
    queued = _queue(catalog)
    malformed = tmp_path / "malformed-backup.json"
    malformed.touch(mode=0o600)
    malformed.write_text("{not valid JSON")
    keyring = catalog.identity.settings.keyring
    config_path = tmp_path / "lifecycle.json"
    config_path.touch(mode=0o600)
    config_path.write_text(
        json.dumps(
            {
                "database_dsn": catalog.identity.settings.database_dsn,
                "origin": catalog.identity.settings.origin,
                "blob_root": str(catalog.identity.settings.blob_root),
                "import_spool": str(parser.spool),
                "renderer_spool": str(renderer_spool),
                "capture": str(malformed),
                "keyring": {
                    "active_key_id": keyring.active_key_id,
                    "keys": {
                        name: base64.b64encode(value).decode()
                        for name, value in keyring.keys.items()
                    },
                },
            }
        )
    )
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            """import json,sys
from pathlib import Path
from offerloom.config import Settings
from offerloom.crypto import Keyring
from offerloom.db import Database
from offerloom.portable_exports import LifecycleWorker
config=json.loads(Path(sys.argv[1]).read_text())
settings=Settings(database_dsn=config['database_dsn'],origin=config['origin'],
    keyring=Keyring.parse(json.dumps(config['keyring'])),mode='benchmark',
    blob_root=Path(config['blob_root']),import_spool=Path(config['import_spool']),
    renderer_spool=Path(config['renderer_spool']),
    backup_capture_config_file=Path(config['capture']))
db=Database(settings)
try: LifecycleWorker(db,settings).run()
finally: db.close()
""",
            str(config_path),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 30
        result: dict[str, object] = {}
        while time.monotonic() < deadline:
            response = catalog.client.get(f"/api/v1/workflows/{queued['id']}")
            result = response.json()
            if result.get("state") in {"succeeded", "permanent_failure", "cancelled"}:
                break
            assert child.poll() is None, "Lifecycle process exited before finishing the export"
            time.sleep(0.05)
        assert result.get("state") == "succeeded", result
        downloaded = catalog.client.get(f"/api/v1/portable-exports/{queued['id']}/download")
        assert downloaded.status_code == 200
        with ZipFile(io.BytesIO(downloaded.content)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            revision = next(
                row
                for row in manifest["records"]["document_revisions"]
                if row["id"] == document["current_revision"]["id"]
            )
            assert revision["body"] == document["current_revision"]["body"]
        assert not expired_path.exists()
    finally:
        child.terminate()
        stdout, _ = child.communicate(timeout=15)
    events = [json.loads(line) for line in stdout.splitlines()]
    assert any(event.get("code") == "backup_configuration_invalid" for event in events)
