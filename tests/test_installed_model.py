# SPDX-License-Identifier: MIT OR Apache-2.0
"""Synthetic installed-model metadata only; no downloads or inference."""

from __future__ import annotations

import json
import os
import socket
import stat
from pathlib import Path
from typing import cast

import pytest

from bonafides import installed_model
from bonafides.local_model import (
    PROMPT_VERSIONS,
    LocalRoute,
    ModelFailure,
    load_route,
    qualification_receipt,
)

MODEL = "qwen2.5:7b-instruct"
DIGEST = "ab" * 32
Metadata = dict[str, dict[str, object]]


@pytest.fixture
def metadata(monkeypatch: pytest.MonkeyPatch) -> Metadata:
    documents: Metadata = {
        "/api/tags": {"models": [{"name": MODEL, "digest": DIGEST}]},
        "/api/version": {"version": "0.6.8-synthetic"},
    }

    def resolve(
        host: str, port: int, *, type: int
    ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        return [(2, 1, 6, "", ("172.19.0.3", 11434))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)

    def read(route: LocalRoute, path: str) -> dict[str, object]:
        return documents[path]

    monkeypatch.setattr(installed_model, "_metadata", read)
    return documents


def invoke(path: Path, model: str = MODEL, authorized: bool = True) -> int:
    args = ["attach", "--route", str(path), "--model", model, "--model-license", "Apache-2.0"]
    return installed_model.main(args + (["--authorize-local-model"] if authorized else []))


def test_attaches_real_manifest_and_runtime_without_qualification(
    tmp_path: Path, metadata: Metadata
) -> None:
    path = tmp_path / "route.json"
    assert invoke(path) == 0
    route = load_route(path)
    assert route is not None
    assert not any(route.qualified_for(stage) for stage in PROMPT_VERSIONS)
    assert json.loads(path.read_text())["qualification_receipts"] == {}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("failure", ["absent", "digest", "version", "cloud", "duplicate", "models"])
def test_bad_metadata_never_publishes_route(
    tmp_path: Path, metadata: Metadata, failure: str
) -> None:
    models = cast(list[dict[str, object]], metadata["/api/tags"]["models"])
    model = models[0]
    if failure == "absent":
        metadata["/api/tags"]["models"] = []
    elif failure == "digest":
        model["digest"] = "abc123"
    elif failure == "version":
        metadata["/api/version"] = {"version": None}
    elif failure == "cloud":
        model["remote_host"] = "https://synthetic.invalid"
    elif failure == "duplicate":
        models.append(dict(model))
    else:
        metadata["/api/tags"]["models"] = "not-a-list"
    assert invoke(tmp_path / "route.json") == 2
    assert not list(tmp_path.iterdir())


def test_requires_explicit_authorization(tmp_path: Path) -> None:
    assert invoke(tmp_path / "route.json", authorized=False) == 2
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("symlink", [False, True])
def test_refuses_existing_file_or_dangling_symlink(tmp_path: Path, symlink: bool) -> None:
    path = tmp_path / "route.json"
    if symlink:
        path.symlink_to(tmp_path / "missing")
    else:
        path.write_text("owner file")
    assert invoke(path) == 2
    assert path.is_symlink() if symlink else path.read_text() == "owner file"


def test_rejects_public_dns_without_metadata(
    tmp_path: Path, metadata: Metadata, monkeypatch: pytest.MonkeyPatch
) -> None:
    def resolve(
        host: str, port: int, *, type: int
    ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        return [(2, 1, 6, "", ("8.8.8.8", 11434))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    assert invoke(tmp_path / "route.json") == 2
    assert not list(tmp_path.iterdir())


def test_atomic_publication_never_overwrites_racing_entry(
    tmp_path: Path, metadata: Metadata, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "route.json"
    link = os.link

    def race(
        src: str, dst: str, *, src_dir_fd: int, dst_dir_fd: int, follow_symlinks: bool
    ) -> None:
        path.symlink_to(tmp_path / "missing")
        link(
            src,
            dst,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    monkeypatch.setattr(os, "link", race)
    assert invoke(path) == 2
    assert path.is_symlink()
    assert list(tmp_path.iterdir()) == [path]


def test_metadata_unavailable_cleans_staging_file(
    tmp_path: Path, metadata: Metadata, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable(route: LocalRoute, path: str) -> dict[str, object]:
        raise OSError("synthetic sensitive metadata must not be printed")

    monkeypatch.setattr(installed_model, "_metadata", unavailable)
    assert invoke(tmp_path / "route.json") == 2
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("result", [0, 1])
def test_process_exit_without_new_receipt_is_not_a_completed_result(
    tmp_path: Path, metadata: Metadata, result: int
) -> None:
    path, original = tmp_path / "route.json", tmp_path / "original"
    assert invoke(path) == 0
    original.write_bytes(path.read_bytes())
    with pytest.raises(ModelFailure, match="qualification_result_not_published"):
        installed_model.verify_result(path, original, "generate_documents", result)


@pytest.mark.parametrize("status,result", [("passed", 0), ("failed", 1)])
def test_completed_result_must_match_new_validated_receipt(
    tmp_path: Path, metadata: Metadata, status: str, result: int
) -> None:
    path, original = tmp_path / "route.json", tmp_path / "original"
    assert invoke(path) == 0
    original.write_bytes(path.read_bytes())
    route = load_route(path)
    assert route is not None
    data = json.loads(path.read_text())
    data["qualification_receipts"]["generate_documents"] = qualification_receipt(
        {
            "status": status,
            "capability_sha256": route.capability_sha256,
            "reviewed_by": "Synthetic fixture",
            "reviewed_at": "2026-11-08T00:00:00+00:00",
            "counts": {
                "valid_first": 20 if result == 0 else 0,
                "valid_after_correction": 0,
                "invalid_after_correction": 20 if result == 1 else 0,
                "failed_before_check": 0,
            },
            "valid_rate_per_mille": 1000 if result == 0 else 0,
            "usable_statement_rate_per_mille": 0,
            "dataset_sha256": "cd" * 32,
            "case_count": 20,
            "duration_ms": 1000,
        }
    )
    path.write_text(json.dumps(data))
    installed_model.verify_result(path, original, "generate_documents", result)
    with pytest.raises(ModelFailure, match="qualification_result_mismatch"):
        installed_model.verify_result(path, original, "generate_documents", 1 - result)
