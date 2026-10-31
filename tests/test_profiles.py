# SPDX-License-Identifier: MIT OR Apache-2.0
"""evidence-linked profiles regressions against the authenticated API and real PostgreSQL only."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from test_identity import IdentityContext, _bootstrap
from test_identity import identity as identity

from bonafides.documents import prepare_synthetic, publish_document
from bonafides.portable_data import collect_owner_data, quarantine_records
from bonafides.problems import Problem
from bonafides.profile_entries import evidence_revision, hydrate_claim
from bonafides.profiles import ProfileSnapshot, _save, canonical, content_hash


@pytest.fixture
def profile_owner(identity: IdentityContext) -> Iterator[IdentityContext]:
    yield identity
    # This fixture can publish derived documents. Remove only its own fixture pins
    # before the shared owner fixture removes immutable profile/evidence links.
    with identity.admin.transaction() as conn:
        owner = conn.execute(
            text("SELECT id FROM bonafides.owners WHERE login_name=:login"),
            {"login": identity.login_name},
        ).scalar_one_or_none()
        if owner is not None:
            conn.execute(
                text("DELETE FROM bonafides.document_claim_links WHERE owner_id=:owner"),
                {"owner": owner},
            )
            conn.execute(
                text("DELETE FROM bonafides.document_drafts WHERE owner_id=:owner"),
                {"owner": owner},
            )


def _json(response: httpx.Response) -> dict[str, Any]:
    return cast(dict[str, Any], response.json())


def _mutate(
    client: TestClient,
    context: IdentityContext,
    csrf: str,
    method: str,
    path: str,
    version: int,
    body: dict[str, object] | None = None,
    key: str | None = None,
) -> httpx.Response:
    return cast(
        httpx.Response,
        client.request(
            method,
            "/api/v1" + path,
            json=body,
            headers={
                "Origin": context.settings.origin,
                "X-CSRF-Token": csrf,
                "If-Match": f'"{version}"',
                "Idempotency-Key": key or uuid4().hex,
            },
        ),
    )


def _attest(
    client: TestClient, context: IdentityContext, csrf: str, version: int = 1
) -> dict[str, Any]:
    response = _mutate(
        client,
        context,
        csrf,
        "POST",
        "/evidence",
        version,
        {
            "payload": {
                "text": "Maintained Linux services",
                "source": "Synthetic owner attestation",
            },
            "provenance": {
                "source_type": "owner_statement",
                "privacy_class": "private",
                "employer_owned": False,
            },
        },
    )
    assert response.status_code == 200, response.text
    return _json(response)


def _claim_body(evidence_id: str, kind: str = "skill", **fields: str) -> dict[str, object]:
    return {
        "kind": kind,
        "payload": {"text": "Maintained Linux services", **fields},
        "evidence_links": [
            {
                "evidence_revision_id": evidence_id,
                "support_kind": "user_asserted",
                "excerpt": "Maintained Linux services",
            }
        ],
    }


def _add_claim(
    client: TestClient,
    context: IdentityContext,
    csrf: str,
    profile: dict[str, Any],
    kind: str = "skill",
    **fields: str,
) -> dict[str, Any]:
    evidence_id = profile["evidence"][0]["revision"]["id"]
    response = _mutate(
        client,
        context,
        csrf,
        "POST",
        "/claims",
        profile["version"],
        _claim_body(evidence_id, kind, **fields),
    )
    assert response.status_code == 200, response.text
    return _json(response)


@pytest.mark.postgres
@pytest.mark.parametrize("character", ["a", "🚀"])
def test_optional_profile_text_limits_clear_and_preserve_on_older_client_saves(
    profile_owner: IdentityContext, character: str
) -> None:
    with profile_owner.client() as client:
        boot = _bootstrap(profile_owner, client)
        csrf = str(_json(boot)["csrf_token"])
        optional = _mutate(client, profile_owner, csrf, "PUT", "/profile", 1, {})
        assert optional.status_code == 200, optional.text
        current = _json(optional)
        assert current["current_revision"]["snapshot"]["headline"] == ""
        assert current["current_revision"]["snapshot"]["description"] == ""
        body: dict[str, object] = {"headline": character * 300, "description": character * 2000}
        accepted = _mutate(client, profile_owner, csrf, "PUT", "/profile", current["version"], body)
        assert accepted.status_code == 200, accepted.text
        current = _json(accepted)
        assert current["current_revision"]["snapshot"]["headline"] == body["headline"]
        assert current["current_revision"]["snapshot"]["description"] == body["description"]
        for field, limit in (("headline", 300), ("description", 2000)):
            invalid = _mutate(
                client,
                profile_owner,
                csrf,
                "PUT",
                "/profile",
                current["version"],
                {**body, field: character * (limit + 1)},
            )
            assert invalid.status_code == 422, invalid.text
            if field == "description":
                assert invalid.json()["fields"][0]["loc"] == ["body", "description"]
            assert client.get("/api/v1/profile").json() == current
        key = uuid4().hex
        older_client = _mutate(
            client,
            profile_owner,
            csrf,
            "PUT",
            "/profile",
            current["version"],
            {"headline": body["headline"], "display_name": "Updated name"},
            key,
        )
        assert older_client.status_code == 200, older_client.text
        different_intent = _mutate(
            client,
            profile_owner,
            csrf,
            "PUT",
            "/profile",
            current["version"],
            {"headline": body["headline"], "display_name": "Updated name", "description": ""},
            key,
        )
        assert different_intent.status_code == 409
        current = _json(older_client)
        assert current["current_revision"]["snapshot"]["description"] == body["description"]
        cleared = _mutate(
            client,
            profile_owner,
            csrf,
            "PUT",
            "/profile",
            current["version"],
            {"headline": "", "description": ""},
        )
        assert cleared.status_code == 200, cleared.text
        assert _json(cleared)["current_revision"]["snapshot"]["headline"] == ""
        assert _json(cleared)["current_revision"]["snapshot"]["description"] == ""


@pytest.mark.postgres
def test_legacy_profile_text_survives_edits_portable_validation_and_historical_restore(
    profile_owner: IdentityContext,
) -> None:
    with profile_owner.client() as client:
        boot = _bootstrap(profile_owner, client)
        csrf, owner_id = str(_json(boot)["csrf_token"]), UUID(str(_json(boot)["owner_id"]))
        revision_id = uuid4()
        legacy = {
            "display_name": "Legacy owner",
            "headline": "🚀" * 300,
            "claim_revision_ids": [],
            "evidence_revision_ids": [],
        }
        digest = content_hash(legacy)
        # Insert an immutable pre-description snapshot, not a normalized model dump.
        with profile_owner.admin.transaction() as conn:
            profile_id = conn.execute(
                text("SELECT id FROM bonafides.profiles WHERE owner_id=:owner"),
                {"owner": owner_id},
            ).scalar_one()
            conn.execute(
                text("""INSERT INTO bonafides.profile_revisions
                (owner_id,id,profile_id,revision_no,snapshot,content_sha256,reason)
                VALUES(:owner,:id,:profile,2,CAST(:snapshot AS jsonb),:sha,'owner_save')"""),
                {
                    "owner": owner_id,
                    "id": revision_id,
                    "profile": profile_id,
                    "snapshot": canonical(legacy),
                    "sha": digest,
                },
            )
            conn.execute(
                text("""UPDATE bonafides.profiles SET current_revision_id=:id,version=version+1
                WHERE owner_id=:owner"""),
                {"owner": owner_id, "id": revision_id},
            )
        historical = client.get(f"/api/v1/profile/revisions/{revision_id}").json()
        assert historical["snapshot"] == legacy
        assert historical["content_sha256"] == digest
        current = client.get("/api/v1/profile").json()
        assert current["current_revision"]["snapshot"] == legacy
        edited = _mutate(
            client,
            profile_owner,
            csrf,
            "PUT",
            "/profile",
            current["version"],
            {**legacy, "description": "Owner biography, not a professional claim."},
        )
        assert edited.status_code == 200, edited.text
        current = _attest(client, profile_owner, csrf, _json(edited)["version"])
        current = _add_claim(client, profile_owner, csrf, current)
        assert current["current_revision"]["snapshot"]["headline"] == legacy["headline"]
        assert current["current_revision"]["snapshot"]["description"] == (
            "Owner biography, not a professional claim."
        )
        assert len(current["current_revision"]["claims"]) == 1
        with (
            profile_owner.admin.engine.connect().execution_options(
                isolation_level="REPEATABLE READ"
            ) as conn,
            conn.begin(),
        ):
            archive = collect_owner_data(conn, profile_owner.settings, owner_id)
            quarantine_records(conn, archive)
            exported = next(
                row
                for row in archive.manifest.records["profile_revisions"]
                if row["id"] == str(revision_id)
            )
            assert exported["snapshot"] == legacy
            assert exported["content_sha256"] == digest
        restored = _mutate(
            client,
            profile_owner,
            csrf,
            "POST",
            "/profile/restore",
            current["version"],
            {"revision_id": str(revision_id)},
        )
        assert restored.status_code == 200, restored.text
        snapshot = _json(restored)["current_revision"]["snapshot"]
        assert snapshot["headline"] == legacy["headline"]
        assert snapshot["description"] == ""
        assert client.get(f"/api/v1/profile/revisions/{revision_id}").json() == historical


@pytest.mark.postgres
def test_profile_wide_conflict_and_idempotent_replay(profile_owner: IdentityContext) -> None:
    with profile_owner.client() as client:
        boot = _bootstrap(profile_owner, client)
        assert boot.status_code == 201
        csrf = str(_json(boot)["csrf_token"])
        payload: dict[str, object] = {
            "payload": {"text": "Synthetic fact", "source": "Owner"},
            "provenance": {
                "source_type": "owner_statement",
                "privacy_class": "private",
                "employer_owned": False,
            },
        }
        key = "profile-evidence-idempotency-001"
        first = _mutate(client, profile_owner, csrf, "POST", "/evidence", 1, payload, key)
        assert first.status_code == 200
        replay = _mutate(client, profile_owner, csrf, "POST", "/evidence", 1, payload, key)
        assert replay.status_code == 200 and replay.json() == first.json()
        assert replay.headers["etag"] == first.headers["etag"] == '"2"'
        stale = _mutate(client, profile_owner, csrf, "POST", "/evidence", 1, payload)
        assert stale.status_code == 412 and _json(stale)["code"] == "version_conflict"
        mismatch = _mutate(client, profile_owner, csrf, "POST", "/evidence", 2, payload, key)
        assert mismatch.status_code == 409
        assert len(_json(client.get("/api/v1/evidence"))["items"]) == 1
        assert _json(client.get("/api/v1/profile"))["version"] == 2


@pytest.mark.postgres
def test_claim_delete_fences_late_updates_and_raw_snapshot_resurrection(
    profile_owner: IdentityContext,
) -> None:
    with profile_owner.client() as client:
        boot = _bootstrap(profile_owner, client)
        csrf = str(_json(boot)["csrf_token"])
        saved = _add_claim(client, profile_owner, csrf, _attest(client, profile_owner, csrf))
        claim = saved["current_revision"]["claims"][0]
        body = _claim_body(saved["evidence"][0]["revision"]["id"])
        deleted = _mutate(
            client, profile_owner, csrf, "DELETE", f"/claims/{claim['id']}", saved["version"]
        )
        assert deleted.status_code == 200
        current = _json(deleted)
        assert current["current_revision"]["snapshot"]["claim_revision_ids"] == []
        late = _mutate(
            client, profile_owner, csrf, "PUT", f"/claims/{claim['id']}", saved["version"], body
        )
        assert late.status_code == 412
        fresh_but_archived = _mutate(
            client, profile_owner, csrf, "PUT", f"/claims/{claim['id']}", current["version"], body
        )
        assert fresh_but_archived.status_code == 409
        resurrect = _mutate(
            client,
            profile_owner,
            csrf,
            "PUT",
            "/profile",
            current["version"],
            saved["current_revision"]["snapshot"],
        )
        assert resurrect.status_code == 422
        assert _json(client.get("/api/v1/claims"))["items"] == []
        historical = client.get(f"/api/v1/claims/{claim['id']}/revisions/{claim['revision']['id']}")
        assert historical.status_code == 200
        assert _json(historical)["payload"]["text"] == "Maintained Linux services"


@pytest.mark.postgres
def test_history_restore_preserves_displaced_revisions_exact_pins_and_authorship(
    profile_owner: IdentityContext,
) -> None:
    with profile_owner.client() as client:
        boot = _bootstrap(profile_owner, client)
        csrf, owner_id = str(_json(boot)["csrf_token"]), str(_json(boot)["owner_id"])
        identity_edit = _mutate(
            client,
            profile_owner,
            csrf,
            "PUT",
            "/profile",
            1,
            {"headline": "Local operations analyst", "description": "Owner-authored biography."},
        )
        assert identity_edit.status_code == 200, identity_edit.text
        initial = _add_claim(
            client,
            profile_owner,
            csrf,
            _attest(client, profile_owner, csrf, _json(identity_edit)["version"]),
            "experience",
            start_date="2022",
            end_date="2023-04",
            organization="Synthetic Labs",
            role="Operator",
        )
        old_revision_id = initial["current_revision"]["id"]
        assert initial["current_revision"]["snapshot"]["description"] == "Owner-authored biography."
        claim = initial["current_revision"]["claims"][0]
        immutable_claim = client.get(
            f"/api/v1/claims/{claim['id']}/revisions/{claim['revision']['id']}"
        ).json()
        historical = client.get(f"/api/v1/profile/revisions/{old_revision_id}").json()
        changed_body = _claim_body(
            initial["evidence"][0]["revision"]["id"],
            "experience",
            text="Wrote SQL runbooks",
            start_date="2024-02-29",
        )
        changed = _mutate(
            client,
            profile_owner,
            csrf,
            "PUT",
            f"/claims/{claim['id']}",
            initial["version"],
            changed_body,
        )
        assert changed.status_code == 200
        displaced_id = _json(changed)["current_revision"]["id"]
        # Ordinary snapshot writes cannot roll current pointers back to old revisions.
        rejected = _mutate(
            client,
            profile_owner,
            csrf,
            "PUT",
            "/profile",
            _json(changed)["version"],
            initial["current_revision"]["snapshot"],
        )
        assert rejected.status_code == 422
        deleted = _mutate(
            client,
            profile_owner,
            csrf,
            "DELETE",
            f"/claims/{claim['id']}",
            _json(changed)["version"],
        )
        assert deleted.status_code == 200
        restored = _mutate(
            client,
            profile_owner,
            csrf,
            "POST",
            "/profile/restore",
            _json(deleted)["version"],
            {"revision_id": old_revision_id},
        )
        assert restored.status_code == 200
        result = _json(restored)
        assert result["current_revision"]["id"] not in (old_revision_id, displaced_id)
        assert result["current_revision"]["snapshot"] == initial["current_revision"]["snapshot"]
        assert (
            result["current_revision"]["claims"][0]["revision"]["payload"]["start_date"] == "2022"
        )
        assert result["current_revision"]["claims"][0]["revision"]["author_owner_id"] == owner_id
        assert (
            result["current_revision"]["claims"][0]["revision"]["evidence_links"][0]["evidence"][
                "author_owner_id"
            ]
            == owner_id
        )
        assert client.get(f"/api/v1/profile/revisions/{old_revision_id}").json() == historical
        assert (
            client.get(f"/api/v1/claims/{claim['id']}/revisions/{claim['revision']['id']}").json()
            == immutable_claim
        )
        displaced = client.get(f"/api/v1/profile/revisions/{displaced_id}")
        assert _json(displaced)["claims"][0]["revision"]["payload"]["text"] == "Wrote SQL runbooks"


@pytest.mark.postgres
def test_dates_reject_impossible_or_reversed_intervals_without_losing_precision(
    profile_owner: IdentityContext,
) -> None:
    with profile_owner.client() as client:
        boot = _bootstrap(profile_owner, client)
        csrf = str(_json(boot)["csrf_token"])
        profile = _attest(client, profile_owner, csrf)
        evidence_id = profile["evidence"][0]["revision"]["id"]
        for fields, field in [
            ({"start_date": "2024-02-30"}, "start_date"),
            ({"start_date": "2025", "end_date": "2024-12"}, "end_date"),
            ({"start_date": "2024-13"}, "start_date"),
        ]:
            invalid = _mutate(
                client,
                profile_owner,
                csrf,
                "POST",
                "/claims",
                profile["version"],
                _claim_body(evidence_id, "experience", **fields),
            )
            assert invalid.status_code == 422
            assert field in invalid.text
            assert "Maintained Linux services" not in invalid.text
            assert _json(client.get("/api/v1/profile"))["version"] == profile["version"]
        # Overlapping imprecise intervals are not definitely reversed; preserve input.
        accepted = _add_claim(
            client,
            profile_owner,
            csrf,
            profile,
            "experience",
            start_date="2024-08",
            end_date="2024",
        )
        payload = accepted["current_revision"]["claims"][0]["revision"]["payload"]
        assert payload["start_date"] == "2024-08" and payload["end_date"] == "2024"
        version = accepted["version"]
        forbidden_author = _claim_body(evidence_id)
        forbidden_author["author_owner_id"] = str(uuid4())
        rejected = _mutate(
            client, profile_owner, csrf, "POST", "/claims", version, forbidden_author
        )
        assert rejected.status_code == 422


@pytest.mark.postgres
def test_evidence_isolation_archive_and_exact_revision_attribution(
    profile_owner: IdentityContext,
) -> None:
    with profile_owner.client() as client:
        boot = _bootstrap(profile_owner, client)
        csrf, owner_id = str(_json(boot)["csrf_token"]), UUID(str(_json(boot)["owner_id"]))
        initial = _add_claim(client, profile_owner, csrf, _attest(client, profile_owner, csrf))
        evidence = initial["evidence"][0]
        claim = initial["current_revision"]["claims"][0]
        original_evidence = evidence["revision"]
        edit = _mutate(
            client,
            profile_owner,
            csrf,
            "PUT",
            f"/evidence/{evidence['id']}",
            initial["version"],
            {
                "payload": {"text": "Revised owner assertion", "source": "New synthetic source"},
                "provenance": {
                    "source_type": "owner_statement",
                    "privacy_class": "private",
                    "employer_owned": False,
                },
            },
        )
        assert edit.status_code == 200
        pinned = _json(edit)["current_revision"]["claims"][0]["revision"]["evidence_links"][0]
        assert pinned["evidence"] == original_evidence
        deleted = _mutate(
            client,
            profile_owner,
            csrf,
            "DELETE",
            f"/evidence/{evidence['id']}",
            _json(edit)["version"],
        )
        assert deleted.status_code == 200 and _json(deleted)["evidence"] == []
        late = _mutate(
            client,
            profile_owner,
            csrf,
            "PUT",
            f"/evidence/{evidence['id']}",
            _json(edit)["version"],
            {
                "payload": {"text": "Delayed", "source": "Owner"},
                "provenance": {
                    "source_type": "owner_statement",
                    "privacy_class": "private",
                    "employer_owned": False,
                },
            },
        )
        assert late.status_code == 412
        fresh = _mutate(
            client,
            profile_owner,
            csrf,
            "PUT",
            f"/evidence/{evidence['id']}",
            _json(deleted)["version"],
            {
                "payload": {"text": "Delayed", "source": "Owner"},
                "provenance": {
                    "source_type": "owner_statement",
                    "privacy_class": "private",
                    "employer_owned": False,
                },
            },
        )
        assert fresh.status_code == 409
        attach_archived = _mutate(
            client,
            profile_owner,
            csrf,
            "POST",
            "/claims",
            _json(deleted)["version"],
            _claim_body(original_evidence["id"]),
        )
        assert attach_archived.status_code == 422
        unknown = _mutate(
            client,
            profile_owner,
            csrf,
            "POST",
            "/claims",
            _json(deleted)["version"],
            _claim_body(str(uuid4())),
        )
        assert unknown.status_code == 422
        with profile_owner.admin.transaction() as conn:
            for read, identifier in (
                (evidence_revision, original_evidence["id"]),
                (hydrate_claim, claim["revision"]["id"]),
            ):
                with pytest.raises(Problem) as missing:
                    read(conn, uuid4(), UUID(identifier))
                assert missing.value.status == 404
            assert (
                evidence_revision(conn, owner_id, UUID(original_evidence["id"]))
                == original_evidence
            )
        restore = _mutate(
            client,
            profile_owner,
            csrf,
            "POST",
            "/profile/restore",
            _json(deleted)["version"],
            {"revision_id": initial["current_revision"]["id"]},
        )
        assert restore.status_code == 200 and len(_json(restore)["evidence"]) == 1
        assert (
            _json(restore)["current_revision"]["claims"][0]["revision"]["evidence_links"][0][
                "evidence"
            ]
            == original_evidence
        )


@pytest.mark.postgres
def test_all_sections_reorder_and_history_cursor(profile_owner: IdentityContext) -> None:
    with profile_owner.client() as client:
        boot = _bootstrap(profile_owner, client)
        csrf, owner_id = str(_json(boot)["csrf_token"]), UUID(str(_json(boot)["owner_id"]))
        profile = _attest(client, profile_owner, csrf)
        for kind in ("skill", "experience", "project", "education", "language"):
            profile = _add_claim(client, profile_owner, csrf, profile, kind)
        assert {item["kind"] for item in profile["current_revision"]["claims"]} == {
            "skill",
            "experience",
            "project",
            "education",
            "language",
        }
        snapshot = dict(profile["current_revision"]["snapshot"])
        snapshot["claim_revision_ids"] = list(reversed(snapshot["claim_revision_ids"]))
        reordered = _mutate(
            client, profile_owner, csrf, "PUT", "/profile", profile["version"], snapshot
        )
        assert reordered.status_code == 200
        assert [
            claim["revision"]["id"] for claim in _json(reordered)["current_revision"]["claims"]
        ] == snapshot["claim_revision_ids"]
        # Empty revisions keep the pagination proof inexpensive and synthetic.
        with profile_owner.admin.transaction() as conn:
            for index in range(101):
                _save(
                    conn,
                    owner_id,
                    ProfileSnapshot(headline=f"Synthetic revision {index}"),
                    "owner_save",
                )
        first = _json(client.get("/api/v1/profile/revisions"))
        assert len(first["items"]) == 100 and first["next_cursor"] is not None
        assert "claims" not in first["items"][0]
        second = _json(client.get(f"/api/v1/profile/revisions?before={first['next_cursor']}"))
        assert second["items"] and second["next_cursor"] is None
        first_ids = {item["id"] for item in first["items"]}
        assert first_ids.isdisjoint({item["id"] for item in second["items"]})
        assert second["items"][-1]["revision_no"] == 1


@pytest.mark.postgres
def test_upstream_change_marks_document_stale_without_mutating_saved_body_or_pins(
    profile_owner: IdentityContext,
) -> None:
    with profile_owner.client() as client:
        boot = _bootstrap(profile_owner, client)
        csrf, owner_id = str(_json(boot)["csrf_token"]), UUID(str(_json(boot)["owner_id"]))
        initial = _add_claim(client, profile_owner, csrf, _attest(client, profile_owner, csrf))
        with profile_owner.admin.transaction() as conn:
            prepared = prepare_synthetic(conn, owner_id, UUID(initial["current_revision"]["id"]))
            document = publish_document(conn, owner_id, uuid4(), prepared)
            before = conn.execute(
                text(
                    "SELECT row_to_json(r) FROM bonafides.document_revisions r WHERE owner_id=:owner"
                ),
                {"owner": owner_id},
            ).scalar_one()
            pins_before = (
                conn.execute(
                    text(
                        "SELECT row_to_json(l) FROM bonafides.document_claim_links l WHERE owner_id=:owner ORDER BY id"
                    ),
                    {"owner": owner_id},
                )
                .scalars()
                .all()
            )
        document_id = document["document_id"]
        current = _json(client.get(f"/api/v1/documents/{document_id}"))
        assert current["stale"] is False and current["stale_reason"] is None
        evidence_id = initial["evidence"][0]["id"]
        deleted = _mutate(
            client, profile_owner, csrf, "DELETE", f"/evidence/{evidence_id}", initial["version"]
        )
        assert deleted.status_code == 200
        stale = _json(client.get(f"/api/v1/documents/{document_id}"))
        assert (
            stale["stale"] is True and stale["stale_reason"] == "upstream_profile_revision_changed"
        )
        assert stale["current_revision"] == current["current_revision"]
        assert stale["unsupported_claims"] == current["unsupported_claims"] == []
        with profile_owner.admin.transaction() as conn:
            after = conn.execute(
                text(
                    "SELECT row_to_json(r) FROM bonafides.document_revisions r WHERE owner_id=:owner"
                ),
                {"owner": owner_id},
            ).scalar_one()
            pins_after = (
                conn.execute(
                    text(
                        "SELECT row_to_json(l) FROM bonafides.document_claim_links l WHERE owner_id=:owner ORDER BY id"
                    ),
                    {"owner": owner_id},
                )
                .scalars()
                .all()
            )
            assert before == after and pins_before == pins_after


@pytest.mark.postgres
def test_unattached_evidence_history_restore_and_post_delete_replay(
    profile_owner: IdentityContext,
) -> None:
    with profile_owner.client() as client:
        boot = _bootstrap(profile_owner, client)
        csrf = str(_json(boot)["csrf_token"])
        body: dict[str, object] = {
            "payload": {"text": "Independent synthetic evidence", "source": "Owner"},
            "provenance": {
                "source_type": "owner_statement",
                "privacy_class": "private",
                "employer_owned": False,
            },
        }
        key = "standalone-evidence-replay-001"
        created = _mutate(client, profile_owner, csrf, "POST", "/evidence", 1, body, key)
        assert created.status_code == 200
        initial = _json(created)
        evidence = initial["evidence"][0]
        revision_id = initial["current_revision"]["id"]
        detail_before = client.get(f"/api/v1/profile/revisions/{revision_id}").json()
        assert detail_before["claims"] == []
        assert detail_before["evidence"][0]["revision"] == evidence["revision"]
        changed = _mutate(
            client,
            profile_owner,
            csrf,
            "PUT",
            f"/evidence/{evidence['id']}",
            initial["version"],
            {
                "payload": {"text": "Later revision", "source": "Owner"},
                "provenance": {
                    "source_type": "owner_statement",
                    "privacy_class": "private",
                    "employer_owned": False,
                },
            },
        )
        assert changed.status_code == 200
        deleted = _mutate(
            client,
            profile_owner,
            csrf,
            "DELETE",
            f"/evidence/{evidence['id']}",
            _json(changed)["version"],
        )
        assert deleted.status_code == 200
        replay = _mutate(client, profile_owner, csrf, "POST", "/evidence", 1, body, key)
        assert replay.json() == initial
        assert _json(client.get("/api/v1/profile"))["evidence"] == []
        assert client.get(f"/api/v1/profile/revisions/{revision_id}").json() == detail_before
        restored = _mutate(
            client,
            profile_owner,
            csrf,
            "POST",
            "/profile/restore",
            _json(deleted)["version"],
            {"revision_id": revision_id},
        )
        assert restored.status_code == 200
        assert _json(restored)["evidence"][0]["revision"] == evidence["revision"]
        assert (
            _json(restored)["current_revision"]["snapshot"]
            == initial["current_revision"]["snapshot"]
        )
        with profile_owner.admin.transaction() as conn:
            owner_id = UUID(str(_json(boot)["owner_id"]))
            pins = (
                conn.execute(
                    text(
                        "SELECT evidence_revision_id FROM bonafides.profile_revision_evidence WHERE owner_id=:owner AND profile_revision_id=:revision"
                    ),
                    {"owner": owner_id, "revision": UUID(revision_id)},
                )
                .scalars()
                .all()
            )
            assert pins == [UUID(evidence["revision"]["id"])]


@pytest.mark.postgres
def test_experience_coverage_merges_overlaps_without_inventing_unknown_dates(
    profile_owner: IdentityContext,
) -> None:
    with profile_owner.client() as client:
        boot = _bootstrap(profile_owner, client)
        csrf = str(_json(boot)["csrf_token"])
        profile = _attest(client, profile_owner, csrf)
        for start, end in (
            ("2024-01-01", "2024-01-31"),
            ("2024-01-15", "2024-02-14"),
            ("2024-01-20", "2024-01-25"),
            ("2024-02-29", "2024-03-01"),
        ):
            profile = _add_claim(
                client,
                profile_owner,
                csrf,
                profile,
                "experience",
                start_date=start,
                end_date=end,
            )
        profile = _add_claim(client, profile_owner, csrf, profile, "experience")
        profile = _add_claim(
            client,
            profile_owner,
            csrf,
            profile,
            "education",
            start_date="2000",
            end_date="2025",
        )
        before = profile["current_revision"]
        reloaded = _json(client.get("/api/v1/profile"))
        assert reloaded["experience_coverage"]["minimum_days"] == 47
        assert reloaded["experience_coverage"]["maximum_days"] == 47
        assert reloaded["experience_coverage"]["undated_or_open_entries"] == 1
        assert reloaded["current_revision"] == before
        profile = _add_claim(
            client,
            profile_owner,
            csrf,
            profile,
            "experience",
            start_date="2023",
            end_date="2023",
        )
        assert profile["experience_coverage"]["minimum_days"] == 47
        assert profile["experience_coverage"]["maximum_days"] == 412
        assert (
            profile["current_revision"]["claims"][-1]["revision"]["payload"]["start_date"] == "2023"
        )


@pytest.mark.postgres
def test_project_link_is_syntactic_and_pinned_without_loading_its_target(
    profile_owner: IdentityContext,
) -> None:
    with profile_owner.client() as client:
        boot = _bootstrap(profile_owner, client)
        csrf = str(_json(boot)["csrf_token"])
        profile = _attest(client, profile_owner, csrf)
        evidence_id = profile["evidence"][0]["revision"]["id"]
        for invalid in (
            "javascript:alert(1)",
            "file:///etc/passwd",
            "https://",
            "https://user:pass@example.test/",
            "https://example.test/\nsecret",
        ):
            response = _mutate(
                client,
                profile_owner,
                csrf,
                "POST",
                "/claims",
                profile["version"],
                _claim_body(evidence_id, "project", project_url=invalid),
            )
            assert response.status_code == 422
            assert _json(client.get("/api/v1/profile"))["version"] == profile["version"]
        profile = _add_claim(
            client,
            profile_owner,
            csrf,
            profile,
            "project",
            project_url="https://127.0.0.1:1/private-project",
        )
        claim = profile["current_revision"]["claims"][0]
        assert claim["kind"] == "project"
        assert claim["revision"]["payload"]["project_url"] == "https://127.0.0.1:1/private-project"
        reloaded = _json(client.get("/api/v1/profile"))
        assert reloaded["current_revision"] == profile["current_revision"]
