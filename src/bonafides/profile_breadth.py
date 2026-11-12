# SPDX-License-Identifier: MIT OR Apache-2.0
"""Snapshot selection and deliberately small, literal publication formatting."""

from __future__ import annotations

import re
from collections.abc import Mapping
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import HttpUrl


def selected_claim_ids(
    snapshot: Mapping[str, object], application_id: UUID | None = None, *, writing: bool = False
) -> list[str]:
    ids = snapshot.get("claim_revision_ids", [])
    result = [str(value) for value in ids] if isinstance(ids, list) else []
    groups = snapshot.get("writing_fact_revision_ids", {})
    if writing and isinstance(groups, dict):
        for scope in ("personal", str(application_id)) if application_id else ("personal",):
            values = groups.get(scope, [])
            if isinstance(values, list):
                result.extend(str(value) for value in values)
    return result


def snapshot_claim_ids(snapshot: Mapping[str, object]) -> list[str]:
    result = selected_claim_ids(snapshot)
    groups = snapshot.get("writing_fact_revision_ids", {})
    if isinstance(groups, dict):
        for values in groups.values():
            if isinstance(values, list):
                result.extend(str(value) for value in values)
    return result


def citation_runs(markup: str) -> list[tuple[str, str]]:
    """Only balanced single/double asterisks have meaning; everything else is literal."""
    tokens = re.findall(r"\\\*|\*+|[^*\\]+|\\", markup)
    result: list[tuple[str, str]] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in ("*", "**"):
            end = index + 1
            while end < len(tokens) and tokens[end] != token:
                end += 1
            if end < len(tokens) and end > index + 1:
                value = "".join(tokens[index + 1 : end]).replace(r"\*", "*")
                result.append(("strong" if token == "**" else "em", value))
                index = end + 1
                continue
        value = "*" if token == r"\*" else token
        if result and result[-1][0] == "text":
            result[-1] = ("text", result[-1][1] + value)
        else:
            result.append(("text", value))
        index += 1
    return result


def citation_text(markup: str) -> str:
    return "".join(value for _, value in citation_runs(markup))


def normalize_citation_url(value: str) -> str:
    if not value:
        return ""
    if (
        any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in value
        )
        or "\\" in value
    ):
        raise ValueError("invalid_citation_url")
    doi = value[4:] if value.lower().startswith("doi:") else value
    if re.fullmatch(r"10\.\d{4,9}/[^\s]+", doi):
        value = "https://doi.org/" + doi
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("invalid_citation_url")
        _ = parsed.port
        HttpUrl(value)
    except ValueError as error:
        raise ValueError("invalid_citation_url") from error
    if len(value) > 500:
        raise ValueError("invalid_citation_url")
    return value
