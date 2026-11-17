# SPDX-License-Identifier: MIT OR Apache-2.0
"""Editable correspondence, separate from trusted profile identity and model input."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, ValidationInfo, field_validator

from bonafides.document_frame import PrintableFrame, ProfileContact, template_digest
from bonafides.languages import LABELS, RenderLanguage
from bonafides.problems import Problem

if TYPE_CHECKING:
    from bonafides.documents import DocumentBody

Tone = Literal["professional", "concise", "friendly"]
PROTECTED_BODY_KEYS = frozenset({"sender", "date", "correspondence_date", "signature"})


def frame_language(frame: dict[str, object]) -> RenderLanguage:
    """Use the printable-frame contract even for untrusted imported provenance."""
    try:
        return PrintableFrame.model_validate(
            {"display_name": "", "language": frame.get("language", "en")}
        ).language
    except ValidationError as exc:
        raise Problem(422, "unsupported_document_language") from exc


def closing_for(tone: str, language: RenderLanguage = "en") -> str:
    return LABELS[frame_language({"language": language})][tone]


class LetterParts(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    correspondence_date: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
    recipient: str = Field(min_length=1, max_length=1000)
    greeting: str = Field(min_length=1, max_length=300)
    closing: str = Field(min_length=1, max_length=300)

    @field_validator("correspondence_date")
    @classmethod
    def calendar_date(cls, value: str) -> str:
        date.fromisoformat(value)
        return value

    @field_validator("recipient", "greeting", "closing")
    @classmethod
    def literal_text(cls, value: str, info: ValidationInfo) -> str:
        ProfileContact.safe_literal(
            value.replace("\n", "") if info.field_name == "recipient" else value
        )
        if not value.strip():
            raise ValueError("empty_letter_part")
        return value


def role(text: str, frame: dict[str, object]) -> Literal["greeting", "closing"] | None:
    text = text.strip()
    if text in {
        *(
            labels[key]
            for labels in LABELS.values()
            for key in ("professional", "concise", "friendly")
        ),
        "Sincerely,",
        "Kind regards,",
        frame.get("closing"),
    }:
        return "closing"
    if (
        text.startswith(("Dear ", "Hello ", "Hi "))
        or text == frame.get("greeting")
        or text in {labels["greeting"] for labels in LABELS.values()}
    ):
        return "greeting"
    return None


def split(body: DocumentBody, frame: dict[str, object]) -> DocumentBody:
    sections = []
    for section in body.sections:
        blocks = [
            block
            for block in section.blocks
            if block.claim_kind != "greeting" or role(block.text, frame) is None
        ]
        if blocks:
            sections.append(section.model_copy(update={"blocks": blocks}))
    if not sections:
        raise Problem(422, "letter_body_empty")
    return body.model_copy(update={"sections": sections})


def parts(frame: dict[str, object], tone: str, body: DocumentBody | None = None) -> LetterParts:
    language = frame_language(frame)
    derived = {}
    if body is not None:
        for section in body.sections:
            for block in section.blocks:
                key = role(block.text, frame) if block.claim_kind == "greeting" else None
                if key and (key == "closing" or key not in derived):
                    derived[key] = block.text
    return LetterParts.model_validate(
        {
            "correspondence_date": frame.get("correspondence_date") or date.today().isoformat(),
            "recipient": frame.get("recipient") or LABELS[language]["recipient"],
            "greeting": frame.get("greeting")
            or derived.get("greeting")
            or LABELS[language]["greeting"],
            "closing": frame.get("closing")
            or derived.get("closing")
            or closing_for(tone, language),
        }
    )


def is_custom(provenance: dict[str, object], body: DocumentBody | None = None) -> bool:
    language = frame_language(cast(dict[str, object], provenance["frame"]))
    if "letter_closing_edited" in provenance:
        return provenance["letter_closing_edited"] is True
    frame = cast(dict[str, object], provenance["frame"])
    return bool(
        frame.get("closing")
        and frame["closing"]
        != closing_for(
            str(provenance.get("tone") or "professional"),
            language,
        )
    )


def pin(
    source: dict[str, object], value: LetterParts, *, owner_edit: bool = False
) -> dict[str, object]:
    from bonafides.documents import parse_body

    provenance = dict(cast(dict[str, object], source["provenance_snapshot"]))
    original = cast(dict[str, object], provenance["frame"])
    before = parts(
        original,
        str(provenance.get("tone") or "professional"),
        parse_body(source["body"]) if "body" in source else None,
    )
    provenance["letter_closing_edited"] = is_custom(provenance) or (
        owner_edit and value.closing != before.closing
    )
    frame = {**original, **value.model_dump(mode="json")}
    provenance["frame"] = PrintableFrame.model_validate(frame).model_dump(mode="json")
    template = str(source["template_version"]).rsplit("-v", 1)[0] + "-v4"
    provenance.update(template_version=template, template_sha256=template_digest(template))
    return {**source, "template_version": template, "provenance_snapshot": provenance}


def state(body: DocumentBody, source: dict[str, object]) -> dict[str, object]:
    provenance = cast(dict[str, object], source["provenance_snapshot"])
    return {
        "body": body.model_dump(mode="json"),
        "frame": provenance["frame"],
        "template_version": source["template_version"],
        "template_sha256": provenance["template_sha256"],
        "closing_edited": provenance.get("letter_closing_edited"),
    }
