# SPDX-License-Identifier: MIT OR Apache-2.0
"""Exact-bound approval controls, not executors or hosted-provider adapters.

All internal operations use the caller's transaction. Domain producers alone prepare
fixed intents; OL013 intentionally has no public prepare, resume, or execute route.
An authorization receipt is not evidence that any external effect happened.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Literal, cast
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import Connection, text

from offerloom.auth import Owner, require_owner
from offerloom.commands import canonical_json, command, lock_installation
from offerloom.db import Database
from offerloom.problems import Problem
from offerloom.workflows import Claim

ActionKind = Literal["hosted_egress", "replace_workspace", "privacy_delete"]
MAX_TTL_SECONDS = 600
MAX_PROJECTION_BYTES = 262144


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ApprovalDecision(StrictModel):
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    destination_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    revision_id: Annotated[UUID, Field(strict=False)] | None
    target_version: int = Field(ge=1)
    confirmed: Literal[True]

    @field_validator("confirmed", mode="before")
    @classmethod
    def exact_confirmation(cls, value: object) -> object:
        if value is not True:
            raise ValueError("explicit confirmation required")
        return value


class HostedDestination(StrictModel):
    provider_key: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,119}$")
    model_id: str = Field(min_length=1, max_length=200, pattern=r"^[\x21-\x7e]+$")
    endpoint: str = Field(min_length=1, max_length=2048)


class HostedPayload(StrictModel):
    purpose: Literal["profile_import", "posting_extract", "matching", "document_generate"]
    projection: dict[str, object]
    max_transmissions: int = Field(ge=1, le=2)
    token_limit: int = Field(ge=1, le=9007199254740991)
    cost_limit_microunits: int = Field(ge=0, le=9007199254740991)


class WorkspaceDestination(StrictModel):
    namespace_id: str


class ReplacementPayload(StrictModel):
    preview_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    replacement_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DeletionPayload(StrictModel):
    preview_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    categories: list[
        Literal[
            "profile",
            "evidence",
            "sources",
            "postings",
            "applications",
            "documents",
            "settings",
            "all_workspace",
        ]
    ] = Field(min_length=1, max_length=8)


def _sha(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _validate_action(
    action_kind: str, destination: dict[str, object], payload: dict[str, object]
) -> None:
    try:
        if action_kind == "hosted_egress":
            target = HostedDestination.model_validate(destination)
            content = HostedPayload.model_validate(payload)
            endpoint = urlsplit(target.endpoint)
            if (
                endpoint.scheme != "https"
                or not endpoint.hostname
                or endpoint.username is not None
                or endpoint.password is not None
                or endpoint.query
                or endpoint.fragment
                or any(ord(char) <= 32 or ord(char) == 127 for char in target.endpoint)
            ):
                raise ValueError("invalid hosted destination")
            # Validate malformed ports as well, without resolving or contacting the host.
            _ = endpoint.port
            if len(canonical_json(content.projection).encode("utf-8")) > MAX_PROJECTION_BYTES:
                raise ValueError("projection too large")
        elif action_kind in {"replace_workspace", "privacy_delete"}:
            target_workspace = WorkspaceDestination.model_validate(destination)
            if str(UUID(target_workspace.namespace_id)) != target_workspace.namespace_id:
                raise ValueError("noncanonical namespace")
            if action_kind == "replace_workspace":
                ReplacementPayload.model_validate(payload)
            else:
                deletion = DeletionPayload.model_validate(payload)
                if deletion.categories != sorted(set(deletion.categories)) or (
                    "all_workspace" in deletion.categories and len(deletion.categories) != 1
                ):
                    raise ValueError("noncanonical categories")
        else:
            raise ValueError("unsupported action")
        canonical_json(destination)
        canonical_json(payload)
    except (ValidationError, ValueError) as exc:
        raise Problem(422, "invalid_approval_intent") from exc


def _context(conn: Connection, owner_id: UUID, workflow_id: UUID) -> dict[str, object]:
    """Installation -> owner settings -> workflow; identity mutations lock installation first."""
    lock_installation(conn)
    installation = (
        conn.execute(
            text(
                "SELECT namespace_id,maintenance_epoch,auth_epoch FROM offerloom.installation_state WHERE singleton"
            )
        )
        .mappings()
        .one()
    )
    settings = (
        conn.execute(
            text("SELECT * FROM offerloom.owner_settings WHERE owner_id=:owner FOR UPDATE"),
            {"owner": owner_id},
        )
        .mappings()
        .first()
    )
    if settings is None:
        raise Problem(404, "owner_not_found")
    workflow = (
        conn.execute(
            text("""SELECT w.*,clock_timestamp() AS db_now,
        EXISTS(SELECT 1 FROM offerloom.workflow_steps s WHERE s.owner_id=w.owner_id
        AND s.workflow_run_id=w.id AND s.delivery_state='unknown') AS unknown_delivery
        FROM offerloom.workflow_runs w WHERE w.owner_id=:owner AND w.id=:workflow FOR UPDATE OF w"""),
            {"owner": owner_id, "workflow": workflow_id},
        )
        .mappings()
        .first()
    )
    if workflow is None:
        raise Problem(404, "workflow_not_found")
    auth_epoch = conn.execute(
        text("SELECT auth_epoch FROM offerloom.owners WHERE id=:owner"), {"owner": owner_id}
    ).scalar_one()
    return {
        "installation": dict(installation),
        "settings": dict(settings),
        "workflow": dict(workflow),
        "auth_epoch": int(auth_epoch),
    }


def _part(context: dict[str, object], key: str) -> dict[str, object]:
    return cast(dict[str, object], context[key])


def _session(conn: Connection, owner_id: UUID, session_id: UUID) -> Mapping[str, object] | None:
    return cast(
        Mapping[str, object] | None,
        conn.execute(
            text("""SELECT s.*,s.authenticated_at>clock_timestamp()-interval '5 minutes'
        AND s.authenticated_at<=clock_timestamp() AS recent
        FROM offerloom.sessions s JOIN offerloom.owners o ON o.id=s.owner_id
        WHERE s.owner_id=:owner AND s.id=:session AND s.revoked_at IS NULL
        AND s.auth_epoch=o.auth_epoch AND s.expires_at>clock_timestamp()
        AND s.last_seen_at>clock_timestamp()-interval '12 hours'"""),
            {"owner": owner_id, "session": session_id},
        )
        .mappings()
        .first(),
    )


def _approval(conn: Connection, owner_id: UUID, workflow_id: UUID) -> dict[str, object] | None:
    row = (
        conn.execute(
            text("""SELECT * FROM offerloom.action_approvals WHERE owner_id=:owner
        AND workflow_run_id=:workflow AND intent IS NOT NULL FOR UPDATE"""),
            {"owner": owner_id, "workflow": workflow_id},
        )
        .mappings()
        .first()
    )
    return None if row is None else dict(row)


def _document(conn: Connection, owner_id: UUID, document_id: UUID) -> dict[str, object]:
    row = (
        conn.execute(
            text("""SELECT d.id,d.version,d.current_revision_id,r.content_sha256 FROM offerloom.document_drafts d
        JOIN offerloom.document_revisions r ON r.owner_id=d.owner_id AND r.id=d.current_revision_id
        WHERE d.owner_id=:owner AND d.id=:document AND d.deleted_at IS NULL"""),
            {"owner": owner_id, "document": document_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise Problem(409, "document_changed")
    return {
        "id": str(row["id"]),
        "version": int(row["version"]),
        "revision_id": str(row["current_revision_id"]),
        "content_sha256": row["content_sha256"],
    }


def _source_reason(conn: Connection, owner_id: UUID, workflow: dict[str, object]) -> str | None:
    from offerloom.workflows import source_block_reason

    return source_block_reason(conn, workflow)


def _reason(
    conn: Connection,
    owner_id: UUID,
    context: dict[str, object],
    approval: dict[str, object],
    *,
    consumed_hosted: bool = False,
) -> str:
    workflow = _part(context, "workflow")
    installation = _part(context, "installation")
    settings = _part(context, "settings")
    intent = cast(dict[str, object], approval["intent"])
    if workflow["cancel_requested_at"] is not None or workflow["state"] == "cancelled":
        return "workflow_cancelled"
    if cast(datetime, approval["expires_at"]) <= cast(datetime, workflow["db_now"]):
        return "approval_expired"
    if approval["revoked_at"] is not None:
        return "approval_revoked"
    if approval["consumed_at"] is not None and not consumed_hosted:
        return "approval_consumed"
    if workflow["state"] not in {"waiting_for_approval", "running", "pending"}:
        return "workflow_not_executable"
    if workflow["unknown_delivery"]:
        return "delivery_reconciliation_required"
    if str(installation["namespace_id"]) != intent["namespace_id"]:
        return "namespace_changed"
    if (
        installation["maintenance_epoch"] != intent["maintenance_epoch"]
        or workflow["maintenance_epoch"] != intent["maintenance_epoch"]
    ):
        return "maintenance_changed"
    if (
        installation["auth_epoch"] != intent["installation_auth_epoch"]
        or context["auth_epoch"] != intent["owner_auth_epoch"]
    ):
        return "authentication_changed"
    if _session(conn, owner_id, UUID(str(intent["session_id"]))) is None:
        return "session_revoked"
    if approval["approved_session_id"] is not None and (
        str(approval["approved_session_id"]) != intent["session_id"]
        or approval["approved_auth_epoch"] != context["auth_epoch"]
    ):
        return "authentication_changed"
    if (
        settings["version"] != intent["settings_version"]
        or _sha(settings["settings"]) != intent["settings_sha256"]
        or settings["privacy_mode"] != intent["privacy_mode"]
    ):
        return "settings_changed"
    if settings["workspace_version"] != intent["workspace_version"]:
        return "workspace_changed"
    if (
        str(workflow["owner_id"]) != intent["owner_id"]
        or str(workflow["id"]) != intent["workflow_run_id"]
        or workflow["kind"] != intent["workflow_kind"]
        or workflow["request_sha256"] != intent["request_sha256"]
        or _sha(workflow["input"]) != intent["input_sha256"]
    ):
        return "input_changed"
    for key in ("max_attempts", "token_budget", "cost_budget_microunits", "budget_currency"):
        if workflow[key] != intent[key]:
            return "budget_changed"
    for key, baseline in (
        ("attempt", "prepared_attempt"),
        ("tokens_used", "tokens_used_at_prepare"),
        ("cost_used_microunits", "cost_used_at_prepare"),
        ("model_network_attempts", "model_attempts_at_prepare"),
    ):
        if int(cast(int, workflow[key])) < int(cast(int, intent[baseline])):
            return "budget_changed"
    if (
        intent["schema_version"] != 1
        or intent["action_kind"] != approval["action_kind"]
        or approval["action_kind"] not in {"hosted_egress", "replace_workspace", "privacy_delete"}
    ):
        return "intent_changed"
    if (
        _sha(intent) != approval["payload_sha256"]
        or _sha(intent["destination"]) != approval["destination_sha256"]
    ):
        return "intent_changed"
    if intent["expires_at"] != cast(datetime, approval["expires_at"]).isoformat():
        return "intent_changed"
    document = intent["document"]
    target = cast(dict[str, object], document) if document is not None else None
    if approval["target_version"] != (
        target["version"] if target is not None else intent["workspace_version"]
    ) or (
        str(approval["document_revision_id"])
        if approval["document_revision_id"] is not None
        else None
    ) != (target["revision_id"] if target is not None else None):
        return "intent_changed"
    if document is not None:
        try:
            current = _document(conn, owner_id, UUID(str(cast(dict[str, object], document)["id"])))
        except Problem:
            return "document_changed"
        if current != document:
            return "document_changed"
    return _source_reason(conn, owner_id, workflow) or (
        "approved" if approval["approved_at"] is not None else "approval_required"
    )


def _audit(
    conn: Connection,
    owner_id: UUID,
    approval: dict[str, object],
    event: str,
    code: str | None = None,
) -> None:
    conn.execute(
        text("""INSERT INTO offerloom.approval_audit_events(owner_id,id,approval_id,workflow_run_id,event,error_code)
        VALUES(:owner,:id,:approval,:workflow,:event,:code)"""),
        {
            "owner": owner_id,
            "id": uuid4(),
            "approval": approval["id"],
            "workflow": approval["workflow_run_id"],
            "event": event,
            "code": code,
        },
    )


def _attempt(conn: Connection, owner_id: UUID, workflow_id: UUID, event: str) -> None:
    from offerloom.workflows import append_attempt_event

    append_attempt_event(conn, owner_id, workflow_id, event)


def _view(
    conn: Connection, owner_id: UUID, context: dict[str, object], approval: dict[str, object]
) -> dict[str, object]:
    reason = _reason(conn, owner_id, context, approval)
    fields = (
        "id",
        "workflow_run_id",
        "action_kind",
        "intent",
        "payload_sha256",
        "destination_sha256",
        "target_version",
        "expires_at",
        "approved_at",
        "consumed_at",
        "version",
    )
    result: dict[str, object] = {}
    for key in fields:
        value = approval[key]
        result[key] = (
            value.isoformat()
            if isinstance(value, datetime)
            else str(value)
            if isinstance(value, UUID)
            else value
        )
    revision = approval["document_revision_id"]
    result.update(
        revision_id=str(revision) if revision is not None else None,
        applicable=reason in {"approval_required", "approved"},
        reason=reason,
        capability_available=False,
    )
    return result


def get_action_approval(
    conn: Connection,
    owner_id: UUID,
    workflow_id: UUID,
    *,
    optional: bool = False,
) -> dict[str, object] | None:
    context = _context(conn, owner_id, workflow_id)
    approval = _approval(conn, owner_id, workflow_id)
    if approval is None:
        if optional:
            return None
        raise Problem(404, "approval_not_found")
    return _view(conn, owner_id, context, approval)


def prepare_action_approval(
    conn: Connection,
    owner_id: UUID,
    workflow_id: UUID,
    *,
    action_kind: ActionKind,
    destination: dict[str, object],
    payload: dict[str, object],
    session_id: UUID,
    ttl_seconds: int = MAX_TTL_SECONDS,
    document_id: UUID | None = None,
    task: Claim | None = None,
) -> dict[str, object]:
    """Called by an actual trusted domain preview producer, never by client payload routing.

    A running producer must provide its current Claim. Pending, unclaimed domain
    producers may prepare atomically with creation. Existing intents never rebind.
    """
    _validate_action(action_kind, destination, payload)
    if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= MAX_TTL_SECONDS:
        raise Problem(422, "invalid_approval_expiry")
    context = _context(conn, owner_id, workflow_id)
    installation, settings, workflow = (
        _part(context, key) for key in ("installation", "settings", "workflow")
    )
    if _approval(conn, owner_id, workflow_id) is not None:
        raise Problem(409, "approval_already_prepared")
    if _session(conn, owner_id, session_id) is None:
        raise Problem(403, "session_revoked")
    if workflow["unknown_delivery"]:
        raise Problem(409, "delivery_reconciliation_required")
    if (
        workflow["cancel_requested_at"] is not None
        or workflow["maintenance_epoch"] != installation["maintenance_epoch"]
    ):
        raise Problem(409, "workflow_not_executable")
    if workflow["state"] == "running":
        if task is None or task.owner_id != owner_id or task.id != workflow_id:
            raise Problem(409, "workflow_fence_required")
        from offerloom.workflows import assert_fence

        assert_fence(conn, task)
    elif workflow["state"] != "pending" or workflow["attempt"] != 0 or task is not None:
        raise Problem(409, "workflow_not_executable")
    if action_kind != "hosted_egress" and destination["namespace_id"] != str(
        installation["namespace_id"]
    ):
        raise Problem(409, "namespace_changed")
    if (
        action_kind == "privacy_delete"
        and workflow["kind"] != "privacy_delete"
        or action_kind == "replace_workspace"
        and workflow["kind"] not in {"portable_import", "restore_validate"}
    ):
        raise Problem(409, "approval_purpose_mismatch")
    if action_kind == "hosted_egress":
        if settings["privacy_mode"] != "hosted_opt_in":
            raise Problem(409, "hosted_opt_in_required")
        if payload["purpose"] != workflow["kind"]:
            raise Problem(409, "approval_purpose_mismatch")
        if int(cast(int, payload["token_limit"])) > int(cast(int, workflow["token_budget"])) or int(
            cast(int, payload["cost_limit_microunits"])
        ) > int(cast(int, workflow["cost_budget_microunits"])):
            raise Problem(409, "approval_budget_exceeded")
    source_reason = _source_reason(conn, owner_id, workflow)
    if source_reason:
        raise Problem(409, source_reason)
    timing = (
        conn.execute(
            text("""SELECT clock_timestamp()+make_interval(secs=>:ttl) AS expiry,
        floor(extract(epoch FROM deadline_at-clock_timestamp()))::bigint AS remaining
        FROM offerloom.workflow_runs WHERE owner_id=:owner AND id=:workflow"""),
            {"ttl": ttl_seconds, "owner": owner_id, "workflow": workflow_id},
        )
        .mappings()
        .one()
    )
    if timing["remaining"] <= 0:
        raise Problem(409, "deadline_exceeded")
    document = _document(conn, owner_id, document_id) if document_id is not None else None
    expiry = cast(datetime, timing["expiry"])
    intent = {
        "schema_version": 1,
        "action_kind": action_kind,
        "owner_id": str(owner_id),
        "workflow_run_id": str(workflow_id),
        "workflow_kind": workflow["kind"],
        "request_sha256": workflow["request_sha256"],
        "input_sha256": _sha(workflow["input"]),
        "namespace_id": str(installation["namespace_id"]),
        "maintenance_epoch": installation["maintenance_epoch"],
        "installation_auth_epoch": installation["auth_epoch"],
        "owner_auth_epoch": context["auth_epoch"],
        "session_id": str(session_id),
        "settings_version": settings["version"],
        "settings_sha256": _sha(settings["settings"]),
        "privacy_mode": settings["privacy_mode"],
        "workspace_version": settings["workspace_version"],
        "document": document,
        "destination": destination,
        "payload": payload,
        "max_attempts": workflow["max_attempts"],
        "token_budget": workflow["token_budget"],
        "cost_budget_microunits": workflow["cost_budget_microunits"],
        "budget_currency": workflow["budget_currency"],
        "remaining_execution_seconds": int(timing["remaining"]),
        "prepared_attempt": workflow["attempt"],
        "tokens_used_at_prepare": workflow["tokens_used"],
        "cost_used_at_prepare": workflow["cost_used_microunits"],
        "model_attempts_at_prepare": workflow["model_network_attempts"],
        "expires_at": expiry.isoformat(),
    }
    approval_id = uuid4()
    target_version = document["version"] if document is not None else settings["workspace_version"]
    conn.execute(
        text("""INSERT INTO offerloom.action_approvals(owner_id,id,workflow_run_id,action_kind,intent,
        payload_sha256,destination_sha256,document_revision_id,target_version,expires_at)
        VALUES(:owner,:id,:workflow,:kind,CAST(:intent AS jsonb),:payload,:destination,:revision,:target,:expiry)"""),
        {
            "owner": owner_id,
            "id": approval_id,
            "workflow": workflow_id,
            "kind": action_kind,
            "intent": canonical_json(intent),
            "payload": _sha(intent),
            "destination": _sha(destination),
            "revision": document["revision_id"] if document is not None else None,
            "target": target_version,
            "expiry": expiry,
        },
    )
    conn.execute(
        text("""UPDATE offerloom.workflow_runs SET state='waiting_for_approval',lease_holder=NULL,
        lease_expires_at=NULL,fence=fence+1,version=version+1,updated_at=clock_timestamp(),
        last_error_code='approval_required',deadline_at=:expiry
        WHERE owner_id=:owner AND id=:workflow"""),
        {"owner": owner_id, "workflow": workflow_id, "expiry": expiry},
    )
    approval = cast(dict[str, object], _approval(conn, owner_id, workflow_id))
    _audit(conn, owner_id, approval, "prepared")
    _attempt(conn, owner_id, workflow_id, "waiting_for_approval")
    result = get_action_approval(conn, owner_id, workflow_id)
    assert result is not None
    return result


def _require_recent_owner(conn: Connection, owner: Owner, approval: Mapping[str, object]) -> None:
    session = _session(conn, owner.id, owner.session_id)
    if session is None:
        raise Problem(403, "session_revoked")
    if (
        session["auth_epoch"] != owner.auth_epoch
        or session["authenticated_at"] != owner.authenticated_at
    ):
        raise Problem(403, "authentication_changed")
    if not session["recent"]:
        raise Problem(403, "reauthentication_required")
    intent = cast(dict[str, object], approval["intent"])
    if str(owner.session_id) != intent["session_id"]:
        raise Problem(409, "session_changed")


def approve_action(
    conn: Connection, owner: Owner, workflow_id: UUID, body: ApprovalDecision
) -> dict[str, object]:
    """Review only; caller's command supplies workflow version and idempotency guard."""
    context = _context(conn, owner.id, workflow_id)
    approval = _approval(conn, owner.id, workflow_id)
    if approval is None:
        raise Problem(404, "approval_not_found")
    _require_recent_owner(conn, owner, approval)
    reason = _reason(conn, owner.id, context, approval)
    if reason != "approval_required":
        raise Problem(409, "approval_already_reviewed" if reason == "approved" else reason)
    if (
        body.payload_sha256 != approval["payload_sha256"]
        or body.destination_sha256 != approval["destination_sha256"]
        or body.revision_id != approval["document_revision_id"]
        or body.target_version != approval["target_version"]
        or body.confirmed is not True
    ):
        raise Problem(409, "approval_comparison_mismatch")
    conn.execute(
        text("""UPDATE offerloom.action_approvals SET approved_at=clock_timestamp(),
        approved_session_id=:session,approved_auth_epoch=:epoch,version=version+1
        WHERE owner_id=:owner AND id=:approval"""),
        {
            "owner": owner.id,
            "approval": approval["id"],
            "session": owner.session_id,
            "epoch": owner.auth_epoch,
        },
    )
    # No real executor exists for these actions in OL013. Approval never queues unsupported work.
    conn.execute(
        text("""UPDATE offerloom.workflow_runs SET version=version+1,updated_at=clock_timestamp(),
        last_error_code='execution_unavailable' WHERE owner_id=:owner AND id=:workflow"""),
        {"owner": owner.id, "workflow": workflow_id},
    )
    _audit(conn, owner.id, approval, "approved")
    _attempt(conn, owner.id, workflow_id, "approved")
    from offerloom.workflows import get_workflow

    return get_workflow(conn, owner.id, workflow_id)


def consume_action_approval(
    conn: Connection,
    task: Claim,
    *,
    action_kind: ActionKind,
    destination: dict[str, object],
    payload: dict[str, object],
    target_version: int,
    revision_id: UUID | None = None,
) -> dict[str, object]:
    """Consume once before the actual domain operation, in its fenced transaction.

    A maintenance producer consumes before changing the maintenance epoch, and
    retains this exact receipt in its own operation-specific coordinator record.
    This does not dispatch, execute, or enable an otherwise unavailable capability.
    """
    _validate_action(action_kind, destination, payload)
    context = _context(conn, task.owner_id, task.id)
    from offerloom.workflows import assert_fence

    assert_fence(conn, task)
    approval = _approval(conn, task.owner_id, task.id)
    if approval is None:
        raise Problem(404, "approval_not_found")
    reason = _reason(conn, task.owner_id, context, approval)
    if reason != "approved":
        raise Problem(409, reason)
    intent = cast(dict[str, object], approval["intent"])
    if (
        action_kind != approval["action_kind"]
        or destination != intent["destination"]
        or _sha(destination) != approval["destination_sha256"]
        or payload != intent["payload"]
        or revision_id != approval["document_revision_id"]
        or target_version != approval["target_version"]
    ):
        raise Problem(409, "approval_comparison_mismatch")
    conn.execute(
        text("""UPDATE offerloom.action_approvals SET consumed_at=clock_timestamp(),version=version+1
        WHERE owner_id=:owner AND id=:approval"""),
        {"owner": task.owner_id, "approval": approval["id"]},
    )
    consent_id: UUID | None = None
    if action_kind == "hosted_egress":
        consent_id = uuid4()
        conn.execute(
            text("""INSERT INTO offerloom.hosted_egress_consents(owner_id,id,workflow_run_id,provider_key,
            model_id,purpose,input_scope_sha256,settings_version,expires_at,approval_id)
            VALUES(:owner,:id,:workflow,:provider,:model,:purpose,:projection,:settings,:expiry,:approval)"""),
            {
                "owner": task.owner_id,
                "id": consent_id,
                "workflow": task.id,
                "provider": destination["provider_key"],
                "model": destination["model_id"],
                "purpose": payload["purpose"],
                "projection": _sha(payload["projection"]),
                "settings": intent["settings_version"],
                "expiry": approval["expires_at"],
                "approval": approval["id"],
            },
        )
    _audit(conn, task.owner_id, approval, "consumed")
    return {
        "approval_id": str(approval["id"]),
        "workflow_run_id": str(task.id),
        "action_kind": action_kind,
        "payload_sha256": approval["payload_sha256"],
        "destination_sha256": approval["destination_sha256"],
        "fence": task.fence,
        "consent_id": str(consent_id) if consent_id is not None else None,
        "capability_available": False,
        "execution_status": "not_executed",
    }


def verify_hosted_consent(
    conn: Connection,
    task: Claim,
    consent_id: UUID,
    *,
    provider_key: str,
    model_id: str,
    purpose: str,
    projection: dict[str, object],
    endpoint: str,
    reserved_tokens: int,
    reserved_cost_microunits: int,
) -> dict[str, object]:
    """Recheck and durably reserve EACH transmission/retry, without performing I/O.

    The caller must commit this reservation, recheck cancellation at its transport
    boundary, and record actual/unknown usage. Reservation is never known-zero cost
    or successful delivery. There is intentionally no hosted transport in OL013.
    """
    if (
        type(reserved_tokens) is not int
        or reserved_tokens <= 0
        or type(reserved_cost_microunits) is not int
        or reserved_cost_microunits < 0
    ):
        raise Problem(422, "invalid_hosted_reservation")
    context = _context(conn, task.owner_id, task.id)
    from offerloom.workflows import assert_fence

    assert_fence(conn, task)
    approval = _approval(conn, task.owner_id, task.id)
    if (
        approval is None
        or approval["action_kind"] != "hosted_egress"
        or approval["consumed_at"] is None
    ):
        raise Problem(409, "hosted_consent_required")
    reason = _reason(conn, task.owner_id, context, approval, consumed_hosted=True)
    if reason != "approved":
        raise Problem(409, reason)
    intent = cast(dict[str, object], approval["intent"])
    payload = cast(dict[str, object], intent["payload"])
    destination = {"provider_key": provider_key, "model_id": model_id, "endpoint": endpoint}
    if (
        destination != intent["destination"]
        or purpose != payload["purpose"]
        or _sha(projection) != _sha(payload["projection"])
    ):
        raise Problem(409, "hosted_consent_mismatch")
    consent = (
        conn.execute(
            text("""SELECT *,expires_at>clock_timestamp() AS unexpired FROM offerloom.hosted_egress_consents
        WHERE owner_id=:owner AND id=:id AND workflow_run_id=:workflow FOR UPDATE"""),
            {"owner": task.owner_id, "id": consent_id, "workflow": task.id},
        )
        .mappings()
        .first()
    )
    if (
        consent is None
        or consent["approval_id"] != approval["id"]
        or consent["revoked_at"] is not None
        or not consent["unexpired"]
    ):
        raise Problem(409, "hosted_consent_revoked_or_expired")
    if (
        consent["provider_key"] != provider_key
        or consent["model_id"] != model_id
        or consent["purpose"] != purpose
        or consent["input_scope_sha256"] != _sha(projection)
        or consent["settings_version"] != intent["settings_version"]
        or consent["expires_at"] != approval["expires_at"]
    ):
        raise Problem(409, "hosted_consent_mismatch")
    usage = (
        conn.execute(
            text("""SELECT COALESCE(sum(reserved_tokens),0) AS tokens,
        COALESCE(sum(reserved_cost_microunits),0) AS cost
        FROM offerloom.model_runs WHERE owner_id=:owner AND workflow_run_id=:workflow"""),
            {"owner": task.owner_id, "workflow": task.id},
        )
        .mappings()
        .one()
    )
    workflow = _part(context, "workflow")
    token_total = (
        max(int(usage["tokens"]), int(cast(int, workflow["tokens_used"]))) + reserved_tokens
    )
    cost_total = (
        max(int(usage["cost"]), int(cast(int, workflow["cost_used_microunits"])))
        + reserved_cost_microunits
    )
    if (
        int(consent["transmission_count"]) >= int(cast(int, payload["max_transmissions"]))
        or int(cast(int, workflow["model_network_attempts"]))
        >= int(cast(int, payload["max_transmissions"]))
        or token_total
        > min(int(cast(int, payload["token_limit"])), int(cast(int, workflow["token_budget"])))
        or cost_total
        > min(
            int(cast(int, payload["cost_limit_microunits"])),
            int(cast(int, workflow["cost_budget_microunits"])),
        )
    ):
        raise Problem(409, "hosted_consent_budget_exhausted")
    run_id = uuid4()
    conn.execute(
        text("""INSERT INTO offerloom.model_runs(owner_id,id,workflow_run_id,provider_key,model_id,task_kind,
        prompt_version,input_sha256,parameters,egress_class,reserved_tokens,reserved_cost_microunits,
        status,actual_cost_microunits,hosted_consent_id,metadata)
        VALUES(:owner,:id,:workflow,:provider,:model,:purpose,'exact-hosted-consent-v1',:sha,'{}',
        'hosted_opt_in',:tokens,:cost,'running',NULL,:consent,'{"usage_status":"unknown","execution_status":"not_executed"}')"""),
        {
            "owner": task.owner_id,
            "id": run_id,
            "workflow": task.id,
            "provider": provider_key,
            "model": model_id,
            "purpose": purpose,
            "sha": _sha(projection),
            "tokens": reserved_tokens,
            "cost": reserved_cost_microunits,
            "consent": consent_id,
        },
    )
    conn.execute(
        text(
            "UPDATE offerloom.hosted_egress_consents SET transmission_count=transmission_count+1 WHERE owner_id=:owner AND id=:id"
        ),
        {"owner": task.owner_id, "id": consent_id},
    )
    conn.execute(
        text(
            "UPDATE offerloom.workflow_runs SET model_network_attempts=model_network_attempts+1 WHERE owner_id=:owner AND id=:workflow"
        ),
        {"owner": task.owner_id, "workflow": task.id},
    )
    return {
        "model_run_id": str(run_id),
        "consent_id": str(consent_id),
        "usage_status": "unknown",
        "actual_cost_microunits": None,
        "execution_status": "not_executed",
        "capability_available": False,
    }


def expire_action_approvals(conn: Connection, owner_id: UUID, workflow_id: UUID) -> bool:
    context = _context(conn, owner_id, workflow_id)
    if _part(context, "workflow")["unknown_delivery"]:
        return False
    approval = _approval(conn, owner_id, workflow_id)
    if (
        approval is None
        or approval["consumed_at"] is not None
        or approval["revoked_at"] is not None
    ):
        return False
    if cast(datetime, approval["expires_at"]) > cast(
        datetime, _part(context, "workflow")["db_now"]
    ):
        return False
    conn.execute(
        text("""UPDATE offerloom.action_approvals SET revoked_at=clock_timestamp(),version=version+1
        WHERE owner_id=:owner AND id=:id"""),
        {"owner": owner_id, "id": approval["id"]},
    )
    _audit(conn, owner_id, approval, "expired", "approval_expired")
    _attempt(conn, owner_id, workflow_id, "approval_expired")
    return True


def build_router(db: Database) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.get("/workflows/{workflow_id}/approval")
    def inspect(
        workflow_id: UUID, owner: Annotated[Owner, Depends(require_owner)]
    ) -> dict[str, object]:
        with db.transaction() as conn:
            result = get_action_approval(conn, owner.id, workflow_id)
            assert result is not None
            return result

    @router.post("/workflows/{workflow_id}/approval/approve")
    def approve(
        workflow_id: UUID,
        body: ApprovalDecision,
        owner: Annotated[Owner, Depends(require_owner)],
        if_match: Annotated[str | None, Header()] = None,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        failure: Problem | None = None
        status: int = 200
        result: dict[str, object] = {}
        with db.transaction() as conn:
            # Locks precede both session checking and command's receipt replay path.
            _context(conn, owner.id, workflow_id)
            try:
                with conn.begin_nested():
                    approval = _approval(conn, owner.id, workflow_id)
                    if approval is None:
                        raise Problem(404, "approval_not_found")
                    _require_recent_owner(conn, owner, approval)
                    status, result = command(
                        conn,
                        owner.id,
                        "POST",
                        "/workflows/{workflow_id}/approval/approve",
                        {"workflow_id": str(workflow_id)},
                        body.model_dump(mode="json"),
                        if_match,
                        idempotency_key,
                        "workflow",
                        workflow_id,
                        lambda active: (200, approve_action(active, owner, workflow_id, body)),
                        workspace_change=False,
                    )
            except Problem as exc:
                # The savepoint rolled back any writes before this refusal. Commit
                # only this safe denial, then propagate the original exception.
                approval = _approval(conn, owner.id, workflow_id)
                if approval is not None:
                    _audit(conn, owner.id, approval, "denied", exc.code)
                failure = exc
        if failure is not None:
            raise failure
        return JSONResponse(result, status_code=status, headers={"ETag": f'"{result["version"]}"'})

    return router
