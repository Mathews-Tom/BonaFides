# SPDX-License-Identifier: MIT OR Apache-2.0
"""Generate local-only private credentials; never contact or modify a database."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import secrets
from pathlib import Path


def private_write(path: Path, content: str) -> None:
    """Exclusive creation prevents accidental credential rotation or symlink overwrite."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(content + "\n")


def initialize(directory: Path, env_file: Path, project: str, port: int, benchmark: bool) -> None:
    if not re.fullmatch(r"[a-z][a-z0-9-]{2,47}", project):
        raise ValueError("project must be a lowercase disposable/local namespace")
    if not 1024 <= port <= 65535:
        raise ValueError("port must be an unprivileged TCP port")
    if directory.exists() or env_file.exists():
        raise ValueError(
            "setup refuses existing secret directory or environment file; preserve your keys"
        )
    directory.mkdir(mode=0o700, parents=True)
    credentials = {name: secrets.token_hex(32) for name in ("admin", "migrator", "runtime")}
    for name, value in credentials.items():
        private_write(directory / f"{name}_password", value)
    for role in ("migrator", "runtime"):
        private_write(
            directory / f"{role}_dsn",
            f"postgresql+psycopg://bonafides_{role}:{credentials[role]}@db:5432/bonafides",
        )
    keyring = {
        "active_key_id": "local-1",
        "keys": {"local-1": base64.b64encode(secrets.token_bytes(32)).decode("ascii")},
    }
    private_write(directory / "keyring", json.dumps(keyring))
    private_write(directory / "bootstrap_token", secrets.token_urlsafe(48))
    # No credential values belong in Compose interpolation or a process environment.
    private_write(
        env_file,
        f"COMPOSE_PROJECT_NAME={project}\nBONAFIDES_SECRET_DIR={directory.resolve()}\n"
        f"BONAFIDES_PORT={port}\nBONAFIDES_ORIGIN=http://localhost:{port}\n"
        f"BONAFIDES_MODE={'benchmark' if benchmark else 'production'}",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path(".secrets"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--project", default="bonafides-local")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--benchmark",
        action="store_true",
        help="enable synthetic M0 proof endpoints, never real integrations",
    )
    parser.add_argument(
        "--show-bootstrap-token",
        action="store_true",
        help="intentional local secret reveal; do not capture terminal output",
    )
    args = parser.parse_args()
    try:
        if args.show_bootstrap_token:
            token = args.directory / "bootstrap_token"
            if token.is_symlink() or not token.is_file() or token.stat().st_mode & 0o077:
                raise ValueError("bootstrap token must be a private regular file")
            print(token.read_text().strip())
        else:
            initialize(args.directory, args.env_file, args.project, args.port, args.benchmark)
            print(
                "Local credentials generated. Keep the secret directory private and escrow the keyring separately."
            )
    except (OSError, ValueError):
        print(
            "Local setup refused: existing/invalid path, permissions, or configuration; no secrets are displayed."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
