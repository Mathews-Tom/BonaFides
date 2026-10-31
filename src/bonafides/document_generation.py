# SPDX-License-Identifier: MIT OR Apache-2.0
"""Production document composition, frozen contexts and immutable revision lifecycle.

Exact-copy checks intentionally reject unestablished paraphrases; they are not a
semantic entailment proof. Human review cannot override unsupported wording.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from typing import Annotated, Literal, cast
from uuid import UUID, uuid4, uuid5

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import Field, ValidationError, field_validator, model_validator
from sqlalchemy import Connection, text
from sqlalchemy.engine import RowMapping

from bonafides import document_defaults, documents, local_model, model_context, suitability
from bonafides.auth import Owner, require_owner
from bonafides.commands import append_outbox, canonical_json, command, lock_installation
from bonafides.config import Settings
from bonafides.db import Database
from bonafides.document_frame import TEMPLATE_PAPER, TEMPLATES, PrintableFrame, template_digest
from bonafides.problems import Problem
from bonafides.workflows import Claim, assert_fence, enqueue, get_workflow

PROTOCOL = 2
POLICY = "ol-documents-exact-facts-v1"
CATEGORIES = {
    "experience": "Experience",
    "education": "Education",
    "skill": "Skills",
    "project": "Projects",
    "certification": "Certifications",
    "language": "Languages",
    "other": "Profile evidence",
}
SAFE_LANGUAGE = {
    "greeting": frozenset(
        {
            "Dear Hiring team,",
            "Thank you for your consideration.",
            "Thank you for considering my application.",
            "Sincerely,",
        }
    ),
    "intent": frozenset(
        {
            "I would welcome the opportunity to discuss this role.",
            "I would welcome a discussion of the role.",
        }
    ),
}
LIMITS = {
    "automatic_check": "Exact accepted wording and explicit safe language only; non-exact paraphrases are unestablished, not automatically proved false.",
    "human_review": "Review every factual block against its sources. Owner assertions are not independently verified. Review cannot waive unsupported content.",
    "privacy": "Dedicated identity/contact data, private tracking data and the content of confidential or employer-owned evidence are excluded from inference. Professional free text may still contain personal data.",
}


def sha(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def lock_workspace(conn: Connection, owner: UUID) -> None:
    """Take the owner's write lock before workflow/domain locks, without later upgrades."""
    lock_installation(conn)
    conn.execute(
        text("SELECT owner_id FROM bonafides.owner_settings WHERE owner_id=:owner FOR UPDATE"),
        {"owner": owner},
    ).one()


def require_template(version: str) -> None:
    if version not in TEMPLATES:
        raise Problem(422, "unsupported_document_template")


def revision_digest(
    body: documents.DocumentBody, template: str, provenance: dict[str, object]
) -> str:
    if provenance.get("schema_version") != PROTOCOL:
        return documents.body_digest(body)
    return sha(
        {
            "body": body.model_dump(mode="json"),
            "template_version": template,
            "template_sha256": provenance["template_sha256"],
            "frame": provenance["frame"],
        }
    )


def _profile(conn: Connection, owner: UUID, revision: UUID) -> RowMapping:
    row = (
        conn.execute(
            text("""SELECT p.id,p.version,p.current_revision_id,r.snapshot,r.content_sha256
        FROM bonafides.profiles p JOIN bonafides.profile_revisions r ON r.owner_id=p.owner_id AND r.profile_id=p.id
        WHERE p.owner_id=:owner AND r.id=:revision FOR UPDATE OF p"""),
            {"owner": owner, "revision": revision},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise Problem(422, "invalid_profile_revision")
    if sha(row["snapshot"]) != row["content_sha256"]:
        raise Problem(409, "profile_integrity_failed")
    return row


def _fact_data(conn: Connection, owner: UUID, revision: UUID) -> list[dict[str, object]]:
    facts = documents._facts(conn, owner, revision)
    profile = conn.execute(
        text("SELECT snapshot FROM bonafides.profile_revisions WHERE owner_id=:owner AND id=:id"),
        {"owner": owner, "id": revision},
    ).scalar_one()
    selected = profile.get("claim_revision_ids", [])
    current = conn.execute(
        text(
            "SELECT r.snapshot FROM bonafides.profiles p JOIN bonafides.profile_revisions r ON r.owner_id=p.owner_id AND r.id=p.current_revision_id WHERE p.owner_id=:owner"
        ),
        {"owner": owner},
    ).scalar_one()
    if set(selected) - set(current.get("claim_revision_ids", [])):
        raise Problem(409, "profile_fact_withdrawn")
    claims: dict[str, RowMapping] = {}
    for identifier in selected:
        row = (
            conn.execute(
                text("""SELECT r.payload,c.kind FROM bonafides.claim_revisions r
            JOIN bonafides.professional_claims c ON c.owner_id=r.owner_id AND c.id=r.claim_id
            WHERE r.owner_id=:owner AND r.id=:id AND c.archived_at IS NULL AND c.current_revision_id=r.id
            FOR SHARE OF c"""),
                {"owner": owner, "id": UUID(identifier)},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise Problem(409, "profile_fact_withdrawn")
        claims[identifier] = row
    accepted: list[dict[str, object]] = []
    for fact in facts:
        if str(fact.evidence_id) not in profile.get("evidence_revision_ids", []):
            raise Problem(409, "profile_evidence_unavailable")
        found = conn.execute(
            text("""SELECT cr.id FROM bonafides.evidence_records e JOIN bonafides.evidence_revisions r
            ON r.owner_id=e.owner_id AND r.evidence_id=e.id
            JOIN bonafides.evidence_revisions cr ON cr.owner_id=e.owner_id
            AND cr.id=e.current_revision_id WHERE e.owner_id=:owner AND r.id=:id
            AND e.archived_at IS NULL AND cr.content_sha256=r.content_sha256 FOR SHARE OF e"""),
            {"owner": owner, "id": fact.evidence_id},
        ).first()
        if found is None or str(found[0]) not in current.get("evidence_revision_ids", []):
            raise Problem(409, "profile_evidence_unavailable")
        claim = claims[str(fact.claim_id)]
        payload = cast(dict[str, object], claim["payload"])
        structured = {
            key: value
            for key in ("role", "organization", "start_date", "end_date", "proficiency")
            if isinstance((value := payload.get(key)), str) and value
        }
        printable = "\n".join([fact.claim_text, *[str(value) for value in structured.values()]])
        if len(printable) > 4000:
            raise Problem(409, "document_context_too_large")
        accepted.append(
            {
                "claim_revision_id": str(fact.claim_id),
                "evidence_revision_id": str(fact.evidence_id),
                "claim_text": fact.claim_text,
                "evidence_text": fact.evidence_text,
                "source": fact.source,
                "claim_sha256": fact.claim_sha256,
                "evidence_sha256": fact.evidence_sha256,
                "status": fact.status,
                "category": claim["kind"],
                "structured_fields": structured,
                "printable_text": printable,
            }
        )
    if set(selected) - {str(item["claim_revision_id"]) for item in accepted}:
        raise Problem(409, "profile_claim_not_supported")
    if len(canonical_json(accepted).encode()) > 262144:
        raise Problem(409, "document_context_too_large")
    return accepted


def _frame(snapshot: dict[str, object], posting: RowMapping | None = None) -> dict[str, object]:
    values: dict[str, object] = {
        "display_name": snapshot.get("display_name", ""),
        "contact": snapshot.get("contact"),
    }
    if posting is not None:
        values.update(
            {
                "correspondence_date": date.today().isoformat(),
                "organization": posting["company"],
                "role": posting["title"],
                "recipient": "Hiring team",
            }
        )
    try:
        return PrintableFrame.model_validate(values).model_dump(mode="json")
    except ValidationError as exc:
        raise Problem(422, "invalid_printable_identity") from exc


def _targets(conn: Connection, owner: UUID, application: UUID | None) -> list[dict[str, object]]:
    rows = (
        conn.execute(
            text("""SELECT id,kind,version,current_revision_id,deleted_at FROM bonafides.document_drafts
        WHERE owner_id=:owner AND ((CAST(:application AS uuid) IS NULL AND scope='base') OR application_id=CAST(:application AS uuid))
        ORDER BY kind,id FOR UPDATE"""),
            {"owner": owner, "application": application},
        )
        .mappings()
        .all()
    )
    return [
        {
            "id": str(row["id"]),
            "kind": row["kind"],
            "version": row["version"],
            "revision_id": str(row["current_revision_id"]) if row["current_revision_id"] else None,
            "deleted": row["deleted_at"] is not None,
        }
        for row in rows
    ]


def _application(
    conn: Connection, owner: UUID, application: UUID, *, active: bool = False
) -> RowMapping:
    row = (
        conn.execute(
            text("""SELECT a.*,o.posting_id,o.disposition FROM bonafides.applications a
        JOIN bonafides.saved_opportunities o ON o.owner_id=a.owner_id AND o.id=a.opportunity_id
        WHERE a.owner_id=:owner AND a.id=:id FOR UPDATE OF a,o"""),
            {"owner": owner, "id": application},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise Problem(404, "application_not_found")
    if active and (row["archived_at"] is not None or row["disposition"] != "saved"):
        raise Problem(409, "application_not_active")
    return row


def route_status(
    conn: Connection, owner: UUID, settings: Settings, *, stage: str = "generate_documents"
) -> dict[str, object]:
    try:
        route = local_model.load_route(settings.local_model_route_file)
    except local_model.ModelFailure:
        return {"status": "unqualified", "reason": "model_route_invalid", "identity": None}
    if route is None:
        return {"status": "disabled", "reason": "local_model_not_configured", "identity": None}
    blocked = conn.execute(
        text(
            "SELECT 1 FROM bonafides.local_model_route_blocks WHERE owner_id=:owner AND route_sha256=:sha AND stage=:stage"
        ),
        {"owner": owner, "sha": sha(route.identity()), "stage": stage},
    ).first()
    qualified = route.qualified_for(stage) and blocked is None
    return {
        "status": "qualified" if qualified else "unqualified",
        "reason": "operator_qualified_local_route"
        if qualified
        else "model_route_failed_live_evaluation"
        if blocked
        else "model_route_not_qualified",
        "identity": route.identity(),
    }


def base_plan(conn: Connection, owner: UUID, revision: UUID, template: str) -> dict[str, object]:
    lock_workspace(conn, owner)
    require_template(template)
    profile = _profile(conn, owner, revision)
    facts = _fact_data(conn, owner, revision)
    if not facts:
        raise Problem(409, "profile_content_required")
    targets = _targets(conn, owner, None)
    if any(item["deleted"] for item in targets):
        raise Problem(409, "document_deleted")
    return {
        "mode": "deterministic_base",
        "profile_revision_id": str(revision),
        "current_profile_revision_id": str(profile["current_revision_id"]),
        "profile_version": profile["version"],
        "profile_sha256": profile["content_sha256"],
        "template_version": template,
        "template_sha256": template_digest(template),
        "facts": facts,
        "frame": _frame(profile["snapshot"]),
        "targets": targets,
        "policy_version": POLICY,
    }


def generation_plan(
    conn: Connection,
    owner: UUID,
    application: UUID,
    revision: UUID,
    template: str,
    tone: str,
    language: str,
    settings: Settings,
    *,
    active: bool = False,
) -> dict[str, object]:
    lock_workspace(conn, owner)
    require_template(template)
    if language != "en":
        raise Problem(422, "unsupported_document_language")
    if tone not in {"professional", "concise"}:
        raise Problem(422, "unsupported_document_tone")
    app = _application(conn, owner, application, active=active)
    posting = suitability._posting(conn, owner, app["posting_id"])
    current = suitability._current_requirements(conn, owner, posting["id"])
    if current is None or not current["confirmed"]:
        raise Problem(409, "requirements_unavailable")
    settings_version = int(
        conn.execute(
            text("SELECT version FROM bonafides.owner_settings WHERE owner_id=:owner FOR SHARE"),
            {"owner": owner},
        ).scalar_one()
    )
    _, requirements = suitability._reviewed_requirements(conn, owner, posting, current["id"])
    profile = _profile(conn, owner, revision)
    facts = _fact_data(conn, owner, revision)
    claims, evidence = model_context.professional_context(conn, owner, facts)
    projection = {
        "posting": {
            "title": posting["title"],
            "organization": posting["company"],
            "source_text": suitability.normalize(str(posting["description_text"])),
        },
        "requirements": [
            {
                "requirement_key": item.requirement_key,
                "text": item.text,
                "importance": item.importance,
                "span": item.span.model_dump(mode="json"),
            }
            for item in requirements
        ],
        "claims": claims,
        "evidence": evidence,
        "tone": tone,
        "language": language,
    }
    route = route_status(conn, owner, settings)
    targets = _targets(conn, owner, application)
    if any(item["deleted"] for item in targets):
        raise Problem(409, "document_deleted")
    plan: dict[str, object] = {
        "mode": "tailored_ai",
        "application_id": str(application),
        "application_version": app["version"],
        "profile_revision_id": str(revision),
        "current_profile_revision_id": str(profile["current_revision_id"]),
        "profile_version": profile["version"],
        "profile_sha256": profile["content_sha256"],
        "requirements_revision_id": str(current["id"]),
        "posting_id": str(posting["id"]),
        "posting_content_sha256": suitability._posting_digest(posting),
        "source_sha256": suitability._text_sha(
            suitability.normalize(str(posting["description_text"]))
        ),
        "posting_snapshot": {
            "posting_id": str(posting["id"]),
            "title": posting["title"],
            "company": posting["company"],
            "source_text": suitability.normalize(str(posting["description_text"])),
            "content_sha256": suitability._posting_digest(posting),
        },
        "template_version": template,
        "template_sha256": template_digest(template),
        "tone": tone,
        "language": language,
        "frame": _frame(profile["snapshot"], posting),
        "facts": facts,
        "targets": targets,
        "context": projection,
        "route_identity": route["identity"],
        "generation_route": route,
        "policy_version": POLICY,
        "privacy_policy_revision": 1,
        "settings_version": settings_version,
        "stage": "generate_documents",
        "prompt_version": local_model.PROMPT_VERSIONS["generate_documents"],
    }
    plan["context_sha256"] = sha(plan)
    return plan


def check_context(conn: Connection, task: Claim, settings: Settings) -> None:
    lock_workspace(conn, task.owner_id)
    assert_fence(conn, task)
    if task.input.get("mode") == "deterministic_base":
        plan = base_plan(
            conn,
            task.owner_id,
            UUID(str(task.input["profile_revision_id"])),
            str(task.input["template_version"]),
        )
        if plan != {key: task.input.get(key) for key in plan}:
            raise Problem(409, "stale_document_context")
        return
    plan = generation_plan(
        conn,
        task.owner_id,
        UUID(str(task.input["application_id"])),
        UUID(str(task.input["profile_revision_id"])),
        str(task.input["template_version"]),
        str(task.input["tone"]),
        str(task.input["language"]),
        settings,
        active=True,
    )
    # The correspondence date belongs to the frozen request, not a worker's next day.
    frame = cast(dict[str, object], plan["frame"])
    frame["correspondence_date"] = cast(dict[str, object], task.input["frame"])[
        "correspondence_date"
    ]
    del plan["context_sha256"]
    if sha(plan) != task.input.get("context_sha256"):
        raise Problem(409, "stale_document_context")


def issues(
    body: documents.DocumentBody, facts: list[dict[str, object]], kind: str
) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    if body.title != (
        "Cover letter" if kind == "cover_letter" else "Base résumé" if kind == "base" else "Résumé"
    ):
        found.append({"path": "/title", "code": "unestablished_heading"})
    for si, section in enumerate(body.sections):
        if section.heading not in {
            *CATEGORIES.values(),
            "Application",
            "Letter",
            "Interest",
            "Summary",
        }:
            found.append({"path": f"/sections/{si}/heading", "code": "unestablished_heading"})
        for bi, block in enumerate(section.blocks):
            path = f"/sections/{si}/blocks/{bi}"
            if any(
                (ord(char) < 32 and char not in "\n\t") or 127 <= ord(char) <= 159
                for char in block.text
            ):
                found.append({"path": path, "code": "disallowed_control"})
            if re.search(
                r"\[(?:add|insert|todo|tbd|fill|replace)\b[^\]]*\]", block.text, re.IGNORECASE
            ):
                found.append({"path": path, "code": "unresolved_omission_marker"})
            if block.claim_kind != "factual":
                if (
                    block.text not in SAFE_LANGUAGE[block.claim_kind]
                    or block.claim_revision_ids
                    or block.evidence_revision_ids
                    or kind == "base"
                ):
                    found.append({"path": path, "code": "unestablished_nonfactual_text"})
                continue
            matches = [
                fact
                for fact in facts
                if str(fact["claim_revision_id"])
                in {str(item) for item in block.claim_revision_ids}
                and str(fact["evidence_revision_id"])
                in {str(item) for item in block.evidence_revision_ids}
                and block.text in (fact["claim_text"], fact.get("printable_text"))
            ]
            if (
                len(block.claim_revision_ids) != 1
                or not matches
                or {str(fact["evidence_revision_id"]) for fact in matches}
                != {str(item) for item in block.evidence_revision_ids}
            ):
                found.append({"path": path, "code": "unestablished_professional_assertion"})
    return found


def compose_base(plan: dict[str, object]) -> documents.DocumentBody:
    facts = cast(list[dict[str, object]], plan["facts"])
    sections: dict[str, list[documents.DocumentBlock]] = {}
    seen: set[str] = set()
    for fact in facts:
        claim = str(fact["claim_revision_id"])
        if claim in seen:
            continue
        seen.add(claim)
        category = str(fact["category"])
        if category not in CATEGORIES:
            category = "other"
        sections.setdefault(category, []).append(
            documents.DocumentBlock(
                block_key=f"claim-{UUID(claim).hex}",
                text=str(fact["printable_text"]),
                claim_kind="factual",
                claim_revision_ids=[UUID(claim)],
                evidence_revision_ids=[
                    UUID(str(item["evidence_revision_id"]))
                    for item in facts
                    if item["claim_revision_id"] == claim
                ],
            )
        )
    return documents.DocumentBody(
        title="Base résumé",
        sections=[
            documents.DocumentSection(key=key, heading=CATEGORIES[key], blocks=blocks)
            for key, blocks in sections.items()
        ],
    )


class CandidateBlock(documents.DocumentBlock):
    block_key: Annotated[str, Field(pattern=r"^b-[0-9]{1,3}$")]
    text: Annotated[str, Field(min_length=1, max_length=2000)]

    @model_validator(mode="after")
    def factual_reference(self) -> CandidateBlock:
        if self.claim_kind == "factual" and not self.claim_revision_ids:
            raise ValueError("factual_reference_required")
        if len(set(self.claim_revision_ids)) != len(self.claim_revision_ids) or len(
            set(self.evidence_revision_ids)
        ) != len(self.evidence_revision_ids):
            raise ValueError("duplicate_document_reference")
        return self


class CandidateSection(documents.StrictModel):
    heading: str = Field(min_length=1, max_length=100)
    blocks: list[CandidateBlock] = Field(min_length=1, max_length=30)


class CandidateDocument(documents.StrictModel):
    kind: Literal["resume", "cover_letter"]
    sections: list[CandidateSection] = Field(min_length=1, max_length=12)


class Gap(documents.StrictModel):
    requirement_key: Annotated[str, Field(pattern=r"^req-[0-9]{1,3}$")]
    question: str = Field(min_length=1, max_length=500)


class CandidatePair(documents.StrictModel):
    schema_version: Literal["1"]
    stage: Literal["generate_documents"]
    documents: list[CandidateDocument] = Field(min_length=2, max_length=2)
    gaps: list[Gap] = Field(max_length=30)
    warnings: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(max_length=20)

    @model_validator(mode="after")
    def complete_pair(self) -> CandidatePair:
        if {item.kind for item in self.documents} != {"resume", "cover_letter"}:
            raise ValueError("incomplete_document_pair")
        keys = [
            block.block_key
            for item in self.documents
            for section in item.sections
            for block in section.blocks
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate_pair_block_key")
        return self


def validate_pair(output: dict[str, object], plan: dict[str, object]) -> CandidatePair:
    try:
        pair = CandidatePair.model_validate_json(canonical_json(output), strict=True)
        permitted_claims = {
            str(item["claim_revision_id"]) for item in cast(list[dict[str, object]], plan["facts"])
        }
        permitted_evidence = {
            str(item["evidence_revision_id"])
            for item in cast(list[dict[str, object]], plan["facts"])
        }
        requirements = {
            str(item["requirement_key"])
            for item in cast(
                list[dict[str, object]], cast(dict[str, object], plan["context"])["requirements"]
            )
        }
        for item in pair.documents:
            # Apply aggregate AST limits before either half is persisted.
            _candidate_body(item)
            for section in item.sections:
                for block in section.blocks:
                    if {str(value) for value in block.claim_revision_ids} - permitted_claims or {
                        str(value) for value in block.evidence_revision_ids
                    } - permitted_evidence:
                        raise Problem(422, "reference_outside_pinned_context")
        if any(gap.requirement_key not in requirements for gap in pair.gaps):
            raise Problem(422, "foreign_document_gap")
        if len({gap.requirement_key for gap in pair.gaps}) != len(pair.gaps):
            raise Problem(422, "duplicate_document_gap")
        return pair
    except ValidationError as exc:
        raise Problem(422, "invalid_model_output") from exc


def _candidate_body(item: CandidateDocument) -> documents.DocumentBody:
    return documents.DocumentBody(
        title="Résumé" if item.kind == "resume" else "Cover letter",
        sections=[
            documents.DocumentSection(
                key=f"section-{index}",
                heading=section.heading,
                blocks=[block for block in section.blocks],
            )
            for index, section in enumerate(item.sections)
        ],
    )


def _provenance(
    plan: dict[str, object], workflow: UUID, run: UUID | None, metadata: dict[str, object] | None
) -> dict[str, object]:
    return {
        "schema_version": PROTOCOL,
        "mode": plan["mode"],
        "workflow_id": str(workflow),
        "profile_revision_id": plan["profile_revision_id"],
        "template_version": plan["template_version"],
        "template_sha256": plan["template_sha256"],
        "profile_version_at_generation": plan["profile_version"],
        "current_profile_revision_id_at_generation": plan["current_profile_revision_id"],
        "assembly_version": "ol-profile-copy-v1" if plan["mode"] == "deterministic_base" else None,
        "settings_version": plan.get("settings_version"),
        "facts": plan["facts"],
        "frame": plan["frame"],
        "policy_version": POLICY,
        "review_limits": LIMITS,
        "safe_language_policy": {key: sorted(value) for key, value in SAFE_LANGUAGE.items()},
        "context_sha256": plan.get("context_sha256"),
        "requirements_revision_id": plan.get("requirements_revision_id"),
        "posting_content_sha256": plan.get("posting_content_sha256"),
        "model_run": {
            "id": str(run),
            "route": plan["route_identity"],
            "prompt_version": plan["prompt_version"],
            "usage": metadata,
        }
        if run
        else None,
        "tone": plan.get("tone"),
        "language": plan.get("language", "en"),
        "privacy_policy_revision": plan.get("privacy_policy_revision", 1),
    }


def append_revision(
    conn: Connection,
    owner: UUID,
    draft: UUID,
    body: documents.DocumentBody,
    source: dict[str, object],
    origin: str,
    reason: str,
    *,
    revision_id: UUID | None = None,
) -> UUID:
    identifier = revision_id or uuid4()
    provenance = cast(dict[str, object], source["provenance_snapshot"])
    template = str(source["template_version"])
    kind = "base" if source["scope"] == "base" else str(source["kind"])
    validation = (
        issues(body, cast(list[dict[str, object]], provenance["facts"]), kind)
        if provenance.get("schema_version") == PROTOCOL
        else [
            {"path": path, "code": "unestablished_professional_assertion"}
            for path in documents.unsupported_paths(
                body, documents._facts(conn, owner, UUID(str(source["profile_revision_id"])))
            )
        ]
    )
    number = conn.execute(
        text(
            "SELECT coalesce(max(revision_no),0)+1 FROM bonafides.document_revisions WHERE owner_id=:owner AND document_draft_id=:id"
        ),
        {"owner": owner, "id": draft},
    ).scalar_one()
    conn.execute(
        text("""INSERT INTO bonafides.document_revisions(owner_id,id,document_draft_id,revision_no,profile_revision_id,model_run_id,scope,body,posting_snapshot,provenance_snapshot,template_version,content_sha256,origin,reason,validation_issues)
        VALUES(:owner,:id,:draft,:number,:profile,:run,:scope,CAST(:body AS jsonb),CAST(:posting AS jsonb),CAST(:provenance AS jsonb),:template,:sha,:origin,:reason,CAST(:issues AS jsonb))"""),
        {
            "owner": owner,
            "id": identifier,
            "draft": draft,
            "number": number,
            "profile": source["profile_revision_id"],
            "run": source.get("model_run_id"),
            "scope": source["scope"],
            "body": canonical_json(body.model_dump(mode="json")),
            "posting": canonical_json(source["posting_snapshot"])
            if source.get("posting_snapshot") is not None
            else None,
            "provenance": canonical_json(provenance),
            "template": template,
            "sha": revision_digest(body, template, provenance),
            "origin": origin,
            "reason": reason,
            "issues": canonical_json(validation),
        },
    )
    facts = cast(list[dict[str, object]], provenance["facts"])
    blocked = {issue["path"] for issue in validation}
    for si, section in enumerate(body.sections):
        for bi, block in enumerate(section.blocks):
            path = f"/sections/{si}/blocks/{bi}"
            if path in blocked or block.claim_kind != "factual":
                continue
            for fact in facts:
                if (
                    UUID(str(fact["claim_revision_id"])) in block.claim_revision_ids
                    and UUID(str(fact["evidence_revision_id"])) in block.evidence_revision_ids
                ):
                    conn.execute(
                        text("""INSERT INTO bonafides.document_claim_links(owner_id,document_revision_id,claim_path,claim_revision_id,evidence_revision_id,excerpt,status)
                        VALUES(:owner,:revision,:path,:claim,:evidence,:excerpt,:status)"""),
                        {
                            "owner": owner,
                            "revision": identifier,
                            "path": path,
                            "claim": fact["claim_revision_id"],
                            "evidence": fact["evidence_revision_id"],
                            "excerpt": block.text,
                            "status": fact["status"],
                        },
                    )
    current_profile = conn.execute(
        text("SELECT current_revision_id FROM bonafides.profiles WHERE owner_id=:owner"),
        {"owner": owner},
    ).scalar_one()
    conn.execute(
        text("""UPDATE bonafides.document_drafts SET profile_revision_id=:profile,
        version=version+CASE WHEN current_revision_id IS NULL THEN 0 ELSE 1 END,current_revision_id=:revision,
        stale_at=CASE WHEN CAST(:current AS uuid)=CAST(:profile AS uuid) THEN NULL ELSE coalesce(stale_at,clock_timestamp()) END WHERE owner_id=:owner AND id=:id AND deleted_at IS NULL"""),
        {
            "revision": identifier,
            "profile": source["profile_revision_id"],
            "current": current_profile,
            "owner": owner,
            "id": draft,
        },
    )
    append_outbox(
        conn,
        owner,
        "document.revision_created",
        "document",
        draft,
        {"revision_id": str(identifier)},
        int(number),
    )
    return identifier


def publish_base(conn: Connection, task: Claim, settings: Settings) -> dict[str, object]:
    check_context(conn, task, settings)
    plan = task.input
    targets = cast(list[dict[str, object]], plan["targets"])
    draft = UUID(str(targets[0]["id"])) if targets else uuid4()
    if not targets:
        conn.execute(
            text(
                "INSERT INTO bonafides.document_drafts(owner_id,id,kind,scope,profile_revision_id) VALUES(:owner,:id,'resume','base',:profile)"
            ),
            {"owner": task.owner_id, "id": draft, "profile": plan["profile_revision_id"]},
        )
    source = {
        "kind": "resume",
        "scope": "base",
        "profile_revision_id": plan["profile_revision_id"],
        "template_version": plan["template_version"],
        "provenance_snapshot": _provenance(plan, task.id, None, None),
        "posting_snapshot": None,
    }
    revision = append_revision(
        conn,
        task.owner_id,
        draft,
        compose_base(plan),
        source,
        "deterministic",
        "Profile-only deterministic composition",
        revision_id=uuid5(task.id, "base-revision"),
    )
    return {"document_id": str(draft), "revision_id": str(revision)}


def publish_pair(
    conn: Connection,
    task: Claim,
    settings: Settings,
    pair: CandidatePair,
    run: UUID,
    metadata: dict[str, object],
) -> dict[str, object]:
    check_context(conn, task, settings)
    plan = task.input
    provenance = _provenance(plan, task.id, run, metadata)
    provenance["gaps"] = [gap.model_dump(mode="json") for gap in pair.gaps]
    provenance["warnings"] = pair.warnings
    targets = {str(item["kind"]): item for item in cast(list[dict[str, object]], plan["targets"])}
    result: list[dict[str, object]] = []
    for item in pair.documents:
        target = targets.get(item.kind)
        draft = UUID(str(target["id"])) if target else uuid4()
        if target is None:
            conn.execute(
                text(
                    "INSERT INTO bonafides.document_drafts(owner_id,id,kind,scope,profile_revision_id,application_id) VALUES(:owner,:id,:kind,'tailored',:profile,:application)"
                ),
                {
                    "owner": task.owner_id,
                    "id": draft,
                    "kind": item.kind,
                    "profile": plan["profile_revision_id"],
                    "application": plan["application_id"],
                },
            )
        source = {
            "kind": item.kind,
            "scope": "tailored",
            "profile_revision_id": plan["profile_revision_id"],
            "template_version": plan["template_version"],
            "provenance_snapshot": provenance,
            "posting_snapshot": plan["posting_snapshot"],
            "model_run_id": run,
        }
        revision = append_revision(
            conn,
            task.owner_id,
            draft,
            _candidate_body(item),
            source,
            "generated",
            "Owner-requested local-model candidate pair",
            revision_id=uuid5(task.id, item.kind),
        )
        result.append({"kind": item.kind, "document_id": str(draft), "revision_id": str(revision)})
    return {"application_id": plan["application_id"], "documents": result, "model_run_id": str(run)}


def revision_view(
    conn: Connection, owner: UUID, document: UUID, revision: UUID
) -> dict[str, object]:
    row = (
        conn.execute(
            text("""SELECT r.*,d.kind FROM bonafides.document_revisions r JOIN bonafides.document_drafts d
        ON d.owner_id=r.owner_id AND d.id=r.document_draft_id WHERE r.owner_id=:owner AND r.document_draft_id=:document AND r.id=:id AND d.deleted_at IS NULL"""),
            {"owner": owner, "document": document, "id": revision},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise Problem(404, "document_revision_not_found")
    body = documents.parse_body(row["body"])
    provenance = cast(dict[str, object], row["provenance_snapshot"])
    if revision_digest(body, row["template_version"], provenance) != row["content_sha256"]:
        raise Problem(409, "document_integrity_failed")
    validation = (
        issues(
            body,
            cast(list[dict[str, object]], provenance["facts"]),
            "base" if row["scope"] == "base" else row["kind"],
        )
        if provenance.get("schema_version") == PROTOCOL
        else [
            {"path": path, "code": "unestablished_professional_assertion"}
            for path in documents.unsupported_paths(
                body, documents._facts(conn, owner, row["profile_revision_id"])
            )
        ]
    )
    return {
        "id": str(row["id"]),
        "revision_no": row["revision_no"],
        "body": body.model_dump(mode="json"),
        "content_sha256": row["content_sha256"],
        "profile_revision_id": str(row["profile_revision_id"]),
        "template_version": row["template_version"],
        "origin": row["origin"],
        "created_at": row["created_at"].isoformat(),
        "reason": row["reason"],
        "provenance_snapshot": provenance,
        "validation_issues": validation,
    }


def export_eligibility(conn: Connection, owner: UUID, revision: dict[str, object]) -> None:
    facts = cast(
        list[dict[str, object]], cast(dict[str, object], revision["provenance_snapshot"])["facts"]
    )
    body = documents.parse_body(revision["body"])
    used = {
        (str(claim), str(evidence))
        for section in body.sections
        for block in section.blocks
        if block.claim_kind == "factual"
        for claim in block.claim_revision_ids
        for evidence in block.evidence_revision_ids
    }
    current = conn.execute(
        text(
            "SELECT r.snapshot FROM bonafides.profiles p JOIN bonafides.profile_revisions r ON r.owner_id=p.owner_id AND r.id=p.current_revision_id WHERE p.owner_id=:owner"
        ),
        {"owner": owner},
    ).scalar_one()
    for fact in facts:
        if (str(fact["claim_revision_id"]), str(fact["evidence_revision_id"])) not in used:
            continue
        if str(fact["claim_revision_id"]) not in current.get("claim_revision_ids", []) or str(
            fact["evidence_revision_id"]
        ) not in current.get("evidence_revision_ids", []):
            raise Problem(409, "document_source_withdrawn")
        row = conn.execute(
            text("""SELECT c.id FROM bonafides.claim_revisions cr JOIN bonafides.professional_claims c ON c.owner_id=cr.owner_id AND c.id=cr.claim_id
            JOIN bonafides.evidence_revisions er ON er.owner_id=c.owner_id AND er.id=:evidence
            JOIN bonafides.evidence_records e ON e.owner_id=er.owner_id AND e.id=er.evidence_id
            WHERE cr.owner_id=:owner AND cr.id=:claim AND c.archived_at IS NULL AND e.archived_at IS NULL FOR SHARE OF c,e"""),
            {
                "owner": owner,
                "claim": fact["claim_revision_id"],
                "evidence": fact["evidence_revision_id"],
            },
        ).first()
        if row is None:
            raise Problem(409, "document_source_withdrawn")


class BaseRequest(documents.StrictModel):
    profile_revision_id: Annotated[UUID, Field(strict=False)]
    template_version: str = Field(min_length=1, max_length=100)


class GenerationRequest(BaseRequest):
    requirements_revision_id: Annotated[UUID, Field(strict=False)]
    tone: Literal["professional", "concise"]
    language: Literal["en"]
    context_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class SaveRequest(documents.StrictModel):
    base_revision_id: Annotated[UUID, Field(strict=False)]
    body: documents.DocumentBody
    reason: str = Field(min_length=1, max_length=300)

    @field_validator("body", mode="before")
    @classmethod
    def strict_document_body(cls, value: object) -> documents.DocumentBody:
        return value if isinstance(value, documents.DocumentBody) else documents.parse_body(value)


class RestoreRequest(documents.StrictModel):
    revision_id: Annotated[UUID, Field(strict=False)]
    reason: str = Field(min_length=1, max_length=300)


def _response(status: int, result: dict[str, object]) -> Response:
    headers = {"Cache-Control": "private, no-store"}
    if "version" in result:
        headers["ETag"] = f'"{result["version"]}"'
    if status == 202:
        headers["Location"] = f"/api/v1/workflows/{result['id']}"
    return Response(
        canonical_json(result), status_code=status, media_type="application/json", headers=headers
    )


def install_routes(router: APIRouter, db: Database, settings: Settings) -> None:
    document_defaults.install_routes(router, db)

    @router.get("/document-templates")
    def templates(owner: Annotated[Owner, Depends(require_owner)]) -> Response:
        del owner
        return _response(
            200,
            {
                "items": [
                    {
                        "version": version,
                        "label": item[0],
                        "language": "en",
                        "paper": TEMPLATE_PAPER[version],
                    }
                    for version, item in TEMPLATES.items()
                ]
            },
        )

    @router.get("/profile/base-resume")
    def get_base(owner: Annotated[Owner, Depends(require_owner)]) -> Response:
        with db.transaction() as conn:
            identifier = conn.execute(
                text(
                    "SELECT id FROM bonafides.document_drafts WHERE owner_id=:owner AND scope='base' AND deleted_at IS NULL"
                ),
                {"owner": owner.id},
            ).scalar_one_or_none()
            if identifier is None:
                raise Problem(404, "base_resume_not_found")
            return _response(200, documents._document(conn, owner.id, identifier, settings))

    @router.post("/profile/base-resume")
    def create_base(
        body: BaseRequest, request: Request, owner: Annotated[Owner, Depends(require_owner)]
    ) -> Response:
        with db.transaction() as conn:
            profile_id = conn.execute(
                text("SELECT id FROM bonafides.profiles WHERE owner_id=:owner"), {"owner": owner.id}
            ).scalar_one()

            def effect(active: Connection) -> tuple[int, dict[str, object]]:
                plan = base_plan(active, owner.id, body.profile_revision_id, body.template_version)
                workflow = enqueue(active, owner.id, "document_generate", plan)
                return 202, get_workflow(active, owner.id, workflow)

            status, result = command(
                conn,
                owner.id,
                "POST",
                "/profile/base-resume",
                {},
                body.model_dump(mode="json"),
                request.headers.get("if-match"),
                request.headers.get("idempotency-key"),
                "profile",
                profile_id,
                effect,
            )
        return _response(status, result)

    @router.get("/applications/{application_id}/documents")
    def application_documents(
        application_id: UUID, owner: Annotated[Owner, Depends(require_owner)]
    ) -> Response:
        with db.transaction() as conn:
            lock_workspace(conn, owner.id)
            app = _application(conn, owner.id, application_id)
            current = suitability._current_requirements(conn, owner.id, app["posting_id"])
            ids = conn.execute(
                text(
                    "SELECT id FROM bonafides.document_drafts WHERE owner_id=:owner AND application_id=:id AND deleted_at IS NULL AND current_revision_id IS NOT NULL ORDER BY kind DESC"
                ),
                {"owner": owner.id, "id": application_id},
            ).scalars()
            return _response(
                200,
                {
                    "application_id": str(application_id),
                    "version": app["version"],
                    "items": [
                        documents._document(conn, owner.id, identifier, settings)
                        for identifier in ids
                    ],
                    "generation_route": route_status(conn, owner.id, settings),
                    "requirements_revision_id": str(current["id"]) if current else None,
                },
            )

    @router.get("/applications/{application_id}/generation-context")
    def preview(
        application_id: UUID,
        owner: Annotated[Owner, Depends(require_owner)],
        profile_revision_id: UUID,
        template_version: str,
        tone: Literal["professional", "concise"] = "professional",
        language: str = "en",
    ) -> Response:
        with db.transaction() as conn:
            plan = generation_plan(
                conn,
                owner.id,
                application_id,
                profile_revision_id,
                template_version,
                tone,
                language,
                settings,
            )
            keys = (
                "application_id",
                "application_version",
                "profile_revision_id",
                "profile_version",
                "requirements_revision_id",
                "template_version",
                "tone",
                "language",
                "context_sha256",
                "frame",
                "generation_route",
            )
            return _response(
                200, {**{key: plan[key] for key in keys}, "model_projection": plan["context"]}
            )

    @router.post("/applications/{application_id}/generation")
    def generate(
        application_id: UUID,
        body: GenerationRequest,
        request: Request,
        owner: Annotated[Owner, Depends(require_owner)],
    ) -> Response:
        def effect(conn: Connection) -> tuple[int, dict[str, object]]:
            plan = generation_plan(
                conn,
                owner.id,
                application_id,
                body.profile_revision_id,
                body.template_version,
                body.tone,
                body.language,
                settings,
                active=True,
            )
            if (
                plan["requirements_revision_id"] != str(body.requirements_revision_id)
                or plan["context_sha256"] != body.context_sha256
            ):
                raise Problem(409, "generation_context_changed")
            if cast(dict[str, object], plan["generation_route"])["status"] != "qualified":
                raise Problem(503, "generation_route_unavailable")
            workflow = enqueue(conn, owner.id, "document_generate", plan)
            return 202, get_workflow(conn, owner.id, workflow)

        with db.transaction() as conn:
            status, result = command(
                conn,
                owner.id,
                "POST",
                "/applications/{application_id}/generation",
                {"application_id": str(application_id)},
                body.model_dump(mode="json"),
                request.headers.get("if-match"),
                request.headers.get("idempotency-key"),
                "application",
                application_id,
                effect,
            )
        return _response(status, result)

    @router.get("/documents/{document_id}/revisions")
    def history(
        document_id: UUID,
        owner: Annotated[Owner, Depends(require_owner)],
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
        before: Annotated[int | None, Query(ge=1)] = None,
    ) -> Response:
        with db.transaction() as conn:
            documents._document(conn, owner.id, document_id, settings)
            rows = (
                conn.execute(
                    text(
                        "SELECT id,revision_no FROM bonafides.document_revisions WHERE owner_id=:owner AND document_draft_id=:id AND (CAST(:before AS bigint) IS NULL OR revision_no<:before) ORDER BY revision_no DESC LIMIT :limit"
                    ),
                    {"owner": owner.id, "id": document_id, "before": before, "limit": limit + 1},
                )
                .mappings()
                .all()
            )
            return _response(
                200,
                {
                    "items": [
                        revision_view(conn, owner.id, document_id, row["id"])
                        for row in rows[:limit]
                    ],
                    "next_cursor": rows[limit - 1]["revision_no"] if len(rows) > limit else None,
                },
            )

    @router.get("/documents/{document_id}/revisions/{revision_id}")
    def historical(
        document_id: UUID, revision_id: UUID, owner: Annotated[Owner, Depends(require_owner)]
    ) -> Response:
        with db.transaction() as conn:
            return _response(200, revision_view(conn, owner.id, document_id, revision_id))

    def mutate(
        document_id: UUID, request: Request, owner: Owner, body: SaveRequest | RestoreRequest
    ) -> Response:
        restoring = isinstance(body, RestoreRequest)

        def effect(conn: Connection) -> tuple[int, dict[str, object]]:
            record = documents._document(conn, owner.id, document_id, settings)
            current = cast(dict[str, object], record["current_revision"])
            if isinstance(body, SaveRequest) and current["id"] != str(body.base_revision_id):
                raise Problem(409, "revision_mismatch")
            selected = (
                body.revision_id if isinstance(body, RestoreRequest) else body.base_revision_id
            )
            row = (
                conn.execute(
                    text(
                        "SELECT * FROM bonafides.document_revisions WHERE owner_id=:owner AND document_draft_id=:draft AND id=:id"
                    ),
                    {"owner": owner.id, "draft": document_id, "id": selected},
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise Problem(404, "document_revision_not_found")
            revision_view(conn, owner.id, document_id, selected)
            source = dict(row)
            source["kind"] = record["kind"]
            content = (
                documents.parse_body(row["body"]) if isinstance(body, RestoreRequest) else body.body
            )
            append_revision(
                conn,
                owner.id,
                document_id,
                content,
                source,
                "restore" if restoring else "manual",
                body.reason,
            )
            return 200, documents._document(conn, owner.id, document_id, settings)

        with db.transaction() as conn:
            status, result = command(
                conn,
                owner.id,
                "POST" if restoring else "PUT",
                "/documents/{document_id}/restore" if restoring else "/documents/{document_id}",
                {"document_id": str(document_id)},
                body.model_dump(mode="json"),
                request.headers.get("if-match"),
                request.headers.get("idempotency-key"),
                "document",
                document_id,
                effect,
            )
        return _response(status, result)

    @router.put("/documents/{document_id}")
    def save(
        document_id: UUID,
        body: SaveRequest,
        request: Request,
        owner: Annotated[Owner, Depends(require_owner)],
    ) -> Response:
        return mutate(document_id, request, owner, body)

    @router.post("/documents/{document_id}/restore")
    def restore(
        document_id: UUID,
        body: RestoreRequest,
        request: Request,
        owner: Annotated[Owner, Depends(require_owner)],
    ) -> Response:
        return mutate(document_id, request, owner, body)

    from bonafides import section_regeneration

    section_regeneration.install_routes(router, db, settings)
