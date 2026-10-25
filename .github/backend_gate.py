# SPDX-License-Identifier: MIT OR Apache-2.0
"""Run ordinary development gates against one disposable PostgreSQL test DB."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def call(
    arguments: list[str], *, env: dict[str, str] | None = None, data: str | None = None
) -> None:
    subprocess.run(arguments, check=True, env=env, input=data, text=True)


def main() -> int:
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
            "offerloom-proof-ci-backend",
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
        "offerloom-proof-ci-backend",
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
            "offerloom",
            "-X",
            "-v",
            "ON_ERROR_STOP=1",
        ],
        data="CREATE DATABASE offerloom_test OWNER offerloom_migrator; REVOKE ALL ON DATABASE offerloom_test FROM PUBLIC; GRANT CONNECT ON DATABASE offerloom_test TO offerloom_runtime;\n",
    )
    env = dict(os.environ)
    env["OFFERLOOM_TEST_PARSER_IMAGE"] = env.get("OFFERLOOM_IMAGE", "offerloom-m0:local")
    for role, name in (
        ("migrator", "OFFERLOOM_TEST_MIGRATION_DSN"),
        ("runtime", "OFFERLOOM_TEST_RUNTIME_DSN"),
    ):
        dsn = (
            (secret_dir / f"{role}_dsn")
            .read_text()
            .strip()
            .replace("@db:5432/offerloom", "@localhost:15432/offerloom_test")
        )
        env[name] = dsn
    env["OFFERLOOM_TEST_DSN"] = env["OFFERLOOM_TEST_RUNTIME_DSN"]
    for arguments in (
        ["uv", "run", "--frozen", "ruff", "check", "."],
        ["uv", "run", "--frozen", "ruff", "format", "--check", "."],
        ["uv", "run", "--frozen", "mypy", "--strict"],
        ["uv", "run", "--frozen", "pytest"],
    ):
        call(arguments, env=env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
