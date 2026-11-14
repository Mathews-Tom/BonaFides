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


def invoke(
    path: Path, model: str = MODEL, authorized: bool = True, *, import_structuring: bool = False
) -> int:
    args = ["attach", "--route", str(path), "--model", model, "--model-license", "Apache-2.0"]
    if import_structuring:
        args.append("--import-structuring")
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


def test_attached_import_opt_in_requires_its_own_measurement(
    tmp_path: Path, metadata: Metadata
) -> None:
    path = tmp_path / "route.json"
    assert invoke(path, import_structuring=True) == 0
    route = load_route(path)
    assert route is not None and route.import_structuring
    assert not route.qualified_for("structure_import")
    assert json.loads(path.read_text())["qualification_receipts"] == {}


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


@pytest.mark.parametrize(
    "stage,status,result,publication",
    [
        ("generate_documents", "passed", 0, "first"),
        ("structure_import", "passed", 0, "first"),
        ("structure_import", "failed", 1, "replacement"),
        ("structure_import", "passed", 0, "formatting"),
        ("regenerate_section", "failed", 1, "replacement"),
        ("regenerate_section", "failed", 1, "formatting"),
        ("refine_documents", "passed", 0, "first"),
        ("refine_documents", "failed", 1, "replacement"),
        ("refine_documents", "passed", 0, "formatting"),
        ("refine_documents", "passed", 0, "other_stage"),
        ("generate_documents", "passed", 0, "other_stage"),
    ],
)
def test_result_requires_changed_selected_stage_receipt(
    tmp_path: Path, metadata: Metadata, stage: str, status: str, result: int, publication: str
) -> None:
    path, original = tmp_path / "route.json", tmp_path / "original"
    assert invoke(path, import_structuring=stage == "structure_import") == 0
    original.write_bytes(path.read_bytes())
    route = load_route(path)
    assert route is not None
    data = json.loads(path.read_text())
    data["qualification_receipts"][stage] = qualification_receipt(
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
            **(
                {
                    "unit_count": 40,
                    "correct_kind_count": 40 if result == 0 else 0,
                    "correct_kind_rate_per_mille": 1000 if result == 0 else 0,
                }
                if stage == "structure_import"
                else {"usable_statement_rate_per_mille": 0}
            ),
            "dataset_sha256": "cd" * 32,
            "case_count": 20,
            "duration_ms": 1000,
        },
        stage=stage,
    )
    if publication != "first":
        original.write_text(json.dumps(data))
        if publication == "replacement":
            receipt = data["qualification_receipts"][stage]
            data["qualification_receipts"][stage] = qualification_receipt(
                {**receipt, "reviewed_at": "2026-11-08T00:00:01+00:00"}, stage=stage
            )
        elif publication == "other_stage":
            other_stage = (
                "regenerate_section" if stage == "generate_documents" else "generate_documents"
            )
            data["qualification_receipts"][other_stage] = dict(
                data["qualification_receipts"][stage]
            )
    path.write_text(json.dumps(data, indent=2, sort_keys=True))
    assert path.read_bytes() != original.read_bytes()
    if publication in {"formatting", "other_stage"}:
        with pytest.raises(ModelFailure, match="qualification_result_not_published"):
            installed_model.verify_result(path, original, stage, result)
        return
    installed_model.verify_result(path, original, stage, result)
    with pytest.raises(ModelFailure, match="qualification_result_mismatch"):
        installed_model.verify_result(path, original, stage, 1 - result)
