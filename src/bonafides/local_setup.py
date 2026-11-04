# SPDX-License-Identifier: MIT OR Apache-2.0
"""Create, validate and publish local private credentials; stdlib only, never touches a database.

One generator serves development, the runtime proofs and the image-based installer. A complete
credential set is reused unchanged; a partial or invalid one is refused and never deleted or rotated.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import re
import secrets
import shutil
import stat
import sys
from pathlib import Path

SECRET_NAMES = (
    "admin_password",
    "migrator_password",
    "runtime_password",
    "migrator_dsn",
    "runtime_dsn",
    "keyring",
    "bootstrap_token",
)
# Source name -> (installed name, mode). The database entrypoint executes the init script directly.
ASSETS = {
    "compose.release.yaml": ("compose.yaml", 0o644),
    "init-db.sh": ("init-db.sh", 0o755),
    "start.sh": ("start.sh", 0o755),
    "stop.sh": ("stop.sh", 0o755),
    "uninstall.sh": ("uninstall.sh", 0o755),
    "lifecycle.sh": ("lifecycle.sh", 0o755),
}
IMAGE_ASSET_ROOT = Path("/app/deploy")
_PROJECT = re.compile(r"[a-z][a-z0-9-]{2,47}")
_HEX_64 = re.compile(r"[0-9a-f]{64}")
_KEY_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43,128}")


class SetupError(ValueError):
    """Refusal whose message names files and remedies, never credential values."""


def private_write(path: Path, content: str) -> None:
    """Exclusive creation prevents accidental credential rotation or symlink overwrite."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(content + "\n")


def _check_request(project: str, port: int) -> None:
    if not _PROJECT.fullmatch(project):
        raise SetupError("project must be a lowercase disposable/local namespace")
    if not 1024 <= port <= 65535:
        raise SetupError("port must be an unprivileged TCP port")


def _env_text(project: str, port: int, benchmark: bool, published: Path) -> str:
    # No credential values belong in Compose interpolation or a process environment.
    if "'" in str(published):
        raise SetupError("the install path must not contain a single quote")
    # Single quotes make Compose read the host path literally (no $ interpolation).
    return (
        f"COMPOSE_PROJECT_NAME={project}\nBONAFIDES_SECRET_DIR='{published}'\n"
        f"BONAFIDES_PORT={port}\nBONAFIDES_ORIGIN=http://localhost:{port}\n"
        f"BONAFIDES_MODE={'benchmark' if benchmark else 'production'}"
    )


def initialize(
    directory: Path,
    env_file: Path,
    project: str,
    port: int,
    benchmark: bool,
    *,
    published: Path | None = None,
) -> None:
    """Create a new credential set; `published` is the absolute path Compose must mount."""
    _check_request(project, port)
    if directory.exists() or env_file.exists():
        raise SetupError(
            "setup refuses existing secret directory or environment file; preserve your keys"
        )
    # The host path is recorded exactly as given: it does not exist inside the container.
    # Rendering first refuses a bad path before anything is created.
    environment = _env_text(
        project, port, benchmark, directory.resolve() if published is None else published
    )
    directory.mkdir(mode=0o700, parents=True)
    credentials = {name: secrets.token_hex(32) for name in ("admin", "migrator", "runtime")}
    for name, value in credentials.items():
        private_write(directory / f"{name}_password", value)
    for role in ("migrator", "runtime"):
        private_write(directory / f"{role}_dsn", _dsn(role, credentials[role]))
    keyring = {
        "active_key_id": "local-1",
        "keys": {"local-1": base64.b64encode(secrets.token_bytes(32)).decode("ascii")},
    }
    private_write(directory / "keyring", json.dumps(keyring))
    private_write(directory / "bootstrap_token", secrets.token_urlsafe(48))
    private_write(env_file, environment)


def _dsn(role: str, password: str) -> str:
    return f"postgresql+psycopg://bonafides_{role}:{password}@db:5432/bonafides"


def _private_regular(path: Path, label: str, *, directory: bool = False) -> str:
    info = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not kind(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
        raise SetupError(
            f"{label} must be a private {'directory' if directory else 'regular file'} "
            "owned by the installing user; fix its type, mode or owner without deleting it"
        )
    return "" if directory else path.read_text().strip()


def _verify_secret_formats(values: dict[str, str]) -> None:
    for role in ("admin", "migrator", "runtime"):
        if not _HEX_64.fullmatch(values[f"{role}_password"]):
            raise SetupError(f"{role}_password is not a generated 64-hex credential")
    for role in ("migrator", "runtime"):
        if values[f"{role}_dsn"] != _dsn(role, values[f"{role}_password"]):
            raise SetupError(f"{role}_dsn does not match {role}_password")
    try:
        keyring = json.loads(values["keyring"])
        if (
            set(keyring) != {"active_key_id", "keys"}
            or keyring["active_key_id"] not in keyring["keys"]
        ):
            raise ValueError
        # Mirror the runtime keyring rules for every key: a bad entry would stop the stack.
        for key_id, encoded in keyring["keys"].items():
            if not _KEY_ID.fullmatch(key_id) or len(base64.b64decode(encoded, validate=True)) != 32:
                raise ValueError
    except (ValueError, KeyError, TypeError, AttributeError, binascii.Error):
        raise SetupError("keyring is not a valid keyring document of 32-byte keys") from None
    if not _TOKEN.fullmatch(values["bootstrap_token"]):
        raise SetupError("bootstrap_token has an invalid shape")


def verify(
    directory: Path, env_file: Path, project: str, port: int, benchmark: bool, published: Path
) -> None:
    """Accept only a complete, private, consistent credential set for this exact install."""
    _private_regular(directory, str(directory), directory=True)
    missing = [name for name in SECRET_NAMES if not (directory / name).is_file()]
    if missing:
        raise SetupError(
            f"incomplete credential directory {directory}: missing {', '.join(missing)}; "
            "restore the missing files from your escrow; nothing was changed"
        )
    values = {name: _private_regular(directory / name, name) for name in SECRET_NAMES}
    _verify_secret_formats(values)
    if _private_regular(env_file, str(env_file)) != _env_text(project, port, benchmark, published):
        raise SetupError(
            f"{env_file} differs from the requested project, port or secret path; rerun with the "
            "original values or reconcile the file by hand; nothing was changed"
        )


def ensure(
    directory: Path,
    env_file: Path,
    project: str,
    port: int,
    benchmark: bool,
    *,
    published: Path,
) -> str:
    """Create credentials when none exist, reuse a complete set, refuse anything between."""
    _check_request(project, port)
    present = [path for path in (directory, env_file) if os.path.lexists(path)]
    if not present:
        initialize(directory, env_file, project, port, benchmark, published=published)
        return "created"
    if len(present) == 1:
        raise SetupError(
            f"partial installation state: only {present[0]} exists; restore the other from "
            "escrow or move this one aside deliberately; nothing was changed"
        )
    verify(directory, env_file, project, port, benchmark, published)
    return "reused"


def publish_assets(source: Path, root: Path) -> None:
    """Replace image-owned Compose assets atomically; credentials and volumes are untouched."""
    for name, (target, mode) in ASSETS.items():
        temporary = root / f".{target}.new"
        # Exclusive creation: a pre-planted link at this path is never followed.
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream, (source / name).open("rb") as asset:
            os.fchmod(stream.fileno(), mode)
            shutil.copyfileobj(asset, stream)
        temporary.replace(root / target)


def _release_preflight(arguments: argparse.Namespace) -> tuple[str, bool]:
    root: Path = arguments.release_root
    host: Path | None = arguments.host_root
    if host is None or not host.is_absolute():
        raise SetupError("--host-root must be the absolute host path of the install directory")
    _check_request(arguments.project, arguments.port)
    if any(character in str(host) for character in ("'", ",", "|", "\n")):
        raise SetupError("the install path cannot contain quotes, commas, pipes or newlines")
    if not arguments.image or not re.fullmatch(r"[A-Za-z0-9_./:@-]+", arguments.image):
        raise SetupError("--image must name the deliberately selected local image")
    expected_resources = "\n".join(
        f"{kind} {name} {arguments.project}_{name}"
        for kind, names in (
            (
                "volume",
                (
                    "app-secrets",
                    "blobs",
                    "db-secrets",
                    "import-spool",
                    "migration-secrets",
                    "pg",
                    "spool",
                ),
            ),
            ("network", ("ingress", "private", "source-outbound", "sources")),
        )
        for name in names
    )
    if arguments.resource_map is not None and arguments.resource_map != expected_resources:
        raise SetupError("image resource names differ from the managed lifecycle contract")
    metadata = f"bonafides-lifecycle-1\n{arguments.project}\n{host}\n{expected_resources}"
    metadata_file = root / ".installer-state"
    existing = os.path.lexists(metadata_file)
    if existing:
        if _private_regular(metadata_file, str(metadata_file)) != metadata:
            raise SetupError("lifecycle metadata belongs to another installation or resource set")
        saved_image = _private_regular(root / ".installer-image", "saved image identity")
        if not re.fullmatch(r"[A-Za-z0-9_./:@-]+", saved_image):
            raise SetupError("saved image identity is invalid")
        verify(
            root / "secrets",
            root / ".env",
            arguments.project,
            arguments.port,
            False,
            host / "secrets",
        )
    elif any(os.path.lexists(root / name) for name in ("secrets", ".env", ".installer-image")):
        raise SetupError(
            "existing configuration has no lifecycle ownership metadata; preserve it and use "
            "its original management procedure or choose a separate empty installation"
        )
    if os.path.lexists(root / ".installer-image.new"):
        raise SetupError("saved-image temporary path already exists; nothing was changed")
    # Validate all source/destination assets before creating credentials or replacing anything.
    for name, (target, mode) in ASSETS.items():
        source = arguments.assets / name
        if not source.is_file() or source.is_symlink():
            raise SetupError(f"image lifecycle asset is missing or linked: {name}")
        destination = root / target
        if os.path.lexists(destination):
            if (
                not existing
                or not destination.is_file()
                or destination.is_symlink()
                or destination.stat().st_uid != os.getuid()
            ):
                raise SetupError(f"refusing unmanaged or linked asset: {target}")
            if arguments.validate_installed and (
                destination.read_bytes() != source.read_bytes()
                or stat.S_IMODE(destination.stat().st_mode) != mode
            ):
                raise SetupError(
                    f"managed asset differs from the saved image: {target}; install while stopped"
                )
        elif arguments.validate_installed:
            raise SetupError(f"managed asset is missing: {target}; install while stopped")
        temporary = root / f".{target}.new"
        if os.path.lexists(temporary):
            raise SetupError(f"asset temporary path already exists: {temporary.name}")
    return metadata, existing


def _release(arguments: argparse.Namespace) -> str:
    metadata, existing = _release_preflight(arguments)
    if arguments.validate_installed and (not arguments.check or not existing):
        raise SetupError(
            "--validate-installed requires --check and an existing managed installation"
        )
    if arguments.check:
        return "reused" if existing else "available"
    root: Path = arguments.release_root
    state = ensure(
        root / "secrets",
        root / ".env",
        arguments.project,
        arguments.port,
        False,
        published=arguments.host_root / "secrets",
    )
    if not existing:
        private_write(root / ".installer-state", metadata)
    temporary = root / ".installer-image.new"
    private_write(temporary, arguments.image)
    temporary.replace(root / ".installer-image")
    publish_assets(arguments.assets, root)
    return state


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path(".secrets"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--project", default="bonafides-local")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--benchmark",
        action="store_true",
        help="enable synthetic benchmark endpoints, never real integrations",
    )
    parser.add_argument(
        "--show-bootstrap-token",
        action="store_true",
        help="intentional local secret reveal; do not capture terminal output",
    )
    parser.add_argument(
        "--release-root",
        type=Path,
        help="install directory as seen here: reuse complete credentials and publish assets",
    )
    parser.add_argument("--host-root", type=Path, help="the same directory's absolute host path")
    parser.add_argument("--assets", type=Path, default=IMAGE_ASSET_ROOT)
    parser.add_argument("--image", help="resolved local image identity for installed start.sh")
    parser.add_argument("--resource-map", help="resolved physical resource map from Compose")
    parser.add_argument("--check", action="store_true", help="read-only release preflight")
    parser.add_argument(
        "--validate-installed",
        action="store_true",
        help="with --check, require installed assets to match the saved image",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse(sys.argv[1:] if argv is None else argv)
    try:
        if args.release_root is not None:
            print(_release(args))
        elif args.show_bootstrap_token:
            token = args.directory / "bootstrap_token"
            if token.is_symlink() or not token.is_file() or token.stat().st_mode & 0o077:
                raise SetupError("bootstrap token must be a private regular file")
            print(token.read_text().strip())
        else:
            initialize(args.directory, args.env_file, args.project, args.port, args.benchmark)
            print(
                "Local credentials generated. Keep the secret directory private and escrow the keyring separately."
            )
    except SetupError as exc:
        print(f"Local setup refused: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(
            f"Local setup refused: {exc.strerror or type(exc).__name__}"
            f"{f' ({exc.filename})' if exc.filename else ''}; no secrets are displayed.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
