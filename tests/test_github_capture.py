# SPDX-License-Identifier: MIT OR Apache-2.0
from __future__ import annotations

import json
import socket
import socketserver
import ssl
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

import pytest
from greenhouse_fixture import FixtureUpstream, Scenario

from bonafides.github_capture import (
    MAX_RESPONSE_BYTES,
    GitHubClient,
    GitHubCommit,
    GitHubGatewayClient,
)
from bonafides.greenhouse import GreenhouseClient, SourceFailure, _Budget, _Wire
from bonafides.source_gateway import SourceGatewayServer

REPOSITORY = "fictional/career"
SHA = "a" * 40
MESSAGE = "Keep exact raw text <script>inert</script> &amp; é 😀\n\nBody\twith spacing.\n"


def _body(message: str = MESSAGE) -> bytes:
    return json.dumps([{"sha": SHA, "commit": {"message": message}}]).encode()


@pytest.fixture
def upstream() -> Iterator[FixtureUpstream]:
    with FixtureUpstream(certificate_host="api.github.com") as fixture:
        fixture.scenario = Scenario(body=_body())
        yield fixture


def _client(upstream: FixtureUpstream) -> GitHubClient:
    return GitHubClient(
        resolver=lambda host, port: ("8.8.8.8",),
        ssl_context=upstream.context,
        address_map=lambda address, port: upstream.address,
    )


def _config(
    path: Path,
    *,
    enabled: bool = True,
    repository: str = REPOSITORY,
    token_file: Path | None = None,
) -> None:
    source: dict[str, object] = {
        "id": "career",
        "kind": "github",
        "label": "Career history",
        "repository": repository,
        "enabled": enabled,
        "privacy_class": "confidential",
        "employer_owned": True,
    }
    if token_file is not None:
        source["token_file"] = str(token_file)
    path.write_text(json.dumps({"version": 1, "sources": [source]}))
    path.chmod(0o600)


@contextmanager
def _gateway(
    upstream: FixtureUpstream, config: Path | None
) -> Iterator[tuple[GitHubGatewayClient, SourceGatewayServer]]:
    with SourceGatewayServer(
        ("127.0.0.1", 0),
        GreenhouseClient(),
        github_client=_client(upstream),
        capture_config_file=config,
    ) as server:
        thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        thread.start()
        client = GitHubGatewayClient(
            resolver=lambda host, port: ("172.30.0.2",),
            address_map=lambda address, port: ("127.0.0.1", server.server_address[1]),
        )
        try:
            yield client, server
        finally:
            server.shutdown()
            thread.join(timeout=2)


def test_real_tls_exact_sha_message_fixed_route_and_no_proxy(
    upstream: FixtureUpstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9")
    resolved: list[tuple[str, int]] = []

    def resolve(host: str, port: int) -> tuple[str]:
        resolved.append((host, port))
        return ("8.8.8.8",)

    client = GitHubClient(
        resolver=resolve,
        ssl_context=upstream.context,
        address_map=lambda address, port: upstream.address,
    )
    assert client.read("Fictional/Career", None) == [GitHubCommit(SHA, MESSAGE)]
    assert resolved == [("api.github.com", 443)]
    assert upstream.server_names == ["api.github.com"]
    assert [(request.method, request.path) for request in upstream.requests] == [
        ("GET", "/repos/fictional/career/commits?per_page=20")
    ]
    assert upstream.requests[0].headers["Host"] == "api.github.com"
    assert "Authorization" not in upstream.requests[0].headers


@pytest.mark.parametrize(
    "repository",
    ["https://github.com/a/b", "a/b/c", "a/..", "a/%2e", "a/b?x=1", "a/b\r\nX: y", "a\\b/c", "a/é"],
)
def test_invalid_repository_never_opens_tls(upstream: FixtureUpstream, repository: str) -> None:
    with pytest.raises(SourceFailure, match="source_invalid_token"):
        _client(upstream).read(repository, None)
    assert upstream.requests == []
    assert upstream.server_names == []


@pytest.mark.parametrize(
    "addresses",
    [("127.0.0.1",), ("169.254.169.254",), ("8.8.8.8", "10.0.0.1"), ("::ffff:8.8.8.8",)],
)
def test_mixed_or_private_dns_never_connects(
    upstream: FixtureUpstream, addresses: tuple[str, ...]
) -> None:
    client = GitHubClient(
        resolver=lambda host, port: addresses,
        ssl_context=upstream.context,
        address_map=lambda address, port: upstream.address,
    )
    with pytest.raises(SourceFailure, match="source_address_denied"):
        client.read(REPOSITORY, None)
    assert upstream.server_names == []
    assert upstream.requests == []


def test_wrong_certificate_identity_is_rejected() -> None:
    with FixtureUpstream() as fixture:
        with pytest.raises(SourceFailure, match="source_tls_failed"):
            _client(fixture).read(REPOSITORY, None)
        assert fixture.requests == []


@pytest.mark.parametrize(
    "status,headers,code,retry",
    [
        (401, {}, "source_forbidden", None),
        (403, {}, "source_forbidden", None),
        (404, {}, "source_not_found", None),
        (403, {"X-RateLimit-Remaining": "0"}, "source_rate_limited", 60),
        (403, {"Retry-After": "17"}, "source_rate_limited", 17),
        (429, {"Retry-After": "999999999999999999999"}, "source_rate_limited", 86400),
        (302, {"Location": "https://127.0.0.1/private"}, "source_redirect_denied", None),
        (500, {}, "source_http_error", None),
    ],
)
def test_http_failures_are_safe_no_retry_or_redirect(
    upstream: FixtureUpstream, status: int, headers: dict[str, str], code: str, retry: int | None
) -> None:
    upstream.scenario = Scenario(status=status, headers=headers, body=b"private error response")
    with pytest.raises(SourceFailure) as failure:
        _client(upstream).read(REPOSITORY, None)
    assert str(failure.value) == code
    assert failure.value.retry_after_seconds == retry
    assert len(upstream.requests) == 1


@pytest.mark.parametrize(
    "body,code",
    [
        (b'{"message":"not commits"}', "source_invalid_response"),
        (b'[{"sha":"abc","commit":{"message":"x"}}]', "source_invalid_response"),
        (
            b'[{"sha":"'
            + SHA.encode()
            + b'","sha":"'
            + SHA.encode()
            + b'","commit":{"message":"x"}}]',
            "source_invalid_response",
        ),
        (
            json.dumps([{"sha": SHA, "commit": {"message": "x"}}] * 2).encode(),
            "source_invalid_response",
        ),
        (_body("x" * 8001), "source_text_limit"),
        (_body("\ud800"), "source_invalid_response"),
        (_body("bad\x00message"), "source_invalid_response"),
        (b"[" * 40 + b"]" * 40, "source_invalid_response"),
        (b" " * MAX_RESPONSE_BYTES + b"[]", "source_response_too_large"),
        (b'[{"sha":', "source_invalid_response"),
        (
            json.dumps(
                [{"sha": f"{index:040x}", "commit": {"message": "x"}} for index in range(21)]
            ).encode(),
            "source_response_too_large",
        ),
    ],
)
def test_invalid_oversized_duplicate_or_truncated_data_never_becomes_success(
    upstream: FixtureUpstream, body: bytes, code: str
) -> None:
    upstream.scenario = Scenario(body=body)
    with pytest.raises(SourceFailure, match=code):
        _client(upstream).read(REPOSITORY, None)
    assert len(upstream.requests) == 1


def test_full_messages_and_sha256_work_over_chunked_tls(upstream: FixtureUpstream) -> None:
    sha256 = "b" * 64
    message = "😀" * 8000
    upstream.scenario = Scenario(
        body=json.dumps([{"sha": sha256, "commit": {"message": message}}]).encode(), chunked=True
    )
    assert _client(upstream).read(REPOSITORY, None) == [GitHubCommit(sha256, message)]


def test_truncated_content_length_is_not_empty_success(upstream: FixtureUpstream) -> None:
    upstream.scenario = Scenario(body=_body(), truncate_bytes=10)
    with pytest.raises(SourceFailure, match="source_invalid_response"):
        _client(upstream).read(REPOSITORY, None)


@pytest.mark.parametrize("mode", ["missing", "disabled", "other"])
def test_gateway_unconfigured_or_disabled_has_zero_tls_egress(
    upstream: FixtureUpstream, tmp_path: Path, mode: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bonafides import github_capture

    token_reads: list[Path] = []

    def deny_token(path: Path, field_name: str) -> str:
        del field_name
        token_reads.append(path)
        raise AssertionError("disabled repository attempted a credential read")

    monkeypatch.setattr(github_capture, "read_secret", deny_token)
    config = None if mode == "missing" else tmp_path / "capture.json"
    if config is not None:
        _config(
            config,
            enabled=mode != "disabled",
            repository="other/repo" if mode == "other" else REPOSITORY,
            token_file=tmp_path / "nonexistent-secret",
        )
    with (
        _gateway(upstream, config) as (client, _),
        pytest.raises(SourceFailure, match="source_forbidden"),
    ):
        client.read(REPOSITORY)
    assert upstream.requests == []
    assert upstream.server_names == []
    assert token_reads == []


def test_gateway_rereads_allowlist_and_private_token_only_at_egress(
    upstream: FixtureUpstream, tmp_path: Path
) -> None:
    token = tmp_path / "token"
    token.write_text("fictional-test-bearer")
    token.chmod(0o600)
    config = tmp_path / "capture.json"
    _config(config, token_file=token)
    with _gateway(upstream, config) as (client, _):
        assert client.read(REPOSITORY) == [GitHubCommit(SHA, MESSAGE)]
        assert upstream.requests[0].headers["Authorization"] == "Bearer " + token.read_text()
        _config(config, enabled=False, token_file=token)
        token.unlink()
        with pytest.raises(SourceFailure, match="source_forbidden"):
            client.read(REPOSITORY)
        _config(config, repository="different/repo")
        with pytest.raises(SourceFailure, match="source_forbidden"):
            client.read(REPOSITORY)
        config.write_text("invalid private configuration")
        with pytest.raises(SourceFailure, match="source_invalid_response"):
            client.read(REPOSITORY)
    assert len(upstream.requests) == 1


@pytest.mark.parametrize("kind", ["readable", "symlink", "injected", "missing"])
def test_invalid_token_file_never_reaches_tls(
    upstream: FixtureUpstream, tmp_path: Path, kind: str
) -> None:
    token = tmp_path / "token"
    token.write_text("fixture\r\nX-Leak: secret" if kind == "injected" else "fictional-test-bearer")
    token.chmod(0o644 if kind == "readable" else 0o600)
    if kind == "symlink":
        linked = tmp_path / "linked"
        linked.symlink_to(token)
        token = linked
    elif kind == "missing":
        token.unlink()
    with pytest.raises(SourceFailure, match="source_forbidden"):
        _client(upstream).read(REPOSITORY, token)
    assert upstream.server_names == []
    assert upstream.requests == []


@pytest.mark.parametrize(
    "headers",
    [
        "Authorization: worker-supplied\r\n",
        "Cookie: session=secret\r\n",
        "Transfer-Encoding: chunked\r\n",
        "Content-Length: 1\r\n",
        "Host: attacker\r\n",
        "X-Duplicate: 1\r\nX-Duplicate: 2\r\n",
    ],
)
def test_gateway_rejects_worker_credentials_and_ambiguous_requests(
    upstream: FixtureUpstream, tmp_path: Path, headers: str
) -> None:
    config = tmp_path / "capture.json"
    _config(config)
    with (
        _gateway(upstream, config) as (_, server),
        socket.create_connection(("127.0.0.1", server.server_address[1]), timeout=2) as stream,
    ):
        stream.setblocking(False)
        wire = _Wire(stream, _Budget(None, 2))
        wire.send(
            (
                f"GET /v1/github/{REPOSITORY}/commits?per_page=20 HTTP/1.1\r\n"
                f"Host: source-egress:8766\r\n{headers}\r\n"
            ).encode()
        )
        status, response_headers = wire.headers()
        assert status == 502
        assert json.loads(wire.body(response_headers))["code"] == "source_invalid_response"
    assert upstream.server_names == []
    assert upstream.requests == []


def test_worker_validates_repository_envelope_over_real_socket() -> None:
    body = json.dumps(
        {
            "schema_version": 1,
            "repository": "wrong/repository",
            "commits": [{"sha": SHA, "message": MESSAGE}],
        }
    ).encode()
    captured: list[bytes] = []

    class Handler(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            stream = cast(socket.socket, self.request)
            stream.settimeout(2)
            request = bytearray()
            while b"\r\n\r\n" not in request:
                packet = stream.recv(4096)
                if not packet:
                    return
                request.extend(packet)
            captured.append(bytes(request))
            stream.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                + str(len(body)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + body
            )

    with socketserver.TCPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        thread.start()
        try:
            client = GitHubGatewayClient(
                resolver=lambda host, port: ("172.30.0.2",),
                address_map=lambda address, port: ("127.0.0.1", server.server_address[1]),
            )
            with pytest.raises(SourceFailure, match="source_invalid_response"):
                client.read(REPOSITORY)
        finally:
            server.shutdown()
            thread.join(timeout=2)
    assert captured == [
        b"GET /v1/github/fictional/career/commits?per_page=20 HTTP/1.1\r\n"
        b"Host: source-egress:8766\r\nAccept: application/json\r\n"
        b"Accept-Encoding: identity\r\nConnection: close\r\n\r\n"
    ]


def test_gateway_cancellation_closes_upstream_and_never_returns_stale_material(
    upstream: FixtureUpstream, tmp_path: Path
) -> None:
    config = tmp_path / "capture.json"
    _config(config)
    upstream.scenario = Scenario(body=_body(), delay_seconds=10)
    interrupted = threading.Event()
    results: list[object] = []
    with _gateway(upstream, config) as (client, _):

        def run() -> None:
            try:
                results.append(client.read(REPOSITORY, interrupted))
            except SourceFailure as failure:
                results.append(failure.code)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        assert upstream.request_received.wait(2)
        interrupted.set()
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert results == ["source_cancelled"]
        assert upstream.client_disconnected.wait(2)


def test_already_cancelled_never_connects(upstream: FixtureUpstream) -> None:
    interrupted = threading.Event()
    interrupted.set()
    with pytest.raises(SourceFailure, match="source_cancelled"):
        _client(upstream).read(REPOSITORY, None, interrupted)
    assert upstream.requests == []
    assert upstream.server_names == []


def test_production_constructors_cannot_override_host_or_tls_verification() -> None:
    with pytest.raises(ValueError, match="source_gateway_address_denied"):
        GitHubGatewayClient("127.0.0.1", 12345)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    with pytest.raises(ValueError, match="source_tls_verification_required"):
        GitHubClient(ssl_context=context)


def test_gateway_preserves_rate_limit_failure_and_bounded_delay(
    upstream: FixtureUpstream, tmp_path: Path
) -> None:
    config = tmp_path / "capture.json"
    _config(config)
    upstream.scenario = Scenario(
        status=403, headers={"X-RateLimit-Remaining": "0", "Retry-After": "19"}
    )
    with _gateway(upstream, config) as (client, _), pytest.raises(SourceFailure) as failure:
        client.read(REPOSITORY)
    assert failure.value.code == "source_rate_limited"
    assert failure.value.retry_after_seconds == 19
    assert len(upstream.requests) == 1


def test_real_tls_body_deadline_is_safe_and_closes_connection(
    upstream: FixtureUpstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bonafides import github_capture

    class ShortBudget(_Budget):
        def __init__(self, interrupted: threading.Event | None) -> None:
            super().__init__(interrupted, seconds=0.5)

    monkeypatch.setattr(github_capture, "_Budget", ShortBudget)
    upstream.scenario = Scenario(body=_body(), delay_seconds=10)
    with pytest.raises(SourceFailure, match="source_timeout"):
        _client(upstream).read(REPOSITORY, None)
    assert upstream.client_disconnected.wait(2)


@pytest.mark.parametrize(
    "headers,code",
    [
        ({"Content-Encoding": "gzip"}, "source_unsupported_encoding"),
        ({"Content-Type": "text/html"}, "source_invalid_response"),
        ({"Content-Length": str(MAX_RESPONSE_BYTES + 1)}, "source_response_too_large"),
    ],
)
def test_real_tls_rejects_unsupported_or_oversized_framing(
    upstream: FixtureUpstream, headers: dict[str, str], code: str
) -> None:
    upstream.scenario = Scenario(body=_body(), headers=headers)
    with pytest.raises(SourceFailure, match=code):
        _client(upstream).read(REPOSITORY, None)
