// SPDX-License-Identifier: MIT OR Apache-2.0
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Api, ApiError, errorText, terminal, type Assessment, type Coverage, type HardConstraint, type Profile, type ProfileRevision, type Requirement, type RequirementRevision, type RequirementsView, type SuitabilityAccepted, type Workflow } from './api';

// Browser selections use UTF-16; the persisted source contract uses Unicode scalar values.
export function scalarSelection(source: string, start: number, end: number): Requirement['span'] {
  return { start: Array.from(source.slice(0, start)).length, end: Array.from(source.slice(0, end)).length, quote: source.slice(start, end) };
}
export function validSpan(source: string | readonly string[], span: Requirement['span']): boolean {
  const scalars = typeof source === 'string' ? Array.from(source) : source;
  return Number.isInteger(span.start) && Number.isInteger(span.end) && span.start >= 0 && span.start < span.end && span.end <= scalars.length && scalars.slice(span.start, span.end).join('') === span.quote;
}
const defaultConstraint: HardConstraint = { kind: 'other', operator: 'manual', required_value: null, owner_value: null, jurisdiction: null, owner_jurisdiction: null, currency: null, owner_currency: null, period: null, owner_period: null, owner_confirmed: false, contradictory: false };
const cloneRequirements = (items: Requirement[]): Requirement[] => items.map(item => ({ ...item, span: { ...item.span }, hard_constraint: item.hard_constraint ? { ...item.hard_constraint } : null }));
const toggle = (values: string[], value: string, selected: boolean) => selected ? [...new Set([...values, value])] : values.filter(item => item !== value);
const constraintFields = [
  ['required_value', 'Required source value'], ['owner_value', 'Your explicit value'], ['jurisdiction', 'Source jurisdiction'], ['owner_jurisdiction', 'Your assertion jurisdiction'],
  ['currency', 'Source salary currency'], ['owner_currency', 'Your salary currency'], ['period', 'Source salary period'], ['owner_period', 'Your salary period'],
] as const;

function ConstraintEditor({ value, onChange }: { value: HardConstraint; onChange: (value: HardConstraint) => void }) {
  const change = (update: Partial<HardConstraint>) => onChange({ ...value, ...update, owner_confirmed: false });
  return <fieldset><legend>Explicit hard constraint</legend>
    <label>Constraint kind<select value={value.kind} onChange={event => change({ kind: event.target.value as HardConstraint['kind'] })}>{(['work_authorization', 'location', 'salary', 'other'] as const).map(item => <option key={item} value={item}>{item.replaceAll('_', ' ')}</option>)}</select></label>
    <label>Comparison operator<select value={value.operator} onChange={event => change({ operator: event.target.value as HardConstraint['operator'] })}>{(['manual', 'equals', 'contains', 'minimum', 'maximum'] as const).map(item => <option key={item} value={item}>{item === 'manual' ? 'Manual / requires interpretation (unknown)' : item}</option>)}</select></label>
    <div className="catalog-filters">{constraintFields.map(([field, label]) => <label key={field}>{label}<input maxLength={1000} value={value[field] ?? ''} onChange={event => change({ [field]: event.target.value || null })} /></label>)}</div>
    <p className="hint">Do not infer permission or protected traits. Work authorization requires both jurisdictions and your explicit assertion. Use decimal strings for numeric bounds and identical currencies/periods; missing facts, legal interpretation, contradictions or incomparable units remain unknown. Contains compares complete tokens, not country-name substrings.</p>
    <label className="check"><input type="checkbox" checked={value.contradictory} onChange={event => change({ contradictory: event.target.checked })} />Facts are contradictory or unresolved</label>
    <label className="check"><input type="checkbox" checked={value.owner_confirmed} onChange={event => onChange({ ...value, owner_confirmed: event.target.checked })} />I confirm these exact source criteria and my explicit facts for this constraint</label>
  </fieldset>;
}
function Snapshot({ value, freshnessUnavailable, requirementsAvailable }: { value: Assessment; freshnessUnavailable: boolean; requirementsAvailable: boolean }) {
  const counts = value.evidence_coverage;
  return <section className="disclosure" aria-label="Immutable assessment snapshot"><h5>Saved assessment {value.id}</h5>
    <p><strong>{!requirementsAvailable ? 'Requirements unavailable — no current suitability assertion' : value.stale ? 'Stale historical snapshot — not a current eligibility assertion' : freshnessUnavailable ? 'Saved receipt — current freshness unavailable, not a current eligibility assertion' : 'Current snapshot'}</strong> · {value.created_at}</p>
    {value.stale && <p>Stale reasons: {value.stale_reasons.join(', ') || 'Upstream context changed'}.</p>}
    {!requirementsAvailable && <p>No reviewed requirements are available (requirements_unavailable). A confirmed empty review and zero-of-zero coverage cannot establish suitability.</p>}
    <p>Hard eligibility: <strong>{value.hard_eligibility.replaceAll('_', ' ')}</strong>. This is independent of evidence coverage.</p>
    <ul>{value.hard_constraints.map(item => <li key={item.requirement_key ?? item.reason}>{item.requirement_key ?? 'No reviewed hard constraints'}: {item.state.replaceAll('_', ' ')} — {item.reason}</li>)}</ul>
    <p>Evidence coverage, not hiring probability: {counts.supported} supported · {counts.partial} partial · {counts.not_evidenced} not evidenced · {counts.conflicting} conflicting · {counts.total} total requirements.</p>
    <p>Profile revision {value.profile_revision_id}; requirements revision {value.requirements_revision_id}. Earlier decisions are never rewritten.</p>
    <details><summary>Inspect saved rows, citations and provenance</summary>{value.assessments.map(item => <div key={item.requirement_key}><p>{item.requirement_key}: {item.coverage.replaceAll('_', ' ')} — {item.explanation}</p><p>Claim revisions: {item.claim_revision_ids.join(', ') || 'None'}. Evidence revisions: {item.evidence_revision_ids.join(', ') || 'None'}.</p></div>)}<pre className="import-source">{JSON.stringify(value.provenance, null, 2)}</pre></details>
  </section>;
}
export interface SuitabilityProps {
  api: Api; postingId: string; postingVersion?: number; postingContentSha256?: string; currentProfile?: Profile | null | undefined; onProfileChange?: ((value: Profile) => void) | undefined;
  disabled?: boolean; onDirtyChange?: (value: boolean) => void; onBusyChange?: (value: boolean) => void; onVersionChange?: (version: number) => void;
}
export function Suitability({ api, postingId, postingVersion, postingContentSha256, currentProfile, onProfileChange, disabled = false, onDirtyChange, onBusyChange, onVersionChange }: SuitabilityProps) {
  const [view, setView] = useState<RequirementsView | null>(null);
  const [profile, setProfile] = useState<Profile | null>(currentProfile ?? null);
  const [profileComparison, setProfileComparison] = useState<Profile | null>(null);
  const [pinnedProfile, setPinnedProfile] = useState<ProfileRevision | null>(null);
  const [profileError, setProfileError] = useState('');
  const [requirements, setRequirements] = useState<Requirement[] | null>(null);
  const [coverage, setCoverage] = useState<Coverage[] | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [staleWarning, setStaleWarning] = useState('');
  const [reconciliationRequired, setReconciliationRequired] = useState(false);
  const [loadedPostingContentSha256, setLoadedPostingContentSha256] = useState(postingContentSha256);
  const [contextWarning, setContextWarning] = useState('');
  const [taskMonitoringError, setTaskMonitoringError] = useState('');
  const [conflict, setConflict] = useState(false);
  const [comparison, setComparison] = useState<RequirementsView | null>(null);
  const [history, setHistory] = useState<RequirementRevision[] | null>(null);
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [workflowId, setWorkflowId] = useState<string | null>(null);
  const [savedTasks, setSavedTasks] = useState<Workflow[] | null>(null);
  const [pollPaused, setPollPaused] = useState(false);
  const [pollGeneration, setPollGeneration] = useState(0);
  const mutation = useRef<{ fingerprint: string; key: string } | null>(null);
  const lock = useRef(false);
  const mounted = useRef(false);
  const controllers = useRef(new Set<AbortController>());
  const sourceRef = useRef<HTMLTextAreaElement>(null);
  const errorRef = useRef<HTMLDivElement>(null);
  const callbacks = useRef({ onDirtyChange, onBusyChange, onVersionChange, onProfileChange });
  callbacks.current = { onDirtyChange, onBusyChange, onVersionChange, onProfileChange };
  const dirty = requirements !== null || coverage !== null || conflict;
  const liveContext = useRef({ currentProfile, postingContentSha256, dirty });
  liveContext.current = { currentProfile, postingContentSha256, dirty };
  const acceptedViewVersion = useRef(0);
  const activeWorkflow = !!workflowId && (!workflow || !terminal(workflow.state));
  const workflowTerminal = !!workflow && terminal(workflow.state);
  const locked = disabled || busy;
  const base = `/postings/${encodeURIComponent(postingId)}`;
  const coverageEditing = coverage !== null;
  let knownProfile = profile;
  for (const candidate of [currentProfile, profileComparison]) {
    if (candidate && (!knownProfile || candidate.current_revision.revision_no > knownProfile.current_revision.revision_no)) knownProfile = candidate;
  }
  const knownProfileId = knownProfile?.current_revision.id;
  const profileChanged = !!knownProfileId && !!profile && knownProfileId !== profile.current_revision.id;
  const sourceContextChanged = !!postingContentSha256 && !!loadedPostingContentSha256 && postingContentSha256 !== loadedPostingContentSha256;
  const postingContextChanged = !!view && ((postingVersion ?? view.version) > view.version || sourceContextChanged);
  const contextUnreconciled = reconciliationRequired || postingContextChanged;
  const request = useCallback(async <T,>(signal: AbortSignal, path: string, method = 'GET', body?: unknown, version?: number, key: boolean | string = false) => {
    const result = await api.request<T>(path, method, body, version, key, signal);
    if (!mounted.current || signal.aborted) throw new DOMException('Request stopped', 'AbortError');
    return result;
  }, [api]);
  const receiveView = useCallback((value: RequirementsView, contentIdentity = liveContext.current.postingContentSha256, replaceBuffer = false) => {
    if (value.version < acceptedViewVersion.current) return;
    if (liveContext.current.dirty && !replaceBuffer) {
      setComparison(existing => existing && existing.version > value.version ? existing : value);
      setConflict(true); setConfirmed(false);
      return;
    }
    acceptedViewVersion.current = value.version;
    setView(value); setLoadedPostingContentSha256(contentIdentity); setReconciliationRequired(false); setContextWarning('');
    callbacks.current.onVersionChange?.(value.version);
  }, []);
  useEffect(() => {
    mounted.current = true;
    const pending = controllers.current;
    return () => { mounted.current = false; for (const controller of pending) controller.abort(); callbacks.current.onDirtyChange?.(false); callbacks.current.onBusyChange?.(false); };
  }, []);
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => { onBusyChange?.(busy || activeWorkflow); }, [busy, activeWorkflow, onBusyChange]);
  useEffect(() => { if (error) errorRef.current?.focus(); }, [error]);
  useEffect(() => {
    if (!dirty && !busy && !activeWorkflow) return;
    const prevent = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener('beforeunload', prevent);
    return () => window.removeEventListener('beforeunload', prevent);
  }, [dirty, busy, activeWorkflow]);
  useEffect(() => {
    const controller = new AbortController(); controllers.current.add(controller);
    const timer = setTimeout(() => controller.abort(), 20_000);
    void Promise.allSettled([
      request<RequirementsView>(controller.signal, `${base}/requirements`).then(value => receiveView(value)).catch(failure => { if (mounted.current) setError(controller.signal.aborted ? 'Requirement review is unavailable: request deadline reached.' : errorText(failure)); }),
      ...(liveContext.current.currentProfile ? [] : [request<Profile>(controller.signal, '/profile').then(value => { setProfile(value); callbacks.current.onProfileChange?.(value); setProfileError(''); }).catch(failure => { if (mounted.current) setProfileError(errorText(failure)); })]),
    ]).finally(() => { clearTimeout(timer); controllers.current.delete(controller); });
    return () => { controller.abort(); clearTimeout(timer); };
  }, [base, request, receiveView]);
  useEffect(() => {
    if (!currentProfile || (profile && currentProfile.current_revision.revision_no <= profile.current_revision.revision_no)) return;
    setConfirmed(false);
    if (coverageEditing) setProfileComparison(currentProfile);
    else { setProfile(currentProfile); setProfileComparison(null); setProfileError(''); }
  }, [currentProfile, coverageEditing, profile]);
  useEffect(() => {
    if (!postingContextChanged || (dirty && comparison?.version === postingVersion)) return;
    setConfirmed(false);
    const controller = new AbortController(); controllers.current.add(controller);
    const timer = setTimeout(() => controller.abort(), 20_000);
    const contentIdentity = postingContentSha256;
    void request<RequirementsView>(controller.signal, `${base}/requirements`).then(value => {
      if (dirty) { setComparison(value); setConflict(true); setContextWarning('Posting context changed. Your old-source buffer is retained; compare the authoritative review before explicitly retaining or discarding it.'); }
      else receiveView(value, contentIdentity);
    }).catch(() => {
      if (mounted.current && !controller.signal.aborted) setContextWarning('The posting changed, but current review reconciliation is unavailable. Earlier source, assessment and buffers remain retained, not current assertions. Reload review state to retry.');
    }).finally(() => { clearTimeout(timer); controllers.current.delete(controller); });
    return () => { controller.abort(); clearTimeout(timer); };
  }, [base, request, postingVersion, postingContentSha256, postingContextChanged, dirty, comparison?.version, receiveView]);
  async function perform(action: (signal: AbortSignal) => Promise<void>, postingPrecondition = false) {
    if (lock.current || disabled) return;
    lock.current = true; setBusy(true); setError('');
    const controller = new AbortController(); controllers.current.add(controller);
    const timer = setTimeout(() => controller.abort(), 20_000);
    try { await action(controller.signal); }
    catch (failure) {
      if (mounted.current) {
        if (postingPrecondition && failure instanceof ApiError && failure.status === 412) { setConflict(true); setComparison(null); }
        setError(controller.signal.aborted ? 'Request deadline reached. The result is unconfirmed; your local buffer and retry identity remain here.' : failure instanceof ApiError && failure.code === 'model_route_unavailable' ? 'Model route unavailable. Manual requirements and coverage remain usable; no zero score was substituted.' : errorText(failure));
      }
    } finally { clearTimeout(timer); controllers.current.delete(controller); lock.current = false; if (mounted.current) setBusy(false); }
  }
  function keyFor(path: string, body: unknown, version: number) {
    const fingerprint = JSON.stringify({ path, body, version });
    if (mutation.current?.fingerprint !== fingerprint) mutation.current = { fingerprint, key: crypto.randomUUID() };
    return mutation.current.key;
  }
  async function save(kind: 'requirements' | 'coverage') {
    if (!view || conflict || !confirmed || activeWorkflow || contextUnreconciled || (kind === 'coverage' && profileChanged)) return;
    const body = kind === 'requirements' ? { confirmed: true, requirements } : { confirmed: true, profile_revision_id: profile?.current_revision.id, requirements_revision_id: view.current_revision?.id, assessments: coverage };
    const path = `${base}/${kind}`;
    await perform(async signal => {
      if (kind === 'coverage' && profile) {
        const latestProfile = await request<Profile>(signal, '/profile');
        if (latestProfile.current_revision.id !== profile.current_revision.id) {
          setProfileComparison(latestProfile);
          callbacks.current.onProfileChange?.(latestProfile);
          setError('The selected profile changed. Your coverage text and pinned citations remain here. Compare the current profile and explicitly restart review; no assessment was saved.');
          return;
        }
      }
      const receipt = await request<RequirementsView>(signal, path, 'PUT', body, view.version, keyFor(path, body, view.version));
      receiveView(receipt, undefined, true); mutation.current = null; setRequirements(null); setCoverage(null); setConfirmed(false); setConflict(false); setComparison(null);
      setNotice(kind === 'requirements' ? `Saved reviewed requirement revision ${receipt.current_revision?.revision_no}. Earlier revisions are unchanged.` : `Saved immutable owner-reviewed assessment ${receipt.latest_assessment?.id}. No profile facts changed.`);
      setStaleWarning('');
      try { receiveView(await request<RequirementsView>(signal, `${base}/requirements`), undefined, true); }
      catch { setStaleWarning('Save receipt retained. Latest-state reconciliation failed; the displayed saved snapshot may now be stale. Reload review state explicitly.'); }
    }, true);
  }
  function editRequirement(index: number, update: Partial<Requirement>) {
    setRequirements(existing => existing?.map((item, position) => position === index ? { ...item, ...update } : item) ?? null); setConfirmed(false);
  }
  function updateCoverage(index: number, update: Partial<Coverage>) {
    setCoverage(existing => existing?.map((item, position) => position === index ? { ...item, ...update } : item) ?? null); setConfirmed(false);
  }
  function addSelection() {
    if (!view || !requirements || !sourceRef.current) return;
    const element = sourceRef.current;
    const span = scalarSelection(view.source_text, element.selectionStart, element.selectionEnd);
    if (!validSpan(view.source_text, span) || Array.from(span.quote).length > 2000) { setError('Select a nonempty source quotation of at most 2000 Unicode characters.'); return; }
    const used = new Set(requirements.map(item => item.requirement_key));
    let number = 1; while (used.has(`req-${number}`)) number += 1;
    if (number > 999) { setError('No further requirement keys are available.'); return; }
    setRequirements([...requirements, { requirement_key: `req-${number}`, text: Array.from(span.quote).slice(0, 1000).join(''), importance: 'unclear', span, hard_constraint: null }]); setConfirmed(false);
  }
  async function start(stage: 'extract' | 'match') {
    if (!view || dirty || conflict || activeWorkflow || contextUnreconciled || (stage === 'match' && profileChanged)) return;
    const body = stage === 'extract' ? {} : { profile_revision_id: profile?.current_revision.id, requirements_revision_id: view.current_revision?.id };
    const path = `${base}/${stage}`;
    await perform(async signal => {
      const accepted = await request<SuitabilityAccepted>(signal, path, 'POST', body, view.version, keyFor(path, body, view.version));
      mutation.current = null; setWorkflow(null); setWorkflowId(accepted.workflow_id); setPollPaused(false); setPollGeneration(value => value + 1); setTaskMonitoringError(''); setReconciliationRequired(true);
      setNotice(`Durable ${stage} task accepted. Any output is an untrusted proposal, not a reviewed decision.`);
      try { receiveView(await request<RequirementsView>(signal, `${base}/requirements`)); }
      catch { setContextWarning('Task acceptance retained. Posting version reconciliation is unavailable; inspect the durable task, then reload review state before adopting or saving any proposal.'); }
    }, true);
  }
  useEffect(() => {
    if (!workflowId || pollPaused || workflowTerminal) return;
    let stopped = false;
    const pending = controllers.current;
    const controller = new AbortController(); pending.add(controller);
    let timer: number | undefined;
    const deadline = setTimeout(() => { if (!stopped) { controller.abort(); setPollPaused(true); setTaskMonitoringError('Task monitoring deadline reached. The durable task may still be running; inspect it explicitly. No assessment was fabricated.'); } }, 120_000);
    const poll = async () => {
      const requestTimer = setTimeout(() => controller.abort(), 20_000);
      try {
        const value = await request<Workflow>(controller.signal, `/workflows/${encodeURIComponent(workflowId)}`);
        if (stopped) return;
        setWorkflow(previous => previous?.id === value.id && previous.version > value.version ? previous : value);
        setTaskMonitoringError('');
        if (!terminal(value.state)) timer = window.setTimeout(() => { void poll(); }, 1500);
        else clearTimeout(deadline);
      } catch (failure) { if (!stopped) { setPollPaused(true); setTaskMonitoringError('Task status unavailable. ' + errorText(failure)); } }
      finally { clearTimeout(requestTimer); }
    };
    void poll();
    return () => { stopped = true; controller.abort(); clearTimeout(timer); clearTimeout(deadline); pending.delete(controller); };
    // Poll lifetime is keyed to the durable identity, not each status response.
  }, [workflowId, workflowTerminal, pollPaused, pollGeneration, request]);
  const selectedClaims = profile?.current_revision.claims.filter(item => !item.archived && profile.current_revision.snapshot.claim_revision_ids.includes(item.revision.id)) ?? [];
  const selectedEvidence = profile?.evidence.filter(item => !item.archived && (profile.current_revision.snapshot.evidence_revision_ids ?? []).includes(item.revision.id)) ?? [];
  const revision = view?.current_revision;
  const sourceScalars = useMemo(() => Array.from(view?.source_text ?? ''), [view?.source_text]);
  const assessment = view?.latest_assessment;
  const localStaleReasons = [...(knownProfileId && assessment?.profile_revision_id !== knownProfileId ? ['profile_revision_changed'] : []), ...(sourceContextChanged ? ['posting_source_changed'] : [])];
  const displayedAssessment = assessment && localStaleReasons.length ? { ...assessment, stale: true, stale_reasons: [...new Set([...assessment.stale_reasons, ...localStaleReasons])] } : assessment;
  const sourceValid = requirements?.every(item => item.text.trim() && validSpan(sourceScalars, item.span)) ?? false;
  const positiveCitationsValid = coverage?.every(row => !['supported', 'partial'].includes(row.coverage) || (row.claim_revision_ids.length > 0 && row.evidence_revision_ids.length > 0 && row.claim_revision_ids.every(id => selectedClaims.some(claim => claim.revision.id === id)) && row.evidence_revision_ids.every(id => selectedEvidence.some(evidence => evidence.revision.id === id) && selectedClaims.some(claim => row.claim_revision_ids.includes(claim.revision.id) && claim.revision.evidence_links.some(link => link.evidence_revision_id === id))))) ?? false;
  function discard() { setRequirements(null); setCoverage(null); setConfirmed(false); setConflict(false); setComparison(null); mutation.current = null; setNotice('Local review buffer discarded. If a response was lost, reload review state to inspect the server receipt.'); }
  const taskAnnouncement = !workflowId ? '' : taskMonitoringError ? 'Task monitoring unavailable. The durable task is not assumed finished.' : !workflow ? 'Task accepted; waiting for its durable status.' : workflow.state === 'succeeded' ? 'Task succeeded. An untrusted proposal is ready for explicit review; nothing was approved or saved.' : workflow.state === 'permanent_failure' ? `Task failed: ${workflow.last_error_code ?? 'reason unavailable'}. No assessment was fabricated.` : workflow.state === 'cancelled' ? 'Task cancelled. No proposal was approved.' : `Task ${workflow.state.replaceAll('_', ' ')}${workflow.last_error_code ? `: ${workflow.last_error_code}` : ''}.`;
  return <section className="suitability" aria-labelledby="suitability-title"><h4 id="suitability-title">Requirements and explainable suitability</h4>
    <p>No fit percentage, hiring probability or application action. Missing evidence does not establish that you lack a skill. Hard constraints and semantic evidence coverage are separate.</p>
    <div className="error" role="alert" ref={errorRef} tabIndex={-1} hidden={!error}>{error}</div><p role="status">{notice}</p>
    {staleWarning && <p className="error" role="status">{staleWarning}</p>}
    {contextWarning && <p className="error" role="status">{contextWarning}</p>}
    {contextUnreconciled && <p className="disclosure">Current posting review is not reconciled. Retained text and snapshots are available for inspection, but proposal adoption and saves are blocked until the authoritative version/source is compared or reloaded.</p>}
    <p role="status" aria-live="polite" aria-atomic="true" aria-label="Suitability task status">{taskAnnouncement}</p>
    <button className="secondary" disabled={locked} onClick={() => { void perform(async signal => { const value = await request<RequirementsView>(signal, `${base}/requirements`); if (dirty) { setComparison(value); setConflict(true); setConfirmed(false); } else receiveView(value); setStaleWarning(''); }); }}>Reload review state</button>
    {!view ? <p role="status">{error ? 'Requirement state unavailable, not an empty requirement set or zero match.' : 'Loading requirement review…'}</p> : <>
      <p>Source SHA-256 <code>{view.source_sha256}</code> · posting version {view.version}. Source spans are zero-based, half-open Unicode scalar indices.</p>
      <details><summary>Inspect normalized source and exact quotations</summary><pre className="import-source">{view.source_text}</pre></details>
      <p>Model assistance: <strong>{view.model_route.status === 'qualified' ? 'Qualified route configured; requests remain explicit' : 'Unavailable'}</strong> — {view.model_route.reason}. Manual review does not need a model.</p>
      <details><summary>Inspect model route identity</summary><pre className="import-source">{JSON.stringify(view.model_route.identity, null, 2)}</pre></details>
      <div className="actions"><button className="secondary" disabled={locked || dirty || activeWorkflow || contextUnreconciled} onClick={() => { setRequirements(cloneRequirements(revision?.requirements ?? [])); setCoverage(null); setConfirmed(false); }}>Review requirements manually</button>
        <button className="secondary" disabled={locked || dirty || activeWorkflow || contextUnreconciled || view.model_route.status !== 'qualified'} onClick={() => { void start('extract'); }}>Request extraction proposal</button>
        <button className="secondary" disabled={locked} onClick={() => { void perform(async signal => { setHistory((await request<{ items: RequirementRevision[] }>(signal, `${base}/requirements/history`)).items); }); }}>Inspect immutable requirement history</button></div>
      {!revision || !revision.requirements.length ? <p><strong>Requirements unavailable.</strong> An empty reviewed set is not a perfect match. Review literal source requirements before assessing coverage.</p> : <section aria-label="Current reviewed requirements"><h5>Reviewed requirement revision {revision.revision_no}</h5><p>{revision.stale || sourceContextChanged ? 'Stale — source review required' : contextUnreconciled ? 'Review freshness unavailable — posting version not reconciled' : 'Source-pinned review'} · {revision.created_at} · SHA-256 <code>{revision.source_sha256}</code></p><ol>{revision.requirements.map(item => <li key={item.requirement_key}><strong>{item.requirement_key}: {item.text}</strong> · {item.importance}<blockquote>{item.span.quote}</blockquote><p>Source [{item.span.start}, {item.span.end}). {item.hard_constraint ? `${item.hard_constraint.kind.replaceAll('_', ' ')} · ${item.hard_constraint.operator} · owner ${item.hard_constraint.owner_confirmed ? 'confirmed' : 'not confirmed'}` : 'No reviewed hard constraint'}</p><details><summary>Inspect exact constraint facts</summary><pre className="import-source">{JSON.stringify(item.hard_constraint, null, 2)}</pre></details></li>)}</ol></section>}
      {requirements !== null && <form onSubmit={event => { event.preventDefault(); void save('requirements'); }}><fieldset disabled={locked || activeWorkflow}><legend>Requirement review buffer</legend>
        <label>Selectable normalized source<textarea ref={sourceRef} readOnly value={view.source_text} /></label><p className="hint">Select an exact phrase above and add it. Keyboard: Shift + arrow keys extends the selection. Alternatively add a row, then edit its quote and scalar start/end below. Source text itself is never edited here.</p>
        <button type="button" className="secondary" onClick={addSelection}>Add selected source requirement</button>
        <button type="button" className="secondary" onClick={() => { const keys = new Set(requirements.map(item => item.requirement_key)); let number = 1; while (keys.has(`req-${number}`)) number += 1; if (number <= 999) { setRequirements([...requirements, { requirement_key: `req-${number}`, text: '', importance: 'unclear', span: { start: 0, end: 0, quote: '' }, hard_constraint: null }]); setConfirmed(false); } }}>Add requirement by quote and scalar indices</button>
        {requirements.map((item, index) => <fieldset key={item.requirement_key}><legend>{item.requirement_key}</legend>
          <label>Requirement wording<textarea required maxLength={1000} value={item.text} onChange={event => editRequirement(index, { text: event.target.value })} /></label>
          <label>Importance<select value={item.importance} onChange={event => editRequirement(index, { importance: event.target.value as Requirement['importance'] })}><option value="unclear">Unclear</option><option value="required">Required</option><option value="preferred">Preferred</option></select></label>
          <label>Exact source quotation<textarea required maxLength={2000} value={item.span.quote} onChange={event => editRequirement(index, { span: { ...item.span, quote: event.target.value } })} /></label>
          <label>Source start (Unicode scalars)<input type="number" required min={0} step={1} value={item.span.start} onChange={event => editRequirement(index, { span: { ...item.span, start: Number(event.target.value) } })} /></label>
          <label>Source end (exclusive Unicode scalars)<input type="number" required min={1} step={1} value={item.span.end} onChange={event => editRequirement(index, { span: { ...item.span, end: Number(event.target.value) } })} /></label>
          {!validSpan(sourceScalars, item.span) && <p className="error">Quotation and scalar span must match the exact normalized source.</p>}
          <label className="check"><input type="checkbox" checked={!!item.hard_constraint} onChange={event => editRequirement(index, { hard_constraint: event.target.checked ? { ...defaultConstraint } : null })} />This is an explicit hard constraint</label>
          {item.hard_constraint && <ConstraintEditor value={item.hard_constraint} onChange={value => editRequirement(index, { hard_constraint: value })} />}
          <button type="button" className="secondary" onClick={() => { setRequirements(requirements.filter((_, position) => position !== index)); setConfirmed(false); }}>Remove {item.requirement_key}</button>
        </fieldset>)}
        <label className="check"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />I reviewed all exact source spans, importance, constraints and explicit owner facts; save a new immutable reviewed revision</label>
        <div className="actions"><button disabled={!confirmed || !sourceValid || conflict || contextUnreconciled}>Save reviewed requirements</button><button type="button" className="secondary" onClick={discard}>Discard review buffer</button></div>
      </fieldset></form>}
      {history && <section aria-label="Requirement history"><h5>Immutable requirement history</h5>{history.length ? history.map(item => <details key={item.id}><summary>Revision {item.revision_no} · {item.created_at} · {item.stale ? 'stale' : 'source pinned'}</summary><p>ID {item.id} · SHA-256 {item.source_sha256} · {item.confirmed ? 'owner confirmed' : 'not confirmed'}</p><pre className="import-source">{JSON.stringify({ requirements: item.requirements, provenance: item.provenance }, null, 2)}</pre></details>) : <p>No reviewed revisions retained.</p>}</section>}
      {displayedAssessment ? <Snapshot value={displayedAssessment} freshnessUnavailable={!!staleWarning || contextUnreconciled} requirementsAvailable={!!revision?.requirements.length} /> : <p>No saved assessment. Unavailable is not a zero score.</p>}
      {displayedAssessment && <button className="secondary" disabled={locked} onClick={() => { void perform(async signal => { setPinnedProfile(await request<ProfileRevision>(signal, `/profile/revisions/${encodeURIComponent(displayedAssessment.profile_revision_id)}`)); }); }}>Inspect assessment's pinned profile evidence</button>}
      {pinnedProfile && <section className="comparison" aria-label="Assessment pinned profile evidence"><h5>Immutable profile revision {pinnedProfile.revision_no}</h5><p>Revision {pinnedProfile.id} · SHA-256 {pinnedProfile.content_sha256} · {pinnedProfile.created_at}. Historical references are not automatically current citation choices.</p>
        {pinnedProfile.claims.map(item => <details key={item.revision.id}><summary>{item.kind}: {item.rendering}</summary><pre className="import-source">{JSON.stringify(item.revision, null, 2)}</pre></details>)}
        {pinnedProfile.evidence.map(item => <details key={item.revision.id}><summary>{item.revision.payload.source}</summary><p>{item.revision.payload.text}</p><p>Evidence revision {item.revision.id} · SHA-256 {item.revision.content_sha256}</p></details>)}
      </section>}
      <section aria-label="Profile citation inspector"><h5>Selected profile claims and evidence</h5>
        {profileError && <p className="error">Profile citation state unavailable. {profileError} Manual requirement review remains available.</p>}
        <button className="secondary" disabled={locked || coverage !== null || activeWorkflow} onClick={() => { void perform(async signal => { const value = await request<Profile>(signal, '/profile'); setProfile(value); callbacks.current.onProfileChange?.(value); setProfileComparison(null); setProfileError(''); }); }}>Reload profile citations</button>
        {coverage !== null && <button className="secondary" disabled={locked} onClick={() => { void perform(async signal => { const value = await request<Profile>(signal, '/profile'); setProfileComparison(value); callbacks.current.onProfileChange?.(value); }); }}>Compare current profile citations</button>}
        {profileComparison && <section className="comparison" aria-label="Current profile comparison"><h5>Current profile comparison</h5><p>Your buffer cites profile {profile?.current_revision.id ?? 'unavailable'}. The currently selected profile is {profileComparison.current_revision.id}. {profileChanged ? 'These revisions differ; the old buffer cannot be saved as a current assessment.' : 'The revisions match; your buffer remains unchanged.'}</p>
          <details><summary>Inspect current profile facts, selected claims and evidence</summary><pre className="import-source">{JSON.stringify({ revision: profileComparison.current_revision, evidence: profileComparison.evidence }, null, 2)}</pre></details>
          <button className="secondary" disabled={locked} onClick={() => { setProfile(profileComparison); callbacks.current.onProfileChange?.(profileComparison); setProfileComparison(null); setProfileError(''); setCoverage(null); setConfirmed(false); mutation.current = null; setNotice('Compared profile explicitly loaded. The old coverage buffer was discarded; start a new row-by-row review against these citations.'); }}>Discard coverage buffer and use compared profile</button>
        </section>}
        {profile && <><p>Profile revision {profile.current_revision.id}. Only selected, live revisions are citation choices. References and structure do not prove meaning; inspect every source.</p>
          {selectedClaims.map(claim => <details key={claim.revision.id}><summary>{claim.kind}: {claim.rendering}</summary><pre className="import-source">{JSON.stringify(claim.revision.payload, null, 2)}</pre><p>Claim revision {claim.revision.id} · SHA-256 {claim.revision.content_sha256}</p>{claim.revision.evidence_links.map(link => <blockquote key={link.evidence_revision_id}><p>{link.excerpt}</p><p>{link.support_kind} · {link.evidence.payload.source}</p><p>{link.evidence.payload.text}</p><p>Evidence revision {link.evidence_revision_id} · SHA-256 {link.evidence.content_sha256}</p></blockquote>)}</details>)}
          {selectedEvidence.map(item => <details key={item.revision.id}><summary>Evidence: {item.revision.payload.source}</summary><p>{item.revision.payload.text}</p><p>Revision {item.revision.id} · SHA-256 {item.revision.content_sha256}</p></details>)}
          {!selectedClaims.length && <p>No live selected claims. Missing evidence stays not evidenced; no skill is added to your profile.</p>}
        </>}
      </section>
      <div className="actions"><button className="secondary" disabled={locked || dirty || activeWorkflow || contextUnreconciled || profileChanged || !profile || !!profileError || !revision?.requirements.length || revision.stale} onClick={() => { setCoverage((revision?.requirements ?? []).map(item => ({ requirement_key: item.requirement_key, coverage: 'not_evidenced', claim_revision_ids: [], evidence_revision_ids: [], explanation: '' }))); setRequirements(null); setConfirmed(false); }}>Review evidence coverage manually</button>
        <button className="secondary" disabled={locked || dirty || activeWorkflow || contextUnreconciled || profileChanged || !profile || !!profileError || !revision?.requirements.length || revision.stale || view.model_route.status !== 'qualified'} onClick={() => { void start('match'); }}>Request matching proposal</button></div>
      {coverage !== null && <form onSubmit={event => { event.preventDefault(); void save('coverage'); }}><fieldset disabled={locked || activeWorkflow}><legend>Owner-reviewed evidence coverage buffer</legend>
        <p>Exactly one row for every reviewed requirement. Positive coverage requires connected selected claim and evidence citations. A valid reference is not proof that its meaning supports the requirement.</p>
        {coverage.map((row, index) => <fieldset key={row.requirement_key}><legend>{row.requirement_key}: {revision?.requirements.find(item => item.requirement_key === row.requirement_key)?.text}</legend>
          <label>Evidence coverage<select value={row.coverage} onChange={event => updateCoverage(index, { coverage: event.target.value as Coverage['coverage'] })}>{(['not_evidenced', 'partial', 'supported', 'conflicting'] as const).map(value => <option key={value} value={value}>{value.replaceAll('_', ' ')}</option>)}</select></label>
          <label>Explanation of evidence and gaps<textarea required maxLength={1000} value={row.explanation} onChange={event => updateCoverage(index, { explanation: event.target.value })} /></label>
          <fieldset><legend>Claim revision citations</legend>{selectedClaims.map(claim => <label className="check" key={claim.revision.id}><input type="checkbox" checked={row.claim_revision_ids.includes(claim.revision.id)} onChange={event => updateCoverage(index, { claim_revision_ids: toggle(row.claim_revision_ids, claim.revision.id, event.target.checked) })} />{claim.rendering} · {claim.revision.id}</label>)}</fieldset>
          <fieldset><legend>Evidence revision citations</legend>{selectedEvidence.map(item => <label className="check" key={item.revision.id}><input type="checkbox" checked={row.evidence_revision_ids.includes(item.revision.id)} onChange={event => updateCoverage(index, { evidence_revision_ids: toggle(row.evidence_revision_ids, item.revision.id, event.target.checked) })} />{item.revision.payload.source}: {item.revision.payload.text} · {item.revision.id}</label>)}</fieldset>
          {(row.claim_revision_ids.some(id => !selectedClaims.some(item => item.revision.id === id)) || row.evidence_revision_ids.some(id => !selectedEvidence.some(item => item.revision.id === id))) && <p className="error">This buffer includes citations outside the current live selected profile. Review or discard it; the server will not accept foreign references.</p>}
        </fieldset>)}
        {!positiveCitationsValid && <p className="error">Supported or partial rows need real selected claim/evidence citations connected to each other. Inspect their meaning before confirming.</p>}
        <label className="check"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />I reviewed every coverage row and the actual cited content; references alone do not establish semantic support</label>
        <div className="actions"><button disabled={!confirmed || !positiveCitationsValid || conflict || contextUnreconciled || profileChanged || !coverage.every(row => row.explanation.trim())}>Save owner-reviewed assessment</button><button type="button" className="secondary" onClick={discard}>Discard review buffer</button></div>
      </fieldset></form>}
      {conflict && <section className="disclosure" aria-label="Review conflict"><h5>Newer posting revision exists</h5><p>Your local buffer and original precondition remain unchanged. No automatic retry selects a newer If-Match.</p>
        <button className="secondary" disabled={locked} onClick={() => { void perform(async signal => { setComparison(await request<RequirementsView>(signal, `${base}/requirements`)); }); }}>Load current review for comparison</button>
        <button className="secondary" disabled={locked} onClick={discard}>Discard retained operation</button>
        {comparison && <><p>Current posting version {comparison.version} · source SHA-256 {comparison.source_sha256}. Compare saved requirements, source and assessment before choosing.</p><pre className="import-source">{comparison.source_text}</pre><pre className="import-source">{JSON.stringify({ requirements: comparison.current_revision, assessment: comparison.latest_assessment }, null, 2)}</pre>
          <button className="secondary" disabled={locked || comparison.source_sha256 !== view.source_sha256 || (coverage !== null && (comparison.current_revision?.id !== view.current_revision?.id || profileChanged))} onClick={() => { receiveView(comparison, undefined, true); setComparison(null); setConflict(false); setConfirmed(false); mutation.current = null; }}>Use compared version and retain review buffer</button>
          <button className="secondary" disabled={locked} onClick={() => { receiveView(comparison, undefined, true); discard(); }}>Discard local buffer and load compared review</button>
          {(comparison.source_sha256 !== view.source_sha256 || (coverage !== null && (comparison.current_revision?.id !== view.current_revision?.id || profileChanged))) && <p>A changed coverage context or source requires discarding and reviewing a fresh buffer; it cannot be silently rebased.</p>}
        </>}
      </section>}
    </>}
    <section className="saved-tasks" aria-label="Durable suitability tasks"><h5>Durable extraction and matching tasks</h5>
      <button className="secondary" disabled={locked} onClick={() => { void perform(async signal => { const page = await request<{ items: Workflow[] }>(signal, '/workflows'); setSavedTasks(page.items.filter(item => (item.kind === 'posting_extract' || item.kind === 'matching') && item.result?.posting_id === postingId)); }); }}>Load saved suitability tasks</button>
      {savedTasks && (savedTasks.length ? <ul>{savedTasks.map(item => <li key={item.id}><button className="secondary" disabled={locked || dirty || activeWorkflow} onClick={() => { setWorkflow(item); setWorkflowId(item.id); setTaskMonitoringError(''); setPollPaused(false); setPollGeneration(value => value + 1); }}>{item.kind} task {item.id} · {item.state}</button></li>)}</ul> : <p>No tasks attributed to this posting in the returned task page.</p>)}
      {workflowId && <section className="task" aria-label="Current suitability task"><p>Task {workflowId}: {workflow?.state.replaceAll('_', ' ') ?? 'loading status'} · attempt {workflow?.attempt ?? 'unknown'} of {workflow?.max_attempts ?? 2}.</p>
        {workflow?.last_error_code && <p className="error">Task failure: {workflow.last_error_code}. No coverage or eligibility result was fabricated.</p>}
        {pollPaused && <p>Monitoring paused or unavailable; the durable task state is not assumed finished.</p>}
        {taskMonitoringError && <p className="error">{taskMonitoringError}</p>}
        <button className="secondary" disabled={locked} onClick={() => { setPollPaused(true); void perform(async signal => { const value = await request<Workflow>(signal, `/workflows/${encodeURIComponent(workflowId)}`); setWorkflow(previous => previous?.id === value.id && previous.version > value.version ? previous : value); setTaskMonitoringError(''); setPollPaused(false); setPollGeneration(generation => generation + 1); }); }}>Inspect task status</button>
        {activeWorkflow && <button className="secondary" disabled={locked || !workflow} onClick={() => { if (!workflow) return; setPollPaused(true); void perform(async signal => { const path = `/workflows/${encodeURIComponent(workflowId)}/cancel`; const value = await request<Workflow>(signal, path, 'POST', {}, workflow.version, keyFor(path, {}, workflow.version)); setWorkflow(previous => previous?.id === value.id && previous.version > value.version ? previous : value); mutation.current = null; setTaskMonitoringError(''); setPollPaused(false); setPollGeneration(generation => generation + 1); }); }}>Cancel task</button>}
        {workflow?.state === 'succeeded' && <><p><strong>Untrusted proposal — not owner-confirmed.</strong> Inspect the complete output, then explicitly load it into a review buffer. This never saves a review or changes profile facts.</p><pre className="import-source">{JSON.stringify(workflow.result, null, 2)}</pre>
          {workflow.result?.requirements && <button className="secondary" disabled={locked || dirty || contextUnreconciled || !view || workflow.result.posting_id !== postingId || workflow.result.source_sha256 !== view.source_sha256} onClick={() => { setRequirements(cloneRequirements(workflow.result?.requirements ?? [])); setCoverage(null); setConfirmed(false); }}>Load extraction proposal into review buffer</button>}
          {workflow.result?.assessments && <button className="secondary" disabled={locked || dirty || contextUnreconciled || profileChanged || !profile || !!profileError || !revision?.requirements.length || revision.stale || workflow.result.posting_id !== postingId || workflow.result.source_sha256 !== view?.source_sha256 || workflow.result.profile_revision_id !== knownProfileId || workflow.result.requirements_revision_id !== revision.id || workflow.result.assessments.length !== revision.requirements.length || !revision.requirements.every(item => workflow.result?.assessments?.filter(row => row.requirement_key === item.requirement_key).length === 1)} onClick={() => { setCoverage((workflow.result?.assessments ?? []).map(item => ({ ...item, claim_revision_ids: [...item.claim_revision_ids], evidence_revision_ids: [...item.evidence_revision_ids] }))); setRequirements(null); setConfirmed(false); }}>Load matching proposal into review buffer</button>}
          {view && workflow.result?.source_sha256 !== view.source_sha256 && <p className="error">Proposal source differs from the displayed source. It cannot be adopted into this review.</p>}
          {workflow.result?.assessments && (workflow.result.profile_revision_id !== knownProfileId || workflow.result.requirements_revision_id !== revision?.id) && <p className="error">Proposal profile or reviewed-requirement pins differ from the current review. Start a new explicit matching request rather than approving a stale proposal.</p>}
        </>}
      </section>}
    </section>
  </section>;
}
