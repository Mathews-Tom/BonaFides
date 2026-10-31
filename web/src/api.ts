// SPDX-License-Identifier: MIT OR Apache-2.0
export interface Session { owner_id: string; login_name: string; csrf_token: string; expires_at: string }
export interface Challenge { csrf_token: string; bootstrap_available: boolean }
export interface ProfileContact { email: string; phone: string; location: string; postal_address: string }
export interface Snapshot { display_name: string; headline: string; description?: string; claim_revision_ids: string[]; evidence_revision_ids?: string[]; contact?: ProfileContact | null }
export type ClaimKind = 'identity' | 'experience' | 'education' | 'skill' | 'project' | 'certification' | 'preference' | 'other' | 'language';
export interface ClaimPayload { text: string; organization?: string; role?: string; start_date?: string; end_date?: string; proficiency?: string; project_url?: string }
export type ClaimRateWording = 'per' | 'a' | 'an' | 'each' | 'every' | 'adverbial' | 'annual';
export type ClaimPeriodStyle = 'parenthetical' | 'in_year' | 'range';
export type ClaimPeriodSeparator = 'hyphen' | 'en_dash' | 'em_dash';
export interface ClaimMetric { value: string; unit: string; magnitude?: string; change?: string; per?: string; rate_wording?: ClaimRateWording | null; measure?: string }
export interface ClaimPeriod { start: string; end: string; style?: ClaimPeriodStyle | null; separator?: ClaimPeriodSeparator | null }
export interface StructuredClaim { schema_version: 1; verb: string; subject: string; scope?: string; metric?: ClaimMetric; period?: ClaimPeriod }
export type StructuredClaimPayload = Omit<ClaimPayload, 'text'> & { claim: StructuredClaim };
export type ProfileClaimPayload = ClaimPayload | StructuredClaimPayload;
export interface ClaimVocabulary { language: string; verbs: { key: string; label: string }[]; units: string[]; changes: string[]; magnitudes: string[]; rates: string[]; rate_wordings: ClaimRateWording[]; period_styles: ClaimPeriodStyle[]; period_separators: ClaimPeriodSeparator[] }
export interface ClaimRendering { language: string; rendering: string }
/** Literal view of a saved claim: structured statements contribute their rendering as text. */
export function literalClaimPayload(claim: PinnedClaim): ClaimPayload {
  const payload = claim.revision.payload;
  if (!('claim' in payload)) return payload;
  const context: Partial<StructuredClaimPayload> = { ...payload };
  delete context.claim;
  return { ...context, text: claim.rendering };
}
export interface RevisionMetadata { id: string; revision_no: number; content_sha256: string; created_at: string; author_owner_id: string }
export type EvidenceSourceType = 'owner_statement' | 'document' | 'work_sample' | 'web_page' | 'credential' | 'other';
export type EvidenceOrigin = 'owner_entry' | 'resume_import';
export type EvidencePrivacyClass = 'public' | 'private' | 'confidential';
export interface EvidenceClassification { source_type: EvidenceSourceType; privacy_class: EvidencePrivacyClass; employer_owned: boolean }
export interface EvidenceProvenance extends EvidenceClassification { origin: EvidenceOrigin }
export const evidenceSourceTypes: Record<EvidenceSourceType, string> = {
  owner_statement: 'Owner statement', document: 'Document', work_sample: 'Work sample', web_page: 'Web page', credential: 'Credential', other: 'Other',
};
export const evidenceOrigins: Record<EvidenceOrigin, string> = { owner_entry: 'Entered by owner', resume_import: 'Résumé import' };
export const evidencePrivacyClasses: Record<EvidencePrivacyClass, string> = { public: 'Public', private: 'Private', confidential: 'Confidential' };
export interface EvidenceRevision extends RevisionMetadata { payload: { text: string; source: string }; provenance: EvidenceProvenance }
export interface EvidenceLink { evidence_revision_id: string; support_kind: 'direct' | 'user_asserted' | 'context'; excerpt: string }
export interface ClaimRevision extends RevisionMetadata { payload: ProfileClaimPayload; evidence_links: (EvidenceLink & { evidence: EvidenceRevision })[] }
export interface PinnedClaim { id: string; kind: ClaimKind; revision: ClaimRevision; rendering: string; needs_attention: boolean }
export interface ClaimRecord extends PinnedClaim { version: number; archived: boolean; current_revision_id: string }
export interface PinnedEvidence { id: string; revision: EvidenceRevision }
export interface EvidenceRecord extends PinnedEvidence { version: number; archived: boolean; current_revision_id: string }
export interface ProfileRevisionSummary extends RevisionMetadata { snapshot: Snapshot; reason: string }
export interface ProfileRevision extends ProfileRevisionSummary { claims: PinnedClaim[]; evidence: PinnedEvidence[] }
export interface Profile { id: string; version: number; current_revision: ProfileRevisionSummary & { claims: ClaimRecord[] }; evidence: EvidenceRecord[]; quality_warnings: string[]; experience_coverage: { minimum_days: number; maximum_days: number; undated_or_open_entries: number } }
export interface RevisionPage { items: ProfileRevisionSummary[]; next_cursor: number | null }
export interface FieldError { loc: (string | number)[]; code: string; message: string }
export interface TaskRetentionStatus {
  state: 'not_run' | 'in_progress' | 'succeeded' | 'failed';
  last_success_at: string | null; last_failure_at: string | null;
  failure_code: 'task_input_retention_failed' | null; processed_count: number;
  policy: { terminal_input_retention_seconds: number; sweep_interval_seconds: number; batch_retry_interval_seconds: number; batch_size: number; extension_supported: false };
}
export type WorkflowState = 'pending' | 'running' | 'waiting_for_approval' | 'retryable_failure' | 'permanent_failure' | 'cancelled' | 'succeeded';
export interface WorkflowPage { items: Workflow[]; next_cursor: string | null }
export interface TaskPage<T> { items: T[]; next_cursor: string | null }
export interface WorkflowStep {
  id: string; step_key: string; attempt: number; fence: number; state: string;
  delivery_state: 'not_attempted' | 'known_failed' | 'confirmed' | 'unknown';
  error_code: string | null; created_at: string; completed_at: string | null;
}
export interface AttemptEvent { id: string; attempt: number; fence: number; event: string; error_code: string | null; created_at: string }
export interface ActionApproval {
  id: string; workflow_run_id: string; action_kind: 'hosted_egress' | 'replace_workspace' | 'privacy_delete';
  intent: Record<string, unknown>; payload_sha256: string; destination_sha256: string; revision_id: string | null;
  target_version: number; expires_at: string; approved_at: string | null; consumed_at: string | null; version: number;
  applicable: boolean; reason: string; capability_available: boolean; confirmation_phrase: string | null;
}
export interface ActionApprovalRequest {
  payload_sha256: string; destination_sha256: string; revision_id: string | null; target_version: number; confirmed: true; confirmation_phrase: string | null;
}
export interface InstallationStatus {
  namespace_id: string; maintenance_epoch: number; workspace_version: number;
  maintenance_active: boolean; recovery_required: boolean;
  operation: { workflow_id: string; purpose: string; phase: string } | null;
  drain: { active_calls: number; provider_executions: number };
}
export interface PortablePlan {
  id: string; workflow_run_id: string; action_kind: 'replace_workspace' | 'privacy_delete';
  binding: { namespace_id: string; workspace_version: number; [key: string]: unknown };
  expires_at: string; remaining_copies: string[]; connections_after_operation: string;
  payload: { categories?: string[]; application_id?: string; application_version?: number; replacement_sha256?: string };
  record_counts: Record<string, number>; credentials_erased?: boolean; local_login_retained?: boolean;
  source_owner_id?: string; source_namespace?: string; source_schema_version?: number;
  manifest_sha256?: string; blob_count?: number; byte_length?: number; checkpoint_id?: string;
  runtime_pins?: Record<string, unknown>; credential_policy?: string;
}
export interface PortablePreview {
  id: string; workflow_run_id: string; version: number; workflow: Workflow; plan: PortablePlan;
  preview_sha256: string; expires_at: string; cleared_at: string | null; clear_reason: string | null;
  approval: ActionApproval | null;
}
export interface PortablePreviewPage { workspace_version: number; items: PortablePreview[] }
export interface InstallationBackups {
  configuration_present: boolean; eligible_checkpoint_id: string | null; eligibility_reason: string | null;
  key_custody: string; offhost_copy: string;
  generations: { id: string; captured_at: string; restore_verified_at: string | null; byte_length: number }[];
}
export const MAX_PORTABLE_BYTES = 10_000 * 64 * 1024 * 1024 + 20 * 1024 * 1024;
export interface Workflow {
  id: string; version: number; state: WorkflowState; attempt: number; kind?: string; mode?: 'deterministic_base' | 'tailored_ai' | 'section_regeneration';
  max_attempts: number; next_attempt_at: string | null; last_error_code: string | null;
  created_at: string; updated_at: string; deadline_at: string; cancel_requested_at: string | null;
  lease_expires_at: string | null; last_heartbeat_at: string | null; queue_age_seconds: number | null;
  remaining_attempts: number; cancelable: boolean; retryable: boolean; retry_block_reason: string | null;
  cancellation: { requested: boolean; local_fence_settled: boolean; provider_stopped: null; completed_effects_preserved: true; reason: 'local_publication_revoked_provider_may_continue' | null };
  delivery_state: WorkflowStep['delivery_state'];
  usage: {
    status: 'not_applicable' | 'known' | 'unknown'; model_runs: number; inference_post_attempts: number; inference_post_limit: number | null;
    input_tokens: number | null; output_tokens: number | null; token_budget: number; tokens_used: number;
    cost_budget_microunits: number; cost_used_microunits: number | null; currency: string;
  };
  steps?: TaskPage<WorkflowStep>; attempt_events?: TaskPage<AttemptEvent>; approval?: ActionApproval | null;
  result: {
    document_id?: string; revision_id?: string; artifact_id?: string; application_id?: string; model_run_id?: string;
    proposal_id?: string; target_revision_id?: string; section_key?: string;
    documents?: { kind: DocumentKind; document_id: string; revision_id: string }[];
    source_id?: string; posting_id?: string; profile_revision_id?: string; requirements_revision_id?: string; source_sha256?: string;
    requirements?: Requirement[]; assessments?: Coverage[]; policy_version?: number; fetch_elapsed_ms?: number; posting_count?: number;
    new_posting_count?: number; changed_posting_count?: number; unchanged_posting_count?: number; excluded_prospect_count?: number;
    fetched_at?: string; freshness?: 'current'; error_code?: string;
  } | null;
}
export type DocumentKind = 'resume' | 'cover_letter';
export interface DocumentBlock { block_key: string; text: string; claim_kind: 'factual' | 'intent' | 'greeting'; claim_revision_ids: string[]; evidence_revision_ids: string[] }
export interface DocumentBody { title: string; sections: { key: string; heading: string; blocks: DocumentBlock[] }[] }
export interface DocumentFact {
  claim_revision_id: string; evidence_revision_id: string | null; claim_text: string; evidence_text: string;
  source: string; claim_sha256: string; evidence_sha256: string | null; status: 'supported' | 'user_asserted' | 'unsupported';
}
export interface DocumentProvenance {
  facts?: DocumentFact[]; frame?: Record<string, unknown>; mode?: string; schema_version?: number | string;
  model_run?: Record<string, unknown> | null; [key: string]: unknown;
}
export interface DocumentIssue { path: string; code: string }
export interface DocumentRevision {
  id: string; revision_no: number; body: DocumentBody; content_sha256: string; profile_revision_id: string;
  template_version: string; origin: string; created_at: string; reason?: string;
  provenance_snapshot: DocumentProvenance; validation_issues: DocumentIssue[];
}
export interface DocumentRecord {
  id: string; version: number; kind: DocumentKind; scope: 'base' | 'tailored'; application_id: string | null;
  current_revision: DocumentRevision; reviewed: boolean; unsupported_claims: string[]; stale: boolean; stale_reason: string | null;
  artifacts: { id: string; revision_id: string; format: 'pdf' }[];
}
export interface DocumentRevisionPage { items: DocumentRevision[]; next_cursor: number | null }
export interface DocumentTemplate { version: string; label: string; language: 'en'; paper: 'A4' | 'Letter' }
export interface DocumentDefaults {
  version: number; template_version: string; tone: 'professional' | 'concise'; language: 'en';
  revision_retention: 'until_owner_erasure'; task_input_retention_seconds: 86400;
  temporary_export_retention_seconds: 86400; extension_supported: false;
}
export type DocumentDefaultsPatch = Partial<Pick<DocumentDefaults, 'template_version' | 'tone'>>;
export interface GenerationRoute { status: 'disabled' | 'unqualified' | 'qualified'; reason: string; identity: Record<string, unknown> | null }
export interface ApplicationDocuments {
  application_id: string; version: number; items: DocumentRecord[]; generation_route: GenerationRoute; requirements_revision_id: string | null;
}
export interface GenerationContext {
  application_id: string; application_version: number; profile_revision_id: string; profile_version: number;
  requirements_revision_id: string; template_version: string; tone: 'professional' | 'concise'; language: 'en';
  context_sha256: string; model_projection: Record<string, unknown>; frame: Record<string, unknown>; generation_route: GenerationRoute;
}
export interface SectionRegenerationContext {
  document_id: string; document_version: number; revision_id: string; content_sha256: string;
  section_key: string; tone: 'professional' | 'concise'; context_sha256: string;
  model_projection: Record<string, unknown>; generation_route: GenerationRoute;
}
export interface SectionProposal {
  id: string; document_id: string; target_revision_id: string; target_content_sha256: string;
  target_document_version: number; section_key: string; original_section: DocumentBody['sections'][number];
  proposed_section: DocumentBody['sections'][number]; validation_issues: DocumentIssue[];
  model_run: Record<string, unknown>; created_at: string; applicable: boolean; historical: boolean;
}
export interface SectionProposalPage { items: SectionProposal[]; next_cursor: string | null }
export interface SectionRegenerationRequest {
  revision_id: string; content_sha256: string; section_key: string; tone: 'professional' | 'concise'; context_sha256: string;
}
export interface SectionProposalApplyRequest { revision_id: string; content_sha256: string; reason: string }
export interface BaseResumeRequest { profile_revision_id: string; template_version: string }
export interface GenerationRequest extends BaseResumeRequest {
  requirements_revision_id: string; tone: 'professional' | 'concise'; language: 'en'; context_sha256: string;
}
export interface DocumentSaveRequest { base_revision_id: string; body: DocumentBody; reason: string }
export interface DocumentRestoreRequest { revision_id: string; reason: string }
export interface DocumentReviewRequest { revision_id: string; decision: 'accepted'; content_sha256: string; reviewed_block_keys: string[] }
export interface DocumentRenderRequest { revision_id: string; content_sha256: string; format: 'pdf' }
export const MAX_IMPORT_BYTES = 10 * 1024 * 1024;
export type ImportMediaType = 'application/pdf' | 'text/plain';
export interface ImportSource { source_id: string; start: number; end: number; quote: string }
export interface ImportComparison { status: 'new' | 'unchanged' | 'conflict' | 'needs_attention'; target_claim_id: string | null; candidate_claim_ids: string[] }
export interface UnparsedPart { text: string; reason: 'unknown_verb' | 'missing_subject' | 'additional_metric' | 'multiple_statements' | 'invalid_value' | 'unplaced_scope' | 'compound_verb' }
export interface ClaimLineProposal { source_id: string; quote: string; claim: StructuredClaim | null; rendering: string | null; unparsed: UnparsedPart[] }
export interface ImportEntry { entry_id: string; kind: ClaimKind | null; identity_field: 'display_name' | 'headline' | null; payload: ClaimPayload; sources: ImportSource[]; issues: string[]; claim_proposals?: ClaimLineProposal[]; comparison: ImportComparison }
export interface ImportBatch { id: string; workflow_id: string; state: WorkflowState; version: number; media_type: ImportMediaType; byte_size: number; created_at: string; source_sha256: string | null; text: string | null; entries: ImportEntry[]; warnings: string[]; profile_version: number; comparison_profile_version: number; error_code: string | null }
export interface ImportDecision { source_ids: string[]; action: 'skip' | 'add' | 'replace' | 'unchanged'; claim_id?: string; kind: ClaimKind | null; identity_field: ImportEntry['identity_field']; payload: ClaimPayload }
export interface ImportMergeRequest { mode: 'merge' | 'replace_all'; replace_all_confirmed: boolean; decisions: ImportDecision[] }
export interface ImportAccepted { import_id: string; workflow_id: string }
export type ImportSummary = Pick<ImportBatch, 'id' | 'media_type' | 'created_at' | 'state'>;
export interface ImportPage { items: ImportSummary[] }
export interface Source {
  id: string; version: number; source_kind: 'manual' | 'greenhouse'; board_token: string | null; company: string; enabled: boolean;
  terms_url: string | null; terms_revision: string | null; terms_reviewed_at: string | null; policy_version: number; allowed_host: string | null;
  request_limit_per_minute: number; last_success_at: string | null; last_error_code: string | null; next_allowed_at: string | null;
  rate_limit_blocked: boolean; retry_after_seconds: string | null; removed_at: string | null;
  health: 'disabled' | 'healthy' | 'rate_limited' | 'degraded' | 'unknown'; quota: { known: false; limit: null }; created_at: string;
}
export interface SourceWrite {
  source_kind: 'greenhouse'; board_token: string; company: string; terms_url: string | null; terms_revision: string | null;
  terms_review_acknowledged: boolean; enabled: boolean; request_limit_per_minute: number;
}
export interface SourcePage { items: Source[] }
export type PostingDisposition = 'saved' | 'dismissed' | 'archived' | 'unmarked';
export interface PostingFilters {
  q: string; location: string; source_id: string; status: string; disposition: string; observed_after: string;
}
export interface PostingSummary {
  id: string; version: number; title: string; company: string; location_text: string; status: 'open' | 'closed' | 'unknown'; created_at: string; updated_at: string;
  disposition: Exclude<PostingDisposition, 'unmarked'> | null; source_config_id: string | null; source_kind: 'manual' | 'greenhouse' | null;
  external_id: string | null; last_observed_at: string | null;
}
export interface PostingWrite { title: string; company: string; location_text: string; description_text: string; canonical_url?: string | null }
export interface PostingDetail extends PostingSummary {
  description_text: string; canonical_url: string | null; content_sha256: string;
}
export interface PostingPage { items: PostingSummary[]; next_cursor: string | null; query_elapsed_ms: number; as_of: string }
export type DuplicateDecision = 'suspected' | 'distinct';
export interface DuplicateCandidate extends PostingSummary { decision: DuplicateDecision }
export interface DuplicatePage { items: DuplicateCandidate[]; next_cursor: string | null }
export interface ObservationSummary {
  id: string; source_config_id: string; source_kind: 'manual' | 'greenhouse'; board_token: string | null;
  external_id: string; content_sha256: string; fetched_at: string; created_at: string; policy_version: number;
}
export interface ObservationDetail extends ObservationSummary { source_url: string; snapshot: Record<string, unknown> }
export interface ObservationPage { items: ObservationSummary[]; next_cursor: string | null }
export interface SourceRefreshAccepted { workflow_id: string; source_id: string }
export interface HardConstraint {
  kind: 'work_authorization' | 'location' | 'salary' | 'other'; operator: 'manual' | 'equals' | 'contains' | 'minimum' | 'maximum';
  required_value: string | null; owner_value: string | null; jurisdiction: string | null; owner_jurisdiction: string | null;
  currency: string | null; owner_currency: string | null; period: string | null; owner_period: string | null;
  owner_confirmed: boolean; contradictory: boolean;
}
export interface Requirement {
  requirement_key: string; text: string; importance: 'required' | 'preferred' | 'unclear';
  span: { start: number; end: number; quote: string }; hard_constraint: HardConstraint | null;
}
export interface RequirementRevision {
  id: string; revision_no: number; source_sha256: string; requirements: Requirement[]; confirmed: boolean;
  created_at: string; provenance: Record<string, unknown>; stale: boolean;
}
export interface Coverage {
  requirement_key: string; coverage: 'supported' | 'partial' | 'not_evidenced' | 'conflicting';
  claim_revision_ids: string[]; evidence_revision_ids: string[]; explanation: string;
}
export interface Assessment {
  id: string; profile_revision_id: string; requirements_revision_id: string; created_at: string; stale: boolean; stale_reasons: string[];
  hard_eligibility: 'met' | 'not_met' | 'unknown';
  hard_constraints: { requirement_key: string | null; state: 'met' | 'not_met' | 'unknown'; reason: string }[];
  assessments: Coverage[]; evidence_coverage: { supported: number; partial: number; not_evidenced: number; conflicting: number; total: number };
  provenance: Record<string, unknown>;
}
export interface RequirementsView {
  posting_id: string; version: number; source_text: string; source_sha256: string; current_revision: RequirementRevision | null;
  model_route: { status: 'disabled' | 'unqualified' | 'qualified'; reason: string; identity: Record<string, unknown> | null };
  latest_assessment: Assessment | null;
}
export interface SuitabilityAccepted { workflow_id: string; posting_id: string }
export class ApiError extends Error {
  constructor(readonly status: number, readonly code: string, readonly fields: FieldError[] = [], readonly retryAfterSeconds?: number) { super(code); }
}
export class Api {
  csrf = '';
  writeBlock: string | null = null;
  onUnavailable: ((failure: unknown) => void) | null = null;
  async request<T>(path: string, method = 'GET', body?: unknown, version?: number, idempotent: boolean | string = false, signal?: AbortSignal): Promise<T> {
    const headers: Record<string, string> = { Accept: 'application/json' };
    if (method !== 'GET') headers['X-CSRF-Token'] = this.csrf;
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (version !== undefined) headers['If-Match'] = `"${version}"`;
    if (idempotent) headers['Idempotency-Key'] = typeof idempotent === 'string' ? idempotent : crypto.randomUUID();
    return this.send<T>(path, { method, headers, ...(signal ? { signal } : {}), ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  }
  private async send<T>(path: string, options: RequestInit): Promise<T> {
    const method = options.method ?? 'GET';
    const authentication = method === 'POST' && ['/session', '/bootstrap', '/session/reauthenticate'].includes(path);
    if (method !== 'GET' && this.writeBlock && !authentication) throw new ApiError(409, this.writeBlock);
    let response: Response;
    try { response = await fetch(`/api/v1${path}`, { ...options, credentials: 'same-origin', cache: 'no-store' }); }
    catch (failure) {
      if (!(failure instanceof DOMException && failure.name === 'AbortError')) this.onUnavailable?.(failure);
      throw failure;
    }
    if (!response.ok) {
      let code = `http_${response.status}`;
      let fields: FieldError[] = [];
      try { const problem = await response.json() as { code?: string; fields?: FieldError[] }; code = problem.code ?? code; fields = problem.fields ?? []; } catch { /* A non-JSON upstream error is still a failure. */ }
      const retryAfter = response.headers.get('Retry-After');
      const parsedRetryAfter = retryAfter && /^\d+$/.test(retryAfter) ? Number(retryAfter) : NaN;
      const retryAfterSeconds = Number.isFinite(parsedRetryAfter) ? parsedRetryAfter : undefined;
      const failure = new ApiError(response.status, code, fields, retryAfterSeconds);
      if (response.status === 401 || response.status === 503) this.onUnavailable?.(failure);
      throw failure;
    }
    if (response.status === 204) return undefined as T;
    try { return await response.json() as T; }
    catch (failure) {
      if (!(failure instanceof DOMException && failure.name === 'AbortError')) this.onUnavailable?.(failure);
      throw failure;
    }
  }
  async upload<T>(path: string, body: Blob, mediaType: string, version: number, key: string, signal?: AbortSignal, extraHeaders: Record<string, string> = {}): Promise<T> {
    return this.send<T>(path, {
      method: 'POST', body, ...(signal ? { signal } : {}),
      headers: { Accept: 'application/json', 'Content-Type': mediaType, 'X-CSRF-Token': this.csrf,
        'If-Match': `"${version}"`, 'Idempotency-Key': key, ...extraHeaders },
    });
  }
  async uploadImport(body: Blob, mediaType: ImportMediaType, profileVersion: number, idempotencyKey: string): Promise<ImportAccepted> {
    return this.upload<ImportAccepted>('/imports', body, mediaType, profileVersion, idempotencyKey);
  }
  async uploadPortable(body: Blob, workspaceVersion: number, idempotencyKey: string, sourceOwner?: string, signal?: AbortSignal): Promise<PortablePreview> {
    return this.upload<PortablePreview>('/portable-previews/import', body, 'application/zip', workspaceVersion, idempotencyKey, signal, sourceOwner ? { 'X-Source-Owner': sourceOwner } : {});
  }
}
export const terminal = (state: WorkflowState) => ['succeeded', 'cancelled', 'permanent_failure'].includes(state);
export function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === 'workspace_reconnect_required') return 'Writes are paused. Reconnect and review the current workspace before saving. Your editor text remains here.';
    if (error.status === 412) return 'A newer revision exists. Your text remains here. Load the current revision for comparison before saving again.';
    if (error.status === 401) return 'Your session is unavailable or expired. Sign in again. Unsaved text remains here for this visit.';
    if (error.status === 422 && error.fields.length) return error.fields.map(field => `${field.loc.filter(part => part !== 'body').join('.') || 'Request'}: ${field.message}`).join(' ');
    return `The operation was not completed (${error.code}). Your existing work has not been replaced.`;
  }
  return 'The server could not be reached. Your text remains here. Retry or copy it before leaving.';
}
