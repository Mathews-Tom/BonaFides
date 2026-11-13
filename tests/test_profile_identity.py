# SPDX-License-Identifier: MIT OR Apache-2.0
"""Identity bounds and complete, static cropped PNG validation."""

import zlib
from datetime import date, timedelta
from io import BytesIO
from uuid import uuid4

import pytest
from PIL import Image
from pydantic import ValidationError

from bonafides.profile_identity import PortraitPin, ReferenceContact, age_on, validate_portrait
from bonafides.profiles import ProfileSnapshot


def png(*, size: tuple[int, int] = (480, 640), animated: bool = False) -> bytes:
    stream = BytesIO()
    image = Image.new("RGB", size, "navy")
    if animated:
        image.save(stream, format="PNG", save_all=True, append_images=[Image.new("RGB", size)])
    else:
        image.save(stream, format="PNG")
    return stream.getvalue()


def test_identity_defaults_leave_historical_snapshot_serialization_unchanged() -> None:
    snapshot = ProfileSnapshot()
    identity = {
        "birth_date",
        "include_age",
        "portrait",
        "include_portrait",
        "references",
        "include_references",
    }
    assert not identity.intersection(snapshot.model_dump(mode="json"))
    assert ProfileSnapshot(birth_date="1800-01-01").birth_date == "1800-01-01"
    assert age_on("1800-01-01", date(2026, 1, 1)) is None
    for value in ("2023-02-29", "2000-1-01", (date.today() + timedelta(days=1)).isoformat()):
        with pytest.raises(ValidationError):
            ProfileSnapshot(birth_date=value)
    with pytest.raises(ValidationError):
        ProfileSnapshot(references=[ReferenceContact()] * 21)
    with pytest.raises(ValidationError):
        ReferenceContact(name="Name\nInjected")
    with pytest.raises(ValidationError):
        ReferenceContact(phone="1" * 61)
    pin = PortraitPin(blob_id=uuid4(), sha256="a" * 64, byte_length=1)
    assert PortraitPin.model_validate(pin.model_dump(mode="json")) == pin
    with pytest.raises(ValidationError):
        PortraitPin(blob_id=uuid4(), sha256="A" * 64, byte_length=1)


def test_age_is_computed_for_composition_day_including_leap_birthdays() -> None:
    assert age_on(None, date(2026, 1, 1)) is None
    assert age_on("2000-02-29", date(2025, 2, 28)) == 24
    assert age_on("2000-02-29", date(2025, 3, 1)) == 25
    assert age_on("2000-02-29", date(2028, 2, 29)) == 28
    assert age_on("1876-11-13", date(2026, 11, 12)) == 149
    assert age_on("1876-11-13", date(2026, 11, 13)) is None
    assert age_on("2026-11-14", date(2026, 11, 13)) is None


def test_portrait_accepts_only_complete_static_cropped_png() -> None:
    image = png()
    oversized = bytearray(image)
    oversized[16:24] = (100000).to_bytes(4, "big") * 2
    oversized[29:33] = zlib.crc32(oversized[12:29]).to_bytes(4, "big")
    validate_portrait(image)
    for invalid in (
        b"<svg/>",
        image[:-1],
        image + b"extra",
        png(size=(640, 480)),
        bytes(oversized),
        png(animated=True),
        image + b"x" * 525000,
    ):
        with pytest.raises(ValueError, match="invalid_portrait"):
            validate_portrait(invalid)


@pytest.mark.parametrize("field", ["include_age", "include_portrait", "include_references"])
@pytest.mark.parametrize("value", ["true", "false", 1, 0])
def test_printing_preferences_reject_coerced_disclosure_flags(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        ProfileSnapshot.model_validate({field: value})
