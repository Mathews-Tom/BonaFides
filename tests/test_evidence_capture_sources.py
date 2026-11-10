# SPDX-License-Identifier: MIT OR Apache-2.0
from __future__ import annotations

import hashlib
import json
import subprocess
import zlib
from pathlib import Path
from threading import Event

import pytest
from test_config import environment

from bonafides.config import Settings
from bonafides.evidence_capture_sources import CaptureSource, collect_commits, load_sources
from bonafides.problems import Problem


def _repository(root: Path, messages: list[str], *, sha256: bool = False) -> tuple[Path, list[str]]:
    root = root.resolve()
    git = root / ".git"
    (git / "objects").mkdir(parents=True)
    (git / "refs" / "heads").mkdir(parents=True)
    config = "[core]\n\trepositoryformatversion = 0\n\tbare = false\n"
    if sha256:
        config = "[core]\n\trepositoryformatversion = 1\n\tbare = false\n[extensions]\n\tobjectformat = sha256\n"
    (git / "config").write_text(config)

    def store(kind: str, content: bytes) -> str:
        raw = kind.encode() + b" " + str(len(content)).encode() + b"\x00" + content
        identity = (
            hashlib.sha256(raw).hexdigest()
            if sha256
            else hashlib.sha1(raw, usedforsecurity=False).hexdigest()
        )
        path = git / "objects" / identity[:2] / identity[2:]
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(zlib.compress(raw))
        return identity

    tree = store("tree", b"")
    ids: list[str] = []
    for index, message in enumerate(messages):
        parent = f"parent {ids[-1]}\n" if ids else ""
        commit = (
            f"tree {tree}\n{parent}"
            f"author Synthetic Owner <owner@example.invalid> {1000000000 + index} +0000\n"
            f"committer Synthetic Owner <owner@example.invalid> {1000000000 + index} +0000\n\n"
            + message
        )
        ids.append(store("commit", commit.encode("utf-8")))
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    (git / "refs" / "heads" / "main").write_text(ids[-1] + "\n")
    return root, ids


def _source(root: Path) -> CaptureSource:
    return CaptureSource(
        id="career",
        label="Synthetic repository",
        kind="local_git",
        repository=root,
        enabled=True,
        privacy_class="confidential",
        employer_owned=True,
    )


@pytest.mark.parametrize("sha256,packed", [(False, False), (False, True), (True, True)])
def test_exact_loose_and_packed_commit_messages_keep_hash_and_classification(
    tmp_path: Path, sha256: bool, packed: bool
) -> None:
    messages = [
        "Built a synthetic fixture.\n",
        "Reviewed café documentation.\n\nNo achievement inferred.\n",
    ]
    root, ids = _repository(tmp_path / "repository", messages, sha256=sha256)
    if packed:
        subprocess.run(
            ["git", f"--git-dir={root / '.git'}", "repack", "-ad"],
            check=True,
            capture_output=True,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": str(tmp_path),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": "/dev/null",
            },
        )
    before = {
        str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }
    candidates = collect_commits(_source(root), Event())
    assert [candidate.id for candidate in candidates] == list(reversed(ids))
    assert [candidate.payload.text for candidate in candidates] == list(reversed(messages))
    assert all(
        candidate.payload.source == f"local-git:career@{candidate.id}" for candidate in candidates
    )
    assert all(
        candidate.provenance.privacy_class == "confidential" and candidate.provenance.employer_owned
        for candidate in candidates
    )
    assert all(candidate.provenance.source_type == "work_sample" for candidate in candidates)
    assert {
        str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()
    } == before


def test_capture_window_reads_latest_twenty_without_running_repository_configuration(
    tmp_path: Path,
) -> None:
    messages = [f"Synthetic commit {number}.\n" for number in range(21)]
    root, ids = _repository(tmp_path / "repository", messages)
    # A normal git invocation rejects this config; the reader must never consume it.
    (root / ".git" / "config").write_text("this is not a valid git configuration\n")
    candidates = collect_commits(_source(root), Event())
    assert [candidate.id for candidate in candidates] == list(reversed(ids[1:]))
    assert [candidate.payload.text for candidate in candidates] == list(reversed(messages[1:]))


@pytest.mark.parametrize("message", ["x" * 8001, "synthetic\x00hidden text"])
def test_oversized_or_nul_message_is_not_silently_shortened(tmp_path: Path, message: str) -> None:
    root, _ = _repository(tmp_path / "repository", [message])
    with pytest.raises(Problem) as failure:
        collect_commits(_source(root), Event())
    assert failure.value.code == "capture_content_invalid"


def test_corrupted_object_cannot_claim_another_commit_hash(tmp_path: Path) -> None:
    root, ids = _repository(tmp_path / "repository", ["Synthetic original.\n"])
    path = root / ".git" / "objects" / ids[0][:2] / ids[0][2:]
    original = zlib.decompress(path.read_bytes())
    path.write_bytes(zlib.compress(original.replace(b"original", b"modified")))
    with pytest.raises(Problem) as failure:
        collect_commits(_source(root), Event())
    assert failure.value.code == "capture_repository_invalid"


def test_alternate_object_store_and_symlinked_roots_are_not_read(tmp_path: Path) -> None:
    outside, ids = _repository(tmp_path / "outside", ["Outside configured repository.\n"])
    root, _ = _repository(tmp_path / "repository", ["Inside configured repository.\n"])
    (root / ".git" / "refs" / "heads" / "main").write_text(ids[-1] + "\n")
    info = root / ".git" / "objects" / "info"
    info.mkdir()
    (info / "alternates").write_text(str(outside / ".git" / "objects") + "\n")
    with pytest.raises(Problem) as failure:
        collect_commits(_source(root), Event())
    assert failure.value.code == "capture_repository_unavailable"
    alias = tmp_path / "alias"
    alias.symlink_to(outside, target_is_directory=True)
    with pytest.raises(Problem) as symlink:
        collect_commits(_source(alias), Event())
    assert symlink.value.code == "capture_repository_invalid"


def test_cancelled_capture_returns_no_proposals(tmp_path: Path) -> None:
    root, _ = _repository(tmp_path / "repository", ["Synthetic message.\n"])
    interrupted = Event()
    interrupted.set()
    with pytest.raises(Problem) as failure:
        collect_commits(_source(root), interrupted)
    assert failure.value.code == "capture_cancelled"


def test_configuration_rejects_duplicate_identity_and_nonprivate_file(tmp_path: Path) -> None:
    settings_env = environment(tmp_path)
    path = tmp_path / "capture.json"
    source = _source(tmp_path / "repository").model_dump(mode="json")
    path.write_text(json.dumps({"version": 1, "sources": [source, source]}))
    path.chmod(0o600)
    settings_env["BONAFIDES_EVIDENCE_CAPTURE_CONFIG_FILE"] = str(path)
    settings = Settings.from_env(settings_env)
    with pytest.raises(Problem) as duplicate:
        load_sources(settings)
    assert duplicate.value.code == "capture_configuration_invalid"
    path.write_text(json.dumps({"version": 1, "sources": [source]}))
    path.chmod(0o644)
    with pytest.raises(Problem) as readable:
        load_sources(settings)
    assert readable.value.code == "capture_configuration_invalid"
    path.chmod(0o600)
    selected = load_sources(settings)["career"]
    assert "repository" not in selected.public()
    assert str(path) not in str(duplicate.value)
