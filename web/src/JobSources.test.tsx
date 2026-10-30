// SPDX-License-Identifier: MIT OR Apache-2.0
import { useState } from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { JobSources } from './JobSources';
import { Api, ApiError, type DuplicateCandidate, type ObservationDetail, type PostingDetail, type PostingWrite, type Source, type SourceWrite, type Workflow } from './api';

const text = 'Fictional engineering role.\n<script>window.sourceExecuted = true</script>\n<a href="https://untrusted.example.com">Inert source words</a>';
const savedPosting: PostingDetail = {
  id: 'posting-one', version: 1, title: 'Fictional engineer', company: 'Fictional North', location_text: 'Remote', status: 'open',
  created_at: '2026-01-01T12:00:00Z', updated_at: '2026-01-01T12:00:00Z', description_text: text,
  canonical_url: 'https://jobs.example.com/fictional-one', content_sha256: 'a'.repeat(64), source_config_id: 'manual-source',
  source_kind: 'manual', external_id: 'manual-posting-one', last_observed_at: '2026-01-01T12:00:00Z', disposition: null,
};
const retainedObservation: ObservationDetail = {
  id: 'observation-one', source_config_id: 'manual-source', source_kind: 'manual', board_token: null, external_id: 'manual-posting-one',
  content_sha256: 'a'.repeat(64), fetched_at: '2026-01-01T12:00:00Z', created_at: '2026-01-01T12:00:00Z', policy_version: 1,
  source_url: 'manual:owner-entry', snapshot: { title: 'Fictional engineer', description_text: text },
};
function reviewedSource(): Source {
  return { id: 'source-one', version: 3, source_kind: 'greenhouse', board_token: 'fictional_north', company: 'Fictional North', enabled: true,
    terms_url: 'https://careers.example.com/terms', terms_revision: 'Reviewed 2026-01-01', terms_reviewed_at: '2026-01-01T12:00:00Z',
    policy_version: 2, allowed_host: 'boards-api.greenhouse.io', request_limit_per_minute: 2, last_success_at: '2026-01-01T12:00:00Z',
    last_error_code: null, next_allowed_at: null, rate_limit_blocked: false, retry_after_seconds: null, removed_at: null, health: 'healthy', quota: { known: false, limit: null }, created_at: '2026-01-01T12:00:00Z' };
}
function runningWorkflow(): Workflow {
  return { id: 'source-task-one', kind: 'source_refresh', version: 7, state: 'running', attempt: 1, max_attempts: 3,
    next_attempt_at: null, last_error_code: null, result: null,
    created_at: '2026-10-25T12:00:00Z', updated_at: '2026-10-25T12:00:00Z', deadline_at: '2030-01-01T00:00:00Z',
    cancel_requested_at: null, lease_expires_at: '2026-10-25T12:01:00Z', last_heartbeat_at: '2026-10-25T12:00:00Z', queue_age_seconds: null,
    remaining_attempts: 2, cancelable: true, retryable: false, retry_block_reason: 'state_not_retryable', delivery_state: 'not_attempted',
    cancellation: { requested: false, local_fence_settled: false, provider_stopped: null, completed_effects_preserved: true, reason: null },
    usage: { status: 'not_applicable', model_runs: 0, inference_post_attempts: 0, inference_post_limit: null, input_tokens: null, output_tokens: null, token_budget: 0, tokens_used: 0, cost_budget_microunits: 0, cost_used_microunits: null, currency: 'USD' } };
}
interface Submission { path: string; method: string; body: unknown; version: number | undefined; key: boolean | string }
function server() {
  const api = new Api();
  const state = {
    postings: [savedPosting] as PostingDetail[], source: reviewedSource(), workflow: runningWorkflow(),
    postingFailures: [] as Error[], sourceWriteFailures: [] as Error[], refreshFailures: [] as Error[], cancelFailures: [] as Error[],
    catalogFailure: null as Error | null, workflowFailure: null as Error | null, submissions: [] as Submission[], workflowReads: 0,
    postingCursor: null as string | null, sourceExists: true,
    catalogWaitsForAbort: false,
    decisionFailures: [] as Error[], retireFailures: [] as Error[],
    duplicates: [{ ...savedPosting, id: 'candidate-one', source_config_id: 'source-one', source_kind: 'greenhouse', external_id: 'board-role-42', decision: 'suspected' }] as DuplicateCandidate[],
    duplicateCursor: null as string | null,
  };
  const request = vi.spyOn(api, 'request').mockImplementation(async <T,>(path: string, method = 'GET', body?: unknown, version?: number, key: boolean | string = false, signal?: AbortSignal): Promise<T> => {
    if (method !== 'GET') state.submissions.push({ path, method, body, version, key });
    const url = new URL(path, 'http://bonafides.local');
    if (path === '/profile' && method === 'GET') return { id: 'profile-one', version: 1, current_revision: { id: 'profile-revision-one', revision_no: 1, content_sha256: 'b'.repeat(64), created_at: savedPosting.created_at, author_owner_id: 'owner-one', reason: 'owner_save', snapshot: { display_name: 'Fictional owner', headline: '', claim_revision_ids: [], evidence_revision_ids: [] }, claims: [] }, evidence: [], quality_warnings: ['empty_profile'], experience_coverage: { minimum_days: 0, maximum_days: 0, undated_or_open_entries: 0 } } as T;
    if (/^\/postings\/[^/]+\/requirements$/.test(path) && method === 'GET') {
      const posting = state.postings.find(item => item.id === path.split('/')[2]);
      if (!posting) throw new ApiError(404, 'posting_not_found');
      return { posting_id: posting.id, version: posting.version, source_text: posting.description_text, source_sha256: posting.content_sha256, current_revision: null, latest_assessment: null, model_route: { status: 'disabled', reason: 'No authorized local model route', identity: null } } as T;
    }
    if (url.pathname === '/postings' && method === 'GET') {
      if (state.catalogWaitsForAbort) {
        if (!signal) throw new Error('Expected an abortable catalog request');
        // The project's ES2022 library target does not expose Promise.withResolvers.
        await new Promise<void>((_resolve, reject) => {
          const stopped = () => reject(new DOMException('Catalog request stopped', 'AbortError'));
          if (signal.aborted) stopped(); else signal.addEventListener('abort', stopped, { once: true });
        });
      }
      if (state.catalogFailure) throw state.catalogFailure;
      if (url.searchParams.has('cursor')) return { items: [{ ...savedPosting, id: 'posting-two', title: 'Fictional designer' }], next_cursor: null, query_elapsed_ms: 0.7, as_of: '2026-01-02T12:00:00Z' } as T;
      const items = state.postings.filter(item => {
        const filter = url.searchParams;
        return (!filter.get('q') || `${item.title} ${item.company} ${item.description_text}`.toLowerCase().includes(filter.get('q')!.toLowerCase()))
          && (!filter.get('location') || item.location_text.toLowerCase().includes(filter.get('location')!.toLowerCase()))
          && (!filter.get('source_id') || item.source_config_id === filter.get('source_id'))
          && (!filter.get('status') || item.status === filter.get('status'))
          && (!filter.get('disposition') || (item.disposition ?? 'unmarked') === filter.get('disposition'))
          && (!filter.get('observed_after') || (item.last_observed_at !== null && Date.parse(item.last_observed_at) >= Date.parse(filter.get('observed_after')!)));
      });
      return { items, next_cursor: state.postingCursor, query_elapsed_ms: 1.25, as_of: '2026-01-02T12:00:00Z' } as T;
    }
    if (path === '/postings' && method === 'POST') {
      const failure = state.postingFailures.shift(); if (failure) throw failure;
      state.postings = [savedPosting]; return savedPosting as T;
    }
    if (path === '/postings/posting-one/disposition' && method === 'PUT') {
      const failure = state.decisionFailures.shift(); if (failure) throw failure;
      if (!body || typeof body !== 'object' || !('disposition' in body)) throw new Error('Expected disposition command');
      const disposition = body.disposition;
      if (disposition !== 'saved' && disposition !== 'dismissed' && disposition !== 'archived' && disposition !== 'unmarked') throw new Error('Unexpected disposition');
      const current = state.postings.find(item => item.id === 'posting-one')!;
      const updated: PostingDetail = { ...current, disposition: disposition === 'unmarked' ? null : disposition, version: current.version + 1 };
      state.postings = state.postings.map(item => item.id === updated.id ? updated : item); return updated as T;
    }
    if (url.pathname === '/postings/posting-one/duplicates' && method === 'GET') return { items: url.searchParams.has('cursor') ? [{ ...state.duplicates[0], id: 'candidate-two', external_id: 'board-role-43' }] : state.duplicates, next_cursor: url.searchParams.has('cursor') ? null : state.duplicateCursor } as T;
    if (/^\/postings\/posting-one\/duplicates\/[^/]+\/decision$/.test(path) && method === 'POST') {
      const failure = state.decisionFailures.shift(); if (failure) throw failure;
      if (!body || typeof body !== 'object' || !('decision' in body) || (body.decision !== 'distinct' && body.decision !== 'suspected')) throw new Error('Expected duplicate decision');
      const candidateId = path.split('/')[4]; const decision = body.decision;
      state.duplicates = state.duplicates.map(item => item.id === candidateId ? { ...item, decision } : item);
      state.postings = state.postings.map(item => item.id === 'posting-one' ? { ...item, version: item.version + 1 } : item);
      return state.postings[0] as T;
    }
    if (/^\/postings\/[^/]+$/.test(path) && method === 'GET') return state.postings.find(item => item.id === path.split('/')[2]) as T;
    if (path === '/postings/posting-one/observations') return { items: [retainedObservation], next_cursor: null } as T;
    if (path === '/postings/posting-one/observations/observation-one') return retainedObservation as T;
    if (path === '/sources' && method === 'GET') return { items: [...(state.sourceExists ? [state.source] : []), { ...reviewedSource(), id: 'manual-source', source_kind: 'manual', company: 'Manual entries', board_token: null, enabled: false }] } as T;
    if (path === '/sources/source-one' && method === 'GET') return state.source as T;
    if (path === '/sources/source-one' && method === 'DELETE') {
      const failure = state.retireFailures.shift(); if (failure) throw failure;
      state.source = { ...state.source, version: state.source.version + 1, enabled: false, removed_at: '2026-01-03T12:00:00Z', health: 'disabled' };
      return state.source as T;
    }
    if ((path === '/sources' && method === 'POST') || (path === '/sources/source-one' && method === 'PUT')) {
      const failure = state.sourceWriteFailures.shift(); if (failure) throw failure;
      const input = body as SourceWrite;
      state.source = { ...state.source, ...input, version: state.source.version + 1, policy_version: state.source.policy_version + 1,
        health: input.enabled ? 'unknown' : 'disabled', terms_reviewed_at: input.terms_review_acknowledged ? '2026-01-02T12:00:00Z' : state.sourceExists ? state.source.terms_reviewed_at : null };
      state.sourceExists = true; return state.source as T;
    }
    if (path === '/sources/source-one/refresh' && method === 'POST') {
      const failure = state.refreshFailures.shift(); if (failure) throw failure;
      return { source_id: 'source-one', workflow_id: 'source-task-one' } as T;
    }
    if (path === '/workflows') return { items: [state.workflow, { ...state.workflow, id: 'unrelated-task', kind: 'document_generation' }] } as T;
    if (path === '/workflows/source-task-one' && method === 'GET') {
      state.workflowReads += 1; if (state.workflowFailure) throw state.workflowFailure; return state.workflow as T;
    }
    if (path === '/workflows/source-task-one/cancel' && method === 'POST') {
      const failure = state.cancelFailures.shift(); if (failure) throw failure;
      state.workflow = { ...state.workflow, version: state.workflow.version + 1, state: 'cancelled' }; return state.workflow as T;
    }
    throw new ApiError(404, 'not_found');
  });
  return { api, state, request };
}
function Harness({ api, disabled = false }: { api: Api; disabled?: boolean }) {
  const [dirty, setDirty] = useState(false); const [busy, setBusy] = useState(false);
  return <><button disabled={dirty || busy}>Sign out guard</button><JobSources api={api} disabled={disabled} onDirtyChange={setDirty} onBusyChange={setBusy} /></>;
}
function showSection(name: 'Posting catalog' | 'Add a manual posting' | 'Manage job sources' | 'Refresh tasks') {
  fireEvent.click(screen.getByRole('button', { name }));
}
function fillManual() {
  showSection('Add a manual posting');
  fireEvent.change(screen.getByLabelText('Job title'), { target: { value: savedPosting.title } });
  fireEvent.change(screen.getByLabelText('Employer'), { target: { value: savedPosting.company } });
  fireEvent.change(screen.getByLabelText('Location'), { target: { value: savedPosting.location_text } });
  fireEvent.change(screen.getByLabelText('Complete original posting text'), { target: { value: text } });
  fireEvent.change(screen.getByLabelText('Optional HTTPS reference URL'), { target: { value: savedPosting.canonical_url } });
}
beforeEach(() => { window.history.replaceState(null, '', '/'); });
afterEach(() => { window.history.replaceState(null, '', '/'); vi.useRealTimers(); vi.restoreAllMocks(); });

describe('manual postings and retained source history', () => {
  it('opens on the catalog and keeps manual and board buffers across sections without starting a board read', async () => {
    const user = userEvent.setup(); const { api, state } = server();
    const view = render(<Harness api={api} />);
    await screen.findByRole('button', { name: 'Read Fictional engineer at Fictional North' });
    expect(screen.getByRole('button', { name: 'Posting catalog' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByRole('group', { name: 'Add a manual posting' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Add a Greenhouse board' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Load saved source refresh tasks' })).not.toBeInTheDocument();
    fillManual();
    await user.click(screen.getByRole('button', { name: 'Posting catalog' }));
    expect(screen.getByText(/Unsaved work is kept when you switch sections:/)).toHaveTextContent('manual posting');
    await user.click(screen.getByRole('button', { name: 'Manage job sources' }));
    expect(screen.getByRole('button', { name: 'Review or edit Fictional North board' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Refresh tasks' }));
    expect(screen.getByRole('button', { name: 'Load saved source refresh tasks' })).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Add a manual posting' }));
    expect(screen.getByLabelText('Job title')).toHaveValue(savedPosting.title);
    expect(screen.getByLabelText('Complete original posting text')).toHaveValue(text);
    await user.click(screen.getByRole('button', { name: 'Discard manual buffer' }));
    await user.click(screen.getByRole('button', { name: 'Manage job sources' }));
    await user.click(screen.getByRole('button', { name: 'Review or edit Fictional North board' }));
    fireEvent.change(await screen.findByLabelText('Board employer'), { target: { value: 'Buffered fictional employer' } });
    await user.click(screen.getByLabelText('I reviewed these exact board-specific terms and policy and acknowledge permission for this configured board'));
    await user.click(screen.getByRole('button', { name: 'Posting catalog' }));
    await user.click(screen.getByRole('button', { name: 'Add a manual posting' }));
    expect(screen.getByRole('button', { name: 'Save manual posting' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Manage job sources' }));
    expect(screen.getByLabelText('Board employer')).toHaveValue('Buffered fictional employer');
    expect(screen.getByLabelText('I reviewed these exact board-specific terms and policy and acknowledge permission for this configured board')).toBeChecked();
    view.rerender(<Harness api={api} disabled />);
    expect(screen.getByRole('button', { name: 'Posting catalog' })).toBeDisabled();
    expect(screen.getByLabelText('Board employer')).toHaveValue('Buffered fictional employer');
    expect(state.submissions).toHaveLength(0);
  });

  it('retains the full manual buffer and retry identity, then exposes inert text and exact attribution without a profile precondition', async () => {
    const user = userEvent.setup(); const { api, state } = server(); state.postings = []; state.postingFailures.push(new TypeError('offline'));
    const { container } = render(<Harness api={api} />);
    await screen.findByText(/^No retained postings/);
    fillManual(); expect(screen.getByRole('button', { name: 'Sign out guard' })).toBeDisabled();
    showSection('Manage job sources');
    expect(screen.getByRole('button', { name: 'Review or edit Fictional North board' })).toBeDisabled();
    showSection('Add a manual posting');
    await user.click(screen.getByRole('button', { name: 'Save manual posting' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('server could not be reached');
    expect(screen.getByLabelText('Complete original posting text')).toHaveValue(text);
    await user.click(screen.getByRole('button', { name: 'Save manual posting' }));
    await screen.findByRole('heading', { level: 3, name: 'Fictional engineer' });
    const submissions = state.submissions.filter(item => item.path === '/postings');
    expect(submissions).toHaveLength(2); expect(submissions[1]?.key).toBe(submissions[0]?.key);
    expect(submissions[1]?.version).toBeUndefined();
    expect(submissions[1]?.body as PostingWrite).toEqual({ title: savedPosting.title, company: savedPosting.company, location_text: 'Remote', description_text: text, canonical_url: savedPosting.canonical_url });
    expect(screen.getByLabelText('Complete original posting text')).toHaveValue('');
    expect(screen.getByRole('button', { name: 'Sign out guard' })).toBeEnabled();
    expect(container.querySelector('script')).toBeNull(); expect(container.querySelector('a[href="https://untrusted.example.com"]')).toBeNull();
    expect(screen.getByRole('link', { name: 'Original posting reference' })).toHaveAttribute('rel', 'noreferrer noopener');
    await user.click(screen.getByRole('button', { name: 'Load attributable observation history' }));
    await user.click(await screen.findByRole('button', { name: 'Read observation observation-one' }));
    await screen.findByRole('heading', { name: 'Exact retained observation observation-one' });
    expect(within(screen.getByRole('region', { name: 'Exact retained observation observation-one' })).getByText(/source manual-source.*external ID manual-posting-one.*policy 1/i)).toBeVisible();
    expect(container.querySelector('script')).toBeNull();
  });

  it('keeps the successful creation receipt and detail when catalog reconciliation reaches its abort deadline', async () => {
    const { api, state } = server(); state.postings = [];
    render(<Harness api={api} />);
    await screen.findByText(/^No retained postings/);
    fillManual(); state.catalogWaitsForAbort = true; vi.useFakeTimers();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save manual posting' })); });
    expect(screen.getByRole('heading', { level: 3, name: savedPosting.title })).toBeVisible();
    expect(screen.getByText(/Manual posting saved with an attributable observation/)).toBeVisible();
    expect(screen.getByRole('button', { name: 'Sign out guard' })).toBeDisabled();
    await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
    expect(screen.getByText(/Earlier loaded postings remain visible/)).toHaveTextContent('catalog request stopped');
    expect(screen.getByText(/latest request is unavailable, not confirmed empty/)).toBeVisible();
    expect(screen.queryByText(/^No retained postings/)).not.toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Retry retained posting decision' })).not.toBeInTheDocument();
    expect(screen.getByLabelText('Complete original posting text')).toHaveValue('');
    showSection('Add a manual posting');
    expect(screen.getByRole('button', { name: 'Discard manual buffer' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Sign out guard' })).toBeEnabled();
    expect(state.submissions).toHaveLength(1);
    expect(screen.getByText(/Manual posting saved with an attributable observation/)).toBeVisible();
    showSection('Posting catalog');
    state.catalogWaitsForAbort = false;
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Reload posting catalog' })); });
    expect(screen.getByRole('button', { name: 'Read Fictional engineer at Fictional North' })).toBeVisible();
    expect(screen.queryByText(/Earlier loaded postings remain visible/)).not.toBeInTheDocument();
    expect(state.submissions).toHaveLength(1);
  });

  it('loads the real next catalog page and preserves the retained list on a later read failure', async () => {
    const user = userEvent.setup(); const { api, state } = server(); state.postingCursor = 'page-two';
    render(<Harness api={api} />);
    await user.click(await screen.findByRole('button', { name: 'Load more postings' }));
    expect(await screen.findByRole('button', { name: 'Read Fictional designer at Fictional North' })).toBeVisible();
    state.catalogFailure = new ApiError(503, 'temporarily_unavailable');
    await user.click(screen.getByRole('button', { name: 'Reload posting catalog' }));
    expect(await screen.findByText(/Earlier loaded postings remain visible/)).toBeVisible();
    expect(screen.getByRole('button', { name: 'Read Fictional designer at Fictional North' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'Read Fictional engineer at Fictional North' })).toBeVisible();
    expect(screen.queryByText(/^No retained postings/)).toBeNull();
  });

  it('never renders an unsafe original reference as a clickable link', async () => {
    const user = userEvent.setup(); const { api, state } = server(); state.postings = [{ ...savedPosting, canonical_url: 'javascript:alert(1)' }];
    render(<Harness api={api} />);
    await user.click(await screen.findByRole('button', { name: 'Read Fictional engineer at Fictional North' }));
    await screen.findByRole('heading', { level: 3, name: 'Fictional engineer' });
    expect(screen.queryByRole('link', { name: 'Original posting reference' })).toBeNull();
    expect(screen.getByText('javascript:alert(1)', { selector: 'code' })).toBeVisible();
  });
});

describe('durable filtered catalog and reviewed decisions', () => {
  it('submits all filters explicitly, binds next pages to loaded filters and restores selection and filters from the URL', async () => {
    const user = userEvent.setup(); const { api, state, request } = server();
    window.history.replaceState(null, '', '/?visit=retained#opportunities');
    state.postingCursor = 'signed+opaque/==&next';
    const view = render(<Harness api={api} />);
    await screen.findByRole('button', { name: 'Read Fictional engineer at Fictional North' });
    fireEvent.change(screen.getByLabelText('Search posting text'), { target: { value: 'Fictional' } });
    fireEvent.change(screen.getByLabelText('Filter location'), { target: { value: 'Remote' } });
    await user.selectOptions(screen.getByLabelText('Filter source'), 'manual-source');
    await user.selectOptions(screen.getByLabelText('Posting status'), 'open');
    await user.selectOptions(screen.getByLabelText('Catalog disposition'), 'unmarked');
    fireEvent.change(screen.getByLabelText('Observed since (UTC)'), { target: { value: '2026-01-01T00:00:01' } });
    expect(request.mock.calls.filter(call => call[0].startsWith('/postings?'))).toHaveLength(0);
    await user.click(screen.getByRole('button', { name: 'Apply catalog filters' }));
    await waitFor(() => expect(screen.getByLabelText('Loaded catalog filters')).toHaveTextContent('Search: Fictional'));
    const expected = { q: 'Fictional', location: 'Remote', source_id: 'manual-source', status: 'open', disposition: 'unmarked' };
    const filteredCall = request.mock.calls.find(call => call[0].startsWith('/postings?q='));
    const received = new URL(filteredCall![0], window.location.origin);
    expect(Date.parse(received.searchParams.get('observed_after')!)).toBe(Date.UTC(2026, 0, 1, 0, 0, 1));
    expect(Object.fromEntries([...received.searchParams].filter(([key]) => key !== 'observed_after'))).toEqual(expected);
    fireEvent.change(screen.getByLabelText('Search posting text'), { target: { value: 'unsubmitted draft' } });
    await user.click(screen.getByRole('button', { name: 'Load more postings' }));
    const nextCall = request.mock.calls.find(call => call[0].includes('cursor='));
    const next = new URL(nextCall![0], window.location.origin);
    expect(next.searchParams.get('cursor')).toBe('signed+opaque/==&next');
    next.searchParams.delete('cursor'); expect(next.searchParams.toString()).toBe(received.searchParams.toString());
    expect(screen.getByLabelText('Search posting text')).toHaveValue('unsubmitted draft');
    expect(await screen.findByRole('button', { name: 'Read Fictional designer at Fictional North' })).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Read Fictional engineer at Fictional North' }));
    await screen.findByRole('heading', { level: 3, name: savedPosting.title });
    expect(new URLSearchParams(window.location.search).get('q')).toBe('Fictional');
    expect(new URLSearchParams(window.location.search).get('posting_id')).toBe(savedPosting.id);
    view.unmount(); render(<Harness api={api} />);
    expect(await screen.findByRole('heading', { level: 3, name: savedPosting.title })).toBeVisible();
    expect(screen.getByLabelText('Search posting text')).toHaveValue('Fictional');
    await user.click(screen.getByRole('button', { name: 'Clear catalog filters' }));
    await waitFor(() => expect(screen.getByLabelText('Loaded catalog filters')).toHaveTextContent('All retained postings'));
    expect(window.location.search).toBe('?visit=retained&posting_id=posting-one');
    expect(window.location.hash).toBe('#opportunities');
  });

  it('displays offset URL timestamps in UTC, preserves their submitted precision and restores them across back and forward navigation', async () => {
    const user = userEvent.setup(); const { api, request } = server();
    const first = '2026-01-01T12:00:00.123456+05:30';
    const second = '2025-12-31T23:15:59.654321-04:00';
    window.history.replaceState(null, '', `/?${new URLSearchParams({ observed_after: first })}`);
    render(<Harness api={api} />);
    await screen.findByLabelText('Loaded catalog filters');
    const input = screen.getByLabelText<HTMLInputElement>('Observed since (UTC)');
    expect(Date.parse(`${input.value}Z`)).toBe(Date.parse('2026-01-01T06:30:00.123Z'));
    expect(screen.getByLabelText('Loaded catalog filters')).toHaveTextContent(first);
    fireEvent.change(screen.getByLabelText('Search posting text'), { target: { value: 'Fictional' } });
    await user.click(screen.getByRole('button', { name: 'Apply catalog filters' }));
    expect(new URLSearchParams(window.location.search).get('observed_after')).toBe(first);
    expect(new URL(request.mock.calls.at(-1)![0], window.location.origin).searchParams.get('observed_after')).toBe(first);
    window.history.pushState(null, '', `/?${new URLSearchParams({ observed_after: second })}`);
    fireEvent(window, new PopStateEvent('popstate'));
    await waitFor(() => expect(screen.getByLabelText('Loaded catalog filters')).toHaveTextContent(second));
    expect(Date.parse(`${input.value}Z`)).toBe(Date.parse('2026-01-01T03:15:59.654Z'));
    act(() => { window.history.back(); });
    await waitFor(() => expect(screen.getByLabelText('Loaded catalog filters')).toHaveTextContent(first));
    expect(Date.parse(`${input.value}Z`)).toBe(Date.parse('2026-01-01T06:30:00.123Z'));
    expect(screen.getByLabelText('Search posting text')).toHaveValue('Fictional');
    act(() => { window.history.forward(); });
    await waitFor(() => expect(screen.getByLabelText('Loaded catalog filters')).toHaveTextContent(second));
    expect(Date.parse(`${input.value}Z`)).toBe(Date.parse('2026-01-01T03:15:59.654Z'));
    fireEvent.change(input, { target: { value: '2026-01-01T03:16:00.750' } });
    await user.click(screen.getByRole('button', { name: 'Apply catalog filters' }));
    const edited = new URLSearchParams(window.location.search).get('observed_after');
    expect(edited).toMatch(/Z$/); expect(Date.parse(edited!)).toBe(Date.parse('2026-01-01T03:16:00.750Z'));
    const readValues = request.mock.calls.filter(call => call[0].startsWith('/postings')).map(call => new URL(call[0], window.location.origin).searchParams.get('observed_after'));
    expect(readValues).toContain(first); expect(readValues).toContain(second); expect(readValues).toContain(edited);
  });

  it.each(['not-a-timestamp', '2026-01-01T12:00:00', '2026-02-30T12:00:00+05:30'])('shows the invalid URL timestamp %s without converting it into another query', async invalid => {
    const user = userEvent.setup(); const { api, state, request } = server();
    state.catalogFailure = new ApiError(422, 'invalid_observed_after');
    window.history.replaceState(null, '', `/?${new URLSearchParams({ observed_after: invalid })}`);
    render(<Harness api={api} />);
    expect(await screen.findByText(/Invalid observed-since value:/)).toHaveTextContent(invalid);
    expect(screen.getByLabelText('Observed since (UTC)')).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByLabelText('Observed since (UTC)')).toHaveValue('');
    expect(new URLSearchParams(window.location.search).get('observed_after')).toBe(invalid);
    expect(new URL(request.mock.calls.find(call => call[0].startsWith('/postings'))![0], window.location.origin).searchParams.get('observed_after')).toBe(invalid);
    expect(await screen.findByText(/Catalog is unavailable, not known to be empty/)).toBeVisible();
    expect(screen.queryByText(/^No retained postings/)).not.toBeInTheDocument();
    state.catalogFailure = null;
    await user.click(screen.getByRole('button', { name: 'Clear catalog filters' }));
    await screen.findByRole('button', { name: 'Read Fictional engineer at Fictional North' });
    expect(screen.queryByText(/Invalid observed-since value:/)).not.toBeInTheDocument();
    expect(new URLSearchParams(window.location.search).has('observed_after')).toBe(false);
  });

  it('retains the old-filter results and every draft on a failed query instead of presenting a false empty result', async () => {
    const user = userEvent.setup(); const { api, state } = server();
    render(<Harness api={api} />); await screen.findByLabelText('Loaded catalog filters');
    fillManual(); state.catalogFailure = new ApiError(503, 'catalog_offline');
    showSection('Posting catalog');
    fireEvent.change(screen.getByLabelText('Search posting text'), { target: { value: 'not confirmed' } });
    await user.click(screen.getByRole('button', { name: 'Apply catalog filters' }));
    expect(await screen.findByText(/Earlier loaded postings remain visible/)).toHaveTextContent('not the failed request');
    expect(screen.getByLabelText('Loaded catalog filters')).toHaveTextContent('All retained postings');
    expect(screen.getByRole('button', { name: 'Read Fictional engineer at Fictional North' })).toBeVisible();
    expect(screen.getByLabelText('Search posting text')).toHaveValue('not confirmed');
    expect(screen.getByLabelText('Complete original posting text')).toHaveValue(text);
    expect(screen.queryByText(/^No postings match/)).not.toBeInTheDocument();
    state.catalogFailure = null;
    await user.click(screen.getByRole('button', { name: 'Reload posting catalog' }));
    expect(await screen.findByText(/^No postings match these filters/)).toBeVisible();
    expect(screen.getByLabelText('Complete original posting text')).toHaveValue(text);
    await user.click(screen.getByRole('button', { name: 'Clear catalog filters' }));
    expect(await screen.findByRole('button', { name: 'Read Fictional engineer at Fictional North' })).toBeVisible();
  });

  it('distinguishes an unavailable initial catalog from an honestly empty catalog', async () => {
    const user = userEvent.setup(); const { api, state } = server(); state.catalogFailure = new TypeError('offline'); state.postings = [];
    render(<Harness api={api} />);
    expect(await screen.findByText(/Catalog is unavailable, not known to be empty/)).toBeVisible();
    expect(screen.queryByText(/^No retained postings/)).not.toBeInTheDocument();
    state.catalogFailure = null; await user.click(screen.getByRole('button', { name: 'Reload posting catalog' }));
    expect(await screen.findByText(/^No retained postings/)).toBeVisible();
  });

  it('does not let an aborted superseded initial read mark a newer catalog result stale', async () => {
    vi.useFakeTimers();
    const { api, state } = server(); state.catalogWaitsForAbort = true;
    await act(async () => { render(<Harness api={api} />); });
    state.catalogWaitsForAbort = false;
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Reload posting catalog' })); });
    expect(screen.getByRole('button', { name: 'Read Fictional engineer at Fictional North' })).toBeVisible();
    await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
    expect(screen.getByRole('button', { name: 'Read Fictional engineer at Fictional North' })).toBeVisible();
    expect(screen.queryByText(/Earlier loaded postings remain visible/)).not.toBeInTheDocument();
    expect(screen.queryByText(/catalog request stopped/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('persists save, dismiss, undo, archive and unmark without changing source status, application state or history', async () => {
    const user = userEvent.setup(); const { api, state } = server(); const view = render(<Harness api={api} />);
    await user.click(await screen.findByRole('button', { name: 'Read Fictional engineer at Fictional North' }));
    await user.click(await screen.findByRole('button', { name: 'Save opportunity' }));
    expect(await screen.findByText('Your disposition: saved')).toBeVisible();
    await user.selectOptions(screen.getByLabelText('Catalog disposition'), 'saved');
    await user.click(screen.getByRole('button', { name: 'Apply catalog filters' }));
    await user.click(screen.getByRole('button', { name: 'Dismiss opportunity' }));
    expect(await screen.findByText('Your disposition: dismissed')).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Read Fictional engineer at Fictional North' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Undo disposition change' }));
    expect(await screen.findByRole('button', { name: 'Read Fictional engineer at Fictional North' })).toBeVisible();
    view.unmount(); render(<Harness api={api} />);
    expect(await screen.findByText('Your disposition: saved')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Archive opportunity' }));
    expect(await screen.findByText('Your disposition: archived')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Unmark opportunity' }));
    expect(await screen.findByText('Your disposition: unmarked')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Load attributable observation history' }));
    expect(await screen.findByRole('button', { name: 'Read observation observation-one' })).toBeVisible();
    const changes = state.submissions.filter(item => item.path.endsWith('/disposition'));
    expect(changes.map(item => item.body)).toEqual([{ disposition: 'saved' }, { disposition: 'dismissed' }, { disposition: 'saved' }, { disposition: 'archived' }, { disposition: 'unmarked' }]);
    expect(changes.map(item => item.version)).toEqual([1, 2, 3, 4, 5]);
    expect(changes.every(item => item.method === 'PUT')).toBe(true);
    expect(state.postings[0]?.status).toBe('open'); expect(state.source).toEqual(reviewedSource());
    expect(state.submissions.every(item => item.path.endsWith('/disposition'))).toBe(true);
  });

  it('keeps a confirmed disposition and undo without offering mutation retry when list reconciliation aborts', async () => {
    const { api, state } = server();
    window.history.replaceState(null, '', '/?disposition=unmarked&posting_id=posting-one');
    render(<Harness api={api} />);
    await screen.findByRole('heading', { level: 3, name: savedPosting.title });
    await screen.findByLabelText('Loaded catalog filters');
    state.catalogWaitsForAbort = true; vi.useFakeTimers();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save opportunity' })); });
    expect(screen.getByText('Your disposition: saved')).toBeVisible();
    await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
    expect(screen.getByText(/Posting marked saved/)).toBeVisible();
    expect(screen.getByText(/Earlier loaded postings remain visible/)).toHaveTextContent('catalog request stopped');
    expect(screen.getByRole('button', { name: 'Undo disposition change' })).toBeEnabled();
    expect(screen.queryByRole('region', { name: 'Retained posting decision' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Retry retained posting decision' })).not.toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.queryByText(/^No postings match these filters/)).not.toBeInTheDocument();
    expect(state.submissions).toHaveLength(1); expect(state.postings[0]?.disposition).toBe('saved');
    expect(screen.getByRole('button', { name: 'Sign out guard' })).toBeEnabled();
  });

  it('keeps an unconfirmed disposition and stable retry key after a lost response', async () => {
    const user = userEvent.setup(); const { api, state } = server(); state.decisionFailures.push(new TypeError('offline'));
    render(<Harness api={api} />);
    await user.click(await screen.findByRole('button', { name: 'Read Fictional engineer at Fictional North' }));
    await user.click(await screen.findByRole('button', { name: 'Dismiss opportunity' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('server could not be reached');
    expect(screen.getByRole('region', { name: 'Retained posting decision' })).toHaveTextContent('dismissed');
    expect(screen.getByText('Your disposition: unmarked')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Sign out guard' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Retry retained posting decision' }));
    expect(await screen.findByText('Your disposition: dismissed')).toBeVisible();
    expect(state.submissions).toHaveLength(2); expect(state.submissions[1]?.key).toBe(state.submissions[0]?.key);
    expect(state.submissions[1]?.version).toBe(1);
  });

  it('requires explicit latest comparison and a separate retry after a posting conflict while preserving the intended decision', async () => {
    const user = userEvent.setup(); const { api, state } = server(); state.decisionFailures.push(new ApiError(412, 'stale_version'));
    render(<Harness api={api} />);
    await user.click(await screen.findByRole('button', { name: 'Read Fictional engineer at Fictional North' }));
    await user.click(await screen.findByRole('button', { name: 'Archive opportunity' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('newer revision');
    expect(screen.getByRole('button', { name: 'Retry retained posting decision' })).toBeDisabled();
    state.postings = [{ ...savedPosting, version: 9, disposition: 'saved', description_text: 'Concurrent retained original text.' }];
    await user.click(screen.getByRole('button', { name: 'Load current posting for comparison' }));
    const comparison = await screen.findByRole('region', { name: 'Current saved posting · version 9' });
    expect(comparison).toHaveTextContent('Concurrent retained original text.');
    expect(screen.getByText('Your disposition: unmarked')).toBeVisible();
    expect(screen.getByRole('region', { name: 'Retained posting decision' })).toHaveTextContent('archived');
    expect(state.submissions).toHaveLength(1);
    await user.click(screen.getByRole('button', { name: 'Use current posting version and retain my decision' }));
    expect(state.submissions).toHaveLength(1);
    expect(screen.getByText('Your disposition: saved')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Retry retained posting decision' }));
    expect(await screen.findByText('Your disposition: archived')).toBeVisible();
    expect(state.submissions[1]?.version).toBe(9);
    expect(state.submissions[1]?.key).not.toBe(state.submissions[0]?.key);
    expect(state.submissions[1]?.body).toEqual({ disposition: 'archived' });
  });

  it('reviews and reloads cross-source pairs explicitly without merging identities and paginates candidates', async () => {
    const user = userEvent.setup(); const { api, state, request } = server(); state.duplicateCursor = 'duplicate+cursor';
    render(<Harness api={api} />);
    await user.click(await screen.findByRole('button', { name: 'Read Fictional engineer at Fictional North' }));
    await user.click(await screen.findByRole('button', { name: 'Load duplicate review candidates' }));
    expect(await screen.findByRole('button', { name: 'Mark candidate-one as distinct' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Mark candidate-one as suspected duplicate' })).toBeEnabled();
    await user.click(screen.getByRole('button', { name: 'Mark candidate-one as distinct' }));
    expect(await screen.findByText(/Duplicate review recorded as distinct/)).toHaveTextContent('nothing was merged');
    await user.click(screen.getByRole('button', { name: 'Load duplicate review candidates' }));
    expect(within(screen.getByRole('region', { name: 'Cross-source duplicate review' })).getByText('distinct', { selector: 'strong' })).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Mark candidate-one as suspected duplicate' }));
    expect(await screen.findByText(/Duplicate review recorded as suspected/)).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Load more duplicate candidates' }));
    expect(await screen.findByRole('button', { name: 'Inspect candidate candidate-two' })).toBeVisible();
    expect(request.mock.calls.some(call => call[0] === '/postings/posting-one/duplicates?cursor=duplicate%2Bcursor')).toBe(true);
    const changes = state.submissions.filter(item => item.path.includes('/duplicates/'));
    expect(changes.map(item => item.body)).toEqual([{ decision: 'distinct' }, { decision: 'suspected' }]);
    expect(changes.map(item => item.version)).toEqual([1, 2]);
    expect(changes.every(item => item.method === 'POST')).toBe(true);
    expect(state.postings[0]?.id).toBe(savedPosting.id);
    expect(state.duplicates[0]?.external_id).toBe('board-role-42');
    expect(screen.queryByRole('button', { name: /merge/i })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Load attributable observation history' }));
    expect(await screen.findByRole('button', { name: 'Read observation observation-one' })).toBeVisible();
  });

  it('retains a duplicate decision on conflict and never retries against a newly loaded version without consent', async () => {
    const user = userEvent.setup(); const { api, state } = server(); state.decisionFailures.push(new ApiError(412, 'stale_version'));
    render(<Harness api={api} />);
    await user.click(await screen.findByRole('button', { name: 'Read Fictional engineer at Fictional North' }));
    await user.click(await screen.findByRole('button', { name: 'Load duplicate review candidates' }));
    await user.click(await screen.findByRole('button', { name: 'Mark candidate-one as distinct' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('newer revision');
    expect(screen.getByRole('region', { name: 'Retained posting decision' })).toHaveTextContent('distinct for candidate candidate-one');
    state.postings = [{ ...savedPosting, version: 7 }];
    await user.click(screen.getByRole('button', { name: 'Load current posting for comparison' }));
    await screen.findByRole('heading', { name: 'Current saved posting · version 7' });
    expect(screen.getByRole('button', { name: 'Retry retained posting decision' })).toBeDisabled();
    expect(state.submissions).toHaveLength(1);
    await user.click(screen.getByRole('button', { name: 'Use current posting version and retain my decision' }));
    await user.click(screen.getByRole('button', { name: 'Retry retained posting decision' }));
    expect(await screen.findByText(/Duplicate review recorded as distinct/)).toBeVisible();
    expect(state.submissions[1]?.version).toBe(7);
    expect(state.submissions[1]?.body).toEqual({ decision: 'distinct' });
  });

  it('confirms retirement, retains a failed retry identity and keeps retired source data inspectable after reload', async () => {
    const user = userEvent.setup(); const { api, state } = server(); state.retireFailures.push(new TypeError('offline'));
    state.postings = [{ ...savedPosting, source_config_id: 'source-one', source_kind: 'greenhouse', disposition: 'saved' }];
    render(<Harness api={api} />);
    showSection('Manage job sources');
    await user.click(await screen.findByRole('button', { name: 'Retire Fictional North board' }));
    expect(state.submissions).toHaveLength(0);
    await user.click(screen.getByRole('button', { name: 'Confirm retirement and retain history' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('server could not be reached');
    showSection('Posting catalog');
    expect(screen.getByRole('button', { name: 'Read Fictional engineer at Fictional North' })).toBeVisible();
    showSection('Manage job sources');
    await user.click(screen.getByRole('button', { name: 'Confirm retirement and retain history' }));
    expect(await screen.findByText('Retired source', { selector: 'strong' })).toBeVisible();
    expect(state.submissions[1]?.key).toBe(state.submissions[0]?.key);
    expect(state.submissions[1]?.version).toBe(3); expect(state.submissions[1]?.body).toBeUndefined();
    expect(state.submissions.every(item => item.method === 'DELETE')).toBe(true);
    await user.click(screen.getByRole('button', { name: 'Reload sources' }));
    expect(screen.getByRole('button', { name: 'Refresh Fictional North board' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Review or edit Fictional North board' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Retire Fictional North board' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Retire Manual entries board' })).not.toBeInTheDocument();
    showSection('Posting catalog');
    await user.selectOptions(screen.getByLabelText('Filter source'), 'source-one');
    await user.click(screen.getByRole('button', { name: 'Apply catalog filters' }));
    await user.click(screen.getByRole('button', { name: 'Read Fictional engineer at Fictional North' }));
    expect(await screen.findByText('Your disposition: saved')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Load attributable observation history' }));
    expect(await screen.findByRole('button', { name: 'Read observation observation-one' })).toBeVisible();
  });

  it('requires a source action comparison and new explicit precondition before retrying retirement after a conflict', async () => {
    const user = userEvent.setup(); const { api, state } = server(); state.retireFailures.push(new ApiError(412, 'stale_version'));
    render(<Harness api={api} />);
    showSection('Manage job sources');
    await user.click(await screen.findByRole('button', { name: 'Retire Fictional North board' }));
    await user.click(screen.getByRole('button', { name: 'Confirm retirement and retain history' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('newer revision');
    expect(screen.getByRole('button', { name: 'Confirm retirement and retain history' })).toBeDisabled();
    state.source = { ...state.source, version: 11, request_limit_per_minute: 1 };
    await user.click(screen.getByRole('button', { name: 'Load current source action comparison' }));
    expect(await screen.findByRole('heading', { name: 'Current source action precondition · version 11' })).toBeVisible();
    expect(state.submissions).toHaveLength(1);
    await user.click(screen.getByRole('button', { name: 'Use current source version and retain my action' }));
    expect(state.submissions).toHaveLength(1);
    await user.click(screen.getByRole('button', { name: 'Confirm retirement and retain history' }));
    expect(await screen.findByText('Retired source', { selector: 'strong' })).toBeVisible();
    expect(state.submissions[1]?.version).toBe(11);
    expect(state.submissions[1]?.key).not.toBe(state.submissions[0]?.key);
  });
  it('restores history navigation without discarding a manual buffer and loads the URL context only when explicitly released', async () => {
    const user = userEvent.setup(); const { api } = server();
    render(<Harness api={api} />); await screen.findByLabelText('Loaded catalog filters'); fillManual();
    window.history.replaceState(null, '', '/?q=no-match&posting_id=posting-one');
    fireEvent(window, new PopStateEvent('popstate'));
    expect(screen.getByLabelText('Complete original posting text')).toHaveValue(text);
    expect(screen.getByRole('button', { name: 'Load current URL context' })).toBeDisabled();
    expect(screen.getByLabelText('Loaded catalog filters')).toHaveTextContent('All retained postings');
    await user.click(screen.getByRole('button', { name: 'Discard manual buffer' }));
    showSection('Posting catalog');
    await user.click(screen.getByRole('button', { name: 'Load current URL context' }));
    expect(await screen.findByText(/^No postings match these filters/)).toBeVisible();
    expect(await screen.findByRole('heading', { level: 3, name: savedPosting.title })).toBeVisible();
    expect(screen.getByLabelText('Search posting text')).toHaveValue('no-match');
  });

  it('holds a stale source refresh behind explicit latest-policy comparison before authorizing its retry', async () => {
    const user = userEvent.setup(); const { api, state } = server(); state.refreshFailures.push(new ApiError(412, 'stale_version'));
    render(<Harness api={api} />);
    showSection('Manage job sources');
    await user.click(await screen.findByRole('button', { name: 'Refresh Fictional North board' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('newer revision');
    expect(screen.getByRole('button', { name: 'Retry source refresh' })).toBeDisabled();
    state.source = { ...state.source, version: 12, policy_version: 8 };
    await user.click(screen.getByRole('button', { name: 'Load current source action comparison' }));
    await screen.findByRole('heading', { name: 'Current source action precondition · version 12' });
    expect(state.submissions).toHaveLength(1);
    await user.click(screen.getByRole('button', { name: 'Use current source version and retain my action' }));
    expect(state.submissions).toHaveLength(1);
    await user.click(screen.getByRole('button', { name: 'Retry source refresh' }));
    showSection('Refresh tasks');
    expect(await screen.findByRole('heading', { name: 'Source refresh task source-task-one' })).toBeVisible();
    expect(state.submissions[1]?.version).toBe(12);
    expect(state.submissions[1]?.key).not.toBe(state.submissions[0]?.key);
  });
});

describe('board-specific policy review and source conflicts', () => {
  it('creates a disabled board without collecting, then requires exact terms acknowledgement before enabling and refreshing', async () => {
    const user = userEvent.setup(); const { api, state } = server(); state.sourceExists = false;
    render(<Harness api={api} />);
    showSection('Manage job sources');
    await screen.findByText(/No Greenhouse boards configured/);
    await user.click(screen.getByRole('button', { name: 'Add a Greenhouse board' }));
    fireEvent.change(screen.getByLabelText('Board employer'), { target: { value: 'Fictional North' } });
    fireEvent.change(screen.getByLabelText('Greenhouse board token'), { target: { value: 'fictional_north' } });
    await user.click(screen.getByRole('button', { name: 'Save board configuration' }));
    const first = state.submissions[0]; expect(first?.path).toBe('/sources'); expect(first?.version).toBeUndefined();
    expect((first?.body as SourceWrite).enabled).toBe(false);
    expect(await screen.findByRole('button', { name: 'Refresh Fictional North board' })).toBeDisabled();
    expect(state.submissions.some(item => item.path.endsWith('/refresh'))).toBe(false);
    await user.click(screen.getByRole('button', { name: 'Review or edit Fictional North board' }));
    expect(await screen.findByLabelText('Greenhouse board token')).toHaveAttribute('readonly');
    expect(screen.getByLabelText('Explicitly enable this reviewed source')).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Board-specific terms URL'), { target: { value: 'https://careers.example.com/terms' } });
    fireEvent.change(screen.getByLabelText('Terms revision or dated review reference'), { target: { value: 'Reviewed fictional terms revision two' } });
    await user.click(screen.getByLabelText('I reviewed these exact board-specific terms and policy and acknowledge permission for this configured board'));
    await user.click(screen.getByLabelText('Explicitly enable this reviewed source'));
    await user.click(screen.getByRole('button', { name: 'Save board configuration' }));
    expect(await screen.findByRole('button', { name: 'Refresh Fictional North board' })).toBeEnabled();
    expect(state.submissions[1]?.version).toBe(4);
    expect((state.submissions[1]?.body as SourceWrite).terms_review_acknowledged).toBe(true);
    expect(screen.getByText(/Provider quota: unknown. Your own request limit: 1 per minute/)).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Refresh Fictional North board' }));
    showSection('Refresh tasks');
    await screen.findByRole('heading', { name: 'Source refresh task source-task-one' });
    expect(state.submissions[2]?.path).toBe('/sources/source-one/refresh'); expect(state.submissions[2]?.version).toBe(5);
    expect(screen.getByRole('button', { name: 'Sign out guard' })).toBeDisabled();
  });

  it('retains a conflicting board buffer until explicit comparison and rebase, then requires renewed review', async () => {
    const user = userEvent.setup(); const { api, state } = server();
    render(<Harness api={api} />);
    showSection('Manage job sources');
    await user.click(await screen.findByRole('button', { name: 'Review or edit Fictional North board' }));
    fireEvent.change(await screen.findByLabelText('Board employer'), { target: { value: 'Edited fictional employer' } });
    await user.click(screen.getByLabelText('I reviewed these exact board-specific terms and policy and acknowledge permission for this configured board'));
    state.sourceWriteFailures.push(new ApiError(412, 'version_mismatch'));
    await user.click(screen.getByRole('button', { name: 'Save board configuration' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('newer revision');
    expect(screen.getByLabelText('Board employer')).toHaveValue('Edited fictional employer');
    expect(screen.getByRole('button', { name: 'Save board configuration' })).toBeDisabled();
    state.source = { ...state.source, version: 9, company: 'Concurrent fictional employer', terms_revision: 'Concurrent reviewed revision' };
    await user.click(screen.getByRole('button', { name: 'Load current source for comparison' }));
    await screen.findByRole('heading', { name: 'Current saved source · version 9' });
    expect(screen.getByText(/Employer: Concurrent fictional employer/)).toBeVisible();
    expect(screen.getByLabelText('Board employer')).toHaveValue('Edited fictional employer');
    await user.click(screen.getByRole('button', { name: 'Use current version and retain my board buffer' }));
    expect(screen.getByRole('button', { name: 'Save board configuration' })).toBeDisabled();
    await user.click(screen.getByLabelText('I reviewed these exact board-specific terms and policy and acknowledge permission for this configured board'));
    await user.click(screen.getByRole('button', { name: 'Save board configuration' }));
    await screen.findByRole('button', { name: 'Review or edit Edited fictional employer board' });
    expect(state.submissions[0]?.version).toBe(3); expect(state.submissions[1]?.version).toBe(9);
    expect(state.submissions[1]?.key).not.toBe(state.submissions[0]?.key);
    expect((state.submissions[1]?.body as SourceWrite).company).toBe('Edited fictional employer');
  });

  it('honors Retry-After as a minimum while retaining catalog and retry identity', async () => {
    const { api, state } = server(); state.refreshFailures.push(new ApiError(429, 'source_rate_limited', [], 120));
    render(<Harness api={api} />);
    showSection('Manage job sources');
    const refresh = await screen.findByRole('button', { name: 'Refresh Fictional North board' });
    vi.useFakeTimers(); vi.setSystemTime(new Date('2026-01-01T12:00:00Z'));
    await act(async () => { fireEvent.click(refresh); });
    expect(screen.getByRole('alert')).toHaveTextContent('source_rate_limited'); expect(refresh).toBeDisabled();
    showSection('Posting catalog');
    expect(screen.getByRole('button', { name: 'Read Fictional engineer at Fictional North' })).toBeVisible();
    showSection('Manage job sources');
    await act(async () => { await vi.advanceTimersByTimeAsync(119_000); }); expect(refresh).toBeDisabled();
    expect(state.submissions).toHaveLength(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); }); expect(refresh).toBeEnabled();
    await act(async () => { fireEvent.click(refresh); });
    expect(state.submissions).toHaveLength(2); expect(state.submissions[1]?.key).toBe(state.submissions[0]?.key);
    expect(state.submissions[1]?.version).toBe(3);
  });

  it('releases an expired source pause even when its response arrives after the deadline', async () => {
    vi.useFakeTimers();
    const start = Date.parse('2026-01-01T12:00:00Z'); vi.setSystemTime(start);
    const { api, state } = server(); state.source.next_allowed_at = new Date(start + 1000).toISOString();
    render(<Harness api={api} />);
    showSection('Manage job sources');
    vi.setSystemTime(start + 1001);
    await act(async () => { await Promise.resolve(); });
    const refresh = screen.getByRole('button', { name: 'Refresh Fictional North board' });
    await act(async () => { await vi.advanceTimersByTimeAsync(2); });
    expect(refresh).toBeEnabled();
    expect(screen.queryByText(/^Refresh is waiting until/)).not.toBeInTheDocument();
  });

  it('never converts an unrepresentable upstream wait into a finite retry or loses its retained catalog', async () => {
    const user = userEvent.setup(); const { api, state } = server();
    state.refreshFailures.push(new ApiError(429, 'source_rate_limited', [], 1e100));
    render(<Harness api={api} />);
    showSection('Manage job sources');
    const refresh = await screen.findByRole('button', { name: 'Refresh Fictional North board' });
    state.source = { ...state.source, version: 4, health: 'rate_limited', next_allowed_at: null };
    await user.click(refresh);
    expect(await screen.findByText(/No finite permitted retry time is available/)).toBeVisible();
    expect(refresh).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Reload sources' }));
    expect(refresh).toBeDisabled();
    expect(state.submissions.filter(item => item.path.endsWith('/refresh'))).toHaveLength(1);
    showSection('Posting catalog');
    expect(screen.getByRole('button', { name: 'Read Fictional engineer at Fictional North' })).toBeVisible();
  });
});

describe('bounded durable refresh observation and cancellation', () => {
  it('pauses while hidden, bounds visible polling, and cancels with the actual workflow version and stable retry identity', async () => {
    const { api, state } = server();
    render(<Harness api={api} />);
    showSection('Manage job sources');
    const refresh = await screen.findByRole('button', { name: 'Refresh Fictional North board' });
    vi.useFakeTimers();
    await act(async () => { fireEvent.click(refresh); });
    showSection('Refresh tasks');
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(true);
    act(() => document.dispatchEvent(new Event('visibilitychange')));
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); }); expect(state.workflowReads).toBe(0);
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(false);
    act(() => document.dispatchEvent(new Event('visibilitychange')));
    for (let index = 0; index < 120; index += 1) await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(state.workflowReads).toBe(120);
    expect(screen.getByText(/Automatic checks paused. The durable task may still be running/)).toBeVisible();
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); }); expect(state.workflowReads).toBe(120);
    state.cancelFailures.push(new TypeError('offline'));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Cancel source refresh' })); });
    expect(screen.getByRole('alert')).toHaveTextContent('server could not be reached');
    expect(screen.getByRole('button', { name: 'Sign out guard' })).toBeDisabled();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Cancel source refresh' })); });
    expect(screen.getByText(/State: cancelled/)).toBeVisible();
    const cancellations = state.submissions.filter(item => item.path.endsWith('/cancel'));
    expect(cancellations).toHaveLength(2); expect(cancellations[0]?.version).toBe(7); expect(cancellations[1]?.version).toBe(7);
    expect(cancellations[1]?.key).toBe(cancellations[0]?.key);
    showSection('Posting catalog');
    expect(screen.getByRole('button', { name: 'Read Fictional engineer at Fictional North' })).toBeVisible();
  });

  it('reconciles a successful read into retained postings and distinguishes excluded prospects from normal published jobs', async () => {
    const user = userEvent.setup(); const { api, state } = server();
    render(<Harness api={api} />);
    showSection('Manage job sources');
    await user.click(await screen.findByRole('button', { name: 'Refresh Fictional North board' }));
    showSection('Refresh tasks');
    state.postings = [savedPosting, { ...savedPosting, id: 'imported-posting', title: 'Published fictional designer', source_kind: 'greenhouse', source_config_id: 'source-one', external_id: 'greenhouse-42' }];
    state.workflow = { ...state.workflow, version: 8, state: 'succeeded', result: { source_id: 'source-one', policy_version: 2, fetched_at: '2026-01-02T12:00:00Z', posting_count: 1, new_posting_count: 1, changed_posting_count: 0, unchanged_posting_count: 0, excluded_prospect_count: 2, freshness: 'current' } };
    state.source = { ...state.source, last_success_at: '2026-01-02T12:00:00Z' };
    await user.click(screen.getByRole('button', { name: 'Check refresh task status' }));
    showSection('Posting catalog');
    expect(await screen.findByRole('button', { name: 'Read Published fictional designer at Fictional North' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'Read Fictional engineer at Fictional North' })).toBeVisible();
    showSection('Refresh tasks');
    expect(screen.getByText(/Excluded prospect posts: 2/)).toBeVisible();
    expect(screen.getByText(/Published normal jobs: 1; new 1, changed 0, unchanged 0/)).toBeVisible();
    showSection('Manage job sources');
    expect(screen.getByText(/Last successful read: 2026-01-02T12:00:00Z/)).toBeVisible();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Sign out guard' })).toBeEnabled());
  });

  it('recovers a saved source task and reconciles failure without replacing retained catalog with an empty state', async () => {
    const user = userEvent.setup(); const { api, state } = server();
    state.workflow = { ...state.workflow, state: 'permanent_failure', last_error_code: 'upstream_forbidden', result: { source_id: 'source-one', policy_version: 2, error_code: 'upstream_forbidden' } };
    render(<Harness api={api} />);
    await screen.findByRole('button', { name: 'Read Fictional engineer at Fictional North' });
    showSection('Refresh tasks');
    await user.click(screen.getByRole('button', { name: 'Load saved source refresh tasks' }));
    expect(screen.queryByRole('button', { name: 'Open source refresh task unrelated-task' })).toBeNull();
    state.catalogFailure = new ApiError(503, 'temporarily_unavailable');
    state.source = { ...state.source, health: 'degraded', last_error_code: 'upstream_forbidden' };
    await user.click(await screen.findByRole('button', { name: 'Open source refresh task source-task-one' }));
    expect(await screen.findByText(/Task ended, but the latest catalog or source health could not be loaded/)).toBeVisible();
    expect(screen.getByText(/Source task failure: upstream_forbidden/)).toBeVisible();
    showSection('Manage job sources');
    expect(screen.getByText(/Health: degraded/)).toBeVisible();
    showSection('Posting catalog');
    expect(screen.getByRole('button', { name: 'Read Fictional engineer at Fictional North' })).toBeVisible();
    expect(screen.queryByText(/^No retained postings/)).toBeNull();
  });

  it('disables the source immediately without deleting its catalog, and disabled parent controls retain the manual buffer', async () => {
    const user = userEvent.setup(); const { api, state } = server(); const { rerender } = render(<Harness api={api} />);
    showSection('Manage job sources');
    await user.click(await screen.findByRole('button', { name: 'Disable Fictional North board' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Refresh Fictional North board' })).toBeDisabled());
    expect(state.submissions[0]?.version).toBe(3); expect((state.submissions[0]?.body as SourceWrite).enabled).toBe(false);
    showSection('Posting catalog');
    expect(screen.getByRole('button', { name: 'Read Fictional engineer at Fictional North' })).toBeVisible();
    fillManual(); rerender(<Harness api={api} disabled />);
    expect(screen.getByRole('button', { name: 'Save manual posting' })).toBeDisabled();
    expect(screen.getByLabelText('Complete original posting text')).toHaveValue(text);
    const unload = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(unload); expect(unload.defaultPrevented).toBe(true);
  });
});
