# SPDX-License-Identifier: MIT OR Apache-2.0
"""Claim usage read from immutable document claim links, and the links' atomic writers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, event, text
from test_checked_generation import (
    _archive_claim,
    _claim_item,
    _edit_claim,
    _factual_blocks,
    _generate,
    _restore,
    _run,
    _stub,
)
from test_document_generation import (
    _base,
    _document,
    _pair_documents,
    _preview,
    _queue_pair,
    _save,
)
from test_identity import identity as identity
from test_sources import SourceContext
from test_sources import catalog as catalog
from test_tracking import _application, _history, _stage, _write
from test_tracking import _get as _get_application

from bonafides import claim_usage, documents
from bonafides.problems import Problem

TABLES = ("document_drafts", "document_revisions", "document_claim_links")


class InjectedFailure(RuntimeError):
    pass


@contextmanager
def _fail_after_link_insert() -> Iterator[list[str]]:
    fired: list[str] = []

    def after(*args: object) -> None:
        statement = str(args[2])
        if statement.lstrip().startswith("INSERT INTO bonafides.document_claim_links"):
            fired.append(statement)
            raise InjectedFailure(statement)

    event.listen(Engine, "after_cursor_execute", after)
    try:
        yield fired
    finally:
        event.remove(Engine, "after_cursor_execute", after)


def _state(context: SourceContext) -> dict[str, Any]:
    with context.runtime.transaction() as conn:
        counts = {
            table: conn.execute(
                text(f"SELECT count(*) FROM bonafides.{table} WHERE owner_id=:owner"),
                {"owner": context.owner_id},
            ).scalar_one()
            for table in TABLES
        }
        drafts = conn.execute(
            text("""SELECT id,current_revision_id,version FROM bonafides.document_drafts
            WHERE owner_id=:owner ORDER BY id"""),
            {"owner": context.owner_id},
        ).all()
    return {"counts": counts, "drafts": [tuple(row) for row in drafts]}


def _get(context: SourceContext, path: str) -> dict[str, Any]:
    response = context.client.get(path)
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def _application_claims(context: SourceContext, application: dict[str, Any]) -> list[Any]:
    path = f"/api/v1/applications/{application['id']}/claims"
    return cast(list[Any], _get(context, path)["items"])


def _usage(context: SourceContext, claim_id: str, history: bool = False) -> dict[str, Any]:
    suffix = "?history=true" if history else ""
    return _get(context, f"/api/v1/claims/{claim_id}/usage{suffix}")


def _pair(
    context: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, scenario: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    with _stub(monkeypatch, tmp_path, scenario):
        profile, application, task = _generate(context)
        assert _run(context, task)["state"] == "succeeded"
    return profile, application


@pytest.mark.postgres
def test_pair_usage_lists_each_cited_claim_revision_under_its_application(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, application = _pair(catalog, monkeypatch, tmp_path, "valid")
    items = _application_claims(catalog, application)
    for document in _pair_documents(catalog, application)["items"]:
        cited = {block["claim_revision_ids"][0] for block in _factual_blocks(document)}
        used = [item for item in items if item["document_id"] == document["id"]]
        assert {item["claim_revision_id"] for item in used} == cited
        assert {item["document_revision_id"] for item in used} == {
            document["current_revision"]["id"]
        }
        assert all(
            item["application"]["id"] == application["id"]
            and item["scope"] == "tailored"
            and not item["superseded"]
            and not item["archived"]
            and item["statements"]
            for item in used
        )
    _, claim = _claim_item(catalog)
    usage = _usage(catalog, claim["id"])
    assert usage["items"] and {item["claim_revision_id"] for item in usage["items"]} == {
        claim["revision"]["id"]
    }
    assert {item["application"]["id"] for item in usage["items"]} == {application["id"]}
    assert {item["kind"] for item in usage["items"]} == {"resume", "cover_letter"}
    outcomes = usage["outcomes"]
    assert outcomes["application_count"] == 1
    assert outcomes["no_progression_count"] == 1
    assert outcomes["inferred_applied_from_rejection"] == 0
    assert outcomes["archived_application_count"] == 0
    assert outcomes["small_sample_threshold"] == 5 and outcomes["small_sample"]
    assert outcomes["stages"] == [
        {"stage": stage, "furthest_count": 0, "reached_count": 0, "rate": 0}
        for stage in ("applied", "screening", "interview", "offer", "accepted")
    ]


@pytest.mark.postgres
def test_edited_archived_and_restored_claims_keep_their_pinned_usage(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile, application = _pair(catalog, monkeypatch, tmp_path, "valid")
    _, claim = _claim_item(catalog)
    pinned = claim["revision"]["id"]
    original = _usage(catalog, claim["id"])
    before = original["items"]
    assert before and all(not item["superseded"] for item in before)

    assert _edit_claim(catalog).status_code == 200
    edited = _usage(catalog, claim["id"])
    assert edited["current_revision_id"] != pinned
    assert [(item["document_id"], item["claim_revision_id"]) for item in edited["items"]] == [
        (item["document_id"], pinned) for item in before
    ]
    assert all(item["superseded"] and not item["archived"] for item in edited["items"])
    assert edited["outcomes"] == original["outcomes"]

    assert _archive_claim(catalog).status_code == 200
    archived = _usage(catalog, claim["id"])
    assert archived["archived"] and len(archived["items"]) == len(before)
    assert archived["outcomes"] == original["outcomes"]
    listed = [
        item
        for item in _application_claims(catalog, application)
        if item["claim_id"] == claim["id"]
    ]
    assert listed and all(item["superseded"] and item["archived"] for item in listed)

    assert _restore(catalog, profile["current_revision"]["id"]).status_code == 200
    restored = _usage(catalog, claim["id"])
    assert not restored["archived"] and restored["current_revision_id"] == pinned
    assert all(not item["superseded"] for item in restored["items"])
    assert restored["outcomes"] == original["outcomes"]


@pytest.mark.postgres
def test_document_edit_moves_current_usage_and_keeps_history(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, application = _pair(catalog, monkeypatch, tmp_path, "valid")
    resume = next(
        item for item in _pair_documents(catalog, application)["items"] if item["kind"] == "resume"
    )
    body = resume["current_revision"]["body"]
    claim_revision = _factual_blocks(resume)[0]["claim_revision_ids"][0]
    claim_id = next(
        item["claim_id"]
        for item in _application_claims(catalog, application)
        if item["claim_revision_id"] == claim_revision
    )
    for section in body["sections"]:
        section["blocks"] = [
            block
            for block in section["blocks"]
            if block["claim_kind"] != "factual" or claim_revision not in block["claim_revision_ids"]
        ]
    saved = _save(catalog, resume, body)
    assert saved.status_code == 200, saved.text
    current = _document(catalog, resume["id"])["current_revision"]
    used = [item for item in _application_claims(catalog, application) if item["kind"] == "resume"]
    assert used and {item["document_revision_id"] for item in used} == {current["id"]}
    assert claim_revision not in {item["claim_revision_id"] for item in used}
    usage = _usage(catalog, claim_id, history=True)
    assert resume["id"] not in {item["document_id"] for item in usage["items"]}
    assert [item["document_revision_id"] for item in usage["history"]] == [
        resume["current_revision"]["id"]
    ]
    assert "history" not in _usage(catalog, claim_id)
    # Remove this claim from the other current document as well: earlier links
    # remain available as history, but cannot keep an attempt in the cohort.
    for document in _pair_documents(catalog, application)["items"]:
        if document["id"] == resume["id"]:
            continue
        other_body = document["current_revision"]["body"]
        for section in other_body["sections"]:
            section["blocks"] = [
                block
                for block in section["blocks"]
                if claim_revision not in block["claim_revision_ids"]
            ]
        response = _save(catalog, document, other_body)
        assert response.status_code == 200, response.text
    empty = _usage(catalog, claim_id)
    expanded = _usage(catalog, claim_id, history=True)
    assert not empty["items"] and expanded["history"]
    assert empty["outcomes"] == expanded["outcomes"]
    assert empty["outcomes"]["application_count"] == 0
    assert empty["outcomes"]["no_progression_count"] == 0
    assert all(stage["rate"] is None for stage in empty["outcomes"]["stages"])
    restored = catalog.client.post(
        f"/api/v1/documents/{resume['id']}/restore",
        json={
            "revision_id": resume["current_revision"]["id"],
            "reason": "Restore checked pinned claim",
        },
        headers=catalog.headers(_document(catalog, resume["id"])["version"]),
    )
    assert restored.status_code == 200, restored.text
    restored_usage = _usage(catalog, claim_id)
    assert restored_usage["outcomes"]["application_count"] == 1
    assert {item["claim_revision_id"] for item in restored_usage["items"]} == {claim_revision}


@pytest.mark.postgres
def test_blocked_statements_are_not_usage(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, application = _pair(catalog, monkeypatch, tmp_path, "posting-only-skill")
    resume = next(
        item for item in _pair_documents(catalog, application)["items"] if item["kind"] == "resume"
    )
    [issue] = resume["current_revision"]["validation_issues"]
    statements = {
        statement
        for item in _application_claims(catalog, application)
        for statement in item["statements"]
    }
    assert statements and issue["statement"] not in statements
    blocked = next(
        block for block in _factual_blocks(resume) if block["text"] == issue["statement"]
    )
    [claim_revision] = blocked["claim_revision_ids"]
    claim_id = next(
        item["claim_id"]
        for item in _application_claims(catalog, application)
        if item["claim_revision_id"] == claim_revision
    )
    # Leave the blocked statement, but remove this claim's checked statements.
    for document in _pair_documents(catalog, application)["items"]:
        body = document["current_revision"]["body"]
        for section in body["sections"]:
            section["blocks"] = [
                block
                for block in section["blocks"]
                if claim_revision not in block["claim_revision_ids"]
                or block["text"] == issue["statement"]
            ]
        response = _save(catalog, document, body)
        assert response.status_code == 200, response.text
    usage = _usage(catalog, claim_id, history=True)
    assert not usage["items"] and usage["history"]
    assert usage["outcomes"]["application_count"] == 0


@pytest.mark.postgres
def test_base_resume_usage_is_not_an_application(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, application = _pair(catalog, monkeypatch, tmp_path, "valid")
    base = _base(catalog, catalog.client.get("/api/v1/profile").json())
    _, claim = _claim_item(catalog)
    usage = _usage(catalog, claim["id"])
    items = usage["items"]
    [entry] = [item for item in items if item["scope"] == "base"]
    assert entry["application"] is None and entry["document_id"] == base["id"]
    assert all(item["application"]["id"] == application["id"] for item in items if item != entry)
    assert all(
        item["document_id"] != base["id"] for item in _application_claims(catalog, application)
    )
    assert usage["outcomes"]["application_count"] == 1
    # Deleted tailored drafts no longer qualify, even though their immutable
    # checked links and the base résumé's links are still present.
    with catalog.runtime.transaction() as conn:
        conn.execute(
            text("""UPDATE bonafides.document_drafts SET deleted_at=clock_timestamp()
            WHERE owner_id=:owner AND application_id=:application"""),
            {"owner": catalog.owner_id, "application": UUID(application["id"])},
        )
    base_only = _usage(catalog, claim["id"], history=True)
    assert base_only["items"] and all(item["scope"] == "base" for item in base_only["items"])
    assert base_only["outcomes"] == {
        "application_count": 0,
        "archived_application_count": 0,
        "no_progression_count": 0,
        "inferred_applied_from_rejection": 0,
        "small_sample_threshold": 5,
        "small_sample": True,
        "stages": [
            {"stage": stage, "furthest_count": 0, "reached_count": 0, "rate": None}
            for stage in ("applied", "screening", "interview", "offer", "accepted")
        ],
    }


@pytest.mark.postgres
def test_outcomes_deduplicate_documents_count_repeats_archives_and_all_recorded_dates(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with _stub(monkeypatch, tmp_path, "valid"):
        profile, first, task = _generate(catalog)
        assert _run(catalog, task)["state"] == "succeeded"
        _, claim = _claim_item(catalog)

        def repeat_documented(previous: dict[str, Any]) -> dict[str, Any]:
            attempt = _write(
                catalog,
                _get_application(catalog, previous),
                "repeat",
                {"confirmed": True},
                expected=201,
            )
            task = _queue_pair(catalog, _preview(catalog, profile, attempt))
            assert _run(catalog, task)["state"] == "succeeded"
            return _get_application(catalog, attempt)

        second = repeat_documented(first)
        third = repeat_documented(second)
        first = _stage(catalog, _get_application(catalog, first), "interview")
        first = _stage(catalog, first, "applied")  # ordinary backward move preserves the peak
        _write(catalog, first, "archive", {"archived": True})
        second = _stage(catalog, _get_application(catalog, second), "interview")
        _stage(catalog, second, "rejected")
        third = _stage(catalog, third, "withdrawn")
        # An application without a checked document for this claim is not a cohort member.
        unrelated = _application(catalog, title="Unrelated checked-document-free role")
        _stage(catalog, unrelated, "accepted")
        at_three = _usage(catalog, claim["id"])["outcomes"]
        assert at_three["application_count"] == 3
        assert at_three["archived_application_count"] == 1
        assert at_three["no_progression_count"] == 1
        assert at_three["inferred_applied_from_rejection"] == 0
        assert [stage["furthest_count"] for stage in at_three["stages"]] == [0, 0, 2, 0, 0]
        assert [stage["reached_count"] for stage in at_three["stages"]] == [2, 2, 2, 0, 0]
        assert [stage["rate"] for stage in at_three["stages"]] == [2 / 3, 2 / 3, 2 / 3, 0, 0]

        # Old documents remain pinned while later attempts cite the new claim revision.
        assert _edit_claim(catalog).status_code == 200
        profile = catalog.client.get("/api/v1/profile").json()
        fourth = repeat_documented(third)
        fourth = _stage(catalog, fourth, "rejected")
        at_four = _usage(catalog, claim["id"])["outcomes"]
        assert at_four["application_count"] == 4 and at_four["small_sample"]
        assert at_four["inferred_applied_from_rejection"] == 1
        fifth = repeat_documented(fourth)
        _stage(catalog, fifth, "accepted", "2099-01-01T00:00:00Z")
        usage = _usage(catalog, claim["id"])
        at_five = usage["outcomes"]
        assert len({item["claim_revision_id"] for item in usage["items"]}) == 2
        assert at_five["application_count"] == at_five["small_sample_threshold"] == 5
        assert at_five["small_sample"] is False
        assert at_five["archived_application_count"] == 1
        assert at_five["no_progression_count"] == 1
        assert at_five["inferred_applied_from_rejection"] == 1
        assert [stage["furthest_count"] for stage in at_five["stages"]] == [1, 0, 2, 0, 1]
        assert [stage["reached_count"] for stage in at_five["stages"]] == [4, 3, 3, 1, 1]
        assert [stage["rate"] for stage in at_five["stages"]] == [4 / 5, 3 / 5, 3 / 5, 1 / 5, 1 / 5]
        assert (
            at_five["no_progression_count"]
            + sum(stage["furthest_count"] for stage in at_five["stages"])
            == at_five["application_count"]
        )
        assert _usage(catalog, claim["id"], history=True)["outcomes"] == at_five


@pytest.mark.postgres
def test_outcomes_recompute_corrected_peaks_and_rejection_inference_without_writes(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, application = _pair(catalog, monkeypatch, tmp_path, "valid")
    _, claim = _claim_item(catalog)
    application = _stage(catalog, _get_application(catalog, application), "interview")
    interview_id = application["current_stage_event_id"]
    application = _stage(catalog, application, "applied")
    applied_id = application["current_stage_event_id"]
    application = _stage(catalog, application, "rejected")
    rejected_id = application["current_stage_event_id"]
    assert [
        stage["furthest_count"] for stage in _usage(catalog, claim["id"])["outcomes"]["stages"]
    ] == [0, 0, 1, 0, 0]
    application = _stage(catalog, application, "offer", correction_of=interview_id)
    offer_id = _history(catalog, application)["items"][0]["id"]
    offer = _usage(catalog, claim["id"])["outcomes"]
    assert [stage["furthest_count"] for stage in offer["stages"]] == [0, 0, 0, 1, 0]
    assert [stage["reached_count"] for stage in offer["stages"]] == [1, 1, 1, 1, 0]
    application = _stage(catalog, application, "withdrawn", correction_of=offer_id)
    # Correcting the replacement does not revive the original interview event.
    applied = _usage(catalog, claim["id"])["outcomes"]
    assert [stage["furthest_count"] for stage in applied["stages"]] == [1, 0, 0, 0, 0]
    assert applied["inferred_applied_from_rejection"] == 0
    application = _stage(catalog, application, "preparing", correction_of=applied_id)
    inferred = _usage(catalog, claim["id"])["outcomes"]
    assert [stage["furthest_count"] for stage in inferred["stages"]] == [1, 0, 0, 0, 0]
    assert inferred["inferred_applied_from_rejection"] == 1
    application = _stage(catalog, application, "withdrawn", correction_of=rejected_id)
    no_progression = _usage(catalog, claim["id"])["outcomes"]
    assert no_progression["application_count"] == no_progression["no_progression_count"] == 1
    assert no_progression["inferred_applied_from_rejection"] == 0
    assert all(
        stage["furthest_count"] == stage["reached_count"] == stage["rate"] == 0
        for stage in no_progression["stages"]
    )
    application = _stage(catalog, application, "screening", "2099-02-01T00:00:00Z")
    before_history = _history(catalog, application)
    before_documents = _state(catalog)
    before_profile = catalog.client.get("/api/v1/profile").json()
    for history in (False, True, False):
        current = _usage(catalog, claim["id"], history=history)["outcomes"]
        assert [stage["furthest_count"] for stage in current["stages"]] == [0, 1, 0, 0, 0]
        assert [stage["reached_count"] for stage in current["stages"]] == [1, 1, 0, 0, 0]
        assert current["inferred_applied_from_rejection"] == 0
    assert _history(catalog, application) == before_history
    assert _state(catalog) == before_documents
    assert catalog.client.get("/api/v1/profile").json() == before_profile


@pytest.mark.postgres
def test_usage_routes_are_owner_scoped(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, application = _pair(catalog, monkeypatch, tmp_path, "valid")
    _, claim = _claim_item(catalog)
    for path in (f"/api/v1/claims/{uuid4()}/usage", f"/api/v1/applications/{uuid4()}/claims"):
        response = catalog.client.get(path)
        assert response.status_code == 404 and response.json()["code"] == "not_found"
    # The installation bootstraps one owner; another owner's id must not see these rows.
    other = uuid4()
    with catalog.runtime.transaction() as conn:
        with pytest.raises(Problem) as claim_refused:
            claim_usage.claim_usage(conn, other, UUID(claim["id"]), history=True)
        with pytest.raises(Problem) as application_refused:
            claim_usage.application_claims(conn, other, UUID(application["id"]))
    assert claim_refused.value.status == application_refused.value.status == 404


@pytest.mark.postgres
def test_failure_after_pair_link_insert_leaves_nothing(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    before = _state(catalog)
    with _stub(monkeypatch, tmp_path, "valid"), _fail_after_link_insert() as fired:
        _, application, task = _generate(catalog)
        assert _run(catalog, task)["state"] != "succeeded"
    assert fired
    assert _state(catalog) == before
    assert _pair_documents(catalog, application)["items"] == []


@pytest.mark.postgres
def test_failure_after_manual_save_link_insert_leaves_nothing(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, application = _pair(catalog, monkeypatch, tmp_path, "valid")
    resume = next(
        item for item in _pair_documents(catalog, application)["items"] if item["kind"] == "resume"
    )
    before = _state(catalog)
    with _fail_after_link_insert() as fired:
        response = _save(catalog, resume, resume["current_revision"]["body"])
    assert response.status_code == 500 and fired
    assert _state(catalog) == before


@pytest.mark.postgres
def test_failure_after_synthetic_base_link_insert_leaves_nothing(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile, _ = _pair(catalog, monkeypatch, tmp_path, "valid")
    revision = profile["current_revision"]["id"]
    before = _state(catalog)
    with (
        _fail_after_link_insert() as fired,
        pytest.raises(InjectedFailure),
        catalog.runtime.transaction() as conn,
    ):
        prepared = documents.prepare_synthetic(conn, catalog.owner_id, revision)
        documents.publish_document(conn, catalog.owner_id, uuid4(), prepared)
    assert fired
    assert _state(catalog) == before
