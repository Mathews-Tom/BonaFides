# SPDX-License-Identifier: MIT OR Apache-2.0
"""Versioned AES-256-GCM envelopes with canonical context binding.

Retained-envelope nonce uniqueness must additionally be enforced by the database
unique key on (key_id, nonce). This helper never accepts caller-selected nonces.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from urllib.parse import urlsplit
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


@dataclass(frozen=True)
class Keyring:
    active_key_id: str
    keys: Mapping[str, bytes] = field(repr=False)

    @classmethod
    def parse(cls, content: str) -> Keyring:
        try:
            value: object = json.loads(content)
            if not isinstance(value, dict) or set(value) != {"active_key_id", "keys"}:
                raise ValueError
            active = value["active_key_id"]
            raw = value["keys"]
            if not isinstance(active, str) or not isinstance(raw, dict) or not raw:
                raise ValueError
            keys: dict[str, bytes] = {}
            for key_id, encoded in raw.items():
                if (
                    not isinstance(key_id, str)
                    or not 1 <= len(key_id) <= 64
                    or not key_id.isascii()
                    or not all(c.isalnum() or c in "-_" for c in key_id)
                    or not isinstance(encoded, str)
                ):
                    raise ValueError
                decoded = base64.b64decode(encoded, validate=True)
                if len(decoded) != 32:
                    raise ValueError
                keys[key_id] = decoded
            if active not in keys:
                raise ValueError
            return cls(active, MappingProxyType(keys))
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError("Invalid keyring configuration") from exc


def canonical_provider_origin(origin: str) -> str:
    """Require an exact HTTPS origin, never normalize a different destination."""
    try:
        parsed = urlsplit(origin)
        host, port = parsed.hostname, parsed.port
        if host is None:
            raise ValueError
        if ":" in host:
            canonical_host = f"[{ipaddress.IPv6Address(host).compressed}]"
        else:
            if len(host) > 253 or any(
                re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) is None
                for label in host.split(".")
            ):
                raise ValueError
            canonical_host = host
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or (port is not None and (not 1 <= port <= 65535 or port == 443))
            or origin != f"https://{canonical_host}{f':{port}' if port is not None else ''}"
        ):
            raise ValueError
    except ValueError as error:
        raise ValueError("Invalid canonical provider origin") from error
    return origin


@dataclass(frozen=True)
class EnvelopeContext:
    namespace_id: UUID
    owner_id: UUID
    purpose: str
    record_id: UUID
    version: int = 1
    provider_origin: str | None = None

    def aad(self) -> bytes:
        if (
            not self.purpose
            or len(self.purpose) > 128
            or self.version not in (1, 2)
            or (self.version == 1 and self.provider_origin is not None)
            or (self.version == 2 and self.provider_origin is None)
        ):
            raise ValueError("Invalid envelope context")
        value: dict[str, str | int] = {
            "algorithm": "AES-256-GCM",
            "namespace_id": str(self.namespace_id),
            "owner_id": str(self.owner_id),
            "purpose": self.purpose,
            "record_id": str(self.record_id),
            "version": self.version,
        }
        if self.provider_origin is not None:
            value["provider_origin"] = canonical_provider_origin(self.provider_origin)
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True)
class Envelope:
    key_id: str
    nonce: bytes
    ciphertext: bytes
    version: int = 1


def encrypt(keyring: Keyring, context: EnvelopeContext, plaintext: bytes) -> Envelope:
    # 96-bit CSPRNG nonce; no timestamp/counter state that a restore can roll back.
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(keyring.keys[keyring.active_key_id]).encrypt(
        nonce, plaintext, context.aad()
    )
    return Envelope(keyring.active_key_id, nonce, ciphertext, context.version)


def decrypt(keyring: Keyring, context: EnvelopeContext, envelope: Envelope) -> bytes:
    if (
        envelope.version != context.version
        or envelope.version not in (1, 2)
        or len(envelope.nonce) != 12
        or len(envelope.ciphertext) < 16
    ):
        raise ValueError("Invalid encrypted envelope")
    # Unknown key and failed authentication have no plaintext/old-key fallback.
    return AESGCM(keyring.keys[envelope.key_id]).decrypt(
        envelope.nonce, envelope.ciphertext, context.aad()
    )


def decrypt_connected_credential(
    keyring: Keyring,
    context: EnvelopeContext,
    envelope: Envelope,
    *,
    configured_provider_origin: str,
    account_status: str,
) -> bytes:
    """Live reads cannot reuse disconnected, reauthorize or legacy credentials.

    Generic decrypt is reserved for explicit envelope migration/native recovery.
    This boundary grants no account connection or provider execution authority.
    """
    if (
        account_status != "connected"
        or context.version != 2
        or context.provider_origin != canonical_provider_origin(configured_provider_origin)
    ):
        raise ValueError("Credential connection and provider origin required")
    return decrypt(keyring, context, envelope)
