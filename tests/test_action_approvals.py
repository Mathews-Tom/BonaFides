# SPDX-License-Identifier: MIT OR Apache-2.0
"""Real control-plane conformance; trusted SQL fixtures are NOT domain executors.

No fixture sends provider traffic or deletes retained workspace data. Actual future
producers must supply their own preview and supported dispatcher before execution.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Barrier
from typing import cast
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError
from test_database import PGContext
from test_database import pg_context as pg_context
from test_identity import (
    NEW_PASSWORD,
    PASSWORD,
    IdentityContext,
    _bootstrap,
    _headers,
    _login,
)
from test_identity import identity as identity

from offerloom.approvals import (
    ActionKind,
    ApprovalDecision,
    approve_action,
    consume_action_approval,
    expire_action_approvals,
    get_action_approval,
    prepare_action_approval,
    verify_hosted_consent,
)
from offerloom.auth import Owner, recover_password
from offerloom.commands import canonical_json, command
from offerloom.problems import Problem
from offerloom.workflows import Claim, cancel_workflow, claim, claim_control, get_workflow


@dataclass(frozen=True)
class ApprovalContext:
    pg: PGContext
    owner: Owner


@dataclass(frozen=True)
class Prepared:
    workflow_id: UUID
    approval: dict[str, object]
    destination: dict[str, object]
    payload: dict[str, object]
    kind: ActionKind


def _owner(conn: Connection, owner_id: UUID, session_id: UUID | None = None) -> Owner:
    row = (
        conn.execute(
            text("""SELECT o.login_name,s.* FROM offerloom.sessions s JOIN offerloom.owners o ON o.id=s.owner_id
        WHERE s.owner_id=:owner AND (CAST(:session AS uuid) IS NULL OR s.id=CAST(:session AS uuid))
        ORDER BY s.created_at DESC LIMIT 1"""),
            {"owner": owner_id, "session": session_id},
        )
        .mappings()
        .one()
    )
    return Owner(
        owner_id,
        row["login_name"],
        row["id"],
        row["csrf_token"],
        row["expires_at"],
        row["authenticated_at"],
        row["auth_epoch"],
    )


@pytest.fixture
def approvals(pg_context: PGContext) -> Iterator[ApprovalContext]:
    session_id = uuid4()
    with pg_context.migrator.begin() as conn:
        conn.execute(
            text("""INSERT INTO offerloom.sessions(owner_id,id,token_sha256,csrf_sha256,csrf_token,
            auth_epoch,expires_at) SELECT id,:session,:token,:csrf,'synthetic-csrf',auth_epoch,
            clock_timestamp()+interval '1 hour' FROM offerloom.owners WHERE id=:owner"""),
            {
                "owner": pg_context.owner_id,
                "session": session_id,
                "token": hashlib.sha256(uuid4().bytes).hexdigest(),
                "csrf": "1" * 64,
            },
        )
        owner = _owner(conn, pg_context.owner_id, session_id)
    try:
        yield ApprovalContext(pg_context, owner)
    finally:
        # Defer only the new approval edge; old evidence FK definitions stay intact.
        with pg_context.migrator.begin() as conn:
            conn.execute(text("SET CONSTRAINTS offerloom.hosted_consent_approval_binding DEFERRED"))
            conn.execute(
                text("DELETE FROM offerloom.action_approvals WHERE owner_id=:owner"),
                {"owner": owner.id},
            )
            conn.execute(
                text("DELETE FROM offerloom.document_drafts WHERE owner_id=:owner"),
                {"owner": owner.id},
            )
            conn.execute(
                text("DELETE FROM offerloom.model_runs WHERE owner_id=:owner"), {"owner": owner.id}
            )
            conn.execute(
                text("DELETE FROM offerloom.hosted_egress_consents WHERE owner_id=:owner"),
                {"owner": owner.id},
            )


def _prepare(
    engine: Engine,
    owner: Owner,
    *,
    kind: ActionKind = "privacy_delete",
    ttl: int = 600,
    document_id: UUID | None = None,
) -> Prepared:
    """Seed the trusted producer input, then exercise the REAL production prepare interface."""
    workflow_id = uuid4()
    snapshot = {"schema_version": 1, "control_conformance": "trusted-domain-preview"}
    with engine.begin() as conn:
        if kind == "hosted_egress":
            conn.execute(
                text(
                    "UPDATE offerloom.owner_settings SET privacy_mode='hosted_opt_in' WHERE owner_id=:owner"
                ),
                {"owner": owner.id},
            )
        namespace = conn.execute(
            text("SELECT namespace_id FROM offerloom.installation_state WHERE singleton")
        ).scalar_one()
        destination: dict[str, object] = {"namespace_id": str(namespace)}
        payload: dict[str, object] = {"preview_sha256": "2" * 64, "categories": ["documents"]}
        workflow_kind = "privacy_delete"
        if kind == "replace_workspace":
            workflow_kind = "portable_import"
            payload = {"preview_sha256": "2" * 64, "replacement_sha256": "3" * 64}
        elif kind == "hosted_egress":
            workflow_kind = "document_generate"
            destination = {
                "provider_key": "synthetic_provider",
                "model_id": "bounded-model",
                "endpoint": "https://provider.invalid/v1/inference",
            }
            payload = {
                "purpose": "document_generate",
                "projection": {"approved_text": "synthetic only"},
                "max_transmissions": 2,
                "token_limit": 100,
                "cost_limit_microunits": 100,
            }
        conn.execute(
            text("""INSERT INTO offerloom.workflow_runs(owner_id,id,kind,input,dedupe_key,request_sha256,
            deadline_at,maintenance_epoch,token_budget,cost_budget_microunits)
            SELECT :owner,:id,:kind,CAST(:input AS jsonb),:dedupe,:sha,clock_timestamp()+interval '120 seconds',
            maintenance_epoch,100,100 FROM offerloom.installation_state WHERE singleton"""),
            {
                "owner": owner.id,
                "id": workflow_id,
                "kind": workflow_kind,
                "input": canonical_json(snapshot),
                "dedupe": str(workflow_id),
                "sha": hashlib.sha256(canonical_json(snapshot).encode()).hexdigest(),
            },
        )
        approval = prepare_action_approval(
            conn,
            owner.id,
            workflow_id,
            action_kind=kind,
            destination=destination,
            payload=payload,
            session_id=owner.session_id,
            ttl_seconds=ttl,
            document_id=document_id,
        )
    return Prepared(workflow_id, approval, destination, payload, kind)


def _decision(prepared: Prepared, **changes: object) -> ApprovalDecision:
    data = {
        key: prepared.approval[key]
        for key in (
            "payload_sha256",
            "destination_sha256",
            "revision_id",
            "target_version",
        )
    }
    data.update(confirmed=True, **changes)
    return ApprovalDecision.model_validate(data)


def _approve(
    engine: Engine, owner: Owner, prepared: Prepared, key: str | None = None
) -> dict[str, object]:
    with engine.begin() as conn:
        workflow = get_workflow(conn, owner.id, prepared.workflow_id)
        body = _decision(prepared)
        _, result = command(
            conn,
            owner.id,
            "POST",
            "/workflows/{workflow_id}/approval/approve",
            {"workflow_id": str(prepared.workflow_id)},
            body.model_dump(mode="json"),
            f'"{workflow["version"]}"',
            key or uuid4().hex,
            "workflow",
            prepared.workflow_id,
            lambda active: (200, approve_action(active, owner, prepared.workflow_id, body)),
            workspace_change=False,
        )
        return result


def _trusted_dispatch(context: ApprovalContext, prepared: Prepared) -> Claim:
    """Model OL014's future supported dispatcher, NOT a production executor/handler.

    The actual queue issues a fresh fence; no domain effects are simulated or
    reported successful. Remaining execution allowance and all budgets survive.
    """
    with context.pg.migrator.begin() as conn:
        changed = conn.execute(
            text("""UPDATE offerloom.workflow_runs w SET state='pending',version=w.version+1,
            deadline_at=LEAST(a.expires_at,clock_timestamp()+make_interval(secs=>
            (a.intent->>'remaining_execution_seconds')::integer))
            FROM offerloom.action_approvals a WHERE w.owner_id=:owner AND w.id=:id
            AND a.owner_id=w.owner_id AND a.workflow_run_id=w.id AND a.approved_at IS NOT NULL
            AND a.revoked_at IS NULL AND w.state='waiting_for_approval' RETURNING w.id"""),
            {"owner": context.owner.id, "id": prepared.workflow_id},
        ).first()
        assert changed is not None
    with context.pg.runtime.begin() as conn:
        task = (claim if prepared.kind == "hosted_egress" else claim_control)(conn, uuid4())
    assert task is not None and task.id == prepared.workflow_id
    return task


def _consume(
    conn: Connection, task: Claim, prepared: Prepared, **changes: object
) -> dict[str, object]:
    arguments: dict[str, object] = {
        "action_kind": prepared.kind,
        "destination": prepared.destination,
        "payload": prepared.payload,
        "target_version": prepared.approval["target_version"],
        "revision_id": UUID(str(prepared.approval["revision_id"]))
        if prepared.approval["revision_id"]
        else None,
    }
    arguments.update(changes)
    return consume_action_approval(
        conn,
        task,
        action_kind=cast(ActionKind, arguments["action_kind"]),
        destination=cast(dict[str, object], arguments["destination"]),
        payload=cast(dict[str, object], arguments["payload"]),
        target_version=cast(int, arguments["target_version"]),
        revision_id=cast(UUID | None, arguments["revision_id"]),
    )


def _hosted(
    conn: Connection, task: Claim, consent_id: UUID, prepared: Prepared, **changes: object
) -> dict[str, object]:
    arguments = {
        **prepared.destination,
        "purpose": prepared.payload["purpose"],
        "projection": prepared.payload["projection"],
        "reserved_tokens": 25,
        "reserved_cost_microunits": 20,
    }
    arguments.update(changes)
    return verify_hosted_consent(
        conn,
        task,
        consent_id,
        provider_key=str(arguments["provider_key"]),
        model_id=str(arguments["model_id"]),
        purpose=str(arguments["purpose"]),
        projection=cast(dict[str, object], arguments["projection"]),
        endpoint=str(arguments["endpoint"]),
        reserved_tokens=cast(int, arguments["reserved_tokens"]),
        reserved_cost_microunits=cast(int, arguments["reserved_cost_microunits"]),
    )


@pytest.mark.postgres
@pytest.mark.parametrize("kind", ["privacy_delete", "replace_workspace", "hosted_egress"])
def test_exact_positive_review_consume_is_single_use_without_execution(
    approvals: ApprovalContext, kind: ActionKind
) -> None:
    prepared = _prepare(approvals.pg.runtime, approvals.owner, kind=kind)
    assert prepared.approval["applicable"] is True
    assert prepared.approval["capability_available"] is False
    assert prepared.approval["reason"] == "approval_required"
    intent = cast(dict[str, object], prepared.approval["intent"])
    assert (
        hashlib.sha256(canonical_json(intent).encode()).hexdigest()
        == prepared.approval["payload_sha256"]
    )
    result = _approve(approvals.pg.runtime, approvals.owner, prepared)
    assert result["state"] == "waiting_for_approval"
    assert result["last_error_code"] == "execution_unavailable"
    with approvals.pg.runtime.begin() as conn:
        assert claim(conn, uuid4()) is None  # Approval cannot dispatch unsupported capability.
        assert (
            conn.execute(
                text(
                    "SELECT workspace_version FROM offerloom.owner_settings WHERE owner_id=:owner"
                ),
                {"owner": approvals.owner.id},
            ).scalar_one()
            == intent["workspace_version"]
        )
    task = _trusted_dispatch(approvals, prepared)
    with approvals.pg.runtime.begin() as conn:
        receipt = _consume(conn, task, prepared)
        assert receipt["execution_status"] == "not_executed"
        assert receipt["capability_available"] is False
        assert (receipt["consent_id"] is not None) == (kind == "hosted_egress")
        assert (
            conn.execute(
                text("SELECT count(*) FROM offerloom.profiles WHERE owner_id=:owner"),
                {"owner": task.owner_id},
            ).scalar_one()
            == 1
        )
    with pytest.raises(Problem, match="approval_consumed"), approvals.pg.runtime.begin() as conn:
        _consume(conn, task, prepared)
    with approvals.pg.runtime.begin() as conn:
        events = (
            conn.execute(
                text(
                    "SELECT event FROM offerloom.approval_audit_events WHERE owner_id=:owner AND approval_id=:id ORDER BY created_at,id"
                ),
                {"owner": task.owner_id, "id": UUID(str(prepared.approval["id"]))},
            )
            .scalars()
            .all()
        )
        assert events == ["prepared", "approved", "consumed"]


@pytest.mark.postgres
@pytest.mark.parametrize(
    "field,value",
    [
        ("payload_sha256", "f" * 64),
        ("destination_sha256", "e" * 64),
        ("revision_id", uuid4()),
        ("target_version", 999),
    ],
)
def test_exact_review_rejects_changed_comparison(
    approvals: ApprovalContext, field: str, value: object
) -> None:
    prepared = _prepare(approvals.pg.runtime, approvals.owner)
    with (
        pytest.raises(Problem, match="approval_comparison_mismatch"),
        approvals.pg.runtime.begin() as conn,
    ):
        approve_action(
            conn, approvals.owner, prepared.workflow_id, _decision(prepared, **{field: value})
        )
    with approvals.pg.runtime.begin() as conn:
        approval = get_action_approval(conn, approvals.owner.id, prepared.workflow_id)
        assert approval is not None and approval["approved_at"] is None


@pytest.mark.parametrize("confirmation", [False, 1, "true", None])
def test_review_requires_literal_boolean_confirmation(confirmation: object) -> None:
    with pytest.raises(ValidationError):
        ApprovalDecision.model_validate(
            {
                "payload_sha256": "1" * 64,
                "destination_sha256": "2" * 64,
                "revision_id": None,
                "target_version": 1,
                "confirmed": confirmation,
            }
        )


@pytest.mark.postgres
@pytest.mark.parametrize(
    "drift,reason",
    [
        ("input", "input_changed"),
        ("request", "input_changed"),
        ("workspace", "workspace_changed"),
        ("settings", "settings_changed"),
        ("settings_bytes", "settings_changed"),
        ("auth", "authentication_changed"),
        ("session", "session_revoked"),
        ("budget", "budget_changed"),
    ],
)
def test_review_rechecks_current_binding_pins(
    approvals: ApprovalContext, drift: str, reason: str
) -> None:
    prepared = _prepare(approvals.pg.runtime, approvals.owner)
    updates = {
        "input": "UPDATE offerloom.workflow_runs SET input='{}' WHERE owner_id=:owner AND id=:id",
        "request": "UPDATE offerloom.workflow_runs SET request_sha256=repeat('f',64) WHERE owner_id=:owner AND id=:id",
        "workspace": "UPDATE offerloom.owner_settings SET workspace_version=workspace_version+1 WHERE owner_id=:owner",
        "settings": "UPDATE offerloom.owner_settings SET version=version+1 WHERE owner_id=:owner",
        "settings_bytes": 'UPDATE offerloom.owner_settings SET settings=\'{"changed":"yes"}\' WHERE owner_id=:owner',
        "auth": "UPDATE offerloom.owners SET auth_epoch=auth_epoch+1 WHERE id=:owner",
        "session": "UPDATE offerloom.sessions SET revoked_at=clock_timestamp() WHERE owner_id=:owner",
        "budget": "UPDATE offerloom.workflow_runs SET token_budget=token_budget+1 WHERE owner_id=:owner AND id=:id",
    }
    with approvals.pg.migrator.begin() as conn:
        conn.execute(
            text(updates[drift]), {"owner": approvals.owner.id, "id": prepared.workflow_id}
        )
    with approvals.pg.runtime.begin() as conn:
        approval = get_action_approval(conn, approvals.owner.id, prepared.workflow_id)
        assert (
            approval is not None
            and approval["applicable"] is False
            and approval["reason"] == reason
        )
    with pytest.raises(Problem), approvals.pg.runtime.begin() as conn:
        approve_action(conn, approvals.owner, prepared.workflow_id, _decision(prepared))


@pytest.mark.postgres
@pytest.mark.parametrize(
    "field,reason",
    [
        ("namespace_id", "namespace_changed"),
        ("maintenance_epoch", "maintenance_changed"),
        ("auth_epoch", "authentication_changed"),
    ],
)
def test_installation_identity_drift_refuses_without_rebinding(
    approvals: ApprovalContext, field: str, reason: str
) -> None:
    prepared = _prepare(approvals.pg.runtime, approvals.owner)
    with approvals.pg.migrator.begin() as conn:
        original = conn.execute(
            text(f"SELECT {field} FROM offerloom.installation_state WHERE singleton")
        ).scalar_one()
        changed = uuid4() if field == "namespace_id" else int(original) + 1
        conn.execute(
            text(f"UPDATE offerloom.installation_state SET {field}=:value WHERE singleton"),
            {"value": changed},
        )
    try:
        with approvals.pg.runtime.begin() as conn:
            result = get_action_approval(conn, approvals.owner.id, prepared.workflow_id)
            assert result is not None and result["reason"] == reason
            assert result["payload_sha256"] == prepared.approval["payload_sha256"]
        with pytest.raises(Problem, match=reason), approvals.pg.runtime.begin() as conn:
            approve_action(conn, approvals.owner, prepared.workflow_id, _decision(prepared))
    finally:
        with approvals.pg.migrator.begin() as conn:
            conn.execute(
                text(f"UPDATE offerloom.installation_state SET {field}=:value WHERE singleton"),
                {"value": original},
            )


@pytest.mark.postgres
@pytest.mark.parametrize(
    "change",
    [
        {"payload": {"preview_sha256": "9" * 64, "categories": ["documents"]}},
        {"destination": {"namespace_id": str(uuid4())}},
        {"revision_id": uuid4()},
        {"target_version": 999},
        {"action_kind": "replace_workspace"},
    ],
)
def test_consumption_rejects_changed_execution_parameters(
    approvals: ApprovalContext, change: dict[str, object]
) -> None:
    prepared = _prepare(approvals.pg.runtime, approvals.owner)
    _approve(approvals.pg.runtime, approvals.owner, prepared)
    task = _trusted_dispatch(approvals, prepared)
    with pytest.raises(Problem), approvals.pg.runtime.begin() as conn:
        _consume(conn, task, prepared, **change)
    with approvals.pg.runtime.begin() as conn:
        result = get_action_approval(conn, approvals.owner.id, prepared.workflow_id)
        assert result is not None and result["consumed_at"] is None


@pytest.mark.postgres
def test_expiry_is_db_clock_bounded_immutable_and_durably_revoked(
    approvals: ApprovalContext,
) -> None:
    prepared = _prepare(approvals.pg.runtime, approvals.owner, ttl=1)
    with pytest.raises(DBAPIError), approvals.pg.runtime.begin() as conn:
        conn.execute(
            text(
                "UPDATE offerloom.action_approvals SET expires_at=expires_at+interval '1 hour' WHERE owner_id=:owner"
            ),
            {"owner": approvals.owner.id},
        )
    with approvals.pg.runtime.begin() as conn:
        conn.execute(text("SELECT pg_sleep(1.05)"))
    with pytest.raises(Problem, match="approval_expired"), approvals.pg.runtime.begin() as conn:
        approve_action(conn, approvals.owner, prepared.workflow_id, _decision(prepared))
    with approvals.pg.runtime.begin() as conn:
        assert expire_action_approvals(conn, approvals.owner.id, prepared.workflow_id)
    with approvals.pg.runtime.begin() as conn:
        assert not expire_action_approvals(conn, approvals.owner.id, prepared.workflow_id)
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM offerloom.approval_audit_events WHERE owner_id=:owner AND event='expired'"
                ),
                {"owner": approvals.owner.id},
            ).scalar_one()
            == 1
        )
        assert (
            conn.execute(
                text("SELECT expires_at FROM offerloom.action_approvals WHERE owner_id=:owner"),
                {"owner": approvals.owner.id},
            )
            .scalar_one()
            .isoformat()
            == prepared.approval["expires_at"]
        )


@pytest.mark.postgres
@pytest.mark.parametrize("ttl", [0, 601, True])
def test_expiry_never_renews_or_exceeds_ten_minutes(approvals: ApprovalContext, ttl: int) -> None:
    with pytest.raises(Problem, match="invalid_approval_expiry"):
        _prepare(approvals.pg.runtime, approvals.owner, ttl=ttl)


@pytest.mark.postgres
def test_recent_reauthentication_is_rechecked_from_live_session(approvals: ApprovalContext) -> None:
    prepared = _prepare(approvals.pg.runtime, approvals.owner)
    with approvals.pg.migrator.begin() as conn:
        conn.execute(
            text(
                "UPDATE offerloom.sessions SET authenticated_at=clock_timestamp()-interval '5 minutes' WHERE owner_id=:owner"
            ),
            {"owner": approvals.owner.id},
        )
        old = _owner(conn, approvals.owner.id)
    with (
        pytest.raises(Problem, match="reauthentication_required"),
        approvals.pg.runtime.begin() as conn,
    ):
        approve_action(conn, old, prepared.workflow_id, _decision(prepared))
    with (
        pytest.raises(Problem, match="authentication_changed"),
        approvals.pg.runtime.begin() as conn,
    ):
        approve_action(conn, approvals.owner, prepared.workflow_id, _decision(prepared))


@pytest.mark.postgres
def test_legacy_unbound_approval_and_cross_owner_never_authorize(
    approvals: ApprovalContext,
) -> None:
    prepared = _prepare(approvals.pg.runtime, approvals.owner)
    with approvals.pg.runtime.begin() as conn:
        with pytest.raises(Problem):
            get_action_approval(conn, uuid4(), prepared.workflow_id)
        workflow_id = uuid4()
        conn.execute(
            text("""INSERT INTO offerloom.workflow_runs(owner_id,id,kind,dedupe_key,request_sha256,deadline_at)
        VALUES(:owner,:id,'privacy_delete',:key,repeat('0',64),clock_timestamp()+interval '1 minute')"""),
            {"owner": approvals.owner.id, "id": workflow_id, "key": str(workflow_id)},
        )
        conn.execute(
            text("""INSERT INTO offerloom.action_approvals(owner_id,workflow_run_id,action_kind,payload_sha256,expires_at)
        VALUES(:owner,:id,'privacy_delete',repeat('0',64),clock_timestamp()+interval '1 minute')"""),
            {"owner": approvals.owner.id, "id": workflow_id},
        )
        assert get_action_approval(conn, approvals.owner.id, workflow_id, optional=True) is None
        with pytest.raises(Problem, match="approval_not_found"):
            approve_action(conn, approvals.owner, workflow_id, _decision(prepared))


@pytest.mark.postgres
@pytest.mark.parametrize(
    "column,expression",
    [
        ("intent", "'{}'::jsonb"),
        ("payload_sha256", "repeat('f',64)"),
        ("destination_sha256", "repeat('f',64)"),
        ("target_version", "999"),
    ],
)
def test_runtime_cannot_rebind_prepared_intent(
    approvals: ApprovalContext, column: str, expression: str
) -> None:
    _prepare(approvals.pg.runtime, approvals.owner)
    with pytest.raises(DBAPIError), approvals.pg.runtime.begin() as conn:
        conn.execute(
            text(
                f"UPDATE offerloom.action_approvals SET {column}={expression} WHERE owner_id=:owner"
            ),
            {"owner": approvals.owner.id},
        )


@pytest.mark.postgres
def test_concurrent_approval_and_cancel_serialize_without_execution_authority(
    approvals: ApprovalContext,
) -> None:
    prepared = _prepare(approvals.pg.runtime, approvals.owner)
    barrier = Barrier(2)

    def review() -> str:
        barrier.wait()
        try:
            _approve(approvals.pg.runtime, approvals.owner, prepared)
            return "approved"
        except Problem:
            return "refused"

    def cancel() -> str:
        barrier.wait()
        with approvals.pg.runtime.begin() as conn:
            result = cancel_workflow(conn, approvals.owner.id, prepared.workflow_id)
            return str(result["state"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        reviewed, cancelled = pool.submit(review), pool.submit(cancel)
        assert reviewed.result(timeout=10) in {"approved", "refused"}
        assert cancelled.result(timeout=10) == "cancelled"
    with approvals.pg.runtime.begin() as conn:
        result = get_action_approval(conn, approvals.owner.id, prepared.workflow_id)
        assert result is not None and result["applicable"] is False
        assert result["reason"] == "workflow_cancelled" and result["consumed_at"] is None
        assert claim(conn, uuid4()) is None


@pytest.mark.postgres
@pytest.mark.parametrize(
    "change",
    [
        {"provider_key": "different"},
        {"model_id": "different"},
        {"purpose": "matching"},
        {"endpoint": "https://other.invalid/v1/inference"},
        {"projection": {"unapproved": "secret"}},
        {"reserved_tokens": 101},
        {"reserved_cost_microunits": 101},
    ],
)
def test_hosted_checks_exact_projection_destination_purpose_and_caps_before_reservation(
    approvals: ApprovalContext, change: dict[str, object]
) -> None:
    prepared = _prepare(approvals.pg.runtime, approvals.owner, kind="hosted_egress")
    _approve(approvals.pg.runtime, approvals.owner, prepared)
    task = _trusted_dispatch(approvals, prepared)
    with approvals.pg.runtime.begin() as conn:
        receipt = _consume(conn, task, prepared)
    with pytest.raises(Problem), approvals.pg.runtime.begin() as conn:
        _hosted(conn, task, UUID(str(receipt["consent_id"])), prepared, **change)
    with approvals.pg.runtime.begin() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM offerloom.model_runs WHERE owner_id=:owner"),
                {"owner": approvals.owner.id},
            ).scalar_one()
            == 0
        )
        assert (
            conn.execute(
                text(
                    "SELECT transmission_count FROM offerloom.hosted_egress_consents WHERE owner_id=:owner"
                ),
                {"owner": approvals.owner.id},
            ).scalar_one()
            == 0
        )


@pytest.mark.postgres
def test_each_hosted_transmission_reserves_once_and_rechecks_revocation(
    approvals: ApprovalContext,
) -> None:
    prepared = _prepare(approvals.pg.runtime, approvals.owner, kind="hosted_egress")
    _approve(approvals.pg.runtime, approvals.owner, prepared)
    task = _trusted_dispatch(approvals, prepared)
    with approvals.pg.runtime.begin() as conn:
        consent_id = UUID(str(_consume(conn, task, prepared)["consent_id"]))
        first = _hosted(conn, task, consent_id, prepared)
        assert first["usage_status"] == "unknown" and first["actual_cost_microunits"] is None
        assert first["execution_status"] == "not_executed"
    with approvals.pg.runtime.begin() as conn:
        row = (
            conn.execute(
                text(
                    "SELECT input_tokens,output_tokens,actual_cost_microunits,usage_status FROM offerloom.model_runs WHERE owner_id=:owner"
                ),
                {"owner": approvals.owner.id},
            )
            .mappings()
            .one()
        )
        assert row["input_tokens"] is None and row["output_tokens"] is None
        assert row["actual_cost_microunits"] is None and row["usage_status"] == "unknown"
        conn.execute(
            text(
                "UPDATE offerloom.hosted_egress_consents SET revoked_at=clock_timestamp() WHERE owner_id=:owner AND id=:id"
            ),
            {"owner": approvals.owner.id, "id": consent_id},
        )
    with (
        pytest.raises(Problem, match="hosted_consent_revoked_or_expired"),
        approvals.pg.runtime.begin() as conn,
    ):
        _hosted(conn, task, consent_id, prepared)
    with approvals.pg.runtime.begin() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM offerloom.model_runs WHERE owner_id=:owner"),
                {"owner": approvals.owner.id},
            ).scalar_one()
            == 1
        )
        assert (
            conn.execute(
                text(
                    "SELECT model_network_attempts FROM offerloom.workflow_runs WHERE owner_id=:owner AND id=:id"
                ),
                {"owner": approvals.owner.id, "id": task.id},
            ).scalar_one()
            == 1
        )


@pytest.mark.postgres
def test_hosted_transmission_budget_is_cumulative_not_reset_by_retries(
    approvals: ApprovalContext,
) -> None:
    prepared = _prepare(approvals.pg.runtime, approvals.owner, kind="hosted_egress")
    _approve(approvals.pg.runtime, approvals.owner, prepared)
    task = _trusted_dispatch(approvals, prepared)
    with approvals.pg.runtime.begin() as conn:
        consent_id = UUID(str(_consume(conn, task, prepared)["consent_id"]))
        _hosted(conn, task, consent_id, prepared)
        _hosted(conn, task, consent_id, prepared)
    with (
        pytest.raises(Problem, match="hosted_consent_budget_exhausted"),
        approvals.pg.runtime.begin() as conn,
    ):
        _hosted(conn, task, consent_id, prepared)
    with approvals.pg.runtime.begin() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM offerloom.model_runs WHERE owner_id=:owner"),
                {"owner": approvals.owner.id},
            ).scalar_one()
            == 2
        )


@pytest.mark.postgres
def test_http_denial_is_durable_and_same_key_success_is_review_only(
    identity: IdentityContext,
) -> None:
    with identity.client() as client:
        bootstrap = _bootstrap(identity, client)
        assert bootstrap.status_code == 201
        with identity.admin.transaction() as conn:
            owner_id = conn.execute(text("SELECT id FROM offerloom.owners")).scalar_one()
            owner = _owner(conn, owner_id)
        prepared = _prepare(identity.admin.engine, owner)
        route = f"/api/v1/workflows/{prepared.workflow_id}/approval"
        inspected = client.get(route)
        assert inspected.status_code == 200 and inspected.json()["capability_available"] is False
        workflow = client.get(f"/api/v1/workflows/{prepared.workflow_id}").json()
        headers = {
            **_headers(identity, owner.csrf_token),
            "If-Match": f'"{workflow["version"]}"',
            "Idempotency-Key": uuid4().hex,
        }
        body = _decision(prepared).model_dump(mode="json")
        denied = client.post(
            route + "/approve", headers=headers, json={**body, "destination_sha256": "f" * 64}
        )
        assert denied.status_code == 409 and denied.json()["code"] == "approval_comparison_mismatch"
        with identity.admin.transaction() as conn:
            assert (
                conn.execute(
                    text(
                        "SELECT error_code FROM offerloom.approval_audit_events WHERE owner_id=:owner AND event='denied'"
                    ),
                    {"owner": owner.id},
                ).scalar_one()
                == "approval_comparison_mismatch"
            )
        headers["Idempotency-Key"] = uuid4().hex
        accepted = client.post(route + "/approve", headers=headers, json=body)
        assert accepted.status_code == 200 and accepted.json()["state"] == "waiting_for_approval"
        assert accepted.json()["last_error_code"] == "execution_unavailable"
        replayed = client.post(route + "/approve", headers=headers, json=body)
        assert replayed.status_code == 200 and replayed.json() == accepted.json()
        conflict = client.post(
            route + "/approve", headers=headers, json={**body, "target_version": 999}
        )
        assert (
            conflict.status_code == 409
            and conflict.json()["code"] == "idempotency_payload_mismatch"
        )
        headers.update({"If-Match": accepted.headers["etag"], "Idempotency-Key": uuid4().hex})
        repeated = client.post(route + "/approve", headers=headers, json=body)
        assert (
            repeated.status_code == 409 and repeated.json()["code"] == "approval_already_reviewed"
        )
        with identity.admin.transaction() as conn:
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM offerloom.approval_audit_events WHERE owner_id=:owner AND event='approved'"
                    ),
                    {"owner": owner.id},
                ).scalar_one()
                == 1
            )
            assert (
                conn.execute(
                    text("SELECT count(*) FROM offerloom.model_runs WHERE owner_id=:owner"),
                    {"owner": owner.id},
                ).scalar_one()
                == 0
            )
            assert (
                conn.execute(
                    text(
                        "SELECT consumed_at FROM offerloom.action_approvals WHERE owner_id=:owner"
                    ),
                    {"owner": owner.id},
                ).scalar_one()
                is None
            )


@pytest.mark.postgres
@pytest.mark.parametrize("recovery", [False, True])
def test_password_rotation_and_recovery_revoke_bound_approval_and_hosted_consent(
    identity: IdentityContext, recovery: bool
) -> None:
    with identity.client() as client:
        bootstrap = _bootstrap(identity, client)
        assert bootstrap.status_code == 201
        with identity.admin.transaction() as conn:
            owner_id = conn.execute(text("SELECT id FROM offerloom.owners")).scalar_one()
            owner = _owner(conn, owner_id)
        prepared = _prepare(identity.admin.engine, owner, kind="hosted_egress")
        _approve(identity.admin.engine, owner, prepared)
        # A real issued fence models future supported dispatch; no transport is invoked.
        with identity.admin.transaction() as conn:
            conn.execute(
                text(
                    "UPDATE offerloom.workflow_runs SET state='pending' WHERE owner_id=:owner AND id=:id"
                ),
                {"owner": owner.id, "id": prepared.workflow_id},
            )
            task = claim(conn, uuid4())
            assert task is not None
            receipt = _consume(conn, task, prepared)
            consent_id = UUID(str(receipt["consent_id"]))
        unused = _prepare(identity.admin.engine, owner)
        _approve(identity.admin.engine, owner, unused)
        unreviewed = _prepare(identity.admin.engine, owner, kind="replace_workspace")
        if recovery:
            recover_password(identity.admin, NEW_PASSWORD)
        else:
            changed = client.post(
                "/api/v1/session/password",
                headers=_headers(identity, owner.csrf_token),
                json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
            )
            assert changed.status_code == 201
        with identity.admin.transaction() as conn:
            consent = (
                conn.execute(
                    text(
                        "SELECT revoked_at,transmission_count FROM offerloom.hosted_egress_consents WHERE owner_id=:owner AND id=:id"
                    ),
                    {"owner": owner.id, "id": consent_id},
                )
                .mappings()
                .one()
            )
            assert consent["revoked_at"] is not None and consent["transmission_count"] == 0
        with pytest.raises(Problem), identity.admin.transaction() as conn:
            _hosted(conn, task, consent_id, prepared)
        with identity.admin.transaction() as conn:
            for other in (unused, unreviewed):
                row = (
                    conn.execute(
                        text(
                            "SELECT revoked_at,consumed_at FROM offerloom.action_approvals WHERE owner_id=:owner AND workflow_run_id=:id"
                        ),
                        {"owner": owner.id, "id": other.workflow_id},
                    )
                    .mappings()
                    .one()
                )
                assert row["revoked_at"] is not None and row["consumed_at"] is None
                audit = conn.execute(
                    text(
                        "SELECT error_code FROM offerloom.approval_audit_events WHERE owner_id=:owner AND workflow_run_id=:id AND event='revoked'"
                    ),
                    {"owner": owner.id, "id": other.workflow_id},
                ).scalar_one()
                assert audit == "authentication_revoked"
        with pytest.raises(Problem), identity.admin.transaction() as conn:
            approve_action(conn, owner, unreviewed.workflow_id, _decision(unreviewed))
        with identity.admin.transaction() as conn:
            assert (
                conn.execute(
                    text("SELECT count(*) FROM offerloom.model_runs WHERE owner_id=:owner"),
                    {"owner": owner.id},
                ).scalar_one()
                == 0
            )
            conn.execute(
                text("DELETE FROM offerloom.hosted_egress_consents WHERE owner_id=:owner"),
                {"owner": owner.id},
            )


def _seed_document(context: ApprovalContext) -> UUID:
    document_id, revision_id = uuid4(), uuid4()
    with context.pg.runtime.begin() as conn:
        conn.execute(
            text("""INSERT INTO offerloom.document_drafts(owner_id,id,kind,scope,profile_revision_id)
            VALUES(:owner,:id,'resume','base',:profile)"""),
            {"owner": context.owner.id, "id": document_id, "profile": context.pg.revision_id},
        )
        conn.execute(
            text("""INSERT INTO offerloom.document_revisions(owner_id,id,document_draft_id,revision_no,
            profile_revision_id,scope,body,posting_snapshot,provenance_snapshot,template_version,
            content_sha256,origin,reason)
            VALUES(:owner,:id,:document,1,:profile,'base','{}',NULL,'{}','synthetic',repeat('a',64),
            'manual','trusted control-plane fixture')"""),
            {
                "owner": context.owner.id,
                "id": revision_id,
                "document": document_id,
                "profile": context.pg.revision_id,
            },
        )
        conn.execute(
            text(
                "UPDATE offerloom.document_drafts SET current_revision_id=:revision WHERE owner_id=:owner AND id=:id"
            ),
            {"owner": context.owner.id, "id": document_id, "revision": revision_id},
        )
    return document_id


@pytest.mark.postgres
def test_actual_document_revision_and_version_remain_exact_at_consume(
    approvals: ApprovalContext,
) -> None:
    document_id = _seed_document(approvals)
    prepared = _prepare(approvals.pg.runtime, approvals.owner, document_id=document_id)
    assert prepared.approval["revision_id"] is not None
    assert prepared.approval["target_version"] == 1
    _approve(approvals.pg.runtime, approvals.owner, prepared)
    task = _trusted_dispatch(approvals, prepared)
    with approvals.pg.runtime.begin() as conn:
        conn.execute(
            text("""INSERT INTO offerloom.document_revisions(owner_id,id,document_draft_id,revision_no,
            profile_revision_id,scope,body,posting_snapshot,provenance_snapshot,template_version,
            content_sha256,origin,reason)
            VALUES(:owner,:revision,:document,2,:profile,'base','{"changed":"yes"}',NULL,'{}','synthetic',
            repeat('b',64),'manual','new exact revision')"""),
            {
                "owner": approvals.owner.id,
                "revision": (revision_id := uuid4()),
                "document": document_id,
                "profile": approvals.pg.revision_id,
            },
        )
        conn.execute(
            text(
                "UPDATE offerloom.document_drafts SET current_revision_id=:revision,version=version+1 WHERE owner_id=:owner AND id=:id"
            ),
            {"owner": approvals.owner.id, "revision": revision_id, "id": document_id},
        )
    with pytest.raises(Problem, match="document_changed"), approvals.pg.runtime.begin() as conn:
        _consume(conn, task, prepared)
    with approvals.pg.runtime.begin() as conn:
        approval = get_action_approval(conn, approvals.owner.id, task.id)
        assert approval is not None and approval["reason"] == "document_changed"
        assert approval["revision_id"] == prepared.approval["revision_id"]
        assert approval["consumed_at"] is None


@pytest.mark.postgres
@pytest.mark.parametrize(
    "drift,reason",
    [
        ("workspace", "workspace_changed"),
        ("settings", "settings_changed"),
        ("input", "input_changed"),
        ("session", "session_revoked"),
    ],
)
def test_consumption_rechecks_live_pins_after_successful_review(
    approvals: ApprovalContext, drift: str, reason: str
) -> None:
    prepared = _prepare(approvals.pg.runtime, approvals.owner)
    _approve(approvals.pg.runtime, approvals.owner, prepared)
    task = _trusted_dispatch(approvals, prepared)
    updates = {
        "workspace": "UPDATE offerloom.owner_settings SET workspace_version=workspace_version+1 WHERE owner_id=:owner",
        "settings": "UPDATE offerloom.owner_settings SET version=version+1 WHERE owner_id=:owner",
        "input": "UPDATE offerloom.workflow_runs SET input='{}' WHERE owner_id=:owner AND id=:id",
        "session": "UPDATE offerloom.sessions SET revoked_at=clock_timestamp() WHERE owner_id=:owner",
    }
    with approvals.pg.migrator.begin() as conn:
        conn.execute(text(updates[drift]), {"owner": approvals.owner.id, "id": task.id})
    with pytest.raises(Problem, match=reason), approvals.pg.runtime.begin() as conn:
        _consume(conn, task, prepared)


@pytest.mark.postgres
def test_wrong_owner_or_stale_workflow_fence_cannot_consume(approvals: ApprovalContext) -> None:
    prepared = _prepare(approvals.pg.runtime, approvals.owner)
    _approve(approvals.pg.runtime, approvals.owner, prepared)
    task = _trusted_dispatch(approvals, prepared)
    stale = Claim(
        task.owner_id, task.id, task.holder, task.fence - 1, task.attempt, task.kind, task.input
    )
    with pytest.raises(DBAPIError), approvals.pg.runtime.begin() as conn:
        _consume(conn, stale, prepared)
    foreign = Claim(uuid4(), task.id, task.holder, task.fence, task.attempt, task.kind, task.input)
    with pytest.raises(Problem), approvals.pg.runtime.begin() as conn:
        _consume(conn, foreign, prepared)
    with approvals.pg.runtime.begin() as conn:
        assert _consume(conn, task, prepared)["execution_status"] == "not_executed"


@pytest.mark.postgres
def test_approved_expiry_cannot_be_consumed_even_with_a_live_worker(
    approvals: ApprovalContext,
) -> None:
    prepared = _prepare(approvals.pg.runtime, approvals.owner, ttl=1)
    _approve(approvals.pg.runtime, approvals.owner, prepared)
    task = _trusted_dispatch(approvals, prepared)
    with approvals.pg.migrator.begin() as conn:
        # Keep the real workflow lease/deadline valid while approval itself expires.
        conn.execute(
            text(
                "UPDATE offerloom.workflow_runs SET deadline_at=clock_timestamp()+interval '1 minute' WHERE owner_id=:owner AND id=:id"
            ),
            {"owner": approvals.owner.id, "id": task.id},
        )
    with approvals.pg.runtime.begin() as conn:
        conn.execute(text("SELECT pg_sleep(1.05)"))
    with pytest.raises(Problem, match="approval_expired"), approvals.pg.runtime.begin() as conn:
        _consume(conn, task, prepared)


@pytest.mark.postgres
def test_unknown_delivery_never_expires_into_automatic_execution(
    approvals: ApprovalContext,
) -> None:
    prepared = _prepare(approvals.pg.runtime, approvals.owner, ttl=1)
    with approvals.pg.migrator.begin() as conn:
        conn.execute(
            text("""INSERT INTO offerloom.workflow_steps(owner_id,workflow_run_id,step_key,attempt,fence,
            state,delivery_state) VALUES(:owner,:id,'unknown-external-boundary',1,1,'waiting_for_approval','unknown')"""),
            {"owner": approvals.owner.id, "id": prepared.workflow_id},
        )
    with approvals.pg.runtime.begin() as conn:
        conn.execute(text("SELECT pg_sleep(1.05)"))
        assert not expire_action_approvals(conn, approvals.owner.id, prepared.workflow_id)
        assert claim(conn, uuid4()) is None
        approval = get_action_approval(conn, approvals.owner.id, prepared.workflow_id)
        assert approval is not None and approval["applicable"] is False
    with approvals.pg.runtime.begin() as conn:
        row = conn.execute(
            text("SELECT revoked_at FROM offerloom.action_approvals WHERE owner_id=:owner"),
            {"owner": approvals.owner.id},
        ).one()
        assert row.revoked_at is None


@pytest.mark.postgres
def test_another_live_recent_session_cannot_rebind_prepared_session(
    approvals: ApprovalContext,
) -> None:
    prepared = _prepare(approvals.pg.runtime, approvals.owner)
    new_id = uuid4()
    with approvals.pg.migrator.begin() as conn:
        conn.execute(
            text("""INSERT INTO offerloom.sessions(owner_id,id,token_sha256,csrf_sha256,csrf_token,
            auth_epoch,expires_at) SELECT id,:session,:token,repeat('2',64),'another-csrf',auth_epoch,
            clock_timestamp()+interval '1 hour' FROM offerloom.owners WHERE id=:owner"""),
            {
                "owner": approvals.owner.id,
                "session": new_id,
                "token": hashlib.sha256(uuid4().bytes).hexdigest(),
            },
        )
        other = _owner(conn, approvals.owner.id, new_id)
    with pytest.raises(Problem, match="session_changed"), approvals.pg.runtime.begin() as conn:
        approve_action(conn, other, prepared.workflow_id, _decision(prepared))
    with approvals.pg.runtime.begin() as conn:
        result = get_action_approval(conn, approvals.owner.id, prepared.workflow_id)
        assert (
            result is not None and result["payload_sha256"] == prepared.approval["payload_sha256"]
        )
        assert result["approved_at"] is None


@pytest.mark.postgres
def test_existing_prepared_intent_cannot_be_reprepared_or_renewed(
    approvals: ApprovalContext,
) -> None:
    prepared = _prepare(approvals.pg.runtime, approvals.owner)
    with (
        pytest.raises(Problem, match="approval_already_prepared"),
        approvals.pg.runtime.begin() as conn,
    ):
        prepare_action_approval(
            conn,
            approvals.owner.id,
            prepared.workflow_id,
            action_kind=prepared.kind,
            destination=prepared.destination,
            payload=prepared.payload,
            session_id=approvals.owner.session_id,
        )
    with approvals.pg.runtime.begin() as conn:
        current = get_action_approval(conn, approvals.owner.id, prepared.workflow_id)
        assert current is not None and current["expires_at"] == prepared.approval["expires_at"]


@pytest.mark.postgres
def test_missing_real_prepared_intent_is_http_404_not_fabricated_proposal(
    identity: IdentityContext,
) -> None:
    with identity.client() as client:
        assert _bootstrap(identity, client).status_code == 201
        absent = client.get(f"/api/v1/workflows/{uuid4()}/approval")
        assert absent.status_code == 404
        with identity.admin.transaction() as conn:
            owner_id = conn.execute(text("SELECT id FROM offerloom.owners")).scalar_one()
            workflow_id = uuid4()
            conn.execute(
                text("""INSERT INTO offerloom.workflow_runs(owner_id,id,kind,dedupe_key,request_sha256,deadline_at)
                VALUES(:owner,:id,'privacy_delete',:key,repeat('0',64),clock_timestamp()+interval '1 minute')"""),
                {"owner": owner_id, "id": workflow_id, "key": str(workflow_id)},
            )
        unprepared = client.get(f"/api/v1/workflows/{workflow_id}/approval")
        assert unprepared.status_code == 404 and unprepared.json()["code"] == "approval_not_found"


@pytest.mark.postgres
@pytest.mark.parametrize(
    "column,expression",
    [
        ("provider_key", "'another_provider'"),
        ("input_scope_sha256", "repeat('f',64)"),
        ("expires_at", "expires_at+interval '1 minute'"),
        ("approval_id", "gen_random_uuid()"),
    ],
)
def test_hosted_consent_binding_cannot_be_rewritten(
    approvals: ApprovalContext, column: str, expression: str
) -> None:
    prepared = _prepare(approvals.pg.runtime, approvals.owner, kind="hosted_egress")
    _approve(approvals.pg.runtime, approvals.owner, prepared)
    task = _trusted_dispatch(approvals, prepared)
    with approvals.pg.runtime.begin() as conn:
        _consume(conn, task, prepared)
    with pytest.raises(DBAPIError), approvals.pg.runtime.begin() as conn:
        conn.execute(
            text(
                f"UPDATE offerloom.hosted_egress_consents SET {column}={expression} WHERE owner_id=:owner"
            ),
            {"owner": approvals.owner.id},
        )


@pytest.mark.postgres
def test_hosted_count_and_revocation_cannot_be_reset(approvals: ApprovalContext) -> None:
    prepared = _prepare(approvals.pg.runtime, approvals.owner, kind="hosted_egress")
    _approve(approvals.pg.runtime, approvals.owner, prepared)
    task = _trusted_dispatch(approvals, prepared)
    with approvals.pg.runtime.begin() as conn:
        consent_id = UUID(str(_consume(conn, task, prepared)["consent_id"]))
        _hosted(conn, task, consent_id, prepared)
        conn.execute(
            text(
                "UPDATE offerloom.hosted_egress_consents SET revoked_at=clock_timestamp() WHERE owner_id=:owner"
            ),
            {"owner": approvals.owner.id},
        )
    for assignment in ("transmission_count=0", "revoked_at=NULL"):
        with pytest.raises(DBAPIError), approvals.pg.runtime.begin() as conn:
            conn.execute(
                text(
                    f"UPDATE offerloom.hosted_egress_consents SET {assignment} WHERE owner_id=:owner"
                ),
                {"owner": approvals.owner.id},
            )
    with approvals.pg.runtime.begin() as conn:
        row = (
            conn.execute(
                text(
                    "SELECT transmission_count,revoked_at FROM offerloom.hosted_egress_consents WHERE owner_id=:owner"
                ),
                {"owner": approvals.owner.id},
            )
            .mappings()
            .one()
        )
        assert row["transmission_count"] == 1 and row["revoked_at"] is not None


@pytest.mark.postgres
def test_same_owner_wrong_parent_audit_and_consent_bindings_are_rejected(
    approvals: ApprovalContext,
) -> None:
    first = _prepare(approvals.pg.runtime, approvals.owner, kind="hosted_egress")
    second = _prepare(approvals.pg.runtime, approvals.owner)
    with pytest.raises(DBAPIError), approvals.pg.runtime.begin() as conn:
        conn.execute(
            text("""INSERT INTO offerloom.approval_audit_events(owner_id,approval_id,workflow_run_id,event)
            VALUES(:owner,:approval,:workflow,'denied')"""),
            {
                "owner": approvals.owner.id,
                "approval": UUID(str(first.approval["id"])),
                "workflow": second.workflow_id,
            },
        )
    with pytest.raises(DBAPIError), approvals.pg.runtime.begin() as conn:
        conn.execute(
            text("""INSERT INTO offerloom.hosted_egress_consents(owner_id,workflow_run_id,provider_key,
            model_id,purpose,input_scope_sha256,settings_version,expires_at,approval_id)
            VALUES(:owner,:workflow,'synthetic_provider','bounded-model','document_generate',
            repeat('1',64),1,clock_timestamp()+interval '1 minute',:approval)"""),
            {
                "owner": approvals.owner.id,
                "approval": UUID(str(first.approval["id"])),
                "workflow": second.workflow_id,
            },
        )


@pytest.mark.postgres
def test_http_success_receipt_cannot_be_replayed_from_another_live_session(
    identity: IdentityContext,
) -> None:
    with identity.client() as first, identity.client() as second:
        assert _bootstrap(identity, first).status_code == 201
        with identity.admin.transaction() as conn:
            owner_id = conn.execute(text("SELECT id FROM offerloom.owners")).scalar_one()
            owner = _owner(conn, owner_id)
        prepared = _prepare(identity.admin.engine, owner)
        route = f"/api/v1/workflows/{prepared.workflow_id}/approval/approve"
        workflow = first.get(f"/api/v1/workflows/{prepared.workflow_id}").json()
        headers = {
            **_headers(identity, owner.csrf_token),
            "If-Match": f'"{workflow["version"]}"',
            "Idempotency-Key": uuid4().hex,
        }
        body = _decision(prepared).model_dump(mode="json")
        accepted = first.post(route, headers=headers, json=body)
        assert accepted.status_code == 200, accepted.text
        login = _login(identity, second)
        assert login.status_code == 201, login.text
        replay = second.post(
            route,
            headers={**headers, **_headers(identity, login.json()["csrf_token"])},
            json=body,
        )
        assert replay.status_code == 409, replay.text
        assert replay.json()["code"] == "session_changed"
        original_replay = first.post(route, headers=headers, json=body)
        assert original_replay.status_code == 200 and original_replay.json() == accepted.json()
        with identity.admin.transaction() as conn:
            approval = conn.execute(
                text("""SELECT approved_session_id,consumed_at FROM offerloom.action_approvals
                WHERE owner_id=:owner AND workflow_run_id=:workflow"""),
                {"owner": owner.id, "workflow": prepared.workflow_id},
            ).one()
            assert approval.approved_session_id == owner.session_id and approval.consumed_at is None
            events = (
                conn.execute(
                    text("""SELECT event,error_code FROM offerloom.approval_audit_events
                WHERE owner_id=:owner AND workflow_run_id=:workflow ORDER BY created_at,id"""),
                    {"owner": owner.id, "workflow": prepared.workflow_id},
                )
                .tuples()
                .all()
            )
            assert events == [
                ("prepared", None),
                ("approved", None),
                ("denied", "session_changed"),
            ]
            assert (
                conn.execute(
                    text("SELECT count(*) FROM offerloom.model_runs WHERE owner_id=:owner"),
                    {"owner": owner.id},
                ).scalar_one()
                == 0
            )
