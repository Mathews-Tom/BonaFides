# SPDX-License-Identifier: MIT OR Apache-2.0
"""TEST ONLY: real HTTPS upstream plus the production fixed-route HTTP relay.

Run ``python tests/greenhouse_fixture.py --scenario-file /tmp/scenario.json``.
The optional JSON file is reread per upstream request and accepts status, headers,
payload, body_text, delay_seconds, chunked and truncate_bytes. It is test data,
not application configuration. No production entry point imports this module.
"""

from __future__ import annotations

import argparse
import contextlib
import ipaddress
import json
import select
import ssl
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from bonafides.greenhouse import UPSTREAM_HOST, GreenhouseClient
from bonafides.source_gateway import SourceGatewayServer


def example_job(identity: int = 101, *, content: str | None = None) -> dict[str, object]:
    return {
        "id": identity,
        "internal_job_id": identity + 1000,
        "title": "Fictional infrastructure engineer",
        "location": {"name": "Remote"},
        "content": content
        if content is not None
        else '&lt;p&gt;Build reliable tools &amp;amp; services.&lt;/p&gt;&lt;script&gt;window.fixtureAttack=true&lt;/script&gt;&lt;img src="http://127.0.0.1/private"&gt;',
        "absolute_url": "https://careers.bonafides-fixture.org/jobs/101",
        "updated_at": "2026-10-25T08:00:00Z",
        "metadata": [
            {"id": 1, "name": "Team", "value": "Infrastructure", "value_type": "short_text"}
        ],
    }


def example_board() -> dict[str, object]:
    prospect = example_job(102)
    prospect["internal_job_id"] = None
    return {"jobs": [example_job(), prospect], "meta": {"total": 2}}


@dataclass
class Scenario:
    body: bytes = field(default_factory=lambda: json.dumps(example_board()).encode("utf-8"))
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    delay_seconds: float = 0.0
    chunked: bool = False
    truncate_bytes: int = 0

    @classmethod
    def from_file(cls, path: Path) -> Scenario:
        value: object = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("invalid_fixture_scenario")
        headers = value.get("headers", {})
        if not isinstance(headers, dict) or any(
            not isinstance(key, str) or not isinstance(member, str)
            for key, member in headers.items()
        ):
            raise ValueError("invalid_fixture_headers")
        body_text = value.get("body_text")
        body = (
            body_text.encode("utf-8")
            if isinstance(body_text, str)
            else json.dumps(value.get("payload", example_board())).encode("utf-8")
        )
        return cls(
            body=body,
            status=int(value.get("status", 200)),
            headers=cast(dict[str, str], headers),
            delay_seconds=float(value.get("delay_seconds", 0)),
            chunked=bool(value.get("chunked", False)),
            truncate_bytes=int(value.get("truncate_bytes", 0)),
        )


@dataclass(frozen=True)
class CapturedRequest:
    method: str
    path: str
    headers: dict[str, str]


class _FixtureServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, address: tuple[str, int], fixture: FixtureUpstream) -> None:
        self.fixture = fixture
        super().__init__(address, _FixtureHandler)

    def handle_error(self, request: object, client_address: object) -> None:
        del request, client_address


class _FixtureHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def do_GET(self) -> None:
        fixture = cast(_FixtureServer, self.server).fixture
        fixture.requests.append(CapturedRequest("GET", self.path, dict(self.headers.items())))
        fixture.request_received.set()
        scenario = (
            Scenario.from_file(fixture.scenario_file)
            if fixture.scenario_file is not None
            else fixture.scenario
        )
        self.send_response(scenario.status)
        headers = {"Content-Type": "application/json", **scenario.headers}
        if scenario.chunked:
            headers["Transfer-Encoding"] = "chunked"
        else:
            headers.setdefault("Content-Length", str(len(scenario.body)))
        headers["Connection"] = "close"
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        if scenario.delay_seconds:
            deadline = time.monotonic() + scenario.delay_seconds
            self.connection.settimeout(0.05)
            try:
                while time.monotonic() < deadline and not fixture.stopping.is_set():
                    ready, _, _ = select.select([self.connection], [], [], 0.05)
                    if ready:
                        try:
                            data = self.connection.recv(1)
                            if not data:
                                fixture.client_disconnected.set()
                                self.close_connection = True
                                return
                        except TimeoutError:
                            continue
                        except OSError:
                            fixture.client_disconnected.set()
                            self.close_connection = True
                            return
            finally:
                self.connection.settimeout(None)
        body = (
            scenario.body[: -scenario.truncate_bytes] if scenario.truncate_bytes else scenario.body
        )
        try:
            if scenario.chunked:
                for start in range(0, len(body), 4096):
                    chunk = body[start : start + 4096]
                    self.wfile.write(f"{len(chunk):x}\r\n".encode("ascii") + chunk + b"\r\n")
                self.wfile.write(b"0\r\n\r\n")
            else:
                self.wfile.write(body)
        except (OSError, ssl.SSLError):
            pass
        self.close_connection = True


class FixtureUpstream:
    def __init__(
        self, *, host: str = "127.0.0.1", port: int = 0, scenario_file: Path | None = None
    ) -> None:
        ipaddress.ip_address(host)
        self.scenario = Scenario()
        self.scenario_file = scenario_file
        self.requests: list[CapturedRequest] = []
        self.server_names: list[str | None] = []
        self.request_received = threading.Event()
        self.client_disconnected = threading.Event()
        self.stopping = threading.Event()
        self._directory = tempfile.TemporaryDirectory(prefix="bonafides-test-tls-")
        root = Path(self._directory.name)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, UPSTREAM_HOST)])
        now = datetime.now(UTC)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(
                x509.SubjectAlternativeName([x509.DNSName(UPSTREAM_HOST)]), critical=False
            )
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .sign(key, hashes.SHA256())
        )
        cert_path = root / "test-ca.pem"
        key_path = root / "test-key.pem"
        cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        key_path.chmod(0o600)
        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.load_cert_chain(cert_path, key_path)
        server_context.set_servername_callback(
            lambda stream, name, context: self.server_names.append(name)
        )
        self.context = ssl.create_default_context(cafile=str(cert_path))
        self.server = _FixtureServer((host, port), self)
        self.server.socket = server_context.wrap_socket(self.server.socket, server_side=True)
        self.address = (host, int(self.server.server_address[1]))
        self._thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self._thread.start()

    def client(self) -> GreenhouseClient:
        return GreenhouseClient(
            resolver=lambda host, port: ("8.8.8.8",),
            ssl_context=self.context,
            address_map=lambda address, port: self.address,
        )

    def close(self) -> None:
        self.stopping.set()
        self.server.shutdown()
        self.server.server_close()
        self._thread.join(timeout=2)
        self._directory.cleanup()

    def __enter__(self) -> FixtureUpstream:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Explicit local TEST TLS upstream and fixed-route gateway"
    )
    parser.add_argument("--scenario-file", type=Path)
    parser.add_argument("--fixture-bind", default="127.0.0.1")
    parser.add_argument("--fixture-port", type=int, default=0)
    parser.add_argument("--gateway-bind", default="0.0.0.0")
    parser.add_argument("--gateway-port", type=int, default=8766)
    args = parser.parse_args()
    with (
        FixtureUpstream(
            host=args.fixture_bind, port=args.fixture_port, scenario_file=args.scenario_file
        ) as fixture,
        SourceGatewayServer((args.gateway_bind, args.gateway_port), fixture.client()) as server,
    ):
        print("LOCAL TEST TLS/GATEWAY READY", flush=True)
        with contextlib.suppress(KeyboardInterrupt):
            server.serve_forever(poll_interval=0.1)


if __name__ == "__main__":
    main()
