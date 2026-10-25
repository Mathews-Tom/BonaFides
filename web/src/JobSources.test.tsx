// SPDX-License-Identifier: MIT OR Apache-2.0
import { useState } from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { JobSources } from './JobSources';
import { Api, ApiError, type ObservationDetail, type PostingDetail, type PostingWrite, type Source, type SourceWrite, type Workflow } from './api';

const text = 'Fictional engineering role.\n<script>window.sourceExecuted = true</script>\n<a href="https://untrusted.example.com">Inert source words</a>';
const savedPosting: PostingDetail = {
  id: 'posting-one', version: 1, title: 'Fictional engineer', company: 'Fictional North', location_text: 'Remote', status: 'open',
  created_at: '2026-01-01T12:00:00Z', updated_at: '2026-01-01T12:00:00Z', description_text: text,
  canonical_url: 'https://jobs.example.com/fictional-one', content_sha256: 'a'.repeat(64), source_config_id: 'manual-source',
  source_kind: 'manual', external_id: 'manual-posting-one', last_observed_at: '2026-01-01T12:00:00Z',
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
    last_error_code: null, next_allowed_at: null, rate_limit_blocked: false, retry_after_seconds: null, health: 'healthy', quota: { known: false, limit: null }, created_at: '2026-01-01T12:00:00Z' };
}
function runningWorkflow(): Workflow {
  return { id: 'source-task-one', kind: 'source_refresh', version: 7, state: 'running', attempt: 1, max_attempts: 3,
    next_attempt_at: null, last_error_code: null, result: null };
}
interface Submission { path: string; body: unknown; version: number | undefined; key: boolean | string }
function server() {
  const api = new Api();
  const state = {
    postings: [savedPosting] as PostingDetail[], source: reviewedSource(), workflow: runningWorkflow(),
    postingFailures: [] as Error[], sourceWriteFailures: [] as Error[], refreshFailures: [] as Error[], cancelFailures: [] as Error[],
    catalogFailure: null as Error | null, workflowFailure: null as Error | null, submissions: [] as Submission[], workflowReads: 0,
    postingCursor: null as string | null, sourceExists: true,
  };
  const request = vi.spyOn(api, 'request').mockImplementation(async <T,>(path: string, method = 'GET', body?: unknown, version?: number, key: boolean | string = false): Promise<T> => {
    if (method !== 'GET') state.submissions.push({ path, body, version, key });
    if (path === '/postings' && method === 'GET') {
      if (state.catalogFailure) throw state.catalogFailure;
      return { items: state.postings, next_cursor: state.postingCursor } as T;
    }
    if (path === '/postings?cursor=page-two' && method === 'GET') return { items: [{ ...savedPosting, id: 'posting-two', title: 'Fictional designer' }], next_cursor: null } as T;
    if (path === '/postings' && method === 'POST') {
      const failure = state.postingFailures.shift(); if (failure) throw failure;
      state.postings = [savedPosting]; return savedPosting as T;
    }
    if (path === '/postings/posting-one') return state.postings[0] as T;
    if (path === '/postings/posting-one/observations') return { items: [retainedObservation], next_cursor: null } as T;
    if (path === '/postings/posting-one/observations/observation-one') return retainedObservation as T;
    if (path === '/sources' && method === 'GET') return { items: state.sourceExists ? [state.source] : [] } as T;
    if (path === '/sources/source-one' && method === 'GET') return state.source as T;
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
function fillManual() {
  fireEvent.change(screen.getByLabelText('Job title'), { target: { value: savedPosting.title } });
  fireEvent.change(screen.getByLabelText('Employer'), { target: { value: savedPosting.company } });
  fireEvent.change(screen.getByLabelText('Location'), { target: { value: savedPosting.location_text } });
  fireEvent.change(screen.getByLabelText('Complete original posting text'), { target: { value: text } });
  fireEvent.change(screen.getByLabelText('Optional HTTPS reference URL'), { target: { value: savedPosting.canonical_url } });
}
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

describe('manual postings and retained source history', () => {
  it('retains the full manual buffer and retry identity, then exposes inert text and exact attribution without a profile precondition', async () => {
    const user = userEvent.setup(); const { api, state } = server(); state.postings = []; state.postingFailures.push(new TypeError('offline'));
    const { container } = render(<Harness api={api} />);
    await screen.findByText(/^No retained postings/);
    fillManual(); expect(screen.getByRole('button', { name: 'Sign out guard' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Review or edit Fictional North board' })).toBeDisabled();
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
    expect(screen.getByText((_value, element) => element?.tagName === 'PRE' && element.textContent === text)).toBeVisible();
    expect(screen.getByRole('link', { name: 'Original posting reference' })).toHaveAttribute('rel', 'noreferrer noopener');
    await user.click(screen.getByRole('button', { name: 'Load attributable observation history' }));
    await user.click(await screen.findByRole('button', { name: 'Read observation observation-one' }));
    await screen.findByRole('heading', { name: 'Exact retained observation observation-one' });
    expect(screen.getByText((_value, element) => element?.tagName === 'PRE' && element.textContent === JSON.stringify(retainedObservation.snapshot, null, 2))).toBeVisible();
    expect(within(screen.getByRole('region', { name: 'Exact retained observation observation-one' })).getByText(/source manual-source.*external ID manual-posting-one.*policy 1/i)).toBeVisible();
    expect(container.querySelector('script')).toBeNull();
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

describe('board-specific policy review and source conflicts', () => {
  it('creates a disabled board without collecting, then requires exact terms acknowledgement before enabling and refreshing', async () => {
    const user = userEvent.setup(); const { api, state } = server(); state.sourceExists = false;
    render(<Harness api={api} />);
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
    await screen.findByRole('heading', { name: 'Source refresh task source-task-one' });
    expect(state.submissions[2]?.path).toBe('/sources/source-one/refresh'); expect(state.submissions[2]?.version).toBe(5);
    expect(screen.getByRole('button', { name: 'Sign out guard' })).toBeDisabled();
  });

  it('retains a conflicting board buffer until explicit comparison and rebase, then requires renewed review', async () => {
    const user = userEvent.setup(); const { api, state } = server();
    render(<Harness api={api} />);
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
    const refresh = await screen.findByRole('button', { name: 'Refresh Fictional North board' });
    vi.useFakeTimers(); vi.setSystemTime(new Date('2026-01-01T12:00:00Z'));
    await act(async () => { fireEvent.click(refresh); });
    expect(screen.getByRole('alert')).toHaveTextContent('source_rate_limited'); expect(refresh).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Read Fictional engineer at Fictional North' })).toBeVisible();
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
    const refresh = await screen.findByRole('button', { name: 'Refresh Fictional North board' });
    state.source = { ...state.source, version: 4, health: 'rate_limited', next_allowed_at: null };
    await user.click(refresh);
    expect(await screen.findByText(/No finite permitted retry time is available/)).toBeVisible();
    expect(refresh).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Reload sources' }));
    expect(refresh).toBeDisabled();
    expect(state.submissions.filter(item => item.path.endsWith('/refresh'))).toHaveLength(1);
    expect(screen.getByRole('button', { name: 'Read Fictional engineer at Fictional North' })).toBeVisible();
  });
});

describe('bounded durable refresh observation and cancellation', () => {
  it('pauses while hidden, bounds visible polling, and cancels with the actual workflow version and stable retry identity', async () => {
    const { api, state } = server();
    render(<Harness api={api} />);
    const refresh = await screen.findByRole('button', { name: 'Refresh Fictional North board' });
    vi.useFakeTimers();
    await act(async () => { fireEvent.click(refresh); });
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
    expect(screen.getByRole('button', { name: 'Read Fictional engineer at Fictional North' })).toBeVisible();
  });

  it('reconciles a successful read into retained postings and distinguishes excluded prospects from normal published jobs', async () => {
    const user = userEvent.setup(); const { api, state } = server();
    render(<Harness api={api} />);
    await user.click(await screen.findByRole('button', { name: 'Refresh Fictional North board' }));
    state.postings = [savedPosting, { ...savedPosting, id: 'imported-posting', title: 'Published fictional designer', source_kind: 'greenhouse', source_config_id: 'source-one', external_id: 'greenhouse-42' }];
    state.workflow = { ...state.workflow, version: 8, state: 'succeeded', result: { source_id: 'source-one', policy_version: 2, fetched_at: '2026-01-02T12:00:00Z', posting_count: 1, new_posting_count: 1, changed_posting_count: 0, unchanged_posting_count: 0, excluded_prospect_count: 2, freshness: 'current' } };
    state.source = { ...state.source, last_success_at: '2026-01-02T12:00:00Z' };
    await user.click(screen.getByRole('button', { name: 'Check refresh task status' }));
    expect(await screen.findByRole('button', { name: 'Read Published fictional designer at Fictional North' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'Read Fictional engineer at Fictional North' })).toBeVisible();
    expect(screen.getByText(/Excluded prospect posts: 2/)).toBeVisible();
    expect(screen.getByText(/Published normal jobs: 1; new 1, changed 0, unchanged 0/)).toBeVisible();
    expect(screen.getByText(/Last successful read: 2026-01-02T12:00:00Z/)).toBeVisible();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Sign out guard' })).toBeEnabled());
  });

  it('recovers a saved source task and reconciles failure without replacing retained catalog with an empty state', async () => {
    const user = userEvent.setup(); const { api, state } = server();
    state.workflow = { ...state.workflow, state: 'permanent_failure', last_error_code: 'upstream_forbidden', result: { source_id: 'source-one', policy_version: 2, error_code: 'upstream_forbidden' } };
    render(<Harness api={api} />);
    await screen.findByRole('button', { name: 'Read Fictional engineer at Fictional North' });
    await user.click(screen.getByRole('button', { name: 'Load saved source refresh tasks' }));
    expect(screen.queryByRole('button', { name: 'Open source refresh task unrelated-task' })).toBeNull();
    state.catalogFailure = new ApiError(503, 'temporarily_unavailable');
    state.source = { ...state.source, health: 'degraded', last_error_code: 'upstream_forbidden' };
    await user.click(await screen.findByRole('button', { name: 'Open source refresh task source-task-one' }));
    expect(await screen.findByText(/Task ended, but the latest catalog or source health could not be loaded/)).toBeVisible();
    expect(screen.getByText(/Source task failure: upstream_forbidden/)).toBeVisible();
    expect(screen.getByText(/Health: degraded/)).toBeVisible();
    expect(screen.getByRole('button', { name: 'Read Fictional engineer at Fictional North' })).toBeVisible();
    expect(screen.queryByText(/^No retained postings/)).toBeNull();
  });

  it('disables the source immediately without deleting its catalog, and disabled parent controls retain the manual buffer', async () => {
    const user = userEvent.setup(); const { api, state } = server(); const { rerender } = render(<Harness api={api} />);
    await user.click(await screen.findByRole('button', { name: 'Disable Fictional North board' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Refresh Fictional North board' })).toBeDisabled());
    expect(state.submissions[0]?.version).toBe(3); expect((state.submissions[0]?.body as SourceWrite).enabled).toBe(false);
    expect(screen.getByRole('button', { name: 'Read Fictional engineer at Fictional North' })).toBeVisible();
    fillManual(); rerender(<Harness api={api} disabled />);
    expect(screen.getByRole('button', { name: 'Save manual posting' })).toBeDisabled();
    expect(screen.getByLabelText('Complete original posting text')).toHaveValue(text);
    const unload = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(unload); expect(unload.defaultPrevented).toBe(true);
  });
});
