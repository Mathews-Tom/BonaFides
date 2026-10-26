// SPDX-License-Identifier: MIT OR Apache-2.0
import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { Api, ApiError, errorText, terminal, type ActionApproval, type ActionApprovalRequest, type AttemptEvent, type Challenge, type Session, type TaskPage, type TaskRetentionStatus, type Workflow, type WorkflowPage, type WorkflowState, type WorkflowStep } from './api';

export interface TasksProps {
  api: Api; session: Session; requestedTask?: Workflow | null; disabled?: boolean; resultDisabled?: boolean;
  onSession: (session: Session) => void; onWorkflowChange?: (workflow: Workflow) => void;
  onOpenDocument: (id: string, key: string) => void; onBusyChange?: (busy: boolean) => void;
}
const states: WorkflowState[] = ['pending', 'running', 'waiting_for_approval', 'retryable_failure', 'permanent_failure', 'cancelled', 'succeeded'];
const stateLabels: Record<WorkflowState, string> = { pending: 'Pending', running: 'Running', waiting_for_approval: 'Waiting for approval', retryable_failure: 'Retryable failure', permanent_failure: 'Permanent failure', cancelled: 'Cancelled', succeeded: 'Succeeded' };
const stateHelp: Record<WorkflowState, string> = {
  pending: 'Saved in the durable queue. A worker must claim it before work starts.',
  running: 'A worker holds a local lease. Completed checkpoints below are evidence of work, not an estimated percentage.',
  waiting_for_approval: 'Work is paused. Inspect the reason and use the original domain review, or review an exact prepared action if one exists.',
  retryable_failure: 'The previous attempt failed. A retry is allowed only when due, replay-safe and within the original cumulative limits.',
  permanent_failure: 'This run is terminal and cannot be reopened. Inspect the safe reason, correct the source or configuration, then use the original workspace to request a new action if appropriate.',
  cancelled: 'The local cancellation checkpoint has revoked this run’s publication authority. Already committed effects remain; this does not prove an in-flight provider stopped.',
  succeeded: 'The workflow completed its local operation. This is not factual acceptance of a proposal, independent verification, or proof of delivery.',
};
const pathFor = (id: string) => `/workflows/${encodeURIComponent(id)}`;
const aborted = (failure: unknown) => failure instanceof DOMException && failure.name === 'AbortError';
function controlError(failure: unknown): string {
  if (failure instanceof ApiError) {
    if (failure.status === 412) return 'The task version changed. Reload task status and compare the prepared action again before making another control request. Existing output and editors are retained.';
    if (failure.code === 'reauthentication_required') return 'Recent successful sign-in is required. Use the sign-in recovery form, then obtain a fresh exact preview from the originating domain; session rotation cannot rebind this intent.';
    if (failure.retryAfterSeconds !== undefined) return `The request was refused (${failure.code}). Wait at least ${failure.retryAfterSeconds} seconds, then reload task status; no retry delay or cumulative limit has been reset.`;
  }
  return `${errorText(failure)} Reload task status before deciding whether a control request is still appropriate.`;
}
const time = (value: string | null) => value ? <time dateTime={value}>{value}</time> : 'Not recorded';
const approvalIdentity = (value: ActionApproval) => JSON.stringify([value.id, value.version, value.workflow_run_id, value.action_kind, value.payload_sha256, value.destination_sha256, value.revision_id, value.target_version, value.expires_at, value.approved_at, value.consumed_at, value.intent, value.confirmation_phrase]);
function approvalHelp(value: ActionApproval): string {
  if (value.consumed_at) return 'This exact authorization has already been consumed. It is not permission to execute again. Inspect the resulting task; do not resend it.';
  if (value.approved_at) return 'This exact action was approved. Approval is not evidence of execution; inspect task checkpoints. It cannot be approved again under another key.';
  if (Date.parse(value.expires_at) <= Date.now() || value.reason === 'approval_expired') return 'This preparation expired. Return to the originating domain preview and prepare a fresh exact action when that capability is available. Expiry is never renewed here.';
  if (['session_changed', 'session_revoked', 'authentication_changed'].includes(value.reason)) return 'The bound authentication changed. Return to the originating domain preview after signing in and prepare a fresh action. Signing in does not rebind this intent.';
  if (!value.applicable) return 'The prepared action no longer matches current state. Inspect the comparison, then return to the originating domain preview for a fresh preparation. Nothing is automatically rebased or retried.';
  if (!value.capability_available) return 'Execution is unavailable on this installation. Reviewing or approving an intent cannot enable a hosted provider or destructive executor. No transmission or external action will be started here.';
  return 'Compare the exact destination, payload, target and limits below. Confirmation applies only to this prepared action and expires with it; there is no general hosted consent.';
}
function mergeItems<T extends { id: string }>(first: T[], second: T[]): T[] {
  const ids = new Set(first.map(item => item.id));
  return [...first, ...second.filter(item => !ids.has(item.id))];
}
function Intent({ approval, label }: { approval: ActionApproval; label: string }) {
  return <section aria-label={label} className="task-intent"><h4>{label}</h4><dl className="task-facts">
    <div><dt>Authorization</dt><dd>{approval.id}; lifecycle version {approval.version}</dd></div>
    <div><dt>Bound task</dt><dd>{approval.workflow_run_id}</dd></div>
    <div><dt>Action</dt><dd>{approval.action_kind.replaceAll('_', ' ')}</dd></div>
    <div><dt>Payload SHA-256</dt><dd><code>{approval.payload_sha256}</code></dd></div>
    <div><dt>Destination SHA-256</dt><dd><code>{approval.destination_sha256}</code></dd></div>
    <div><dt>Target revision</dt><dd>{approval.revision_id ?? 'No document revision applies'}</dd></div>
    <div><dt>Target version</dt><dd>{approval.target_version}</dd></div>
    <div><dt>Expires</dt><dd>{time(approval.expires_at)}</dd></div>
    <div><dt>Approved</dt><dd>{time(approval.approved_at)}</dd></div>
    <div><dt>Consumed</dt><dd>{time(approval.consumed_at)}</dd></div>
  </dl><p>The server-authored envelope below contains the exact destination/projection or destructive payload, version pins and bounded budgets. It is review data, not instructions to execute.</p><pre>{JSON.stringify(approval.intent, null, 2)}</pre></section>;
}

export function Tasks({ api, session, requestedTask, disabled = false, resultDisabled = false, onSession, onWorkflowChange, onOpenDocument, onBusyChange }: TasksProps) {
  const [page, setPage] = useState<WorkflowPage | null>(null);
  const [filter, setFilter] = useState<WorkflowState | ''>('');
  const [pageFilter, setPageFilter] = useState<WorkflowState | ''>('');
  const [listLoading, setListLoading] = useState(false);
  const [listError, setListError] = useState('');
  const [retention, setRetention] = useState<TaskRetentionStatus | null>(null);
  const [retentionBusy, setRetentionBusy] = useState(false);
  const [retentionError, setRetentionError] = useState('');
  const [detail, setDetail] = useState<Workflow | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [statusError, setStatusError] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [paused, setPaused] = useState(false);
  const [pollGeneration, setPollGeneration] = useState(0);
  const [hidden, setHidden] = useState(document.hidden);
  const [historyBusy, setHistoryBusy] = useState<'steps' | 'attempt_events' | null>(null);
  const [approval, setApproval] = useState<ActionApproval | null>(null);
  const [comparison, setComparison] = useState<ActionApproval | null>(null);
  const [approvalMissing, setApprovalMissing] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  useEffect(() => { setConfirmed(false); }, [session.csrf_token]);
  const mounted = useRef(false);
  const controllers = useRef(new Set<AbortController>());
  const selection = useRef({ id: null as string | null, epoch: 0 });
  const current = useRef<Workflow | null>(null);
  const listEpoch = useRef(0);
  const operation = useRef(false);
  const olderLoaded = useRef({ steps: false, attempt_events: false });
  const mutation = useRef<{ fingerprint: string; key: string } | null>(null);
  const callbacks = useRef({ onSession, onWorkflowChange, onOpenDocument, onBusyChange });
  callbacks.current = { onSession, onWorkflowChange, onOpenDocument, onBusyChange };
  const errorRef = useRef<HTMLDivElement>(null);
  const detailHeading = useRef<HTMLHeadingElement>(null);
  const matches = (id: string, epoch: number) => mounted.current && selection.current.id === id && selection.current.epoch === epoch;
  const focusError = error || statusError || listError || retentionError;
  useEffect(() => { if (focusError) errorRef.current?.focus(); }, [focusError]);
  useEffect(() => { callbacks.current.onBusyChange?.(busy); }, [busy]);
  useEffect(() => {
    mounted.current = true; const pending = controllers.current;
    const visibility = () => setHidden(document.hidden);
    document.addEventListener('visibilitychange', visibility);
    return () => { mounted.current = false; for (const controller of pending) controller.abort(); document.removeEventListener('visibilitychange', visibility); callbacks.current.onBusyChange?.(false); };
  }, []);
  const request = useCallback(async <T,>(path: string, method = 'GET', body?: unknown, version?: number, key: boolean | string = false): Promise<T> => {
    const controller = new AbortController(); controllers.current.add(controller);
    let timedOut = false;
    const timeout = window.setTimeout(() => { timedOut = true; controller.abort(); }, 15000);
    try {
      const value = await api.request<T>(path, method, body, version, key, controller.signal);
      if (!mounted.current || controller.signal.aborted) throw new DOMException('Request stopped', 'AbortError');
      return value;
    } catch (failure) { if (timedOut) throw new ApiError(408, 'task_request_timeout'); throw failure; }
    finally { clearTimeout(timeout); controllers.current.delete(controller); }
  }, [api]);
  async function loadRetention() {
    setRetentionBusy(true); setRetentionError('');
    try {
      const value = await request<{ task_input_retention: TaskRetentionStatus }>('/status');
      setRetention(value.task_input_retention);
    } catch (failure) {
      if (!aborted(failure)) setRetentionError(`${errorText(failure)} Any previous retention status is only the last observation, not current success.`);
    } finally { if (mounted.current) setRetentionBusy(false); }
  }
  const receive = useCallback((value: Workflow) => {
    const previous = current.current;
    if (selection.current.id !== value.id || (previous?.id === value.id && previous.version > value.version)) return;
    const merged = { ...value };
    for (const field of ['steps', 'attempt_events'] as const) {
      const incoming = value[field]; const old = previous?.id === value.id ? previous[field] : undefined;
      if (!incoming && old) Object.assign(merged, { [field]: old });
      else if (incoming && old) Object.assign(merged, { [field]: { items: mergeItems<WorkflowStep | AttemptEvent>(incoming.items, old.items), next_cursor: olderLoaded.current[field] ? old.next_cursor : incoming.next_cursor } });
    }
    current.current = merged; setDetail(merged);
    setPage(previousPage => previousPage ? { ...previousPage, items: previousPage.items.map(item => item.id === value.id && item.version <= value.version ? value : item) } : previousPage);
    callbacks.current.onWorkflowChange?.(merged);
  }, []);
  const loadList = useCallback(async (scope: WorkflowState | '', cursor?: string) => {
    const epoch = ++listEpoch.current; setListLoading(true); setListError('');
    const query = new URLSearchParams({ limit: '30' });
    if (scope) query.set('state', scope); if (cursor) query.set('cursor', cursor);
    try {
      const value = await request<WorkflowPage>(`/workflows?${query}`);
      if (epoch !== listEpoch.current) return;
      const items = value.items.map(item => current.current?.id === item.id && current.current.version > item.version ? current.current : item);
      setPage(previous => cursor && previous ? { ...value, items: mergeItems(previous.items, items) } : { ...value, items });
      setPageFilter(scope);
    } catch (failure) { if (!aborted(failure) && epoch === listEpoch.current) setListError(errorText(failure)); }
    finally { if (mounted.current && epoch === listEpoch.current) setListLoading(false); }
  }, [request]);
  const loadDetail = useCallback(async (id: string, epoch: number) => {
    const value = await request<Workflow>(pathFor(id));
    if (!mounted.current || selection.current.id !== id || selection.current.epoch !== epoch) return;
    receive(value); setStatusError('');
  }, [request, receive]);
  const selectTask = useCallback((id: string, seed?: Workflow) => {
    if (operation.current) return;
    const epoch = selection.current.epoch + 1; selection.current = { id, epoch };
    current.current = seed ?? null; olderLoaded.current = { steps: false, attempt_events: false };
    setSelectedId(id); setDetail(seed ?? null); setDetailLoading(true); setStatusError(''); setError(''); setNotice('');
    setApproval(null); setComparison(null); setApprovalMissing(false); setConfirmed(false); mutation.current = null;
    setHistoryBusy(null); setPaused(false); setPollGeneration(value => value + 1);
    void loadDetail(id, epoch).then(() => {
      if (selection.current.id === id && selection.current.epoch === epoch) detailHeading.current?.focus();
    }).catch(failure => {
      if (!aborted(failure) && mounted.current && selection.current.id === id && selection.current.epoch === epoch) { setStatusError(errorText(failure)); setPaused(true); }
    }).finally(() => { if (mounted.current && selection.current.id === id && selection.current.epoch === epoch) setDetailLoading(false); });
  }, [loadDetail]);
  const requestedId = requestedTask?.id;
  const requestedSeed = useRef(requestedTask); requestedSeed.current = requestedTask;
  useEffect(() => { if (requestedId) selectTask(requestedId, requestedSeed.current ?? undefined); }, [requestedId, selectTask]);
  const isTerminal = detail ? terminal(detail.state) : true;
  useEffect(() => {
    if (!selectedId || isTerminal || paused || hidden || detailLoading) return;
    const id = selectedId; const epoch = selection.current.epoch;
    let stopped = false; let timer: number | undefined; let delay = 1000; let failures = 0; let reads = 0;
    const poll = async () => {
      if (stopped || document.hidden || operation.current) { if (!stopped && !document.hidden) timer = window.setTimeout(() => { void poll(); }, delay); return; }
      try { await loadDetail(id, epoch); failures = 0; }
      catch (failure) {
        if (stopped || aborted(failure) || selection.current.id !== id || selection.current.epoch !== epoch) return;
        failures++; setStatusError(`${errorText(failure)} Last visible task data is retained.`);
        if (failures >= 3) { setPaused(true); return; }
      }
      reads++;
      if (stopped || selection.current.id !== id || selection.current.epoch !== epoch || (current.current && terminal(current.current.state))) return;
      if (reads >= 120) { setPaused(true); setNotice('The bounded monitoring window ended. Resume monitoring or reload status explicitly.'); return; }
      delay = Math.min(delay * 2, 15000); timer = window.setTimeout(() => { void poll(); }, delay);
    };
    timer = window.setTimeout(() => { void poll(); }, delay);
    return () => { stopped = true; clearTimeout(timer); };
  }, [selectedId, isTerminal, paused, hidden, detailLoading, pollGeneration, loadDetail]);
  async function reload(resume: boolean) {
    const id = selection.current.id; const epoch = selection.current.epoch;
    if (!id || operation.current) return;
    setDetailLoading(true); setError('');
    try { await loadDetail(id, epoch); if (matches(id, epoch) && resume) { setPaused(false); setPollGeneration(value => value + 1); } }
    catch (failure) { if (!aborted(failure) && matches(id, epoch)) { setStatusError(errorText(failure)); setPaused(true); } }
    finally { if (matches(id, epoch)) setDetailLoading(false); }
  }
  async function loadHistory(field: 'steps' | 'attempt_events') {
    const live = current.current; if (!live || historyBusy || operation.current) return;
    const cursor = live[field]?.next_cursor; if (!cursor) return;
    const epoch = selection.current.epoch; setHistoryBusy(field); setError('');
    try {
      const endpoint = field === 'steps' ? 'steps' : 'attempt-events';
      const value = await request<TaskPage<WorkflowStep | AttemptEvent>>(`${pathFor(live.id)}/${endpoint}?${new URLSearchParams({ limit: '30', before: cursor })}`);
      if (!matches(live.id, epoch) || !current.current) return;
      olderLoaded.current[field] = true;
      const updated = { ...current.current, [field]: { ...value, items: mergeItems<WorkflowStep | AttemptEvent>(current.current[field]?.items ?? [], value.items) } };
      current.current = updated; setDetail(updated);
    } catch (failure) { if (!aborted(failure) && matches(live.id, epoch)) setError(`${errorText(failure)} Loaded history is retained; reload task status or try this page again.`); }
    finally { if (matches(live.id, epoch)) setHistoryBusy(null); }
  }
  function keyFor(path: string, body: unknown, version: number) {
    const fingerprint = JSON.stringify({ path, body, version });
    if (mutation.current?.fingerprint !== fingerprint) mutation.current = { fingerprint, key: crypto.randomUUID() };
    return mutation.current.key;
  }
  async function run(action: (live: Workflow, epoch: number) => Promise<void>) {
    const live = current.current; if (!live || operation.current || disabled) return;
    operation.current = true; setBusy(true); setError(''); setNotice('');
    try { await action(live, selection.current.epoch); }
    catch (failure) { if (!aborted(failure) && mounted.current) { setConfirmed(false); setError(controlError(failure)); setPaused(true); } }
    finally { operation.current = false; if (mounted.current) setBusy(false); }
  }
  async function inspectApproval() {
    await run(async (live, epoch) => {
      try {
        const value = await request<ActionApproval>(`${pathFor(live.id)}/approval`);
        if (!matches(live.id, epoch)) return;
        setApprovalMissing(false); setConfirmed(false);
        if (approval && approvalIdentity(approval) !== approvalIdentity(value)) setComparison(value);
        else { setApproval(value); setComparison(null); }
      } catch (failure) {
        if (failure instanceof ApiError && failure.status === 404 && matches(live.id, epoch)) { setApprovalMissing(true); setApproval(null); setComparison(null); setConfirmed(false); }
        else throw failure;
      }
    });
  }
  async function approve() {
    if (!approval || approval.action_kind !== 'hosted_egress' || !confirmed || comparison) return;
    const reviewed = approval;
    await run(async (live, epoch) => {
      let latest: ActionApproval;
      try { latest = await request<ActionApproval>(`${pathFor(live.id)}/approval`); }
      catch (failure) {
        if (failure instanceof ApiError && failure.status === 404 && matches(live.id, epoch)) { setApprovalMissing(true); setApproval(null); setComparison(null); setConfirmed(false); return; }
        throw failure;
      }
      if (!matches(live.id, epoch)) return;
      if (approvalIdentity(latest) !== approvalIdentity(reviewed)) { setComparison(latest); setConfirmed(false); setError('The prepared action changed. Compare both envelopes and explicitly review the current one; no approval was sent.'); return; }
      if (latest.action_kind !== 'hosted_egress' || latest.workflow_run_id !== live.id || !latest.applicable || !latest.capability_available || latest.consumed_at || latest.approved_at || Date.parse(latest.expires_at) <= Date.now() || live.state !== 'waiting_for_approval' || live.cancel_requested_at || live.delivery_state === 'unknown') {
        setApproval(latest); setConfirmed(false); setError(`${approvalHelp(latest)} No approval was sent.`); return;
      }
      const body: ActionApprovalRequest = { payload_sha256: reviewed.payload_sha256, destination_sha256: reviewed.destination_sha256, revision_id: reviewed.revision_id, target_version: reviewed.target_version, confirmed: true, confirmation_phrase: null };
      const path = `${pathFor(live.id)}/approval/approve`;
      const value = await request<Workflow>(path, 'POST', body, live.version, keyFor(path, body, live.version));
      if (!matches(live.id, epoch)) return;
      mutation.current = null; receive(value); setConfirmed(false); setApproval(null); setComparison(null);
      setNotice('The exact approval was recorded. This is not proof of execution or permission for another action. Reload the prepared action to inspect its lifecycle.');
      setPaused(false); setPollGeneration(generation => generation + 1);
    });
  }
  async function reauthenticate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = event.currentTarget; const password = String(new FormData(form).get('task_password'));
    await run(async (live, epoch) => {
      const auth = new Api(); const challenge = await auth.request<Challenge>('/auth/challenge'); auth.csrf = challenge.csrf_token;
      const renewed = await auth.request<Session>('/session', 'POST', { login_name: session.login_name, password });
      if (!mounted.current) return;
      api.csrf = renewed.csrf_token; callbacks.current.onSession(renewed); form.reset(); setConfirmed(false);
      if (!matches(live.id, epoch)) return;
      setNotice('Signed in again. The old prepared session is not rebound. Inspect the old intent, then obtain a fresh preview/preparation from the originating domain when available.');
      if (approval) {
        const value = await request<ActionApproval>(`${pathFor(live.id)}/approval`);
        if (matches(live.id, epoch)) { setComparison(value); setConfirmed(false); }
      }
    });
  }
  const retryDue = !!detail && detail.state === 'retryable_failure' && detail.retryable && detail.remaining_attempts > 0 && Date.parse(detail.deadline_at) > Date.now() && (!detail.next_attempt_at || Date.parse(detail.next_attempt_at) <= Date.now()) && !detail.cancel_requested_at && detail.delivery_state !== 'unknown';
  const approvalAllowed = !!detail && !!approval && approval.workflow_run_id === detail.id && !comparison && !approvalMissing && approval.applicable && approval.capability_available && approval.action_kind === 'hosted_egress' && !approval.approved_at && !approval.consumed_at && Date.parse(approval.expires_at) > Date.now() && detail.state === 'waiting_for_approval' && !detail.cancel_requested_at && detail.delivery_state !== 'unknown';
  const locked = disabled || busy || !!api.writeBlock;
  const resultDocuments = detail?.state === 'succeeded' ? detail.result?.documents ?? (detail.result?.document_id ? [{ kind: null, document_id: detail.result.document_id }] : []) : [];
  return <section className="panel tasks" aria-labelledby="tasks-title"><h2 id="tasks-title">Task workspace</h2>
    <p>Durable status, local safety checkpoints and exact action review. Opening a task does not replace an editor or accept its output.</p>
    <div ref={errorRef} role="alert" tabIndex={-1} className="error" hidden={!focusError}>{[error, statusError, listError, retentionError].filter(Boolean).join(' ')}</div>
    <p role="status">{notice}</p>
    <section aria-labelledby="task-retention-title"><h3 id="task-retention-title">Task input retention</h3>
      <p>Unused task text becomes eligible for deletion 24 hours after terminal completion; the sweep schedule below determines actual removal. Saved facts, document revisions and PDFs remain. Active work and unresolved provider delivery are not silently purged; limited task IDs and hashes remain for history and scoped deletion. This does not remove retained encrypted backups or downloaded copies.</p>
      <button className="secondary" disabled={retentionBusy || disabled} onClick={() => { void loadRetention(); }}>{retentionBusy ? 'Loading retention status…' : 'Load input-retention status'}</button>
      {retention && <><p>Last observed sweep: {retention.state.replaceAll('_', ' ')}. Latest bounded batch: {retention.processed_count} tasks.</p>
        <dl className="task-facts"><div><dt>Last complete sweep</dt><dd>{time(retention.last_success_at)}</dd></div><div><dt>Last failed sweep</dt><dd>{time(retention.last_failure_at)}</dd></div></dl>
        {retention.failure_code && <p>Recorded failure: <code>{retention.failure_code}</code>. Later success does not erase this historical failure.</p>}
        <p>A completed sweep runs at least daily while the lifecycle controller is available; bounded backlog batches and failures retry no sooner than one minute. Extensions are not supported.</p></>}
    </section>
    <div className="task-toolbar"><label>Task state filter<select value={filter} disabled={listLoading} onChange={event => { const value = event.target.value as WorkflowState | ''; setFilter(value); if (page) void loadList(value); }}><option value="">All states</option>{states.map(state => <option key={state} value={state}>{stateLabels[state]}</option>)}</select></label>
      <button className="secondary" disabled={listLoading} onClick={() => { void loadList(filter); }}>{page ? 'Reload tasks' : 'Load tasks'}</button></div>
    {listLoading && <p role="status">Loading task page… Existing visible tasks are retained.</p>}
    {page && <><p>Loaded state scope: {pageFilter ? stateLabels[pageFilter] : 'All states'}. Newest created first; state reflects the latest server observation, not a frozen historical state. {pageFilter !== filter && 'The selected filter has not loaded successfully; the earlier scope remains visible.'}</p><ul className="task-list" aria-label="Saved tasks">{page.items.map(item => <li key={item.id}><button className="secondary" aria-current={selectedId === item.id ? 'true' : undefined} disabled={busy} onClick={() => selectTask(item.id)}>{stateLabels[item.state]} · {item.kind?.replaceAll('_', ' ') ?? 'Task'} · {item.id}</button></li>)}</ul>{!page.items.length && !listError && <p>No saved tasks match this returned page. This is not a failed request or a fabricated sample.</p>}{page.next_cursor && <button className="secondary" disabled={listLoading} onClick={() => { void loadList(pageFilter, page.next_cursor ?? undefined); }}>Load older tasks</button>}</>}
    {selectedId && <section className="task-detail" aria-labelledby="task-detail-title"><h3 id="task-detail-title" ref={detailHeading} tabIndex={-1}>Task {selectedId}</h3>
      <div className="actions"><button className="secondary" disabled={detailLoading || busy} onClick={() => { void reload(false); }}>Reload task status</button>{detail && !terminal(detail.state) && <button className="secondary" disabled={detailLoading || busy || hidden} onClick={() => { if (paused) void reload(true); else setPaused(true); }}>{paused ? 'Resume monitoring' : 'Pause monitoring'}</button>}</div>
      {hidden ? <p>Monitoring is paused while this tab is hidden. No background polling is sent.</p> : paused ? <p>Monitoring is paused. Visible data may be stale; reload or explicitly resume.</p> : detail && !terminal(detail.state) ? <p>Monitoring with bounded backoff. Polling does not execute or retry a task.</p> : null}
      {detailLoading && <p role="status">Loading saved status…</p>}
      {detail && <><p role="status" aria-label="Task state"><strong>{stateLabels[detail.state]}</strong></p><p>{stateHelp[detail.state]}</p>
        {detail.kind === 'profile_import' && detail.state === 'waiting_for_approval' && <p>This is a résumé import review, not a consequential-action authorization. In “Import a résumé”, load saved imports and select the import with workflow {detail.id}; compare its source lines and decide each proposal there. This task workspace never merges the profile or approves all proposals.</p>}
        {detail.last_error_code && <p>Safe reason: <code>{detail.last_error_code}</code>. Inspect the source or configuration in its original workspace; do not resend blindly.</p>}
        {detail.delivery_state === 'unknown' && <p className="disclosure"><strong>Delivery outcome unknown — reconciliation required.</strong> Automatic and manual replay are blocked, including after the ordinary deadline. Check the actual external outcome through an authorized channel; this workspace cannot turn uncertainty into resend permission.</p>}
        {detail.cancellation.requested && <p>Cancellation intent recorded at {time(detail.cancel_requested_at)}. {detail.cancellation.local_fence_settled ? 'The local publication fence is settled: this run can no longer publish.' : 'The local cancellation checkpoint has not been confirmed; reload status before assuming publication has stopped.'} Provider termination is unknown. Completed effects are not rolled back.</p>}
        <dl className="task-facts"><div><dt>Created</dt><dd>{time(detail.created_at)}</dd></div><div><dt>Updated</dt><dd>{time(detail.updated_at)}</dd></div><div><dt>Deadline</dt><dd>{time(detail.deadline_at)}</dd></div><div><dt>Next due retry</dt><dd>{time(detail.next_attempt_at)}</dd></div><div><dt>Queued age</dt><dd>{detail.queue_age_seconds === null ? 'Not queued' : `${detail.queue_age_seconds} seconds (server observation)`}</dd></div><div><dt>Attempts</dt><dd>{detail.attempt} of {detail.max_attempts}; {detail.remaining_attempts} remaining. A retry request is not a worker claim.</dd></div><div><dt>Lease expires</dt><dd>{time(detail.lease_expires_at)}</dd></div><div><dt>Last heartbeat</dt><dd>{time(detail.last_heartbeat_at)}</dd></div><div><dt>Delivery state</dt><dd>{detail.delivery_state.replaceAll('_', ' ')}</dd></div></dl>
        <section aria-label="Task usage and limits"><h4>Usage and original limits</h4><p>{detail.usage.status === 'not_applicable' ? 'No model attempt: model usage is not applicable, not a measured zero bill.' : detail.usage.status === 'unknown' ? 'Actual model usage or cost is unknown. No zero-cost assumption is made.' : 'Recorded model usage is known; these are recorded counters, not an independent provider invoice.'}</p>
          <dl className="task-facts"><div><dt>Model runs</dt><dd>{detail.usage.model_runs}</dd></div><div><dt>Inference POST attempts</dt><dd>{detail.usage.inference_post_attempts} / {detail.usage.inference_post_limit ?? 'No model POST allowance'}</dd></div><div><dt>Recorded input / output tokens</dt><dd>{detail.usage.input_tokens ?? 'Unknown'} / {detail.usage.output_tokens ?? 'Unknown'}</dd></div><div><dt>Cumulative token budget accounting</dt><dd>{detail.usage.tokens_used} / {detail.usage.token_budget} tokens (accounting/reservations, not a usage estimate)</dd></div><div><dt>Recorded actual cost</dt><dd>{detail.usage.cost_used_microunits === null ? 'Unknown' : `${detail.usage.cost_used_microunits} microunits`} {detail.usage.currency}</dd></div><div><dt>Original cost ceiling</dt><dd>{detail.usage.cost_budget_microunits} microunits {detail.usage.currency}; a limit, not a bill</dd></div></dl></section>
        <div className="actions">{!terminal(detail.state) && <button className="secondary" disabled={locked || !detail.cancelable} onClick={() => { void run(async (live, epoch) => { if (!live.cancelable) return; const path = `${pathFor(live.id)}/cancel`; const value = await request<Workflow>(path, 'POST', {}, live.version, keyFor(path, {}, live.version)); if (!matches(live.id, epoch)) return; mutation.current = null; receive(value); setConfirmed(false); setNotice('Local cancellation checkpoint recorded. This does not prove an in-flight provider stopped; committed effects remain.'); }); }}>Cancel task</button>}
          {detail.state === 'retryable_failure' && <button className="secondary" disabled={locked || !retryDue || !!statusError} onClick={() => { if (!retryDue) return; void run(async (live, epoch) => { const path = `${pathFor(live.id)}/retry`; const value = await request<Workflow>(path, 'POST', {}, live.version, keyFor(path, {}, live.version)); if (!matches(live.id, epoch)) return; mutation.current = null; receive(value); setNotice('Due retry requested within the same original limits. The next worker claim records a new attempt; no budget or history was reset.'); setPaused(false); setPollGeneration(generation => generation + 1); }); }}>Request due retry</button>}</div>
        {detail.state === 'retryable_failure' && !retryDue && <p>Retry unavailable: {detail.retry_block_reason ?? 'The retry is not due or the original attempt/deadline limits are exhausted'}. Reload status after the due time or correct the originating source; terminal runs cannot be reset here.</p>}
        <section aria-label="Completed task checkpoints"><h4>Recorded checkpoints</h4>{detail.steps ? <><ol>{detail.steps.items.map(step => <li key={step.id}><strong>{step.step_key}</strong>: {step.state}; delivery {step.delivery_state}; attempt {step.attempt}, fence {step.fence}. Created {time(step.created_at)}; completed {time(step.completed_at)}{step.error_code && <>; safe reason <code>{step.error_code}</code></>}</li>)}</ol>{!detail.steps.items.length && <p>No checkpoints recorded. None are inferred from the task state.</p>}{detail.steps.next_cursor && <button className="secondary" disabled={!!historyBusy || busy} onClick={() => { void loadHistory('steps'); }}>Load older checkpoints</button>}</> : <p>Checkpoint details are unavailable in this response. Reload task status to inspect them.</p>}</section>
        <section aria-label="Task attempt history"><h4>Attempt events</h4>{detail.attempt_events ? <><ol>{detail.attempt_events.items.map(item => <li key={item.id}>{item.event.replaceAll('_', ' ')}; attempt {item.attempt}, fence {item.fence}; {time(item.created_at)}{item.error_code && <>; safe reason <code>{item.error_code}</code></>}</li>)}</ol>{!detail.attempt_events.items.length && <p>No attempt events recorded. Historical attempts are not reconstructed or invented.</p>}{detail.attempt_events.next_cursor && <button className="secondary" disabled={!!historyBusy || busy} onClick={() => { void loadHistory('attempt_events'); }}>Load older attempt events</button>}</> : <p>Attempt history is unavailable in this response. Reload task status to inspect it.</p>}</section>
        <section aria-label="Prepared action review"><h4>Exact prepared action</h4><button className="secondary" disabled={locked} onClick={() => { void inspectApproval(); }}>{approval || approvalMissing ? 'Reload prepared action' : 'Review prepared action'}</button>
          {approvalMissing && <p>{detail.kind === 'profile_import' ? 'No generic prepared action exists for a résumé import. Use the existing “Import a résumé” review to inspect source lines and make individual merge decisions.' : 'No prepared action exists for this task. No generic approval or proposal was fabricated. Use the existing requirements or document proposal editor for domain-specific review. A new destructive preview is available only when its actual domain capability exists.'}</p>}
          {approval && <><p>Applicability: {approval.reason}. {approvalHelp(approval)}</p><p>Capability: {approval.capability_available ? 'Available for this exact prepared action only' : 'Unavailable — approval cannot enable execution'}</p><Intent approval={approval} label="Reviewed prepared action" />
            {comparison && <><p className="disclosure">The server now reports a different prepared action or lifecycle. No approval was sent. Compare this current envelope with the one you reviewed.</p><Intent approval={comparison} label="Current server comparison" /><button className="secondary" disabled={locked} onClick={() => { setApproval(comparison); setComparison(null); setConfirmed(false); mutation.current = null; }}>Review this current preparation</button></>}
            {approval.action_kind !== 'hosted_egress' && <p>Destructive actions cannot be approved in Tasks. Open <a href="#owner-data-portability">Owner-data portability and complete owner-data review</a> to review the full preview, record counts, remaining copies, credential policy, owner identity and checkpoint/archive before confirming there.</p>}
            <label className="check"><input type="checkbox" checked={confirmed} disabled={locked || !approvalAllowed} onChange={event => setConfirmed(event.target.checked)} />I compared the exact payload, destination, target revision/version and limits, and confirm only this prepared action.</label>
            <button disabled={locked || !approvalAllowed || !confirmed || !!statusError} onClick={() => { void approve(); }}>Approve this exact action</button>
            <details><summary>Recent sign-in and bound-session recovery</summary><p>Approval requires a successful sign-in within five minutes, checked by the server clock. Signing in rotates the session and invalidates its old bound intent; obtain a genuine fresh domain preview afterwards. It never renews or rebinds an approval.</p><form onSubmit={event => { void reauthenticate(event); }}><label>Current password for task review<input type="password" name="task_password" autoComplete="current-password" required maxLength={128} /></label><button className="secondary" disabled={locked}>Sign in again for task review</button></form></details>
          </>}
        </section>
        {resultDocuments.length > 0 && <section aria-label="Task document results"><h4>Saved result</h4><p>Open explicitly for review. Task success is not factual approval. Unsaved workspace editors must be saved or discarded first.</p>{resultDocuments.map(item => <button key={item.document_id} className="secondary" disabled={busy || resultDisabled} onClick={() => callbacks.current.onOpenDocument(item.document_id, `${detail.id}-${crypto.randomUUID()}`)}>Open {item.kind === 'cover_letter' ? 'cover letter' : item.kind === 'resume' ? 'résumé' : 'document'} result</button>)}{resultDisabled && <p>Your current workspace has unsaved or active work. The result is retained; opening it will not replace that work.</p>}</section>}
      </>}
    </section>}
  </section>;
}
