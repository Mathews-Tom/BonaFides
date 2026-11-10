# SPDX-License-Identifier: MIT OR Apache-2.0
"""Owner-triggered bounded capture; workers propose and only owner commands accept."""

from __future__ import annotations

import hashlib
from threading import Event
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response
from fastapi.responses import JSONResponse
from pydantic import Field
from sqlalchemy import Connection, text
from sqlalchemy.engine import RowMapping

from bonafides.auth import Owner, StrictModel, require_owner
from bonafides.commands import canonical_json, command
from bonafides.config import Settings
from bonafides.db import Database
from bonafides.evidence_capture_sources import (
    CaptureCandidate,
    CaptureSource,
    collect_commits,
    load_sources,
)
from bonafides.evidence_provenance import EvidenceClassification, InitialEvidenceProvenance
from bonafides.model_context import lock_evidence_write
from bonafides.problems import Problem
from bonafides.profile_entries import EvidenceCreate, write_evidence
from bonafides.workflows import Claim, assert_fence, enqueue, get_workflow, publish


class ScanRequest(StrictModel):
    pass


class CaptureApproval(EvidenceClassification):
    candidate_id: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


def _configuration(settings: Settings) -> tuple[dict[str, CaptureSource], str]:
    sources = load_sources(settings)
    digest = hashlib.sha256(
        canonical_json({key: source.identity_sha256 for key, source in sources.items()}).encode()
    ).hexdigest()
    return sources, digest


def _namespace(conn: Connection) -> str:
    return str(
        conn.execute(
            text("SELECT namespace_id FROM bonafides.installation_state WHERE singleton")
        ).scalar_one()
    )


def _current_source(
    conn: Connection, settings: Settings, pinned: dict[str, object]
) -> CaptureSource:
    sources, digest = _configuration(settings)
    source = sources.get(str(pinned.get("source_id")))
    if (
        source is None
        or not source.enabled
        or digest != pinned.get("config_sha256")
        or source.identity_sha256 != pinned.get("source_sha256")
        or source.privacy_class != pinned.get("privacy_class")
        or source.employer_owned != pinned.get("employer_owned")
        or _namespace(conn) != pinned.get("namespace_id")
    ):
        raise Problem(409, "capture_source_changed")
    return source


def _scan(conn: Connection, owner: UUID, workflow: UUID, *, locked: bool = False) -> RowMapping:
    row = (
        conn.execute(
            text(
                """SELECT * FROM bonafides.workflow_runs
        WHERE owner_id=:owner AND id=:id AND kind='integration_sync'
        AND input->>'mode'='evidence_capture'"""
                + (" FOR UPDATE" if locked else "")
            ),
            {"owner": owner, "id": workflow},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise Problem(404, "capture_scan_not_found")
    return row


def _approved(conn: Connection, owner: UUID, source: str) -> bool:
    # Immutable revisions, not the current record: edits/archive never reset acceptance.
    return bool(
        conn.execute(
            text("""SELECT EXISTS(SELECT 1 FROM bonafides.evidence_revisions
        WHERE owner_id=:owner AND origin='evidence_capture' AND payload->>'source'=:source)"""),
            {"owner": owner, "source": source},
        ).scalar_one()
    )


def execute_capture(db: Database, settings: Settings, task: Claim, interrupted: Event) -> None:
    with db.transaction() as conn:
        assert_fence(conn, task)
        source = _current_source(conn, settings, task.input)
    # No SQL transaction/owner lock is held during bounded filesystem work.
    candidates = collect_commits(source, interrupted)
    if interrupted.is_set():
        raise Problem(409, "capture_interrupted")

    def result(conn: Connection) -> dict[str, object]:
        _current_source(conn, settings, task.input)
        if interrupted.is_set():
            raise Problem(409, "capture_interrupted")
        return {
            "proposals": [candidate.model_dump(mode="json") for candidate in candidates],
            "window_limit": 20,
        }

    with db.transaction() as conn:
        publish(conn, task, result)


def build_router(db: Database, settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/v1/evidence-capture")

    @router.get("/sources")
    def sources(owner: Annotated[Owner, Depends(require_owner)]) -> dict[str, object]:
        configured, _ = _configuration(settings)
        return {"items": [source.public() for source in configured.values()]}

    @router.post("/sources/{source_id}/scan")
    def scan(
        source_id: str,
        body: ScanRequest,
        owner: Annotated[Owner, Depends(require_owner)],
        if_match: Annotated[str | None, Header()] = None,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        with db.transaction() as conn:
            profile = conn.execute(
                text("SELECT id FROM bonafides.profiles WHERE owner_id=:owner"), {"owner": owner.id}
            ).scalar_one()

            def effect(connection: Connection) -> tuple[int, dict[str, object]]:
                configured, digest = _configuration(settings)
                source = configured.get(source_id)
                if source is None:
                    raise Problem(404, "capture_source_not_found")
                if not source.enabled:
                    raise Problem(409, "capture_source_disabled")
                workflow = enqueue(
                    connection,
                    owner.id,
                    "integration_sync",
                    {
                        "mode": "evidence_capture",
                        "source_id": source.id,
                        "source_label": source.label,
                        "source_sha256": source.identity_sha256,
                        "config_sha256": digest,
                        "namespace_id": _namespace(connection),
                        "privacy_class": source.privacy_class,
                        "employer_owned": source.employer_owned,
                    },
                )
                return 202, {"workflow_id": str(workflow)}

            status, result = command(
                conn,
                owner.id,
                "POST",
                "/evidence-capture/sources/{source_id}/scan",
                {"source_id": source_id},
                body.model_dump(),
                if_match,
                idempotency_key,
                "profile",
                profile,
                effect,
            )
        return JSONResponse(result, status_code=status)

    @router.get("/scans")
    def scans(owner: Annotated[Owner, Depends(require_owner)]) -> dict[str, object]:
        with db.transaction() as conn:
            rows = conn.execute(
                text("""SELECT id,input->>'source_id' AS source_id,
                input->>'source_label' AS source_label,state,created_at FROM bonafides.workflow_runs
                WHERE owner_id=:owner AND kind='integration_sync' AND input->>'mode'='evidence_capture'
                ORDER BY created_at DESC,id DESC LIMIT 100"""),
                {"owner": owner.id},
            ).mappings()
            return {
                "items": [
                    {**dict(row), "id": str(row["id"]), "created_at": row["created_at"].isoformat()}
                    for row in rows
                ]
            }

    @router.get("/scans/{workflow_id}")
    def detail(
        workflow_id: UUID, owner: Annotated[Owner, Depends(require_owner)]
    ) -> dict[str, object]:
        with db.transaction() as conn:
            workflow = get_workflow(conn, owner.id, workflow_id)
            row = _scan(conn, owner.id, workflow_id)
            pinned = cast(dict[str, object], row["input"])
            try:
                _current_source(conn, settings, pinned)
                changed = False
            except Problem as exc:
                if exc.code != "capture_source_changed":
                    raise
                changed = True
            stored = cast(dict[str, object], row["result"] or {})
            proposals = []
            for item in cast(list[object], stored.get("proposals", [])):
                candidate = CaptureCandidate.model_validate(item)
                proposals.append(
                    {
                        **candidate.model_dump(mode="json"),
                        "origin": "evidence_capture",
                        "already_approved": _approved(conn, owner.id, candidate.payload.source),
                    }
                )
            version = conn.execute(
                text("SELECT version FROM bonafides.profiles WHERE owner_id=:owner"),
                {"owner": owner.id},
            ).scalar_one()
            return {
                "workflow": workflow,
                "proposals": proposals,
                "profile_version": version,
                "source_changed": changed,
            }

    @router.post("/scans/{workflow_id}/approve")
    def approve(
        workflow_id: UUID,
        body: CaptureApproval,
        owner: Annotated[Owner, Depends(require_owner)],
        response: Response,
        if_match: Annotated[str | None, Header()] = None,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        with db.transaction() as conn:
            lock_evidence_write(conn, owner.id)
            profile = conn.execute(
                text("SELECT id FROM bonafides.profiles WHERE owner_id=:owner"), {"owner": owner.id}
            ).scalar_one()

            def effect(connection: Connection) -> tuple[int, dict[str, object]]:
                row = _scan(connection, owner.id, workflow_id, locked=True)
                source = _current_source(
                    connection, settings, cast(dict[str, object], row["input"])
                )
                if row["state"] != "succeeded":
                    raise Problem(409, "capture_scan_not_succeeded")
                stored = cast(dict[str, object], row["result"] or {})
                candidates = [
                    CaptureCandidate.model_validate(item)
                    for item in cast(list[object], stored.get("proposals", []))
                ]
                candidate = next(
                    (item for item in candidates if item.id == body.candidate_id), None
                )
                if candidate is None:
                    raise Problem(404, "capture_candidate_not_found")
                if (
                    candidate.payload.source != f"local-git:{source.id}@{candidate.id}"
                    or candidate.provenance.privacy_class != source.privacy_class
                    or candidate.provenance.employer_owned != source.employer_owned
                    or candidate.provenance.source_type != "work_sample"
                ):
                    raise Problem(409, "capture_candidate_changed")
                if _approved(connection, owner.id, candidate.payload.source):
                    raise Problem(409, "capture_already_approved")
                evidence = EvidenceCreate(
                    payload=candidate.payload,
                    provenance=InitialEvidenceProvenance(
                        source_type="work_sample",
                        privacy_class=body.privacy_class,
                        employer_owned=body.employer_owned,
                    ),
                )
                return 200, write_evidence(
                    connection, owner.id, evidence, None, origin="evidence_capture"
                )

            status, result = command(
                conn,
                owner.id,
                "POST",
                "/evidence-capture/scans/{workflow_id}/approve",
                {"workflow_id": str(workflow_id)},
                body.model_dump(),
                if_match,
                idempotency_key,
                "profile",
                profile,
                effect,
            )
        response.status_code = status
        response.headers["ETag"] = f'"{result["version"]}"'
        return result

    return router
