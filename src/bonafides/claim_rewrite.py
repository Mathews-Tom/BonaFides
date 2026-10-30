# SPDX-License-Identifier: MIT OR Apache-2.0
"""Rewrite free-text experience and project claims in place as claim schema v1.

A claim is rewritten only when the mapper represents its whole statement and every evidence
excerpt still quotes each literal part; any other claim stays free text and needs attention.
Earlier revisions are untouched, so revision history and restore keep working.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Connection, text

from bonafides.claim_mapping import map_line
from bonafides.claims import STRUCTURED_KINDS, grounded
from bonafides.profile_entries import (
    EvidenceLink,
    StructuredClaimPayload,
    _snapshot,
    append_claim_revision,
)
from bonafides.profiles import _save

RESTRUCTURE_REASON = "claims_restructured"


def _links(conn: Connection, owner_id: UUID, revision_id: UUID) -> list[EvidenceLink] | None:
    """The revision's evidence links, or None when any linked evidence is archived."""
    rows = (
        conn.execute(
            text("""SELECT l.evidence_revision_id,l.support_kind,l.excerpt,e.archived_at
            FROM bonafides.claim_evidence_links l
            JOIN bonafides.evidence_revisions r ON r.owner_id=l.owner_id AND r.id=l.evidence_revision_id
            JOIN bonafides.evidence_records e ON e.owner_id=r.owner_id AND e.id=r.evidence_id
            WHERE l.owner_id=:owner AND l.claim_revision_id=:revision
            ORDER BY l.created_at,l.evidence_revision_id"""),
            {"owner": owner_id, "revision": revision_id},
        )
        .mappings()
        .all()
    )
    if not rows or any(row["archived_at"] is not None for row in rows):
        return None
    return [
        EvidenceLink(
            evidence_revision_id=row["evidence_revision_id"],
            support_kind=row["support_kind"],
            excerpt=row["excerpt"],
        )
        for row in rows
    ]


def restructure_free_text_claims(conn: Connection, owner_id: UUID) -> int:
    """Rewrite each representable free-text claim of the current profile; return the count."""
    snapshot = _snapshot(conn, owner_id)
    rows = (
        conn.execute(
            text("""SELECT c.id AS claim_id,r.id AS revision_id,r.payload
            FROM bonafides.professional_claims c JOIN bonafides.claim_revisions r
            ON r.owner_id=c.owner_id AND r.id=c.current_revision_id
            WHERE c.owner_id=:owner AND c.archived_at IS NULL AND c.kind=ANY(CAST(:kinds AS text[]))
            AND r.id=ANY(CAST(:ids AS uuid[])) AND r.payload->'claim' IS NULL
            ORDER BY c.created_at,c.id"""),
            {
                "owner": owner_id,
                "kinds": sorted(STRUCTURED_KINDS),
                "ids": [str(item) for item in snapshot.claim_revision_ids],
            },
        )
        .mappings()
        .all()
    )
    replaced: dict[UUID, UUID] = {}
    for row in rows:
        payload = dict(row["payload"])
        statement = payload.pop("text", None)
        if not isinstance(statement, str):
            raise ValueError("claim_without_statement")
        mapping = map_line(statement)
        links = _links(conn, owner_id, row["revision_id"])
        if mapping.claim is None or not mapping.representable or links is None:
            continue
        if not all(grounded(mapping.claim, link.excerpt) for link in links):
            continue
        structured = StructuredClaimPayload.model_validate({**payload, "claim": mapping.claim})
        replaced[row["revision_id"]] = append_claim_revision(
            conn,
            owner_id,
            row["claim_id"],
            structured.model_dump(mode="json", exclude_none=True),
            links,
            increment=1,
        )
    if replaced:
        ids = [replaced.get(item, item) for item in snapshot.claim_revision_ids]
        _save(
            conn,
            owner_id,
            snapshot.model_copy(update={"claim_revision_ids": ids}),
            RESTRUCTURE_REASON,
        )
    return len(replaced)
