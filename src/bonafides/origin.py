# SPDX-License-Identifier: MIT OR Apache-2.0
"""Origin syntax shared by runtime configuration and the offline installer."""

from __future__ import annotations

from ipaddress import ip_address
from urllib.parse import urlsplit


def validate_origin(origin: str, *, canonical_https: bool = False) -> None:
    """Require an exact secure/loopback origin, optionally canonical install HTTPS."""
    try:
        parsed = urlsplit(origin)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Invalid origin") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or any(c.isspace() for c in origin)
        or any(ord(c) < 33 or ord(c) == 127 for c in origin)
        or "\\" in origin
        or "%" in origin
        or not parsed.hostname.isascii()
        or parsed.hostname.endswith(".")
        or not all(c.isalnum() or c in ".-:" for c in parsed.hostname)
        or (parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"})
        or (port is not None and not 1 <= port <= 65535)
        or origin != f"{parsed.scheme}://{parsed.netloc}"
    ):
        raise ValueError("Invalid origin: exact HTTPS origin or loopback HTTP required")
    host = parsed.hostname or ""
    if ":" not in host and (
        len(host) > 253
        or any(
            not label or len(label) > 63 or not label[0].isalnum() or not label[-1].isalnum()
            for label in host.split(".")
        )
    ):
        raise ValueError("Invalid origin: invalid hostname")
    last_label = host.rsplit(".", 1)[-1]
    if (
        ":" in host
        or last_label.isdigit()
        or (
            last_label.lower().startswith("0x")
            and all(c in "0123456789abcdefABCDEF" for c in last_label[2:])
        )
    ):
        try:
            numeric_host = str(ip_address(host))
        except ValueError as exc:
            raise ValueError("Invalid origin: noncanonical numeric hostname") from exc
        if host != numeric_host:
            raise ValueError("Invalid origin: noncanonical numeric hostname")
    if canonical_https:
        authority = f"[{host}]" if ":" in host else host
        if port is not None:
            authority += f":{port}"
        if origin != f"https://{authority}" or port == 443:
            raise ValueError("Invalid origin: canonical HTTPS required without default :443")
