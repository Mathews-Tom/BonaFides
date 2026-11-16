# SPDX-License-Identifier: MIT OR Apache-2.0
"""Plain-data generation projections shared by production and local qualification."""

from __future__ import annotations

from collections.abc import Mapping

from bonafides import documents, letter_frame, local_model, suitability


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
    return _projection(
        posting,
        suitability.normalize(str(posting["description_text"])),
        requirements,
        claims,
        evidence,
        tone,
        language,
    )


def regeneration_projection(
    posting: Mapping[str, object],
    requirements: list[dict[str, object]],
    claims: list[dict[str, object]],
    evidence: list[dict[str, object]],
    tone: str,
    language: str,
    target: dict[str, object] | None,
) -> dict[str, object]:
    """Preserve the historical posting and validate the complete target-only wire."""
    projection = _projection(
        posting, posting["description_text"], requirements, claims, evidence, tone, language
    )
    if target is None:
        return projection
    projection["target"] = target
    return local_model.section_projection(projection)


def refinement_projection(
    base_contexts: list[dict[str, object]],
    request: str,
    turns: list[dict[str, object]],
    targets: list[dict[str, object]],
) -> dict[str, object]:
    """Share identical professional sources across the selected documents."""
    sources = [
        {key: value for key, value in context.items() if key != "target"}
        for context in base_contexts
    ]
    if not sources:
        raise local_model.ModelFailure("model_context_invalid")
    shared = {
        key: value
        for key, value in sources[0].items()
        if key != "document_id" and all(source.get(key) == value for source in sources)
    }
    return local_model.refinement_context(
        {
            "shared": shared,
            "documents": [
                {key: value for key, value in source.items() if key not in shared}
                for source in sources
            ],
            "request": request,
            "turns": turns[-9:],
            "targets": targets,
        }
    )


def application_email_projection(
    posting: Mapping[str, object],
    requirements: list[dict[str, object]],
    claims: list[dict[str, object]],
    evidence: list[dict[str, object]],
    tone: str,
    language: str,
    letter_body: documents.DocumentBody,
    *,
    frame: dict[str, object],
) -> dict[str, object]:
    """Use the shared, already-minimized professional sources and letter body only.

    Evidence must come from model_context's current-classification projection, exactly
    as for generation. Never pass checker facts (which retain withheld evidence text).
    The local frame is used only to strip legacy correspondence, never transmitted.
    """
    projection = generation_projection(posting, requirements, claims, evidence, tone, language)
    body = letter_frame.split(letter_body, frame)
    projection["letter_body"] = "\n\n".join(
        block.text
        for section in body.sections
        if section.key not in letter_frame.PROTECTED_BODY_KEYS
        for block in section.blocks
        if block.block_key not in letter_frame.PROTECTED_BODY_KEYS
    )
    return local_model.email_context(projection)
