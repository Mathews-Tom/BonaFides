// SPDX-License-Identifier: MIT OR Apache-2.0
import { useEffect, useRef, useState } from 'react';
import { Api, ApiError, errorText, type ApplicationConversation, type ConversationRequest } from './api';

export interface ConversationDraft {
  message: string;
  requestKey?: { fingerprint: string; key: string };
}
interface Props {
  api: Api; applicationId: string; disabled: boolean; archived: boolean; dirtyDocument: boolean;
  draft: ConversationDraft; onDraft: (draft: ConversationDraft) => void; onBusy: (busy: boolean) => void; onSaved: () => void;
}
function failureText(failure: unknown): string {
  if (failure instanceof ApiError && (failure.status === 412 || failure.code === 'conversation_context_changed')) {
    return 'Saved application context changed. Refresh the conversation, then send again explicitly. Your request and document text are retained.';
  }
  return errorText(failure);
}

export function Conversation({ api, applicationId, disabled, archived, dirtyDocument, draft, onDraft, onBusy, onSaved }: Props) {
  const [view, setView] = useState<ApplicationConversation | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [stale, setStale] = useState(false);
  const mounted = useRef(false);
  const lock = useRef(false);
  const active = useRef<AbortController | null>(null);
  const callbacks = useRef({ draft, onDraft, onBusy, onSaved });
  callbacks.current = { draft, onDraft, onBusy, onSaved };
  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    lock.current = true;
    void api.getConversation(applicationId, controller.signal).then(value => {
      if (!controller.signal.aborted) setView(value);
    }).catch(failure => { if (!controller.signal.aborted) setError(failureText(failure)); }).finally(() => {
      if (!controller.signal.aborted) { lock.current = false; setBusy(false); }
    });
    return () => {
      mounted.current = false; controller.abort(); active.current?.abort(); callbacks.current.onBusy(false);
    };
  }, [api, applicationId]);
  async function run(action: (signal: AbortSignal) => Promise<void>) {
    if (lock.current) return;
    const controller = new AbortController();
    active.current = controller; lock.current = true; setBusy(true); callbacks.current.onBusy(true); setError('');
    try { await action(controller.signal); }
    catch (failure) {
      if (mounted.current && !controller.signal.aborted) {
        setError(failureText(failure));
        if (failure instanceof ApiError && (failure.status === 412 || failure.code === 'conversation_context_changed')) setStale(true);
      }
    } finally {
      lock.current = false;
      if (mounted.current) { setBusy(false); callbacks.current.onBusy(false); }
    }
  }
  function refresh() {
    void run(async signal => {
      const value = await api.getConversation(applicationId, signal);
      if (signal.aborted) return;
      setView(value); setStale(false); setNotice('Conversation refreshed. Your unsent request and document text are unchanged.');
    });
  }
  const message = draft.message.trim();
  const locked = disabled || busy;
  function send() {
    if (!view || locked || archived || stale || !message || message.length > 2000) return;
    const body: ConversationRequest = { message, context_sha256: view.context_sha256 };
    const fingerprint = JSON.stringify({ applicationId, body, version: view.application_version });
    const saved = callbacks.current.draft.requestKey;
    const intent = saved?.fingerprint === fingerprint ? saved : { fingerprint, key: crypto.randomUUID() };
    callbacks.current.onDraft({ ...callbacks.current.draft, requestKey: intent });
    const original = draft.message;
    void run(async signal => {
      const { turn, application_version, context_sha256 } = await api.sendConversation(applicationId, body, view.application_version, intent.key, signal);
      if (signal.aborted) return;
      setView(previous => previous ? { ...previous, application_version, context_sha256, turns: [...previous.turns.filter(item => item.id !== turn.id), turn] } : previous);
      callbacks.current.onSaved();
      if (callbacks.current.draft.message === original) callbacks.current.onDraft({ message: '' });
      setNotice('Local guidance saved. No model was called. No document changes were applied.');
    });
  }
  return <section className="section-proposals" aria-label="Application conversation">
    <h3>Application conversation</h3>
    <p>Save a request and local guidance for this application. This panel cannot edit documents or call a model. Unsent requests stay only in this open workspace.</p>
    {error && <p className="error" role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    <button className="secondary" disabled={locked} onClick={refresh}>Refresh conversation</button>
    {dirtyDocument && <p>Your unsaved document text is retained. Conversation requests use saved context only.</p>}
    <fieldset disabled={locked || archived}><legend>Local conversation request</legend>
      <label>Conversation message<textarea maxLength={2000} value={draft.message} onChange={event => onDraft({ ...draft, message: event.target.value })} /></label>
      <p>{message.length} / 2000 characters after trimming.</p>
      <button disabled={!view || stale || !message || message.length > 2000} onClick={send}>Send conversation message</button>
      <button className="secondary" disabled={!draft.message} onClick={() => onDraft({ message: '' })}>Discard unsent message</button>
    </fieldset>
    {archived && <p>Archived application conversation is read-only.</p>}
    {view && <ol aria-label="Saved conversation">{[...view.turns].sort((a, b) => a.created_at.localeCompare(b.created_at)).map(turn =>
      <li key={turn.id}><article aria-label={`Conversation reply ${turn.id}`}>
        <h4>Request · <time dateTime={turn.created_at}>{turn.created_at}</time></h4><p>{turn.request_text}</p>
        {turn.historical && <p>Imported history, shown for reference only.</p>}
        {turn.reply ? <><p>{turn.reply.text}</p>{turn.reply.declined && <p>Request declined.</p>}</> : <p>No saved reply is available.</p>}
        {turn.undone_at && <p>Historical reversal recorded at {turn.undone_at}.</p>}
      </article></li>
    )}</ol>}
  </section>;
}
