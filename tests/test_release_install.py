# SPDX-License-Identifier: MIT OR Apache-2.0
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from bonafides.local_setup import main

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "bonafides-test"
IMAGE = "sha256:" + "1" * 64
VOLUMES = ("app-secrets", "blobs", "db-secrets", "import-spool", "migration-secrets", "pg", "spool")
NETWORKS = ("ingress", "private", "source-outbound", "sources")
# This models Docker's resource/state API, not the lifecycle implementation: image-owned Python
# preflight and publication run for real. Refusal assertions compare persisted state and files.
FAKE_DOCKER = r"""import json, os, subprocess, sys
from pathlib import Path
args = sys.argv[1:]
state_path = Path(os.environ["FAKE_STATE"])
state = json.loads(state_path.read_text())
def save():
    state_path.write_text(json.dumps(state, sort_keys=True))
def option(name, default=None):
    return args[args.index(name) + 1] if name in args else default
def output(value):
    if value: print(value)
def labels(item):
    return item.get("labels", {})
def filter_items(items):
    filters = [args[index + 1] for index, arg in enumerate(args[:-1]) if arg == "--filter"]
    for name, item in items.items():
        matches = True
        for expression in filters:
            if expression.startswith("label="):
                key, value = expression[6:].split("=", 1)
                matches &= labels(item).get(key) == value
            else:
                kind, value = expression.split("=", 1)
                matches &= value in item.get(kind + "s", [])
        if matches: yield name, item
if args == ["info"] or args == ["compose", "version"]:
    sys.exit(0)
if args[:2] == ["version", "--format"]:
    output("linux/arm64")
    sys.exit(0)
if args[:2] == ["image", "inspect"]:
    if not state["image_present"]: sys.exit(1)
    if args[-1] != os.environ["FAKE_IMAGE_ID"]: sys.exit(1)
    output("linux/arm64" if option("--format") == "{{.Os}}/{{.Architecture}}" else os.environ["FAKE_IMAGE_ID"] if "--format" in args else "[]")
elif args[0] == "ps":
    items = list(filter_items(state["containers"]))
    output("\n".join("directory=" + item["directory"] if option("--format", "").startswith("directory=") else name for name, item in items))
elif args[0] == "inspect":
    item = state["containers"][args[-1]]
    fmt = option("--format")
    if "working_dir" in fmt: output(item["directory"])
    elif ".State.Running" in fmt: output("true" if item["running"] else "false")
    elif ".State.Health" in fmt: output(item["health"])
    else: raise AssertionError(args)
elif args[0] in ("volume", "network"):
    kind, command = args[:2]
    items = state[kind + "s"]
    if command == "ls":
        output("\n".join(name for name, _ in filter_items(items)))
    elif command == "inspect":
        key = option("--format").split('"')[1]
        output(labels(items[args[-1]]).get(key, "<no value>"))
    elif command == "rm":
        del items[args[-1]]
        state["mutations"].append(args)
        save()
    elif command == "create":
        pairs = [args[index + 1].split("=", 1) for index, arg in enumerate(args[:-1]) if arg == "--label"]
        items[args[-1]] = {"labels": dict(pairs), "data": ""}
        state["mutations"].append(args)
        save()
        output(args[-1])
    else: raise AssertionError(args)
elif args[0] == "create":
    name = "port-probe"
    state["containers"][name] = {"labels": {}, "running": False}
    state["mutations"].append(args)
    save()
    output(name)
elif args[0] in ("start", "stop", "rm"):
    item = state["containers"][args[-1]]
    if args[0] in ("start", "stop"): item["running"] = args[0] == "start"
    else: del state["containers"][args[-1]]
    state["mutations"].append(args)
    save()
elif args[0] == "run":
    index = args.index("--entrypoint")
    program = args[index + 1]
    assert args[index + 2] == os.environ["FAKE_IMAGE_ID"]
    command = args[index + 3:]
    repo = Path(os.environ["FAKE_REPO"])
    if program == "cat":
        output((repo / command[0].removeprefix("/app/")).read_text())
    elif program == "python":
        if command[:2] == ["-m", "bonafides.local_setup"]:
            mount = option("--mount", "")
            target = next((part[7:] for part in mount.split(",") if part.startswith("source=")), os.environ["FAKE_EMPTY_ROOT"])
            command = [target if arg == "/install" else arg for arg in command]
            command += ["--assets", str(repo / "deploy")]
        environment = dict(os.environ, PYTHONPATH=str(repo / "src"))
        sys.exit(subprocess.run([sys.executable, *command], env=environment, check=False).returncode)
    else: raise AssertionError(args)
elif args[0] == "compose" and "config" in args:
    project = option("--project-name")
    print(json.dumps({key: {name: {"name": project + "_" + name} for name in names} for key, names in (("volumes", os.environ["FAKE_VOLUMES"].split()), ("networks", os.environ["FAKE_NETWORKS"].split()))}))
elif args[0] == "compose":
    index = args.index("-f") + 2
    command = args[index:]
    project = option("--project-name")
    directory = option("--project-directory")
    if command[:2] == ["ps", "-q"]:
        output("\n".join(name for name, item in state["containers"].items() if labels(item).get("com.docker.compose.project") == project and labels(item).get("com.docker.compose.service") == command[-1]))
    elif command[:2] == ["rm", "-f"]:
        for name, item in list(state["containers"].items()):
            if labels(item).get("com.docker.compose.project") == project and labels(item).get("com.docker.compose.service") in command[2:]:
                del state["containers"][name]
        state["mutations"].append(args)
        save()
    elif command[:2] == ["up", "-d"]:
        for service in ("db", "api", "worker", "ingress", "lifecycle", "source-egress", "renderer", "parser"):
            state["containers"][project + "-" + service] = {
                "labels": {"com.docker.compose.project": project, "com.docker.compose.service": service},
                "directory": directory, "running": True, "health": "healthy",
            }
        state["mutations"].append(args)
        save()
    elif command == ["exec", "-T", "api", "python", "-m", "bonafides", "init-bootstrap-if-ownerless"]:
        assert any(item["running"] and labels(item).get("com.docker.compose.project") == project and labels(item).get("com.docker.compose.service") == "api" for item in state["containers"].values())
        state["mutations"].append(args)
        save()
    else: raise AssertionError(args)
else:
    raise AssertionError("unexpected Docker mutation: " + repr(args))
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
    assert result.returncode != 0


@pytest.fixture
def sandbox(tmp_path: Path) -> dict[str, Path]:
    binaries = tmp_path / "bin"
    binaries.mkdir()
    docker = binaries / "docker"
    docker.write_text(f"#!{sys.executable}\n" + FAKE_DOCKER)
    docker.chmod(0o755)
    directory = tmp_path / "install"
    directory.mkdir()
    state = tmp_path / "docker.json"
    state.write_text(
        json.dumps(
            {
                "image_present": True,
                "containers": {},
                "volumes": {},
                "networks": {},
                "mutations": [],
            }
        )
    )
    return {"bin": binaries, "install": directory, "state": state, "empty": tmp_path / "absent"}


def run(
    sandbox: dict[str, Path],
    script: str,
    *arguments: str,
    entrypoint: Path | None = None,
    without_home: bool = False,
    explicit_directory: bool = True,
) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment.update(
        PATH=f"{sandbox['bin']}:/usr/bin:/bin",
        FAKE_STATE=str(sandbox["state"]),
        FAKE_REPO=str(ROOT),
        FAKE_IMAGE_ID=IMAGE,
        FAKE_EMPTY_ROOT=str(sandbox["empty"]),
        FAKE_VOLUMES=" ".join(VOLUMES),
        FAKE_NETWORKS=" ".join(NETWORKS),
        BONAFIDES_PORT="9999",
        HOME=str(sandbox["install"].parent / "home"),
    )
    if without_home:
        environment.pop("HOME", None)
    directory_arguments = ["--dir", str(sandbox["install"])] if explicit_directory else []
    return subprocess.run(
        ["/bin/sh", str(entrypoint or ROOT / "deploy" / script), *directory_arguments, *arguments],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )


def files(directory: Path) -> dict[str, tuple[bytes, int]]:
    return {
        str(path.relative_to(directory)): (
            path.read_bytes() if path.is_file() else b"",
            path.lstat().st_mode,
        )
        for path in directory.rglob("*")
    }


def install_assets(sandbox: dict[str, Path]) -> None:
    directory = sandbox["install"]
    assert (
        main(
            [
                "--release-root",
                str(directory),
                "--host-root",
                str(directory),
                "--assets",
                str(ROOT / "deploy"),
                "--project",
                PROJECT,
                "--port",
                "18400",
                "--image",
                IMAGE,
            ]
        )
        == 0
    )


def populate(sandbox: dict[str, Path]) -> dict[str, Any]:
    install_assets(sandbox)
    state: dict[str, Any] = json.loads(sandbox["state"].read_text())
    for kind, names in (("volume", VOLUMES), ("network", NETWORKS)):
        state[kind + "s"] = {
            f"{PROJECT}_{name}": {
                "labels": {
                    "com.docker.compose.project": PROJECT,
                    f"com.docker.compose.{kind}": name,
                    "io.bonafides.installation": str(sandbox["install"]),
                },
                "data": f"retained-{name}",
            }
            for name in names
        }
    state["containers"] = {
        "target-api": {
            "labels": {"com.docker.compose.project": PROJECT},
            "directory": str(sandbox["install"]),
            "running": True,
            "health": "healthy",
            "volumes": [f"{PROJECT}_blobs"],
            "networks": [f"{PROJECT}_private"],
        },
        "owner-api": {
            "labels": {"com.docker.compose.project": "bonafides-owner"},
            "directory": "/owner",
            "running": True,
            "health": "healthy",
            "volumes": ["owner-pg"],
            "networks": ["owner-private"],
        },
    }
    sandbox["state"].write_text(json.dumps(state))
    return state


def test_install_only_and_check_do_not_create_runtime_resources(sandbox: dict[str, Path]) -> None:
    before = files(sandbox["install"])
    state = sandbox["state"].read_text()
    result = run(sandbox, "install.sh", "--check", "--project", PROJECT, "--port", "18400", IMAGE)
    assert result.returncode == 0, result.stderr
    assert files(sandbox["install"]) == before
    assert sandbox["state"].read_text() == state
    result = run(sandbox, "install.sh", "--project", PROJECT, "--port", "18400", IMAGE)
    assert result.returncode == 0, result.stderr
    assert sandbox["state"].read_text() == state
    assert (sandbox["install"] / "secrets" / "keyring").is_file()
    assert (sandbox["install"] / ".installer-image").read_text().strip() == IMAGE


@pytest.mark.parametrize("source_installer", [False, True], ids=["image-owned", "source"])
def test_reinstall_accepts_equivalent_decimal_port_without_changing_saved_installation(
    sandbox: dict[str, Path], source_installer: bool
) -> None:
    result = run(sandbox, "install.sh", "--project", PROJECT, "--port", "8000", IMAGE)
    assert result.returncode == 0, result.stderr
    saved_files = files(sandbox["install"])
    saved_runtime = json.loads(sandbox["state"].read_text())
    entrypoint = ROOT / "install.sh" if source_installer else ROOT / "deploy" / "install.sh"
    # The source entrypoint must reuse its saved image without an explicit --image.
    image_arguments = [] if source_installer else [IMAGE]
    result = run(sandbox, "install.sh", "--port", "08000", *image_arguments, entrypoint=entrypoint)
    assert result.returncode == 0, result.stderr
    assert files(sandbox["install"]) == saved_files
    after = json.loads(sandbox["state"].read_text())
    for kind in ("containers", "volumes", "networks"):
        assert after[kind] == saved_runtime[kind]
    # A true reassignment must still refuse before even a temporary runtime mutation.
    persisted = sandbox["state"].read_text()
    for settings in (["--port", "8001"], ["--project", "bonafides-other"]):
        result = run(sandbox, "install.sh", *settings, *image_arguments, entrypoint=entrypoint)
        assert result.returncode != 0
        assert files(sandbox["install"]) == saved_files
        assert sandbox["state"].read_text() == persisted


@pytest.mark.parametrize("source_installer", [False, True], ids=["image-owned", "source"])
@pytest.mark.parametrize(
    "port", ["0008000", "00000", "+8000", " 8000", "08000x", "8000\n", "01023", "65536"]
)
def test_reinstall_rejects_invalid_raw_ports_without_normalizing_them_into_valid_input(
    sandbox: dict[str, Path], source_installer: bool, port: str
) -> None:
    result = run(sandbox, "install.sh", "--project", PROJECT, "--port", "8000", IMAGE)
    assert result.returncode == 0, result.stderr
    saved_files = files(sandbox["install"])
    persisted = sandbox["state"].read_text()
    entrypoint = ROOT / "install.sh" if source_installer else ROOT / "deploy" / "install.sh"
    image_arguments = [] if source_installer else [IMAGE]
    result = run(sandbox, "install.sh", "--port", port, *image_arguments, entrypoint=entrypoint)
    assert result.returncode != 0
    assert files(sandbox["install"]) == saved_files
    assert sandbox["state"].read_text() == persisted


@pytest.mark.parametrize(
    "source_wrapper", [False, True], ids=["installed-default", "source-explicit"]
)
@pytest.mark.parametrize("script", ["start.sh", "stop.sh", "uninstall.sh"])
def test_lifecycle_without_home_uses_only_the_selected_installation(
    sandbox: dict[str, Path], source_wrapper: bool, script: str
) -> None:
    before = populate(sandbox)
    before["volumes"]["owner-pg"] = {
        "labels": {"com.docker.compose.project": "bonafides-owner"},
        "data": "foreign-database",
    }
    before["networks"]["owner-private"] = {
        "labels": {"com.docker.compose.project": "bonafides-owner"}
    }
    if script == "start.sh":
        del before["containers"]["target-api"]
    sandbox["state"].write_text(json.dumps(before))
    saved_files = files(sandbox["install"])
    entrypoint = ROOT / script if source_wrapper else sandbox["install"] / script
    result = run(
        sandbox,
        script,
        *(["--yes"] if script == "uninstall.sh" else []),
        entrypoint=entrypoint,
        without_home=True,
        explicit_directory=source_wrapper,
    )
    assert result.returncode == 0, result.stderr
    after = json.loads(sandbox["state"].read_text())
    assert after["containers"]["owner-api"] == before["containers"]["owner-api"]
    assert after["volumes"] == before["volumes"]
    assert files(sandbox["install"]) == saved_files
    if script == "start.sh":
        targets = {
            name: item
            for name, item in after["containers"].items()
            if item["labels"].get("com.docker.compose.project") == PROJECT
        }
        assert len(targets) == 8
        assert all(item["running"] for item in targets.values())
        assert all(item["directory"] == str(sandbox["install"]) for item in targets.values())
        assert after["networks"] == before["networks"]
    elif script == "stop.sh":
        assert set(after["containers"]) == set(before["containers"])
        assert not after["containers"]["target-api"]["running"]
        assert after["networks"] == before["networks"]
    else:
        assert after["containers"] == {"owner-api": before["containers"]["owner-api"]}
        assert after["networks"] == {"owner-private": before["networks"]["owner-private"]}


@pytest.mark.parametrize("script", ["start.sh", "stop.sh", "uninstall.sh"])
def test_source_lifecycle_without_home_or_directory_refuses_without_writes(
    sandbox: dict[str, Path], script: str
) -> None:
    populate(sandbox)
    saved_files = files(sandbox["install"])
    persisted = sandbox["state"].read_text()
    result = run(
        sandbox,
        script,
        *(["--yes"] if script == "uninstall.sh" else []),
        entrypoint=ROOT / script,
        without_home=True,
        explicit_directory=False,
    )
    assert result.returncode != 0
    assert sandbox["state"].read_text() == persisted
    assert files(sandbox["install"]) == saved_files


def test_install_refuses_running_target_without_changing_files_or_runtime(
    sandbox: dict[str, Path],
) -> None:
    populate(sandbox)
    before = files(sandbox["install"])
    state = sandbox["state"].read_text()
    for flag in ([], ["--check"]):
        result = run(sandbox, "install.sh", *flag, IMAGE)
        assert result.returncode != 0
        assert files(sandbox["install"]) == before
        assert sandbox["state"].read_text() == state


@pytest.mark.parametrize(
    "damage", ["foreign_volume", "foreign_consumer", "other_directory", "unknown_volume"]
)
@pytest.mark.parametrize("root_without_home", [False, True], ids=["image-owned", "source-no-home"])
def test_destructive_commands_preflight_every_resource_before_any_mutation(
    sandbox: dict[str, Path], damage: str, root_without_home: bool
) -> None:
    state = populate(sandbox)
    if damage == "foreign_volume":
        state["volumes"][f"{PROJECT}_spool"]["labels"]["io.bonafides.installation"] = "/foreign"
    elif damage == "foreign_consumer":
        state["containers"]["owner-api"]["volumes"].append(f"{PROJECT}_spool")
    elif damage == "other_directory":
        state["containers"]["target-api"]["directory"] = "/another-installation"
    else:
        state["volumes"][f"{PROJECT}_unexpected"] = {
            "labels": {"com.docker.compose.project": PROJECT}
        }
    sandbox["state"].write_text(json.dumps(state))
    before = files(sandbox["install"])
    persisted = sandbox["state"].read_text()
    for script, options in (
        ("start.sh", []),
        ("stop.sh", []),
        ("uninstall.sh", ["--yes"]),
        ("uninstall.sh", ["--yes", "--purge", "--confirm-purge", PROJECT]),
    ):
        result = run(
            sandbox,
            script,
            *options,
            entrypoint=ROOT / script if root_without_home else None,
            without_home=root_without_home,
        )
        assert result.returncode != 0
        assert sandbox["state"].read_text() == persisted
        assert files(sandbox["install"]) == before


def test_stop_preserves_containers_volumes_and_files_without_application_image(
    sandbox: dict[str, Path],
) -> None:
    before = populate(sandbox)
    before["image_present"] = False
    sandbox["state"].write_text(json.dumps(before))
    saved_files = files(sandbox["install"])
    result = run(sandbox, "stop.sh")
    assert result.returncode == 0, result.stderr
    after = json.loads(sandbox["state"].read_text())
    assert set(after["containers"]) == set(before["containers"])
    assert not after["containers"]["target-api"]["running"]
    assert after["containers"]["owner-api"] == before["containers"]["owner-api"]
    assert after["volumes"] == before["volumes"]
    assert after["networks"] == before["networks"]
    assert files(sandbox["install"]) == saved_files


def test_uninstall_retains_data_and_supports_install_again_without_key_rotation(
    sandbox: dict[str, Path],
) -> None:
    before = populate(sandbox)
    before["image_present"] = False
    sandbox["state"].write_text(json.dumps(before))
    saved_files = files(sandbox["install"])
    result = run(sandbox, "uninstall.sh", "--yes")
    assert result.returncode == 0, result.stderr
    after = json.loads(sandbox["state"].read_text())
    assert after["containers"] == {"owner-api": before["containers"]["owner-api"]}
    assert after["volumes"] == before["volumes"]
    assert after["networks"] == {}
    assert files(sandbox["install"]) == saved_files
    after["image_present"] = True
    sandbox["state"].write_text(json.dumps(after))
    result = run(sandbox, "install.sh", IMAGE)
    assert result.returncode == 0, result.stderr
    assert files(sandbox["install"]) == saved_files


@pytest.mark.parametrize("confirmation", [[], ["--confirm-purge", "bonafides-other"]])
def test_yes_does_not_authorize_purge(sandbox: dict[str, Path], confirmation: list[str]) -> None:
    populate(sandbox)
    before = sandbox["state"].read_text()
    saved_files = files(sandbox["install"])
    result = run(sandbox, "uninstall.sh", "--yes", "--purge", *confirmation)
    assert result.returncode != 0
    assert sandbox["state"].read_text() == before
    assert files(sandbox["install"]) == saved_files


def test_confirmed_purge_removes_only_managed_owned_resources_without_image(
    sandbox: dict[str, Path],
) -> None:
    before = populate(sandbox)
    before["image_present"] = False
    sandbox["state"].write_text(json.dumps(before))
    directory = sandbox["install"]
    (directory / "personal-backup").write_text("leave root backup")
    (directory / "secrets" / "personal-key").write_text("leave extra secret")
    route = directory / "secrets" / "local_model_route.json"
    route.write_text("synthetic local model authorization")
    route.chmod(0o600)
    result = run(sandbox, "uninstall.sh", "--yes", "--purge", "--confirm-purge", PROJECT)
    assert result.returncode == 0, result.stderr
    after = json.loads(sandbox["state"].read_text())
    assert after["containers"] == {"owner-api": before["containers"]["owner-api"]}
    assert after["volumes"] == after["networks"] == {}
    assert {path.name for path in directory.iterdir()} == {"personal-backup", "secrets"}
    assert (directory / "personal-backup").read_text() == "leave root backup"
    assert {path.name for path in (directory / "secrets").iterdir()} == {"personal-key"}
    assert (directory / "secrets" / "personal-key").read_text() == "leave extra secret"


def test_purge_refuses_linked_managed_secret_before_runtime_removal(
    sandbox: dict[str, Path],
) -> None:
    populate(sandbox)
    directory = sandbox["install"]
    victim = directory / "unrelated"
    victim.write_text("keep")
    keyring = directory / "secrets" / "keyring"
    keyring.unlink()
    keyring.symlink_to(victim)
    before = sandbox["state"].read_text()
    saved_files = files(directory)
    result = run(sandbox, "uninstall.sh", "--yes", "--purge", "--confirm-purge", PROJECT)
    assert result.returncode != 0
    assert sandbox["state"].read_text() == before
    assert files(directory) == saved_files


def test_start_refuses_invalid_credentials_before_creating_resources(
    sandbox: dict[str, Path],
) -> None:
    install_assets(sandbox)
    directory = sandbox["install"]
    (directory / "secrets" / "keyring").write_text("invalid\n")
    before = sandbox["state"].read_text()
    saved_files = files(directory)
    result = run(sandbox, "start.sh")
    assert result.returncode != 0
    assert sandbox["state"].read_text() == before
    assert files(directory) == saved_files


@pytest.mark.parametrize("owner", [None, "bonafides-foreign"])
def test_new_install_refuses_physical_volume_collision_not_found_by_project_label(
    sandbox: dict[str, Path], owner: str | None
) -> None:
    state = json.loads(sandbox["state"].read_text())
    labels = {} if owner is None else {"com.docker.compose.project": owner}
    state["volumes"][f"{PROJECT}_pg"] = {"labels": labels, "data": "foreign-database"}
    sandbox["state"].write_text(json.dumps(state))
    persisted = sandbox["state"].read_text()
    before = files(sandbox["install"])
    result = run(sandbox, "install.sh", "--project", PROJECT, "--port", "18400", IMAGE)
    assert result.returncode != 0
    assert sandbox["state"].read_text() == persisted
    assert files(sandbox["install"]) == before


def test_environment_configuration_is_not_executed(sandbox: dict[str, Path]) -> None:
    populate(sandbox)
    directory = sandbox["install"]
    marker = directory / "executed"
    environment = directory / ".env"
    environment.write_text(environment.read_text() + f"UNTRUSTED=$(touch '{marker}')\n")
    before = sandbox["state"].read_text()
    saved_files = files(directory)
    result = run(sandbox, "uninstall.sh", "--yes")
    assert result.returncode != 0
    assert not marker.exists()
    assert sandbox["state"].read_text() == before
    assert files(directory) == saved_files
