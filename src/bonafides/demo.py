# SPDX-License-Identifier: MIT OR Apache-2.0
"""`bonafides demo load`: synthetic profile, evidence and claims for an existing owner."""

from __future__ import annotations

import argparse
import sys
from uuid import UUID

from sqlalchemy import Connection, text

from bonafides.config import ConfigurationError, Settings
from bonafides.db import Database, PreBaselineDatabase
from bonafides.model_context import lock_evidence_write
from bonafides.problems import Problem
from bonafides.profiles import (
    SYNTHETIC_DISPLAY_NAME,
    SYNTHETIC_FACTS,
    SYNTHETIC_HEADLINE,
    seed_synthetic_profile,
)

DEMO_REASON = "demo_load"
_EMPTY_REASON = "bootstrap"


def _counts(conn: Connection, owner_id: UUID) -> dict[str, int]:
    row = (
        conn.execute(
            text("""SELECT
            (SELECT count(*) FROM bonafides.professional_claims WHERE owner_id=:o) AS claims,
            (SELECT count(*) FROM bonafides.claim_revisions WHERE owner_id=:o) AS claim_revisions,
            (SELECT count(*) FROM bonafides.evidence_records WHERE owner_id=:o) AS evidence,
            (SELECT count(*) FROM bonafides.evidence_revisions WHERE owner_id=:o) AS evidence_revisions,
            (SELECT count(*) FROM bonafides.evidence_classification_changes WHERE owner_id=:o)
                AS classifications"""),
            {"o": owner_id},
        )
        .mappings()
        .one()
    )
    return {key: int(value) for key, value in row.items()}


def _state(conn: Connection, owner_id: UUID) -> str:
    """`empty`, `demo` (byte-for-byte what a prior load wrote) or `changed`."""
    profile = (
        conn.execute(
            text("""SELECT r.revision_no,r.reason,r.snapshot FROM bonafides.profiles p
            JOIN bonafides.profile_revisions r ON r.owner_id=p.owner_id AND r.id=p.current_revision_id
            WHERE p.owner_id=:o"""),
            {"o": owner_id},
        )
        .mappings()
        .one_or_none()
    )
    if profile is None:
        return "changed"
    counts = _counts(conn, owner_id)
    snapshot = profile["snapshot"]
    if (
        profile["revision_no"] == 1
        and profile["reason"] == _EMPTY_REASON
        and not any(counts.values())
        and not snapshot.get("claim_revision_ids")
        and not snapshot.get("evidence_revision_ids")
        and not snapshot.get("display_name")
        and not snapshot.get("headline")
        and not snapshot.get("description")
        and not snapshot.get("contact")
    ):
        return "empty"
    expected = len(SYNTHETIC_FACTS)
    if (
        profile["revision_no"] == 2
        and profile["reason"] == DEMO_REASON
        and snapshot.get("display_name") == SYNTHETIC_DISPLAY_NAME
        and snapshot.get("headline") == SYNTHETIC_HEADLINE
        and not snapshot.get("description")
        and not snapshot.get("contact")
        and counts
        == {
            "claims": expected,
            "claim_revisions": expected,
            "evidence": expected,
            "evidence_revisions": expected,
            "classifications": expected,
        }
        and _untouched(conn, owner_id, snapshot)
    ):
        return "demo"
    return "changed"


def _untouched(conn: Connection, owner_id: UUID, snapshot: dict[str, list[str]]) -> bool:
    """Every seeded row still sits at its first revision, unarchived, unreclassified."""
    facts = conn.execute(
        text("""SELECT c.version,c.archived_at IS NULL AS live,c.current_revision_id,
        r.id AS revision_id,r.revision_no,r.payload->>'text' AS fact
        FROM bonafides.professional_claims c
        JOIN bonafides.claim_revisions r ON r.owner_id=c.owner_id AND r.claim_id=c.id
        WHERE c.owner_id=:o"""),
        {"o": owner_id},
    ).mappings()
    claims = list(facts)
    evidence = list(
        conn.execute(
            text("""SELECT e.version,e.archived_at IS NULL AS live,e.privacy_class,e.employer_owned,
            e.current_revision_id=r.id AS current,r.id AS revision_id,r.revision_no,r.payload->>'text' AS fact
            FROM bonafides.evidence_records e
            JOIN bonafides.evidence_revisions r ON r.owner_id=e.owner_id AND r.evidence_id=e.id
            WHERE e.owner_id=:o"""),
            {"o": owner_id},
        ).mappings()
    )
    claims_ok = (
        sorted(str(row["fact"]) for row in claims) == sorted(SYNTHETIC_FACTS)
        and all(
            row["version"] == 1
            and row["live"]
            and row["revision_no"] == 1
            and row["current_revision_id"] == row["revision_id"]
            for row in claims
        )
        and sorted(str(row["revision_id"]) for row in claims)
        == sorted(snapshot.get("claim_revision_ids", []))
    )
    evidence_ok = (
        sorted(str(row["fact"]) for row in evidence) == sorted(SYNTHETIC_FACTS)
        and sorted(str(row["revision_id"]) for row in evidence)
        == sorted(snapshot.get("evidence_revision_ids", []))
    ) and all(
        row["version"] == 1
        and row["live"]
        and row["current"]
        and row["revision_no"] == 1
        and row["privacy_class"] == "private"
        and not row["employer_owned"]
        for row in evidence
    )
    return claims_ok and evidence_ok


def load_demo(db: Database) -> bool:
    """Seed into an empty owner installation; True when written, False for an identical rerun."""
    with db.transaction() as conn:
        owners = conn.execute(text("SELECT id FROM bonafides.owners")).scalars().all()
        if len(owners) != 1:
            raise Problem(409, "owner_setup_required")
        owner_id = owners[0]
        lock_evidence_write(conn, owner_id)
        state = _state(conn, owner_id)
        if state == "demo":
            return False
        if state != "empty":
            raise Problem(409, "demo_requires_empty_profile")
        seed_synthetic_profile(conn, owner_id, DEMO_REASON)
        return True


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="bonafides demo")
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser(
        "load",
        help="load synthetic profile, evidence and claims into an existing, empty owner profile",
    )
    parser.parse_args(argv)
    try:
        db = Database(Settings.load())
        try:
            written = load_demo(db)
        finally:
            db.close()
    except PreBaselineDatabase as exc:
        print(f"Operation refused: {exc}", file=sys.stderr)
        return 2
    except Problem as exc:
        print(f"Operation refused: {exc.code}", file=sys.stderr)
        return 2
    except ConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(
        "Synthetic demo data loaded."
        if written
        else "Synthetic demo data already loaded; nothing changed."
    )
    return 0
