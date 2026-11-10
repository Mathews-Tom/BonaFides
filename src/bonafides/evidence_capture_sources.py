# SPDX-License-Identifier: MIT OR Apache-2.0
"""Read configured repositories, never their config, hooks, worktree or network."""

from __future__ import annotations

import hashlib
import json
import os
import re
import selectors
import signal
import stat
import subprocess
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path
from threading import Event
from typing import Literal

from pydantic import Field, StrictBool, field_validator

from bonafides.auth import StrictModel
from bonafides.commands import canonical_json
from bonafides.config import Settings, read_secret
from bonafides.evidence_provenance import InitialEvidenceProvenance, PrivacyClass
from bonafides.problems import Problem
from bonafides.profile_entries import EvidencePayload
from bonafides.storage import directory, subdirectory

MAX_COMMITS = 20
MAX_TEXT = 8000
_MAX_OUTPUT = 1024 * 1024
_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_PACK = re.compile(r"pack-(?:[0-9a-f]{40}|[0-9a-f]{64})\.(?:pack|idx|rev)\Z")


class CaptureSource(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    label: str = Field(min_length=1, max_length=150)
    kind: Literal["local_git"]
    repository: Path
    enabled: StrictBool
    privacy_class: PrivacyClass
    employer_owned: StrictBool

    @field_validator("repository")
    @classmethod
    def absolute_repository(cls, value: Path) -> Path:
        if not value.is_absolute() or ".." in value.parts or "\x00" in str(value):
            raise ValueError("absolute repository required")
        return value

    @property
    def identity_sha256(self) -> str:
        return hashlib.sha256(canonical_json(self.model_dump(mode="json")).encode()).hexdigest()

    def public(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"repository"})


class CaptureCandidate(StrictModel):
    id: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    payload: EvidencePayload
    provenance: InitialEvidenceProvenance


class _Configuration(StrictModel):
    version: Literal[1]
    sources: list[CaptureSource] = Field(max_length=20)


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate configuration key")
        result[key] = value
    return result


def load_sources(settings: Settings) -> dict[str, CaptureSource]:
    path = settings.evidence_capture_config_file
    if path is None:
        return {}
    try:
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("absolute configuration required")
        config = _Configuration.model_validate(
            json.loads(
                read_secret(path, "evidence capture configuration"), object_pairs_hook=_object
            )
        )
        sources = {source.id: source for source in config.sources}
        if len(sources) != len(config.sources):
            raise ValueError("duplicate source identity")
        return sources
    except (OSError, ValueError, TypeError) as error:
        raise Problem(503, "capture_configuration_invalid") from error


def _read_file(root: Path, name: str, limit: int = 65536) -> bytes:
    parts = name.split("/")
    if any(not part or part in {".", ".."} or "\x00" in part for part in parts):
        raise ValueError("invalid git reference")
    with ExitStack() as stack:
        fd = stack.enter_context(directory(root))
        for part in parts[:-1]:
            fd = stack.enter_context(subdirectory(fd, part))
        file = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            info = os.fstat(file)
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                raise ValueError("invalid git metadata")
            with os.fdopen(file, "rb", closefd=False) as stream:
                value = stream.read(limit + 1)
            if len(value) > limit:
                raise ValueError("oversized git metadata")
            return value
        finally:
            os.close(file)


def _head(root: Path) -> str:
    value = _read_file(root, "HEAD").strip().decode("utf-8")
    seen: set[str] = set()
    for _ in range(4):
        if _SHA.fullmatch(value):
            return value
        if not value.startswith("ref: refs/") or value in seen:
            break
        seen.add(value)
        reference = value[5:]
        try:
            value = _read_file(root, reference).strip().decode("utf-8")
        except FileNotFoundError:
            for line in _read_file(root, "packed-refs").decode("utf-8").splitlines():
                if line.endswith(" " + reference):
                    value = line.split(" ", 1)[0]
                    break
            else:
                break
    raise ValueError("missing git HEAD")


def _check(interrupted: Event, deadline: float) -> None:
    if interrupted.is_set():
        raise Problem(409, "capture_cancelled")
    if time.monotonic() >= deadline:
        raise Problem(504, "capture_timeout")


def _project_objects(root: Path, target: Path, interrupted: Event, deadline: float) -> None:
    # Deliberately omit objects/info (alternates), refs, config, hooks and remotes.
    # The operator's repository mount is read-only; only object files are visible to git.
    target.mkdir()
    count = 0
    with directory(root) as fd:
        for entry in os.scandir(fd):
            _check(interrupted, deadline)
            if not re.fullmatch(r"[0-9a-f]{2}|pack", entry.name):
                continue
            with subdirectory(fd, entry.name) as bucket:
                destination = target / entry.name
                destination.mkdir()
                for item in os.scandir(bucket):
                    _check(interrupted, deadline)
                    count += 1
                    if count > 20000:
                        raise Problem(422, "capture_repository_too_large")
                    valid = (
                        _PACK.fullmatch(item.name)
                        if entry.name == "pack"
                        else re.fullmatch(r"(?:[0-9a-f]{38}|[0-9a-f]{62})", item.name)
                    )
                    if valid is None:
                        continue
                    if not item.is_file(follow_symlinks=False):
                        raise ValueError("git object must be a regular file")
                    (destination / item.name).symlink_to(root / entry.name / item.name)


def _git(
    root: Path, arguments: list[str], interrupted: Event, deadline: float, input_bytes: bytes = b""
) -> bytes:
    process = subprocess.Popen(
        ["git", "--no-pager", "--no-replace-objects", f"--git-dir={root}", *arguments],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(root),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_NO_LAZY_FETCH": "1",
        },
        start_new_session=True,
    )
    try:
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(input_bytes)
        process.stdin.close()
        output = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                _check(interrupted, deadline)
                for key, _ in selector.select(0.1):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fd)
                        break
                    output.extend(chunk)
                    if len(output) > _MAX_OUTPUT:
                        raise Problem(422, "capture_content_too_large")
        process.wait(timeout=max(0.01, deadline - time.monotonic()))
        if process.returncode:
            raise Problem(422, "capture_repository_unavailable")
        return bytes(output)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        if process.stdout is not None:
            process.stdout.close()


def collect_commits(source: CaptureSource, interrupted: Event) -> list[CaptureCandidate]:
    if not source.enabled:
        raise Problem(409, "capture_source_disabled")
    deadline = time.monotonic() + 15
    try:
        root = source.repository
        with directory(root):
            git = root / ".git" if (root / ".git").exists() else root
            head = _head(git)
        with tempfile.TemporaryDirectory(prefix="bonafides-git-") as temporary:
            isolated = Path(temporary)
            (isolated / "refs").mkdir()
            (isolated / "HEAD").write_text(head + "\n", encoding="ascii")
            config = "[core]\n\tbare = true\n"
            if len(head) == 64:
                config += "\trepositoryformatversion = 1\n[extensions]\n\tobjectformat = sha256\n"
            (isolated / "config").write_text(config, encoding="ascii")
            try:
                shallow = _read_file(git, "shallow")
            except FileNotFoundError:
                shallow = b""
            if shallow:
                if any(not _SHA.fullmatch(line) for line in shallow.decode("ascii").splitlines()):
                    raise ValueError("invalid shallow boundary")
                (isolated / "shallow").write_bytes(shallow)
            _project_objects(git / "objects", isolated / "objects", interrupted, deadline)
            ids = (
                _git(
                    isolated,
                    ["log", "--format=%H", f"--max-count={MAX_COMMITS}", "HEAD", "--"],
                    interrupted,
                    deadline,
                )
                .decode("ascii")
                .splitlines()
            )
            if len(ids) > MAX_COMMITS or any(not _SHA.fullmatch(value) for value in ids):
                raise ValueError("invalid commit inventory")
            raw = _git(
                isolated,
                ["cat-file", "--batch"],
                interrupted,
                deadline,
                "".join(value + "\n" for value in ids).encode("ascii"),
            )
        result: list[CaptureCandidate] = []
        offset = 0
        for commit in ids:
            end = raw.index(b"\n", offset)
            identity, kind, size = raw[offset:end].decode("ascii").split(" ")
            length = int(size)
            if identity != commit or kind != "commit" or not 0 < length <= 48000:
                raise ValueError("invalid commit object")
            start = end + 1
            content = raw[start : start + length]
            offset = start + length + 1
            if raw[offset - 1 : offset] != b"\n" or len(content) != length:
                raise ValueError("incomplete commit object")
            encoded = b"commit " + str(length).encode("ascii") + b"\x00" + content
            actual = (
                hashlib.sha256(encoded).hexdigest()
                if len(commit) == 64
                else hashlib.sha1(encoded, usedforsecurity=False).hexdigest()
            )
            if actual != commit:
                raise ValueError("commit hash mismatch")
            message = content.split(b"\n\n", 1)[1].decode("utf-8")
            if not message.strip() or len(message) > MAX_TEXT or "\x00" in message:
                raise Problem(422, "capture_content_invalid")
            result.append(
                CaptureCandidate(
                    id=commit,
                    payload=EvidencePayload(text=message, source=f"local-git:{source.id}@{commit}"),
                    provenance=InitialEvidenceProvenance(
                        source_type="work_sample",
                        privacy_class=source.privacy_class,
                        employer_owned=source.employer_owned,
                    ),
                )
            )
        if offset != len(raw):
            raise ValueError("unexpected commit output")
        _check(interrupted, deadline)
        return result
    except subprocess.TimeoutExpired as error:
        raise Problem(504, "capture_timeout") from error
    except FileNotFoundError as error:
        raise Problem(422, "capture_repository_unavailable") from error
    except (OSError, ValueError, IndexError) as error:
        raise Problem(422, "capture_repository_invalid") from error
