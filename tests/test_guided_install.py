# SPDX-License-Identifier: MIT OR Apache-2.0
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install.sh"
# Only read-only engine queries are supported: an unexpected mutation is an error.
ENGINE = """#!/bin/sh
case "$1 $2" in
  'compose version') exit 0 ;;
  'version --format')
    [ "${ENGINE_STOPPED:-}" != yes ] || exit 1
    echo linux/arm64 ;;
  'ps -a') printf '%s' "${ENGINE_DIRECTORIES:-}" ;;
  'volume ls') printf '%s' "${ENGINE_VOLUMES:-}" ;;
  'image inspect') echo linux/arm64 ;;
  *) echo 'Unexpected engine operation during preflight' >&2; exit 99 ;;
esac
"""


@pytest.fixture
def environment(tmp_path: Path) -> dict[str, str]:
    binaries = tmp_path / "bin"
    binaries.mkdir()
    engine = binaries / "docker"
    engine.write_text(ENGINE)
    engine.chmod(0o755)
    return {"HOME": str(tmp_path), "PATH": f"{binaries}:/usr/bin:/bin"}


def invoke(environment: dict[str, str], *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(INSTALLER), *arguments],
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def test_stopped_docker_is_not_replaced_or_started(environment: dict[str, str]) -> None:
    result = invoke(environment | {"ENGINE_STOPPED": "yes"})
    assert result.returncode == 1
    assert not (Path(environment["HOME"]) / "bonafides").exists()


@pytest.mark.parametrize("port", ["0", "80", "65536", "8000\n9999", "$(touch marker)"])
def test_invalid_ports_refuse_before_installation(environment: dict[str, str], port: str) -> None:
    result = invoke(environment, "--port", port)
    assert result.returncode == 1
    assert not (Path(environment["HOME"]) / "bonafides").exists()


@pytest.mark.parametrize("settings", ["COMPOSE_PROJECT_NAME=other", "BONAFIDES_PORT=18471"])
def test_existing_settings_cannot_be_reassigned(environment: dict[str, str], settings: str) -> None:
    directory = Path(environment["HOME"]) / "bonafides"
    directory.mkdir()
    saved = "COMPOSE_PROJECT_NAME=saved-project\nBONAFIDES_PORT=18470\n"
    (directory / ".env").write_text(saved)
    key, value = settings.split("=")
    flag = "--project" if key == "COMPOSE_PROJECT_NAME" else "--port"
    result = invoke(environment, flag, value)
    assert result.returncode == 1
    assert (directory / ".env").read_text() == saved
    assert not (directory / ".installer-image").exists()


def test_saved_configuration_is_data_not_shell_code(environment: dict[str, str]) -> None:
    directory = Path(environment["HOME"]) / "bonafides"
    directory.mkdir()
    marker = directory / "executed"
    saved = f"COMPOSE_PROJECT_NAME=$(touch {marker})\nBONAFIDES_PORT=18470\n"
    (directory / ".env").write_text(saved)
    result = invoke(environment)
    assert result.returncode == 1
    assert not marker.exists()
    assert (directory / ".env").read_text() == saved


@pytest.mark.parametrize(
    "directories,volumes", [("directory=/another/install", ""), ("directory=", ""), ("", "old-pg")]
)
def test_existing_projects_and_orphaned_volumes_are_not_adopted(
    environment: dict[str, str], directories: str, volumes: str
) -> None:
    result = invoke(
        environment | {"ENGINE_DIRECTORIES": directories, "ENGINE_VOLUMES": volumes},
    )
    assert result.returncode == 1
    assert not (Path(environment["HOME"]) / "bonafides").exists()


def test_foreign_physical_volume_is_refused_before_credentials_are_created(tmp_path: Path) -> None:
    image = os.environ.get("BONAFIDES_TEST_PARSER_IMAGE")
    if not image:
        pytest.fail("Installer volume isolation requires the current Linux image and Docker.")
    project = "bonafides-proof-vol-" + os.urandom(6).hex()
    volume = project + "_app-secrets"
    directory = tmp_path / "install"
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("BONAFIDES_", "COMPOSE_"))
    }

    def docker(*arguments: str) -> str:
        return subprocess.run(
            ["docker", *arguments],
            capture_output=True,
            text=True,
            env=environment,
            timeout=120,
            check=True,
        ).stdout

    docker(
        "volume",
        "create",
        "--label",
        f"com.docker.compose.project={project}-other",
        "--label",
        "com.docker.compose.volume=app-secrets",
        volume,
    )
    try:
        docker(
            "run",
            "--rm",
            "--pull",
            "never",
            "--network",
            "none",
            "--user",
            "0",
            "--mount",
            f"type=volume,source={volume},target=/state",
            "--entrypoint",
            "python",
            image,
            "-c",
            'from pathlib import Path; Path("/state/keyring").write_text("synthetic foreign keyring")',
        )
        result = invoke(
            environment,
            "--dir",
            str(directory),
            "--port",
            "18478",
            "--project",
            project,
            "--image",
            image,
        )
        assert result.returncode == 1
        assert not (directory / "secrets").exists()
        assert not (directory / ".env").exists()
        assert (
            docker(
                "run",
                "--rm",
                "--pull",
                "never",
                "--network",
                "none",
                "--user",
                "0",
                "--mount",
                f"type=volume,source={volume},target=/state,readonly",
                "--entrypoint",
                "cat",
                image,
                "/state/keyring",
            )
            == "synthetic foreign keyring"
        )
    finally:
        if (directory / "compose.yaml").exists():
            subprocess.run(
                [
                    "docker",
                    "compose",
                    "--project-name",
                    project,
                    "--project-directory",
                    str(directory),
                    "--env-file",
                    str(directory / ".env"),
                    "-f",
                    str(directory / "compose.yaml"),
                    "down",
                    "--volumes",
                ],
                env=environment | {"BONAFIDES_IMAGE": image},
                capture_output=True,
                timeout=120,
                check=True,
            )
        docker("volume", "rm", "--force", volume)
