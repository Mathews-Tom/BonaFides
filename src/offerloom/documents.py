# SPDX-License-Identifier: MIT OR Apache-2.0
"""Evidence-pinned deterministic M0 documents; workflow success is not review."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Annotated, Final, Literal
from uuid import UUID, uuid4, uuid5

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Connection, text

from offerloom.auth import Owner, require_owner
from offerloom.config import Settings
from offerloom.db import Database
from offerloom.problems import Problem
from offerloom.storage import BlobStore, StoredBlob

TEMPLATE_VERSION: Final = "offerloom-base-v1"
RENDERER_VERSION: Final = "weasyprint-70-offerloom-base-v1"
Key = Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")]
BoundedText = Annotated[str, Field(min_length=1, max_length=4000)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class DocumentBlock(StrictModel):
    block_key: Key
    text: BoundedText
    claim_kind: Literal["factual", "intent", "greeting"]
    claim_revision_ids: list[UUID] = Field(max_length=16)
    evidence_revision_ids: list[UUID] = Field(max_length=16)


class DocumentSection(StrictModel):
    key: Key
    heading: Annotated[str, Field(min_length=1, max_length=120)]
    blocks: list[DocumentBlock] = Field(min_length=1, max_length=128)


class DocumentBody(StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=160)]
    sections: list[DocumentSection] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def unique_bounded_keys(self) -> DocumentBody:
        section_keys = [section.key for section in self.sections]
        blocks = [block for section in self.sections for block in section.blocks]
        keys = [block.block_key for block in blocks]
        if len(section_keys) != len(set(section_keys)) or len(keys) != len(set(keys)):
            raise ValueError("duplicate_document_key")
        if len(blocks) > 128 or sum(len(block.text) for block in blocks) > 100_000:
            raise ValueError("document_too_large")
        for block in blocks:
            for references in (block.claim_revision_ids, block.evidence_revision_ids):
                if len(references) != len(set(references)):
                    raise ValueError("duplicate_document_reference")
        return self


def canonical(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def body_digest(body: DocumentBody) -> str:
    return hashlib.sha256(canonical(body.model_dump(mode="json")).encode()).hexdigest()


def parse_body(value: object) -> DocumentBody:
    # JSON mode accepts UUID wire strings, but never coerces numbers or unknown fields.
    return DocumentBody.model_validate_json(canonical(value), strict=True)


@dataclass(frozen=True)
class Fact:
    claim_id: UUID
    evidence_id: UUID
    claim_text: str
    evidence_text: str
    source: str
    claim_sha256: str
    evidence_sha256: str
    status: Literal["supported", "user_asserted"]


@dataclass(frozen=True)
class PreparedDocument:
    profile_revision_id: UUID
    body: DocumentBody
    facts: tuple[Fact, ...]


@dataclass(frozen=True)
class PreparedRender:
    document_id: UUID
    revision_id: UUID
    content_sha256: str
    body: DocumentBody


def _facts(conn: Connection, owner_id: UUID, profile_revision_id: UUID) -> tuple[Fact, ...]:
    profile = (
        conn.execute(
            text(
                "SELECT snapshot FROM offerloom.profile_revisions WHERE owner_id=:owner AND id=:id"
            ),
            {"owner": owner_id, "id": profile_revision_id},
        )
        .mappings()
        .first()
    )
    if profile is None:
        raise Problem(404, "not_found")
    rows = (
        conn.execute(
            text("""
        SELECT c.id AS claim_id,c.payload AS claim_payload,c.content_sha256 AS claim_sha256,
               e.id AS evidence_id,e.payload AS evidence_payload,e.content_sha256 AS evidence_sha256,
               l.support_kind,l.excerpt
        FROM offerloom.profile_revision_claims p
        JOIN offerloom.claim_revisions c ON c.owner_id=p.owner_id AND c.id=p.claim_revision_id
        JOIN offerloom.claim_evidence_links l ON l.owner_id=c.owner_id AND l.claim_revision_id=c.id
        JOIN offerloom.evidence_revisions e ON e.owner_id=l.owner_id AND e.id=l.evidence_revision_id
        WHERE p.owner_id=:owner AND p.profile_revision_id=:profile
        ORDER BY c.id,e.id
    """),
            {"owner": owner_id, "profile": profile_revision_id},
        )
        .mappings()
        .all()
    )
    selected = profile["snapshot"].get("claim_revision_ids", [])
    order = {str(identifier): index for index, identifier in enumerate(selected)}
    facts: list[Fact] = []
    for row in rows:
        claim = row["claim_payload"]
        evidence = row["evidence_payload"]
        claim_text = claim.get("text")
        evidence_text = evidence.get("text")
        source = evidence.get("source")
        if (
            not isinstance(claim_text, str)
            or not isinstance(evidence_text, str)
            or not isinstance(source, str)
        ):
            raise Problem(409, "unsupported_claims")
        if (
            not claim_text
            or len(claim_text) > 4000
            or len(evidence_text) > 100_000
            or len(source) > 2000
        ):
            raise Problem(409, "unsupported_claims")
        if (
            hashlib.sha256(canonical(claim).encode()).hexdigest() != row["claim_sha256"]
            or hashlib.sha256(canonical(evidence).encode()).hexdigest() != row["evidence_sha256"]
        ):
            raise Problem(409, "evidence_integrity_failed")
        if (
            row["support_kind"] not in ("direct", "user_asserted")
            or row["excerpt"] != claim_text
            or claim_text not in evidence_text
        ):
            continue
        if str(row["claim_id"]) not in order:
            raise Problem(409, "profile_integrity_failed")
        facts.append(
            Fact(
                row["claim_id"],
                row["evidence_id"],
                claim_text,
                evidence_text,
                source,
                row["claim_sha256"],
                row["evidence_sha256"],
                "supported" if row["support_kind"] == "direct" else "user_asserted",
            )
        )
    facts.sort(key=lambda fact: (order[str(fact.claim_id)], str(fact.evidence_id)))
    return tuple(facts)


def unsupported_paths(body: DocumentBody, facts: tuple[Fact, ...]) -> list[str]:
    """M0 permits exact saved facts only, not semantic inference from a citation."""
    unsupported: list[str] = []
    if body.title != "Base résumé":
        unsupported.append("/title")
    for section_index, section in enumerate(body.sections):
        if section.heading != "Profile evidence":
            unsupported.append(f"/sections/{section_index}/heading")
        for block_index, block in enumerate(section.blocks):
            matches = [
                fact
                for fact in facts
                if fact.claim_id in block.claim_revision_ids
                and fact.evidence_id in block.evidence_revision_ids
                and fact.claim_text == block.text
            ]
            if (
                block.claim_kind != "factual"
                or len(block.claim_revision_ids) != 1
                or not matches
                or {fact.evidence_id for fact in matches} != set(block.evidence_revision_ids)
            ):
                unsupported.append(f"/sections/{section_index}/blocks/{block_index}")
    return unsupported


def prepare_synthetic(
    conn: Connection, owner_id: UUID, profile_revision_id: UUID
) -> PreparedDocument:
    facts = _facts(conn, owner_id, profile_revision_id)
    by_claim: dict[UUID, list[Fact]] = {}
    for fact in facts:
        by_claim.setdefault(fact.claim_id, []).append(fact)
    if not by_claim:
        raise Problem(409, "empty_usable_profile")
    body = DocumentBody(
        title="Base résumé",
        sections=[
            DocumentSection(
                key="saved-facts",
                heading="Profile evidence",
                blocks=[
                    DocumentBlock(
                        block_key=f"claim-{claim.hex}",
                        text=items[0].claim_text,
                        claim_kind="factual",
                        claim_revision_ids=[claim],
                        evidence_revision_ids=[item.evidence_id for item in items],
                    )
                    for claim, items in by_claim.items()
                ],
            )
        ],
    )
    return PreparedDocument(profile_revision_id, body, facts)


def publish_document(
    conn: Connection, owner_id: UUID, workflow_id: UUID, prepared: PreparedDocument
) -> dict[str, object]:
    facts = _facts(conn, owner_id, prepared.profile_revision_id)
    if facts != prepared.facts or unsupported_paths(prepared.body, facts):
        raise Problem(409, "unsupported_claims")
    revision_id = uuid5(workflow_id, "document-revision")
    existing = conn.execute(
        text(
            "SELECT document_draft_id FROM offerloom.document_revisions WHERE owner_id=:owner AND id=:id"
        ),
        {"owner": owner_id, "id": revision_id},
    ).scalar_one_or_none()
    if existing is not None:
        return {"document_id": str(existing), "revision_id": str(revision_id)}
    draft = (
        conn.execute(
            text(
                "SELECT id,version FROM offerloom.document_drafts WHERE owner_id=:owner AND scope='base' FOR UPDATE"
            ),
            {"owner": owner_id},
        )
        .mappings()
        .first()
    )
    document_id = uuid4() if draft is None else draft["id"]
    if draft is None:
        conn.execute(
            text(
                "INSERT INTO offerloom.document_drafts(owner_id,id,kind,scope,profile_revision_id) VALUES(:owner,:id,'resume','base',:profile)"
            ),
            {"owner": owner_id, "id": document_id, "profile": prepared.profile_revision_id},
        )
    revision_no = conn.execute(
        text(
            "SELECT COALESCE(MAX(revision_no),0)+1 FROM offerloom.document_revisions WHERE owner_id=:owner AND document_draft_id=:id"
        ),
        {"owner": owner_id, "id": document_id},
    ).scalar_one()
    provenance = {
        "schema_version": 1,
        "mode": "deterministic_base",
        "workflow_id": str(workflow_id),
        "profile_revision_id": str(prepared.profile_revision_id),
        "model_run": None,
        "template_version": TEMPLATE_VERSION,
        "facts": [
            {
                "claim_revision_id": str(fact.claim_id),
                "evidence_revision_id": str(fact.evidence_id),
                "claim_text": fact.claim_text,
                "evidence_text": fact.evidence_text,
                "source": fact.source,
                "claim_sha256": fact.claim_sha256,
                "evidence_sha256": fact.evidence_sha256,
                "status": fact.status,
            }
            for fact in facts
        ],
    }
    conn.execute(
        text("""INSERT INTO offerloom.document_revisions(owner_id,id,document_draft_id,revision_no,profile_revision_id,scope,body,posting_snapshot,provenance_snapshot,template_version,content_sha256,origin,reason)
        VALUES(:owner,:id,:draft,:revision_no,:profile,'base',CAST(:body AS jsonb),NULL,CAST(:provenance AS jsonb),:template,:digest,'deterministic','M0 saved-evidence deterministic assembly')"""),
        {
            "owner": owner_id,
            "id": revision_id,
            "draft": document_id,
            "revision_no": revision_no,
            "profile": prepared.profile_revision_id,
            "body": canonical(prepared.body.model_dump(mode="json")),
            "provenance": canonical(provenance),
            "template": TEMPLATE_VERSION,
            "digest": body_digest(prepared.body),
        },
    )
    for section_index, section in enumerate(prepared.body.sections):
        for block_index, block in enumerate(section.blocks):
            for fact in facts:
                if (
                    fact.claim_id in block.claim_revision_ids
                    and fact.evidence_id in block.evidence_revision_ids
                ):
                    conn.execute(
                        text(
                            "INSERT INTO offerloom.document_claim_links(owner_id,document_revision_id,claim_path,claim_revision_id,evidence_revision_id,excerpt,status) VALUES(:owner,:revision,:path,:claim,:evidence,:excerpt,:status)"
                        ),
                        {
                            "owner": owner_id,
                            "revision": revision_id,
                            "path": f"/sections/{section_index}/blocks/{block_index}",
                            "claim": fact.claim_id,
                            "evidence": fact.evidence_id,
                            "excerpt": fact.claim_text,
                            "status": fact.status,
                        },
                    )
    conn.execute(
        text(
            "UPDATE offerloom.document_drafts SET current_revision_id=:revision,profile_revision_id=:profile,stale_at=NULL,version=version+:increment WHERE owner_id=:owner AND id=:id"
        ),
        {
            "revision": revision_id,
            "profile": prepared.profile_revision_id,
            "increment": 0 if draft is None else 1,
            "owner": owner_id,
            "id": document_id,
        },
    )
    from offerloom.commands import append_outbox

    append_outbox(
        conn,
        owner_id,
        "document.revision_created",
        "document",
        document_id,
        {"revision_id": str(revision_id)},
        aggregate_version=int(revision_no),
    )
    return {"document_id": str(document_id), "revision_id": str(revision_id)}


def _document(conn: Connection, owner_id: UUID, document_id: UUID) -> dict[str, object]:
    row = (
        conn.execute(
            text("""SELECT d.id,d.version,r.id AS revision_id,r.revision_no,r.profile_revision_id,r.body,r.content_sha256,
        EXISTS(SELECT 1 FROM offerloom.document_reviews v WHERE v.owner_id=r.owner_id AND v.document_revision_id=r.id AND v.decision='accepted' AND v.reviewed_content_sha256=r.content_sha256) AS reviewed,
        r.profile_revision_id<>(SELECT current_revision_id FROM offerloom.profiles WHERE owner_id=d.owner_id) AS stale
        FROM offerloom.document_drafts d JOIN offerloom.document_revisions r ON r.owner_id=d.owner_id AND r.id=d.current_revision_id
        WHERE d.owner_id=:owner AND d.id=:id"""),
            {"owner": owner_id, "id": document_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise Problem(404, "not_found")
    body = parse_body(row["body"])
    if body_digest(body) != row["content_sha256"]:
        raise Problem(409, "document_integrity_failed")
    unsupported = unsupported_paths(body, _facts(conn, owner_id, row["profile_revision_id"]))
    links = (
        conn.execute(
            text(
                "SELECT claim_path,claim_revision_id,evidence_revision_id,excerpt,status FROM offerloom.document_claim_links WHERE owner_id=:owner AND document_revision_id=:id"
            ),
            {"owner": owner_id, "id": row["revision_id"]},
        )
        .mappings()
        .all()
    )
    for si, section in enumerate(body.sections):
        for bi, block in enumerate(section.blocks):
            path = f"/sections/{si}/blocks/{bi}"
            matching = [
                link
                for link in links
                if link["claim_path"] == path
                and link["excerpt"] == block.text
                and link["status"] in ("supported", "user_asserted")
                and link["claim_revision_id"] in block.claim_revision_ids
            ]
            if {link["evidence_revision_id"] for link in matching} != set(
                block.evidence_revision_ids
            ) and path not in unsupported:
                unsupported.append(path)
    return {
        "id": str(row["id"]),
        "version": row["version"],
        "current_revision": {
            "id": str(row["revision_id"]),
            "revision_no": row["revision_no"],
            "body": body.model_dump(mode="json"),
            "content_sha256": row["content_sha256"],
        },
        "reviewed": row["reviewed"],
        "unsupported_claims": unsupported,
        "stale": bool(row["stale"]),
        "stale_reason": "upstream_profile_revision_changed" if row["stale"] else None,
    }


def prepare_render(
    conn: Connection, owner_id: UUID, document_id: UUID, revision_id: UUID
) -> PreparedRender:
    document = _document(conn, owner_id, document_id)
    revision = document["current_revision"]
    assert isinstance(revision, dict)
    if revision["id"] != str(revision_id):
        raise Problem(409, "revision_mismatch")
    if document["unsupported_claims"]:
        raise Problem(409, "unsupported_claims")
    if not document["reviewed"]:
        raise Problem(409, "review_required")
    digest = revision["content_sha256"]
    assert isinstance(digest, str)
    return PreparedRender(document_id, revision_id, digest, parse_body(revision["body"]))


def publish_artifact(
    conn: Connection, owner_id: UUID, workflow_id: UUID, prepared: PreparedRender, blob: StoredBlob
) -> dict[str, object]:
    # Called only within the worker's fenced publication transaction; recheck gate.
    conn.execute(
        text(
            "SELECT id FROM offerloom.document_drafts WHERE owner_id=:owner AND id=:id FOR UPDATE"
        ),
        {"owner": owner_id, "id": prepared.document_id},
    )
    current = prepare_render(conn, owner_id, prepared.document_id, prepared.revision_id)
    if (
        current.content_sha256 != prepared.content_sha256
        or body_digest(prepared.body) != current.content_sha256
    ):
        raise Problem(409, "document_integrity_failed")
    conn.execute(
        text(
            """INSERT INTO offerloom.blobs(owner_id,sha256,byte_length,media_type,storage_key,state) VALUES(:owner,:digest,:size,'application/pdf',:key,'ready') ON CONFLICT(owner_id,sha256) DO NOTHING"""
        ),
        {
            "owner": owner_id,
            "digest": blob.sha256,
            "size": blob.byte_length,
            "key": blob.storage_key,
        },
    )
    stored = (
        conn.execute(
            text(
                "SELECT id,byte_length,storage_key,state FROM offerloom.blobs WHERE owner_id=:owner AND sha256=:digest FOR UPDATE"
            ),
            {"owner": owner_id, "digest": blob.sha256},
        )
        .mappings()
        .one()
    )
    if (
        stored["state"] != "ready"
        or stored["byte_length"] != blob.byte_length
        or stored["storage_key"] != blob.storage_key
    ):
        raise Problem(409, "blob_integrity_failed")
    blob_id = stored["id"]
    artifact_id = conn.execute(
        text(
            """INSERT INTO offerloom.document_artifacts(owner_id,document_revision_id,blob_id,renderer_version,format) VALUES(:owner,:revision,:blob,:renderer,'pdf') ON CONFLICT(owner_id,document_revision_id,renderer_version,format) DO NOTHING RETURNING id"""
        ),
        {
            "owner": owner_id,
            "revision": prepared.revision_id,
            "blob": blob_id,
            "renderer": RENDERER_VERSION,
        },
    ).scalar_one_or_none()
    if artifact_id is None:
        artifact_id = conn.execute(
            text(
                "SELECT id FROM offerloom.document_artifacts WHERE owner_id=:owner AND document_revision_id=:revision AND renderer_version=:renderer AND format='pdf'"
            ),
            {"owner": owner_id, "revision": prepared.revision_id, "renderer": RENDERER_VERSION},
        ).scalar_one()
    from offerloom.commands import append_outbox

    append_outbox(
        conn,
        owner_id,
        "document.artifact_ready",
        "artifact",
        artifact_id,
        {"artifact_id": str(artifact_id), "workflow_id": str(workflow_id)},
    )
    return {"document_id": str(prepared.document_id), "artifact_id": str(artifact_id)}


class ReviewRequest(StrictModel):
    revision_id: Annotated[UUID, Field(strict=False)]
    decision: Literal["accepted"]
    content_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class RenderRequest(StrictModel):
    revision_id: Annotated[UUID, Field(strict=False)]


def build_router(db: Database, settings: Settings) -> APIRouter:
    from offerloom.commands import command
    from offerloom.workflows import enqueue, get_workflow

    router = APIRouter(prefix="/api/v1")

    @router.get("/documents/{document_id}")
    def get_document(
        document_id: UUID, response: Response, owner: Annotated[Owner, Depends(require_owner)]
    ) -> dict[str, object]:
        with db.transaction() as conn:
            result = _document(conn, owner.id, document_id)
        response.headers["ETag"] = f'"{result["version"]}"'
        response.headers["Cache-Control"] = "private, no-store"
        return result

    @router.post("/documents/{document_id}/review")
    def review(
        document_id: UUID,
        body: ReviewRequest,
        request: Request,
        owner: Annotated[Owner, Depends(require_owner)],
    ) -> Response:
        def effect(conn: Connection) -> tuple[int, dict[str, object]]:
            document = _document(conn, owner.id, document_id)
            revision = document["current_revision"]
            assert isinstance(revision, dict)
            if (
                revision["id"] != str(body.revision_id)
                or revision["content_sha256"] != body.content_sha256
            ):
                raise Problem(409, "revision_mismatch")
            if document["unsupported_claims"]:
                raise Problem(409, "unsupported_claims")
            conn.execute(
                text(
                    "INSERT INTO offerloom.document_reviews(owner_id,document_revision_id,decision,reviewed_content_sha256) VALUES(:owner,:revision,'accepted',:digest) ON CONFLICT(owner_id,document_revision_id) DO NOTHING"
                ),
                {"owner": owner.id, "revision": body.revision_id, "digest": body.content_sha256},
            )
            conn.execute(
                text(
                    "UPDATE offerloom.document_drafts SET version=version+1 WHERE owner_id=:owner AND id=:id"
                ),
                {"owner": owner.id, "id": document_id},
            )
            conn.execute(
                text(
                    "INSERT INTO offerloom.audit_events(owner_id,action,actor_kind,actor_id,request_id,entity_type,entity_id,detail) VALUES(:owner,'document_reviewed','owner',:actor,:request,'document',:document,CAST(:detail AS jsonb))"
                ),
                {
                    "owner": owner.id,
                    "actor": str(owner.id),
                    "request": request.state.request_id,
                    "document": document_id,
                    "detail": canonical(
                        {
                            "revision_id": str(body.revision_id),
                            "content_sha256": body.content_sha256,
                        }
                    ),
                },
            )
            return 200, _document(conn, owner.id, document_id)

        with db.transaction() as conn:
            status, result = command(
                conn,
                owner.id,
                "POST",
                "/documents/{document_id}/review",
                {"document_id": str(document_id)},
                body.model_dump(mode="json"),
                request.headers.get("if-match"),
                request.headers.get("idempotency-key"),
                "document",
                document_id,
                effect,
            )
        return Response(
            canonical(result),
            status_code=status,
            media_type="application/json",
            headers={"ETag": f'"{result["version"]}"', "Cache-Control": "private, no-store"},
        )

    @router.post("/documents/{document_id}/renders")
    def render(
        document_id: UUID,
        body: RenderRequest,
        request: Request,
        owner: Annotated[Owner, Depends(require_owner)],
    ) -> Response:
        def effect(conn: Connection) -> tuple[int, dict[str, object]]:
            prepared = prepare_render(conn, owner.id, document_id, body.revision_id)
            workflow_id = enqueue(
                conn,
                owner.id,
                "pdf_render",
                {
                    "schema_version": 1,
                    "document_id": str(document_id),
                    "revision_id": str(body.revision_id),
                    "content_sha256": prepared.content_sha256,
                },
            )
            return 202, get_workflow(conn, owner.id, workflow_id)

        with db.transaction() as conn:
            status, result = command(
                conn,
                owner.id,
                "POST",
                "/documents/{document_id}/renders",
                {"document_id": str(document_id)},
                body.model_dump(mode="json"),
                request.headers.get("if-match"),
                request.headers.get("idempotency-key"),
                "document",
                document_id,
                effect,
            )
        return Response(
            canonical(result),
            status_code=status,
            media_type="application/json",
            headers={
                "Location": f"/api/v1/workflows/{result['id']}",
                "ETag": f'"{result["version"]}"',
                "Cache-Control": "private, no-store",
            },
        )

    @router.get("/artifacts/{artifact_id}")
    def artifact(artifact_id: UUID, owner: Annotated[Owner, Depends(require_owner)]) -> Response:
        with db.transaction() as conn:
            row = (
                conn.execute(
                    text(
                        "SELECT b.sha256,b.byte_length,b.storage_key FROM offerloom.document_artifacts a JOIN offerloom.blobs b ON b.owner_id=a.owner_id AND b.id=a.blob_id WHERE a.owner_id=:owner AND a.id=:id AND b.state='ready' AND a.format='pdf'"
                    ),
                    {"owner": owner.id, "id": artifact_id},
                )
                .mappings()
                .first()
            )
        if row is None:
            raise Problem(404, "not_found")
        try:
            data = BlobStore(settings.blob_root).read(
                owner.id, row["storage_key"], row["sha256"], row["byte_length"]
            )
        except (OSError, ValueError) as error:
            raise Problem(410, "artifact_gone") from error
        return Response(
            data,
            media_type="application/pdf",
            headers={
                "Content-Disposition": 'attachment; filename="offerloom-resume.pdf"',
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    return router
