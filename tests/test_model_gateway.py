# SPDX-License-Identifier: MIT OR Apache-2.0
"""Real local sockets only; no installed model, Docker, or external service required."""

from __future__ import annotations

import contextlib
import http.client
import json
import select
import socket
import threading
import time
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast

import pytest

from bonafides import greenhouse
from bonafides import model_gateway as gateway

Relay = tuple[Callable[..., tuple[int, bytes]], dict[str, Any], gateway.ModelGatewayServer]

MODEL = "fixture-model:q4"
CHAT = {
    "model": MODEL,
    "stream": False,
    "keep_alive": 0,
    "messages": [
        {"role": "system", "content": "synthetic instruction"},
        {"role": "user", "content": "synthetic context"},
    ],
    "format": {"type": "object", "properties": {"answer": {"type": "string"}}},
    "options": {"temperature": 0, "num_ctx": 32768, "num_predict": 6000},
}


@pytest.fixture
def relay(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> Iterator[Relay]:
    state: dict[str, Any] = {"requests": [], "reply": None, "remote": False, "delay": 0}
    monkeypatch.setattr(gateway, "MAX_IN_FLIGHT", getattr(request, "param", gateway.MAX_IN_FLIGHT))

    class Upstream(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            state["requests"].append((self.command, self.path, body))
            if self.path == "/api/chat" and state.get("hold_response"):
                state["entered"].set()
                self.connection.settimeout(2)
                with contextlib.suppress(ConnectionResetError):
                    assert self.connection.recv(1) == b""
                state["closed"].set()
                self.close_connection = True
                return
            time.sleep(state["delay"])
            reply = state["reply"]
            if reply is None:
                payload: dict[str, object] = {
                    "models": [
                        {
                            "name": MODEL,
                            "digest": "a" * 64,
                            "remote_host": "cloud.invalid" if state["remote"] else "",
                        }
                    ]
                }
                if self.path == "/api/version":
                    payload = {"version": "fixture-runtime-1"}
                elif self.path == "/api/chat":
                    payload = {"done": True, "message": {"role": "assistant", "content": "{}"}}
                data = json.dumps(payload).encode()
                reply = (
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    + f"Content-Length: {len(data)}\r\n\r\n".encode()
                    + data
                )
            with contextlib.suppress(OSError):
                self.connection.sendall(reply)
            self.close_connection = True

        do_POST = do_GET

        def log_message(self, format: str, *args: object) -> None:
            pass

    with (
        ThreadingHTTPServer(("127.0.0.1", 0), Upstream) as upstream,
        gateway.ModelGatewayServer(("127.0.0.1", 0)) as server,
    ):

        def resolve(host: str, port: int) -> tuple[str, ...]:
            assert (host, port) == ("host.docker.internal", 11434)
            return ("127.0.0.1",)

        def connect(
            address: str, port: int, budget: greenhouse._Budget, context: None
        ) -> socket.socket:
            assert (address, port, context) == ("127.0.0.1", 11434, None)
            return greenhouse._connect(address, upstream.server_port, budget, context)

        monkeypatch.setattr(gateway, "_resolve", resolve)
        monkeypatch.setattr(gateway, "_connect", connect)
        state["connect"] = connect
        threads = [
            threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
            for s in (upstream, server)
        ]
        for thread in threads:
            thread.start()

        def send(
            first: bytes = b"GET /api/tags",
            body: bytes = b"",
            extra: bytes = b"",
            *,
            length: int | None = None,
        ) -> tuple[int, bytes]:
            with socket.create_connection(
                cast(tuple[str, int], server.server_address), timeout=2
            ) as stream:
                declared = len(body) if length is None else length
                stream.sendall(
                    first
                    + b" HTTP/1.1\r\nHost: source-egress:11434\r\nContent-Type: application/json\r\n"
                    + f"Content-Length: {declared}\r\n".encode()
                    + extra
                    + b"\r\n"
                    + body
                )
                response = http.client.HTTPResponse(stream)
                response.begin()
                assert response.getheader("Connection") == "close"
                data = response.read()
                content_length = response.getheader("Content-Length")
                assert content_length is not None and int(content_length) == len(data)
                return response.status, data

        try:
            yield send, state, server
        finally:
            server.shutdown()
            upstream.shutdown()
            for thread in threads:
                thread.join(timeout=2)


def test_real_metadata_chat_sequence_preserves_bytes(relay: Relay) -> None:
    request, state, _ = relay
    payload = json.dumps(CHAT, indent=1).encode()
    assert request()[0] == request(b"GET /api/version")[0] == 200
    status, body = request(b"POST /api/chat", payload)
    assert status == 200 and json.loads(body)["done"] is True
    assert request()[0] == 200
    assert [path for _, path, _ in state["requests"]] == [
        "/api/tags",
        "/api/version",
        "/api/chat",
        "/api/tags",
    ]
    assert state["requests"][2][2] == payload


@pytest.mark.parametrize(
    "first,body,extra",
    [
        (b"CONNECT host.invalid:443", b"", b""),
        (b"GET http://host.invalid/api/tags", b"", b""),
        (b"POST /api/pull", b"{}", b""),
        (b"DELETE /api/delete", b"", b""),
        (b"GET /api/tags?url=http://host.invalid", b"", b""),
        (b"GET /api/tags", b"", b"Authorization: Bearer synthetic-secret\r\n"),
        (b"GET /api/tags", b"", b"Host: host.invalid\r\n"),
        (b"GET /api/tags", b"", b"Content-Length: 0\r\n"),
        (b"GET /api/tags", b"", b"Transfer-Encoding: chunked\r\n"),
        (b"GET /api/tags", b"", b"User-Agent: " + b"x" * 9000 + b"\r\n"),
        (b"POST /api/chat", b"{}", b"Expect: 100-continue\r\n"),
        (b"POST /api/chat", b'{"model":"fixture-model:q4","model":"other:q4"}', b""),
    ],
)
def test_hostile_protocol_never_forwarded(
    relay: Relay, first: bytes, body: bytes, extra: bytes
) -> None:
    request, state, _ = relay
    status, data = request(first, body, extra)
    assert status == 400 and b"synthetic-secret" not in data
    assert state["requests"] == []


@pytest.mark.parametrize(
    "change",
    [
        {"stream": True},
        {"model": "fixture-model"},
        {"model": "fixture-model:latest"},
        {"model": "fixture-cloud:q4"},
        {"model": "host.invalid/model:q4"},
        {"tools": []},
        {"keep_alive": "10m"},
        {"options": {"num_ctx": 32768, "num_predict": -1, "temperature": 0}},
    ],
)
def test_chat_controls_rejected_before_upstream(relay: Relay, change: dict[str, object]) -> None:
    request, state, _ = relay
    assert request(b"POST /api/chat", json.dumps(CHAT | change).encode())[0] == 400
    assert not state["requests"]


@pytest.mark.parametrize("remote,model", [(True, MODEL), (False, "missing:q4")])
def test_only_exact_installed_local_model_can_infer(relay: Relay, remote: bool, model: str) -> None:
    request, state, _ = relay
    state["remote"] = remote
    assert request()[0] == 200
    assert request(b"POST /api/chat", json.dumps(CHAT | {"model": model}).encode())[0] == 403
    assert [path for _, path, _ in state["requests"]] == ["/api/tags"]


@pytest.mark.parametrize(
    "reply",
    [
        b"HTTP/1.1 302 Found\r\nLocation: http://host.invalid\r\nContent-Length: 0\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 262145\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 10\r\n\r\n{}",
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Encoding: gzip\r\nContent-Length: 2\r\n\r\n{}",
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n40001\r\n",
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n" + b"x" * 262145,
        b"HTTP/1.1 200 OK\r\nSensitive: " + b"sensitive-upstream" * 2000 + b"\r\n\r\n",
    ],
)
def test_response_boundaries_are_redacted(relay: Relay, reply: bytes) -> None:
    request, state, _ = relay
    state["reply"] = reply
    assert request() == (502, b'{"code":"model_gateway_rejected"}')
    assert len(state["requests"]) == 1


def test_request_and_response_size_edges(relay: Relay) -> None:
    request, state, _ = relay
    assert request(b"POST /api/chat", length=gateway.MAX_REQUEST + 1)[0] == 400
    assert not state["requests"]
    assert request()[0] == 200
    payload = json.dumps(CHAT).encode()
    data = json.loads(payload)
    data["messages"][1]["content"] += "x" * (gateway.MAX_REQUEST - len(payload))
    payload = json.dumps(data).encode()
    assert len(payload) == gateway.MAX_REQUEST
    assert request(b"POST /api/chat", payload)[0] == 200
    body = b'{"padding":"' + b"x" * (gateway.MAX_RESPONSE - 14) + b'"}'
    assert len(body) == gateway.MAX_RESPONSE
    state["reply"] = (
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
        + f"Content-Length: {len(body)}\r\n\r\n".encode()
        + body
    )
    assert request() == (200, body)


def test_upstream_deadline_is_bounded(relay: Relay, monkeypatch: pytest.MonkeyPatch) -> None:
    request, state, _ = relay
    monkeypatch.setattr(gateway, "TOTAL_SECONDS", 0.03)
    state["delay"] = 0.1
    assert request()[0] == 502


def test_slow_requests_cannot_exceed_admission_bound(relay: Relay) -> None:
    _, state, server = relay
    with contextlib.ExitStack() as stack:
        for _ in range(gateway.MAX_IN_FLIGHT):
            stream = stack.enter_context(
                socket.create_connection(cast(tuple[str, int], server.server_address), timeout=2)
            )
            stream.sendall(b"GET /api/tags HTTP/1.1\r\n")
        with socket.create_connection(
            cast(tuple[str, int], server.server_address), timeout=2
        ) as stream:
            stream.sendall(b"GET /api/tags HTTP/1.1\r\n")
            assert stream.recv(4096).startswith(b"HTTP/1.1 503")
        assert not state["requests"]


def test_slow_request_header_deadline(relay: Relay, monkeypatch: pytest.MonkeyPatch) -> None:
    _, state, server = relay
    monkeypatch.setattr(gateway, "REQUEST_SECONDS", 0.03)
    with socket.create_connection(
        cast(tuple[str, int], server.server_address), timeout=2
    ) as stream:
        stream.sendall(b"GET /api/tags HTTP/1.1\r\n")
        response = http.client.HTTPResponse(stream)
        response.begin()
        assert response.status == 400
        assert response.read() == b'{"code":"model_gateway_rejected"}'
    assert not state["requests"]


def test_chat_requires_fresh_observed_metadata_without_extra_requests(
    relay: Relay, monkeypatch: pytest.MonkeyPatch
) -> None:
    request, state, _ = relay
    payload = json.dumps(CHAT).encode()
    assert request(b"POST /api/chat", payload)[0] == 403
    assert not state["requests"]
    assert request()[0] == 200
    monkeypatch.setattr(gateway, "METADATA_SECONDS", 0)
    assert request(b"POST /api/chat", payload)[0] == 403
    assert [path for _, path, _ in state["requests"]] == ["/api/tags"]


@pytest.mark.parametrize(
    "models",
    [
        [],
        [{"name": MODEL, "digest": "invalid"}],
        [{"name": MODEL, "digest": "a" * 64, "remote_model": "remote:q4"}],
        [{"name": MODEL, "digest": "a" * 64}] * 2,
        [{"name": "fixture:latest", "digest": "a" * 64}],
        [{"name": "fixture:cloud", "digest": "a" * 64}],
    ],
)
def test_new_metadata_revokes_previously_allowed_model(
    relay: Relay, models: list[dict[str, object]]
) -> None:
    request, state, _ = relay
    assert request()[0] == 200
    body = json.dumps({"models": models}).encode()
    state["reply"] = (
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
        + f"Content-Length: {len(body)}\r\n\r\n".encode()
        + body
    )
    assert request() == (200, body)
    assert request(b"POST /api/chat", json.dumps(CHAT).encode())[0] == 403
    assert [path for _, path, _ in state["requests"]] == ["/api/tags", "/api/tags"]


@pytest.mark.parametrize("phase", ["resolution", "connect", "response"])
@pytest.mark.parametrize("relay", [1], indirect=True)
def test_disconnect_cancels_upstream_and_releases_admission(
    relay: Relay, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    request, state, server = relay
    assert request()[0] == 200
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    cancellation: list[threading.Event] = []
    resolve = greenhouse._bounded_resolve
    connect: Callable[[str, int, greenhouse._Budget, None], socket.socket] = state["connect"]

    def gate(budget: greenhouse._Budget) -> None:
        if not entered.is_set():
            assert budget.interrupted is not None
            cancellation.append(budget.interrupted)
            entered.set()
            assert release.wait(2)

    def held_resolution(
        resolver: greenhouse.Resolver, host: str, port: int, budget: greenhouse._Budget
    ) -> tuple[str, ...]:
        gate(budget)
        return resolve(resolver, host, port, budget)

    def held_connect(
        address: str, port: int, budget: greenhouse._Budget, context: None
    ) -> socket.socket:
        gate(budget)
        return connect(address, port, budget, context)

    if phase == "resolution":
        monkeypatch.setattr(gateway, "_bounded_resolve", held_resolution)
    elif phase == "connect":
        monkeypatch.setattr(gateway, "_connect", held_connect)
    else:
        state.update(hold_response=True, entered=entered, closed=closed)
    payload = json.dumps(CHAT).encode()
    stream: socket.socket | None = None
    try:
        deadline = time.monotonic() + 1
        while not entered.is_set() and time.monotonic() < deadline:
            stream = socket.create_connection(
                cast(tuple[str, int], server.server_address), timeout=2
            )
            stream.sendall(
                b"POST /api/chat HTTP/1.1\r\nHost: source-egress:11434\r\n"
                b"Content-Type: application/json\r\n"
                + f"Content-Length: {len(payload)}\r\n\r\n".encode()
                + payload
            )
            while not entered.is_set() and time.monotonic() < deadline:
                if select.select([stream], [], [], 0.01)[0]:
                    # A preceding response can still be finishing its watcher.
                    assert stream.recv(4096).startswith(b"HTTP/1.1 503")
                    stream.close()
                    stream = None
                    break
        assert stream is not None and entered.is_set()
        assert request()[0] == 503
        stream.close()
        stream = None
        if phase == "response":
            assert closed.wait(1)
        else:
            assert cancellation[0].wait(1)
        release.set()
        deadline = time.monotonic() + 1
        status = 503
        while time.monotonic() < deadline:
            status, _ = request(b"GET /api/version")
            if status != 503:
                break
            time.sleep(0.005)
        assert status == 200
        paths = [path for _, path, _ in state["requests"]]
        assert paths == (
            ["/api/tags", "/api/chat", "/api/version"]
            if phase == "response"
            else ["/api/tags", "/api/version"]
        )
    finally:
        release.set()
        if stream is not None:
            stream.close()
