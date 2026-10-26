# SPDX-License-Identifier: MIT OR Apache-2.0
"""Explicitly open a verified restored target; never promote or reconnect it."""

from __future__ import annotations

import json
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Self
from uuid import UUID

from pydantic import ValidationError, model_validator
from sqlalchemy import Connection, text
from sqlalchemy.exc import SQLAlchemyError

from offerloom.backup_archive import BackupError
from offerloom.commands import canonical_json
from offerloom.config import ConfigurationError, read_secret
from offerloom.execution_guard import ExecutionGuard
from offerloom.maintenance_operator import MaintenanceRecoveryConfiguration, _database
from offerloom.spool_guard import pause_spool


class RestoredOpenConfiguration(MaintenanceRecoveryConfiguration):
    operation_id: UUID

    @model_validator(mode="after")
    def restored_identity(self) -> Self:
        if self.namespace_id is None or self.maintenance_epoch is None:
            raise ValueError("Opening requires the exact restored namespace and epoch")
        if self.control_id is not None:
            raise ValueError("Interrupted controls require maintenance-recover, not backup-open")
        return self


def _check_target(
    conn: Connection, config: RestoredOpenConfiguration, guard: ExecutionGuard
) -> None:
    state = conn.execute(
        text("""SELECT namespace_id,maintenance_epoch,maintenance_active,execution_guard_id
        FROM offerloom.installation_state WHERE singleton FOR UPDATE""")
    ).one()
    if (
        state.namespace_id != config.namespace_id
        or state.maintenance_epoch != config.maintenance_epoch
        or not state.maintenance_active
        or state.execution_guard_id != guard.verify()
    ):
        raise BackupError("backup_open_target_identity_mismatch")
    restored = conn.execute(
        text("""SELECT count(*) FROM offerloom.audit_events
        WHERE action='installation_restored' AND actor_kind='operator'
        AND request_id=:operation AND detail @> CAST(:binding AS jsonb)
        AND NOT EXISTS(SELECT 1 FROM offerloom.audit_events opened
          WHERE opened.action='restored_admission_opened'
          AND opened.request_id=:operation)"""),
        {
            "operation": config.operation_id,
            "binding": canonical_json(
                {
                    "maintenance_active": True,
                    "promoted": False,
                    "namespace_id": str(config.namespace_id),
                    "maintenance_epoch": config.maintenance_epoch,
                    "execution_guard_id": str(guard.identity),
                }
            ),
        },
    ).scalar_one()
    if restored != 1:
        raise BackupError("backup_open_restore_receipt_required")
    unsafe = conn.execute(
        text("""SELECT
        EXISTS(SELECT 1 FROM offerloom.installation_controls WHERE phase<>'released')
        OR EXISTS(SELECT 1 FROM offerloom.installation_activities)
        OR EXISTS(SELECT 1 FROM offerloom.provider_executions)
        OR EXISTS(SELECT 1 FROM offerloom.workflow_runs
          WHERE state NOT IN ('succeeded','cancelled','permanent_failure'))
        OR EXISTS(SELECT 1 FROM offerloom.sessions WHERE revoked_at IS NULL)
        OR EXISTS(SELECT 1 FROM offerloom.action_approvals WHERE revoked_at IS NULL)
        OR EXISTS(SELECT 1 FROM offerloom.hosted_egress_consents WHERE revoked_at IS NULL)
        OR EXISTS(SELECT 1 FROM offerloom.oauth_transactions)
        OR EXISTS(SELECT 1 FROM offerloom.source_configs WHERE enabled)
        OR EXISTS(SELECT 1 FROM offerloom.external_accounts
          WHERE status IN ('connected','revocation_pending'))
        OR EXISTS(SELECT 1 FROM offerloom.owner_settings WHERE privacy_mode<>'local_only')
        OR EXISTS(SELECT 1 FROM offerloom.backup_daily_runs
          WHERE finished_at IS NULL AND state IN ('running','created','copied'))""")
    ).scalar_one()
    if unsafe:
        raise BackupError("backup_open_restored_authority_not_revoked")


def open_restored_target(config: RestoredOpenConfiguration) -> dict[str, object]:
    """Require stopped actors, the native receipt and two live sealed daemons."""
    try:
        guard = ExecutionGuard(config.blob_root, exclusive=True, create=False)
    except BlockingIOError as error:
        raise BackupError("backup_open_stop_api_worker_lifecycle_backup_first") from error
    with guard:
        db = _database(config)
        db.execution_guard = guard
        try:
            with db.transaction() as conn:
                _check_target(conn, config, guard)
            deadline = time.monotonic() + 60
            with ExitStack() as stack:
                parser = stack.enter_context(
                    pause_spool(
                        config.parser_spool,
                        timeout=max(0.001, deadline - time.monotonic()),
                        recovery_guard=guard,
                    )
                )
                renderer = stack.enter_context(
                    pause_spool(
                        config.renderer_spool,
                        timeout=max(0.001, deadline - time.monotonic()),
                        recovery_guard=guard,
                    )
                )
                if time.monotonic() >= deadline:
                    raise BackupError("backup_open_daemon_drain_timeout")
                parser_nonce, renderer_nonce = parser.nonce, renderer.nonce
                # Native restore already committed its closed settlement. Keep both
                # spool locks and target exclusion until resumption and SQL release.
                parser.resume()
                renderer.resume()
                with db.transaction() as conn:
                    _check_target(conn, config, guard)
                    updated = conn.execute(
                        text("""UPDATE offerloom.installation_state SET maintenance_active=false
                        WHERE singleton AND maintenance_active AND namespace_id=:namespace
                        AND maintenance_epoch=:epoch AND execution_guard_id=:guard
                        RETURNING namespace_id"""),
                        {
                            "namespace": config.namespace_id,
                            "epoch": config.maintenance_epoch,
                            "guard": guard.verify(),
                        },
                    ).scalar_one()
                    if updated != config.namespace_id:
                        raise BackupError("backup_open_target_identity_mismatch")
                    conn.execute(
                        text("""INSERT INTO offerloom.audit_events
                        (owner_id,action,actor_kind,actor_id,request_id,entity_type,detail)
                        SELECT id,'restored_admission_opened','operator','host-local-recovery',
                        :operation,'installation',CAST(:detail AS jsonb) FROM offerloom.owners"""),
                        {
                            "operation": config.operation_id,
                            "detail": canonical_json(
                                {
                                    "namespace_id": str(config.namespace_id),
                                    "maintenance_epoch": config.maintenance_epoch,
                                    "execution_guard_id": str(guard.identity),
                                    "parser_nonce": parser_nonce,
                                    "renderer_nonce": renderer_nonce,
                                    "promoted": False,
                                }
                            ),
                        },
                    )
            return {
                "operation_id": str(config.operation_id),
                "namespace_id": str(config.namespace_id),
                "maintenance_epoch": config.maintenance_epoch,
                "maintenance_active": False,
                "connections": "disabled_reauthorization_and_review_required",
                "privacy_mode": "local_only",
                "promoted": False,
            }
        finally:
            db.execution_guard = None
            db.close()


def run(path: Path) -> int:
    try:
        config = RestoredOpenConfiguration.model_validate_json(
            read_secret(path, "restored_open_configuration")
        )
        result = open_restored_target(config)
    except (ValidationError, json.JSONDecodeError) as error:
        raise BackupError("backup_open_configuration_invalid") from error
    except BackupError:
        raise
    except SQLAlchemyError as error:
        raise BackupError("backup_open_database_authority_or_integrity_refused") from error
    except (ConfigurationError, OSError, ValueError) as error:
        raise BackupError("backup_open_exclusion_or_daemon_refused") from error
    print(canonical_json(result))
    return 0
