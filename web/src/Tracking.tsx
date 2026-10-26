// SPDX-License-Identifier: MIT OR Apache-2.0
import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { Api, ApiError, errorText, type PostingPage, type PostingSummary } from './api';
import { MAX_ATTACHMENT_BYTES, attachmentDigest, stageName, stages, uploadAttachment, type ApplicationCounts, type ApplicationDetail, type ApplicationPage, type ApplicationSummary, type Appointment, type AppointmentWrite, type AttentionPage, type Stage, type StageEvent, type StagePage } from './tracking-api';

type Draft =
  | { kind: 'stage'; stage: Stage; effective: string; reason: string; correction: string; retained: string[] }
  | { kind: 'note'; id?: string; body: string }
  | { kind: 'contact'; id?: string; name: string; email: string; role: string }
  | { kind: 'appointment'; id?: string; actionKind: Appointment['kind']; title: string; start: string; end: string; zone: string; startOffset: string; endOffset: string }
  | { kind: 'timezone'; appointmentId: string; timezone: string }
  | { kind: 'status'; appointmentId: string; status: Appointment['status'] }
  | { kind: 'archive'; archived: boolean }
  | { kind: 'repeat' }
  | { kind: 'attachment'; file: File | null };
interface Filters { q: string; stage: string; archived: string }
const emptyFilters: Filters = { q: '', stage: '', archived: 'active' };
const utcInput = (value: string) => value.slice(0, 16);
const withSeconds = (value: string) => value.length === 16 ? `${value}:00` : value;
const nowInput = () => utcInput(new Date().toISOString());
const closed = (stage: Stage) => stage === 'rejected' || stage === 'withdrawn';
const aborted = (failure: unknown) => failure instanceof DOMException && failure.name === 'AbortError';
function monthBounds() {
  const now = new Date();
  return { start: new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1)).toISOString().slice(0, 10), end: new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + 1, 1)).toISOString().slice(0, 10) };
}
function AppointmentWords({ value }: { value: Appointment }) {
  return <><p><strong>{value.title}</strong> · {value.kind === 'interview' ? 'Interview' : 'Follow-up'} · {value.status}</p>
    <p>Display: <time dateTime={value.display_start}>{value.display_start}</time>{value.display_end && <> to <time dateTime={value.display_end}>{value.display_end}</time></>} ({value.display_timezone}).</p>
    <p className="hint">Original intent: {value.local_start} to {value.local_end ?? 'no end supplied'} in {value.timezone}. UTC: {value.starts_at} to {value.ends_at ?? 'no end supplied'}{value.duration_minutes !== null ? `; ${value.duration_minutes} elapsed minutes` : ''}.</p>
    {value.kind === 'interview' && !value.ends_at && <p className="disclosure">Legacy interview interval is incomplete. Edit it to supply an explicit end; no historical end has been invented.</p>}
    {value.completed_at && <p>Completed: {value.completed_at}</p>}{value.cancelled_at && <p>Cancelled: {value.cancelled_at}</p>}</>;
}
function PrivateComparison({ value }: { value: ApplicationDetail }) {
  return <><p>{stageName(value.current_stage)} · {value.archived_at ? 'Archived' : 'Active'} · attempt {value.attempt_no}.</p>
    <details><summary>Compare current saved notes, contacts and schedules</summary>
      <h5>Saved private notes</h5>{value.notes.length ? value.notes.map(note => <p className="import-source" key={note.id}>{note.body}</p>) : <p>No saved notes.</p>}
      <h5>Saved contacts</h5>{value.contacts.length ? value.contacts.map(contact => <p key={contact.id}>{contact.display_name}; {contact.email ?? 'email unknown'}; {contact.role_label ?? 'role unknown'}</p>) : <p>No saved contacts.</p>}
      <h5>Saved schedules</h5>{value.appointments.length ? value.appointments.map(item => <div key={item.id}><AppointmentWords value={item} /></div>) : <p>No saved schedules.</p>}
      <h5>Saved attachments</h5><ul>{value.attachments.map(item => <li key={item.id}>{item.display_name} · {item.byte_length} bytes</li>)}</ul>
    </details></>;
}
export function Tracking({ api, disabled = false, onDirtyChange, onBusyChange }: {
  api: Api; disabled?: boolean; onDirtyChange?: (value: boolean) => void; onBusyChange?: (value: boolean) => void;
}) {
  const [filters, setFilters] = useState<Filters>(emptyFilters);
  const [appliedFilters, setAppliedFilters] = useState<Filters>(emptyFilters);
  const [page, setPage] = useState<ApplicationPage | null>(null);
  const [listLoading, setListLoading] = useState(false);
  const [listError, setListError] = useState('');
  const [grouped, setGrouped] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ApplicationDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState('');
  const [history, setHistory] = useState<StageEvent[] | null>(null);
  const [historyCursor, setHistoryCursor] = useState<number | null>(null);
  const [historyError, setHistoryError] = useState('');
  const [historyLoading, setHistoryLoading] = useState(false);
  const [counts, setCounts] = useState<ApplicationCounts | null>(null);
  const [countError, setCountError] = useState('');
  const [period, setPeriod] = useState(monthBounds);
  const [countPeriod, setCountPeriod] = useState(monthBounds);
  const [attention, setAttention] = useState<AttentionPage | null>(null);
  const [attentionError, setAttentionError] = useState('');
  const [saved, setSaved] = useState<PostingPage | null>(null);
  const [savedError, setSavedError] = useState('');
  const [postingId, setPostingId] = useState('');
  const [draft, setDraft] = useState<Draft | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [comparison, setComparison] = useState<ApplicationDetail | null>(null);
  const [reconfirm, setReconfirm] = useState(false);
  const [pendingSelection, setPendingSelection] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const mounted = useRef(false);
  const controllers = useRef(new Set<AbortController>());
  const selection = useRef({ id: null as string | null, epoch: 0 });
  const live = useRef({ detail, draft }); live.current = { detail, draft };
  const callbacks = useRef({ onDirtyChange, onBusyChange }); callbacks.current = { onDirtyChange, onBusyChange };
  const listEpoch = useRef(0); const countEpoch = useRef(0); const attentionEpoch = useRef(0);
  const mutation = useRef<{ fingerprint: string; key: string } | null>(null);
  const fileIdentity = useRef<{ file: File; identity: string } | null>(null);
  const operation = useRef(false);
  const errorRef = useRef<HTMLDivElement>(null);
  const detailHeading = useRef<HTMLHeadingElement>(null);
  const editorHeading = useRef<HTMLHeadingElement>(null);
  const dirty = draft !== null;
  const locked = disabled || busy;
  const draftKind = draft?.kind;
  useEffect(() => { callbacks.current.onDirtyChange?.(dirty); }, [dirty]);
  useEffect(() => { callbacks.current.onBusyChange?.(busy); }, [busy]);
  useEffect(() => { if (error) errorRef.current?.focus(); }, [error]);
  useEffect(() => { if (draftKind) editorHeading.current?.focus(); }, [draftKind]);
  useEffect(() => {
    if (!dirty) return;
    const prevent = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    window.addEventListener('beforeunload', prevent);
    return () => window.removeEventListener('beforeunload', prevent);
  }, [dirty]);
  const request = useCallback(async <T,>(path: string, method = 'GET', body?: unknown, version?: number, key: boolean | string = false) => {
    const controller = new AbortController(); controllers.current.add(controller);
    try {
      const value = await api.request<T>(path, method, body, version, key, controller.signal);
      if (!mounted.current || controller.signal.aborted) throw new DOMException('Request stopped', 'AbortError');
      return value;
    } finally { controllers.current.delete(controller); }
  }, [api]);
  const loadList = useCallback(async (scope: Filters, cursor?: string) => {
    const epoch = ++listEpoch.current; setListLoading(true); setListError('');
    const query = new URLSearchParams();
    if (scope.q) query.set('q', scope.q); if (scope.stage) query.set('stage', scope.stage);
    if (scope.archived !== 'active') query.set('archived', scope.archived); if (cursor) query.set('cursor', cursor);
    try {
      const value = await request<ApplicationPage>(`/applications${query.size ? `?${query}` : ''}`);
      if (epoch !== listEpoch.current) return;
      setPage(previous => cursor && previous ? { ...value, items: [...previous.items, ...value.items.filter(item => !previous.items.some(old => old.id === item.id))] } : value);
      setAppliedFilters(scope);
    } catch (failure) { if (!aborted(failure) && epoch === listEpoch.current) setListError(errorText(failure)); }
    finally { if (mounted.current && epoch === listEpoch.current) setListLoading(false); }
  }, [request]);
  const loadCounts = useCallback(async (scope: { start: string; end: string }) => {
    const epoch = ++countEpoch.current; setCountError('');
    const query = new URLSearchParams({ period_start: `${scope.start}T00:00:00Z`, period_end: `${scope.end}T00:00:00Z` });
    try { const value = await request<ApplicationCounts>(`/applications/counts?${query}`); if (epoch === countEpoch.current) { setCounts(value); setCountPeriod(scope); } }
    catch (failure) { if (!aborted(failure) && epoch === countEpoch.current) setCountError(errorText(failure)); }
  }, [request]);
  const loadAttention = useCallback(async () => {
    const epoch = ++attentionEpoch.current; setAttentionError('');
    try { const value = await request<AttentionPage>('/applications/attention'); if (epoch === attentionEpoch.current) setAttention(value); }
    catch (failure) { if (!aborted(failure) && epoch === attentionEpoch.current) setAttentionError(errorText(failure)); }
  }, [request]);
  const initialPeriod = useRef(period);
  useEffect(() => {
    mounted.current = true;
    void loadList(emptyFilters); void loadCounts(initialPeriod.current); void loadAttention();
    const pending = controllers.current;
    return () => { mounted.current = false; for (const controller of pending) controller.abort(); callbacks.current.onDirtyChange?.(false); callbacks.current.onBusyChange?.(false); };
  }, [loadList, loadCounts, loadAttention]);
  async function loadHistory(id: string, epoch: number, before?: number) {
    setHistoryLoading(true); setHistoryError('');
    try {
      const value = await request<StagePage>(`/applications/${encodeURIComponent(id)}/stage-events${before === undefined ? '' : `?before=${before}`}`);
      if (selection.current.id !== id || selection.current.epoch !== epoch) return;
      setHistory(previous => before === undefined ? value.items : [...(previous ?? []), ...value.items.filter(item => !previous?.some(old => old.id === item.id))]); setHistoryCursor(value.next_cursor);
    } catch (failure) { if (!aborted(failure) && selection.current.id === id && selection.current.epoch === epoch) setHistoryError(errorText(failure)); }
    finally { if (mounted.current && selection.current.id === id && selection.current.epoch === epoch) setHistoryLoading(false); }
  }
  async function selectApplication(id: string) {
    if (operation.current || disabled) return;
    if (live.current.draft) { setPendingSelection(id); return; }
    const epoch = selection.current.epoch + 1; selection.current = { id, epoch };
    setSelectedId(id); setDetail(null); setDetailError(''); setDetailLoading(true); setHistory(null); setHistoryCursor(null); setHistoryError('');
    setComparison(null); setConflict(false); setPendingSelection(null); setError(''); setNotice('');
    void loadHistory(id, epoch);
    try { const value = await request<ApplicationDetail>(`/applications/${encodeURIComponent(id)}`); if (selection.current.id === id && selection.current.epoch === epoch) { setDetail(value); live.current.detail = value; } }
    catch (failure) { if (!aborted(failure) && selection.current.id === id && selection.current.epoch === epoch) setDetailError(errorText(failure)); }
    finally { if (mounted.current && selection.current.id === id && selection.current.epoch === epoch) setDetailLoading(false); }
  }
  function begin(value: Draft) { if (locked || dirty) return; setDraft(value); live.current.draft = value; setConfirmed(false); setReconfirm(false); setConflict(false); setComparison(null); setError(''); setNotice(''); }
  function change(value: Draft) { setDraft(value); live.current.draft = value; setConfirmed(false); }
  function discard() { setDraft(null); live.current.draft = null; setConfirmed(false); setReconfirm(false); setConflict(false); setComparison(null); mutation.current = null; fileIdentity.current = null; setError(''); detailHeading.current?.focus(); }
  async function compareCurrent() {
    const current = live.current.detail; if (!current || operation.current) return;
    const epoch = selection.current.epoch; setError('');
    try {
      const value = await request<ApplicationDetail>(`/applications/${encodeURIComponent(current.id)}`);
      if (selection.current.id !== current.id || selection.current.epoch !== epoch) return;
      if (value.version < (live.current.detail?.version ?? 0)) return;
      if (live.current.draft) { setComparison(existing => existing && existing.version > value.version ? existing : value); if (value.version > (live.current.detail?.version ?? 0)) setConflict(true); setConfirmed(false); }
      else { setDetail(value); live.current.detail = value; }
    } catch (failure) { if (!aborted(failure) && selection.current.id === current.id && selection.current.epoch === epoch) setError(errorText(failure)); }
  }
  async function loadSaved(cursor?: string) {
    if (locked) return;
    setSavedError('');
    try { const value = await request<PostingPage>(`/postings?disposition=saved${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`); setSaved(previous => cursor && previous ? { ...value, items: [...previous.items, ...value.items.filter(item => !previous.items.some(old => old.id === item.id))] } : value); }
    catch (failure) { if (!aborted(failure)) setSavedError(errorText(failure)); }
  }
  function keyFor(fingerprint: string) {
    if (mutation.current?.fingerprint !== fingerprint) mutation.current = { fingerprint, key: crypto.randomUUID() };
    return mutation.current.key;
  }
  async function mutate(path: string, method: string, body: unknown, version: number | undefined, file?: File) {
    if (operation.current || disabled || conflict || (reconfirm && !confirmed)) return;
    operation.current = true; setBusy(true); setError(''); setNotice('');
    const selected = selection.current.id; const epoch = selection.current.epoch;
    try {
      if (file && fileIdentity.current?.file !== file) fileIdentity.current = { file, identity: await attachmentDigest(file) };
      if (!mounted.current) throw new DOMException('Request stopped', 'AbortError');
      const key = keyFor(JSON.stringify({ path, method, body, version, file: file ? fileIdentity.current?.identity : null }));
      let value: ApplicationDetail & { created?: boolean };
      if (file) {
        const controller = new AbortController(); controllers.current.add(controller);
        try { value = await uploadAttachment(api, selected!, file, version!, key, controller.signal); if (!mounted.current || controller.signal.aborted) throw new DOMException('Request stopped', 'AbortError'); }
        finally { controllers.current.delete(controller); }
      } else value = await request<ApplicationDetail & { created?: boolean }>(path, method, body, version, key);
      if (selection.current.id !== selected || selection.current.epoch !== epoch) return;
      mutation.current = null; fileIdentity.current = null; setDraft(null); live.current.draft = null; setConfirmed(false); setReconfirm(false); setConflict(false); setComparison(null); setPendingSelection(null);
      selection.current = { id: value.id, epoch: epoch + 1 }; setSelectedId(value.id); setDetail(value); live.current.detail = value; setDetailError('');
      setNotice(value.created === false ? 'Existing latest attempt opened. No new application or delivery was created.' : 'Manual tracking saved. No message, application or calendar event was sent.');
      void loadHistory(value.id, epoch + 1); void loadList(appliedFilters); void loadCounts(countPeriod); void loadAttention();
      window.setTimeout(() => detailHeading.current?.focus(), 0);
    } catch (failure) {
      if (!aborted(failure) && selection.current.id === selected && selection.current.epoch === epoch) { setError(errorText(failure)); if (failure instanceof ApiError && failure.status === 412) { setConflict(true); setConfirmed(false); setComparison(null); } }
    } finally { operation.current = false; if (mounted.current) setBusy(false); }
  }
  function submit(event: FormEvent) {
    event.preventDefault(); if (!draft || !detail || conflict || (reconfirm && !confirmed)) return;
    const base = `/applications/${encodeURIComponent(detail.id)}`;
    switch (draft.kind) {
      case 'note': void mutate(`${base}/notes${draft.id ? `/${encodeURIComponent(draft.id)}` : ''}`, draft.id ? 'PUT' : 'POST', { body: draft.body }, detail.version); break;
      case 'contact': void mutate(`${base}/contacts${draft.id ? `/${encodeURIComponent(draft.id)}` : ''}`, draft.id ? 'PUT' : 'POST', { display_name: draft.name, email: draft.email || null, role_label: draft.role || null }, detail.version); break;
      case 'stage': if (confirmed) void mutate(`${base}/stage`, 'POST', { to_stage: draft.stage, effective_at: `${withSeconds(draft.effective)}Z`, reason: draft.reason, correction_of: draft.correction || null, retain_follow_up_ids: draft.retained }, detail.version); break;
      case 'appointment': {
        const body: AppointmentWrite = { kind: draft.actionKind, title: draft.title, local_start: withSeconds(draft.start), local_end: draft.end ? withSeconds(draft.end) : null, timezone: draft.zone,
          ...(draft.startOffset === '' ? {} : { utc_offset_minutes: Number(draft.startOffset) }), ...(draft.endOffset === '' ? {} : { end_utc_offset_minutes: Number(draft.endOffset) }) };
        void mutate(`${base}/appointments${draft.id ? `/${encodeURIComponent(draft.id)}` : ''}`, draft.id ? 'PUT' : 'POST', body, detail.version); break;
      }
      case 'timezone': void mutate(`${base}/appointments/${encodeURIComponent(draft.appointmentId)}/timezone`, 'POST', { timezone: draft.timezone }, detail.version); break;
      case 'status': if (confirmed) void mutate(`${base}/appointments/${encodeURIComponent(draft.appointmentId)}/status`, 'POST', { status: draft.status }, detail.version); break;
      case 'archive': if (confirmed) void mutate(`${base}/archive`, 'POST', { archived: draft.archived }, detail.version); break;
      case 'repeat': if (confirmed) void mutate(`${base}/repeat`, 'POST', { confirmed: true }, detail.version); break;
      case 'attachment': if (draft.file) {
        if (!draft.file.size || draft.file.size > MAX_ATTACHMENT_BYTES) { setError('Choose a nonempty PDF or UTF-8 text file no larger than 10 MiB. Your application is unchanged.'); return; }
        void mutate(`${base}/attachments`, 'POST', { display_name: draft.file.name }, detail.version, draft.file);
      } break;
    }
  }
  function editAppointment(value: Appointment) {
    // Persisted local intent is editable without deriving an offset from the browser's zone.
    begin({ kind: 'appointment', id: value.id, actionKind: value.kind, title: value.title, start: value.local_start, end: value.local_end ?? '', zone: value.timezone, startOffset: '', endOffset: '' });
  }
  const superseded = new Set(history?.flatMap(item => item.correction_of ? [item.correction_of] : []) ?? []);
  const correctionTarget = draft?.kind === 'stage' ? history?.find(item => item.id === draft.correction) : undefined;
  const historicalCorrection = !!correctionTarget && correctionTarget.id !== detail?.current_stage_event_id;
  const closingStage = draft?.kind === 'stage' && closed(draft.stage) && !historicalCorrection;
  function applicationButton(item: Pick<ApplicationSummary, 'id' | 'title' | 'company'>) {
    return <button className="secondary" disabled={locked} aria-pressed={selectedId === item.id} onClick={() => { void selectApplication(item.id); }}>Open {item.title} at {item.company}</button>;
  }
  function applicationRow(item: ApplicationSummary) {
    return <li key={item.id}>{applicationButton(item)}<p>{stageName(item.current_stage)} · attempt {item.attempt_no} · {item.archived_at ? 'Archived' : 'Active'}</p>
      {item.next_action && <p>Next action: {item.next_action.title} · {item.next_action.display_start} ({item.next_action.display_timezone})</p>}<small>Created {item.created_at}; updated {item.updated_at}</small></li>;
  }
  const editorTitle = draft?.kind === 'stage' ? 'Record stage or historical correction' : draft?.kind === 'note' ? 'Private note editor' : draft?.kind === 'contact' ? 'Contact editor' : draft?.kind === 'appointment' ? 'Local schedule editor' : draft?.kind === 'timezone' ? 'Change display timezone only' : draft?.kind === 'status' ? 'Confirm action status' : draft?.kind === 'archive' ? (draft.archived ? 'Archive this attempt' : 'Reactivate this attempt') : draft?.kind === 'repeat' ? 'Create a separate repeat attempt' : 'Retain an inert attachment';
  const needsConfirmation = draft && ['stage', 'status', 'archive', 'repeat'].includes(draft.kind);
  return <section className="panel tracking" aria-labelledby="tracking-title"><h2 id="tracking-title">Applications and activity</h2>
    <p>Your manual application dossier. Recording Applied does not send anything. Notes stay out of routine model context. Contacts and attachments may contain other people's personal data; add only what you have a lawful reason to keep.</p>
    <div ref={errorRef} role="alert" className="error" tabIndex={-1} hidden={!error}>{error}</div><p role="status" aria-label="Tracking confirmation" className="status">{notice}</p>
    <section aria-labelledby="tracking-list-title"><h3 id="tracking-list-title">Application list</h3>
      <form onSubmit={event => { event.preventDefault(); void loadList(filters); }}><fieldset disabled={locked || listLoading}><legend>Filter applications</legend><div className="catalog-filters">
        <label>Search application title or company<input value={filters.q} maxLength={300} onChange={event => setFilters({ ...filters, q: event.target.value })} /></label>
        <label>Filter stage<select value={filters.stage} onChange={event => setFilters({ ...filters, stage: event.target.value })}><option value="">All stages</option>{stages.map(stage => <option key={stage} value={stage}>{stageName(stage)}</option>)}</select></label>
        <label>Archive scope<select value={filters.archived} onChange={event => setFilters({ ...filters, archived: event.target.value })}><option value="active">Active only</option><option value="archived">Archived only</option><option value="all">Active and archived</option></select></label>
      </div><div className="actions"><button>Apply application filters</button><button type="button" className="secondary" onClick={() => { setFilters(emptyFilters); void loadList(emptyFilters); }}>Clear application filters</button></div></fieldset></form>
      <button className="secondary" disabled={locked || listLoading} onClick={() => { void loadList(appliedFilters); }}>Refresh applications</button>
      {listError && <p className="error" role="status">Application list unavailable. {listError} {page ? 'Previously loaded rows remain visible and are stale; their scope is unchanged.' : 'This is not an empty list.'}</p>}
      {listLoading && <p role="status">Loading applications…</p>}
      {page && <><p>{page.items.length} loaded applications; {appliedFilters.archived} archive scope, {appliedFilters.stage || 'all stages'}, search {appliedFilters.q || 'not restricted'}. Snapshot {page.as_of}. Loaded rows are not an all-owner total.</p>
        <label className="check"><input type="checkbox" checked={grouped} onChange={event => setGrouped(event.target.checked)} />Group loaded subset by stage (not a complete board)</label>
        {!page.items.length ? <p>No applications in this loaded scope. Clear filters or track a saved posting below.</p> : grouped ? <div className="tracking-board" aria-label="Stage groups of loaded subset">{stages.map(stage => <section key={stage}><h4>{stageName(stage)} · {page.items.filter(item => item.current_stage === stage).length} loaded</h4><ul>{page.items.filter(item => item.current_stage === stage).map(applicationRow)}</ul></section>)}</div> : <ul className="tracking-list">{page.items.map(applicationRow)}</ul>}
        {page.next_cursor && <button className="secondary" disabled={locked || listLoading} onClick={() => { void loadList(appliedFilters, page.next_cursor!); }}>Load more applications</button>}</>}
    </section>
    <details><summary>Track a saved posting manually</summary><p>This opens the latest existing attempt, or creates the first Preparing attempt. Saving a posting alone does not create an application. No model or sending provider is needed.</p>
      <button className="secondary" disabled={locked || dirty} onClick={() => { void loadSaved(); }}>Load saved postings for tracking</button>
      {savedError && <p role="status" className="error">Saved posting choices unavailable. {savedError} {saved ? 'Earlier choices may be stale.' : 'No empty result is assumed.'}</p>}
      {saved && <><label>Saved posting to track<select disabled={locked || dirty} value={postingId} onChange={event => setPostingId(event.target.value)}><option value="">Choose a saved posting</option>{saved.items.map((item: PostingSummary) => <option key={item.id} value={item.id}>{item.title} at {item.company}</option>)}</select></label>
        {!saved.items.length && <p>No saved postings in this page. Save a posting in Opportunities and job sources first.</p>}
        {saved.next_cursor && <button className="secondary" disabled={locked || dirty} onClick={() => { void loadSaved(saved.next_cursor!); }}>Load more saved postings</button>}
        <button disabled={locked || dirty || !postingId} onClick={() => { void mutate('/applications', 'POST', { posting_id: postingId }, undefined); }}>Track selected posting manually</button></>}
    </details>
    <details><summary>All-owner counts and period activity</summary><p>Current counts include every owner application, independent of this list, filters and pagination. Active and archived counts are separate. Period counts include archived attempts; stage counts are distinct applications reaching each stage, with superseded events excluded. No percentages or delivery counts.</p>
      <form onSubmit={event => { event.preventDefault(); if (period.start >= period.end) { setCountError('Choose an exclusive end date after the start date. Earlier counts are unchanged.'); return; } void loadCounts(period); }}><div className="catalog-filters"><label>Period start date (UTC, inclusive)<input type="date" required value={period.start} onChange={event => setPeriod({ ...period, start: event.target.value })} /></label><label>Period end date (UTC, exclusive)<input type="date" required value={period.end} onChange={event => setPeriod({ ...period, end: event.target.value })} /></label></div><button disabled={locked}>Refresh all-owner counts</button></form>
      {countError && <p role="status" className="error">Counts unavailable, not zero. {countError} {counts ? 'Earlier counts below are stale and retain their original period.' : ''}</p>}
      {!counts && !countError && <p>Loading all-owner counts…</p>}
      {counts && <><p>Period {counts.period_start} inclusive to {counts.period_end} exclusive. Created in period: {counts.created_in_period} (active and archived).</p>{counts.created_in_period === 0 && <p>No applications created in this period; existing applications can still have stage activity.</p>}
        <dl className="tracking-counts">{stages.map(stage => <div key={stage}><dt>{stageName(stage)}</dt><dd>Active current: {counts.current_by_stage[stage]}; archived current: {counts.archived_by_stage[stage]}; reached in period: {counts.stage_reached_in_period[stage]}</dd></div>)}</dl></>}
    </details>
    <details><summary>Attention: due actions and upcoming interviews</summary><p>Pending due actions and interviews within seven days, excluding archived applications. Local reminders only.</p><button className="secondary" disabled={locked} onClick={() => { void loadAttention(); }}>Refresh attention list</button>
      {attentionError && <p className="error" role="status">Attention unavailable, not empty. {attentionError} {attention ? 'Earlier reminders below may be stale.' : ''}</p>}
      {attention && <><p>Server snapshot {attention.as_of}. Bounded attention list, not an all-owner total.</p>{!attention.items.length ? <p>No due actions or upcoming interviews in this attention response.</p> : <ul>{attention.items.map(item => <li key={item.appointment.id}>{applicationButton({ id: item.application_id, title: item.title, company: item.company })}<p>{Date.parse(item.appointment.starts_at) <= Date.parse(attention.as_of) ? 'Due action' : 'Upcoming interview'}</p><AppointmentWords value={item.appointment} /></li>)}</ul>}</>}
    </details>
    {pendingSelection && <section className="disclosure" aria-label="Unsaved tracking selection"><h3>Keep or discard this tracking buffer</h3><p>Your unsaved editor is still attached to this attempt. Changing selection cannot silently discard it.</p><div className="actions"><button className="secondary" disabled={locked} onClick={() => setPendingSelection(null)}>Keep editing this attempt</button><button disabled={locked} onClick={() => { const id = pendingSelection; discard(); setPendingSelection(null); void selectApplication(id); }}>Discard buffer and open selected application</button></div></section>}
    {selectedId && <section className="tracking-dossier" aria-labelledby="tracking-detail-title"><h3 id="tracking-detail-title" ref={detailHeading} tabIndex={-1}>{detail ? `${detail.title} at ${detail.company} · attempt ${detail.attempt_no}` : 'Selected application'}</h3>
      {detailLoading && <p role="status">Loading saved application…</p>}{detailError && <p role="status" className="error">Application detail unavailable. {detailError}</p>}
      {!detail && !detailLoading && <button className="secondary" disabled={locked || dirty} onClick={() => { void selectApplication(selectedId); }}>Retry selected application</button>}
      {detail && <><p><strong>{stageName(detail.current_stage)}</strong> · {detail.archived_at ? `Archived ${detail.archived_at}` : 'Active'} · saved version {detail.version}.</p><p>Application {detail.id}. Created {detail.created_at}; updated {detail.updated_at}.</p>
        <div className="actions"><button className="secondary" disabled={locked} onClick={() => { void compareCurrent(); }}>{dirty ? 'Load current application for comparison' : 'Refresh selected application'}</button>
          <button disabled={locked || dirty} onClick={() => begin({ kind: 'stage', stage: detail.current_stage, effective: nowInput(), reason: '', correction: '', retained: [] })}>Change stage or reopen same attempt</button>
          <button className="secondary" disabled={locked || dirty} onClick={() => begin({ kind: 'repeat' })}>Start a separate repeat attempt</button>
          <button className="secondary" disabled={locked || dirty} onClick={() => begin({ kind: 'archive', archived: !detail.archived_at })}>{detail.archived_at ? 'Reactivate archived attempt' : 'Archive attempt'}</button></div>
        <p className="hint">Reopening is a stage change on this same attempt. A repeat is a separately confirmed new Preparing pursuit. Reactivation changes archive visibility only, never the stage.</p>
        <section aria-labelledby="tracking-history-title"><h4 id="tracking-history-title">Append-only stage history</h4><p>Newest recorded first. Effective time determines period reporting; recorded time tells when you entered the event. Older events are retained after correction.</p>
          <button className="secondary" disabled={locked || historyLoading} onClick={() => { void loadHistory(detail.id, selection.current.epoch); }}>Refresh stage history</button>
          {historyError && <p className="error" role="status">Stage history unavailable. {historyError} {history ? 'Earlier loaded events may be stale.' : ''}</p>}
          {historyLoading && <p role="status">Loading stage history…</p>}
          {history && <ol className="tracking-history">{history.map(item => <li key={item.id}><p><strong>Event {item.sequence}: {item.from_stage ? stageName(item.from_stage) : 'Initial'} to {stageName(item.to_stage)}</strong>{superseded.has(item.id) ? ' · superseded by a loaded correction' : ''}</p><p>Effective <time dateTime={item.effective_at}>{item.effective_at}</time>; recorded <time dateTime={item.recorded_at}>{item.recorded_at}</time>.</p><p className="import-source">{item.reason || 'No reason supplied.'}</p><p>{item.correction_of ? `Corrects event ${item.correction_of}. ` : ''}{item.affects_current_stage ? 'Changed the current-stage projection when recorded.' : 'Historical-only correction; current stage unchanged.'} Event ID {item.id}.</p>
            <button className="secondary" disabled={locked || dirty || superseded.has(item.id)} onClick={() => begin({ kind: 'stage', stage: item.to_stage, effective: utcInput(new Date(item.effective_at).toISOString()), reason: '', correction: item.id, retained: [] })}>Correct event {item.sequence}</button></li>)}</ol>}
          {historyCursor !== null && <button className="secondary" disabled={locked || historyLoading} onClick={() => { void loadHistory(detail.id, selection.current.epoch, historyCursor); }}>Load older stage events</button>}
        </section>
        <section aria-labelledby="tracking-notes-title"><h4 id="tracking-notes-title">Private notes</h4><button className="secondary" disabled={locked || dirty} onClick={() => begin({ kind: 'note', body: '' })}>Add private note</button>{!detail.notes.length ? <p>No private notes saved.</p> : <ul>{detail.notes.map((item, index) => <li key={item.id}><p className="import-source">{item.body}</p><small>Updated {item.updated_at}</small><button className="secondary" disabled={locked || dirty} onClick={() => begin({ kind: 'note', id: item.id, body: item.body })}>Edit private note {index + 1}</button></li>)}</ul>}</section>
        <section aria-labelledby="tracking-contacts-title"><h4 id="tracking-contacts-title">Contacts</h4><button className="secondary" disabled={locked || dirty} onClick={() => begin({ kind: 'contact', name: '', email: '', role: '' })}>Add contact</button>{!detail.contacts.length ? <p>No contacts saved. Unknown email stays blank.</p> : <ul>{detail.contacts.map(item => <li key={item.id}><p>{item.display_name} · {item.email ?? 'Email unknown'} · {item.role_label ?? 'Role unknown'}</p><button className="secondary" disabled={locked || dirty} onClick={() => begin({ kind: 'contact', id: item.id, name: item.display_name, email: item.email ?? '', role: item.role_label ?? '' })}>Edit contact {item.display_name}</button></li>)}</ul>}</section>
        <section aria-labelledby="tracking-schedule-title"><h4 id="tracking-schedule-title">Follow-ups and local interviews</h4><p>No external calendar is connected. Completion and cancellation are explicit, retained records. Rejected or withdrawn attempts must be reopened before scheduling or reactivating an action.</p>
          <div className="actions">{(['follow_up', 'interview'] as const).map(kind => <button className="secondary" key={kind} disabled={locked || dirty || closed(detail.current_stage)} onClick={() => begin({ kind: 'appointment', actionKind: kind, title: '', start: '', end: '', zone: 'UTC', startOffset: '', endOffset: '' })}>{kind === 'interview' ? 'Schedule local interview' : 'Add local follow-up'}</button>)}</div>
          {!detail.appointments.length ? <p>No local actions saved.</p> : <ul>{detail.appointments.map(item => <li key={item.id}><AppointmentWords value={item} /><div className="actions"><button className="secondary" disabled={locked || dirty || closed(detail.current_stage)} onClick={() => editAppointment(item)}>Edit schedule {item.title}</button><button className="secondary" disabled={locked || dirty} onClick={() => begin({ kind: 'timezone', appointmentId: item.id, timezone: item.display_timezone })}>Display timezone for {item.title}</button>
            {(['completed', 'cancelled', 'scheduled'] as const).filter(status => status !== item.status).map(status => <button className="secondary" key={status} disabled={locked || dirty || (status === 'scheduled' && closed(detail.current_stage))} onClick={() => begin({ kind: 'status', appointmentId: item.id, status })}>{status === 'completed' ? 'Complete' : status === 'cancelled' ? 'Cancel' : 'Reactivate'} action {item.title}</button>)}</div></li>)}</ul>}
        </section>
        <section aria-labelledby="tracking-attachments-title"><h4 id="tracking-attachments-title">Inert attachments</h4><p>PDF or UTF-8 text only, up to 10 MiB each; executables and archives are rejected. Files are retained without preview or execution. Acceptance is not malware certification. Download only if you trust the source and handle the file safely.</p><button className="secondary" disabled={locked || dirty} onClick={() => begin({ kind: 'attachment', file: null })}>Add inert attachment</button>
          {!detail.attachments.length ? <p>No attachments retained.</p> : <ul>{detail.attachments.map(item => <li key={item.id}><a className="tracking-download" href={`/api/v1/applications/${encodeURIComponent(detail.id)}/attachments/${encodeURIComponent(item.id)}/download`} download>{item.display_name} — opaque binary download, {item.byte_length} bytes</a><small>application/octet-stream · SHA-256 {item.sha256}</small></li>)}</ul>}
        </section>
        {draft && <section className="tracking-editor" aria-labelledby="tracking-editor-title"><h4 id="tracking-editor-title" ref={editorHeading} tabIndex={-1}>{editorTitle}</h4><p>Editing attempt {detail.attempt_no}; save precondition version {detail.version}. Offline failures retain this buffer for this visit; copy it before closing the browser.</p>
          <form onSubmit={submit}><fieldset disabled={locked}><legend>{editorTitle}</legend>
            {draft.kind === 'stage' && <><label>Stage event to correct<select value={draft.correction} onChange={event => { const target = history?.find(item => item.id === event.target.value); change({ ...draft, correction: event.target.value, stage: target?.to_stage ?? detail.current_stage, effective: target ? utcInput(new Date(target.effective_at).toISOString()) : nowInput(), retained: [] }); }}><option value="">New stage change / reopen this same attempt</option>{history?.map(item => <option key={item.id} value={item.id} disabled={superseded.has(item.id)}>Event {item.sequence}: {stageName(item.to_stage)} effective {item.effective_at}{superseded.has(item.id) ? ' (superseded)' : ''}</option>)}</select></label><p>Load older history pages to select any earlier unsuperseded event. A historical Applied correction can change period counts while the current Interview stage stays unchanged.</p>
              <label>Recorded stage<select value={draft.stage} onChange={event => change({ ...draft, stage: event.target.value as Stage, retained: [] })}>{stages.map(stage => <option key={stage} value={stage}>{stageName(stage)}</option>)}</select></label>
              <label>Effective date and time (UTC)<input type="datetime-local" step={60} required value={draft.effective} onChange={event => change({ ...draft, effective: event.target.value })} /></label><p>{historicalCorrection ? 'This correction changes historical reporting only, not the current stage or pending actions.' : 'This event changes the current stage of this same attempt.'}</p>
              <label>Stage reason{draft.correction ? ' (required for correction)' : ''}<textarea required={!!draft.correction} maxLength={5000} value={draft.reason} onChange={event => change({ ...draft, reason: event.target.value })} /></label>
              {closingStage && <fieldset><legend>Closing pending actions</legend><p>Outstanding interviews and follow-ups will be cancelled. Explicitly retain only the distinct pending follow-ups you still need:</p>{detail.appointments.filter(item => item.kind === 'follow_up' && item.status === 'scheduled').map(item => <label className="check" key={item.id}><input type="checkbox" checked={draft.retained.includes(item.id)} onChange={event => change({ ...draft, retained: event.target.checked ? [...draft.retained, item.id] : draft.retained.filter(id => id !== item.id) })} />Retain follow-up {item.title}</label>)}</fieldset>}
              {draft.retained.length > 0 && <><p>Retained follow-up IDs: {draft.retained.join(', ')}. {historicalCorrection ? 'Historical-only corrections cannot retain pending actions; clear this selection before saving.' : 'After a rebase, only still-pending follow-ups can be retained.'}</p><button type="button" className="secondary" onClick={() => change({ ...draft, retained: [] })}>Clear retained follow-up selection</button></>}
              <p>Recording Applied is manual tracking, never proof of submission or delivery. Corrections append; they do not delete the original event.</p></>}
            {draft.kind === 'note' && <label>Private note text<textarea required value={draft.body} onChange={event => change({ ...draft, body: event.target.value })} aria-describedby="tracking-note-limit" /><span id="tracking-note-limit" className="hint">Up to 50,000 Unicode characters; literal private text, not HTML. Notes are excluded from routine model context.</span></label>}
            {draft.kind === 'contact' && <><label>Contact name<input required maxLength={500} value={draft.name} onChange={event => change({ ...draft, name: event.target.value })} /></label><label>Contact email (optional)<input type="email" maxLength={320} value={draft.email} onChange={event => change({ ...draft, email: event.target.value })} /></label><label>Contact role (optional)<input maxLength={200} value={draft.role} onChange={event => change({ ...draft, role: event.target.value })} /></label><p>Leave unknown email blank. Saving a contact never sends a message.</p></>}
            {draft.kind === 'appointment' && <><label>Local action type<select value={draft.actionKind} onChange={event => change({ ...draft, actionKind: event.target.value as Appointment['kind'] })}><option value="follow_up">Follow-up</option><option value="interview">Interview</option></select></label><label>Local action title<input required maxLength={500} value={draft.title} onChange={event => change({ ...draft, title: event.target.value })} /></label>
              <div className="catalog-filters"><label>Local start date and time<input type="datetime-local" required step={60} value={draft.start} onChange={event => change({ ...draft, start: event.target.value })} /></label><label>Local end date and time{draft.actionKind === 'interview' ? ' (required)' : ' (optional)'}<input type="datetime-local" required={draft.actionKind === 'interview'} step={60} value={draft.end} onChange={event => change({ ...draft, end: event.target.value })} /></label></div>
              <label>Original IANA timezone<input required value={draft.zone} maxLength={200} onChange={event => change({ ...draft, zone: event.target.value })} placeholder="America/New_York" /></label>
              <p>Each endpoint is resolved independently. Nonexistent spring-forward times reject. At a fall-back overlap, explicitly choose the UTC offset for each ambiguous endpoint. No browser offset is inferred. New York 2026-11-01 01:30 permits −240 (first occurrence) or −300 (second occurrence).</p>
              <div className="catalog-filters"><label>Start UTC offset in minutes (optional unless ambiguous)<input type="number" min={-840} max={840} step={1} value={draft.startOffset} onChange={event => change({ ...draft, startOffset: event.target.value })} /></label><label>End UTC offset in minutes (independent)<input type="number" min={-840} max={840} step={1} value={draft.endOffset} onChange={event => change({ ...draft, endOffset: event.target.value })} /></label></div><p>With an end, elapsed duration must be 1–1,440 whole minutes. Across midnight, select the next date explicitly. Changing the original schedule changes the intended instants; use Display timezone instead to preserve them.</p></>}
            {draft.kind === 'timezone' && <><label>Display IANA timezone<input required maxLength={200} value={draft.timezone} onChange={event => change({ ...draft, timezone: event.target.value })} /></label><p>Changes display only. Original local intent, original zone and UTC start/end remain unchanged.</p></>}
            {draft.kind === 'status' && <p>Set action {draft.appointmentId} to {draft.status}. This retains the record and does not contact anyone.</p>}
            {draft.kind === 'archive' && <p>{draft.archived ? 'Remove this attempt from active counts and attention. Keep its stage, outcomes, history, private notes, contacts, attachments and schedules.' : 'Return this attempt to active counts without reopening or changing its stage. All retained records remain.'}</p>}
            {draft.kind === 'repeat' && <p>Create a new, separate Preparing attempt for this saved opportunity. The current attempt, stage, history and children stay retained. This is not a same-attempt reopen. If a newer repeat already exists, reload rather than creating another accidentally.</p>}
            {draft.kind === 'attachment' && <><label>Attachment file<input type="file" onChange={event => change({ ...draft, file: event.target.files?.[0] ?? null })} /></label>{draft.file && <p>Selected: {draft.file.name} · {draft.file.size} bytes. No preview is generated.</p>}<p>PDF and literal UTF-8 text only. Maximum 10 MiB; no empty files, executable/archive content or unsafe filenames. Content remains private and is not added to routine model input.</p></>}
            {(needsConfirmation || reconfirm) && <label className="check"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />{reconfirm ? 'I compared the current saved version and reconfirm this exact retained change' : 'I confirm this exact manual tracking action; it sends nothing'}</label>}
            {conflict && <p className="error">The application changed elsewhere. Your buffer remains here. Load a comparison and explicitly rebase before reconfirming.</p>}
            <div className="actions"><button disabled={conflict || ((needsConfirmation || reconfirm) && !confirmed) || (draft.kind === 'attachment' && !draft.file)}>Save manual tracking change</button><button type="button" className="secondary" onClick={discard}>Discard tracking buffer</button><button type="button" className="secondary" onClick={() => { void compareCurrent(); }}>Load current application for comparison</button></div>
          </fieldset></form>
          {comparison && <section className="comparison" aria-labelledby="tracking-comparison-title"><h5 id="tracking-comparison-title">Current saved application · version {comparison.version}</h5><PrivateComparison value={comparison} /><p>Your local editor above is unchanged. Selecting this version keeps your text and schedule, but clears confirmation; review and reconfirm before saving. Newly conflicting historical targets or retained follow-ups are validated again by the server.</p><button className="secondary" disabled={locked} onClick={() => { const latest = comparison; setDetail(latest); live.current.detail = latest; setComparison(null); setConflict(false); setReconfirm(true); setConfirmed(false); mutation.current = null; setNotice('Current application version selected explicitly. Your retained buffer needs fresh confirmation.'); }}>Use current version and retain my tracking buffer</button></section>}
        </section>}
      </>}
    </section>}
  </section>;
}
