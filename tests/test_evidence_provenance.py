# SPDX-License-Identifier: MIT OR Apache-2.0
"""Evidence records carry source type, origin, privacy class and employer ownership."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_identity import IdentityContext, _bootstrap
from test_identity import identity as identity
from test_profiles import _claim_body, _json, _mutate
from test_profiles import profile_owner as profile_owner


def _evidence(
    source_type: str = "document", privacy_class: str = "private", employer_owned: bool = False
) -> dict[str, object]:
    return {
        "payload": {"text": "Maintained Linux services", "source": "Synthetic review"},
        "provenance": {
            "source_type": source_type,
            "privacy_class": privacy_class,
            "employer_owned": employer_owned,
        },
    }


@pytest.mark.postgres
def test_evidence_save_records_owner_provenance_and_owner_entry_origin(
    profile_owner: IdentityContext,
) -> None:
    with profile_owner.client() as client:
        csrf = str(_json(_bootstrap(profile_owner, client))["csrf_token"])
        created = _json(_mutate(client, profile_owner, csrf, "POST", "/evidence", 1, _evidence()))
        record: dict[str, Any] = created["evidence"][0]
        assert record["revision"]["provenance"] == {
            "source_type": "document",
            "origin": "owner_entry",
            "privacy_class": "private",
            "employer_owned": False,
        }
        updated = _json(
            _mutate(
                client,
                profile_owner,
                csrf,
                "PUT",
                f"/evidence/{record['id']}",
                created["version"],
                {"payload": _evidence()["payload"], "provenance": {"source_type": "work_sample"}},
            )
        )
        current: dict[str, Any] = updated["evidence"][0]
        assert current["revision"]["revision_no"] == 2
        assert current["revision"]["provenance"] == {
            "source_type": "work_sample",
            "origin": "owner_entry",
            "privacy_class": "private",
            "employer_owned": False,
        }
        first = _json(
            client.get(f"/api/v1/evidence/{record['id']}/revisions/{record['revision']['id']}")
        )
        assert first["provenance"]["privacy_class"] == "private"
        assert (
            _json(client.get("/api/v1/evidence"))["items"][0]["revision"]["provenance"]
            == (current["revision"]["provenance"])
        )


@pytest.mark.postgres
@pytest.mark.parametrize(
    "provenance",
    [
        None,
        {"source_type": "document", "privacy_class": "private"},
        {"source_type": "rumour", "privacy_class": "private", "employer_owned": False},
        {"source_type": "document", "privacy_class": "secret", "employer_owned": False},
        {"source_type": "document", "privacy_class": "private", "employer_owned": "no"},
        {
            "source_type": "document",
            "privacy_class": "private",
            "employer_owned": False,
            "origin": "resume_import",
        },
    ],
)
def test_evidence_save_rejects_missing_or_invalid_provenance(
    profile_owner: IdentityContext, provenance: dict[str, object] | None
) -> None:
    body: dict[str, object] = {"payload": _evidence()["payload"]}
    if provenance is not None:
        body["provenance"] = provenance
    with profile_owner.client() as client:
        csrf = str(_json(_bootstrap(profile_owner, client))["csrf_token"])
        response = _mutate(client, profile_owner, csrf, "POST", "/evidence", 1, body)
        assert response.status_code == 422, response.text
        assert _json(client.get("/api/v1/evidence"))["items"] == []


@pytest.mark.postgres
def test_classification_preserves_revisions_pins_and_survives_profile_restore(
    profile_owner: IdentityContext,
) -> None:
    with profile_owner.client() as client:
        csrf = str(_json(_bootstrap(profile_owner, client))["csrf_token"])
        created = _json(_mutate(client, profile_owner, csrf, "POST", "/evidence", 1, _evidence()))
        evidence = created["evidence"][0]
        revision = evidence["revision"]
        claimed = _json(
            _mutate(
                client,
                profile_owner,
                csrf,
                "POST",
                "/claims",
                created["version"],
                _claim_body(revision["id"]),
            )
        )
        old_profile_revision = claimed["current_revision"]["id"]
        old_claim = claimed["current_revision"]["claims"][0]
        path = f"/evidence/{evidence['id']}/classification"
        body = {"privacy_class": "confidential", "employer_owned": True}
        key = uuid4().hex
        response = _mutate(
            client,
            profile_owner,
            csrf,
            "PUT",
            path,
            claimed["version"],
            body,
            key,
        )
        assert response.status_code == 200, response.text
        classified = _json(response)
        assert classified["version"] == claimed["version"] + 1
        current = classified["evidence"][0]
        assert current["version"] == evidence["version"] + 1
        assert current["revision"]["id"] == revision["id"]
        assert current["revision"]["revision_no"] == revision["revision_no"]
        assert current["current_revision_id"] == evidence["current_revision_id"]
        assert current["revision"]["payload"] == revision["payload"]
        assert current["revision"]["provenance"] == {
            "source_type": "document",
            "origin": "owner_entry",
            **body,
        }
        pin = classified["current_revision"]["claims"][0]
        assert pin["revision"]["id"] == old_claim["revision"]["id"]
        assert pin["revision"]["evidence_links"][0]["evidence_revision_id"] == revision["id"]
        replay = _mutate(
            client,
            profile_owner,
            csrf,
            "PUT",
            path,
            claimed["version"],
            body,
            key,
        )
        assert replay.status_code == 200
        assert _json(replay) == classified
        stale = _mutate(
            client,
            profile_owner,
            csrf,
            "PUT",
            path,
            claimed["version"],
            body,
        )
        assert stale.status_code == 412
        conflict = _mutate(
            client,
            profile_owner,
            csrf,
            "PUT",
            path,
            classified["version"],
            {"privacy_class": "private", "employer_owned": False},
            key,
        )
        assert conflict.status_code == 409
        historical = _json(
            client.get(f"/api/v1/evidence/{evidence['id']}/revisions/{revision['id']}")
        )
        assert historical["provenance"] == current["revision"]["provenance"]
        restored_response = _mutate(
            client,
            profile_owner,
            csrf,
            "POST",
            "/profile/restore",
            classified["version"],
            {"revision_id": old_profile_revision},
        )
        assert restored_response.status_code == 200, restored_response.text
        restored = _json(restored_response)
        assert restored["evidence"][0]["revision"]["provenance"] == historical["provenance"]
        restored_pin = restored["current_revision"]["claims"][0]["revision"]["evidence_links"][0]
        assert restored_pin["evidence_revision_id"] == revision["id"]
        assert restored_pin["evidence"]["provenance"] == historical["provenance"]
        with profile_owner.admin.transaction() as conn:
            rows = (
                conn.execute(
                    text("""SELECT sequence,privacy_class,employer_owned
                FROM bonafides.evidence_classification_changes
                WHERE evidence_id=:evidence ORDER BY sequence"""),
                    {"evidence": evidence["id"]},
                )
                .mappings()
                .all()
            )
            assert [dict(row) for row in rows] == [
                {"sequence": 1, "privacy_class": "private", "employer_owned": False},
                {"sequence": 2, **body},
            ]
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM bonafides.evidence_revisions WHERE evidence_id=:evidence"
                    ),
                    {"evidence": evidence["id"]},
                ).scalar_one()
                == 1
            )
        with (
            pytest.raises(DBAPIError, match="immutable"),
            profile_owner.admin.transaction() as conn,
        ):
            conn.execute(
                text("""UPDATE bonafides.evidence_classification_changes
                SET privacy_class='public' WHERE evidence_id=:evidence"""),
                {"evidence": evidence["id"]},
            )


@pytest.mark.postgres
@pytest.mark.parametrize(
    "body",
    [
        {},
        {"privacy_class": "private"},
        {"privacy_class": "secret", "employer_owned": False},
        {"privacy_class": "private", "employer_owned": "false"},
        {"privacy_class": "private", "employer_owned": 0},
        {"privacy_class": "private", "employer_owned": None},
        {"privacy_class": "private", "employer_owned": False, "source_type": "document"},
    ],
)
def test_classification_rejects_invalid_or_extra_fields(
    profile_owner: IdentityContext,
    body: dict[str, object],
) -> None:
    with profile_owner.client() as client:
        csrf = str(_json(_bootstrap(profile_owner, client))["csrf_token"])
        created = _json(_mutate(client, profile_owner, csrf, "POST", "/evidence", 1, _evidence()))
        evidence = created["evidence"][0]
        response = _mutate(
            client,
            profile_owner,
            csrf,
            "PUT",
            f"/evidence/{evidence['id']}/classification",
            created["version"],
            body,
        )
        assert response.status_code == 422, response.text
        assert _json(client.get("/api/v1/profile")) == {
            key: value for key, value in created.items() if key != "changed_evidence_id"
        }


@pytest.mark.postgres
@pytest.mark.parametrize(
    "extra",
    [{"privacy_class": "confidential"}, {"employer_owned": True}],
)
def test_content_update_forbids_record_classification(
    profile_owner: IdentityContext,
    extra: dict[str, object],
) -> None:
    with profile_owner.client() as client:
        csrf = str(_json(_bootstrap(profile_owner, client))["csrf_token"])
        created = _json(_mutate(client, profile_owner, csrf, "POST", "/evidence", 1, _evidence()))
        response = _mutate(
            client,
            profile_owner,
            csrf,
            "PUT",
            f"/evidence/{created['evidence'][0]['id']}",
            created["version"],
            {"payload": _evidence()["payload"], "provenance": {"source_type": "document", **extra}},
        )
        assert response.status_code == 422, response.text
