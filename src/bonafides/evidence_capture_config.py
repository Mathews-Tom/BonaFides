# SPDX-License-Identifier: MIT OR Apache-2.0
"""Private capture configuration shared by the worker and source gateway."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StrictBool, field_validator

from bonafides.auth import StrictModel
from bonafides.commands import canonical_json
from bonafides.config import read_secret
from bonafides.evidence_provenance import PrivacyClass
from bonafides.problems import Problem

MAX_COMMITS = 20
MAX_TEXT = 8000
_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_OWNER = re.compile(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,37}[a-zA-Z0-9])?\Z")
_REPOSITORY = re.compile(r"[a-zA-Z0-9_.-]{1,100}\Z")


def validate_github_repository(value: str) -> str:
    if not isinstance(value, str) or value.count("/") != 1:
        raise ValueError("explicit GitHub owner/repository required")
    owner, repository = value.split("/")
    if (
        _OWNER.fullmatch(owner) is None
        or "--" in owner
        or _REPOSITORY.fullmatch(repository) is None
        or repository in {".", ".."}
    ):
        raise ValueError("invalid GitHub owner/repository")
    return value.lower()


class _Source(StrictModel):
    @property
    def identity_sha256(self) -> str:
        return hashlib.sha256(canonical_json(self.model_dump(mode="json")).encode()).hexdigest()

    def public(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"repository", "token_file"})


class LocalGitSource(_Source):
    # Keep the PR1 fields, including required enabled, exactly unchanged.
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    label: str = Field(min_length=1, max_length=150)
    kind: Literal["local_git"]
    repository: Path
    enabled: StrictBool
    privacy_class: PrivacyClass
    employer_owned: StrictBool

    @field_validator("repository")
    @classmethod
    def absolute_repository(cls, value: Path) -> Path:
        if not value.is_absolute() or ".." in value.parts or "\x00" in str(value):
            raise ValueError("absolute repository required")
        return value


class GitHubSource(_Source):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    label: str = Field(min_length=1, max_length=150)
    kind: Literal["github"]
    repository: str
    enabled: StrictBool = False
    token_file: Path | None = None
    privacy_class: PrivacyClass
    employer_owned: StrictBool

    @field_validator("repository")
    @classmethod
    def explicit_repository(cls, value: str) -> str:
        return validate_github_repository(value)

    @field_validator("token_file")
    @classmethod
    def absolute_token_file(cls, value: Path | None) -> Path | None:
        # Permissions/content are checked only by the gateway when reading the secret.
        if value is not None and (
            not value.is_absolute() or ".." in value.parts or "\x00" in str(value)
        ):
            raise ValueError("absolute token file required")
        return value


CaptureSource = Annotated[LocalGitSource | GitHubSource, Field(discriminator="kind")]


def capture_reference(source: CaptureSource, sha: str) -> str:
    if not isinstance(sha, str) or _SHA.fullmatch(sha) is None:
        raise ValueError("full commit SHA required")
    if source.kind == "github":
        return f"https://github.com/{source.repository}/commit/{sha}"
    return f"local-git:{source.id}@{sha}"


class _Configuration(StrictModel):
    version: Literal[1]
    sources: list[CaptureSource] = Field(max_length=20)


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate configuration key")
        result[key] = value
    return result


def load_sources(path: Path | None) -> dict[str, CaptureSource]:
    if path is None:
        return {}
    try:
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("absolute configuration required")
        config = _Configuration.model_validate(
            json.loads(
                read_secret(path, "evidence capture configuration"), object_pairs_hook=_object
            )
        )
        sources = {source.id: source for source in config.sources}
        if len(sources) != len(config.sources):
            raise ValueError("duplicate source identity")
        repositories = [source.repository for source in config.sources if source.kind == "github"]
        if len(repositories) != len(set(repositories)):
            raise ValueError("duplicate GitHub repository")
        return sources
    except (OSError, ValueError, TypeError) as error:
        raise Problem(503, "capture_configuration_invalid") from error
