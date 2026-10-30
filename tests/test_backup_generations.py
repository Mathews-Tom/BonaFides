# SPDX-License-Identifier: MIT OR Apache-2.0
"""Actual PostgreSQL backup selection and separation of recovery authority."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_database import PGContext
from test_database import pg_context as pg_context

from bonafides.execution_guard import ExecutionGuard
from bonafides.installation_control import begin_control
from bonafides.workflows import claim, claim_control, enqueue


@pytest.mark.postgres
def test_operator_claim_does_not_steal_an_older_lifecycle_job(pg_context: PGContext) -> None:
    with pg_context.runtime.begin() as conn:
        older = enqueue(conn, pg_context.owner_id, "portable_export", {})
        selected = enqueue(conn, pg_context.owner_id, "backup", {})
        assert claim(conn, uuid4()) is None
        task = claim_control(conn, uuid4(), workflow_id=selected)
        assert task is not None and task.id == selected and task.kind == "backup"
        assert (
            conn.execute(
                text("SELECT state FROM bonafides.workflow_runs WHERE owner_id=:owner AND id=:id"),
                {"owner": pg_context.owner_id, "id": older},
            ).scalar_one()
            == "pending"
        )
        next_task = claim_control(conn, uuid4())
        assert next_task is not None and next_task.id == older


@pytest.mark.postgres
@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
def test_runtime_cannot_forge_or_remove_a_generation_drill(
    pg_context: PGContext, operation: str, tmp_path: Path
) -> None:
    with ExecutionGuard(tmp_path) as guard, pg_context.runtime.begin() as conn:
        conn.info["execution_guard"] = guard
        identifier = enqueue(conn, pg_context.owner_id, "backup", {})
        task = claim_control(conn, uuid4(), workflow_id=identifier)
        assert task is not None
        control = begin_control(conn, task)
    with pg_context.migrator.begin() as conn:
        # This is an ACL fixture, not an asserted physical backup or drain receipt.
        conn.execute(
            text("""INSERT INTO bonafides.installation_backups(
            id,owner_id,namespace_id,maintenance_epoch,workspace_version,
            installation_auth_epoch,owner_auth_epoch,schema_versions,
            image_sha256,source_sha256,config_sha256,keyring_sha256,recipient_sha256,
            file_name,cipher_sha256,manifest_sha256,byte_length,captured_at)
            SELECT :id,:owner,:namespace,:epoch,s.workspace_version,i.auth_epoch,o.auth_epoch,
            (SELECT jsonb_object_agg(version::text,source_sha256) FROM bonafides.schema_migrations),
            repeat('0',64),repeat('0',64),repeat('0',64),repeat('0',64),repeat('0',64),
            :file,repeat('0',64),repeat('0',64),1,clock_timestamp()
            FROM bonafides.owner_settings s JOIN bonafides.owners o ON o.id=s.owner_id
            CROSS JOIN bonafides.installation_state i WHERE s.owner_id=:owner AND i.singleton"""),
            {
                "id": identifier,
                "owner": pg_context.owner_id,
                "namespace": control.namespace_id,
                "epoch": control.maintenance_epoch,
                "file": str(uuid4()) + ".cms",
            },
        )
    query = (
        "UPDATE bonafides.installation_backups SET restore_verified_at=clock_timestamp(),"
        "restore_receipt_sha256=repeat('0',64),restore_target_namespace=:target WHERE id=:id"
        if operation == "UPDATE"
        else "DELETE FROM bonafides.installation_backups WHERE id=:id"
    )
    with pytest.raises(DBAPIError) as denied, pg_context.runtime.begin() as conn:
        conn.execute(text(query), {"id": identifier, "target": uuid4()})
    assert getattr(denied.value.orig, "sqlstate", None) == "42501"
    with pg_context.runtime.begin() as conn:
        assert conn.execute(
            text(
                "SELECT id,restore_verified_at,restore_receipt_sha256 FROM bonafides.installation_backups"
            ),
        ).one() == (identifier, None, None)
