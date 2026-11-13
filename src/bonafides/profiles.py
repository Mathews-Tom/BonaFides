# SPDX-License-Identifier: MIT OR Apache-2.0
"""Owner profile snapshots, immutable revisions and benchmark-only synthetic facts."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from datetime import date
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Response
from pydantic import Field, field_validator
from sqlalchemy import Connection, text

from bonafides.auth import Owner, StrictModel, audit, require_owner
from bonafides.commands import append_outbox, command
from bonafides.config import Settings
from bonafides.db import Database
from bonafides.document_frame import ProfileContact
from bonafides.model_context import lock_evidence_write
from bonafides.problems import Problem
from bonafides.profile_breadth import citation_text, selected_claim_ids, snapshot_claim_ids
from bonafides.profile_identity import (
    MAX_PORTRAIT_BASE64,
    PortraitPin,
    ReferenceContact,
    portrait_bytes,
    portrait_storage_key,
    validate_portrait,
)
from bonafides.storage import BlobStore

MAX_HEADLINE_LENGTH = 300
PRESERVED_SNAPSHOT_FIELDS: dict[str, object] = {
    "description": "",
    "writing_fact_revision_ids": {},
    "ordering_modes": {},
    "birth_date": None,
    "include_age": False,
    "portrait": None,
    "include_portrait": True,
    "references": [],
    "include_references": False,
}
SYNTHETIC_FACTS = (
    "Maintained Linux batch processing runbooks",
    "Wrote SQL queries for incident analysis",
)
SYNTHETIC_DISPLAY_NAME = "Mira Vale"
SYNTHETIC_HEADLINE = "Operations Analyst · Linux and SQL"


class ProfileSnapshot(StrictModel):
    display_name: str = Field(default="", max_length=120)
    headline: str = Field(default="", max_length=MAX_HEADLINE_LENGTH)
    description: str = Field(default="", max_length=2000)
    claim_revision_ids: list[UUID] = Field(default_factory=list, max_length=100)
    evidence_revision_ids: list[UUID] = Field(default_factory=list, max_length=200)
    contact: ProfileContact | None = None
    writing_fact_revision_ids: dict[str, list[UUID]] = Field(default_factory=dict)
    ordering_modes: dict[
        Literal["experience", "project", "education"], Literal["date", "manual"]
    ] = Field(default_factory=dict)
    birth_date: str | None = Field(default=None, exclude_if=lambda value: value is None)
    include_age: bool = Field(default=False, strict=True, exclude_if=lambda value: not value)
    portrait: PortraitPin | None = Field(default=None, exclude_if=lambda value: value is None)
    include_portrait: bool = Field(default=True, strict=True, exclude_if=lambda value: value)
    references: list[ReferenceContact] = Field(
        default_factory=list, max_length=20, exclude_if=lambda value: not value
    )
    include_references: bool = Field(default=False, strict=True, exclude_if=lambda value: not value)

    @field_validator("birth_date")
    @classmethod
    def valid_birth_date(cls, value: str | None) -> str | None:
        if value is not None and (
            re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None
            or date.fromisoformat(value) > date.today()
        ):
            raise ValueError("invalid_birth_date")
        return value

    @field_validator("writing_fact_revision_ids")
    @classmethod
    def valid_writing_groups(cls, groups: dict[str, list[UUID]]) -> dict[str, list[UUID]]:
        for scope, ids in groups.items():
            if scope != "personal" and str(UUID(scope)) != scope:
                raise ValueError("invalid_writing_scope")
            if len(ids) > 50:
                raise ValueError("writing_fact_limit")
        return groups


class ProfileUpdate(ProfileSnapshot):
    portrait_upload: str | None = Field(default=None, max_length=MAX_PORTRAIT_BASE64)


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


def get_profile(
    conn: Connection, owner_id: UUID, application_id: UUID | None = None
) -> dict[str, object]:
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

    if application_id is not None:
        validate_writing_scope(conn, owner_id, str(application_id))
    claims = hydrate_claims(
        conn, owner_id, selected_claim_ids(snapshot, application_id, writing=True)
    )
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


def validate_writing_scope(
    conn: Connection, owner_id: UUID, scope: str, *, active: bool = False
) -> None:
    if scope == "personal":
        return
    found = conn.execute(
        text("""SELECT id FROM bonafides.applications WHERE owner_id=:owner AND id=:id
        AND (NOT :active OR archived_at IS NULL) FOR SHARE"""),
        {"owner": owner_id, "id": UUID(scope), "active": active},
    ).scalar_one_or_none()
    if found is None:
        raise Problem(422, "invalid_writing_scope")


def _save(
    conn: Connection,
    owner_id: UUID,
    snapshot: ProfileSnapshot,
    reason: str,
    *,
    restoring: bool = False,
    portrait_upload: bytes | None = None,
    settings: Settings | None = None,
) -> dict[str, object]:
    snapshot = ProfileSnapshot.model_validate(snapshot.model_dump(mode="json"))
    profile = (
        conn.execute(
            text("SELECT id,version FROM bonafides.profiles WHERE owner_id=:owner FOR UPDATE"),
            {"owner": owner_id},
        )
        .mappings()
        .one()
    )
    claim_ids = snapshot_claim_ids(snapshot.model_dump(mode="json"))
    if len(set(claim_ids)) != len(claim_ids):
        raise Problem(422, "duplicate_claim_revision")
    groups = {
        str(item): scope
        for scope, ids in snapshot.writing_fact_revision_ids.items()
        for item in ids
    }
    for scope, ids in snapshot.writing_fact_revision_ids.items():
        if ids:
            validate_writing_scope(conn, owner_id, scope)
    for claim_id in claim_ids:
        found = (
            conn.execute(
                text(
                    "SELECT r.id,c.kind,r.payload FROM bonafides.claim_revisions r JOIN bonafides.professional_claims c ON c.owner_id=r.owner_id AND c.id=r.claim_id WHERE r.owner_id=:owner AND r.id=:claim AND c.profile_id=:profile AND c.archived_at IS NULL AND (:restoring OR c.current_revision_id=r.id)"
                ),
                {
                    "owner": owner_id,
                    "claim": claim_id,
                    "profile": profile["id"],
                    "restoring": restoring,
                },
            )
            .mappings()
            .one_or_none()
        )
        if found is None:
            raise Problem(422, "invalid_claim_revision")
        claim_scope = groups.get(claim_id)
        if (found["kind"] == "writing_fact") != (claim_scope is not None):
            raise Problem(422, "invalid_claim_membership")
        if (
            claim_scope is not None
            and str(found["payload"].get("application_id") or "personal") != claim_scope
        ):
            raise Problem(422, "writing_scope_immutable")
        payload = found["payload"]
        if found["kind"] != "publication" and payload.keys() & {
            "citation_markup",
            "citation_url",
            "include_in_resume",
        }:
            raise Problem(422, "publication_metadata")
        if found["kind"] == "publication":
            from bonafides.profile_entries import ClaimPayload

            try:
                citation = ClaimPayload.model_validate(payload)
                if len(citation.text) > 3000 or (
                    citation.citation_markup is not None
                    and citation_text(citation.citation_markup) != citation.text
                ):
                    raise ValueError("invalid_citation")
            except ValueError as error:
                raise Problem(422, "invalid_citation") from error
    if claim_ids:
        count = conn.execute(
            text(
                "SELECT count(DISTINCT claim_id) FROM bonafides.claim_revisions WHERE owner_id=:owner AND id=ANY(CAST(:ids AS uuid[]))"
            ),
            {"owner": owner_id, "ids": claim_ids},
        ).scalar_one()
        if count != len(claim_ids):
            raise Problem(422, "duplicate_claim")
    # All membership and saved-pin checks precede the first filesystem/SQL mutation.
    if snapshot.portrait is not None:
        try:
            portrait_storage_key(conn, owner_id, snapshot.portrait)
            if settings is not None:
                portrait_bytes(conn, settings, owner_id, snapshot.portrait)
        except (OSError, ValueError) as error:
            raise Problem(422, "invalid_portrait_pin") from error
    if portrait_upload is not None:
        if settings is None:
            raise ValueError("portrait_settings_required")
        validate_portrait(portrait_upload)
        digest = hashlib.sha256(portrait_upload).hexdigest()
        existing = conn.execute(
            text("SELECT id FROM bonafides.blobs WHERE owner_id=:owner AND sha256=:sha"),
            {"owner": owner_id, "sha": digest},
        ).scalar_one_or_none()
        pin = PortraitPin(
            blob_id=existing or uuid4(), sha256=digest, byte_length=len(portrait_upload)
        )
        if existing is not None:
            try:
                portrait_bytes(conn, settings, owner_id, pin)
            except (OSError, ValueError) as error:
                raise Problem(422, "invalid_portrait_pin") from error
        else:
            try:
                blob = BlobStore(settings.blob_root).put_bytes(owner_id, portrait_upload)
            except (OSError, ValueError) as error:
                raise Problem(503, "portrait_storage_unavailable") from error
            conn.execute(
                text("""INSERT INTO bonafides.blobs
                (owner_id,id,sha256,byte_length,media_type,storage_key,state)
                VALUES(:owner,:id,:sha,:size,'image/png',:key,'ready')"""),
                {
                    "owner": owner_id,
                    "id": pin.blob_id,
                    "sha": blob.sha256,
                    "size": blob.byte_length,
                    "key": blob.storage_key,
                },
            )
        snapshot = snapshot.model_copy(update={"portrait": pin})
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


def seed_synthetic_profile(conn: Connection, owner_id: UUID, reason: str) -> dict[str, object]:
    profile_id = conn.execute(
        text("SELECT id FROM bonafides.profiles WHERE owner_id=:owner"), {"owner": owner_id}
    ).scalar_one()
    claims: list[UUID] = []
    for fact in SYNTHETIC_FACTS:
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
            display_name=SYNTHETIC_DISPLAY_NAME,
            headline=SYNTHETIC_HEADLINE,
            claim_revision_ids=claims,
        ),
        reason,
    )


def build_router(db: Database, settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/v1")
    from bonafides.profile_entries import build_entry_router

    router.include_router(build_entry_router(db))

    @router.get("/profile")
    def profile(
        response: Response,
        owner: Annotated[Owner, Depends(require_owner)],
        application_id: UUID | None = None,
    ) -> dict[str, object]:
        with db.transaction() as conn:
            result = get_profile(conn, owner.id, application_id)
        response.headers["ETag"] = f'"{result["version"]}"'
        return result

    @router.get("/profile/portrait/{blob_id}")
    def portrait(blob_id: UUID, owner: Annotated[Owner, Depends(require_owner)]) -> Response:
        with db.transaction() as conn:
            row = (
                conn.execute(
                    text("""SELECT sha256,byte_length FROM bonafides.blobs
                WHERE owner_id=:owner AND id=:id AND state='ready' AND media_type='image/png'"""),
                    {"owner": owner.id, "id": blob_id},
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise Problem(404, "portrait_not_found")
            try:
                pin = PortraitPin(
                    blob_id=blob_id, sha256=row["sha256"], byte_length=row["byte_length"]
                )
                data = portrait_bytes(conn, settings, owner.id, pin)
            except (OSError, ValueError) as error:
                raise Problem(422, "invalid_portrait_pin") from error
        return Response(
            content=data,
            media_type="image/png",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    @router.put("/profile")
    def save(
        body: ProfileUpdate,
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
                updates = {
                    field: current.get(field, default)
                    for field, default in PRESERVED_SNAPSHOT_FIELDS.items()
                    if field not in body.model_fields_set
                }
                snapshot = ProfileSnapshot.model_validate(
                    {**body.model_dump(mode="json", exclude={"portrait_upload"}), **updates}
                )
                upload = None
                if body.portrait_upload is not None:
                    try:
                        upload = base64.b64decode(body.portrait_upload, validate=True)
                        validate_portrait(upload)
                    except (ValueError, binascii.Error) as error:
                        raise Problem(422, "invalid_portrait_upload") from error
                return 200, _save(
                    connection,
                    owner.id,
                    snapshot,
                    "owner_save",
                    portrait_upload=upload,
                    settings=settings,
                )

            request_body = body.model_dump(
                mode="json",
                exclude=(set(PRESERVED_SNAPSHOT_FIELDS) | {"portrait_upload"})
                - body.model_fields_set,
            )
            # exclude_if preserves historical digests, not command intent: explicit
            # clears/defaults must remain distinct from omitted legacy fields.
            for field in body.model_fields_set - request_body.keys():
                request_body[field] = getattr(body, field)
            status, result = command(
                conn,
                owner.id,
                "PUT",
                "/api/v1/profile",
                {},
                request_body,
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
                return 200, seed_synthetic_profile(connection, owner.id, "benchmark_SYN-CORE")

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
