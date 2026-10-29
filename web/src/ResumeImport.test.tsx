// SPDX-License-Identifier: MIT OR Apache-2.0
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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
    reason: 'Initial', claims: [oldClaim], snapshot: { display_name: 'Mira', headline: 'Engineer', claim_revision_ids: ['claim-r1'] } }, evidence: [], quality_warnings: [], experience_coverage: { minimum_days: 0, maximum_days: 0, undated_or_open_entries: 0 },
};
const quote = '😀 Maintained fictional systems.';
const source = `Experience\n${quote}\nSkills\nFictional SQL\n<script>Do not execute this source</script>`;
const start = Array.from(source.slice(0, source.indexOf(quote))).length;
const skillStart = Array.from(source.slice(0, source.indexOf('Fictional SQL'))).length;
function staged(): ImportBatch {
  return { id: 'import-one', workflow_id: 'workflow-one', state: 'waiting_for_approval', version: 4, media_type: 'text/plain', byte_size: 150,
    created_at: '2026-01-01', source_sha256: 'b'.repeat(64), text: source, profile_version: 1, error_code: null,
    warnings: ['deterministic_literal_extraction', 'evidence_is_owner_asserted_not_independently_verified'], entries: [
      { entry_id: 'entry-work', kind: 'experience', identity_field: null, payload: { text: quote, organization: 'Fictional North', role: 'Analyst', start_date: '2020' }, sources: [{ source_id: 'source-work', start, end: start + Array.from(quote).length, quote }], issues: [] },
      { entry_id: 'entry-skill', kind: 'skill', identity_field: null, payload: { text: 'Fictional SQL' }, sources: [{ source_id: 'source-skill', start: skillStart, end: skillStart + 13, quote: 'Fictional SQL' }], issues: [] },
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
  const [visible, setVisible] = useState(true);
  const acceptProfile = useCallback((value: Profile) => { setCurrent(value); onProfile?.(value); }, [onProfile]);
  return <><button disabled={dirty || busy}>Other profile editor</button><button onClick={() => setOtherDirty(value => !value)}>Toggle competing editor</button>
    <button onClick={() => setVisible(value => !value)}>Toggle import visibility</button>
    <output aria-label="Loaded profile revision">{current.current_revision.revision_no}</output>
    <div hidden={!visible}><ResumeImport api={api} profile={current} disabled={otherDirty} onProfile={acceptProfile} onDirtyChange={setDirty} onBusyChange={setBusy} /></div></>;
}
async function open(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: 'Load saved imports' }));
  await user.click(await screen.findByRole('button', { name: 'Open import import-one' }));
  await screen.findByRole('heading', { name: 'Import import-one' });
}
async function addFirstAndReviewAll(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: 'Keep entry' }));
  await user.click(screen.getByRole('button', { name: 'Next entry' }));
  await user.click(screen.getByRole('button', { name: 'Exclude entry' }));
  await user.click(screen.getByRole('button', { name: 'Previous entry' }));
}
function attest() { return screen.getByLabelText('I reviewed every entry and confirm the kept values are accurate owner assertions, not independent verification'); }
function approve() { return screen.getByRole('button', { name: /^Save \d+ profile entr/ }); }
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

describe('structured résumé review', () => {
  it('leaves entries unresolved until explicitly kept or excluded and preserves inert exact Unicode provenance', async () => {
    const user = userEvent.setup(); const { api, state } = server(); const { container } = render(<Harness api={api} />);
    await open(user);
    expect(screen.getAllByLabelText('Save to')).toHaveLength(1);
    expect(screen.getByLabelText('Save to')).toHaveValue('experience');
    expect(within(screen.getByRole('region', { name: 'Saved value preview' })).getByText(quote)).toBeVisible();
    expect(attest()).toBeDisabled(); expect(approve()).toBeDisabled();
    await user.click(attest()); expect(attest()).not.toBeChecked();
    await user.click(screen.getByText('Read complete normalized source text'));
    expect(screen.getByText((_text, element) => element?.tagName === 'PRE' && element.textContent === source)).toBeVisible();
    expect(container.querySelector('script')).toBeNull();
    await user.click(screen.getByText('View original source and provenance'));
    expect(screen.getByText(`Exact source quote (Unicode span ${start}–${start + Array.from(quote).length}, end exclusive):`)).toBeVisible();
    expect(screen.getByText('b'.repeat(64), { selector: 'code' })).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Keep entry' }));
    expect(attest()).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Next entry needing review' }));
    expect(screen.getByText('Not reviewed')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Exclude entry' }));
    expect(screen.getByText('1 ready to save · 1 excluded · 0 need review or placement')).toBeVisible();
    expect(attest()).toBeEnabled(); await user.click(attest()); expect(approve()).toBeEnabled();
    expect(state.submissions).toHaveLength(0);
    expect(screen.getByRole('button', { name: 'Other profile editor' })).toBeDisabled();
    expect(screen.getByLabelText('Saved import')).toBeDisabled();
    const unload = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(unload); expect(unload.defaultPrevented).toBe(true);
    await user.click(screen.getByRole('button', { name: 'Toggle competing editor' })); expect(approve()).toBeDisabled();
  });

  it('requires placement, shows consumer fields and invalidates kept decisions and attestation when edited', async () => {
    const user = userEvent.setup(); const batch = staged();
    batch.entries[0] = { ...batch.entries[0]!, kind: null, payload: { text: quote }, issues: ['needs_placement'] };
    const { api, state } = server(batch); render(<Harness api={api} />); await open(user);
    expect(screen.getByLabelText('Save to')).toHaveValue('');
    expect(screen.getByRole('button', { name: 'Keep entry' })).toBeDisabled();
    const picker = screen.getByLabelText('Save to');
    for (const value of ['display_name', 'headline', 'experience', 'education', 'skill', 'project', 'certification', 'language', 'preference', 'other']) {
      expect(picker.querySelector(`option[value="${value}"]`)).not.toBeNull();
    }
    await user.selectOptions(picker, 'education'); await user.click(screen.getByRole('button', { name: 'Edit entry' }));
    expect(screen.getByLabelText('School or institution')).toHaveValue('');
    expect(screen.getByLabelText('Qualification')).toHaveValue('');
    await user.type(screen.getByLabelText('School or institution'), 'Fictional College');
    await user.click(screen.getByRole('button', { name: 'Keep changes' }));
    await user.click(screen.getByRole('button', { name: 'Next entry' })); await user.click(screen.getByRole('button', { name: 'Exclude entry' }));
    await user.click(attest()); expect(approve()).toBeEnabled();
    await user.click(screen.getByRole('button', { name: 'Previous entry' }));
    await user.click(screen.getByRole('button', { name: 'Edit entry' }));
    expect(attest()).not.toBeChecked(); expect(attest()).toBeDisabled();
    await user.clear(screen.getByLabelText('Description'));
    expect(screen.getByRole('button', { name: 'Keep changes' })).toBeDisabled();
    await user.type(screen.getByLabelText('Description'), 'Fictional qualification.');
    await user.click(screen.getByRole('button', { name: 'Keep changes' }));
    await user.selectOptions(screen.getByLabelText('Save to'), 'skill');
    expect(screen.getByText('Not reviewed')).toBeVisible(); expect(attest()).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Keep entry' })); await user.click(attest()); await user.click(approve());
    expect(state.submissions[0]?.body.decisions[0]).toMatchObject({ kind: 'skill', identity_field: null, source_ids: ['source-work'], payload: { text: 'Fictional qualification.' } });
  });

  it('never saves an all-excluded selection and keeps PDF layout warnings discoverable', async () => {
    const user = userEvent.setup(); const batch = staged(); batch.media_type = 'application/pdf'; batch.warnings.push('pdf_layout_requires_review');
    const { api, state } = server(batch); render(<Harness api={api} />); await open(user);
    expect(screen.getByRole('list', { name: 'Import warnings' })).toHaveTextContent('PDF layout');
    await user.click(screen.getByRole('button', { name: 'Exclude entry' }));
    await user.click(screen.getByRole('button', { name: 'Next entry' })); await user.click(screen.getByRole('button', { name: 'Exclude entry' }));
    expect(attest()).toBeDisabled(); expect(approve()).toBeDisabled(); expect(state.submissions).toHaveLength(0);
  });

  it('combines neighboring entries without losing source IDs and retains grouping on reload', async () => {
    const user = userEvent.setup(); const { api, state } = server(); render(<Harness api={api} />); await open(user);
    await addFirstAndReviewAll(user); await user.click(attest());
    await user.click(screen.getByRole('button', { name: 'Combine with next entry' }));
    expect(screen.getByText('Entry 1 of 1')).toBeVisible(); expect(screen.getByLabelText('Save to')).toHaveValue('');
    expect(attest()).not.toBeChecked(); expect(attest()).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Reload selected import' }));
    expect(screen.getByText('Entry 1 of 1')).toBeVisible();
    await user.selectOptions(screen.getByLabelText('Save to'), 'other');
    await user.click(screen.getByRole('button', { name: 'Keep entry' })); await user.click(attest()); await user.click(approve());
    expect(state.submissions[0]?.body.decisions).toHaveLength(1);
    expect(state.submissions[0]?.body.decisions[0]).toMatchObject({ source_ids: ['source-work', 'source-skill'], kind: 'other', action: 'add', payload: { text: `${quote}\nFictional SQL` } });
  });

  it('splits grouped entries into a complete original-source partition, requiring independent decisions', async () => {
    const user = userEvent.setup(); const batch = staged();
    batch.entries = [{ ...batch.entries[0]!, sources: [...batch.entries[0]!.sources, ...batch.entries[1]!.sources] }];
    const { api, state } = server(batch); render(<Harness api={api} />); await open(user);
    await user.click(screen.getByRole('button', { name: 'Keep entry' })); await user.click(attest());
    await user.click(screen.getByRole('button', { name: 'Split into original source parts' }));
    expect(attest()).not.toBeChecked(); expect(attest()).toBeDisabled();
    expect(screen.getByText('Entry 1 of 2')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Keep entry' }));
    await user.click(screen.getByRole('button', { name: 'Next entry' }));
    expect(within(screen.getByRole('region', { name: 'Saved value preview' })).getByText('Fictional SQL')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Exclude entry' })); await user.click(attest()); await user.click(approve());
    expect(state.submissions[0]?.body.decisions.map(decision => ({ source_ids: decision.source_ids, action: decision.action }))).toEqual([
      { source_ids: ['source-work'], action: 'add' }, { source_ids: ['source-skill'], action: 'skip' },
    ]);
  });

  it('retains unconfirmed edited buffers across navigation, visibility and reload without silently reviewing them', async () => {
    const user = userEvent.setup(); const { api } = server(); render(<Harness api={api} />); await open(user);
    await user.click(screen.getByRole('button', { name: 'Edit entry' }));
    await user.type(screen.getByLabelText('Description'), ' Unconfirmed correction.');
    await user.click(screen.getByRole('button', { name: 'Next entry' }));
    await user.click(screen.getByRole('button', { name: 'Previous entry' }));
    await user.click(screen.getByRole('button', { name: 'Toggle import visibility' }));
    await user.click(screen.getByRole('button', { name: 'Toggle import visibility' }));
    await user.click(screen.getByRole('button', { name: 'Reload selected import' }));
    expect(screen.getByLabelText('Description')).toHaveValue(`${quote} Unconfirmed correction.`);
    expect(screen.getByText('Not reviewed')).toBeVisible(); expect(attest()).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Other profile editor' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Discard review choices' }));
    expect(screen.queryByLabelText('Description')).not.toBeInTheDocument();
    expect(within(screen.getByRole('region', { name: 'Saved value preview' })).getByText(quote)).toBeVisible();
    expect(screen.getByRole('button', { name: 'Other profile editor' })).toBeEnabled();
  });

  it('blocks impossible or reversed dates and unsafe project URLs before allowing Keep changes', async () => {
    const user = userEvent.setup(); const { api } = server(); render(<Harness api={api} />); await open(user);
    await user.selectOptions(screen.getByLabelText('Save to'), 'project');
    await user.click(screen.getByRole('button', { name: 'Edit entry' }));
    await user.type(screen.getByLabelText('Start date'), '2024-02-30');
    expect(screen.getByRole('button', { name: 'Keep changes' })).toBeDisabled();
    await user.clear(screen.getByLabelText('Start date')); await user.type(screen.getByLabelText('Start date'), '2024-02-29');
    await user.type(screen.getByLabelText('End date'), '2023');
    expect(screen.getByRole('button', { name: 'Keep changes' })).toBeDisabled();
    await user.clear(screen.getByLabelText('End date')); await user.type(screen.getByLabelText('End date'), '2024');
    await user.type(screen.getByLabelText('Project URL'), 'javascript:alert(1)');
    expect(screen.getByRole('button', { name: 'Keep changes' })).toBeDisabled();
    await user.clear(screen.getByLabelText('Project URL')); await user.type(screen.getByLabelText('Project URL'), 'https://user:secret@example.test/project');
    expect(screen.getByRole('button', { name: 'Keep changes' })).toBeDisabled();
    await user.clear(screen.getByLabelText('Project URL')); await user.type(screen.getByLabelText('Project URL'), 'https://example.test/project');
    expect(screen.getByRole('button', { name: 'Keep changes' })).toBeEnabled();
    await user.click(screen.getByRole('button', { name: 'Keep changes' }));
    expect(screen.getByText('Reviewed · Ready to save')).toBeVisible();
  });

  it('keeps one of 67 entries focused with readable navigation and mounted edited buffers', async () => {
    const user = userEvent.setup(); const batch = staged();
    const skills = Array.from({ length: 66 }, (_, index) => `Fictional skill ${index}`);
    batch.text = `Experience\n${quote}\nSkills\n${skills.join('\n')}`;
    batch.entries = [batch.entries[0]!, ...skills.map((text, index) => {
      const offset = Array.from(batch.text!.slice(0, batch.text!.indexOf(text))).length;
      return { ...batch.entries[1]!, entry_id: `skill-${index}`, payload: { text },
        sources: [{ source_id: `source-${index}`, start: offset, end: offset + text.length, quote: text }] };
    })];
    const { api, state } = server(batch); render(<Harness api={api} />); await open(user);
    await user.click(screen.getByRole('button', { name: 'Edit entry' }));
    await user.clear(screen.getByLabelText('Description')); await user.type(screen.getByLabelText('Description'), 'Retained fictional correction.');
    await user.click(screen.getByRole('button', { name: 'Keep changes' }));
    await user.click(screen.getByRole('button', { name: 'Next entry' }));
    await user.click(screen.getByRole('button', { name: 'Exclude entry' }));
    await user.click(screen.getByRole('button', { name: 'Toggle import visibility' }));
    await user.click(screen.getByRole('button', { name: 'Toggle import visibility' }));
    await user.click(screen.getByRole('button', { name: 'Previous entry' }));
    expect(within(screen.getByRole('region', { name: 'Saved value preview' })).getByText('Retained fictional correction.')).toBeVisible();
    expect(screen.getByLabelText('Go to entry').querySelector('option')).toHaveTextContent('Retained fictional correction. · Work experience · kept');
    await user.selectOptions(screen.getByLabelText('Go to entry'), '66'); await user.click(screen.getByRole('button', { name: 'Exclude entry' }));
    await user.click(screen.getByRole('button', { name: 'Next entry needing review' }));
    expect(screen.getByText('Entry 3 of 67')).toBeVisible();
    for (let number = 3; number <= 66; number += 1) {
      expect(screen.getAllByLabelText('Save to')).toHaveLength(1);
      await user.click(screen.getByRole('button', { name: 'Exclude entry' }));
      if (number < 66) await user.click(screen.getByRole('button', { name: 'Next entry needing review' }));
    }
    expect(attest()).toBeEnabled(); await user.click(attest()); await user.click(approve());
    expect(state.submissions[0]?.body.decisions).toHaveLength(67);
    expect(state.submissions[0]?.body.decisions[0]?.payload.text).toBe('Retained fictional correction.');
    expect(state.submissions[0]?.body.decisions.slice(1).every(decision => decision.action === 'skip')).toBe(true);
  }, 20000);
});

describe('local résumé source buffers', () => {
  it('keeps the actual raw file and its retry identity through network and session failures', async () => {
    const user = userEvent.setup(); const { api, state } = server({ ...staged(), state: 'pending', text: null, entries: [], source_sha256: null });
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
  it('requires every differing replacement field, rebases after 412 without losing edits and refreshes only after async success', async () => {
    const user = userEvent.setup(); const { api, state } = server(); const acceptedProfiles = vi.fn(); render(<Harness api={api} onProfile={acceptedProfiles} />);
    await open(user);
    await user.selectOptions(screen.getByLabelText('Existing profile entry'), 'replace');
    await user.selectOptions(screen.getByLabelText('Replacement target'), 'claim-work');
    expect(screen.getByText('Current: Existing fictional work.')).toBeVisible(); expect(screen.getByText(`Imported: ${quote}`)).toBeVisible();
    expect(screen.getByRole('button', { name: 'Keep entry' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Keep current description' }));
    await user.clear(screen.getByLabelText('Description')); await user.type(screen.getByLabelText('Description'), 'My retained typed statement.');
    expect(screen.getByRole('button', { name: 'Keep entry' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Keep current job title' }));
    await user.click(screen.getByRole('button', { name: 'Keep entry' }));
    await user.click(screen.getByRole('button', { name: 'Next entry' })); await user.click(screen.getByRole('button', { name: 'Exclude entry' }));
    await user.click(attest()); await user.click(screen.getByRole('button', { name: 'Previous entry' }));
    state.failures.push(new ApiError(412, 'version_conflict'));
    await user.click(approve()); expect(await screen.findByRole('alert')).toHaveTextContent('A newer revision exists');
    expect(screen.getByLabelText('Description')).toHaveValue('My retained typed statement.');
    const newerClaim = { ...oldClaim, revision: { ...oldClaim.revision, payload: { ...oldClaim.revision.payload, role: 'Senior engineer' } } };
    state.currentProfile = { ...profile, version: 2, current_revision: { ...profile.current_revision, id: 'profile-r2', revision_no: 2, claims: [newerClaim] } };
    await user.click(screen.getByRole('button', { name: 'Load current profile for comparison' }));
    await screen.findByRole('heading', { name: 'Current server profile revision 2' });
    await user.click(screen.getByRole('button', { name: 'Use latest profile and retain review' }));
    expect(screen.getByLabelText('Replacement target')).toHaveValue('claim-work');
    expect(screen.getByLabelText('Description')).toHaveValue('My retained typed statement.');
    expect(screen.getByText('Current: Senior engineer')).toBeVisible(); expect(attest()).not.toBeChecked(); expect(attest()).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Next entry' })); expect(screen.getByText('Reviewed · Excluded')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Previous entry' }));
    await user.click(screen.getByRole('button', { name: 'Keep current job title' }));
    expect(screen.getByLabelText('Job title')).toHaveValue('Senior engineer');
    await user.click(screen.getByRole('button', { name: 'Keep entry' })); await user.click(attest()); await user.click(approve());
    expect(await screen.findByText(/Reviewed merge is pending/)).toBeVisible();
    expect(screen.getByRole('button', { name: 'Other profile editor' })).toBeDisabled(); expect(screen.getByLabelText('Loaded profile revision')).toHaveTextContent('2');
    expect(state.submissions[1]?.version).toBe(2); expect(state.submissions[1]?.key).not.toBe(state.submissions[0]?.key);
    expect(state.submissions[1]?.body.decisions[0]).toMatchObject({ action: 'replace', claim_id: 'claim-work', payload: { text: 'My retained typed statement.', role: 'Senior engineer' } });
    state.currentProfile = { ...state.currentProfile, version: 3, current_revision: { ...state.currentProfile.current_revision, id: 'profile-r3', revision_no: 3 } };
    state.batch = { ...state.batch, state: 'succeeded', version: 7 };
    await user.click(screen.getByRole('button', { name: 'Reload selected import' }));
    expect(await screen.findByText(/Import completed. Loaded profile revision 3/)).toBeVisible();
    expect(screen.getByLabelText('Loaded profile revision')).toHaveTextContent('3');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Other profile editor' })).toBeEnabled());
  });

  it('retains review and retry identity across session/network failures, changing identity only when the payload changes', async () => {
    const user = userEvent.setup(); const { api, state } = server(); render(<Harness api={api} />); await open(user); await addFirstAndReviewAll(user);
    state.failures.push(new ApiError(401, 'session_required'), new TypeError('offline'), new TypeError('offline'));
    await user.click(attest()); await user.click(approve()); expect(await screen.findByRole('alert')).toHaveTextContent('session is unavailable');
    expect(attest()).toBeChecked();
    await user.click(screen.getByRole('button', { name: 'Reload selected import' }));
    expect(screen.getByText('Reviewed · Ready to save')).toBeVisible(); expect(attest()).toBeChecked();
    await user.click(approve()); expect(await screen.findByRole('alert')).toHaveTextContent('server could not be reached');
    expect(state.submissions[1]?.key).toBe(state.submissions[0]?.key);
    await user.click(screen.getByRole('button', { name: 'Edit entry' }));
    await user.type(screen.getByLabelText('Description'), ' Owner correction.'); expect(attest()).not.toBeChecked();
    await user.click(screen.getByRole('button', { name: 'Keep changes' })); await user.click(attest()); await user.click(approve());
    expect(await screen.findByRole('alert')).toHaveTextContent('server could not be reached');
    expect(state.submissions[2]?.key).not.toBe(state.submissions[1]?.key);
    expect(state.submissions[2]?.body.decisions[0]?.payload.text).toBe(`${quote} Owner correction.`);
  });

  it('separately confirms replace-all and cancels the real workflow without dropping review or refreshing the profile', async () => {
    const user = userEvent.setup(); const { api, state } = server(); const acceptedProfiles = vi.fn(); render(<Harness api={api} onProfile={acceptedProfiles} />);
    await open(user); await addFirstAndReviewAll(user); await user.click(attest());
    await user.selectOptions(screen.getByLabelText('Import mode'), 'replace_all');
    expect(attest()).not.toBeChecked(); expect(attest()).toBeDisabled(); expect(approve()).toBeDisabled();
    await user.click(screen.getByLabelText('I separately confirm replacing all non-identity facts and archiving displaced sources'));
    await user.click(attest()); await user.click(approve()); expect(await screen.findByText(/Reviewed merge is pending/)).toBeVisible();
    expect(state.submissions[0]?.body.mode).toBe('replace_all'); expect(state.submissions[0]?.body.replace_all_confirmed).toBe(true);
    await user.click(screen.getByRole('button', { name: 'Cancel import' }));
    expect(await screen.findByText(/State: cancelled/)).toBeVisible(); expect(state.cancelVersions).toEqual([5]);
    expect(within(screen.getByRole('region', { name: 'Saved value preview' })).getByText(quote)).toBeVisible(); expect(acceptedProfiles).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Discard review choices' }));
    expect(screen.getByRole('button', { name: 'Other profile editor' })).toBeEnabled();
  });

  it('retains review when atomic merge fails and never claims completion', async () => {
    const user = userEvent.setup(); const { api, state } = server(); const acceptedProfiles = vi.fn(); render(<Harness api={api} onProfile={acceptedProfiles} />);
    await open(user); await addFirstAndReviewAll(user); await user.click(attest()); await user.click(approve());
    state.batch = { ...state.batch, state: 'permanent_failure', version: 6, error_code: 'profile_version_conflict' };
    await user.click(screen.getByRole('button', { name: 'Reload selected import' }));
    expect(await screen.findByText(/Import stopped \(profile_version_conflict\)/)).toBeVisible();
    expect(within(screen.getByRole('region', { name: 'Saved value preview' })).getByText(quote)).toBeVisible(); expect(acceptedProfiles).not.toHaveBeenCalled();
    expect(screen.queryByText(/Import completed/)).not.toBeInTheDocument();
  });

  it('pauses active polling while hidden and stops once extraction reaches review', async () => {
    vi.useFakeTimers(); let hidden = false; vi.spyOn(document, 'hidden', 'get').mockImplementation(() => hidden);
    const { api, state, request } = server({ ...staged(), state: 'pending', text: null, entries: [], source_sha256: null });
    render(<ResumeImport api={api} profile={profile} disabled={false} onProfile={() => undefined} />);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Load saved imports' })); });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Open import import-one' })); });
    const reads = () => request.mock.calls.filter(call => call[0] === '/imports/import-one').length;
    expect(reads()).toBe(1);
    hidden = true; act(() => { fireEvent(document, new Event('visibilitychange')); });
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); }); expect(reads()).toBe(1);
    state.batch = staged(); hidden = false; act(() => { fireEvent(document, new Event('visibilitychange')); });
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(reads()).toBe(2); expect(screen.getByLabelText('Save to')).toHaveValue('experience');
    expect(screen.getByText('Not reviewed')).toBeVisible();
    await act(async () => { await vi.advanceTimersByTimeAsync(9000); }); expect(reads()).toBe(2);
  });
});

describe('personal identity review', () => {
  it('requires an explicit populated-field comparison, invalidates it after editing and submits a canonical identity replacement', async () => {
    const user = userEvent.setup(); const batch = staged(); batch.entries = [batch.entries[0]!];
    const { api, state } = server(batch); render(<Harness api={api} />); await open(user);
    await user.selectOptions(screen.getByLabelText('Save to'), 'display_name');
    expect(screen.getByText('Current: Mira')).toBeVisible(); expect(screen.getByText(`Imported: ${quote}`)).toBeVisible();
    expect(screen.getByRole('button', { name: 'Keep entry' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Edit entry' }));
    expect(screen.getByLabelText('Full name')).toHaveAttribute('maxlength', '120');
    await user.clear(screen.getByLabelText('Full name')); await user.type(screen.getByLabelText('Full name'), 'Fictional Candidate');
    await user.click(screen.getByLabelText('Replace my current full name with this value'));
    await user.click(screen.getByRole('button', { name: 'Keep changes' })); await user.click(attest());
    await user.click(screen.getByRole('button', { name: 'Edit entry' }));
    await user.type(screen.getByLabelText('Full name'), ' Jr');
    expect(screen.getByLabelText('Replace my current full name with this value')).not.toBeChecked();
    expect(screen.getByRole('button', { name: 'Keep changes' })).toBeDisabled(); expect(attest()).not.toBeChecked();
    await user.click(screen.getByLabelText('Replace my current full name with this value'));
    await user.click(screen.getByRole('button', { name: 'Keep changes' })); await user.click(attest()); await user.click(approve());
    expect(state.submissions[0]?.body.decisions[0]).toEqual({ source_ids: ['source-work'], action: 'replace', kind: 'identity', identity_field: 'display_name', payload: { text: 'Fictional Candidate Jr' } });
  });

  it('adds an empty personal field without a professional claim replacement target', async () => {
    const user = userEvent.setup(); const batch = staged(); batch.entries = [{ ...batch.entries[0]!, kind: 'identity', identity_field: 'headline', payload: { text: 'Fictional engineer' } }];
    const { api, state } = server(batch);
    const empty = { ...profile, current_revision: { ...profile.current_revision, snapshot: { ...profile.current_revision.snapshot, headline: '' } } };
    render(<ResumeImport api={api} profile={empty} disabled={false} onProfile={() => undefined} />); await open(user);
    expect(screen.queryByLabelText('Replacement target')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Keep entry' })); await user.click(attest()); await user.click(approve());
    expect(state.submissions[0]?.body.decisions[0]).toMatchObject({ action: 'add', kind: 'identity', identity_field: 'headline' });
    expect(state.submissions[0]?.body.decisions[0]).not.toHaveProperty('claim_id');
  });

  it('blocks duplicate personal destinations and requires comparison again when the canonical current field changes on rebase', async () => {
    const user = userEvent.setup(); const { api, state } = server(); render(<Harness api={api} />); await open(user);
    for (let index = 0; index < 2; index += 1) {
      await user.selectOptions(screen.getByLabelText('Save to'), 'headline');
      await user.click(screen.getByLabelText('Replace my current headline with this value'));
      await user.click(screen.getByRole('button', { name: 'Keep entry' }));
      if (!index) await user.click(screen.getByRole('button', { name: 'Next entry' }));
    }
    expect(attest()).toBeDisabled(); expect(approve()).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Exclude entry' }));
    await user.click(attest()); state.failures.push(new ApiError(412, 'version_conflict')); await user.click(approve());
    state.currentProfile = { ...profile, version: 2, current_revision: { ...profile.current_revision, revision_no: 2, snapshot: { ...profile.current_revision.snapshot, headline: 'Changed current headline' } } };
    await user.click(screen.getByRole('button', { name: 'Load current profile for comparison' }));
    await screen.findByRole('heading', { name: 'Current server profile revision 2' });
    await user.click(screen.getByRole('button', { name: 'Use latest profile and retain review' }));
    await user.click(screen.getByRole('button', { name: 'Previous entry' }));
    expect(screen.getByText('Current: Changed current headline')).toBeVisible();
    expect(screen.getByLabelText('Replace my current headline with this value')).not.toBeChecked();
    expect(attest()).toBeDisabled(); expect(screen.getByRole('button', { name: 'Keep entry' })).toBeDisabled();
    await user.click(screen.getByLabelText('Replace my current headline with this value'));
    await user.click(screen.getByRole('button', { name: 'Keep entry' })); await user.click(attest()); await user.click(approve());
    expect(state.submissions[1]?.version).toBe(2);
    expect(state.submissions[1]?.body.decisions[0]?.payload.text).toBe(quote);
  });
});
