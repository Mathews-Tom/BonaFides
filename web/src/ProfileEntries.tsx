// SPDX-License-Identifier: MIT OR Apache-2.0
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Api, errorText, literalClaimPayload, type ClaimKind, type ClaimPayload, type ClaimPeriodSeparator, type ClaimPeriodStyle, type ClaimRateWording, type ClaimRecord, type ClaimRendering, type ClaimVocabulary, type StructuredClaim, type EvidenceLink, type EvidencePrivacyClass, type EvidenceRecord, type EvidenceRevision, type EvidenceSourceType, evidencePrivacyClasses, evidenceSourceTypes, type PinnedClaim, type Profile, type ProfileRevision, type ProfileRevisionSummary, type RevisionPage } from './api';
import './ProfileEntries.css';

const kinds: { value: ClaimKind; label: string }[] = [
  { value: 'skill', label: 'Skills' }, { value: 'experience', label: 'Work experience' },
  { value: 'project', label: 'Projects' }, { value: 'education', label: 'Education' },
  { value: 'language', label: 'Languages' }, { value: 'certification', label: 'Certifications' },
  { value: 'identity', label: 'Identity facts' }, { value: 'preference', label: 'Preferences' },
  { value: 'other', label: 'Other facts' },
];
interface StructuredDraft { verb: string; subject: string; scope: string; value: string; unit: string; magnitude: string; change: string; per: string; rate_wording: ClaimRateWording | ''; measure: string; start: string; end: string; style: ClaimPeriodStyle | ''; separator: ClaimPeriodSeparator | '' }
interface ClaimDraft { id?: string; kind: ClaimKind; payload: ClaimPayload; structured: StructuredDraft | null; legacyText: string; evidence_links: EvidenceLink[]; pinnedEvidence: EvidenceRevision[] }
const structuredKinds: ReadonlySet<ClaimKind> = new Set(['experience', 'project']);
const emptyStructured: StructuredDraft = { verb: 'built', subject: '', scope: '', value: '', unit: 'percent', magnitude: '', change: '', per: '', rate_wording: '', measure: '', start: '', end: '', style: '', separator: '' };
const rateWordingLabels: Record<ClaimRateWording, string> = {
  per: 'Per', a: 'A', an: 'An', each: 'Each', every: 'Every',
  adverbial: 'Adverb (daily, weekly, monthly, quarterly, yearly)', annual: 'Annually (year only)',
};
const periodStyleLabels: Record<ClaimPeriodStyle, string> = {
  parenthetical: 'Parenthetical (2021–2022)', in_year: 'In year (in 2022)', range: 'Range (2021–2022, without parentheses)',
};
const periodSeparatorLabels: Record<ClaimPeriodSeparator, string> = {
  hyphen: 'Hyphen (-)', en_dash: 'En dash (–)', em_dash: 'Em dash (—)',
};

function isSingleYear(draft: StructuredDraft): boolean {
  return /^\d{4}$/.test(draft.start) && draft.start === draft.end;
}

function wordingErrorOf(draft: StructuredDraft): string {
  if (draft.value && draft.rate_wording) {
    if (!draft.per) return 'Choose a metric rate for the selected rate wording.';
    if (draft.rate_wording === 'annual' && draft.per !== 'year') return 'Annually requires the year metric rate.';
  }
  if ((draft.start || draft.end) && draft.style === 'in_year' && !isSingleYear(draft)) return 'In year wording requires matching year-only start and end dates.';
  return '';
}

function draftOf(claim: StructuredClaim): StructuredDraft {
  const metric = claim.metric;
  return { verb: claim.verb, subject: claim.subject, scope: claim.scope ?? '', value: metric?.value ?? '', unit: metric?.unit ?? 'percent',
    magnitude: metric?.magnitude ?? '', change: metric?.change ?? '', per: metric?.per ?? '', rate_wording: metric?.rate_wording ?? '', measure: metric?.measure ?? '',
    start: claim.period?.start ?? '', end: claim.period?.end ?? '', style: claim.period?.style ?? '', separator: claim.period?.separator ?? '' };
}

/** Omitted optional parts stay absent; nothing is inferred to fill them. */
function claimOf(draft: StructuredDraft): StructuredClaim {
  const claim: StructuredClaim = { schema_version: 1, verb: draft.verb, subject: draft.subject };
  if (draft.scope) claim.scope = draft.scope;
  if (draft.value) {
    claim.metric = { value: draft.value, unit: draft.unit };
    if (draft.magnitude) claim.metric.magnitude = draft.magnitude;
    if (draft.change) claim.metric.change = draft.change;
    if (draft.per) claim.metric.per = draft.per;
    if (draft.rate_wording) claim.metric.rate_wording = draft.rate_wording;
    if (draft.measure) claim.metric.measure = draft.measure;
  }
  if (draft.start && draft.end) {
    claim.period = { start: draft.start, end: draft.end };
    if (draft.style) claim.period.style = draft.style;
    if (draft.separator) claim.period.separator = draft.separator;
  }
  return claim;
}

function contextOf(payload: ClaimPayload): Omit<ClaimPayload, 'text'> {
  const context: Partial<ClaimPayload> = { ...payload };
  delete context.text;
  return context;
}
interface EvidenceDraft { id?: string; text: string; source: string; attestation: boolean; source_type: EvidenceSourceType | ''; privacy_class: EvidencePrivacyClass | ''; employer_owned: boolean }
interface ArchiveChoice { kind: 'claim' | 'evidence'; id: string; text: string }

function ClaimWords({ claim }: { claim: PinnedClaim }) {
  const payload = claim.revision.payload;
  return <><p>{claim.rendering}</p>{claim.needs_attention && <p className="hint">Needs attention: this statement is still free text. Edit the entry to restructure it as a claim.</p>}{(payload.organization || payload.role) && <p>{[payload.role, payload.organization].filter(Boolean).join(' · ')}</p>}
    {(payload.start_date || payload.end_date) && <p>{payload.start_date || 'Start not supplied'} — {payload.end_date || 'End not supplied'}</p>}
    {payload.proficiency && <p>Proficiency: {payload.proficiency}</p>}
    {payload.project_url && <p>Project reference (not fetched): <code>{payload.project_url}</code></p>}
    <details><summary>Evidence and authorship · revision {claim.revision.revision_no}</summary>
      <p>Owner assertion by {claim.revision.author_owner_id} · {claim.revision.created_at}. Not independently verified.</p>
      <p>Claim revision {claim.revision.id} · SHA-256 {claim.revision.content_sha256}</p>
      {claim.revision.evidence_links.map(link => <blockquote key={link.evidence_revision_id}><p>{link.excerpt}</p>
        <p>{link.support_kind.replaceAll('_', ' ')} · {link.evidence.payload.source}</p><p>{link.evidence.payload.text}</p>
        <small>Evidence revision {link.evidence_revision_id} · {link.evidence.created_at} · owner {link.evidence.author_owner_id} · SHA-256 {link.evidence.content_sha256}</small></blockquote>)}
    </details></>;
}

export function ProfileEntries({ api, profile, disabled, onProfile, onDirtyChange, onBusyChange }: {
  api: Api; profile: Profile; disabled: boolean; onProfile: (value: Profile) => void;
  onDirtyChange: (value: boolean) => void; onBusyChange: (value: boolean) => void;
}) {
  const [claim, setClaim] = useState<ClaimDraft | null>(null);
  const [section, setSection] = useState<ClaimKind | 'sources' | 'history'>('skill');
  const [evidence, setEvidence] = useState<EvidenceDraft | null>(null);
  const [archive, setArchive] = useState<ArchiveChoice | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [comparison, setComparison] = useState<Profile | null>(null);
  const [history, setHistory] = useState<ProfileRevisionSummary[] | null>(null);
  const [cursor, setCursor] = useState<number | null>(null);
  const [preview, setPreview] = useState<ProfileRevision | null>(null);
  const [restoreConfirmed, setRestoreConfirmed] = useState(false);
  const mutation = useRef<{ fingerprint: string; key: string } | null>(null);
  const errorRef = useRef<HTMLDivElement>(null);
  const claimInput = useRef<HTMLTextAreaElement>(null);
  const subjectInput = useRef<HTMLInputElement>(null);
  const [vocabulary, setVocabulary] = useState<ClaimVocabulary | null>(null);
  const [vocabularyError, setVocabularyError] = useState('');
  const [vocabularyAttempt, setVocabularyAttempt] = useState(0);
  const [rendering, setRendering] = useState<string | null>(null);
  const [renderingError, setRenderingError] = useState('');
  const evidenceInput = useRef<HTMLTextAreaElement>(null);
  const claimOpen = claim !== null;
  const evidenceOpen = evidence !== null;
  useEffect(() => { if (claimOpen) (claimInput.current ?? subjectInput.current)?.focus(); }, [claimOpen]);
  const structuredOpen = !!claim?.structured;
  useEffect(() => {
    if (!structuredOpen || vocabulary) return;
    const controller = new AbortController();
    setVocabularyError('');
    api.request<ClaimVocabulary>('/claims/vocabulary', 'GET', undefined, undefined, false, controller.signal)
      .then(value => { if (!controller.signal.aborted) setVocabulary(value); },
        (failure: unknown) => { if (!controller.signal.aborted) setVocabularyError(errorText(failure)); });
    return () => controller.abort();
  }, [api, structuredOpen, vocabulary, vocabularyAttempt]);
  const structuredDraft = claim?.structured;
  const wordingError = structuredDraft ? wordingErrorOf(structuredDraft) : '';
  const renderable = structuredDraft?.subject.trim() && !wordingError ? JSON.stringify(claimOf(structuredDraft)) : '';
  useEffect(() => {
    setRendering(null);
    setRenderingError('');
    if (!renderable) return;
    // The server renderer is the only renderer, so the preview matches the saved profile and PDF.
    const controller = new AbortController();
    api.request<ClaimRendering>('/claims/render', 'POST', { claim: JSON.parse(renderable) as StructuredClaim }, undefined, false, controller.signal)
      .then(value => { if (!controller.signal.aborted) setRendering(value.rendering); },
        (failure: unknown) => { if (!controller.signal.aborted) setRenderingError(errorText(failure)); });
    return () => controller.abort();
  }, [api, renderable, structuredDraft]);
  useEffect(() => { if (evidenceOpen) evidenceInput.current?.focus(); }, [evidenceOpen]);
  const dirty = !!(claim || evidence || archive);
  const locked = busy || disabled;
  useEffect(() => { onDirtyChange(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => { onBusyChange(busy); }, [busy, onBusyChange]);
  useEffect(() => { if (error) errorRef.current?.focus(); }, [error]);
  useEffect(() => {
    if (!dirty) return;
    const prevent = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    window.addEventListener('beforeunload', prevent);
    return () => window.removeEventListener('beforeunload', prevent);
  }, [dirty]);

  async function read(action: () => Promise<void>) {
    setBusy(true); setError(''); setNotice('');
    try { await action(); } catch (failure) { setError(errorText(failure)); } finally { setBusy(false); }
  }
  async function mutate(path: string, method: string, body?: unknown) {
    setBusy(true); setError(''); setNotice('');
    const fingerprint = JSON.stringify({ path, method, body, version: profile.version });
    if (mutation.current?.fingerprint !== fingerprint) mutation.current = { fingerprint, key: crypto.randomUUID() };
    try {
      const value = await api.request<Profile>(path, method, body, profile.version, mutation.current.key);
      mutation.current = null; onProfile(value); setClaim(null); setEvidence(null); setArchive(null);
      setComparison(null); setHistory(null); setPreview(null); setRestoreConfirmed(false);
      setNotice(`Saved profile revision ${value.current_revision.revision_no}. Earlier revisions are unchanged.`);
    } catch (failure) { setError(errorText(failure)); } finally { setBusy(false); }
  }
  function editClaim(value: ClaimRecord) {
    const saved = value.revision.payload;
    const structured = 'claim' in saved ? draftOf(saved.claim) : structuredKinds.has(value.kind) ? { ...emptyStructured } : null;
    setError(''); setNotice(''); setClaim({ id: value.id, kind: value.kind, payload: literalClaimPayload(value), structured, legacyText: value.needs_attention ? value.rendering : '',
      evidence_links: value.revision.evidence_links.map(({ evidence_revision_id, support_kind, excerpt }) => ({ evidence_revision_id, support_kind, excerpt })),
      pinnedEvidence: value.revision.evidence_links.map(link => link.evidence) });
  }
  function editEvidence(value: EvidenceRecord) {
    setError(''); setNotice(''); const { source_type, privacy_class, employer_owned } = value.revision.provenance;
    setEvidence({ id: value.id, text: value.revision.payload.text, source: value.revision.payload.source, attestation: value.revision.payload.source === 'Owner attestation', source_type, privacy_class, employer_owned });
  }
  function payloadField(field: keyof ClaimPayload, value: string) {
    if (!claim) return;
    const payload = { ...claim.payload };
    if (field !== 'text' && !value) delete payload[field]; else payload[field] = value;
    setClaim({ ...claim, payload });
  }
  function structuredField<K extends keyof StructuredDraft>(field: K, value: StructuredDraft[K]) {
    if (claim?.structured) setClaim({ ...claim, structured: { ...claim.structured, [field]: value } });
  }
  function changeLink(index: number, update: Partial<EvidenceLink>) {
    if (claim) setClaim({ ...claim, evidence_links: claim.evidence_links.map((link, position) => position === index ? { ...link, ...update } : link) });
  }
  function submitClaim(event: FormEvent) {
    event.preventDefault(); if (!claim || (claim.structured && (!vocabulary || wordingErrorOf(claim.structured)))) return;
    void mutate(claim.id ? `/claims/${encodeURIComponent(claim.id)}` : '/claims', claim.id ? 'PUT' : 'POST', {
      kind: claim.kind, payload: claim.structured ? { ...contextOf(claim.payload), claim: claimOf(claim.structured) } : claim.payload, evidence_links: claim.evidence_links,
    });
  }
  function submitEvidence(event: FormEvent) {
    event.preventDefault(); if (!evidence) return;
    const { source_type, privacy_class, employer_owned } = evidence;
    if (!source_type || !privacy_class) { setError('Choose the source type and privacy class.'); return; }
    void mutate(evidence.id ? `/evidence/${encodeURIComponent(evidence.id)}` : '/evidence', evidence.id ? 'PUT' : 'POST', {
      payload: { text: evidence.text, source: evidence.attestation ? 'Owner attestation' : evidence.source },
      provenance: { source_type, privacy_class, employer_owned },
    });
  }
  async function loadHistory(before?: number) {
    await read(async () => {
      const page = await api.request<RevisionPage>(`/profile/revisions${before === undefined ? '' : `?before=${before}`}`);
      setHistory(existing => before === undefined ? page.items : [...(existing ?? []), ...page.items]); setCursor(page.next_cursor);
    });
  }
  function move(value: ClaimRecord, target: ClaimRecord | undefined) {
    if (!target) return;
    const ids = profile.current_revision.snapshot.claim_revision_ids.map(id =>
      id === value.revision.id ? target.revision.id : id === target.revision.id ? value.revision.id : id);
    void mutate('/profile', 'PUT', { ...profile.current_revision.snapshot, claim_revision_ids: ids });
  }
  const sources = new Map(profile.evidence.map(item => [item.revision.id, item.revision]));
  for (const item of claim?.pinnedEvidence ?? []) sources.set(item.id, item);
  const currentClaims = profile.current_revision.claims;
  return <>
    <section className="panel profile-entries" aria-labelledby="profile-entries-title"><h2 id="profile-entries-title">Profile entries</h2>
      <p>Record only facts you supply. Every saved entry pins an exact source revision or explicit owner attestation. Source links and owner authorship are not independent verification.</p>
      <nav aria-label="Profile sections" className="profile-section-selector">
        {[...kinds, { value: 'sources' as const, label: 'Sources' }, { value: 'history' as const, label: 'History' }].map(item => {
          const count = item.value === 'sources' ? profile.evidence.length : item.value === 'history' ? null : currentClaims.filter(entry => entry.kind === item.value).length;
          return <button key={item.value} type="button" className="secondary" aria-pressed={section === item.value}
            aria-describedby={count === null ? undefined : `profile-section-count-${item.value}`}
            disabled={busy || (dirty && section !== item.value)} onClick={() => setSection(item.value)}>
            {item.label}{count !== null && <span className="profile-section-count" aria-hidden="true">{count}</span>}
          </button>;
        })}
      </nav>
      <div className="profile-section-counts">
        {kinds.map(item => <span key={item.value} id={`profile-section-count-${item.value}`}>{currentClaims.filter(entry => entry.kind === item.value).length} saved entries</span>)}
        <span id="profile-section-count-sources">{profile.evidence.length} saved sources</span>
      </div>
      {dirty && <p className="profile-buffer-notice" role="status">An entry, source or archive confirmation is open. Save or discard changes, or keep the active record, before switching sections.</p>}
      {disabled && <p className="hint">Save or discard identity changes before editing entries or sources.</p>}
      <div className="error" ref={errorRef} tabIndex={-1} role="alert" hidden={!error}>{error}</div><p role="status">{notice}</p>
      {error && <button className="secondary" disabled={locked} onClick={() => { void read(async () => { setComparison(await api.request<Profile>('/profile')); }); }}>Load latest profile for comparison</button>}
      {comparison && <div className="comparison"><h3>Latest saved profile · revision {comparison.current_revision.revision_no}</h3>
        <p>{comparison.current_revision.snapshot.display_name} · {comparison.current_revision.snapshot.headline}</p>
        <p className="profile-description-preview">Description: {comparison.current_revision.snapshot.description || 'Not supplied'}</p>
        {comparison.current_revision.claims.map(item => <div key={item.id}><strong>{item.kind}</strong><ClaimWords claim={item} /></div>)}
        {comparison.evidence.map(item => <blockquote key={item.id}><p>{item.revision.payload.source}</p><p>{item.revision.payload.text}</p></blockquote>)}
        <p>Your open editor remains unchanged. Rebasing changes the save precondition, not the entry text. Archived entries cannot be revived by saving an old editor.</p>
        <button className="secondary" disabled={locked} onClick={() => { onProfile(comparison); setComparison(null); setNotice('Latest version selected explicitly. Review your retained text before saving.'); }}>Use latest version and retain my editor</button>
      </div>}
      {kinds.filter(kind => kind.value === section).map(kind => {
        const entries = currentClaims.filter(item => item.kind === kind.value);
        return <section key={kind.value} aria-labelledby={`profile-${kind.value}`}><h3 id={`profile-${kind.value}`} tabIndex={-1}>{kind.label}</h3>
          {kind.value === 'experience' && <p className="hint">Owner-supplied closed dates cover {profile.experience_coverage.minimum_days}–{profile.experience_coverage.maximum_days} calendar days, with overlaps counted once. This is a conservative date range, not verified employment or a qualification. {profile.experience_coverage.undated_or_open_entries} open or undated entries are excluded; no present date is assumed.</p>}
          {entries.length === 0 ? <p className="hint">No saved {kind.label.toLowerCase()}.</p> : <ol>{entries.map((item, index) => <li key={item.id}><ClaimWords claim={item} />
            <div className="actions"><button className="secondary" disabled={locked || dirty} onClick={() => editClaim(item)}>Edit {kind.label.toLowerCase()} entry {index + 1}</button>
              <button className="secondary" disabled={locked || dirty || index === 0} onClick={() => move(item, entries[index - 1])}>Move {kind.label.toLowerCase()} entry {index + 1} up</button>
              <button className="secondary" disabled={locked || dirty || index === entries.length - 1} onClick={() => move(item, entries[index + 1])}>Move {kind.label.toLowerCase()} entry {index + 1} down</button>
              <button className="secondary" disabled={locked || dirty} onClick={() => setArchive({ kind: 'claim', id: item.id, text: item.rendering })}>Archive {kind.label.toLowerCase()} entry {index + 1}</button></div>
          </li>)}</ol>}
          <button className="secondary" disabled={locked || dirty || profile.evidence.length === 0} onClick={() => { setError(''); setClaim({ kind: kind.value, payload: { text: '' }, structured: structuredKinds.has(kind.value) ? { ...emptyStructured } : null, legacyText: '', evidence_links: [], pinnedEvidence: [] }); }}>Add {kind.label.toLowerCase()} entry</button>
        </section>;
      })}
      {profile.evidence.length === 0 && section !== 'sources' && section !== 'history' && <p className="hint">Choose Sources and add a source or explicit owner attestation before adding an entry.</p>}
      {claim && <form onSubmit={submitClaim}><fieldset disabled={locked}><legend>{claim.id ? 'Edit saved entry' : 'New entry'}</legend>
        <p>Kind: {kinds.find(kind => kind.value === claim.kind)?.label}. Entry kind cannot change after creation.</p>
        {claim.structured ? <fieldset><legend>Structured claim</legend>
          {claim.legacyText && <p className="hint">Needs attention. Saved free-text statement: “{claim.legacyText}”. Saving stores the structured fields below instead of that sentence.</p>}
          {vocabulary ? <label>Ownership verb<select required value={claim.structured.verb} onChange={event => structuredField('verb', event.target.value)}>{vocabulary.verbs.map((verb, index) => <option key={verb.key} value={verb.key}>{index + 1}. {verb.label}</option>)}</select></label> : vocabularyError ? <div>
            <p className="error" role="alert">Could not load the ownership vocabulary: {vocabularyError}</p>
            <button type="button" className="secondary" onClick={() => { setVocabularyError(''); setVocabularyAttempt(value => value + 1); }}>Retry ownership vocabulary</button>
          </div> : <p>Loading the ownership vocabulary…</p>}
          <p className="hint">Verbs run from strongest to weakest ownership. Choose the one your evidence supports.</p>
          <label>Subject<input ref={subjectInput} required maxLength={300} value={claim.structured.subject} onChange={event => structuredField('subject', event.target.value)} /></label>
          <label>Scope (optional)<input maxLength={200} value={claim.structured.scope} onChange={event => structuredField('scope', event.target.value)} /></label>
          <label>Metric value (optional)<input inputMode="decimal" pattern="[0-9]{1,12}([.][0-9]{1,4})?" maxLength={17} value={claim.structured.value} onChange={event => structuredField('value', event.target.value)} /></label>
          {vocabulary && claim.structured.value && <>
            <label>Metric unit<select value={claim.structured.unit} onChange={event => structuredField('unit', event.target.value)}>{vocabulary.units.map(unit => <option key={unit} value={unit}>{unit}</option>)}</select></label>
            <label>Metric magnitude<select value={claim.structured.magnitude} onChange={event => structuredField('magnitude', event.target.value)}><option value="">None</option>{vocabulary.magnitudes.map(value => <option key={value} value={value}>{value}</option>)}</select></label>
            <label>Metric change<select value={claim.structured.change} onChange={event => structuredField('change', event.target.value)}><option value="">None</option>{vocabulary.changes.map(value => <option key={value} value={value}>{value}</option>)}</select></label>
            <label>Metric rate<select value={claim.structured.per} onChange={event => structuredField('per', event.target.value)}><option value="">None</option>{vocabulary.rates.map(value => <option key={value} value={value}>per {value}</option>)}</select></label>
            <label>Rate wording<select value={claim.structured.rate_wording} onChange={event => structuredField('rate_wording', event.target.value as StructuredDraft['rate_wording'])}><option value="">Per (default)</option>{vocabulary.rate_wordings.map(value => <option key={value} value={value} disabled={!claim.structured?.per || (value === 'annual' && claim.structured.per !== 'year')}>{rateWordingLabels[value]}</option>)}</select></label>
            <label>Metric measure (optional)<input maxLength={200} value={claim.structured.measure} onChange={event => structuredField('measure', event.target.value)} /></label>
          </>}
          <label>Period start (optional)<input maxLength={7} placeholder="YYYY or YYYY-MM" required={claim.structured.end !== ''} value={claim.structured.start} onChange={event => structuredField('start', event.target.value)} /></label>
          <label>Period end (optional)<input maxLength={7} placeholder="YYYY or YYYY-MM" required={claim.structured.start !== ''} value={claim.structured.end} onChange={event => structuredField('end', event.target.value)} /></label>
          <p className="hint">A period needs both a start and an end.</p>
          {vocabulary && (claim.structured.start || claim.structured.end) && <>
            <label>Period wording<select value={claim.structured.style} onChange={event => structuredField('style', event.target.value as StructuredDraft['style'])}><option value="">Parenthetical (default)</option>{vocabulary.period_styles.map(value => <option key={value} value={value} disabled={value === 'in_year' && (!structuredDraft || !isSingleYear(structuredDraft))}>{periodStyleLabels[value]}</option>)}</select></label>
            <label>Period separator<select value={claim.structured.separator} onChange={event => structuredField('separator', event.target.value as StructuredDraft['separator'])}><option value="">En dash (default)</option>{vocabulary.period_separators.map(value => <option key={value} value={value}>{periodSeparatorLabels[value]}</option>)}</select></label>
            <p className="hint">In year wording needs the same year-only date at both bounds. Separators apply when a range is rendered.</p>
          </>}
          {wordingError && <p className="error" role="alert">{wordingError}</p>}
          <p className="hint">The source excerpt must contain the complete rendered sentence as written.</p>
          <h4>Rendering (English)</h4><output aria-live="polite" aria-label="Claim rendering">{rendering ?? (renderingError ? 'Preview unavailable.' : 'Complete the structured fields to preview the rendering.')}</output>
          {renderingError && <p className="error" role="alert">Could not preview the claim: {renderingError}</p>}
        </fieldset> : <label>Entry statement<textarea ref={claimInput} required maxLength={4000} value={claim.payload.text} onChange={event => payloadField('text', event.target.value)} /></label>}
        <label>Organization<input maxLength={200} value={claim.payload.organization ?? ''} onChange={event => payloadField('organization', event.target.value)} /></label>
        <label>Role or qualification<input maxLength={200} value={claim.payload.role ?? ''} onChange={event => payloadField('role', event.target.value)} /></label>
        <p className="hint">Dates accept YYYY, YYYY-MM or YYYY-MM-DD. Omit unknown dates; precision is never invented. Impossible or definitely reversed intervals are rejected.</p>
        <label>Start date<input maxLength={10} placeholder="YYYY, YYYY-MM or YYYY-MM-DD" value={claim.payload.start_date ?? ''} onChange={event => payloadField('start_date', event.target.value)} /></label>
        <label>End date<input maxLength={10} placeholder="YYYY, YYYY-MM or YYYY-MM-DD" value={claim.payload.end_date ?? ''} onChange={event => payloadField('end_date', event.target.value)} /></label>
        <label>Proficiency or level<input maxLength={120} value={claim.payload.proficiency ?? ''} onChange={event => payloadField('proficiency', event.target.value)} /></label>
        <p className="hint">Include the scale in your owner assertion, for example “CEFR B2” or “intermediate on my own beginner/intermediate/advanced scale”. A language name alone is not proof of a required level.</p>
        {claim.kind === 'project' && <label>Project reference URL (optional)<input type="url" maxLength={2048} value={claim.payload.project_url ?? ''} onChange={event => payloadField('project_url', event.target.value)} /><span className="hint">HTTP or HTTPS syntax only. The target is not fetched, crawled or independently verified.</span></label>}
        <h4>Pinned evidence links</h4><p className="hint">Select at least one exact source revision. Updating a source later never silently updates an existing entry.</p>
        {claim.evidence_links.map((link, index) => <fieldset key={index}><legend>Source link {index + 1}</legend>
          <label>Evidence revision {index + 1}<select required value={link.evidence_revision_id} onChange={event => changeLink(index, { evidence_revision_id: event.target.value })}><option value="">Choose a saved source</option>{Array.from(sources.values()).map(source => <option key={source.id} value={source.id}>{source.payload.source} · revision {source.revision_no} · {source.payload.text.slice(0, 100)} · {source.id}</option>)}</select></label>
          {sources.get(link.evidence_revision_id) && <blockquote>{sources.get(link.evidence_revision_id)?.payload.text}</blockquote>}
          <label>Support type {index + 1}<select value={link.support_kind} onChange={event => changeLink(index, { support_kind: event.target.value as EvidenceLink['support_kind'] })}><option value="direct">Direct source (owner supplied)</option><option value="user_asserted">Owner attestation (unverified)</option><option value="context">Context only</option></select></label>
          <label>Source excerpt {index + 1}<textarea required maxLength={4000} value={link.excerpt} onChange={event => changeLink(index, { excerpt: event.target.value })} /></label>
          <button type="button" className="secondary" onClick={() => setClaim({ ...claim, evidence_links: claim.evidence_links.filter((_, position) => position !== index) })}>Remove source link {index + 1}</button>
        </fieldset>)}
        <button type="button" className="secondary" disabled={claim.evidence_links.length >= 16} onClick={() => setClaim({ ...claim, evidence_links: [...claim.evidence_links, { evidence_revision_id: '', support_kind: 'user_asserted', excerpt: '' }] })}>Add evidence link</button>
        <div className="actions"><button disabled={claim.evidence_links.length === 0 || (!!claim.structured && (!vocabulary || !!wordingError))}>Save entry revision</button><button type="button" className="secondary" onClick={() => { setClaim(null); setComparison(null); setError(''); }}>Discard entry changes</button></div>
      </fieldset></form>}
      {archive && <div className="comparison"><h3>Confirm archive</h3><p>{archive.text}</p><p>This removes the {archive.kind} from the active profile. Historical revisions and their source links remain readable. Only an explicit historical restore can reactivate archived records.</p>
        <button disabled={locked} onClick={() => { void mutate(`/${archive.kind === 'claim' ? 'claims' : 'evidence'}/${encodeURIComponent(archive.id)}`, 'DELETE'); }}>Confirm archive</button><button className="secondary" disabled={locked} onClick={() => setArchive(null)}>Keep active record</button>
      </div>}
    </section>
    <section className="panel" hidden={section !== 'sources'} aria-labelledby="profile-evidence-title"><h2 id="profile-evidence-title">Sources and owner attestations</h2>
      <p>Manual, versioned source text only. No file import, external verification or provider calls. Archiving removes a source from the active editor, not from immutable historical links.</p>
      {profile.evidence.length === 0 ? <p>No saved sources.</p> : <ul>{profile.evidence.map((item, index) => <li key={item.id}><h3>{item.revision.payload.source}</h3><p>{item.revision.payload.text}</p>
        <small>Revision {item.revision.revision_no} · {item.revision.id} · {item.revision.created_at} · owner {item.revision.author_owner_id}</small>
        <div className="actions"><button className="secondary" disabled={locked || dirty} onClick={() => editEvidence(item)}>Edit source {index + 1}</button><button className="secondary" disabled={locked || dirty} onClick={() => setArchive({ kind: 'evidence', id: item.id, text: item.revision.payload.text })}>Archive source {index + 1}</button></div>
      </li>)}</ul>}
      <button className="secondary" disabled={locked || dirty} onClick={() => { setError(''); setEvidence({ text: '', source: '', attestation: false, source_type: '', privacy_class: '', employer_owned: false }); }}>Add source or owner attestation</button>
      {evidence && <form onSubmit={submitEvidence}><fieldset disabled={locked}><legend>{evidence.id ? 'Edit saved source' : 'New source'}</legend>
        <label>Source text<textarea ref={evidenceInput} required maxLength={8000} value={evidence.text} onChange={event => setEvidence({ ...evidence, text: event.target.value })} /></label>
        <label className="check"><input type="checkbox" checked={evidence.attestation} onChange={event => setEvidence({ ...evidence, attestation: event.target.checked })} />This is my explicit owner attestation, not independent verification</label>
        <label>Source reference<input required={!evidence.attestation} disabled={evidence.attestation} maxLength={500} value={evidence.attestation ? 'Owner attestation' : evidence.source} onChange={event => setEvidence({ ...evidence, source: event.target.value })} /></label>
        <label>Source type<select required value={evidence.source_type} onChange={event => setEvidence({ ...evidence, source_type: event.target.value as EvidenceSourceType | '' })}><option value="">Choose a source type</option>{Object.entries(evidenceSourceTypes).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label>Privacy class<select required value={evidence.privacy_class} onChange={event => setEvidence({ ...evidence, privacy_class: event.target.value as EvidencePrivacyClass | '' })}><option value="">Choose a privacy class</option>{Object.entries(evidencePrivacyClasses).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label className="check"><input type="checkbox" checked={evidence.employer_owned} onChange={event => setEvidence({ ...evidence, employer_owned: event.target.checked })} />My employer owns this content</label>
        <div className="actions"><button>Save source revision</button><button type="button" className="secondary" onClick={() => { setEvidence(null); setComparison(null); setError(''); }}>Discard source changes</button></div>
      </fieldset></form>}
    </section>
    <section className="panel" hidden={section !== 'history'} aria-labelledby="profile-history-title"><h2 id="profile-history-title">Immutable profile history</h2>
      <p>Preview exact saved statements, evidence and owner authorship. Restore appends a new revision; it never overwrites history or existing documents.</p>
      <button className="secondary" disabled={locked} onClick={() => { void loadHistory(); }}>Load profile history</button>
      {history && <><ol>{history.map(item => <li key={item.id}><button className="secondary" disabled={locked} onClick={() => { void read(async () => { setPreview(await api.request<ProfileRevision>(`/profile/revisions/${encodeURIComponent(item.id)}`)); setRestoreConfirmed(false); }); }}>Preview profile revision {item.revision_no}</button><p>{item.reason} · {item.created_at} · owner {item.author_owner_id}</p></li>)}</ol>
        {cursor !== null && <button className="secondary" disabled={locked} onClick={() => { void loadHistory(cursor); }}>Load earlier profile revisions</button>}</>}
      {preview && <article className="comparison"><h3>Historical profile revision {preview.revision_no}</h3><p>{preview.snapshot.display_name} · {preview.snapshot.headline}</p>
        <p className="profile-description-preview">Description: {preview.snapshot.description || 'Not supplied'}</p>
        <p>{preview.created_at} · {preview.reason} · owner {preview.author_owner_id}</p><small>Revision {preview.id} · SHA-256 {preview.content_sha256}</small>
        {preview.claims.map(item => <section key={item.id}><h4>{item.kind}</h4><ClaimWords claim={item} /></section>)}
        <h4>Exact saved source state</h4>{preview.evidence.map(item => <blockquote key={item.id}><p>{item.revision.payload.source}</p><p>{item.revision.payload.text}</p><small>{item.revision.id} · {item.revision.created_at} · owner {item.revision.author_owner_id} · SHA-256 {item.revision.content_sha256}</small></blockquote>)}
        {dirty && <p className="hint">Save or discard the open editor before restoring history.</p>}
        <label className="check"><input type="checkbox" disabled={locked || dirty} checked={restoreConfirmed} onChange={event => setRestoreConfirmed(event.target.checked)} />Restore this exact saved profile and source state as a new revision; retain displaced history</label>
        <button disabled={locked || dirty || !restoreConfirmed} onClick={() => { void mutate('/profile/restore', 'POST', { revision_id: preview.id }); }}>Restore as new profile revision</button>
      </article>}
    </section>
  </>;
}
