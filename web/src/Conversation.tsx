// SPDX-License-Identifier: MIT OR Apache-2.0
import { useCallback, useEffect, useRef, useState } from 'react';
import { Api, ApiError, errorText, type ApplicationConversation, type ConversationRequest, type ConversationUnit, type Workflow } from './api';

export interface ConversationDraft {
  message: string; documentIds?: string[]; unit?: string;
  requestKey?: { body: ConversationRequest; version: number; key: string } | undefined;
}
interface Props {
  api: Api; applicationId: string; disabled: boolean; archived: boolean; dirtyDocument: boolean;
  draft: ConversationDraft; onDraft: (draft: ConversationDraft) => void; onBusy: (busy: boolean) => void; onSaved: () => void; onRefreshDocuments: () => void;
}
function failureText(failure: unknown): string {
  if (failure instanceof ApiError && (failure.status === 412 || failure.code === 'conversation_context_changed')) {
    return 'Saved application context changed. Refresh the conversation, then send again explicitly. Your request and document text are retained.';
  }
  const reasons: Record<string, string> = {
    conversation_in_progress: 'Another refinement is unfinished. Refresh its status; your request is retained.',
    conversation_undo_conflict: 'Undo conflict: a produced unit or writing tone changed. Nothing was reversed. Compare the latest saved documents; your local text is retained.',
    conversation_already_undone: 'This exact reply was already reversed. No further changes were made.',
    conversation_not_undoable: 'This reply has no reversible document edits or is imported history.',
    conversation_route_unavailable: 'Refinement needs its own qualified refine_documents route. Other stage receipts do not qualify it.',
  };
  return failure instanceof ApiError ? reasons[failure.code] ?? errorText(failure) : errorText(failure);
}

const kindLabels: Record<string, string> = { resume: 'Résumé', cover_letter: 'Cover letter' };
const unitKey = (unit: ConversationUnit) => JSON.stringify([unit.document_id, unit.section_key, unit.block_key]);
export function Conversation({ api, applicationId, disabled, archived, dirtyDocument, draft, onDraft, onBusy, onSaved, onRefreshDocuments }: Props) {
  const [view, setView] = useState<ApplicationConversation | null>(null);
  const [workflows, setWorkflows] = useState<Record<string, Workflow>>({});
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [stale, setStale] = useState(false);
  const mounted = useRef(false);
  const lock = useRef(false);
  const active = useRef<AbortController | null>(null);
  const mutation = useRef<{ fingerprint: string; key: string } | null>(null);
  const callbacks = useRef({ draft, onDraft, onBusy, onSaved });
  callbacks.current = { draft, onDraft, onBusy, onSaved };
  const load = useCallback(async (signal: AbortSignal) => {
    const value = await api.getConversation(applicationId, signal);
    if (signal.aborted) return;
    const pending = value.turns.filter(turn => turn.workflow_run_id && !turn.historical && !turn.reply);
    try {
      const loaded = await Promise.all(pending.map(turn => api.request<Workflow>(`/workflows/${encodeURIComponent(turn.workflow_run_id!)}`, 'GET', undefined, undefined, false, signal)));
      if (!signal.aborted) { setView(value); setWorkflows(Object.fromEntries(loaded.map(item => [item.id, item]))); }
    } catch (failure) {
      if (!signal.aborted) { setView(value); setWorkflows({}); }
      throw failure;
    }
  }, [api, applicationId]);
  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    lock.current = true;
    void load(controller.signal).catch(failure => { if (!controller.signal.aborted) setError(failureText(failure)); }).finally(() => {
      if (!controller.signal.aborted) { lock.current = false; setBusy(false); }
    });
    return () => {
      mounted.current = false; controller.abort(); active.current?.abort(); callbacks.current.onBusy(false);
    };
  }, [load]);
  useEffect(() => {
    if (!view?.active_workflow_id || busy) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      void load(controller.signal).catch(failure => { if (!controller.signal.aborted) setError(`Status could not be confirmed. ${failureText(failure)}`); });
    }, 2000);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [view, busy, load]);
  async function run(action: (signal: AbortSignal) => Promise<void>, sending = false) {
    if (lock.current) return;
    const controller = new AbortController();
    active.current = controller; lock.current = true; setBusy(true); callbacks.current.onBusy(true); setError('');
    try { await action(controller.signal); }
    catch (failure) {
      if (mounted.current && !controller.signal.aborted) {
        setError(failureText(failure));
        if (sending && failure instanceof ApiError && failure.status >= 400 && failure.status < 500 &&
            ![401, 403, 408, 429].includes(failure.status) && !(failure.status === 409 && failure.code === api.writeBlock))
          callbacks.current.onDraft({ ...callbacks.current.draft, requestKey: undefined });
        if (failure instanceof ApiError && (failure.status === 412 || failure.code === 'conversation_context_changed')) setStale(true);
      }
    } finally {
      lock.current = false;
      if (mounted.current) { setBusy(false); callbacks.current.onBusy(false); }
    }
  }
  function refresh() {
    void run(async signal => {
      await load(signal);
      if (!signal.aborted) { setStale(false); setNotice('Conversation refreshed. Your unsent request and document text are unchanged.'); }
    });
  }
  const message = draft.message.trim();
  const length = [...message].length;
  const locked = disabled || busy;
  const selectedIds = draft.documentIds ?? (view?.route_status.status === 'qualified' ? view.documents.filter(item => item.units.length).map(item => item.document_id) : []);
  const writable = view?.documents.filter(item => selectedIds.includes(item.document_id) && item.units.length) ?? [];
  const units = writable.flatMap(item => item.units);
  const selectedUnit = units.find(item => unitKey(item) === draft.unit);
  const missingSelection = !!draft.unit && !selectedUnit;
  const blocked = !view || stale || (!draft.requestKey && (!message || length > 2000 || !!view.active_workflow_id || missingSelection || (!!writable.length && view.route_status.status !== 'qualified')));
  function send() {
    if (!view || locked || archived || blocked) return;
    const intent = callbacks.current.draft.requestKey ?? { body: {
      message, context_sha256: view.context_sha256, document_ids: writable.map(item => item.document_id),
      units: selectedUnit ? [{ document_id: selectedUnit.document_id, section_key: selectedUnit.section_key, block_key: selectedUnit.block_key }] : [],
    }, version: view.application_version, key: crypto.randomUUID() };
    callbacks.current.onDraft({ ...callbacks.current.draft, requestKey: intent });
    void run(async signal => {
      const { turn, workflow, application_version, context_sha256 } = await api.sendConversation(applicationId, intent.body, intent.version, intent.key, signal);
      if (signal.aborted) return;
      setView(previous => previous ? { ...previous, ...(application_version > previous.application_version ? { application_version, context_sha256 } : {}), turns: [...previous.turns.filter(item => item.id !== turn.id), turn], active_workflow_id: workflow && !['succeeded', 'permanent_failure', 'cancelled'].includes(workflow.state) ? workflow.id : previous.active_workflow_id } : previous);
      if (workflow) setWorkflows(previous => ({ ...previous, [workflow.id]: workflow }));
      callbacks.current.onSaved();
      callbacks.current.onDraft({ ...callbacks.current.draft, requestKey: undefined, message: callbacks.current.draft.message.trim() === intent.body.message ? '' : callbacks.current.draft.message });
      setNotice(workflow ? 'Refinement request saved. Refresh its recorded status. Saved changes never replace your local editor text.' : 'Local guidance saved. No model was called. No document changes were applied.');
    }, true);
  }
  function keyFor(path: string, version: number) {
    const fingerprint = JSON.stringify({ path, version });
    if (mutation.current?.fingerprint !== fingerprint) mutation.current = { fingerprint, key: crypto.randomUUID() };
    return mutation.current.key;
  }
  function control(workflow: Workflow, action: 'retry' | 'cancel') {
    if (locked || archived) return;
    const path = `/workflows/${encodeURIComponent(workflow.id)}/${action}`;
    void run(async signal => {
      const value = await api.request<Workflow>(path, 'POST', {}, workflow.version, keyFor(path, workflow.version), signal);
      if (signal.aborted) return;
      mutation.current = null; setWorkflows(previous => ({ ...previous, [value.id]: value }));
      setNotice(action === 'retry' ? 'Retry requested for the original saved request and frozen context.' : 'Cancellation requested. Local publication is fenced; provider stopping is not confirmed.');
      await load(signal);
    });
  }
  function undo(turnId: string) {
    if (!view || locked || archived) return;
    void run(async signal => {
      const result = await api.undoConversation(applicationId, turnId, view.application_version, keyFor(`undo/${turnId}`, view.application_version), signal);
      if (signal.aborted) return;
      mutation.current = null;
      setView(previous => previous ? { ...previous, turns: previous.turns.map(turn => turn.id === turnId ? result.turn : turn) } : previous);
      callbacks.current.onSaved();
      setNotice('This reply’s recorded edits were reversed together. Refresh saved documents explicitly; local editor text is unchanged.');
      await load(signal);
    });
  }
  return <section className="section-proposals" aria-label="Application conversation">
    <h3>Application conversation</h3>
    <p>Ask for checked changes to saved résumé and letter prose, or deselect documents for local guidance without a model call. Contacts, portraits, private notes and withheld evidence are not model targets. Nothing sends an application. Unsent requests stay only in this open workspace. A pending request is retried before sending changed text.</p>
    {error && <p className="error" role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    <button className="secondary" disabled={locked} onClick={refresh}>Refresh conversation</button>
    <button className="secondary" disabled={locked} onClick={onRefreshDocuments}>Refresh saved documents for comparison</button>
    {dirtyDocument && <p>Your unsaved document text is retained. Conversation requests use saved context only.</p>}
    <fieldset disabled={locked || archived}><legend>Conversation request scope</legend>
      {view && <>
        <p>Conversational refinement route: {view.route_status.status}. {view.route_status.reason} This stage requires its own refine_documents qualification.</p>
        {view.documents.map(item => <label className="check" key={item.document_id}><input type="checkbox" checked={selectedIds.includes(item.document_id)} disabled={!item.units.length} onChange={event => onDraft({ ...draft, documentIds: event.target.checked ? [...selectedIds, item.document_id] : selectedIds.filter(id => id !== item.document_id), unit: !event.target.checked && selectedUnit?.document_id === item.document_id ? '' : draft.unit ?? '' })} />{kindLabels[item.kind] ?? item.kind}{!item.units.length && ' — no eligible saved prose'}</label>)}
        <label>Optional saved part<select value={draft.unit ?? ''} onChange={event => onDraft({ ...draft, unit: event.target.value })}><option value="">All eligible parts in selected documents</option>{missingSelection && <option value={draft.unit}>Previously selected part is no longer eligible — choose again</option>}{units.map(unit => <option key={unitKey(unit)} value={unitKey(unit)}>{kindLabels[view.documents.find(item => item.document_id === unit.document_id)!.kind]} · {unit.heading} · {unit.block_key}</option>)}</select></label>
        {selectedUnit && <blockquote>{selectedUnit.text}</blockquote>}
        {!writable.length && <p>No writable document selected. Sending saves local guidance only and makes no model call.</p>}
      </>}
      <label>Conversation message<textarea value={draft.message} onChange={event => onDraft({ ...draft, message: event.target.value })} /></label>
      <p>{length} / 2000 characters after trimming.</p>
      <button disabled={blocked} onClick={send}>Send conversation message</button>
      <button className="secondary" disabled={!draft.message} onClick={() => onDraft({ ...draft, message: '' })}>Discard unsent message</button>
    </fieldset>
    {archived && <p>Archived application conversation is read-only.</p>}
    {view?.active_workflow_id && <p role="status">One refinement is unfinished. Wait for its recorded result or cancel it before another request.</p>}
    {view && <ol aria-label="Saved conversation">{[...view.turns].sort((a, b) => a.created_at.localeCompare(b.created_at)).map(turn => {
      const workflow = !turn.historical && turn.workflow_run_id ? workflows[turn.workflow_run_id] : undefined;
      return <li key={turn.id}><article aria-label={`Conversation reply ${turn.id}`}>
        <h4>Request · <time dateTime={turn.created_at}>{turn.created_at}</time></h4><p>{turn.request_text}</p>
        {turn.historical && <p>Imported history, shown for reference only.</p>}
        {turn.reply ? <><p>{turn.reply.text}</p>
          {turn.reply.declined ? <p>Request declined. No document text or tone changed.</p> : <p>{turn.reply.applied.length} saved text units applied; {turn.reply.tones.length} writing tone changes.</p>}
          {!turn.reply.applied.length && !turn.reply.tones.length && <p>No document changes were applied.</p>}
          {!!turn.reply.applied.length && <ul aria-label="Applied units">{turn.reply.applied.map(unit => <li key={unitKey(unit)}>{unit.document_id} · {unit.section_key} / {unit.block_key}<details><summary>Recorded change</summary><blockquote>{unit.before.text}</blockquote><p>{unit.after.text}</p></details></li>)}</ul>}
          {!!turn.reply.skipped.length && <ul aria-label="Skipped units">{turn.reply.skipped.map((unit, index) => <li key={`${unitKey(unit)}-${index}`}>Skipped {unit.document_id} · {unit.section_key} / {unit.block_key}: {unit.reason}. The target was not recreated or overwritten.</li>)}</ul>}
          {!!turn.reply.tones.length && <ul aria-label="Writing tone changes">{turn.reply.tones.map(tone => <li key={tone.document_id}>{tone.document_id}: {tone.before} → {tone.after}</li>)}</ul>}
          {!turn.undone_at && !turn.historical && turn.can_undo && !!(turn.reply.applied.length || turn.reply.tones.length) && <button className="secondary" disabled={locked || archived || !!view.active_workflow_id} onClick={() => undo(turn.id)}>Undo this reply’s document edits</button>}
        </> : <><p>No saved reply is available.</p>
          {turn.workflow_run_id && !turn.historical && <p>Task: {(workflow?.state ?? turn.workflow_state ?? 'status unavailable').replaceAll('_', ' ')}. No reply or document changes are claimed.</p>}
          {workflow?.last_error_code && <p className="error">Failure: {workflow.last_error_code}</p>}
          {workflow?.retryable && <button className="secondary" disabled={locked || archived} onClick={() => control(workflow, 'retry')}>Retry saved refinement request</button>}
          {workflow?.cancelable && <button className="secondary" disabled={locked || archived} onClick={() => control(workflow, 'cancel')}>Cancel refinement task</button>}
          {workflow?.cancellation.requested && <p>Cancellation requested; provider stopping is not confirmed.</p>}
        </>}
        {turn.undone_at && <p>Already reversed at {turn.undone_at}. This reply cannot be undone twice.</p>}
      </article></li>;
    })}</ol>}
  </section>;
}
