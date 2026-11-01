// SPDX-License-Identifier: MIT OR Apache-2.0
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Api, ApiError, type Assessment, type Coverage, type Profile, type Requirement, type RequirementsView, type Workflow } from './api';
import { JobSources } from './JobSources';
import { scalarSelection, Suitability, validSpan } from './Suitability';

const metadata = { id: 'evidence-one', revision_no: 1, content_sha256: 'e'.repeat(64), created_at: '2026-01-01T12:00:00Z', author_owner_id: 'owner-one' };
const evidence = { ...metadata, payload: { text: 'Maintained SQL queries on Linux.', source: 'Owner attestation' }, provenance: { source_type: 'owner_statement' as const, origin: 'owner_entry' as const, privacy_class: 'private' as const, employer_owned: false } };
const requirement: Requirement = { requirement_key: 'req-1', text: 'SQL', importance: 'required', span: { start: 2, end: 5, quote: 'SQL' }, hard_constraint: null };
const source = '😀 SQL and Kubernetes.\n<script>untrusted()</script>';
function initialProfile(): Profile {
  return { id: 'profile-one', version: 1, current_revision: { ...metadata, id: 'profile-rev-one', reason: 'updated', snapshot: { display_name: 'Synthetic owner', headline: '', claim_revision_ids: ['claim-one'], evidence_revision_ids: ['evidence-one'] }, claims: [
    { id: 'claim-record', kind: 'skill', version: 1, archived: false, current_revision_id: 'claim-one', rendering: 'Maintained SQL queries on Linux.', needs_attention: false, revision: { ...metadata, id: 'claim-one', payload: { text: 'Maintained SQL queries on Linux.' }, evidence_links: [{ evidence_revision_id: 'evidence-one', support_kind: 'user_asserted', excerpt: 'SQL queries on Linux', evidence }] } },
    { id: 'unselected-record', kind: 'skill', version: 1, archived: false, current_revision_id: 'unselected', rendering: 'Unselected Kubernetes claim', needs_attention: false, revision: { ...metadata, id: 'unselected', payload: { text: 'Unselected Kubernetes claim' }, evidence_links: [] } },
  ] }, evidence: [{ id: 'evidence-record', version: 1, archived: false, current_revision_id: evidence.id, revision: evidence }], quality_warnings: [], experience_coverage: { minimum_days: 0, maximum_days: 0, undated_or_open_entries: 0 } };
}
function initialView(): RequirementsView {
  return { posting_id: 'posting-one', version: 1, source_text: source, source_sha256: 'a'.repeat(64), model_route: { status: 'disabled', reason: 'No authorized local model route', identity: null }, latest_assessment: null,
    current_revision: { id: 'requirements-one', revision_no: 1, source_sha256: 'a'.repeat(64), requirements: [{ ...requirement, span: { ...requirement.span } }], confirmed: true, created_at: metadata.created_at, provenance: { origin: 'manual' }, stale: false } };
}
function assessmentSnapshot(): Assessment {
  return { id: 'assessment-current', profile_revision_id: 'profile-rev-one', requirements_revision_id: 'requirements-one', created_at: metadata.created_at, stale: false, stale_reasons: [], hard_eligibility: 'unknown', hard_constraints: [{ requirement_key: null, state: 'unknown', reason: 'no_reviewed_constraints' }], assessments: [], evidence_coverage: { supported: 0, partial: 0, not_evidenced: 1, conflicting: 0, total: 1 }, provenance: { origin: 'owner_reviewed' } };
}
interface Submission { path: string; body: unknown; version: number | undefined; key: boolean | string }
function server() {
  const api = new Api();
  const state = { view: initialView(), profile: initialProfile(), failure: null as Error | null, readFailure: null as Error | null, reconcileFailure: false, submissions: [] as Submission[],
    workflowReads: 0, workflowSignal: null as AbortSignal | null, workflowHangs: false, reviewReads: 0, reviewReadWait: null as Promise<void> | null, postingContentSha256: 'c'.repeat(64),
    workflow: {
      id: 'task-one', version: 1, state: 'succeeded', attempt: 1, kind: 'posting_extract', max_attempts: 2, last_error_code: null, result: { posting_id: 'posting-one', source_sha256: 'a'.repeat(64), requirements: [requirement] },
      created_at: '2026-10-25T12:00:00Z', updated_at: '2026-10-25T12:00:00Z', deadline_at: '2030-01-01T00:00:00Z', next_attempt_at: null,
      cancel_requested_at: null, lease_expires_at: null, last_heartbeat_at: null, queue_age_seconds: null, remaining_attempts: 1,
      cancelable: false, retryable: false, retry_block_reason: 'state_not_retryable', delivery_state: 'not_attempted',
      cancellation: { requested: false, local_fence_settled: false, provider_stopped: null, completed_effects_preserved: true, reason: null },
      usage: { status: 'known', model_runs: 1, inference_post_attempts: 1, inference_post_limit: 2, input_tokens: 40, output_tokens: 20, token_budget: 1000, tokens_used: 60, cost_budget_microunits: 1000, cost_used_microunits: 0, currency: 'USD' },
    } as Workflow };
  vi.spyOn(api, 'request').mockImplementation(async <T,>(path: string, method = 'GET', body?: unknown, version?: number, key: boolean | string = false, signal?: AbortSignal): Promise<T> => {
    if (method !== 'GET') {
      state.submissions.push({ path, body, version, key });
      if (state.failure) { const failure = state.failure; state.failure = null; throw failure; }
      if (path.startsWith('/postings/posting-one/') && version !== state.view.version) throw new ApiError(412, 'version_conflict');
    }
    if (path === '/profile') return state.profile as T;
    if (path.startsWith('/profile/revisions/')) return { ...state.profile.current_revision, id: path.split('/').at(-1), evidence: state.profile.evidence } as T;
    if (path === '/postings/posting-one/requirements' && method === 'GET') {
      state.reviewReads += 1;
      const snapshot = structuredClone(state.view);
      if (state.reviewReadWait) await state.reviewReadWait;
      if (state.readFailure) throw state.readFailure;
      if (state.reconcileFailure && state.submissions.length) throw new Error('Offline reconciliation');
      return snapshot as T;
    }
    if (path === '/postings/posting-one/requirements' && method === 'PUT') {
      const input = body as { requirements: Requirement[] };
      state.view = { ...state.view, version: state.view.version + 1, current_revision: { ...state.view.current_revision!, id: 'requirements-two', revision_no: 2, requirements: input.requirements } };
      return state.view as T;
    }
    if (path === '/postings/posting-one/coverage' && method === 'PUT') {
      const input = body as { profile_revision_id: string; requirements_revision_id: string; assessments: Coverage[] };
      const assessment: Assessment = { id: 'assessment-one', profile_revision_id: input.profile_revision_id, requirements_revision_id: input.requirements_revision_id, created_at: metadata.created_at, stale: false, stale_reasons: [], hard_eligibility: 'unknown', hard_constraints: [{ requirement_key: null, state: 'unknown', reason: 'no_reviewed_constraints' }], assessments: input.assessments,
        evidence_coverage: { supported: input.assessments.filter(item => item.coverage === 'supported').length, partial: 0, not_evidenced: input.assessments.filter(item => item.coverage === 'not_evidenced').length, conflicting: 0, total: input.assessments.length }, provenance: { origin: 'owner_reviewed' } };
      state.view = { ...state.view, version: state.view.version + 1, latest_assessment: assessment }; return state.view as T;
    }
    if (path === '/postings/posting-one/extract' || path === '/postings/posting-one/match') {
      state.view = { ...state.view, version: state.view.version + 1 };
      return { posting_id: 'posting-one', workflow_id: 'task-one' } as T;
    }
    if (path === '/workflows/task-one') {
      state.workflowReads += 1; state.workflowSignal = signal ?? null;
      // ES2022's configured library does not expose Promise.withResolvers.
      if (state.workflowHangs) await new Promise<void>((_resolve, reject) => { signal?.addEventListener('abort', () => reject(new DOMException('Stopped', 'AbortError')), { once: true }); });
      return state.workflow as T;
    }
    if (path === '/workflows') return { items: [state.workflow] } as T;
    if (path === '/postings/posting-one/requirements/history') return { items: [state.view.current_revision] } as T;
    if (path.startsWith('/postings') && method === 'GET') {
      const posting = { id: 'posting-one', version: state.view.version, title: 'Engineer', company: 'Synthetic North', location_text: 'Remote', status: 'open', created_at: metadata.created_at, updated_at: metadata.created_at, description_text: state.view.source_text, canonical_url: null, content_sha256: state.postingContentSha256, source_config_id: 'manual', source_kind: 'manual', external_id: 'one', last_observed_at: metadata.created_at, disposition: null };
      return (path === '/postings/posting-one' ? posting : { items: [posting, { ...posting, id: 'posting-two', title: 'Designer' }], next_cursor: null, as_of: metadata.created_at, query_elapsed_ms: 1 }) as T;
    }
    if (path === '/sources') return { items: [] } as T;
    throw new Error(`Unexpected request ${method} ${path}`);
  });
  return { api, state };
}
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); window.history.replaceState(null, '', '/'); });

describe('reviewed suitability boundaries', () => {
  it('displays evidence provenance in selected claims, selected evidence, citation choices and pinned history', async () => {
    const { api, state } = server();
    state.view.latest_assessment = assessmentSnapshot();
    render(<Suitability api={api} postingId="posting-one" />);
    const inspector = await screen.findByRole('region', { name: 'Profile citation inspector' });
    await screen.findByText('Evidence: Owner attestation');
    fireEvent.click(within(inspector).getByText('skill: Maintained SQL queries on Linux.', { selector: 'summary' }));
    fireEvent.click(within(inspector).getByText('Evidence: Owner attestation', { selector: 'summary' }));
    const provenance = 'Source type: Owner statement · Origin: Entered by owner · Privacy: Private';
    expect(within(inspector).getAllByText(provenance)).toHaveLength(2);
    within(inspector).getAllByText(provenance).forEach(summary => expect(summary).toBeVisible());

    fireEvent.click(screen.getByRole('button', { name: 'Review evidence coverage manually' }));
    const citations = screen.getByRole('group', { name: 'Evidence revision citations' });
    expect(within(citations).getByText(provenance)).toBeVisible();
    expect(within(citations).getByRole('checkbox', { name: /Owner attestation: Maintained SQL queries on Linux/ })).toBeEnabled();

    fireEvent.click(screen.getByRole('button', { name: "Inspect assessment's pinned profile evidence" }));
    const historical = await screen.findByRole('region', { name: 'Assessment pinned profile evidence' });
    fireEvent.click(within(historical).getByText('Owner attestation', { selector: 'summary' }));
    expect(within(historical).getByText(provenance)).toBeVisible();
  });

  it.each(['older', 'newer'] as const)('preserves the source of a review buffer when a delayed %s initial read arrives', async direction => {
    const { api, state } = server();
    let release!: () => void;
    state.reviewReadWait = new Promise<void>(resolve => { release = resolve; });
    state.view = { ...initialView(), version: direction === 'older' ? 1 : 3 };
    render(<Suitability api={api} postingId="posting-one" />);
    await waitFor(() => expect(state.reviewReads).toBe(1));
    state.reviewReadWait = null;
    state.view = { ...initialView(), version: 2, source_text: 'Rust required.', source_sha256: 'b'.repeat(64),
      current_revision: { ...initialView().current_revision!, id: 'requirements-two', source_sha256: 'b'.repeat(64), requirements: [{ ...requirement, text: 'Rust', span: { start: 0, end: 4, quote: 'Rust' } }] } };
    fireEvent.click(screen.getByRole('button', { name: 'Reload review state' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Review requirements manually' }));
    await screen.findByLabelText('Requirement wording');
    fireEvent.change(screen.getByLabelText('Requirement wording'), { target: { value: 'Rust owner review buffer' } });
    await act(async () => { release(); });
    expect(screen.getByLabelText('Selectable normalized source')).toHaveValue('Rust required.');
    expect(screen.getByLabelText('Requirement wording')).toHaveValue('Rust owner review buffer');
    if (direction === 'older') {
      fireEvent.click(screen.getByRole('button', { name: 'Reload review state' }));
      const retain = await screen.findByRole('button', { name: 'Use compared version and retain review buffer' });
      expect(retain).toBeEnabled();
      fireEvent.click(retain);
      expect(screen.getByLabelText('Requirement wording')).toHaveValue('Rust owner review buffer');
    } else {
      expect(screen.getByRole('button', { name: 'Use compared version and retain review buffer' })).toBeDisabled();
      fireEvent.click(screen.getByRole('button', { name: 'Discard local buffer and load compared review' }));
      expect(screen.queryByLabelText('Requirement wording')).not.toBeInTheDocument();
      expect(screen.getByText(/Source SHA-256/)).toHaveTextContent('posting version 3');
    }
    expect(state.submissions).toEqual([]);
  });

  it('converts UTF-16 selections to Unicode scalar spans and rejects split or mismatched quotes', () => {
    expect(scalarSelection(source, 3, 6)).toEqual({ start: 2, end: 5, quote: 'SQL' });
    expect(validSpan(source, scalarSelection(source, 3, 6))).toBe(true);
    expect(validSpan(source, { start: 3, end: 6, quote: 'SQL' })).toBe(false);
    expect(validSpan(source, { start: 0, end: 1, quote: source.slice(0, 1) })).toBe(false);
    expect(validSpan(source, { start: 2, end: 500, quote: 'SQL' })).toBe(false);
  });

  it('saves selected source with scalar indices, explicit confirmation, immutable receipt and no model', async () => {
    const { api, state } = server(); state.view.current_revision = null;
    render(<Suitability api={api} postingId="posting-one" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Review requirements manually' }));
    const sourceField = screen.getByLabelText('Selectable normalized source') as HTMLTextAreaElement;
    sourceField.setSelectionRange(3, 6);
    fireEvent.click(screen.getByRole('button', { name: 'Add selected source requirement' }));
    expect(screen.getByLabelText('Source start (Unicode scalars)')).toHaveValue(2);
    expect(screen.getByLabelText('Source end (exclusive Unicode scalars)')).toHaveValue(5);
    expect(screen.getByRole('button', { name: 'Save reviewed requirements' })).toBeDisabled();
    fireEvent.click(screen.getByLabelText(/I reviewed all exact source spans/));
    fireEvent.click(screen.getByRole('button', { name: 'Save reviewed requirements' }));
    await screen.findByText(/Saved reviewed requirement revision 2/);
    expect(state.submissions).toHaveLength(1);
    expect(state.submissions[0]).toMatchObject({ path: '/postings/posting-one/requirements', version: 1, body: { confirmed: true, requirements: [{ requirement_key: 'req-1', text: 'SQL', span: { start: 2, end: 5, quote: 'SQL' }, hard_constraint: null }] } });
    expect(typeof state.submissions[0]!.key).toBe('string');
    expect(screen.getByRole('button', { name: 'Request extraction proposal' })).toBeDisabled();
    expect(document.querySelector('.suitability script')).toBeNull();
  });

  it('requires explicit hard constraint owner confirmation and clears it on fact changes', async () => {
    const { api, state } = server(); render(<Suitability api={api} postingId="posting-one" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Review requirements manually' }));
    fireEvent.click(screen.getByLabelText('This is an explicit hard constraint'));
    fireEvent.change(screen.getByLabelText('Constraint kind'), { target: { value: 'work_authorization' } });
    fireEvent.change(screen.getByLabelText('Comparison operator'), { target: { value: 'equals' } });
    fireEvent.change(screen.getByLabelText('Required source value'), { target: { value: 'authorized' } });
    fireEvent.change(screen.getByLabelText('Your explicit value'), { target: { value: 'authorized' } });
    fireEvent.change(screen.getByLabelText('Source jurisdiction'), { target: { value: 'Canada' } });
    fireEvent.change(screen.getByLabelText('Your assertion jurisdiction'), { target: { value: 'Canada' } });
    const owner = screen.getByLabelText(/I confirm these exact source criteria/);
    fireEvent.click(owner); expect(owner).toBeChecked();
    fireEvent.change(screen.getByLabelText('Your assertion jurisdiction'), { target: { value: 'Unknown' } });
    expect(owner).not.toBeChecked();
    fireEvent.click(screen.getByLabelText(/I reviewed all exact source spans/));
    fireEvent.click(screen.getByRole('button', { name: 'Save reviewed requirements' }));
    await waitFor(() => expect(state.submissions).toHaveLength(1));
    expect(state.submissions[0]!.body).toMatchObject({ requirements: [{ hard_constraint: { owner_confirmed: false, jurisdiction: 'Canada', owner_jurisdiction: 'Unknown' } }] });
  });

  it('renders one coverage row per requirement and requires real selected connected citations for positive support', async () => {
    const { api, state } = server(); state.view.current_revision!.requirements.push({ ...requirement, requirement_key: 'req-2', text: 'Kubernetes', span: { start: 10, end: 20, quote: 'Kubernetes' } });
    render(<Suitability api={api} postingId="posting-one" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Review evidence coverage manually' }));
    const rows = screen.getAllByLabelText('Evidence coverage'); expect(rows).toHaveLength(2);
    expect(screen.queryByLabelText(/Unselected Kubernetes claim/)).not.toBeInTheDocument();
    const explanations = screen.getAllByLabelText('Explanation of evidence and gaps');
    fireEvent.change(explanations[0]!, { target: { value: 'SQL queries on Linux are explicitly asserted.' } });
    fireEvent.change(explanations[1]!, { target: { value: 'No Kubernetes evidence is selected.' } });
    fireEvent.change(rows[0]!, { target: { value: 'supported' } });
    fireEvent.click(screen.getByLabelText(/I reviewed every coverage row/));
    expect(screen.getByRole('button', { name: 'Save owner-reviewed assessment' })).toBeDisabled();
    const buffer = screen.getByRole('group', { name: 'Owner-reviewed evidence coverage buffer' });
    const first = within(buffer).getByRole('group', { name: 'req-1: SQL' });
    fireEvent.click(within(first).getByLabelText(/Maintained SQL queries on Linux.*claim-one/));
    fireEvent.click(within(first).getByLabelText(/Owner attestation: Maintained SQL queries on Linux.*evidence-one/));
    fireEvent.click(screen.getByLabelText(/I reviewed every coverage row/));
    fireEvent.click(screen.getByRole('button', { name: 'Save owner-reviewed assessment' }));
    await screen.findByText(/Saved immutable owner-reviewed assessment/);
    expect(state.submissions[0]!.body).toMatchObject({ profile_revision_id: 'profile-rev-one', requirements_revision_id: 'requirements-one', assessments: [
      { requirement_key: 'req-1', coverage: 'supported', claim_revision_ids: ['claim-one'], evidence_revision_ids: ['evidence-one'] },
      { requirement_key: 'req-2', coverage: 'not_evidenced', claim_revision_ids: [], evidence_revision_ids: [] },
    ] });
    expect(screen.getByText(/Evidence coverage, not hiring probability: 1 supported/)).toBeVisible();
    expect(screen.getByText('No reviewed hard constraints: unknown — no_reviewed_constraints')).toBeVisible();
    expect(state.profile.current_revision.snapshot.claim_revision_ids).toEqual(['claim-one']);
  });

  it('retains local text and old If-Match on 412 until explicit comparison and rebasing', async () => {
    const { api, state } = server(); state.failure = new ApiError(412, 'version_conflict');
    render(<Suitability api={api} postingId="posting-one" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Review requirements manually' }));
    fireEvent.change(screen.getByLabelText('Requirement wording'), { target: { value: 'My reviewed SQL requirement' } });
    fireEvent.click(screen.getByLabelText(/I reviewed all exact source spans/));
    fireEvent.click(screen.getByRole('button', { name: 'Save reviewed requirements' }));
    await screen.findByRole('region', { name: 'Review conflict' });
    expect(screen.getByLabelText('Requirement wording')).toHaveValue('My reviewed SQL requirement');
    expect(screen.getByRole('button', { name: 'Save reviewed requirements' })).toBeDisabled();
    state.view = { ...state.view, version: 9 };
    fireEvent.click(screen.getByRole('button', { name: 'Load current review for comparison' }));
    await screen.findByText(/Current posting version 9/);
    expect(state.submissions).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: 'Use compared version and retain review buffer' }));
    expect(screen.getByLabelText('Requirement wording')).toHaveValue('My reviewed SQL requirement');
    expect(screen.getByRole('button', { name: 'Save reviewed requirements' })).toBeDisabled();
    fireEvent.click(screen.getByLabelText(/I reviewed all exact source spans/));
    fireEvent.click(screen.getByRole('button', { name: 'Save reviewed requirements' }));
    await waitFor(() => expect(state.submissions).toHaveLength(2));
    expect(state.submissions.map(item => item.version)).toEqual([1, 9]);
    expect(state.submissions[1]!.key).not.toBe(state.submissions[0]!.key);
  });

  it('retains mutation receipt and warns separately when reconciliation is unavailable', async () => {
    const { api, state } = server(); state.reconcileFailure = true;
    render(<Suitability api={api} postingId="posting-one" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Review requirements manually' }));
    fireEvent.click(screen.getByLabelText(/I reviewed all exact source spans/));
    fireEvent.click(screen.getByRole('button', { name: 'Save reviewed requirements' }));
    expect(await screen.findByText(/Save receipt retained.*may now be stale/)).toBeVisible();
    expect(screen.getByText(/Saved reviewed requirement revision 2/)).toBeVisible();
    expect(screen.getByRole('heading', { name: 'Reviewed requirement revision 2' })).toBeVisible();
    expect(state.submissions).toHaveLength(1);
  });

  it('keeps extraction output untrusted until explicit adoption and confirmation', async () => {
    const { api, state } = server(); state.view.model_route.status = 'qualified';
    render(<Suitability api={api} postingId="posting-one" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Request extraction proposal' }));
    expect(await screen.findByText('Untrusted proposal — not owner-confirmed.')).toBeVisible();
    expect(screen.queryByLabelText('Requirement wording')).not.toBeInTheDocument();
    expect(state.submissions).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: 'Load extraction proposal into review buffer' }));
    expect(screen.getByLabelText('Requirement wording')).toHaveValue('SQL');
    expect(screen.getByRole('button', { name: 'Save reviewed requirements' })).toBeDisabled();
    expect(state.submissions).toHaveLength(1);
  });

  it('shows model outage as unavailable without replacing manual review or generating a score', async () => {
    const { api, state } = server(); state.view.model_route.status = 'qualified'; state.failure = new ApiError(503, 'model_route_unavailable');
    render(<Suitability api={api} postingId="posting-one" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Request extraction proposal' }));
    expect(await screen.findByText(/Model route unavailable.*Manual requirements and coverage remain usable/)).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Review requirements manually' }));
    expect(screen.getByLabelText('Requirement wording')).toHaveValue('SQL');
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
    expect(state.view.latest_assessment).toBeNull();
  });

  it('aborts pending workflow reads on unmount and never treats them as finished results', async () => {
    const { api, state } = server(); state.view.model_route.status = 'qualified'; state.workflowHangs = true;
    const { unmount } = render(<Suitability api={api} postingId="posting-one" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Request extraction proposal' }));
    await waitFor(() => expect(state.workflowReads).toBe(1));
    unmount();
    expect(state.workflowSignal?.aborted).toBe(true);
    expect(state.view.latest_assessment).toBeNull();
  });

  it('preserves the dirty selected-posting buffer across unrelated catalog updates and blocks selection', async () => {
    const { api } = server();
    render(<JobSources api={api} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Read Engineer at Synthetic North' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Review requirements manually' }));
    fireEvent.change(screen.getByLabelText('Requirement wording'), { target: { value: 'Retain this exact local buffer' } });
    expect(screen.getByRole('button', { name: 'Read Designer at Synthetic North' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Reload posting catalog' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Reload posting catalog' })).toBeEnabled());
    expect(screen.getByLabelText('Requirement wording')).toHaveValue('Retain this exact local buffer');
    act(() => { window.history.pushState(null, '', '/?posting_id=posting-two'); window.dispatchEvent(new PopStateEvent('popstate')); });
    expect(screen.getByRole('button', { name: 'Load current URL context' })).toBeDisabled();
    expect(screen.getByLabelText('Requirement wording')).toHaveValue('Retain this exact local buffer');
    fireEvent.click(screen.getByRole('button', { name: 'Discard review buffer' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Read Designer at Synthetic North' })).toBeEnabled());
  });

  it('keeps stale eligibility snapshots explicitly historical and independent from evidence counts', async () => {
    const { api, state } = server();
    state.view.latest_assessment = { id: 'assessment-old', profile_revision_id: 'old-profile', requirements_revision_id: 'requirements-one', created_at: metadata.created_at, stale: true, stale_reasons: ['profile_revision_changed'], hard_eligibility: 'not_met', hard_constraints: [{ requirement_key: 'req-1', state: 'not_met', reason: 'explicit_mismatch' }], assessments: [], evidence_coverage: { supported: 1, partial: 0, not_evidenced: 0, conflicting: 0, total: 1 }, provenance: { origin: 'owner_reviewed' } };
    render(<Suitability api={api} postingId="posting-one" />);
    expect(await screen.findByText('Stale historical snapshot — not a current eligibility assertion')).toBeVisible();
    expect(screen.getByText(/Evidence coverage, not hiring probability: 1 supported/)).toBeVisible();
    expect(screen.getByText(/req-1: not met — explicit_mismatch/)).toBeVisible();
    expect(screen.getByText(/profile_revision_changed/)).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: "Inspect assessment's pinned profile evidence" }));
    const historical = await screen.findByRole('region', { name: 'Assessment pinned profile evidence' });
    expect(within(historical).getByText(/Revision old-profile/)).toBeVisible();
    fireEvent.click(within(historical).getByText('Owner attestation', { selector: 'summary' }));
    expect(within(historical).getByText('Maintained SQL queries on Linux.', { selector: 'p' })).toBeVisible();
    expect(screen.queryByRole('button', { name: /apply|reject/i })).not.toBeInTheDocument();
  });

  it('detects profile changes before coverage save without losing the pinned local explanation', async () => {
    const { api, state } = server();
    render(<Suitability api={api} postingId="posting-one" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Review evidence coverage manually' }));
    fireEvent.change(screen.getByLabelText('Explanation of evidence and gaps'), { target: { value: 'Retain this careful missing-evidence explanation.' } });
    fireEvent.click(screen.getByLabelText(/I reviewed every coverage row/));
    state.profile = { ...state.profile, current_revision: { ...state.profile.current_revision, id: 'profile-rev-two', revision_no: 2 } };
    fireEvent.click(screen.getByRole('button', { name: 'Save owner-reviewed assessment' }));
    expect(await screen.findByRole('region', { name: 'Current profile comparison' })).toBeVisible();
    expect(screen.getByLabelText('Explanation of evidence and gaps')).toHaveValue('Retain this careful missing-evidence explanation.');
    expect(state.submissions).toHaveLength(0);
    expect(screen.getByRole('button', { name: 'Save owner-reviewed assessment' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Discard coverage buffer and use compared profile' }));
    expect(screen.queryByLabelText('Explanation of evidence and gaps')).not.toBeInTheDocument();
    expect(screen.getByText(/Profile revision profile-rev-two/)).toBeVisible();
  });

  it('rejects adopting a proposal pinned to a different source without altering reviewed requirements', async () => {
    const { api, state } = server(); state.view.model_route.status = 'qualified';
    state.workflow = { ...state.workflow, result: { posting_id: 'posting-one', source_sha256: 'b'.repeat(64), requirements: [requirement] } };
    render(<Suitability api={api} postingId="posting-one" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Request extraction proposal' }));
    expect(await screen.findByRole('button', { name: 'Load extraction proposal into review buffer' })).toBeDisabled();
    expect(screen.getByText(/Proposal source differs/)).toBeVisible();
    expect(screen.queryByLabelText('Requirement wording')).not.toBeInTheDocument();
    expect(state.submissions).toHaveLength(1);
    expect(state.view.current_revision?.id).toBe('requirements-one');
  });

  it('retains all manual coverage rows while a stale-profile matching proposal cannot be adopted', async () => {
    const { api, state } = server(); state.view.model_route.status = 'qualified';
    state.workflow = { ...state.workflow, kind: 'matching', result: { posting_id: 'posting-one', source_sha256: 'a'.repeat(64), profile_revision_id: 'old-profile', requirements_revision_id: 'requirements-one',
      assessments: [{ requirement_key: 'req-1', coverage: 'not_evidenced', claim_revision_ids: [], evidence_revision_ids: [], explanation: 'A proposed gap.' }] } };
    render(<Suitability api={api} postingId="posting-one" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Request matching proposal' }));
    expect(await screen.findByRole('button', { name: 'Load matching proposal into review buffer' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Review evidence coverage manually' }));
    expect(screen.getAllByLabelText('Evidence coverage')).toHaveLength(1);
    expect(screen.getByLabelText('Explanation of evidence and gaps')).toHaveValue('');
    expect(state.submissions).toHaveLength(1);
  });

  it('pauses stalled task monitoring at its request deadline without a fabricated assessment', async () => {
    const { api, state } = server(); state.view.model_route.status = 'qualified'; state.workflowHangs = true;
    render(<Suitability api={api} postingId="posting-one" />);
    await screen.findByRole('button', { name: 'Request extraction proposal' });
    vi.useFakeTimers();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Request extraction proposal' })); });
    expect(state.workflowReads).toBe(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(20_001); });
    expect(state.workflowSignal?.aborted).toBe(true);
    expect(screen.getByText(/Monitoring paused or unavailable/)).toBeVisible();
    expect(screen.queryByText('Untrusted proposal — not owner-confirmed.')).not.toBeInTheDocument();
    expect(state.view.latest_assessment).toBeNull();
  });

  it('distinguishes an unavailable initial requirement read from a reviewed empty set', async () => {
    const { api, state } = server(); state.readFailure = new Error('Offline');
    render(<Suitability api={api} postingId="posting-one" />);
    expect(await screen.findByText('Requirement state unavailable, not an empty requirement set or zero match.')).toBeVisible();
    expect(screen.queryByText('Requirements unavailable.')).not.toBeInTheDocument();
    expect(state.submissions).toHaveLength(0);
  });
  it('reconciles the posting version bumped by extraction before adopting and saving its proposal', async () => {
    const { api, state } = server(); state.view.model_route.status = 'qualified';
    render(<Suitability api={api} postingId="posting-one" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Request extraction proposal' }));
    const adopt = await screen.findByRole('button', { name: 'Load extraction proposal into review buffer' });
    await waitFor(() => expect(adopt).toBeEnabled());
    expect(state.view.version).toBe(2);
    fireEvent.click(adopt);
    fireEvent.click(screen.getByLabelText(/I reviewed all exact source spans/));
    fireEvent.click(screen.getByRole('button', { name: 'Save reviewed requirements' }));
    await screen.findByText(/Saved reviewed requirement revision 2/);
    expect(state.submissions.map(item => item.version)).toEqual([1, 2]);
    expect(screen.queryByRole('region', { name: 'Review conflict' })).not.toBeInTheDocument();
  });

  it('saves an explicitly reviewed matching proposal against the post-202 version without discarding its buffer', async () => {
    const { api, state } = server(); state.view.model_route.status = 'qualified';
    state.workflow = { ...state.workflow, kind: 'matching', result: { posting_id: 'posting-one', source_sha256: 'a'.repeat(64), profile_revision_id: 'profile-rev-one', requirements_revision_id: 'requirements-one',
      assessments: [{ requirement_key: 'req-1', coverage: 'not_evidenced', claim_revision_ids: [], evidence_revision_ids: [], explanation: 'Owner must still inspect this proposed gap.' }] } };
    render(<Suitability api={api} postingId="posting-one" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Request matching proposal' }));
    const adopt = await screen.findByRole('button', { name: 'Load matching proposal into review buffer' });
    await waitFor(() => expect(adopt).toBeEnabled());
    fireEvent.click(adopt);
    fireEvent.change(screen.getByLabelText('Explanation of evidence and gaps'), { target: { value: 'My reviewed explanation stays intact through save.' } });
    fireEvent.click(screen.getByLabelText(/I reviewed every coverage row/));
    fireEvent.click(screen.getByRole('button', { name: 'Save owner-reviewed assessment' }));
    await screen.findByText(/Saved immutable owner-reviewed assessment/);
    expect(state.submissions.map(item => item.version)).toEqual([1, 2]);
    expect(state.view.latest_assessment?.assessments[0]?.explanation).toBe('My reviewed explanation stays intact through save.');
    expect(screen.queryByRole('region', { name: 'Review conflict' })).not.toBeInTheDocument();
  });

  it('retains accepted durable work but gates proposal adoption until a failed version reconciliation is retried', async () => {
    const { api, state } = server(); state.view.model_route.status = 'qualified'; state.reconcileFailure = true;
    render(<Suitability api={api} postingId="posting-one" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Request extraction proposal' }));
    const adopt = await screen.findByRole('button', { name: 'Load extraction proposal into review buffer' });
    expect(adopt).toBeDisabled();
    expect(screen.getByRole('region', { name: 'Current suitability task' })).toHaveTextContent('task-one');
    expect(screen.getByText(/Task acceptance retained.*Posting version reconciliation is unavailable/)).toBeVisible();
    expect(screen.getByRole('button', { name: 'Review requirements manually' })).toBeDisabled();
    expect(state.submissions).toHaveLength(1);
    state.reconcileFailure = false;
    fireEvent.click(screen.getByRole('button', { name: 'Reload review state' }));
    await waitFor(() => expect(adopt).toBeEnabled());
    fireEvent.click(adopt);
    fireEvent.click(screen.getByLabelText(/I reviewed all exact source spans/));
    fireEvent.click(screen.getByRole('button', { name: 'Save reviewed requirements' }));
    await screen.findByText(/Saved reviewed requirement revision 2/);
    expect(state.submissions.map(item => item.version)).toEqual([1, 2]);
  });

  it('immediately invalidates an old snapshot and matching proposal when a known parent profile revision changes', async () => {
    const { api, state } = server(); state.view.model_route.status = 'qualified'; state.view.latest_assessment = assessmentSnapshot();
    state.workflow = { ...state.workflow, kind: 'matching', result: { posting_id: 'posting-one', source_sha256: 'a'.repeat(64), profile_revision_id: 'profile-rev-one', requirements_revision_id: 'requirements-one',
      assessments: [{ requirement_key: 'req-1', coverage: 'not_evidenced', claim_revision_ids: [], evidence_revision_ids: [], explanation: 'Old pinned profile.' }] } };
    const { rerender } = render(<Suitability api={api} postingId="posting-one" currentProfile={state.profile} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Request matching proposal' }));
    const adopt = await screen.findByRole('button', { name: 'Load matching proposal into review buffer' });
    await waitFor(() => expect(adopt).toBeEnabled());
    expect(screen.getByText('Current snapshot')).toBeVisible();
    state.profile = { ...state.profile, version: 2, current_revision: { ...state.profile.current_revision, id: 'profile-rev-two', revision_no: 2 } };
    rerender(<Suitability api={api} postingId="posting-one" currentProfile={state.profile} />);
    expect(screen.getByText('Stale historical snapshot — not a current eligibility assertion')).toBeVisible();
    expect(adopt).toBeDisabled();
    expect(state.view.latest_assessment?.stale).toBe(false);
    expect(state.submissions).toHaveLength(1);
  });

  it('retains pinned coverage text for explicit comparison when the parent profile changes', async () => {
    const { api, state } = server();
    const { rerender } = render(<Suitability api={api} postingId="posting-one" currentProfile={state.profile} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Review evidence coverage manually' }));
    fireEvent.change(screen.getByLabelText('Explanation of evidence and gaps'), { target: { value: 'Keep this pinned coverage review.' } });
    fireEvent.click(screen.getByLabelText(/I reviewed every coverage row/));
    state.profile = { ...state.profile, version: 2, current_revision: { ...state.profile.current_revision, id: 'profile-rev-two', revision_no: 2 } };
    rerender(<Suitability api={api} postingId="posting-one" currentProfile={state.profile} />);
    expect(screen.getByLabelText('Explanation of evidence and gaps')).toHaveValue('Keep this pinned coverage review.');
    expect(screen.getByRole('button', { name: 'Save owner-reviewed assessment' })).toBeDisabled();
    expect(screen.getByRole('region', { name: 'Current profile comparison' })).toHaveTextContent('profile-rev-two');
    expect(state.submissions).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: 'Discard coverage buffer and use compared profile' }));
    expect(screen.queryByLabelText('Explanation of evidence and gaps')).not.toBeInTheDocument();
  });

  it('reconciles reread same-ID posting content while retaining and invalidating its old proposal', async () => {
    const { api, state } = server(); state.view.model_route.status = 'qualified'; state.view.latest_assessment = assessmentSnapshot();
    render(<JobSources api={api} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Read Engineer at Synthetic North' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Request extraction proposal' }));
    const adopt = await screen.findByRole('button', { name: 'Load extraction proposal into review buffer' });
    await waitFor(() => expect(adopt).toBeEnabled());
    const reread = screen.getByRole('button', { name: 'Read Engineer at Synthetic North' });
    // Proposal readiness can precede the child's busy-state update unlocking the parent catalog.
    await waitFor(() => expect(reread).toBeEnabled());
    let releaseRead!: () => void;
    // ES2022 library: keep this response pending to observe the invalidation before reconciliation.
    state.reviewReadWait = new Promise<void>(resolve => { releaseRead = resolve; });
    state.postingContentSha256 = 'd'.repeat(64);
    state.view = { ...state.view, version: 3, source_text: 'A changed posting requires Rust.', source_sha256: 'b'.repeat(64), current_revision: { ...state.view.current_revision!, stale: true }, latest_assessment: { ...state.view.latest_assessment!, stale: true, stale_reasons: ['posting_source_changed'] } };
    fireEvent.click(reread);
    expect(await screen.findByText('Stale historical snapshot — not a current eligibility assertion')).toBeVisible();
    expect(adopt).toBeDisabled();
    expect(screen.getByRole('region', { name: 'Current suitability task' })).toHaveTextContent('task-one');
    const review = screen.getByRole('region', { name: 'Requirements and explainable suitability' });
    expect(review).toHaveTextContent('😀 SQL and Kubernetes.');
    expect(review).not.toHaveTextContent(state.view.source_text);
    await act(async () => { releaseRead(); });
    await waitFor(() => expect(review).toHaveTextContent(state.view.source_text));
    expect(review).toHaveTextContent('b'.repeat(64));
    expect(screen.getByRole('button', { name: 'Load extraction proposal into review buffer' })).toBeDisabled();
    expect(screen.getByText(/Proposal source differs/)).toBeVisible();
    expect(state.submissions).toHaveLength(1);
  });

  it('retains recoverable old-source edits across a same-ID content change until the owner discards them explicitly', async () => {
    const { api, state } = server();
    const { rerender } = render(<Suitability api={api} postingId="posting-one" postingVersion={1} postingContentSha256={state.postingContentSha256} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Review requirements manually' }));
    fireEvent.change(screen.getByLabelText('Requirement wording'), { target: { value: 'Recover this old-source local wording.' } });
    state.view = { ...state.view, version: 2, source_text: 'Changed source with Rust.', source_sha256: 'b'.repeat(64), current_revision: { ...state.view.current_revision!, stale: true } };
    state.postingContentSha256 = 'd'.repeat(64);
    rerender(<Suitability api={api} postingId="posting-one" postingVersion={2} postingContentSha256={state.postingContentSha256} />);
    await screen.findByRole('region', { name: 'Review conflict' });
    expect(screen.getByLabelText('Requirement wording')).toHaveValue('Recover this old-source local wording.');
    expect(screen.getByLabelText('Selectable normalized source')).toHaveValue(source);
    expect(screen.getByRole('button', { name: 'Use compared version and retain review buffer' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Save reviewed requirements' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Discard local buffer and load compared review' }));
    expect(screen.queryByLabelText('Requirement wording')).not.toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Requirements and explainable suitability' })).toHaveTextContent('Changed source with Rust.');
    expect(state.submissions).toHaveLength(0);
  });

  it('does not fabricate a source change for a version-only disposition update and can explicitly retain coverage', async () => {
    const { api, state } = server(); state.view.latest_assessment = assessmentSnapshot();
    const { rerender } = render(<Suitability api={api} postingId="posting-one" postingVersion={1} postingContentSha256={state.postingContentSha256} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Review evidence coverage manually' }));
    fireEvent.change(screen.getByLabelText('Explanation of evidence and gaps'), { target: { value: 'Retain coverage when only disposition changed.' } });
    state.view = { ...state.view, version: 2 };
    rerender(<Suitability api={api} postingId="posting-one" postingVersion={2} postingContentSha256={state.postingContentSha256} />);
    const retain = await screen.findByRole('button', { name: 'Use compared version and retain review buffer' });
    expect(retain).toBeEnabled();
    expect(screen.queryByText(/posting_source_changed/)).not.toBeInTheDocument();
    fireEvent.click(retain);
    expect(screen.getByLabelText('Explanation of evidence and gaps')).toHaveValue('Retain coverage when only disposition changed.');
    expect(screen.getByText('Current snapshot')).toBeVisible();
    fireEvent.click(screen.getByLabelText(/I reviewed every coverage row/));
    fireEvent.click(screen.getByRole('button', { name: 'Save owner-reviewed assessment' }));
    await screen.findByText(/Saved immutable owner-reviewed assessment/);
    expect(state.submissions[0]?.version).toBe(2);
  });

  it.each(['succeeded', 'permanent_failure'] as const)('politely announces automatic %s without moving focus or announcing the proposal JSON', async outcome => {
    const { api, state } = server(); state.view.model_route.status = 'qualified';
    state.workflow = { ...state.workflow, state: 'running', result: { posting_id: 'posting-one', source_sha256: 'a'.repeat(64) } };
    render(<Suitability api={api} postingId="posting-one" />);
    await screen.findByRole('button', { name: 'Request extraction proposal' });
    vi.useFakeTimers();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Request extraction proposal' })); });
    const live = screen.getByRole('status', { name: 'Suitability task status' });
    expect(live).toHaveTextContent('Task running.');
    const focused = screen.getByRole('button', { name: 'Inspect immutable requirement history' });
    focused.focus();
    state.workflow = { ...state.workflow, version: 2, state: outcome, last_error_code: outcome === 'permanent_failure' ? 'model_unreachable' : null,
      result: outcome === 'succeeded' ? { posting_id: 'posting-one', source_sha256: 'a'.repeat(64), requirements: [requirement] } : null };
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(live).toHaveTextContent(outcome === 'succeeded' ? 'Task succeeded.' : 'Task failed: model_unreachable.');
    expect(live).toHaveAttribute('aria-live', 'polite');
    expect(live).toHaveAttribute('aria-atomic', 'true');
    expect(live.querySelector('pre')).toBeNull();
    expect(live).not.toHaveTextContent('requirement_key');
    expect(focused).toHaveFocus();
    expect(state.view.latest_assessment).toBeNull();
  });

  it('keeps a confirmed empty review explicitly unavailable instead of asserting suitability from zero-of-zero coverage', async () => {
    const { api, state } = server();
    state.view.current_revision = { ...state.view.current_revision!, requirements: [] };
    state.view.latest_assessment = { ...assessmentSnapshot(), evidence_coverage: { supported: 0, partial: 0, not_evidenced: 0, conflicting: 0, total: 0 } };
    render(<Suitability api={api} postingId="posting-one" />);
    expect(await screen.findByText('Requirements unavailable — no current suitability assertion')).toBeVisible();
    expect(screen.queryByText('Current snapshot')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Review evidence coverage manually' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Request matching proposal' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Review requirements manually' }));
    expect(screen.queryByLabelText('Requirement wording')).not.toBeInTheDocument();
    fireEvent.click(screen.getByLabelText(/I reviewed all exact source spans/));
    fireEvent.click(screen.getByRole('button', { name: 'Save reviewed requirements' }));
    await screen.findByText(/Saved reviewed requirement revision 2/);
    expect(state.submissions[0]?.body).toEqual({ confirmed: true, requirements: [] });
    expect(screen.getByText('Requirements unavailable — no current suitability assertion')).toBeVisible();
    expect(screen.getByText('No reviewed hard constraints: unknown — no_reviewed_constraints')).toBeVisible();
    expect(screen.queryByText('Current snapshot')).not.toBeInTheDocument();
  });
});
