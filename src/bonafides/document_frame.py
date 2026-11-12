# SPDX-License-Identifier: MIT OR Apache-2.0
"""Trusted printable identity is frozen separately from inference data."""

from __future__ import annotations

import hashlib
from importlib.resources import files
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from bonafides.profile_breadth import citation_text, normalize_citation_url

TEMPLATES: Final = {
    "bonafides-evidence-v1": ("Evidence", "evidence-v1.html"),
    "bonafides-compact-v1": ("Compact", "compact-v1.html"),
    "bonafides-evidence-letter-v1": ("Evidence", "evidence-letter-v1.html"),
    "bonafides-compact-letter-v1": ("Compact", "compact-letter-v1.html"),
    "bonafides-evidence-v2": ("Evidence — citations", "profile-v2.html"),
    "bonafides-compact-v2": ("Compact — citations", "profile-v2.html"),
    "bonafides-evidence-letter-v2": ("Evidence — citations", "profile-v2.html"),
    "bonafides-compact-letter-v2": ("Compact — citations", "profile-v2.html"),
}
TEMPLATE_PAPER: Final[dict[str, Literal["A4", "Letter"]]] = {
    "bonafides-evidence-v1": "A4",
    "bonafides-compact-v1": "A4",
    "bonafides-evidence-letter-v1": "Letter",
    "bonafides-compact-letter-v1": "Letter",
    "bonafides-evidence-v2": "A4",
    "bonafides-compact-v2": "A4",
    "bonafides-evidence-letter-v2": "Letter",
    "bonafides-compact-letter-v2": "Letter",
}
TemplateVersion = Literal[
    "bonafides-base-v1",
    "bonafides-evidence-v1",
    "bonafides-compact-v1",
    "bonafides-evidence-letter-v1",
    "bonafides-compact-letter-v1",
    "bonafides-evidence-v2",
    "bonafides-compact-v2",
    "bonafides-evidence-letter-v2",
    "bonafides-compact-letter-v2",
]


def template_digest(version: str) -> str:
    if version not in TEMPLATES:
        raise ValueError("unsupported_document_template")
    return hashlib.sha256(
        files("bonafides").joinpath("templates", TEMPLATES[version][1]).read_bytes()
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


class PublicationPresentation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    text: str = Field(min_length=1, max_length=3000)
    markup: str = Field(max_length=3000)
    url: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def literal_presentation(self) -> PublicationPresentation:
        if citation_text(self.markup) != self.text:
            raise ValueError("publication_presentation_mismatch")
        if normalize_citation_url(self.url) != self.url:
            raise ValueError("publication_url_not_canonical")
        return self


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
    publication_styles: dict[str, PublicationPresentation] = Field(
        default_factory=dict, max_length=100, exclude_if=lambda value: not value
    )

    @field_validator("display_name", "organization", "role")
    @classmethod
    def safe_literal(cls, value: str | None) -> str | None:
        if value is not None:
            ProfileContact.safe_literal(value)
        return value
