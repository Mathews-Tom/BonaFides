# SPDX-License-Identifier: MIT OR Apache-2.0
"""Professional evidence as it enters model context.

Confidential or employer-owned evidence content never enters model context. The evidence
item keeps its identifier and carries only a withheld marker, so the claims citing it stay
usable and every model citation still resolves. The local checker keeps reading the full
evidence text; this projection applies to transmitted model context only.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import Connection, text
from sqlalchemy.exc import SQLAlchemyError

from bonafides.db import Database
from bonafides.problems import Problem

WithheldReason = Literal["confidential", "employer_owned"]


# Two-int advisory keys are disjoint from the bigint keys used by maintenance,
# migration and provider coordinators. Keep this namespace exclusive to evidence.
_EVIDENCE_LOCK_NAMESPACE = 741903230


def _owner_lock_key(owner: UUID) -> dict[str, int]:
    return {
        "namespace": _EVIDENCE_LOCK_NAMESPACE,
        "owner": int.from_bytes(hashlib.sha256(owner.bytes).digest()[:4], "big", signed=True),
    }


def lock_evidence_write(conn: Connection, owner: UUID) -> None:
    """First statement of every evidence-writing transaction; never wait on sends."""
    acquired = conn.execute(
        text("SELECT pg_try_advisory_xact_lock(:namespace, :owner)"),
        _owner_lock_key(owner),
    ).scalar_one()
    if not acquired:
        raise Problem(409, "evidence_in_model_use")


@contextmanager
def model_send_lock(db: Database, owner: UUID) -> Iterator[None]:
    """Share one owner send lock without retaining a transaction or row locks.

    Acquire before final context validation and release after the bounded HTTP
    exchange. Validation uses a separate transaction so heartbeats, cancellation
    and provider admission retain their existing independent fences.
    """
    parameters = _owner_lock_key(owner)
    with db.engine.connect() as conn:
        acquired = False
        try:
            conn.execute(text("SELECT pg_advisory_lock_shared(:namespace, :owner)"), parameters)
            acquired = True
            conn.commit()
            yield
        finally:
            if acquired:
                try:
                    conn.execute(
                        text("SELECT pg_advisory_unlock_shared(:namespace, :owner)"), parameters
                    )
                    conn.commit()
                except SQLAlchemyError:
                    # A session lock must never escape back into the pool.
                    conn.invalidate()


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


def withheld_explanations(
    conn: Connection, owner: UUID, facts: Sequence[Mapping[str, object]]
) -> list[dict[str, object]]:
    """Why each withheld evidence item carries no content, for the owner's preview only.

    The explanation is never part of model context; it names the evidence, its source
    reference, the reasons and the claims that are still sent.
    """
    withheld = withheld_evidence(
        conn, owner, (UUID(str(fact["evidence_revision_id"])) for fact in facts)
    )
    explanations: dict[str, dict[str, object]] = {}
    for fact in facts:
        evidence_id = str(fact["evidence_revision_id"])
        reasons = withheld.get(UUID(evidence_id))
        if reasons is None:
            continue
        explanation = explanations.setdefault(
            evidence_id,
            {
                "evidence_revision_id": evidence_id,
                "source": fact["source"],
                "reasons": reasons,
                "claim_revision_ids": [],
            },
        )
        claims = cast(list[str], explanation["claim_revision_ids"])
        if str(fact["claim_revision_id"]) not in claims:
            claims.append(str(fact["claim_revision_id"]))
    return list(explanations.values())
