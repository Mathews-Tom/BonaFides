// SPDX-License-Identifier: MIT OR Apache-2.0
import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { Api, ApiError, errorText, terminal, type ObservationDetail, type ObservationPage, type ObservationSummary, type PostingDetail, type PostingPage, type PostingSummary, type PostingWrite, type Source, type SourcePage, type SourceRefreshAccepted, type SourceWrite, type Workflow } from './api';

export interface JobSourcesProps {
  api: Api; disabled?: boolean; onDirtyChange?: (dirty: boolean) => void; onBusyChange?: (busy: boolean) => void;
}
interface SourceEditor { id?: string; version?: number; draft: SourceWrite; baseline: string }
interface RefreshRun { id: string; sourceId: string | null; workflow: Workflow | null }
const emptyPosting = (): PostingWrite => ({ title: '', company: '', location_text: '', description_text: '', canonical_url: '' });
const sourceDraft = (source: Source): SourceWrite => ({ source_kind: 'greenhouse', board_token: source.board_token ?? '', company: source.company, terms_url: source.terms_url, terms_revision: source.terms_revision, terms_review_acknowledged: false, enabled: source.enabled, request_limit_per_minute: source.request_limit_per_minute });

// References are displayed only, never fetched. Server validation remains authoritative.
function reference(value: string | null): string | null {
  if (!value || /[\s\\]/.test(value) || !/^https:\/\//i.test(value)) return null;
  for (const character of value) {
    const point = character.charCodeAt(0);
    if (point < 32 || point === 127) return null;
  }
  const authority = value.slice(value.indexOf('//') + 2).split(/[/?#]/, 1)[0] ?? '';
  if (authority.includes('%')) return null;
  try {
    const url = new URL(value);
    const host = url.hostname.toLowerCase();
    if (url.protocol !== 'https:' || url.username || url.password || (url.port && url.port !== '443') || !host.includes('.') || /^[\d.]+$/.test(host) || host.includes(':') || /(^|\.)(localhost|local|internal|invalid|test|onion|home|lan)$/.test(host)) return null;
    return url.href;
  } catch { return null; }
}
function Reference({ value, label }: { value: string | null; label: string }) {
  const href = reference(value);
  return href ? <a href={href} target="_blank" rel="noreferrer noopener">{label}</a> : value ? <p>{label} (not an outbound link): <code>{value}</code></p> : null;
}
function SourceFacts({ source }: { source: Source }) {
  return <><p>Health: {source.health.replaceAll('_', ' ')}. {source.enabled ? 'Enabled' : 'Disabled'}. Policy version {source.policy_version}; source version {source.version}.</p>
    <p>Last successful read: {source.last_success_at ?? 'Never'}. Next allowed read: {source.rate_limit_blocked ? 'No representable permitted retry time; source remains paused' : source.next_allowed_at ?? 'Not scheduled'}.</p>
    <p className="hint">Freshness describes the last successful collection, not a live guarantee that the employer still publishes every retained role.</p>
    <p>Provider quota: unknown. Your own request limit: {source.request_limit_per_minute} per minute. This is not a provider allowance.</p>
    {source.retry_after_seconds !== null && <p>Retained provider minimum wait: <code>{source.retry_after_seconds}</code> seconds. Changing review or enablement cannot shorten this minimum.</p>}
    {source.last_error_code && <p>Last source failure: {source.last_error_code}. Retained postings and observations are unchanged by a failed read.</p>}
    <p>Board: {source.board_token}. Allowed upstream host: {source.allowed_host ?? 'Not reviewed'}. Terms revision: {source.terms_revision ?? 'Not reviewed'}; reviewed at {source.terms_reviewed_at ?? 'Never'}.</p>
    <Reference value={source.terms_url} label="Reviewed board terms" /></>;
}

export function JobSources({ api, disabled = false, onDirtyChange, onBusyChange }: JobSourcesProps) {
  const [posting, setPosting] = useState<PostingWrite>(emptyPosting);
  const [postings, setPostings] = useState<PostingSummary[] | null>(null);
  const [postingCursor, setPostingCursor] = useState<string | null>(null);
  const [catalogError, setCatalogError] = useState('');
  const [sources, setSources] = useState<Source[] | null>(null);
  const [sourceError, setSourceError] = useState('');
  const [detail, setDetail] = useState<PostingDetail | null>(null);
  const [observations, setObservations] = useState<ObservationSummary[] | null>(null);
  const [observationCursor, setObservationCursor] = useState<string | null>(null);
  const [observation, setObservation] = useState<ObservationDetail | null>(null);
  const [editor, setEditor] = useState<SourceEditor | null>(null);
  const [comparison, setComparison] = useState<Source | null>(null);
  const [conflict, setConflict] = useState(false);
  const [run, setRun] = useState<RefreshRun | null>(null);
  const [savedRuns, setSavedRuns] = useState<Workflow[] | null>(null);
  const [pollPaused, setPollPaused] = useState(false);
  const [pollGeneration, setPollGeneration] = useState(0);
  const [completionPending, setCompletionPending] = useState(false);
  const [operation, setOperation] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [now, setNow] = useState(() => Date.now());
  const [retryAt, setRetryAt] = useState<Record<string, number>>({});
  const [blockedPolicies, setBlockedPolicies] = useState<Record<string, number>>({});
  const mutationKeys = useRef(new Map<string, { fingerprint: string; key: string }>());
  const controllers = useRef(new Set<AbortController>());
  const mounted = useRef(false);
  const operationLock = useRef(false);
  const pollCount = useRef({ id: '', generation: -1, count: 0 });
  const completed = useRef(new Set<string>());
  const catalogRead = useRef(0);
  const sourceRead = useRef(0);
  const errorRef = useRef<HTMLDivElement>(null);
  const callbacks = useRef({ onDirtyChange, onBusyChange });
  callbacks.current = { onDirtyChange, onBusyChange };
  const manualDirty = Object.values(posting).some(value => typeof value === 'string' && value.length > 0);
  const sourceDirty = !!editor && JSON.stringify(editor.draft) !== editor.baseline;
  const dirty = manualDirty || sourceDirty;
  const activeRun = !!run && (!run.workflow || !terminal(run.workflow.state));
  const busy = operation || activeRun || completionPending;
  const locked = disabled || operation;
  const runId = run?.id;
  const runState = run?.workflow?.state;

  useEffect(() => {
    mounted.current = true;
    const pendingControllers = controllers.current;
    return () => {
      mounted.current = false;
      for (const controller of pendingControllers) controller.abort();
      callbacks.current.onDirtyChange?.(false); callbacks.current.onBusyChange?.(false);
    };
  }, []);
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => { onBusyChange?.(busy); }, [busy, onBusyChange]);
  useEffect(() => { if (error) errorRef.current?.focus(); }, [error]);
  useEffect(() => {
    if (!dirty && !busy) return;
    const prevent = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener('beforeunload', prevent);
    return () => window.removeEventListener('beforeunload', prevent);
  }, [dirty, busy]);
  useEffect(() => {
    // Keep overdue deadlines until the rendered clock observes their expiry.
    const future = [...(sources ?? []).map(source => Date.parse(source.next_allowed_at ?? '')), ...Object.values(retryAt)].filter(time => Number.isFinite(time) && time > now);
    if (!future.length || document.hidden) return;
    const timer = setTimeout(() => setNow(Date.now()), Math.min(Math.max(Math.min(...future) - Date.now(), 1), 60_000));
    return () => clearTimeout(timer);
  }, [sources, retryAt, now]);
  useEffect(() => {
    const visible = () => { if (!document.hidden) setNow(Date.now()); };
    document.addEventListener('visibilitychange', visible);
    return () => document.removeEventListener('visibilitychange', visible);
  }, []);

  const request = useCallback(async <T,>(signal: AbortSignal, path: string, method = 'GET', body?: unknown, version?: number, key: boolean | string = false): Promise<T> => {
    const value = await api.request<T>(path, method, body, version, key, signal);
    if (!mounted.current || signal.aborted) throw new DOMException('Request stopped', 'AbortError');
    return value;
  }, [api]);
  const receiveSource = useCallback((source: Source) => {
    sourceRead.current += 1;
    setSources(existing => existing ? existing.some(item => item.id === source.id) ? existing.map(item => item.id === source.id ? source : item) : [...existing, source] : [source]);
  }, []);
  const reloadCatalog = useCallback(async (signal: AbortSignal) => {
    const generation = ++catalogRead.current;
    try {
      const page = await request<PostingPage>(signal, '/postings');
      if (catalogRead.current === generation) { setPostings(page.items); setPostingCursor(page.next_cursor); setCatalogError(''); }
    } catch (failure) { if (!signal.aborted && mounted.current && catalogRead.current === generation) setCatalogError(errorText(failure)); throw failure; }
  }, [request]);
  const reloadSources = useCallback(async (signal: AbortSignal) => {
    const generation = ++sourceRead.current;
    try { const page = await request<SourcePage>(signal, '/sources'); if (sourceRead.current === generation) { setSources(page.items); setSourceError(''); } }
    catch (failure) { if (!signal.aborted && mounted.current && sourceRead.current === generation) setSourceError(errorText(failure)); throw failure; }
  }, [request]);
  useEffect(() => {
    let stopped = false;
    const controller = new AbortController(); controllers.current.add(controller);
    const timer = setTimeout(() => controller.abort(), 20_000);
    void Promise.allSettled([reloadCatalog(controller.signal), reloadSources(controller.signal)]).then(results => {
      clearTimeout(timer); controllers.current.delete(controller);
      if (!stopped && mounted.current && controller.signal.aborted) {
        if (results[0]?.status === 'rejected') setCatalogError(existing => existing || 'Initial catalog request stopped. Reload the catalog to try again.');
        if (results[1]?.status === 'rejected') setSourceError(existing => existing || 'Initial source request stopped. Reload sources to try again.');
      }
    });
    return () => { stopped = true; controller.abort(); clearTimeout(timer); };
  }, [reloadCatalog, reloadSources]);

  async function perform(action: (signal: AbortSignal) => Promise<void>, sourceConflict = false) {
    if (operationLock.current || disabled) return;
    operationLock.current = true; setOperation(true); setError(''); setNotice('');
    const controller = new AbortController(); controllers.current.add(controller);
    const timer = setTimeout(() => controller.abort(), 20_000);
    try { await action(controller.signal); }
    catch (failure) {
      if (!mounted.current) return;
      if (sourceConflict && failure instanceof ApiError && failure.status === 412) setConflict(true);
      setError(controller.signal.aborted ? 'The request stopped before its result was confirmed. Your buffers and retry identity remain here; check or retry the operation.' : errorText(failure));
    } finally {
      clearTimeout(timer); controllers.current.delete(controller); operationLock.current = false;
      if (mounted.current) setOperation(false);
    }
  }
  function keyFor(slot: string, path: string, body: unknown, version?: number) {
    const fingerprint = JSON.stringify({ path, body, version });
    const previous = mutationKeys.current.get(slot);
    if (previous?.fingerprint === fingerprint) return previous.key;
    const key = crypto.randomUUID(); mutationKeys.current.set(slot, { fingerprint, key }); return key;
  }
  async function createPosting(event: FormEvent) {
    event.preventDefault(); if (locked || sourceDirty) return;
    const body = { ...posting, canonical_url: posting.canonical_url || null };
    await perform(async signal => {
      const value = await request<PostingDetail>(signal, '/postings', 'POST', body, undefined, keyFor('posting', '/postings', body));
      mutationKeys.current.delete('posting'); setPosting(emptyPosting()); setDetail(value); setObservations(null); setObservation(null);
      catalogRead.current += 1;
      setPostings(existing => [value, ...(existing ?? []).filter(item => item.id !== value.id)]);
      setNotice('Manual posting saved with an attributable observation. No reference URL was fetched.');
    });
  }
  async function openPosting(id: string) {
    await perform(async signal => {
      const value = await request<PostingDetail>(signal, `/postings/${encodeURIComponent(id)}`);
      setDetail(value); setObservations(null); setObservation(null); setObservationCursor(null);
    });
  }
  async function loadObservations(cursor?: string) {
    if (!detail) return;
    await perform(async signal => {
      const page = await request<ObservationPage>(signal, `/postings/${encodeURIComponent(detail.id)}/observations${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`);
      setObservations(existing => cursor ? [...(existing ?? []), ...page.items.filter(item => !existing?.some(old => old.id === item.id))] : page.items); setObservationCursor(page.next_cursor);
    });
  }
  async function editSource(source: Source) {
    if (dirty || locked) return;
    await perform(async signal => {
      const latest = await request<Source>(signal, `/sources/${encodeURIComponent(source.id)}`); receiveSource(latest);
      const draft = sourceDraft(latest); setEditor({ id: latest.id, version: latest.version, draft, baseline: JSON.stringify(draft) });
      setConflict(false); setComparison(null);
    });
  }
  function changeSource(update: Partial<SourceWrite>, changesReview = true) {
    setEditor(existing => existing ? { ...existing, draft: { ...existing.draft, ...update, ...(changesReview ? { terms_review_acknowledged: false } : {}) } } : existing);
  }
  async function saveSource(event: FormEvent) {
    event.preventDefault(); if (!editor || locked || conflict || manualDirty) return;
    const path = editor.id ? `/sources/${encodeURIComponent(editor.id)}` : '/sources';
    const body = editor.draft;
    await perform(async signal => {
      const value = await request<Source>(signal, path, editor.id ? 'PUT' : 'POST', body, editor.version, keyFor('source', path, body, editor.version));
      receiveSource(value); mutationKeys.current.delete('source'); setEditor(null); setComparison(null); setConflict(false);
      setNotice(value.enabled ? 'Reviewed source saved and explicitly enabled. A read starts only when you refresh.' : 'Source saved disabled. No board read was started.');
    }, true);
  }
  async function disableSource(source: Source) {
    const path = `/sources/${encodeURIComponent(source.id)}`;
    const body = { ...sourceDraft(source), enabled: false };
    await perform(async signal => {
      const value = await request<Source>(signal, path, 'PUT', body, source.version, keyFor(`disable:${source.id}`, path, body, source.version));
      receiveSource(value); mutationKeys.current.delete(`disable:${source.id}`);
      setNotice('Source disabled. Old policy work cannot publish; retained postings and observations remain available.');
    });
  }
  async function refreshSource(source: Source) {
    if (activeRun || dirty || locked) return;
    const path = `/sources/${encodeURIComponent(source.id)}/refresh`;
    await perform(async signal => {
      try {
        const value = await request<SourceRefreshAccepted>(signal, path, 'POST', {}, source.version, keyFor(`refresh:${source.id}`, path, {}, source.version));
        mutationKeys.current.delete(`refresh:${source.id}`); setCompletionPending(false); setRun({ id: value.workflow_id, sourceId: value.source_id, workflow: null });
        setPollPaused(false); setPollGeneration(value => value + 1); setNotice('Source read accepted as a durable task. Existing catalog remains visible while it runs.');
      } catch (failure) {
        if (failure instanceof ApiError && failure.status === 429) {
          const next = failure.retryAfterSeconds === undefined ? NaN : Date.now() + failure.retryAfterSeconds * 1000;
          if (Number.isFinite(next) && next <= 8_640_000_000_000_000) setRetryAt(existing => ({ ...existing, [source.id]: next }));
          else setBlockedPolicies(existing => ({ ...existing, [source.id]: source.policy_version }));
          setNow(Date.now());
          try { await reloadSources(signal); } catch { /* The original 429 stays the command error; source-read failure is shown separately. */ }
        }
        throw failure;
      }
    });
  }
  const receiveWorkflow = useCallback((workflow: Workflow) => {
    setRun(existing => existing && existing.id === workflow.id ? { ...existing, sourceId: existing.sourceId ?? workflow.result?.source_id ?? null, workflow } : existing);
    setSavedRuns(existing => existing?.map(item => item.id === workflow.id ? workflow : item) ?? null);
  }, []);
  useEffect(() => {
    if (!runId || (runState && terminal(runState)) || pollPaused) return;
    let stopped = false; let timer: ReturnType<typeof setTimeout> | undefined; let current: AbortController | undefined; let reading = false;
    if (pollCount.current.id !== runId || pollCount.current.generation !== pollGeneration) pollCount.current = { id: runId, generation: pollGeneration, count: 0 };
    const schedule = () => { if (!stopped && !document.hidden && !reading) timer = setTimeout(() => { void poll(); }, 1500); };
    const poll = async () => {
      if (stopped || document.hidden || reading) return;
      reading = true; current = new AbortController(); controllers.current.add(current);
      const timeout = setTimeout(() => current?.abort(), 20_000);
      try {
        const workflow = await request<Workflow>(current.signal, `/workflows/${encodeURIComponent(runId)}`);
        if (stopped) return;
        receiveWorkflow(workflow); pollCount.current.count += 1;
        if (terminal(workflow.state)) return;
        if (pollCount.current.count >= 120) { setPollPaused(true); setNotice('Automatic status checks paused after 120 requests. Check task status explicitly to resume.'); return; }
      } catch (failure) { if (!stopped) { setError(errorText(failure)); setPollPaused(true); } return; }
      finally { clearTimeout(timeout); if (current) controllers.current.delete(current); reading = false; }
      schedule();
    };
    const visibility = () => { clearTimeout(timer); if (document.hidden) return; schedule(); };
    document.addEventListener('visibilitychange', visibility); schedule();
    return () => { stopped = true; clearTimeout(timer); current?.abort(); document.removeEventListener('visibilitychange', visibility); };
  }, [runId, runState, pollPaused, pollGeneration, request, receiveWorkflow]);

  // Only an actual successful list reload clears the stale-catalog warning.
  useEffect(() => {
    if (!runId || !runState || !terminal(runState) || completed.current.has(runId)) return;
    const controller = new AbortController(); controllers.current.add(controller); setCompletionPending(true);
    const timer = setTimeout(() => controller.abort(), 20_000);
    let stopped = false;
    void Promise.allSettled([reloadCatalog(controller.signal), reloadSources(controller.signal)]).then(results => {
      if (stopped || !mounted.current) return;
      completed.current.add(runId); setCompletionPending(false);
      if (results.some(result => result.status === 'rejected')) setNotice('Task ended, but the latest catalog or source health could not be loaded. Earlier results remain visible; reload to reconcile.');
      else setNotice(runState === 'succeeded' ? 'Read completed. Latest retained catalog and source health loaded; absent posts were not deleted.' : 'Read ended without replacing retained catalog history. Latest source health loaded.');
    }).finally(() => { clearTimeout(timer); controllers.current.delete(controller); });
    return () => { stopped = true; controller.abort(); clearTimeout(timer); };
  }, [runId, runState, reloadCatalog, reloadSources]);
  async function checkRun() {
    if (!run) return;
    await perform(async signal => { receiveWorkflow(await request<Workflow>(signal, `/workflows/${encodeURIComponent(run.id)}`)); setPollPaused(false); setPollGeneration(value => value + 1); });
  }
  async function cancelRun() {
    if (!run?.workflow || terminal(run.workflow.state)) return;
    const path = `/workflows/${encodeURIComponent(run.id)}/cancel`; const version = run.workflow.version;
    await perform(async signal => {
      const workflow = await request<Workflow>(signal, path, 'POST', {}, version, keyFor('cancel', path, {}, version));
      receiveWorkflow(workflow); mutationKeys.current.delete('cancel'); setNotice('Cancellation recorded. Retained catalog and source observations remain available.');
    });
  }
  const source = editor?.draft;
  const reviewReady = !!source && !!source.company.trim() && /^[A-Za-z0-9_-]{1,120}$/.test(source.board_token) && !!reference(source.terms_url) && !!source.terms_revision?.trim();
  return <section className="panel job-sources" aria-labelledby="job-sources-title"><h2 id="job-sources-title">Opportunities and job sources</h2>
    <p>Keep complete posting text and attributable source history. Manual entries never fetch a URL. Board reads require your specific terms review; public API access is not permission to collect content.</p>
    {disabled && <p className="hint">Finish the other open operation before changing job postings or sources. Your buffers remain here.</p>}
    <div className="error" role="alert" tabIndex={-1} ref={errorRef} hidden={!error}>{error}</div><p role="status">{notice}</p>
    {operation && <p role="status">Waiting for the server. Your existing catalog and buffers stay visible.</p>}
    {completionPending && <p role="status">Reconciling the retained catalog and source health after the task ended…</p>}
    <form onSubmit={event => { void createPosting(event); }}><fieldset disabled={locked || sourceDirty}><legend>Add a manual posting</legend>
      <label>Job title<input required maxLength={500} value={posting.title} onChange={event => { const value = event.target.value; setPosting(existing => ({ ...existing, title: value })); }} /></label>
      <label>Employer<input required maxLength={500} value={posting.company} onChange={event => { const value = event.target.value; setPosting(existing => ({ ...existing, company: value })); }} /></label>
      <label>Location<input maxLength={2000} value={posting.location_text} onChange={event => { const value = event.target.value; setPosting(existing => ({ ...existing, location_text: value })); }} /></label>
      <label>Complete original posting text<textarea required maxLength={200000} value={posting.description_text} onChange={event => { const value = event.target.value; setPosting(existing => ({ ...existing, description_text: value })); }} /></label>
      <label>Optional HTTPS reference URL<input type="url" maxLength={2048} value={posting.canonical_url ?? ''} onChange={event => { const value = event.target.value; setPosting(existing => ({ ...existing, canonical_url: value })); }} /></label>
      <p className="hint">Plain text only. No requirements extraction, ranking or application is performed.</p>
      <div className="actions"><button>Save manual posting</button><button type="button" className="secondary" disabled={!manualDirty} onClick={() => { setPosting(emptyPosting()); mutationKeys.current.delete('posting'); }}>Discard manual buffer</button></div>
    </fieldset></form>
    <section aria-labelledby="catalog-title"><h3 id="catalog-title">Retained posting catalog</h3>
      <button className="secondary" disabled={locked} onClick={() => { void perform(reloadCatalog); }}>Reload posting catalog</button>
      {catalogError && <p className="error" role="status">{catalogError} {postings !== null ? 'Earlier loaded postings remain visible; this list may be stale.' : 'Catalog is unavailable, not known to be empty.'}</p>}
      {postings === null ? <p role="status">{catalogError ? 'Load the catalog again when the server is available.' : 'Loading posting catalog…'}</p> : postings.length === 0 ? <p>No retained postings. Paste a manual posting above or explicitly refresh a reviewed source below.</p> : <ul>{postings.map(item => <li key={item.id}><button className="secondary" disabled={locked} onClick={() => { void openPosting(item.id); }}>Read {item.title} at {item.company}</button><p>{item.location_text || 'Location not supplied'} · {item.status} · updated {item.updated_at}</p></li>)}</ul>}
      {postingCursor && <button className="secondary" disabled={locked} onClick={() => { void perform(async signal => { const page = await request<PostingPage>(signal, `/postings?cursor=${encodeURIComponent(postingCursor)}`); setPostings(existing => [...(existing ?? []), ...page.items.filter(item => !existing?.some(old => old.id === item.id))]); setPostingCursor(page.next_cursor); }); }}>Load more postings</button>}
      {detail && <article className="comparison" aria-labelledby="posting-detail-title"><h3 id="posting-detail-title">{detail.title}</h3><p>{detail.company} · {detail.location_text || 'Location not supplied'} · {detail.status}</p>
        <h4>Complete original posting text</h4><pre className="import-source">{detail.description_text}</pre><Reference value={detail.canonical_url} label="Original posting reference" />
        <p>Source {detail.source_config_id} ({detail.source_kind}); external ID {detail.external_id}. Last observed {detail.last_observed_at}.</p><p>Posting {detail.id} · SHA-256 <code>{detail.content_sha256}</code></p>
        <button className="secondary" disabled={locked} onClick={() => { void loadObservations(); }}>Load attributable observation history</button>
        {observations && (observations.length ? <ol>{observations.map(item => <li key={item.id}><button className="secondary" disabled={locked} onClick={() => { void perform(async signal => { setObservation(await request<ObservationDetail>(signal, `/postings/${encodeURIComponent(detail.id)}/observations/${encodeURIComponent(item.id)}`)); }); }}>Read observation {item.id}</button><p>{item.source_kind} · board {item.board_token ?? 'Manual owner entry'} · source {item.source_config_id} · external ID {item.external_id} · policy {item.policy_version} · fetched {item.fetched_at}</p><p>SHA-256 <code>{item.content_sha256}</code></p></li>)}</ol> : <p>No observations returned for this posting.</p>)}
        {observationCursor && <button className="secondary" disabled={locked} onClick={() => { void loadObservations(observationCursor); }}>Load earlier observations</button>}
        {observation && <section aria-labelledby="observation-detail-title"><h4 id="observation-detail-title">Exact retained observation {observation.id}</h4><p>Source {observation.source_config_id} · board {observation.board_token ?? 'Manual owner entry'} · external ID {observation.external_id} · policy {observation.policy_version} · fetched {observation.fetched_at} · retained {observation.created_at}</p><Reference value={observation.source_url} label="Observation source reference" /><p>SHA-256 <code>{observation.content_sha256}</code></p><pre className="import-source">{JSON.stringify(observation.snapshot, null, 2)}</pre></section>}
      </article>}
    </section>
    <section aria-labelledby="board-sources-title"><h3 id="board-sources-title">Configured Greenhouse boards</h3>
      <p>Only the exact configured board is read. Missing posts, an outage, or disabling a source never removes retained history. Provider quota is unknown; your own request policy is separate.</p>
      <div className="actions"><button className="secondary" disabled={locked} onClick={() => { void perform(reloadSources); }}>Reload sources</button><button className="secondary" disabled={locked || dirty} onClick={() => { const draft: SourceWrite = { source_kind: 'greenhouse', board_token: '', company: '', terms_url: null, terms_revision: null, terms_review_acknowledged: false, enabled: false, request_limit_per_minute: 1 }; setEditor({ draft, baseline: JSON.stringify(draft) }); setConflict(false); setComparison(null); }}>Add a Greenhouse board</button></div>
      {sourceError && <p className="error" role="status">{sourceError} {sources !== null ? 'Earlier source health remains visible and may be stale.' : 'Source list is unavailable, not known to be empty.'}</p>}
      {sources === null ? <p role="status">{sourceError ? 'Reload sources when the server is available.' : 'Loading source configurations…'}</p> : sources.filter(item => item.source_kind === 'greenhouse').length === 0 ? <p>No Greenhouse boards configured. Manual posting works without any board.</p> : <ul>{sources.filter(item => item.source_kind === 'greenhouse').map(item => {
        const next = Math.max(Date.parse(item.next_allowed_at ?? '') || 0, retryAt[item.id] ?? 0);
        const blocked = item.rate_limit_blocked || (!item.next_allowed_at && (item.health === 'rate_limited' || blockedPolicies[item.id] === item.policy_version));
        return <li key={item.id}><h4>{item.company} · {item.board_token}</h4><SourceFacts source={item} />
          {next > now && <p>Refresh is waiting until at least {new Date(next).toISOString()}. Retry-After is a minimum wait.</p>}
          {blocked && <p>No finite permitted retry time is available. Refresh stays blocked; review the source policy instead of shortening the upstream wait.</p>}
          <div className="actions"><button className="secondary" disabled={locked || dirty} onClick={() => { void editSource(item); }}>Review or edit {item.company} board</button>
            <button className="secondary" disabled={locked || dirty || !item.enabled} onClick={() => { void disableSource(item); }}>Disable {item.company} board</button>
            <button disabled={locked || dirty || activeRun || completionPending || blocked || !item.enabled || !item.terms_reviewed_at || next > now} onClick={() => { void refreshSource(item); }}>Refresh {item.company} board</button></div>
        </li>;
      })}</ul>}
      {editor && source && <form onSubmit={event => { void saveSource(event); }}><fieldset disabled={locked || manualDirty}><legend>{editor.id ? 'Review and edit configured board' : 'New board · disabled by default'}</legend>
        <label>Board employer<input required maxLength={500} value={source.company} onChange={event => changeSource({ company: event.target.value })} /></label>
        <label>Greenhouse board token<input required readOnly={!!editor.id} pattern="[A-Za-z0-9_-]{1,120}" maxLength={120} value={source.board_token} onChange={event => changeSource({ board_token: event.target.value })} /></label>
        <p className="hint">A saved board token is immutable. Configure a new source to use a different board; existing history keeps its attribution.</p>
        <label>Board-specific terms URL<input type="url" maxLength={2048} value={source.terms_url ?? ''} onChange={event => changeSource({ terms_url: event.target.value || null })} /></label>
        <label>Terms revision or dated review reference<input maxLength={500} value={source.terms_revision ?? ''} onChange={event => changeSource({ terms_revision: event.target.value || null })} /></label>
        <label>Your own maximum requests per minute<input type="number" required min={1} max={60} step={1} value={source.request_limit_per_minute} onChange={event => changeSource({ request_limit_per_minute: Number(event.target.value) })} /></label>
        <p>Review this employer, exact board token, terms URL and revision, allowed host boards-api.greenhouse.io, and your own rate policy. Public endpoint availability and robots rules do not establish collection rights. This action records your acknowledgement, not an automated legal approval.</p>
        <label className="check"><input type="checkbox" disabled={!reviewReady} checked={source.terms_review_acknowledged} onChange={event => changeSource({ terms_review_acknowledged: event.target.checked }, false)} />I reviewed these exact board-specific terms and policy and acknowledge permission for this configured board</label>
        <label className="check"><input type="checkbox" disabled={!source.enabled && !source.terms_review_acknowledged} checked={source.enabled} onChange={event => changeSource({ enabled: event.target.checked }, false)} />Explicitly enable this reviewed source</label>
        {conflict && <p className="error">The source changed elsewhere. Your board review and edited values remain here. Load and compare the current source before explicitly rebasing.</p>}
        <div className="actions"><button disabled={conflict || (source.enabled && (!reviewReady || !source.terms_review_acknowledged))}>Save board configuration</button><button type="button" className="secondary" onClick={() => { setEditor(null); setComparison(null); setConflict(false); mutationKeys.current.delete('source'); }}>Discard board buffer</button>
          {editor.id && <button type="button" className="secondary" onClick={() => { void perform(async signal => { setComparison(await request<Source>(signal, `/sources/${encodeURIComponent(editor.id!)}`)); }); }}>Load current source for comparison</button>}</div>
      </fieldset>
        {comparison && <section className="comparison" aria-labelledby="source-comparison-title"><h4 id="source-comparison-title">Current saved source · version {comparison.version}</h4><p>Employer: {comparison.company}. Board: {comparison.board_token}. Own limit: {comparison.request_limit_per_minute} per minute.</p><SourceFacts source={comparison} />
          <p>Your buffer above is unchanged. Rebasing only selects the current save precondition, clears your old acknowledgement and requires a fresh review before enablement.</p>
          <button type="button" className="secondary" disabled={locked} onClick={() => { const latest = comparison; setEditor(existing => existing ? { ...existing, version: latest.version, baseline: JSON.stringify(sourceDraft(latest)), draft: { ...existing.draft, terms_review_acknowledged: false } } : existing); receiveSource(latest); setConflict(false); setComparison(null); mutationKeys.current.delete('source'); setNotice('Current source version selected explicitly. Retained values need your review before saving.'); }}>Use current version and retain my board buffer</button>
        </section>}
      </form>}
    </section>
    <section className="saved-tasks" aria-labelledby="source-tasks-title"><h3 id="source-tasks-title">Durable source refresh tasks</h3>
      <button className="secondary" disabled={locked} onClick={() => { void perform(async signal => { const page = await request<{ items: Workflow[] }>(signal, '/workflows'); setSavedRuns(page.items.filter(item => item.kind === 'source_refresh')); }); }}>Load saved source refresh tasks</button>
      {savedRuns && (savedRuns.length ? <ul>{savedRuns.map(item => <li key={item.id}><button className="secondary" disabled={locked || completionPending || (activeRun && run?.id !== item.id)} onClick={() => { void perform(async signal => { const workflow = await request<Workflow>(signal, `/workflows/${encodeURIComponent(item.id)}`); setRun({ id: workflow.id, sourceId: workflow.result?.source_id ?? null, workflow }); setPollPaused(false); setPollGeneration(value => value + 1); }); }}>Open source refresh task {item.id}</button><p>{item.state.replaceAll('_', ' ')}</p></li>)}</ul> : <p>No source refresh tasks in the returned saved-task page.</p>)}
      {run && <div className="task" aria-live="polite"><h4>Source refresh task {run.id}</h4><p>Source: {run.sourceId ?? 'Source attribution is not present in this task response'}.</p>
        {run.workflow ? <><p>State: {run.workflow.state.replaceAll('_', ' ')}. Attempt {run.workflow.attempt}{run.workflow.max_attempts ? ` of ${run.workflow.max_attempts}` : ''}. No estimated completion percentage.</p>{run.workflow.next_attempt_at && !terminal(run.workflow.state) && <p>Next attempt no earlier than {run.workflow.next_attempt_at}.</p>}{run.workflow.last_error_code && <p>Source task failure: {run.workflow.last_error_code}. Prior catalog remains retained.</p>}
          {run.workflow.result?.excluded_prospect_count !== undefined && <p>Excluded prospect posts: {run.workflow.result.excluded_prospect_count}. Prospect posts are not imported as normal jobs.</p>}
          {run.workflow.result?.posting_count !== undefined && <p>Published normal jobs: {run.workflow.result.posting_count}; new {run.workflow.result.new_posting_count}, changed {run.workflow.result.changed_posting_count}, unchanged {run.workflow.result.unchanged_posting_count}. Previously retained absent posts remain unchanged.</p>}
          {run.workflow.result?.fetched_at && <p>Fetched at {run.workflow.result.fetched_at}; policy version {run.workflow.result.policy_version}.</p>}</> : <p>Task accepted. Current task state is not loaded yet.</p>}
        {pollPaused && <p>Automatic checks paused. The durable task may still be running; check status before deciding what to do.</p>}
        <div className="actions"><button className="secondary" disabled={locked} onClick={() => { void checkRun(); }}>Check refresh task status</button>{activeRun && <button className="secondary" disabled={locked || !run.workflow} onClick={() => { void cancelRun(); }}>Cancel source refresh</button>}</div>
      </div>}
    </section>
  </section>;
}
