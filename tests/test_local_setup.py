# SPDX-License-Identifier: MIT OR Apache-2.0
from __future__ import annotations

import errno
import json
import os
import shutil
import stat
from pathlib import Path
from typing import BinaryIO

import pytest

from bonafides import local_setup
from bonafides.local_setup import SECRET_NAMES, main

HOST_ROOT = Path("/srv/host/bonafides")


@pytest.fixture
def assets(tmp_path: Path) -> Path:
    directory = tmp_path / "assets"
    directory.mkdir()
    (directory / "compose.release.yaml").write_text("services: {}\n# build one\n")
    (directory / "init-db.sh").write_text("#!/bin/sh\n# build one\n")
    for name in ("start.sh", "stop.sh", "uninstall.sh", "lifecycle.sh"):
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


def test_start_validation_refuses_changed_assets_without_republishing(
    root: Path, assets: Path
) -> None:
    assert install(root, assets) == 0
    (root / "compose.yaml").write_text("services: {foreign: {image: foreign}}\n")
    before = snapshot(root)
    assert install(root, assets, "--check", "--validate-installed") == 1
    assert snapshot(root) == before


@pytest.mark.parametrize(
    "name",
    [".installer-image.new", ".stop.sh.new", ".compose.yaml.new", "stop.sh", "compose.yaml"],
)
def test_new_install_refuses_unmanaged_destinations_without_creating_keys(
    root: Path, assets: Path, name: str
) -> None:
    (root / name).write_text("unrelated\n")
    before = snapshot(root)
    assert install(root, assets) == 1
    assert snapshot(root) == before


def test_missing_image_asset_is_refused_before_credential_creation(
    root: Path, assets: Path
) -> None:
    (assets / "stop.sh").unlink()
    before = snapshot(root)
    assert install(root, assets) == 1
    assert snapshot(root) == before
