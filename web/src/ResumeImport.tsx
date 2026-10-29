// SPDX-License-Identifier: MIT OR Apache-2.0
import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { Api, ApiError, errorText, MAX_IMPORT_BYTES, terminal, type ClaimKind, type ClaimPayload, type ClaimRecord, type ImportAccepted, type ImportBatch, type ImportDecision, type ImportEntry, type ImportMediaType, type ImportMergeRequest, type ImportPage, type ImportSummary, type Profile, type Workflow } from './api';
import './ResumeImport.css';

export interface ResumeImportProps {
  api: Api; profile: Profile; disabled: boolean; onProfile: (profile: Profile) => void;
  onDirtyChange?: (dirty: boolean) => void; onBusyChange?: (busy: boolean) => void;
}
type PayloadField = keyof ClaimPayload;
type FieldChoice = 'keep' | 'import' | 'edit';
interface ReviewDraft {
  action: ImportDecision['action']; kind: ImportEntry['kind']; identity_field: ImportEntry['identity_field'];
  claim_id: string; payload: ClaimPayload; reviewed: boolean; editing: boolean; identityConfirmed: boolean;
  choices: Partial<Record<PayloadField, FieldChoice>>;
}
const fields: { key: PayloadField; label: string; limit: number }[] = [
  { key: 'text', label: 'Description', limit: 4000 }, { key: 'organization', label: 'Organization', limit: 200 },
  { key: 'role', label: 'Role or qualification', limit: 200 }, { key: 'start_date', label: 'Start date', limit: 10 },
  { key: 'end_date', label: 'End date', limit: 10 }, { key: 'proficiency', label: 'Proficiency or level', limit: 120 },
  { key: 'project_url', label: 'Project URL', limit: 2048 },
];
const warningLabels: Record<string, string> = {
  deterministic_literal_extraction: 'Entries are suggested from literal local extraction, not AI interpretation.',
  evidence_is_owner_asserted_not_independently_verified: 'Imported facts are owner assertions, not independently verified evidence.',
  ambiguous_section_kept_as_other: 'Some source sections need you to choose where they belong.',
  contact_details_excluded: 'Recognizable contact or reference details were excluded from review.',
  pdf_layout_requires_review: 'Complex PDF layout was preserved in extraction order. Check source line breaks and reading order before accepting facts.',
};
const destinations: { value: string; label: string }[] = [
  { value: '', label: 'Needs placement' }, { value: 'display_name', label: 'Personal details · Full name' },
  { value: 'headline', label: 'Personal details · Headline' }, { value: 'experience', label: 'Work experience' },
  { value: 'education', label: 'Education' }, { value: 'skill', label: 'Skills' }, { value: 'project', label: 'Projects' },
  { value: 'certification', label: 'Certifications' }, { value: 'language', label: 'Languages' },
  { value: 'preference', label: 'Preferences' }, { value: 'other', label: 'Additional information' },
];
function destination(draft: ReviewDraft): string { return draft.kind === 'identity' ? draft.identity_field ?? '' : draft.kind ?? ''; }
function destinationLabel(draft: ReviewDraft): string { return destinations.find(item => item.value === destination(draft))?.label ?? 'Needs placement'; }
function snippet(text: string): string { const line = text.replace(/\s+/g, ' ').trim(); return line.length > 72 ? `${line.slice(0, 69)}…` : line || 'Untitled entry'; }
function initialDraft(entry: ImportEntry): ReviewDraft {
  return { action: 'add', kind: entry.kind, identity_field: entry.identity_field, claim_id: '', payload: entry.kind === 'identity' ? { text: entry.payload.text } : { ...entry.payload }, reviewed: false, editing: false, identityConfirmed: false, choices: {} };
}
function initialDrafts(batch: ImportBatch): Record<string, ReviewDraft> {
  return Object.fromEntries(batch.entries.map(entry => [entry.entry_id, initialDraft(entry)]));
}
function entryFields(draft: ReviewDraft, target?: ClaimRecord) {
  const keys: PayloadField[] = draft.kind === 'identity' ? ['text'] : draft.kind === 'skill' || draft.kind === 'language' ? ['text', 'proficiency']
    : draft.kind === 'preference' || draft.kind === 'other' || !draft.kind ? ['text']
    : ['text', 'organization', 'role', 'start_date', 'end_date', ...(draft.kind === 'project' ? ['project_url' as const] : [])];
  return fields.filter(field => keys.includes(field.key) || !!draft.payload[field.key] || !!target?.revision.payload[field.key]).map(field => ({
    ...field, label: field.key === 'text' ? draft.kind === 'identity' ? draft.identity_field === 'display_name' ? 'Full name' : 'Headline'
      : draft.kind === 'skill' ? 'Skill' : draft.kind === 'language' ? 'Language' : 'Description'
      : field.key === 'organization' && draft.kind === 'education' ? 'School or institution'
      : field.key === 'role' && draft.kind === 'experience' ? 'Job title'
      : field.key === 'role' && draft.kind === 'education' ? 'Qualification' : field.label,
  }));
}
function payloadValue(payload: ClaimPayload, field: PayloadField, value: string): ClaimPayload {
  const next = { ...payload };
  if (field !== 'text' && !value) delete next[field]; else next[field] = value;
  return next;
}
function active(batch: ImportBatch): boolean { return !terminal(batch.state) && batch.state !== 'waiting_for_approval'; }
function replacement(profile: Profile, draft: ReviewDraft): ClaimRecord | undefined {
  return profile.current_revision.claims.find(claim => claim.id === draft.claim_id && claim.kind === draft.kind && !claim.archived && claim.kind !== 'identity');
}
function unresolvedFields(entry: ImportEntry, draft: ReviewDraft, target: ClaimRecord): PayloadField[] {
  return fields.filter(field => (target.revision.payload[field.key] ?? '') !== (entry.payload[field.key] ?? '') && !draft.choices[field.key]).map(field => field.key);
}
function dateBounds(value: string): [number, number] | null {
  if (!/^\d{4}(-\d{2}(-\d{2})?)?$/.test(value)) return null;
  const parts = value.split('-').map(Number);
  const year = parts[0]!;
  const month = parts[1] ?? 1;
  const day = parts[2] ?? 1;
  const days = [31, year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0) ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (!year || month < 1 || month > 12 || day < 1 || day > days[month - 1]!) return null;
  const lastMonth = parts.length > 1 ? month : 12;
  const lastDay = parts.length > 2 ? day : days[lastMonth - 1]!;
  return [year * 10000 + month * 100 + day, year * 10000 + lastMonth * 100 + lastDay];
}
function entryProblems(entry: ImportEntry, draft: ReviewDraft, profile: Profile): string[] {
  const problems: string[] = [];
  if (!destination(draft)) problems.push('Choose a destination before keeping this entry.');
  if (!draft.payload.text.trim()) problems.push('Enter a value to save.');
  for (const field of fields) {
    const value = draft.payload[field.key] ?? '';
    const limit = draft.kind === 'identity' && field.key === 'text' ? draft.identity_field === 'display_name' ? 120 : 300 : field.limit;
    if (value.length > limit) problems.push(`${field.label} must be no longer than ${limit} characters.`);
    if ((field.key === 'start_date' || field.key === 'end_date') && value && !dateBounds(value)) problems.push('Use a real calendar date in YYYY, YYYY-MM or YYYY-MM-DD format.');
  }
  const start = draft.payload.start_date ? dateBounds(draft.payload.start_date) : null;
  const end = draft.payload.end_date ? dateBounds(draft.payload.end_date) : null;
  if (start && end && start[0] > end[1]) problems.push('End date cannot be before start date.');
  if (draft.payload.project_url) {
    try {
      const url = new URL(draft.payload.project_url);
      let hasControl = false;
      for (const character of draft.payload.project_url) {
        if (character.charCodeAt(0) < 32) { hasControl = true; break; }
      }
      if (!['http:', 'https:'].includes(url.protocol) || !url.hostname || url.username || url.password || /[\s\\]/.test(draft.payload.project_url) || hasControl) problems.push('Use an HTTP or HTTPS project URL without credentials or whitespace.');
    } catch { problems.push('Use an HTTP or HTTPS project URL without credentials or whitespace.'); }
  }
  if (entry.sources.map(source => source.quote).join('\n').length > 4000) problems.push('This source group is too long to save as one entry. Split it into original source parts.');
  if (draft.kind === 'identity' && draft.identity_field && profile.current_revision.snapshot[draft.identity_field]
      && !draft.identityConfirmed) problems.push('Confirm the personal detail comparison before keeping this entry.');
  if (draft.action === 'replace' && draft.kind !== 'identity') {
    const target = replacement(profile, draft);
    if (!target) problems.push('Choose an active existing entry in the same destination.');
    else if (unresolvedFields(entry, draft, target).length) problems.push('Choose current or imported values, or edit, for each differing field.');
  }
  return [...new Set(problems)];
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
  const [entries, setEntries] = useState<ImportEntry[]>([]);
  const [entryIndex, setEntryIndex] = useState(0);
  const [savedId, setSavedId] = useState('');
  const [sourceExpanded, setSourceExpanded] = useState(true);
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
  const reviewBatchId = useRef<string | null>(null);
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
  const entryCount = entries.length;
  const currentIndex = Math.min(entryIndex, Math.max(0, entryCount - 1));
  const targetEntries = new Map<string, string[]>();
  for (const entry of entries) {
    const draft = drafts[entry.entry_id];
    if (!draft?.reviewed || draft.action === 'skip') continue;
    const key = draft.kind === 'identity' ? `identity:${draft.identity_field}` : draft.action === 'replace' ? `claim:${draft.claim_id}` : '';
    if (key) targetEntries.set(key, [...(targetEntries.get(key) ?? []), entry.entry_id]);
  }
  const duplicateEntryIds = new Set([...targetEntries.values()].filter(ids => ids.length > 1).flat());
  const excludedCount = entries.filter(entry => drafts[entry.entry_id]?.reviewed && drafts[entry.entry_id]?.action === 'skip').length;
  const readyCount = entries.filter(entry => {
    const draft = drafts[entry.entry_id];
    return draft?.reviewed && draft.action !== 'skip' && !duplicateEntryIds.has(entry.entry_id) && !entryProblems(entry, draft, current).length;
  }).length;
  const unreviewedCount = entryCount - excludedCount - readyCount;
  const reviewedCount = entryCount - unreviewedCount;
  const stage = selected?.state === 'succeeded' ? 3 : selected?.text != null ? 2 : selected || acceptedUpload ? 1 : 0;
  const historyId = batches?.some(batch => batch.id === savedId) ? savedId : batches?.[0]?.id ?? '';

  function nextUnreviewed() {
    if (!selected) return;
    for (let offset = 1; offset <= entryCount; offset += 1) {
      const index = (currentIndex + offset) % entryCount;
      const entry = entries[index]!;
      const draft = drafts[entry.entry_id];
      if (!draft?.reviewed || duplicateEntryIds.has(entry.entry_id) || (draft.action !== 'skip' && entryProblems(entry, draft, current).length)) { setEntryIndex(index); return; }
    }
  }

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
    const changed = reviewBatchId.current !== batch.id;
    reviewBatchId.current = batch.id;
    setEntries(existing => changed || !existing.length ? batch.entries : existing);
    setDrafts(existing => changed || !Object.keys(existing).length ? initialDrafts(batch) : { ...initialDrafts(batch), ...existing });
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
        setEntryIndex(0);
        setReviewDirty(false); setAttested(false); setMode('merge'); setReplaceConfirmed(false); setConflict(false);
        mergeRetry.current = null;
      }
      receiveBatch(batch); setPollPaused(false); setPollRun(value => value + 1);
      setSourceExpanded(false);
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
      setEntryIndex(0); setEntries([]); reviewBatchId.current = null; setSourceExpanded(false);
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
  function chooseField(entry: ImportEntry, draft: ReviewDraft, target: ClaimRecord, key: PayloadField, choice: 'keep' | 'import') {
    const value = (choice === 'keep' ? target.revision.payload[key] : entry.payload[key]) ?? '';
    updateDraft(entry.entry_id, () => ({ ...draft, payload: payloadValue(draft.payload, key, value), reviewed: false, choices: { ...draft.choices, [key]: choice } }));
  }
  function regroup(index: number, split: boolean) {
    const first = entries[index];
    if (!first || locked || !waiting) return;
    let grouped: ImportEntry[];
    let removeCount = 1;
    if (split) {
      if (first.sources.length < 2) return;
      const draft = drafts[first.entry_id] ?? initialDraft(first);
      grouped = first.sources.map(source => ({
        entry_id: crypto.randomUUID(), kind: draft.kind === 'identity' ? null : draft.kind, identity_field: null,
        payload: { text: source.quote }, sources: [source], issues: [],
      }));
    } else {
      const second = entries[index + 1];
      if (!second) return;
      const a = drafts[first.entry_id] ?? initialDraft(first);
      const b = drafts[second.entry_id] ?? initialDraft(second);
      const sameDestination = destination(a) === destination(b);
      const payload = { ...a.payload, text: `${a.payload.text}\n${b.payload.text}` };
      for (const field of fields) {
        if (field.key === 'text' || !b.payload[field.key]) continue;
        const left = a.payload[field.key];
        const right = b.payload[field.key]!;
        if (!left) payload[field.key] = right;
        else if (left !== right) {
          delete payload[field.key];
          payload.text += `\n${field.label}: ${left}\n${field.label}: ${right}`;
        }
      }
      grouped = [{
        entry_id: crypto.randomUUID(), kind: sameDestination ? a.kind : null, identity_field: sameDestination ? a.identity_field : null,
        payload,
        sources: [...first.sources, ...second.sources], issues: [],
      }];
      removeCount = 2;
    }
    setEntries(existing => [...existing.slice(0, index), ...grouped, ...existing.slice(index + removeCount)]);
    setDrafts(existing => {
      const next = { ...existing };
      for (const removed of entries.slice(index, index + removeCount)) delete next[removed.entry_id];
      for (const entry of grouped) next[entry.entry_id] = initialDraft(entry);
      return next;
    });
    setEntryIndex(index); setReviewDirty(true); setAttested(false); setError('');
  }
  function discardReview() {
    if (!selected) return;
    setDrafts(initialDrafts(selected)); setReviewDirty(false); setAttested(false); setMode('merge'); setReplaceConfirmed(false);
    setEntries(selected.entries); setEntryIndex(0);
    setComparison(null); setReviewProfile(profile); setConflict(false); setError(''); mergeRetry.current = null;
  }
  async function loadComparison() {
    await perform(async () => { setComparison(await api.request<Profile>('/profile')); });
  }
  function rebase() {
    if (!comparison) return;
    const latest = comparison;
    setDrafts(existing => Object.fromEntries(entries.map(entry => {
      const draft = existing[entry.entry_id] ?? initialDraft(entry);
      if (draft.action === 'skip') return [entry.entry_id, draft];
      if (draft.kind === 'identity' && draft.identity_field
          && current.current_revision.snapshot[draft.identity_field] !== latest.current_revision.snapshot[draft.identity_field]) {
        return [entry.entry_id, { ...draft, reviewed: false, identityConfirmed: false }];
      }
      if (draft.action !== 'replace') return [entry.entry_id, draft];
      const before = replacement(current, draft);
      const after = replacement(latest, draft);
      const changed = fields.filter(field => (before?.revision.payload[field.key] ?? '') !== (after?.revision.payload[field.key] ?? ''));
      const choices = { ...draft.choices };
      for (const field of changed) delete choices[field.key];
      return [entry.entry_id, { ...draft, choices, reviewed: changed.length || !after ? false : draft.reviewed }];
    })));
    setReviewProfile(latest); onProfile(latest); setComparison(null); setConflict(false); setAttested(false); setReplaceConfirmed(false);
    setNotice('Latest profile selected. Your actions and edited text are retained. Review any changed replacement fields and approve again.');
  }
  const problems: string[] = [];
  let acceptedCount = 0;
  const targets = new Set<string>();
  const identityTargets = new Set<string>();
  if (selected && waiting) {
    for (const entry of entries) {
      const draft = drafts[entry.entry_id];
      const label = snippet(draft?.payload.text ?? entry.payload.text);
      if (!draft?.reviewed) problems.push(`Review “${label}”: keep or exclude it.`);
      if (!draft || draft.action === 'skip') continue;
      acceptedCount += 1;
      problems.push(...entryProblems(entry, draft, current).map(problem => `${label}: ${problem}`));
      if (draft.kind === 'identity' && draft.identity_field) {
        if (identityTargets.has(draft.identity_field)) problems.push('Keep only one entry for each personal detail.');
        identityTargets.add(draft.identity_field);
      }
      if (draft.action === 'replace' && draft.kind !== 'identity') {
        if (targets.has(draft.claim_id)) problems.push('Each existing entry can be replaced only once.');
        targets.add(draft.claim_id);
      }
    }
  }
  function submitReview(event: FormEvent) {
    event.preventDefault();
    if (!selected || !waiting || locked || conflict || stale || !attested || problems.length || !acceptedCount || (mode === 'replace_all' && !replaceConfirmed)) return;
    const body: ImportMergeRequest = {
      mode, replace_all_confirmed: mode === 'replace_all' && replaceConfirmed,
      decisions: entries.map(entry => {
        const draft = drafts[entry.entry_id] ?? initialDraft(entry);
        return { source_ids: entry.sources.map(source => source.source_id), action: draft.action, kind: draft.kind, identity_field: draft.identity_field, payload: draft.payload,
          ...(draft.action === 'replace' && draft.kind !== 'identity' ? { claim_id: draft.claim_id } : {}) };
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

  return <section className="panel resume-import" aria-labelledby="resume-import-title">
    <div className="import-intro"><h2 id="resume-import-title">Import a résumé</h2>
      <p>Turn your résumé into profile facts, one decision at a time. Nothing changes in your profile until you review and approve it.</p>
    </div>
    <ol className="import-steps" aria-label="Import progress">{['Choose source', 'Extract', 'Review', 'Complete'].map((label, index) => <li key={label} aria-current={stage === index ? 'step' : undefined} className={stage > index ? 'is-complete' : undefined}>
      <span aria-hidden="true">{stage > index ? '✓' : index + 1}</span><span>{label}</span>
    </li>)}</ol>
    <p className="hint">Local extraction only. PDF with selectable text or UTF-8 plain text, up to 10 MiB. No OCR, AI interpretation or hosted fallback. Embedded instructions remain inert; extracted facts are not independently verified.</p>
    {disabled && <p className="disclosure">Save or discard changes in the other profile editor before uploading or approving an import. Your import buffers remain here.</p>}
    <div className="error" ref={errorRef} tabIndex={-1} role="alert" hidden={!error}>{error}</div>
    <p className="status" role="status">{notice}</p>
    <details className="import-source-picker" open={sourceExpanded} onToggle={event => setSourceExpanded(event.currentTarget.open)}>
      <summary>Choose a local source{file || paste ? ' · source buffer retained' : ''}</summary>
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
    </details>
    {acceptedUpload && <div className="disclosure"><p>The upload was accepted as {acceptedUpload.import_id}. The source buffer is retained until its status loads; do not upload it again.</p>
      <button className="secondary" disabled={operation || disabled} onClick={() => { void openBatch(acceptedUpload.import_id); }}>Load accepted import</button></div>}
    <div className="saved-tasks import-history"><h3>Saved imports</h3>
      <p className="hint">Resume a previous review or check its outcome.</p>
      <button className="secondary" disabled={operation || disabled} onClick={() => { void perform(async () => { setBatches((await api.request<ImportPage>('/imports')).items); }); }}>{batches ? 'Reload saved imports' : 'Load saved imports'}</button>
      {batches?.length === 0 && <p>No saved imports.</p>}
      {batches && batches.length > 0 && <div className="import-history-picker">
        <label>Saved import<select value={historyId} disabled={operation || disabled || busy || dirty || !!acceptedUpload} onChange={event => setSavedId(event.target.value)}>
          {batches.map(batch => <option key={batch.id} value={batch.id}>{batch.created_at} · {batch.media_type} · {batch.state.replaceAll('_', ' ')} · {batch.id}</option>)}
        </select></label>
        <button className="secondary" disabled={operation || disabled || busy || (dirty && selected?.id !== historyId) || !!acceptedUpload} onClick={() => { void openBatch(historyId); }}>Open import {historyId}</button>
      </div>}
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
      {selected.text === null && !terminal(selected.state) && <p className="disclosure">Extracting your source. You can leave this screen and return; no profile facts have been accepted.</p>}
      {selected.state === 'succeeded' && <p className="disclosure">{completionRefreshing || needsCompletionRefresh ? 'Merge completed. Loading the updated profile before releasing other editors.' : 'Complete. The updated profile is loaded; earlier history is retained.'}</p>}
      {waiting && entries.length === 0 && <p className="disclosure">No entries were extracted. Nothing can be saved. Cancel this import and choose another source.</p>}
      {mergeAwaiting && <p className="disclosure">Reviewed merge is pending. Other profile editors stay blocked until completion, cancellation, or failure. A successful request alone is not a saved profile.</p>}
      {pollPaused && <p className="hint">Automatic status checks are paused. Reload this import to retry. Source and review text remain here.</p>}
      <div className="actions"><button className="secondary" disabled={operation || disabled || completionRefreshing} onClick={() => { void openBatch(selected.id); }}>Reload selected import</button>
        {!terminal(selected.state) && <button className="secondary" disabled={operation || disabled || completionRefreshing} onClick={() => { void cancel(); }}>Cancel import</button>}
        {refreshFailed && <button className="secondary" disabled={operation || disabled} onClick={() => { setRefreshAttempt(value => value + 1); }}>Refresh completed profile</button>}
        {reviewDirty && <button className="secondary" disabled={locked} onClick={discardReview}>Discard review choices</button>}
      </div>
      {selected.warnings.length > 0 && <ul className="disclosure" aria-label="Import warnings">{selected.warnings.map((warning, index) => <li key={`${warning}-${index}`}>{warningLabels[warning] ?? warning.replaceAll('_', ' ')}</li>)}</ul>}
      <details className="import-provenance"><summary>Read complete normalized source text</summary>
        {selected.source_sha256 && <p>Normalized source SHA-256: <code>{selected.source_sha256}</code></p>}
        {selected.text !== null ? <pre className="import-source">{selected.text}</pre> : <p>Source text is not available yet.</p>}
      </details>
      {entries.length > 0 && <form onSubmit={submitReview}>
        <div className="import-review-heading"><h4>Review your profile entries</h4>
          <p>Check where each entry belongs and what will be saved. Keep, edit or exclude every entry; nothing is selected for saving until you review it.</p>
          <p className="import-review-progress" role="status">{readyCount} ready to save · {excludedCount} excluded · {unreviewedCount} need review or placement</p>
          <progress aria-label="Entries reviewed" max={entryCount} value={reviewedCount} />
        </div>
        <nav className="import-review-nav" aria-label="Entry navigation">
          <div className="actions">
            <button type="button" className="secondary" disabled={currentIndex === 0} onClick={() => setEntryIndex(currentIndex - 1)}>Previous entry</button>
            <span className="import-position" aria-live="polite">Entry {currentIndex + 1} of {entryCount}</span>
            <button type="button" className="secondary" disabled={currentIndex >= entryCount - 1} onClick={() => setEntryIndex(currentIndex + 1)}>Next entry</button>
            <button type="button" className="secondary" disabled={!unreviewedCount} onClick={nextUnreviewed}>Next entry needing review</button>
          </div>
          <label>Go to entry<select value={currentIndex} onChange={event => setEntryIndex(Number(event.target.value))}>
            {entries.map((entry, index) => {
              const draft = drafts[entry.entry_id] ?? initialDraft(entry);
              return <option key={entry.entry_id} value={index}>{snippet(draft.payload.text)} · {destinationLabel(draft)} · {draft.reviewed ? draft.action === 'skip' ? 'excluded' : 'kept' : 'needs review'}</option>;
            })}
          </select></label>
        </nav>
        {entries.slice(currentIndex, currentIndex + 1).map(entry => {
          const draft = drafts[entry.entry_id] ?? initialDraft(entry);
          const target = draft.action === 'replace' && draft.kind !== 'identity' ? replacement(current, draft) : undefined;
          const available = current.current_revision.claims.filter(claim => !claim.archived && claim.kind === draft.kind && claim.kind !== 'identity');
          const issues = entryProblems(entry, draft, current);
          if (duplicateEntryIds.has(entry.entry_id)) issues.push('More than one kept entry updates this same destination or replacement target. Exclude one or choose a different destination.');
          const visibleFields = entryFields(draft, target);
          const identityValue = draft.kind === 'identity' && draft.identity_field ? current.current_revision.snapshot[draft.identity_field] : '';
          return <fieldset className="import-entry" key={entry.entry_id} disabled={locked || !waiting}>
            <legend>{destinationLabel(draft)}</legend>
            <p className="import-entry-title">{snippet(draft.payload.text)}</p>
            <label>Save to<select value={destination(draft)} onChange={event => {
              const value = event.target.value;
              const identity = value === 'display_name' || value === 'headline';
              updateDraft(entry.entry_id, previous => ({
                ...previous, kind: identity ? 'identity' : value ? value as ClaimKind : null,
                identity_field: value === 'display_name' || value === 'headline' ? value : null, payload: identity ? { text: previous.payload.text } : { ...previous.payload },
                action: 'add', claim_id: '', choices: {}, reviewed: false, identityConfirmed: false,
              }));
            }}>{destinations.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
            {!destination(draft) && <p className="disclosure">Choose where this belongs, or exclude it. Unplaced text cannot be saved.</p>}
            {entry.issues.length > 0 && <p className="hint">Check this suggestion: {entry.issues.map(issue => issue.replaceAll('_', ' ')).join('; ')}.</p>}
            <section className="import-saved-preview" aria-label="Saved value preview">
              <h4>What will be saved</h4>
              <p>{draft.reviewed && draft.action === 'skip' ? 'Excluded — this entry will not change your profile.' : `${destinationLabel(draft)}${draft.kind === 'identity' ? identityValue ? ' · updates your current personal detail' : ' · adds your personal detail' : draft.action === 'replace' ? ' · replaces the selected existing entry' : ' · new profile entry'}`}</p>
              <dl>{visibleFields.filter(field => draft.payload[field.key]).map(field => <div key={field.key}><dt>{field.label}</dt><dd>{draft.payload[field.key]}</dd></div>)}</dl>
            </section>
            <details className="import-provenance">
              <summary>View original source and provenance</summary>
              {entry.sources.map(source => <div key={source.source_id}>
                <p>Exact source quote (Unicode span {source.start}–{source.end}, end exclusive):</p>
                <blockquote className="import-source">{source.quote}</blockquote>
                <small>Source part {source.source_id}</small>
              </div>)}
              <small>Source SHA-256 {selected.source_sha256 ?? 'Not available'}</small>
            </details>
            <div className="actions import-grouping">
              <button type="button" className="secondary" disabled={currentIndex === 0} onClick={() => regroup(currentIndex - 1, false)}>Combine with previous entry</button>
              <button type="button" className="secondary" disabled={currentIndex >= entryCount - 1} onClick={() => regroup(currentIndex, false)}>Combine with next entry</button>
              <button type="button" className="secondary" disabled={entry.sources.length < 2} onClick={() => regroup(currentIndex, true)}>Split into original source parts</button>
            </div>
            <p className="hint">Combining requires a fresh review. Splitting restores the original source text and clears edited fields for those parts. Exact source parts are always retained.</p>
            {draft.kind === 'identity' && <section className="comparison" aria-label="Personal detail comparison">
              <h4>{draft.identity_field === 'display_name' ? 'Full name' : 'Headline'} comparison</h4>
              <p>Current: {identityValue || 'Not supplied'}</p><p>Imported: {entry.payload.text}</p>
              <p>Will save: {draft.payload.text}</p>
              {identityValue && <label className="import-check"><input type="checkbox" checked={draft.identityConfirmed} onChange={event => updateDraft(entry.entry_id, previous => ({ ...previous, identityConfirmed: event.target.checked, reviewed: false }))} />Replace my current {draft.identity_field === 'display_name' ? 'full name' : 'headline'} with this value</label>}
            </section>}
            {draft.kind && draft.kind !== 'identity' && <label>Existing profile entry<select value={draft.action === 'replace' ? 'replace' : 'add'} onChange={event => updateDraft(entry.entry_id, previous => ({ ...previous, action: event.target.value as 'add' | 'replace', claim_id: '', choices: {}, reviewed: false }))}>
              <option value="add">Add a new entry; keep existing entries</option><option value="replace">Replace an existing entry in this category</option>
            </select></label>}
            {draft.action === 'replace' && draft.kind !== 'identity' && <><label>Replacement target<select value={draft.claim_id} onChange={event => updateDraft(entry.entry_id, previous => ({ ...previous, claim_id: event.target.value, choices: {}, reviewed: false }))}>
              <option value="">Choose an existing entry</option>
              {draft.claim_id && !target && <option value={draft.claim_id}>Previous target unavailable · {draft.claim_id}</option>}
              {available.map(claim => <option key={claim.id} value={claim.id}>{claim.revision.payload.text} · {claim.id}</option>)}
            </select></label><p className="hint">Compare against profile revision {current.current_revision.revision_no}. Choose current, imported, or your own edited value for each differing field.</p></>}
            {(draft.editing || target) && <div className="import-entry-fields">
              {draft.kind !== 'identity' && <p className="hint">Dates accept YYYY, YYYY-MM or YYYY-MM-DD. Leave unknown values empty; do not invent precision.</p>}
              {visibleFields.map(field => <div className={target ? 'comparison' : undefined} key={field.key}>
                {target && <><h4>{field.label} comparison</h4><p>Current: {target.revision.payload[field.key] || 'Not supplied'}</p><p>Imported: {entry.payload[field.key] || 'Not supplied'}</p>
                  {(target.revision.payload[field.key] ?? '') !== (entry.payload[field.key] ?? '') && <div className="actions">
                    <button type="button" className="secondary" aria-pressed={draft.choices[field.key] === 'keep'} onClick={() => chooseField(entry, draft, target, field.key, 'keep')}>Keep current {field.label.toLowerCase()}</button>
                    <button type="button" className="secondary" aria-pressed={draft.choices[field.key] === 'import'} onClick={() => chooseField(entry, draft, target, field.key, 'import')}>Use imported {field.label.toLowerCase()}</button>
                  </div>}
                  <p className="hint">Choice: {draft.choices[field.key] === 'keep' ? 'keep current' : draft.choices[field.key] === 'import' ? 'use imported' : draft.choices[field.key] === 'edit' ? 'your edited value' : (target.revision.payload[field.key] ?? '') === (entry.payload[field.key] ?? '') ? 'values match' : 'not yet chosen'}</p>
                </>}
                <label>{field.label}{field.key === 'text' ? <textarea maxLength={draft.kind === 'identity' ? draft.identity_field === 'display_name' ? 120 : 300 : field.limit} value={draft.payload[field.key] ?? ''} onChange={event => updateDraft(entry.entry_id, previous => ({ ...previous, payload: payloadValue(previous.payload, field.key, event.target.value), choices: { ...previous.choices, [field.key]: 'edit' }, identityConfirmed: false, reviewed: false }))} />
                  : <input maxLength={field.limit} value={draft.payload[field.key] ?? ''} onChange={event => updateDraft(entry.entry_id, previous => ({ ...previous, payload: payloadValue(previous.payload, field.key, event.target.value), choices: { ...previous.choices, [field.key]: 'edit' }, reviewed: false }))} />}</label>
              </div>)}
            </div>}
            {issues.length > 0 && !(draft.reviewed && draft.action === 'skip') && <ul className="import-entry-issues" aria-label="Entry needs attention">{issues.map(issue => <li key={issue}>{issue}</li>)}</ul>}
            <p className="import-decision-status" role="status">{draft.reviewed ? draft.action === 'skip' ? 'Reviewed · Excluded' : issues.length ? 'Needs review before saving' : 'Reviewed · Ready to save' : 'Not reviewed'}</p>
            <div className="actions import-entry-decisions">
              <button type="button" disabled={issues.length > 0} onClick={() => updateDraft(entry.entry_id, previous => ({ ...previous, action: previous.kind === 'identity' ? identityValue ? 'replace' : 'add' : previous.action === 'replace' ? 'replace' : 'add', reviewed: true, editing: false }))}>{draft.editing ? 'Keep changes' : 'Keep entry'}</button>
              <button type="button" className="secondary" onClick={() => updateDraft(entry.entry_id, previous => ({ ...previous, editing: true, reviewed: false, action: previous.action === 'skip' ? 'add' : previous.action }))}>Edit entry</button>
              <button type="button" className="secondary" onClick={() => updateDraft(entry.entry_id, previous => ({ ...previous, action: 'skip', reviewed: true, editing: false }))}>Exclude entry</button>
            </div>
          </fieldset>;
        })}
        {waiting && <fieldset className="import-approval" disabled={locked}>
          <legend>Save reviewed entries</legend>
          <label>Import mode<select value={mode} onChange={event => { setMode(event.target.value as ImportMergeRequest['mode']); setReplaceConfirmed(false); setAttested(false); setReviewDirty(true); }}><option value="merge">Merge only selected facts; keep unrelated profile facts</option><option value="replace_all">Replace all non-identity profile facts with selected facts</option></select></label>
          {mode === 'replace_all' && <div className="disclosure"><p>Replace all archives displaced active facts and sources. Unrelated personal details and immutable history are retained; reviewed personal details may change. Unrelated professional entries will leave the current profile. This is separate from ordinary merge.</p>
            <label><input type="checkbox" checked={replaceConfirmed} onChange={event => { setReplaceConfirmed(event.target.checked); setAttested(false); setReviewDirty(true); }} />I separately confirm replacing all non-identity facts and archiving displaced sources</label></div>}
          <p>{readyCount} of {entries.length} entries ready to save. Profile revision {current.current_revision.revision_no} is the exact save precondition.</p>
          {problems.length > 0 && <div className="import-remaining">
            <p>{unreviewedCount ? `${unreviewedCount} entries still need review or placement.` : 'Resolve the remaining comparisons before saving.'}</p>
            {unreviewedCount > 0 && <button type="button" className="secondary" onClick={nextUnreviewed}>Continue reviewing entries</button>}
            <details><summary>Remaining review steps ({problems.length})</summary><ul aria-label="Remaining review steps">{Array.from(new Set(problems)).map(problem => <li key={problem}>{problem}</li>)}</ul></details>
          </div>}
          {!readyCount && <p className="hint">Keep at least one entry. Excluded entries alone cannot change your profile.</p>}
          <label><input type="checkbox" checked={attested} disabled={problems.length > 0 || !readyCount || conflict || stale || (mode === 'replace_all' && !replaceConfirmed)} onChange={event => { setAttested(event.target.checked); setReviewDirty(true); }} />I reviewed every entry and confirm the kept values are accurate owner assertions, not independent verification</label>
          <button disabled={!attested || problems.length > 0 || !readyCount || conflict || stale || (mode === 'replace_all' && !replaceConfirmed)}>Save {readyCount} profile {readyCount === 1 ? 'entry' : 'entries'}</button>
        </fieldset>}
      </form>}
    </article>}
  </section>;
}
