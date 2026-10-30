# SPDX-License-Identifier: MIT OR Apache-2.0
"""Short owner-scoped transactions; callers own commit/rollback and authentication."""

import hashlib
import json
import re
from collections.abc import Callable
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import Connection, text

from bonafides.problems import Problem


def canonical_json(value: object) -> str:
    """RFC 8785 for normalized API values (no API field uses floating point)."""
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise Problem(422, "noncanonical_request") from exc
    if value is None or isinstance(value, (str, bool)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, int) and abs(value) <= 9007199254740991:
        return str(value)
    if isinstance(value, list):
        return "[" + ",".join(canonical_json(item) for item in value) + "]"
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        pairs = cast(dict[str, object], value)
        for key in pairs:
            canonical_json(key)
        return (
            "{"
            + ",".join(
                canonical_json(key) + ":" + canonical_json(pairs[key])
                for key in sorted(pairs, key=lambda key: key.encode("utf-16-be"))
            )
            + "}"
        )
    raise Problem(422, "noncanonical_request")


def require_if_match(value: str | None) -> int:
    if value is None:
        raise Problem(428, "if_match_required")
    if re.fullmatch(r'"[1-9][0-9]{0,18}"', value) is None:
        raise Problem(400, "invalid_if_match")
    version = int(value[1:-1])
    if version > 9223372036854775807:
        raise Problem(400, "invalid_if_match")
    return version


def lock_installation(conn: Connection) -> int:
    row = (
        conn.execute(
            text(
                "SELECT maintenance_active,maintenance_epoch FROM bonafides.installation_state WHERE singleton FOR SHARE"
            )
        )
        .mappings()
        .one()
    )
    if row["maintenance_active"]:
        raise Problem(503, "installation_maintenance")
    return int(row["maintenance_epoch"])


def append_outbox(
    conn: Connection,
    owner_id: UUID,
    event_type: str,
    aggregate_type: str,
    aggregate_id: UUID,
    payload: dict[str, object],
    aggregate_version: int = 1,
) -> UUID:
    event_id = uuid4()
    dedupe = f"{aggregate_type}:{aggregate_id}:{aggregate_version}:{event_type}"
    result = conn.execute(
        text("""INSERT INTO bonafides.outbox_events
        (owner_id,id,aggregate_type,aggregate_id,aggregate_version,event_type,payload,dedupe_key)
        VALUES (:owner,:id,:aggregate,:entity,:version,:event,CAST(:payload AS jsonb),:dedupe)
        ON CONFLICT(owner_id,dedupe_key) DO UPDATE SET dedupe_key=EXCLUDED.dedupe_key RETURNING id"""),
        {
            "owner": owner_id,
            "id": event_id,
            "aggregate": aggregate_type,
            "entity": aggregate_id,
            "version": aggregate_version,
            "event": event_type,
            "payload": canonical_json(payload),
            "dedupe": dedupe,
        },
    ).scalar_one()
    return cast(UUID, result)


def command(
    conn: Connection,
    owner_id: UUID,
    method: str,
    route: str,
    path: dict[str, str],
    body: dict[str, object],
    if_match: str | None,
    idempotency_key: str | None,
    resource_kind: str,
    resource_id: UUID,
    effect: Callable[[Connection], tuple[int, dict[str, object]]],
    *,
    creation_kind: str | None = None,
    workspace_change: bool = True,
) -> tuple[int, dict[str, object]]:
    expected: int | None
    if creation_kind is not None:
        if (
            creation_kind not in {"source", "posting", "application"}
            or resource_kind != creation_kind
        ):
            raise ValueError("unsupported creation aggregate")
        if if_match is not None:
            raise Problem(400, "creation_if_match_forbidden")
        expected = None
    else:
        expected = require_if_match(if_match)
    if idempotency_key is None or re.fullmatch(r"[\x21-\x7e]{16,128}", idempotency_key) is None:
        raise Problem(400, "invalid_idempotency_key")
    tables = {
        "profile": "profiles",
        "document": "document_drafts",
        "workflow": "workflow_runs",
        "source": "source_configs",
        "posting": "job_postings",
        "application": "applications",
        "workspace": "owner_settings",
    }
    if resource_kind not in tables:
        raise ValueError("unsupported command aggregate")
    lock_installation(conn)
    # Serializes an owner's request keys even when the same key changes path/resource.
    workspace = conn.execute(
        text("SELECT owner_id FROM bonafides.owner_settings WHERE owner_id=:owner FOR UPDATE"),
        {"owner": owner_id},
    ).first()
    if workspace is None:
        raise Problem(404, "owner_not_found")
    fingerprint = hashlib.sha256(
        canonical_json(
            {
                "owner": str(owner_id),
                "method": method.upper(),
                "route": route,
                "path": path,
                "body": body,
                "if_match": if_match,
            }
        ).encode()
    ).hexdigest()
    erased = conn.execute(
        text("""SELECT request_sha256 FROM bonafides.erased_request_keys
        WHERE owner_id=:owner AND route_sha256=:route AND key_sha256=:key"""),
        {
            "owner": owner_id,
            "route": hashlib.sha256(route.encode()).hexdigest(),
            "key": hashlib.sha256(idempotency_key.encode()).hexdigest(),
        },
    ).scalar_one_or_none()
    if erased is not None:
        if erased != fingerprint:
            raise Problem(409, "idempotency_payload_mismatch")
        raise Problem(410, "data_erased")
    replay = (
        conn.execute(
            text(
                "SELECT request_sha256,response_status,response_body FROM bonafides.idempotency_records WHERE owner_id=:owner AND route_key=:route AND key=:key FOR UPDATE"
            ),
            {"owner": owner_id, "route": route, "key": idempotency_key},
        )
        .mappings()
        .first()
    )
    if replay is not None:
        if replay["request_sha256"] != fingerprint:
            raise Problem(409, "idempotency_payload_mismatch")
        return int(replay["response_status"]), cast(dict[str, object], replay["response_body"])
    if creation_kind is None:
        if resource_kind == "source":
            # Source commands may invalidate runs. Match worker publication's lock order.
            conn.execute(
                text("""SELECT id FROM bonafides.workflow_runs
                WHERE owner_id=:owner AND source_config_id=:id
                AND state IN ('pending','running','retryable_failure','waiting_for_approval')
                ORDER BY id FOR UPDATE"""),
                {"owner": owner_id, "id": resource_id},
            )
        table = tables[resource_kind]  # Static allowlist, never user-controlled SQL.
        if resource_kind == "workspace":
            current = conn.execute(
                text("""SELECT workspace_version FROM bonafides.owner_settings
                WHERE owner_id=:owner FOR UPDATE"""),
                {"owner": owner_id},
            ).scalar_one_or_none()
        else:
            current = conn.execute(
                text(
                    f"SELECT version FROM bonafides.{table} WHERE owner_id=:owner AND id=:id FOR UPDATE"
                ),
                {"owner": owner_id, "id": resource_id},
            ).scalar_one_or_none()
        if current is None:
            raise Problem(404, "resource_not_found")
        if int(current) != expected:
            raise Problem(412, "version_conflict")
    status, response = effect(conn)
    if workspace_change:
        conn.execute(
            text(
                "UPDATE bonafides.owner_settings SET workspace_version=workspace_version+1,updated_at=clock_timestamp() WHERE owner_id=:owner"
            ),
            {"owner": owner_id},
        )
    conn.execute(
        text("""INSERT INTO bonafides.idempotency_records(owner_id,route_key,key,request_sha256,response_status,response_body,expires_at)
        VALUES(:owner,:route,:key,:sha,:status,CAST(:response AS jsonb),clock_timestamp()+interval '31 days')"""),
        {
            "owner": owner_id,
            "route": route,
            "key": idempotency_key,
            "sha": fingerprint,
            "status": status,
            "response": canonical_json(response),
        },
    )
    return status, response
