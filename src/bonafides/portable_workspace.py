# SPDX-License-Identifier: MIT OR Apache-2.0
"""The published ``bonafides-workspace/1`` owner-data contract.

The contract has its own version axis: no database schema number appears in it.
Every row keeps the exact JSON values the exporting database produced, so an
import reproduces identifiers, revision pins and statuses without recomputing
anything. Any change to these models is a new ``format_version``: keep a reader
for each published version and regenerate the committed JSON Schema with
``python -m bonafides.portable_workspace > docs/schemas/bonafides-workspace-1.schema.json``.
"""

from __future__ import annotations

import json
import sys
from typing import Annotated, Final, Literal, cast
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue

FORMAT: Final = "bonafides-workspace"
FORMAT_VERSION: Final = 1
MAX_BLOB_BYTES = 64 * 1024 * 1024
MAX_BLOBS = 10_000
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
# Exact PostgreSQL JSON text: never re-rendered, so pins and hashes stay byte-identical.
Timestamp = Annotated[
    str,
    Field(
        pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?[+-][0-9]{2}(:[0-9]{2}){0,2}$"
    ),
]
LocalTimestamp = Annotated[
    str, Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?$")
]
Stage = Literal[
    "preparing", "applied", "screening", "interview", "offer", "accepted", "rejected", "withdrawn"
]


class ArchiveModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class BlobMember(ArchiveModel):
    blob_id: UUID
    sha256: Digest
    byte_length: Annotated[int, Field(ge=1, le=MAX_BLOB_BYTES)]
    media_type: Annotated[str, Field(min_length=1, max_length=200)]
    archive_member: Annotated[str, Field(pattern=r"^blobs/[0-9a-f]{64}$")]


class ProfileRow(ArchiveModel):
    owner_id: UUID
    id: UUID
    version: int
    current_revision_id: UUID | None
    created_at: Timestamp


class ProfileRevisionRow(ArchiveModel):
    owner_id: UUID
    id: UUID
    profile_id: UUID
    revision_no: int
    snapshot: JsonValue
    content_sha256: str
    reason: str
    created_at: Timestamp


class ProfileRevisionClaimRow(ArchiveModel):
    owner_id: UUID
    profile_revision_id: UUID
    claim_revision_id: UUID


class ProfileRevisionEvidenceRow(ArchiveModel):
    owner_id: UUID
    profile_revision_id: UUID
    evidence_revision_id: UUID


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
    ]
    version: int
    current_revision_id: UUID | None
    archived_at: Timestamp | None
    created_at: Timestamp


class ClaimRevisionRow(ArchiveModel):
    owner_id: UUID
    id: UUID
    claim_id: UUID
    revision_no: int
    payload: JsonValue
    content_sha256: str
    created_at: Timestamp


class ClaimEvidenceLinkRow(ArchiveModel):
    owner_id: UUID
    claim_revision_id: UUID
    evidence_revision_id: UUID
    support_kind: Literal["direct", "user_asserted", "context"]
    excerpt: str
    created_at: Timestamp


class EvidenceRecordRow(ArchiveModel):
    owner_id: UUID
    id: UUID
    version: int
    current_revision_id: UUID | None
    archived_at: Timestamp | None
    created_at: Timestamp
    privacy_class: Literal["public", "private", "confidential"]
    employer_owned: bool


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
    origin: Literal["owner_entry", "resume_import"]


class EvidenceClassificationChangeRow(ArchiveModel):
    owner_id: UUID
    evidence_id: UUID
    sequence: int
    privacy_class: Literal["public", "private", "confidential"]
    employer_owned: bool
    changed_at: Timestamp


class DocumentClaimLinkRow(ArchiveModel):
    owner_id: UUID
    id: UUID
    document_revision_id: UUID
    claim_path: str
    claim_revision_id: UUID | None
    evidence_revision_id: UUID | None
    excerpt: str
    status: Literal["supported", "unsupported", "user_asserted"]
    created_at: Timestamp


class DocumentDraftRow(ArchiveModel):
    owner_id: UUID
    id: UUID
    application_id: UUID | None
    kind: Literal["resume", "cover_letter"]
    profile_revision_id: UUID
    current_revision_id: UUID | None
    version: int
    stale_at: Timestamp | None
    created_at: Timestamp
    scope: Literal["base", "tailored"]
    deleted_at: Timestamp | None


class DocumentRevisionRow(ArchiveModel):
    owner_id: UUID
    id: UUID
    document_draft_id: UUID
    revision_no: int
    profile_revision_id: UUID
    model_run_id: UUID | None
    body: JsonValue
    posting_snapshot: JsonValue
    provenance_snapshot: JsonValue
    template_version: str
    content_sha256: str
    origin: Literal["generated", "deterministic", "manual", "restore"]
    reason: str
    created_at: Timestamp
    scope: Literal["base", "tailored"]
    validation_issues: JsonValue


class DocumentReviewRow(ArchiveModel):
    owner_id: UUID
    id: UUID
    document_revision_id: UUID
    decision: Literal["accepted", "changes_requested"]
    reviewed_content_sha256: str
    created_at: Timestamp
    reviewed_block_keys: JsonValue


class DocumentArtifactRow(ArchiveModel):
    owner_id: UUID
    id: UUID
    document_revision_id: UUID
    blob_id: UUID
    renderer_version: str
    format: Literal["pdf", "docx"]
    created_at: Timestamp


class DocumentRegenerationProposalRow(ArchiveModel):
    owner_id: UUID
    id: UUID
    document_draft_id: UUID
    target_revision_id: UUID
    target_content_sha256: str
    target_document_version: int
    section_key: str
    original_section: JsonValue
    proposed_section: JsonValue
    validation_issues: JsonValue
    model_run_id: UUID | None
    workflow_run_id: UUID | None
    model_run: JsonValue
    context_snapshot: JsonValue
    created_at: Timestamp


class ApplicationRow(ArchiveModel):
    owner_id: UUID
    id: UUID
    opportunity_id: UUID
    current_stage: Stage
    version: int
    created_at: Timestamp
    updated_at: Timestamp
    archived_at: Timestamp | None
    attempt_no: int
    current_stage_event_id: UUID | None


class ApplicationStageEventRow(ArchiveModel):
    owner_id: UUID
    id: UUID
    application_id: UUID
    from_stage: Stage | None
    to_stage: Stage
    effective_at: Timestamp
    reason: str
    created_at: Timestamp
    sequence: int
    correction_of: UUID | None
    affects_current_stage: bool


class ApplicationNoteRow(ArchiveModel):
    owner_id: UUID
    id: UUID
    application_id: UUID
    body: str
    version: int
    created_at: Timestamp
    updated_at: Timestamp


class ApplicationContactRow(ArchiveModel):
    owner_id: UUID
    id: UUID
    application_id: UUID
    display_name: str
    email: str | None
    role_label: str | None
    version: int
    created_at: Timestamp


class AppointmentRow(ArchiveModel):
    owner_id: UUID
    id: UUID
    application_id: UUID
    kind: Literal["interview", "follow_up"]
    title: str
    starts_at: Timestamp
    timezone: str
    local_start: LocalTimestamp
    duration_minutes: int | None
    completed_at: Timestamp | None
    version: int
    created_at: Timestamp
    ends_at: Timestamp | None
    local_end: LocalTimestamp | None
    display_timezone: str
    status: Literal["scheduled", "completed", "cancelled"]
    cancelled_at: Timestamp | None


class ApplicationAttachmentRow(ArchiveModel):
    owner_id: UUID
    id: UUID
    application_id: UUID
    blob_id: UUID
    display_name: str
    created_at: Timestamp


class JobPostingRow(ArchiveModel):
    owner_id: UUID
    id: UUID
    title: str
    company: str
    location_text: str
    description_text: str
    canonical_url: str | None
    dedupe_key: str
    version: int
    status: Literal["open", "closed", "unknown"]
    requirements: JsonValue
    requirements_reviewed_at: Timestamp | None
    created_at: Timestamp
    updated_at: Timestamp
    source_config_id: UUID | None
    external_id: str | None
    content_sha256: str | None
    last_observed_at: Timestamp | None


class JobSourceObservationRow(ArchiveModel):
    owner_id: UUID
    id: UUID
    posting_id: UUID
    source_config_id: UUID
    external_id: str
    content_sha256: str
    fetched_at: Timestamp
    source_url: str
    snapshot: JsonValue
    created_at: Timestamp
    policy_version: int
    policy_review_id: UUID | None


class SavedOpportunityRow(ArchiveModel):
    owner_id: UUID
    id: UUID
    posting_id: UUID
    disposition: Literal["saved", "dismissed", "archived", "unmarked"]
    version: int
    created_at: Timestamp


class PostingDuplicateDecisionRow(ArchiveModel):
    owner_id: UUID
    posting_id: UUID
    candidate_id: UUID
    decision: Literal["suspected", "distinct"]
    version: int
    created_at: Timestamp
    updated_at: Timestamp


class PostingRequirementRevisionRow(ArchiveModel):
    owner_id: UUID
    id: UUID
    posting_id: UUID
    revision_no: int
    source_text: str
    source_sha256: str
    posting_content_sha256: str
    requirements: JsonValue
    confirmed: bool
    provenance: JsonValue
    created_at: Timestamp


class MatchAssessmentRow(ArchiveModel):
    owner_id: UUID
    id: UUID
    posting_id: UUID
    profile_revision_id: UUID
    model_run_id: UUID | None
    posting_version: int
    hard_constraints: JsonValue
    evidence_coverage: JsonValue
    explanation: str
    created_at: Timestamp
    requirements_revision_id: UUID | None
    hard_eligibility: Literal["met", "not_met", "unknown"]
    provenance: JsonValue


class SourceConfigRow(ArchiveModel):
    owner_id: UUID
    id: UUID
    source_kind: Literal["manual", "greenhouse"]
    board_token: str | None
    enabled: bool
    terms_url: str | None
    terms_reviewed_at: Timestamp | None
    terms_revision: str | None
    allowed_host: str | None
    request_limit_per_minute: int
    last_success_at: Timestamp | None
    last_error_code: str | None
    next_allowed_at: Timestamp | None
    version: int
    created_at: Timestamp
    company: str
    policy_version: int
    current_policy_review_id: UUID | None
    rate_limit_blocked: bool
    rate_limit_retry_after_seconds: str | None
    removed_at: Timestamp | None


class SourcePolicyReviewRow(ArchiveModel):
    owner_id: UUID
    id: UUID
    source_config_id: UUID
    policy_version: int
    board_token: str
    company: str
    terms_url: str
    terms_revision: str
    allowed_host: str
    request_limit_per_minute: int
    policy_sha256: str
    acknowledged_by_owner_id: UUID
    reviewed_at: Timestamp


class ModelRunRecord(ArchiveModel):
    owner_id: UUID
    id: UUID
    workflow_run_id: UUID
    provider_key: str
    model_id: str
    model_revision: str | None
    task_kind: str
    prompt_version: str
    input_sha256: str
    output_sha256: str | None
    parameters: JsonValue
    egress_class: str
    input_tokens: int | None
    output_tokens: int | None
    reserved_tokens: int
    reserved_cost_microunits: int
    actual_cost_microunits: int | None
    status: str
    created_at: Timestamp
    finished_at: Timestamp | None
    usage_status: str
    metadata: JsonValue


class ModelRunProvenance(ArchiveModel):
    """A historical model-run observation, never live execution authority."""

    source_namespace: UUID
    source_owner_id: UUID
    record: ModelRunRecord


class ProfileSection(ArchiveModel):
    """The owner's profile and its immutable revisions."""

    profiles: list[ProfileRow]
    profile_revisions: list[ProfileRevisionRow]
    profile_revision_claims: list[ProfileRevisionClaimRow]
    profile_revision_evidence: list[ProfileRevisionEvidenceRow]


class ClaimsSection(ArchiveModel):
    """Structured claims, their revisions and evidence support."""

    professional_claims: list[ProfessionalClaimRow]
    claim_revisions: list[ClaimRevisionRow]
    claim_evidence_links: list[ClaimEvidenceLinkRow]


class EvidenceSection(ArchiveModel):
    """Evidence records, revisions and append-only classification history."""

    evidence_records: list[EvidenceRecordRow]
    evidence_revisions: list[EvidenceRevisionRow]
    evidence_classification_changes: list[EvidenceClassificationChangeRow]


class ClaimUsageSection(ArchiveModel):
    """Which pinned claim revision each document statement cites, and its status."""

    document_claim_links: list[DocumentClaimLinkRow]


class DocumentsSection(ArchiveModel):
    """Résumé and letter drafts, revisions, reviews and rendered artifacts."""

    document_drafts: list[DocumentDraftRow]
    document_revisions: list[DocumentRevisionRow]
    document_reviews: list[DocumentReviewRow]
    document_artifacts: list[DocumentArtifactRow]
    document_regeneration_proposals: list[DocumentRegenerationProposalRow]


class TrackingSection(ArchiveModel):
    """Applications, stage history, notes, contacts, appointments and attachments."""

    applications: list[ApplicationRow]
    application_stage_events: list[ApplicationStageEventRow]
    application_notes: list[ApplicationNoteRow]
    application_contacts: list[ApplicationContactRow]
    appointments: list[AppointmentRow]
    application_attachments: list[ApplicationAttachmentRow]


class PostingsSection(ArchiveModel):
    """Saved postings, observations, requirements and match assessments."""

    job_postings: list[JobPostingRow]
    job_source_observations: list[JobSourceObservationRow]
    saved_opportunities: list[SavedOpportunityRow]
    posting_duplicate_decisions: list[PostingDuplicateDecisionRow]
    posting_requirement_revisions: list[PostingRequirementRevisionRow]
    match_assessments: list[MatchAssessmentRow]


class SourcesSection(ArchiveModel):
    """Posting source configurations and their policy reviews."""

    source_configs: list[SourceConfigRow]
    source_policy_reviews: list[SourcePolicyReviewRow]


class ProvenanceSection(ArchiveModel):
    """Model runs that produced exported records."""

    model_run_provenance: list[ModelRunProvenance]


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


# Section name -> its row lists, in contract order.
SECTION_TABLES = {
    name: tuple(cast(type[BaseModel], field.annotation).model_fields)
    for name, field in WorkspaceSections.model_fields.items()
}


class WorkspaceManifest(ArchiveModel):
    """``manifest.json`` of a ``bonafides-workspace/1`` archive."""

    format: Literal["bonafides-workspace"]
    format_version: Literal[1]
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
