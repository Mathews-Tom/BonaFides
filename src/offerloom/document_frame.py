# SPDX-License-Identifier: MIT OR Apache-2.0
"""Trusted printable identity is frozen separately from inference data."""

from __future__ import annotations

import hashlib
from importlib.resources import files
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

TEMPLATES: Final = {
    "offerloom-evidence-v1": ("Evidence", "evidence-v1.html"),
    "offerloom-compact-v1": ("Compact", "compact-v1.html"),
}
TemplateVersion = Literal["offerloom-base-v1", "offerloom-evidence-v1", "offerloom-compact-v1"]


def template_digest(version: str) -> str:
    if version not in TEMPLATES:
        raise ValueError("unsupported_document_template")
    return hashlib.sha256(
        files("offerloom").joinpath("templates", TEMPLATES[version][1]).read_bytes()
    ).hexdigest()


class ProfileContact(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    email: str = Field(default="", max_length=254)
    phone: str = Field(default="", max_length=120)
    location: str = Field(default="", max_length=300)
    postal_address: str = Field(default="", max_length=1000)

    @field_validator("email", "phone", "location", "postal_address")
    @classmethod
    def safe_literal(cls, value: str) -> str:
        if any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value):
            raise ValueError("contact_controls_rejected")
        value.encode("utf-8")
        return value

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        import re

        if value and re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value) is None:
            raise ValueError("invalid_contact_email")
        return value


class PrintableFrame(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    display_name: str = Field(max_length=120)
    contact: ProfileContact | None = None
    correspondence_date: Annotated[str, Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")] | None = (
        None
    )
    organization: str | None = Field(default=None, max_length=300)
    role: str | None = Field(default=None, max_length=300)
    recipient: Literal["Hiring team"] | None = None

    @field_validator("display_name", "organization", "role")
    @classmethod
    def safe_literal(cls, value: str | None) -> str | None:
        if value is not None:
            ProfileContact.safe_literal(value)
        return value
