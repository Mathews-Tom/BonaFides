# SPDX-License-Identifier: MIT OR Apache-2.0
"""Local operator commands. No token or password is accepted as an argument."""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from offerloom.backup_archive import BackupError
from offerloom.config import ConfigurationError, MigrationSettings, Settings
from offerloom.problems import Problem


def _password() -> str:
    if sys.stdin.isatty():
        first = getpass.getpass("New password: ")
        second = getpass.getpass("Confirm password: ")
    else:
        # Piped local input, never argv/environment/history. Preserve password spaces.
        first = sys.stdin.readline().removesuffix("\n").removesuffix("\r")
        second = sys.stdin.readline().removesuffix("\n").removesuffix("\r")
    if first != second:
        raise ValueError("Passwords do not match")
    return first


def _healthcheck() -> int:
    origin = os.environ.get("OFFERLOOM_ORIGIN", "")
    parsed = urlsplit(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigurationError("Missing/invalid OFFERLOOM_ORIGIN")
    request = Request("http://127.0.0.1:8000/health/ready", headers={"Host": parsed.netloc})
    try:
        # Healthchecks never use ambient proxy settings or leave this container.
        with build_opener(ProxyHandler({})).open(request, timeout=3) as response:
            return 0 if response.status == 200 else 1
    except (URLError, OSError):
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="offerloom")
    parser.add_argument(
        "command",
        choices=(
            "api",
            "worker",
            "lifecycle",
            "renderer",
            "parser",
            "source-gateway",
            "migrate",
            "healthcheck",
            "config-check",
            "init-bootstrap",
            "recover-password",
            "backup-create",
            "backup-status",
            "backup-restore",
            "backup-verify",
            "backup-schedule-run",
            "backup-schedule-resolve",
            "backup-open",
            "maintenance-inspect",
            "maintenance-recover",
        ),
    )
    parser.add_argument(
        "--config", type=Path, help="protected backup or offline maintenance configuration file"
    )
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "backup-open":
            from offerloom.installation_open import run as open_run

            if arguments.config is None:
                raise BackupError("backup_open_configuration_required")
            return open_run(arguments.config)
        if arguments.command.startswith("maintenance-"):
            from offerloom.maintenance_operator import run as maintenance_run

            if arguments.config is None:
                raise BackupError("maintenance_recovery_configuration_required")
            return maintenance_run(arguments.command, arguments.config)
        if arguments.command.startswith("backup-"):
            from offerloom.backup_operator import run

            if arguments.config is None:
                raise BackupError("backup_configuration_required")
            return run(arguments.command, arguments.config)
        if arguments.config is not None:
            raise ConfigurationError("--config is only valid for backup/maintenance commands")
        if arguments.command == "renderer":
            from offerloom.render_service import run_renderer

            run_renderer(Path(os.environ.get("OFFERLOOM_RENDERER_SPOOL", "/spool")))
            return 0
        if arguments.command == "parser":
            from offerloom.import_parser import run_parser

            spool = os.environ.get("OFFERLOOM_IMPORT_SPOOL")
            if not spool:
                raise ConfigurationError("Missing OFFERLOOM_IMPORT_SPOOL")
            run_parser(Path(spool))
            return 0
        if arguments.command == "source-gateway":
            from offerloom.source_gateway import serve

            serve()
            return 0
        if arguments.command == "migrate":
            from offerloom.workflows import migrate

            migrate(MigrationSettings.load())
            print("Schema migrations applied.")
            return 0
        if arguments.command == "healthcheck":
            return _healthcheck()
        settings = Settings.load()
        if arguments.command == "config-check":
            print(
                "Runtime configuration valid; secrets redacted. Database connectivity not checked."
            )
            return 0
        if arguments.command == "api":
            import uvicorn

            from offerloom.api import create_app

            uvicorn.run(
                create_app(settings),
                host="0.0.0.0",
                port=8000,
                access_log=False,
                proxy_headers=False,
                server_header=False,
            )
            return 0
        if arguments.command == "worker":
            from offerloom.worker import main as run_worker

            run_worker(settings)
            return 0
        if arguments.command == "lifecycle":
            from offerloom.db import Database
            from offerloom.portable_exports import LifecycleWorker

            db = Database(settings)
            try:
                LifecycleWorker(db, settings).run()
            finally:
                db.close()
            return 0
        from offerloom.auth import init_bootstrap, recover_password
        from offerloom.db import Database

        db = Database(settings)
        try:
            if arguments.command == "init-bootstrap":
                init_bootstrap(db, settings)
                print("Bootstrap verifier initialized for 15 minutes; use the local secret file.")
            else:
                recover_password(db, _password())
                print("Owner password recovered; existing sessions and approvals invalidated.")
        finally:
            db.close()
        return 0
    except (ConfigurationError, ValueError, Problem) as exc:
        # Never print exception repr/causes, which may contain connection inputs.
        if isinstance(exc, Problem):
            print(f"Operation refused: {exc.code}", file=sys.stderr)
        elif isinstance(exc, BackupError):
            print(f"Operation refused: {exc}", file=sys.stderr)
        elif isinstance(exc, ConfigurationError):
            print(str(exc), file=sys.stderr)
        else:
            print(
                "Operation refused: invalid operator input or unavailable setup state.",
                file=sys.stderr,
            )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
