# SPDX-License-Identifier: MIT OR Apache-2.0
"""Bounded owner identity values and verified, private cropped portrait storage."""

from __future__ import annotations

from datetime import date
from io import BytesIO
from uuid import UUID

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import Connection, text

from bonafides.config import Settings
from bonafides.storage import BlobStore

MAX_PORTRAIT_BYTES = 525000
MAX_PORTRAIT_BASE64 = 700000


class PortraitPin(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    blob_id: UUID
    sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    byte_length: int = Field(ge=1, le=MAX_PORTRAIT_BYTES)

    @field_validator("blob_id", mode="before")
    @classmethod
    def canonical_uuid(cls, value: object) -> object:
        if isinstance(value, str):
            parsed = UUID(value)
            if str(parsed) != value:
                raise ValueError("noncanonical_portrait_id")
            return parsed
        return value


class ReferenceContact(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    name: str = Field(default="", max_length=200)
    relationship: str = Field(default="", max_length=200)
    organization: str = Field(default="", max_length=200)
    email: str = Field(default="", max_length=320)
    phone: str = Field(default="", max_length=60)

    @field_validator("name", "relationship", "organization", "email", "phone")
    @classmethod
    def safe_literal(cls, value: str) -> str:
        if any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value):
            raise ValueError("reference_controls_rejected")
        value.encode("utf-8")
        return value


def age_on(birth_date: str | None, on_date: date) -> int | None:
    if birth_date is None:
        return None
    born = date.fromisoformat(birth_date)
    age = on_date.year - born.year - ((on_date.month, on_date.day) < (born.month, born.day))
    return age if born <= on_date and 0 <= age < 150 else None


def validate_portrait(data: bytes) -> None:
    """Accept one complete, static 480×640 PNG, never an original source image."""
    if not 0 < len(data) <= MAX_PORTRAIT_BYTES or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("invalid_portrait")
    offset = 8
    while offset + 12 <= len(data):
        size = int.from_bytes(data[offset : offset + 4], "big")
        kind = data[offset + 4 : offset + 8]
        offset += size + 12
        if offset > len(data) or kind == b"acTL":
            raise ValueError("invalid_portrait")
        if kind == b"IEND":
            if size != 0 or offset != len(data) or data[-12:] != b"\0\0\0\0IEND\xaeB`\x82":
                raise ValueError("invalid_portrait")
            break
    else:
        raise ValueError("invalid_portrait")
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format != "PNG" or image.size != (480, 640):
                raise ValueError("invalid_portrait")
            image.verify()
        with Image.open(BytesIO(data)) as image:
            image.load()
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as error:
        raise ValueError("invalid_portrait") from error


def portrait_storage_key(conn: Connection, owner_id: UUID, pin: PortraitPin) -> str:
    """Validate owner-scoped metadata without granting a snapshot writer filesystem access."""
    row = (
        conn.execute(
            text("""SELECT sha256,byte_length,storage_key,media_type,state FROM bonafides.blobs
        WHERE owner_id=:owner AND id=:id FOR SHARE"""),
            {"owner": owner_id, "id": pin.blob_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None or (
        row["state"] != "ready"
        or row["media_type"] != "image/png"
        or row["sha256"] != pin.sha256
        or row["byte_length"] != pin.byte_length
        or row["storage_key"] != f"{pin.sha256[:2]}/{pin.sha256}"
    ):
        raise ValueError("invalid_portrait_pin")
    return str(row["storage_key"])


def portrait_bytes(conn: Connection, settings: Settings, owner_id: UUID, pin: PortraitPin) -> bytes:
    key = portrait_storage_key(conn, owner_id, pin)
    try:
        data = BlobStore(settings.blob_root).read(owner_id, key, pin.sha256, pin.byte_length)
        validate_portrait(data)
    except (OSError, ValueError) as error:
        raise ValueError("invalid_portrait_pin") from error
    return data
