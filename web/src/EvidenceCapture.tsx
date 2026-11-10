// SPDX-License-Identifier: MIT OR Apache-2.0
import { useEffect, useRef, useState } from 'react';
import { Api, ApiError, errorText, evidencePrivacyClasses, terminal, type CaptureApproval, type CaptureScan, type CaptureScanSummary, type CaptureSource, type EvidencePrivacyClass, type Profile } from './api';

type Intent = { path: string; version: number; key: string } & ({ kind: 'scan'; body: Record<string, never> } | { kind: 'approve'; body: CaptureApproval });
type Review = CaptureApproval & { version: number };

export function EvidenceCapture({ api, profile, active, disabled, onProfileChange, onDirtyChange, onBusyChange }: {
  api: Api; profile: Profile; active: boolean; disabled: boolean; onProfileChange: (value: Profile) => void;
  onDirtyChange: (value: boolean) => void; onBusyChange: (value: boolean) => void;
}) {
  const [sources, setSources] = useState<CaptureSource[] | null>(null);
  const [scans, setScans] = useState<CaptureScanSummary[]>([]);
  const [scanId, setScanId] = useState('');
  const [detail, setDetail] = useState<CaptureScan | null>(null);
  const [review, setReview] = useState<Review | null>(null);
  const [intent, setIntent] = useState<Intent | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [readGeneration, setReadGeneration] = useState(0);
  const [catalogGeneration, setCatalogGeneration] = useState(0);
  const [pollPaused, setPollPaused] = useState(false);
  const mounted = useRef(false);
  const operation = useRef(false);
  const controllers = useRef(new Set<AbortController>());
  const latestProfile = useRef(profile);
  latestProfile.current = profile;
  const callbacks = useRef({ onDirtyChange, onBusyChange, onProfileChange });
  callbacks.current = { onDirtyChange, onBusyChange, onProfileChange };
  const dirty = review !== null || intent !== null;
  useEffect(() => {
    const activeControllers = controllers.current;
    mounted.current = true;
    return () => { mounted.current = false; activeControllers.forEach(controller => controller.abort()); };
  }, []);
  useEffect(() => { callbacks.current.onDirtyChange(dirty); }, [dirty]);
  useEffect(() => { callbacks.current.onBusyChange(busy); }, [busy]);
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    void Promise.all([
      api.request<{ items: CaptureSource[] }>('/evidence-capture/sources', 'GET', undefined, undefined, false, controller.signal),
      api.request<{ items: CaptureScanSummary[] }>('/evidence-capture/scans', 'GET', undefined, undefined, false, controller.signal),
    ]).then(([configured, saved]) => {
      if (controller.signal.aborted) return;
      setSources(configured.items); setScans(saved.items);
      setScanId(current => current || saved.items.find(item => !terminal(item.state))?.id || saved.items[0]?.id || '');
    }).catch((failure: unknown) => { if (!controller.signal.aborted) setError(errorText(failure)); });
    return () => controller.abort();
  }, [api, active, catalogGeneration]);
  useEffect(() => {
    if (!active || !scanId) return;
    let stopped = false;
    let timer: number | undefined;
    let controller: AbortController | undefined;
    let reading = false;
    let reads = 0;
    const schedule = () => { if (!stopped && !document.hidden) timer = window.setTimeout(() => { void read(); }, 1500); };
    async function read() {
      if (stopped || reading || document.hidden) return;
      reading = true; controller = new AbortController();
      const timeout = setTimeout(() => controller?.abort(), 20_000);
      try {
        const value = await api.request<CaptureScan>(`/evidence-capture/scans/${encodeURIComponent(scanId)}`, 'GET', undefined, undefined, false, controller.signal);
        if (stopped) return;
        setDetail(value); reads += 1;
        if (!terminal(value.workflow.state) && reads < 120) schedule();
        else if (!terminal(value.workflow.state)) setPollPaused(true);
      } catch (failure) { if (!stopped) { setError(errorText(failure)); setPollPaused(true); } }
      finally { clearTimeout(timeout); reading = false; }
    }
    const visibility = () => { clearTimeout(timer); if (!document.hidden && !pollPaused) schedule(); };
    document.addEventListener('visibilitychange', visibility);
    if (!pollPaused) void read();
    return () => { stopped = true; clearTimeout(timer); controller?.abort(); document.removeEventListener('visibilitychange', visibility); };
  }, [api, active, scanId, readGeneration, pollPaused]);

  async function submit(request: Intent) {
    if (operation.current || disabled) return;
    operation.current = true; setBusy(true); setError(''); setNotice(''); setIntent(request);
    const controller = new AbortController(); controllers.current.add(controller);
    const timeout = setTimeout(() => controller.abort(), 20_000);
    try {
      if (request.kind === 'scan') {
        const accepted = await api.request<{ workflow_id: string }>(request.path, 'POST', request.body, request.version, request.key, controller.signal);
        if (!mounted.current || controller.signal.aborted) return;
        setIntent(null); setDetail(null); setScanId(accepted.workflow_id); setPollPaused(false);
        setReadGeneration(value => value + 1); setCatalogGeneration(value => value + 1);
      } else {
        const updated = await api.request<Profile>(request.path, 'POST', request.body, request.version, request.key, controller.signal);
        if (!mounted.current || controller.signal.aborted) return;
        setIntent(null); setReview(null);
        const body = request.body;
        setDetail(current => current && { ...current, profile_version: updated.version, proposals: current.proposals.map(item => item.id === body.candidate_id ? { ...item, already_approved: true } : item) });
        if (updated.id === latestProfile.current.id && updated.version >= latestProfile.current.version) callbacks.current.onProfileChange(updated);
        setNotice('Selected proposal approved as one source revision. Other proposals remain unapproved; no claim was created.');
      }
    } catch (failure) {
      if (!mounted.current) return;
      const uncertain = !(failure instanceof ApiError) || failure.status >= 500;
      if (!uncertain) setIntent(null);
      setError(`${errorText(failure)}${uncertain ? ' The outcome is uncertain. Retry only the original request below, or close it and check saved sources before making another decision.' : ''}`);
    } finally {
      clearTimeout(timeout); controllers.current.delete(controller); operation.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  async function loadLatestProfile() {
    if (operation.current || disabled || intent) return;
    operation.current = true; setBusy(true);
    const controller = new AbortController(); controllers.current.add(controller);
    const timeout = setTimeout(() => controller.abort(), 20_000);
    try {
      const value = await api.request<Profile>('/profile', 'GET', undefined, undefined, false, controller.signal);
      if (!mounted.current || controller.signal.aborted) return;
      if (value.id !== latestProfile.current.id) throw new Error('The workspace changed. Reconnect before reviewing capture proposals.');
      if (value.version >= latestProfile.current.version) callbacks.current.onProfileChange(value);
      setNotice('Latest profile loaded. Retained capture choices are unchanged; close and review again to authorize a newer version.');
    } catch (failure) { if (mounted.current) setError(errorText(failure)); }
    finally { clearTimeout(timeout); controllers.current.delete(controller); operation.current = false; if (mounted.current) setBusy(false); }
  }
  const locked = disabled || busy;
  const candidate = detail?.proposals.find(item => item.id === review?.candidate_id);
  const currentDetail = detail?.workflow.id === scanId ? detail : null;
  const stale = !!review && (review.version !== profile.version || currentDetail?.profile_version !== profile.version);
  function refresh() { setPollPaused(false); setReadGeneration(value => value + 1); setCatalogGeneration(value => value + 1); }

  return <section aria-labelledby="evidence-capture-title">
    <h3 id="evidence-capture-title">Capture from configured repositories</h3>
    <p>Scans read at most the 20 most recent commits, not the whole history. Commit messages are source proposals, not verified achievements. No model is used, and scanning creates no evidence or claims. Only your explicit approval adds one source.</p>
    {error && <p className="error" role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    <button className="secondary" disabled={locked || !!intent} onClick={refresh}>Refresh capture sources and status</button>
    <button className="secondary" disabled={locked || !!intent} onClick={() => { void loadLatestProfile(); }}>Load latest profile and retain capture review</button>
    {sources === null ? <p>Configured sources have not loaded.</p> : sources.length === 0 ? <p>No capture sources are configured. Ask the installation operator to configure a read-only local Git source or explicitly enable a GitHub repository. Manual sources remain available.</p> : <ul>{sources.map(source => <li key={source.id}>
      <strong>{source.label}</strong> · {source.kind === 'github' ? 'GitHub' : 'Local git'} · Preset: {evidencePrivacyClasses[source.privacy_class]}{source.employer_owned ? ' · Employer-owned' : ''}
      {!source.enabled && <p>Disabled by the installation operator. Scanning and approval are unavailable.</p>}
      <button className="secondary" disabled={locked || dirty || !source.enabled} onClick={() => { void submit({ kind: 'scan', path: `/evidence-capture/sources/${encodeURIComponent(source.id)}/scan`, body: {}, version: profile.version, key: crypto.randomUUID() }); }}>Scan {source.label}</button>
    </li>)}</ul>}
    {scans.length > 0 && <label>Saved capture scan<select disabled={locked || dirty} value={scanId} onChange={event => { setScanId(event.target.value); setDetail(null); setPollPaused(false); }}><option value="">Choose a scan</option>{scans.map(scan => <option key={scan.id} value={scan.id}>{scan.source_label} · {scan.state.replaceAll('_', ' ')} · {scan.created_at}</option>)}</select></label>}
    {scanId && !currentDetail && <p role="status">Loading capture scan {scanId}…</p>}
    {pollPaused && <p role="status">Automatic status checks paused. Refresh capture sources and status to continue; saved data is unchanged.</p>}
    {currentDetail && <>
      <p role="status">Scan {currentDetail.workflow.id}: {currentDetail.workflow.state.replaceAll('_', ' ')}{currentDetail.workflow.progress && ` · ${currentDetail.workflow.progress.completed}/${currentDetail.workflow.progress.total}`}</p>
      {currentDetail.workflow.last_error_code && <p className="error" role="alert">Scan failed: {currentDetail.workflow.last_error_code}. Saved sources are unchanged.</p>}
      {currentDetail.source_changed && <p role="alert">The configured source changed or was disabled. These proposals cannot be approved. Close any review and scan the enabled source again.</p>}
      {currentDetail.profile_version !== profile.version && <p role="status">A newer profile version is available. Load the latest profile, refresh capture status, and review again before approving. Existing choices are not replayed.</p>}
      {currentDetail.workflow.state === 'succeeded' && currentDetail.proposals.length === 0 && <p>No proposals in this bounded commit window.</p>}
      {currentDetail.workflow.state === 'succeeded' && <ol>{currentDetail.proposals.map(proposal => <li key={proposal.id}>
        <p className="profile-description-preview">{proposal.payload.text}</p><p>Source: <code>{proposal.payload.source}</code></p><p>Full commit SHA: <code>{proposal.id}</code></p>
        <p>Origin on approval: Source capture approved by owner, not independently verified. Type: Work sample. Preset: {evidencePrivacyClasses[proposal.provenance.privacy_class]}{proposal.provenance.employer_owned ? ' · Employer-owned' : ''}</p>
        {proposal.already_approved ? <p>Already approved. This source cannot be added again, including after editing or archiving.</p> : <button className="secondary" disabled={locked || dirty || currentDetail.source_changed || currentDetail.profile_version !== profile.version} onClick={() => setReview({ candidate_id: proposal.id, privacy_class: proposal.provenance.privacy_class, employer_owned: proposal.provenance.employer_owned, version: profile.version })}>Review proposal {proposal.id}</button>}
      </li>)}</ol>}
    </>}
    {review && candidate && <form onSubmit={event => { event.preventDefault(); if (!currentDetail || stale || currentDetail.source_changed || candidate.already_approved || intent) return; const { candidate_id, privacy_class, employer_owned } = review; void submit({ kind: 'approve', path: `/evidence-capture/scans/${encodeURIComponent(scanId)}/approve`, body: { candidate_id, privacy_class, employer_owned }, version: review.version, key: crypto.randomUUID() }); }}>
      <fieldset disabled={locked || !!intent}><legend>Approve selected source proposal</legend>
        <p>{candidate.payload.text}</p><p><code>{candidate.payload.source}</code> · Full SHA: <code>{candidate.id}</code></p>
        <label>Capture privacy class<select value={review.privacy_class} onChange={event => setReview({ ...review, privacy_class: event.target.value as EvidencePrivacyClass })}>{Object.entries(evidencePrivacyClasses).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label className="check"><input type="checkbox" checked={review.employer_owned} onChange={event => setReview({ ...review, employer_owned: event.target.checked })} />My employer owns this captured content</label>
        <p className="hint">Confidential or employer-owned evidence text is withheld from models. Approval creates a source revision, not a claim or independent verification.</p>
        {stale && <p role="alert">The profile changed. Your classification choices remain here. Close this review, refresh status, and review the proposal again before approving.</p>}
        <button disabled={stale || !!currentDetail?.source_changed || candidate.already_approved}>Approve selected proposal as source</button>
      </fieldset>
    </form>}
    {intent && !busy && <button disabled={disabled} onClick={() => { void submit(intent); }}>Retry original {intent.kind === 'scan' ? 'scan' : 'approval'} request</button>}
    {dirty && <button className="secondary" disabled={locked} onClick={() => { setReview(null); setIntent(null); setNotice('Review closed. This does not undo any saved approval. Refresh status before another decision.'); }}>Close capture review without retry</button>}
  </section>;
}
