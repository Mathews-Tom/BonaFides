# SPDX-License-Identifier: MIT OR Apache-2.0
"""Release-fixed schema 30 backfill; never import live route or receipt code here."""

import hashlib
import json
import re
from typing import cast
from uuid import UUID

from sqlalchemy import Connection, text


def preserve_route_blocks(connection: Connection) -> None:
    """Append mapped capability blocks; preserve unbound rows for legacy readers."""
    bindings: dict[tuple[UUID, str, str], str] = {}
    observations = connection.execute(
        text("""SELECT DISTINCT m.owner_id,m.task_kind,m.parameters->'route_identity' AS identity
        FROM bonafides.model_runs m JOIN bonafides.local_model_route_blocks b
        ON b.owner_id=m.owner_id AND b.stage=m.task_kind""")
    ).mappings()
    for observation in observations:
        identity = observation["identity"]
        if not isinstance(identity, dict):
            continue
        capability = identity.get("capability_sha256")
        if not isinstance(capability, str) or re.fullmatch(r"[0-9a-f]{64}", capability) is None:
            continue
        # The historical identity has ASCII field names and no floating-point values.
        encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        legacy = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        owner, stage = observation["owner_id"], observation["task_kind"]
        bindings[(owner, stage, legacy)] = capability
        bindings[(owner, stage, capability)] = capability
    blocks = (
        connection.execute(
            text("SELECT * FROM bonafides.local_model_route_blocks ORDER BY created_at")
        )
        .mappings()
        .all()
    )
    for block in blocks:
        capability = bindings.get((block["owner_id"], block["stage"], block["route_sha256"]))
        if capability is None:
            continue
        connection.execute(
            text("""INSERT INTO bonafides.local_model_route_blocks
            (owner_id,route_sha256,stage,error_code,created_at)
            VALUES(:owner,:sha,:stage,:code,:created) ON CONFLICT DO NOTHING"""),
            {
                "owner": cast(UUID, block["owner_id"]),
                "sha": capability,
                "stage": block["stage"],
                "code": block["error_code"],
                "created": block["created_at"],
            },
        )
