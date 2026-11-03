# SPDX-License-Identifier: MIT OR Apache-2.0
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "deploy" / "install.sh"
FAKE_DOCKER = """#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_LOG"
case "$1 $2" in
  "compose version") exit 0 ;;
  "image inspect") [ "$FAKE_IMAGE" = present ] || exit 1; exit 0 ;;
esac
case "$1" in
  run)
    [ -z "$FAKE_SETUP_REFUSES" ] || { echo "Local setup refused: partial" >&2; exit 1; }
    printf 'BONAFIDES_PORT=8123\\n' > "$FAKE_INSTALL/.env"
    echo created ;;
  compose) case " $* " in *" exec "*) echo "Owner already exists; bootstrap not initialized." ;; esac ;;
esac
"""


def test_release_compose_requires_an_explicit_image() -> None:
    result = subprocess.run(
        ["docker", "compose", "-f", str(ROOT / "deploy" / "compose.release.yaml"), "config"],
        capture_output=True,
        text=True,
        env={key: value for key, value in os.environ.items() if not key.startswith("BONAFIDES_")}
        | {"BONAFIDES_SECRET_DIR": "/srv/secrets"},
        check=False,
    )

    assert result.returncode != 0 and "BONAFIDES_IMAGE" in result.stderr


@pytest.fixture
def sandbox(tmp_path: Path) -> dict[str, Path]:
    binaries = tmp_path / "bin"
    binaries.mkdir()
    docker = binaries / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(0o755)
    install = tmp_path / "install"
    install.mkdir()
    return {"bin": binaries, "install": install, "log": tmp_path / "docker.log"}


def run_installer(sandbox: dict[str, Path], **env: str) -> subprocess.CompletedProcess[str]:
    environment = {
        "PATH": f"{sandbox['bin']}:/usr/bin:/bin",
        "HOME": str(sandbox["install"]),
        "FAKE_LOG": str(sandbox["log"]),
        "FAKE_INSTALL": str(sandbox["install"]),
        "FAKE_IMAGE": "present",
        "FAKE_SETUP_REFUSES": "",
        "BONAFIDES_PORT": "9999",
    } | env
    return subprocess.run(
        ["/bin/sh", str(INSTALLER), "--dir", str(sandbox["install"]), "bonafides:test"],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )


def docker_calls(sandbox: dict[str, Path]) -> list[str]:
    return sandbox["log"].read_text().splitlines() if sandbox["log"].exists() else []


def test_installer_never_pulls_and_stops_when_the_image_is_not_loaded(
    sandbox: dict[str, Path],
) -> None:
    result = run_installer(sandbox, FAKE_IMAGE="absent")

    assert result.returncode == 1 and "docker load" in result.stderr
    assert not any(call.startswith(("pull", "run", "compose up")) for call in docker_calls(sandbox))


def test_installer_stops_before_touching_services_when_credentials_are_refused(
    sandbox: dict[str, Path],
) -> None:
    result = run_installer(sandbox, FAKE_SETUP_REFUSES="1")

    assert result.returncode == 1 and "no credential was deleted or rotated" in result.stderr
    assert not any(" down" in call or " up" in call for call in docker_calls(sandbox))
