# SPDX-License-Identifier: MIT OR Apache-2.0
from __future__ import annotations

import hashlib
import json
import subprocess
import zlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread

import pytest
from pydantic import ValidationError

from bonafides import evidence_capture_sources
from bonafides.evidence_capture_config import (
    GitHubSource,
    LocalGitSource,
    capture_reference,
    load_sources,
    validate_github_repository,
)
from bonafides.evidence_capture_sources import collect_commits
from bonafides.github_capture import GitHubGatewayClient
from bonafides.problems import Problem


@dataclass
class _GitHubGateway:
    repository: str = "example/career"
    commits: list[dict[str, str]] = field(
        default_factory=lambda: [
            {"sha": "a" * 40, "message": "Exact café message.\n\n<b>Not HTML</b>\n"}
        ]
    )
    status: int = 200
    raw: bytes | None = None
    requests: list[str] = field(default_factory=list)
    after_request: Callable[[], None] | None = None


@pytest.fixture
def github_gateway(monkeypatch: pytest.MonkeyPatch) -> Iterator[_GitHubGateway]:
    """Use the actual worker-side HTTP parser against a loopback-only fixture."""
    fixture = _GitHubGateway()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            fixture.requests.append(self.path)
            if fixture.after_request is not None:
                fixture.after_request()
            body = fixture.raw
            if body is None:
                body = json.dumps(
                    {
                        "schema_version": 1,
                        "repository": fixture.repository,
                        "commits": fixture.commits,
                    }
                ).encode()
            try:
                self.send_response(fixture.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(
        evidence_capture_sources,
        "GitHubGatewayClient",
        lambda: GitHubGatewayClient(
            resolver=lambda host, port: ("172.30.0.2",),
            address_map=lambda address, port: ("127.0.0.1", server.server_port),
        ),
    )
    try:
        yield fixture
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _github_source(**overrides: object) -> GitHubSource:
    return GitHubSource.model_validate(
        {
            "id": "career",
            "label": "Synthetic GitHub repository",
            "kind": "github",
            "repository": "Example/Career",
            "enabled": True,
            "privacy_class": "confidential",
            "employer_owned": True,
            **overrides,
        }
    )


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


def _source(root: Path) -> LocalGitSource:
    return LocalGitSource(
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
    path = tmp_path / "capture.json"
    source = _source(tmp_path / "repository").model_dump(mode="json")
    path.write_text(json.dumps({"version": 1, "sources": [source, source]}))
    path.chmod(0o600)
    with pytest.raises(Problem) as duplicate:
        load_sources(path)
    assert duplicate.value.code == "capture_configuration_invalid"
    path.write_text(json.dumps({"version": 1, "sources": [source]}))
    path.chmod(0o644)
    with pytest.raises(Problem) as readable:
        load_sources(path)
    assert readable.value.code == "capture_configuration_invalid"
    path.chmod(0o600)
    selected = load_sources(path)["career"]
    assert "repository" not in selected.public()
    assert str(path) not in str(duplicate.value)


def test_local_configuration_retains_pr1_serialization_and_pinned_identity(tmp_path: Path) -> None:
    original = {
        "id": "career",
        "label": "Synthetic repository",
        "kind": "local_git",
        "repository": str(tmp_path / "repository"),
        "enabled": True,
        "privacy_class": "confidential",
        "employer_owned": True,
    }
    source = LocalGitSource.model_validate(original)
    assert source.model_dump(mode="json") == original
    expected = hashlib.sha256(
        json.dumps(original, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert source.identity_sha256 == expected
    assert capture_reference(source, "a" * 40) == "local-git:career@" + "a" * 40
    with pytest.raises(ValidationError):
        LocalGitSource.model_validate(
            {key: value for key, value in original.items() if key != "enabled"}
        )


@pytest.mark.parametrize(
    "repository",
    [
        "https://github.com/example/career",
        "example/career/extra",
        "example/..",
        "../career",
        "example/%2e%2e",
        "example/career?token=secret",
        "example\\career",
        "example/career\n",
        "example/\x00career",
        "-example/career",
        "example-/career",
        "exam--ple/career",
        "example/",
    ],
)
def test_github_configuration_rejects_nonliteral_repository(repository: str) -> None:
    with pytest.raises(ValueError):
        validate_github_repository(repository)
    with pytest.raises(ValidationError):
        _github_source(repository=repository)


def test_github_config_rejects_credential_aliases_and_hides_private_paths(tmp_path: Path) -> None:
    path = tmp_path / "capture.json"
    source = _github_source(token_file=tmp_path / "not-mounted-in-worker")
    assert source.repository == "example/career"
    assert source.public() == {
        "id": "career",
        "label": "Synthetic GitHub repository",
        "kind": "github",
        "enabled": True,
        "privacy_class": "confidential",
        "employer_owned": True,
    }
    duplicate = source.model_dump(mode="json") | {"id": "alias", "repository": "EXAMPLE/CAREER"}
    path.write_text(
        json.dumps({"version": 1, "sources": [source.model_dump(mode="json"), duplicate]})
    )
    path.chmod(0o600)
    with pytest.raises(Problem) as failure:
        load_sources(path)
    assert failure.value.code == "capture_configuration_invalid"
    for changes in (
        {"token": "raw-credential-not-supported"},
        {"token_file": "relative-secret"},
        {"token_file": str(tmp_path / ".." / "outside")},
        {"enabled": 1},
        {"enabled": "true"},
    ):
        with pytest.raises(ValidationError):
            _github_source(**changes)


def test_disabled_github_source_performs_no_gateway_request(github_gateway: _GitHubGateway) -> None:
    value = _github_source().model_dump(mode="json")
    del value["enabled"]
    source = GitHubSource.model_validate(value)
    with pytest.raises(Problem) as failure:
        collect_commits(source, Event())
    assert failure.value.code == "capture_source_disabled"
    assert github_gateway.requests == []


@pytest.mark.parametrize("sha", ["b" * 40, "c" * 64])
def test_github_exact_gateway_material_and_frozen_classification(
    tmp_path: Path, github_gateway: _GitHubGateway, sha: str
) -> None:
    message = "Exact café message.\n\n<b>Not HTML</b>\n"
    github_gateway.commits = [{"sha": sha, "message": message}]
    # The worker must neither stat nor read this gateway-only credential.
    source = _github_source(token_file=tmp_path / "gateway-only-token")
    candidates = collect_commits(source, Event())
    assert [candidate.model_dump(mode="json") for candidate in candidates] == [
        {
            "id": sha,
            "payload": {
                "text": message,
                "source": f"https://github.com/example/career/commit/{sha}",
            },
            "provenance": {
                "source_type": "work_sample",
                "privacy_class": "confidential",
                "employer_owned": True,
            },
        }
    ]
    assert github_gateway.requests == ["/v1/github/example/career/commits?per_page=20"]


@pytest.mark.parametrize(
    "code,status",
    [
        ("source_rate_limited", 429),
        ("source_forbidden", 403),
        ("source_not_found", 404),
        ("source_timeout", 504),
        ("source_redirect_denied", 502),
        ("source_response_too_large", 502),
        ("source_invalid_response", 502),
    ],
)
def test_gateway_errors_are_visible_safe_capture_failures(
    github_gateway: _GitHubGateway, code: str, status: int
) -> None:
    github_gateway.status = 502
    github_gateway.raw = json.dumps({"code": code, "retry_after_seconds": 60}).encode()
    with pytest.raises(Problem) as failure:
        collect_commits(_github_source(), Event())
    assert (failure.value.status, failure.value.code) == (status, code)


@pytest.mark.parametrize(
    "boundary", ["repository", "sha", "message", "duplicate", "inventory", "json"]
)
def test_invalid_gateway_envelope_never_becomes_proposals(
    github_gateway: _GitHubGateway, boundary: str
) -> None:
    if boundary == "repository":
        github_gateway.repository = "other/repository"
    elif boundary == "sha":
        github_gateway.commits[0]["sha"] = "a" * 39
    elif boundary == "message":
        github_gateway.commits[0]["message"] = "x" * 8001
    elif boundary == "duplicate":
        github_gateway.commits *= 2
    elif boundary == "inventory":
        github_gateway.commits = [
            {"sha": f"{index:040x}", "message": "Exact"} for index in range(21)
        ]
    else:
        github_gateway.raw = b'{"schema_version":1,'
    with pytest.raises(Problem) as failure:
        collect_commits(_github_source(), Event())
    assert failure.value.status == 502


def test_cancelled_github_capture_does_not_return_received_proposals(
    github_gateway: _GitHubGateway,
) -> None:
    interrupted = Event()
    github_gateway.after_request = interrupted.set
    with pytest.raises(Problem) as failure:
        collect_commits(_github_source(), interrupted)
    assert failure.value.code in {"source_cancelled", "capture_cancelled"}


@pytest.mark.parametrize("sha", ["a" * 39, "a" * 41, "A" * 40, "a" * 40 + "\n", "g" * 40])
def test_capture_reference_rejects_partial_or_noncanonical_commit_identity(sha: str) -> None:
    with pytest.raises(ValueError):
        capture_reference(_github_source(), sha)
