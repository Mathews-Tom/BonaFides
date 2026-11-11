# SPDX-License-Identifier: MIT OR Apache-2.0
"""DB-free classification of literal import units; no generated profile facts."""

from __future__ import annotations

import re
from typing import cast, get_args
from uuid import UUID

from bonafides.local_model import ModelFailure
from bonafides.model_calls import OutputMismatch
from bonafides.profile_entries import ClaimKind

MAX_SOURCE_BYTES = 16000
MAX_UNITS = 100
KINDS = get_args(ClaimKind)


def projection(source_sha256: str, units: list[dict[str, object]]) -> dict[str, object]:
    """Validate the exact non-contact units selected by the deterministic importer."""
    if not isinstance(source_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
        raise ModelFailure("model_context_invalid")
    if not isinstance(units, list) or not units:
        raise ModelFailure("import_structuring_projection_unavailable")
    if len(units) > MAX_UNITS:
        raise ModelFailure("import_structuring_input_limit")
    seen: set[str] = set()
    total = 0
    for unit in units:
        if not isinstance(unit, dict) or set(unit) != {"source_id", "text", "kind"}:
            raise ModelFailure("model_context_invalid")
        identifier, text, kind = unit["source_id"], unit["text"], unit["kind"]
        if not isinstance(identifier, str) or identifier in seen:
            raise ModelFailure("model_context_invalid")
        try:
            if str(UUID(identifier)) != identifier:
                raise ValueError
        except ValueError:
            raise ModelFailure("model_context_invalid") from None
        if (
            not isinstance(text, str)
            or not text.strip()
            or not isinstance(kind, str)
            or kind not in KINDS
        ):
            raise ModelFailure("model_context_invalid")
        try:
            total += len(text.encode("utf-8"))
        except UnicodeError:
            raise ModelFailure("model_context_invalid") from None
        seen.add(identifier)
    if total > MAX_SOURCE_BYTES:
        raise ModelFailure("import_structuring_input_limit")
    return {"source_sha256": source_sha256, "units": [dict(unit) for unit in units]}


def checked_units(output: dict[str, object], context: dict[str, object]) -> dict[str, str]:
    """Accept only a complete ID-to-kind classification, never text or metadata."""

    def mismatch() -> OutputMismatch:
        return OutputMismatch(["items: invalid_source_classification"])

    if set(output) != {"schema_version", "stage", "items"} or (
        output.get("schema_version") != "1" or output.get("stage") != "structure_import"
    ):
        raise mismatch()
    expected = {str(unit["source_id"]) for unit in cast(list[dict[str, object]], context["units"])}
    items = output.get("items")
    if not isinstance(items, list) or len(items) != len(expected):
        raise mismatch()
    result: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict) or set(item) != {"source_id", "kind"}:
            raise mismatch()
        identifier, kind = item["source_id"], item["kind"]
        if (
            not isinstance(identifier, str)
            or identifier not in expected
            or identifier in result
            or not isinstance(kind, str)
            or kind not in KINDS
        ):
            raise mismatch()
        result[identifier] = kind
    if result.keys() != expected:
        raise mismatch()
    return result
