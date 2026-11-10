# SPDX-License-Identifier: MIT OR Apache-2.0
"""Bounded, fixed-host commit reads; only the egress role reads a bearer file."""

from __future__ import annotations

import json
import re
import ssl
import threading
from dataclasses import dataclass
from pathlib import Path

from bonafides.config import read_secret
from bonafides.evidence_capture_config import MAX_COMMITS, MAX_TEXT, validate_github_repository
from bonafides.greenhouse import (
    SOURCE_ERROR_CODES,
    AddressMap,
    Resolver,
    SourceFailure,
    _bounded_resolve,
    _Budget,
    _check_status,
    _connect,
    _internal_gateway_address,
    _resolve,
    _retry_after,
    _text,
    _Wire,
    decode_json,
    public_address,
)

UPSTREAM_HOST = "api.github.com"
UPSTREAM_PORT = 443
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_RETRY_AFTER = 86400
_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z", re.ASCII)
_BEARER = re.compile(r"[A-Za-z0-9._~+/-]{1,4096}=*\Z", re.ASCII)


@dataclass(frozen=True)
class GitHubCommit:
    sha: str
    message: str


def _repository(value: str) -> str:
    try:
        return validate_github_repository(value)
    except (TypeError, ValueError):
        raise SourceFailure("source_invalid_token") from None


def _commits(value: object, budget: _Budget, *, upstream: bool) -> list[GitHubCommit]:
    if not isinstance(value, list):
        raise SourceFailure("source_invalid_response")
    if len(value) > MAX_COMMITS:
        raise SourceFailure("source_response_too_large")
    result: list[GitHubCommit] = []
    seen: set[str] = set()
    for record in value:
        budget.check()
        if not isinstance(record, dict):
            raise SourceFailure("source_invalid_response")
        if not upstream and set(record) != {"sha", "message"}:
            raise SourceFailure("source_invalid_response")
        sha = record.get("sha")
        detail = record.get("commit") if upstream else record
        if (
            not isinstance(sha, str)
            or _SHA.fullmatch(sha) is None
            or sha in seen
            or not isinstance(detail, dict)
        ):
            raise SourceFailure("source_invalid_response")
        message = _text(detail.get("message"), MAX_TEXT)
        seen.add(sha)
        result.append(GitHubCommit(sha, message))
    budget.check()
    return result


def serialize_commits(repository: str, commits: list[GitHubCommit]) -> bytes:
    payload = json.dumps(
        {
            "schema_version": 1,
            "repository": _repository(repository),
            "commits": [{"sha": commit.sha, "message": commit.message} for commit in commits],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(payload) > MAX_RESPONSE_BYTES:
        raise SourceFailure("source_response_too_large")
    return payload


def deserialize_commits(data: bytes, repository: str, budget: _Budget) -> list[GitHubCommit]:
    value = decode_json(data, budget, maximum=MAX_RESPONSE_BYTES)
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "repository", "commits"}
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["repository"] != _repository(repository)
    ):
        raise SourceFailure("source_invalid_response")
    return _commits(value["commits"], budget, upstream=False)


def _token(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError
        token = read_secret(path, "github_capture_token")
        if len(token) > 4096 or _BEARER.fullmatch(token) is None:
            raise ValueError
        return token
    except (OSError, ValueError, TypeError):
        raise SourceFailure("source_forbidden") from None


def _github_status(status: int, headers: dict[str, str]) -> None:
    if status == 401:
        raise SourceFailure("source_forbidden")
    if (
        status == 429
        or status == 403
        and (headers.get("x-ratelimit-remaining") == "0" or "retry-after" in headers)
    ):
        raise SourceFailure(
            "source_rate_limited", min(MAX_RETRY_AFTER, _retry_after(headers.get("retry-after")))
        )
    _check_status(status, headers)


class GitHubClient:
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

    def read(
        self,
        repository: str,
        token_file: Path | None,
        interrupted: threading.Event | None = None,
    ) -> list[GitHubCommit]:
        repository = _repository(repository)
        budget = _Budget(interrupted)
        try:
            budget.check()
            token = _token(token_file)
            addresses = _bounded_resolve(self._resolver, UPSTREAM_HOST, UPSTREAM_PORT, budget)
            if not all(public_address(address) for address in addresses):
                raise SourceFailure("source_address_denied")
            address, port = addresses[0], UPSTREAM_PORT
            if self._address_map is not None:
                address, port = self._address_map(address, port)
            with _connect(
                address, port, budget, self._context, server_hostname=UPSTREAM_HOST
            ) as stream:
                wire = _Wire(stream, budget, MAX_RESPONSE_BYTES)
                authorization = f"Authorization: Bearer {token}\r\n" if token is not None else ""
                wire.send(
                    (
                        f"GET /repos/{repository}/commits?per_page={MAX_COMMITS} HTTP/1.1\r\n"
                        f"Host: {UPSTREAM_HOST}\r\nUser-Agent: BonaFides\r\n"
                        "Accept: application/json\r\nX-GitHub-Api-Version: 2022-11-28\r\n"
                        f"Accept-Encoding: identity\r\n{authorization}Connection: close\r\n\r\n"
                    ).encode("ascii")
                )
                status, headers = wire.headers()
                _github_status(status, headers)
                value = decode_json(wire.body(headers), budget, maximum=MAX_RESPONSE_BYTES)
                return _commits(value, budget, upstream=True)
        except SourceFailure:
            raise
        except ssl.SSLError:
            raise SourceFailure("source_tls_failed") from None
        except (OSError, ValueError, OverflowError):
            raise SourceFailure("source_network_failed") from None
        except Exception:
            raise SourceFailure("source_invalid_response") from None


class GitHubGatewayClient:
    def __init__(
        self,
        host: str = "source-egress",
        port: int = 8766,
        *,
        resolver: Resolver | None = None,
        address_map: AddressMap | None = None,
    ) -> None:
        if host != "source-egress" or port != 8766:
            raise ValueError("source_gateway_address_denied")
        self._resolver = resolver or _resolve
        self._address_map = address_map

    def read(
        self, repository: str, interrupted: threading.Event | None = None
    ) -> list[GitHubCommit]:
        repository = _repository(repository)
        budget = _Budget(interrupted)
        try:
            addresses = _bounded_resolve(self._resolver, "source-egress", 8766, budget)
            if any(not _internal_gateway_address(address) for address in addresses):
                raise SourceFailure("source_address_denied")
            address, port = addresses[0], 8766
            if self._address_map is not None:
                address, port = self._address_map(address, port)
            with _connect(address, port, budget, None) as stream:
                wire = _Wire(stream, budget, MAX_RESPONSE_BYTES)
                wire.send(
                    (
                        f"GET /v1/github/{repository}/commits?per_page={MAX_COMMITS} HTTP/1.1\r\n"
                        "Host: source-egress:8766\r\nAccept: application/json\r\n"
                        "Accept-Encoding: identity\r\nConnection: close\r\n\r\n"
                    ).encode("ascii")
                )
                status, headers = wire.headers()
                body = wire.body(headers)
                if status != 200:
                    value = decode_json(body, budget, maximum=MAX_RESPONSE_BYTES)
                    if (
                        not isinstance(value, dict)
                        or set(value) != {"code", "retry_after_seconds"}
                        or not isinstance(value["code"], str)
                        or value["code"] not in SOURCE_ERROR_CODES
                        or (
                            value["retry_after_seconds"] is not None
                            and (
                                type(value["retry_after_seconds"]) is not int
                                or not 0 <= value["retry_after_seconds"] <= MAX_RETRY_AFTER
                            )
                        )
                    ):
                        raise SourceFailure("source_invalid_response")
                    raise SourceFailure(value["code"], value["retry_after_seconds"])
                return deserialize_commits(body, repository, budget)
        except SourceFailure:
            raise
        except Exception:
            raise SourceFailure("source_gateway_unavailable") from None
