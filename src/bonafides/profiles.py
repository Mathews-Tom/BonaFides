# SPDX-License-Identifier: MIT OR Apache-2.0
"""Owner profile snapshots, immutable revisions and benchmark-only synthetic facts."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Response
from pydantic import Field
from sqlalchemy import Connection, text

from bonafides.auth import Owner, StrictModel, audit, require_owner
from bonafides.commands import append_outbox, command
from bonafides.config import Settings
from bonafides.db import Database
from bonafides.document_frame import ProfileContact
from bonafides.model_context import lock_evidence_write
from bonafides.problems import Problem

MAX_HEADLINE_LENGTH = 300


class ProfileSnapshot(StrictModel):
    display_name: str = Field(default="", max_length=120)
    headline: str = Field(default="", max_length=MAX_HEADLINE_LENGTH)
    description: str = Field(default="", max_length=2000)
    claim_revision_ids: list[UUID] = Field(default_factory=list, max_length=100)
    evidence_revision_ids: list[UUID] = Field(default_factory=list, max_length=200)
    contact: ProfileContact | None = None


class BenchmarkProfile(StrictModel):
    fixture: Literal["SYN-CORE"]


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(value: object) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def create_empty_profile(
    conn: Connection, owner_id: UUID, *, profile_id: UUID | None = None
) -> None:
    revision_id = uuid4()
    snapshot: dict[str, object] = {
        "display_name": "",
        "headline": "",
        "description": "",
        "claim_revision_ids": [],
        "evidence_revision_ids": [],
    }
    if profile_id is None:
        profile_id = uuid4()
        conn.execute(
            text(
                "INSERT INTO bonafides.profiles(owner_id,id,current_revision_id) VALUES(:owner,:id,:revision)"
            ),
            {"owner": owner_id, "id": profile_id, "revision": revision_id},
        )
    else:
        conn.execute(
            text("""UPDATE bonafides.profiles SET current_revision_id=:revision
            WHERE owner_id=:owner AND id=:id AND current_revision_id IS NULL RETURNING id"""),
            {"owner": owner_id, "id": profile_id, "revision": revision_id},
        ).scalar_one()
    conn.execute(
        text(
            "INSERT INTO bonafides.profile_revisions(owner_id,id,profile_id,revision_no,snapshot,content_sha256,reason) VALUES(:owner,:revision,:profile,1,CAST(:snapshot AS jsonb),:sha,'bootstrap')"
        ),
        {
            "owner": owner_id,
            "revision": revision_id,
            "profile": profile_id,
            "snapshot": canonical(snapshot),
            "sha": content_hash(snapshot),
        },
    )


def get_profile(conn: Connection, owner_id: UUID) -> dict[str, object]:
    row = (
        conn.execute(
            text(
                "SELECT p.id,p.version,p.created_at,r.id AS revision_id,r.revision_no,r.snapshot,r.content_sha256,r.reason,r.created_at AS revised_at FROM bonafides.profiles p JOIN bonafides.profile_revisions r ON r.owner_id=p.owner_id AND r.id=p.current_revision_id WHERE p.owner_id=:owner"
            ),
            {"owner": owner_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise Problem(404, "profile_not_found")
    snapshot = row["snapshot"]
    from bonafides.profile_entries import active_evidence, experience_coverage, hydrate_claims

    claims = hydrate_claims(conn, owner_id, snapshot.get("claim_revision_ids", []))
    warnings: list[str] = []
    if not snapshot.get("claim_revision_ids"):
        warnings.append("empty_profile")
    else:
        warnings.append("evidence_is_owner_asserted_not_independently_verified")
    return {
        "id": str(row["id"]),
        "version": int(row["version"]),
        "created_at": row["created_at"].isoformat(),
        "current_revision": {
            "id": str(row["revision_id"]),
            "revision_no": int(row["revision_no"]),
            "snapshot": snapshot,
            "content_sha256": str(row["content_sha256"]),
            "created_at": row["revised_at"].isoformat(),
            "reason": str(row.get("reason", "owner_save")),
            "author_owner_id": str(owner_id),
            "claims": claims,
        },
        "quality_warnings": warnings,
        "evidence": active_evidence(conn, owner_id),
        "experience_coverage": experience_coverage(claims),
    }


def _save(
    conn: Connection,
    owner_id: UUID,
    snapshot: ProfileSnapshot,
    reason: str,
    *,
    restoring: bool = False,
) -> dict[str, object]:
    profile = (
        conn.execute(
            text("SELECT id,version FROM bonafides.profiles WHERE owner_id=:owner FOR UPDATE"),
            {"owner": owner_id},
        )
        .mappings()
        .one()
    )
    claim_ids = snapshot.claim_revision_ids
    if len(set(claim_ids)) != len(claim_ids):
        raise Problem(422, "duplicate_claim_revision")
    for claim_id in claim_ids:
        found = conn.execute(
            text(
                "SELECT r.id FROM bonafides.claim_revisions r JOIN bonafides.professional_claims c ON c.owner_id=r.owner_id AND c.id=r.claim_id WHERE r.owner_id=:owner AND r.id=:claim AND c.profile_id=:profile AND c.archived_at IS NULL AND (:restoring OR c.current_revision_id=r.id)"
            ),
            {
                "owner": owner_id,
                "claim": claim_id,
                "profile": profile["id"],
                "restoring": restoring,
            },
        ).scalar_one_or_none()
        if found is None:
            raise Problem(422, "invalid_claim_revision")
    if claim_ids:
        count = conn.execute(
            text(
                "SELECT count(DISTINCT claim_id) FROM bonafides.claim_revisions WHERE owner_id=:owner AND id=ANY(CAST(:ids AS uuid[]))"
            ),
            {"owner": owner_id, "ids": claim_ids},
        ).scalar_one()
        if count != len(claim_ids):
            raise Problem(422, "duplicate_claim")
    revision_id = uuid4()
    revision_no = int(
        conn.execute(
            text(
                "SELECT COALESCE(max(revision_no),0)+1 FROM bonafides.profile_revisions WHERE owner_id=:owner AND profile_id=:profile"
            ),
            {"owner": owner_id, "profile": profile["id"]},
        ).scalar_one()
    )
    value = snapshot.model_dump(mode="json")
    # Evidence state is maintained by evidence commands, not edited via raw snapshot PUT.
    value["evidence_revision_ids"] = [
        str(identifier)
        for identifier in conn.execute(
            text(
                "SELECT current_revision_id FROM bonafides.evidence_records WHERE owner_id=:owner AND archived_at IS NULL ORDER BY created_at,id"
            ),
            {"owner": owner_id},
        ).scalars()
    ]
    conn.execute(
        text(
            "INSERT INTO bonafides.profile_revisions(owner_id,id,profile_id,revision_no,snapshot,content_sha256,reason) VALUES(:owner,:revision,:profile,:number,CAST(:snapshot AS jsonb),:sha,:reason)"
        ),
        {
            "owner": owner_id,
            "revision": revision_id,
            "profile": profile["id"],
            "number": revision_no,
            "snapshot": canonical(value),
            "sha": content_hash(value),
            "reason": reason,
        },
    )
    for claim_id in claim_ids:
        conn.execute(
            text(
                "INSERT INTO bonafides.profile_revision_claims(owner_id,profile_revision_id,claim_revision_id) VALUES(:owner,:revision,:claim)"
            ),
            {"owner": owner_id, "revision": revision_id, "claim": claim_id},
        )
    conn.execute(
        text(
            "INSERT INTO bonafides.profile_revision_evidence(owner_id,profile_revision_id,evidence_revision_id) SELECT owner_id,:revision,current_revision_id FROM bonafides.evidence_records WHERE owner_id=:owner AND archived_at IS NULL"
        ),
        {"owner": owner_id, "revision": revision_id},
    )
    conn.execute(
        text(
            "UPDATE bonafides.profiles SET current_revision_id=:revision,version=version+1 WHERE owner_id=:owner AND id=:profile"
        ),
        {"owner": owner_id, "revision": revision_id, "profile": profile["id"]},
    )
    conn.execute(
        text(
            "UPDATE bonafides.document_drafts SET stale_at=COALESCE(stale_at,clock_timestamp()),version=version+1 WHERE owner_id=:owner AND profile_revision_id<>:revision AND stale_at IS NULL"
        ),
        {"owner": owner_id, "revision": revision_id},
    )
    append_outbox(
        conn,
        owner_id,
        "profile.revision_created",
        "profile",
        profile["id"],
        {"revision_id": str(revision_id), "reason": reason},
        aggregate_version=int(profile["version"]) + 1,
    )
    audit(conn, owner_id, "profile_saved")
    return get_profile(conn, owner_id)


def _seed(conn: Connection, owner_id: UUID) -> dict[str, object]:
    profile_id = conn.execute(
        text("SELECT id FROM bonafides.profiles WHERE owner_id=:owner"), {"owner": owner_id}
    ).scalar_one()
    claims: list[UUID] = []
    for fact in (
        "Maintained Linux batch processing runbooks",
        "Wrote SQL queries for incident analysis",
    ):
        claim_id, claim_revision, evidence_id, evidence_revision = (
            uuid4(),
            uuid4(),
            uuid4(),
            uuid4(),
        )
        claim_payload = {"text": fact}
        evidence_payload = {"text": fact, "source": "SYN-CORE owner assertion"}
        conn.execute(
            text(
                "INSERT INTO bonafides.professional_claims(owner_id,id,profile_id,kind,current_revision_id) VALUES(:owner,:id,:profile,'skill',:revision)"
            ),
            {"owner": owner_id, "id": claim_id, "profile": profile_id, "revision": claim_revision},
        )
        conn.execute(
            text(
                "INSERT INTO bonafides.claim_revisions(owner_id,id,claim_id,revision_no,payload,content_sha256) VALUES(:owner,:id,:claim,1,CAST(:payload AS jsonb),:sha)"
            ),
            {
                "owner": owner_id,
                "id": claim_revision,
                "claim": claim_id,
                "payload": canonical(claim_payload),
                "sha": content_hash(claim_payload),
            },
        )
        conn.execute(
            text(
                "INSERT INTO bonafides.evidence_records(owner_id,id,current_revision_id,privacy_class,employer_owned) VALUES(:owner,:id,:revision,'private',false)"
            ),
            {"owner": owner_id, "id": evidence_id, "revision": evidence_revision},
        )
        conn.execute(
            text("""INSERT INTO bonafides.evidence_classification_changes
            (owner_id,evidence_id,sequence,privacy_class,employer_owned)
            VALUES(:owner,:id,1,'private',false)"""),
            {"owner": owner_id, "id": evidence_id},
        )
        conn.execute(
            text(
                "INSERT INTO bonafides.evidence_revisions(owner_id,id,evidence_id,revision_no,payload,content_sha256,source_type,origin) VALUES(:owner,:id,:evidence,1,CAST(:payload AS jsonb),:sha,'owner_statement','owner_entry')"
            ),
            {
                "owner": owner_id,
                "id": evidence_revision,
                "evidence": evidence_id,
                "payload": canonical(evidence_payload),
                "sha": content_hash(evidence_payload),
            },
        )
        conn.execute(
            text(
                "INSERT INTO bonafides.claim_evidence_links(owner_id,claim_revision_id,evidence_revision_id,support_kind,excerpt) VALUES(:owner,:claim,:evidence,'user_asserted',:excerpt)"
            ),
            {
                "owner": owner_id,
                "claim": claim_revision,
                "evidence": evidence_revision,
                "excerpt": fact,
            },
        )
        claims.append(claim_revision)
    return _save(
        conn,
        owner_id,
        ProfileSnapshot(
            display_name="Mira Vale",
            headline="Operations Analyst · Linux and SQL",
            claim_revision_ids=claims,
        ),
        "benchmark_SYN-CORE",
    )


def build_router(db: Database, settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/v1")
    from bonafides.profile_entries import build_entry_router

    router.include_router(build_entry_router(db))

    @router.get("/profile")
    def profile(
        response: Response, owner: Annotated[Owner, Depends(require_owner)]
    ) -> dict[str, object]:
        with db.transaction() as conn:
            result = get_profile(conn, owner.id)
        response.headers["ETag"] = f'"{result["version"]}"'
        return result

    @router.put("/profile")
    def save(
        body: ProfileSnapshot,
        response: Response,
        owner: Annotated[Owner, Depends(require_owner)],
        if_match: Annotated[str | None, Header()] = None,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        with db.transaction() as conn:
            lock_evidence_write(conn, owner.id)
            profile_id = conn.execute(
                text("SELECT id FROM bonafides.profiles WHERE owner_id=:owner"), {"owner": owner.id}
            ).scalar_one()

            def effect(connection: Connection) -> tuple[int, dict[str, object]]:
                current = connection.execute(
                    text("""SELECT r.snapshot FROM bonafides.profiles p
                    JOIN bonafides.profile_revisions r
                    ON r.owner_id=p.owner_id AND r.id=p.current_revision_id
                    WHERE p.owner_id=:owner"""),
                    {"owner": owner.id},
                ).scalar_one()
                snapshot = body
                if "description" not in body.model_fields_set:
                    snapshot = body.model_copy(
                        update={"description": current.get("description", "")}
                    )
                return 200, _save(connection, owner.id, snapshot, "owner_save")

            status, result = command(
                conn,
                owner.id,
                "PUT",
                "/api/v1/profile",
                {},
                body.model_dump(
                    mode="json",
                    exclude={"description"}
                    if "description" not in body.model_fields_set
                    else set(),
                ),
                if_match,
                idempotency_key,
                "profile",
                profile_id,
                effect,
            )
        response.status_code = status
        response.headers["ETag"] = f'"{result["version"]}"'
        return result

    @router.post("/benchmarks/profile")
    def seed(
        body: BenchmarkProfile,
        response: Response,
        owner: Annotated[Owner, Depends(require_owner)],
        if_match: Annotated[str | None, Header()] = None,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        if settings.mode != "benchmark":
            raise Problem(404, "not_found")
        with db.transaction() as conn:
            lock_evidence_write(conn, owner.id)
            profile_id = conn.execute(
                text("SELECT id FROM bonafides.profiles WHERE owner_id=:owner"), {"owner": owner.id}
            ).scalar_one()

            def effect(connection: Connection) -> tuple[int, dict[str, object]]:
                return 200, _seed(connection, owner.id)

            status, result = command(
                conn,
                owner.id,
                "POST",
                "/api/v1/benchmarks/profile",
                {},
                body.model_dump(mode="json"),
                if_match,
                idempotency_key,
                "profile",
                profile_id,
                effect,
            )
        response.status_code = status
        response.headers["ETag"] = f'"{result["version"]}"'
        return result

    return router
