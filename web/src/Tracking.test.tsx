// SPDX-License-Identifier: MIT OR Apache-2.0
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { webcrypto } from 'node:crypto';
import { Api, ApiError, type Profile } from './api';
import { App } from './App';
import { Tracking } from './Tracking';
import { MAX_ATTACHMENT_BYTES, stages, type ApplicationCounts, type ApplicationDetail, type Appointment, type StageEvent } from './tracking-api';

const timestamp = '2026-10-25T12:00:00Z';
function application(id = 'application-one', title = 'Engineer'): ApplicationDetail {
  return { id, title, company: 'Synthetic North', version: 3, attempt_no: 1, posting_id: `posting-${id}`, opportunity_id: `opportunity-${id}`, current_stage: 'interview', archived_at: null, created_at: timestamp, updated_at: timestamp, next_action: null, current_stage_event_id: 'event-interview', notes: [{ id: 'note-one', version: 1, body: 'Saved fictional note', created_at: timestamp, updated_at: timestamp }], contacts: [], attachments: [], appointments: [] };
}
const events: StageEvent[] = [
  { id: 'event-interview', sequence: 3, from_stage: 'applied', to_stage: 'interview', effective_at: '2026-10-01T12:00:00Z', recorded_at: timestamp, reason: 'Fictional interview', correction_of: null, affects_current_stage: true },
  { id: 'event-applied', sequence: 2, from_stage: 'preparing', to_stage: 'applied', effective_at: '2026-09-20T12:00:00Z', recorded_at: timestamp, reason: 'Owner reported', correction_of: null, affects_current_stage: true },
  { id: 'event-initial', sequence: 1, from_stage: null, to_stage: 'preparing', effective_at: '2026-09-19T12:00:00Z', recorded_at: timestamp, reason: '', correction_of: null, affects_current_stage: true },
];
function schedule(id = 'action-one', kind: Appointment['kind'] = 'interview'): Appointment {
  return { id, kind, version: 1, title: kind === 'interview' ? 'Fictional panel' : 'Fictional follow-up', starts_at: '2026-11-01T05:30:00Z', ends_at: '2026-11-01T06:45:00Z', local_start: '2026-11-01T01:30:00', local_end: '2026-11-01T01:45:00', timezone: 'America/New_York', display_timezone: 'America/New_York', display_start: '2026-11-01T01:30:00-04:00', display_end: '2026-11-01T01:45:00-05:00', duration_minutes: 75, status: 'scheduled', completed_at: null, cancelled_at: null, created_at: timestamp };
}
function countResponse(path: string): ApplicationCounts {
  const query = new URLSearchParams(path.split('?')[1]);
  const zero = Object.fromEntries(stages.map(stage => [stage, 0])) as ApplicationCounts['current_by_stage'];
  return { period_start: query.get('period_start')!, period_end: query.get('period_end')!, current_by_stage: { ...zero, interview: 7 }, archived_by_stage: { ...zero, rejected: 4 }, created_in_period: 2, stage_reached_in_period: { ...zero, applied: 3 }, query_elapsed_ms: 1 };
}
type Handler = (path: string, method: string, body: unknown, version: number | undefined, key: boolean | string) => Promise<unknown>;
function connected(overrides?: Handler, items = [application()]) {
  const api = new Api(); api.csrf = 'synthetic-csrf';
  const request = vi.spyOn(api, 'request').mockImplementation(async <T,>(path: string, method = 'GET', body?: unknown, version?: number, key: boolean | string = false) => {
    const overridden = await overrides?.(path, method, body, version, key); if (overridden !== undefined) return overridden as T;
    if (path === '/applications') return { items, next_cursor: null, query_elapsed_ms: 1, as_of: timestamp } as T;
    if (path.startsWith('/applications/counts?')) return countResponse(path) as T;
    if (path === '/applications/attention') return { items: [], query_elapsed_ms: 1, as_of: timestamp } as T;
    if (path.endsWith('/stage-events')) return { items: events, next_cursor: null } as T;
    if (method === 'GET' && items.some(item => path === `/applications/${item.id}`)) return items.find(item => path === `/applications/${item.id}`) as T;
    throw new ApiError(404, 'not_found');
  });
  return { api, request };
}
async function openFirst() { fireEvent.click(await screen.findByRole('button', { name: 'Open Engineer at Synthetic North' })); await screen.findByRole('heading', { name: 'Engineer at Synthetic North · attempt 1' }); }
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done; }); return { promise, resolve }; }
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('manual application buffers and selection boundaries', () => {
  it('retains an offline private note, fences selection and reuses only unchanged payload/version keys', async () => {
    const { api, request } = connected(async (path, method) => { if (path.endsWith('/notes/note-one') && method === 'PUT') throw new TypeError('offline'); }, [application(), application('application-two', 'Analyst')]);
    const dirty = vi.fn(); render(<Tracking api={api} onDirtyChange={dirty} />); await openFirst();
    fireEvent.click(screen.getByRole('button', { name: 'Edit private note 1' }));
    fireEvent.change(screen.getByLabelText(/Private note text/), { target: { value: 'Fictional retained draft <script>literal</script>' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save manual tracking change' }));
    const alert = await screen.findByRole('alert'); expect(alert).toHaveFocus(); expect(alert).toHaveTextContent('server could not be reached');
    expect(screen.getByLabelText(/Private note text/)).toHaveValue('Fictional retained draft <script>literal</script>'); expect(dirty).toHaveBeenLastCalledWith(true);
    fireEvent.click(screen.getByRole('button', { name: 'Save manual tracking change' }));
    await waitFor(() => expect(request.mock.calls.filter(call => call[1] === 'PUT')).toHaveLength(2));
    const unchanged = request.mock.calls.filter(call => call[1] === 'PUT'); expect(unchanged[0]?.[4]).toBe(unchanged[1]?.[4]);
    fireEvent.change(screen.getByLabelText(/Private note text/), { target: { value: 'Changed fictional retained draft' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save manual tracking change' }));
    await waitFor(() => expect(request.mock.calls.filter(call => call[1] === 'PUT')).toHaveLength(3));
    expect(request.mock.calls.filter(call => call[1] === 'PUT')[2]?.[4]).not.toBe(unchanged[0]?.[4]);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Open Analyst at Synthetic North' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Open Analyst at Synthetic North' }));
    expect(screen.getByRole('region', { name: 'Unsaved tracking selection' })).toBeVisible(); expect(screen.getByLabelText(/Private note text/)).toHaveValue('Changed fictional retained draft');
    expect(request.mock.calls.some(call => call[0] === '/applications/application-two')).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: 'Keep editing this attempt' }));
    fireEvent.click(screen.getByRole('button', { name: 'Open Analyst at Synthetic North' })); fireEvent.click(screen.getByRole('button', { name: 'Discard buffer and open selected application' }));
    expect(await screen.findByRole('heading', { name: 'Analyst at Synthetic North · attempt 1' })).toBeVisible(); expect(screen.queryByLabelText(/Private note text/)).not.toBeInTheDocument();
  });

  it('requires comparison, explicit rebase and fresh confirmation after 412 without overwriting a contact', async () => {
    let current = application(); let reads = 0; let writes = 0;
    const { api, request } = connected(async (path, method) => {
      if (path === '/applications/application-one' && method === 'GET') { reads++; return current; }
      if (path.endsWith('/contacts') && method === 'POST') { writes++; if (writes === 1) { current = { ...current, version: 4, contacts: [{ id: 'contact-server', version: 1, display_name: 'Saved peer contact', email: null, role_label: 'Recruiter', created_at: timestamp }] }; throw new ApiError(412, 'version_conflict'); } return { ...current, version: 5, contacts: [...current.contacts, { id: 'contact-new', version: 1, display_name: 'Retained contact', email: null, role_label: null, created_at: timestamp }] }; }
    });
    render(<Tracking api={api} />); await openFirst(); fireEvent.click(screen.getByRole('button', { name: 'Add contact' }));
    fireEvent.change(screen.getByLabelText('Contact name'), { target: { value: 'Retained contact' } }); fireEvent.click(screen.getByRole('button', { name: 'Save manual tracking change' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('newer revision'); expect(screen.getByLabelText('Contact name')).toHaveValue('Retained contact'); expect(screen.getByRole('button', { name: 'Save manual tracking change' })).toBeDisabled();
    fireEvent.click(screen.getAllByRole('button', { name: 'Load current application for comparison' })[0]!);
    expect(await screen.findByRole('heading', { name: 'Current saved application · version 4' })).toBeVisible();
    fireEvent.click(screen.getByText('Compare current saved notes, contacts and schedules')); expect(screen.getByText(/Saved peer contact/)).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Use current version and retain my tracking buffer' }));
    expect(screen.getByLabelText('Contact name')).toHaveValue('Retained contact'); expect(screen.getByRole('button', { name: 'Save manual tracking change' })).toBeDisabled();
    fireEvent.click(screen.getByLabelText('I compared the current saved version and reconfirm this exact retained change')); fireEvent.click(screen.getByRole('button', { name: 'Save manual tracking change' }));
    await waitFor(() => expect(screen.getByRole('status', { name: 'Tracking confirmation' })).toHaveTextContent('Manual tracking saved'));
    const calls = request.mock.calls.filter(call => call[0].endsWith('/contacts') && call[1] === 'POST'); expect(calls[0]?.[3]).toBe(3); expect(calls[1]?.[3]).toBe(4); expect(calls[0]?.[4]).not.toBe(calls[1]?.[4]);
    expect(reads).toBe(2); expect(screen.getByText(/Retained contact · Email unknown/)).toBeVisible();
  });

  it('ignores late details and history for an earlier selection without overwriting the selected draft', async () => {
    const firstDetail = deferred<ApplicationDetail>(); const firstHistory = deferred<{ items: StageEvent[]; next_cursor: null }>();
    const { api } = connected(async (path, method) => {
      if (path === '/applications/application-one' && method === 'GET') return firstDetail.promise;
      if (path === '/applications/application-one/stage-events') return firstHistory.promise;
    }, [application(), application('application-two', 'Analyst')]);
    render(<Tracking api={api} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Open Engineer at Synthetic North' }));
    fireEvent.click(screen.getByRole('button', { name: 'Open Analyst at Synthetic North' }));
    await screen.findByRole('heading', { name: 'Analyst at Synthetic North · attempt 1' });
    fireEvent.click(screen.getByRole('button', { name: 'Add private note' })); fireEvent.change(screen.getByLabelText(/Private note text/), { target: { value: 'Second selection draft' } });
    await act(async () => { firstDetail.resolve({ ...application(), notes: [] }); firstHistory.resolve({ items: [{ ...events[0]!, reason: 'Late first history must not appear' }], next_cursor: null }); });
    expect(screen.getByRole('heading', { name: 'Analyst at Synthetic North · attempt 1' })).toBeVisible(); expect(screen.getByLabelText(/Private note text/)).toHaveValue('Second selection draft'); expect(screen.queryByText('Late first history must not appear')).not.toBeInTheDocument();
  });

  it('does not overwrite a newly opened buffer when an earlier clean refresh resolves', async () => {
    const pending = deferred<ApplicationDetail>(); let reads = 0;
    const { api } = connected(async (path, method) => { if (path === '/applications/application-one' && method === 'GET') { reads++; return reads === 1 ? application() : pending.promise; } });
    render(<Tracking api={api} />); await openFirst(); fireEvent.click(screen.getByRole('button', { name: 'Refresh selected application' }));
    fireEvent.click(screen.getByRole('button', { name: 'Edit private note 1' })); fireEvent.change(screen.getByLabelText(/Private note text/), { target: { value: 'New local buffer during GET' } });
    await act(async () => { pending.resolve({ ...application(), version: 8, notes: [{ id: 'note-one', version: 2, body: 'New server text', created_at: timestamp, updated_at: timestamp }] }); });
    expect(screen.getByLabelText(/Private note text/)).toHaveValue('New local buffer during GET'); expect(screen.getByRole('heading', { name: 'Current saved application · version 8' })).toBeVisible(); expect(screen.getByText(/saved version 3/)).toBeVisible();
  });

  it('keeps the newer comparison when two concurrent comparison reads resolve out of order', async () => {
    const older = deferred<ApplicationDetail>(); const newer = deferred<ApplicationDetail>(); let reads = 0;
    const { api } = connected(async (path, method) => {
      if (path === '/applications/application-one' && method === 'GET') { reads++; return reads === 1 ? application() : reads === 2 ? older.promise : newer.promise; }
    });
    render(<Tracking api={api} />); await openFirst(); fireEvent.click(screen.getByRole('button', { name: 'Add private note' })); fireEvent.change(screen.getByLabelText(/Private note text/), { target: { value: 'Synthetic buffer remains unchanged' } });
    fireEvent.click(screen.getAllByRole('button', { name: 'Load current application for comparison' })[0]!); fireEvent.click(screen.getAllByRole('button', { name: 'Load current application for comparison' })[0]!);
    await act(async () => { newer.resolve({ ...application(), version: 8 }); }); await act(async () => { older.resolve({ ...application(), version: 4 }); });
    expect(screen.getByRole('heading', { name: 'Current saved application · version 8' })).toBeVisible(); expect(screen.queryByRole('heading', { name: 'Current saved application · version 4' })).not.toBeInTheDocument(); expect(screen.getByLabelText(/Private note text/)).toHaveValue('Synthetic buffer remains unchanged'); expect(screen.getByRole('button', { name: 'Save manual tracking change' })).toBeDisabled();
  });

  it('fences authenticated sign out and preserves a note across theme changes until explicit discard', async () => {
    const { api, request } = connected(async (path, method) => {
      if (path === '/session') return { owner_id: 'synthetic-owner', login_name: 'mira', csrf_token: 'csrf', expires_at: timestamp };
      if (path === '/profile' && method === 'GET') return { id: 'profile', version: 1, current_revision: { id: 'revision', revision_no: 1, content_sha256: 'a'.repeat(64), created_at: timestamp, author_owner_id: 'synthetic-owner', reason: '', snapshot: { display_name: 'Mira', headline: '', claim_revision_ids: [] }, claims: [] }, evidence: [], quality_warnings: [] } satisfies Profile;
      if (path === '/postings') return { items: [], next_cursor: null, as_of: timestamp, query_elapsed_ms: 1 };
      if (path === '/sources') return { items: [] };
    });
    render(<App api={api} />); await openFirst(); fireEvent.click(screen.getByRole('button', { name: 'Add private note' })); fireEvent.change(screen.getByLabelText(/Private note text/), { target: { value: 'Fictional logout-fenced draft' } });
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeDisabled(); fireEvent.change(screen.getByLabelText('Theme'), { target: { value: 'dark' } }); expect(screen.getByLabelText(/Private note text/)).toHaveValue('Fictional logout-fenced draft');
    fireEvent.click(screen.getByRole('button', { name: 'Sign out' })); expect(request.mock.calls.some(call => call[0] === '/session' && call[1] === 'DELETE')).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: 'Discard tracking buffer' })); expect(screen.getByRole('button', { name: 'Sign out' })).toBeEnabled();
  });
});

describe('tracking history, totals and explicit attempt identity', () => {
  it('corrects an arbitrary earlier effective date while retaining Interview and original recorded events', async () => {
    let saved = application(); let history = [...events];
    const { api, request } = connected(async (path, method) => {
      if (path.endsWith('/stage-events')) return { items: history, next_cursor: null };
      if (path.endsWith('/stage') && method === 'POST') { saved = { ...saved, version: 4 }; history = [{ ...events[1]!, id: 'correction-new', sequence: 4, effective_at: '2026-10-02T12:00:00Z', reason: 'Correct fictional applied date', correction_of: 'event-applied', affects_current_stage: false }, ...history]; return saved; }
    });
    render(<Tracking api={api} />); await openFirst(); await screen.findByRole('button', { name: 'Correct event 2' }); fireEvent.click(screen.getByRole('button', { name: 'Change stage or reopen same attempt' }));
    fireEvent.change(screen.getByLabelText('Stage event to correct'), { target: { value: 'event-applied' } });
    expect(screen.getByLabelText('Recorded stage')).toHaveValue('applied'); expect(screen.getByText('This correction changes historical reporting only, not the current stage or pending actions.')).toBeVisible();
    fireEvent.change(screen.getByLabelText('Effective date and time (UTC)'), { target: { value: '2026-10-02T12:00' } }); fireEvent.change(screen.getByLabelText(/Stage reason/), { target: { value: 'Correct fictional applied date' } });
    fireEvent.click(screen.getByLabelText('I confirm this exact manual tracking action; it sends nothing')); fireEvent.click(screen.getByRole('button', { name: 'Save manual tracking change' }));
    await waitFor(() => expect(screen.queryByLabelText('Recorded stage')).not.toBeInTheDocument());
    expect(screen.getByRole('region', { name: 'Engineer at Synthetic North · attempt 1' })).toHaveTextContent('Interview · Active · saved version 4.'); expect(await screen.findByText('Correct fictional applied date')).toBeVisible(); expect(screen.getByText('Owner reported')).toBeVisible();
    const mutation = request.mock.calls.find(call => call[0].endsWith('/stage') && call[1] === 'POST'); expect(mutation?.[2]).toEqual({ to_stage: 'applied', effective_at: '2026-10-02T12:00:00Z', correction_of: 'event-applied', reason: 'Correct fictional applied date', retain_follow_up_ids: [] });
    expect(request.mock.calls.filter(call => call[1] === 'POST')).toHaveLength(1); await waitFor(() => expect(screen.getByRole('heading', { name: 'Engineer at Synthetic North · attempt 1' })).toHaveFocus());
  });

  it('retains prior counts as stale on failure instead of presenting zero and keeps filters separate from totals', async () => {
    let failed = false;
    const { api, request } = connected(async (path) => {
      if (path.startsWith('/applications/counts?') && failed) throw new TypeError('offline');
      if (path.startsWith('/applications?')) return { items: [], next_cursor: null, query_elapsed_ms: 1, as_of: timestamp };
    });
    render(<Tracking api={api} />); fireEvent.click(screen.getByText('All-owner counts and period activity'));
    expect(await screen.findByText('Active current: 7; archived current: 0; reached in period: 0')).toBeVisible();
    fireEvent.change(screen.getByLabelText('Filter stage'), { target: { value: 'rejected' } }); fireEvent.change(screen.getByLabelText('Archive scope'), { target: { value: 'all' } }); fireEvent.click(screen.getByRole('button', { name: 'Apply application filters' }));
    expect(await screen.findByText(/No applications in this loaded scope/)).toBeVisible(); expect(screen.getByText('Active current: 7; archived current: 0; reached in period: 0')).toBeVisible();
    failed = true; fireEvent.click(screen.getByRole('button', { name: 'Refresh all-owner counts' })); expect(await screen.findByText(/Counts unavailable, not zero/)).toHaveTextContent('stale'); expect(screen.getByText('Active current: 7; archived current: 0; reached in period: 0')).toBeVisible();
    expect(request.mock.calls.some(call => call[0] === '/applications?stage=rejected&archived=all')).toBe(true);
  });

  it('loads successive cursor pages and exposes only a labelled loaded-subset board', async () => {
    const { api, request } = connected(async path => {
      if (path === '/applications') return { items: [application()], next_cursor: 'opaque-page-two', query_elapsed_ms: 1, as_of: timestamp };
      if (path === '/applications?cursor=opaque-page-two') return { items: [application('application-two', 'Analyst')], next_cursor: null, query_elapsed_ms: 1, as_of: timestamp };
    });
    render(<Tracking api={api} />); fireEvent.click(await screen.findByRole('button', { name: 'Load more applications' })); expect(await screen.findByRole('button', { name: 'Open Analyst at Synthetic North' })).toBeVisible();
    expect(screen.getByText(/2 loaded applications/)).toHaveTextContent('not an all-owner total'); fireEvent.click(screen.getByLabelText('Group loaded subset by stage (not a complete board)')); expect(screen.getByLabelText('Stage groups of loaded subset')).toHaveTextContent('Interview · 2 loaded'); expect(request.mock.calls.some(call => call[0] === '/applications?cursor=opaque-page-two')).toBe(true);
  });

  it('creates from a saved posting, distinguishes repeat from reopen, and preserves closed outcomes when archived', async () => {
    const { api, request } = connected(async (path, method) => {
      if (path === '/postings?disposition=saved') return { items: [{ id: 'saved-one', title: 'Engineer', company: 'Synthetic North' }], next_cursor: null, query_elapsed_ms: 1, as_of: timestamp };
      if (path === '/applications' && method === 'POST') return { ...application(), created: false };
      if (path.endsWith('/repeat') && method === 'POST') return { ...application('repeat-two'), attempt_no: 2, current_stage: 'preparing', version: 1, created: true, notes: [] };
      if (path === '/applications/repeat-two/stage' && method === 'POST') return { ...application('repeat-two'), attempt_no: 2, current_stage: 'rejected', version: 2, notes: [] };
      if (path === '/applications/repeat-two/archive' && method === 'POST') return { ...application('repeat-two'), attempt_no: 2, current_stage: 'rejected', archived_at: timestamp, version: 3, notes: [] };
    });
    render(<Tracking api={api} />); fireEvent.click(screen.getByText('Track a saved posting manually')); fireEvent.click(screen.getByRole('button', { name: 'Load saved postings for tracking' }));
    fireEvent.change(await screen.findByLabelText('Saved posting to track'), { target: { value: 'saved-one' } }); fireEvent.click(screen.getByRole('button', { name: 'Track selected posting manually' })); await screen.findByRole('heading', { name: 'Engineer at Synthetic North · attempt 1' });
    expect(screen.getByRole('status', { name: 'Tracking confirmation' })).toHaveTextContent('Existing latest attempt'); expect(request.mock.calls.find(call => call[1] === 'POST')?.[3]).toBeUndefined();
    fireEvent.click(screen.getByRole('button', { name: 'Start a separate repeat attempt' })); expect(screen.getByText(/This is not a same-attempt reopen/)).toBeVisible(); expect(screen.getByRole('button', { name: 'Save manual tracking change' })).toBeDisabled();
    fireEvent.click(screen.getByLabelText('I confirm this exact manual tracking action; it sends nothing')); fireEvent.click(screen.getByRole('button', { name: 'Save manual tracking change' }));
    await screen.findByRole('heading', { name: 'Engineer at Synthetic North · attempt 2' }); expect(screen.getByRole('region', { name: 'Engineer at Synthetic North · attempt 2' })).toHaveTextContent('Preparing · Active');
    fireEvent.click(screen.getByRole('button', { name: 'Change stage or reopen same attempt' })); fireEvent.change(screen.getByLabelText('Recorded stage'), { target: { value: 'rejected' } }); fireEvent.click(screen.getByLabelText('I confirm this exact manual tracking action; it sends nothing')); fireEvent.click(screen.getByRole('button', { name: 'Save manual tracking change' }));
    await waitFor(() => expect(screen.queryByLabelText('Recorded stage')).not.toBeInTheDocument()); fireEvent.click(screen.getByRole('button', { name: 'Archive attempt' })); fireEvent.click(screen.getByLabelText('I confirm this exact manual tracking action; it sends nothing')); fireEvent.click(screen.getByRole('button', { name: 'Save manual tracking change' }));
    expect(await screen.findByRole('button', { name: 'Reactivate archived attempt' })).toBeVisible(); expect(screen.getByRole('region', { name: 'Engineer at Synthetic North · attempt 2' })).toHaveTextContent('Rejected · Archived');
    const writes = request.mock.calls.filter(call => call[1] === 'POST'); expect(writes.map(call => call[0])).toEqual(['/applications', '/applications/application-one/repeat', '/applications/repeat-two/stage', '/applications/repeat-two/archive']); expect(writes[1]?.[2]).toEqual({ confirmed: true });
  });

  it('loads older history without truncating it and allows selecting an earlier event for effective-date correction', async () => {
    const { api } = connected(async path => {
      if (path === '/applications/application-one/stage-events') return { items: [events[0]], next_cursor: 3 };
      if (path === '/applications/application-one/stage-events?before=3') return { items: events.slice(1), next_cursor: null };
    });
    render(<Tracking api={api} />); await openFirst(); expect(screen.queryByRole('button', { name: 'Correct event 2' })).not.toBeInTheDocument();
    fireEvent.click(await screen.findByRole('button', { name: 'Load older stage events' })); fireEvent.click(await screen.findByRole('button', { name: 'Correct event 2' }));
    expect(screen.getByLabelText('Stage event to correct')).toHaveValue('event-applied'); expect(screen.getByLabelText('Recorded stage')).toHaveValue('applied'); expect(screen.getByLabelText('Effective date and time (UTC)')).toHaveValue('2026-09-20T12:00'); expect(screen.getByText('Owner reported')).toBeVisible(); expect(screen.getByRole('button', { name: 'Correct event 3' })).toBeDisabled();
  });

  it('distinguishes server-due actions from upcoming interviews and keeps stale reminders after refresh failure', async () => {
    let failed = false;
    const due = { ...schedule('follow-due', 'follow_up'), starts_at: '2026-10-24T12:00:00Z', display_start: '2026-10-24T12:00:00Z', timezone: 'UTC', display_timezone: 'UTC' };
    const upcoming = { ...schedule('interview-upcoming'), starts_at: '2026-10-27T12:00:00Z', display_start: '2026-10-27T12:00:00Z' };
    const { api } = connected(async path => {
      if (path === '/applications/attention') { if (failed) throw new TypeError('offline'); return { items: [{ application_id: 'application-one', title: 'Engineer', company: 'Synthetic North', appointment: due }, { application_id: 'application-two', title: 'Analyst', company: 'Synthetic North', appointment: upcoming }], query_elapsed_ms: 1, as_of: timestamp }; }
    });
    render(<Tracking api={api} />); fireEvent.click(screen.getByText('Attention: due actions and upcoming interviews'));
    expect(await screen.findByText('Due action')).toBeVisible(); expect(screen.getByText('Upcoming interview')).toBeVisible(); failed = true; fireEvent.click(screen.getByRole('button', { name: 'Refresh attention list' }));
    expect(await screen.findByText(/Attention unavailable, not empty/)).toHaveTextContent('stale'); expect(screen.getByText('Due action')).toBeVisible(); expect(screen.getByText('Upcoming interview')).toBeVisible();
  });
});

describe('local time, closure and inert attachment boundaries', () => {
  it('retains full start/end and independent overlap choices on a DST validation error, then displays the accepted interval', async () => {
    let writes = 0;
    const { api, request } = connected(async (path, method) => { if (path.endsWith('/appointments') && method === 'POST') { writes++; if (writes === 1) throw new ApiError(422, 'ambiguous_local_time'); return { ...application(), version: 4, appointments: [schedule()] }; } });
    render(<Tracking api={api} />); await openFirst(); fireEvent.click(screen.getByRole('button', { name: 'Schedule local interview' }));
    fireEvent.change(screen.getByLabelText('Local action title'), { target: { value: 'Fictional panel' } }); fireEvent.change(screen.getByLabelText('Local start date and time'), { target: { value: '2026-11-01T01:30' } }); fireEvent.change(screen.getByLabelText('Local end date and time (required)'), { target: { value: '2026-11-01T01:45' } }); fireEvent.change(screen.getByLabelText('Original IANA timezone'), { target: { value: 'America/New_York' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save manual tracking change' })); expect(await screen.findByRole('alert')).toHaveTextContent('ambiguous_local_time'); expect(screen.getByLabelText('Local start date and time')).toHaveValue('2026-11-01T01:30'); expect(screen.getByLabelText('Local end date and time (required)')).toHaveValue('2026-11-01T01:45');
    fireEvent.change(screen.getByLabelText('Start UTC offset in minutes (optional unless ambiguous)'), { target: { value: '-240' } }); fireEvent.change(screen.getByLabelText('End UTC offset in minutes (independent)'), { target: { value: '-300' } }); fireEvent.click(screen.getByRole('button', { name: 'Save manual tracking change' }));
    expect(await screen.findByText(/75 elapsed minutes/)).toBeVisible();
    const calls = request.mock.calls.filter(call => call[0].endsWith('/appointments') && call[1] === 'POST'); expect(calls[0]?.[2]).not.toHaveProperty('utc_offset_minutes'); expect(calls[1]?.[2]).toEqual({ kind: 'interview', title: 'Fictional panel', local_start: '2026-11-01T01:30:00', local_end: '2026-11-01T01:45:00', timezone: 'America/New_York', utc_offset_minutes: -240, end_utc_offset_minutes: -300 }); expect(calls[0]?.[4]).not.toBe(calls[1]?.[4]);
  });

  it('uses a display-only command and retains original intent and UTC instants after timezone change', async () => {
    const original = schedule();
    const { api, request } = connected(async (path, method) => {
      if (path === '/applications/application-one' && method === 'GET') return { ...application(), appointments: [original] };
      if (path.endsWith('/timezone') && method === 'POST') return { ...application(), version: 4, appointments: [{ ...original, display_timezone: 'UTC', display_start: original.starts_at, display_end: original.ends_at }] };
    });
    render(<Tracking api={api} />); await openFirst(); fireEvent.click(screen.getByRole('button', { name: 'Display timezone for Fictional panel' })); fireEvent.change(screen.getByLabelText('Display IANA timezone'), { target: { value: 'UTC' } }); fireEvent.click(screen.getByRole('button', { name: 'Save manual tracking change' }));
    const displayedStart = await screen.findByText(original.starts_at, { selector: 'time' });
    expect(displayedStart).toBeVisible();
    expect(displayedStart).toHaveAttribute('datetime', original.starts_at);
    expect(screen.getByText(original.ends_at!, { selector: 'time' })).toBeVisible();
    expect(screen.getByText(/Original intent: 2026-11-01T01:30:00 to 2026-11-01T01:45:00 in America\/New_York/)).toHaveTextContent('UTC: 2026-11-01T05:30:00Z');
    const call = request.mock.calls.find(item => item[1] === 'POST'); expect(call?.[0]).toBe('/applications/application-one/appointments/action-one/timezone'); expect(call?.[2]).toEqual({ timezone: 'UTC' });
  });

  it('offers explicit distinct follow-up retention on closure and retains cancellation/completion controls', async () => {
    const follow = schedule('follow-one', 'follow_up'); const interview = schedule();
    const { api, request } = connected(async (path, method) => {
      if (path === '/applications/application-one' && method === 'GET') return { ...application(), appointments: [follow, interview] };
      if (path.endsWith('/stage') && method === 'POST') return { ...application(), version: 4, current_stage: 'rejected', appointments: [follow, { ...interview, status: 'cancelled', cancelled_at: timestamp }] };
    });
    render(<Tracking api={api} />); await openFirst(); fireEvent.click(screen.getByRole('button', { name: 'Change stage or reopen same attempt' })); fireEvent.change(screen.getByLabelText('Recorded stage'), { target: { value: 'rejected' } });
    expect(screen.getByText(/Outstanding interviews and follow-ups will be cancelled/)).toBeVisible(); expect(screen.queryByLabelText('Retain follow-up Fictional panel')).not.toBeInTheDocument(); fireEvent.click(screen.getByLabelText('Retain follow-up Fictional follow-up')); fireEvent.click(screen.getByLabelText('I confirm this exact manual tracking action; it sends nothing')); fireEvent.click(screen.getByRole('button', { name: 'Save manual tracking change' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Schedule local interview' })).toBeDisabled()); expect(screen.getByRole('button', { name: 'Complete action Fictional follow-up' })).toBeEnabled(); expect(screen.getByRole('button', { name: 'Reactivate action Fictional panel' })).toBeDisabled();
    expect(request.mock.calls.find(call => call[0].endsWith('/stage') && call[1] === 'POST')?.[2]).toHaveProperty('retain_follow_up_ids', ['follow-one']);
  });

  it('keeps rejected attachment selections, uses authenticated opaque downloads and never renders hostile text as markup', async () => {
    const hostile = '<svg onload=alert(1)>.txt';
    const detail = { ...application(), attachments: [{ id: 'opaque-attachment', display_name: hostile, byte_length: 25, sha256: 'b'.repeat(64), media_type: 'application/octet-stream' as const, created_at: timestamp }] };
    const { api } = connected(async (path, method) => { if (path === '/applications/application-one' && method === 'GET') return detail; });
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 'unsafe_attachment_filename' }), { status: 422, headers: { 'Content-Type': 'application/json' } })); vi.stubGlobal('fetch', fetcher);
    vi.stubGlobal('crypto', webcrypto);
    render(<Tracking api={api} />); await openFirst(); const link = screen.getByRole('link', { name: /opaque binary download, 25 bytes/ }); expect(link).toHaveAttribute('href', '/api/v1/applications/application-one/attachments/opaque-attachment/download'); expect(link).toHaveAttribute('download'); expect(link.querySelector('svg')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Add inert attachment' })); const file = new File(['<svg onload=alert(1)>'], hostile, { type: 'text/plain' }); fireEvent.change(screen.getByLabelText('Attachment file'), { target: { files: [file] } }); fireEvent.click(screen.getByRole('button', { name: 'Save manual tracking change' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('unsafe_attachment_filename'); expect(screen.getByText(/Selected:/)).toHaveTextContent(hostile); expect(screen.queryByText('Manual tracking saved.')).not.toBeInTheDocument();
    const first = fetcher.mock.calls[0] as [string, RequestInit]; expect(first[0]).toContain(encodeURIComponent(hostile)); expect(first[1].credentials).toBe('same-origin'); expect(first[1].body).toBe(file); expect(first[1].headers).toMatchObject({ 'Content-Type': 'application/octet-stream', 'X-CSRF-Token': 'synthetic-csrf', 'If-Match': '"3"' });
    fireEvent.click(screen.getByRole('button', { name: 'Save manual tracking change' })); await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2)); const second = fetcher.mock.calls[1] as [string, RequestInit]; expect((first[1].headers as Record<string, string>)['Idempotency-Key']).toBe((second[1].headers as Record<string, string>)['Idempotency-Key']);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save manual tracking change' })).toBeEnabled());
    fireEvent.change(screen.getByLabelText('Attachment file'), { target: { files: [new File(['<svg onload=alert(1)>'], hostile, { type: 'text/plain' })] } }); fireEvent.click(screen.getByRole('button', { name: 'Save manual tracking change' }));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(3)); const identical = fetcher.mock.calls[2] as [string, RequestInit]; expect((identical[1].headers as Record<string, string>)['Idempotency-Key']).toBe((first[1].headers as Record<string, string>)['Idempotency-Key']);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save manual tracking change' })).toBeEnabled());
    fireEvent.change(screen.getByLabelText('Attachment file'), { target: { files: [new File(['changed literal bytes'], hostile, { type: 'text/plain' })] } }); fireEvent.click(screen.getByRole('button', { name: 'Save manual tracking change' }));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(4)); const changed = fetcher.mock.calls[3] as [string, RequestInit]; expect((changed[1].headers as Record<string, string>)['Idempotency-Key']).not.toBe((first[1].headers as Record<string, string>)['Idempotency-Key']);
  });

  it('rejects empty and oversized attachment files before transport without losing the application', async () => {
    const { api } = connected(); const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher); render(<Tracking api={api} />); await openFirst(); fireEvent.click(screen.getByRole('button', { name: 'Add inert attachment' }));
    fireEvent.change(screen.getByLabelText('Attachment file'), { target: { files: [new File([], 'empty.txt')] } }); fireEvent.click(screen.getByRole('button', { name: 'Save manual tracking change' })); expect(await screen.findByRole('alert')).toHaveTextContent('nonempty');
    const large = new File(['synthetic'], 'large.txt'); Object.defineProperty(large, 'size', { value: MAX_ATTACHMENT_BYTES + 1 }); fireEvent.change(screen.getByLabelText('Attachment file'), { target: { files: [large] } }); fireEvent.click(screen.getByRole('button', { name: 'Save manual tracking change' }));
    expect(fetcher).not.toHaveBeenCalled(); expect(screen.getByRole('heading', { name: 'Engineer at Synthetic North · attempt 1' })).toBeVisible();
  });

  it('retains a failed explicit completion action until a successful command without claiming completion early', async () => {
    const original = schedule(); let completed = false;
    const { api, request } = connected(async (path, method) => {
      if (path === '/applications/application-one' && method === 'GET') return { ...application(), appointments: [original] };
      if (path.endsWith('/status') && method === 'POST') { if (!completed) throw new TypeError('offline'); return { ...application(), version: 4, appointments: [{ ...original, status: 'completed', completed_at: timestamp }] }; }
    });
    render(<Tracking api={api} />); await openFirst(); fireEvent.click(screen.getByRole('button', { name: 'Complete action Fictional panel' })); fireEvent.click(screen.getByLabelText('I confirm this exact manual tracking action; it sends nothing')); fireEvent.click(screen.getByRole('button', { name: 'Save manual tracking change' })); expect(await screen.findByRole('alert')).toHaveTextContent('server could not be reached');
    expect(screen.getByRole('region', { name: 'Confirm action status' })).toHaveTextContent('completed'); expect(screen.getByText('Fictional panel', { selector: 'strong' }).closest('li')).toHaveTextContent('scheduled');
    completed = true; fireEvent.click(screen.getByRole('button', { name: 'Save manual tracking change' })); expect(await screen.findByText(`Completed: ${timestamp}`)).toBeVisible();
    const calls = request.mock.calls.filter(call => call[0].endsWith('/status')); expect(calls[0]?.[4]).toBe(calls[1]?.[4]); expect(calls[1]?.[2]).toEqual({ status: 'completed' });
  });

  it.each([
    ['nonexistent_local_time', '2026-03-08T02:30', '2026-03-08T03:30', ''],
    ['invalid_utc_offset', '2026-11-01T01:30', '2026-11-01T01:45', '-120'],
    ['invalid_appointment_interval', '2026-10-25T23:30', '2026-10-25T00:30', ''],
  ])('retains both local endpoints and the explicit zone after %s', async (code, start, end, offset) => {
    const { api } = connected(async (path, method) => { if (path.endsWith('/appointments') && method === 'POST') throw new ApiError(422, code); });
    render(<Tracking api={api} />); await openFirst(); fireEvent.click(screen.getByRole('button', { name: 'Schedule local interview' }));
    fireEvent.change(screen.getByLabelText('Local action title'), { target: { value: 'Fictional interval requiring correction' } }); fireEvent.change(screen.getByLabelText('Local start date and time'), { target: { value: start } }); fireEvent.change(screen.getByLabelText('Local end date and time (required)'), { target: { value: end } }); fireEvent.change(screen.getByLabelText('Original IANA timezone'), { target: { value: 'America/New_York' } }); fireEvent.change(screen.getByLabelText('Start UTC offset in minutes (optional unless ambiguous)'), { target: { value: offset } });
    fireEvent.click(screen.getByRole('button', { name: 'Save manual tracking change' })); expect(await screen.findByRole('alert')).toHaveTextContent(code);
    expect(screen.getByLabelText('Local start date and time')).toHaveValue(start); expect(screen.getByLabelText('Local end date and time (required)')).toHaveValue(end); expect(screen.getByLabelText('Original IANA timezone')).toHaveValue('America/New_York');
    expect(screen.getByRole('status', { name: 'Tracking confirmation' })).not.toHaveTextContent('saved');
  });

  it('retains an edited original schedule offline and restores persisted values only after deliberate discard', async () => {
    const original = schedule();
    const { api } = connected(async (path, method) => {
      if (path === '/applications/application-one' && method === 'GET') return { ...application(), appointments: [original] };
      if (path.endsWith('/appointments/action-one') && method === 'PUT') throw new TypeError('offline');
    });
    render(<Tracking api={api} />); await openFirst(); fireEvent.click(screen.getByRole('button', { name: 'Edit schedule Fictional panel' }));
    fireEvent.change(screen.getByLabelText('Local start date and time'), { target: { value: '2026-11-02T23:30' } }); fireEvent.change(screen.getByLabelText('Local end date and time (required)'), { target: { value: '2026-11-03T00:30' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save manual tracking change' })); expect(await screen.findByRole('alert')).toHaveTextContent('server could not be reached');
    expect(screen.getByLabelText('Local start date and time')).toHaveValue('2026-11-02T23:30'); expect(screen.getByLabelText('Local end date and time (required)')).toHaveValue('2026-11-03T00:30');
    fireEvent.click(screen.getByRole('button', { name: 'Discard tracking buffer' })); expect(screen.getByText(/Original intent: 2026-11-01T01:30:00/)).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Edit schedule Fictional panel' })); expect(screen.getByLabelText('Local start date and time')).toHaveValue('2026-11-01T01:30');
  });
});
