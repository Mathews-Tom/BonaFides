// SPDX-License-Identifier: MIT OR Apache-2.0
import { useEffect, useRef, useState } from 'react';
import { Api, ApiError, errorText, type ApplicationConversation, type ConversationRequest } from './api';

export interface ConversationDraft {
  message: string;
  requestKey?: { body: ConversationRequest; version: number; key: string };
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
          callbacks.current.onDraft({ message: callbacks.current.draft.message });
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
  const length = [...message].length;
  const locked = disabled || busy;
  function send() {
    if (!view || locked || archived || stale || (!draft.requestKey && (!message || length > 2000))) return;
    const intent = callbacks.current.draft.requestKey ?? { body: { message, context_sha256: view.context_sha256 }, version: view.application_version, key: crypto.randomUUID() };
    callbacks.current.onDraft({ ...callbacks.current.draft, requestKey: intent });
    void run(async signal => {
      const { turn, application_version, context_sha256 } = await api.sendConversation(applicationId, intent.body, intent.version, intent.key, signal);
      if (signal.aborted) return;
      setView(previous => previous ? { ...previous, ...(application_version > previous.application_version ? { application_version, context_sha256 } : {}), turns: [...previous.turns.filter(item => item.id !== turn.id), turn] } : previous);
      callbacks.current.onSaved();
      callbacks.current.onDraft({ message: callbacks.current.draft.message.trim() === intent.body.message ? '' : callbacks.current.draft.message });
      setNotice('Local guidance saved. No model was called. No document changes were applied.');
    }, true);
  }
  return <section className="section-proposals" aria-label="Application conversation">
    <h3>Application conversation</h3>
    <p>Save a request and local guidance for this application. This panel cannot edit documents or call a model. Unsent requests stay only in this open workspace. A pending request is retried before sending changed text.</p>
    {error && <p className="error" role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    <button className="secondary" disabled={locked} onClick={refresh}>Refresh conversation</button>
    {dirtyDocument && <p>Your unsaved document text is retained. Conversation requests use saved context only.</p>}
    <fieldset disabled={locked || archived}><legend>Local conversation request</legend>
      <label>Conversation message<textarea value={draft.message} onChange={event => onDraft({ ...draft, message: event.target.value })} /></label>
      <p>{length} / 2000 characters after trimming.</p>
      <button disabled={!view || stale || (!draft.requestKey && (!message || length > 2000))} onClick={send}>Send conversation message</button>
      <button className="secondary" disabled={!draft.message} onClick={() => onDraft({ ...draft, message: '' })}>Discard unsent message</button>
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
