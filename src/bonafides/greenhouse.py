# SPDX-License-Identifier: MIT OR Apache-2.0
"""Fixed-route, credential-free Greenhouse published-job reads.

Reference URLs are inert data, never input to this transport. Explicit constructor
seams exist for real local TLS tests only; no environment or source setting can
change the upstream host, certificate identity, method, port, or route.
"""

from __future__ import annotations

import errno
import hashlib
import html
import ipaddress
import json
import math
import re
import select
import socket
import ssl
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import cast
from urllib.parse import urlsplit, urlunsplit

UPSTREAM_HOST = "boards-api.greenhouse.io"
UPSTREAM_PORT = 443
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_GATEWAY_BYTES = 20 * 1024 * 1024
MAX_JOBS = 2000
MAX_DEPTH = 32
MAX_DESCRIPTION = 200_000
MAX_TITLE = 300
MAX_LOCATION = 1000
TOTAL_TIMEOUT = 15.0
CONNECT_TIMEOUT = 3.0
_HEADER_LIMIT = 32 * 1024
# Admitted decimal Retry-After values stay below Python's default 4300-digit ceiling.
_HEADER_LINE_LIMIT = 4096
_POLL = 0.05
_TOKEN = re.compile(r"[A-Za-z0-9_-]{1,120}\Z", re.ASCII)
_DNS_SLOTS = threading.BoundedSemaphore(4)
Resolver = Callable[[str, int], Sequence[str]]
AddressMap = Callable[[str, int], tuple[str, int]]
SOURCE_ERROR_CODES = frozenset(
    {
        "source_invalid_token",
        "source_dns_failed",
        "source_address_denied",
        "source_peer_mismatch",
        "source_tls_failed",
        "source_timeout",
        "source_cancelled",
        "source_network_failed",
        "source_redirect_denied",
        "source_forbidden",
        "source_not_found",
        "source_rate_limited",
        "source_http_error",
        "source_response_too_large",
        "source_invalid_response",
        "source_incomplete_response",
        "source_unsupported_encoding",
        "source_text_limit",
        "source_gateway_busy",
        "source_gateway_unavailable",
    }
)


@dataclass(frozen=True)
class PublishedJob:
    external_id: str
    title: str
    location_text: str
    description_text: str
    canonical_url: str | None
    source_updated_at: str | None
    source_content_sha256: str
    snapshot: dict[str, object]


@dataclass(frozen=True)
class BoardRead:
    jobs: tuple[PublishedJob, ...]
    excluded_prospect_count: int
    fetch_elapsed_ms: int


class SourceFailure(RuntimeError):
    def __init__(self, code: str, retry_after_seconds: int | None = None) -> None:
        self.code = code if code in SOURCE_ERROR_CODES else "source_invalid_response"
        self.retry_after_seconds = (
            retry_after_seconds
            if type(retry_after_seconds) is int and retry_after_seconds >= 0
            else None
        )
        super().__init__(self.code)


def validate_board_token(value: str) -> str:
    if not isinstance(value, str) or _TOKEN.fullmatch(value) is None:
        raise SourceFailure("source_invalid_token")
    return value


def public_address(value: str) -> bool:
    """Deny special and transition ranges, including mixed DNS/mapped addresses."""
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if not address.is_global or "%" in value:
        return False
    if isinstance(address, ipaddress.IPv4Address):
        # These are not uniformly classified across supported Python patch versions.
        return not any(
            address in ipaddress.ip_network(network)
            for network in ("192.0.0.0/24", "192.88.99.0/24", "198.18.0.0/15")
        )
    return address in ipaddress.ip_network("2000::/3") and not any(
        address in ipaddress.ip_network(network)
        for network in ("2001::/23", "2002::/16", "3fff::/20")
    )


def safe_reference_url(value: str | None) -> str | None:
    """Canonical inert HTTPS reference; deliberately does not resolve remote DNS."""
    if not value or not isinstance(value, str) or len(value) > 2048:
        return None
    if any(ord(char) <= 32 or ord(char) == 127 for char in value) or "\\" in value:
        return None
    if re.search(r"%(?:0[0-9a-f]|1[0-9a-f]|20|5c|7f)", value, re.IGNORECASE):
        return None
    try:
        parts = urlsplit(value)
        host = parts.hostname
        if (
            parts.scheme != "https"
            or not host
            or parts.username is not None
            or parts.password is not None
            or parts.port not in (None, 443)
            or "%" in parts.netloc
            or not host.isascii()
        ):
            return None
        host = host.lower()
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            if (
                len(host) > 253
                or "." not in host
                or host.endswith(
                    (
                        ".",
                        ".local",
                        ".localhost",
                        ".internal",
                        ".home",
                        ".lan",
                        ".test",
                        ".invalid",
                        ".example",
                        ".onion",
                    )
                )
                or re.fullmatch(r"(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+))*", host)
                or re.fullmatch(r"(?:0x[0-9a-f]+|[0-9]+)", host.rsplit(".", 1)[-1])
                or any(
                    re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) is None
                    for label in host.split(".")
                )
            ):
                return None
            authority = host
        else:
            if not public_address(str(literal)):
                return None
            authority = f"[{literal}]" if literal.version == 6 else str(literal)
        return urlunsplit(("https", authority, parts.path or "/", parts.query, parts.fragment))
    except ValueError:
        return None


class _Budget:
    def __init__(self, interrupted: threading.Event | None, seconds: float = TOTAL_TIMEOUT) -> None:
        self.interrupted = interrupted
        self.started = time.monotonic()
        self.deadline = self.started + seconds

    def check(self) -> None:
        if self.interrupted is not None and self.interrupted.is_set():
            raise SourceFailure("source_cancelled")
        if time.monotonic() >= self.deadline:
            raise SourceFailure("source_timeout")

    def wait(self, stream: socket.socket, *, write: bool, deadline: float | None = None) -> None:
        while True:
            self.check()
            remaining = min(self.deadline, deadline or self.deadline) - time.monotonic()
            if remaining <= 0:
                raise SourceFailure("source_timeout")
            readable, writable, _ = select.select(
                [] if write else [stream], [stream] if write else [], [], min(_POLL, remaining)
            )
            if readable or writable:
                return


def _resolve(host: str, port: int) -> Sequence[str]:
    addresses: dict[str, None] = {}
    for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
        address = item[4][0]
        if not isinstance(address, str):
            raise SourceFailure("source_dns_failed")
        addresses[address] = None
    return tuple(addresses)


def _bounded_resolve(resolver: Resolver, host: str, port: int, budget: _Budget) -> tuple[str, ...]:
    # libc resolution cannot be cancelled. At most four daemon lookups can remain
    # blocked; callers still stop promptly and no unbounded thread pool is created.
    budget.check()
    if not _DNS_SLOTS.acquire(blocking=False):
        raise SourceFailure("source_dns_failed")
    done = threading.Event()
    addresses: list[str] = []
    failed = threading.Event()

    def run() -> None:
        try:
            result = resolver(host, port)
            if not 0 < len(result) <= 32 or any(not isinstance(item, str) for item in result):
                failed.set()
            else:
                addresses.extend(result)
        except Exception:
            failed.set()
        finally:
            _DNS_SLOTS.release()
            done.set()

    try:
        threading.Thread(target=run, daemon=True, name="source-dns").start()
    except RuntimeError:
        _DNS_SLOTS.release()
        raise SourceFailure("source_dns_failed") from None
    deadline = time.monotonic() + CONNECT_TIMEOUT
    while not done.wait(_POLL):
        budget.check()
        if time.monotonic() >= deadline:
            raise SourceFailure("source_timeout")
    budget.check()
    if failed.is_set() or not addresses:
        raise SourceFailure("source_dns_failed")
    return tuple(dict.fromkeys(addresses))


def _connect(
    address: str,
    port: int,
    budget: _Budget,
    context: ssl.SSLContext | None,
    *,
    server_hostname: str | None = None,
) -> socket.socket:
    budget.check()
    deadline = min(budget.deadline, time.monotonic() + CONNECT_TIMEOUT)
    parsed = ipaddress.ip_address(address)
    stream = socket.socket(
        socket.AF_INET6 if parsed.version == 6 else socket.AF_INET, socket.SOCK_STREAM
    )
    stream.setblocking(False)
    try:
        result = stream.connect_ex((str(parsed), port))
        if result not in (0, errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY):
            raise SourceFailure("source_network_failed")
        while result != 0:
            budget.wait(stream, write=True, deadline=deadline)
            result = stream.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
            if result:
                raise SourceFailure("source_network_failed")
            if stream.getpeername():
                break
        peer = stream.getpeername()
        if ipaddress.ip_address(peer[0]) != parsed or peer[1] != port:
            raise SourceFailure("source_peer_mismatch")
        if context is not None:
            secure = context.wrap_socket(
                stream, server_hostname=server_hostname, do_handshake_on_connect=False
            )
            stream = secure
            while True:
                try:
                    secure.do_handshake()
                    break
                except ssl.SSLWantReadError:
                    budget.wait(stream, write=False, deadline=deadline)
                except ssl.SSLWantWriteError:
                    budget.wait(stream, write=True, deadline=deadline)
            peer = secure.getpeername()
            if ipaddress.ip_address(peer[0]) != parsed or peer[1] != port:
                raise SourceFailure("source_peer_mismatch")
        budget.check()
        return stream
    except BaseException:
        stream.close()
        raise


class _Wire:
    def __init__(
        self, stream: socket.socket, budget: _Budget, maximum: int = MAX_RESPONSE_BYTES
    ) -> None:
        self.stream = stream
        self.budget = budget
        self.maximum = maximum
        self.buffer = bytearray()
        self.received = 0

    def send(self, data: bytes) -> None:
        remaining = memoryview(data)
        while remaining:
            self.budget.check()
            try:
                sent = self.stream.send(remaining)
                if not sent:
                    raise SourceFailure("source_network_failed")
                remaining = remaining[sent:]
            except (ssl.SSLWantWriteError, BlockingIOError):
                self.budget.wait(self.stream, write=True)
            except ssl.SSLWantReadError:
                self.budget.wait(self.stream, write=False)

    def _receive(self, *, allow_eof: bool = False) -> bool:
        while True:
            self.budget.check()
            try:
                data = self.stream.recv(min(64 * 1024, self.maximum + 1 - self.received))
                if not data:
                    if allow_eof:
                        return False
                    raise SourceFailure("source_invalid_response")
                self.received += len(data)
                if self.received > self.maximum:
                    raise SourceFailure("source_response_too_large")
                self.buffer.extend(data)
                return True
            except (ssl.SSLWantReadError, BlockingIOError):
                self.budget.wait(self.stream, write=False)
            except ssl.SSLWantWriteError:
                self.budget.wait(self.stream, write=True)

    def line(self, limit: int) -> bytes:
        while True:
            position = self.buffer.find(b"\r\n")
            if position >= 0:
                if position > limit:
                    raise SourceFailure("source_invalid_response")
                result = bytes(self.buffer[:position])
                del self.buffer[: position + 2]
                return result
            if len(self.buffer) > limit:
                raise SourceFailure("source_invalid_response")
            self._receive()

    def take(self, count: int) -> bytes:
        if count > self.maximum:
            raise SourceFailure("source_response_too_large")
        while len(self.buffer) < count:
            self._receive()
        data = bytes(self.buffer[:count])
        del self.buffer[:count]
        return data

    def headers(self) -> tuple[int, dict[str, str]]:
        first = self.line(1024)
        match = re.fullmatch(rb"HTTP/1\.[01] ([0-9]{3})(?: [\x20-\x7e]*)?", first)
        if match is None:
            raise SourceFailure("source_invalid_response")
        headers: dict[str, str] = {}
        size = len(first) + 2
        for _ in range(64):
            line = self.line(_HEADER_LINE_LIMIT)
            size += len(line) + 2
            if size > _HEADER_LIMIT:
                raise SourceFailure("source_invalid_response")
            if not line:
                return int(match[1]), headers
            key, separator, value = line.partition(b":")
            if (
                not separator
                or re.fullmatch(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key) is None
                or any(char < 32 or char > 126 for char in value)
            ):
                raise SourceFailure("source_invalid_response")
            name = key.decode("ascii").lower()
            if name in headers:
                raise SourceFailure("source_invalid_response")
            headers[name] = value.decode("ascii").strip()
        raise SourceFailure("source_invalid_response")

    def body(self, headers: dict[str, str]) -> bytes:
        if headers.get("content-encoding", "identity").lower() != "identity":
            raise SourceFailure("source_unsupported_encoding")
        if headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise SourceFailure("source_invalid_response")
        transfer = headers.get("transfer-encoding")
        length = headers.get("content-length")
        if transfer is not None:
            if transfer.lower() != "chunked" or length is not None:
                raise SourceFailure("source_invalid_response")
            body = bytearray()
            while True:
                line = self.line(32)
                if re.fullmatch(rb"[0-9a-fA-F]{1,8}", line) is None:
                    raise SourceFailure("source_invalid_response")
                count = int(line, 16)
                if not count:
                    if self.line(2) or self.buffer:
                        raise SourceFailure("source_invalid_response")
                    return bytes(body)
                if len(body) + count > self.maximum:
                    raise SourceFailure("source_response_too_large")
                body.extend(self.take(count))
                if self.take(2) != b"\r\n":
                    raise SourceFailure("source_invalid_response")
        if length is None:
            body = self.buffer
            self.buffer = bytearray()
            while self._receive(allow_eof=True):
                body.extend(self.buffer)
                self.buffer.clear()
            return bytes(body)
        if re.fullmatch(r"[0-9]{1,10}", length) is None:
            raise SourceFailure("source_invalid_response")
        fixed_body = self.take(int(length))
        if self.buffer:
            raise SourceFailure("source_invalid_response")
        return fixed_body


def _retry_after(value: str | None) -> int:
    if value is None:
        return 60
    if re.fullmatch(r"[0-9]+", value):
        return int(value)
    try:
        instant = parsedate_to_datetime(value)
        if instant.tzinfo is None:
            return 60
        return max(0, math.ceil((instant - datetime.now(UTC)).total_seconds()))
    except (ValueError, TypeError, OverflowError):
        return 60


def _check_status(status: int, headers: dict[str, str]) -> None:
    if status == 200:
        return
    code = {
        403: "source_forbidden",
        404: "source_not_found",
        429: "source_rate_limited",
    }.get(status, "source_redirect_denied" if 300 <= status < 400 else "source_http_error")
    raise SourceFailure(code, _retry_after(headers.get("retry-after")) if status == 429 else None)


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SourceFailure("source_invalid_response")
        result[key] = value
    return result


def _invalid_constant(value: str) -> object:
    del value
    raise SourceFailure("source_invalid_response")


def decode_json(
    data: bytes,
    budget: _Budget | None = None,
    *,
    maximum: int = MAX_RESPONSE_BYTES,
    maximum_depth: int = MAX_DEPTH,
) -> object:
    if len(data) > maximum:
        raise SourceFailure("source_response_too_large")
    try:
        text = data.decode("utf-8", errors="strict")
        depth = 0
        quoted = False
        escaped = False
        for index, char in enumerate(text):
            if budget is not None and index % 4096 == 0:
                budget.check()
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char in "[{":
                depth += 1
                if depth > maximum_depth:
                    raise SourceFailure("source_invalid_response")
            elif char in "]}":
                depth -= 1
        value: object = json.loads(
            text, object_pairs_hook=_no_duplicate_keys, parse_constant=_invalid_constant
        )
        _check_structure(value, budget, maximum_depth=maximum_depth)
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise SourceFailure("source_invalid_response") from None


def _check_structure(
    value: object, budget: _Budget | None, depth: int = 0, *, maximum_depth: int = MAX_DEPTH
) -> None:
    if budget is not None:
        budget.check()
    if depth > maximum_depth:
        raise SourceFailure("source_invalid_response")
    if value is None or type(value) in (bool, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise SourceFailure("source_invalid_response")
        return
    if isinstance(value, str):
        _text(value, MAX_DESCRIPTION, empty=True)
        return
    if isinstance(value, list):
        if len(value) > MAX_JOBS:
            raise SourceFailure("source_response_too_large")
        for member in value:
            _check_structure(member, budget, depth + 1, maximum_depth=maximum_depth)
        return
    if isinstance(value, dict):
        if len(value) > 256:
            raise SourceFailure("source_invalid_response")
        for key, member in value.items():
            if not isinstance(key, str):
                raise SourceFailure("source_invalid_response")
            _text(key, 256)
            _check_structure(member, budget, depth + 1, maximum_depth=maximum_depth)
        return
    raise SourceFailure("source_invalid_response")


def _text(value: object, maximum: int, *, empty: bool = False) -> str:
    if not isinstance(value, str):
        raise SourceFailure("source_invalid_response")
    if len(value) > maximum:
        raise SourceFailure("source_text_limit")
    if (not empty and not value.strip()) or any(
        ord(char) < 32 and char not in "\n\r\t" or 0xD800 <= ord(char) <= 0xDFFF or ord(char) == 127
        for char in value
    ):
        raise SourceFailure("source_invalid_response")
    return value


class _InertHTML(HTMLParser):
    _ACTIVE = frozenset(
        {
            "script",
            "style",
            "iframe",
            "object",
            "embed",
            "svg",
            "math",
            "canvas",
            "template",
            "noscript",
            "head",
        }
    )
    _VOID = frozenset(
        {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }
    )
    _BLOCK = frozenset(
        {
            "p",
            "div",
            "br",
            "li",
            "ul",
            "ol",
            "section",
            "article",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "table",
            "tr",
            "hr",
        }
    )

    def __init__(self, budget: _Budget) -> None:
        super().__init__(convert_charrefs=True)
        self.budget = budget
        self.stack: list[str] = []
        self.parts: list[str] = []
        self.size = 0

    def _append(self, text: str) -> None:
        self.budget.check()
        self.size += len(text)
        if self.size > MAX_DESCRIPTION:
            raise SourceFailure("source_text_limit")
        self.parts.append(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        self.budget.check()
        if tag not in self._VOID:
            self.stack.append(tag)
            if len(self.stack) > 64:
                raise SourceFailure("source_invalid_response")
        if not any(item in self._ACTIVE for item in self.stack) and tag in self._BLOCK:
            self._append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in self._VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        self.budget.check()
        if tag in self.stack:
            del self.stack[len(self.stack) - 1 - self.stack[::-1].index(tag) :]
        if not any(item in self._ACTIVE for item in self.stack) and tag in self._BLOCK:
            self._append("\n")

    def handle_data(self, data: str) -> None:
        if not any(item in self._ACTIVE for item in self.stack):
            self._append(data)


def _description(value: object, budget: _Budget) -> str:
    original = _text(value, MAX_DESCRIPTION, empty=True)
    parser = _InertHTML(budget)
    parser.feed(html.unescape(original))
    parser.close()
    text = re.sub(r"[ \t\r\f\v]+", " ", "".join(parser.parts))
    text = re.sub(r" *\n *", "\n", text)
    return _text(re.sub(r"\n{3,}", "\n\n", text).strip(), MAX_DESCRIPTION, empty=True)


def _snapshot_hash(snapshot: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    ).hexdigest()


def _published(record: object, budget: _Budget) -> PublishedJob | None:
    if not isinstance(record, dict):
        raise SourceFailure("source_invalid_response")
    snapshot = cast(dict[str, object], record)
    identity = snapshot.get("id")
    if type(identity) is not int or not 0 < identity < 2**63:
        raise SourceFailure("source_invalid_response")
    if "internal_job_id" not in snapshot:
        raise SourceFailure("source_invalid_response")
    internal = snapshot["internal_job_id"]
    if internal is None:
        return None
    if type(internal) is not int or not 0 < internal < 2**63:
        raise SourceFailure("source_invalid_response")
    location = snapshot.get("location")
    if not isinstance(location, dict):
        raise SourceFailure("source_invalid_response")
    updated = snapshot.get("updated_at")
    updated_text: str | None = None
    if updated is not None:
        updated_text = _text(updated, 64)
        try:
            if datetime.fromisoformat(updated_text).tzinfo is None:
                raise ValueError
        except ValueError:
            raise SourceFailure("source_invalid_response") from None
    reference = snapshot.get("absolute_url")
    if reference is not None and not isinstance(reference, str):
        raise SourceFailure("source_invalid_response")
    return PublishedJob(
        external_id=str(identity),
        title=_text(snapshot.get("title"), MAX_TITLE),
        location_text=_text(location.get("name"), MAX_LOCATION, empty=True),
        description_text=_description(snapshot.get("content"), budget),
        canonical_url=safe_reference_url(reference),
        source_updated_at=updated_text,
        source_content_sha256=_snapshot_hash(snapshot),
        snapshot=snapshot,
    )


def parse_board(data: bytes, budget: _Budget) -> BoardRead:
    value = decode_json(data, budget)
    if not isinstance(value, dict) or set(value) != {"jobs", "meta"}:
        raise SourceFailure("source_incomplete_response")
    jobs = value["jobs"]
    meta = value["meta"]
    if not isinstance(jobs, list) or not isinstance(meta, dict) or set(meta) != {"total"}:
        raise SourceFailure("source_incomplete_response")
    if type(meta["total"]) is not int or meta["total"] != len(jobs):
        raise SourceFailure("source_incomplete_response")
    if len(jobs) > MAX_JOBS:
        raise SourceFailure("source_response_too_large")
    result: list[PublishedJob] = []
    seen: set[int] = set()
    excluded = 0
    for record in jobs:
        budget.check()
        job = _published(record, budget)
        identity = cast(dict[str, object], record)["id"]
        identity = cast(int, identity)
        if identity in seen:
            raise SourceFailure("source_invalid_response")
        seen.add(identity)
        if job is None:
            excluded += 1
        else:
            result.append(job)
    budget.check()
    return BoardRead(tuple(result), excluded, math.ceil((time.monotonic() - budget.started) * 1000))


class GreenhouseClient:
    def __init__(
        self,
        *,
        resolver: Resolver | None = None,
        ssl_context: ssl.SSLContext | None = None,
        address_map: AddressMap | None = None,
    ) -> None:
        self._resolver = resolver or _resolve
        self._context = ssl_context or ssl.create_default_context()
        if not self._context.check_hostname or self._context.verify_mode != ssl.CERT_REQUIRED:
            raise ValueError("source_tls_verification_required")
        self._address_map = address_map

    def read(self, board_token: str, interrupted: threading.Event | None = None) -> BoardRead:
        token = validate_board_token(board_token)
        budget = _Budget(interrupted)
        try:
            addresses = _bounded_resolve(self._resolver, UPSTREAM_HOST, UPSTREAM_PORT, budget)
            if not all(public_address(address) for address in addresses):
                raise SourceFailure("source_address_denied")
            # One vetted address, no second hostname lookup and no automatic retries.
            address, port = addresses[0], UPSTREAM_PORT
            if self._address_map is not None:
                address, port = self._address_map(address, port)
            with _connect(
                address, port, budget, self._context, server_hostname=UPSTREAM_HOST
            ) as stream:
                wire = _Wire(stream, budget)
                wire.send(
                    (
                        f"GET /v1/boards/{token}/jobs?content=true HTTP/1.1\r\nHost: {UPSTREAM_HOST}\r\nAccept: application/json\r\nAccept-Encoding: identity\r\nConnection: close\r\n\r\n"
                    ).encode("ascii")
                )
                status, headers = wire.headers()
                _check_status(status, headers)
                return parse_board(wire.body(headers), budget)
        except SourceFailure:
            raise
        except ssl.SSLError:
            raise SourceFailure("source_tls_failed") from None
        except (OSError, ValueError, OverflowError):
            raise SourceFailure("source_network_failed") from None
        except Exception:
            raise SourceFailure("source_invalid_response") from None


def serialize_board(board: BoardRead) -> bytes:
    payload = {
        "schema_version": 1,
        "jobs": [
            {
                "external_id": job.external_id,
                "title": job.title,
                "location_text": job.location_text,
                "description_text": job.description_text,
                "canonical_url": job.canonical_url,
                "source_updated_at": job.source_updated_at,
                "source_content_sha256": job.source_content_sha256,
                "snapshot": job.snapshot,
            }
            for job in board.jobs
        ],
        "excluded_prospect_count": board.excluded_prospect_count,
        "fetch_elapsed_ms": board.fetch_elapsed_ms,
    }
    data = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    if len(data) > MAX_GATEWAY_BYTES:
        raise SourceFailure("source_response_too_large")
    return data


def deserialize_board(data: bytes, budget: _Budget) -> BoardRead:
    value = decode_json(data, budget, maximum=MAX_GATEWAY_BYTES, maximum_depth=MAX_DEPTH + 1)
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "jobs", "excluded_prospect_count", "fetch_elapsed_ms"}
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
    ):
        raise SourceFailure("source_invalid_response")
    raw_jobs = value["jobs"]
    excluded = value["excluded_prospect_count"]
    elapsed = value["fetch_elapsed_ms"]
    if (
        not isinstance(raw_jobs, list)
        or type(excluded) is not int
        or not 0 <= excluded <= MAX_JOBS
        or type(elapsed) is not int
        or not 0 <= elapsed <= 15_000
        or len(raw_jobs) + excluded > MAX_JOBS
    ):
        raise SourceFailure("source_invalid_response")
    jobs: list[PublishedJob] = []
    seen: set[str] = set()
    expected = {
        "external_id",
        "title",
        "location_text",
        "description_text",
        "canonical_url",
        "source_updated_at",
        "source_content_sha256",
        "snapshot",
    }
    for raw in raw_jobs:
        budget.check()
        if not isinstance(raw, dict) or set(raw) != expected:
            raise SourceFailure("source_invalid_response")
        _check_structure(raw["snapshot"], budget, depth=2)
        normalized = _published(raw["snapshot"], budget)
        if normalized is None:
            raise SourceFailure("source_invalid_response")
        actual = {
            "external_id": normalized.external_id,
            "title": normalized.title,
            "location_text": normalized.location_text,
            "description_text": normalized.description_text,
            "canonical_url": normalized.canonical_url,
            "source_updated_at": normalized.source_updated_at,
            "source_content_sha256": normalized.source_content_sha256,
        }
        if (
            any(raw[key] != member for key, member in actual.items())
            or normalized.external_id in seen
        ):
            raise SourceFailure("source_invalid_response")
        seen.add(normalized.external_id)
        jobs.append(normalized)
    return BoardRead(tuple(jobs), excluded, elapsed)


class SourceGatewayClient:
    def __init__(self, host: str = "source-egress", port: int = 8766) -> None:
        if host != "source-egress" or port != 8766:
            raise ValueError("source_gateway_address_denied")
        self.host = host
        self.port = port

    def read(self, board_token: str, interrupted: threading.Event | None = None) -> BoardRead:
        token = validate_board_token(board_token)
        budget = _Budget(interrupted)
        try:
            addresses = _bounded_resolve(_resolve, self.host, self.port, budget)
            # This is the dedicated internal source network, not upstream DNS.
            # Never accept public addresses or a route to the loopback/metadata host.
            if any(not _internal_gateway_address(address) for address in addresses):
                raise SourceFailure("source_address_denied")
            with _connect(addresses[0], self.port, budget, None) as stream:
                wire = _Wire(stream, budget, MAX_GATEWAY_BYTES)
                wire.send(
                    (
                        f"GET /v1/boards/{token}/jobs?content=true HTTP/1.1\r\nHost: source-egress:8766\r\nAccept: application/json\r\nAccept-Encoding: identity\r\nConnection: close\r\n\r\n"
                    ).encode("ascii")
                )
                status, headers = wire.headers()
                body = wire.body(headers)
                if status != 200:
                    value = decode_json(body, budget)
                    if (
                        not isinstance(value, dict)
                        or set(value) != {"code", "retry_after_seconds"}
                        or not isinstance(value["code"], str)
                        or value["code"] not in SOURCE_ERROR_CODES
                    ):
                        raise SourceFailure("source_invalid_response")
                    code = value["code"]
                    delay = value["retry_after_seconds"]
                    if delay is not None and (type(delay) is not int or delay < 0):
                        raise SourceFailure("source_invalid_response")
                    raise SourceFailure(code, delay)
                return deserialize_board(body, budget)
        except SourceFailure:
            raise
        except (OSError, ValueError, OverflowError):
            raise SourceFailure("source_gateway_unavailable") from None
        except Exception:
            raise SourceFailure("source_invalid_response") from None


def _internal_gateway_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv4Address):
        return any(
            address in ipaddress.ip_network(network)
            for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
        )
    return address in ipaddress.ip_network("fc00::/7") and address.ipv4_mapped is None
