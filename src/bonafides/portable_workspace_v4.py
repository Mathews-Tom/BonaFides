# SPDX-License-Identifier: MIT OR Apache-2.0
"""Workspace/4 preserves application conversations as inert owner history."""

from __future__ import annotations

import json
import sys
from typing import Annotated, Final, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, JsonValue

from bonafides.portable_workspace import (
    MAX_BLOBS,
    ArchiveModel,
    BlobMember,
    Digest,
    Timestamp,
)
from bonafides.portable_workspace import (
    DocumentsSection as PreviousDocumentsSection,
)
from bonafides.portable_workspace_v3 import WorkspaceSections as PreviousWorkspaceSections

FORMAT_VERSION: Final = 4


class DocumentConversationTurnRow(ArchiveModel):
    owner_id: UUID
    id: UUID
    application_id: UUID
    created_at: Timestamp
    request_text: Annotated[str, Field(min_length=1, max_length=2000)]
    context_snapshot: dict[str, JsonValue]
    reply: dict[str, JsonValue] | None
    # Like workspace3 model provenance, these are original identifiers, not authority.
    # Export coalesces live/history; import stores only historical references.
    workflow_run_id: UUID | None
    model_run_id: UUID | None
    undone_at: Timestamp | None


class DocumentsSection(PreviousDocumentsSection):
    document_conversation_turns: list[DocumentConversationTurnRow]


class WorkspaceSections(PreviousWorkspaceSections):
    documents: DocumentsSection


class WorkspaceManifest(ArchiveModel):
    format: Literal["bonafides-workspace"]
    format_version: Literal[4]
    created_at: AwareDatetime
    source_namespace: UUID
    source_owner_id: UUID
    current_profile_revision_id: UUID
    manifest_sha256: Digest
    sections: WorkspaceSections
    blob_manifest: Annotated[list[BlobMember], Field(max_length=MAX_BLOBS)]
    exclusions: list[str]


def json_schema() -> str:
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **WorkspaceManifest.model_json_schema(),
    }
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    sys.stdout.write(json_schema())
