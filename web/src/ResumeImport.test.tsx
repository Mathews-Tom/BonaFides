// SPDX-License-Identifier: MIT OR Apache-2.0
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useCallback, useState } from 'react';
import { ResumeImport } from './ResumeImport';
import { Api, ApiError, MAX_IMPORT_BYTES, type ClaimRecord, type ImportBatch, type ImportMergeRequest, type Profile } from './api';

const oldClaim: ClaimRecord = {
  id: 'claim-work', kind: 'experience', archived: false, version: 1, current_revision_id: 'claim-r1',
  revision: { id: 'claim-r1', revision_no: 1, content_sha256: 'c'.repeat(64), created_at: '2026-01-01', author_owner_id: 'owner',
    payload: { text: 'Existing fictional work.', organization: 'Fictional North', role: 'Engineer', start_date: '2020' }, evidence_links: [] },
};
const profile: Profile = {
  id: 'profile', version: 1, current_revision: { id: 'profile-r1', revision_no: 1, content_sha256: 'a'.repeat(64), created_at: '2026-01-01', author_owner_id: 'owner',
    reason: 'Initial', claims: [oldClaim], snapshot: { display_name: 'Mira', headline: 'Engineer', claim_revision_ids: ['claim-r1'] } }, evidence: [], quality_warnings: [],
};
const quote = '😀 Maintained fictional systems.';
const source = `Experience\n${quote}\nSkills\nFictional SQL\n<script>Do not execute this source</script>`;
const start = Array.from(source.slice(0, source.indexOf(quote))).length;
const skillStart = Array.from(source.slice(0, source.indexOf('Fictional SQL'))).length;
function staged(): ImportBatch {
  return { id: 'import-one', workflow_id: 'workflow-one', state: 'waiting_for_approval', version: 4, media_type: 'text/plain', byte_size: 150,
    created_at: '2026-01-01', source_sha256: 'b'.repeat(64), text: source, profile_version: 1, error_code: null,
    warnings: ['deterministic_literal_extraction', 'evidence_is_owner_asserted_not_independently_verified'], proposals: [
      { proposal_id: 'proposal-work', kind: 'experience', payload: { text: quote, organization: 'Fictional North', role: 'Analyst', start_date: '2020' }, source_start: start, source_end: start + Array.from(quote).length, quote },
      { proposal_id: 'proposal-skill', kind: 'skill', payload: { text: 'Fictional SQL' }, source_start: skillStart, source_end: skillStart + 13, quote: 'Fictional SQL' },
    ] };
}
interface ServerState {
  batch: ImportBatch; currentProfile: Profile; failures: Error[]; submissions: { body: ImportMergeRequest; version: number | undefined; key: boolean | string }[];
  pendingMerge: boolean; cancelVersions: (number | undefined)[];
}
function server(initial: ImportBatch = staged()) {
  const api = new Api();
  const state: ServerState = { batch: initial, currentProfile: profile, failures: [], submissions: [], pendingMerge: false, cancelVersions: [] };
  const request = vi.spyOn(api, 'request').mockImplementation(async <T,>(path: string, method = 'GET', body?: unknown, version?: number, key: boolean | string = false): Promise<T> => {
    if (path === '/imports' && method === 'GET') return { items: [state.batch, { ...staged(), id: 'import-two', workflow_id: 'workflow-two' }] } as T;
    if (path === '/imports/import-one' && method === 'GET') return state.batch as T;
    if (path === '/profile' && method === 'GET') return state.currentProfile as T;
    if (path === '/imports/import-one/merge' && method === 'POST') {
      state.submissions.push({ body: body as ImportMergeRequest, version, key });
      const failure = state.failures.shift(); if (failure) throw failure;
      state.pendingMerge = true; state.batch = { ...state.batch, state: 'pending', version: state.batch.version + 1 };
      return { import_id: state.batch.id, workflow_id: state.batch.workflow_id } as T;
    }
    if (path === '/workflows/workflow-one/cancel' && method === 'POST') {
      state.cancelVersions.push(version); state.pendingMerge = false; state.batch = { ...state.batch, state: 'cancelled', version: state.batch.version + 1 };
      return {
        id: 'workflow-one', kind: 'profile_import', state: 'cancelled', version: state.batch.version, attempt: 1, max_attempts: 3, result: null, last_error_code: null,
        created_at: '2026-10-25T12:00:00Z', updated_at: '2026-10-25T12:00:00Z', deadline_at: '2030-01-01T00:00:00Z', next_attempt_at: null,
        cancel_requested_at: '2026-10-25T12:00:00Z', lease_expires_at: null, last_heartbeat_at: null, queue_age_seconds: null,
        remaining_attempts: 2, cancelable: false, retryable: false, retry_block_reason: 'state_not_retryable', delivery_state: 'not_attempted',
        cancellation: { requested: true, local_fence_settled: true, provider_stopped: null, completed_effects_preserved: true, reason: 'local_publication_revoked_provider_may_continue' },
        usage: { status: 'not_applicable', model_runs: 0, inference_post_attempts: 0, inference_post_limit: null, input_tokens: null, output_tokens: null, token_budget: 0, tokens_used: 0, cost_budget_microunits: 0, cost_used_microunits: null, currency: 'USD' },
      } as T;
    }
    throw new ApiError(404, 'not_found');
  });
  return { api, state, request };
}
function Harness({ api, onProfile }: { api: Api; onProfile?: (value: Profile) => void }) {
  const [current, setCurrent] = useState(profile);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [otherDirty, setOtherDirty] = useState(false);
  const acceptProfile = useCallback((value: Profile) => { setCurrent(value); onProfile?.(value); }, [onProfile]);
  return <><button disabled={dirty || busy}>Other profile editor</button><button onClick={() => setOtherDirty(value => !value)}>Toggle competing editor</button>
    <output aria-label="Loaded profile revision">{current.current_revision.revision_no}</output>
    <ResumeImport api={api} profile={current} disabled={otherDirty} onProfile={acceptProfile} onDirtyChange={setDirty} onBusyChange={setBusy} /></>;
}
async function open(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: 'Load saved imports' }));
  await user.click(await screen.findByRole('button', { name: 'Open import import-one' }));
  await screen.findByRole('heading', { name: 'Import import-one' });
}
async function addFirstAndReviewAll(user: ReturnType<typeof userEvent.setup>) {
  await user.selectOptions(screen.getByLabelText('Decision for proposal 1'), 'add');
  await user.click(screen.getByLabelText('I reviewed proposal 1 and its source and field choices'));
  await user.click(screen.getByLabelText('I reviewed proposal 2 and its skip decision'));
}
function attest() { return screen.getByLabelText('I reviewed every proposal and attest that selected facts and edited values are accurate owner assertions, not independent verification'); }
function approve() { return screen.getByRole('button', { name: 'Approve and merge reviewed facts' }); }
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

describe('local résumé source and explicit review', () => {
  it('renders inert source and exact provenance; defaults to skip and requires review of skipped proposals too', async () => {
    const user = userEvent.setup(); const { api, state } = server(); const { container } = render(<Harness api={api} />);
    await open(user);
    expect(screen.getByLabelText('Decision for proposal 1')).toHaveValue('skip');
    expect(screen.getByLabelText('Decision for proposal 2')).toHaveValue('skip');
    expect(approve()).toBeDisabled(); expect(state.submissions).toHaveLength(0);
    await user.click(screen.getByText('Read complete normalized source text'));
    expect(screen.getByText((_text, element) => element?.tagName === 'PRE' && element.textContent === source)).toBeVisible(); expect(container.querySelector('script')).toBeNull();
    expect(screen.getByText(`Exact source quote (Unicode span ${start}–${start + Array.from(quote).length}, end exclusive):`)).toBeVisible();
    expect(screen.getByText('b'.repeat(64), { selector: 'code' })).toBeVisible();
    await user.selectOptions(screen.getByLabelText('Decision for proposal 1'), 'add');
    await user.click(screen.getByLabelText('I reviewed proposal 1 and its source and field choices'));
    await user.click(attest()); expect(approve()).toBeDisabled();
    await user.click(screen.getByLabelText('I reviewed proposal 2 and its skip decision'));
    expect(attest()).not.toBeChecked(); expect(approve()).toBeDisabled();
    await user.click(attest()); expect(approve()).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Other profile editor' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Open import import-two' })).toBeDisabled();
    const unload = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(unload); expect(unload.defaultPrevented).toBe(true);
    await user.click(screen.getByRole('button', { name: 'Toggle competing editor' })); expect(approve()).toBeDisabled();
    expect(screen.getByLabelText('Statement for proposal 1')).toHaveValue(quote);
  });

  it('keeps the actual raw file and its retry identity through network and session failures', async () => {
    const user = userEvent.setup(); const { api, state } = server({ ...staged(), state: 'pending', text: null, proposals: [], source_sha256: null });
    const upload = vi.spyOn(api, 'uploadImport').mockRejectedValueOnce(new TypeError('offline')).mockRejectedValueOnce(new ApiError(401, 'session_required'))
      .mockResolvedValue({ import_id: 'import-one', workflow_id: 'workflow-one' });
    render(<Harness api={api} />);
    const file = new File(['A fictional local résumé.'], 'fictional.txt', { type: 'text/plain' });
    await user.upload(screen.getByLabelText('Résumé PDF or text file'), file);
    await user.click(screen.getByRole('button', { name: 'Upload for local extraction' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('server could not be reached');
    expect(screen.getByLabelText('Résumé PDF or text file')).toHaveProperty('files', expect.objectContaining({ 0: file }));
    await user.click(screen.getByRole('button', { name: 'Upload for local extraction' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('session is unavailable');
    expect(screen.getByText(/Selected: fictional.txt/)).toBeVisible();
    expect(upload.mock.calls[0]?.[0]).toBe(file); expect(upload.mock.calls[1]?.[0]).toBe(file);
    expect(upload.mock.calls[1]?.[3]).toBe(upload.mock.calls[0]?.[3]);
    await user.click(screen.getByRole('button', { name: 'Upload for local extraction' }));
    expect(await screen.findByText(/Source accepted for isolated local extraction/)).toBeVisible();
    expect(screen.getByLabelText('Résumé PDF or text file')).toHaveValue(''); expect(state.currentProfile).toBe(profile);
    expect(screen.queryByText(/Import completed/)).not.toBeInTheDocument();
  });

  it('uploads pasted text as a bounded Blob, retains it on failure, and prevents oversized uploads', async () => {
    const user = userEvent.setup(); const { api } = server();
    const upload = vi.spyOn(api, 'uploadImport').mockRejectedValue(new TypeError('offline'));
    render(<Harness api={api} />);
    await user.type(screen.getByLabelText('Or paste résumé text'), 'Fictional literal experience.');
    await user.click(screen.getByRole('button', { name: 'Upload for local extraction' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('server could not be reached');
    expect(screen.getByLabelText('Or paste résumé text')).toHaveValue('Fictional literal experience.');
    const [blob, media] = upload.mock.calls[0] ?? [];
    expect(blob).toBeInstanceOf(Blob); expect(blob?.size).toBe(new Blob(['Fictional literal experience.']).size); expect(media).toBe('text/plain');
    await user.click(screen.getByRole('button', { name: 'Upload for local extraction' })); expect(upload.mock.calls[1]?.[3]).toBe(upload.mock.calls[0]?.[3]);
    await user.click(screen.getByRole('button', { name: 'Discard source buffer' }));
    const huge = new File([new Uint8Array(MAX_IMPORT_BYTES + 1)], 'huge.pdf', { type: 'application/pdf' });
    await user.upload(screen.getByLabelText('Résumé PDF or text file'), huge);
    await user.click(screen.getByRole('button', { name: 'Upload for local extraction' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('no larger than 10 MiB'); expect(upload).toHaveBeenCalledTimes(2);
    expect(screen.getByText(/Selected: huge.pdf/)).toBeVisible();
  });
});

describe('reviewed conflict resolution and durable completion', () => {
  it('requires field choices, rebases safely after 412 without losing edits, and refreshes only after asynchronous success', async () => {
    const user = userEvent.setup(); const { api, state } = server(); const acceptedProfiles = vi.fn(); render(<Harness api={api} onProfile={acceptedProfiles} />);
    await open(user);
    await user.selectOptions(screen.getByLabelText('Decision for proposal 1'), 'replace');
    await user.selectOptions(screen.getByLabelText('Replacement target for proposal 1'), 'claim-work');
    expect(screen.getByText('Current: Existing fictional work.')).toBeVisible(); expect(screen.getByText(`Imported: ${quote}`)).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Keep current statement for proposal 1' }));
    await user.clear(screen.getByLabelText('Statement for proposal 1')); await user.type(screen.getByLabelText('Statement for proposal 1'), 'My retained typed statement.');
    await user.click(screen.getByLabelText('I reviewed proposal 1 and its source and field choices'));
    await user.click(screen.getByLabelText('I reviewed proposal 2 and its skip decision')); await user.click(attest());
    expect(approve()).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Keep current role or qualification for proposal 1' }));
    await user.click(screen.getByLabelText('I reviewed proposal 1 and its source and field choices')); await user.click(attest());
    state.failures.push(new ApiError(412, 'version_conflict'));
    await user.click(approve()); expect(await screen.findByRole('alert')).toHaveTextContent('A newer revision exists');
    expect(screen.getByLabelText('Statement for proposal 1')).toHaveValue('My retained typed statement.');
    const newerClaim = { ...oldClaim, revision: { ...oldClaim.revision, payload: { ...oldClaim.revision.payload, role: 'Senior engineer' } } };
    state.currentProfile = { ...profile, version: 2, current_revision: { ...profile.current_revision, id: 'profile-r2', revision_no: 2, claims: [newerClaim] } };
    await user.click(screen.getByRole('button', { name: 'Load current profile for comparison' }));
    expect(await screen.findByRole('heading', { name: 'Current server profile revision 2' })).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Use latest profile and retain review' }));
    expect(screen.getByLabelText('Decision for proposal 1')).toHaveValue('replace'); expect(screen.getByLabelText('Decision for proposal 2')).toHaveValue('skip');
    expect(screen.getByLabelText('Statement for proposal 1')).toHaveValue('My retained typed statement.');
    expect(screen.getByText('Current: Senior engineer')).toBeVisible(); expect(attest()).not.toBeChecked(); expect(approve()).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Keep current role or qualification for proposal 1' }));
    expect(screen.getByLabelText('Role or qualification for proposal 1')).toHaveValue('Senior engineer');
    await user.click(screen.getByLabelText('I reviewed proposal 1 and its source and field choices')); await user.click(attest()); await user.click(approve());
    expect(await screen.findByText(/Reviewed merge is pending/)).toBeVisible();
    expect(screen.getByRole('button', { name: 'Other profile editor' })).toBeDisabled(); expect(screen.getByLabelText('Loaded profile revision')).toHaveTextContent('2');
    expect(state.submissions[1]?.version).toBe(2); expect(state.submissions[1]?.key).not.toBe(state.submissions[0]?.key);
    const reviewed = state.submissions[1]?.body.decisions[0]; expect(reviewed?.payload.text).toBe('My retained typed statement.'); expect(reviewed?.payload.role).toBe('Senior engineer');
    state.currentProfile = { ...state.currentProfile, version: 3, current_revision: { ...state.currentProfile.current_revision, id: 'profile-r3', revision_no: 3 } };
    state.batch = { ...state.batch, state: 'succeeded', version: 7 };
    await user.click(screen.getByRole('button', { name: 'Reload selected import' }));
    expect(await screen.findByText(/Import completed. Loaded profile revision 3/)).toBeVisible();
    expect(screen.getByLabelText('Loaded profile revision')).toHaveTextContent('3');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Other profile editor' })).toBeEnabled());
  });

  it('retains reviewed edits and retry key across session/network failures, but changes identity when the reviewed payload changes', async () => {
    const user = userEvent.setup(); const { api, state } = server(); render(<Harness api={api} />); await open(user); await addFirstAndReviewAll(user);
    state.failures.push(new ApiError(401, 'session_required'), new TypeError('offline'), new TypeError('offline'));
    await user.click(attest()); await user.click(approve()); expect(await screen.findByRole('alert')).toHaveTextContent('session is unavailable');
    expect(screen.getByLabelText('Statement for proposal 1')).toHaveValue(quote); expect(attest()).toBeChecked();
    await user.click(screen.getByRole('button', { name: 'Reload selected import' }));
    expect(screen.getByLabelText('Statement for proposal 1')).toHaveValue(quote);
    expect(screen.getByLabelText('I reviewed proposal 1 and its source and field choices')).toBeChecked(); expect(attest()).toBeChecked();
    await user.click(approve()); expect(await screen.findByRole('alert')).toHaveTextContent('server could not be reached');
    expect(state.submissions[1]?.key).toBe(state.submissions[0]?.key);
    await user.type(screen.getByLabelText('Statement for proposal 1'), ' Owner correction.'); expect(attest()).not.toBeChecked();
    await user.click(screen.getByLabelText('I reviewed proposal 1 and its source and field choices')); await user.click(attest()); await user.click(approve());
    expect(await screen.findByRole('alert')).toHaveTextContent('server could not be reached');
    expect(state.submissions[2]?.key).not.toBe(state.submissions[1]?.key); expect(screen.getByLabelText('Statement for proposal 1')).toHaveValue(`${quote} Owner correction.`);
  });

  it('separately confirms replace-all and cancels the real workflow without losing the reviewed buffer or refreshing the profile', async () => {
    const user = userEvent.setup(); const { api, state } = server(); const acceptedProfiles = vi.fn(); render(<Harness api={api} onProfile={acceptedProfiles} />);
    await open(user); await addFirstAndReviewAll(user); await user.click(attest());
    await user.selectOptions(screen.getByLabelText('Import mode'), 'replace_all'); expect(attest()).not.toBeChecked();
    await user.click(attest()); expect(approve()).toBeDisabled();
    await user.click(screen.getByLabelText('I separately confirm replacing all non-identity facts and archiving displaced sources'));
    expect(attest()).not.toBeChecked(); await user.click(attest()); expect(approve()).toBeEnabled();
    await user.click(approve()); expect(await screen.findByText(/Reviewed merge is pending/)).toBeVisible();
    expect(state.submissions[0]?.body.mode).toBe('replace_all'); expect(state.submissions[0]?.body.replace_all_confirmed).toBe(true);
    await user.click(screen.getByRole('button', { name: 'Cancel import' }));
    expect(await screen.findByText(/State: cancelled/)).toBeVisible(); expect(state.cancelVersions).toEqual([5]);
    expect(screen.getByLabelText('Statement for proposal 1')).toHaveValue(quote); expect(acceptedProfiles).not.toHaveBeenCalled();
    expect(screen.getByLabelText('Loaded profile revision')).toHaveTextContent('1');
    await user.click(screen.getByRole('button', { name: 'Discard review choices' }));
    expect(screen.getByRole('button', { name: 'Other profile editor' })).toBeEnabled();
  });

  it('shows a stale atomic merge failure without claiming completion or discarding reviewed text', async () => {
    const user = userEvent.setup(); const { api, state } = server(); const acceptedProfiles = vi.fn(); render(<Harness api={api} onProfile={acceptedProfiles} />);
    await open(user); await addFirstAndReviewAll(user); await user.click(attest()); await user.click(approve());
    state.batch = { ...state.batch, state: 'permanent_failure', version: 6, error_code: 'profile_version_conflict' };
    await user.click(screen.getByRole('button', { name: 'Reload selected import' }));
    expect(await screen.findByText(/Import stopped \(profile_version_conflict\)/)).toBeVisible();
    expect(screen.getByLabelText('Statement for proposal 1')).toHaveValue(quote); expect(acceptedProfiles).not.toHaveBeenCalled();
    expect(screen.queryByText(/Import completed/)).not.toBeInTheDocument();
  });

  it('pauses active polling while hidden and stops once extraction reaches review', async () => {
    vi.useFakeTimers(); let hidden = false; vi.spyOn(document, 'hidden', 'get').mockImplementation(() => hidden);
    const { api, state, request } = server({ ...staged(), state: 'pending', text: null, proposals: [], source_sha256: null });
    render(<ResumeImport api={api} profile={profile} disabled={false} onProfile={() => undefined} />);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Load saved imports' })); });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Open import import-one' })); });
    const reads = () => request.mock.calls.filter(call => call[0] === '/imports/import-one').length;
    expect(reads()).toBe(1);
    hidden = true; act(() => { fireEvent(document, new Event('visibilitychange')); });
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); }); expect(reads()).toBe(1);
    state.batch = staged(); hidden = false; act(() => { fireEvent(document, new Event('visibilitychange')); });
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(reads()).toBe(2); expect(screen.getByLabelText('Decision for proposal 1')).toHaveValue('skip');
    await act(async () => { await vi.advanceTimersByTimeAsync(9000); }); expect(reads()).toBe(2);
  });
});
