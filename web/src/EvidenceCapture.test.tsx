// SPDX-License-Identifier: MIT OR Apache-2.0
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { Api, ApiError, type CaptureScan, type CaptureSource, type Profile, type Workflow } from './api';
import { EvidenceCapture } from './EvidenceCapture';
import { ProfileEntries } from './ProfileEntries';

const metadata = { id: 'profile-revision', revision_no: 1, content_sha256: 'a'.repeat(64), created_at: '2026-01-01T00:00:00Z', author_owner_id: 'synthetic-owner' };
const profile: Profile = { id: 'profile', version: 1, current_revision: { ...metadata, reason: 'Fixture', snapshot: { display_name: 'Synthetic Person', headline: '', claim_revision_ids: [] }, claims: [] }, evidence: [], quality_warnings: [], experience_coverage: { minimum_days: 0, maximum_days: 0, undated_or_open_entries: 0 } };
const source: CaptureSource = { id: 'work', label: 'Synthetic repository', kind: 'local_git', enabled: true, privacy_class: 'private', employer_owned: true };
const workflow: Workflow = {
  id: 'scan-1', version: 1, state: 'succeeded', attempt: 1, max_attempts: 1, next_attempt_at: null, last_error_code: null,
  created_at: metadata.created_at, updated_at: metadata.created_at, deadline_at: metadata.created_at, cancel_requested_at: null,
  lease_expires_at: null, last_heartbeat_at: null, queue_age_seconds: null, remaining_attempts: 0, cancelable: false, retryable: false, retry_block_reason: null,
  cancellation: { requested: false, local_fence_settled: false, provider_stopped: null, completed_effects_preserved: true, reason: null }, delivery_state: 'not_attempted',
  usage: { status: 'not_applicable', model_runs: 0, inference_post_attempts: 0, inference_post_limit: null, input_tokens: null, output_tokens: null, token_budget: 0, tokens_used: 0, cost_budget_microunits: 0, cost_used_microunits: null, currency: 'USD' }, result: null,
};
const proposals = ['b', 'c'].map((letter, index) => ({ id: letter.repeat(40), payload: { text: `Synthetic commit ${index + 1}`, source: `local-git:work@${letter.repeat(40)}` }, provenance: { source_type: 'work_sample' as const, privacy_class: 'private' as const, employer_owned: true }, already_approved: false }));
const scan: CaptureScan = { workflow, proposals, profile_version: 1, source_changed: false };
const approved: Profile = { ...profile, version: 2, evidence: [{ id: 'captured-evidence', version: 1, archived: false, current_revision_id: 'captured-revision', revision: { ...metadata, id: 'captured-revision', payload: proposals[0]!.payload, provenance: { source_type: 'work_sample', origin: 'evidence_capture', privacy_class: 'confidential', employer_owned: false } } }] };

function setup({ saved = true, configured = [source], detail = scan, approval = async () => approved, embedded = false }: { saved?: boolean; configured?: CaptureSource[]; detail?: CaptureScan; approval?: () => Promise<Profile>; embedded?: boolean } = {}) {
  const api = new Api(); const onProfileChange = vi.fn(); const onDirtyChange = vi.fn(); const onBusyChange = vi.fn();
  const request = vi.spyOn(api, 'request').mockImplementation(async <T,>(path: string, method = 'GET') => {
    if (path === '/evidence-capture/sources') return { items: configured } as T;
    if (path === '/evidence-capture/scans') return { items: saved ? [{ id: workflow.id, source_id: source.id, source_label: source.label, state: detail.workflow.state, created_at: metadata.created_at }] : [] } as T;
    if (path === '/evidence-capture/sources/work/scan' && method === 'POST') { saved = true; return { workflow_id: workflow.id } as T; }
    if (path === '/evidence-capture/scans/scan-1') return detail as T;
    if (path === '/evidence-capture/scans/scan-1/approve' && method === 'POST') return await approval() as T;
    throw new ApiError(404, 'not_found');
  });
  const props = { api, profile, active: true, disabled: false, onProfileChange, onDirtyChange, onBusyChange };
  const view = render(embedded ? <ProfileEntries api={api} profile={profile} disabled={false} onProfile={onProfileChange} onDirtyChange={onDirtyChange} onBusyChange={onBusyChange} /> : <EvidenceCapture {...props} />);
  return { ...view, props, user: userEvent.setup(), request, onProfileChange, onDirtyChange };
}

describe('owner-controlled source capture', () => {
  it('restores pending scan status and exposes proposals only after successful publication', async () => {
    const pending: CaptureScan = { ...scan, workflow: { ...workflow, state: 'pending' }, proposals: [] };
    const { user, request, onProfileChange } = setup({ detail: pending });
    expect(await screen.findByText('Scan scan-1: pending')).toBeVisible();
    expect(screen.queryByRole('button', { name: /Review proposal/ })).not.toBeInTheDocument();
    pending.workflow = workflow; pending.proposals = proposals;
    await user.click(screen.getByRole('button', { name: 'Refresh capture sources and status' }));
    expect(await screen.findByRole('button', { name: `Review proposal ${proposals[0]!.id}` })).toBeEnabled();
    expect(request.mock.calls.filter(call => call[1] === 'POST')).toHaveLength(0);
    expect(onProfileChange).not.toHaveBeenCalled();
  });

  it('disables an already-open approval when refreshed source identity changes without discarding classification', async () => {
    const changed = { ...scan };
    const { user } = setup({ detail: changed });
    await user.click(await screen.findByRole('button', { name: `Review proposal ${proposals[0]!.id}` }));
    await user.selectOptions(screen.getByLabelText('Capture privacy class'), 'confidential');
    changed.source_changed = true;
    await user.click(screen.getByRole('button', { name: 'Refresh capture sources and status' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Approve selected proposal as source' })).toBeDisabled());
    expect(screen.getByLabelText('Capture privacy class')).toHaveValue('confidential');
  });

  it('keeps capture review and manual source buffers mutually exclusive without losing either decision', async () => {
    const { user, onDirtyChange } = setup({ embedded: true });
    await user.click(screen.getByRole('button', { name: 'Sources' }));
    await user.click(await screen.findByRole('button', { name: `Review proposal ${proposals[0]!.id}` }));
    expect(screen.getByRole('button', { name: 'Skills' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Add source or owner attestation' })).toBeDisabled();
    expect(onDirtyChange).toHaveBeenLastCalledWith(true);
    await user.click(screen.getByRole('button', { name: 'Close capture review without retry' }));
    expect(onDirtyChange).toHaveBeenLastCalledWith(false);
    await user.click(screen.getByRole('button', { name: 'Add source or owner attestation' }));
    await user.type(screen.getByLabelText('Source text'), 'Retained owner draft');
    expect(screen.getByRole('button', { name: 'Scan Synthetic repository' })).toBeDisabled();
    expect(screen.getByRole('button', { name: `Review proposal ${proposals[0]!.id}` })).toBeDisabled();
    expect(screen.getByLabelText('Source text')).toHaveValue('Retained owner draft');
  });

  it('scans only on request and approves just the reviewed commit with adjusted preset classification', async () => {
    const { user, request, onProfileChange, onDirtyChange } = setup({ saved: false });
    await user.click(await screen.findByRole('button', { name: 'Scan Synthetic repository' }));
    await user.click(await screen.findByRole('button', { name: `Review proposal ${proposals[0]!.id}` }));
    expect(onProfileChange).not.toHaveBeenCalled();
    expect(onDirtyChange).toHaveBeenLastCalledWith(true);
    expect(screen.getByLabelText('Capture privacy class')).toHaveValue('private');
    expect(screen.getByLabelText('My employer owns this captured content')).toBeChecked();
    await user.selectOptions(screen.getByLabelText('Capture privacy class'), 'confidential');
    await user.click(screen.getByLabelText('My employer owns this captured content'));
    await user.click(screen.getByRole('button', { name: 'Approve selected proposal as source' }));
    expect(onProfileChange).toHaveBeenCalledExactlyOnceWith(approved);
    expect(screen.queryByRole('button', { name: `Review proposal ${proposals[0]!.id}` })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: `Review proposal ${proposals[1]!.id}` })).toBeInTheDocument();
    const posts = request.mock.calls.filter(call => call[1] === 'POST');
    expect(posts).toHaveLength(2);
    expect(posts[0]?.slice(0, 4)).toEqual(['/evidence-capture/sources/work/scan', 'POST', {}, 1]);
    expect(posts[1]?.slice(0, 4)).toEqual(['/evidence-capture/scans/scan-1/approve', 'POST', { candidate_id: proposals[0]!.id, privacy_class: 'confidential', employer_owned: false }, 1]);
  });

  it('restores saved proposals without writes and blocks approval after a source configuration change', async () => {
    const { request, onProfileChange } = setup({ configured: [{ ...source, enabled: false }], detail: { ...scan, source_changed: true } });
    expect(await screen.findByRole('button', { name: `Review proposal ${proposals[0]!.id}` })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Scan Synthetic repository' })).toBeDisabled();
    expect(request.mock.calls.filter(call => call[1] === 'POST')).toHaveLength(0);
    expect(onProfileChange).not.toHaveBeenCalled();
  });

  it('retries uncertain approval only explicitly with its original body, version and key even after a profile refresh', async () => {
    let attempts = 0;
    const { user, request, rerender, props, onProfileChange } = setup({ approval: async () => { attempts += 1; if (attempts === 1) throw new TypeError('Synthetic connection lost'); return approved; } });
    await user.click(await screen.findByRole('button', { name: `Review proposal ${proposals[0]!.id}` }));
    await user.click(screen.getByRole('button', { name: 'Approve selected proposal as source' }));
    const retry = await screen.findByRole('button', { name: 'Retry original approval request' });
    expect(screen.getByLabelText('Capture privacy class')).toBeDisabled();
    rerender(<EvidenceCapture {...props} profile={{ ...profile, version: 2 }} />);
    expect(attempts).toBe(1);
    await user.click(retry);
    const writes = request.mock.calls.filter(call => call[0].endsWith('/approve'));
    expect(writes).toHaveLength(2);
    expect(writes[1]?.slice(0, 5)).toEqual(writes[0]?.slice(0, 5));
    expect(writes[0]?.[3]).toBe(1);
    expect(typeof writes[0]?.[4]).toBe('string');
    expect(onProfileChange).toHaveBeenCalledExactlyOnceWith(approved);
  });

  it('retains classification but requires a new explicit review when the profile version changes', async () => {
    const { user, rerender, props, request } = setup();
    await user.click(await screen.findByRole('button', { name: `Review proposal ${proposals[1]!.id}` }));
    await user.selectOptions(screen.getByLabelText('Capture privacy class'), 'confidential');
    rerender(<EvidenceCapture {...props} profile={{ ...profile, version: 2 }} />);
    expect(screen.getByLabelText('Capture privacy class')).toHaveValue('confidential');
    expect(screen.getByRole('button', { name: 'Approve selected proposal as source' })).toBeDisabled();
    expect(request.mock.calls.filter(call => call[1] === 'POST')).toHaveLength(0);
  });

  it('never applies a late approval response to a replacement owner workspace', async () => {
    // ES2022 does not include Promise.withResolvers.
    let resolve!: (value: Profile) => void;
    const pending = new Promise<Profile>(done => { resolve = done; });
    const { user, rerender, props, onProfileChange } = setup({ approval: () => pending });
    await user.click(await screen.findByRole('button', { name: `Review proposal ${proposals[0]!.id}` }));
    await user.click(screen.getByRole('button', { name: 'Approve selected proposal as source' }));
    rerender(<EvidenceCapture {...props} key="replacement-owner" profile={{ ...profile, id: 'replacement-profile' }} active={false} />);
    await act(async () => { resolve(approved); await pending; });
    expect(onProfileChange).not.toHaveBeenCalled();
    expect(screen.queryByLabelText('Capture privacy class')).not.toBeInTheDocument();
  });

  it.each(['source_rate_limited', 'source_forbidden', 'source_invalid_response'])('keeps %s scans visibly failed instead of presenting empty success', async code => {
    const { request, onProfileChange } = setup({ detail: { ...scan, workflow: { ...workflow, state: 'permanent_failure', last_error_code: code }, proposals: [] } });
    expect(await screen.findByRole('alert')).toHaveTextContent(code);
    expect(screen.queryByRole('button', { name: /Review proposal/ })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Scan Synthetic repository' })).toBeEnabled();
    expect(request.mock.calls.filter(call => call[1] === 'POST')).toHaveLength(0);
    expect(onProfileChange).not.toHaveBeenCalled();
  });

  it('preserves the review after a rejected approval and respects an external busy boundary', async () => {
    const { user, rerender, props } = setup({ approval: async () => { throw new ApiError(409, 'capture_source_changed'); } });
    await user.click(await screen.findByRole('button', { name: `Review proposal ${proposals[0]!.id}` }));
    await user.selectOptions(screen.getByLabelText('Capture privacy class'), 'confidential');
    await user.click(screen.getByRole('button', { name: 'Approve selected proposal as source' }));
    await waitFor(() => expect(screen.getByRole('alert')).toBeVisible());
    expect(screen.getByLabelText('Capture privacy class')).toHaveValue('confidential');
    rerender(<EvidenceCapture {...props} disabled />);
    expect(screen.getByRole('button', { name: 'Approve selected proposal as source' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Close capture review without retry' })).toBeDisabled();
  });
});
