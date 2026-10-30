// SPDX-License-Identifier: MIT OR Apache-2.0
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent, { type UserEvent } from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Tasks, type TasksProps } from './Tasks';
import { Api, ApiError, type ActionApproval, type AttemptEvent, type Session, type Workflow, type WorkflowState, type WorkflowStep } from './api';

const session: Session = { owner_id: 'owner', login_name: 'mira', csrf_token: 'csrf', expires_at: '2030-01-01T00:00:00Z' };
const timestamp = '2026-10-25T12:00:00Z';
function task(overrides: Partial<Workflow> = {}): Workflow {
  return {
    id: 'task-one', kind: 'document_generate', version: 4, state: 'running', attempt: 1, max_attempts: 3,
    last_error_code: null, result: null, created_at: timestamp, updated_at: timestamp, deadline_at: '2030-01-01T00:00:00Z',
    next_attempt_at: null, cancel_requested_at: null, lease_expires_at: '2026-10-25T12:01:00Z', last_heartbeat_at: timestamp,
    queue_age_seconds: null, remaining_attempts: 2, cancelable: true, retryable: false, retry_block_reason: 'state_not_retryable', delivery_state: 'not_attempted',
    cancellation: { requested: false, local_fence_settled: false, provider_stopped: null, completed_effects_preserved: true, reason: null },
    usage: { status: 'not_applicable', model_runs: 0, inference_post_attempts: 0, inference_post_limit: null, input_tokens: null, output_tokens: null, token_budget: 0, tokens_used: 0, cost_budget_microunits: 0, cost_used_microunits: null, currency: 'USD' },
    steps: { items: [], next_cursor: null }, attempt_events: { items: [], next_cursor: null }, approval: null, ...overrides,
  };
}
function prepared(overrides: Partial<ActionApproval> = {}): ActionApproval {
  return {
    id: 'approval-one', workflow_run_id: 'task-one', action_kind: 'hosted_egress', version: 1, applicable: true,
    reason: 'approval_required', capability_available: true, confirmation_phrase: null, payload_sha256: 'a'.repeat(64), destination_sha256: 'b'.repeat(64),
    revision_id: 'revision-one', target_version: 7, expires_at: '2030-01-01T00:00:00Z', approved_at: null, consumed_at: null,
    intent: { destination: { provider: 'synthetic-disabled-test-provider', model: 'bounded-test' }, projection: { text: '<script>privateReview()</script>' }, token_budget: 500, cost_budget_microunits: 200, workspace_version: 7 }, ...overrides,
  };
}
const checkpoint: WorkflowStep = { id: 'step-new', step_key: 'validated_source', attempt: 1, fence: 3, state: 'completed', delivery_state: 'not_attempted', error_code: null, created_at: timestamp, completed_at: timestamp };
const attempt: AttemptEvent = { id: 'event-new', attempt: 1, fence: 3, event: 'claimed', error_code: null, created_at: timestamp };
type Handler = (path: string, method: string, body: unknown, version: number | undefined, key: boolean | string) => Promise<unknown>;
function server(initial = task(), handler?: Handler) {
  const api = new Api();
  const state = { workflows: { [initial.id]: initial } as Record<string, Workflow>, approval: null as ActionApproval | null, failure: null as Error | null, detailWaits: {} as Record<string, Promise<void>>, listCursor: null as string | null };
  const request = vi.spyOn(api, 'request').mockImplementation(async <T,>(path: string, method = 'GET', body?: unknown, version?: number, key: boolean | string = false) => {
    if (handler) { const value = await handler(path, method, body, version, key); if (value !== undefined) return value as T; }
    const url = new URL(path, 'http://bonafides.local');
    if (url.pathname === '/workflows') return { items: Object.values(state.workflows).filter(item => !url.searchParams.get('state') || item.state === url.searchParams.get('state')), next_cursor: state.listCursor } as T;
    const id = url.pathname.split('/')[2] ?? ''; const live = state.workflows[id];
    if (!live) throw new ApiError(404, 'workflow_not_found');
    if (url.pathname.endsWith('/approval') && method === 'GET') { if (!state.approval) throw new ApiError(404, 'approval_not_found'); return structuredClone(state.approval) as T; }
    if (url.pathname.endsWith('/cancel')) {
      const value = task({ ...live, version: live.version + 1, state: 'cancelled', cancelable: false, cancel_requested_at: timestamp, cancellation: { requested: true, local_fence_settled: true, provider_stopped: null, completed_effects_preserved: true, reason: 'local_publication_revoked_provider_may_continue' } });
      state.workflows[id] = value; return value as T;
    }
    if (url.pathname.endsWith('/retry')) { state.workflows[id] = { ...live, version: live.version + 1, state: 'pending', retryable: false }; return state.workflows[id] as T; }
    if (url.pathname.endsWith('/approval/approve')) { state.workflows[id] = { ...live, version: live.version + 1, state: 'pending' }; if (state.approval) state.approval.approved_at = timestamp; return state.workflows[id] as T; }
    if (url.pathname === `/workflows/${id}`) {
      const snapshot = structuredClone(live); if (state.detailWaits[id]) await state.detailWaits[id];
      if (state.failure) throw state.failure;
      return snapshot as T;
    }
    throw new ApiError(404, 'not_found');
  });
  return { api, state, request };
}
function show(api: Api, initial?: Workflow, extra: Partial<TasksProps> = {}) {
  const onOpenDocument = vi.fn(); const onWorkflowChange = vi.fn(); const onSession = vi.fn();
  const rendered = render(<Tasks api={api} session={session} {...(initial ? { requestedTask: initial } : {})} onSession={onSession} onOpenDocument={onOpenDocument} onWorkflowChange={onWorkflowChange} {...extra} />);
  return { ...rendered, onOpenDocument, onWorkflowChange, onSession };
}
const stateStatus = () => screen.getByRole('status', { name: 'Task state' });
const approvalCheckbox = () => screen.getByRole('checkbox', { name: /I compared the exact payload/ });
const approvalButton = () => screen.getByRole('button', { name: 'Approve this exact action' });
async function openPrepared(user: UserEvent) {
  await screen.findByRole('status', { name: 'Task state' });
  await user.click(screen.getByRole('button', { name: 'Review prepared action' }));
  await screen.findByRole('region', { name: 'Reviewed prepared action' });
}
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('durable task status and recovery', () => {
  it.each<WorkflowState>(['pending', 'running', 'waiting_for_approval', 'retryable_failure', 'permanent_failure', 'cancelled', 'succeeded'])('renders saved %s state without opening or accepting output', async state => {
    const initial = task({ state, cancelable: !['permanent_failure', 'cancelled', 'succeeded'].includes(state), result: { document_id: 'saved-document' } });
    const { api, request } = server(initial); const { onOpenDocument } = show(api, initial);
    expect(await screen.findByRole('status', { name: 'Task state' })).toHaveTextContent(new RegExp(`^${state.replaceAll('_', ' ')}$`, 'i'));
    expect(onOpenDocument).not.toHaveBeenCalled();
    expect(request.mock.calls.filter(call => call[1] === 'POST')).toHaveLength(0);
    if (['permanent_failure', 'cancelled', 'succeeded'].includes(state)) expect(screen.queryByRole('button', { name: 'Cancel task' })).not.toBeInTheDocument();
  });

  it('distinguishes recorded zero model usage from no model attempt and unknown actual cost', async () => {
    const initial = task({ state: 'succeeded', usage: { status: 'known', model_runs: 1, inference_post_attempts: 1, inference_post_limit: 2, input_tokens: 0, output_tokens: 0, token_budget: 500, tokens_used: 50, cost_budget_microunits: 200, cost_used_microunits: 0, currency: 'EUR' } });
    const { api, state } = server(initial); show(api, initial);
    await screen.findByRole('status', { name: 'Task state' });
    const usage = screen.getByRole('region', { name: 'Task usage and limits' });
    expect(within(usage).getByText('0 microunits EUR')).toBeVisible();
    expect(within(usage).getByText('0 / 0')).toBeVisible();
    expect(within(usage).getByText(/50 \/ 500 tokens/)).toBeVisible();
    state.workflows[initial.id] = { ...initial, version: 5, usage: { ...initial.usage, status: 'unknown', input_tokens: null, output_tokens: null, cost_used_microunits: null } };
    fireEvent.click(screen.getByRole('button', { name: 'Reload task status' }));
    await waitFor(() => expect(within(usage).getByText('Unknown EUR')).toBeVisible());
    expect(within(usage).queryByText('0 microunits EUR')).not.toBeInTheDocument();
    state.workflows[initial.id] = { ...initial, version: 6, usage: task().usage };
    fireEvent.click(screen.getByRole('button', { name: 'Reload task status' }));
    await waitFor(() => expect(within(usage).getByText(/No model attempt/)).toBeVisible());
  });

  it('retains visible tasks on a failed page, focuses recovery, and reloads rather than claiming an empty list', async () => {
    const user = userEvent.setup(); let fail = false;
    const { api } = server(task(), async path => { if (path.startsWith('/workflows?') && fail) throw new TypeError('offline'); return undefined; });
    show(api); await user.click(screen.getByRole('button', { name: 'Load tasks' }));
    const saved = await screen.findByRole('button', { name: /Running · document generate · task-one/ });
    fail = true; await user.click(screen.getByRole('button', { name: 'Reload tasks' }));
    expect(await screen.findByRole('alert')).toHaveFocus(); expect(saved).toBeVisible();
    expect(screen.queryByText(/No saved tasks match/)).not.toBeInTheDocument();
    fail = false; await user.click(screen.getByRole('button', { name: 'Reload tasks' }));
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument()); expect(saved).toBeVisible();
  });

  it('preserves cumulative attempt and usage limits when requesting an actually due retry', async () => {
    const user = userEvent.setup(); const initial = task({ state: 'retryable_failure', retryable: true, retry_block_reason: null, next_attempt_at: '2026-01-01T00:00:00Z', attempt: 2, remaining_attempts: 1, usage: { ...task().usage, status: 'unknown', model_runs: 2, inference_post_attempts: 2, inference_post_limit: 3, token_budget: 2000, tokens_used: 1200, cost_budget_microunits: 900, cost_used_microunits: null } });
    const { api, request } = server(initial); show(api, initial);
    await screen.findByRole('status', { name: 'Task state' });
    expect(within(screen.getByRole('region', { name: 'Task usage and limits' })).getByText('Unknown USD')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Request due retry' }));
    await waitFor(() => expect(stateStatus()).toHaveTextContent('Pending'));
    const calls = request.mock.calls.filter(call => call[0] === '/workflows/task-one/retry'); expect(calls).toHaveLength(1);
    expect(calls[0]?.slice(1, 5)).toEqual(['POST', {}, initial.version, expect.any(String)]);
    expect(screen.getByText(/2 of 3; 1 remaining/)).toBeVisible(); expect(screen.getByText(/1200 \/ 2000 tokens/)).toBeVisible();
    expect(screen.getByText(/900 microunits USD; a limit/)).toBeVisible();
  });

  it.each([
    { next_attempt_at: '2030-01-01T00:00:00Z', retry_block_reason: 'retry_not_due' },
    { remaining_attempts: 0, retry_block_reason: 'attempts_exhausted' },
    { deadline_at: '2020-01-01T00:00:00Z', retry_block_reason: 'deadline_exhausted' },
    { delivery_state: 'unknown' as const, retry_block_reason: 'delivery_reconciliation_required' },
    { cancel_requested_at: timestamp, retry_block_reason: 'cancelled' },
  ])('refuses unsafe retry even if stale summary says retryable: %j', async guards => {
    const initial = task({ state: 'retryable_failure', retryable: true, ...guards });
    const { api, request } = server(initial); show(api, initial);
    await screen.findByRole('status', { name: 'Task state' });
    expect(screen.getByRole('button', { name: 'Request due retry' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Request due retry' }));
    expect(request.mock.calls.filter(call => call[1] === 'POST')).toHaveLength(0);
  });

  it('shows conflict recovery and retains the task instead of blindly retrying a control command', async () => {
    const user = userEvent.setup(); const initial = task({ state: 'retryable_failure', retryable: true }); let conflict = true;
    const { api, state, request } = server(initial, async (path, method) => { if (path.endsWith('/retry') && method === 'POST' && conflict) throw new ApiError(412, 'version_conflict'); return undefined; });
    show(api, initial); await screen.findByRole('status', { name: 'Task state' });
    await user.click(screen.getByRole('button', { name: 'Request due retry' }));
    expect(await screen.findByRole('alert')).toHaveFocus(); expect(stateStatus()).toHaveTextContent('Retryable failure');
    conflict = false; state.workflows[initial.id] = { ...initial, version: 8 };
    await user.click(screen.getByRole('button', { name: 'Reload task status' }));
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: 'Request due retry' }));
    await waitFor(() => expect(stateStatus()).toHaveTextContent('Pending'));
    const attempts = request.mock.calls.filter(call => call[0].endsWith('/retry'));
    expect(attempts.map(call => call[3])).toEqual([4, 8]); expect(attempts[1]?.[4]).not.toBe(attempts[0]?.[4]);
  });

  it('cancels through the exact workflow precondition without claiming provider termination or undo', async () => {
    const user = userEvent.setup(); const initial = task({ steps: { items: [checkpoint], next_cursor: null } });
    const { api, request } = server(initial); show(api, initial);
    await screen.findByRole('status', { name: 'Task state' }); await user.click(screen.getByRole('button', { name: 'Cancel task' }));
    await waitFor(() => expect(stateStatus()).toHaveTextContent('Cancelled'));
    expect(screen.getByText(/local publication fence is settled/)).toBeVisible();
    expect(screen.getByText(/Provider termination is unknown/)).toBeVisible();
    expect(within(screen.getByRole('region', { name: 'Completed task checkpoints' })).getByText('validated_source')).toBeVisible();
    const cancellation = request.mock.calls.find(call => call[0].endsWith('/cancel'));
    expect(cancellation?.slice(1, 5)).toEqual(['POST', {}, initial.version, expect.any(String)]);
  });

  it('does not let a stale same-selection status response reverse a settled cancellation', async () => {
    const user = userEvent.setup(); let release!: () => void;
    // This repository targets ES2022, which does not expose Promise.withResolvers.
    const wait = new Promise<void>(resolve => { release = resolve; });
    const initial = task(); const { api, state } = server(initial); show(api, initial);
    await screen.findByRole('status', { name: 'Task state' }); state.detailWaits[initial.id] = wait;
    await user.click(screen.getByRole('button', { name: 'Reload task status' }));
    await user.click(screen.getByRole('button', { name: 'Cancel task' }));
    await waitFor(() => expect(stateStatus()).toHaveTextContent('Cancelled'));
    await act(async () => { release(); });
    expect(stateStatus()).toHaveTextContent('Cancelled');
    expect(screen.queryByRole('button', { name: 'Cancel task' })).not.toBeInTheDocument();
  });

  it('ignores late detail and history responses after selecting another task', async () => {
    const user = userEvent.setup(); let releaseDetail!: () => void; let releaseHistory!: () => void;
    // This repository targets ES2022, which does not expose Promise.withResolvers.
    const detailWait = new Promise<void>(resolve => { releaseDetail = resolve; }); const historyWait = new Promise<void>(resolve => { releaseHistory = resolve; });
    const old = task({ steps: { items: [checkpoint], next_cursor: checkpoint.id } });
    const other = task({ id: 'task-two', state: 'succeeded', result: { document_id: 'other-document' } });
    const { api, state } = server(old, async path => { if (path.includes('/task-one/steps?')) { await historyWait; return { items: [{ ...checkpoint, id: 'late-step', step_key: 'late_private_checkpoint' }], next_cursor: null }; } return undefined; });
    state.workflows[other.id] = other; const { onOpenDocument } = show(api);
    await user.click(screen.getByRole('button', { name: 'Load tasks' }));
    await user.click(await screen.findByRole('button', { name: /Running · document generate · task-one/ }));
    await screen.findByRole('region', { name: 'Completed task checkpoints' });
    await user.click(screen.getByRole('button', { name: 'Load older checkpoints' }));
    state.detailWaits[old.id] = detailWait;
    await user.click(screen.getByRole('button', { name: 'Reload task status' }));
    await user.click(screen.getByRole('button', { name: /Succeeded · document generate · task-two/ }));
    await waitFor(() => expect(stateStatus()).toHaveTextContent('Succeeded'));
    await act(async () => { releaseDetail(); releaseHistory(); });
    expect(screen.getByRole('heading', { name: 'Task task-two' })).toBeVisible();
    expect(screen.queryByText('late_private_checkpoint')).not.toBeInTheDocument(); expect(onOpenDocument).not.toHaveBeenCalled();
  });

  it('paginates task pages and both immutable histories without duplication or hidden truncation', async () => {
    const user = userEvent.setup(); const oldStep = { ...checkpoint, id: 'step-old', step_key: 'older_checkpoint' }; const oldEvent = { ...attempt, id: 'event-old', event: 'interrupted' };
    const initial = task({ state: 'succeeded', steps: { items: [checkpoint], next_cursor: checkpoint.id }, attempt_events: { items: [attempt], next_cursor: attempt.id } });
    const { api, state, request } = server(initial, async path => {
      if (path.includes('/steps?')) return { items: [checkpoint, oldStep], next_cursor: null };
      if (path.includes('/attempt-events?')) return { items: [attempt, oldEvent], next_cursor: null };
      if (path.includes('cursor=opaque-next')) return { items: [initial, task({ id: 'task-older', state: 'cancelled' })], next_cursor: null };
      return undefined;
    });
    state.listCursor = 'opaque-next'; show(api, initial);
    await screen.findByRole('status', { name: 'Task state' });
    await user.click(screen.getByRole('button', { name: 'Load tasks' })); await user.click(await screen.findByRole('button', { name: 'Load older tasks' }));
    expect(within(screen.getByRole('list', { name: 'Saved tasks' })).getAllByRole('button')).toHaveLength(2);
    await user.click(screen.getByRole('button', { name: 'Load older checkpoints' })); await screen.findByText('older_checkpoint');
    await user.click(screen.getByRole('button', { name: 'Load older attempt events' }));
    await waitFor(() => expect(within(screen.getByRole('region', { name: 'Task attempt history' })).getAllByRole('listitem')).toHaveLength(2));
    expect(within(screen.getByRole('region', { name: 'Completed task checkpoints' })).getAllByRole('listitem')).toHaveLength(2);
    expect(request.mock.calls.some(call => call[0] === '/workflows/task-one/steps?limit=30&before=step-new')).toBe(true);
    expect(request.mock.calls.some(call => call[0] === '/workflows/task-one/attempt-events?limit=30&before=event-new')).toBe(true);
    expect(screen.queryByRole('button', { name: 'Load older checkpoints' })).not.toBeInTheDocument();
  });

  it('pauses hidden polling, bounds consecutive failures, retains stale status and explicitly resumes', async () => {
    vi.useFakeTimers(); let hidden = false; vi.spyOn(document, 'hidden', 'get').mockImplementation(() => hidden);
    const initial = task(); const { api, state, request } = server(initial); show(api, initial);
    await act(async () => {});
    const reads = () => request.mock.calls.filter(call => call[0] === '/workflows/task-one').length;
    expect(reads()).toBe(1);
    hidden = true; act(() => { fireEvent(document, new Event('visibilitychange')); });
    await act(async () => { await vi.advanceTimersByTimeAsync(60000); }); expect(reads()).toBe(1);
    hidden = false; state.failure = new TypeError('offline'); act(() => { fireEvent(document, new Event('visibilitychange')); });
    await act(async () => { await vi.advanceTimersByTimeAsync(7000); });
    expect(reads()).toBe(4); expect(stateStatus()).toHaveTextContent('Running'); expect(screen.getByRole('alert')).toHaveFocus();
    expect(screen.getByRole('button', { name: 'Resume monitoring' })).toBeEnabled();
    await act(async () => { await vi.advanceTimersByTimeAsync(60000); }); expect(reads()).toBe(4);
    state.failure = null; state.workflows[initial.id] = { ...initial, version: 5, state: 'succeeded' };
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Resume monitoring' })); });
    expect(stateStatus()).toHaveTextContent('Succeeded'); expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(60000); }); expect(reads()).toBe(5);
  });
});

describe('exact action review and privacy', () => {
  it('requires explicit keyboard confirmation and posts only the exact prepared hashes and target', async () => {
    const user = userEvent.setup(); const initial = task({ state: 'waiting_for_approval' }); const { api, state, request } = server(initial);
    state.approval = prepared(); const storage = vi.spyOn(Storage.prototype, 'setItem'); const { onOpenDocument } = show(api, initial);
    await openPrepared(user); expect(approvalButton()).toBeDisabled();
    expect(screen.getByRole('region', { name: 'Reviewed prepared action' })).toHaveTextContent('<script>privateReview()</script>');
    expect(screen.queryByRole('link')).not.toBeInTheDocument(); expect(request.mock.calls.filter(call => call[1] === 'POST')).toHaveLength(0);
    approvalCheckbox().focus(); await user.keyboard(' '); expect(approvalCheckbox()).toBeChecked();
    await user.tab(); expect(approvalButton()).toHaveFocus(); await user.keyboard('{Enter}');
    await waitFor(() => expect(stateStatus()).toHaveTextContent('Pending'));
    const posts = request.mock.calls.filter(call => call[1] === 'POST'); expect(posts).toHaveLength(1);
    expect(posts[0]?.slice(0, 5)).toEqual(['/workflows/task-one/approval/approve', 'POST', { payload_sha256: 'a'.repeat(64), destination_sha256: 'b'.repeat(64), revision_id: 'revision-one', target_version: 7, confirmed: true, confirmation_phrase: null }, initial.version, expect.any(String)]);
    expect(storage).not.toHaveBeenCalled(); expect(onOpenDocument).not.toHaveBeenCalled();
  });

  it.each(['privacy_delete', 'replace_workspace'] as const)('routes %s to full owner-data review even when the generic intent is applicable and executable', async actionKind => {
    const user = userEvent.setup(); const initial = task({ state: 'waiting_for_approval', kind: actionKind });
    const { api, state, request } = server(initial);
    state.approval = prepared({ action_kind: actionKind, applicable: true, capability_available: true, confirmation_phrase: actionKind === 'privacy_delete' ? 'DELETE ALL WORKSPACE DATA' : 'REPLACE WORKSPACE' });
    show(api, initial); await openPrepared(user);
    expect(screen.getByRole('region', { name: 'Reviewed prepared action' })).toBeVisible();
    expect(screen.queryByLabelText(/Type the exact task confirmation phrase/)).not.toBeInTheDocument();
    expect(approvalCheckbox()).toBeDisabled(); expect(approvalButton()).toBeDisabled();
    fireEvent.click(approvalCheckbox()); fireEvent.click(approvalButton());
    expect(request.mock.calls.filter(call => call[1] === 'POST')).toHaveLength(0);
    expect(screen.getByRole('button', { name: 'Cancel task' })).toBeEnabled();
    await user.click(screen.getByRole('button', { name: 'Reload prepared action' }));
    expect(approvalButton()).toBeDisabled();
    expect(request.mock.calls.filter(call => call[1] === 'POST')).toHaveLength(0);
  });

  it('clears hosted confirmation when the browser session changes', async () => {
    const user = userEvent.setup(); const initial = task({ state: 'waiting_for_approval' });
    const { api, state, request } = server(initial); state.approval = prepared();
    const { rerender, onSession, onOpenDocument } = show(api, initial); await openPrepared(user);
    await user.click(approvalCheckbox()); expect(approvalButton()).toBeEnabled();
    rerender(<Tasks api={api} session={{ ...session, csrf_token: 'rotated-session' }} requestedTask={initial} onSession={onSession} onOpenDocument={onOpenDocument} />);
    await waitFor(() => expect(approvalCheckbox()).not.toBeChecked());
    expect(approvalButton()).toBeDisabled();
    expect(request.mock.calls.filter(call => call[1] === 'POST')).toHaveLength(0);
  });

  it.each([
    { capability_available: false, reason: 'execution_unavailable' },
    { action_kind: 'privacy_delete' as const, confirmation_phrase: null, reason: 'approval_required' },
    { applicable: false, reason: 'workspace_changed' },
    { applicable: false, reason: 'session_revoked' },
    { expires_at: '2020-01-01T00:00:00Z', reason: 'approval_expired' },
    { consumed_at: timestamp, reason: 'approval_consumed' },
    { approved_at: timestamp, reason: 'approved' },
  ])('never offers execution permission for unavailable or invalid intent %j', async invalid => {
    const user = userEvent.setup(); const initial = task({ state: 'waiting_for_approval' }); const { api, state, request } = server(initial); state.approval = prepared(invalid);
    show(api, initial); await openPrepared(user);
    expect(approvalCheckbox()).toBeDisabled(); expect(approvalButton()).toBeDisabled();
    fireEvent.click(approvalButton()); expect(request.mock.calls.filter(call => call[1] === 'POST')).toHaveLength(0);
  });

  it('does not invent a proposal, approval route or execution when the prepared action is missing', async () => {
    const user = userEvent.setup(); const initial = task({ state: 'waiting_for_approval' }); const { api, request } = server(initial); show(api, initial);
    await screen.findByRole('status', { name: 'Task state' }); await user.click(screen.getByRole('button', { name: 'Review prepared action' }));
    expect(await screen.findByText(/No prepared action exists for this task/)).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Approve this exact action' })).not.toBeInTheDocument(); expect(screen.queryByRole('link')).not.toBeInTheDocument();
    expect(request.mock.calls.filter(call => call[1] === 'POST')).toHaveLength(0);
  });

  it('returns an import-review wait to its existing domain decisions without exposing generic approval', async () => {
    const user = userEvent.setup(); const initial = task({ state: 'waiting_for_approval', kind: 'profile_import' });
    const { api, request } = server(initial); show(api, initial);
    await screen.findByRole('status', { name: 'Task state' });
    expect(screen.getByText(/This is a résumé import review/)).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Review prepared action' }));
    expect(await screen.findByText(/No generic prepared action exists for a résumé import/)).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Approve this exact action' })).not.toBeInTheDocument();
    expect(request.mock.calls.filter(call => call[1] === 'POST')).toHaveLength(0);
  });

  it('removes a preparation that disappears after confirmation without sending an approval', async () => {
    const user = userEvent.setup(); const initial = task({ state: 'waiting_for_approval' });
    const { api, state, request } = server(initial); state.approval = prepared();
    show(api, initial); await openPrepared(user); await user.click(approvalCheckbox()); state.approval = null;
    await user.click(approvalButton());
    expect(await screen.findByText(/No prepared action exists for this task/)).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Approve this exact action' })).not.toBeInTheDocument();
    expect(request.mock.calls.filter(call => call[1] === 'POST')).toHaveLength(0);
  });

  it('keeps a refused recent-auth approval unapproved and focuses explicit session recovery', async () => {
    const user = userEvent.setup(); const initial = task({ state: 'waiting_for_approval' });
    const { api, state, request } = server(initial, async (path, method) => {
      if (path.endsWith('/approval/approve') && method === 'POST') throw new ApiError(403, 'reauthentication_required');
      return undefined;
    });
    state.approval = prepared(); show(api, initial); await openPrepared(user); await user.click(approvalCheckbox()); await user.click(approvalButton());
    const alert = await screen.findByRole('alert'); expect(alert).toHaveFocus();
    expect(alert).toHaveTextContent(/fresh exact preview/); expect(stateStatus()).toHaveTextContent('Waiting for approval');
    expect(approvalCheckbox()).not.toBeChecked(); expect(approvalButton()).toBeDisabled();
    expect(request.mock.calls.filter(call => call[1] === 'POST')).toHaveLength(1);
  });

  it('compares a changed destination and requires fresh explicit confirmation instead of sending a stale approval', async () => {
    const user = userEvent.setup(); const initial = task({ state: 'waiting_for_approval' }); const { api, state, request } = server(initial); state.approval = prepared();
    show(api, initial); await openPrepared(user); await user.click(approvalCheckbox());
    state.approval = prepared({ destination_sha256: 'c'.repeat(64), payload_sha256: 'd'.repeat(64), target_version: 8, intent: { destination: 'different-exact-target', workspace_version: 8 } });
    await user.click(approvalButton());
    expect(await screen.findByRole('alert')).toHaveFocus();
    expect(screen.getByRole('region', { name: 'Reviewed prepared action' })).toHaveTextContent('b'.repeat(64));
    expect(screen.getByRole('region', { name: 'Current server comparison' })).toHaveTextContent('c'.repeat(64));
    expect(approvalButton()).toBeDisabled(); expect(request.mock.calls.filter(call => call[1] === 'POST')).toHaveLength(0);
    await user.click(screen.getByRole('button', { name: 'Review this current preparation' })); expect(approvalCheckbox()).not.toBeChecked();
    await user.click(approvalCheckbox()); await user.click(approvalButton());
    await waitFor(() => expect(stateStatus()).toHaveTextContent('Pending'));
    expect(request.mock.calls.find(call => call[1] === 'POST')?.[2]).toEqual({ payload_sha256: 'd'.repeat(64), destination_sha256: 'c'.repeat(64), revision_id: 'revision-one', target_version: 8, confirmed: true, confirmation_phrase: null });
  });

  it('keeps an unavailable result inspectable without opening over a dirty workspace', async () => {
    const user = userEvent.setup(); const initial = task({ state: 'succeeded', result: { document_id: 'saved-document' } }); const { api } = server(initial);
    const { onOpenDocument, rerender } = show(api, initial, { resultDisabled: true });
    await screen.findByRole('status', { name: 'Task state' }); expect(screen.getByRole('button', { name: 'Open document result' })).toBeDisabled(); expect(onOpenDocument).not.toHaveBeenCalled();
    rerender(<Tasks api={api} session={session} requestedTask={initial} onSession={() => undefined} onOpenDocument={onOpenDocument} resultDisabled={false} />);
    await user.click(screen.getByRole('button', { name: 'Open document result' })); expect(onOpenDocument).toHaveBeenCalledWith('saved-document', expect.any(String));
  });

  it('reauthenticates explicitly, rotates CSRF and requires a new genuine preparation instead of rebinding', async () => {
    const user = userEvent.setup(); const initial = task({ state: 'waiting_for_approval' }); const { api, state, request } = server(initial); state.approval = prepared();
    const renewed = { ...session, csrf_token: 'new-csrf' }; const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ csrf_token: 'challenge', bootstrap_available: false }), { status: 200 })).mockImplementationOnce(async () => { state.approval = prepared({ applicable: false, reason: 'session_revoked' }); return new Response(JSON.stringify(renewed), { status: 201 }); });
    vi.stubGlobal('fetch', fetcher); const { onSession } = show(api, initial); await openPrepared(user);
    await user.click(screen.getByText('Recent sign-in and bound-session recovery'));
    await user.type(screen.getByLabelText('Current password for task review'), 'not-a-real-password-value');
    await user.click(screen.getByRole('button', { name: 'Sign in again for task review' }));
    await screen.findByRole('region', { name: 'Current server comparison' });
    expect(onSession).toHaveBeenCalledWith(renewed); expect(api.csrf).toBe('new-csrf');
    expect(screen.getByLabelText('Current password for task review')).toHaveValue('');
    expect(approvalButton()).toBeDisabled(); expect(request.mock.calls.filter(call => call[1] === 'POST')).toHaveLength(0);
    expect(fetcher.mock.calls[1]?.[0]).toBe('/api/v1/session');
    await user.click(screen.getByRole('button', { name: 'Review this current preparation' })); expect(approvalCheckbox()).toBeDisabled();
  });
});
