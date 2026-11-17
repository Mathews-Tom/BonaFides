# SPDX-License-Identifier: MIT OR Apache-2.0
"""Localized deterministic displays preserve source wording and evidence boundaries."""

from __future__ import annotations

from typing import cast
from uuid import uuid4

import pytest
from test_document_defaults import _defaults
from test_document_generation import _base
from test_identity import identity as identity
from test_sources import SourceContext
from test_sources import catalog as catalog
from test_suitability import _profile

from bonafides.claims import (
    METRIC_UNITS,
    OWNERSHIP_VERBS,
    RENDER_LANGUAGES,
    StructuredClaim,
    render_claim,
    vocabulary,
)
from bonafides.document_generation import compose_base
from bonafides.documents import DocumentBlock, DocumentBody, DocumentSection
from bonafides.languages import LABELS, RenderLanguage, display_field, format_date
from bonafides.letter_frame import closing_for, parts
from bonafides.statement_check import CATEGORIES, check_statements, connectives, printable_text

LANGUAGES: tuple[RenderLanguage, ...] = ("de", "fr", "es")


def _structure(**values: object) -> StructuredClaim:
    return StructuredClaim.model_validate(
        {"schema_version": 1, "verb": "led", "subject": "the billing migration", **values}
    )


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_ownership_and_unit_has_deterministic_rendering(language: RenderLanguage) -> None:
    assert set(RENDER_LANGUAGES) == {"en", "de", "fr", "es"}
    choices = cast(list[dict[str, str]], vocabulary(language)["verbs"])
    assert [item["key"] for item in choices] == list(OWNERSHIP_VERBS)
    for verb, choice in zip(OWNERSHIP_VERBS, choices, strict=True):
        text = render_claim(_structure(verb=verb), language)
        assert text == f"{choice['label']} the billing migration"
    for unit in METRIC_UNITS:
        text = render_claim(_structure(metric={"value": "2.5", "unit": unit}), language)
        assert "2,5" in text and "the billing migration" in text


@pytest.mark.parametrize(
    ("language", "expected", "day", "ongoing", "proficiency"),
    [
        (
            "de",
            "Leitete the billing migration (März 2026–April 2026)",
            "1. März 2026",
            "heute",
            "Fließend",
        ),
        (
            "fr",
            "A dirigé the billing migration (mars 2026–avril 2026)",
            "1er mars 2026",
            "à ce jour",
            "Courant",
        ),
        (
            "es",
            "Lideró the billing migration (marzo de 2026–abril de 2026)",
            "1 de marzo de 2026",
            "actualidad",
            "Fluido",
        ),
    ],
)
def test_dates_ongoing_and_proficiency(
    language: RenderLanguage, expected: str, day: str, ongoing: str, proficiency: str
) -> None:
    assert (
        render_claim(_structure(period={"start": "2026-03", "end": "2026-04"}), language)
        == expected
    )
    assert format_date("2026-03-01", language) == day
    assert format_date("2026-03-02", "fr") == "2 mars 2026"
    assert display_field("end_date", "Present", language) == ongoing
    assert display_field("proficiency", "fluent", language) == proficiency
    assert display_field("proficiency", "C1 (owner wording)", language) == "C1 (owner wording)"
    assert format_date("2026", language) == "2026"
    assert format_date("Owner wording", language) == "Owner wording"


@pytest.mark.parametrize("language", LANGUAGES)
def test_localized_checker_requires_original_evidence_and_exact_rendering(
    language: RenderLanguage,
) -> None:
    structure = _structure(metric={"value": "30", "unit": "percent", "change": "decrease"})
    claim, evidence = uuid4(), uuid4()
    text = render_claim(structure, language)
    fields = {"start_date": "2026-03", "end_date": "Present", "proficiency": "fluent"}
    fact: dict[str, object] = {
        "claim_revision_id": str(claim),
        "evidence_revision_id": str(evidence),
        "claim_text": render_claim(structure),
        "evidence_text": render_claim(structure),
        "localized_claim": structure.model_dump(mode="json"),
        "display_language": language,
        "structured_fields": fields,
        "printable_text": printable_text(text, fields, language),
        "category": "experience",
    }
    body = compose_base({"facts": [fact], "language": language})
    assert body.title == LABELS[language]["base"]
    assert body.sections[0].heading == LABELS[language]["experience"]
    assert check_statements(body, [fact], {"language": language}, "base") == []
    block = body.sections[0].blocks[0]
    for unsupported in (
        text.replace("30", "300"),
        text + " and led 20 teams",
        "Unrelated achievement",
    ):
        changed = body.model_copy(
            update={
                "sections": [
                    body.sections[0].model_copy(
                        update={"blocks": [block.model_copy(update={"text": unsupported})]}
                    )
                ]
            }
        )
        assert {
            issue["code"]
            for issue in check_statements(changed, [fact], {"language": language}, "base")
        } == {"statement_not_in_claim"}
    bad_evidence = {**fact, "evidence_text": "Unrelated source"}
    assert (
        check_statements(body, [bad_evidence], {"language": language}, "base")[0]["code"]
        == "statement_not_in_claim_evidence"
    )
    inflated_structure = _structure(verb="owned").model_dump(mode="json")
    assert (
        check_statements(
            body, [{**fact, "localized_claim": inflated_structure}], {"language": language}, "base"
        )[0]["code"]
        == "statement_not_in_claim"
    )


@pytest.mark.parametrize("language", LANGUAGES)
def test_all_headings_connectives_and_tone_closings_are_checked(language: RenderLanguage) -> None:
    frame: dict[str, object] = {"language": language, "role": "Engineer", "organization": "Example"}
    for tone in ("professional", "concise", "friendly"):
        assert closing_for(tone, language) == LABELS[language][tone]
        assert parts(frame, tone).closing == LABELS[language][tone]
    for heading in (*CATEGORIES, "Application", "Letter", "Interest", "Summary"):
        body = DocumentBody(
            title=LABELS[language]["cover_letter"],
            sections=[
                DocumentSection(
                    key="section-0",
                    heading=LABELS[language][heading],
                    blocks=[
                        DocumentBlock(
                            block_key="b-1",
                            text=LABELS[language]["greeting"],
                            claim_kind="greeting",
                            claim_revision_ids=[],
                            evidence_revision_ids=[],
                        )
                    ],
                )
            ],
        )
        assert check_statements(body, [], frame, "cover_letter") == []
        for text, kind in connectives(frame).items():
            altered = body.model_copy(
                update={
                    "sections": [
                        body.sections[0].model_copy(
                            update={
                                "blocks": [
                                    body.sections[0]
                                    .blocks[0]
                                    .model_copy(update={"text": text, "claim_kind": kind})
                                ]
                            }
                        )
                    ]
                }
            )
            assert check_statements(altered, [], frame, "cover_letter") == []


@pytest.mark.postgres
@pytest.mark.parametrize("language", LANGUAGES)
def test_base_display_language_does_not_translate_owner_text_or_rewrite_saved_revisions(
    catalog: SourceContext,
    language: RenderLanguage,
) -> None:
    profile = _profile(catalog)
    original = _base(catalog, profile)
    revision = original["current_revision"]
    before = _defaults(catalog)
    saved = catalog.client.patch(
        "/api/v1/document-defaults",
        json={
            "language": "es",
            "base_resume_language": language,
        },
        headers=catalog.headers(before["version"]),
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["language"] == "es"
    assert saved.json()["base_resume_language"] == language
    assert _defaults(catalog) == saved.json()
    historical = catalog.client.get(
        f"/api/v1/documents/{original['id']}/revisions/{revision['id']}"
    )
    assert historical.json() == revision
    rebuilt = _base(catalog, profile)["current_revision"]
    assert rebuilt["body"]["title"] == LABELS[language]["base"]
    assert rebuilt["validation_issues"] == []
    old_text = [
        block["text"] for section in revision["body"]["sections"] for block in section["blocks"]
    ]
    new_text = [
        block["text"] for section in rebuilt["body"]["sections"] for block in section["blocks"]
    ]
    assert new_text == old_text, "English owner-written prose is not translated."
    assert rebuilt["provenance_snapshot"]["frame"]["language"] == language
    # Settings alone never mutate the profile.
    loaded = catalog.client.get("/api/v1/profile")
    assert loaded.json() == profile


@pytest.mark.postgres
@pytest.mark.parametrize("field", ["language", "base_resume_language"])
@pytest.mark.parametrize("invalid", ["it", "ar", "DE", "", None, 1])
def test_unsupported_settings_languages_return_422(
    catalog: SourceContext, field: str, invalid: object
) -> None:
    before = _defaults(catalog)
    response = catalog.client.patch(
        "/api/v1/document-defaults",
        json={field: invalid},
        headers=catalog.headers(before["version"]),
    )
    assert response.status_code == 422, response.text
    assert _defaults(catalog) == before


def test_english_fields_and_claim_output_remain_literal() -> None:
    assert (
        render_claim(_structure(period={"start": "2026-03", "end": "2026-04"}))
        == "Led the billing migration (Mar 2026–Apr 2026)"
    )
    assert (
        printable_text(
            "Owner text", {"start_date": "2026-03", "end_date": "Present", "proficiency": "fluent"}
        )
        == "Owner text\n2026-03\nPresent\nfluent"
    )
    assert closing_for("professional") == "Yours sincerely,"


@pytest.mark.parametrize(
    ("frame", "title", "heading", "path"),
    [
        ({}, "Résumé", "Kenntnisse", "/sections/0/heading"),
        ({}, "Lebenslauf", "Skills", "/title"),
        ({"language": "en"}, "Résumé", "Kenntnisse", "/sections/0/heading"),
        ({"language": "en"}, "Lebenslauf", "Skills", "/title"),
        ({"language": "de"}, "Lebenslauf", "Kenntnisse", None),
        ({"language": "de"}, "Lebenslauf", "Compétences", "/sections/0/heading"),
    ],
)
def test_heading_checks_use_only_the_saved_document_language(
    frame: dict[str, object], title: str, heading: str, path: str | None
) -> None:
    language = cast(RenderLanguage, frame.get("language", "en"))
    body = DocumentBody(
        title=title,
        sections=[
            DocumentSection(
                key="section-0",
                heading=heading,
                blocks=[
                    DocumentBlock(
                        block_key="b-1",
                        text="Dear Hiring team,"
                        if language == "en"
                        else LABELS[language]["greeting"],
                        claim_kind="greeting",
                        claim_revision_ids=[],
                        evidence_revision_ids=[],
                    )
                ],
            )
        ],
    )
    issues = check_statements(body, [], frame, "resume")
    assert issues == (
        []
        if path is None
        else [
            {
                "path": path,
                "code": "unestablished_heading",
                "statement": title if path == "/title" else heading,
            }
        ]
    )


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("de", "Ich bewerbe mich auf die Stelle als Engineer bei Example."),
        ("fr", "Je vous présente ma candidature au poste de Engineer chez Example."),
        ("es", "Presento mi candidatura al puesto de Engineer en Example."),
    ],
)
def test_native_letter_intent_wording(language: RenderLanguage, expected: str) -> None:
    assert (
        connectives({"language": language, "role": "Engineer", "organization": "Example"})[expected]
        == "intent"
    )


@pytest.mark.parametrize(
    ("verb", "label"),
    [
        ("partnered", "Kooperation:"),
        ("collaborated", "Zusammenarbeit:"),
        ("contributed", "Mitwirkung:"),
        ("contributed_to", "Mitwirkung:"),
        ("worked_on", "Arbeit:"),
        ("participated_in", "Beteiligung:"),
        ("took_part_in", "Teilnahme:"),
        ("assisted_with", "Hilfestellung:"),
        ("assisted_in", "Hilfestellung:"),
        ("helped", "Hilfestellung:"),
        ("helped_to", "Hilfestellung:"),
        ("assisted", "Assistenz:"),
    ],
)
def test_german_contribution_labels_do_not_change_owner_object_case(verb: str, label: str) -> None:
    structure = _structure(verb=verb, subject="die Migration")
    text = render_claim(structure, "de")
    assert text == f"{label} die Migration"
    claim, evidence = uuid4(), uuid4()
    fact: dict[str, object] = {
        "claim_revision_id": str(claim),
        "evidence_revision_id": str(evidence),
        "claim_text": render_claim(structure),
        "evidence_text": render_claim(structure),
        "localized_claim": structure.model_dump(mode="json"),
        "display_language": "de",
        "structured_fields": {},
        "printable_text": text,
        "category": "experience",
    }
    assert (
        check_statements(
            compose_base({"facts": [fact], "language": "de"}), [fact], {"language": "de"}, "base"
        )
        == []
    )


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("kind", "language", "length", "status"),
    [
        ("publication", "de", 30, 202),
        ("other", "en", 3984, 202),
        ("other", "en", 3985, 409),
        ("other", "de", 3984, 409),
        ("other", "fr", 3984, 409),
        ("other", "es", 3984, 409),
    ],
)
def test_base_printable_bounds_and_publication_rule_apply_after_localization(
    catalog: SourceContext, kind: str, language: RenderLanguage, length: int, status: int
) -> None:
    profile = catalog.client.get("/api/v1/profile").json()
    owner_text = "Doe, J. Paper. ACM 2021." if kind == "publication" else "A" * length
    response = catalog.client.post(
        "/api/v1/evidence",
        json={
            "payload": {"text": owner_text, "source": "Synthetic language source"},
            "provenance": {
                "source_type": "owner_statement",
                "privacy_class": "private",
                "employer_owned": False,
            },
        },
        headers=catalog.headers(profile["version"]),
    )
    assert response.status_code == 200, response.text
    profile = response.json()
    response = catalog.client.post(
        "/api/v1/claims",
        json={
            "kind": kind,
            "payload": {"text": owner_text, "start_date": "2020-09", "end_date": "2021-09"},
            "evidence_links": [
                {
                    "evidence_revision_id": profile["evidence"][0]["revision"]["id"],
                    "support_kind": "user_asserted",
                    "excerpt": owner_text,
                }
            ],
        },
        headers=catalog.headers(profile["version"]),
    )
    assert response.status_code == 200, response.text
    profile = response.json()
    defaults = _defaults(catalog)
    response = catalog.client.patch(
        "/api/v1/document-defaults",
        json={"base_resume_language": language},
        headers=catalog.headers(defaults["version"]),
    )
    assert response.status_code == 200, response.text
    if status == 409:
        response = catalog.client.post(
            "/api/v1/profile/base-resume",
            json={
                "profile_revision_id": profile["current_revision"]["id"],
                "template_version": "bonafides-evidence-v1",
            },
            headers=catalog.headers(profile["version"]),
        )
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "document_context_too_large"
    else:
        revision = _base(catalog, profile)["current_revision"]
        assert revision["validation_issues"] == []
        text = revision["body"]["sections"][0]["blocks"][0]["text"]
        assert text == (owner_text if kind == "publication" else owner_text + "\n2020-09\n2021-09")
