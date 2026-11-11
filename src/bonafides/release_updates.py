# SPDX-License-Identifier: MIT OR Apache-2.0
"""Credential-free release metadata over one fixed, bounded relay route."""

from __future__ import annotations

import json
import re
import ssl
import threading
from dataclasses import dataclass

from bonafides.greenhouse import (
    AddressMap,
    Resolver,
    SourceFailure,
    _bounded_resolve,
    _Budget,
    _check_status,
    _connect,
    _internal_gateway_address,
    _resolve,
    _Wire,
    decode_json,
    public_address,
)

TIMEOUT = 10.0
MAX_BYTES = 64 * 1024
RELAY_PATH = "/v1/releases/latest"
RELEASE_ROOT = "https://github.com/Mathews-Tom/BonaFides/releases/tag/"
_SEMVER = re.compile(r"v?(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\Z", re.ASCII)


def version_tuple(value: str) -> tuple[int, int, int]:
    match = _SEMVER.fullmatch(value)
    if match is None:
        raise SourceFailure("source_invalid_response")
    return int(match[1]), int(match[2]), int(match[3])


@dataclass(frozen=True)
class Release:
    version: str
    url: str


def parse_release(data: bytes, budget: _Budget, *, relay: bool = False) -> Release | None:
    value = decode_json(data, budget, maximum=MAX_BYTES)
    if value is None and relay:
        return None
    if not isinstance(value, dict):
        raise SourceFailure("source_invalid_response")
    tag, url = value.get("tag_name"), value.get("html_url")
    if (
        not isinstance(tag, str)
        or not isinstance(url, str)
        or value.get("draft") is not False
        or value.get("prerelease") is not False
        or url != RELEASE_ROOT + tag
    ):
        raise SourceFailure("source_invalid_response")
    version_tuple(tag)
    return Release(tag.removeprefix("v"), url)


def serialize_release(release: Release | None) -> bytes:
    return json.dumps(
        None
        if release is None
        else {
            "tag_name": release.url.removeprefix(RELEASE_ROOT),
            "html_url": release.url,
            "draft": False,
            "prerelease": False,
        },
        separators=(",", ":"),
    ).encode("ascii")


class ReleaseClient:
    """Direct TLS is used only by source-egress; constructor seams are test-only."""

    def __init__(
        self,
        *,
        resolver: Resolver | None = None,
        ssl_context: ssl.SSLContext | None = None,
        address_map: AddressMap | None = None,
        relay: bool = False,
    ) -> None:
        self._resolver = resolver or _resolve
        self._context = ssl_context or ssl.create_default_context()
        if not self._context.check_hostname or self._context.verify_mode != ssl.CERT_REQUIRED:
            raise ValueError("source_tls_verification_required")
        self._address_map, self._relay = address_map, relay

    def read(
        self,
        interrupted: threading.Event | None = None,
        *,
        budget: _Budget | None = None,
    ) -> Release | None:
        budget = budget or _Budget(interrupted, seconds=TIMEOUT)
        host, port = ("source-egress", 8766) if self._relay else ("api.github.com", 443)
        path = RELAY_PATH if self._relay else "/repos/Mathews-Tom/BonaFides/releases/latest"
        try:
            addresses = _bounded_resolve(self._resolver, host, port, budget)
            allowed = _internal_gateway_address if self._relay else public_address
            if not all(allowed(address) for address in addresses):
                raise SourceFailure("source_address_denied")
            address = addresses[0]
            if self._address_map is not None:
                address, port = self._address_map(address, port)
            with _connect(
                address,
                port,
                budget,
                None if self._relay else self._context,
                server_hostname=None if self._relay else host,
            ) as stream:
                wire = _Wire(stream, budget, MAX_BYTES)
                authority = "source-egress:8766" if self._relay else host
                wire.send(
                    (
                        f"GET {path} HTTP/1.1\r\nHost: {authority}\r\nUser-Agent: BonaFides\r\n"
                        "Accept: application/json\r\nAccept-Encoding: identity\r\nConnection: close\r\n\r\n"
                    ).encode("ascii")
                )
                status, headers = wire.headers()
                if status == 404 and not self._relay:
                    return None
                _check_status(status, headers)
                return parse_release(wire.body(headers), budget, relay=self._relay)
        except SourceFailure:
            raise
        except ssl.SSLError:
            raise SourceFailure("source_tls_failed") from None
        except (OSError, ValueError, OverflowError):
            raise SourceFailure("source_network_failed") from None
        except Exception:
            raise SourceFailure("source_invalid_response") from None
