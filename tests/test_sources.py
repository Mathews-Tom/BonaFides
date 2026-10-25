# SPDX-License-Identifier: MIT OR Apache-2.0
"""Real PostgreSQL/API policy and catalog invariants, with explicit transport injection."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Event
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_identity import IdentityContext, _bootstrap
from test_identity import identity as identity

from offerloom import catalog as catalog_api
from offerloom import sources
from offerloom.commands import canonical_json
from offerloom.db import Database
from offerloom.greenhouse import BoardRead, PublishedJob, SourceFailure
from offerloom.problems import Problem
from offerloom.workflows import Claim, claim, enqueue, get_workflow

MANUAL: dict[str, object] = {
    "title": "Fictional infrastructure engineer",
    "company": "Fictional Acme",
    "location_text": "Remote",
    "description_text": "Original pasted text\n<script>inert()</script>",
    "canonical_url": "https://careers.fictional-employer.com/role/1",
}


def _source_body(**changes: object) -> dict[str, object]:
    return {
        "source_kind": "greenhouse",
        "board_token": "fictional-board",
        "company": "Fictional Acme",
        "terms_url": "https://careers.fictional-employer.com/terms",
        "terms_revision": "review-2026-10-25",
        "terms_review_acknowledged": True,
        "enabled": True,
        "request_limit_per_minute": 60,
        **changes,
    }


def _job(
    external_id: str = "42",
    description: str = "Original inert source text",
    title: str = "Fictional engineer",
) -> PublishedJob:
    snapshot: dict[str, object] = {
        "id": int(external_id),
        "internal_job_id": 123,
        "title": title,
        "location": {"name": "Remote"},
        "content": description,
        "absolute_url": "https://careers.fictional-employer.com/role/" + external_id,
        "updated_at": "2026-10-25T00:00:00Z",
    }
    sha = hashlib.sha256(canonical_json(snapshot).encode()).hexdigest()
    return PublishedJob(
        external_id,
        title,
        "Remote",
        description,
        cast(str, snapshot["absolute_url"]),
        "2026-10-25T00:00:00Z",
        sha,
        snapshot,
    )


@dataclass
class BoardClient:
    board: BoardRead = BoardRead((_job(),), 2, 15)
    failure: SourceFailure | None = None
    during_read: Callable[[], None] | None = None
    calls: int = 0

    def read(self, board_token: str, interrupted: Event | None = None) -> BoardRead:
        assert board_token in {"fictional-board", "other-board"}
        assert interrupted is not None and not interrupted.is_set()
        self.calls += 1
        if self.during_read is not None:
            self.during_read()
        if self.failure is not None:
            raise self.failure
        return self.board


@dataclass
class SourceContext:
    identity: IdentityContext
    client: TestClient
    csrf: str
    owner_id: UUID
    runtime: Database

    def headers(self, version: int | None = None, key: str | None = None) -> dict[str, str]:
        result = {
            "Origin": self.identity.settings.origin,
            "X-CSRF-Token": self.csrf,
            "Idempotency-Key": key or uuid4().hex,
        }
        if version is not None:
            result["If-Match"] = f'"{version}"'
        return result

    def source(self, source_id: str) -> dict[str, Any]:
        response = self.client.get("/api/v1/sources/" + source_id)
        assert response.status_code == 200, response.text
        assert response.headers["etag"] == f'"{response.json()["version"]}"'
        return cast(dict[str, Any], response.json())

    def create(self, **changes: object) -> dict[str, Any]:
        response = self.client.post(
            "/api/v1/sources", json=_source_body(**changes), headers=self.headers()
        )
        assert response.status_code == 201, response.text
        return cast(dict[str, Any], response.json())

    def refresh(self, source: dict[str, Any], key: str | None = None) -> httpx.Response:
        return cast(
            httpx.Response,
            self.client.post(
                f"/api/v1/sources/{source['id']}/refresh",
                json={},
                headers=self.headers(source["version"], key),
            ),
        )

    def allow_now(self, source_id: str) -> None:
        with self.identity.admin.transaction() as conn:
            conn.execute(
                text(
                    "UPDATE offerloom.source_configs SET next_allowed_at=clock_timestamp()-interval '1 second' WHERE owner_id=:owner AND id=:id"
                ),
                {"owner": self.owner_id, "id": UUID(source_id)},
            )

    def run(self, client: BoardClient) -> Claim:
        with self.runtime.transaction() as conn:
            task = claim(conn, uuid4())
        assert task is not None
        sources.execute_refresh(self.runtime, task, Event(), client)
        return task

    def workflow(self, workflow_id: UUID | str) -> dict[str, Any]:
        response = self.client.get("/api/v1/workflows/" + str(workflow_id))
        assert response.status_code == 200, response.text
        return cast(dict[str, Any], response.json())

    def postings(self) -> list[dict[str, Any]]:
        response = self.client.get("/api/v1/postings")
        assert response.status_code == 200
        return cast(list[dict[str, Any]], response.json()["items"])

    def history(self, posting_id: str) -> list[dict[str, Any]]:
        response = self.client.get(f"/api/v1/postings/{posting_id}/observations")
        assert response.status_code == 200
        return cast(list[dict[str, Any]], response.json()["items"])


@pytest.fixture
def catalog(identity: IdentityContext) -> Iterator[SourceContext]:
    runtime = Database(identity.settings)
    try:
        with identity.client() as client:
            assert _bootstrap(identity, client).status_code == 201
            session = client.get("/api/v1/session").json()
            yield SourceContext(
                identity,
                client,
                str(session["csrf_token"]),
                UUID(str(session["owner_id"])),
                runtime,
            )
    finally:
        runtime.close()


@pytest.mark.postgres
def test_manual_creation_is_independent_idempotent_exact_and_attributable(
    catalog: SourceContext,
) -> None:
    key = uuid4().hex
    response = catalog.client.post(
        "/api/v1/postings", json=MANUAL, headers=catalog.headers(key=key)
    )
    assert response.status_code == 201, response.text
    posting = response.json()
    assert (
        posting["source_kind"] == "manual"
        and posting["description_text"] == MANUAL["description_text"]
    )
    assert posting["version"] == 1 and posting["external_id"] == posting["id"]
    other = catalog.client.post("/api/v1/postings", json=MANUAL, headers=catalog.headers())
    assert other.status_code == 201 and other.json()["id"] != posting["id"]
    assert other.json()["source_config_id"] == posting["source_config_id"]
    replay = catalog.client.post("/api/v1/postings", json=MANUAL, headers=catalog.headers(key=key))
    assert replay.status_code == 201 and replay.json() == posting
    mismatch = catalog.client.post(
        "/api/v1/postings", json={**MANUAL, "title": "Changed"}, headers=catalog.headers(key=key)
    )
    assert mismatch.status_code == 409 and mismatch.json()["code"] == "idempotency_payload_mismatch"
    assert catalog.client.get("/api/v1/postings/" + posting["id"]).json() == posting
    history = catalog.history(posting["id"])
    assert len(history) == 1 and history[0]["source_kind"] == "manual"
    observation = catalog.client.get(
        f"/api/v1/postings/{posting['id']}/observations/{history[0]['id']}"
    ).json()
    assert observation["snapshot"] == MANUAL and observation["source_url"] == "manual:paste"
    assert observation["content_sha256"] == posting["content_sha256"]
    with catalog.runtime.transaction() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM offerloom.workflow_runs WHERE owner_id=:owner"),
                {"owner": catalog.owner_id},
            ).scalar_one()
            == 0
        )


@pytest.mark.postgres
def test_posting_cursor_is_stable_and_summaries_never_contain_source_bodies(
    catalog: SourceContext,
) -> None:
    ids = []
    for title in ("One", "Two", "Three"):
        response = catalog.client.post(
            "/api/v1/postings", json={**MANUAL, "title": title}, headers=catalog.headers()
        )
        assert response.status_code == 201
        ids.append(response.json()["id"])
    first = catalog.client.get("/api/v1/postings?limit=2").json()
    assert len(first["items"]) == 2 and first["next_cursor"]
    assert all(
        set(item)
        == {
            "id",
            "version",
            "title",
            "company",
            "location_text",
            "status",
            "created_at",
            "updated_at",
            "source_config_id",
            "source_kind",
            "external_id",
            "last_observed_at",
            "disposition",
        }
        for item in first["items"]
    )
    added = catalog.client.post(
        "/api/v1/postings", json={**MANUAL, "title": "New after cursor"}, headers=catalog.headers()
    ).json()
    second = catalog.client.get(
        "/api/v1/postings", params={"limit": 2, "cursor": first["next_cursor"]}
    ).json()
    seen = [item["id"] for item in first["items"] + second["items"]]
    assert set(seen) == set(ids) and len(seen) == 3 and added["id"] not in seen
    assert second["next_cursor"] is None
    assert catalog.client.get("/api/v1/postings?cursor=not-a-cursor").status_code == 400
    assert catalog.client.get("/api/v1/postings?limit=101").status_code == 422


@pytest.mark.postgres
@pytest.mark.parametrize(
    "changes",
    [
        {"canonical_url": "http://127.0.0.1/private"},
        {"canonical_url": "https://localhost/"},
        {"requirements": []},
        {"description_text": " "},
        {"title": "\x00"},
    ],
)
def test_manual_rejects_unsafe_or_out_of_scope_input(
    catalog: SourceContext, changes: dict[str, object]
) -> None:
    response = catalog.client.post(
        "/api/v1/postings", json={**MANUAL, **changes}, headers=catalog.headers()
    )
    assert response.status_code == 422
    assert catalog.postings() == []


@pytest.mark.postgres
def test_sources_default_disabled_require_exact_review_and_source_version(
    catalog: SourceContext,
) -> None:
    body = {
        "source_kind": "greenhouse",
        "board_token": "fictional-board",
        "company": "Fictional Acme",
    }
    response = catalog.client.post("/api/v1/sources", json=body, headers=catalog.headers())
    assert response.status_code == 201, response.text
    source = response.json()
    assert (
        source["enabled"] is False
        and source["health"] == "disabled"
        and source["terms_reviewed_at"] is None
    )
    assert source["quota"] == {"known": False, "limit": None}
    assert catalog.refresh(source).status_code == 409
    url = "/api/v1/sources/" + source["id"]
    assert (
        catalog.client.put(url, json=_source_body(), headers=catalog.headers()).status_code == 428
    )
    assert (
        catalog.client.put(
            url, json=_source_body(terms_review_acknowledged=False), headers=catalog.headers(1)
        ).status_code
        == 422
    )
    assert (
        catalog.client.put(
            url, json=_source_body(terms_revision=None), headers=catalog.headers(1)
        ).status_code
        == 422
    )
    enabled = catalog.client.put(url, json=_source_body(), headers=catalog.headers(1))
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["policy_version"] == 2 and enabled.json()["terms_reviewed_at"]
    assert (
        catalog.client.put(url, json=_source_body(), headers=catalog.headers(1)).status_code == 412
    )
    assert (
        catalog.client.put(
            url, json=_source_body(board_token="other-board"), headers=catalog.headers(2)
        ).status_code
        == 409
    )
    with catalog.runtime.transaction() as conn:
        reviews = (
            conn.execute(
                text(
                    "SELECT board_token,company,policy_version,acknowledged_by_owner_id FROM offerloom.source_policy_reviews WHERE owner_id=:owner"
                ),
                {"owner": catalog.owner_id},
            )
            .mappings()
            .all()
        )
        assert len(reviews) == 1 and reviews[0]["policy_version"] == 2
        assert reviews[0]["acknowledged_by_owner_id"] == catalog.owner_id


@pytest.mark.postgres
def test_refresh_idempotency_one_active_and_immutable_policy_pin(catalog: SourceContext) -> None:
    source = catalog.create()
    key = uuid4().hex
    queued = catalog.refresh(source, key)
    assert queued.status_code == 202, queued.text
    assert catalog.refresh(source, key).json() == queued.json()
    assert catalog.refresh(source).status_code == 409
    with catalog.runtime.transaction() as conn:
        workflow = get_workflow(conn, catalog.owner_id, UUID(queued.json()["workflow_id"]))
        assert workflow["max_attempts"] == 3 and workflow["attempt"] == 0
    with pytest.raises(DBAPIError), catalog.runtime.transaction() as conn:
        conn.execute(
            text(
                "UPDATE offerloom.workflow_runs SET max_attempts=4 WHERE owner_id=:owner AND id=:id"
            ),
            {"owner": catalog.owner_id, "id": UUID(queued.json()["workflow_id"])},
        )
    with pytest.raises(DBAPIError), catalog.runtime.transaction() as conn:
        enqueue(
            conn,
            catalog.owner_id,
            "source_refresh",
            {
                "source_id": source["id"],
                "policy_version": 1,
                "policy_review_id": str(
                    conn.execute(
                        text(
                            "SELECT current_policy_review_id FROM offerloom.source_configs WHERE id=:id"
                        ),
                        {"id": UUID(source["id"])},
                    ).scalar_one()
                ),
                "policy_sha256": "a" * 64,
            },
        )
    reader = BoardClient()
    task = catalog.run(reader)
    assert reader.calls == 1 and catalog.workflow(task.id)["state"] == "succeeded"
    assert catalog.refresh(source).status_code == 412


@pytest.mark.postgres
def test_concurrent_refresh_requests_reserve_only_one_workflow(catalog: SourceContext) -> None:
    source = catalog.create()
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: catalog.refresh(source), range(2)))
    assert sorted(response.status_code for response in responses) == [202, 409]
    with catalog.runtime.transaction() as conn:
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM offerloom.workflow_runs WHERE owner_id=:owner AND source_config_id=:source"
                ),
                {"owner": catalog.owner_id, "source": UUID(source["id"])},
            ).scalar_one()
            == 1
        )


@pytest.mark.postgres
def test_identical_replay_preserves_identity_changed_content_keeps_exact_history(
    catalog: SourceContext,
) -> None:
    source = catalog.create()
    assert catalog.refresh(source).status_code == 202
    first_task = catalog.run(BoardClient())
    result = catalog.workflow(first_task.id)["result"]
    assert (
        result["posting_count"] == 1
        and result["excluded_prospect_count"] == 2
        and result["new_posting_count"] == 1
    )
    posting = catalog.postings()[0]
    original = catalog.client.get("/api/v1/postings/" + posting["id"]).json()
    first_history = catalog.history(posting["id"])
    catalog.allow_now(source["id"])
    assert catalog.refresh(catalog.source(source["id"])).status_code == 202
    unchanged_task = catalog.run(BoardClient())
    unchanged = catalog.client.get("/api/v1/postings/" + posting["id"]).json()
    assert (
        unchanged["version"] == original["version"]
        and unchanged["updated_at"] == original["updated_at"]
    )
    assert unchanged["last_observed_at"] > original["last_observed_at"]
    assert catalog.history(posting["id"]) == first_history
    assert catalog.workflow(unchanged_task.id)["result"]["unchanged_posting_count"] == 1
    catalog.allow_now(source["id"])
    assert catalog.refresh(catalog.source(source["id"])).status_code == 202
    catalog.run(BoardClient(BoardRead((_job(description="Changed text"),), 0, 4)))
    changed = catalog.client.get("/api/v1/postings/" + posting["id"]).json()
    assert changed["id"] == original["id"] and changed["version"] == original["version"] + 1
    assert (
        changed["description_text"] == "Changed text"
        and changed["content_sha256"] != original["content_sha256"]
    )
    history = catalog.history(posting["id"])
    assert len(history) == 2
    old = catalog.client.get(
        f"/api/v1/postings/{posting['id']}/observations/{first_history[0]['id']}"
    ).json()
    assert old["snapshot"] == _job().snapshot
    assert old["content_sha256"] == original["content_sha256"] and old["policy_version"] == 1
    page = catalog.client.get(f"/api/v1/postings/{posting['id']}/observations?limit=1").json()
    next_page = catalog.client.get(
        f"/api/v1/postings/{posting['id']}/observations",
        params={"limit": 1, "cursor": page["next_cursor"]},
    ).json()
    assert {page["items"][0]["id"], next_page["items"][0]["id"]} == {item["id"] for item in history}
    assert "snapshot" not in page["items"][0]


@pytest.mark.postgres
def test_missing_posts_and_other_source_same_vendor_id_do_not_merge_or_erase(
    catalog: SourceContext,
) -> None:
    first = catalog.create()
    second = catalog.create(board_token="other-board", company="Other fictional employer")
    for source in (first, second):
        assert catalog.refresh(source).status_code == 202
        catalog.run(BoardClient())
    postings = catalog.postings()
    assert len(postings) == 2 and {item["company"] for item in postings} == {
        "Fictional Acme",
        "Other fictional employer",
    }
    first_id = next(item["id"] for item in postings if item["company"] == "Fictional Acme")
    history = catalog.history(first_id)
    catalog.allow_now(first["id"])
    assert catalog.refresh(catalog.source(first["id"])).status_code == 202
    catalog.run(BoardClient(BoardRead((), 0, 1)))
    assert len(catalog.postings()) == 2 and catalog.history(first_id) == history
    assert catalog.client.get("/api/v1/postings/" + first_id).json()["status"] == "open"


@pytest.mark.postgres
@pytest.mark.parametrize(
    "code,retry_after",
    [
        ("source_rate_limited", 3600),
        ("source_forbidden", None),
        ("source_incomplete_response", None),
    ],
)
def test_source_failure_retains_catalog_and_minimum_pause_across_workflow_exhaustion(
    catalog: SourceContext,
    code: str,
    retry_after: int | None,
) -> None:
    source = catalog.create()
    assert catalog.refresh(source).status_code == 202
    catalog.run(BoardClient())
    posting = catalog.postings()[0]
    before = catalog.history(posting["id"])
    catalog.allow_now(source["id"])
    assert catalog.refresh(catalog.source(source["id"])).status_code == 202
    failed = catalog.run(BoardClient(failure=SourceFailure(code, retry_after)))
    workflow = catalog.workflow(failed.id)
    assert workflow["state"] == "permanent_failure" and workflow["last_error_code"] == code
    assert workflow["result"] == {
        "source_id": source["id"],
        "policy_version": 1,
        "error_code": code,
    }
    current = catalog.source(source["id"])
    assert current["last_error_code"] == code and current["last_success_at"] is not None
    assert len(catalog.postings()) == 1 and catalog.history(posting["id"]) == before
    if retry_after is not None:
        assert current["health"] == "rate_limited"
        blocked = catalog.refresh(current)
        assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) >= 3599
        with catalog.runtime.transaction() as conn:
            remaining = conn.execute(
                text(
                    "SELECT extract(epoch FROM next_allowed_at-clock_timestamp()) FROM offerloom.source_configs WHERE id=:id"
                ),
                {"id": UUID(source["id"])},
            ).scalar_one()
            assert remaining >= 3599


@pytest.mark.postgres
def test_unrepresentable_retry_after_never_overflows_or_resends(catalog: SourceContext) -> None:
    source = catalog.create()
    assert catalog.refresh(source).status_code == 202
    minimum = 10**100
    task = catalog.run(BoardClient(failure=SourceFailure("source_rate_limited", minimum)))
    assert catalog.workflow(task.id)["state"] == "permanent_failure"
    current = catalog.source(source["id"])
    assert current["health"] == "rate_limited" and current["next_allowed_at"] is None
    assert current["rate_limit_blocked"] is True and current["retry_after_seconds"] == str(minimum)
    response = catalog.refresh(current)
    assert response.status_code == 429 and int(response.headers["retry-after"]) >= minimum


@pytest.mark.postgres
def test_worker_reserves_database_clock_interval_before_io_and_retries_no_earlier(
    catalog: SourceContext,
) -> None:
    source = catalog.create(request_limit_per_minute=1)
    assert catalog.refresh(source).status_code == 202

    def inspect_reservation() -> None:
        # A separate connection sees the committed reservation: no transaction spans I/O.
        with catalog.runtime.transaction() as conn:
            remaining = conn.execute(
                text(
                    "SELECT extract(epoch FROM next_allowed_at-clock_timestamp()) FROM offerloom.source_configs WHERE id=:id"
                ),
                {"id": UUID(source["id"])},
            ).scalar_one()
            assert remaining >= 59

    task = catalog.run(
        BoardClient(failure=SourceFailure("source_network_failed"), during_read=inspect_reservation)
    )
    workflow = catalog.workflow(task.id)
    assert workflow["state"] == "retryable_failure" and workflow["attempt"] == 1
    with catalog.runtime.transaction() as conn:
        remaining = conn.execute(
            text(
                "SELECT extract(epoch FROM next_attempt_at-clock_timestamp()) FROM offerloom.workflow_runs WHERE id=:id"
            ),
            {"id": task.id},
        ).scalar_one()
        assert remaining >= 59
        assert claim(conn, uuid4()) is None
    response = catalog.refresh(catalog.source(source["id"]))
    assert response.status_code == 429 and int(response.headers["retry-after"]) >= 59


@pytest.mark.postgres
def test_retry_exhaustion_keeps_source_pause_and_original_three_attempt_budget(
    catalog: SourceContext,
) -> None:
    source = catalog.create()
    response = catalog.refresh(source)
    assert response.status_code == 202
    workflow_id = UUID(response.json()["workflow_id"])
    for attempt in range(1, 4):
        if attempt > 1:
            # Advance only this fixture's persisted schedule after asserting each minimum below.
            catalog.allow_now(source["id"])
            with catalog.identity.admin.transaction() as conn:
                conn.execute(
                    text(
                        "UPDATE offerloom.workflow_runs SET next_attempt_at=clock_timestamp()-interval '1 second' WHERE id=:id"
                    ),
                    {"id": workflow_id},
                )
        task = catalog.run(BoardClient(failure=SourceFailure("source_rate_limited", 5)))
        workflow = catalog.workflow(task.id)
        assert workflow["attempt"] == attempt and workflow["max_attempts"] == 3
        assert workflow["state"] == ("permanent_failure" if attempt == 3 else "retryable_failure")
        with catalog.runtime.transaction() as conn:
            assert (
                conn.execute(
                    text(
                        "SELECT extract(epoch FROM next_allowed_at-clock_timestamp()) FROM offerloom.source_configs WHERE id=:id"
                    ),
                    {"id": UUID(source["id"])},
                ).scalar_one()
                >= 4
            )
    current = catalog.source(source["id"])
    blocked = catalog.refresh(current)
    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) >= 4
    with catalog.runtime.transaction() as conn:
        assert claim(conn, uuid4()) is None


@pytest.mark.postgres
def test_new_review_keeps_old_employer_evidence_and_disable_retains_jobs(
    catalog: SourceContext,
) -> None:
    source = catalog.create()
    assert catalog.refresh(source).status_code == 202
    catalog.run(BoardClient())
    posting_id = catalog.postings()[0]["id"]
    original_history = catalog.history(posting_id)
    current = catalog.source(source["id"])
    changed = catalog.client.put(
        "/api/v1/sources/" + source["id"],
        json=_source_body(company="Reviewed replacement employer", terms_revision="revision-two"),
        headers=catalog.headers(current["version"]),
    )
    assert changed.status_code == 200
    catalog.allow_now(source["id"])
    assert catalog.refresh(changed.json()).status_code == 202
    catalog.run(BoardClient())
    history = catalog.history(posting_id)
    assert len(history) == 2 and {item["policy_version"] for item in history} == {1, 2}
    assert original_history[0] in history
    with catalog.runtime.transaction() as conn:
        rows = conn.execute(
            text(
                "SELECT company,terms_revision FROM offerloom.source_policy_reviews WHERE owner_id=:owner ORDER BY policy_version"
            ),
            {"owner": catalog.owner_id},
        ).all()
        assert [tuple(row) for row in rows] == [
            ("Fictional Acme", "review-2026-10-25"),
            ("Reviewed replacement employer", "revision-two"),
        ]
    current = catalog.source(source["id"])
    disabled = catalog.client.put(
        "/api/v1/sources/" + source["id"],
        json=_source_body(
            company="Reviewed replacement employer", enabled=False, terms_review_acknowledged=False
        ),
        headers=catalog.headers(current["version"]),
    )
    assert disabled.status_code == 200 and disabled.json()["health"] == "disabled"
    assert catalog.refresh(disabled.json()).status_code == 409
    assert len(catalog.postings()) == 1 and catalog.history(posting_id) == history
    assert (
        catalog.client.get("/api/v1/postings/" + posting_id).json()["company"]
        == "Reviewed replacement employer"
    )


@pytest.mark.postgres
@pytest.mark.parametrize("mutation", ["disable", "review", "cancel", "epoch", "lease"])
@pytest.mark.parametrize("retry_after", [None, 3600, 10**100])
def test_disable_policy_cancel_and_epoch_changes_fence_late_network_results(
    catalog: SourceContext, mutation: str, retry_after: int | None
) -> None:
    source = catalog.create()
    queued = catalog.refresh(source)
    assert queued.status_code == 202
    workflow_id = queued.json()["workflow_id"]

    def mutate() -> None:
        if mutation in {"disable", "review"}:
            body = _source_body(
                enabled=mutation != "disable",
                terms_review_acknowledged=mutation != "disable",
                terms_revision="new exact review" if mutation == "review" else "review-2026-10-25",
            )
            response = catalog.client.put(
                "/api/v1/sources/" + source["id"],
                json=body,
                headers=catalog.headers(source["version"]),
            )
            assert response.status_code == 200, response.text
        elif mutation == "cancel":
            workflow = catalog.workflow(workflow_id)
            response = catalog.client.post(
                f"/api/v1/workflows/{workflow_id}/cancel",
                json={},
                headers=catalog.headers(workflow["version"]),
            )
            assert response.status_code == 200
        elif mutation == "lease":
            with catalog.identity.admin.transaction() as conn:
                conn.execute(
                    text(
                        "UPDATE offerloom.workflow_runs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=:id"
                    ),
                    {"id": UUID(workflow_id)},
                )
        else:
            with catalog.identity.admin.transaction() as conn:
                conn.execute(
                    text(
                        "UPDATE offerloom.installation_state SET maintenance_epoch=maintenance_epoch+1 WHERE singleton"
                    )
                )

    with pytest.raises((DBAPIError, Problem)):
        catalog.run(
            BoardClient(
                during_read=mutate,
                failure=(
                    SourceFailure("source_rate_limited", retry_after)
                    if retry_after is not None
                    else None
                ),
            )
        )
    assert catalog.postings() == []
    assert catalog.source(source["id"])["last_success_at"] is None
    if mutation not in {"epoch", "lease"}:
        assert catalog.workflow(workflow_id)["state"] == "cancelled"
    if retry_after is not None:
        paused = catalog.source(source["id"])
        assert paused["retry_after_seconds"] == str(retry_after)
        assert paused["last_error_code"] is None
        assert catalog.workflow(workflow_id)["result"] is None
        rereviewed = catalog.client.put(
            "/api/v1/sources/" + source["id"],
            json=_source_body(terms_revision="Reviewed after the delayed response"),
            headers=catalog.headers(paused["version"]),
        )
        assert rereviewed.status_code == 200, rereviewed.text
        blocked = catalog.refresh(rereviewed.json())
        assert blocked.status_code == 429
        assert int(blocked.headers["retry-after"]) >= retry_after - 1
        assert catalog.postings() == []


@pytest.mark.postgres
def test_publication_is_atomic_and_runtime_cannot_erase_history(catalog: SourceContext) -> None:
    source = catalog.create()
    assert catalog.refresh(source).status_code == 202
    bad = _job("43", title="x" * 501)
    with pytest.raises(DBAPIError):
        catalog.run(BoardClient(BoardRead((_job(), bad), 0, 2)))
    assert catalog.postings() == [] and catalog.source(source["id"])["last_success_at"] is None
    with catalog.runtime.transaction() as conn:
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM offerloom.job_source_observations WHERE owner_id=:owner"
                ),
                {"owner": catalog.owner_id},
            ).scalar_one()
            == 0
        )
    for table in (
        "source_configs",
        "source_policy_reviews",
        "job_postings",
        "job_source_observations",
    ):
        with pytest.raises(DBAPIError), catalog.runtime.transaction() as conn:
            conn.execute(
                text(f"DELETE FROM offerloom.{table} WHERE owner_id=:owner"),
                {"owner": catalog.owner_id},
            )
    with pytest.raises(DBAPIError), catalog.runtime.transaction() as conn:
        conn.execute(
            text(
                "UPDATE offerloom.source_policy_reviews SET company='relabeled' WHERE owner_id=:owner"
            ),
            {"owner": catalog.owner_id},
        )


@pytest.mark.postgres
def test_owner_scope_csrf_and_content_free_events(catalog: SourceContext) -> None:
    assert catalog.client.post("/api/v1/postings", json=MANUAL).status_code == 403
    source = catalog.create()
    assert catalog.refresh(source).status_code == 202
    catalog.run(BoardClient())
    posting = catalog.postings()[0]
    with catalog.runtime.transaction() as conn:
        for getter, identifier in (
            (sources.get_source, UUID(source["id"])),
            (catalog_api.get_posting, UUID(posting["id"])),
        ):
            with pytest.raises(Problem) as error:
                getter(conn, uuid4(), identifier)
            assert error.value.status == 404
        with pytest.raises(Problem) as error:
            sources.observations(conn, uuid4(), UUID(posting["id"]))
        assert error.value.status == 404
        events = (
            conn.execute(
                text("SELECT payload FROM offerloom.outbox_events WHERE owner_id=:owner"),
                {"owner": catalog.owner_id},
            )
            .scalars()
            .all()
        )
        audits = (
            conn.execute(
                text("SELECT detail FROM offerloom.audit_events WHERE owner_id=:owner"),
                {"owner": catalog.owner_id},
            )
            .scalars()
            .all()
        )
        serialized = canonical_json([*events, *audits])
        assert "Original inert source text" not in serialized and "Fictional Acme" not in serialized
        assert "fictional-board" not in serialized and source["id"] in serialized
