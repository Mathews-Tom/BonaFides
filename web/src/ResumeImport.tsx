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
  { value: 'headline', label: 'Personal details · Highlight / tagline' }, { value: 'experience', label: 'Work experience' },
  { value: 'education', label: 'Education' }, { value: 'skill', label: 'Skills' }, { value: 'project', label: 'Projects' },
  { value: 'certification', label: 'Certifications' }, { value: 'language', label: 'Languages' },
  { value: 'preference', label: 'Preferences' }, { value: 'other', label: 'Additional information' },
];
function destination(draft: ReviewDraft): string { return draft.kind === 'identity' ? draft.identity_field ?? '' : draft.kind ?? ''; }
function destinationLabel(draft: ReviewDraft): string { return destinations.find(item => item.value === destination(draft))?.label ?? 'Needs placement'; }
function snippet(text: string): string { const line = text.replace(/\s+/g, ' ').trim(); return line.length > 72 ? `${line.slice(0, 69)}…` : line || 'Untitled entry'; }
function samePayload(left: ClaimPayload, right: ClaimPayload): boolean {
  return fields.every(field => (left[field.key] ?? null) === (right[field.key] ?? null));
}
function initialDraft(entry: ImportEntry, profile: Profile): ReviewDraft {
  const action = entry.comparison.status === 'unchanged' ? 'unchanged' : entry.comparison.status === 'conflict' ? 'replace' : 'add';
  const draft: ReviewDraft = { action, kind: entry.kind, identity_field: entry.identity_field, claim_id: entry.comparison.target_claim_id ?? '',
    payload: entry.kind === 'identity' ? { text: entry.payload.text } : { ...entry.payload }, reviewed: false, editing: false, identityConfirmed: false, choices: {} };
  const target = action === 'replace' ? replacement(profile, draft) : undefined;
  if (target) {
    for (const field of fields) {
      if (!entry.payload[field.key] && target.revision.payload[field.key]) {
        draft.payload = payloadValue(draft.payload, field.key, target.revision.payload[field.key]!);
        draft.choices[field.key] = 'keep';
      }
    }
  }
  return draft;
}
function initialDrafts(batch: ImportBatch, profile: Profile): Record<string, ReviewDraft> {
  return Object.fromEntries(batch.entries.map(entry => [entry.entry_id, initialDraft(entry, profile)]));
}
function isUnchanged(draft: ReviewDraft, profile: Profile): boolean {
  if (draft.kind === 'identity' && draft.identity_field) return draft.payload.text === profile.current_revision.snapshot[draft.identity_field];
  const target = replacement(profile, draft);
  return !!target && samePayload(draft.payload, target.revision.payload)
    && profile.current_revision.claims.filter(claim => !claim.archived && claim.kind === draft.kind && samePayload(claim.revision.payload, draft.payload)).length === 1;
}
function needsConfirmation(entry: ImportEntry, draft: ReviewDraft): boolean {
  return entry.comparison.status === 'needs_attention' && !draft.reviewed;
}
function previewPayload(draft: ReviewDraft, profile: Profile): ClaimPayload {
  if (draft.action === 'replace') {
    const target = replacement(profile, draft);
    if (target) {
      const payload = { ...target.revision.payload };
      for (const field of fields) if (draft.choices[field.key] || (draft.payload[field.key] ?? '') === (target.revision.payload[field.key] ?? '')) {
        const value = draft.payload[field.key];
        if (value === undefined) delete payload[field.key]; else payload[field.key] = value;
      }
      return payload;
    }
    if (draft.kind === 'identity' && draft.identity_field && !draft.identityConfirmed) return { text: profile.current_revision.snapshot[draft.identity_field] };
  }
  return draft.payload;
}
function entryFields(draft: ReviewDraft, target?: ClaimRecord) {
  const keys: PayloadField[] = draft.kind === 'identity' ? ['text'] : draft.kind === 'skill' || draft.kind === 'language' ? ['text', 'proficiency']
    : draft.kind === 'preference' || draft.kind === 'other' || !draft.kind ? ['text']
    : ['text', 'organization', 'role', 'start_date', 'end_date', ...(draft.kind === 'project' ? ['project_url' as const] : [])];
  return fields.filter(field => keys.includes(field.key) || !!draft.payload[field.key] || !!target?.revision.payload[field.key]).map(field => ({
    ...field, label: field.key === 'text' ? draft.kind === 'identity' ? draft.identity_field === 'display_name' ? 'Full name' : 'Highlight / tagline'
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
  return fields.filter(field => ((target.revision.payload[field.key] ?? '') !== (entry.payload[field.key] ?? '') || (target.revision.payload[field.key] ?? '') !== (draft.payload[field.key] ?? ''))
    && !!(entry.payload[field.key] || draft.payload[field.key]) && !draft.choices[field.key]).map(field => field.key);
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
    const isHeadline = draft.kind === 'identity' && field.key === 'text' && draft.identity_field === 'headline';
    const limit = draft.kind === 'identity' && field.key === 'text' ? draft.identity_field === 'display_name' ? 120 : 300 : field.limit;
    if ((isHeadline ? [...value].length : value.length) > limit) problems.push(`${isHeadline ? 'Highlight / tagline' : field.label} must be no longer than ${limit} characters.`);
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
      && draft.payload.text !== profile.current_revision.snapshot[draft.identity_field] && !draft.identityConfirmed) problems.push('Choose whether to keep or replace this personal detail.');
  if (draft.action === 'unchanged' && !isUnchanged(draft, profile)) problems.push('The saved value changed. Compare this entry with the latest profile.');
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
  const [comparisonBatch, setComparisonBatch] = useState<ImportBatch | null>(null);
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
  const stale = waiting && (current.version !== profile.version || selected.comparison_profile_version !== current.version);
  const selectedId = selected?.id;
  const selectedState = selected?.state;
  const entryCount = entries.length;
  const currentIndex = Math.min(entryIndex, Math.max(0, entryCount - 1));
  const targetEntries = new Map<string, string[]>();
  for (const entry of entries) {
    const draft = drafts[entry.entry_id];
    if (!draft || draft.action === 'skip') continue;
    const key = draft.kind === 'identity' ? `identity:${draft.identity_field}` : draft.action === 'replace' || draft.action === 'unchanged' ? `claim:${draft.claim_id}` : '';
    if (key) targetEntries.set(key, [...(targetEntries.get(key) ?? []), entry.entry_id]);
  }
  const duplicateEntryIds = new Set([...targetEntries.values()].filter(ids => ids.length > 1
    && ids.some(id => drafts[id]?.kind === 'identity' || drafts[id]?.action !== 'unchanged')).flat());
  const excludedCount = entries.filter(entry => drafts[entry.entry_id]?.action === 'skip').length;
  const readyCount = entries.filter(entry => {
    const draft = drafts[entry.entry_id];
    return !!draft && draft.action !== 'skip' && !needsConfirmation(entry, draft) && !duplicateEntryIds.has(entry.entry_id) && !entryProblems(entry, draft, current).length;
  }).length;
  const unreviewedCount = entryCount - excludedCount - readyCount;
  const unchangedCount = entries.filter(entry => drafts[entry.entry_id]?.action === 'unchanged').length;
  const noProfileChanges = mode === 'merge' && readyCount > 0 && readyCount === unchangedCount;
  const stage = selected?.state === 'succeeded' ? 3 : selected?.text != null ? 2 : selected || acceptedUpload ? 1 : 0;
  const historyId = batches?.some(batch => batch.id === savedId) ? savedId : batches?.[0]?.id ?? '';

  function nextUnreviewed() {
    if (!selected) return;
    for (let offset = 1; offset <= entryCount; offset += 1) {
      const index = (currentIndex + offset) % entryCount;
      const entry = entries[index]!;
      const draft = drafts[entry.entry_id];
      if (draft && draft.action !== 'skip' && (needsConfirmation(entry, draft) || duplicateEntryIds.has(entry.entry_id) || entryProblems(entry, draft, current).length)) { focusEntry(index); return; }
    }
  }
  function focusEntry(index: number) {
    setEntryIndex(index);
    const entry = entries[index];
    if (entry) setDrafts(existing => ({ ...existing, [entry.entry_id]: { ...(existing[entry.entry_id] ?? initialDraft(entry, current)), editing: true } }));
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
    setDrafts(existing => changed || !Object.keys(existing).length ? initialDrafts(batch, current) : { ...initialDrafts(batch, current), ...existing });
    if (terminal(batch.state)) setMergeAwaiting(false);
  }, [current]);

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
        setDrafts(initialDrafts(batch, profile)); setReviewProfile(profile); setComparison(null);
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
      if (!draft) return existing;
      const next = update(draft);
      if (next.action !== 'skip' && next.kind === 'identity' && next.identity_field) {
        const saved = current.current_revision.snapshot[next.identity_field];
        next.action = next.payload.text === saved ? 'unchanged' : saved ? 'replace' : 'add';
      } else if (next.action === 'unchanged' && !isUnchanged(next, current)) next.action = replacement(current, next) ? 'replace' : 'add';
      return { ...existing, [id]: next };
    });
    setReviewDirty(true); setAttested(false); setError('');
  }
  function chooseField(entry: ImportEntry, draft: ReviewDraft, target: ClaimRecord, key: PayloadField, choice: 'keep' | 'import') {
    const value = (choice === 'keep' ? target.revision.payload[key] : entry.payload[key]) ?? '';
    updateDraft(entry.entry_id, () => ({ ...draft, payload: payloadValue(draft.payload, key, value), choices: { ...draft.choices, [key]: choice } }));
  }
  function regroup(index: number, split: boolean) {
    const first = entries[index];
    if (!first || locked || !waiting) return;
    let grouped: ImportEntry[];
    let removeCount = 1;
    if (split) {
      if (first.sources.length < 2) return;
      const draft = drafts[first.entry_id] ?? initialDraft(first, current);
      grouped = first.sources.map(source => ({
        entry_id: crypto.randomUUID(), kind: draft.kind === 'identity' ? null : draft.kind, identity_field: null,
        payload: { text: source.quote }, sources: [source], issues: [], comparison: { status: 'needs_attention', target_claim_id: null, candidate_claim_ids: [] },
      }));
    } else {
      const second = entries[index + 1];
      if (!second) return;
      const a = drafts[first.entry_id] ?? initialDraft(first, current);
      const b = drafts[second.entry_id] ?? initialDraft(second, current);
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
        sources: [...first.sources, ...second.sources], issues: [], comparison: { status: 'needs_attention', target_claim_id: null, candidate_claim_ids: [] },
      }];
      removeCount = 2;
    }
    setEntries(existing => [...existing.slice(0, index), ...grouped, ...existing.slice(index + removeCount)]);
    setDrafts(existing => {
      const next = { ...existing };
      for (const removed of entries.slice(index, index + removeCount)) delete next[removed.entry_id];
      for (const entry of grouped) next[entry.entry_id] = initialDraft(entry, current);
      return next;
    });
    setEntryIndex(index); setReviewDirty(true); setAttested(false); setError('');
  }
  function discardReview() {
    if (!selected) return;
    setDrafts(initialDrafts(selected, profile)); setReviewDirty(false); setAttested(false); setMode('merge'); setReplaceConfirmed(false);
    setEntries(selected.entries); setEntryIndex(0);
    setComparison(null); setReviewProfile(profile); setConflict(false); setError(''); mergeRetry.current = null;
  }
  async function loadComparison() {
    if (!selected) return;
    await perform(async () => {
      const [latest, batch] = await Promise.all([api.request<Profile>('/profile'), api.request<ImportBatch>(`/imports/${encodeURIComponent(selected.id)}`)]);
      if (batch.comparison_profile_version !== latest.version) { setError('The profile changed during comparison. Load the current profile again.'); return; }
      setComparison(latest); setComparisonBatch(batch);
    });
  }
  function rebase() {
    if (!comparison || !comparisonBatch || comparisonBatch.comparison_profile_version !== comparison.version) return;
    const latest = comparison;
    setDrafts(existing => Object.fromEntries(entries.map(entry => {
      const draft = existing[entry.entry_id] ?? initialDraft(entry, current);
      if (draft.action === 'skip') return [entry.entry_id, draft];
      const fresh = comparisonBatch.entries.find(item => item.entry_id === entry.entry_id);
      if (draft.kind === 'identity' && draft.identity_field) {
        const saved = latest.current_revision.snapshot[draft.identity_field];
        const changed = current.current_revision.snapshot[draft.identity_field] !== saved;
        return [entry.entry_id, { ...draft, action: draft.payload.text === saved ? 'unchanged' : saved ? 'replace' : 'add', reviewed: changed ? false : draft.reviewed, identityConfirmed: changed ? false : draft.identityConfirmed }];
      }
      if (draft.action === 'add' && entry.comparison.status === 'new' && fresh && draft.kind === entry.kind && fresh.comparison.status !== 'new') {
        const suggested = initialDraft(fresh, latest);
        if (samePayload(draft.payload, entry.payload)) return [entry.entry_id, { ...suggested, editing: draft.editing }];
        return [entry.entry_id, { ...draft, action: fresh.comparison.status === 'conflict' ? 'replace' : 'add', claim_id: fresh.comparison.target_claim_id ?? '', choices: {}, reviewed: false }];
      }
      if (draft.action !== 'replace' && draft.action !== 'unchanged') return [entry.entry_id, draft];
      const before = replacement(current, draft);
      const after = replacement(latest, draft);
      const changed = fields.filter(field => (before?.revision.payload[field.key] ?? '') !== (after?.revision.payload[field.key] ?? ''));
      const choices = { ...draft.choices };
      const payload = { ...draft.payload };
      for (const field of changed) {
        delete choices[field.key];
        if (!entry.payload[field.key] && draft.choices[field.key] === 'keep' && after?.revision.payload[field.key]) {
          payload[field.key] = after.revision.payload[field.key]!;
          choices[field.key] = 'keep';
        }
      }
      const action = draft.action === 'unchanged' && !isUnchanged(draft, latest) ? 'replace' : draft.action;
      return [entry.entry_id, { ...draft, action, payload, choices, reviewed: changed.length || !after ? false : draft.reviewed }];
    })));
    setSelected(comparisonBatch);
    setEntries(existing => existing.map(entry => {
      const fresh = comparisonBatch.entries.find(item => item.entry_id === entry.entry_id);
      return fresh ? { ...entry, comparison: fresh.comparison } : entry;
    }));
    setReviewProfile(latest); onProfile(latest); setComparison(null); setConflict(false); setAttested(false); setReplaceConfirmed(false);
    setNotice('Latest profile selected. Your actions and edited text are retained. Review any changed replacement fields and approve again.');
  }
  const problems: string[] = [];
  let acceptedCount = 0;
  const targets = new Map<string, ImportDecision['action']>();
  const identityTargets = new Set<string>();
  if (selected && waiting) {
    for (const entry of entries) {
      const draft = drafts[entry.entry_id];
      const label = snippet(draft?.payload.text ?? entry.payload.text);
      if (draft && draft.action !== 'skip' && needsConfirmation(entry, draft)) problems.push(`Confirm the extraction or exclude “${label}”.`);
      if (!draft || draft.action === 'skip') continue;
      acceptedCount += 1;
      problems.push(...entryProblems(entry, draft, current).map(problem => `${label}: ${problem}`));
      if (draft.kind === 'identity' && draft.identity_field) {
        if (identityTargets.has(draft.identity_field)) problems.push('Keep only one entry for each personal detail.');
        identityTargets.add(draft.identity_field);
      }
      if ((draft.action === 'replace' || draft.action === 'unchanged') && draft.kind !== 'identity') {
        if (targets.has(draft.claim_id) && (draft.action !== 'unchanged' || targets.get(draft.claim_id) !== 'unchanged')) problems.push('Each existing entry can be updated only once; an update cannot also retain it unchanged.');
        targets.set(draft.claim_id, draft.action);
      }
    }
  }
  function submitReview(event: FormEvent) {
    event.preventDefault();
    if (!selected || !waiting || locked || conflict || stale || !attested || problems.length || !acceptedCount || (mode === 'replace_all' && !replaceConfirmed)) return;
    const body: ImportMergeRequest = {
      mode, replace_all_confirmed: mode === 'replace_all' && replaceConfirmed,
      decisions: entries.map(entry => {
        const draft = drafts[entry.entry_id] ?? initialDraft(entry, current);
        return { source_ids: entry.sources.map(source => source.source_id), action: draft.action, kind: draft.kind, identity_field: draft.identity_field, payload: draft.payload,
          ...((draft.action === 'replace' || draft.action === 'unchanged') && draft.kind !== 'identity' ? { claim_id: draft.claim_id } : {}) };
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

  function renderEntryEditor(entry: ImportEntry) {
          const draft = drafts[entry.entry_id] ?? initialDraft(entry, current);
          if (!draft.editing) return null;
          const target = draft.action === 'replace' && draft.kind !== 'identity' ? replacement(current, draft) : undefined;
          const available = current.current_revision.claims.filter(claim => !claim.archived && claim.kind === draft.kind && claim.kind !== 'identity');
          const issues = entryProblems(entry, draft, current);
          if (duplicateEntryIds.has(entry.entry_id)) issues.push('More than one kept entry updates this same destination or replacement target. Exclude one or choose a different destination.');
          const visibleFields = entryFields(draft, target);
          const identityValue = draft.kind === 'identity' && draft.identity_field ? current.current_revision.snapshot[draft.identity_field] : '';
          return <fieldset className="import-entry" key={entry.entry_id} disabled={locked || !waiting}>
            <legend>Edit entry {currentIndex + 1} · {destinationLabel(draft)}</legend>
        {drafts[entries[currentIndex]!.entry_id]?.editing && <nav className="import-review-nav" aria-label="Entry editor navigation">
          <div className="actions">
            <button type="button" className="secondary" disabled={currentIndex === 0} onClick={() => focusEntry(currentIndex - 1)}>Previous entry</button>
            <span className="import-position" aria-live="polite">Entry {currentIndex + 1} of {entryCount}</span>
            <button type="button" className="secondary" disabled={currentIndex >= entryCount - 1} onClick={() => focusEntry(currentIndex + 1)}>Next entry</button>
            <button type="button" className="secondary" disabled={!unreviewedCount} onClick={nextUnreviewed}>Next entry needing attention</button>
          </div>
          <label>Go to entry<select value={currentIndex} onChange={event => focusEntry(Number(event.target.value))}>
            {entries.map((entry, index) => <option key={entry.entry_id} value={index}>{snippet(drafts[entry.entry_id]?.payload.text ?? entry.payload.text)} · {destinationLabel(drafts[entry.entry_id] ?? initialDraft(entry, current))}</option>)}
          </select></label>
        </nav>}
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
            <p className="hint">Edits appear in the complete preview immediately. Original source quotes below never change.</p>
            <details className="import-provenance">
              <summary>View original source and provenance</summary>
              {entry.sources.map(source => <div key={source.source_id}>
                <p>Exact source quote (Unicode span {source.start}–{source.end}, end exclusive):</p>
                <blockquote className="import-source">{source.quote}</blockquote>
                <small>Source part {source.source_id}</small>
              </div>)}
              <small>Source SHA-256 {selected?.source_sha256 ?? 'Not available'}</small>
            </details>
            <div className="actions import-grouping">
              <button type="button" className="secondary" disabled={currentIndex === 0} onClick={() => regroup(currentIndex - 1, false)}>Combine with previous entry</button>
              <button type="button" className="secondary" disabled={currentIndex >= entryCount - 1} onClick={() => regroup(currentIndex, false)}>Combine with next entry</button>
              <button type="button" className="secondary" disabled={entry.sources.length < 2} onClick={() => regroup(currentIndex, true)}>Split into original source parts</button>
            </div>
            <p className="hint">Combining requires a fresh review. Splitting restores the original source text and clears edited fields for those parts. Exact source parts are always retained.</p>
            {draft.kind === 'identity' && <section className="comparison" aria-label="Personal detail comparison">
              <h4>{draft.identity_field === 'display_name' ? 'Full name' : 'Highlight / tagline'} comparison</h4>
              <p>Current: {identityValue || 'Not supplied'}</p><p>Imported: {entry.payload.text}</p>
              <p>Will save: {draft.payload.text}</p>
              {identityValue && identityValue !== draft.payload.text && <><button type="button" className="secondary" onClick={() => updateDraft(entry.entry_id, previous => ({ ...previous, payload: { text: identityValue }, action: 'unchanged', identityConfirmed: false }))}>Keep current personal detail</button>
                <label className="import-check"><input type="checkbox" checked={draft.identityConfirmed} onChange={event => updateDraft(entry.entry_id, previous => ({ ...previous, identityConfirmed: event.target.checked, reviewed: false }))} />Replace my current {draft.identity_field === 'display_name' ? 'full name' : 'highlight / tagline'} with this value</label></>}
            </section>}
            {draft.kind && draft.kind !== 'identity' && <label>Existing profile entry<select value={draft.action === 'skip' ? 'add' : draft.action} onChange={event => {
              const action = event.target.value as ReviewDraft['action'];
              updateDraft(entry.entry_id, previous => ({ ...previous, action, claim_id: action === 'replace' ? previous.claim_id : '', choices: {}, reviewed: false }));
            }}>
              {draft.action === 'unchanged' && <option value="unchanged">Keep unchanged; no new claim or source evidence</option>}
              <option value="add">Add a new entry; keep existing entries</option><option value="replace">Replace an existing entry / attach this source evidence</option>
            </select></label>}
            {draft.action === 'unchanged' && <p className="hint">This exact saved entry is retained without new evidence. To explicitly attach the uploaded source, choose “Replace an existing entry / attach this source evidence”.</p>}
            {draft.action === 'replace' && draft.kind !== 'identity' && <><label>Replacement target<select value={draft.claim_id} onChange={event => {
              const targetId = event.target.value;
              const selectedTarget = available.find(claim => claim.id === targetId);
              updateDraft(entry.entry_id, previous => {
                const payload = { ...previous.payload };
                const choices: ReviewDraft['choices'] = {};
                for (const field of fields) {
                  if (previous.choices[field.key] === 'edit') choices[field.key] = 'edit';
                  else if (!entry.payload[field.key] && selectedTarget?.revision.payload[field.key]) {
                    payload[field.key] = selectedTarget.revision.payload[field.key]!;
                    choices[field.key] = 'keep';
                  }
                }
                return { ...previous, claim_id: targetId, payload, choices, reviewed: false };
              });
            }}>
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
                    <button type="button" className="secondary" aria-pressed={draft.choices[field.key] === 'import'} onClick={() => chooseField(entry, draft, target, field.key, 'import')}>{entry.payload[field.key] ? 'Use imported' : 'Explicitly clear'} {field.label.toLowerCase()}</button>
                  </div>}
                  <p className="hint">Choice: {draft.choices[field.key] === 'keep' ? 'keep current' : draft.choices[field.key] === 'import' ? 'use imported' : draft.choices[field.key] === 'edit' ? 'your edited value' : (target.revision.payload[field.key] ?? '') === (entry.payload[field.key] ?? '') ? 'values match' : 'not yet chosen'}</p>
                </>}
                <label>{field.label}{field.key === 'text' ? <textarea maxLength={draft.kind === 'identity' ? draft.identity_field === 'display_name' ? 120 : undefined : field.limit} value={draft.payload[field.key] ?? ''} onChange={event => updateDraft(entry.entry_id, previous => ({ ...previous, payload: payloadValue(previous.payload, field.key, event.target.value), choices: { ...previous.choices, [field.key]: 'edit' }, identityConfirmed: false }))} />
                  : <input maxLength={field.limit} value={draft.payload[field.key] ?? ''} onChange={event => updateDraft(entry.entry_id, previous => ({ ...previous, payload: payloadValue(previous.payload, field.key, event.target.value), choices: { ...previous.choices, [field.key]: 'edit' } }))} />}</label>
              </div>)}
            </div>}
            {issues.length > 0 && draft.action !== 'skip' && <ul className="import-entry-issues" aria-label="Entry needs attention">{issues.map(issue => <li key={issue}>{issue}</li>)}</ul>}
            <p className="import-decision-status" role="status">{draft.action === 'skip' ? 'Excluded' : issues.length || needsConfirmation(entry, draft) ? 'Needs attention before saving' : draft.action === 'unchanged' ? 'Unchanged · no profile changes' : 'Ready for batch approval'}</p>
            <div className="actions import-entry-decisions">
              {needsConfirmation(entry, draft) && draft.action !== 'skip' && <button type="button" disabled={issues.length > 0} onClick={() => updateDraft(entry.entry_id, previous => ({ ...previous, reviewed: true }))}>Confirm extraction</button>}
              <button type="button" className="secondary" onClick={() => setDrafts(existing => ({ ...existing, [entry.entry_id]: { ...draft, editing: false } }))}>Close entry editor</button>
              <button type="button" className="secondary" onClick={() => updateDraft(entry.entry_id, previous => ({ ...previous, action: 'skip', reviewed: false, editing: false }))}>Exclude entry</button>
            </div>
          </fieldset>;
  }
  return <section className="panel resume-import" aria-labelledby="resume-import-title">
    <div className="import-intro"><h2 id="resume-import-title">Import a résumé</h2>
      <p>Preview your complete profile, make any corrections, then save the assembled result in one batch. Nothing changes before your approval.</p>
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
      <p className="profile-description-preview">Description: {comparison.current_revision.snapshot.description || 'Not supplied'}</p>
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
        <div className="import-review-heading"><h4>Complete profile preview</h4>
          <p>New and unchanged entries are ready without individual approval. Edit or exclude any entry; resolve highlighted comparisons before saving. Unresolved fields show the current saved value until you choose.</p>
          <p className="import-review-progress" role="status">{readyCount} ready · {unchangedCount} unchanged · {excludedCount} excluded · {unreviewedCount} need attention</p>
        </div>
        <section className="import-document" aria-label="Assembled profile preview">
          {[{ value: 'identity', label: 'Personal details' }, ...destinations.filter(item => item.value && item.value !== 'display_name' && item.value !== 'headline'), { value: '', label: 'Needs placement' }].map(section => {
            const sectionEntries = entries.filter(entry => (drafts[entry.entry_id]?.kind ?? '') === section.value);
            const retained = mode === 'merge' ? current.current_revision.claims.filter(claim => !claim.archived && claim.kind === section.value
              && !entries.some(entry => { const draft = drafts[entry.entry_id]; return draft && draft.action !== 'skip' && (draft.action === 'replace' || draft.action === 'unchanged') && draft.claim_id === claim.id; })) : [];
            const retainedIdentity = section.value === 'identity' ? (['display_name', 'headline', 'description'] as const).filter(key => current.current_revision.snapshot[key]
              && !entries.some(entry => drafts[entry.entry_id]?.kind === 'identity' && drafts[entry.entry_id]?.identity_field === key && drafts[entry.entry_id]?.action !== 'skip')) : [];
            if (!sectionEntries.length && !retained.length && !retainedIdentity.length) return null;
            return <section className={`import-document-section${section.value === 'skill' || section.value === 'language' ? ' import-token-section' : ''}`} aria-label={section.label} key={section.value}>
              <h5>{section.label}</h5>
              {retainedIdentity.map(key => <div className="import-document-entry" key={key}><dl><dt>{key === 'display_name' ? 'Full name' : key === 'headline' ? 'Highlight / tagline' : 'Description'}</dt><dd className={key === 'description' ? 'profile-description-preview' : undefined}>{current.current_revision.snapshot[key]}</dd></dl><p className="hint">Current value retained</p></div>)}
              {retained.map(claim => <div className="import-document-entry" key={claim.id}><dl>{fields.filter(field => claim.revision.payload[field.key]).map(field => <div key={field.key}><dt>{field.label}</dt><dd>{claim.revision.payload[field.key]}</dd></div>)}</dl><p className="hint">Current entry retained</p></div>)}
              {sectionEntries.map(entry => {
                const draft = drafts[entry.entry_id] ?? initialDraft(entry, current);
                const payload = previewPayload(draft, current);
                const issues = entryProblems(entry, draft, current);
                const attention = needsConfirmation(entry, draft) || issues.length > 0 || duplicateEntryIds.has(entry.entry_id);
                const status = draft.action === 'skip' ? 'Excluded' : attention ? 'Needs attention' : draft.action === 'unchanged' ? 'Unchanged' : draft.action === 'replace' ? 'Ready to update' : 'New entry';
                const index = entries.indexOf(entry);
                const compact = (section.value === 'skill' || section.value === 'language') && !attention && draft.action !== 'skip' && !draft.editing;
                return <article className={`import-document-entry${compact ? ' is-compact' : ''}${attention && draft.action !== 'skip' ? ' needs-attention' : ''}${currentIndex === index && draft.editing ? ' is-editing' : ''}`} aria-label={`Preview entry ${index + 1}`} key={entry.entry_id}>
                  {compact ? <button type="button" className="secondary import-token-button" disabled={locked || !waiting} onClick={() => focusEntry(index)} aria-label={`Edit preview entry ${index + 1}`} aria-description={status}>
                    {payload.text}{payload.proficiency && <small>{payload.proficiency}</small>}
                  </button> : <>
                  {draft.action === 'skip' ? <p>{snippet(draft.payload.text)} — not included in the saved profile.</p>
                    : <dl>{entryFields(draft, replacement(current, draft)).filter(field => payload[field.key]).map(field => <div className={field.key === 'text' && draft.kind !== 'identity' ? 'import-primary-field' : undefined} key={field.key}><dt>{field.label}</dt><dd>{payload[field.key]}</dd></div>)}</dl>}
                  {attention && draft.action !== 'skip' && <p className="hint">{issues[0] ?? 'Check the extraction, choose a target if needed, then confirm or exclude this entry.'}</p>}
                  <div className="import-preview-actions">
                    <span className="import-entry-status">{status}{draft.action === 'replace' && issues.length ? ' · current values retained until resolved' : ''}</span>
                    <button type="button" className="secondary" disabled={locked || !waiting} onClick={() => focusEntry(index)} aria-label={`Edit preview entry ${index + 1}`}>Edit</button>
                    {draft.action === 'skip' && <button type="button" className="secondary" disabled={locked || !waiting} onClick={() => updateDraft(entry.entry_id, previous => ({ ...previous, action: initialDraft(entry, current).action, reviewed: false }))} aria-label={`Include preview entry ${index + 1}`}>Include</button>}
                  </div>
                  </>}
                  {currentIndex === index && renderEntryEditor(entry)}
                </article>;
              })}
            </section>;
          })}
        </section>
        {waiting && <fieldset className="import-approval" disabled={locked}>
          <legend>Approve assembled profile</legend>
          <label>Import mode<select value={mode} onChange={event => { setMode(event.target.value as ImportMergeRequest['mode']); setReplaceConfirmed(false); setAttested(false); setReviewDirty(true); }}><option value="merge">Merge only selected facts; keep unrelated profile facts</option><option value="replace_all">Replace all non-identity profile facts with selected facts</option></select></label>
          {mode === 'replace_all' && <div className="disclosure"><p>Replace all archives displaced active facts and sources. Unrelated personal details and immutable history are retained; reviewed personal details may change. Unrelated professional entries will leave the current profile. This is separate from ordinary merge.</p>
            <label><input type="checkbox" checked={replaceConfirmed} onChange={event => { setReplaceConfirmed(event.target.checked); setAttested(false); setReviewDirty(true); }} />I separately confirm replacing all non-identity facts and archiving displaced sources</label></div>}
          <p>{readyCount} of {entries.length} entries ready to save. Profile revision {current.current_revision.revision_no} is the exact save precondition.</p>
          {noProfileChanges && <p className="hint">All included entries are unchanged. Completing this import keeps the existing profile revision and source evidence; the original upload remains in import history.</p>}
          {problems.length > 0 && <div className="import-remaining">
            <p>{unreviewedCount ? `${unreviewedCount} entries still need review or placement.` : 'Resolve the remaining comparisons before saving.'}</p>
            {unreviewedCount > 0 && <button type="button" className="secondary" onClick={nextUnreviewed}>Continue reviewing entries</button>}
            <details><summary>Remaining review steps ({problems.length})</summary><ul aria-label="Remaining review steps">{Array.from(new Set(problems)).map(problem => <li key={problem}>{problem}</li>)}</ul></details>
          </div>}
          {!readyCount && <p className="hint">Include at least one valid entry. An all-excluded batch cannot be completed.</p>}
          <label><input type="checkbox" checked={attested} disabled={problems.length > 0 || !readyCount || conflict || stale || (mode === 'replace_all' && !replaceConfirmed)} onChange={event => { setAttested(event.target.checked); setReviewDirty(true); }} />I approve this assembled profile and confirm its values are accurate owner assertions, not independent verification</label>
          <button disabled={!attested || problems.length > 0 || !readyCount || conflict || stale || (mode === 'replace_all' && !replaceConfirmed)}>{noProfileChanges ? 'Complete import without profile changes' : 'Save profile'}</button>
        </fieldset>}
      </form>}
    </article>}
  </section>;
}
