# SPDX-License-Identifier: MIT OR Apache-2.0
"""Claim-structured pair generation through the real adapter and the deterministic stub.

The stub route's qualification receipt is fixture-only; nothing here qualifies a model.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from threading import Event, Thread
from typing import Any, cast

import pytest
from sqlalchemy import text
from test_document_generation import (
    _claim,
    _document,
    _pair_documents,
    _preview,
    _queue_pair,
    _review,
    _setup_pair,
)
from test_identity import identity as identity
from test_sources import SourceContext
from test_sources import catalog as catalog
from test_suitability import _after_model_send

from bonafides import local_model, model_context, suitability
from bonafides.claims import contains_statement
from bonafides.worker import Worker
from bonafides.workflows import Claim
from tools import stub_model
from tools.stub_model import ABSENT_CLAIM_ID, StubModel, make_server
from tools.stub_model import route as write_route


@contextmanager
def _stub(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, scenario: str) -> Iterator[StubModel]:
    stub = StubModel(scenario)
    server = make_server("127.0.0.1", 0, stub)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        path = tmp_path / "stub-route.json"
        write_route(f"http://127.0.0.1:{server.server_port}", "127.0.0.1", path, 30)
        route = local_model.load_route(path)
        monkeypatch.setattr(local_model, "load_route", lambda _path: route)
        yield stub
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _run(context: SourceContext, task: Claim) -> dict[str, Any]:
    Worker(context.runtime, replace(context.identity.settings, mode="production"))._execute(
        task, Event()
    )
    return context.workflow(task.id)


def _generate(context: SourceContext) -> tuple[dict[str, Any], dict[str, Any], Claim]:
    profile, application = _setup_pair(context)
    task = _queue_pair(context, _preview(context, profile, application))
    return profile, application, task


def _render(context: SourceContext, document: dict[str, Any]) -> Any:
    revision = document["current_revision"]
    return context.client.post(
        f"/api/v1/documents/{document['id']}/renders",
        json={
            "revision_id": revision["id"],
            "content_sha256": revision["content_sha256"],
            "format": "pdf",
        },
        headers=context.headers(document["version"]),
    )


def _count(context: SourceContext, table: str) -> int:
    with context.runtime.transaction() as conn:
        return int(
            conn.execute(
                text(f"SELECT count(*) FROM bonafides.{table} WHERE owner_id=:owner"),
                {"owner": context.owner_id},
            ).scalar_one()
        )


def _factual_blocks(document: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        block
        for section in document["current_revision"]["body"]["sections"]
        for block in section["blocks"]
        if block["claim_kind"] == "factual"
    ]


@pytest.mark.parametrize(
    ("claim_text", "expected"),
    [
        ("Assisted the team that led the billing migration", "Led the billing migration"),
        ("Supported analysts who managed the vendor portfolio.", "Managed the vendor portfolio"),
        (
            "Helped the engineer who took ownership of the rotation",
            "Took ownership of the rotation",
        ),
        (
            "Worked on a squad that built and shipped the dashboard",
            "Built and shipped the dashboard",
        ),
    ],
)
def test_inflated_stub_cuts_its_claim_at_a_later_stronger_verb(
    claim_text: str, expected: str
) -> None:
    projection: dict[str, object] = {
        "posting": {},
        "claims": [{"id": "claim-1", "text": claim_text, "evidence_revision_ids": ["evidence-1"]}],
        "evidence": [{"id": "evidence-1", "text": claim_text}],
    }
    response = stub_model.respond(
        "inflated",
        {"messages": [{"content": ""}, {"content": json.dumps(projection)}]},
    )
    message = cast(dict[str, str], response["message"])
    output = json.loads(message["content"])
    block = output["documents"][0]["sections"][0]["blocks"][-1]
    assert block["claim_id"] == "claim-1"
    assert block["text"] == expected
    assert contains_statement(block["text"], claim_text)
    assert not claim_text.casefold().startswith(block["text"].casefold())


@pytest.mark.parametrize(
    "claim_text",
    [
        "Led the team that managed the migration",
        "Managed the rollout",
        "Migration project for the payments team",
        "Assisted the custom-built dashboard team",
        "Helped the team that custom-built the dashboard",
    ],
)
def test_inflated_stub_refuses_claims_without_a_later_stronger_verb(claim_text: str) -> None:
    projection: dict[str, object] = {
        "claims": [{"id": "claim-1", "text": claim_text, "evidence_revision_ids": ["evidence-1"]}],
        "evidence": [{"id": "evidence-1", "text": claim_text}],
    }
    with pytest.raises(stub_model.StubFailure, match="later verb stronger"):
        stub_model._inflated(projection)


@pytest.mark.postgres
def test_valid_stub_pair_links_every_factual_sentence_and_exports(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with _stub(monkeypatch, tmp_path, "valid") as stub:
        _, application, task = _generate(catalog)
        workflow = _run(catalog, task)
    assert workflow["state"] == "succeeded", workflow
    assert len(stub.requests) == 1
    items = _pair_documents(catalog, application)["items"]
    assert {item["kind"] for item in items} == {"resume", "cover_letter"}
    for document in items:
        provenance = document["current_revision"]["provenance_snapshot"]
        assert provenance["checker_version"] == "claim-evidence-v4"
        pinned = {fact["claim_revision_id"] for fact in provenance["facts"]}
        blocks = _factual_blocks(document)
        assert blocks and all(
            len(block["claim_revision_ids"]) == 1 and block["claim_revision_ids"][0] in pinned
            for block in blocks
        )
        assert document["current_revision"]["validation_issues"] == []
        reviewed = _review(catalog, document)
        assert reviewed.status_code == 200, reviewed.text
        rendered = _render(catalog, _document(catalog, document["id"]))
        assert rendered.status_code == 202, rendered.text


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("scenario", "code"),
    [
        ("absent-claim", "claim_not_in_profile"),
        ("posting-only-skill", "statement_not_in_claim"),
    ],
)
def test_fabricated_stub_statement_blocks_export_even_after_review(
    catalog: SourceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    scenario: str,
    code: str,
) -> None:
    with _stub(monkeypatch, tmp_path, scenario) as stub:
        _, application, task = _generate(catalog)
        workflow = _run(catalog, task)
    # A fabricated statement is a per-statement blocker, not a task failure or a retry.
    assert workflow["state"] == "succeeded", workflow
    assert len(stub.requests) == 1
    resume = next(
        item for item in _pair_documents(catalog, application)["items"] if item["kind"] == "resume"
    )
    issues = resume["current_revision"]["validation_issues"]
    assert [issue["code"] for issue in issues] == [code]
    blocked = next(
        block for block in _factual_blocks(resume) if block["text"] == issues[0]["statement"]
    )
    if scenario == "absent-claim":
        assert blocked["claim_revision_ids"] == [ABSENT_CLAIM_ID]
    else:
        assert blocked["text"].startswith("Hands-on experience with ")
    assert resume["unsupported_claims"] == [issues[0]["path"]]
    refused = _review(catalog, resume)
    assert refused.status_code == 409 and refused.json()["code"] == "unsupported_claims"
    rendered = _render(catalog, _document(catalog, resume["id"]))
    assert rendered.status_code == 409 and rendered.json()["code"] == "unsupported_claims"


@pytest.mark.postgres
def test_malformed_once_is_corrected_once_on_the_same_projection(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with _stub(monkeypatch, tmp_path, "malformed-once") as stub:
        _, application, task = _generate(catalog)
        workflow = _run(catalog, task)
    assert workflow["state"] == "succeeded", workflow
    first, second = stub.requests
    # The retry resends the identical pinned projection plus a code-only error summary.
    assert second["messages"][:2] == first["messages"]
    correction = second["messages"][2]["content"]
    assert "documents: too_short" in correction
    projection = first["messages"][1]["content"]
    assert all(
        claim["text"] not in correction
        for claim in cast(list[dict[str, Any]], json.loads(projection)["claims"])
    )
    items = _pair_documents(catalog, application)["items"]
    assert len(items) == 2
    with catalog.runtime.transaction() as conn:
        runs = conn.execute(
            text("""SELECT id,status FROM bonafides.model_runs
            WHERE owner_id=:owner AND workflow_run_id=:id ORDER BY id"""),
            {"owner": catalog.owner_id, "id": task.id},
        ).all()
    assert sorted(run.status for run in runs) == ["succeeded", "unknown_usage"]
    usage = items[0]["current_revision"]["provenance_snapshot"]["model_run"]["usage"]
    assert usage["corrected_model_run_id"] in {str(run.id) for run in runs}


@pytest.mark.postgres
@pytest.mark.parametrize("repeated", [False, True])
@pytest.mark.parametrize("value", [0.9, 1 << 64])
def test_noncanonical_output_gets_one_corrective_call(
    catalog: SourceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    repeated: bool,
    value: float | int,
) -> None:
    respond = stub_model.respond

    def noncanonical(scenario: str, request: dict[str, object]) -> dict[str, object]:
        envelope = respond(scenario, request)
        if repeated or len(cast(list[object], request["messages"])) == 2:
            message = cast(dict[str, object], envelope["message"])
            output = json.loads(str(message["content"]))
            output["confidence"] = value
            message["content"] = json.dumps(output)
        return envelope

    monkeypatch.setattr(stub_model, "respond", noncanonical)
    with _stub(monkeypatch, tmp_path, "valid") as stub:
        _, application, task = _generate(catalog)
        workflow = _run(catalog, task)
    assert len(stub.requests) == 2
    assert "output: noncanonical_json" in stub.requests[1]["messages"][2]["content"]
    if repeated:
        assert workflow["state"] == "permanent_failure", workflow
        assert workflow["last_error_code"] == "model_output_invalid_after_correction"
        assert _pair_documents(catalog, application)["items"] == []
        assert _count(catalog, "document_drafts") == 0
        assert _count(catalog, "local_model_route_blocks") == 1
    else:
        assert workflow["state"] == "succeeded", workflow
        assert len(_pair_documents(catalog, application)["items"]) == 2
        assert _count(catalog, "local_model_route_blocks") == 0


@pytest.mark.postgres
def test_busy_corrective_reservation_does_not_spend_the_correction(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    reserve = suitability._reserve_call
    attempts = 0

    def busy_once(*args: Any, **kwargs: Any) -> Any:
        nonlocal attempts
        if kwargs.get("correction"):
            attempts += 1
            if attempts == 1:
                return None
        return reserve(*args, **kwargs)

    monkeypatch.setattr(suitability, "_reserve_call", busy_once)
    with _stub(monkeypatch, tmp_path, "malformed-once") as stub:
        _, application, task = _generate(catalog)
        workflow = _run(catalog, task)
    assert attempts == 2
    assert len(stub.requests) == 2
    assert len(stub.requests[1]["messages"]) == 3
    assert workflow["state"] == "succeeded", workflow
    assert len(_pair_documents(catalog, application)["items"]) == 2
    with catalog.runtime.transaction() as conn:
        spent = conn.execute(
            text("""SELECT count(*) FROM bonafides.model_runs
            WHERE owner_id=:owner AND workflow_run_id=:id
            AND metadata->>'corrective_retry'='true'"""),
            {"owner": catalog.owner_id, "id": task.id},
        ).scalar_one()
    assert spent == 1


@pytest.mark.postgres
@pytest.mark.parametrize("scenario", ["malformed", "truncated"])
def test_failed_generation_creates_no_documents_or_applications(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, scenario: str
) -> None:
    with _stub(monkeypatch, tmp_path, scenario) as stub:
        _, application, task = _generate(catalog)
        applications = _count(catalog, "applications")
        workflow = _run(catalog, task)
    assert workflow["state"] == "permanent_failure", workflow
    if scenario == "malformed":
        assert workflow["last_error_code"] == "model_output_invalid_after_correction"
        assert len(stub.requests) == 2
    else:
        # Truncation is not a schema mismatch: no corrective call.
        assert workflow["last_error_code"] == "model_incomplete"
        assert len(stub.requests) == 1
    assert _pair_documents(catalog, application)["items"] == []
    assert _count(catalog, "document_drafts") == 0
    assert _count(catalog, "applications") == applications


@pytest.mark.postgres
@pytest.mark.parametrize("scenario", ["malformed-once", "malformed"])
def test_corrective_call_never_exhausts_the_ordinary_attempt_budget(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, scenario: str
) -> None:
    with _stub(monkeypatch, tmp_path, scenario) as stub:
        _, application, task = _generate(catalog)
        with catalog.runtime.transaction() as conn:
            # The primary call is the second ordinary attempt after a transient failure.
            conn.execute(
                text(
                    "UPDATE bonafides.workflow_runs SET model_network_attempts=1 WHERE owner_id=:owner AND id=:id"
                ),
                {"owner": catalog.owner_id, "id": task.id},
            )
        workflow = _run(catalog, task)
    assert len(stub.requests) == 2, (workflow["state"], workflow["last_error_code"])
    with catalog.runtime.transaction() as conn:
        attempts = conn.execute(
            text(
                "SELECT model_network_attempts FROM bonafides.workflow_runs WHERE owner_id=:owner AND id=:id"
            ),
            {"owner": catalog.owner_id, "id": task.id},
        ).scalar_one()
    # Only the primary call is an ordinary attempt; the correction is its own model run.
    assert attempts == 2
    if scenario == "malformed-once":
        assert workflow["state"] == "succeeded", workflow
        assert len(_pair_documents(catalog, application)["items"]) == 2
    else:
        assert workflow["last_error_code"] == "model_output_invalid_after_correction"


def _evidence_write(context: SourceContext) -> Any:
    current = context.client.get("/api/v1/profile").json()
    record = current["evidence"][0]
    return context.client.put(
        f"/api/v1/evidence/{record['id']}/classification",
        json={"privacy_class": "private", "employer_owned": True},
        headers=context.headers(current["version"]),
    )


@pytest.mark.postgres
def test_owner_send_lock_refuses_evidence_writers_during_the_corrective_call(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entered, released = Event(), Event()

    def hold_correction(number: int) -> None:
        if number == 2:
            entered.set()
            assert released.wait(30), "test must release the corrective call"

    with _stub(monkeypatch, tmp_path, "malformed-once") as stub:
        stub.on_chat = hold_correction
        _, application, task = _generate(catalog)
        with ThreadPoolExecutor(max_workers=1) as executor:
            running = executor.submit(_run, catalog, task)
            try:
                assert entered.wait(15), "worker must reach the corrective call"
                refused = _evidence_write(catalog)
                assert refused.status_code == 409
                assert refused.json()["code"] == "evidence_in_model_use"
            finally:
                released.set()
            workflow = running.result(timeout=30)
    assert workflow["state"] == "succeeded", workflow
    assert len(_pair_documents(catalog, application)["items"]) == 2
    assert _evidence_write(catalog).status_code == 200


def _claim_item(context: SourceContext) -> tuple[dict[str, Any], dict[str, Any]]:
    current = context.client.get("/api/v1/profile").json()
    return current, current["current_revision"]["claims"][0]


def _edit_claim(context: SourceContext) -> Any:
    current, item = _claim_item(context)
    revision = item["revision"]
    return context.client.put(
        f"/api/v1/claims/{item['id']}",
        json={
            "kind": item["kind"],
            "payload": revision["payload"],
            "evidence_links": [
                {key: link[key] for key in ("evidence_revision_id", "support_kind", "excerpt")}
                for link in revision["evidence_links"]
            ],
        },
        headers=context.headers(current["version"]),
    )


def _cited_evidence(context: SourceContext) -> tuple[dict[str, Any], dict[str, Any]]:
    current, item = _claim_item(context)
    cited = item["revision"]["evidence_links"][0]["evidence_revision_id"]
    record = next(entry for entry in current["evidence"] if entry["revision"]["id"] == cited)
    return current, record


def _edit_evidence(context: SourceContext) -> Any:
    current, record = _cited_evidence(context)
    return context.client.put(
        f"/api/v1/evidence/{record['id']}",
        json={
            "payload": record["revision"]["payload"],
            "provenance": {"source_type": "work_sample"},
        },
        headers=context.headers(current["version"]),
    )


def _reclassify(context: SourceContext) -> Any:
    current, record = _cited_evidence(context)
    return context.client.put(
        f"/api/v1/evidence/{record['id']}/classification",
        json={"privacy_class": "confidential", "employer_owned": True},
        headers=context.headers(current["version"]),
    )


def _archive_claim(context: SourceContext) -> Any:
    current, item = _claim_item(context)
    return context.client.delete(
        f"/api/v1/claims/{item['id']}", headers=context.headers(current["version"])
    )


def _archive_evidence(context: SourceContext) -> Any:
    current, record = _cited_evidence(context)
    return context.client.delete(
        f"/api/v1/evidence/{record['id']}", headers=context.headers(current["version"])
    )


def _restore(context: SourceContext, revision: str) -> Any:
    current = context.client.get("/api/v1/profile").json()
    return context.client.post(
        "/api/v1/profile/restore",
        json={"revision_id": revision},
        headers=context.headers(current["version"]),
    )


def _restore_older(context: SourceContext) -> Any:
    # A restore that drops the pinned claim revisions: a profile before any claim existed.
    with context.runtime.transaction() as conn:
        older = conn.execute(
            text("""SELECT id FROM bonafides.profile_revisions WHERE owner_id=:owner
            ORDER BY revision_no LIMIT 1"""),
            {"owner": context.owner_id},
        ).scalar_one()
    return _restore(context, str(older))


WRITES: dict[str, Callable[[SourceContext], Any]] = {
    "claim_edit": _edit_claim,
    "evidence_edit": _edit_evidence,
    "reclassify": _reclassify,
    "claim_archive": _archive_claim,
    "restore_older": _restore_older,
}


@pytest.mark.postgres
@pytest.mark.parametrize("timing", ["queued", "after_response"])
@pytest.mark.parametrize("write", sorted(WRITES))
def test_link_change_before_publish_fails_without_drafts(
    catalog: SourceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    write: str,
    timing: str,
) -> None:
    with _stub(monkeypatch, tmp_path, "valid") as stub:
        _, application, task = _generate(catalog)
        if timing == "queued":
            assert WRITES[write](catalog).status_code == 200
        else:
            _after_model_send(monkeypatch, lambda: WRITES[write](catalog).raise_for_status())
        workflow = _run(catalog, task)
    assert len(stub.requests) == (0 if timing == "queued" else 1)
    assert workflow["state"] == "permanent_failure", workflow
    assert workflow["last_error_code"] in {
        "stale_document_context",
        "profile_fact_withdrawn",
        "profile_evidence_unavailable",
    }
    assert _pair_documents(catalog, application)["items"] == []


def _published(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    with _stub(monkeypatch, tmp_path, "valid"):
        profile, application, task = _generate(catalog)
        assert _run(catalog, task)["state"] == "succeeded"
    resume = next(
        item for item in _pair_documents(catalog, application)["items"] if item["kind"] == "resume"
    )
    assert _review(catalog, resume).status_code == 200
    return profile, _document(catalog, resume["id"])


@pytest.mark.postgres
@pytest.mark.parametrize(
    "write", ["claim_edit", "evidence_edit", "claim_archive", "evidence_archive", "restore_older"]
)
def test_link_change_after_publish_refuses_export_until_pins_are_restored(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, write: str
) -> None:
    profile, resume = _published(catalog, monkeypatch, tmp_path)
    changes = {**WRITES, "evidence_archive": _archive_evidence}
    assert changes[write](catalog).status_code == 200
    refused = _render(catalog, _document(catalog, resume["id"]))
    assert refused.status_code == 409 and refused.json()["code"] == "document_source_withdrawn"
    letter = next(
        item
        for item in _pair_documents(catalog, {"id": resume["application_id"]})["items"]
        if item["kind"] == "cover_letter"
    )
    review = _review(catalog, letter)
    assert review.status_code == 409 and review.json()["code"] == "document_source_withdrawn"
    # Restoring exactly the pinned profile revision makes the reviewed revision exportable.
    assert _restore(catalog, profile["current_revision"]["id"]).status_code == 200
    assert _render(catalog, _document(catalog, resume["id"])).status_code == 202


@pytest.mark.postgres
def test_reclassification_after_publish_keeps_eligibility_and_check_result(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, resume = _published(catalog, monkeypatch, tmp_path)
    before = resume["current_revision"]["validation_issues"]
    assert _reclassify(catalog).status_code == 200
    after = _document(catalog, resume["id"])
    assert after["current_revision"]["validation_issues"] == before == []
    assert after["reviewed"] and not after["unsupported_claims"]
    assert _render(catalog, after).status_code == 202


@pytest.mark.postgres
def test_generation_beside_another_send_never_makes_writers_wait(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with _stub(monkeypatch, tmp_path, "valid"):
        _, application, task = _generate(catalog)
        with model_context.model_send_lock(catalog.runtime, catalog.owner_id):
            refused = _evidence_write(catalog)
            assert refused.status_code == 409
            assert refused.json()["code"] == "evidence_in_model_use"
            # The shared lock admits this generation while another send holds it.
            assert _run(catalog, task)["state"] == "succeeded"
    assert len(_pair_documents(catalog, application)["items"]) == 2
    assert _evidence_write(catalog).status_code == 200


@pytest.mark.postgres
def test_correction_is_spent_once_per_generation_across_worker_retries(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def drop_correction(number: int) -> None:
        if number == 2:
            raise ConnectionResetError("synthetic transport loss during the correction")

    with _stub(monkeypatch, tmp_path, "malformed") as stub:
        stub.on_chat = drop_correction
        _, application, task = _generate(catalog)
        first = _run(catalog, task)
        assert first["state"] == "retryable_failure", first
        with catalog.runtime.transaction() as conn:
            # Stand in for reconciliation of the lost request, then make the retry due.
            conn.execute(
                text("DELETE FROM bonafides.provider_executions WHERE owner_id=:owner"),
                {"owner": catalog.owner_id},
            )
            conn.execute(
                text(
                    "UPDATE bonafides.local_model_reservations SET reserved_until=clock_timestamp() WHERE singleton"
                )
            )
            conn.execute(
                text(
                    "UPDATE bonafides.workflow_runs SET next_attempt_at=clock_timestamp() WHERE owner_id=:owner AND id=:id"
                ),
                {"owner": catalog.owner_id, "id": task.id},
            )
        retried = _claim(catalog, str(task.id))
        workflow = _run(catalog, retried)
    # Primary, correction lost in transport, primary again: no second correction.
    assert len(stub.requests) == 3
    assert workflow["state"] == "permanent_failure", workflow
    assert workflow["last_error_code"] == "model_output_invalid_after_correction"
    assert _pair_documents(catalog, application)["items"] == []
