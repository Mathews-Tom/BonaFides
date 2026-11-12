# SPDX-License-Identifier: MIT OR Apache-2.0
from __future__ import annotations

import errno
import json
import os
import shutil
import stat
from dataclasses import replace
from pathlib import Path
from typing import BinaryIO

import pytest

from bonafides import local_setup
from bonafides.config import ConfigurationError, Settings
from bonafides.crypto import Keyring
from bonafides.local_setup import SECRET_NAMES, main

HOST_ROOT = Path("/srv/host/bonafides")


@pytest.fixture
def assets(tmp_path: Path) -> Path:
    directory = tmp_path / "assets"
    directory.mkdir()
    (directory / "compose.release.yaml").write_text("services: {}\n# build one\n")
    (directory / "compose.proxy.yaml").write_text("services: {}\n")
    (directory / "init-db.sh").write_text("#!/bin/sh\n# build one\n")
    for name in ("start.sh", "stop.sh", "uninstall.sh", "lifecycle.sh", "models.sh"):
        (directory / name).write_text("#!/bin/sh\n# build one\n")
    return directory


@pytest.fixture
def root(tmp_path: Path) -> Path:
    directory = tmp_path / "install"
    directory.mkdir()
    return directory


def install(root: Path, assets: Path, *extra: str) -> int:
    return main(
        [
            "--release-root",
            str(root),
            "--host-root",
            str(HOST_ROOT),
            "--assets",
            str(assets),
            "--project",
            "bonafides-test",
            "--port",
            "18400",
            "--image",
            "sha256:" + "1" * 64,
            *extra,
        ]
    )


def snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        str(path.relative_to(root)): (
            path.read_bytes() if path.is_file() else b"",
            path.stat().st_mode,
        )
        for path in sorted(root.rglob("*"))
    }


def test_release_install_creates_private_files_and_keeps_the_host_secret_path(
    root: Path, assets: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert install(root, assets) == 0

    out, err = capsys.readouterr()
    token = (root / "secrets" / "bootstrap_token").read_text().strip()
    assert out.strip() == "created" and token not in out + err
    assert stat.S_IMODE((root / "secrets").stat().st_mode) == 0o700
    assert {path.name for path in (root / "secrets").iterdir()} == set(SECRET_NAMES)
    assert all(
        stat.S_IMODE((root / "secrets" / name).stat().st_mode) == 0o600 for name in SECRET_NAMES
    )
    environment = (root / ".env").read_text()
    assert f"BONAFIDES_SECRET_DIR='{HOST_ROOT / 'secrets'}'\n" in environment
    assert "BONAFIDES_MODE=production" in environment
    assert (root / "compose.yaml").read_text().endswith("# build one\n")
    assert stat.S_IMODE((root / "init-db.sh").stat().st_mode) == 0o755


def test_release_rerun_reuses_credentials_and_replaces_only_image_owned_assets(
    root: Path, assets: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    install(root, assets)
    capsys.readouterr()
    credentials = snapshot(root / "secrets")
    (assets / "compose.release.yaml").write_text("services: {}\n# build two\n")

    assert install(root, assets) == 0

    assert capsys.readouterr().out.strip() == "reused"
    assert snapshot(root / "secrets") == credentials
    assert (root / "compose.yaml").read_text().endswith("# build two\n")


@pytest.mark.parametrize(
    "origin",
    [
        "http://public.example",
        "https://public.example/",
        "https://PUBLIC.example",
        "https://public.example:443",
        "https://user@public.example",
        "http://localhost:18400",
        "https://public.example:",
        "https://public.example:08443",
        "https://public.example:0",
        "https://public.example:65536",
        "https://public.example?x=1",
        "https://public.example#fragment",
        "https://public.example\\foreign",
        "https://public.example%0a",
        "https://public.example\nBONAFIDES_MODE=benchmark",
        "https://public.example\t",
        "https://public.example\x00",
        "https://públic.example",
        "https://${PUBLIC_HOST}",
        "https://public..example",
        "https://-public.example",
        "https://public-.example",
        "https://127.1",
        "https://0177.0.0.1",
        "https://0x7f000001",
        "https://2130706433",
        "https://999.1.1.1",
    ],
)
def test_release_refuses_noncanonical_public_origin_without_writes(
    root: Path, assets: Path, origin: str
) -> None:
    before = snapshot(root)
    assert install(root, assets, "--origin", origin) == 1
    assert snapshot(root) == before


def test_release_public_origin_reuse_and_conflict_preserve_credentials(
    root: Path, assets: Path
) -> None:
    assert install(root, assets, "--origin", "https://career.example") == 0
    before = snapshot(root)
    assert install(root, assets, "--check", "--validate-installed") == 0
    assert install(root, assets) == 0
    assert snapshot(root) == before
    assert install(root, assets, "--origin", "https://other.example") == 1
    assert snapshot(root) == before


@pytest.mark.parametrize(
    "origin",
    [
        "https://career.example:8443",
        "https://127.0.0.1",
        "https://[::1]:8443",
        "https://[2001:db8::1]",
        "https://internal.0xyz",
    ],
)
def test_release_reuses_nondefault_https_authorities(root: Path, assets: Path, origin: str) -> None:
    assert install(root, assets, "--origin", origin) == 0
    assert f"BONAFIDES_ORIGIN={origin}\n" in (root / ".env").read_text()
    before = snapshot(root)
    assert install(root, assets, "--origin", origin) == 0
    assert install(root, assets, "--check", "--validate-installed") == 0
    assert snapshot(root) == before


@pytest.mark.parametrize("network_id", ["a" * 64, "b" * 25])
def test_release_proxy_network_intent_is_optional_saved_and_immutable(
    root: Path, assets: Path, network_id: str
) -> None:
    selection = (
        "--origin",
        "https://career.example",
        "--proxy-network",
        "operator.proxy-1",
        "--proxy-network-id",
        network_id,
    )
    assert install(root, assets, *selection) == 0
    environment = (root / ".env").read_text()
    assert environment.endswith(
        f"BONAFIDES_PROXY_NETWORK=operator.proxy-1\nBONAFIDES_PROXY_NETWORK_ID={network_id}\n"
    )
    before = snapshot(root)
    assert install(root, assets) == 0
    assert install(root, assets, *selection, "--check", "--validate-installed") == 0
    assert snapshot(root) == before
    for option in (
        ("--origin", "https://other.example"),
        ("--proxy-network", "another.proxy"),
        ("--proxy-network-id", "c" * 64),
        ("--proxy-network", ""),
    ):
        assert install(root, assets, *option) == 1
        assert snapshot(root) == before


@pytest.mark.parametrize(
    ("origin", "network", "network_id"),
    [
        (None, "operator", "a" * 64),
        ("https://career.example", "operator", ""),
        ("https://career.example", "", "a" * 64),
        ("https://career.example", "operator", "a" * 63),
        ("https://career.example", "operator", "A" * 64),
        ("https://career.example", "../operator", "a" * 64),
        ("https://career.example", "operator\nBONAFIDES_MODE=benchmark", "a" * 64),
        ("https://career.example", "${OPERATOR}", "a" * 64),
    ],
)
def test_release_refuses_unsafe_or_incomplete_proxy_intent_before_writes(
    root: Path, assets: Path, origin: str | None, network: str, network_id: str
) -> None:
    selection = ["--proxy-network", network, "--proxy-network-id", network_id]
    if origin is not None:
        selection += ["--origin", origin]
    before = snapshot(root)
    assert install(root, assets, *selection) == 1
    assert snapshot(root) == before


@pytest.mark.parametrize(
    "extra",
    [
        "BONAFIDES_ORIGIN=https://career.example",
        "BONAFIDES_PROXY_NETWORK=operator",
        "BONAFIDES_PROXY_NETWORK_ID=" + "a" * 64,
        "BONAFIDES_MODE=benchmark",
        "BONAFIDES_UNKNOWN=unmanaged",
    ],
)
def test_release_refuses_duplicate_or_unmanaged_saved_deployment_settings(
    root: Path, assets: Path, extra: str
) -> None:
    assert (
        install(
            root,
            assets,
            "--origin",
            "https://career.example",
            "--proxy-network",
            "operator",
            "--proxy-network-id",
            "a" * 64,
        )
        == 0
    )
    environment = root / ".env"
    environment.write_text(environment.read_text() + extra + "\n")
    before = snapshot(root)
    assert install(root, assets, "--check", "--validate-installed") == 1
    assert install(root, assets) == 1
    assert snapshot(root) == before


def test_local_cli_and_helpers_preserve_the_selected_deployment(tmp_path: Path) -> None:
    directory = tmp_path / "secrets"
    environment = tmp_path / ".env"
    assert (
        main(
            [
                "--directory",
                str(directory),
                "--env-file",
                str(environment),
                "--project",
                "bonafides-test",
                "--port",
                "18400",
                "--origin",
                "https://career.example",
                "--proxy-network",
                "operator",
                "--proxy-network-id",
                "a" * 64,
            ]
        )
        == 0
    )
    before = snapshot(tmp_path)
    local_setup.verify(directory, environment, "bonafides-test", 18400, False, directory)
    assert (
        local_setup.ensure(
            directory, environment, "bonafides-test", 18400, False, published=directory
        )
        == "reused"
    )
    assert snapshot(tmp_path) == before


def test_default_loopback_environment_remains_byte_compatible(root: Path, assets: Path) -> None:
    assert install(root, assets) == 0
    assert (root / ".env").read_bytes() == (
        f"COMPOSE_PROJECT_NAME=bonafides-test\nBONAFIDES_SECRET_DIR='{HOST_ROOT / 'secrets'}'\n"
        "BONAFIDES_PORT=18400\nBONAFIDES_ORIGIN=http://localhost:18400\n"
        "BONAFIDES_MODE=production\n"
    ).encode()


@pytest.mark.parametrize("operation", ["copy", "chmod", "replace"])
def test_release_publication_failure_preserves_install_and_allows_retry(
    root: Path,
    assets: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    assert install(root, assets) == 0
    before = snapshot(root)
    credentials = snapshot(root / "secrets")
    environment = (root / ".env").read_bytes()
    (assets / "compose.release.yaml").write_text("services: {}\n# build two\n")
    failure = OSError(errno.ENOSPC, "publication storage exhausted")

    with monkeypatch.context() as patch:
        if operation == "copy":

            def fail_copy(asset: BinaryIO, stream: BinaryIO) -> None:
                stream.write(asset.read(8))
                stream.flush()
                raise failure

            patch.setattr(shutil, "copyfileobj", fail_copy)
        elif operation == "chmod":

            def fail_chmod(descriptor: int, mode: int) -> None:
                raise failure

            patch.setattr(os, "fchmod", fail_chmod)
        else:
            replace = Path.replace

            def fail_replace(path: Path, target: Path) -> Path:
                if path == root / ".compose.yaml.new":
                    raise failure
                return replace(path, target)

            patch.setattr(Path, "replace", fail_replace)

        with pytest.raises(OSError) as raised:
            local_setup.publish_assets(assets, root)
        assert raised.value is failure

    assert snapshot(root) == before
    assert not (root / ".compose.yaml.new").exists()

    assert install(root, assets) == 0
    assert (root / "compose.yaml").read_bytes() == (assets / "compose.release.yaml").read_bytes()
    assert snapshot(root / "secrets") == credentials
    assert (root / ".env").read_bytes() == environment
    assert install(root, assets, "--check", "--validate-installed") == 0


@pytest.mark.parametrize(
    "damage", ["missing_secret", "missing_environment", "loose_mode", "wrong_dsn"]
)
def test_release_refuses_partial_or_invalid_state_without_changing_it(
    root: Path,
    assets: Path,
    capsys: pytest.CaptureFixture[str],
    damage: str,
) -> None:
    install(root, assets)
    secrets_dir = root / "secrets"
    if damage == "missing_secret":
        (secrets_dir / "keyring").unlink()
    elif damage == "missing_environment":
        (root / ".env").unlink()
    elif damage == "loose_mode":
        (secrets_dir / "keyring").chmod(0o644)
    else:
        (secrets_dir / "runtime_dsn").chmod(0o600)
        (secrets_dir / "runtime_dsn").write_text("postgresql+psycopg://other@db/bonafides\n")
    token = (secrets_dir / "bootstrap_token").read_text().strip()
    capsys.readouterr()
    (assets / "compose.release.yaml").write_text("services: {}\n# build two\n")
    before = snapshot(root)

    assert install(root, assets) == 1

    out, err = capsys.readouterr()
    assert out == "" and err.startswith("Local setup refused: ") and token not in err
    assert snapshot(root) == before


@pytest.mark.parametrize("option", [("--port", "18401"), ("--project", "bonafides-other")])
def test_release_refuses_a_different_project_or_port_than_the_installed_state(
    root: Path, assets: Path, capsys: pytest.CaptureFixture[str], option: tuple[str, str]
) -> None:
    install(root, assets)
    capsys.readouterr()
    before = snapshot(root)

    assert install(root, assets, *option) == 1

    assert snapshot(root) == before


def test_release_refuses_a_keyring_with_any_invalid_key_without_changing_state(
    root: Path, assets: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    install(root, assets)
    keyring = root / "secrets" / "keyring"
    document = json.loads(keyring.read_text())
    document["keys"]["bad key!"] = document["keys"]["local-1"]
    keyring.write_text(json.dumps(document) + "\n")
    capsys.readouterr()
    before = snapshot(root)

    assert install(root, assets) == 1

    assert "keyring" in capsys.readouterr().err
    assert snapshot(root) == before


def test_release_refuses_an_install_path_containing_a_single_quote(
    root: Path, assets: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--release-root", str(root), "--host-root", "/srv/it's", "--assets", str(assets)])

    assert code == 1
    assert not (root / "secrets").exists() or not (root / ".env").exists()


def test_release_publish_never_follows_a_planted_link_at_the_temporary_path(
    root: Path, assets: Path, tmp_path: Path
) -> None:
    victim = tmp_path / "victim"
    victim.write_text("keep")
    (root / ".compose.yaml.new").symlink_to(victim)

    assert install(root, assets) == 1

    assert victim.read_text() == "keep"
    assert (root / ".compose.yaml.new").is_symlink()
    assert not (root / "secrets").exists()
    assert not (root / ".env").exists()


@pytest.mark.parametrize("linked", [False, True])
def test_asset_publication_preserves_a_preplanted_temporary_path_on_exclusive_open_failure(
    root: Path, assets: Path, tmp_path: Path, linked: bool
) -> None:
    victim = tmp_path / "victim"
    victim.write_text("keep")
    temporary = root / ".compose.yaml.new"
    if linked:
        temporary.symlink_to(victim)
    else:
        temporary.write_text("unrelated")
    before = snapshot(root)

    with pytest.raises(FileExistsError):
        local_setup.publish_assets(assets, root)

    assert snapshot(root) == before
    assert temporary.is_symlink() is linked
    assert victim.read_text() == "keep"


def test_release_check_is_read_only_for_new_and_retained_installs(root: Path, assets: Path) -> None:
    before = snapshot(root)
    assert install(root, assets, "--check") == 0
    assert snapshot(root) == before
    assert install(root, assets) == 0
    before = snapshot(root)
    assert install(root, assets, "--check", "--validate-installed") == 0
    assert snapshot(root) == before


@pytest.mark.parametrize(
    "damage",
    ["metadata_root", "metadata_volume", "missing_metadata", "linked_asset", "asset_directory"],
)
def test_release_refuses_uncertain_ownership_before_any_write(
    root: Path, assets: Path, tmp_path: Path, damage: str
) -> None:
    assert install(root, assets) == 0
    metadata = root / ".installer-state"
    if damage == "metadata_root":
        metadata.write_text(metadata.read_text().replace(str(HOST_ROOT), "/srv/another-install"))
    elif damage == "metadata_volume":
        metadata.write_text(metadata.read_text().replace("bonafides-test_pg", "foreign_pg"))
    elif damage == "missing_metadata":
        metadata.unlink()
    else:
        (root / "compose.yaml").unlink()
        if damage == "asset_directory":
            (root / "compose.yaml").mkdir()
        else:
            victim = tmp_path / "foreign-compose"
            victim.write_text("foreign")
            (root / "compose.yaml").symlink_to(victim)
    before = snapshot(root)
    assert install(root, assets) == 1
    assert snapshot(root) == before


@pytest.mark.parametrize("name", ["compose.yaml", "compose.proxy.yaml"])
def test_start_validation_refuses_changed_assets_without_republishing(
    root: Path, assets: Path, name: str
) -> None:
    assert install(root, assets) == 0
    (root / name).write_text("services: {foreign: {image: foreign}}\n")
    before = snapshot(root)
    assert install(root, assets, "--check", "--validate-installed") == 1
    assert snapshot(root) == before


@pytest.mark.parametrize(
    "name",
    [
        ".installer-image.new",
        ".stop.sh.new",
        ".compose.yaml.new",
        ".compose.proxy.yaml.new",
        "stop.sh",
        "compose.yaml",
        "compose.proxy.yaml",
    ],
)
def test_new_install_refuses_unmanaged_destinations_without_creating_keys(
    root: Path, assets: Path, name: str
) -> None:
    (root / name).write_text("unrelated\n")
    before = snapshot(root)
    assert install(root, assets) == 1
    assert snapshot(root) == before


@pytest.mark.parametrize("name", ["stop.sh", "compose.proxy.yaml"])
def test_missing_image_asset_is_refused_before_credential_creation(
    root: Path, assets: Path, name: str
) -> None:
    (assets / name).unlink()
    before = snapshot(root)
    assert install(root, assets) == 1
    assert snapshot(root) == before


def test_reinstall_preserves_explicit_model_opt_in_and_credentials(
    root: Path, assets: Path
) -> None:
    assert install(root, assets) == 0
    environment = root / ".env"
    environment.write_text(environment.read_text() + local_setup.MODEL_ROUTE_ENV + "\n")
    before = snapshot(root / "secrets")
    expected = environment.read_bytes()

    assert install(root, assets) == 0

    assert environment.read_bytes() == expected
    assert snapshot(root / "secrets") == before


@pytest.mark.parametrize(
    "extra",
    [
        "BONAFIDES_LOCAL_MODEL_ROUTE_FILE=/tmp/other.json",
        "BONAFIDES_LOCAL_MODEL_ROUTE_FILE=",
        f"{local_setup.MODEL_ROUTE_ENV}\nBONAFIDES_DATABASE_URL=foreign",
        f"{local_setup.MODEL_ROUTE_ENV}\n{local_setup.MODEL_ROUTE_ENV}",
    ],
)
def test_reinstall_refuses_unmanaged_route_environment(
    root: Path, assets: Path, extra: str
) -> None:
    assert install(root, assets) == 0
    environment = root / ".env"
    environment.write_text(environment.read_text() + extra + "\n")
    before = snapshot(root)

    assert install(root, assets) == 1

    assert snapshot(root) == before


@pytest.mark.parametrize(
    "origin",
    [
        "https://public..example",
        "https://-public.example",
        "https://public-.example",
        "https://127.1",
        "https://0177.0.0.1",
        "https://0x7f000001",
        "https://2130706433",
        "https://999.1.1.1",
    ],
)
def test_runtime_refuses_unusable_or_browser_rewritten_origins(tmp_path: Path, origin: str) -> None:
    roots = [tmp_path / name for name in ("blobs", "render", "import")]
    for directory in roots:
        directory.mkdir()
    settings = Settings(
        database_dsn="postgresql+psycopg://runtime:synthetic@db:5432/bonafides",
        keyring=Keyring("test", {"test": b"a" * 32}),
        origin="https://career.example",
        blob_root=roots[0],
        renderer_spool=roots[1],
        import_spool=roots[2],
    )
    with pytest.raises(ConfigurationError):
        replace(settings, origin=origin)


@pytest.mark.parametrize("linked", ["directory", "environment"])
def test_local_initialize_refuses_dangling_links_before_creating_credentials(
    tmp_path: Path, linked: str
) -> None:
    directory = tmp_path / "secrets"
    environment = tmp_path / ".env"
    missing = tmp_path / "missing"
    planted = directory if linked == "directory" else environment
    planted.symlink_to(missing)
    with pytest.raises(local_setup.SetupError):
        local_setup.initialize(directory, environment, "bonafides-test", 18400, False)
    assert planted.is_symlink()
    assert not missing.exists()
    if linked == "environment":
        assert not directory.exists()
    else:
        assert not environment.exists()


@pytest.mark.parametrize("character", ["\n", "\r", "\t"])
def test_local_initialize_refuses_environment_path_injection_before_writes(
    tmp_path: Path, character: str
) -> None:
    directory = tmp_path / f"secrets{character}BONAFIDES_MODE=benchmark"
    environment = tmp_path / ".env"
    assert main(["--directory", str(directory), "--env-file", str(environment)]) == 1
    assert list(tmp_path.iterdir()) == []
