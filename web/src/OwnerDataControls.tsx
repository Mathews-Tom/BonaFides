// SPDX-License-Identifier: MIT OR Apache-2.0
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Api, ApiError, errorText, MAX_PORTABLE_BYTES, terminal, type ActionApprovalRequest, type InstallationBackups, type InstallationStatus, type PortablePlan, type PortablePreview, type PortablePreviewPage, type Workflow } from './api';
import type { ApplicationDetail, ApplicationPage, ApplicationSummary } from './tracking-api';

// Compare the complete server envelope, not a browser reconstruction of its authority.
function identity(value: unknown): string {
  return JSON.stringify(value, (_key, item: unknown) => item && typeof item === 'object' && !Array.isArray(item)
    ? Object.fromEntries(Object.entries(item).sort(([left], [right]) => left.localeCompare(right))) : item);
}
function workflowIdentity(value: Workflow) {
  return identity({ id: value.id, version: value.version, state: value.state, cancel_requested_at: value.cancel_requested_at, delivery_state: value.delivery_state });
}
function unavailableReason(preview: PortablePreview): string | null {
  const { approval, workflow, plan } = preview;
  if (preview.cleared_at) return `Preview bytes cleared: ${preview.clear_reason ?? 'unavailable'}.`;
  if (!Number.isFinite(Date.parse(preview.expires_at)) || Date.parse(preview.expires_at) <= Date.now()
    || !Number.isFinite(Date.parse(plan.expires_at)) || Date.parse(plan.expires_at) <= Date.now()) return 'This preview has expired. Cancel it and prepare a new preview.';
  if (preview.id !== plan.id || preview.workflow_run_id !== plan.workflow_run_id || workflow.id !== preview.workflow_run_id || preview.version !== workflow.version) return 'Preview and workflow identities do not agree.';
  if (workflow.delivery_state === 'unknown') return 'Delivery is unknown. Do not replay this destructive action; operator investigation is required.';
  if (workflow.cancel_requested_at || workflow.state !== 'waiting_for_approval') return `Workflow is ${workflow.state.replaceAll('_', ' ')}; approval is unavailable.`;
  if (!approval || approval.workflow_run_id !== workflow.id || approval.action_kind !== plan.action_kind) return 'No matching server approval is available.';
  if (approval.consumed_at) return 'This approval has already been consumed. Do not replay it.';
  if (approval.approved_at) return 'Approval is recorded. Reload dispatch status; do not approve again.';
  if (!approval.applicable || !approval.capability_available) return `Server approval is unavailable: ${approval.reason}.`;
  if (!Number.isFinite(Date.parse(approval.expires_at)) || Date.parse(approval.expires_at) <= Date.now()) return 'The approval has expired. Prepare a new preview.';
  if (!approval.confirmation_phrase) return 'The server did not provide a typed confirmation phrase.';
  return null;
}
function PlanDetails({ plan }: { plan: PortablePlan }) {
  const replacement = plan.action_kind === 'replace_workspace';
  return <>
    <h4>{replacement ? 'Replace all saved workspace data' : plan.payload.application_id ? 'Permanently delete one application' : 'Permanently delete all workspace data'}</h4>
    <dl>
      <dt>Scope</dt><dd>{replacement ? 'Complete workspace replacement, not a merge' : (plan.payload.categories ?? []).join(', ')}</dd>
      {plan.payload.application_id && <><dt>Application identity / version</dt><dd>{plan.payload.application_id} / {plan.payload.application_version}</dd></>}
      <dt>Workspace namespace / version</dt><dd>{plan.binding.namespace_id} / {plan.binding.workspace_version}</dd>
      <dt>Expires</dt><dd>{new Date(plan.expires_at).toLocaleString()}</dd>
      <dt>Connection policy</dt><dd>{plan.connections_after_operation}</dd>
      <dt>Credentials</dt><dd>{replacement ? 'Saved installation credentials are erased. The archive supplies no replacement credentials; reconnect separately.' : plan.credentials_erased ? 'Saved credentials are erased' : 'Saved credentials are retained'}</dd>
      <dt>Local login</dt><dd>{replacement ? 'The local installation owner is retained; imported credentials are excluded' : plan.local_login_retained ? 'Retained' : 'Not retained'}. Current owner sessions are revoked by destructive execution.</dd>
    </dl>
    <h4>{replacement ? 'Incoming archive record counts' : 'Records in the deletion scope'}</h4>
    <ul aria-label="Exact server record counts">{Object.entries(plan.record_counts).map(([kind, count]) => <li key={kind}>{kind}: {count.toLocaleString()}</li>)}</ul>
    {replacement && <dl>
      <dt>Expected original owner</dt><dd>{plan.source_owner_id}</dd>
      <dt>Source namespace / schema</dt><dd>{plan.source_namespace} / {plan.source_schema_version}</dd>
      <dt>Archive bytes / blobs</dt><dd>{plan.byte_length?.toLocaleString()} / {plan.blob_count?.toLocaleString()}</dd>
      <dt>Archive SHA-256</dt><dd><code style={{ overflowWrap: 'anywhere' }}>{plan.payload.replacement_sha256}</code></dd>
      <dt>Manifest SHA-256</dt><dd><code style={{ overflowWrap: 'anywhere' }}>{plan.manifest_sha256}</code></dd>
      <dt>Verified installation checkpoint</dt><dd>{plan.checkpoint_id}</dd>
    </dl>}
    <h4>Copies not erased by this operation</h4>
    <ul>{plan.remaining_copies.map(copy => <li key={copy}>{copy}</li>)}</ul>
    <p>Downloaded exports, encrypted backups and copies held elsewhere need separate retention decisions. This action does not prove those copies were erased.</p>
    <details><summary>Complete server plan and runtime bindings</summary><pre style={{ overflowWrap: 'anywhere', whiteSpace: 'pre-wrap' }}>{JSON.stringify(plan, null, 2)}</pre></details>
  </>;
}

interface Attempt { fingerprint: string; key: string }
export function OwnerDataControls({ api, active, disabled, onBusyChange }: {
  api: Api; active: boolean; disabled: boolean; onBusyChange: (busy: boolean) => void;
}) {
  const [page, setPage] = useState<PortablePreviewPage | null>(null);
  const [reviewed, setReviewed] = useState<PortablePreview | null>(null);
  const [comparison, setComparison] = useState<PortablePreview | null>(null);
  const [readiness, setReadiness] = useState<InstallationBackups | null>(null);
  const [applications, setApplications] = useState<ApplicationSummary[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [applicationsLoaded, setApplicationsLoaded] = useState(false);
  const [applicationId, setApplicationId] = useState('');
  const [scope, setScope] = useState<'application' | 'workspace'>('application');
  const [file, setFile] = useState<File | null>(null);
  const [sourceOwner, setSourceOwner] = useState('');
  const [phrase, setPhrase] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [uncertain, setUncertain] = useState(false);
  const operation = useRef(false);
  const mounted = useRef(true);
  const callbacks = useRef(onBusyChange); callbacks.current = onBusyChange;
  const controllers = useRef(new Set<AbortController>());
  const preparation = useRef<Attempt | null>(null);
  const mutation = useRef<Attempt | null>(null);
  const cancellation = useRef<Attempt | null>(null);
  const reviewSession = useRef('');
  const csrf = api.csrf;
  useEffect(() => {
    mounted.current = true; const pending = controllers.current;
    return () => { mounted.current = false; for (const controller of pending) controller.abort(); callbacks.current(false); };
  }, []);
  useEffect(() => { setConfirmed(false); mutation.current = null; cancellation.current = null; }, [csrf]);
  useEffect(() => { setConfirmed(false); }, [disabled]);
  async function request<T>(path: string, method = 'GET', body?: unknown, version?: number, key: boolean | string = false): Promise<T> {
    const controller = new AbortController(); controllers.current.add(controller);
    const timeout = window.setTimeout(() => controller.abort(), 30000);
    try {
      const value = await api.request<T>(path, method, body, version, key, controller.signal);
      if (!mounted.current || controller.signal.aborted) throw new DOMException('Request stopped', 'AbortError');
      return value;
    } finally { clearTimeout(timeout); controllers.current.delete(controller); }
  }
  async function run(action: () => Promise<void>, write = false) {
    if (operation.current || (write && (disabled || api.writeBlock))) return;
    operation.current = true; setBusy(true); callbacks.current(true); setError(''); setNotice('');
    try { await action(); }
    catch (failure) {
      if (mounted.current) { setConfirmed(false); setError(`${errorText(failure)} Your file, expected owner and typed text remain in memory. Nothing is automatically retried.`); }
    } finally {
      operation.current = false;
      if (mounted.current) { setBusy(false); callbacks.current(false); }
    }
  }
  function accept(value: PortablePreview) {
    if (!reviewed || identity(reviewed) !== identity(value) || reviewSession.current !== api.csrf) {
      mutation.current = null; cancellation.current = null;
    }
    setReviewed(value); setComparison(null); setConfirmed(false); setPhrase(''); setUncertain(false);
    reviewSession.current = api.csrf;
    setPage(previous => previous ? { ...previous, items: [value, ...previous.items.filter(item => item.id !== value.id)] } : previous);
  }
  async function loadPreviews() {
    const value = await request<PortablePreviewPage>('/portable-previews');
    setPage(value);
    if (reviewed) {
      const fresh = await request<PortablePreview>(`/portable-previews/${encodeURIComponent(reviewed.id)}`);
      setConfirmed(false);
      if (identity(fresh) !== identity(reviewed)) setComparison(fresh);
      else setNotice('Current server preview matches the displayed review. Approval still requires an explicit typed confirmation.');
    }
  }
  useEffect(() => {
    if (!active) return;
    let alive = true;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 30000);
    void api.request<PortablePreviewPage>('/portable-previews', 'GET', undefined, undefined, false, controller.signal)
      .then(value => { if (alive && !controller.signal.aborted) setPage(value); })
      .catch(failure => { if (alive) setError(`${errorText(failure)} Previous preview data is retained. Reload explicitly.`); })
      .finally(() => clearTimeout(timeout));
    return () => { alive = false; controller.abort(); clearTimeout(timeout); };
  }, [api, active]);
  function keyFor(ref: { current: Attempt | null }, fingerprint: string) {
    if (ref.current?.fingerprint !== fingerprint) ref.current = { fingerprint, key: crypto.randomUUID() };
    return ref.current.key;
  }
  async function prepare(event: FormEvent<HTMLFormElement>, replacement: boolean) {
    event.preventDefault();
    if (replacement && (!file || file.size === 0 || file.size > MAX_PORTABLE_BYTES)) { setError('Choose a nonempty owner-data ZIP no larger than 625 GiB + 20 MiB.'); return; }
    if (replacement && sourceOwner && !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(sourceOwner)) { setError('Expected original owner must be a UUID obtained independently of this archive.'); return; }
    if (!replacement && scope === 'application' && !applications.some(item => item.id === applicationId)) { setError('Choose a saved application from the loaded list.'); return; }
    const chosenFile = file; const expectedOwner = sourceOwner; const session = api.csrf;
    await run(async () => {
      const current = await request<PortablePreviewPage>('/portable-previews'); setPage(current);
      const application = !replacement && scope === 'application'
        ? await request<ApplicationDetail>(`/applications/${encodeURIComponent(applicationId)}`) : null;
      if (session !== api.csrf) throw new ApiError(409, 'session_changed');
      if (application && application.id !== applicationId) throw new ApiError(409, 'application_identity_changed');
      const body = application ? { scope: 'application', application_id: application.id, application_version: application.version } : { scope: 'workspace' };
      const fingerprint = identity({ replacement, version: current.workspace_version, session, body, expectedOwner });
      if (preparation.current && preparation.current.fingerprint !== fingerprint) {
        keyFor(preparation, fingerprint);
        setError('Workspace, application or session context changed. No preview was submitted. Review your choices and prepare again explicitly.'); return;
      }
      const key = keyFor(preparation, fingerprint);
      let value: PortablePreview;
      if (replacement && chosenFile) {
        const controller = new AbortController(); controllers.current.add(controller);
        try {
          value = await api.uploadPortable(chosenFile, current.workspace_version, key, expectedOwner || undefined, controller.signal);
          if (!mounted.current || controller.signal.aborted) throw new DOMException('Upload stopped', 'AbortError');
        } finally { controllers.current.delete(controller); }
      } else value = await request<PortablePreview>('/portable-previews/deletion', 'POST', body, current.workspace_version, key);
      preparation.current = null; accept(value);
      setNotice('Server preview saved. No destructive approval has been sent. Inspect every scope, count, retained copy and binding before confirming.');
    }, true);
  }
  async function approve(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!reviewed || comparison || uncertain || !confirmed || phrase !== reviewed.approval?.confirmation_phrase) return;
    const blockedReason = unavailableReason(reviewed);
    if (blockedReason) { setConfirmed(false); setError(`${blockedReason} No approval was sent.`); return; }
    const original = reviewed; const session = reviewSession.current;
    await run(async () => {
      const [fresh, workflow, context] = await Promise.all([
        request<PortablePreview>(`/portable-previews/${encodeURIComponent(original.id)}`),
        request<Workflow>(`/workflows/${encodeURIComponent(original.workflow_run_id)}`),
        request<InstallationStatus>('/maintenance'),
      ]);
      if (session !== api.csrf) { setConfirmed(false); throw new ApiError(409, 'session_changed'); }
      if (identity(fresh) !== identity(original) || workflowIdentity(workflow) !== workflowIdentity(original.workflow)
        || fresh.version !== workflow.version || workflow.id !== fresh.workflow_run_id) {
        setComparison(fresh); setConfirmed(false); mutation.current = null;
        setError(`The server preview, approval or workflow changed. Current workflow: ${workflow.state}, version ${workflow.version}, delivery ${workflow.delivery_state}. Compare the current response and explicitly review it; no approval was sent.`); return;
      }
      const reason = unavailableReason(fresh);
      if (reason || context.maintenance_active || context.recovery_required || context.namespace_id !== fresh.plan.binding.namespace_id
        || context.workspace_version !== fresh.plan.binding.workspace_version || context.maintenance_epoch !== fresh.plan.binding.maintenance_epoch) {
        setConfirmed(false); setError(`${reason ?? 'Installation or workspace bindings changed; prepare a fresh preview.'} No approval was sent.`); return;
      }
      const approval = fresh.approval!;
      const body: ActionApprovalRequest = { payload_sha256: approval.payload_sha256, destination_sha256: approval.destination_sha256,
        revision_id: approval.revision_id, target_version: approval.target_version, confirmed: true, confirmation_phrase: phrase };
      const path = `/workflows/${encodeURIComponent(workflow.id)}/approval/approve`;
      const key = keyFor(mutation, identity({ path, body, version: workflow.version, preview: fresh.preview_sha256, session }));
      try {
        const dispatched = await request<Workflow>(path, 'POST', body, workflow.version, key);
        setReviewed({ ...fresh, workflow: dispatched, version: dispatched.version }); setConfirmed(false); setUncertain(false);
        setNotice('The exact approval was recorded and queued, not proven executed. Reload dispatch status explicitly. Execution revokes owner sessions; sign in again when requested without discarding unsaved editors.');
      } catch (failure) {
        setUncertain(true);
        throw failure;
      }
    }, true);
  }
  async function cancel() {
    if (!reviewed) return;
    const original = reviewed;
    await run(async () => {
      const [fresh, workflow] = await Promise.all([
        request<PortablePreview>(`/portable-previews/${encodeURIComponent(original.id)}`),
        request<Workflow>(`/workflows/${encodeURIComponent(original.workflow_run_id)}`),
      ]);
      if (fresh.workflow_run_id !== workflow.id || fresh.version !== workflow.version) throw new ApiError(409, 'portable_preview_changed');
      const path = `/portable-previews/${encodeURIComponent(fresh.id)}/cancel`;
      const key = keyFor(cancellation, identity({ path, version: workflow.version, session: api.csrf }));
      const value = await request<PortablePreview>(path, 'POST', {}, workflow.version, key);
      accept(value);
      setNotice('Cancellation response received. Retained candidate bytes are cleared even for failed terminal previews. Completed effects are not undone; inspect the current workflow below.');
    }, true);
  }
  const blocked = disabled || busy || !!api.writeBlock;
  const reason = reviewed ? unavailableReason(reviewed) : null;
  return <section aria-label="Replace or delete owner data">
    <h2>Replace or permanently delete saved data</h2>
    <p>These local workflows require recent owner confirmation. Use “Confirm owner identity” first. Preparing a preview does not approve it. Only a fresh, exact server plan can be approved; there is no automatic destructive retry.</p>
    <p role="status">{notice}</p>
    {error && <p role="alert" className="error">{error}</p>}
    <div className="actions"><button type="button" className="secondary" disabled={busy} onClick={() => { void run(loadPreviews); }}>Reload previews and dispatch status</button>
      <button type="button" className="secondary" disabled={busy} onClick={() => { void run(async () => { const value = await request<InstallationBackups>('/installation-backups'); setReadiness(value); setNotice('Readiness checked once. The server rechecks the exact checkpoint when preparing replacement; this is not continuing authorization.'); }); }}>Check replacement readiness</button></div>
    {readiness && <section aria-label="Installation backup readiness"><h3>Installation backup readiness</h3>
      <p>{readiness.configuration_present ? 'Backup configuration present.' : 'Backup configuration absent.'} Eligible checkpoint: {readiness.eligible_checkpoint_id ?? 'none'}. {readiness.eligibility_reason}</p>
      <p>Key custody: {readiness.key_custody}. Off-host copy: {readiness.offhost_copy}.</p>
      <ul>{readiness.generations.map(generation => <li key={generation.id}>{generation.id} · captured {new Date(generation.captured_at).toLocaleString()} · {generation.byte_length.toLocaleString()} bytes · {generation.restore_verified_at ? `restore verified ${new Date(generation.restore_verified_at).toLocaleString()}` : 'restore not verified'}</li>)}</ul>
    </section>}
    <form onSubmit={event => { void prepare(event, true); }}>
      <fieldset disabled={busy}><legend>Prepare workspace replacement</legend>
        <label>Owner-data ZIP<input type="file" accept=".zip,application/zip" onChange={event => { setFile(event.target.files?.[0] ?? null); preparation.current = null; }} /></label>
        <label>Expected original owner UUID (optional)<input value={sourceOwner} maxLength={36} autoComplete="off" spellCheck={false} onChange={event => { setSourceOwner(event.target.value); preparation.current = null; }} /></label>
        <p className="hint">Leave blank to require the current installation owner. For a different original owner, obtain the UUID independently; never copy an untrusted archive’s claim as proof. The server streams the raw ZIP (maximum 625 GiB + 20 MiB) into private quarantine and validates its schema, records, files and restore-verified checkpoint. Credentials are excluded; connections require reauthorization and review.</p>
        {file && <p>Selected file: {file.name} · {file.size.toLocaleString()} bytes. Your file remains available after a failed request.</p>}
        <button type="submit" disabled={blocked || !file || file.size === 0 || file.size > MAX_PORTABLE_BYTES}>Upload ZIP and prepare replacement preview</button>
      </fieldset>
    </form>
    <form onSubmit={event => { void prepare(event, false); }}>
      <fieldset disabled={busy}><legend>Prepare permanent deletion</legend>
        <label>Deletion scope<select value={scope} onChange={event => { setScope(event.target.value as 'application' | 'workspace'); preparation.current = null; }}><option value="application">One saved application</option><option value="workspace">All saved workspace data</option></select></label>
        {scope === 'application' && <>
          <button type="button" className="secondary" onClick={() => { void run(async () => { const result = await request<ApplicationPage>('/applications?archived=all&limit=100'); setApplications(result.items); setCursor(result.next_cursor); setApplicationsLoaded(true); }); }}>Load saved applications</button>
          <label>Saved application<select value={applicationId} onChange={event => { setApplicationId(event.target.value); preparation.current = null; }}><option value="">Choose an application</option>{applications.map(item => <option key={item.id} value={item.id}>{item.company} — {item.title} · attempt {item.attempt_no}{item.archived_at ? ' · archived' : ''} · {item.id}</option>)}</select></label>
          {applicationsLoaded && applications.length === 0 && <p>No saved applications were returned.</p>}
          {cursor && <button type="button" className="secondary" onClick={() => { void run(async () => { const result = await request<ApplicationPage>(`/applications?${new URLSearchParams({ archived: 'all', limit: '100', cursor })}`); setApplications(previous => [...previous, ...result.items.filter(item => !previous.some(old => old.id === item.id))]); setCursor(result.next_cursor); }); }}>Load more applications</button>}
          <p className="hint">Includes archived applications. The selected application’s current version is fetched again before preparing a deletion preview. No manually entered identity is accepted.</p>
        </>}
        <button type="submit" disabled={blocked || (scope === 'application' && !applications.some(item => item.id === applicationId))}>Prepare deletion preview</button>
      </fieldset>
    </form>
    {page && <><p>Last loaded workspace version: {page.workspace_version}. Status updates are explicit, not automatic.</p><ul aria-label="Saved owner-data previews">{page.items.map(item => <li key={item.id}>
      <p>{item.plan.action_kind} · {item.workflow.state.replaceAll('_', ' ')} · {item.id}{item.cleared_at ? ` · bytes cleared (${item.clear_reason})` : ''}</p>
      <button type="button" className="secondary" disabled={busy} onClick={() => { void run(async () => { accept(await request<PortablePreview>(`/portable-previews/${encodeURIComponent(item.id)}`)); }); }}>Review preview {item.id}</button>
    </li>)}</ul>{page.items.length === 0 && <p>No saved destructive previews.</p>}</>}
    {reviewed && <section aria-label="Exact owner-data preview"><h3>Preview {reviewed.id}</h3><PlanDetails plan={reviewed.plan} />
      <p>Workflow {reviewed.workflow_run_id} · version {reviewed.version} · {reviewed.workflow.state.replaceAll('_', ' ')} · delivery {reviewed.workflow.delivery_state}.</p>
      {reviewed.workflow.last_error_code && <p>Workflow error: {reviewed.workflow.last_error_code}. Failure is not proof of completion or rollback.</p>}
      <p>Preview SHA-256: <code style={{ overflowWrap: 'anywhere' }}>{reviewed.preview_sha256}</code></p>
      {reason && <p role="status">{reason}</p>}
      {comparison && <section className="comparison" aria-label="Changed server preview"><h4>Current server response differs</h4><PlanDetails plan={comparison.plan} /><p>Current workflow: {comparison.workflow.state} · version {comparison.version} · delivery {comparison.workflow.delivery_state}. {comparison.workflow.last_error_code && `Error: ${comparison.workflow.last_error_code}.`} Your previous review is retained above.</p>
        <button type="button" className="secondary" disabled={busy} onClick={() => { accept(comparison); setNotice('Current server response loaded for a new review. Typed confirmation and checkbox were cleared; nothing was approved.'); }}>Review current server preview instead</button>
      </section>}
      {uncertain && <p role="alert">The approval response was not confirmed. Do not replay it. Reload dispatch status and review the current response before taking any further action.</p>}
      <details><summary>Exact server approval envelope</summary><pre style={{ overflowWrap: 'anywhere', whiteSpace: 'pre-wrap' }}>{JSON.stringify(reviewed.approval, null, 2)}</pre></details>
      <form onSubmit={event => { void approve(event); }}>
        <fieldset disabled={busy}><legend>Approve this exact destructive plan</legend>
          <p>Type exactly: <strong>{reviewed.approval?.confirmation_phrase ?? 'No server confirmation phrase is available'}</strong></p>
          <label>Typed destructive confirmation<input value={phrase} autoComplete="off" spellCheck={false} onChange={event => { setPhrase(event.target.value); setConfirmed(false); }} /></label>
          <label className="check"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />I reviewed the exact scope, counts, credentials, remaining copies and server approval; I authorize this one destructive operation.</label>
          <button type="submit" disabled={blocked || !!comparison || !!reason || uncertain || !confirmed || phrase !== reviewed.approval?.confirmation_phrase}>Approve exact plan and queue execution</button>
        </fieldset>
      </form>
      <button type="button" className="secondary" disabled={blocked || !!reviewed.cleared_at || (!terminal(reviewed.workflow.state) && !reviewed.workflow.cancelable)} onClick={() => { void cancel(); }}>Cancel preview and clear retained bytes</button>
      <p className="hint">Cancellation cannot undo committed deletion or replacement and does not prove an unknown provider execution stopped. Failed terminal previews can still be canceled to clear retained upload bytes.</p>
    </section>}
  </section>;
}
