# SPDX-License-Identifier: MIT OR Apache-2.0
"""The published ``bonafides-workspace/2`` owner-data contract.

Version 2 adds captured evidence origins. Version 1 remains frozen and readable;
unchanged row and section contracts are shared, not regenerated or reinterpreted.
"""

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
    ClaimsSection,
    ClaimUsageSection,
    Digest,
    DocumentsSection,
    EvidenceClassificationChangeRow,
    EvidenceRecordRow,
    PostingsSection,
    ProfileSection,
    ProvenanceSection,
    SourcesSection,
    Timestamp,
    TrackingSection,
)

FORMAT_VERSION: Final = 2


class EvidenceRevisionRow(ArchiveModel):
    owner_id: UUID
    id: UUID
    evidence_id: UUID
    revision_no: int
    payload: JsonValue
    blob_id: UUID | None
    content_sha256: str
    created_at: Timestamp
    source_type: Literal[
        "owner_statement", "document", "work_sample", "web_page", "credential", "other"
    ]
    origin: Literal["owner_entry", "resume_import", "evidence_capture"]


class EvidenceSection(ArchiveModel):
    """Evidence records, revisions and append-only classification history."""

    evidence_records: list[EvidenceRecordRow]
    evidence_revisions: list[EvidenceRevisionRow]
    evidence_classification_changes: list[EvidenceClassificationChangeRow]


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
    """``manifest.json`` of a ``bonafides-workspace/2`` archive."""

    format: Literal["bonafides-workspace"]
    format_version: Literal[2]
    created_at: AwareDatetime
    source_namespace: UUID
    source_owner_id: UUID
    current_profile_revision_id: UUID
    manifest_sha256: Digest
    sections: WorkspaceSections
    blob_manifest: Annotated[list[BlobMember], Field(max_length=MAX_BLOBS)]
    exclusions: list[str]


def json_schema() -> str:
    """The committed schema text, byte for byte."""
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **WorkspaceManifest.model_json_schema(),
    }
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    sys.stdout.write(json_schema())
