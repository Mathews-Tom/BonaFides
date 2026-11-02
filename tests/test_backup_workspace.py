# SPDX-License-Identifier: MIT OR Apache-2.0
"""The workspace archive a backup carries: built from its snapshot, checked on restore."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from test_identity import identity as identity
from test_import_parser import parser as parser
from test_installation_control import renderer_spool as renderer_spool
from test_portable_erasure import _attachment
from test_portable_workspace import _import
from test_sources import SourceContext
from test_sources import catalog as catalog
from test_tracking import _application, _write

from bonafides.backup_archive import MAX_BUNDLE_BYTES, BackupError
from bonafides.import_parser import ImportParserClient
from bonafides.installation_backup import build_workspace
from bonafides.installation_control import Control, InstallationMaintenance, begin_control
from bonafides.installation_restore import verify_workspace
from bonafides.portable_archive import content_digest, decode_archive
from bonafides.portable_data import collect_controlled_owner_data
from bonafides.workflows import claim_control, enqueue


def _backup_control(context: SourceContext) -> Control:
    with context.runtime.transaction() as conn:
        identifier = enqueue(conn, context.owner_id, "backup", {})
        task = claim_control(conn, uuid4(), workflow_id=identifier)
        assert task is not None
        return begin_control(conn, task)


def _build(context: SourceContext, control: Control, staging: Path, budget: int) -> bytes:
    """Build exactly as capture does: one read-only repeatable-read snapshot."""
    # staged_workspace needs Linux /dev/fd directory paths; the archive test covers it.
    path = staging / "workspace.zip"
    with (
        context.runtime.engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as conn,
        conn.begin(),
        path.open("w+b") as workspace,
    ):
        conn.execute(text("SET TRANSACTION READ ONLY"))
        build_workspace(conn, context.identity.settings, control, workspace, budget)
        snapshot = collect_controlled_owner_data(conn, context.identity.settings, control)
        workspace.seek(0)
        with decode_archive(workspace, expected_source_owner=context.owner_id) as carried:
            assert content_digest(carried.manifest) == content_digest(snapshot.manifest)
        workspace.seek(0)
        payload = workspace.read()
    path.unlink()
    return payload


def _verify(context: SourceContext, path: Path) -> None:
    with context.runtime.transaction() as conn:
        verify_workspace(conn, path, context.owner_id, context.identity.settings.blob_root)


@pytest.mark.postgres
def test_backup_workspace_matches_its_snapshot_fails_closed_and_imports(
    catalog: SourceContext,
    parser: ImportParserClient,
    renderer_spool: Path,
    tmp_path: Path,
) -> None:
    application = _application(catalog, title="Synthetic backup workspace application")
    stored = _attachment(catalog, application, b"Synthetic backup workspace attachment")
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)
    control = _backup_control(catalog)
    maintenance = InstallationMaintenance(catalog.runtime, control, parser.spool, renderer_spool)
    with maintenance.quiescent():
        payload = _build(catalog, control, staging, MAX_BUNDLE_BYTES)
        with pytest.raises(BackupError, match="^backup_size_limit$"):
            _build(catalog, control, staging, stored.byte_length - 1)
        blob = catalog.identity.settings.blob_root / str(catalog.owner_id) / stored.storage_key
        content = blob.read_bytes()
        blob.unlink()
        try:
            with pytest.raises(BackupError, match="^backup_workspace_unavailable$"):
                _build(catalog, control, staging, MAX_BUNDLE_BYTES)
        finally:
            blob.write_bytes(content)
        maintenance.finish({"generation_id": str(control.id)})
    with decode_archive(payload, expected_source_owner=catalog.owner_id) as carried:
        assert [row["id"] for row in carried.manifest.records["applications"]] == [
            application["id"]
        ]
        assert [blob.sha256 for blob in carried.manifest.blob_manifest] == [stored.sha256]
    archive = tmp_path / "carried.zip"
    archive.write_bytes(payload)
    _verify(catalog, archive)
    garbage = tmp_path / "garbage.zip"
    garbage.write_bytes(b"not a workspace archive")
    with pytest.raises(BackupError, match="^restore_workspace_unreadable$"):
        _verify(catalog, garbage)
    _write(catalog, application, "notes", {"body": "Synthetic note added after the backup"})
    with pytest.raises(BackupError, match="^restore_workspace_mismatch$"):
        _verify(catalog, archive)
    _import(catalog, parser, renderer_spool, payload)
    with catalog.runtime.transaction() as conn:
        restored = conn.execute(text("SELECT id FROM bonafides.applications")).scalars().all()
        notes = conn.execute(text("SELECT count(*) FROM bonafides.application_notes")).scalar()
    assert restored == [UUID(application["id"])] and notes == 0
