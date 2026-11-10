# SPDX-License-Identifier: MIT OR Apache-2.0
"""Bounded exact commit proposals from configured local or gateway repositories."""

from __future__ import annotations

import hashlib
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

from pydantic import Field

from bonafides import evidence_capture_config as capture_config
from bonafides.auth import StrictModel
from bonafides.evidence_provenance import InitialEvidenceProvenance
from bonafides.github_capture import GitHubGatewayClient
from bonafides.greenhouse import SourceFailure
from bonafides.problems import Problem
from bonafides.profile_entries import EvidencePayload
from bonafides.storage import directory, subdirectory

_MAX_OUTPUT = 1024 * 1024
_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_PACK = re.compile(r"pack-(?:[0-9a-f]{40}|[0-9a-f]{64})\.(?:pack|idx|rev)\Z")


class CaptureCandidate(StrictModel):
    id: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    payload: EvidencePayload
    provenance: InitialEvidenceProvenance


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


def _candidate(source: capture_config.CaptureSource, sha: str, message: str) -> CaptureCandidate:
    if (
        not message.strip()
        or len(message) > capture_config.MAX_TEXT
        or "\x00" in message
        or any(0xD800 <= ord(character) <= 0xDFFF for character in message)
    ):
        raise Problem(422, "capture_content_invalid")
    return CaptureCandidate(
        id=sha,
        payload=EvidencePayload(text=message, source=capture_config.capture_reference(source, sha)),
        provenance=InitialEvidenceProvenance(
            source_type="work_sample",
            privacy_class=source.privacy_class,
            employer_owned=source.employer_owned,
        ),
    )


def _github_commits(
    source: capture_config.GitHubSource, interrupted: Event, deadline: float
) -> list[CaptureCandidate]:
    try:
        commits = GitHubGatewayClient().read(source.repository, interrupted)
        _check(interrupted, deadline)
        if len(commits) > capture_config.MAX_COMMITS or len({item.sha for item in commits}) != len(
            commits
        ):
            raise SourceFailure("source_invalid_response")
        candidates = [_candidate(source, item.sha, item.message) for item in commits]
        _check(interrupted, deadline)
        return candidates
    except SourceFailure as error:
        status = {
            "source_rate_limited": 429,
            "source_forbidden": 403,
            "source_not_found": 404,
            "source_timeout": 504,
            "source_cancelled": 409,
            "source_gateway_busy": 503,
            "source_gateway_unavailable": 503,
        }.get(error.code, 502)
        raise Problem(status, error.code) from None
    except ValueError:
        raise Problem(502, "source_invalid_response") from None


def collect_commits(
    source: capture_config.CaptureSource, interrupted: Event
) -> list[CaptureCandidate]:
    if not source.enabled:
        raise Problem(409, "capture_source_disabled")
    deadline = time.monotonic() + 15
    _check(interrupted, deadline)
    if source.kind == "github":
        return _github_commits(source, interrupted, deadline)
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
                    [
                        "log",
                        "--format=%H",
                        f"--max-count={capture_config.MAX_COMMITS}",
                        "HEAD",
                        "--",
                    ],
                    interrupted,
                    deadline,
                )
                .decode("ascii")
                .splitlines()
            )
            if len(ids) > capture_config.MAX_COMMITS or any(
                not _SHA.fullmatch(value) for value in ids
            ):
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
            result.append(_candidate(source, commit, message))
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
