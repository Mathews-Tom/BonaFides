# SPDX-License-Identifier: MIT OR Apache-2.0
"""Workspace/5 preserves application-email drafts without changing published readers."""

from __future__ import annotations

import json
import sys
from typing import Annotated, Final, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from bonafides.portable_workspace import (
    MAX_BLOBS,
    ArchiveModel,
    BlobMember,
    ClaimUsageSection,
    Digest,
    DocumentArtifactRow,
    DocumentRegenerationProposalRow,
    DocumentReviewRow,
    DocumentRevisionRow,
    PostingsSection,
    ProfileSection,
    ProvenanceSection,
    SourcesSection,
    Timestamp,
    TrackingSection,
)
from bonafides.portable_workspace_v2 import EvidenceSection
from bonafides.portable_workspace_v3 import ClaimsSection
from bonafides.portable_workspace_v4 import DocumentConversationTurnRow

FORMAT_VERSION: Final = 5


class DocumentDraftRow(ArchiveModel):
    owner_id: UUID
    id: UUID
    application_id: UUID | None
    kind: Literal["resume", "cover_letter", "application_email"]
    profile_revision_id: UUID
    current_revision_id: UUID | None
    version: int
    stale_at: Timestamp | None
    created_at: Timestamp
    scope: Literal["base", "tailored"]
    deleted_at: Timestamp | None


class DocumentsSection(ArchiveModel):
    document_drafts: list[DocumentDraftRow]
    document_revisions: list[DocumentRevisionRow]
    document_reviews: list[DocumentReviewRow]
    document_artifacts: list[DocumentArtifactRow]
    document_regeneration_proposals: list[DocumentRegenerationProposalRow]
    document_conversation_turns: list[DocumentConversationTurnRow]


class WorkspaceSections(ArchiveModel):
    profile: ProfileSection
    claims: ClaimsSection
    evidence: EvidenceSection
    claim_usage: ClaimUsageSection
    documents: DocumentsSection
    tracking: TrackingSection
    postings: PostingsSection
    sources: SourcesSection
    provenance: ProvenanceSection


class WorkspaceManifest(ArchiveModel):
    format: Literal["bonafides-workspace"]
    format_version: Literal[5]
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
