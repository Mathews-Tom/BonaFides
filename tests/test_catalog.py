# SPDX-License-Identifier: MIT OR Apache-2.0
"""OL-008 uses real PostgreSQL, authenticated HTTP and independent retained-row oracles."""

from __future__ import annotations

import base64
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from threading import Event
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
import pytest
from psycopg.errors import ObjectNotInPrerequisiteState
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_identity import identity as identity
from test_sources import MANUAL, BoardClient, SourceContext, _job, _source_body
from test_sources import catalog as catalog

from offerloom import catalog as catalog_api
from offerloom import sources
from offerloom.commands import canonical_json
from offerloom.crypto import Keyring
from offerloom.greenhouse import BoardRead, SourceFailure
from offerloom.problems import Problem
from offerloom.workflows import claim


def _manual(context: SourceContext, **changes: object) -> dict[str, Any]:
    response = context.client.post(
        "/api/v1/postings", json={**MANUAL, **changes}, headers=context.headers()
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def _detail(context: SourceContext, posting: dict[str, Any]) -> dict[str, Any]:
    response = context.client.get("/api/v1/postings/" + posting["id"])
    assert response.status_code == 200, response.text
    assert response.headers["etag"] == f'"{response.json()["version"]}"'
    return cast(dict[str, Any], response.json())


def _disposition(context: SourceContext, posting: dict[str, Any], value: str) -> dict[str, Any]:
    response = context.client.put(
        f"/api/v1/postings/{posting['id']}/disposition",
        json={"disposition": value},
        headers=context.headers(posting["version"]),
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def _ids(context: SourceContext, **filters: object) -> set[str]:
    response = context.client.get("/api/v1/postings", params=cast(Any, filters))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["query_elapsed_ms"] >= 0
    assert datetime.fromisoformat(result["as_of"]).tzinfo is not None
    return {item["id"] for item in result["items"]}


@pytest.mark.postgres
def test_real_fts_boolean_phrase_simple_vector_and_literal_filters(catalog: SourceContext) -> None:
    first = _manual(
        catalog,
        title="Signal Operations Engineer",
        company="Reedglass Labs",
        location_text="Remote 100%_Team",
        description_text="Maintain Linux procedures and query incident records with SQL.",
    )
    second = _manual(
        catalog,
        title="Database Reliability Engineer",
        company="Reedglass Labs",
        location_text="London",
        description_text="PostgreSQL Linux distributed systems",
    )
    third = _manual(
        catalog,
        title="Signal Operations Engineer",
        company="Other employer",
        location_text="Berlin",
        description_text="Windows only",
    )
    fourth = _manual(
        catalog,
        title="Running specialist",
        location_text="Edinburgh",
        description_text="Running databases",
    )
    assert _ids(catalog, q="Linux") == {first["id"], second["id"]}
    assert _ids(catalog, q="Linux SQL") == {first["id"]}
    assert _ids(catalog, q='"distributed systems"') == {second["id"]}
    assert _ids(catalog, q="Linux OR Windows") == {first["id"], second["id"], third["id"]}
    assert _ids(catalog, q="Linux -PostgreSQL") == {first["id"]}
    assert _ids(catalog, q="Reedglass") == {first["id"], second["id"]}
    assert _ids(catalog, q="London") == {second["id"]}
    assert _ids(catalog, q="run") == set()  # simple is explicitly not English stemming.
    assert _ids(catalog, q="Running") == {fourth["id"]}
    assert _ids(catalog, q="-") == set()
    assert _ids(catalog, q="nonexistent' OR '1'='1") == set()
    assert _ids(catalog, location="remote") == {first["id"]}
    assert _ids(catalog, location="%_") == {first["id"]}
    assert _ids(catalog, location="%") == {first["id"]}
    assert _ids(catalog, location="\\") == set()
    with catalog.identity.admin.transaction() as conn:
        conn.execute(
            text("""UPDATE offerloom.job_postings SET status='closed',version=version+1,
            last_observed_at=timestamptz '2026-01-01 00:00:00+00' WHERE owner_id=:owner AND id=:id"""),
            {"owner": catalog.owner_id, "id": UUID(first["id"])},
        )
    assert _ids(catalog, status="closed") == {first["id"]}
    assert _ids(catalog, q="Linux", status="open", location="london") == {second["id"]}
    assert _ids(catalog, source_id=first["source_config_id"]) == {
        first["id"],
        second["id"],
        third["id"],
        fourth["id"],
    }
    assert _ids(catalog, source_id=str(uuid4())) == set()
    assert first["id"] not in _ids(catalog, observed_after="2026-02-01T00:00:00Z")
    assert _ids(catalog, observed_after="9999-01-01T00:00:00Z") == set()
    assert _ids(catalog, q="Linux", disposition="unmarked", location="london") == {second["id"]}


@pytest.mark.postgres
@pytest.mark.parametrize(
    "filters",
    [
        {"q": "x" * 301},
        {"q": "bad\x00text"},
        {"location": "x" * 2001},
        {"location": "bad\x00text"},
        {"status": "applied"},
        {"disposition": "applied"},
        {"source_id": "not-a-uuid"},
        {"observed_after": "2026-01-01T00:00:00"},
        {"observed_after": "not-a-date"},
        {"limit": 0},
        {"limit": 101},
        {"owner_id": str(uuid4())},
    ],
)
def test_catalog_filter_validation_is_explicit(
    catalog: SourceContext, filters: dict[str, object]
) -> None:
    response = catalog.client.get("/api/v1/postings", params=cast(Any, filters))
    assert response.status_code == 422, response.text


@pytest.mark.postgres
def test_signed_cursor_ties_cutoff_filter_binding_owner_and_key_rotation(
    catalog: SourceContext,
) -> None:
    identifiers = [uuid4() for _ in range(5)]
    tied = datetime(2026, 1, 1, tzinfo=UTC)
    with catalog.identity.admin.transaction() as conn:
        for identifier in identifiers:
            conn.execute(
                text("""INSERT INTO offerloom.job_postings
                (owner_id,id,title,company,description_text,dedupe_key,created_at)
                VALUES(:owner,:id,'Tie','Reedglass Labs','Linux',:dedupe,:created)"""),
                {
                    "owner": catalog.owner_id,
                    "id": identifier,
                    "dedupe": "SYN-TIE:" + str(identifier),
                    "created": tied,
                },
            )
    first = catalog.client.get("/api/v1/postings", params={"q": "Linux", "limit": 2}).json()
    cursor = first["next_cursor"]
    assert cursor and first["items"][0]["source_kind"] is None
    assert first["items"][0]["disposition"] is None
    legacy = _detail(catalog, first["items"][0])
    assert legacy["source_config_id"] is None and legacy["last_observed_at"] is None
    added = _manual(catalog, description_text="Linux")
    second = catalog.client.get(
        "/api/v1/postings", params={"q": "Linux", "cursor": cursor, "limit": 1}
    ).json()
    third = catalog.client.get(
        "/api/v1/postings", params={"q": "Linux", "cursor": second["next_cursor"], "limit": 100}
    ).json()
    seen = [item["id"] for page in (first, second, third) for item in page["items"]]
    assert seen == [str(value) for value in sorted(identifiers, reverse=True)]
    assert added["id"] not in seen and third["next_cursor"] is None
    assert first["as_of"] == second["as_of"] == third["as_of"]
    for changed in (
        {"q": "SQL"},
        {"q": "Linux", "location": "Remote"},
        {"q": "Linux", "status": "open"},
        {"q": "Linux", "source_id": str(uuid4())},
        {"q": "Linux", "disposition": "saved"},
        {"q": "Linux", "observed_after": "2026-01-01T00:00:00Z"},
    ):
        response = catalog.client.get("/api/v1/postings", params={**changed, "cursor": cursor})
        assert response.status_code == 400 and response.json()["code"] == "invalid_cursor"
    payload, signature = cursor.split(".")
    decoded = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    decoded["id"] = str(uuid4())
    tampered = (
        base64.urlsafe_b64encode(json.dumps(decoded).encode()).decode().rstrip("=")
        + "."
        + signature
    )
    for invalid in ("not-a-cursor", tampered, payload + "." + "A" * 43, cursor + "=", payload):
        response = catalog.client.get("/api/v1/postings", params={"q": "Linux", "cursor": invalid})
        assert response.status_code == 400, response.text
    query = catalog_api.CatalogQuery(q="Linux", cursor=cursor)
    with catalog.runtime.transaction() as conn:
        with pytest.raises(Problem) as error:
            catalog_api.list_postings(conn, uuid4(), catalog.identity.settings.keyring, query)
        assert error.value.status == 400
        original = catalog.identity.settings.keyring
        rotated = Keyring("new", {**original.keys, "new": b"N" * 32})
        page = catalog_api.list_postings(conn, catalog.owner_id, rotated, query)
        assert len(cast(list[object], page["items"])) == 3
        with pytest.raises(Problem) as error:
            catalog_api.list_postings(
                conn, catalog.owner_id, Keyring("new", {"new": b"N" * 32}), query
            )
        assert error.value.code == "invalid_cursor"


@pytest.mark.postgres
def test_disposition_persists_independent_application_and_source_replay(
    catalog: SourceContext,
) -> None:
    source = catalog.create()
    assert catalog.refresh(source).status_code == 202
    catalog.run(BoardClient())
    posting = catalog.postings()[0]
    saved = _disposition(catalog, posting, "saved")
    assert saved["disposition"] == "saved" and saved["version"] == posting["version"] + 1
    with catalog.identity.admin.transaction() as conn:
        opportunity = conn.execute(
            text(
                "SELECT id FROM offerloom.saved_opportunities WHERE owner_id=:owner AND posting_id=:posting"
            ),
            {"owner": catalog.owner_id, "posting": UUID(posting["id"])},
        ).scalar_one()
        application = uuid4()
        conn.execute(
            text(
                "INSERT INTO offerloom.applications(owner_id,id,opportunity_id,current_stage) VALUES(:owner,:id,:opportunity,'interview')"
            ),
            {"owner": catalog.owner_id, "id": application, "opportunity": opportunity},
        )
    for value in ("dismissed", "archived", "unmarked", "saved"):
        saved = _disposition(catalog, saved, value)
        assert _detail(catalog, posting)["disposition"] == (None if value == "unmarked" else value)
        assert _ids(catalog, disposition=value) == {posting["id"]}
        with catalog.runtime.transaction() as conn:
            retained = (
                conn.execute(
                    text("""SELECT o.id,o.disposition,a.current_stage FROM offerloom.saved_opportunities o
                JOIN offerloom.applications a ON a.owner_id=o.owner_id AND a.opportunity_id=o.id
                WHERE o.owner_id=:owner AND o.posting_id=:posting"""),
                    {"owner": catalog.owner_id, "posting": UUID(posting["id"])},
                )
                .mappings()
                .one()
            )
            assert retained["id"] == opportunity and retained["disposition"] == value
            assert retained["current_stage"] == "interview"
    catalog.allow_now(source["id"])
    assert catalog.refresh(catalog.source(source["id"])).status_code == 202
    catalog.run(BoardClient(BoardRead((_job(description="Changed Linux content"),), 0, 3)))
    refreshed = _detail(catalog, posting)
    assert refreshed["id"] == posting["id"] and refreshed["disposition"] == "saved"
    assert refreshed["version"] == saved["version"] + 1
    assert len(catalog.history(posting["id"])) == 2
    with catalog.identity.client() as fresh:
        fresh.cookies.update(catalog.client.cookies)
        assert fresh.get("/api/v1/postings/" + posting["id"]).json()["disposition"] == "saved"


@pytest.mark.postgres
def test_disposition_command_replay_cas_race_and_transactional_events(
    catalog: SourceContext,
) -> None:
    posting = _manual(catalog)
    path = f"/api/v1/postings/{posting['id']}/disposition"
    body = {"disposition": "saved"}
    assert catalog.client.put(path, json=body, headers=catalog.headers()).status_code == 428
    bad = catalog.headers(posting["version"])
    bad.pop("Idempotency-Key")
    assert catalog.client.put(path, json=body, headers=bad).status_code == 400
    key = uuid4().hex
    headers = catalog.headers(posting["version"], key)
    response = catalog.client.put(path, json=body, headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["etag"] == f'"{response.json()["version"]}"'
    assert catalog.client.put(path, json=body, headers=headers).json() == response.json()
    assert (
        catalog.client.put(path, json={"disposition": "dismissed"}, headers=headers).status_code
        == 409
    )
    assert (
        catalog.client.put(path, json=body, headers=catalog.headers(posting["version"])).status_code
        == 412
    )
    current = response.json()
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda value: catalog.client.put(
                    path, json={"disposition": value}, headers=catalog.headers(current["version"])
                ),
                ("archived", "dismissed"),
            )
        )
    assert sorted(item.status_code for item in responses) == [200, 412]
    winner = next(item.json() for item in responses if item.status_code == 200)
    assert _detail(catalog, posting) == winner
    assert len(catalog.history(posting["id"])) == 1
    with catalog.runtime.transaction() as conn:
        events = (
            conn.execute(
                text(
                    "SELECT payload FROM offerloom.outbox_events WHERE owner_id=:owner AND event_type='opportunity.disposition_changed' ORDER BY aggregate_version"
                ),
                {"owner": catalog.owner_id},
            )
            .scalars()
            .all()
        )
        assert len(events) == 2 and events[-1]["disposition"] == winner["disposition"]
        audits = (
            conn.execute(
                text(
                    "SELECT detail FROM offerloom.audit_events WHERE owner_id=:owner AND action='opportunity.disposition_changed'"
                ),
                {"owner": catalog.owner_id},
            )
            .scalars()
            .all()
        )
        assert len(audits) == 2
        assert cast(str, MANUAL["description_text"]) not in canonical_json([*events, *audits])


def _board_postings(context: SourceContext) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    source = context.create()
    different_location = _job("44", title="Signal Operations Engineer")
    snapshot = {**different_location.snapshot, "location": {"name": "Berlin"}}
    different_location = replace(
        different_location,
        location_text="Berlin",
        snapshot=snapshot,
        source_content_sha256=hashlib.sha256(canonical_json(snapshot).encode()).hexdigest(),
    )
    jobs = (
        _job("42", title="Signal Operations Engineer"),
        _job("43", title="Signal Operations Engineer"),
        different_location,
        _job("45", title="Different role"),
    )
    assert context.refresh(source).status_code == 202
    context.run(BoardClient(BoardRead(jobs, 0, 2)))
    return source, context.postings()


@pytest.mark.postgres
def test_conservative_candidates_distinct_external_ids_locations_and_persisted_review(
    catalog: SourceContext,
) -> None:
    source, board = _board_postings(catalog)
    manual = _manual(
        catalog,
        title=" \tSIGNAL   Operations Engineer\n",
        company="\tfictional acme \n",
        location_text="remote",
    )
    unrelated = _manual(catalog, title="Unrelated role")
    manual_same_source = _manual(
        catalog, title=manual["title"], company=manual["company"], location_text="remote"
    )
    path = f"/api/v1/postings/{manual['id']}/duplicates"
    page = catalog.client.get(path, params={"limit": 1}).json()
    assert len(page["items"]) == 1 and page["next_cursor"]
    next_page = catalog.client.get(path, params={"cursor": page["next_cursor"]}).json()
    candidates = page["items"] + next_page["items"]
    assert {item["external_id"] for item in candidates} == {"42", "43"}
    assert all(item["decision"] == "suspected" for item in candidates)
    assert all(item["source_config_id"] == source["id"] for item in candidates)
    assert manual_same_source["id"] not in {item["id"] for item in candidates}
    candidate = candidates[0]
    decision_path = path + f"/{candidate['id']}/decision"
    key = uuid4().hex
    headers = catalog.headers(manual["version"], key)
    response = catalog.client.post(decision_path, json={"decision": "distinct"}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["version"] == manual["version"] + 1
    assert (
        catalog.client.post(decision_path, json={"decision": "distinct"}, headers=headers).json()
        == response.json()
    )
    assert (
        catalog.client.post(
            decision_path, json={"decision": "suspected"}, headers=headers
        ).status_code
        == 409
    )
    assert (
        catalog.client.post(
            decision_path,
            json={"decision": "suspected"},
            headers=catalog.headers(manual["version"]),
        ).status_code
        == 412
    )
    reviewed = catalog.client.get(path).json()["items"]
    assert (
        next(item for item in reviewed if item["id"] == candidate["id"])["decision"] == "distinct"
    )
    reverse = catalog.client.get(f"/api/v1/postings/{candidate['id']}/duplicates").json()["items"]
    assert next(item for item in reverse if item["id"] == manual["id"])["decision"] == "distinct"
    rejected = catalog.client.post(
        path + f"/{unrelated['id']}/decision",
        json={"decision": "suspected"},
        headers=catalog.headers(response.json()["version"]),
    )
    assert rejected.status_code == 404
    assert (
        catalog.client.post(
            path + f"/{manual['id']}/decision",
            json={"decision": "suspected"},
            headers=catalog.headers(response.json()["version"]),
        ).status_code
        == 404
    )
    # A source update may change the hint, but must not discard an explicit owner review.
    with catalog.identity.admin.transaction() as conn:
        conn.execute(
            text(
                "UPDATE offerloom.job_postings SET title='Changed source title',version=version+1 WHERE owner_id=:owner AND id=:id"
            ),
            {"owner": catalog.owner_id, "id": UUID(candidate["id"])},
        )
    assert (
        next(
            item
            for item in catalog.client.get(path).json()["items"]
            if item["id"] == candidate["id"]
        )["decision"]
        == "distinct"
    )
    reverted = catalog.client.post(
        decision_path,
        json={"decision": "suspected"},
        headers=catalog.headers(response.json()["version"]),
    )
    assert reverted.status_code == 200
    assert (
        next(
            item
            for item in catalog.client.get(path).json()["items"]
            if item["id"] == candidate["id"]
        )["decision"]
        == "suspected"
    )
    assert len(catalog.postings()) == len(board) + 3
    assert len(catalog.history(candidate["id"])) == 1
    assert (
        catalog.client.get(
            f"/api/v1/postings/{unrelated['id']}/duplicates", params={"cursor": page["next_cursor"]}
        ).status_code
        == 400
    )
    assert (
        catalog.client.get("/api/v1/postings", params={"cursor": page["next_cursor"]}).status_code
        == 400
    )
    with catalog.runtime.transaction() as conn:
        pair = (
            conn.execute(
                text(
                    "SELECT posting_id,candidate_id,decision FROM offerloom.posting_duplicate_decisions WHERE owner_id=:owner"
                ),
                {"owner": catalog.owner_id},
            )
            .mappings()
            .one()
        )
        assert {str(pair["posting_id"]), str(pair["candidate_id"])} == {
            manual["id"],
            candidate["id"],
        }
        assert pair["decision"] == "suspected"
    assert (
        catalog.client.get(f"/api/v1/postings/{board[0]['id']}/duplicates?limit=101").status_code
        == 422
    )


@pytest.mark.postgres
@pytest.mark.parametrize("late_failure", [False, True])
def test_source_retirement_preserves_history_and_fences_delayed_worker(
    catalog: SourceContext, late_failure: bool
) -> None:
    source, board = _board_postings(catalog)
    posting = _disposition(catalog, board[0], "saved")
    history = catalog.history(posting["id"])
    application_id = uuid4()
    with catalog.identity.admin.transaction() as conn:
        opportunity_id = conn.execute(
            text(
                "SELECT id FROM offerloom.saved_opportunities WHERE owner_id=:owner AND posting_id=:posting"
            ),
            {"owner": catalog.owner_id, "posting": UUID(posting["id"])},
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO offerloom.applications(owner_id,id,opportunity_id,current_stage) VALUES(:owner,:id,:opportunity,'applied')"
            ),
            {"owner": catalog.owner_id, "id": application_id, "opportunity": opportunity_id},
        )
        conn.execute(
            text(
                "INSERT INTO offerloom.application_notes(owner_id,application_id,body) VALUES(:owner,:id,'Retained private note')"
            ),
            {"owner": catalog.owner_id, "id": application_id},
        )
        conn.execute(
            text(
                "INSERT INTO offerloom.application_stage_events(owner_id,application_id,to_stage,effective_at) VALUES(:owner,:id,'applied',clock_timestamp())"
            ),
            {"owner": catalog.owner_id, "id": application_id},
        )
    manual = _manual(
        catalog,
        title=posting["title"],
        company=posting["company"],
        location_text=posting["location_text"],
    )
    review = catalog.client.post(
        f"/api/v1/postings/{manual['id']}/duplicates/{posting['id']}/decision",
        json={"decision": "distinct"},
        headers=catalog.headers(manual["version"]),
    )
    assert review.status_code == 200
    reviewed_posting = _detail(catalog, posting)
    assert reviewed_posting["version"] == posting["version"] + 1
    posting = reviewed_posting
    catalog.allow_now(source["id"])
    assert catalog.refresh(catalog.source(source["id"])).status_code == 202
    with catalog.runtime.transaction() as conn:
        task = claim(conn, uuid4())
    assert task is not None
    current = catalog.source(source["id"])
    key = uuid4().hex
    headers = catalog.headers(current["version"], key)
    path = "/api/v1/sources/" + source["id"]
    retired_by_command: dict[str, Any] | None = None

    def retire_during_read() -> None:
        nonlocal retired_by_command
        result = catalog.client.delete(path, headers=headers)
        assert result.status_code == 200, result.text
        retired = result.json()
        assert retired["removed_at"] is not None and not retired["enabled"]
        assert retired["health"] == "retired"
        assert retired["version"] == current["version"] + 1
        assert catalog.client.delete(path, headers=headers).json() == retired
        retired_by_command = retired

    reader = BoardClient(
        BoardRead((_job("99", title="Must not publish"),), 0, 2),
        SourceFailure("source_rate_limited", 120) if late_failure else None,
        retire_during_read,
    )
    if late_failure:
        with pytest.raises(Problem) as rejected_failure:
            sources.execute_refresh(catalog.runtime, task, Event(), reader)
        assert rejected_failure.value.status == 409
        assert rejected_failure.value.code == "source_removed"
    else:
        with pytest.raises(DBAPIError) as rejected_publish:
            sources.execute_refresh(catalog.runtime, task, Event(), reader)
        error = rejected_publish.value.orig
        assert isinstance(error, ObjectNotInPrerequisiteState)
        assert error.sqlstate == "55000"
        assert error.diag.message_primary == "workflow lease fenced, cancelled, or expired"
    retired = catalog.source(source["id"])
    assert retired == retired_by_command
    assert reader.calls == 1 and retired["removed_at"] is not None
    assert catalog.workflow(task.id)["state"] == "cancelled"
    assert catalog.workflow(task.id)["result"] is None
    assert _detail(catalog, posting) == posting
    assert catalog.history(posting["id"]) == history
    assert len(catalog.postings()) == len(board) + 1
    assert _ids(catalog, source_id=source["id"]) == {item["id"] for item in board}
    assert (
        catalog.client.put(
            path, json=_source_body(), headers=catalog.headers(retired["version"])
        ).status_code
        == 409
    )
    assert catalog.refresh(retired).status_code == 409
    assert (
        catalog.client.delete(path, headers=catalog.headers(current["version"])).status_code == 412
    )
    assert (
        catalog.client.delete(path, headers=catalog.headers(retired["version"])).status_code == 409
    )
    sources_list = catalog.client.get("/api/v1/sources").json()["items"]
    assert (
        next(item for item in sources_list if item["id"] == source["id"])["removed_at"]
        == retired["removed_at"]
    )
    manual_source = catalog.source(manual["source_config_id"])
    cannot_remove = catalog.client.delete(
        "/api/v1/sources/" + manual["source_config_id"],
        headers=catalog.headers(manual_source["version"]),
    )
    assert cannot_remove.status_code == 409
    assert cannot_remove.json()["code"] == "manual_source_not_removable"
    with catalog.runtime.transaction() as conn:
        retained = conn.execute(
            text("""SELECT a.current_stage,n.body,e.to_stage FROM offerloom.applications a
            JOIN offerloom.application_notes n ON n.owner_id=a.owner_id AND n.application_id=a.id
            JOIN offerloom.application_stage_events e ON e.owner_id=a.owner_id AND e.application_id=a.id
            WHERE a.owner_id=:owner AND a.id=:id"""),
            {"owner": catalog.owner_id, "id": application_id},
        ).one()
        assert retained == ("applied", "Retained private note", "applied")
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM offerloom.source_policy_reviews WHERE owner_id=:owner AND source_config_id=:source"
                ),
                {"owner": catalog.owner_id, "source": UUID(source["id"])},
            ).scalar_one()
            == 1
        )
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM offerloom.posting_duplicate_decisions WHERE owner_id=:owner"
                ),
                {"owner": catalog.owner_id},
            ).scalar_one()
            == 1
        )
        assert conn.execute(
            text(
                "SELECT last_error_code,rate_limit_retry_after_seconds FROM offerloom.source_configs WHERE owner_id=:owner AND id=:source"
            ),
            {"owner": catalog.owner_id, "source": UUID(source["id"])},
        ).one() == (None, None)
    with pytest.raises(DBAPIError), catalog.runtime.transaction() as conn:
        conn.execute(
            text(
                "UPDATE offerloom.source_configs SET removed_at=NULL,version=version+1 WHERE owner_id=:owner AND id=:source"
            ),
            {"owner": catalog.owner_id, "source": UUID(source["id"])},
        )


@pytest.mark.postgres
def test_new_routes_auth_csrf_owner_and_candidate_boundaries(catalog: SourceContext) -> None:
    posting = _manual(catalog)
    stranger = str(uuid4())
    write_paths = (
        ("PUT", f"/api/v1/postings/{posting['id']}/disposition", {"disposition": "saved"}),
        (
            "POST",
            f"/api/v1/postings/{posting['id']}/duplicates/{stranger}/decision",
            {"decision": "distinct"},
        ),
        ("DELETE", "/api/v1/sources/" + posting["source_config_id"], None),
    )
    for method, path, body in write_paths:
        assert catalog.client.request(method, path, json=body).status_code == 403
        headers = catalog.headers(1)
        headers["Origin"] = "https://foreign.invalid"
        assert catalog.client.request(method, path, json=body, headers=headers).status_code == 403
    with catalog.identity.client() as anonymous:
        for path in ("/api/v1/postings", f"/api/v1/postings/{posting['id']}/duplicates"):
            assert anonymous.get(path).status_code == 401
        for method, path, body in write_paths:
            assert (
                anonymous.request(method, path, json=body, headers=catalog.headers(1)).status_code
                == 401
            )
    assert (
        catalog.client.put(
            f"/api/v1/postings/{stranger}/disposition",
            json={"disposition": "saved"},
            headers=catalog.headers(1),
        ).status_code
        == 404
    )
    assert (
        catalog.client.post(
            f"/api/v1/postings/{posting['id']}/duplicates/{stranger}/decision",
            json={"decision": "distinct"},
            headers=catalog.headers(posting["version"]),
        ).status_code
        == 404
    )
    assert (
        catalog.client.delete("/api/v1/sources/" + stranger, headers=catalog.headers(1)).status_code
        == 404
    )
    assert catalog.client.get(f"/api/v1/postings/{stranger}/duplicates").status_code == 404
    with catalog.runtime.transaction() as conn:
        assert (
            catalog_api.list_postings(
                conn, uuid4(), catalog.identity.settings.keyring, catalog_api.CatalogQuery()
            )["items"]
            == []
        )
        with pytest.raises(Problem) as error:
            catalog_api.list_duplicates(
                conn, uuid4(), UUID(posting["id"]), catalog.identity.settings.keyring, None, 20
            )
        assert error.value.status == 404
        with pytest.raises(Problem) as error:
            catalog_api.decide_duplicate(conn, uuid4(), UUID(posting["id"]), uuid4(), "distinct")
        assert error.value.status == 404


@pytest.mark.postgres
def test_retirement_refresh_race_cancels_every_accepted_run(catalog: SourceContext) -> None:
    source = catalog.create()
    with ThreadPoolExecutor(max_workers=2) as pool:
        removing = pool.submit(
            catalog.client.delete,
            "/api/v1/sources/" + source["id"],
            headers=catalog.headers(source["version"]),
        )
        refreshing = pool.submit(catalog.refresh, source)
        removed, queued = removing.result(), refreshing.result()
    assert removed.status_code == 200, removed.text
    assert queued.status_code in {202, 412}, queued.text
    if queued.status_code == 202:
        workflow = catalog.workflow(queued.json()["workflow_id"])
        assert workflow["state"] == "cancelled"
    with catalog.runtime.transaction() as conn:
        assert claim(conn, uuid4()) is None
    assert catalog.postings() == []


@pytest.mark.postgres
def test_duplicate_review_race_uses_posting_cas(catalog: SourceContext) -> None:
    _board_postings(catalog)
    posting = _manual(catalog, title="Signal Operations Engineer")
    candidates = catalog.client.get(f"/api/v1/postings/{posting['id']}/duplicates").json()["items"]
    assert len(candidates) == 2
    candidate_id = candidates[0]["id"]
    path = f"/api/v1/postings/{posting['id']}/duplicates/{candidate_id}/decision"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda decision: catalog.client.post(
                    path,
                    json={"decision": decision},
                    headers=catalog.headers(posting["version"]),
                ),
                ("suspected", "distinct"),
            )
        )
    assert sorted(result.status_code for result in results) == [200, 412]
    winner = next(result.json() for result in results if result.status_code == 200)
    assert _detail(catalog, posting)["version"] == winner["version"] == posting["version"] + 1
    with catalog.runtime.transaction() as conn:
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM offerloom.posting_duplicate_decisions WHERE owner_id=:owner"
                ),
                {"owner": catalog.owner_id},
            ).scalar_one()
            == 1
        )


@pytest.mark.postgres
@pytest.mark.parametrize("review_order", ["forward", "reverse", "concurrent"])
def test_duplicate_review_reverse_direction_fences_both_posting_tokens(
    catalog: SourceContext, review_order: str
) -> None:
    _board_postings(catalog)
    first = _manual(catalog, title="Signal Operations Engineer")
    candidates = catalog.client.get(f"/api/v1/postings/{first['id']}/duplicates").json()["items"]
    assert len(candidates) == 2
    second = _detail(catalog, candidates[0])
    postings = (first, second)
    histories = [catalog.history(posting["id"]) for posting in postings]
    reviews = [
        (first["id"], second["id"], "distinct", catalog.headers(first["version"])),
        (second["id"], first["id"], "suspected", catalog.headers(second["version"])),
    ]
    if review_order == "reverse":
        reviews.reverse()

    def send(review: tuple[str, str, str, dict[str, str]]) -> httpx.Response:
        posting_id, candidate_id, decision, headers = review
        return cast(
            httpx.Response,
            catalog.client.post(
                f"/api/v1/postings/{posting_id}/duplicates/{candidate_id}/decision",
                json={"decision": decision},
                headers=headers,
            ),
        )

    # Both requests retain the initial A/B tokens, even if the other direction commits first.
    if review_order == "concurrent":
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(send, reviews))
    else:
        results = [send(review) for review in reviews]
    assert sorted(result.status_code for result in results) == [200, 412]
    winner_index = next(index for index, result in enumerate(results) if result.status_code == 200)
    loser_index = 1 - winner_index
    assert results[loser_index].json()["code"] == "version_conflict"
    for posting in postings:
        assert _detail(catalog, posting)["version"] == posting["version"] + 1
    pair_query = text(
        "SELECT posting_id,candidate_id,decision,version FROM offerloom.posting_duplicate_decisions WHERE owner_id=:owner"
    )
    left, right = sorted(UUID(posting["id"]) for posting in postings)
    with catalog.runtime.transaction() as conn:
        assert conn.execute(pair_query, {"owner": catalog.owner_id}).one() == (
            left,
            right,
            reviews[winner_index][2],
            1,
        )

    # Explicitly refreshing the losing anchor permits an update of that same shared row.
    loser = reviews[loser_index]
    anchor = next(posting for posting in postings if posting["id"] == loser[0])
    accepted = send(
        (
            loser[0],
            loser[1],
            loser[2],
            catalog.headers(_detail(catalog, anchor)["version"]),
        )
    )
    assert accepted.status_code == 200, accepted.text
    with catalog.runtime.transaction() as conn:
        assert conn.execute(pair_query, {"owner": catalog.owner_id}).one() == (
            left,
            right,
            loser[2],
            2,
        )

    # Replaying the original successful command returns its original response, not a new write.
    replay = send(reviews[winner_index])
    assert replay.status_code == 200
    assert replay.json() == results[winner_index].json()
    assert replay.headers["etag"] == results[winner_index].headers["etag"]
    for posting, history in zip(postings, histories, strict=True):
        assert _detail(catalog, posting)["version"] == posting["version"] + 2
        assert catalog.history(posting["id"]) == history


@pytest.mark.postgres
def test_review_pair_database_constraints_and_history_privileges(catalog: SourceContext) -> None:
    first = _manual(catalog)
    second = _manual(catalog)
    left, right = sorted((UUID(first["id"]), UUID(second["id"])))
    statements = (
        (
            "INSERT INTO offerloom.posting_duplicate_decisions(owner_id,posting_id,candidate_id,decision) VALUES(:owner,:left,:left,'distinct')",
            {"owner": catalog.owner_id, "left": left},
        ),
        (
            "INSERT INTO offerloom.posting_duplicate_decisions(owner_id,posting_id,candidate_id,decision) VALUES(:owner,:right,:left,'distinct')",
            {"owner": catalog.owner_id, "left": left, "right": right},
        ),
        (
            "INSERT INTO offerloom.posting_duplicate_decisions(owner_id,posting_id,candidate_id,decision) VALUES(:owner,:left,:right,'merged')",
            {"owner": catalog.owner_id, "left": left, "right": right},
        ),
        (
            "DELETE FROM offerloom.posting_duplicate_decisions WHERE owner_id=:owner",
            {"owner": catalog.owner_id},
        ),
    )
    for sql, parameters in statements:
        with pytest.raises(DBAPIError), catalog.runtime.transaction() as conn:
            conn.execute(text(sql), parameters)
    assert _detail(catalog, first)["version"] == first["version"]
    assert len(catalog.postings()) == 2
