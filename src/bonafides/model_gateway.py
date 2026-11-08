# SPDX-License-Identifier: MIT OR Apache-2.0
"""Secret-free Ollama gateway: fixed host, installed tagged models, bounded JSON only."""

from __future__ import annotations

import contextlib
import ipaddress
import json
import re
import select
import socket
import socketserver
import threading
import time
from typing import cast

from bonafides.greenhouse import (
    _bounded_resolve,
    _Budget,
    _connect,
    _resolve,
    _Wire,
)

GATEWAY_PORT = 11434
UPSTREAM_HOST = "host.docker.internal"
MAX_IN_FLIGHT = 4
MAX_HEADERS = 8192
MAX_REQUEST = 131072
MAX_RESPONSE = 262144
REQUEST_SECONDS = 3.0
TOTAL_SECONDS = 300.0
METADATA_SECONDS = 300.0
_ROUTES = {b"GET /api/tags HTTP/1.1", b"GET /api/version HTTP/1.1", b"POST /api/chat HTTP/1.1"}
_HEADERS = {
    "host",
    "content-length",
    "content-type",
    "accept",
    "accept-encoding",
    "connection",
    "user-agent",
}


def _reply(wire: _Wire, status: int, body: bytes) -> None:
    wire.send(
        (
            f"HTTP/1.1 {status} Response\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\nCache-Control: no-store\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii")
    )
    wire.send(body)


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def _invalid_constant(value: str) -> object:
    raise ValueError("invalid_json_constant")


def _json(body: bytes) -> dict[str, object]:
    value = json.loads(
        body.decode("utf-8"), object_pairs_hook=_object, parse_constant=_invalid_constant
    )
    if not isinstance(value, dict):
        raise ValueError("object_required")
    return value


def _request(wire: _Wire) -> tuple[str, str, bytes]:
    first = wire.line(128)
    if first not in _ROUTES:
        raise ValueError("route_denied")
    headers: dict[str, str] = {}
    size = len(first) + 2
    for _ in range(16):
        line = wire.line(2048)
        size += len(line) + 2
        if size > MAX_HEADERS:
            raise ValueError("headers_limit")
        if not line:
            break
        key, separator, value = line.partition(b":")
        if (
            not separator
            or not re.fullmatch(rb"[A-Za-z0-9-]+", key)
            or any(c < 32 or c > 126 for c in value)
        ):
            raise ValueError("headers_invalid")
        name = key.decode("ascii").lower()
        if name not in _HEADERS or name in headers:
            raise ValueError("headers_denied")
        headers[name] = value.decode("ascii").strip()
    else:
        raise ValueError("headers_limit")
    if (
        headers.get("host") != "source-egress:11434"
        or headers.get("accept-encoding", "identity") != "identity"
    ):
        raise ValueError("headers_denied")
    length = headers.get("content-length", "0")
    if re.fullmatch(r"[0-9]{1,6}", length) is None or int(length) > MAX_REQUEST:
        raise ValueError("body_limit")
    method, path, _ = first.decode("ascii").split(" ")
    if method == "GET" and int(length) != 0:
        raise ValueError("body_denied")
    if method == "POST" and (not int(length) or headers.get("content-type") != "application/json"):
        raise ValueError("body_required")
    body = wire.take(int(length))
    if wire.buffer:
        raise ValueError("pipeline_denied")
    return method, path, body


def _local_tag(model: object) -> bool:
    return (
        isinstance(model, str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*:[A-Za-z0-9_.-]+", model) is not None
        and "cloud" not in model.lower()
        and not model.lower().endswith(":latest")
    )


def _chat_model(body: bytes) -> str:
    data = _json(body)
    model = data.get("model")
    messages, options = data.get("messages"), data.get("options")
    if (
        set(data) != {"model", "stream", "keep_alive", "messages", "format", "options"}
        or not isinstance(model, str)
        or not _local_tag(model)
        or data["stream"] is not False
        or type(data["keep_alive"]) is not int
        or data["keep_alive"] != 0
        or not isinstance(data["format"], dict)
        or not isinstance(messages, list)
        or not 1 <= len(messages) <= 64
        or any(
            not isinstance(m, dict)
            or set(m) != {"role", "content"}
            or m["role"] not in ("system", "user", "assistant")
            or not isinstance(m["content"], str)
            for m in messages
        )
        or not isinstance(options, dict)
        or set(options) != {"temperature", "num_ctx", "num_predict"}
        or type(options["temperature"]) not in (int, float)
        or options["temperature"] != 0
        or type(options["num_ctx"]) is not int
        or not 1024 <= options["num_ctx"] <= 131072
        or type(options["num_predict"]) is not int
        or not 1 <= options["num_predict"] <= 6000
    ):
        raise ValueError("chat_denied")
    return model


def _upstream(method: str, path: str, body: bytes, budget: _Budget) -> tuple[int, bytes]:
    # Never consult proxy environment variables or a caller-supplied destination.
    addresses = _bounded_resolve(_resolve, UPSTREAM_HOST, GATEWAY_PORT, budget)
    address = addresses[0]
    parsed = ipaddress.ip_address(address)
    if (
        not parsed.is_private
        or parsed.is_unspecified
        or parsed.is_multicast
        or parsed.is_link_local
    ):
        raise ValueError("upstream_denied")
    with _connect(address, GATEWAY_PORT, budget, None) as stream:
        wire = _Wire(stream, budget, MAX_RESPONSE + 16384)
        wire.send(
            (
                f"{method} {path} HTTP/1.1\r\nHost: {UPSTREAM_HOST}:11434\r\n"
                "Content-Type: application/json\r\nAccept: application/json\r\n"
                f"Accept-Encoding: identity\r\nContent-Length: {len(body)}\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii")
        )
        wire.send(body)
        status, headers = wire.headers()
        if not 200 <= status <= 599 or 300 <= status <= 399 or "location" in headers:
            raise ValueError("upstream_redirect_denied")
        length = headers.get("content-length")
        if length is not None and (not length.isdecimal() or int(length) > MAX_RESPONSE):
            raise ValueError("response_limit")
        payload = wire.body(headers)
        if len(payload) > MAX_RESPONSE:
            raise ValueError("response_limit")
        return status, payload


class _Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        stream = cast(socket.socket, self.request)
        stream.setblocking(False)
        wire = _Wire(stream, _Budget(None, seconds=REQUEST_SECONDS), MAX_REQUEST + MAX_HEADERS)
        status = 400
        interrupted, completed = threading.Event(), threading.Event()
        watcher: threading.Thread | None = None
        try:
            method, path, body = _request(wire)
            model = _chat_model(body) if method == "POST" else None
            server = cast(ModelGatewayServer, self.server)
            if model is not None and not server.allows_model(model):
                status = 403
                raise ValueError("model_not_local")
            status = 502

            def disconnect_watch() -> None:
                while not completed.is_set():
                    try:
                        readable, _, _ = select.select([stream], [], [], 0.05)
                        if readable:
                            stream.recv(1, socket.MSG_PEEK)
                            interrupted.set()
                            return
                    except BlockingIOError:
                        continue
                    except OSError:
                        interrupted.set()
                        return

            monitor = threading.Thread(
                target=disconnect_watch, daemon=True, name="model-disconnect"
            )
            monitor.start()
            watcher = monitor
            wire.budget = _Budget(interrupted, seconds=TOTAL_SECONDS)
            started = time.monotonic()
            upstream_status, payload = _upstream(method, path, body, wire.budget)
            if path == "/api/tags" and upstream_status == 200:
                server.observe_models(payload, started)
            _reply(wire, upstream_status, payload)
        except Exception:
            # No request, exception, upstream diagnostic, or credential enters logs.
            with contextlib.suppress(Exception):
                wire.budget = _Budget(None, seconds=0.5)
                _reply(wire, status, b'{"code":"model_gateway_rejected"}')
        finally:
            completed.set()
            if watcher is not None:
                watcher.join(timeout=0.2)


class ModelGatewayServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    """Bound admission before creating threads, including slow request senders."""

    allow_reuse_address = True
    daemon_threads = True
    block_on_close = False
    request_queue_size = 8

    def __init__(self, address: tuple[str, int]) -> None:
        self._slots = threading.BoundedSemaphore(MAX_IN_FLIGHT)
        self._metadata_lock = threading.Lock()
        self._models: set[str] = set()
        self._observed_at = 0.0
        super().__init__(address, _Handler)

    def observe_models(self, payload: bytes, started: float) -> None:
        allowed: set[str] = set()
        seen: set[str] = set()
        duplicates: set[str] = set()
        try:
            models = _json(payload).get("models")
            if not isinstance(models, list) or len(models) > 1024:
                raise ValueError("metadata_invalid")
            for item in models:
                if not isinstance(item, dict):
                    continue
                name, digest = item.get("name"), item.get("digest")
                if not isinstance(name, str):
                    continue
                if name in seen:
                    duplicates.add(name)
                seen.add(name)
                if (
                    _local_tag(name)
                    and isinstance(digest, str)
                    and re.fullmatch(r"[0-9a-f]{64}", digest) is not None
                    and not item.get("remote_host")
                    and not item.get("remote_model")
                ):
                    allowed.add(name)
        except (ValueError, UnicodeError, RecursionError):
            allowed.clear()
        with self._metadata_lock:
            # A slow older response must not undo a newer observation's revocation.
            if started >= self._observed_at:
                self._models = allowed - duplicates
                self._observed_at = started

    def allows_model(self, model: str) -> bool:
        with self._metadata_lock:
            return time.monotonic() - self._observed_at < METADATA_SECONDS and model in self._models

    def process_request(
        self, request: socket.socket | tuple[bytes, socket.socket], client_address: tuple[str, int]
    ) -> None:
        if not isinstance(request, socket.socket):
            raise TypeError("model_gateway_tcp_required")
        if not self._slots.acquire(blocking=False):
            request.setblocking(False)
            with contextlib.suppress(OSError):
                request.send(b"HTTP/1.1 503 Busy\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(
        self, request: socket.socket | tuple[bytes, socket.socket], client_address: tuple[str, int]
    ) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def handle_error(
        self, request: socket.socket | tuple[bytes, socket.socket], client_address: tuple[str, int]
    ) -> None:
        del request, client_address
