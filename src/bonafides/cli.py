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

from bonafides.backup_archive import BackupError
from bonafides.config import ConfigurationError, MigrationSettings, Settings
from bonafides.problems import Problem


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
    origin = os.environ.get("BONAFIDES_ORIGIN", "")
    parsed = urlsplit(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigurationError("Missing/invalid BONAFIDES_ORIGIN")
    request = Request("http://127.0.0.1:8000/health/ready", headers={"Host": parsed.netloc})
    try:
        # Healthchecks never use ambient proxy settings or leave this container.
        with build_opener(ProxyHandler({})).open(request, timeout=3) as response:
            return 0 if response.status == 200 else 1
    except (URLError, OSError):
        return 1


def main(argv: list[str] | None = None) -> int:
    arguments_list = sys.argv[1:] if argv is None else argv
    if arguments_list[:1] == ["claims"]:
        from bonafides.claim_coverage import main as claims_main

        return claims_main(arguments_list[1:])
    parser = argparse.ArgumentParser(
        prog="bonafides",
        epilog="bonafides claims coverage <file> reports the share of bullets in a local text "
        "file that structured claims represent",
    )
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
            "credential-rotate",
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="protected backup, credential or offline maintenance configuration",
    )
    arguments = parser.parse_args(arguments_list)
    try:
        if arguments.command == "credential-rotate":
            from bonafides.credential_rotation import run as rotation_run

            if arguments.config is None:
                raise BackupError("credential_rotation_configuration_required")
            return rotation_run(arguments.config)
        if arguments.command == "backup-open":
            from bonafides.installation_open import run as open_run

            if arguments.config is None:
                raise BackupError("backup_open_configuration_required")
            return open_run(arguments.config)
        if arguments.command.startswith("maintenance-"):
            from bonafides.maintenance_operator import run as maintenance_run

            if arguments.config is None:
                raise BackupError("maintenance_recovery_configuration_required")
            return maintenance_run(arguments.command, arguments.config)
        if arguments.command.startswith("backup-"):
            from bonafides.backup_operator import run

            if arguments.config is None:
                raise BackupError("backup_configuration_required")
            return run(arguments.command, arguments.config)
        if arguments.config is not None:
            raise ConfigurationError(
                "--config is only valid for backup/maintenance/credential commands"
            )
        if arguments.command == "renderer":
            from bonafides.render_service import run_renderer

            run_renderer(Path(os.environ.get("BONAFIDES_RENDERER_SPOOL", "/spool")))
            return 0
        if arguments.command == "parser":
            from bonafides.import_parser import run_parser

            spool = os.environ.get("BONAFIDES_IMPORT_SPOOL")
            if not spool:
                raise ConfigurationError("Missing BONAFIDES_IMPORT_SPOOL")
            run_parser(Path(spool))
            return 0
        if arguments.command == "source-gateway":
            from bonafides.source_gateway import serve

            serve()
            return 0
        if arguments.command == "migrate":
            from bonafides.workflows import migrate

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

            from bonafides.api import create_app

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
            from bonafides.worker import main as run_worker

            run_worker(settings)
            return 0
        if arguments.command == "lifecycle":
            from bonafides.db import Database
            from bonafides.portable_exports import LifecycleWorker

            db = Database(settings)
            try:
                LifecycleWorker(db, settings).run()
            finally:
                db.close()
            return 0
        from bonafides.auth import init_bootstrap, recover_password
        from bonafides.db import Database

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
