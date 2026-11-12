# SPDX-License-Identifier: MIT OR Apache-2.0
"""Fixed-route egress relay; no application settings or DB access.

Deployment grants only the dedicated internal worker network and outbound bridge.
The normal module entry point always constructs the production TLS client.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import select
import socket
import socketserver
import threading
from pathlib import Path
from typing import cast

from bonafides.evidence_capture_config import (
    GitHubSource,
    load_sources,
    validate_github_repository,
)
from bonafides.github_capture import (
    GitHubClient,
    deserialize_commits,
    serialize_commits,
)
from bonafides.greenhouse import (
    TOTAL_TIMEOUT,
    BoardRead,
    GreenhouseClient,
    SourceFailure,
    _Budget,
    _Wire,
    deserialize_board,
    serialize_board,
    validate_board_token,
)
from bonafides.model_gateway import GATEWAY_PORT as MODEL_GATEWAY_PORT
from bonafides.model_gateway import ModelGatewayServer
from bonafides.release_updates import TIMEOUT as RELEASE_TIMEOUT
from bonafides.release_updates import ReleaseClient, parse_release, serialize_release

GATEWAY_PORT = 8766
MAX_IN_FLIGHT = 4
_REQUEST_LIMIT = 4096
_ROUTE = re.compile(rb"GET /v1/boards/([A-Za-z0-9_-]{1,120})/jobs\?content=true HTTP/1\.[01]\Z")
_GITHUB_ROUTE = re.compile(
    rb"GET /v1/github/([A-Za-z0-9_.-]{1,39}/[A-Za-z0-9_.-]{1,100})/commits\?per_page=20 HTTP/1\.[01]\Z"
)
_STATUS = {
    "source_invalid_token": 400,
    "source_forbidden": 403,
    "source_not_found": 404,
    "source_rate_limited": 429,
    "source_gateway_busy": 503,
    "source_timeout": 504,
    "source_cancelled": 409,
}


def _error_bytes(failure: SourceFailure) -> bytes:
    return json.dumps(
        {"code": failure.code, "retry_after_seconds": failure.retry_after_seconds},
        separators=(",", ":"),
    ).encode("ascii")


def _reply(wire: _Wire, status: int, body: bytes, delay: int | None = None) -> None:
    retry = f"Retry-After: {delay}\r\n" if delay is not None else ""
    wire.send(
        (
            f"HTTP/1.1 {status} Response\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\nCache-Control: no-store\r\n"
            f"Connection: close\r\n{retry}\r\n"
        ).encode("ascii")
    )
    wire.send(body)


class SourceGatewayServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    """Bounded socket admission; overload never launches another request thread."""

    allow_reuse_address = True
    daemon_threads = True
    block_on_close = False
    request_queue_size = 8

    def __init__(
        self,
        address: tuple[str, int],
        client: GreenhouseClient,
        *,
        github_client: GitHubClient | None = None,
        release_client: ReleaseClient | None = None,
        capture_config_file: Path | None = None,
    ) -> None:
        self.client = client
        self.github_client = github_client or GitHubClient()
        self.release_client = release_client or ReleaseClient()
        self.capture_config_file = capture_config_file
        self._slots = threading.BoundedSemaphore(MAX_IN_FLIGHT)
        super().__init__(address, _Handler)

    def github_read(self, repository: str, interrupted: threading.Event) -> bytes:
        # Re-read the private allowlist before any upstream DNS/socket/token access.
        # The worker supplies only an inert repository, never a path or credential.
        try:
            sources = load_sources(self.capture_config_file)
        except Exception:
            raise SourceFailure("source_invalid_response") from None
        allowed = [
            source
            for source in sources.values()
            if isinstance(source, GitHubSource) and source.repository == repository
        ]
        if len(allowed) != 1 or not allowed[0].enabled:
            raise SourceFailure("source_forbidden")
        commits = self.github_client.read(repository, allowed[0].token_file, interrupted)
        return serialize_commits(repository, commits)

    def process_request(
        self, request: socket.socket | tuple[bytes, socket.socket], client_address: tuple[str, int]
    ) -> None:
        if not isinstance(request, socket.socket):
            raise TypeError("source_gateway_tcp_required")
        if not self._slots.acquire(blocking=False):
            # A nonblocking, tiny reply cannot stall the accept loop on a slow peer.
            request.setblocking(False)
            with contextlib.suppress(OSError):
                body = _error_bytes(SourceFailure("source_gateway_busy", 1))
                request.send(
                    (
                        f"HTTP/1.1 503 Busy\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nRetry-After: 1\r\nConnection: close\r\n\r\n"
                    ).encode("ascii")
                    + body
                )
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
        # socketserver's default traceback can include source data from exceptions.
        del request, client_address


def _request_source(wire: _Wire) -> tuple[str, str]:
    first = wire.line(512)
    match = _ROUTE.fullmatch(first)
    github_match = _GITHUB_ROUTE.fullmatch(first)
    release = first in {b"GET /v1/releases/latest HTTP/1.0", b"GET /v1/releases/latest HTTP/1.1"}
    if match is None and github_match is None and not release:
        raise SourceFailure("source_invalid_token")
    size = len(first) + 2
    headers: dict[str, str] = {}
    for _ in range(16):
        line = wire.line(1024)
        size += len(line) + 2
        if size > _REQUEST_LIMIT:
            raise SourceFailure("source_invalid_response")
        if not line:
            if wire.buffer:
                raise SourceFailure("source_invalid_response")
            if (
                headers.get("host") != "source-egress:8766"
                or headers.get("content-length", "0") != "0"
                or "transfer-encoding" in headers
                or "authorization" in headers
                or "proxy-authorization" in headers
                or "cookie" in headers
                or "expect" in headers
            ):
                raise SourceFailure("source_invalid_response")
            if release:
                return "release", ""
            if match is not None:
                return "greenhouse", validate_board_token(match[1].decode("ascii"))
            assert github_match is not None
            repository = github_match[1].decode("ascii")
            # Require the canonical spelling in the internal protocol.
            try:
                if validate_github_repository(repository) != repository:
                    raise ValueError
            except ValueError:
                raise SourceFailure("source_invalid_token") from None
            return "github", repository
        key, separator, value = line.partition(b":")
        if (
            not separator
            or re.fullmatch(rb"[A-Za-z0-9-]+", key) is None
            or any(char < 32 or char > 126 for char in value)
        ):
            raise SourceFailure("source_invalid_response")
        name = key.decode("ascii").lower()
        if name in headers:
            raise SourceFailure("source_invalid_response")
        headers[name] = value.decode("ascii").strip()
    raise SourceFailure("source_invalid_response")


class _Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        stream = cast(socket.socket, self.request)
        stream.setblocking(False)
        wire = _Wire(stream, _Budget(None, seconds=3.0), _REQUEST_LIMIT)
        interrupted = threading.Event()
        completed = threading.Event()
        watcher: threading.Thread | None = None
        try:
            kind, identity = _request_source(wire)
            wire.budget = _Budget(
                interrupted if kind == "release" else None,
                seconds=RELEASE_TIMEOUT if kind == "release" else TOTAL_TIMEOUT + 1.0,
            )

            def disconnect_watch() -> None:
                # The worker closes this connection on cancellation. No HTTP
                # cancellation endpoint, body, credential, or policy enters relay.
                while not completed.wait(0.05):
                    try:
                        ready, _, _ = select.select([stream], [], [], 0)
                        if ready:
                            stream.recv(1, socket.MSG_PEEK)
                            interrupted.set()
                            return
                    except BlockingIOError:
                        continue
                    except OSError:
                        interrupted.set()
                        return

            watcher = threading.Thread(
                target=disconnect_watch, daemon=True, name="source-disconnect"
            )
            watcher.start()
            server = cast(SourceGatewayServer, self.server)
            if kind == "release":
                payload = serialize_release(
                    server.release_client.read(interrupted, budget=wire.budget)
                )
                parse_release(payload, wire.budget, relay=True)
            elif kind == "github":
                payload = server.github_read(identity, interrupted)
                deserialize_commits(payload, identity, wire.budget)
            else:
                board: BoardRead = server.client.read(identity, interrupted)
                payload = serialize_board(board)
                # Independently verify the exact relay envelope before publishing it.
                deserialize_board(payload, wire.budget)
            if interrupted.is_set():
                raise SourceFailure("source_cancelled")
            _reply(wire, 200, payload)
        except SourceFailure as failure:
            # Never include upstream exception, request path, headers, or content.
            with contextlib.suppress(OSError, SourceFailure):
                wire.budget = _Budget(None, seconds=0.5)
                _reply(
                    wire,
                    _STATUS.get(failure.code, 502),
                    _error_bytes(failure),
                    failure.retry_after_seconds,
                )
        except Exception:
            with contextlib.suppress(OSError, SourceFailure):
                wire.budget = _Budget(None, seconds=0.5)
                _reply(wire, 502, _error_bytes(SourceFailure("source_invalid_response")))
        finally:
            completed.set()
            if watcher is not None:
                watcher.join(timeout=0.2)


def serve(
    client: GreenhouseClient | None = None, host: str = "0.0.0.0", port: int = GATEWAY_PORT
) -> None:
    if port != GATEWAY_PORT or host not in {"0.0.0.0", "127.0.0.1"}:
        raise ValueError("source_gateway_address_denied")
    configured = os.environ.get("BONAFIDES_EVIDENCE_CAPTURE_CONFIG_FILE")
    if configured is not None and not configured:
        raise ValueError("capture_configuration_invalid")
    capture_config_file = Path(configured) if configured is not None else None
    # Bind both roles before accepting anything. One accept loop also means an
    # unexpected listener failure cannot silently leave the other role running.
    with (
        SourceGatewayServer(
            (host, port),
            client or GreenhouseClient(),
            capture_config_file=capture_config_file,
        ) as source,
        ModelGatewayServer((host, MODEL_GATEWAY_PORT)) as model,
    ):
        servers = (source, model)
        for server in servers:
            server.socket.setblocking(False)
            server.timeout = 0
        while True:
            readable, _, _ = select.select([server.socket for server in servers], [], [])
            for server in servers:
                if server.socket in readable:
                    server.handle_request()


if __name__ == "__main__":
    serve()
