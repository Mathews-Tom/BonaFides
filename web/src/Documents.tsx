// SPDX-License-Identifier: MIT OR Apache-2.0
import { useCallback, useEffect, useRef, useState } from 'react';
import { Api, ApiError, errorText, terminal, type ApplicationDocuments, type BaseResumeRequest, type DocumentBody, type DocumentDefaults, type DocumentDefaultsPatch, type DocumentFact, type DocumentIssue, type DocumentRecord, type DocumentRenderRequest, type DocumentRestoreRequest, type DocumentReviewRequest, type DocumentRevision, type DocumentRevisionPage, type DocumentSaveRequest, type DocumentTemplate, type GenerationContext, type GenerationRequest, type Profile, type ProfileRevisionSummary, type RevisionPage, type SectionProposal, type SectionProposalApplyRequest, type SectionProposalPage, type SectionRegenerationContext, type SectionRegenerationRequest, type Workflow } from './api';
import type { ApplicationPage, ApplicationSummary } from './tracking-api';
import { copyBody, ISSUE_REASONS, issueAnchor, linkedClaim, rebaseBody, sameBody } from './document-state';
import { proposalMatches, SectionProposals } from './SectionProposals';
import './Documents.css';

export interface DocumentsProps {
  api: Api; profile: Profile | null; disabled?: boolean; externalDocumentId?: string | null; externalRequestKey?: string | null;
  onDirtyChange?: (dirty: boolean) => void; onBusyChange?: (busy: boolean) => void;
}
const docPath = (id: string) => `/documents/${encodeURIComponent(id)}`;
const appPath = (id: string) => `/applications/${encodeURIComponent(id)}`;
const aborted = (error: unknown) => error instanceof DOMException && error.name === 'AbortError';
function withheldClaimText(projection: Record<string, unknown>, id: string): string {
  const claims = Array.isArray(projection.claims) ? projection.claims as { id?: unknown; text?: unknown }[] : [];
  const claim = claims.find(item => item.id === id);
  return typeof claim?.text === 'string' ? `“${claim.text}”` : `claim revision ${id}`;
}


const TASK_REASONS: Record<string, string> = {
  model_output_invalid_after_correction: 'The model output did not match the required document shape, even after one corrective retry. No documents or application changes were created.',
  model_incomplete: 'The model output was cut off before it finished. No documents were created.',
  stale_document_context: 'Your profile, posting or settings changed after the request. No documents were created; preview and request again.',
};


function Sources({ facts, claimIds, evidenceIds }: { facts: DocumentFact[]; claimIds: string[]; evidenceIds: string[] }) {
  const selected = facts.filter(fact => claimIds.includes(fact.claim_revision_id) && (!fact.evidence_revision_id || evidenceIds.includes(fact.evidence_revision_id)));
  return <>{selected.length ? selected.map((fact, index) => <div className="document-source" key={`${fact.claim_revision_id}-${fact.evidence_revision_id}-${index}`}>
    <p><strong>{fact.status === 'user_asserted' ? 'Owner-asserted — not independently verified' : fact.status === 'supported' ? 'Supported by the supplied source — not independently verified' : 'Unsupported'}</strong></p>
    <p>Accepted claim: {fact.claim_text}</p><blockquote>{fact.evidence_text || 'No separate source text was supplied.'}</blockquote><p>Source: {fact.source || 'Owner statement'}</p>
    <small>Claim {fact.claim_revision_id} · SHA-256 {fact.claim_sha256}</small>{fact.evidence_revision_id && <small>Evidence {fact.evidence_revision_id} · SHA-256 {fact.evidence_sha256 ?? 'Unavailable'}</small>}
  </div>) : <p>No frozen supporting source is available for this block. References alone do not establish support.</p>}</>;
}
const RATE_STAGES = [['generate_documents', 'Pair generation'], ['regenerate_section', 'Section regeneration']] as const;
function ClaimJsonRates({ rates }: { rates: ApplicationDocuments['claim_json_rates'] }) {
  return <section className="disclosure" aria-label="Valid claim JSON rate for this route"><h4>Valid claim JSON rate for this route</h4><ul>{RATE_STAGES.map(([stage, label]) => { const item = rates[stage]; return <li key={stage}>
    <strong>{label}: {item.rate_percent === null ? 'no checked generations yet' : `${item.rate_percent}% valid`}.</strong> {item.valid_first} valid on the first response, {item.valid_after_correction} valid after one correction, {item.invalid_after_correction} invalid after correction; {item.failed_before_check} ended before a response could be checked (cut off, unreachable or interrupted), not counted in the rate.
  </li>; })}</ul><p>Counts cover finished generations sent to this exact route. Valid JSON is a shape check, not proof that statements are supported.</p></section>;
}
function ReadonlyBody({ revision }: { revision: DocumentRevision }) {
  return <article className="paper"><h3>{revision.body.title}</h3>{revision.body.sections.map(section => <section key={section.key}><h4>{section.heading}</h4>{section.blocks.map(block => <div className="evidence-seam" key={block.block_key}><p>{block.text}</p><details><summary>Statement sources ({block.claim_kind})</summary><Sources facts={revision.provenance_snapshot.facts ?? []} claimIds={block.claim_revision_ids} evidenceIds={block.evidence_revision_ids} /></details></div>)}</section>)}</article>;
}

export function Documents({ api, profile, disabled = false, externalDocumentId, externalRequestKey, onDirtyChange, onBusyChange }: DocumentsProps) {
  const [mode, setMode] = useState<'base' | 'tailored'>('base');
  const [templates, setTemplates] = useState<DocumentTemplate[]>([]);
  const [template, setTemplate] = useState('');
  const [defaults, setDefaults] = useState<DocumentDefaults | null>(null);
  const [defaultsDraft, setDefaultsDraft] = useState({ template_version: '', tone: 'professional' as DocumentDefaults['tone'] });
  const [defaultsError, setDefaultsError] = useState('');
  const [defaultsConflict, setDefaultsConflict] = useState(false);
  const defaultsState = useRef({ saved: defaults, draft: defaultsDraft });
  defaultsState.current = { saved: defaults, draft: defaultsDraft };
  const choicesInitialized = useRef(false);
  const [profileRevisions, setProfileRevisions] = useState<ProfileRevisionSummary[]>([]);
  const [profileCursor, setProfileCursor] = useState<number | null>(null);
  const [selectedProfile, setSelectedProfile] = useState('');
  const [applications, setApplications] = useState<ApplicationSummary[]>([]);
  const [applicationCursor, setApplicationCursor] = useState<string | null>(null);
  const [applicationId, setApplicationId] = useState('');
  const [pair, setPair] = useState<ApplicationDocuments | null>(null);
  const [context, setContext] = useState<GenerationContext | null>(null);
  const [contextConfirmed, setContextConfirmed] = useState(false);
  const [tone, setTone] = useState<'professional' | 'concise'>('professional');
  const [doc, setDoc] = useState<DocumentRecord | null>(null);
  const [buffer, setBuffer] = useState<DocumentBody | null>(null);
  const [comparison, setComparison] = useState<DocumentRecord | null>(null);
  const [reconcile, setReconcile] = useState(false);
  const [rebaseConfirmed, setRebaseConfirmed] = useState(false);
  const [history, setHistory] = useState<DocumentRevisionPage | null>(null);
  const [preview, setPreview] = useState<DocumentRevision | null>(null);
  const [restoreConfirmed, setRestoreConfirmed] = useState(false);
  const [reviewedKeys, setReviewedKeys] = useState<string[]>([]);
  const [sectionKey, setSectionKey] = useState('');
  const [sectionTone, setSectionTone] = useState<'professional' | 'concise'>('professional');
  const [sectionContext, setSectionContext] = useState<SectionRegenerationContext | null>(null);
  const [sectionConfirmed, setSectionConfirmed] = useState(false);
  const [proposals, setProposals] = useState<SectionProposalPage | null>(null);
  const [proposal, setProposal] = useState<SectionProposal | null>(null);
  const [applyConfirmed, setApplyConfirmed] = useState(false);
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [monitorError, setMonitorError] = useState('');
  const [pollToken, setPollToken] = useState(0);
  const mounted = useRef(false);
  const lock = useRef(false);
  const selection = useRef(0);
  // Monitoring retries must retain the selection that initiated the task.
  const workflowSelection = useRef(0);
  const controllers = useRef(new Set<AbortController>());
  const mutation = useRef<{ fingerprint: string; key: string } | null>(null);
  const handledExternal = useRef<string | null>(null);
  const processedWorkflow = useRef<string | null>(null);
  const errorRef = useRef<HTMLDivElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const callbacks = useRef({ onDirtyChange, onBusyChange });
  callbacks.current = { onDirtyChange, onBusyChange };
  const activeTask = !!workflow && !terminal(workflow.state);
  const dirty = buffer !== null || reconcile;
  const locked = disabled || busy || activeTask;
  const current = useRef({ doc, buffer, dirty, disabled, profile, mode, applicationId, activeTask });
  current.current = { doc, buffer, dirty, disabled, profile, mode, applicationId, activeTask };
  const profileId = selectedProfile || profile?.current_revision.id || '';
  const upstreamChanged = !!doc && !!profile && doc.current_revision.profile_revision_id !== profile.current_revision.id;
  const application = applications.find(item => item.id === applicationId);
  const factualKeys = doc?.current_revision.body.sections.flatMap(section => section.blocks.filter(block => block.claim_kind === 'factual').map(block => block.block_key)) ?? [];
  const issues: DocumentIssue[] = doc ? [...doc.current_revision.validation_issues, ...doc.unsupported_claims.filter(path => !doc.current_revision.validation_issues.some(issue => issue.path === path)).map(path => ({ path, code: 'unsupported_claim' }))] : [];
  const allReviewed = factualKeys.every(key => reviewedKeys.includes(key));

  useEffect(() => {
    mounted.current = true; const pending = controllers.current;
    return () => { mounted.current = false; for (const controller of pending) controller.abort(); callbacks.current.onDirtyChange?.(false); callbacks.current.onBusyChange?.(false); };
  }, []);
  useEffect(() => { callbacks.current.onDirtyChange?.(dirty); }, [dirty]);
  useEffect(() => { callbacks.current.onBusyChange?.(busy || activeTask); }, [busy, activeTask]);
  useEffect(() => { if (error) errorRef.current?.focus(); }, [error]);
  useEffect(() => {
    if (!dirty && !busy && !activeTask) return;
    const prevent = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', prevent); return () => window.removeEventListener('beforeunload', prevent);
  }, [dirty, busy, activeTask]);
  useEffect(() => { setContext(null); setContextConfirmed(false); }, [profileId, profile?.version, template, tone, applicationId]);
  useEffect(() => {
    setSectionContext(null); setSectionConfirmed(false); setApplyConfirmed(false);
  }, [doc?.id, doc?.version, doc?.current_revision.id, profile?.version, profile?.current_revision.id, sectionKey, sectionTone, dirty]);
  useEffect(() => { setProposals(null); setProposal(null); setSectionKey(''); }, [doc?.id]);
  useEffect(() => {
    const sections = current.current.doc?.current_revision.body.sections ?? [];
    setSectionKey(previous => sections.some(section => section.key === previous) ? previous : sections[0]?.key ?? '');
  }, [doc?.id, doc?.current_revision.id]);

  const request = useCallback(async <T,>(signal: AbortSignal, path: string, method = 'GET', body?: unknown, version?: number, key: boolean | string = false): Promise<T> => {
    const value = await api.request<T>(path, method, body, version, key, signal);
    if (!mounted.current || signal.aborted) throw new DOMException('Request stopped', 'AbortError');
    return value;
  }, [api]);
  const receive = useCallback((value: DocumentRecord, replace = false) => {
    const live = current.current;
    if (live.doc?.id === value.id && live.doc.version > value.version) return;
    if (!replace && live.dirty) {
      if (live.doc?.id !== value.id) { setNotice('A different result is available. Your dirty document has not been replaced; save or discard before opening it.'); return; }
      setComparison(value); setReconcile(true); setRebaseConfirmed(false); return;
    }
    current.current.doc = value; current.current.buffer = null; current.current.dirty = false;
    setPair(previous => previous && previous.application_id === value.application_id ? { ...previous, items: previous.items.map(item => item.id === value.id ? value : item) } : previous);
    setDoc(value); setBuffer(null); setComparison(null); setReconcile(false); setRebaseConfirmed(false);
    setPreview(null); setRestoreConfirmed(false); setReviewedKeys([]); setHistory(null);
  }, []);
  const run = useCallback(async (action: (signal: AbortSignal) => Promise<void>) => {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError(''); setNotice('');
    const controller = new AbortController(); controllers.current.add(controller);
    try { await action(controller.signal); }
    catch (failure) { if (!aborted(failure) && mounted.current) setError(errorText(failure)); }
    finally { controllers.current.delete(controller); lock.current = false; if (mounted.current) setBusy(false); }
  }, []);
  function keyFor(path: string, body: unknown, version: number) {
    const fingerprint = JSON.stringify({ path, body, version });
    if (mutation.current?.fingerprint !== fingerprint) mutation.current = { fingerprint, key: crypto.randomUUID() };
    return mutation.current.key;
  }
  const loadBase = useCallback(async (signal: AbortSignal, epoch: number) => {
    try {
      const value = await request<DocumentRecord>(signal, '/profile/base-resume');
      if (epoch === selection.current) receive(value);
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 404 && failure.code === 'base_resume_not_found') { if (epoch === selection.current && !current.current.dirty) setDoc(null); }
      else throw failure;
    }
  }, [request, receive]);
  const loadPair = useCallback(async (signal: AbortSignal, id: string, epoch: number, preferredId?: string, expected?: NonNullable<Workflow['result']>['documents']) => {
    const value = await request<ApplicationDocuments>(signal, `${appPath(id)}/documents`);
    if (epoch !== selection.current || current.current.applicationId !== id) return;
    if (expected) {
      const keys = value.items.flatMap(item => item.current_revision.body.sections.flatMap(section => section.blocks.map(block => block.block_key)));
      if (value.application_id !== id || value.items.length !== 2 || new Set(expected.map(item => item.document_id)).size !== 2 ||
        !expected.every(member => value.items.some(item => item.id === member.document_id && item.kind === member.kind && item.scope === 'tailored' && item.application_id === id && item.current_revision.id === member.revision_id)) ||
        new Set(keys).size !== keys.length) throw new Error('incomplete_pair');
    }
    setPair(value); setContext(null); setContextConfirmed(false);
    const chosen = value.items.find(item => item.id === preferredId) ?? value.items.find(item => item.kind === 'resume') ?? value.items[0];
    if (chosen) receive(chosen); else if (!current.current.dirty) { setDoc(null); setHistory(null); setPreview(null); }
  }, [request, receive]);
  const loadDefaults = useCallback(async (signal: AbortSignal) => {
    try {
      const value = await request<DocumentDefaults>(signal, '/document-defaults');
      const previous = defaultsState.current;
      const draft = {
        template_version: previous.saved && previous.draft.template_version !== previous.saved.template_version ? previous.draft.template_version : value.template_version,
        tone: previous.saved && previous.draft.tone !== previous.saved.tone ? previous.draft.tone : value.tone,
      };
      setDefaults(value); setDefaultsDraft(draft); setDefaultsError(''); setDefaultsConflict(false);
      defaultsState.current = { saved: value, draft };
      if (!choicesInitialized.current) {
        choicesInitialized.current = true;
        setTemplate(value.template_version); setTone(value.tone); setSectionTone(value.tone);
      }
    } catch (failure) {
      if (!aborted(failure)) setDefaultsError(`Remembered defaults could not be loaded. ${errorText(failure)}`);
      throw failure;
    }
  }, [request]);
  function saveDefaults() {
    if (!defaults || locked || defaultsConflict) return;
    const body: DocumentDefaultsPatch = {};
    if (defaultsDraft.template_version !== defaults.template_version) body.template_version = defaultsDraft.template_version;
    if (defaultsDraft.tone !== defaults.tone) body.tone = defaultsDraft.tone;
    if (!Object.keys(body).length) return;
    void run(async signal => {
      try {
        const value = await request<DocumentDefaults>(signal, '/document-defaults', 'PATCH', body, defaults.version, keyFor('/document-defaults', body, defaults.version));
        mutation.current = null; setDefaults(value); setDefaultsDraft({ template_version: value.template_version, tone: value.tone }); setDefaultsError(''); setDefaultsConflict(false);
        setNotice('Remembered defaults saved for future work. Current operation choices, saved documents, history, PDFs and permissions are unchanged.');
      } catch (failure) {
        if (!aborted(failure)) {
          setDefaultsError(`Remembered defaults were not confirmed saved. ${errorText(failure)}`);
          if (failure instanceof ApiError && failure.status === 412) setDefaultsConflict(true);
        }
        throw failure;
      }
    });
  }
  useEffect(() => {
    const epoch = selection.current;
    void run(async signal => {
      const results = await Promise.allSettled([
        request<{ items: DocumentTemplate[] }>(signal, '/document-templates').then(result => {
          setTemplates(result.items);
        }),
        loadBase(signal, epoch),
        loadDefaults(signal),
      ]);
      for (const result of results) if (result.status === 'rejected') throw result.reason;
    });
  }, [request, run, loadBase, loadDefaults]);
  useEffect(() => {
    const externalKey = externalRequestKey ?? externalDocumentId ?? null;
    if (!externalDocumentId || !externalKey || handledExternal.current === externalKey || busy || lock.current) return;
    handledExternal.current = externalKey;
    if (current.current.dirty || current.current.activeTask) { setNotice('Save or discard document changes and finish the active task before opening a different result. Your editor is unchanged.'); return; }
    const epoch = ++selection.current;
    void run(async signal => {
      const value = await request<DocumentRecord>(signal, docPath(externalDocumentId));
      if (epoch !== selection.current) return;
      if (!current.current.dirty) {
        setMode(value.scope); setApplicationId(value.application_id ?? ''); current.current.applicationId = value.application_id ?? '';
        receive(value);
        if (value.application_id) await loadPair(signal, value.application_id, epoch, value.id);
      } else if (current.current.doc?.id === value.id) receive(value);
      else setNotice('A result arrived after editing began. Your local document has not been replaced; reopen the result after saving or discarding.');
      headingRef.current?.focus();
    });
  }, [externalDocumentId, externalRequestKey, busy, request, receive, run, loadPair]);

  function changeMode(value: 'base' | 'tailored') {
    if (locked || dirty) return;
    selection.current++; setMode(value); setDoc(null); setHistory(null); setPreview(null); setContext(null); setError(''); setNotice('');
    if (value === 'base') void run(signal => loadBase(signal, selection.current));
  }
  function selectApplication(id: string) {
    if (locked || dirty) return;
    const epoch = ++selection.current; setApplicationId(id); current.current.applicationId = id;
    setDoc(null); setHistory(null); setPreview(null); setPair(null); setContext(null); setError('');
    if (id) void run(signal => loadPair(signal, id, epoch));
  }
  function loadApplications(cursor?: string) {
    void run(async signal => {
      const value = await request<ApplicationPage>(signal, `/applications${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`);
      setApplications(old => cursor ? [...old.filter(item => !value.items.some(next => next.id === item.id)), ...value.items] : value.items); setApplicationCursor(value.next_cursor);
    });
  }
  function edit(change: (body: DocumentBody) => void) {
    if (!doc || locked || preview) return;
    const next = copyBody(buffer ?? doc.current_revision.body); change(next);
    const value = sameBody(next, doc.current_revision.body) ? null : next;
    current.current.buffer = value; current.current.dirty = value !== null || reconcile;
    setBuffer(value); setReviewedKeys([]); setRebaseConfirmed(false); setNotice('Unsaved document changes. Save explicitly to create a revision.');
  }
  function startBase() {
    if (!profile || !profileId || !template || dirty || locked) return;
    const epoch = selection.current;
    void run(async signal => {
      const body: BaseResumeRequest = { profile_revision_id: profileId, template_version: template };
      const value = await request<Workflow>(signal, '/profile/base-resume', 'POST', body, profile.version, keyFor('/profile/base-resume', body, profile.version));
      mutation.current = null; workflowSelection.current = epoch; setWorkflow(value); setMonitorError(''); setNotice('Base composition accepted. Existing words remain until the saved result is loaded. No inference is requested.');
    });
  }
  function previewContext() {
    if (!profile || !applicationId || !template || locked || dirty) return;
    const epoch = selection.current;
    void run(async signal => {
      const query = new URLSearchParams({ profile_revision_id: profileId, template_version: template, tone, language: 'en' });
      const value = await request<GenerationContext>(signal, `${appPath(applicationId)}/generation-context?${query}`);
      if (epoch !== selection.current || current.current.profile?.version !== value.profile_version) { setNotice('Profile or selection changed. Preview the current generation context again.'); return; }
      setContext(value); setContextConfirmed(false);
    });
  }
  function startPair() {
    if (!context || !contextConfirmed || !profile || context.generation_route.status !== 'qualified' || dirty || locked || application?.archived_at) return;
    const epoch = selection.current;
    void run(async signal => {
      const body: GenerationRequest = { profile_revision_id: context.profile_revision_id, requirements_revision_id: context.requirements_revision_id, template_version: context.template_version, tone: context.tone, language: context.language, context_sha256: context.context_sha256 };
      const path = `${appPath(context.application_id)}/generation`;
      const value = await request<Workflow>(signal, path, 'POST', body, context.application_version, keyFor(path, body, context.application_version));
      mutation.current = null; workflowSelection.current = epoch; setWorkflow(value); setMonitorError(''); setContextConfirmed(false); setNotice('Tailored pair task accepted. Acceptance is not publication or factual approval.');
    });
  }
  function save() {
    if (!doc || !buffer || locked || preview || reconcile) return;
    const documentId = doc.id;
    void run(async signal => {
      const path = docPath(documentId); const body: DocumentSaveRequest = { base_revision_id: doc.current_revision.id, body: buffer, reason: 'Owner edited document text' };
      try {
        const value = await request<DocumentRecord>(signal, path, 'PUT', body, doc.version, keyFor(path, body, doc.version));
        mutation.current = null; receive(value, true); setNotice(`Saved document revision ${value.current_revision.revision_no}. Review must match this saved revision.`);
      } catch (failure) { if (!aborted(failure)) { setReconcile(true); setRebaseConfirmed(false); } throw failure; }
    });
  }
  function compare() {
    if (!doc) return;
    const epoch = selection.current;
    void run(async signal => {
      const value = await request<DocumentRecord>(signal, docPath(doc.id));
      if (epoch !== selection.current) return;
      setComparison(value); setReconcile(true); setRebaseConfirmed(false); setNotice('Current server revision loaded for comparison. Your editor has not changed.');
    });
  }
  function rebase() {
    if (!doc || !comparison || !rebaseConfirmed || locked) return;
    const merged = buffer ? rebaseBody(doc.current_revision.body, buffer, comparison.current_revision.body) : null;
    if (buffer && !merged) { setError('A locally edited block was removed on the server. Copy your attempted text, then explicitly replace the editor with the server revision and edit again. Nothing has been saved.'); return; }
    receive(comparison, true); const nextBuffer = merged && !sameBody(merged, comparison.current_revision.body) ? merged : null;
    setBuffer(nextBuffer); current.current.buffer = nextBuffer; current.current.dirty = nextBuffer !== null;
    setNotice('Compared server revision is now the save precondition. Your changed fields are retained; inspect them and explicitly save again if any changes remain.');
  }
  function historyPage(before?: number) {
    if (!doc) return; const epoch = selection.current;
    void run(async signal => {
      const value = await request<DocumentRevisionPage>(signal, `${docPath(doc.id)}/revisions?limit=20${before === undefined ? '' : `&before=${before}`}`);
      if (epoch === selection.current) setHistory(previous => before !== undefined && previous ? { items: [...previous.items, ...value.items], next_cursor: value.next_cursor } : value);
    });
  }
  function historyPreview(id: string) {
    if (!doc || dirty || locked) return; const epoch = selection.current;
    void run(async signal => { const value = await request<DocumentRevision>(signal, `${docPath(doc.id)}/revisions/${encodeURIComponent(id)}`); if (epoch === selection.current) { setPreview(value); setRestoreConfirmed(false); setReviewedKeys([]); setNotice(`Historical revision ${value.revision_no}. Viewing does not change the current document.`); } });
  }
  function restore() {
    if (!doc || !preview || !restoreConfirmed || locked || dirty) return;
    void run(async signal => {
      const path = `${docPath(doc.id)}/restore`; const body: DocumentRestoreRequest = { revision_id: preview.id, reason: 'Owner explicitly restored historical document revision' };
      try {
        const value = await request<DocumentRecord>(signal, path, 'POST', body, doc.version, keyFor(path, body, doc.version));
        mutation.current = null; receive(value, true); setNotice(`Restored as new revision ${value.current_revision.revision_no}. Displaced history and frozen source context are retained. Review again before PDF.`);
      } catch (failure) {
        if (!aborted(failure)) { setRestoreConfirmed(false); setReconcile(true); setNotice('Return to the current saved revision and compare/reconfirm its version before trying restoration again. Historical content is unchanged.'); }
        throw failure;
      }
    });
  }
  function review() {
    if (!doc || locked || dirty || preview || issues.length || !allReviewed) return;
    void run(async signal => {
      const path = `${docPath(doc.id)}/review`; const body: DocumentReviewRequest = { revision_id: doc.current_revision.id, decision: 'accepted', content_sha256: doc.current_revision.content_sha256, reviewed_block_keys: factualKeys };
      try {
        const value = await request<DocumentRecord>(signal, path, 'POST', body, doc.version, keyFor(path, body, doc.version));
        mutation.current = null; receive(value, true); setNotice('Review saved for this exact revision and content hash. It does not independently verify credentials.');
      } catch (failure) {
        if (failure instanceof ApiError && failure.status === 412) { setReviewedKeys([]); setReconcile(true); }
        throw failure;
      }
    });
  }
  function renderPdf() {
    if (!doc || locked || dirty || preview || issues.length || !doc.reviewed) return;
    const epoch = selection.current;
    void run(async signal => {
      const path = `${docPath(doc.id)}/renders`; const body: DocumentRenderRequest = { revision_id: doc.current_revision.id, content_sha256: doc.current_revision.content_sha256, format: 'pdf' };
      const value = await request<Workflow>(signal, path, 'POST', body, doc.version, keyFor(path, body, doc.version));
      mutation.current = null; workflowSelection.current = epoch; setWorkflow(value); setMonitorError(''); setNotice(`PDF task accepted for saved revision ${doc.current_revision.revision_no}. Nothing was sent.`);
    });
  }
  function previewSection() {
    if (!doc || doc.scope !== 'tailored' || !sectionKey || locked || dirty || preview || application?.archived_at) return;
    const epoch = selection.current; const profileVersion = profile?.version;
    void run(async signal => {
      setSectionContext(null); setSectionConfirmed(false);
      const query = new URLSearchParams({ section_key: sectionKey, tone: sectionTone });
      const value = await request<SectionRegenerationContext>(signal, `${docPath(doc.id)}/regeneration-context?${query}`);
      const live = current.current;
      if (epoch !== selection.current || live.dirty || live.profile?.version !== profileVersion || live.doc?.id !== value.document_id ||
        live.doc.version !== value.document_version || live.doc.current_revision.id !== value.revision_id || live.doc.current_revision.content_sha256 !== value.content_sha256 ||
        value.section_key !== sectionKey || value.tone !== sectionTone) {
        setNotice('The saved target or professional context changed. Preview this section again; no task was requested.'); return;
      }
      setSectionContext(value);
    });
  }
  function requestSection() {
    if (!doc || doc.scope !== 'tailored' || !sectionContext || !sectionConfirmed || sectionContext.generation_route.status !== 'qualified' ||
      locked || dirty || preview || application?.archived_at || sectionContext.document_id !== doc.id || sectionContext.document_version !== doc.version ||
      sectionContext.revision_id !== doc.current_revision.id || sectionContext.content_sha256 !== doc.current_revision.content_sha256 ||
      sectionContext.section_key !== sectionKey || sectionContext.tone !== sectionTone) return;
    const epoch = selection.current;
    void run(async signal => {
      const path = `${docPath(doc.id)}/regeneration-proposals`;
      const body: SectionRegenerationRequest = { revision_id: sectionContext.revision_id, content_sha256: sectionContext.content_sha256, section_key: sectionContext.section_key, tone: sectionContext.tone, context_sha256: sectionContext.context_sha256 };
      try {
        const value = await request<Workflow>(signal, path, 'POST', body, sectionContext.document_version, keyFor(path, body, sectionContext.document_version));
        mutation.current = null; workflowSelection.current = epoch; setWorkflow(value); setMonitorError(''); setSectionConfirmed(false); setApplyConfirmed(false);
        setNotice('Section proposal task accepted. Completion will store a separate proposal, not change this document or approve its facts.');
      } catch (failure) {
        if (!aborted(failure)) {
          setSectionConfirmed(false);
          if (failure instanceof ApiError && failure.status === 412) { setReconcile(true); setRebaseConfirmed(false); }
          if (failure instanceof ApiError && failure.code === 'regeneration_context_changed') setSectionContext(null);
        }
        throw failure;
      }
    });
  }
  function proposalPage(before?: string) {
    if (!doc || doc.scope !== 'tailored' || locked) return;
    const epoch = selection.current; const documentId = doc.id;
    void run(async signal => {
      const page = await request<SectionProposalPage>(signal, `${docPath(documentId)}/regeneration-proposals?limit=20${before ? `&before=${encodeURIComponent(before)}` : ''}`);
      if (epoch !== selection.current || current.current.doc?.id !== documentId) return;
      setProposals(previous => before && previous ? { items: [...previous.items.filter(item => !page.items.some(next => next.id === item.id)), ...page.items], next_cursor: page.next_cursor } : page);
      setApplyConfirmed(false);
      setProposal(previous => previous ? page.items.find(item => item.id === previous.id) ?? previous : null);
    });
  }
  function openProposal(id: string) {
    if (!doc || doc.scope !== 'tailored' || locked) return;
    const epoch = selection.current; const documentId = doc.id;
    void run(async signal => {
      const value = await request<SectionProposal>(signal, `${docPath(documentId)}/regeneration-proposals/${encodeURIComponent(id)}`);
      if (epoch !== selection.current || current.current.doc?.id !== documentId) return;
      setProposal(value); setApplyConfirmed(false); setNotice('Stored proposal loaded for comparison. The saved document and any local edits are unchanged.');
    });
  }
  function applyProposal() {
    if (!doc || doc.scope !== 'tailored' || !proposal || !proposal.applicable || !proposalMatches(proposal, doc) || !applyConfirmed || locked || dirty || preview) return;
    const epoch = selection.current; const documentId = doc.id;
    void run(async signal => {
      const path = `${docPath(documentId)}/regeneration-proposals/${encodeURIComponent(proposal.id)}/apply`;
      const body: SectionProposalApplyRequest = { revision_id: proposal.target_revision_id, content_sha256: proposal.target_content_sha256, reason: 'Owner explicitly applied a compared model section proposal as an editable draft' };
      try {
        const value = await request<DocumentRecord>(signal, path, 'POST', body, proposal.target_document_version, keyFor(path, body, proposal.target_document_version));
        mutation.current = null;
        if (epoch !== selection.current || current.current.doc?.id !== documentId) return;
        receive(value); setProposal(previous => previous ? { ...previous, applicable: false } : null); setApplyConfirmed(false);
        setProposals(null); setNotice('Applied the selected section as a new draft revision. Other sections, frame and history are retained. Inspect blockers and review this exact revision before PDF. Nothing was sent.');
      } catch (failure) {
        if (!aborted(failure) && epoch === selection.current && current.current.doc?.id === documentId) {
          setApplyConfirmed(false); setReconcile(true); setRebaseConfirmed(false);
          setNotice('Proposal application was not confirmed. Your displayed text and local edits are retained. Compare the current server revision and explicitly reconfirm; no automatic overwrite or retry.');
        }
        throw failure;
      }
    });
  }

  const workflowId = workflow?.id; const workflowState = workflow?.state;
  useEffect(() => {
    if (!workflowId || !workflowState || (terminal(workflowState) && processedWorkflow.current === workflowId && !monitorError)) return;
    let stopped = false; let timer: number | undefined; const controller = new AbortController(); const pending = controllers.current; pending.add(controller);
    const epoch = workflowSelection.current;
    let completedWorkflow: Workflow | null = null;
    const poll = async () => {
      if (stopped) return;
      if (document.hidden) { timer = window.setTimeout(() => { void poll(); }, 1000); return; }
      try {
        const value = await request<Workflow>(controller.signal, `/workflows/${encodeURIComponent(workflowId)}`);
        if (stopped) return;
        if (terminal(value.state)) {
          completedWorkflow = value;
          if (value.state === 'succeeded') {
            if (value.mode === 'section_regeneration' || value.result?.proposal_id) {
              const result = value.result;
              if (!result?.document_id || !result.proposal_id || !result.target_revision_id || !result.section_key || !result.model_run_id) throw new Error('missing_section_proposal_result');
              const stored = await request<SectionProposal>(controller.signal, `${docPath(result.document_id)}/regeneration-proposals/${encodeURIComponent(result.proposal_id)}`);
              if (stored.id !== result.proposal_id || stored.document_id !== result.document_id || stored.target_revision_id !== result.target_revision_id || stored.section_key !== result.section_key) throw new Error('invalid_section_proposal_result');
              if (epoch === selection.current && current.current.doc?.id === stored.document_id) {
                setProposal(stored); setApplyConfirmed(false);
                setProposals(previous => previous ? { ...previous, items: [stored, ...previous.items.filter(item => item.id !== stored.id)] } : null);
                setNotice('Section proposal stored for comparison. Saved wording and local edits are unchanged. Apply explicitly only after comparing the original target and proposed text.');
              } else setNotice('A section proposal was stored for an earlier selection. Your current document and edits are unchanged. Open that document’s saved section proposals to compare it.');
            } else if (value.result?.application_id) {
              const members = value.result.documents;
              if (!members || members.length !== 2 || new Set(members.map(item => item.kind)).size !== 2 || !members.some(item => item.kind === 'resume') || !members.some(item => item.kind === 'cover_letter')) throw new Error('incomplete_pair');
              await loadPair(controller.signal, value.result.application_id, epoch, current.current.doc?.id, members);
            } else if (value.result?.document_id) {
              const saved = await request<DocumentRecord>(controller.signal, docPath(value.result.document_id));
              if (epoch === selection.current) receive(saved);
              if (value.result.artifact_id && !saved.artifacts.some(artifact => artifact.id === value.result?.artifact_id && artifact.revision_id === saved.current_revision.id)) {
                if (!stopped) setNotice('The PDF task completed, but no matching artifact is available for the displayed current revision. No download success is claimed. Reload the saved revision or inspect its history.');
                processedWorkflow.current = value.id; setWorkflow(value); setMonitorError(''); return;
              }
            } else throw new Error('missing_document_result');
            if (!stopped && value.mode !== 'section_regeneration' && !value.result?.proposal_id) setNotice(value.result?.artifact_id ? 'PDF created for the saved revision. Download appears only if its artifact belongs to the displayed revision. Nothing was sent.' : 'Saved document result loaded. Review its sources and blockers; task success is not factual approval.');
          }
          if (!stopped) { processedWorkflow.current = value.id; setWorkflow(value); setMonitorError(''); }
          return;
        }
        setWorkflow(value); setMonitorError('');
      } catch (failure) {
        if (stopped || aborted(failure)) return;
        if (completedWorkflow) setWorkflow(completedWorkflow);
        setMonitorError(failure instanceof Error && failure.message === 'incomplete_pair' ? 'The task returned an incomplete pair. Existing documents are unchanged; no complete-pair success is being shown.' : `Task status/result could not be loaded. Existing documents are unchanged. ${errorText(failure)}`);
      }
      if (!stopped) timer = window.setTimeout(() => { void poll(); }, 2000);
    };
    timer = window.setTimeout(() => { void poll(); }, 1000);
    return () => { stopped = true; controller.abort(); pending.delete(controller); clearTimeout(timer); };
  }, [request, workflowId, workflowState, pollToken, receive, loadPair, monitorError]);

  const displayed = buffer ?? doc?.current_revision.body;
  const route = context?.generation_route ?? pair?.generation_route;
  return <section className="documents document-panel" aria-labelledby="documents-heading">
    <header className="documents-intro"><h2 id="documents-heading" ref={headingRef} tabIndex={-1}>Documents</h2>
      <p>Prepare your résumé, review the wording and export a saved revision. Nothing here sends an application.</p>
    </header>
    <div ref={errorRef} className="error" role="alert" tabIndex={-1} hidden={!error}>{error}</div><p className="status" role="status">{notice}</p>
    {defaultsError && <p className="error">{defaultsError}</p>}
    <fieldset className="document-mode" disabled={locked || dirty}><legend>Document workspace</legend>
      <div className="document-mode-options">
        <div><button className="secondary" aria-pressed={mode === 'base'} aria-describedby="document-base-description" onClick={() => changeMode('base')}>Base résumé</button><p id="document-base-description">Start with your accepted profile facts. No model needed.</p></div>
        <div><button className="secondary" aria-pressed={mode === 'tailored'} aria-describedby="document-tailored-description" onClick={() => changeMode('tailored')}>Tracked application documents</button><p id="document-tailored-description">Optional job-specific résumé and cover letter, with explicit model authorization.</p></div>
      </div>
      {dirty && <p className="hint">Save or discard your document changes before switching workspaces.</p>}
    </fieldset>
    <fieldset className="document-compose" disabled={locked || dirty}><legend>{mode === 'base' ? 'Profile-only composition' : 'Tailored pair preparation'}</legend>
      <div className="document-compose-fields">
      <div>
      <label>Saved profile revision<select value={profileId} onChange={event => setSelectedProfile(event.target.value === profile?.current_revision.id ? '' : event.target.value)}><option value={profile?.current_revision.id ?? ''}>{profile ? `Current revision ${profile.current_revision.revision_no}` : 'Load and save your profile first'}</option>{profileRevisions.filter(item => item.id !== profile?.current_revision.id).map(item => <option key={item.id} value={item.id}>Revision {item.revision_no} · {item.created_at}</option>)}</select></label>
      <button className="secondary" onClick={() => { void run(async signal => { const page = await request<RevisionPage>(signal, `/profile/revisions?limit=20${profileCursor === null ? '' : `&before=${profileCursor}`}`); setProfileRevisions(old => [...old.filter(item => !page.items.some(next => next.id === item.id)), ...page.items]); setProfileCursor(page.next_cursor); }); }}>Load {profileRevisions.length && profileCursor !== null ? 'more ' : ''}profile revisions</button>
      </div><div>
      <label>Built-in presentation<select value={template} onChange={event => { choicesInitialized.current = true; setTemplate(event.target.value); }}><option value="" disabled>{templates.length ? 'Choose a template' : 'Templates unavailable — reload to retry'}</option>{templates.map(item => <option key={item.version} value={item.version}>{item.label} · English · {item.paper}</option>)}</select></label>
      {!templates.length && <button className="secondary" onClick={() => { void run(async signal => { const value = await request<{ items: DocumentTemplate[] }>(signal, '/document-templates'); setTemplates(value.items); }); }}>Reload built-in templates</button>}
      </div></div>
      <details className="document-secondary"><summary>Presentation and frozen-input details</summary><p className="hint">Current operation choices below are separate from remembered defaults. English only. No translation or user-authored HTML/CSS templates. Existing revisions retain their original template and printable contact frame.</p></details>
      {mode === 'base' ? <><p className="document-mode-note">Create a model-free résumé from the selected saved profile. No posting, application or cover letter is needed.</p><details className="document-secondary"><summary>How base composition works</summary><p>This résumé copies accepted facts from the selected profile snapshot on this installation. No posting, application, letter, model or external provider is needed. No inference or ModelRun is created. An empty usable profile cannot produce a résumé.</p></details><div className="actions"><button disabled={!profile || !profileId || !template} onClick={startBase}>{doc ? 'Rebuild base from selected profile' : 'Create base résumé'}</button><button className="secondary" onClick={() => { void run(signal => loadBase(signal, selection.current)); }}>Reload saved base résumé</button></div></> : <>
        <div className="actions"><button className="secondary" onClick={() => loadApplications()}>Reload tracked applications</button>{applicationCursor && <button className="secondary" onClick={() => loadApplications(applicationCursor)}>Load more tracked applications</button>}</div>
        <label>Tracked application<select value={applicationId} onChange={event => selectApplication(event.target.value)}><option value="">Select an existing tracked application</option>{applications.map(item => <option key={item.id} value={item.id}>{item.title} · {item.company} · attempt {item.attempt_no}{item.archived_at ? ' · archived (read-only generation)' : ''}</option>)}</select></label>
        {!applications.length && <p>No tracked applications loaded. Begin tracking explicitly in Tracking; generation never creates an application.</p>}
        {applicationId && <><button className="secondary" onClick={() => { void run(signal => loadPair(signal, applicationId, selection.current, doc?.id)); }}>Reload application documents</button>{pair && <><p>{pair.items.some(item => item.kind === 'resume') && pair.items.some(item => item.kind === 'cover_letter') ? 'Saved résumé and cover letter available.' : pair.items.length ? 'The requested pair is incomplete. A missing member is not a successful pair.' : 'No saved pair. Existing application data remains available.'}</p><div className="actions">{(['resume', 'cover_letter'] as const).map(kind => { const member = pair.items.find(item => item.kind === kind); return <button key={kind} className="secondary" disabled={!member} aria-pressed={doc?.id === member?.id} onClick={() => { if (member) { selection.current++; receive(member, true); } }}>{kind === 'resume' ? 'Résumé' : 'Cover letter'}{!member ? ' unavailable' : ''}</button>; })}</div></>}
          {route && <p className="disclosure"><strong>Generation route: {route.status}.</strong> {route.reason}</p>}{pair && <ClaimJsonRates rates={pair.claim_json_rates} />}{!pair?.requirements_revision_id && <p>Reviewed, current, nonempty requirements are required. Review them in the posting workspace before preparing a pair.</p>}{application?.archived_at && <p>Archived applications remain readable. Generation is unavailable.</p>}
          <label>Writing tone<select value={tone} onChange={event => { choicesInitialized.current = true; setTone(event.target.value as 'professional' | 'concise'); }}><option value="professional">Professional</option><option value="concise">Concise</option></select></label><button className="secondary" disabled={!profile || !profileId || !template || !!application?.archived_at} onClick={previewContext}>Preview reviewed generation context</button>
          {context && <section className="disclosure"><h3>Exact minimized model projection</h3>
            <p>Destination from this operation’s route: <code>{typeof context.generation_route.identity?.endpoint === 'string' ? context.generation_route.identity.endpoint : 'No endpoint supplied — inspect route identity below'}</code>. Model: {typeof context.generation_route.identity?.model === 'string' ? context.generation_route.identity.model : 'Not supplied'}. A local endpoint is not a universal no-network guarantee; prose may still contain personal information, not anonymization. No hosted fallback.</p>
            <p>Included categories: posting title, organization and source text; reviewed requirements with importance and source spans; accepted professional claim text, structured fields and source references; evidence text and references, except that confidential or employer-owned evidence is sent as a reference without its content; selected tone and language. Dedicated identity/contact fields, private tracking notes/contacts/appointments/attachments, credentials, internal owner/workflow IDs and route/budget settings are excluded.</p>
            <p>Temporary task inputs are eligible for clearing 24 hours after terminal completion, with a daily sweep and no extension. Saved revisions remain until explicit, confirmed scoped erasure; local backups/downloads and external copies are independent.</p>
            <pre className="import-source">{JSON.stringify(context.model_projection, null, 2)}</pre>
            {context.withheld_evidence.length > 0 && <section aria-labelledby="withheld-evidence-title"><h4 id="withheld-evidence-title">Evidence withheld from the model</h4><p>These sources appear in the projection above only as <code>{'{"id": …, "withheld": true}'}</code>. Their content is not sent, based on the evidence record’s current classification. Reclassification takes effect immediately; no entry re-save or re-pin is needed. The claims citing them are still sent with their own text, and BonaFides checks them locally against the full source.</p>
              <ul>{context.withheld_evidence.map(item => <li key={item.evidence_revision_id}><p><strong>{item.source}</strong> · evidence revision <code>{item.evidence_revision_id}</code></p><p>Content withheld because the source is {item.reasons.map(reason => reason === 'employer_owned' ? 'owned by your employer' : 'confidential').join(' and ')}.</p>
                <p>Claims still sent: {item.claim_revision_ids.map(id => withheldClaimText(context.model_projection, id)).join('; ')}</p></li>)}</ul></section>}
            <details><summary>Trusted printable frame (not sent to the model)</summary><pre className="import-source">{JSON.stringify(context.frame, null, 2)}</pre></details><details><summary>Frozen selection and route identity</summary><p>Profile {context.profile_revision_id}; reviewed requirements {context.requirements_revision_id}; template {context.template_version}; context SHA-256 {context.context_sha256}.</p><pre className="import-source">{JSON.stringify(context.generation_route.identity, null, 2)}</pre></details><p>At most two inference POST attempts across retries/reclaims. Workflow success is not semantic or human qualification. A synthetic conformance route does not qualify real model weights.</p><label className="check"><input type="checkbox" checked={contextConfirmed} disabled={context.generation_route.status !== 'qualified'} onChange={event => setContextConfirmed(event.target.checked)} />I reviewed this exact projection, route, frozen inputs and privacy limitation for this one task</label><button disabled={!contextConfirmed || context.generation_route.status !== 'qualified' || !!application?.archived_at} onClick={startPair}>Generate résumé and cover letter pair</button></section>}
        </>}
      </>}
    </fieldset>
    {workflow && <section className="task" aria-live="polite"><h3>Document task: {workflow.state.replaceAll('_', ' ')}</h3><p>Attempt {workflow.attempt}{workflow.max_attempts === undefined ? '' : ` of ${workflow.max_attempts}`}. No estimated progress percentage.</p><small>Task {workflow.id}</small>{workflow.next_attempt_at && <p>Next scheduled attempt: {workflow.next_attempt_at}</p>}{workflow.last_error_code && <p>{workflow.last_error_code === 'render_text_unsupported' ? 'The saved text contains glyphs or text this built-in PDF renderer cannot preserve. Edit to supported text, or keep your portable owner data and ask the operator about font qualification. Save and review any changed revision before retrying. No PDF was created.' : `${TASK_REASONS[workflow.last_error_code] ?? 'The task failed.'} Failure code: ${workflow.last_error_code}. Existing saved documents remain available.`}</p>}{workflow.state === 'waiting_for_approval' && <p>This task is waiting for a separate approval; opening or reviewing a document does not grant provider access.</p>}{activeTask && <button className="secondary" disabled={busy || disabled} onClick={() => { void run(async signal => { const path = `/workflows/${encodeURIComponent(workflow.id)}/cancel`; const value = await request<Workflow>(signal, path, 'POST', {}, workflow.version, keyFor(path, {}, workflow.version)); mutation.current = null; setWorkflow(value); setNotice('Cancellation requested. The task may still be stopping.'); }); }}>Cancel document task</button>}{monitorError && <><p className="error">{monitorError}</p><button className="secondary" disabled={busy} onClick={() => setPollToken(value => value + 1)}>Reconnect document task</button></>}</section>}
    {doc && displayed ? <section className="document-current" aria-label="Selected saved document">
      <header className="document-current-heading"><h3>{doc.scope === 'base' ? 'Base résumé' : doc.kind === 'resume' ? 'Application résumé' : 'Application cover letter'} · saved revision {doc.current_revision.revision_no}</h3>
        <p className={`document-save-state${dirty || issues.length ? ' needs-attention' : ''}`} aria-label="Document state">{preview ? 'Historical preview · read-only' : reconcile ? 'Reconciliation required · local edits retained' : dirty ? 'Unsaved changes · review and export locked' : issues.length ? 'Saved · export blocked' : doc.reviewed ? 'Saved · exact revision reviewed' : 'Saved · review required'}</p>
      </header>
      {(doc.stale || upstreamChanged) && <aside className="disclosure"><strong>Outdated frozen inputs.</strong> {doc.stale_reason ?? 'upstream_profile_revision_changed'}. Profile/contact updates do not change this wording, printable frame, history or existing PDF. Rebuild is explicit.</aside>}
      {preview ? <section className="document-history-preview"><h3>Historical revision {preview.revision_no} — read-only</h3><p>Viewing does not change the current document. Restore copies this historical frozen context into a new revision, not the newest profile.</p><ReadonlyBody revision={preview} /><details><summary>Historical provenance and validation issues</summary><pre className="import-source">{JSON.stringify({ provenance: preview.provenance_snapshot, issues: preview.validation_issues }, null, 2)}</pre></details><button className="secondary" disabled={busy} onClick={() => { setPreview(null); setRestoreConfirmed(false); }}>Return to current saved revision</button><label className="check"><input type="checkbox" disabled={locked || dirty} checked={restoreConfirmed} onChange={event => setRestoreConfirmed(event.target.checked)} />Restore this historical revision as a new current revision, retaining displaced history</label><button disabled={locked || dirty || !restoreConfirmed} onClick={restore}>Restore historical revision</button>{reconcile && <p>Return to the current revision, compare the server version and reconfirm before restoring. No history has been replaced.</p>}</section> : <>
        {issues.length > 0 && <div className="error" tabIndex={-1}><strong>Export blocked: unresolved factual or source issues.</strong><ul>{issues.map((issue, index) => <li key={`${issue.path}-${issue.code}-${index}`}><a href="#/documents" onClick={event => { event.preventDefault(); const target = document.getElementById(issueAnchor(issue.path, doc.current_revision.body, displayed)); target?.focus(); target?.scrollIntoView({ block: 'center' }); }}>{ISSUE_REASONS[issue.code] ?? `Unsupported (${issue.code}).`}</a>{issue.statement !== undefined && <> Statement: <q>{issue.statement}</q></>} <small>{issue.path} · {issue.code}</small></li>)}</ul><p>These issues belong to the saved revision. Unsaved corrections or removals must be saved and revalidated before review/export. Remove/reword unsupported assertions or add a genuinely accepted profile claim/evidence and explicitly rebuild. Human review cannot waive unsupported content. Conservative checks do not establish the meaning of paraphrases.</p></div>}
        <div className="actions"><button disabled={locked || !buffer || reconcile || !displayed.title.trim() || displayed.sections.some(section => !section.heading.trim() || section.blocks.some(block => !block.text.trim()))} onClick={save}>Save document revision</button><button className="secondary" disabled={locked} onClick={compare}>Compare current document revision</button>{dirty && <button className="secondary" disabled={locked} onClick={() => { setBuffer(null); setComparison(null); setReconcile(false); setReviewedKeys([]); mutation.current = null; setNotice('Local document edits discarded. Saved wording is unchanged.'); }}>Discard document changes</button>}</div>
        <article className="paper document-editor">
          <h3>{displayed.title}</h3>
          <label>Document title<input id="document-saved-title" maxLength={160} value={displayed.title} disabled={locked} onChange={event => edit(body => { body.title = event.target.value; })} /></label>
          {displayed.sections.map(section => <section key={section.key} id={`document-section-${section.key}`} tabIndex={-1}>
            <h4>{section.heading}</h4>
            <label>Section heading: {section.key}<input maxLength={120} disabled={locked} value={section.heading} onChange={event => edit(body => { const target = body.sections.find(item => item.key === section.key); if (target) target.heading = event.target.value; })} /></label>
            {section.blocks.map(block => <div className="evidence-seam" id={`document-block-${block.block_key}`} key={block.block_key} tabIndex={-1}>
              <label>Block text: {block.block_key}<textarea maxLength={4000} value={block.text} disabled={locked} onChange={event => edit(body => { const target = body.sections.find(item => item.key === section.key)?.blocks.find(item => item.block_key === block.block_key); if (target) target.text = event.target.value; })} /></label>
              <small>{block.text.length}/4000 characters. {block.claim_kind}. Stable block {block.block_key}.</small>
              {block.claim_kind === 'factual' && <p className="claim-link">Linked claim: {linkedClaim(doc.current_revision.provenance_snapshot.facts ?? [], block.claim_revision_ids)}</p>}
              <details><summary>Statement sources ({block.claim_kind})</summary><Sources facts={doc.current_revision.provenance_snapshot.facts ?? []} claimIds={block.claim_revision_ids} evidenceIds={block.evidence_revision_ids} /></details>
              <button className="secondary" disabled={locked || displayed.sections.reduce((count, item) => count + item.blocks.length, 0) <= 1} onClick={() => edit(body => { const target = body.sections.find(item => item.key === section.key); if (target) target.blocks = target.blocks.filter(item => item.block_key !== block.block_key); body.sections = body.sections.filter(item => item.blocks.length > 0); })}>Remove block {block.block_key}</button>
              {block.claim_kind === 'factual' && <label className="check"><input type="checkbox" checked={reviewedKeys.includes(block.block_key)} disabled={locked || dirty || !!issues.length || doc.reviewed} onChange={event => setReviewedKeys(keys => event.target.checked ? [...keys, block.block_key] : keys.filter(key => key !== block.block_key))} />I checked block {block.block_key} against its frozen sources for this exact saved revision</label>}
            </div>)}
          </section>)}
        </article>
        <p className="hint">{dirty ? 'Unsaved/reconciliation pending. Review and PDF use saved revisions only.' : 'Edits are saved. Check each factual block against its frozen sources before accepting this revision.'} Source references and structural checks are not semantic proof or independently verified credentials. Meaning needs your review.</p>
        {comparison && <section className="comparison"><h4>Current server document revision {comparison.current_revision.revision_no}</h4><ReadonlyBody revision={comparison.current_revision} /><p>Your editor above is unchanged. Rebase preserves unrelated server fields and applies your changed fields only; inspect both versions before reconfirming. Any overlapping edits will use your displayed local wording.</p><label className="check"><input type="checkbox" checked={rebaseConfirmed} disabled={locked} onChange={event => setRebaseConfirmed(event.target.checked)} />I compared both revisions and reconfirm my changed fields against this server version</label><div className="actions"><button className="secondary" disabled={locked || !rebaseConfirmed} onClick={rebase}>Rebase my edits onto compared revision</button><button className="secondary" disabled={locked || !rebaseConfirmed} onClick={() => { receive(comparison, true); mutation.current = null; setNotice('Editor explicitly replaced with compared saved revision.'); }}>Replace editor with compared revision</button></div></section>}
        {reconcile && !comparison && <p className="disclosure">Your attempted text is retained. Compare the server revision, explicitly rebase/reconfirm, then save again. No automatic overwrite or retry.</p>}
        <section className="document-review" aria-label="Review and export">
          <div><h3>Review and export</h3><p>{dirty ? 'Save and revalidate your changes before reviewing or rendering a PDF.' : issues.length ? 'Resolve the factual or source issues shown above before review or export.' : doc.reviewed ? 'This saved revision is reviewed and ready to render.' : `Checked ${factualKeys.filter(key => reviewedKeys.includes(key)).length} of ${factualKeys.length} factual blocks. Accept only after checking the wording and sources.`}</p></div>
        <div className="actions"><button disabled={locked || dirty || doc.reviewed || !!issues.length || !allReviewed} onClick={review}>{doc.reviewed ? 'Exact revision reviewed' : 'Accept this exact revision'}</button><button disabled={locked || dirty || !doc.reviewed || !!issues.length} onClick={renderPdf}>Render reviewed PDF</button></div>
        </section>
        {doc.scope === 'tailored' && <SectionProposals document={doc} dirty={dirty} locked={locked} archived={!!application?.archived_at} sectionKey={sectionKey} tone={sectionTone} context={sectionContext} confirmed={sectionConfirmed} proposals={proposals} proposal={proposal} applyConfirmed={applyConfirmed} onSection={setSectionKey} onTone={value => { choicesInitialized.current = true; setSectionTone(value); }} onPreview={previewSection} onConfirm={setSectionConfirmed} onRequest={requestSection} onReload={proposalPage} onOpen={openProposal} onApplyConfirm={setApplyConfirmed} onApply={applyProposal} />}
        {doc.artifacts.filter(artifact => artifact.format === 'pdf' && artifact.revision_id === doc.current_revision.id).map(artifact => <p className="download" key={artifact.id}><a className="document-download" href={`/api/v1/artifacts/${encodeURIComponent(artifact.id)}`} download>Download rendered PDF for revision {doc.current_revision.revision_no}</a><span>Actual saved-revision artifact. Downloaded files contain personal data; store securely. Nothing was sent.</span></p>)}
      </>}
      <section><h3>Immutable document history</h3><button className="secondary" disabled={locked} onClick={() => historyPage()}>Load document history</button>{history && <><ol>{history.items.map(item => <li key={item.id}><button className="secondary" disabled={locked || dirty} onClick={() => historyPreview(item.id)}>Preview revision {item.revision_no} — {item.origin}</button><p>{item.created_at}{item.reason ? ` · ${item.reason}` : ''}</p></li>)}</ol>{history.next_cursor !== null && <button className="secondary" disabled={locked} onClick={() => historyPage(history.next_cursor!)}>Load older document revisions</button>}</>}</section>
      <details><summary>Frozen provenance, printable frame and validation limits</summary><p>These inputs belong to the saved revision, not your current profile or unsaved editor. Deterministic composition performs no inference. Human factual/semantic qualification is not established by this interface.</p><pre className="import-source">{JSON.stringify(doc.current_revision.provenance_snapshot, null, 2)}</pre></details>
      <details className="document-secondary"><summary>Saved revision identity and presentation</summary>
      <p>Current saved revision {doc.current_revision.id}. SHA-256 {doc.current_revision.content_sha256}.</p>
      <p>Origin: {doc.current_revision.origin}. Profile revision {doc.current_revision.profile_revision_id}. Template {doc.current_revision.template_version}. Saved {doc.current_revision.created_at}.</p>
      </details>
    </section> : <div className="empty"><p>{mode === 'base' ? 'No saved base résumé loaded. Save factual profile entries, choose a built-in template and create your résumé without a model.' : 'Select a tracked application and independently load its saved documents. Missing or failed generation is not an empty successful pair.'}</p><p>No invented résumé, letter or provider result is shown.</p></div>}
    <details className="document-secondary document-defaults"><summary>Remembered defaults and retention</summary>
    <fieldset disabled={locked}><legend>Remembered defaults for future work</legend>
      <p>These owner-local defaults initialize future workspace choices, not existing documents or this operation. Saving grants no integration permissions. English only; retention is fixed, not an adjustable expiry.</p>
      {defaults ? <p>Last loaded remembered defaults: {templates.find(item => item.version === defaults.template_version)?.label ?? defaults.template_version} · {templates.find(item => item.version === defaults.template_version)?.paper ?? 'paper metadata unavailable'} · {defaults.tone}. Version {defaults.version}.</p> : <p>Remembered defaults have not been loaded. Current choices are not confirmed saved defaults.</p>}
      <button className="secondary" onClick={() => { void run(signal => loadDefaults(signal)); }}>Reload remembered defaults</button>
      {defaults && <>
        <label>Remembered built-in presentation<select value={defaultsDraft.template_version} onChange={event => setDefaultsDraft(value => ({ ...value, template_version: event.target.value }))}>{!templates.length && <option value={defaultsDraft.template_version}>{defaultsDraft.template_version} · catalog unavailable</option>}{templates.map(item => <option key={item.version} value={item.version}>{item.label} · English · {item.paper}</option>)}</select></label>
        <label>Remembered writing tone<select value={defaultsDraft.tone} onChange={event => setDefaultsDraft(value => ({ ...value, tone: event.target.value as DocumentDefaults['tone'] }))}><option value="professional">Professional</option><option value="concise">Concise</option></select></label>
        {defaultsConflict && <p className="disclosure">The saved version changed. Reload remembered defaults, compare the last loaded values with your retained choices, then explicitly save again.</p>}
        <button disabled={defaultsConflict || !templates.some(item => item.version === defaultsDraft.template_version) || (defaultsDraft.template_version === defaults.template_version && defaultsDraft.tone === defaults.tone)} onClick={saveDefaults}>Save remembered defaults</button>
        <button className="secondary" disabled={dirty || defaultsConflict || !!defaultsError} onClick={() => { choicesInitialized.current = true; setTemplate(defaults.template_version); setTone(defaults.tone); setSectionTone(defaults.tone); setNotice('Last loaded remembered defaults selected for this operation. Saved documents are unchanged.'); }}>Use remembered defaults for current operation</button>
      </>}
      <p>Terminal task inputs are eligible for clearing after 24 hours, with a daily sweep and no extension. Temporary portable exports remain downloadable until their displayed expiry, currently 24 hours after creation; physical removal waits for the lifecycle controller. Committed document revisions do not expire automatically.</p>
    </fieldset>
    <p className="disclosure">Document history: saved revisions and committed proposals remain until explicit, confirmed scoped erasure in Privacy and portable data; rebuilding, changing defaults or archiving is not erasure. Local backups and downloaded files, and any external copies, have independent retention and must be handled separately.</p>
    </details>
  </section>;
}
