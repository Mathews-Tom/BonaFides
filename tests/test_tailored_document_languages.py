# SPDX-License-Identifier: MIT OR Apache-2.0
"""API-level language pinning, exact model wire and synthetic qualification controls."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from sqlalchemy import text
from test_checked_generation import _run, _stub
from test_checked_section_regeneration import _proposal, _regenerate
from test_document_conversations import _post as _conversation_post
from test_document_conversations import _queue as _conversation
from test_document_defaults import _defaults
from test_document_generation import (
    _claim,
    _document,
    _pair_documents,
    _preview,
    _queue_pair,
    _setup_pair,
)
from test_identity import _login
from test_identity import identity as identity
from test_import_parser import parser as parser
from test_installation_control import renderer_spool as renderer_spool
from test_portable_archive import _encoded
from test_portable_data import _digest, _export
from test_portable_workspace import _import
from test_section_regeneration import _apply
from test_sources import SourceContext
from test_sources import catalog as catalog

from bonafides import document_generation as generation
from bonafides import (
    documents,
    letter_frame,
    local_model,
    model_calls,
    model_context,
    model_projections,
    model_qualification,
    suitability,
)
from bonafides.commands import canonical_json
from bonafides.import_parser import ImportParserClient
from bonafides.installed_model import verify_result
from bonafides.languages import LABELS, RenderLanguage
from bonafides.problems import Problem
from bonafides.statement_check import check_statements
from tools import stub_model

DATASET = Path(__file__).parent / "fixtures/qualification"
STAGES = ("generate_documents", "regenerate_section", "refine_documents")
LANGUAGES: tuple[RenderLanguage, ...] = ("de", "fr", "es")


def _context(
    context: SourceContext,
    profile: dict[str, Any],
    application: dict[str, Any],
    language: str | None = None,
) -> Any:
    return context.client.get(
        f"/api/v1/applications/{application['id']}/generation-context",
        params={
            "profile_revision_id": profile["current_revision"]["id"],
            "template_version": "bonafides-evidence-v1",
            **({"language": language} if language is not None else {}),
        },
    )


def _structured_profile(
    context: SourceContext, fields: dict[str, str] | None = None
) -> dict[str, Any]:
    profile = context.client.get("/api/v1/profile").json()
    source = "Led the billing migration"
    evidence = context.client.post(
        "/api/v1/evidence",
        json={
            "payload": {"text": source, "source": "Synthetic language proof"},
            "provenance": {
                "source_type": "owner_statement",
                "privacy_class": "private",
                "employer_owned": False,
            },
        },
        headers=context.headers(profile["version"]),
    )
    assert evidence.status_code == 200, evidence.text
    saved = context.client.post(
        "/api/v1/claims",
        json={
            "kind": "experience",
            "payload": {
                "claim": {"schema_version": 1, "verb": "led", "subject": "the billing migration"},
                **(fields or {}),
            },
            "evidence_links": [
                {
                    "evidence_revision_id": evidence.json()["evidence"][-1]["revision"]["id"],
                    "support_kind": "user_asserted",
                    "excerpt": source,
                }
            ],
        },
        headers=context.headers(evidence.json()["version"]),
    )
    assert saved.status_code == 200, saved.text
    return cast(dict[str, Any], saved.json())


@pytest.mark.postgres
@pytest.mark.parametrize("language", ("en", *LANGUAGES))
def test_each_document_stage_fits_attach_limits_with_full_correction_reserve(
    catalog: SourceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    language: RenderLanguage,
) -> None:
    _, application = _setup_pair(catalog)
    profile = _structured_profile(
        catalog,
        {
            "role": "Engineer",
            "organization": "Example",
            "start_date": "2020-01",
            "end_date": "2021-02",
        },
    )
    with _stub(monkeypatch, tmp_path, "valid") as stub:
        route = local_model.load_route(tmp_path / "stub-route.json")
        assert route is not None
        # models.sh attach uses 2048, unlike the fixture route writer's 512.
        route = replace(route, framing_token_overhead=2048)
        assert (
            route.context_window,
            route.max_input_tokens,
            route.max_output_tokens,
            route.max_request_bytes,
            route.max_response_bytes,
        ) == (32768, 12000, 6000, 131072, 262144)
        monkeypatch.setattr(local_model, "load_route", lambda _path: route)
        preview = _context(catalog, profile, application, language)
        assert preview.status_code == 200, preview.text
        assert _run(catalog, _queue_pair(catalog, preview.json()))["state"] == "succeeded"
        pair = _pair_documents(catalog, application)["items"]
        resume = next(item for item in pair if item["kind"] == "resume")
        letter = next(item for item in pair if item["kind"] == "cover_letter")
        assert _regenerate(catalog, resume)["state"] == "succeeded"
        queued = _conversation_post(
            catalog,
            application,
            [letter["id"]],
            message="Please make the letter more concise.",
        )
        assert queued.status_code == 202, queued.text
        task = _claim(catalog, queued.json()["workflow"]["id"])
        assert _run(catalog, task)["state"] == "succeeded"
        stages = set()
        for request in stub.requests:
            stage = request["format"]["properties"]["stage"]["const"]
            stages.add(stage)
            wire = json.loads(request["messages"][1]["content"])
            source = {key: value for key, value in wire.items() if key != "localized_rendering"}
            payload, input_bound, _, _ = local_model.OllamaClient(route).prepare(
                stage,
                source,
                local_model.PROMPT_VERSIONS[stage],
                correction=("x" * 120,) * 12,
            )
            assert input_bound == len(payload) + 2048
            assert input_bound <= 12000
            if language != "en" and stage != "generate_documents":
                assert "headings" not in wire.get("localized_rendering", {})
        assert stages == set(STAGES)


@pytest.mark.postgres
@pytest.mark.parametrize("language", LANGUAGES)
def test_tailored_pair_rewrites_and_refinement_keep_saved_language(
    catalog: SourceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    language: RenderLanguage,
) -> None:
    _, application = _setup_pair(catalog)
    profile = _structured_profile(catalog)
    defaults = _defaults(catalog)
    seeded = catalog.client.patch(
        "/api/v1/document-defaults",
        json={"language": language},
        headers=catalog.headers(defaults["version"]),
    )
    assert seeded.status_code == 200, seeded.text
    with _stub(monkeypatch, tmp_path, "valid") as stub:
        response = _context(catalog, profile, application, language)
        assert response.status_code == 200, response.text
        preview = response.json()
        assert preview["language"] == preview["frame"]["language"] == language
        assert _run(catalog, _queue_pair(catalog, preview))["state"] == "succeeded"
        pair = _pair_documents(catalog, application)["items"]
        assert {item["kind"] for item in pair} == {"resume", "cover_letter"}
        for item in pair:
            revision = item["current_revision"]
            provenance = revision["provenance_snapshot"]
            assert provenance["language"] == provenance["frame"]["language"] == language
            assert revision["validation_issues"] == []
            assert revision["body"]["title"] == LABELS[language][item["kind"]]
            assert (
                revision["body"]["sections"][0]["heading"]
                == LABELS[language]["experience" if item["kind"] == "resume" else "Letter"]
            )
        resume = next(item for item in pair if item["kind"] == "resume")
        letter = next(item for item in pair if item["kind"] == "cover_letter")
        assert (
            letter["current_revision"]["provenance_snapshot"]["frame"]["closing"]
            == LABELS[language]["professional"]
        )
        facts = resume["current_revision"]["provenance_snapshot"]["facts"]
        localized = next(fact for fact in facts if "localized_claim" in fact)
        rendered = {"de": "Leitete", "fr": "A dirigé", "es": "Lideró"}[language]
        assert localized["printable_text"] == f"{rendered} the billing migration"
        body_text = "\n".join(
            block["text"]
            for section in resume["current_revision"]["body"]["sections"]
            for block in section["blocks"]
        )
        for fact in facts:
            assert fact["printable_text"] in body_text
            if "localized_claim" not in fact:
                assert fact["claim_text"] in fact["printable_text"]
        wire = json.loads(stub.requests[0]["messages"][1]["content"])
        assert wire["language"] == language
        assert (
            wire["localized_rendering"]["headings"]["experience"] == LABELS[language]["experience"]
        )
        assert "Do not translate or paraphrase" in stub.requests[0]["messages"][0]["content"]
        assert localized["printable_text"] in {claim["text"] for claim in wire["claims"]}

        defaults = _defaults(catalog)
        changed = catalog.client.patch(
            "/api/v1/document-defaults",
            json={"language": "en"},
            headers=catalog.headers(defaults["version"]),
        )
        assert changed.status_code == 200, changed.text
        effective = _context(catalog, profile, application)
        assert effective.status_code == 200 and effective.json()["language"] == language
        mismatch = _context(catalog, profile, application, "en")
        assert mismatch.status_code == 409
        assert mismatch.json()["code"] == "application_document_language_mismatch"

        proposal = _proposal(catalog, resume, _regenerate(catalog, resume))
        assert proposal["validation_issues"] == []
        applied = _apply(catalog, resume, proposal)
        assert applied.status_code == 200, applied.text
        resume = applied.json()
        assert resume["current_revision"]["provenance_snapshot"]["language"] == language
        assert json.loads(stub.requests[-1]["messages"][1]["content"])["language"] == language

        task = _conversation(catalog, application, [letter])
        workflow = _run(catalog, task)
        assert workflow["state"] == "succeeded", (workflow, stub.requests[-1]["messages"])
        refined = _document(catalog, letter["id"])
        assert refined["current_revision"]["id"] != letter["current_revision"]["id"]
        assert refined["current_revision"]["provenance_snapshot"]["language"] == language
        assert refined["current_revision"]["validation_issues"] == []
        assert (
            refined["current_revision"]["provenance_snapshot"]["frame"]["closing"]
            == LABELS[language]["concise"]
        )
        refinement_wire = json.loads(stub.requests[-1]["messages"][1]["content"])
        assert refinement_wire["shared"]["language"] == language
        assert "localized_rendering" in refinement_wire


@pytest.mark.postgres
def test_default_only_seeds_unsaved_applications_and_unknown_request_is_422(
    catalog: SourceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    profile, application = _setup_pair(catalog)
    with _stub(monkeypatch, tmp_path, "valid"):
        explicit = _preview(catalog, profile, application)
        assert _context(catalog, profile, application).json() == explicit
        assert "language" not in explicit["frame"]
        settings = _defaults(catalog)
        response = catalog.client.patch(
            "/api/v1/document-defaults",
            json={"language": "fr"},
            headers=catalog.headers(settings["version"]),
        )
        assert response.status_code == 200, response.text
        assert _context(catalog, profile, application).json()["language"] == "fr"
        assert _context(catalog, profile, application, "es").json()["language"] == "es"
        unknown = _context(catalog, profile, application, "xx")
        assert unknown.status_code == 422
        assert unknown.json()["code"] == "unsupported_document_language"
        request = {
            key: explicit[key]
            for key in (
                "profile_revision_id",
                "requirements_revision_id",
                "template_version",
                "tone",
                "language",
                "context_sha256",
            )
        }
        request["language"] = "xx"
        unknown = catalog.client.post(
            f"/api/v1/applications/{application['id']}/generation",
            json=request,
            headers=catalog.headers(explicit["application_version"]),
        )
        assert unknown.status_code == 422
        assert unknown.json()["code"] == "unsupported_document_language"


@pytest.mark.parametrize("stage", STAGES)
def test_shipped_qualification_discovers_language_cases_and_stub_passes(
    stage: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    baseline, baseline_digest = model_qualification.load_cases(DATASET, stage)
    cases, suite_digest = model_qualification.load_cases(DATASET, stage, include_languages=True)
    assert len(baseline) == 20 and len(cases) == 26 and baseline_digest != suite_digest
    assert cases[:20] == baseline
    with _stub(monkeypatch, tmp_path, "qualification"):
        original = tmp_path / "original-route.json"
        original.write_bytes((tmp_path / "stub-route.json").read_bytes())
        result = model_qualification.main(
            [
                "qualify",
                "--route",
                str(tmp_path / "stub-route.json"),
                "--stage",
                stage,
                "--dataset",
                str(DATASET),
                "--reviewed-by",
                "synthetic-only",
            ]
        )
        verify_result(tmp_path / "stub-route.json", original, stage, result)
    receipt = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result == 0 and receipt["status"] == "passed"
    assert receipt["case_count"] == 26
    assert receipt["counts"]["valid_first"] == 26
    assert receipt["usable_statement_rate_per_mille"] == 1000
    for case in cases[20:]:
        model_qualification.check_negative_control(case, stage)
        if "withheld_text" in case:
            source = cast(dict[str, Any], case)
            facts = (
                source["documents"][0]["provenance_snapshot"]["facts"]
                if stage == "refine_documents"
                else source["facts"]
            )
            with pytest.raises(ValueError, match="qualification_paraphrase_not_withheld"):
                model_qualification.check_negative_control(
                    {**case, "withheld_text": facts[0]["printable_text"]},
                    stage,
                )


@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize("language", LANGUAGES)
def test_stub_paraphrases_are_withheld_by_the_existing_checker(
    stage: str,
    language: RenderLanguage,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cases, _ = model_qualification.load_cases(DATASET, stage, include_languages=True)
    case = next(case for case in cases if case["id"] == f"{language}-withheld-on-paraphrase")
    context = model_qualification.projection(case, stage)
    with _stub(monkeypatch, tmp_path, "paraphrase"):
        route = local_model.load_route(tmp_path / "stub-route.json")
        assert route is not None
        output = (
            local_model.OllamaClient(route)
            .call(
                stage,
                context,
                local_model.PROMPT_VERSIONS[stage],
            )
            .output
        )
    if stage == "refine_documents":
        with pytest.raises(model_calls.OutputMismatch):
            model_qualification.checked_refinement(output, case)
    else:
        plan: dict[str, object] = {
            "context": context,
            "facts": case["facts"],
            "language": language,
            "frame": {"language": language},
        }
        bodies = model_qualification.checked_bodies(
            output,
            stage,
            plan,
            cast(dict[str, object], case["target"]),
            documents.parse_body(case["base_body"]) if stage == "regenerate_section" else None,
        )
        factual, usable = model_qualification.statement_counts(bodies, plan)
        assert factual > usable


@pytest.mark.parametrize("stage", STAGES)
def test_english_wire_keeps_original_text_and_prompt(stage: str, tmp_path: Path) -> None:
    from test_local_model import qualified_route, route_data

    cases, _ = model_qualification.load_cases(DATASET, stage)
    context = model_qualification.projection(cases[0], stage)
    route = qualified_route(tmp_path, route_data("http://127.0.0.1:11434"))
    payload = local_model.OllamaClient(route).prepare(
        stage,
        context,
        local_model.PROMPT_VERSIONS[stage],
    )[0]
    messages = json.loads(payload)["messages"]
    assert messages[0]["content"] == local_model._SYSTEM + " " + local_model._SUFFIXES[stage]
    assert messages[1]["content"] == canonical_json(context)
    assert "localized_rendering" not in messages[1]["content"]
    output = stub_model.respond("valid", json.loads(payload))
    if stage == "generate_documents":
        pair = json.loads(cast(dict[str, Any], output["message"])["content"])
        assert pair == stub_model._pair(context)
        posting = cast(dict[str, object], context["posting"])
        frame = {"role": posting["title"], "organization": posting["organization"]}
        plan: dict[str, object] = {"context": context, "facts": cases[0]["facts"], "frame": frame}
        for candidate in generation.validate_pair(pair, plan).documents:
            body = generation._candidate_body(candidate, plan)
            assert (
                check_statements(
                    body, cast(list[dict[str, object]], cases[0]["facts"]), frame, candidate.kind
                )
                == []
            )


@pytest.mark.parametrize("consumer", ("parts", "is_custom", "closing_for"))
def test_untrusted_frame_language_uses_printable_frame_validation(consumer: str) -> None:
    with pytest.raises(Problem) as error:
        if consumer == "parts":
            letter_frame.parts({"language": "xx"}, "professional")
        elif consumer == "is_custom":
            letter_frame.is_custom({"frame": {"language": "xx"}, "letter_closing_edited": True})
        else:
            letter_frame.closing_for("professional", cast(RenderLanguage, "xx"))
    assert error.value.status == 422 and error.value.code == "unsupported_document_language"


@pytest.mark.postgres
def test_unknown_language_in_imported_provenance_is_a_422_not_a_500(
    catalog: SourceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    parser: ImportParserClient,
    renderer_spool: Path,
) -> None:
    profile, application = _setup_pair(catalog)
    with _stub(monkeypatch, tmp_path, "valid"):
        assert (
            _run(catalog, _queue_pair(catalog, _preview(catalog, profile, application)))["state"]
            == "succeeded"
        )
    archive = _export(catalog)
    for revision in archive.manifest.records["document_revisions"]:
        provenance = cast(dict[str, Any], revision["provenance_snapshot"])
        provenance["language"] = provenance["frame"]["language"] = "xx"
        revision["content_sha256"] = generation.revision_digest(
            documents.parse_body(revision["body"]),
            str(revision["template_version"]),
            provenance,
        )
    archive = _digest(archive)
    _import(catalog, parser, renderer_spool, _encoded(archive.manifest, archive.blobs))
    login = _login(catalog.identity, catalog.client)
    assert login.status_code == 201, login.text
    for draft in archive.manifest.records["document_drafts"]:
        response = catalog.client.get(f"/api/v1/documents/{draft['id']}")
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "unsupported_document_language"


@pytest.mark.postgres
@pytest.mark.parametrize("language", LANGUAGES)
def test_non_english_email_admission_refuses_without_writes_calls_or_route_blocks(
    catalog: SourceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    language: RenderLanguage,
) -> None:
    _, application = _setup_pair(catalog)
    profile = _structured_profile(catalog)

    def state() -> dict[str, object]:
        with catalog.runtime.transaction() as conn:
            return {
                table: conn.execute(
                    text(
                        f"SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),"
                        f"'[]'::jsonb) FROM bonafides.{table} t WHERE owner_id=:owner"
                    ),
                    {"owner": catalog.owner_id},
                ).scalar_one()
                for table in (
                    "owner_settings",
                    "applications",
                    "document_drafts",
                    "document_revisions",
                    "workflow_runs",
                    "model_runs",
                    "local_model_route_blocks",
                    "match_assessments",
                    "outbox_events",
                    "idempotency_records",
                )
            }

    with _stub(monkeypatch, tmp_path, "valid") as stub:
        response = _context(catalog, profile, application, language)
        assert response.status_code == 200, response.text
        assert _run(catalog, _queue_pair(catalog, response.json()))["state"] == "succeeded"
        version = _pair_documents(catalog, application)["version"]
        with catalog.runtime.transaction() as conn:
            policy = suitability._policy(conn, catalog.owner_id, catalog.identity.settings)
            route = generation.route_status(
                conn, catalog.owner_id, catalog.identity.settings, stage="write_application_email"
            )
        assert route["status"] == "qualified"
        before = state()
        calls = len(stub.requests)
        refused = catalog.client.post(
            f"/api/v1/applications/{application['id']}/email",
            json={},
            headers=catalog.headers(version),
        )
        assert refused.status_code == 409, refused.text
        assert refused.json()["code"] == "application_email_language_unsupported"
        assert len(stub.requests) == calls
        assert state() == before
        with catalog.runtime.transaction() as conn:
            assert suitability._policy(conn, catalog.owner_id, catalog.identity.settings) == policy
            assert (
                generation.route_status(
                    conn,
                    catalog.owner_id,
                    catalog.identity.settings,
                    stage="write_application_email",
                )
                == route
            )


@pytest.mark.parametrize("language", LANGUAGES)
def test_email_projection_stays_english_only(language: RenderLanguage) -> None:
    cases, _ = model_qualification.load_cases(DATASET, "write_application_email")
    context = model_qualification.projection(cases[0], "write_application_email")
    assert local_model.email_context(context) == context
    assert (
        local_model._EmailProjection.model_json_schema()["properties"]["language"]["const"] == "en"
    )
    with pytest.raises(local_model.ModelFailure, match="model_context_invalid"):
        local_model.email_context({**context, "language": language})


@pytest.mark.parametrize("mounted_path", ("/stub_model.py", "/proof/stub_model.py"))
@pytest.mark.parametrize("explicit_dataset", (False, True))
def test_qualification_stub_dataset_is_independent_of_its_mount_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mounted_path: str,
    explicit_dataset: bool,
) -> None:
    dataset = DATASET.resolve()
    cases, _ = model_qualification.load_cases(dataset, "refine_documents")
    case = next(case for case in cases if cast(dict[str, object], case["expected"])["declined"])
    context = model_qualification.projection(case, "refine_documents")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(stub_model, "__file__", mounted_path)
    if explicit_dataset:
        monkeypatch.setenv("BONAFIDES_QUALIFICATION_DATASET", str(dataset))
        monkeypatch.setattr(stub_model, "PACKAGED_QUALIFICATION_DATASET", tmp_path / "missing")
    else:
        monkeypatch.delenv("BONAFIDES_QUALIFICATION_DATASET", raising=False)
        monkeypatch.setattr(stub_model, "PACKAGED_QUALIFICATION_DATASET", dataset)
    output = stub_model.respond(
        "qualification",
        {
            "messages": [
                {"role": "system", "content": ""},
                {"role": "user", "content": canonical_json(context)},
            ]
        },
    )
    reply = json.loads(cast(dict[str, Any], output["message"])["content"])
    assert reply["declined"] is True and reply["edits"] == []


@pytest.mark.parametrize("explicit_dataset", (False, True))
def test_qualification_stub_missing_dataset_fails_loudly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, explicit_dataset: bool
) -> None:
    dataset = DATASET.resolve()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BONAFIDES_QUALIFICATION_DATASET", raising=False)
    monkeypatch.setattr(stub_model, "PACKAGED_QUALIFICATION_DATASET", tmp_path / "missing")
    if explicit_dataset:
        monkeypatch.setattr(stub_model, "PACKAGED_QUALIFICATION_DATASET", dataset)
        monkeypatch.setenv("BONAFIDES_QUALIFICATION_DATASET", str(tmp_path / "missing"))
    with pytest.raises(stub_model.StubFailure, match="BONAFIDES_QUALIFICATION_DATASET"):
        stub_model._refinement_dataset()


@pytest.mark.postgres
@pytest.mark.parametrize("language", LANGUAGES)
def test_localized_fields_are_printed_once_without_per_claim_wire_overhead(
    catalog: SourceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    language: RenderLanguage,
) -> None:
    fields = {
        "role": "Engineer",
        "organization": "Example",
        "start_date": "2020-01",
        "end_date": "2021-02",
    }
    for _ in range(20):
        profile = _structured_profile(catalog, fields)
    bounds: dict[tuple[str, str, int], int] = {}
    posting = {"title": "Engineer", "company": "Example", "description_text": "Engineer needed."}
    document_id = str(UUID(int=1))
    with _stub(monkeypatch, tmp_path, "valid"):
        route = local_model.load_route(tmp_path / "stub-route.json")
        assert route is not None
        route = replace(route, framing_token_overhead=2048)
        client = local_model.OllamaClient(route)
        for projected_language in ("en", language):
            with catalog.runtime.transaction() as conn:
                facts = generation._fact_data(
                    conn,
                    catalog.owner_id,
                    UUID(profile["current_revision"]["id"]),
                    language=projected_language,
                )
                unchanged = canonical_json(facts)
                claims, evidence = model_context.professional_context(
                    conn, catalog.owner_id, facts, language=projected_language
                )
            assert len(claims) == len(facts) == 20
            assert all(fact["structured_fields"] == fields for fact in facts)
            if projected_language != "en":
                assert all(claim["structured_fields"] == {} for claim in claims)
                assert [claim["text"] for claim in claims] == [
                    fact["printable_text"] for fact in facts
                ]
                source = model_projections.generation_projection(
                    posting, [], claims, evidence, "professional", projected_language
                )
                frame = {
                    "language": projected_language,
                    "role": "Engineer",
                    "organization": "Example",
                }
                plan: dict[str, object] = {
                    "language": projected_language,
                    "frame": frame,
                    "facts": facts,
                    "context": source,
                }
                # Output statements have only text and references, not wire display fields.
                bodies = model_qualification.checked_bodies(
                    stub_model._pair(source), "generate_documents", plan, {}
                )
                for kind, body in bodies:
                    assert check_statements(body, facts, frame, kind) == []
                resume = next(body for kind, body in bodies if kind == "resume")
                assert [block.text for section in resume.sections for block in section.blocks] == [
                    fact["printable_text"] for fact in facts
                ]
                original = resume.sections[0].blocks[0]
                resume.sections[0].blocks[0] = original.model_copy(
                    update={"text": original.text.replace("Example", "Invented company")}
                )
                assert check_statements(resume, facts, frame, "resume")
            else:
                assert all(claim["structured_fields"] == fields for claim in claims)
                assert [claim["text"] for claim in claims] == [fact["claim_text"] for fact in facts]
            assert canonical_json(facts) == unchanged
            templates: dict[str, dict[str, Any]] = {}
            for count in (1, 20):
                selected = claims[:count]
                evidence_ids = {
                    identifier
                    for claim in selected
                    for identifier in cast(list[str], claim["evidence_revision_ids"])
                }
                source = model_projections.generation_projection(
                    posting,
                    [],
                    selected,
                    [item for item in evidence if item["id"] in evidence_ids],
                    "professional",
                    projected_language,
                )
                before = {
                    "block_key": "fact",
                    "text": facts[0]["printable_text"],
                    "claim_kind": "factual",
                    "claim_revision_ids": [selected[0]["id"]],
                    "evidence_revision_ids": selected[0]["evidence_revision_ids"],
                }
                contexts = {
                    "generate_documents": source,
                    "regenerate_section": model_projections.regeneration_projection(
                        posting,
                        [],
                        selected,
                        cast(list[dict[str, object]], source["evidence"]),
                        "professional",
                        projected_language,
                        {
                            "kind": "resume",
                            "section_key": "experience",
                            "heading": "Experience",
                            "blocks": [
                                {key: value for key, value in before.items() if key != "block_key"}
                            ],
                        },
                    ),
                    "refine_documents": model_projections.refinement_projection(
                        [{**source, "document_id": document_id}],
                        "Please make the letter more concise.",
                        [],
                        [
                            {
                                "document_id": document_id,
                                "section_key": "experience",
                                "block_key": "fact",
                                "heading": "Experience",
                                "previous_sha256": generation.sha(before),
                                "before": before,
                            }
                        ],
                    ),
                }
                for stage, context in contexts.items():
                    if count == 1:
                        payload, bound, _, _ = client.prepare(
                            stage,
                            context,
                            local_model.PROMPT_VERSIONS[stage],
                            correction=("x" * 120,) * 12,
                        )
                        templates[stage] = json.loads(payload)
                        assert bound <= 12000
                    else:
                        # Measure the complete worst-correction wire even when twenty
                        # claims exceed the unchanged cap in English as well.
                        wire = templates[stage]
                        prepared = json.loads(wire["messages"][1]["content"])
                        wire["messages"][1]["content"] = canonical_json(
                            {
                                **context,
                                **(
                                    {"localized_rendering": prepared["localized_rendering"]}
                                    if "localized_rendering" in prepared
                                    else {}
                                ),
                            }
                        )
                        bound = len(canonical_json(wire).encode()) + 2048
                        assert bound > 12000
                        with pytest.raises(local_model.ModelFailure, match="model_context_limit"):
                            client.prepare(
                                stage,
                                context,
                                local_model.PROMPT_VERSIONS[stage],
                                correction=("x" * 120,) * 12,
                            )
                    bounds[projected_language, stage, count] = bound
    for stage in STAGES:
        small = bounds[language, stage, 1] - bounds["en", stage, 1]
        large = bounds[language, stage, 20] - bounds["en", stage, 20]
        assert large <= small <= (1024 if stage == "generate_documents" else 384)
