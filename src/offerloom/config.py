# SPDX-License-Identifier: MIT OR Apache-2.0
"""Explicit runtime configuration. Importing this module never reads secrets."""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from sqlalchemy.engine import make_url

from offerloom.crypto import Keyring


class ConfigurationError(ValueError):
    """Configuration errors name fields, never values or underlying secret text."""


def read_secret(path: Path, field_name: str) -> str:
    try:
        # Refuse symlink substitution and files readable by other host users.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > 65536:
                raise ConfigurationError(f"Invalid {field_name} secret file permissions/type")
            with os.fdopen(fd, "r", encoding="utf-8", closefd=False) as stream:
                value = stream.read(65537).strip()
        finally:
            os.close(fd)
        if not value:
            raise ConfigurationError(f"Missing {field_name}")
        return value
    except (OSError, UnicodeError) as exc:
        raise ConfigurationError(f"Cannot read {field_name} secret file") from exc


def _dsn(value: str, name: str) -> str:
    try:
        url = make_url(value)
        if (
            url.drivername not in {"postgresql", "postgresql+psycopg"}
            or not url.database
            or not url.username
            or not url.password
            or not url.host
            or any(k not in {"sslmode", "connect_timeout"} for k in url.query)
        ):
            raise ValueError
        sslmode = url.query.get("sslmode")
        if sslmode is not None and sslmode not in {
            "disable",
            "allow",
            "prefer",
            "require",
            "verify-ca",
            "verify-full",
        }:
            raise ValueError
        timeout = url.query.get("connect_timeout")
        if timeout is not None and (
            not isinstance(timeout, str) or not timeout.isdecimal() or not 1 <= int(timeout) <= 30
        ):
            raise ValueError
        return url.set(drivername="postgresql+psycopg").render_as_string(hide_password=False)
    except Exception as exc:
        raise ConfigurationError(f"Invalid {name}") from exc


@dataclass(frozen=True)
class Settings:
    database_dsn: str = field(repr=False)
    keyring: Keyring = field(repr=False)
    origin: str
    blob_root: Path
    renderer_spool: Path
    import_spool: Path
    mode: Literal["production", "benchmark"] = "production"
    migration_dsn: str | None = field(default=None, repr=False)
    bootstrap_token_file: Path | None = None
    web_dist: Path = Path("web/dist")
    max_body_bytes: int = 1048576
    local_model_route_file: Path | None = None
    backup_capture_config_file: Path | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "database_dsn", _dsn(self.database_dsn, "database_dsn"))
        if self.migration_dsn is not None:
            object.__setattr__(self, "migration_dsn", _dsn(self.migration_dsn, "migration_dsn"))
        try:
            parsed = urlsplit(self.origin)
            port = parsed.port
        except ValueError as exc:
            raise ConfigurationError("Invalid origin") from exc
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or any(c.isspace() for c in self.origin)
            or any(ord(c) < 33 or ord(c) == 127 for c in self.origin)
            or "\\" in self.origin
            or "%" in self.origin
            or not parsed.hostname.isascii()
            or parsed.hostname.endswith(".")
            or not all(c.isalnum() or c in ".-:" for c in parsed.hostname)
            or (
                parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            )
            or (port is not None and not 1 <= port <= 65535)
            or self.origin != f"{parsed.scheme}://{parsed.netloc}"
        ):
            raise ConfigurationError("Invalid origin: exact HTTPS origin or loopback HTTP required")
        if self.mode not in {"production", "benchmark"}:
            raise ConfigurationError("Invalid mode")
        private_roots = (
            ("blob_root", self.blob_root),
            ("renderer_spool", self.renderer_spool),
            ("import_spool", self.import_spool),
        )
        for name, path in private_roots:
            if not path.is_absolute() or path.is_symlink() or not path.is_dir():
                raise ConfigurationError(f"Invalid {name}: existing absolute directory required")
        if len({path.resolve() for _, path in private_roots}) != len(private_roots):
            raise ConfigurationError("blob_root, renderer_spool and import_spool must be separate")
        if not 1024 <= self.max_body_bytes <= 10485760:
            raise ConfigurationError("Invalid max_body_bytes")

    @property
    def render_spool(self) -> Path:
        return self.renderer_spool

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        values = os.environ if environ is None else environ

        def required(name: str) -> str:
            result = values.get(f"OFFERLOOM_{name}", "")
            if not result:
                raise ConfigurationError(f"Missing OFFERLOOM_{name}")
            return result

        key_path = Path(required("KEYRING_FILE"))
        try:
            keyring = Keyring.parse(read_secret(key_path, "keyring"))
        except ValueError as exc:
            raise ConfigurationError("Invalid keyring") from exc
        migration_path = values.get("OFFERLOOM_MIGRATION_DSN_FILE")
        bootstrap_path = values.get("OFFERLOOM_BOOTSTRAP_TOKEN_FILE")
        mode = values.get("OFFERLOOM_MODE", "production")
        if mode not in {"production", "benchmark"}:
            raise ConfigurationError("Invalid mode")
        return cls(
            database_dsn=read_secret(Path(required("DATABASE_DSN_FILE")), "database_dsn"),
            keyring=keyring,
            origin=required("ORIGIN"),
            blob_root=Path(required("BLOB_ROOT")),
            renderer_spool=Path(required("RENDERER_SPOOL")),
            import_spool=Path(required("IMPORT_SPOOL")),
            mode="benchmark" if mode == "benchmark" else "production",
            migration_dsn=read_secret(Path(migration_path), "migration_dsn")
            if migration_path
            else None,
            bootstrap_token_file=Path(bootstrap_path) if bootstrap_path else None,
            web_dist=Path(values.get("OFFERLOOM_WEB_DIST", "web/dist")),
            local_model_route_file=Path(values["OFFERLOOM_LOCAL_MODEL_ROUTE_FILE"])
            if values.get("OFFERLOOM_LOCAL_MODEL_ROUTE_FILE")
            else None,
            backup_capture_config_file=Path(values["OFFERLOOM_BACKUP_CAPTURE_CONFIG_FILE"])
            if values.get("OFFERLOOM_BACKUP_CAPTURE_CONFIG_FILE")
            else None,
        )

    @classmethod
    def load(cls) -> Settings:
        return cls.from_env()


@dataclass(frozen=True)
class MigrationSettings:
    migration_dsn: str | None = field(repr=False)

    def __post_init__(self) -> None:
        if self.migration_dsn is not None:
            object.__setattr__(self, "migration_dsn", _dsn(self.migration_dsn, "migration_dsn"))

    @classmethod
    def load(cls) -> MigrationSettings:
        path = os.environ.get("OFFERLOOM_MIGRATION_DSN_FILE")
        if not path:
            raise ConfigurationError("Missing OFFERLOOM_MIGRATION_DSN_FILE")
        return cls(read_secret(Path(path), "migration_dsn"))
