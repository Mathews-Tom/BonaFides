# SPDX-License-Identifier: MIT OR Apache-2.0
"""Plain-data generation projections shared by production and local qualification."""

import unicodedata
from collections.abc import Mapping

from bonafides import local_model


def _projection(
    posting: Mapping[str, object],
    source: object,
    requirements: list[dict[str, object]],
    claims: list[dict[str, object]],
    evidence: list[dict[str, object]],
    tone: str,
    language: str,
) -> dict[str, object]:
    return {
        "posting": {
            "title": posting["title"],
            "organization": posting["company"],
            "source_text": source,
        },
        "requirements": requirements,
        "claims": claims,
        "evidence": evidence,
        "tone": tone,
        "language": language,
    }


def generation_projection(
    posting: Mapping[str, object],
    requirements: list[dict[str, object]],
    claims: list[dict[str, object]],
    evidence: list[dict[str, object]],
    tone: str,
    language: str,
) -> dict[str, object]:
    """Project a current posting and already-minimized professional evidence."""
    source = str(posting["description_text"])
    source = unicodedata.normalize("NFC", source.replace("\r\n", "\n").replace("\r", "\n"))
    return _projection(posting, source, requirements, claims, evidence, tone, language)


def regeneration_projection(
    posting: Mapping[str, object],
    requirements: list[dict[str, object]],
    claims: list[dict[str, object]],
    evidence: list[dict[str, object]],
    tone: str,
    language: str,
    target: dict[str, object],
) -> dict[str, object]:
    """Preserve the historical posting and validate the complete target-only wire."""
    projection = _projection(
        posting, posting["description_text"], requirements, claims, evidence, tone, language
    )
    projection["target"] = target
    return local_model.section_projection(projection)
