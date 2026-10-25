// SPDX-License-Identifier: MIT OR Apache-2.0
export interface Session { owner_id: string; login_name: string; csrf_token: string; expires_at: string }
export interface Challenge { csrf_token: string; bootstrap_available: boolean }
export interface Snapshot { display_name: string; headline: string; claim_revision_ids: string[]; evidence_revision_ids?: string[] }
export type ClaimKind = 'identity' | 'experience' | 'education' | 'skill' | 'project' | 'certification' | 'preference' | 'other' | 'language';
export interface ClaimPayload { text: string; organization?: string; role?: string; start_date?: string; end_date?: string; proficiency?: string }
export interface RevisionMetadata { id: string; revision_no: number; content_sha256: string; created_at: string; author_owner_id: string }
export interface EvidenceRevision extends RevisionMetadata { payload: { text: string; source: string } }
export interface EvidenceLink { evidence_revision_id: string; support_kind: 'direct' | 'user_asserted' | 'context'; excerpt: string }
export interface ClaimRevision extends RevisionMetadata { payload: ClaimPayload; evidence_links: (EvidenceLink & { evidence: EvidenceRevision })[] }
export interface PinnedClaim { id: string; kind: ClaimKind; revision: ClaimRevision }
export interface ClaimRecord extends PinnedClaim { version: number; archived: boolean; current_revision_id: string }
export interface PinnedEvidence { id: string; revision: EvidenceRevision }
export interface EvidenceRecord extends PinnedEvidence { version: number; archived: boolean; current_revision_id: string }
export interface ProfileRevisionSummary extends RevisionMetadata { snapshot: Snapshot; reason: string }
export interface ProfileRevision extends ProfileRevisionSummary { claims: PinnedClaim[]; evidence: PinnedEvidence[] }
export interface Profile { id: string; version: number; current_revision: ProfileRevisionSummary & { claims: ClaimRecord[] }; evidence: EvidenceRecord[]; quality_warnings: string[] }
export interface RevisionPage { items: ProfileRevisionSummary[]; next_cursor: number | null }
export interface FieldError { loc: (string | number)[]; code: string; message: string }
export type WorkflowState = 'pending' | 'running' | 'waiting_for_approval' | 'retryable_failure' | 'permanent_failure' | 'cancelled' | 'succeeded';
export interface Workflow { id: string; version: number; state: WorkflowState; attempt: number; kind?: string; max_attempts?: number; next_attempt_at?: string | null; last_error_code: string | null; result: { document_id?: string; artifact_id?: string; source_id?: string; policy_version?: number; fetch_elapsed_ms?: number; posting_count?: number; new_posting_count?: number; changed_posting_count?: number; unchanged_posting_count?: number; excluded_prospect_count?: number; fetched_at?: string; freshness?: 'current'; error_code?: string } | null }
export interface DocumentRecord { id: string; version: number; current_revision: { id: string; body: { title: string; sections: { key: string; heading: string; blocks: { block_key: string; text: string; claim_kind: string; claim_revision_ids: string[]; evidence_revision_ids: string[] }[] }[] }; content_sha256: string }; reviewed: boolean; unsupported_claims: string[]; stale: boolean; stale_reason: 'upstream_profile_revision_changed' | null }
export const MAX_IMPORT_BYTES = 10 * 1024 * 1024;
export type ImportMediaType = 'application/pdf' | 'text/plain';
export interface ImportProposal { proposal_id: string; kind: ClaimKind; payload: ClaimPayload; source_start: number; source_end: number; quote: string }
export interface ImportBatch { id: string; workflow_id: string; state: WorkflowState; version: number; media_type: ImportMediaType; byte_size: number; created_at: string; source_sha256: string | null; text: string | null; proposals: ImportProposal[]; warnings: string[]; profile_version: number; error_code: string | null }
export interface ImportDecision { proposal_id: string; action: 'skip' | 'add' | 'replace'; claim_id?: string; kind: ClaimKind; payload: ClaimPayload }
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
export class ApiError extends Error {
  constructor(readonly status: number, readonly code: string, readonly fields: FieldError[] = [], readonly retryAfterSeconds?: number) { super(code); }
}
export class Api {
  csrf = '';
  async request<T>(path: string, method = 'GET', body?: unknown, version?: number, idempotent: boolean | string = false, signal?: AbortSignal): Promise<T> {
    const headers: Record<string, string> = { Accept: 'application/json' };
    if (method !== 'GET') headers['X-CSRF-Token'] = this.csrf;
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (version !== undefined) headers['If-Match'] = `"${version}"`;
    if (idempotent) headers['Idempotency-Key'] = typeof idempotent === 'string' ? idempotent : crypto.randomUUID();
    const response = await fetch(`/api/v1${path}`, { method, headers, credentials: 'same-origin', ...(signal ? { signal } : {}), ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
    if (!response.ok) {
      let code = `http_${response.status}`;
      let fields: FieldError[] = [];
      try { const problem = await response.json() as { code?: string; fields?: FieldError[] }; code = problem.code ?? code; fields = problem.fields ?? []; } catch { /* A non-JSON upstream error is still a failure. */ }
      const retryAfter = response.headers.get('Retry-After');
      const parsedRetryAfter = retryAfter && /^\d+$/.test(retryAfter) ? Number(retryAfter) : NaN;
      const retryAfterSeconds = Number.isFinite(parsedRetryAfter) ? parsedRetryAfter : undefined;
      throw new ApiError(response.status, code, fields, retryAfterSeconds);
    }
    if (response.status === 204) return undefined as T;
    return await response.json() as T;
  }
  async uploadImport(body: Blob, mediaType: ImportMediaType, profileVersion: number, idempotencyKey: string): Promise<ImportAccepted> {
    const response = await fetch('/api/v1/imports', {
      method: 'POST', credentials: 'same-origin', body,
      headers: { Accept: 'application/json', 'Content-Type': mediaType, 'X-CSRF-Token': this.csrf,
        'If-Match': `"${profileVersion}"`, 'Idempotency-Key': idempotencyKey },
    });
    if (!response.ok) {
      let code = `http_${response.status}`;
      let fields: FieldError[] = [];
      try { const problem = await response.json() as { code?: string; fields?: FieldError[] }; code = problem.code ?? code; fields = problem.fields ?? []; } catch { /* An upstream error never becomes an accepted import. */ }
      throw new ApiError(response.status, code, fields);
    }
    return await response.json() as ImportAccepted;
  }
}
export const terminal = (state: WorkflowState) => ['succeeded', 'cancelled', 'permanent_failure'].includes(state);
export function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 412) return 'A newer revision exists. Your text remains here. Load the current revision for comparison before saving again.';
    if (error.status === 401) return 'Your session is unavailable or expired. Sign in again. Unsaved text remains here for this visit.';
    if (error.status === 422 && error.fields.length) return error.fields.map(field => `${field.loc.filter(part => part !== 'body').join('.') || 'Request'}: ${field.message}`).join(' ');
    return `The operation was not completed (${error.code}). Your existing work has not been replaced.`;
  }
  return 'The server could not be reached. Your text remains here. Retry or copy it before leaving.';
}
