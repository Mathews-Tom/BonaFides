# SPDX-License-Identifier: MIT OR Apache-2.0
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "deploy" / "install.sh"
COMPOSE_ENV = {"BONAFIDES_IMAGE": "bonafides:test", "BONAFIDES_SECRET_DIR": "/srv/secrets"}
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


def compose_config(path: Path, **env: str) -> dict[str, Any]:
    result = subprocess.run(
        ["docker", "compose", "-f", str(path), "config", "--format", "json"],
        capture_output=True,
        text=True,
        env={key: value for key, value in os.environ.items() if not key.startswith("BONAFIDES_")}
        | env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    config: dict[str, Any] = json.loads(result.stdout)
    # Extension anchors only carry shared YAML; the resolved services are what run.
    return {key: value for key, value in config.items() if not key.startswith("x-")}


def test_release_compose_has_the_same_topology_and_isolation_without_building() -> None:
    development = compose_config(ROOT / "compose.yaml", **COMPOSE_ENV)
    released = compose_config(ROOT / "deploy" / "compose.release.yaml", **COMPOSE_ENV)

    for service in development["services"].values():
        service.pop("build", None)
    for service in released["services"].values():
        if service["image"] == COMPOSE_ENV["BONAFIDES_IMAGE"]:
            assert service.pop("pull_policy") == "never"
    assert released == development


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


def test_installer_stops_services_without_removing_volumes_then_starts_and_reports_the_path(
    sandbox: dict[str, Path],
) -> None:
    result = run_installer(sandbox)

    calls = docker_calls(sandbox)
    run_call = next(call for call in calls if call.startswith("run "))
    compose_calls = [
        call for call in calls if call.startswith("compose ") and "version" not in call
    ]
    install = sandbox["install"].resolve()
    assert result.returncode == 0, result.stderr
    assert "--pull never" in run_call and "--network none" in run_call
    assert [call.split(" ")[-1] for call in compose_calls][:1] == ["--remove-orphans"]
    assert any(" up -d --wait" in call for call in compose_calls)
    assert "init-bootstrap-if-ownerless" in compose_calls[-1]
    assert not any("--volumes" in call or " -v" in call for call in compose_calls)
    assert "Sign in: http://localhost:8123" in result.stdout
    assert f"{install}/secrets" in result.stdout
