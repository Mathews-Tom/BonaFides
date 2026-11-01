# SPDX-License-Identifier: MIT OR Apache-2.0
"""Professional evidence as it enters model context.

Confidential or employer-owned evidence content never enters model context. The evidence
item keeps its identifier and carries only a withheld marker, so the claims citing it stay
usable and every model citation still resolves. The local checker keeps reading the full
evidence text; this projection applies to transmitted model context only.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import Connection, text

from bonafides.problems import Problem

WithheldReason = Literal["confidential", "employer_owned"]


def lock_transmitted_evidence(conn: Connection, owner: UUID, context: Mapping[str, object]) -> None:
    """Hold only transmitted evidence records until the model exchange returns.

    The caller keeps this transaction open while checking the workflow on a separate
    connection: its owner/workflow locks must not block heartbeats or provider admission.
    Withheld items contain no evidence text and need no send lock.
    """
    evidence = cast(list[dict[str, object]], context.get("evidence", []))
    identifiers = sorted({UUID(str(item["id"])) for item in evidence if "text" in item}, key=str)
    if not identifiers:
        return
    rows = conn.execute(
        text("""SELECT r.id FROM bonafides.evidence_revisions r
        JOIN bonafides.evidence_records e ON e.owner_id=r.owner_id AND e.id=r.evidence_id
        WHERE r.owner_id=:owner AND r.id=ANY(CAST(:ids AS uuid[]))
        ORDER BY e.id,r.id FOR SHARE OF e"""),
        {"owner": owner, "ids": identifiers},
    ).all()
    if len(rows) != len(identifiers):
        raise Problem(409, "profile_evidence_unavailable")


def withheld_evidence(
    conn: Connection, owner: UUID, revision_ids: Iterable[UUID]
) -> dict[UUID, list[WithheldReason]]:
    """Evidence revisions whose content stays out of model context, with the reasons.

    The record's current classification is the single authority, independently of
    which immutable revision a claim cites or a historical profile restores.
    """
    identifiers = sorted(set(revision_ids), key=str)
    if not identifiers:
        return {}
    rows = (
        conn.execute(
            text("""SELECT r.id,
            e.privacy_class='confidential' AS confidential,
            e.employer_owned AS employer_owned
            FROM bonafides.evidence_revisions r JOIN bonafides.evidence_records e
            ON e.owner_id=r.owner_id AND e.id=r.evidence_id
            WHERE r.owner_id=:owner AND r.id=ANY(CAST(:ids AS uuid[]))"""),
            {"owner": owner, "ids": identifiers},
        )
        .mappings()
        .all()
    )
    if len(rows) != len(identifiers):
        raise Problem(409, "profile_evidence_unavailable")
    withheld: dict[UUID, list[WithheldReason]] = {}
    for row in rows:
        reasons: list[WithheldReason] = []
        if row["confidential"]:
            reasons.append("confidential")
        if row["employer_owned"]:
            reasons.append("employer_owned")
        if reasons:
            withheld[row["id"]] = reasons
    return withheld


def evidence_item(identifier: str, content: str, withheld: bool) -> dict[str, object]:
    """One model-context evidence item: its text, or only a withheld marker."""
    if withheld:
        return {"id": identifier, "withheld": True}
    return {"id": identifier, "text": content}


def professional_context(
    conn: Connection, owner: UUID, facts: Sequence[Mapping[str, object]]
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Claims and evidence items of document facts as they enter model context."""
    withheld = withheld_evidence(
        conn, owner, (UUID(str(fact["evidence_revision_id"])) for fact in facts)
    )
    claims: dict[str, dict[str, object]] = {}
    evidence: dict[str, dict[str, object]] = {}
    for fact in facts:
        claim_id, evidence_id = str(fact["claim_revision_id"]), str(fact["evidence_revision_id"])
        claim = claims.setdefault(
            claim_id,
            {
                "id": claim_id,
                "text": fact["claim_text"],
                "structured_fields": fact["structured_fields"],
                "evidence_revision_ids": [],
            },
        )
        cast(list[str], claim["evidence_revision_ids"]).append(evidence_id)
        evidence[evidence_id] = evidence_item(
            evidence_id, str(fact["evidence_text"]), UUID(evidence_id) in withheld
        )
    return list(claims.values()), list(evidence.values())
