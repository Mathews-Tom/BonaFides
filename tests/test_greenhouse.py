# SPDX-License-Identifier: MIT OR Apache-2.0
from __future__ import annotations

import gzip
import json
import socket
import ssl
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import cast

import pytest
from greenhouse_fixture import FixtureUpstream, Scenario, example_job

from offerloom import greenhouse
from offerloom.greenhouse import (
    MAX_DESCRIPTION,
    MAX_GATEWAY_BYTES,
    MAX_JOBS,
    MAX_RESPONSE_BYTES,
    BoardRead,
    GreenhouseClient,
    SourceFailure,
    SourceGatewayClient,
    safe_reference_url,
    validate_board_token,
)
from offerloom.source_gateway import MAX_IN_FLIGHT, SourceGatewayServer


@pytest.fixture
def upstream() -> Iterator[FixtureUpstream]:
    with FixtureUpstream() as fixture:
        yield fixture


def _failure(client: GreenhouseClient | SourceGatewayClient, code: str) -> SourceFailure:
    with pytest.raises(SourceFailure) as raised:
        client.read("fictional_board")
    assert raised.value.code == code
    assert str(raised.value) == code
    return raised.value


@pytest.mark.parametrize(
    "token",
    ["", "https://evil.org", "a/b", "a%2fb", "a?callback=x", " a", "a\n", "a\\b", "a" * 121, "é"],
)
def test_board_tokens_cannot_change_request(token: str, upstream: FixtureUpstream) -> None:
    with pytest.raises(SourceFailure, match="source_invalid_token"):
        upstream.client().read(token)
    assert not upstream.requests


@pytest.mark.parametrize(
    "value",
    [
        "http://careers.org/jobs",
        "https://user:pass@careers.org/",
        "https://careers.org:444/",
        "https://127.0.0.1/",
        "https://127.1/",
        "https://2130706433/",
        "https://0x7f.0x0.0x0.0x1/",
        "https://[::ffff:8.8.8.8]/",
        "https://[2002:0808:0808::1]/",
        "https://[64:ff9b::808:808]/",
        "https://169.254.169.254/",
        "https://10.1.2.3/",
        "https://example.local/",
        "https://localhost/",
        "https://careers.org\\@127.0.0.1/",
        "https://careers%2eorg/",
        "https://careers.org/%0aheader",
        "https://careers.org/\x00",
        "https://careers.org/a b",
        "https://é.org/",
    ],
)
def test_unsafe_references_are_not_links(value: str) -> None:
    assert safe_reference_url(value) is None


def test_reference_is_canonical_inert_and_never_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    def deny_resolution(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("a reference must never resolve")

    monkeypatch.setattr(socket, "getaddrinfo", deny_resolution)
    assert (
        safe_reference_url("https://Careers.Offerloom.org:443/jobs/4?q=role#details")
        == "https://careers.offerloom.org/jobs/4?q=role#details"
    )
    assert safe_reference_url("") is None
    assert validate_board_token("Board-123_abc") == "Board-123_abc"


def test_real_tls_published_jobs_original_snapshot_inert_html_and_hash(
    upstream: FixtureUpstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9")
    first = upstream.client().read("fictional_board")
    assert len(first.jobs) == 1
    assert first.excluded_prospect_count == 1
    job = first.jobs[0]
    assert job.external_id == "101"
    assert job.title == "Fictional infrastructure engineer"
    assert job.description_text == "Build reliable tools & services."
    assert job.canonical_url == "https://careers.offerloom-fixture.org/jobs/101"
    assert job.source_updated_at == "2026-10-25T08:00:00Z"
    assert job.snapshot == example_job()
    assert len(job.source_content_sha256) == 64
    repeated = upstream.client().read("fictional_board")
    assert repeated.jobs[0].source_content_sha256 == job.source_content_sha256
    changed = example_job(content="<p>Changed retained content</p>")
    upstream.scenario.body = json.dumps({"jobs": [changed], "meta": {"total": 1}}).encode()
    assert (
        upstream.client().read("fictional_board").jobs[0].source_content_sha256
        != job.source_content_sha256
    )
    assert upstream.server_names == [greenhouse.UPSTREAM_HOST] * 3
    assert len(upstream.requests) == 3
    for request in upstream.requests:
        assert request.method == "GET"
        assert request.path == "/v1/boards/fictional_board/jobs?content=true"
        assert request.headers["Host"] == greenhouse.UPSTREAM_HOST
        assert request.headers["Accept-Encoding"] == "identity"
        assert not {"authorization", "cookie", "proxy-authorization"} & {
            key.lower() for key in request.headers
        }


@pytest.mark.parametrize(
    "addresses",
    [
        ("127.0.0.1",),
        ("8.8.8.8", "10.0.0.1"),
        ("8.8.8.8", "::1"),
        ("::ffff:8.8.8.8",),
        ("2002:0808:0808::1",),
        ("64:ff9b::808:808",),
        ("2001::808:808",),
        ("169.254.169.254",),
        ("192.0.0.9",),
    ],
)
def test_all_dns_answers_must_be_public_before_any_request(
    addresses: tuple[str, ...], upstream: FixtureUpstream
) -> None:
    client = GreenhouseClient(
        resolver=lambda host, port: addresses,
        ssl_context=upstream.context,
        address_map=lambda address, port: upstream.address,
    )
    _failure(client, "source_address_denied")
    assert not upstream.requests


def test_dns_rebinding_cannot_cause_a_second_lookup(upstream: FixtureUpstream) -> None:
    calls = 0

    def rebind(host: str, port: int) -> tuple[str, ...]:
        nonlocal calls
        assert (host, port) == (greenhouse.UPSTREAM_HOST, 443)
        calls += 1
        return ("8.8.8.8",) if calls == 1 else ("127.0.0.1",)

    client = GreenhouseClient(
        resolver=rebind,
        ssl_context=upstream.context,
        address_map=lambda address, port: upstream.address,
    )
    assert len(client.read("fictional_board").jobs) == 1
    assert calls == 1
    _failure(client, "source_address_denied")
    assert calls == 2
    assert len(upstream.requests) == 1


def test_actual_peer_is_checked_before_tls_or_http(
    upstream: FixtureUpstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    class WrongPeer(socket.socket):
        def getpeername(self) -> tuple[str, int]:
            actual = super().getpeername()
            return "8.8.4.4", int(actual[1])

    monkeypatch.setattr(socket, "socket", WrongPeer)
    _failure(upstream.client(), "source_peer_mismatch")
    assert not upstream.requests
    assert not upstream.server_names


def test_real_certificate_verification_is_not_disabled(upstream: FixtureUpstream) -> None:
    client = GreenhouseClient(
        resolver=lambda host, port: ("8.8.8.8",), address_map=lambda address, port: upstream.address
    )
    _failure(client, "source_tls_failed")
    assert not upstream.requests
    with pytest.raises(ValueError, match="source_tls_verification_required"):
        GreenhouseClient(ssl_context=ssl._create_unverified_context())


@pytest.mark.parametrize(
    "status,code",
    [
        (301, "source_redirect_denied"),
        (302, "source_redirect_denied"),
        (307, "source_redirect_denied"),
        (403, "source_forbidden"),
        (404, "source_not_found"),
        (500, "source_http_error"),
    ],
)
def test_redirects_and_failures_are_not_followed(
    status: int, code: str, upstream: FixtureUpstream
) -> None:
    upstream.scenario = Scenario(status=status, headers={"Location": "http://127.0.0.1/private"})
    _failure(upstream.client(), code)
    assert len(upstream.requests) == 1


def test_retry_after_is_a_provider_minimum_including_dates(upstream: FixtureUpstream) -> None:
    upstream.scenario = Scenario(status=429, headers={"Retry-After": "1200"})
    assert _failure(upstream.client(), "source_rate_limited").retry_after_seconds == 1200
    instant = datetime.now(UTC) + timedelta(seconds=600)
    upstream.scenario.headers["Retry-After"] = format_datetime(instant, usegmt=True)
    delay = _failure(upstream.client(), "source_rate_limited").retry_after_seconds
    assert delay is not None and 598 <= delay <= 601
    upstream.scenario.headers["Retry-After"] = "999999999999999999999999999999"
    assert (
        _failure(upstream.client(), "source_rate_limited").retry_after_seconds
        == 999999999999999999999999999999
    )
    upstream.scenario.headers["Retry-After"] = "invalid"
    assert _failure(upstream.client(), "source_rate_limited").retry_after_seconds == 60
    assert len(upstream.requests) == 4


@pytest.mark.parametrize(
    "payload",
    [
        {"jobs": [], "meta": {"total": 1}},
        {"jobs": [], "meta": {"total": 0, "next_page": 2}},
        {"jobs": [], "meta": {"total": 0}, "next": "https://evil.org"},
        {"jobs": [], "meta": {"total": 0}, "pagination": {"page": 1}},
        {"jobs": [], "meta": {"total": True}},
        {"jobs": []},
    ],
)
def test_undocumented_pagination_or_partial_batches_are_refused(
    payload: dict[str, object], upstream: FixtureUpstream
) -> None:
    upstream.scenario.body = json.dumps(payload).encode()
    _failure(upstream.client(), "source_incomplete_response")


@pytest.mark.parametrize(
    "body",
    [
        b'callback({"jobs":[],"meta":{"total":0}})',
        b'{"jobs":[',
        b"\xff",
        b'{"jobs":[],"jobs":[],"meta":{"total":0}}',
        b'{"jobs":[],"meta":{"total":0},"bad":NaN}',
    ],
)
def test_non_json_invalid_utf8_truncation_and_duplicate_keys_fail(
    body: bytes, upstream: FixtureUpstream
) -> None:
    upstream.scenario.body = body
    _failure(upstream.client(), "source_invalid_response")


def test_duplicate_ids_are_not_silently_deduplicated(upstream: FixtureUpstream) -> None:
    upstream.scenario.body = json.dumps(
        {"jobs": [example_job(), example_job()], "meta": {"total": 2}}
    ).encode()
    _failure(upstream.client(), "source_invalid_response")


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("title", "a" * 301, "source_text_limit"),
        ("content", "a" * (MAX_DESCRIPTION + 1), "source_text_limit"),
        ("content", "<div>" * 65 + "text" + "</div>" * 65, "source_invalid_response"),
        ("title", "bad\x00title", "source_invalid_response"),
        ("title", "bad\ud800title", "source_invalid_response"),
        ("internal_job_id", "101", "source_invalid_response"),
    ],
)
def test_record_and_html_limits_fail_whole_batch(
    field: str, value: object, code: str, upstream: FixtureUpstream
) -> None:
    bad = example_job(200)
    bad[field] = value
    upstream.scenario.body = json.dumps(
        {"jobs": [example_job(), bad], "meta": {"total": 2}}
    ).encode()
    _failure(upstream.client(), code)


def test_wire_depth_count_and_encoding_caps(upstream: FixtureUpstream) -> None:
    upstream.scenario = Scenario(body=b"a" * (MAX_RESPONSE_BYTES + 1))
    _failure(upstream.client(), "source_response_too_large")
    nested: object = "value"
    for _ in range(40):
        nested = [nested]
    job = example_job()
    job["metadata"] = nested
    upstream.scenario = Scenario(body=json.dumps({"jobs": [job], "meta": {"total": 1}}).encode())
    _failure(upstream.client(), "source_invalid_response")
    upstream.scenario = Scenario(
        body=json.dumps(
            {
                "jobs": [example_job(index + 1) for index in range(MAX_JOBS + 1)],
                "meta": {"total": MAX_JOBS + 1},
            }
        ).encode()
    )
    _failure(upstream.client(), "source_response_too_large")
    upstream.scenario = Scenario(
        body=gzip.compress(b"x" * (2 * MAX_RESPONSE_BYTES)), headers={"Content-Encoding": "gzip"}
    )
    _failure(upstream.client(), "source_unsupported_encoding")
    upstream.scenario = Scenario(headers={"Content-Type": "text/javascript"})
    _failure(upstream.client(), "source_invalid_response")
    upstream.scenario = Scenario(truncate_bytes=8)
    _failure(upstream.client(), "source_invalid_response")


def test_chunked_published_list_is_bounded_and_supported(upstream: FixtureUpstream) -> None:
    upstream.scenario.chunked = True
    result = upstream.client().read("fictional_board")
    assert len(result.jobs) == 1
    assert result.excluded_prospect_count == 1


def test_cancel_closes_real_connection_promptly_and_pre_cancel_does_not_resolve(
    upstream: FixtureUpstream,
) -> None:
    interrupted = threading.Event()
    upstream.scenario.delay_seconds = 5
    outcomes: list[str] = []

    def read() -> None:
        try:
            upstream.client().read("fictional_board", interrupted)
        except SourceFailure as error:
            outcomes.append(error.code)

    thread = threading.Thread(target=read)
    thread.start()
    assert upstream.request_received.wait(2)
    started = time.monotonic()
    interrupted.set()
    thread.join(timeout=1)
    assert not thread.is_alive()
    assert time.monotonic() - started < 1
    assert outcomes == ["source_cancelled"]
    assert upstream.client_disconnected.wait(1)
    calls: list[str] = []

    def resolve(host: str, port: int) -> tuple[str]:
        calls.append(host)
        return ("8.8.8.8",)

    client = GreenhouseClient(
        resolver=resolve,
        ssl_context=upstream.context,
        address_map=lambda address, port: upstream.address,
    )
    with pytest.raises(SourceFailure, match="source_cancelled"):
        client.read("fictional_board", interrupted)
    assert not calls


@pytest.fixture
def gateway(
    upstream: FixtureUpstream, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[SourceGatewayClient, SourceGatewayServer]]:
    server = SourceGatewayServer(("127.0.0.1", 0), upstream.client())
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    original_connect = greenhouse._connect

    def local_gateway(
        address: str,
        port: int,
        budget: greenhouse._Budget,
        context: ssl.SSLContext | None,
        *,
        server_hostname: str | None = None,
    ) -> socket.socket:
        if context is None:
            assert (address, port) == ("172.30.0.2", 8766)
            return original_connect("127.0.0.1", int(server.server_address[1]), budget, None)
        return original_connect(address, port, budget, context, server_hostname=server_hostname)

    monkeypatch.setattr(greenhouse, "_resolve", lambda host, port: ("172.30.0.2",))
    monkeypatch.setattr(greenhouse, "_connect", local_gateway)
    try:
        yield SourceGatewayClient(), server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_real_worker_client_http_gateway_tls_upstream_and_safe_error(
    gateway: tuple[SourceGatewayClient, SourceGatewayServer], upstream: FixtureUpstream
) -> None:
    client, _ = gateway
    result = client.read("fictional_board")
    assert len(result.jobs) == 1 and result.excluded_prospect_count == 1
    assert result.jobs[0].snapshot == example_job()
    upstream.scenario = Scenario(status=429, headers={"Retry-After": "1200"})
    assert _failure(client, "source_rate_limited").retry_after_seconds == 1200
    upstream.scenario = Scenario(status=403, body=b"private diagnostic must not leave relay")
    assert _failure(client, "source_forbidden").retry_after_seconds is None


@pytest.mark.parametrize("digits", [129, 4000])
def test_large_numeric_retry_after_is_not_shortened(
    gateway: tuple[SourceGatewayClient, SourceGatewayServer],
    upstream: FixtureUpstream,
    digits: int,
) -> None:
    client, _ = gateway
    minimum = "9" * digits
    upstream.scenario = Scenario(status=429, headers={"Retry-After": minimum})
    assert _failure(client, "source_rate_limited").retry_after_seconds == int(minimum)


def test_over_limit_numeric_retry_after_is_rejected_not_defaulted(
    gateway: tuple[SourceGatewayClient, SourceGatewayServer], upstream: FixtureUpstream
) -> None:
    client, _ = gateway
    upstream.scenario = Scenario(status=429, headers={"Retry-After": "9" * 4096})
    assert _failure(client, "source_invalid_response").retry_after_seconds is None


def test_gateway_cap_allows_raw_and_normalized_snapshot_duplication(
    gateway: tuple[SourceGatewayClient, SourceGatewayServer], upstream: FixtureUpstream
) -> None:
    client, _ = gateway
    content = "x" * 175_000
    payload = {
        "jobs": [example_job(index + 1, content=content) for index in range(18)],
        "meta": {"total": 18},
    }
    upstream.scenario.body = json.dumps(payload).encode()
    assert len(upstream.scenario.body) < MAX_RESPONSE_BYTES
    result = client.read("fictional_board")
    assert len(result.jobs) == 18
    assert MAX_RESPONSE_BYTES < len(greenhouse.serialize_board(result)) < MAX_GATEWAY_BYTES


@pytest.mark.parametrize(
    "raw_request",
    [
        b"POST /v1/boards/fictional_board/jobs?content=true HTTP/1.1\r\nHost: source-egress:8766\r\n\r\n",
        b"GET https://evil.org/ HTTP/1.1\r\nHost: source-egress:8766\r\n\r\n",
        b"GET /v1/boards/a%2fb/jobs?content=true HTTP/1.1\r\nHost: source-egress:8766\r\n\r\n",
        b"GET /v1/boards/fictional_board/jobs?content=true&callback=x HTTP/1.1\r\nHost: source-egress:8766\r\n\r\n",
        b"GET /v1/boards/fictional_board/jobs?content=true HTTP/1.1\r\nHost: evil.org\r\n\r\n",
        b"GET /v1/boards/fictional_board/jobs?content=true HTTP/1.1\r\nHost: source-egress:8766\r\nAuthorization: Bearer secret\r\n\r\n",
    ],
)
def test_gateway_is_not_a_proxy_or_authenticated_fetcher(
    raw_request: bytes,
    gateway: tuple[SourceGatewayClient, SourceGatewayServer],
    upstream: FixtureUpstream,
) -> None:
    _, server = gateway
    with socket.create_connection(
        ("127.0.0.1", int(server.server_address[1])), timeout=2
    ) as stream:
        stream.sendall(raw_request)
        response = stream.recv(4096)
    assert response.startswith((b"HTTP/1.1 400", b"HTTP/1.1 502"))
    assert not upstream.requests


def test_gateway_revalidates_normalized_data_not_just_schema(
    gateway: tuple[SourceGatewayClient, SourceGatewayServer],
    upstream: FixtureUpstream,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = gateway
    good = upstream.client().read("fictional_board")
    original = greenhouse.serialize_board

    def tampered(board: BoardRead) -> bytes:
        value = cast(dict[str, object], json.loads(original(board)))
        jobs = cast(list[dict[str, object]], value["jobs"])
        jobs[0]["description_text"] = "unattributed tampered description"
        return json.dumps(value).encode()

    with pytest.raises(SourceFailure, match="source_invalid_response"):
        greenhouse.deserialize_board(tampered(good), greenhouse._Budget(None))
    monkeypatch.setattr("offerloom.source_gateway.serialize_board", tampered)
    _failure(client, "source_invalid_response")


def test_worker_cancellation_reaches_gateway_upstream(
    gateway: tuple[SourceGatewayClient, SourceGatewayServer], upstream: FixtureUpstream
) -> None:
    client, _ = gateway
    interrupted = threading.Event()
    upstream.scenario.delay_seconds = 5
    outcomes: list[str] = []

    def read() -> None:
        try:
            client.read("fictional_board", interrupted)
        except SourceFailure as error:
            outcomes.append(error.code)

    thread = threading.Thread(target=read)
    thread.start()
    assert upstream.request_received.wait(2)
    interrupted.set()
    thread.join(timeout=1)
    assert outcomes == ["source_cancelled"]
    assert not thread.is_alive()
    assert upstream.client_disconnected.wait(1)


def test_gateway_admission_rejects_without_unbounded_threads(
    gateway: tuple[SourceGatewayClient, SourceGatewayServer], upstream: FixtureUpstream
) -> None:
    _, server = gateway
    sockets: list[socket.socket] = []
    try:
        for _ in range(MAX_IN_FLIGHT):
            stream = socket.create_connection(
                ("127.0.0.1", int(server.server_address[1])), timeout=2
            )
            stream.sendall(b"GET /v1/boards/")
            sockets.append(stream)
        time.sleep(0.1)
        with socket.create_connection(
            ("127.0.0.1", int(server.server_address[1])), timeout=2
        ) as stream:
            assert stream.recv(4096).startswith(b"HTTP/1.1 503")
        assert not upstream.requests
    finally:
        for stream in sockets:
            stream.close()


def test_worker_gateway_target_is_fixed() -> None:
    for host, port in [
        ("localhost", 8766),
        ("127.0.0.1", 8766),
        ("source-egress", 443),
        ("https://source-egress", 8766),
    ]:
        with pytest.raises(ValueError, match="source_gateway_address_denied"):
            SourceGatewayClient(host, port)
    error = SourceFailure("sensitive unexpected upstream text")
    assert str(error) == "source_invalid_response"
    assert error.retry_after_seconds is None


def test_dns_lookup_deadline_is_bounded_without_opening_a_connection(
    upstream: FixtureUpstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = threading.Event()

    def stalled(host: str, port: int) -> tuple[str, ...]:
        del host, port
        release.wait(2)
        return ("8.8.8.8",)

    monkeypatch.setattr(greenhouse, "CONNECT_TIMEOUT", 0.1)
    client = GreenhouseClient(
        resolver=stalled,
        ssl_context=upstream.context,
        address_map=lambda address, port: upstream.address,
    )
    started = time.monotonic()
    try:
        _failure(client, "source_timeout")
        assert time.monotonic() - started < 1
        assert not upstream.requests
    finally:
        release.set()


def test_total_deadline_bounds_a_real_slow_tls_response(
    upstream: FixtureUpstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    class ShortBudget(greenhouse._Budget):
        def __init__(self, interrupted: threading.Event | None, seconds: float = 15.0) -> None:
            del seconds
            super().__init__(interrupted, seconds=0.2)

    monkeypatch.setattr(greenhouse, "_Budget", ShortBudget)
    upstream.scenario.delay_seconds = 2
    started = time.monotonic()
    _failure(upstream.client(), "source_timeout")
    assert time.monotonic() - started < 1
    assert upstream.client_disconnected.wait(1)
