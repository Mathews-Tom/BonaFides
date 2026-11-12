# SPDX-License-Identifier: MIT OR Apache-2.0
"""Workspace/3 adds source-backed publication and writing-fact kinds.

Published workspace/1 and /2 remain frozen. Their JSON snapshot fields already
preserve the new scoped revision pins and ordering preferences losslessly.
"""

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
    ClaimEvidenceLinkRow,
    ClaimRevisionRow,
    ClaimUsageSection,
    Digest,
    DocumentsSection,
    PostingsSection,
    ProfileSection,
    ProvenanceSection,
    SourcesSection,
    Timestamp,
    TrackingSection,
)
from bonafides.portable_workspace_v2 import EvidenceSection

FORMAT_VERSION: Final = 3


class ProfessionalClaimRow(ArchiveModel):
    owner_id: UUID
    id: UUID
    profile_id: UUID
    kind: Literal[
        "identity",
        "experience",
        "education",
        "skill",
        "project",
        "certification",
        "preference",
        "other",
        "language",
        "publication",
        "writing_fact",
    ]
    version: int
    current_revision_id: UUID | None
    archived_at: Timestamp | None
    created_at: Timestamp


class ClaimsSection(ArchiveModel):
    professional_claims: list[ProfessionalClaimRow]
    claim_revisions: list[ClaimRevisionRow]
    claim_evidence_links: list[ClaimEvidenceLinkRow]


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
    format_version: Literal[3]
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
