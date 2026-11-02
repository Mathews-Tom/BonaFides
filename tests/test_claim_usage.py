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
from test_document_generation import _base, _document, _pair_documents, _save
from test_identity import identity as identity
from test_sources import SourceContext
from test_sources import catalog as catalog

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
    assert {item["kind"] for item in usage["items"]} <= {"resume", "cover_letter"}


@pytest.mark.postgres
def test_edited_archived_and_restored_claims_keep_their_pinned_usage(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile, application = _pair(catalog, monkeypatch, tmp_path, "valid")
    _, claim = _claim_item(catalog)
    pinned = claim["revision"]["id"]
    before = _usage(catalog, claim["id"])["items"]
    assert before and all(not item["superseded"] for item in before)

    assert _edit_claim(catalog).status_code == 200
    edited = _usage(catalog, claim["id"])
    assert edited["current_revision_id"] != pinned
    assert [(item["document_id"], item["claim_revision_id"]) for item in edited["items"]] == [
        (item["document_id"], pinned) for item in before
    ]
    assert all(item["superseded"] and not item["archived"] for item in edited["items"])

    assert _archive_claim(catalog).status_code == 200
    archived = _usage(catalog, claim["id"])
    assert archived["archived"] and len(archived["items"]) == len(before)
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


@pytest.mark.postgres
def test_base_resume_usage_is_not_an_application(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, application = _pair(catalog, monkeypatch, tmp_path, "valid")
    base = _base(catalog, catalog.client.get("/api/v1/profile").json())
    _, claim = _claim_item(catalog)
    items = _usage(catalog, claim["id"])["items"]
    [entry] = [item for item in items if item["scope"] == "base"]
    assert entry["application"] is None and entry["document_id"] == base["id"]
    assert all(item["application"]["id"] == application["id"] for item in items if item != entry)
    assert all(
        item["document_id"] != base["id"] for item in _application_claims(catalog, application)
    )


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
