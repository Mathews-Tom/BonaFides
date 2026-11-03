# SPDX-License-Identifier: MIT OR Apache-2.0
from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from bonafides.local_setup import SECRET_NAMES, main

HOST_ROOT = Path("/srv/host/bonafides")


@pytest.fixture
def assets(tmp_path: Path) -> Path:
    directory = tmp_path / "assets"
    directory.mkdir()
    (directory / "compose.release.yaml").write_text("services: {}\n# build one\n")
    (directory / "init-db.sh").write_text("#!/bin/sh\n# build one\n")
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

    assert "differs from the requested project, port" in capsys.readouterr().err
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

    assert code == 1 and "single quote" in capsys.readouterr().err
    assert not (root / "secrets").exists() or not (root / ".env").exists()


def test_release_publish_never_follows_a_planted_link_at_the_temporary_path(
    root: Path, assets: Path, tmp_path: Path
) -> None:
    victim = tmp_path / "victim"
    victim.write_text("keep")
    (root / ".compose.yaml.new").symlink_to(victim)

    assert install(root, assets) == 0

    assert victim.read_text() == "keep"
    assert (root / "compose.yaml").read_text().endswith("# build one\n")
