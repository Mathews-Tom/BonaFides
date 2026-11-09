# SPDX-License-Identifier: MIT OR Apache-2.0
"""Private sealed-renderer measurements and reversible whole-block page fitting."""

from __future__ import annotations

import io
from collections.abc import Callable
from threading import Event
from typing import Annotated, Literal, cast
from uuid import UUID, uuid5

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BeforeValidator, Field, model_validator
from pypdf import PdfReader
from sqlalchemy import Connection, text

from bonafides import document_generation as generation
from bonafides import documents
from bonafides.auth import Owner, require_owner
from bonafides.commands import command
from bonafides.config import Settings
from bonafides.db import Database
from bonafides.document_frame import PrintableFrame, template_digest
from bonafides.problems import Problem
from bonafides.render_service import RenderClient, RenderFailure
from bonafides.workflows import Claim, assert_fence, enqueue, get_workflow, publish


def _strict_target(value: object) -> object:
    if type(value) is not int:
        raise ValueError("invalid_page_target")
    return value


PageTarget = Annotated[Literal[1, 2], BeforeValidator(_strict_target)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class SectionOrder(documents.StrictModel):
    key: documents.Key
    heading: Annotated[str, Field(min_length=1, max_length=120)]
    block_keys: list[documents.Key] = Field(min_length=1, max_length=128)


class Omission(documents.StrictModel):
    section_key: documents.Key
    block: documents.DocumentBlock
    reason: Literal["page_target", "owner"]


class PageFit(documents.StrictModel):
    page_target: PageTarget | None
    page_count: Annotated[int, Field(ge=1)] | None
    measured_content_sha256: Digest | None
    order: list[SectionOrder] = Field(min_length=1, max_length=16)
    omissions: list[Omission] = Field(max_length=127)

    @model_validator(mode="after")
    def complete_measurement(self) -> PageFit:
        if (self.page_count is None) != (self.measured_content_sha256 is None):
            raise ValueError("incomplete_page_measurement")
        return self


def inventory(body: documents.DocumentBody, provenance: dict[str, object]) -> PageFit:
    value = provenance.get("page_fit")
    if "page_fit" not in provenance:
        return PageFit(
            page_target=None,
            page_count=None,
            measured_content_sha256=None,
            order=[
                SectionOrder(
                    key=s.key, heading=s.heading, block_keys=[b.block_key for b in s.blocks]
                )
                for s in body.sections
            ],
            omissions=[],
        )
    fit = PageFit.model_validate_json(documents.canonical(value), strict=True)
    visible = {b.block_key: (s.key, b) for s in body.sections for b in s.blocks}
    hidden = {item.block.block_key: (item.section_key, item.block) for item in fit.omissions}
    if len(hidden) != len(fit.omissions) or visible.keys() & hidden.keys():
        raise ValueError("page_fit_key_collision")
    all_blocks = visible | hidden
    ordered = [key for section in fit.order for key in section.block_keys]
    if len(ordered) != len(set(ordered)) or set(ordered) != set(all_blocks):
        raise ValueError("page_fit_inventory_mismatch")
    expanded = documents.DocumentBody(
        title=body.title,
        sections=[
            documents.DocumentSection(
                key=section.key,
                heading=section.heading,
                blocks=[all_blocks[key][1] for key in section.block_keys],
            )
            for section in fit.order
        ],
    )
    # Reconstructing the logical document applies the ordinary bounds and reference checks.
    documents.parse_body(expanded.model_dump(mode="json"))
    for section in fit.order:
        if any(all_blocks[key][0] != section.key for key in section.block_keys):
            raise ValueError("page_fit_section_mismatch")
    projected = [
        s.model_copy(update={"blocks": [b for b in s.blocks if b.block_key in visible]})
        for s in expanded.sections
        if any(b.block_key in visible for b in s.blocks)
    ]
    if projected != body.sections:
        raise ValueError("page_fit_order_mismatch")
    return fit


def reconcile(body: documents.DocumentBody, provenance: dict[str, object]) -> dict[str, object]:
    """Keep hidden blocks across owner edits; ordinary visible deletion stays deletion."""
    if "page_fit" not in provenance:
        return provenance
    fit = PageFit.model_validate_json(documents.canonical(provenance["page_fit"]), strict=True)
    hidden = {item.block.block_key for item in fit.omissions}
    visible = {b.block_key for s in body.sections for b in s.blocks}
    if hidden & visible:
        raise Problem(422, "page_fit_key_collision")
    sections = {s.key: s for s in body.sections}
    order: list[SectionOrder] = []
    for previous in fit.order:
        section = sections.pop(previous.key, None)
        keys = [k for k in previous.block_keys if k in hidden]
        if section is not None:
            # Anchor hidden runs to the next surviving visible neighbor. This also
            # respects explicit visible reordering without replacing edited neighbors.
            pending: dict[str, list[str]] = {}
            tail: list[str] = []
            current_keys = {b.block_key for b in section.blocks}
            for key in previous.block_keys:
                if key in hidden:
                    tail.append(key)
                elif key in current_keys:
                    pending[key] = tail
                    tail = []
            keys = [
                key
                for block in section.blocks
                for key in [*pending.get(block.block_key, []), block.block_key]
            ] + tail
        if keys:
            order.append(
                SectionOrder(
                    key=previous.key,
                    heading=section.heading if section else previous.heading,
                    block_keys=keys,
                )
            )
    for section in sections.values():
        order.append(
            SectionOrder(
                key=section.key,
                heading=section.heading,
                block_keys=[b.block_key for b in section.blocks],
            )
        )
    # Match the owner's visible section ordering while keeping fully hidden sections
    # anchored before their next surviving original neighbor.
    visible_order = {s.key: i for i, s in enumerate(body.sections)}
    anchors: dict[str, int] = {}
    next_index = len(body.sections)
    for ordered_section in reversed(order):
        next_index = visible_order.get(ordered_section.key, next_index)
        anchors[ordered_section.key] = next_index
    order.sort(key=lambda s: anchors[s.key])
    updated = fit.model_copy(update={"order": order})
    result = {**provenance, "page_fit": updated.model_dump(mode="json")}
    if updated.measured_content_sha256 != render_digest(body, result):
        updated = updated.model_copy(update={"page_count": None, "measured_content_sha256": None})
        result["page_fit"] = updated.model_dump(mode="json")
    inventory(body, result)
    return result


def render_digest(
    body: documents.DocumentBody, provenance: dict[str, object], template: str | None = None
) -> str:
    return generation.revision_digest(
        body,
        template or str(provenance["template_version"]),
        {k: v for k, v in provenance.items() if k != "page_fit"},
    )


def change_block(
    body: documents.DocumentBody,
    fit: PageFit,
    operation: str,
    block_key: str,
    reason: Literal["page_target", "owner"] = "owner",
) -> tuple[documents.DocumentBody, PageFit]:
    visible = {b.block_key: b for s in body.sections for b in s.blocks}
    omissions = list(fit.omissions)
    if operation == "omit":
        if block_key not in visible:
            raise Problem(409, "block_not_visible")
        if len(visible) == 1:
            raise Problem(422, "document_requires_block")
        section = next(s for s in body.sections if any(b.block_key == block_key for b in s.blocks))
        omissions.append(
            Omission(section_key=section.key, block=visible.pop(block_key), reason=reason)
        )
    else:
        selected = next((item for item in omissions if item.block.block_key == block_key), None)
        if selected is None:
            raise Problem(409, "block_not_omitted")
        if block_key in visible:
            raise Problem(422, "page_fit_key_collision")
        visible[block_key] = selected.block
        omissions.remove(selected)
    sections = [
        documents.DocumentSection(
            key=s.key,
            heading=s.heading,
            blocks=[visible[key] for key in s.block_keys if key in visible],
        )
        for s in fit.order
        if any(key in visible for key in s.block_keys)
    ]
    changed = documents.DocumentBody(title=body.title, sections=sections)
    updated = fit.model_copy(
        update={"omissions": omissions, "page_count": None, "measured_content_sha256": None}
    )
    inventory(changed, {"page_fit": updated.model_dump(mode="json")})
    return changed, updated


class PinnedRequest(documents.StrictModel):
    revision_id: Annotated[UUID, Field(strict=False)]
    content_sha256: Digest


class FitRequest(PinnedRequest):
    page_target: PageTarget


class OmissionRequest(PinnedRequest):
    operation: Literal["omit", "restore"]
    block_key: documents.Key


def pinned_source(
    conn: Connection,
    owner: UUID,
    document_id: UUID,
    revision_id: UUID,
    digest: str,
    settings: Settings | None,
    *,
    fitting: bool = False,
) -> dict[str, object]:
    generation.lock_workspace(conn, owner)
    record = documents._document(conn, owner, document_id, settings)
    revision = cast(dict[str, object], record["current_revision"])
    if revision["id"] != str(revision_id) or revision["content_sha256"] != digest:
        raise Problem(409, "revision_mismatch")
    if record["scope"] != "tailored" or record["kind"] != "resume":
        raise Problem(422, "page_fit_requires_tailored_resume")
    provenance = cast(dict[str, object], revision["provenance_snapshot"])
    if provenance.get("schema_version") != generation.PROTOCOL:
        raise Problem(422, "page_fit_requires_supported_revision")
    generation.require_template(str(revision["template_version"]))
    generation._application(conn, owner, UUID(str(record["application_id"])), active=True)
    if fitting:
        if record["stale"]:
            raise Problem(409, "stale_document_context")
        if record["unsupported_claims"]:
            raise Problem(409, "unsupported_claims")
        generation.export_eligibility(conn, owner, revision)
    row = (
        conn.execute(
            text("SELECT * FROM bonafides.document_revisions WHERE owner_id=:owner AND id=:id"),
            {"owner": owner, "id": revision_id},
        )
        .mappings()
        .one()
    )
    return {**dict(row), "kind": record["kind"]}


def queue_fit(
    conn: Connection,
    owner: UUID,
    document: UUID,
    revision: UUID,
    digest: str,
    target: PageTarget,
    *,
    pair: list[dict[str, object]] | None = None,
    application: str | None = None,
) -> UUID:
    plan: dict[str, object] = {
        "mode": "page_fit",
        "document_id": str(document),
        "revision_id": str(revision),
        "content_sha256": digest,
        "page_target": target,
    }
    if pair is not None:
        plan["documents"] = pair
        plan["application_id"] = application
    return enqueue(conn, owner, "document_generate", plan)


def reduce_to_target(
    body: documents.DocumentBody,
    provenance: dict[str, object],
    target: PageTarget,
    measure: Callable[[documents.DocumentBody], int],
) -> tuple[documents.DocumentBody, PageFit]:
    fit = inventory(body, provenance).model_copy(update={"page_target": target})
    keys = [b.block_key for s in body.sections for b in s.blocks]

    def candidate(keep: int) -> tuple[documents.DocumentBody, PageFit]:
        retained = set(keys[:keep])
        hidden = [
            Omission(section_key=s.key, block=b, reason="page_target")
            for s in body.sections
            for b in s.blocks
            if b.block_key not in retained
        ]
        content = documents.DocumentBody(
            title=body.title,
            sections=[
                documents.DocumentSection(
                    key=s.key,
                    heading=s.heading,
                    blocks=[b for b in s.blocks if b.block_key in retained],
                )
                for s in body.sections
                if any(b.block_key in retained for b in s.blocks)
            ],
        )
        return content, fit.model_copy(update={"omissions": [*fit.omissions, *hidden]})

    count = measure(body)
    selected, selected_fit = body, fit
    if count > target and len(keys) > 1:
        # At most nine renders for 128 blocks, including the original. Never rely
        # on text length or an unmeasured estimate, even for the one-block floor.
        selected, selected_fit = candidate(1)
        count = measure(selected)
        if count <= target:
            low, high = 1, len(keys)
            while high - low > 1:
                middle = (low + high) // 2
                content, candidate_fit = candidate(middle)
                pages = measure(content)
                if pages <= target:
                    low = middle
                    selected, selected_fit, count = content, candidate_fit, pages
                else:
                    high = middle
    return selected, selected_fit.model_copy(
        update={
            "page_count": count,
            "measured_content_sha256": render_digest(selected, provenance),
        }
    )


def validate_hidden_references(conn: Connection, owner: UUID, fit: PageFit) -> None:
    for relation, identifiers in (
        (
            "claim_revisions",
            {ref for item in fit.omissions for ref in item.block.claim_revision_ids},
        ),
        (
            "evidence_revisions",
            {ref for item in fit.omissions for ref in item.block.evidence_revision_ids},
        ),
    ):
        if identifiers and conn.execute(
            text(
                f"SELECT count(*) FROM bonafides.{relation} WHERE owner_id=:owner AND id=ANY(:ids)"
            ),
            {"owner": owner, "ids": list(identifiers)},
        ).scalar_one() != len(identifiers):
            raise Problem(422, "invalid_page_fit_reference")


def check_source_pair(conn: Connection, owner: UUID, snapshot: dict[str, object]) -> None:
    for item in cast(list[dict[str, object]], snapshot.get("documents", [])):
        current = conn.execute(
            text(
                "SELECT current_revision_id FROM bonafides.document_drafts WHERE owner_id=:owner AND id=:id AND deleted_at IS NULL"
            ),
            {"owner": owner, "id": UUID(str(item["document_id"]))},
        ).scalar_one_or_none()
        if str(current) != str(item["revision_id"]):
            raise Problem(409, "revision_mismatch")


class _Interrupted(RuntimeError):
    pass


def execute_fit(db: Database, settings: Settings, task: Claim, interrupted: Event) -> None:
    document, revision = UUID(str(task.input["document_id"])), UUID(str(task.input["revision_id"]))
    digest = str(task.input["content_sha256"])
    target = FitRequest.model_validate(
        {
            "revision_id": revision,
            "content_sha256": digest,
            "page_target": task.input["page_target"],
        }
    ).page_target
    with db.transaction() as conn:
        source = pinned_source(
            conn, task.owner_id, document, revision, digest, settings, fitting=True
        )
        assert_fence(conn, task)
        check_source_pair(conn, task.owner_id, task.input)
    body = documents.parse_body(source["body"])
    provenance = cast(dict[str, object], source["provenance_snapshot"])
    frame = PrintableFrame.model_validate(provenance["frame"])
    template = str(source["template_version"])
    template_sha = str(provenance["template_sha256"])
    if template_sha != template_digest(template):
        raise Problem(409, "document_template_unavailable")
    renderer = RenderClient(settings.renderer_spool)

    def measure(content: documents.DocumentBody) -> int:
        if interrupted.is_set():
            raise _Interrupted
        pdf = renderer.render(content, template, frame, template_sha)
        try:
            count = len(PdfReader(io.BytesIO(pdf), strict=True).pages)
        except Exception as exc:
            raise RenderFailure("render_output_invalid") from exc
        if count < 1:
            raise RenderFailure("render_output_invalid")
        return count

    try:
        content, fit = reduce_to_target(body, provenance, target, measure)
    except _Interrupted:
        return
    if interrupted.is_set():
        return

    def effect(conn: Connection) -> dict[str, object]:
        current = pinned_source(
            conn, task.owner_id, document, revision, digest, settings, fitting=True
        )
        check_source_pair(conn, task.owner_id, task.input)
        current["provenance_snapshot"] = {**provenance, "page_fit": fit.model_dump(mode="json")}
        identifier = generation.append_revision(
            conn,
            task.owner_id,
            document,
            content,
            current,
            "manual",
            "Owner-requested page fitting",
            revision_id=uuid5(task.id, "page-fit-revision"),
        )
        assert_fence(conn, task)
        result: dict[str, object] = {
            "document_id": str(document),
            "revision_id": str(identifier),
            "page_target": target,
            "page_count": fit.page_count,
        }
        if "documents" in task.input:
            result["documents"] = [
                {**item, "revision_id": str(identifier)}
                if item["document_id"] == str(document)
                else item
                for item in cast(list[dict[str, object]], task.input["documents"])
            ]
            result["application_id"] = task.input["application_id"]
        return result

    with db.transaction() as conn:
        generation.lock_workspace(conn, task.owner_id)
        publish(conn, task, effect)


def install_routes(router: APIRouter, db: Database, settings: Settings) -> None:
    def mutate(
        document_id: UUID, body: FitRequest | OmissionRequest, request: Request, owner: Owner
    ) -> Response:
        fitting = isinstance(body, FitRequest)
        route = "/documents/{document_id}/fit" if fitting else "/documents/{document_id}/omissions"

        def effect(conn: Connection) -> tuple[int, dict[str, object]]:
            source = pinned_source(
                conn,
                owner.id,
                document_id,
                body.revision_id,
                body.content_sha256,
                settings,
                fitting=fitting,
            )
            if isinstance(body, FitRequest):
                workflow = queue_fit(
                    conn,
                    owner.id,
                    document_id,
                    body.revision_id,
                    body.content_sha256,
                    body.page_target,
                )
                return 202, get_workflow(conn, owner.id, workflow)
            content = documents.parse_body(source["body"])
            provenance = cast(dict[str, object], source["provenance_snapshot"])
            content, fit = change_block(
                content, inventory(content, provenance), body.operation, body.block_key
            )
            source["provenance_snapshot"] = {**provenance, "page_fit": fit.model_dump(mode="json")}
            generation.append_revision(
                conn,
                owner.id,
                document_id,
                content,
                source,
                "restore" if body.operation == "restore" else "manual",
                f"Owner {body.operation}: {body.block_key}",
            )
            return 200, documents._document(conn, owner.id, document_id, settings)

        with db.transaction() as conn:
            status, result = command(
                conn,
                owner.id,
                "POST",
                route,
                {"document_id": str(document_id)},
                body.model_dump(mode="json"),
                request.headers.get("if-match"),
                request.headers.get("idempotency-key"),
                "document",
                document_id,
                effect,
            )
        return generation._response(status, result)

    @router.post("/documents/{document_id}/fit")
    def fit(
        document_id: UUID,
        body: FitRequest,
        request: Request,
        owner: Annotated[Owner, Depends(require_owner)],
    ) -> Response:
        return mutate(document_id, body, request, owner)

    @router.post("/documents/{document_id}/omissions")
    def omissions(
        document_id: UUID,
        body: OmissionRequest,
        request: Request,
        owner: Annotated[Owner, Depends(require_owner)],
    ) -> Response:
        return mutate(document_id, body, request, owner)
