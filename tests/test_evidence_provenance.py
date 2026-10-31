# SPDX-License-Identifier: MIT OR Apache-2.0
"""Evidence records carry source type, origin, privacy class and employer ownership."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from test_identity import IdentityContext, _bootstrap
from test_identity import identity as identity
from test_profiles import _json, _mutate
from test_profiles import profile_owner as profile_owner

from bonafides.frozen_migrations.evidence_provenance_v1 import legacy_provenance
from bonafides.profiles import canonical, content_hash

MIGRATION = Path(__file__).resolve().parents[1] / "migrations/sql/029_evidence_provenance.up.sql"


def _evidence(
    source_type: str = "document", privacy_class: str = "private", employer_owned: bool = False
) -> dict[str, object]:
    return {
        "payload": {"text": "Synthetic quarterly review excerpt", "source": "Synthetic review"},
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
                _evidence("work_sample", "confidential", True),
            )
        )
        current: dict[str, Any] = updated["evidence"][0]
        assert current["revision"]["revision_no"] == 2
        assert current["revision"]["provenance"] == {
            "source_type": "work_sample",
            "origin": "owner_entry",
            "privacy_class": "confidential",
            "employer_owned": True,
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
def test_provenance_upgrade_assigns_the_frozen_legacy_values_without_updating_revisions(
    profile_owner: IdentityContext,
) -> None:
    statements = [
        statement
        for statement in (
            "\n".join(
                line for line in part.splitlines() if not line.lstrip().startswith("--")
            ).strip()
            for part in MIGRATION.read_text(encoding="utf-8").split(";")
        )
        if statement.startswith("ALTER TABLE evidence_revisions")
    ]
    assert len(statements) == 3
    payloads = [
        {"text": "Owner typed fact", "source": "Owner attestation"},
        {"text": "Imported line", "source": "Imported", "import_id": str(uuid4())},
    ]
    with profile_owner.admin.transaction() as conn:
        conn.execute(
            text("""CREATE TEMP TABLE evidence_revisions ON COMMIT DROP AS
            SELECT owner_id,id,evidence_id,revision_no,payload,blob_id,content_sha256,created_at
            FROM bonafides.evidence_revisions WITH NO DATA""")
        )
        conn.execute(
            text("""CREATE TRIGGER immutable_update BEFORE UPDATE ON pg_temp.evidence_revisions
            FOR EACH ROW EXECUTE FUNCTION bonafides.reject_revision_update()""")
        )
        for payload in payloads:
            conn.execute(
                text("""INSERT INTO pg_temp.evidence_revisions
                (owner_id,id,evidence_id,revision_no,payload,content_sha256)
                VALUES(:owner,:id,:evidence,1,CAST(:payload AS jsonb),:sha)"""),
                {
                    "owner": uuid4(),
                    "id": uuid4(),
                    "evidence": uuid4(),
                    "payload": canonical(payload),
                    "sha": content_hash(payload),
                },
            )
        for statement in statements:
            conn.execute(
                text(
                    re.sub(
                        r"\bevidence_revisions\b", "pg_temp.evidence_revisions", statement, count=1
                    )
                )
            )
        rows = (
            conn.execute(
                text("""SELECT payload,source_type,origin,privacy_class,employer_owned
                FROM pg_temp.evidence_revisions ORDER BY payload ? 'import_id'""")
            )
            .mappings()
            .all()
        )
        assert [
            {key: row[key] for key in ("source_type", "origin", "privacy_class", "employer_owned")}
            for row in rows
        ] == [legacy_provenance(payload) for payload in payloads]
        assert [legacy_provenance(payload)["origin"] for payload in payloads] == [
            "owner_entry",
            "resume_import",
        ]
        defaults = conn.execute(
            text("""SELECT count(*) FROM information_schema.columns
            WHERE table_schema='bonafides' AND table_name='evidence_revisions'
            AND column_name IN ('source_type','origin','privacy_class','employer_owned')
            AND column_default IS NULL AND is_nullable='NO'""")
        ).scalar_one()
        assert defaults == 4
