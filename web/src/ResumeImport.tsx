// SPDX-License-Identifier: MIT OR Apache-2.0
import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { Api, ApiError, errorText, MAX_IMPORT_BYTES, terminal, type ClaimPayload, type ClaimRecord, type ImportAccepted, type ImportBatch, type ImportDecision, type ImportMediaType, type ImportMergeRequest, type ImportPage, type ImportProposal, type ImportSummary, type Profile, type Workflow } from './api';

export interface ResumeImportProps {
  api: Api; profile: Profile; disabled: boolean; onProfile: (profile: Profile) => void;
  onDirtyChange?: (dirty: boolean) => void; onBusyChange?: (busy: boolean) => void;
}
type PayloadField = keyof ClaimPayload;
type FieldChoice = 'keep' | 'import' | 'edit';
interface ReviewDraft {
  action: ImportDecision['action']; claim_id: string; payload: ClaimPayload; reviewed: boolean;
  choices: Partial<Record<PayloadField, FieldChoice>>;
}
const fields: { key: PayloadField; label: string; limit: number }[] = [
  { key: 'text', label: 'Statement', limit: 4000 }, { key: 'organization', label: 'Organization', limit: 200 },
  { key: 'role', label: 'Role or qualification', limit: 200 }, { key: 'start_date', label: 'Start date', limit: 10 },
  { key: 'end_date', label: 'End date', limit: 10 }, { key: 'proficiency', label: 'Proficiency or level', limit: 120 },
];
const warningLabels: Record<string, string> = {
  deterministic_literal_extraction: 'Proposals are deterministic, literal local extraction, not AI interpretation.',
  evidence_is_owner_asserted_not_independently_verified: 'Imported facts are owner assertions, not independently verified evidence.',
  ambiguous_section_kept_as_other: 'Ambiguous source sections remain categorized as other facts. Review them carefully.',
  contact_details_excluded: 'Recognizable contact or reference details were excluded from proposals.',
};
function initialDraft(proposal: ImportProposal): ReviewDraft {
  return { action: 'skip', claim_id: '', payload: { ...proposal.payload }, reviewed: false, choices: {} };
}
function initialDrafts(batch: ImportBatch): Record<string, ReviewDraft> {
  return Object.fromEntries(batch.proposals.map(proposal => [proposal.proposal_id, initialDraft(proposal)]));
}
function payloadValue(payload: ClaimPayload, field: PayloadField, value: string): ClaimPayload {
  const next = { ...payload };
  if (field !== 'text' && !value) delete next[field]; else next[field] = value;
  return next;
}
function active(batch: ImportBatch): boolean { return !terminal(batch.state) && batch.state !== 'waiting_for_approval'; }
function replacement(profile: Profile, proposal: ImportProposal, draft: ReviewDraft): ClaimRecord | undefined {
  return profile.current_revision.claims.find(claim => claim.id === draft.claim_id && claim.kind === proposal.kind && !claim.archived && claim.kind !== 'identity');
}
function unresolvedFields(proposal: ImportProposal, draft: ReviewDraft, target: ClaimRecord): PayloadField[] {
  return fields.filter(field => (target.revision.payload[field.key] ?? '') !== (proposal.payload[field.key] ?? '') && !draft.choices[field.key]).map(field => field.key);
}
function fileMediaType(file: File): ImportMediaType | null {
  const type = file.type.split(';')[0]?.trim().toLowerCase();
  if (type === 'application/pdf' || type === 'text/plain') return type;
  if (!type && /\.pdf$/i.test(file.name)) return 'application/pdf';
  if (!type && /\.txt$/i.test(file.name)) return 'text/plain';
  return null;
}

export function ResumeImport({ api, profile, disabled, onProfile, onDirtyChange, onBusyChange }: ResumeImportProps) {
  const [file, setFile] = useState<File | null>(null);
  const [paste, setPaste] = useState('');
  const [batches, setBatches] = useState<ImportSummary[] | null>(null);
  const [selected, setSelected] = useState<ImportBatch | null>(null);
  const [acceptedUpload, setAcceptedUpload] = useState<ImportAccepted | null>(null);
  const [drafts, setDrafts] = useState<Record<string, ReviewDraft>>({});
  const [reviewProfile, setReviewProfile] = useState<Profile | null>(null);
  const [comparison, setComparison] = useState<Profile | null>(null);
  const [reviewDirty, setReviewDirty] = useState(false);
  const [attested, setAttested] = useState(false);
  const [mode, setMode] = useState<ImportMergeRequest['mode']>('merge');
  const [replaceConfirmed, setReplaceConfirmed] = useState(false);
  const [operation, setOperation] = useState(false);
  const [mergeAwaiting, setMergeAwaiting] = useState(false);
  const [completionRefreshing, setCompletionRefreshing] = useState(false);
  const [refreshFailed, setRefreshFailed] = useState(false);
  const [refreshAttempt, setRefreshAttempt] = useState(0);
  const [pollPaused, setPollPaused] = useState(false);
  const [pollRun, setPollRun] = useState(0);
  const [conflict, setConflict] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const fileInput = useRef<HTMLInputElement>(null);
  const errorRef = useRef<HTMLDivElement>(null);
  const uploadRetry = useRef<{ source: File | string; version: number; key: string } | null>(null);
  const mergeRetry = useRef<{ fingerprint: string; key: string } | null>(null);
  const cancelRetry = useRef<{ workflow: string; version: number; key: string } | null>(null);
  const refreshed = useRef(new Set<string>());
  const pollBudget = useRef({ id: '', run: -1, count: 0 });
  const current = reviewProfile ?? profile;
  const inFlightMerge = !!selected && selected.text !== null && active(selected);
  const needsCompletionRefresh = !!selected && selected.state === 'succeeded' && !refreshed.current.has(selected.id);
  const busy = operation || mergeAwaiting || inFlightMerge || completionRefreshing || refreshFailed || needsCompletionRefresh;
  const dirty = file !== null || paste.length > 0 || reviewDirty;
  const locked = disabled || busy;
  const waiting = selected?.state === 'waiting_for_approval';
  const stale = waiting && !!reviewProfile && reviewProfile.version !== profile.version;
  const selectedId = selected?.id;
  const selectedState = selected?.state;

  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => { onBusyChange?.(busy); }, [busy, onBusyChange]);
  useEffect(() => { if (error) errorRef.current?.focus(); }, [error]);
  useEffect(() => {
    if (!dirty && !busy) return;
    const prevent = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    window.addEventListener('beforeunload', prevent);
    return () => window.removeEventListener('beforeunload', prevent);
  }, [dirty, busy]);

  const receiveBatch = useCallback((batch: ImportBatch) => {
    setSelected(batch);
    setBatches(existing => existing ? [batch, ...existing.filter(item => item.id !== batch.id)] : existing);
    setDrafts(existing => Object.fromEntries(batch.proposals.map(proposal => [proposal.proposal_id, existing[proposal.proposal_id] ?? initialDraft(proposal)])));
    if (terminal(batch.state)) setMergeAwaiting(false);
  }, []);

  useEffect(() => {
    if (!selectedId || !selectedState || selectedState === 'waiting_for_approval' || terminal(selectedState) || pollPaused) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    if (pollBudget.current.id !== selectedId || pollBudget.current.run !== pollRun) pollBudget.current = { id: selectedId, run: pollRun, count: 0 };
    let reading = false;
    const schedule = () => {
      if (!stopped && !document.hidden && !reading) timer = setTimeout(() => { void poll(); }, 1500);
    };
    const poll = async () => {
      if (stopped || document.hidden) return;
      reading = true;
      try {
        const batch = await api.request<ImportBatch>(`/imports/${encodeURIComponent(selectedId)}`);
        if (stopped) return;
        receiveBatch(batch);
        pollBudget.current.count += 1;
        if (!active(batch)) return;
        if (pollBudget.current.count >= 120) { setPollPaused(true); setNotice('Automatic checks paused after 120 requests. Reload the selected import to check again.'); return; }
      } catch (failure) {
        if (!stopped) { setError(errorText(failure)); setPollPaused(true); }
        return;
      } finally { reading = false; }
      schedule();
    };
    const visibility = () => {
      clearTimeout(timer);
      if (!document.hidden) schedule();
    };
    document.addEventListener('visibilitychange', visibility);
    schedule();
    return () => { stopped = true; clearTimeout(timer); document.removeEventListener('visibilitychange', visibility); };
  }, [api, selectedId, selectedState, pollPaused, pollRun, receiveBatch]);

  useEffect(() => {
    if (!selectedId || selectedState !== 'succeeded' || refreshed.current.has(selectedId)) return;
    let stopped = false;
    setCompletionRefreshing(true); setRefreshFailed(false);
    void api.request<Profile>('/profile').then(value => {
      if (stopped) return;
      refreshed.current.add(selectedId); onProfile(value); setReviewDirty(false); setMergeAwaiting(false);
      setError(''); setNotice(`Import completed. Loaded profile revision ${value.current_revision.revision_no}; earlier history is retained.`);
    }).catch((failure: unknown) => {
      if (!stopped) { setError(errorText(failure)); setRefreshFailed(true); }
    }).finally(() => { if (!stopped) setCompletionRefreshing(false); });
    return () => { stopped = true; };
  }, [api, selectedId, selectedState, onProfile, refreshAttempt]);

  async function perform(action: () => Promise<void>) {
    setOperation(true); setError(''); setNotice('');
    try { await action(); }
    catch (failure) {
      if (failure instanceof ApiError && failure.status === 412) setConflict(true);
      setError(errorText(failure));
    } finally { setOperation(false); }
  }
  function clearUpload() {
    setFile(null); setPaste(''); uploadRetry.current = null;
    if (fileInput.current) fileInput.current.value = '';
  }
  async function openBatch(id: string) {
    await perform(async () => {
      const batch = await api.request<ImportBatch>(`/imports/${encodeURIComponent(id)}`);
      if (selected?.id !== id) {
        setDrafts(initialDrafts(batch)); setReviewProfile(profile); setComparison(null);
        setReviewDirty(false); setAttested(false); setMode('merge'); setReplaceConfirmed(false); setConflict(false);
        mergeRetry.current = null;
      }
      receiveBatch(batch); setPollPaused(false); setPollRun(value => value + 1);
      if (acceptedUpload?.import_id === id) { setAcceptedUpload(null); clearUpload(); }
    });
  }
  function upload(event: FormEvent) {
    event.preventDefault();
    if (locked || reviewDirty || acceptedUpload || (selected && active(selected))) return;
    void perform(async () => {
      const source = file ?? paste;
      const media = file ? fileMediaType(file) : 'text/plain';
      if (!media) { setError('Choose a PDF or UTF-8 plain text file. No other file types are accepted.'); return; }
      const body = file ?? new Blob([paste], { type: 'text/plain;charset=utf-8' });
      if (!body.size || body.size > MAX_IMPORT_BYTES) { setError('Import a non-empty source no larger than 10 MiB. Your selected source remains here.'); return; }
      if (uploadRetry.current?.source !== source || uploadRetry.current.version !== profile.version) {
        uploadRetry.current = { source, version: profile.version, key: crypto.randomUUID() };
      }
      const accepted = await api.uploadImport(body, media, profile.version, uploadRetry.current.key);
      setAcceptedUpload(accepted); setSelected(null); setDrafts({}); setReviewProfile(profile); setComparison(null);
      setReviewDirty(false); setAttested(false); setMode('merge'); setReplaceConfirmed(false); setConflict(false);
      const batch = await api.request<ImportBatch>(`/imports/${encodeURIComponent(accepted.import_id)}`);
      receiveBatch(batch); setAcceptedUpload(null); clearUpload(); setPollPaused(false); setPollRun(value => value + 1);
      setNotice('Source accepted for isolated local extraction. No profile facts have been accepted.');
    });
  }
  function updateDraft(id: string, update: (draft: ReviewDraft) => ReviewDraft) {
    setDrafts(existing => {
      const draft = existing[id];
      return draft ? { ...existing, [id]: update(draft) } : existing;
    });
    setReviewDirty(true); setAttested(false); setError('');
  }
  function chooseField(proposal: ImportProposal, draft: ReviewDraft, target: ClaimRecord, key: PayloadField, choice: 'keep' | 'import') {
    const value = (choice === 'keep' ? target.revision.payload[key] : proposal.payload[key]) ?? '';
    updateDraft(proposal.proposal_id, () => ({ ...draft, payload: payloadValue(draft.payload, key, value), reviewed: false, choices: { ...draft.choices, [key]: choice } }));
  }
  function discardReview() {
    if (!selected) return;
    setDrafts(initialDrafts(selected)); setReviewDirty(false); setAttested(false); setMode('merge'); setReplaceConfirmed(false);
    setComparison(null); setReviewProfile(profile); setConflict(false); setError(''); mergeRetry.current = null;
  }
  async function loadComparison() {
    await perform(async () => { setComparison(await api.request<Profile>('/profile')); });
  }
  function rebase() {
    if (!comparison) return;
    const latest = comparison;
    if (selected) setDrafts(existing => Object.fromEntries(selected.proposals.map(proposal => {
      const draft = existing[proposal.proposal_id] ?? initialDraft(proposal);
      if (draft.action !== 'replace') return [proposal.proposal_id, draft];
      const before = replacement(current, proposal, draft);
      const after = replacement(latest, proposal, draft);
      const changed = fields.filter(field => (before?.revision.payload[field.key] ?? '') !== (after?.revision.payload[field.key] ?? ''));
      const choices = { ...draft.choices };
      for (const field of changed) delete choices[field.key];
      return [proposal.proposal_id, { ...draft, choices, reviewed: changed.length || !after ? false : draft.reviewed }];
    })));
    setReviewProfile(latest); onProfile(latest); setComparison(null); setConflict(false); setAttested(false); setReplaceConfirmed(false);
    setNotice('Latest profile selected. Your actions and edited text are retained. Review any changed replacement fields and approve again.');
  }
  const problems: string[] = [];
  let acceptedCount = 0;
  const targets = new Set<string>();
  if (selected && waiting) {
    for (const [index, proposal] of selected.proposals.entries()) {
      const draft = drafts[proposal.proposal_id];
      if (!draft?.reviewed) problems.push(`Review proposal ${index + 1}, including skipped proposals.`);
      if (!draft || draft.action === 'skip') continue;
      acceptedCount += 1;
      if (!draft.payload.text.trim()) problems.push(`Proposal ${index + 1} needs a statement.`);
      if (draft.action === 'replace') {
        const target = replacement(current, proposal, draft);
        if (!target) problems.push(`Choose an active, same-kind replacement for proposal ${index + 1}.`);
        else if (unresolvedFields(proposal, draft, target).length) problems.push(`Choose current or imported values for every differing field in proposal ${index + 1}.`);
        if (targets.has(draft.claim_id)) problems.push('Each existing fact can be replaced only once.');
        targets.add(draft.claim_id);
      }
    }
  }
  function submitReview(event: FormEvent) {
    event.preventDefault();
    if (!selected || !waiting || locked || conflict || stale || !attested || problems.length || !acceptedCount || (mode === 'replace_all' && !replaceConfirmed)) return;
    const body: ImportMergeRequest = {
      mode, replace_all_confirmed: mode === 'replace_all' && replaceConfirmed,
      decisions: selected.proposals.map(proposal => {
        const draft = drafts[proposal.proposal_id] ?? initialDraft(proposal);
        return { proposal_id: proposal.proposal_id, action: draft.action, kind: proposal.kind, payload: draft.payload,
          ...(draft.action === 'replace' ? { claim_id: draft.claim_id } : {}) };
      }),
    };
    const path = `/imports/${encodeURIComponent(selected.id)}/merge`;
    const fingerprint = JSON.stringify({ path, body, version: current.version });
    if (mergeRetry.current?.fingerprint !== fingerprint) mergeRetry.current = { fingerprint, key: crypto.randomUUID() };
    const key = mergeRetry.current.key;
    void perform(async () => {
      await api.request<ImportAccepted>(path, 'POST', body, current.version, key);
      setMergeAwaiting(true); setNotice('Reviewed import accepted. Waiting for the atomic profile update; it is not saved yet.');
      const batch = await api.request<ImportBatch>(`/imports/${encodeURIComponent(selected.id)}`);
      receiveBatch(batch); setPollPaused(false); setPollRun(value => value + 1);
    });
  }
  async function cancel() {
    if (!selected) return;
    await perform(async () => {
      if (cancelRetry.current?.workflow !== selected.workflow_id || cancelRetry.current.version !== selected.version) {
        cancelRetry.current = { workflow: selected.workflow_id, version: selected.version, key: crypto.randomUUID() };
      }
      const workflow = await api.request<Workflow>(`/workflows/${encodeURIComponent(selected.workflow_id)}/cancel`, 'POST', {}, selected.version, cancelRetry.current.key);
      receiveBatch({ ...selected, state: workflow.state, version: workflow.version, error_code: workflow.last_error_code });
      const batch = await api.request<ImportBatch>(`/imports/${encodeURIComponent(selected.id)}`);
      receiveBatch(batch); setNotice('Import cancelled. Review text is retained here; no further merge will run.');
    });
  }

  return <section className="panel" aria-labelledby="resume-import-title">
    <h2 id="resume-import-title">Import a résumé</h2>
    <p>Local deterministic extraction copies literal source lines into proposals. It does not use AI, invent missing details, or independently verify facts. No provider or API key is required. Hosted extraction is unavailable and off; there is no hosted fallback.</p>
    <p className="hint">PDF with selectable text or UTF-8 plain text, up to 10 MiB. No OCR. Embedded instructions remain inert source text. Your profile changes only after explicit review and approval.</p>
    {disabled && <p className="disclosure">Save or discard changes in the other profile editor before uploading or approving an import. Your import buffers remain here.</p>}
    <div className="error" ref={errorRef} tabIndex={-1} role="alert" hidden={!error}>{error}</div>
    <p className="status" role="status">{notice}</p>
    <form onSubmit={upload}>
      <fieldset disabled={locked || reviewDirty || !!acceptedUpload || !!(selected && active(selected))}>
        <legend>Choose a local source</legend>
        <label>Résumé PDF or text file<input ref={fileInput} type="file" accept="application/pdf,text/plain,.pdf,.txt" disabled={paste.length > 0} onChange={event => { setFile(event.target.files?.[0] ?? null); setError(''); }} /></label>
        {file && <p>Selected: {file.name} · {file.size.toLocaleString()} bytes. The filename is not sent to the server.</p>}
        <label>Or paste résumé text<textarea value={paste} disabled={file !== null} onChange={event => { setPaste(event.target.value); setError(''); }} /></label>
        <div className="actions"><button disabled={!file && !paste.length}>Upload for local extraction</button>
          <button type="button" className="secondary" disabled={!file && !paste.length} onClick={clearUpload}>Discard source buffer</button></div>
      </fieldset>
    </form>
    {acceptedUpload && <div className="disclosure"><p>The upload was accepted as {acceptedUpload.import_id}. The source buffer is retained until its status loads; do not upload it again.</p>
      <button className="secondary" disabled={operation || disabled} onClick={() => { void openBatch(acceptedUpload.import_id); }}>Load accepted import</button></div>}
    <div className="saved-tasks"><h3>Saved imports</h3>
      <button className="secondary" disabled={operation || disabled} onClick={() => { void perform(async () => { setBatches((await api.request<ImportPage>('/imports')).items); }); }}>{batches ? 'Reload saved imports' : 'Load saved imports'}</button>
      {batches?.length === 0 && <p>No saved imports.</p>}
      {batches && <ul>{batches.map(batch => <li key={batch.id}><button className="secondary" disabled={operation || disabled || busy || (dirty && selected?.id !== batch.id) || !!acceptedUpload} onClick={() => { void openBatch(batch.id); }}>Open import {batch.id}</button><p>{batch.media_type} · {batch.created_at} · {batch.state.replaceAll('_', ' ')}</p></li>)}</ul>}
      {dirty && <p className="hint">Discard or finish the current source/review buffer before switching imports.</p>}
    </div>
    {(conflict || stale) && <p className="disclosure">A newer profile needs comparison. Your review selections and edited values have not been overwritten. Approval is blocked until you explicitly use the latest profile.</p>}
    {(conflict || stale || (error && waiting)) && <button className="secondary" disabled={operation || disabled || mergeAwaiting} onClick={() => { void loadComparison(); }}>Load current profile for comparison</button>}
    {comparison && <div className="comparison"><h3>Current server profile revision {comparison.current_revision.revision_no}</h3>
      <p>{comparison.current_revision.snapshot.display_name} · {comparison.current_revision.snapshot.headline}</p>
      {comparison.current_revision.claims.map(claim => <div key={claim.id}><strong>{claim.kind} · {claim.id}</strong><dl>{fields.map(field => <div key={field.key}><dt>{field.label}</dt><dd>{claim.revision.payload[field.key] || 'Not supplied'}</dd></div>)}</dl></div>)}
      <p>Use this version only after comparison. Your reviewed values remain buffered; replacement fields whose current values changed need a fresh choice. Missing or archived targets must be selected again.</p>
      <button className="secondary" disabled={operation || disabled || mergeAwaiting} onClick={rebase}>Use latest profile and retain review</button>
    </div>}
    {selected && <article className="task" aria-labelledby="selected-import-title"><h3 id="selected-import-title">Import {selected.id}</h3>
      <p>State: {selected.state.replaceAll('_', ' ')} · workflow version {selected.version} · {selected.byte_size.toLocaleString()} bytes</p>
      <p className="hint">Workflow {selected.workflow_id} · source accepted {selected.created_at} · profile version {selected.profile_version}</p>
      {selected.error_code && <p className="disclosure">Import stopped ({selected.error_code}). A failed import does not partially update your profile.</p>}
      {mergeAwaiting && <p className="disclosure">Reviewed merge is pending. Other profile editors stay blocked until completion, cancellation, or failure. A successful request alone is not a saved profile.</p>}
      {pollPaused && <p className="hint">Automatic status checks are paused. Reload this import to retry. Source and review text remain here.</p>}
      <div className="actions"><button className="secondary" disabled={operation || disabled || completionRefreshing} onClick={() => { void openBatch(selected.id); }}>Reload selected import</button>
        {!terminal(selected.state) && <button className="secondary" disabled={operation || disabled || completionRefreshing} onClick={() => { void cancel(); }}>Cancel import</button>}
        {refreshFailed && <button className="secondary" disabled={operation || disabled} onClick={() => { setRefreshAttempt(value => value + 1); }}>Refresh completed profile</button>}
        {reviewDirty && <button className="secondary" disabled={locked} onClick={discardReview}>Discard review choices</button>}
      </div>
      {selected.warnings.length > 0 && <ul className="disclosure" aria-label="Import warnings">{selected.warnings.map((warning, index) => <li key={`${warning}-${index}`}>{warningLabels[warning] ?? warning.replaceAll('_', ' ')}</li>)}</ul>}
      {selected.source_sha256 && <p>Normalized source SHA-256: <code>{selected.source_sha256}</code></p>}
      {selected.text !== null && <details><summary>Read complete normalized source text</summary><pre className="import-source">{selected.text}</pre></details>}
      {selected.proposals.length > 0 && <form onSubmit={submitReview}>
        <p>Each proposal starts skipped. Mark every proposal reviewed, including skips. Accepted claims pin the exact quote, source digest and Unicode span below as owner-asserted provenance.</p>
        {selected.proposals.map((proposal, index) => {
          const draft = drafts[proposal.proposal_id] ?? initialDraft(proposal);
          const target = replacement(current, proposal, draft);
          const available = current.current_revision.claims.filter(claim => !claim.archived && claim.kind === proposal.kind && claim.kind !== 'identity');
          return <fieldset key={proposal.proposal_id} disabled={locked || !waiting}>
            <legend>Proposal {index + 1} · {proposal.kind}</legend>
            <p>Exact source quote (Unicode span {proposal.source_start}–{proposal.source_end}, end exclusive):</p>
            <blockquote className="import-source">{proposal.quote}</blockquote>
            <small>Proposal {proposal.proposal_id} · source SHA-256 {selected.source_sha256 ?? 'Not available'}</small>
            <label>Decision for proposal {index + 1}<select value={draft.action} onChange={event => updateDraft(proposal.proposal_id, value => ({ ...value, action: event.target.value as ImportDecision['action'], reviewed: false }))}>
              <option value="skip">Skip this proposal</option><option value="add">Add as a new fact</option>
              {proposal.kind !== 'identity' && <option value="replace">Replace an existing same-kind fact</option>}
            </select></label>
            {draft.action === 'replace' && <><label>Replacement target for proposal {index + 1}<select value={draft.claim_id} onChange={event => updateDraft(proposal.proposal_id, value => ({ ...value, claim_id: event.target.value, choices: {}, reviewed: false }))}>
              <option value="">Choose an existing {proposal.kind} fact</option>
              {draft.claim_id && !target && <option value={draft.claim_id}>Previous target unavailable · {draft.claim_id}</option>}
              {available.map(claim => <option key={claim.id} value={claim.id}>{claim.revision.payload.text} · {claim.id}</option>)}
            </select></label><p className="hint">Compare against profile revision {current.current_revision.revision_no}. Every differing field needs an explicit current/imported choice or your own edit. No existing value wins silently.</p></>}
            {draft.action !== 'skip' && <><p className="hint">Dates accept YYYY, YYYY-MM or YYYY-MM-DD. Leave unknown values empty; do not invent precision. Typed changes are your own assertion.</p>
              {fields.map(field => <div className={target ? 'comparison' : undefined} key={field.key}>
                {target && <><h4>{field.label} comparison</h4><p>Current: {target.revision.payload[field.key] || 'Not supplied'}</p><p>Imported: {proposal.payload[field.key] || 'Not supplied'}</p>
                  {(target.revision.payload[field.key] ?? '') !== (proposal.payload[field.key] ?? '') && <div className="actions">
                    <button type="button" className="secondary" aria-pressed={draft.choices[field.key] === 'keep'} onClick={() => chooseField(proposal, draft, target, field.key, 'keep')}>Keep current {field.label.toLowerCase()} for proposal {index + 1}</button>
                    <button type="button" className="secondary" aria-pressed={draft.choices[field.key] === 'import'} onClick={() => chooseField(proposal, draft, target, field.key, 'import')}>Use imported {field.label.toLowerCase()} for proposal {index + 1}</button>
                  </div>}
                  <p className="hint">Choice: {draft.choices[field.key] === 'keep' ? 'keep current' : draft.choices[field.key] === 'import' ? 'use imported' : draft.choices[field.key] === 'edit' ? 'your edited value' : (target.revision.payload[field.key] ?? '') === (proposal.payload[field.key] ?? '') ? 'values match' : 'not yet chosen'}</p>
                </>}
                <label>{field.label} for proposal {index + 1}{field.key === 'text' ? <textarea required maxLength={field.limit} value={draft.payload[field.key] ?? ''} onChange={event => updateDraft(proposal.proposal_id, value => ({ ...value, payload: payloadValue(value.payload, field.key, event.target.value), choices: { ...value.choices, [field.key]: 'edit' }, reviewed: false }))} />
                  : <input maxLength={field.limit} value={draft.payload[field.key] ?? ''} onChange={event => updateDraft(proposal.proposal_id, value => ({ ...value, payload: payloadValue(value.payload, field.key, event.target.value), choices: { ...value.choices, [field.key]: 'edit' }, reviewed: false }))} />}</label>
              </div>)}
            </>}
            <label><input type="checkbox" checked={draft.reviewed} onChange={event => updateDraft(proposal.proposal_id, value => ({ ...value, reviewed: event.target.checked }))} />I reviewed proposal {index + 1} and its {draft.action === 'skip' ? 'skip decision' : 'source and field choices'}</label>
          </fieldset>;
        })}
        {waiting && <fieldset disabled={locked}>
          <legend>Approve reviewed facts</legend>
          <label>Import mode<select value={mode} onChange={event => { setMode(event.target.value as ImportMergeRequest['mode']); setReplaceConfirmed(false); setAttested(false); setReviewDirty(true); }}><option value="merge">Merge only selected facts; keep unrelated profile facts</option><option value="replace_all">Replace all non-identity profile facts with selected facts</option></select></label>
          {mode === 'replace_all' && <div className="disclosure"><p>Replace all archives displaced active facts and sources. Identity and immutable history are retained, but unrelated active facts will leave the current profile. This is separate from ordinary merge.</p>
            <label><input type="checkbox" checked={replaceConfirmed} onChange={event => { setReplaceConfirmed(event.target.checked); setAttested(false); setReviewDirty(true); }} />I separately confirm replacing all non-identity facts and archiving displaced sources</label></div>}
          <p>{acceptedCount} of {selected.proposals.length} proposals selected. Profile revision {current.current_revision.revision_no} will be the exact merge precondition.</p>
          {problems.length > 0 && <ul aria-label="Remaining review steps">{Array.from(new Set(problems)).map(problem => <li key={problem}>{problem}</li>)}</ul>}
          {!acceptedCount && <p className="hint">Choose at least one proposal to add or replace. Skips alone cannot change the profile.</p>}
          <label><input type="checkbox" checked={attested} onChange={event => { setAttested(event.target.checked); setReviewDirty(true); }} />I reviewed every proposal and attest that selected facts and edited values are accurate owner assertions, not independent verification</label>
          <button disabled={!attested || problems.length > 0 || !acceptedCount || conflict || stale || (mode === 'replace_all' && !replaceConfirmed)}>Approve and merge reviewed facts</button>
        </fieldset>}
      </form>}
    </article>}
  </section>;
}
