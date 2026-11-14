# SPDX-License-Identifier: MIT OR Apache-2.0
"""Plain-data generation projections shared by production and local qualification."""

from __future__ import annotations

from collections.abc import Mapping

from bonafides import local_model, suitability


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
