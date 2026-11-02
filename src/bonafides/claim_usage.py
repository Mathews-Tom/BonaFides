# SPDX-License-Identifier: MIT OR Apache-2.0
"""Where each claim revision is used, read from the immutable document claim links.

A link is written in the same transaction as its document revision and only for a factual
statement that passed the checker, so links are the usage record: blocked statements and
non-factual blocks never count.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import Connection, RowMapping, text

from bonafides.auth import Owner, require_owner
from bonafides.claims import claim_text
from bonafides.db import Database
from bonafides.problems import Problem

_USAGE = """WITH used AS (
 SELECT document_revision_id,claim_revision_id,array_agg(DISTINCT excerpt ORDER BY excerpt) AS statements
 FROM bonafides.document_claim_links
 WHERE owner_id=:owner AND status IN ('supported','user_asserted') AND claim_revision_id IS NOT NULL
 GROUP BY document_revision_id,claim_revision_id)
SELECT d.id AS document_id,d.kind,d.scope,d.application_id,d.current_revision_id,
 r.id AS document_revision_id,r.revision_no AS document_revision_no,
 cr.claim_id,cr.id AS claim_revision_id,cr.revision_no AS claim_revision_no,cr.payload,
 c.current_revision_id AS claim_current_revision_id,c.archived_at AS claim_archived_at,
 p.title,p.company,a.attempt_no,a.archived_at AS application_archived_at,u.statements
FROM used u
JOIN bonafides.claim_revisions cr ON cr.owner_id=:owner AND cr.id=u.claim_revision_id
JOIN bonafides.professional_claims c ON c.owner_id=:owner AND c.id=cr.claim_id
JOIN bonafides.document_revisions r ON r.owner_id=:owner AND r.id=u.document_revision_id
JOIN bonafides.document_drafts d ON d.owner_id=:owner AND d.id=r.document_draft_id
LEFT JOIN bonafides.applications a ON a.owner_id=:owner AND a.id=d.application_id
LEFT JOIN bonafides.saved_opportunities o ON o.owner_id=:owner AND o.id=a.opportunity_id
LEFT JOIN bonafides.job_postings p ON p.owner_id=:owner AND p.id=o.posting_id
WHERE d.deleted_at IS NULL AND {scope}
ORDER BY d.scope,a.created_at,a.id,d.kind DESC,r.revision_no DESC,cr.revision_no"""


def _item(row: RowMapping) -> dict[str, object]:
    application = (
        None
        if row["application_id"] is None
        else {
            "id": str(row["application_id"]),
            "title": row["title"],
            "company": row["company"],
            "attempt_no": row["attempt_no"],
            "archived": row["application_archived_at"] is not None,
        }
    )
    return {
        "scope": row["scope"],
        "application": application,
        "document_id": str(row["document_id"]),
        "kind": row["kind"],
        "document_revision_id": str(row["document_revision_id"]),
        "document_revision_no": row["document_revision_no"],
        "claim_id": str(row["claim_id"]),
        "claim_revision_id": str(row["claim_revision_id"]),
        "claim_revision_no": row["claim_revision_no"],
        "claim_text": claim_text(row["payload"]),
        "superseded": row["claim_revision_id"] != row["claim_current_revision_id"],
        "archived": row["claim_archived_at"] is not None,
        "statements": list(row["statements"]),
    }


def _usage(conn: Connection, owner_id: UUID, scope: str, **params: object) -> list[RowMapping]:
    return list(
        conn.execute(text(_USAGE.format(scope=scope)), {"owner": owner_id, **params})
        .mappings()
        .all()
    )


def claim_usage(
    conn: Connection, owner_id: UUID, claim_id: UUID, *, history: bool = False
) -> dict[str, object]:
    claim = (
        conn.execute(
            text(
                "SELECT current_revision_id,archived_at FROM bonafides.professional_claims WHERE owner_id=:owner AND id=:id"
            ),
            {"owner": owner_id, "id": claim_id},
        )
        .mappings()
        .one_or_none()
    )
    if claim is None:
        raise Problem(404, "not_found")
    rows = _usage(conn, owner_id, "cr.claim_id=:claim", claim=claim_id)
    current = [
        _item(row) for row in rows if row["document_revision_id"] == row["current_revision_id"]
    ]
    result: dict[str, object] = {
        "claim_id": str(claim_id),
        "current_revision_id": str(claim["current_revision_id"]),
        "archived": claim["archived_at"] is not None,
        "items": current,
    }
    if history:
        result["history"] = [
            _item(row) for row in rows if row["document_revision_id"] != row["current_revision_id"]
        ]
    return result


def application_claims(conn: Connection, owner_id: UUID, application_id: UUID) -> dict[str, object]:
    exists = conn.execute(
        text(
            "SELECT EXISTS(SELECT 1 FROM bonafides.applications WHERE owner_id=:owner AND id=:id)"
        ),
        {"owner": owner_id, "id": application_id},
    ).scalar_one()
    if not exists:
        raise Problem(404, "not_found")
    rows = _usage(
        conn,
        owner_id,
        "d.application_id=:application AND r.id=d.current_revision_id",
        application=application_id,
    )
    return {"application_id": str(application_id), "items": [_item(row) for row in rows]}


def build_router(db: Database) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.get("/claims/{claim_id}/usage")
    def read_claim_usage(
        claim_id: UUID, owner: Annotated[Owner, Depends(require_owner)], history: bool = False
    ) -> dict[str, object]:
        with db.transaction() as conn:
            return claim_usage(conn, owner.id, claim_id, history=history)

    @router.get("/applications/{application_id}/claims")
    def read_application_claims(
        application_id: UUID, owner: Annotated[Owner, Depends(require_owner)]
    ) -> dict[str, object]:
        with db.transaction() as conn:
            return application_claims(conn, owner.id, application_id)

    return router
