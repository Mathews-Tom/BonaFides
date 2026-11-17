// SPDX-License-Identifier: MIT OR Apache-2.0
import { useCallback, useEffect, useRef, useState } from 'react';
import { Api, ApiError, errorText, terminal, type DocumentBody, type DocumentRecord, type EmailParts, type Workflow } from './api';
import { copyBody, DOCUMENT_LANGUAGES, documentLanguage, ISSUE_REASONS } from './document-state';

interface EmailDraft extends EmailParts { body: DocumentBody }
interface Props {
  api: Api; applicationId: string; disabled?: boolean; archived?: boolean;
  documents: DocumentRecord[]; onDirty: (dirty: boolean) => void; onBusy: (busy: boolean) => void;
}
const bodyText = (body: DocumentBody) => body.sections.flatMap(section => section.blocks.map(block => block.text)).join('\n\n');
const draftOf = (doc: DocumentRecord): EmailDraft => ({ recipient: '', subject: '', ...doc.current_revision.provenance_snapshot.email, body: copyBody(doc.current_revision.body) });
export function emailMailto(recipient: string, subject: string, body: string): string {
  const encode = (value: string) => encodeURIComponent(value).replace(/[!'()*]/g, char => `%${char.charCodeAt(0).toString(16).toUpperCase()}`);
  return `mailto:${encode(recipient).replace(/%40/g, '@')}?subject=${encode(subject)}&body=${encode(body.replace(/\r\n|\r|\n/g, '\r\n'))}`;
}

export function ApplicationEmail({ api, applicationId, documents, disabled = false, archived = false, onDirty, onBusy }: Props) {
  const [doc, setDoc] = useState<DocumentRecord | null>(null);
  const [draft, setDraft] = useState<EmailDraft | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [conflict, setConflict] = useState(false);
  const [comparison, setComparison] = useState<DocumentRecord | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [monitorError, setMonitorError] = useState('');
  const [pollToken, setPollToken] = useState(0);
  const [clock, setClock] = useState(Date.now);
  const current = useRef({ doc, draft, disabled, archived });
  current.current = { doc, draft, disabled, archived };
  const mounted = useRef(false);
  const locked = useRef(false);
  const controllers = useRef(new Set<AbortController>());
  const intent = useRef<{ fingerprint: string; key: string } | null>(null);
  const callbacks = useRef({ onDirty, onBusy }); callbacks.current = { onDirty, onBusy };
  const fields = useRef<Record<string, HTMLInputElement | HTMLTextAreaElement | null>>({});
  const active = !!workflow && !terminal(workflow.state);
  const displayed = draft ?? (doc ? draftOf(doc) : null);
  const coverLetter = documents.find(item => item.kind === 'cover_letter');
  const languageSource = coverLetter ?? doc;
  const language = languageSource ? documentLanguage(languageSource.current_revision) : null;
  const restoreId = doc?.current_revision.provenance_snapshot.email_restore_revision_id;
  const handoff = displayed ? emailMailto(displayed.recipient, displayed.subject, bodyText(displayed.body)) : '';
  const tooLong = handoff.length > 2000;
  const retryDue = workflow?.state === 'retryable_failure' && workflow.retryable && workflow.remaining_attempts > 0 &&
    Date.parse(workflow.deadline_at) > clock && (!workflow.next_attempt_at || Date.parse(workflow.next_attempt_at) <= clock) &&
    !workflow.cancel_requested_at && workflow.delivery_state !== 'unknown';

  useEffect(() => {
    mounted.current = true; const pending = controllers.current;
    return () => { mounted.current = false; for (const controller of pending) controller.abort(); callbacks.current.onDirty(false); callbacks.current.onBusy(false); };
  }, []);
  useEffect(() => { callbacks.current.onDirty(draft !== null); }, [draft]);
  useEffect(() => { callbacks.current.onBusy(busy || active); }, [busy, active]);

  const receive = useCallback((value: DocumentRecord, replace = false) => {
    if (value.kind !== 'application_email' || value.application_id !== applicationId) throw new Error('invalid_application_email');
    const live = current.current;
    if (live.doc && live.doc.version > value.version) return;
    if (live.draft && !replace && live.doc?.current_revision.id === value.current_revision.id) {
      current.current.doc = value; setDoc(value); setConflict(false); setComparison(null);
      setNotice('The saved email is unchanged. Your unsaved edits are kept; you can save them.'); return;
    }
    if (live.draft && !replace) { setComparison(value); setConflict(true); setNotice('A saved email is available. Your unsaved changes are still here. Compare before replacing them.'); return; }
    current.current.doc = value; current.current.draft = null;
    setDoc(value); setDraft(null); setConflict(false); setComparison(null); setLoaded(true); setConfirmed(false);
  }, [applicationId]);
  const run = useCallback(async (action: (signal: AbortSignal) => Promise<void>) => {
    if (locked.current) return;
    locked.current = true; setBusy(true); setError('');
    const controller = new AbortController(); controllers.current.add(controller);
    try { await action(controller.signal); }
    catch (failure) {
      if (!controller.signal.aborted && mounted.current) {
        if (failure instanceof ApiError && (failure.status === 412 || ['revision_mismatch', 'application_email_exists'].includes(failure.code))) {
          setConflict(true); setError('The saved email or application changed. Your text is still here. Reload email to compare; nothing was overwritten.');
        } else setError(errorText(failure));
      }
    } finally { controllers.current.delete(controller); locked.current = false; if (mounted.current) setBusy(false); }
  }, []);
  const load = useCallback(async (signal: AbortSignal) => {
    try { const value = await api.getApplicationEmail(applicationId, signal); if (!signal.aborted) receive(value); }
    catch (failure) {
      if (signal.aborted) return;
      if (failure instanceof ApiError && failure.code === 'application_email_not_found') { setLoaded(true); setConflict(false); }
      else throw failure;
    }
  }, [api, applicationId, receive]);
  useEffect(() => { void run(load); }, [run, load]);

  const workflowId = workflow?.id;
  useEffect(() => {
    if (!workflowId) return;
    let stopped = false; let timer: number | undefined;
    const controller = new AbortController(); const pending = controllers.current; pending.add(controller);
    const poll = async () => {
      if (document.hidden || locked.current) { timer = window.setTimeout(() => { void poll(); }, 1000); return; }
      try {
        const value = await api.request<Workflow>(`/workflows/${encodeURIComponent(workflowId)}`, 'GET', undefined, undefined, false, controller.signal);
        if (stopped) return;
        setClock(Date.now()); setMonitorError('');
        if (value.state === 'succeeded') {
          const saved = await api.getApplicationEmail(applicationId, controller.signal);
          if (stopped) return;
          receive(saved);
          if (!current.current.draft) setNotice(saved.current_revision.provenance_snapshot.email_restore_revision_id
            ? 'Rewrite saved. It may have replaced edits saved while writing. Undo email rewrite brings back the whole email saved just before completion.'
            : 'Email draft saved. Review every part before using it. Nothing was sent.');
        }
        setWorkflow(value);
        if (!terminal(value.state)) timer = window.setTimeout(() => { void poll(); }, 1000);
      } catch (failure) {
        if (!stopped) setMonitorError(`Email progress could not be checked. Your content is still here. ${errorText(failure)}`);
      }
    };
    void poll();
    return () => { stopped = true; controller.abort(); clearTimeout(timer); pending.delete(controller); };
  }, [api, applicationId, workflowId, pollToken, receive]);

  function keyFor(action: string, payload: unknown, version: number) {
    const fingerprint = JSON.stringify([action, payload, version]);
    if (intent.current?.fingerprint !== fingerprint) intent.current = { fingerprint, key: crypto.randomUUID() };
    return intent.current.key;
  }
  function start() {
    if (disabled || archived || busy || draft || conflict || active || (doc && !confirmed)) return;
    void run(async signal => {
      let value: Workflow;
      if (doc) value = await api.rewriteApplicationEmail(doc.id, doc.version, keyFor('rewrite', doc.id, doc.version), signal);
      else {
        const application = await api.request<{ version: number }>(`/applications/${encodeURIComponent(applicationId)}/documents`, 'GET', undefined, undefined, false, signal);
        if (signal.aborted) return;
        value = await api.draftApplicationEmail(applicationId, application.version, keyFor('draft', applicationId, application.version), signal);
      }
      if (signal.aborted) return;
      intent.current = null; setWorkflow(value); setPollToken(value => value + 1); setConfirmed(false);
      setNotice('Email writing requested. Existing words stay available while it runs.');
    });
  }
  function save() {
    if (!doc || !draft || disabled || busy || conflict) return;
    const snapshot = draft;
    const body = { base_revision_id: doc.current_revision.id, body: snapshot.body, email: { recipient: snapshot.recipient, subject: snapshot.subject }, reason: 'Owner edited application email' };
    void run(async signal => {
      const value = await api.saveApplicationEmail(doc.id, body, doc.version, keyFor('save', body, doc.version), signal);
      if (signal.aborted) return;
      intent.current = null; receive(value, true); setNotice('Email saved. Any earlier rewrite undo is no longer available. Nothing was sent.');
    });
  }
  function undo() {
    if (!doc || !restoreId || disabled || busy || active || draft || conflict) return;
    void run(async signal => {
      try {
        const value = await api.restoreApplicationEmail(doc.id, restoreId, doc.version, keyFor('undo', restoreId, doc.version), signal);
        if (signal.aborted) return;
        intent.current = null; receive(value, true); setNotice('The email from before the rewrite is restored. Nothing was sent.');
      } catch (failure) {
        if (!(failure instanceof ApiError) || (failure.status !== 412 && failure.code !== 'email_restore_conflict')) throw failure;
        const latest = await api.getApplicationEmail(applicationId, signal);
        if (signal.aborted) return;
        receive(latest, true); setNotice(''); intent.current = null;
        const latestRestoreId = latest.current_revision.provenance_snapshot.email_restore_revision_id;
        if (!latestRestoreId) setError("Undo isn't available because the email changed after the rewrite. Your current email has not been replaced.");
        else if (latest.current_revision.id !== doc.current_revision.id || latestRestoreId !== restoreId) {
          setError('The email was rewritten again. The newer email is shown. Undo email rewrite now restores the email from before that newer rewrite. Nothing was restored.');
        } else setError('The email status changed, but its words are unchanged. Nothing was restored. You can try Undo email rewrite again.');
      }
    });
  }
  function retry() {
    if (!workflow || disabled || archived || busy) return;
    if (workflow.state !== 'retryable_failure') { setWorkflow(null); start(); return; }
    if (!retryDue) return;
    void run(async signal => {
      const value = await api.request<Workflow>(`/workflows/${encodeURIComponent(workflow.id)}/retry`, 'POST', {}, workflow.version, keyFor('retry', workflow.id, workflow.version), signal);
      if (signal.aborted) return;
      intent.current = null; setWorkflow(value); setPollToken(value => value + 1);
    });
  }
  function edit(update: (value: EmailDraft) => void) {
    if (!displayed || disabled || busy) return;
    const value = { ...displayed, body: copyBody(displayed.body) }; update(value);
    current.current.draft = value; setDraft(value); setConfirmed(false);
  }
  async function copy(part: 'recipient' | 'subject' | 'body') {
    if (!displayed) return;
    try {
      if (!navigator.clipboard?.writeText) throw new Error('clipboard_unavailable');
      await navigator.clipboard.writeText(part === 'body' ? bodyText(displayed.body) : displayed[part]);
      if (mounted.current) { setError(''); setNotice(`Email ${part} copied.`); }
    } catch {
      if (mounted.current) { setNotice(''); fields.current[part]?.focus(); fields.current[part]?.select(); setError(`Clipboard copy failed. Select the email ${part} and copy it manually${part === 'body' ? ', one passage at a time' : ''}.`); }
    }
  }
  let passage = 0;
  return <section className="application-email disclosure" aria-labelledby="application-email-heading">
    <h3 id="application-email-heading">Application email</h3>
    <p>Prepare an email for your own mail app. Nothing here sends email, attaches files or marks an application sent.</p>
    {language && <p>Email language: <strong>{DOCUMENT_LANGUAGES.find(item => item.value === language)?.label}</strong>. Set by the application’s cover letter; owner-written text is not translated.</p>}
    {error && <p role="alert" className="error">{error}</p>}{notice && <p role="status" aria-label="Email notice">{notice}</p>}
    <div className="actions"><button className="secondary" disabled={busy} onClick={() => { void run(load); }}>Reload email</button></div>
    {!loaded && <p>Loading saved email…</p>}
    {loaded && !doc && <><p>No application email yet. Draft one after saving a cover letter. Writing needs a separately qualified email writing route.</p><button disabled={disabled || archived || busy || active || conflict || !documents.some(item => item.kind === 'cover_letter')} onClick={start}>Draft application email</button></>}
    {workflow && <div aria-label="Email writing progress" role="status">
      <p>Email writing: {workflow.state.replaceAll('_', ' ')}.</p>
      {['retryable_failure', 'permanent_failure', 'cancelled'].includes(workflow.state) && <><p>Writing did not finish. Your existing email is kept. {workflow.last_error_code && <>Reason: {workflow.last_error_code}.</>}</p><button disabled={disabled || archived || busy || (workflow.state === 'retryable_failure' && !retryDue) || (workflow.state !== 'retryable_failure' && !!doc && (!confirmed || !!draft || conflict))} onClick={retry}>Retry email writing</button>{workflow.state === 'retryable_failure' && !retryDue && <p>Retry is not available yet or the task limits are exhausted. {workflow.retry_block_reason}</p>}</>}
    </div>}
    {monitorError && <><p role="alert" className="error">{monitorError}</p><button className="secondary" disabled={busy} onClick={() => setPollToken(value => value + 1)}>Reconnect email progress</button></>}
    {displayed && doc && <>
      <p>{draft ? 'Unsaved email changes.' : `Saved email revision ${doc.current_revision.revision_no}.`}</p>
      <fieldset disabled={disabled || busy}><legend>Email details</legend>
        <label>Email recipient<input ref={node => { fields.current.recipient = node; }} value={displayed.recipient} maxLength={320} onChange={event => edit(value => { value.recipient = event.target.value; })} /></label>
        {!displayed.recipient && <p>No recipient yet. Type one or leave it empty; it is never invented.</p>}
        <label>Email subject<input ref={node => { fields.current.subject = node; }} value={displayed.subject} maxLength={500} onChange={event => edit(value => { value.subject = event.target.value; })} /></label>
        <p>Edit the body below, one passage at a time. Blank lines separate passages when copied.</p>
        {displayed.body.sections.map((section, si) => section.blocks.map((block, bi) => {
          const index = ++passage;
          return <label key={block.block_key}>{index === 1 ? 'Email body' : `Email body passage ${index}`}<textarea ref={node => { if (index === 1) fields.current.body = node; }} rows={4} maxLength={4000} value={block.text} onChange={event => edit(value => { value.body.sections[si]!.blocks[bi]!.text = event.target.value; })} /></label>;
        }))}
      </fieldset>
      {doc.current_revision.validation_issues.length > 0 && <div role="alert"><p>Check these saved-email wording problems before using it. Saving an edit checks it again.</p><ul>{doc.current_revision.validation_issues.map((issue, index) => <li key={index}>{ISSUE_REASONS[issue.code] ?? issue.code}{issue.statement && <>: {issue.statement}</>}</li>)}</ul></div>}
      <div className="actions"><button disabled={disabled || busy || !draft || conflict || !displayed.subject.trim() || displayed.body.sections.some(section => section.blocks.some(block => !block.text.trim()))} onClick={save}>Save email changes</button><button className="secondary" disabled={busy || !draft} onClick={() => { current.current.draft = null; setDraft(null); if (comparison) receive(comparison, true); else setConflict(false); }}>Discard email changes</button></div>
      {conflict && <p>Your email has not been overwritten. Reload email to compare the saved version; copy any local text you want to keep before replacing it.</p>}
      {comparison && <section aria-label="Saved email comparison"><h4>Saved email to compare</h4><p>{comparison.current_revision.provenance_snapshot.email?.recipient || 'No recipient yet.'}</p><p>{comparison.current_revision.provenance_snapshot.email?.subject}</p><pre>{bodyText(comparison.current_revision.body)}</pre><button disabled={busy} onClick={() => { receive(comparison, true); intent.current = null; }}>Use saved email instead of my changes</button></section>}
      <p id="email-rewrite-explanation">Rewrite replaces the subject and body, resets the subject to the application heading and job title in the email language, and keeps your typed recipient. Edits saved while writing may be replaced at completion. Undo brings back the whole email saved just before completion, only until the next email change.</p>
      <label className="check"><input type="checkbox" checked={confirmed} disabled={disabled || archived || busy || active || !!draft || conflict} onChange={event => setConfirmed(event.target.checked)} />I want to replace the email subject and body</label>
      <div className="actions"><button aria-describedby="email-rewrite-explanation" disabled={disabled || archived || busy || active || !!draft || conflict || !confirmed} onClick={start}>Rewrite application email</button>{restoreId && <button className="secondary" disabled={disabled || busy || active || !!draft || conflict} onClick={undo}>Undo email rewrite</button>}</div>
      <div className="actions">{(['recipient', 'subject', 'body'] as const).map(part => <button key={part} className="secondary" onClick={() => { void copy(part); }}>Copy {part}</button>)}</div>
      <p>Copy and the mail link use the words currently shown, including unsaved edits. Check them first.</p>
      {tooLong ? <><a role="link" aria-disabled="true">Open in mail app</a><p>This email is too long for a mail link. Use Copy body and paste it into your mail app. Nothing has been shortened.</p></> : <a href={handoff}>Open in mail app</a>}
      {!displayed.recipient && <p>Add the recipient yourself in your mail app before sending.</p>}
    </>}
    <section aria-label="Email PDF attachments"><h4>PDFs to attach yourself</h4><p>Download your résumé and cover letter, then attach them in your mail app. Review and render missing PDFs in the document editor below. There is no email PDF.</p>
      {(['resume', 'cover_letter'] as const).map(kind => {
        const member = documents.find(item => item.kind === kind); const label = kind === 'resume' ? 'résumé' : 'cover letter';
        const artifact = member?.artifacts.find(item => item.format === 'pdf' && item.revision_id === member.current_revision.id);
        return artifact ? <p key={kind}><a href={`/api/v1/artifacts/${encodeURIComponent(artifact.id)}`} download>Download {label} PDF</a></p> : <p key={kind}>No current {label} PDF yet.</p>;
      })}
    </section>
  </section>;
}
