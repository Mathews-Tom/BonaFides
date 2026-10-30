# SPDX-License-Identifier: MIT OR Apache-2.0
"""Privileged local crash recovery; stop API/worker/lifecycle/backup actors first.

Keep both sealed spool daemons running. This command needs the migrator secret,
canonical blob volume and both actual spool volumes, never a runtime override.
"""

from __future__ import annotations

import json
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from bonafides.backup_archive import BackupError
from bonafides.commands import canonical_json
from bonafides.config import ConfigurationError, MigrationSettings, read_secret
from bonafides.db import SCHEMA_VERSION, Database
from bonafides.execution_guard import ExecutionGuard
from bonafides.installation_control import Control, InstallationMaintenance
from bonafides.portable_archive import ArchiveError
from bonafides.portable_erasure import abort_staged_files, purge_scope
from bonafides.problems import Problem
from bonafides.spool_guard import pause_spool


class MaintenanceRecoveryConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    migration_dsn_file: Path
    blob_root: Path
    parser_spool: Path
    renderer_spool: Path
    namespace_id: UUID | None = None
    maintenance_epoch: int | None = Field(default=None, ge=0)
    control_id: UUID | None = None
    control_holder: UUID | None = None
    control_fence: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_paths_and_control(self) -> Self:
        for value in (
            self.migration_dsn_file,
            self.blob_root,
            self.parser_spool,
            self.renderer_spool,
        ):
            if not value.is_absolute() or ".." in value.parts:
                raise ValueError("Recovery paths must be absolute and normalized")
        if self.parser_spool == self.renderer_spool:
            raise ValueError("Recovery requires two distinct spool roots")
        if (
            len({self.control_id is None, self.control_holder is None, self.control_fence is None})
            != 1
        ):
            raise ValueError("Supply the complete exact control identity or no control")
        return self


def _database(config: MaintenanceRecoveryConfiguration) -> Database:
    migration = MigrationSettings(
        read_secret(config.migration_dsn_file, "maintenance_migration_dsn")
    )
    if migration.migration_dsn is None:
        raise BackupError("maintenance_migration_dsn_required")
    db = Database(migration.migration_dsn)
    try:
        with db.transaction() as conn:
            if conn.execute(text("SELECT current_user")).scalar_one() != "bonafides_migrator":
                raise BackupError("maintenance_offline_operator_role_required")
            if (
                conn.execute(
                    text("SELECT max(version) FROM bonafides.schema_migrations")
                ).scalar_one()
                != SCHEMA_VERSION
            ):
                raise BackupError("maintenance_current_schema_required")
        return db
    except BaseException:
        db.close()
        raise


def inspect(config: MaintenanceRecoveryConfiguration) -> dict[str, object]:
    """Read operator identity only; never create a lock or write a session/barrier."""
    db = _database(config)
    try:
        with db.transaction() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            state = (
                conn.execute(
                    text("""SELECT namespace_id,maintenance_epoch,maintenance_active,
                execution_guard_id FROM bonafides.installation_state WHERE singleton""")
                )
                .mappings()
                .one()
            )
            control = (
                conn.execute(
                    text("""SELECT id,holder,fence,purpose,phase,execution_guard_id
                FROM bonafides.installation_controls WHERE phase<>'released'""")
                )
                .mappings()
                .one_or_none()
            )
            return {
                **{
                    key: str(value) if isinstance(value, UUID) else value
                    for key, value in state.items()
                },
                "control": {
                    key: str(value) if isinstance(value, UUID) else value
                    for key, value in control.items()
                }
                if control is not None
                else None,
                "active_calls": conn.execute(
                    text("SELECT count(*) FROM bonafides.installation_activities")
                ).scalar_one(),
                "unknown_providers": conn.execute(
                    text("SELECT count(*) FROM bonafides.provider_executions")
                ).scalar_one(),
                "required": "Stop API, worker, lifecycle and backup actors; keep both sealed daemons running.",
            }
    finally:
        db.close()


def recover(config: MaintenanceRecoveryConfiguration) -> dict[str, object]:
    if config.namespace_id is None or config.maintenance_epoch is None:
        raise BackupError("maintenance_exact_namespace_and_epoch_required")
    # create=False is essential: a new lock can never prove old actors stopped.
    try:
        guard = ExecutionGuard(config.blob_root, exclusive=True, create=False)
    except BlockingIOError as error:
        raise BackupError(
            "maintenance_live_actor_stop_api_worker_lifecycle_backup_first"
        ) from error
    with guard:
        db = _database(config)
        db.execution_guard = guard
        try:
            parameters = {
                "namespace": config.namespace_id,
                "epoch": config.maintenance_epoch,
                "control": config.control_id,
                "previous_holder": config.control_holder,
                "previous_fence": config.control_fence,
                "guard": guard.identity,
            }
            with db.transaction() as conn:
                conn.execute(
                    text("""SELECT bonafides.inspect_maintenance_recovery(
                    :namespace,:epoch,:control,:previous_holder,:previous_fence,:guard)"""),
                    parameters,
                )
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
                    raise BackupError("maintenance_recovery_drain_timeout")
                guard.verify()
                recovery_id, holder = uuid4(), uuid4()
                with db.transaction() as conn:
                    row = (
                        conn.execute(
                            text("""SELECT * FROM bonafides.begin_maintenance_recovery(
                        :recovery,:namespace,:epoch,:control,:previous_holder,:previous_fence,
                        :guard,:parser,:renderer,:holder)"""),
                            {
                                **parameters,
                                "recovery": recovery_id,
                                "holder": holder,
                                "parser": parser.nonce,
                                "renderer": renderer.nonce,
                            },
                        )
                        .mappings()
                        .one_or_none()
                    )
                if row is None:
                    parser.resume()
                    renderer.resume()
                    guard.verify()
                    with db.transaction() as conn:
                        conn.execute(
                            text("""SELECT bonafides.release_orphan_recovery(
                            :recovery,:namespace,:epoch,:guard)"""),
                            {**parameters, "recovery": recovery_id},
                        )
                    outcome = "ADMISSION_RECOVERED"
                else:
                    control = Control(
                        row["id"],
                        row["owner_id"],
                        row["holder"],
                        row["fence"],
                        row["namespace_id"],
                        row["maintenance_epoch"],
                        row["purpose"],
                    )
                    maintenance = InstallationMaintenance(
                        db, control, config.parser_spool, config.renderer_spool
                    )
                    with maintenance.quiescent(recovered_spools=(parser, renderer)):
                        with db.transaction() as conn:
                            journal = (
                                conn.execute(
                                    text("""SELECT scope,application_id FROM bonafides.portable_purges
                                WHERE control_id=:id AND owner_id=:owner"""),
                                    {"id": control.id, "owner": control.owner_id},
                                )
                                .mappings()
                                .one_or_none()
                            )
                        if journal is not None:
                            # Never call erase_scope/import again. SQL publication
                            # already committed; only the published journal resumes.
                            purge_scope(db, config, control)
                            maintenance.finish(
                                {
                                    "outcome": "PURGE_RESUMED",
                                    "scope": journal["scope"],
                                    "application_id": str(journal["application_id"])
                                    if journal["application_id"]
                                    else None,
                                    "filesystem_complete": True,
                                }
                            )
                            outcome = "PURGE_RESUMED"
                        else:
                            abort_staged_files(db, config, control)
                            maintenance.abort()
                            outcome = "ABORTED"
                return {
                    "recovery_id": str(recovery_id),
                    "namespace_id": str(config.namespace_id),
                    "control_id": str(config.control_id) if config.control_id else None,
                    "outcome": outcome,
                    "maintenance_active": False,
                }
        finally:
            db.execution_guard = None
            db.close()


def run(command: str, path: Path) -> int:
    try:
        config = MaintenanceRecoveryConfiguration.model_validate_json(
            read_secret(path, "maintenance_recovery_configuration")
        )
        result = inspect(config) if command == "maintenance-inspect" else recover(config)
    except (ValidationError, json.JSONDecodeError) as error:
        raise BackupError("maintenance_recovery_configuration_invalid") from error
    except BackupError:
        raise
    except SQLAlchemyError as error:
        raise BackupError("maintenance_recovery_refused_database_authority_or_integrity") from error
    except (ArchiveError, Problem, ConfigurationError, OSError, ValueError) as error:
        raise BackupError(
            "maintenance_recovery_refused_exclusion_daemon_or_file_integrity"
        ) from error
    print(canonical_json(result))
    return 0
