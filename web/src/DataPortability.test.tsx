// SPDX-License-Identifier: MIT OR Apache-2.0
import { useState } from 'react';
import { webcrypto } from 'node:crypto';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent, { type UserEvent } from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Api, ApiError, MAX_PORTABLE_BYTES, type ActionApprovalRequest, type InstallationBackups, type InstallationStatus, type PortablePreview, type Profile, type Workflow } from './api';
import { DataPortability } from './DataPortability';
import { OwnerDataControls } from './OwnerDataControls';
import type { ApplicationDetail, ApplicationSummary } from './tracking-api';

const owner = '11111111-1111-4111-8111-111111111111';
const applicationId = '22222222-2222-4222-8222-222222222222';
const expiry = '2099-01-01T00:00:00Z';
const profile: Profile = { id: 'profile', version: 9, evidence: [], quality_warnings: [], current_revision: {
  id: 'profile-revision', revision_no: 1, content_sha256: 'a'.repeat(64), created_at: '2026-01-01', author_owner_id: owner,
  reason: 'Initial', claims: [], snapshot: { display_name: 'Fictional owner', headline: '', claim_revision_ids: [] },
} };
function workflow(): Workflow {
  return { id: 'workflow-one', version: 7, state: 'waiting_for_approval', attempt: 1, max_attempts: 1,
    next_attempt_at: null, last_error_code: null, created_at: '2026-01-01', updated_at: '2026-01-01', deadline_at: expiry,
    cancel_requested_at: null, lease_expires_at: null, last_heartbeat_at: null, queue_age_seconds: null, remaining_attempts: 0,
    cancelable: true, retryable: false, retry_block_reason: 'state_not_retryable', delivery_state: 'not_attempted',
    cancellation: { requested: false, local_fence_settled: false, provider_stopped: null, completed_effects_preserved: true, reason: null },
    usage: { status: 'not_applicable', model_runs: 0, inference_post_attempts: 0, inference_post_limit: null,
      input_tokens: null, output_tokens: null, token_budget: 0, tokens_used: 0, cost_budget_microunits: 0, cost_used_microunits: null, currency: 'USD' }, result: null };
}
function preview(replacement = false): PortablePreview {
  const task = workflow();
  const kind = replacement ? 'replace_workspace' as const : 'privacy_delete' as const;
  return { id: 'preview-one', workflow_run_id: task.id, version: task.version, workflow: task, preview_sha256: 'b'.repeat(64), expires_at: expiry, cleared_at: null, clear_reason: null,
    plan: { id: 'preview-one', workflow_run_id: task.id, action_kind: kind, binding: { namespace_id: 'namespace-one', workspace_version: 42, maintenance_epoch: 3, session_id: 'session-one', owner_id: owner },
      expires_at: expiry, remaining_copies: ['downloaded_exports', 'encrypted_backups', 'external_copies'], connections_after_operation: 'disabled_reauthorization_and_review_required',
      record_counts: { applications: 1, application_notes: 2 }, payload: replacement ? { replacement_sha256: 'c'.repeat(64) } : { categories: ['all_workspace'] },
      ...(replacement ? { source_owner_id: owner, source_namespace: 'original-namespace', source_schema_version: 22, manifest_sha256: 'd'.repeat(64), byte_length: 120, blob_count: 2, checkpoint_id: 'checkpoint-one', credential_policy: 'excluded_from_portable_archive', runtime_pins: { backend: 'immutable-image' } } : { credentials_erased: true, local_login_retained: true }) },
    approval: { id: 'approval-one', workflow_run_id: task.id, action_kind: kind, intent: { session_id: 'session-one', payload: { preview_sha256: 'b'.repeat(64) } },
      payload_sha256: 'e'.repeat(64), destination_sha256: 'f'.repeat(64), revision_id: null, target_version: 42, expires_at: expiry,
      approved_at: null, consumed_at: null, version: 1, applicable: true, reason: 'approval_required', capability_available: true,
      confirmation_phrase: replacement ? 'REPLACE ALL WORKSPACE DATA' : 'DELETE ALL WORKSPACE DATA' } };
}
const summary: ApplicationSummary = { id: applicationId, version: 2, attempt_no: 2, posting_id: 'posting', opportunity_id: 'opportunity', title: 'Fictional engineer', company: 'Fictional North', current_stage: 'preparing', archived_at: '2026-01-01', created_at: '2026-01-01', updated_at: '2026-01-01', next_action: null };
const detail: ApplicationDetail = { ...summary, version: 8, current_stage_event_id: null, notes: [], contacts: [], attachments: [], appointments: [] };
const ready: InstallationBackups = { configuration_present: true, eligible_checkpoint_id: 'checkpoint-one', eligibility_reason: null, key_custody: 'operator_separate', offhost_copy: 'not_attested', generations: [{ id: 'checkpoint-one', captured_at: '2026-01-01', restore_verified_at: '2026-01-02', byte_length: 300 }] };
interface Submission { path: string; body: unknown; version: number | undefined; key: boolean | string }
function server(initial = preview()) {
  const api = new Api(); api.csrf = 'csrf-original';
  const state: { preview: PortablePreview; workspaceVersion: number; context: InstallationStatus; workflow: Workflow | null; submissions: Submission[]; failures: Error[]; exportCopies: unknown[] } = {
    preview: initial, workspaceVersion: 42, context: { namespace_id: 'namespace-one', maintenance_epoch: 3, workspace_version: 42, maintenance_active: false, recovery_required: false, operation: null, drain: { active_calls: 0, provider_executions: 0 } }, workflow: null, submissions: [], failures: [], exportCopies: [],
  };
  const request = vi.spyOn(api, 'request').mockImplementation(async <T,>(path: string, method = 'GET', body?: unknown, version?: number, key: boolean | string = false): Promise<T> => {
    if (method === 'GET') {
      if (path === '/maintenance') return state.context as T;
      if (path === '/portable-exports') return { items: state.exportCopies } as T;
      if (path === '/portable-previews') return { workspace_version: state.workspaceVersion, items: [state.preview] } as T;
      if (path === '/portable-previews/preview-one') return state.preview as T;
      if (path === '/workflows/workflow-one') return (state.workflow ?? state.preview.workflow) as T;
      if (path === '/installation-backups') return ready as T;
      if (path === '/applications?archived=all&limit=100') return { items: [summary], next_cursor: 'next-page', query_elapsed_ms: 1, as_of: '2026-01-01' } as T;
      if (path.includes('cursor=next-page')) return { items: [{ ...summary, id: 'another-application', company: 'Fictional South' }], next_cursor: null, query_elapsed_ms: 1, as_of: '2026-01-01' } as T;
      if (path === `/applications/${applicationId}`) return detail as T;
    }
    if (method === 'POST') {
      state.submissions.push({ path, body, version, key });
      const failure = state.failures.shift(); if (failure) throw failure;
      if (path === '/portable-previews/deletion') return state.preview as T;
      if (path === '/workflows/workflow-one/approval/approve') {
        const dispatched = { ...state.preview.workflow, version: state.preview.version + 1, state: 'pending' as const };
        state.preview = { ...state.preview, version: dispatched.version, workflow: dispatched, approval: { ...state.preview.approval!, approved_at: '2026-01-02', version: 2 } };
        return dispatched as T;
      }
      if (path === '/portable-previews/preview-one/cancel') {
        state.preview = { ...state.preview, version: state.preview.version + 1, workflow: { ...state.preview.workflow, version: state.preview.version + 1, state: 'cancelled' }, cleared_at: '2026-01-02', clear_reason: 'cancelled' };
        return state.preview as T;
      }
      if (path === '/portable-exports') return { ...workflow(), state: 'pending' } as T;
    }
    throw new ApiError(404, 'not_found');
  });
  return { api, state, request };
}
function Controls({ api, disabled = false }: { api: Api; disabled?: boolean }) { return <OwnerDataControls api={api} active disabled={disabled} onBusyChange={() => undefined} />; }
async function openPreview(user: UserEvent) {
  const button = await screen.findByRole('button', { name: 'Review preview preview-one' });
  await waitFor(() => expect(button).toBeEnabled()); await user.click(button);
  await screen.findByRole('heading', { name: 'Preview preview-one' });
  await waitFor(() => expect(screen.getByLabelText('Typed destructive confirmation')).toBeEnabled());
}
function approvalButton() { return screen.getByRole('button', { name: 'Approve exact plan and queue execution' }); }
function confirmation() { return screen.getByRole('checkbox', { name: /I reviewed the exact scope/ }); }
async function confirm(user: UserEvent, phrase = 'DELETE ALL WORKSPACE DATA') {
  await user.type(screen.getByLabelText('Typed destructive confirmation'), phrase);
  await user.click(confirmation());
}
function approvals(submissions: Submission[]) { return submissions.filter(item => item.path.endsWith('/approval/approve')); }
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe('owner-data destructive review boundaries', () => {
  it('requires both an independently typed exact phrase and checkbox, then submits only the fresh server envelope with the WORKFLOW version', async () => {
    const user = userEvent.setup(); const { api, state } = server(); render(<Controls api={api} />); await openPreview(user);
    expect(screen.getByLabelText('Typed destructive confirmation')).toHaveValue(''); expect(confirmation()).not.toBeChecked(); expect(approvalButton()).toBeDisabled();
    await user.click(confirmation()); expect(approvalButton()).toBeDisabled();
    await user.type(screen.getByLabelText('Typed destructive confirmation'), 'DELETE ALL WORKSPACE DATA '); expect(confirmation()).not.toBeChecked();
    await user.click(confirmation()); expect(approvalButton()).toBeDisabled();
    await user.clear(screen.getByLabelText('Typed destructive confirmation')); await confirm(user);
    await user.click(approvalButton());
    await screen.findByText(/The exact approval was recorded and queued/);
    const [sent] = approvals(state.submissions);
    expect(sent?.version).toBe(7); expect(sent?.key).toEqual(expect.any(String));
    expect(sent?.body).toEqual({ payload_sha256: 'e'.repeat(64), destination_sha256: 'f'.repeat(64), revision_id: null, target_version: 42, confirmed: true, confirmation_phrase: 'DELETE ALL WORKSPACE DATA' } satisfies ActionApprovalRequest);
    expect(approvals(state.submissions)).toHaveLength(1); expect(confirmation()).not.toBeChecked(); expect(approvalButton()).toBeDisabled();
  });

  it('retains the reviewed counts and refuses a changed plan or approval instead of silently approving the newer envelope', async () => {
    const user = userEvent.setup(); const { api, state } = server(); render(<Controls api={api} />); await openPreview(user); await confirm(user);
    state.preview = { ...state.preview, preview_sha256: '9'.repeat(64), plan: { ...state.preview.plan, record_counts: { applications: 10 } }, approval: { ...state.preview.approval!, payload_sha256: '8'.repeat(64), version: 2 } };
    await user.click(approvalButton()); await screen.findByRole('region', { name: 'Changed server preview' });
    expect(approvals(state.submissions)).toHaveLength(0); expect(confirmation()).not.toBeChecked();
    expect(screen.getByText('applications: 1')).toBeVisible(); expect(screen.getByText('applications: 10')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Review current server preview instead' }));
    expect(screen.getByLabelText('Typed destructive confirmation')).toHaveValue(''); expect(approvalButton()).toBeDisabled();
    await confirm(user); await user.click(approvalButton());
    await waitFor(() => expect(approvals(state.submissions)).toHaveLength(1));
    expect(approvals(state.submissions)[0]?.body).toEqual(expect.objectContaining({ payload_sha256: '8'.repeat(64) }));
  });

  it.each(['workflow', 'namespace', 'workspace', 'maintenance', 'session'] as const)('blocks changed %s context before destructive dispatch', async fence => {
    const user = userEvent.setup(); const { api, state } = server(); render(<Controls api={api} />); await openPreview(user); await confirm(user);
    if (fence === 'workflow') state.workflow = { ...state.preview.workflow, version: 8, delivery_state: 'unknown' };
    if (fence === 'namespace') state.context = { ...state.context, namespace_id: 'different-namespace' };
    if (fence === 'workspace') state.context = { ...state.context, workspace_version: 43 };
    if (fence === 'maintenance') state.context = { ...state.context, maintenance_epoch: 4 };
    if (fence === 'session') api.csrf = 'csrf-rotated';
    await user.click(approvalButton());
    await waitFor(() => expect(screen.getAllByRole('alert').length).toBeGreaterThan(0));
    expect(approvals(state.submissions)).toHaveLength(0); expect(confirmation()).not.toBeChecked();
    expect(screen.getByLabelText('Typed destructive confirmation')).toHaveValue('DELETE ALL WORKSPACE DATA');
  });

  it.each(['expired', 'unknown', 'approved', 'consumed', 'incapable', 'stale'] as const)('does not expose approval for a server %s preview', async boundary => {
    const value = preview();
    if (boundary === 'expired') value.expires_at = '2000-01-01T00:00:00Z';
    if (boundary === 'unknown') value.workflow.delivery_state = 'unknown';
    if (boundary === 'approved') value.approval!.approved_at = '2026-01-02';
    if (boundary === 'consumed') value.approval!.consumed_at = '2026-01-02';
    if (boundary === 'incapable') value.approval!.capability_available = false;
    if (boundary === 'stale') { value.approval!.applicable = false; value.approval!.reason = 'workspace_changed'; }
    const user = userEvent.setup(); const { api, state } = server(value); render(<Controls api={api} />); await openPreview(user); await confirm(user);
    expect(approvalButton()).toBeDisabled(); expect(approvals(state.submissions)).toHaveLength(0);
  });

  it('fences an uncertain dispatch, performs no background retry and retains the same key for an explicit exact-envelope retry', async () => {
    const user = userEvent.setup(); const { api, state } = server(); state.failures.push(new TypeError('offline'));
    render(<Controls api={api} />); await openPreview(user); await confirm(user); await user.click(approvalButton());
    await screen.findByText(/The approval response was not confirmed/); expect(approvals(state.submissions)).toHaveLength(1);
    expect(approvalButton()).toBeDisabled(); expect(screen.getByLabelText('Typed destructive confirmation')).toHaveValue('DELETE ALL WORKSPACE DATA');
    await user.click(screen.getByRole('button', { name: 'Reload previews and dispatch status' }));
    expect(approvals(state.submissions)).toHaveLength(1); expect(approvalButton()).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Review preview preview-one' }));
    await confirm(user); await user.click(approvalButton());
    await waitFor(() => expect(approvals(state.submissions)).toHaveLength(2));
    expect(approvals(state.submissions)[1]?.key).toBe(approvals(state.submissions)[0]?.key);
  });

  it('uses a real paginated application choice and fetches its current version, never the list version or a manual identity', async () => {
    const user = userEvent.setup(); const { api, state, request } = server(); render(<Controls api={api} />);
    await user.click(await screen.findByRole('button', { name: 'Load saved applications' }));
    expect(screen.queryByRole('textbox', { name: /application.*id/i })).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText('Saved application'), applicationId);
    await user.click(screen.getByRole('button', { name: 'Load more applications' }));
    expect(await screen.findByRole('option', { name: /Fictional South/ })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Prepare deletion preview' }));
    await waitFor(() => expect(state.submissions).toHaveLength(1));
    expect(request).toHaveBeenCalledWith(`/applications/${applicationId}`, 'GET', undefined, undefined, false, expect.any(AbortSignal));
    expect(state.submissions[0]).toMatchObject({ path: '/portable-previews/deletion', version: 42, body: { scope: 'application', application_id: applicationId, application_version: 8 } });
  });

  it('clears failed terminal preview bytes via the dedicated cancel route and fresh workflow precondition, without replaying execution', async () => {
    const value = preview(true); value.workflow = { ...value.workflow, state: 'permanent_failure', cancelable: false, last_error_code: 'portable_restore_refused' };
    const user = userEvent.setup(); const { api, state } = server(value); render(<Controls api={api} />); await openPreview(user);
    expect(screen.getByRole('button', { name: 'Cancel preview and clear retained bytes' })).toBeEnabled();
    await user.click(screen.getByRole('button', { name: 'Cancel preview and clear retained bytes' }));
    await screen.findByText(/Cancellation response received/);
    expect(state.submissions).toEqual([{ path: '/portable-previews/preview-one/cancel', body: {}, version: 7, key: expect.any(String) }]);
    expect(screen.getByRole('button', { name: 'Cancel preview and clear retained bytes' })).toBeDisabled(); expect(approvals(state.submissions)).toHaveLength(0);
  });

  it('keeps the reviewed scope and typed buffer when an explicit reload loses the session, with no fallback approval', async () => {
    const user = userEvent.setup(); const { api, state, request } = server(); render(<Controls api={api} />); await openPreview(user); await confirm(user);
    request.mockRejectedValueOnce(new ApiError(401, 'session_required'));
    await user.click(screen.getByRole('button', { name: 'Reload previews and dispatch status' }));
    await screen.findByRole('alert');
    expect(screen.getByText('applications: 1')).toBeVisible();
    expect(screen.getByLabelText('Typed destructive confirmation')).toHaveValue('DELETE ALL WORKSPACE DATA');
    expect(confirmation()).not.toBeChecked(); expect(approvals(state.submissions)).toHaveLength(0);
  });

  it('checks the shared writer fence at action time even when it closes after the last render', async () => {
    const user = userEvent.setup(); const { api, state, request } = server(); render(<Controls api={api} />); await openPreview(user); await confirm(user);
    const readsBefore = request.mock.calls.length;
    api.writeBlock = 'workspace_reconnect_required';
    fireEvent.submit(approvalButton().closest('form')!);
    expect(request.mock.calls).toHaveLength(readsBefore); expect(approvals(state.submissions)).toHaveLength(0);
    expect(screen.getByLabelText('Typed destructive confirmation')).toHaveValue('DELETE ALL WORKSPACE DATA');
  });
});

describe('replacement source and browser-buffer safety', () => {
  it('retains the raw file and independently supplied owner through network, session and disabled states with stable upload identity', async () => {
    const user = userEvent.setup(); const { api, state } = server(preview(true));
    const upload = vi.spyOn(api, 'uploadPortable').mockRejectedValueOnce(new TypeError('offline')).mockRejectedValueOnce(new ApiError(401, 'session_required')).mockResolvedValue(state.preview);
    const { rerender } = render(<Controls api={api} />);
    const file = new File(['untrusted archive bytes, not a source of owner authority'], 'owner.zip', { type: 'application/zip' });
    await user.upload(await screen.findByLabelText('Owner-data ZIP'), file);
    await user.type(screen.getByLabelText('Expected original owner UUID (optional)'), owner);
    await user.click(screen.getByRole('button', { name: 'Upload ZIP and prepare replacement preview' }));
    await screen.findByRole('alert'); expect(upload).toHaveBeenCalledTimes(1);
    rerender(<Controls api={api} disabled />);
    expect(screen.getByLabelText('Owner-data ZIP')).toHaveProperty('files', expect.objectContaining({ 0: file }));
    expect(screen.getByLabelText('Expected original owner UUID (optional)')).toHaveValue(owner);
    expect(screen.getByLabelText('Expected original owner UUID (optional)')).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Upload ZIP and prepare replacement preview' })).toBeDisabled();
    rerender(<Controls api={api} />);
    await user.click(screen.getByRole('button', { name: 'Upload ZIP and prepare replacement preview' }));
    await waitFor(() => expect(upload).toHaveBeenCalledTimes(2));
    await user.click(screen.getByRole('button', { name: 'Upload ZIP and prepare replacement preview' }));
    await screen.findByRole('heading', { name: 'Preview preview-one' });
    expect(upload).toHaveBeenCalledTimes(3);
    for (const call of upload.mock.calls) expect(call).toEqual([file, 42, upload.mock.calls[0]?.[2], owner, expect.any(AbortSignal)]);
    expect(screen.getByRole('list', { name: 'Exact server record counts' })).toHaveTextContent('application_notes: 2');
    expect(screen.getByText('original-namespace / 22')).toBeVisible(); expect(screen.getByText('checkpoint-one')).toBeVisible();
  });

  it('lets the server default the expected original owner instead of reading the uploaded manifest', async () => {
    const user = userEvent.setup(); const { api, state } = server(preview(true)); const upload = vi.spyOn(api, 'uploadPortable').mockResolvedValue(state.preview);
    render(<Controls api={api} />);
    await user.upload(await screen.findByLabelText('Owner-data ZIP'), new File([JSON.stringify({ owner_id: 'attacker-claimed-owner' })], 'owner.zip', { type: 'application/zip' }));
    await user.click(screen.getByRole('button', { name: 'Upload ZIP and prepare replacement preview' }));
    await waitFor(() => expect(upload).toHaveBeenCalledTimes(1)); expect(upload.mock.calls[0]?.[3]).toBeUndefined();
    expect(screen.getByLabelText('Expected original owner UUID (optional)')).toHaveValue('');
  });

  it('requires another explicit action and a new key when a retry detects changed workspace context', async () => {
    const user = userEvent.setup(); const { api, state } = server(preview(true));
    const upload = vi.spyOn(api, 'uploadPortable').mockRejectedValueOnce(new TypeError('offline')).mockResolvedValue(state.preview);
    render(<Controls api={api} />); await user.upload(await screen.findByLabelText('Owner-data ZIP'), new File(['raw'], 'owner.zip', { type: 'application/zip' }));
    await user.click(screen.getByRole('button', { name: 'Upload ZIP and prepare replacement preview' })); await screen.findByRole('alert');
    state.workspaceVersion = 43;
    await user.click(screen.getByRole('button', { name: 'Upload ZIP and prepare replacement preview' }));
    await screen.findByText(/No preview was submitted/); expect(upload).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole('button', { name: 'Upload ZIP and prepare replacement preview' }));
    await waitFor(() => expect(upload).toHaveBeenCalledTimes(2));
    expect(upload.mock.calls[1]?.[1]).toBe(43); expect(upload.mock.calls[1]?.[2]).not.toBe(upload.mock.calls[0]?.[2]);
  });

  it('bounds upload size before transport and allows local edits/readiness while the shared writer fence is closed', async () => {
    const user = userEvent.setup(); const { api, request } = server(); const upload = vi.spyOn(api, 'uploadPortable'); api.writeBlock = 'workspace_reconnect_required';
    render(<Controls api={api} disabled />);
    const file = new File(['x'], 'large.zip', { type: 'application/zip' }); Object.defineProperty(file, 'size', { value: MAX_PORTABLE_BYTES + 1 });
    fireEvent.change(await screen.findByLabelText('Owner-data ZIP'), { target: { files: [file] } });
    await user.type(screen.getByLabelText('Expected original owner UUID (optional)'), owner);
    expect(screen.getByLabelText('Expected original owner UUID (optional)')).toHaveValue(owner);
    expect(screen.getByRole('button', { name: 'Upload ZIP and prepare replacement preview' })).toBeDisabled(); expect(upload).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Check replacement readiness' }));
    await screen.findByRole('region', { name: 'Installation backup readiness' });
    vi.useFakeTimers(); await act(async () => { await vi.advanceTimersByTimeAsync(10 * 60 * 1000); });
    expect(request.mock.calls.filter(call => call[0] === '/installation-backups')).toHaveLength(1);
  });

  it('does not latch its own controls when public onBusyChange feeds back into disabled', async () => {
    const user = userEvent.setup(); const { api } = server();
    function Harness() { const [busy, setBusy] = useState(false); return <DataPortability api={api} profile={profile} disabled={busy} onBusyChange={setBusy} />; }
    render(<Harness />); await user.click(screen.getByText('Owner-data portability', { selector: 'summary' }));
    const readiness = await screen.findByRole('button', { name: 'Check replacement readiness' });
    await waitFor(() => expect(readiness).toBeEnabled()); await user.click(readiness);
    await screen.findByRole('region', { name: 'Installation backup readiness' });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Create owner-data export' })).toBeEnabled());
    await user.click(screen.getByRole('button', { name: 'Create owner-data export' }));
    await screen.findByText(/Export task workflow-one is saved/);
    const load = screen.getByRole('button', { name: 'Load saved applications' });
    await waitFor(() => expect(load).toBeEnabled()); await user.click(load);
    await user.selectOptions(screen.getByLabelText('Saved application'), applicationId);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Prepare deletion preview' })).toBeEnabled());
    expect(screen.getByRole('button', { name: 'Check replacement readiness' })).toBeEnabled();
  });
});

describe('existing owner export integrity', () => {
  it.each(['digest', 'length'] as const)('never publishes a downloaded ZIP when its %s does not match the saved server identity', async mismatch => {
    const user = userEvent.setup(); const { api, state } = server(); vi.stubGlobal('crypto', webcrypto);
    const bytes = new TextEncoder().encode('owner archive');
    const digest = Buffer.from(await webcrypto.subtle.digest('SHA-256', bytes)).toString('hex');
    state.exportCopies = [{ id: 'export-one', state: 'succeeded', last_error_code: null, created_at: '2026-01-01', archive_sha256: mismatch === 'digest' ? '0'.repeat(64) : digest, manifest_sha256: '1'.repeat(64), byte_length: mismatch === 'length' ? bytes.length + 1 : bytes.length, expires_at: expiry, downloadable: true, copy_state: 'ready' }];
    const createObjectURL = vi.fn(); vi.stubGlobal('URL', class extends URL { static createObjectURL = createObjectURL; });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(bytes, { status: 200 })));
    render(<DataPortability api={api} profile={profile} disabled={false} onBusyChange={() => undefined} />);
    await user.click(screen.getByText('Owner-data portability', { selector: 'summary' }));
    await user.click(await screen.findByRole('button', { name: 'Download owner data' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('portable_export_integrity_failed'); expect(createObjectURL).not.toHaveBeenCalled();
    expect(within(screen.getByRole('list', { name: 'Recent owner-data exports' })).getByText(/succeeded/)).toBeVisible();
  });
});
