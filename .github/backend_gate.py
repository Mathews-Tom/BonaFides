# SPDX-License-Identifier: MIT OR Apache-2.0
"""Run ordinary development gates against one disposable PostgreSQL test DB."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def call(
    arguments: list[str], *, env: dict[str, str] | None = None, data: str | None = None
) -> None:
    subprocess.run(arguments, check=True, env=env, input=data, text=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--lint-only", action="store_true", help="Run Ruff, format and mypy without a DB."
    )
    mode.add_argument(
        "--pytest-only", nargs="+", metavar="TEST_PATH", help="Run only these pytest paths."
    )
    arguments = parser.parse_args()
    checks = (
        ["uv", "run", "--frozen", "ruff", "check", "."],
        ["uv", "run", "--frozen", "ruff", "format", "--check", "."],
        ["uv", "run", "--frozen", "mypy", "--strict"],
    )
    if arguments.lint_only:
        for check in checks:
            call(check)
        return 0
    root = Path(".local/ci-backend")
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    env_file, secret_dir = root / "compose.env", root / "secrets"
    call(
        [
            sys.executable,
            "-m",
            "tools.dev_setup",
            "--directory",
            str(secret_dir),
            "--env-file",
            str(env_file),
            "--project",
            "bonafides-proof-ci-backend",
            "--benchmark",
        ]
    )
    compose = [
        "docker",
        "compose",
        "-f",
        "compose.yaml",
        "-f",
        ".github/compose.ci.yaml",
        "--env-file",
        str(env_file),
        "--project-name",
        "bonafides-proof-ci-backend",
    ]
    call([*compose, "up", "-d", "--wait", "db", "db-access"])
    call(
        [
            *compose,
            "exec",
            "-T",
            "db",
            "psql",
            "-U",
            "postgres",
            "-d",
            "bonafides",
            "-X",
            "-v",
            "ON_ERROR_STOP=1",
        ],
        data="CREATE DATABASE bonafides_test OWNER bonafides_migrator; REVOKE ALL ON DATABASE bonafides_test FROM PUBLIC; GRANT CONNECT ON DATABASE bonafides_test TO bonafides_runtime;\n",
    )
    env = dict(os.environ)
    env["BONAFIDES_TEST_PARSER_IMAGE"] = env.get("BONAFIDES_IMAGE", "bonafides:local")
    for role, name in (
        ("migrator", "BONAFIDES_TEST_MIGRATION_DSN"),
        ("runtime", "BONAFIDES_TEST_RUNTIME_DSN"),
    ):
        dsn = (
            (secret_dir / f"{role}_dsn")
            .read_text()
            .strip()
            .replace("@db:5432/bonafides", "@localhost:15432/bonafides_test")
        )
        env[name] = dsn
    env["BONAFIDES_TEST_DSN"] = env["BONAFIDES_TEST_RUNTIME_DSN"]
    if not arguments.pytest_only:
        for check in checks:
            call(check, env=env)
    call(["uv", "run", "--frozen", "pytest", *(arguments.pytest_only or [])], env=env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
