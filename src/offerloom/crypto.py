# SPDX-License-Identifier: MIT OR Apache-2.0
"""Versioned AES-256-GCM envelopes with canonical context binding.

Retained-envelope nonce uniqueness must additionally be enforced by the database
unique key on (key_id, nonce). This helper never accepts caller-selected nonces.
"""

from __future__ import annotations

import base64
import json
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
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


@dataclass(frozen=True)
class EnvelopeContext:
    namespace_id: UUID
    owner_id: UUID
    purpose: str
    record_id: UUID
    version: int = 1

    def aad(self) -> bytes:
        if not self.purpose or len(self.purpose) > 128 or self.version != 1:
            raise ValueError("Invalid envelope context")
        return json.dumps(
            {
                "algorithm": "AES-256-GCM",
                "namespace_id": str(self.namespace_id),
                "owner_id": str(self.owner_id),
                "purpose": self.purpose,
                "record_id": str(self.record_id),
                "version": self.version,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")


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
    return Envelope(keyring.active_key_id, nonce, ciphertext)


def decrypt(keyring: Keyring, context: EnvelopeContext, envelope: Envelope) -> bytes:
    if (
        envelope.version != context.version
        or envelope.version != 1
        or len(envelope.nonce) != 12
        or len(envelope.ciphertext) < 16
    ):
        raise ValueError("Invalid encrypted envelope")
    # Unknown key and failed authentication have no plaintext/old-key fallback.
    return AESGCM(keyring.keys[envelope.key_id]).decrypt(
        envelope.nonce, envelope.ciphertext, context.aad()
    )
