# SPDX-License-Identifier: MIT OR Apache-2.0
"""Evidence-pinned immutable documents, exact review and private PDF artifacts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Annotated, Final, Literal
from uuid import UUID, uuid4, uuid5

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import Connection, text

from bonafides.auth import Owner, require_owner
from bonafides.claims import grounded, render_claim, structured_claim
from bonafides.config import Settings
from bonafides.db import Database
from bonafides.document_frame import PrintableFrame, template_digest
from bonafides.problems import Problem
from bonafides.storage import BlobStore, StoredBlob

TEMPLATE_VERSION: Final = "bonafides-base-v1"
RENDERER_VERSION: Final = "weasyprint-70-bonafides-base-v1"
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
    template_version: str = TEMPLATE_VERSION
    frame: PrintableFrame | None = None
    template_sha256: str | None = None


def _facts(conn: Connection, owner_id: UUID, profile_revision_id: UUID) -> tuple[Fact, ...]:
    profile = (
        conn.execute(
            text(
                "SELECT snapshot FROM bonafides.profile_revisions WHERE owner_id=:owner AND id=:id"
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
        FROM bonafides.profile_revision_claims p
        JOIN bonafides.claim_revisions c ON c.owner_id=p.owner_id AND c.id=p.claim_revision_id
        JOIN bonafides.claim_evidence_links l ON l.owner_id=c.owner_id AND l.claim_revision_id=c.id
        JOIN bonafides.evidence_revisions e ON e.owner_id=l.owner_id AND e.id=l.evidence_revision_id
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
        try:
            structured = structured_claim(claim)
            claim_text = render_claim(structured) if structured else claim.get("text")
        except ValidationError as error:
            raise Problem(409, "unsupported_claims") from error
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
        # A literal claim must be quoted verbatim; a structured claim's literal parts must all
        # appear in a verbatim evidence quote, since its sentence is derived, never stored.
        quoted = (
            row["excerpt"] in evidence_text and grounded(structured, row["excerpt"])
            if structured
            else row["excerpt"] == claim_text and claim_text in evidence_text
        )
        if row["support_kind"] not in ("direct", "user_asserted") or not quoted:
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
    """Assembly permits exact saved facts only, not semantic inference from a citation."""
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
            "SELECT document_draft_id FROM bonafides.document_revisions WHERE owner_id=:owner AND id=:id"
        ),
        {"owner": owner_id, "id": revision_id},
    ).scalar_one_or_none()
    if existing is not None:
        return {"document_id": str(existing), "revision_id": str(revision_id)}
    draft = (
        conn.execute(
            text(
                "SELECT id,version FROM bonafides.document_drafts WHERE owner_id=:owner AND scope='base' FOR UPDATE"
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
                "INSERT INTO bonafides.document_drafts(owner_id,id,kind,scope,profile_revision_id) VALUES(:owner,:id,'resume','base',:profile)"
            ),
            {"owner": owner_id, "id": document_id, "profile": prepared.profile_revision_id},
        )
    revision_no = conn.execute(
        text(
            "SELECT COALESCE(MAX(revision_no),0)+1 FROM bonafides.document_revisions WHERE owner_id=:owner AND document_draft_id=:id"
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
        text("""INSERT INTO bonafides.document_revisions(owner_id,id,document_draft_id,revision_no,profile_revision_id,scope,body,posting_snapshot,provenance_snapshot,template_version,content_sha256,origin,reason)
        VALUES(:owner,:id,:draft,:revision_no,:profile,'base',CAST(:body AS jsonb),NULL,CAST(:provenance AS jsonb),:template,:digest,'deterministic','saved-evidence deterministic assembly')"""),
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
                            "INSERT INTO bonafides.document_claim_links(owner_id,document_revision_id,claim_path,claim_revision_id,evidence_revision_id,excerpt,status) VALUES(:owner,:revision,:path,:claim,:evidence,:excerpt,:status)"
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
            "UPDATE bonafides.document_drafts SET current_revision_id=:revision,profile_revision_id=:profile,stale_at=NULL,version=version+:increment WHERE owner_id=:owner AND id=:id"
        ),
        {
            "revision": revision_id,
            "profile": prepared.profile_revision_id,
            "increment": 0 if draft is None else 1,
            "owner": owner_id,
            "id": document_id,
        },
    )
    from bonafides.commands import append_outbox

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


def _document(
    conn: Connection, owner_id: UUID, document_id: UUID, settings: Settings | None = None
) -> dict[str, object]:
    from bonafides.document_generation import POLICY, revision_view

    row = (
        conn.execute(
            text("""SELECT d.*,
        d.profile_revision_id<>(SELECT current_revision_id FROM bonafides.profiles WHERE owner_id=d.owner_id) AS profile_stale
        FROM bonafides.document_drafts d WHERE d.owner_id=:owner AND d.id=:id AND d.deleted_at IS NULL"""),
            {"owner": owner_id, "id": document_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None or row["current_revision_id"] is None:
        raise Problem(404, "not_found")
    revision = revision_view(conn, owner_id, document_id, row["current_revision_id"])
    body = parse_body(revision["body"])
    issues = revision["validation_issues"]
    assert isinstance(issues, list)
    unsupported = list(dict.fromkeys(issue["path"] for issue in issues))
    links = (
        conn.execute(
            text(
                "SELECT claim_path,claim_revision_id,evidence_revision_id,excerpt,status FROM bonafides.document_claim_links WHERE owner_id=:owner AND document_revision_id=:id"
            ),
            {"owner": owner_id, "id": row["current_revision_id"]},
        )
        .mappings()
        .all()
    )
    for si, section in enumerate(body.sections):
        for bi, block in enumerate(section.blocks):
            if block.claim_kind != "factual":
                continue
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
                issues.append({"path": path, "code": "document_claim_link_missing"})
    reviewed = conn.execute(
        text(
            "SELECT EXISTS(SELECT 1 FROM bonafides.document_reviews WHERE owner_id=:owner AND document_revision_id=:id AND decision='accepted' AND reviewed_content_sha256=:sha)"
        ),
        {"owner": owner_id, "id": row["current_revision_id"], "sha": revision["content_sha256"]},
    ).scalar_one()
    artifacts = (
        conn.execute(
            text(
                "SELECT id,document_revision_id,format FROM bonafides.document_artifacts WHERE owner_id=:owner AND document_revision_id=:id AND format='pdf' ORDER BY created_at,id"
            ),
            {"owner": owner_id, "id": row["current_revision_id"]},
        )
        .mappings()
        .all()
    )
    stale_reason = "upstream_profile_revision_changed" if row["profile_stale"] else None
    provenance = revision["provenance_snapshot"]
    assert isinstance(provenance, dict)
    if reviewed and provenance.get("schema_version") == 2:
        keys = conn.execute(
            text(
                "SELECT reviewed_block_keys FROM bonafides.document_reviews WHERE owner_id=:owner AND document_revision_id=:id"
            ),
            {"owner": owner_id, "id": row["current_revision_id"]},
        ).scalar_one()
        required = {
            block.block_key
            for section in body.sections
            for block in section.blocks
            if block.claim_kind == "factual"
        }
        reviewed = isinstance(keys, list) and len(keys) == len(set(keys)) and set(keys) == required
    if provenance.get("schema_version") == 2:
        try:
            if template_digest(str(revision["template_version"])) != provenance.get(
                "template_sha256"
            ):
                stale_reason = stale_reason or "pinned_document_template_changed"
        except (ValueError, OSError):
            stale_reason = stale_reason or "pinned_document_template_unavailable"
        if provenance.get("policy_version") != POLICY:
            stale_reason = stale_reason or "document_validation_policy_changed"
    if row["scope"] == "tailored" and provenance.get("schema_version") == 2:
        from bonafides import suitability

        application = (
            conn.execute(
                text(
                    "SELECT o.posting_id FROM bonafides.applications a JOIN bonafides.saved_opportunities o ON o.owner_id=a.owner_id AND o.id=a.opportunity_id WHERE a.owner_id=:owner AND a.id=:id"
                ),
                {"owner": owner_id, "id": row["application_id"]},
            )
            .mappings()
            .one_or_none()
        )
        if application is not None:
            posting = suitability._posting(conn, owner_id, application["posting_id"])
            requirements = suitability._current_requirements(conn, owner_id, posting["id"])
            if requirements is None or str(requirements["id"]) != provenance.get(
                "requirements_revision_id"
            ):
                stale_reason = stale_reason or "upstream_requirements_revision_changed"
            elif suitability._posting_digest(posting) != provenance.get("posting_content_sha256"):
                stale_reason = stale_reason or "upstream_posting_content_changed"
        if settings is not None:
            from bonafides import local_model

            try:
                route = local_model.load_route(settings.local_model_route_file)
            except local_model.ModelFailure:
                route = None
            model_run = provenance.get("model_run")
            if (
                not isinstance(model_run, dict)
                or route is None
                or route.identity() != model_run.get("route")
            ):
                stale_reason = stale_reason or "generation_model_route_changed"
            if provenance.get("settings_version") != suitability._settings_version(conn, owner_id):
                stale_reason = stale_reason or "generation_privacy_settings_changed"
    return {
        "id": str(row["id"]),
        "version": row["version"],
        "kind": row["kind"],
        "scope": row["scope"],
        "application_id": str(row["application_id"]) if row["application_id"] else None,
        "current_revision": revision,
        "reviewed": bool(reviewed),
        "unsupported_claims": unsupported,
        "stale": stale_reason is not None,
        "stale_reason": stale_reason,
        "artifacts": [
            {
                "id": str(item["id"]),
                "revision_id": str(item["document_revision_id"]),
                "format": item["format"],
            }
            for item in artifacts
        ],
    }


def prepare_render(
    conn: Connection, owner_id: UUID, document_id: UUID, revision_id: UUID
) -> PreparedRender:
    from bonafides.document_generation import lock_workspace

    lock_workspace(conn, owner_id)
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
    from bonafides.document_generation import export_eligibility

    export_eligibility(conn, owner_id, revision)
    template = revision["template_version"]
    provenance = revision["provenance_snapshot"]
    assert isinstance(template, str) and isinstance(provenance, dict)
    frame = (
        PrintableFrame.model_validate(provenance["frame"])
        if provenance.get("schema_version") == 2
        else None
    )
    template_sha = (
        provenance.get("template_sha256") if provenance.get("schema_version") == 2 else None
    )
    if frame is not None and template_sha != template_digest(template):
        raise Problem(409, "document_template_unavailable")
    assert template_sha is None or isinstance(template_sha, str)
    return PreparedRender(
        document_id,
        revision_id,
        digest,
        parse_body(revision["body"]),
        template,
        frame,
        template_sha,
    )


def publish_artifact(
    conn: Connection, owner_id: UUID, workflow_id: UUID, prepared: PreparedRender, blob: StoredBlob
) -> dict[str, object]:
    # Called only within the worker's fenced publication transaction; recheck gate.
    conn.execute(
        text(
            "SELECT id FROM bonafides.document_drafts WHERE owner_id=:owner AND id=:id FOR UPDATE"
        ),
        {"owner": owner_id, "id": prepared.document_id},
    )
    current = prepare_render(conn, owner_id, prepared.document_id, prepared.revision_id)
    if current != prepared:
        raise Problem(409, "document_integrity_failed")
    renderer_version = (
        RENDERER_VERSION
        if prepared.template_version == TEMPLATE_VERSION
        else f"weasyprint-70-{prepared.template_version}"
    )
    conn.execute(
        text(
            """INSERT INTO bonafides.blobs(owner_id,sha256,byte_length,media_type,storage_key,state) VALUES(:owner,:digest,:size,'application/pdf',:key,'ready') ON CONFLICT(owner_id,sha256) DO NOTHING"""
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
                "SELECT id,byte_length,storage_key,state FROM bonafides.blobs WHERE owner_id=:owner AND sha256=:digest FOR UPDATE"
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
            """INSERT INTO bonafides.document_artifacts(owner_id,document_revision_id,blob_id,renderer_version,format) VALUES(:owner,:revision,:blob,:renderer,'pdf') ON CONFLICT(owner_id,document_revision_id,renderer_version,format) DO NOTHING RETURNING id"""
        ),
        {
            "owner": owner_id,
            "revision": prepared.revision_id,
            "blob": blob_id,
            "renderer": renderer_version,
        },
    ).scalar_one_or_none()
    if artifact_id is None:
        artifact_id = conn.execute(
            text(
                "SELECT id FROM bonafides.document_artifacts WHERE owner_id=:owner AND document_revision_id=:revision AND renderer_version=:renderer AND format='pdf'"
            ),
            {"owner": owner_id, "revision": prepared.revision_id, "renderer": renderer_version},
        ).scalar_one()
    from bonafides.commands import append_outbox

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
    reviewed_block_keys: list[Key] | None = Field(default=None, max_length=128)


class RenderRequest(StrictModel):
    revision_id: Annotated[UUID, Field(strict=False)]
    content_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    format: Literal["pdf"]


def build_router(db: Database, settings: Settings) -> APIRouter:
    from bonafides.commands import command
    from bonafides.workflows import enqueue, get_workflow

    router = APIRouter(prefix="/api/v1")

    @router.get("/documents/{document_id}")
    def get_document(
        document_id: UUID, response: Response, owner: Annotated[Owner, Depends(require_owner)]
    ) -> dict[str, object]:
        with db.transaction() as conn:
            result = _document(conn, owner.id, document_id, settings)
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
            document = _document(conn, owner.id, document_id, settings)
            revision = document["current_revision"]
            assert isinstance(revision, dict)
            if (
                revision["id"] != str(body.revision_id)
                or revision["content_sha256"] != body.content_sha256
            ):
                raise Problem(409, "revision_mismatch")
            if document["unsupported_claims"]:
                raise Problem(409, "unsupported_claims")
            provenance = revision["provenance_snapshot"]
            assert isinstance(provenance, dict)
            if provenance.get("schema_version") == 2:
                content = parse_body(revision["body"])
                required = {
                    block.block_key
                    for section in content.sections
                    for block in section.blocks
                    if block.claim_kind == "factual"
                }
                keys = body.reviewed_block_keys
                if keys is None or len(keys) != len(set(keys)) or set(keys) != required:
                    raise Problem(422, "reviewed_block_keys_mismatch")
            from bonafides.document_generation import export_eligibility

            export_eligibility(conn, owner.id, revision)
            conn.execute(
                text(
                    "INSERT INTO bonafides.document_reviews(owner_id,document_revision_id,decision,reviewed_content_sha256,reviewed_block_keys) VALUES(:owner,:revision,'accepted',:digest,CAST(:keys AS jsonb)) ON CONFLICT(owner_id,document_revision_id) DO NOTHING"
                ),
                {
                    "owner": owner.id,
                    "revision": body.revision_id,
                    "digest": body.content_sha256,
                    "keys": canonical(body.reviewed_block_keys)
                    if body.reviewed_block_keys is not None
                    else None,
                },
            )
            conn.execute(
                text(
                    "UPDATE bonafides.document_drafts SET version=version+1 WHERE owner_id=:owner AND id=:id"
                ),
                {"owner": owner.id, "id": document_id},
            )
            conn.execute(
                text(
                    "INSERT INTO bonafides.audit_events(owner_id,action,actor_kind,actor_id,request_id,entity_type,entity_id,detail) VALUES(:owner,'document_reviewed','owner',:actor,:request,'document',:document,CAST(:detail AS jsonb))"
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
            return 200, _document(conn, owner.id, document_id, settings)

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
            if prepared.content_sha256 != body.content_sha256:
                raise Problem(409, "document_content_changed")
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
                        "SELECT b.sha256,b.byte_length,b.storage_key FROM bonafides.document_artifacts a JOIN bonafides.blobs b ON b.owner_id=a.owner_id AND b.id=a.blob_id WHERE a.owner_id=:owner AND a.id=:id AND b.state='ready' AND a.format='pdf'"
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
                "Content-Disposition": 'attachment; filename="bonafides-resume.pdf"',
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    from bonafides.document_generation import install_routes

    install_routes(router, db, settings)
    return router
