// SPDX-License-Identifier: MIT OR Apache-2.0
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useCallback, useState } from 'react';
import { ResumeImport } from './ResumeImport';
import { Api, ApiError, MAX_IMPORT_BYTES, type ClaimRecord, type ImportBatch, type ImportEntry, type ImportMergeRequest, type Profile } from './api';

const oldPayload = { text: 'Existing fictional work.', organization: 'Fictional North', role: 'Engineer', start_date: '2020', end_date: '2022' };
const oldClaim: ClaimRecord = {
  id: 'claim-work', kind: 'experience', archived: false, version: 1, current_revision_id: 'claim-r1', rendering: oldPayload.text, needs_attention: true,
  revision: { id: 'claim-r1', revision_no: 1, content_sha256: 'c'.repeat(64), created_at: '2026-01-01', author_owner_id: 'owner',
    payload: oldPayload, evidence_links: [] },
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
    created_at: '2026-01-01', source_sha256: 'b'.repeat(64), text: source, profile_version: 1, comparison_profile_version: 1, error_code: null,
    warnings: ['deterministic_literal_extraction', 'evidence_is_owner_asserted_not_independently_verified'], entries: [
      { entry_id: 'entry-work', kind: 'experience', identity_field: null, payload: { text: quote, organization: 'Fictional North', role: 'Analyst', start_date: '2020' }, sources: [{ source_id: 'source-work', start, end: start + Array.from(quote).length, quote }], issues: [], comparison: { status: 'new', target_claim_id: null, candidate_claim_ids: [] } },
      { entry_id: 'entry-skill', kind: 'skill', identity_field: null, payload: { text: 'Fictional SQL' }, sources: [{ source_id: 'source-skill', start: skillStart, end: skillStart + 13, quote: 'Fictional SQL' }], issues: [], comparison: { status: 'new', target_claim_id: null, candidate_claim_ids: [] } },
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
async function edit(user: ReturnType<typeof userEvent.setup>, number = 1) {
  await user.click(screen.getByRole('button', { name: `Edit preview entry ${number}` }));
}
async function exclude(user: ReturnType<typeof userEvent.setup>, number = 2) {
  await edit(user, number);
  await user.click(screen.getByRole('button', { name: 'Exclude entry' }));
}
function attest() { return screen.getByLabelText('I approve this assembled profile and confirm its values are accurate owner assertions, not independent verification'); }
function approve() { return screen.getByRole('button', { name: /^Save profile$|^Complete import without profile changes$/ }); }
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

describe('structured résumé review', () => {
  it('keeps the saved description visible in merge and replace-all previews without making it an imported fact', async () => {
    const user = userEvent.setup(); const { api, state } = server();
    const described = { ...profile, current_revision: { ...profile.current_revision, snapshot: { ...profile.current_revision.snapshot, description: 'Saved personal introduction.' } } };
    render(<ResumeImport api={api} profile={described} disabled={false} onProfile={vi.fn()} />);
    await open(user);
    const preview = within(screen.getByRole('region', { name: 'Assembled profile preview' }));
    expect(preview.getByText('Saved personal introduction.')).toBeVisible();
    await user.selectOptions(screen.getByLabelText('Import mode'), 'replace_all');
    expect(preview.getByText('Saved personal introduction.')).toBeVisible();
    await user.click(screen.getByLabelText('I separately confirm replacing all non-identity facts and archiving displaced sources'));
    await user.click(attest()); await user.click(approve());
    expect(state.submissions[0]?.body.decisions.every(decision => decision.kind !== 'identity')).toBe(true);
  });

  it('validates imported headline limits in Unicode codepoints and keeps oversized pasted values for correction', async () => {
    const user = userEvent.setup(); const batch = staged();
    batch.entries = [{ ...batch.entries[0]!, kind: 'identity', identity_field: 'headline', payload: { text: '🚀'.repeat(300) } }];
    const { api, state } = server(batch);
    const empty = { ...profile, current_revision: { ...profile.current_revision, snapshot: { ...profile.current_revision.snapshot, headline: '' } } };
    render(<ResumeImport api={api} profile={empty} disabled={false} onProfile={vi.fn()} />); await open(user);
    expect(attest()).toBeEnabled();
    await edit(user);
    const headline = screen.getByLabelText('Highlight / tagline');
    expect(headline).toHaveValue('🚀'.repeat(300));
    await user.click(headline); await user.keyboard('{End}'); await user.paste('🚀');
    expect(headline).toHaveValue('🚀'.repeat(301));
    expect(attest()).toBeDisabled(); expect(state.submissions).toHaveLength(0);
    fireEvent.change(headline, { target: { value: '🚀'.repeat(300) } });
    expect(attest()).toBeEnabled();
    await user.click(attest()); await user.click(approve());
    expect(state.submissions[0]?.body.decisions[0]?.payload.text).toBe('🚀'.repeat(300));
  });

  it('shows the entire first profile immediately and saves clean entries with one batch approval', async () => {
    const user = userEvent.setup(); const { api, state } = server();
    const empty = { ...profile, current_revision: { ...profile.current_revision, claims: [], snapshot: { ...profile.current_revision.snapshot, display_name: '', headline: '', claim_revision_ids: [] } } };
    render(<ResumeImport api={api} profile={empty} disabled={false} onProfile={() => undefined} />); await open(user);
    const work = within(screen.getByRole('region', { name: 'Work experience' }));
    const skills = within(screen.getByRole('region', { name: 'Skills' }));
    expect(work.getByText(quote)).toBeVisible(); expect(work.getByText('Fictional North')).toBeVisible();
    expect(work.getByText('Analyst')).toBeVisible(); expect(work.getByText('2020')).toBeVisible();
    expect(skills.getByText('Fictional SQL')).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Keep entry' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Description')).not.toBeInTheDocument();
    expect(attest()).toBeEnabled(); expect(approve()).toBeDisabled();
    await user.click(attest()); await user.click(approve());
    expect(state.submissions).toHaveLength(1);
    expect(state.submissions[0]?.body.decisions.map(decision => decision.action)).toEqual(['add', 'add']);
    expect(state.submissions[0]?.version).toBe(1);
  });
  it('shows a large extracted skill section together and approves it without a review queue', async () => {
    const user = userEvent.setup(); const batch = staged();
    const labels = Array.from({ length: 106 }, (_, index) => `Fictional skill ${index}`);
    batch.text = `Skills\n${labels.join('\n')}`; batch.byte_size = new Blob([batch.text]).size;
    let position = 7;
    batch.entries = labels.map((text, index): ImportEntry => {
      const start = position; position += text.length + 1;
      return { entry_id: `skill-${index}`, kind: 'skill', identity_field: null, payload: { text }, issues: [],
        sources: [{ source_id: `source-${index}`, start, end: start + text.length, quote: text }],
        comparison: { status: 'new', target_claim_id: null, candidate_claim_ids: [] } };
    });
    const { api, state } = server(batch); render(<Harness api={api} />); await open(user);
    const skills = within(screen.getByRole('region', { name: 'Skills' }));
    expect(skills.getByText(labels[0]!)).toBeVisible(); expect(skills.getByText(labels[105]!)).toBeVisible();
    expect(attest()).toBeEnabled(); await user.click(attest()); await user.click(approve());
    expect(state.submissions[0]?.body.decisions).toHaveLength(106);
  });


  it('retains exact uniquely matched claims without a per-entry review or new-source promise', async () => {
    const user = userEvent.setup(); const batch = staged();
    batch.entries = [{ ...batch.entries[0]!, payload: { ...oldPayload }, comparison: { status: 'unchanged', target_claim_id: oldClaim.id, candidate_claim_ids: [oldClaim.id] } }];
    const { api, state } = server(batch); render(<Harness api={api} />); await open(user);
    expect(within(screen.getByRole('article', { name: 'Preview entry 1' })).getByText('Unchanged')).toBeVisible();
    expect(attest()).toBeEnabled(); expect(approve()).toHaveAccessibleName('Complete import without profile changes');
    await edit(user);
    expect(screen.getByLabelText('Existing profile entry')).toHaveValue('unchanged');
    expect(screen.getByText(/This exact saved entry is retained without new evidence/)).toBeVisible();
    await user.click(attest()); await user.click(approve());
    expect(state.submissions[0]?.body.decisions[0]).toMatchObject({ action: 'unchanged', claim_id: oldClaim.id, payload: oldClaim.revision.payload });
  });
  it('allows repeated source occurrences that retain the same unchanged claim without duplicate-target approval', async () => {
    const user = userEvent.setup(); const batch = staged();
    const skill = { ...oldClaim, id: 'claim-skill', kind: 'skill' as const, revision: { ...oldClaim.revision, payload: { text: 'Fictional SQL' } } };
    const saved = { ...profile, current_revision: { ...profile.current_revision, claims: [skill] } };
    batch.text = 'Skills\nFictional SQL\nFictional SQL';
    batch.entries = [0, 1].map((index): ImportEntry => ({ entry_id: `entry-${index}`, kind: 'skill', identity_field: null, payload: { text: 'Fictional SQL' }, issues: [],
      sources: [{ source_id: `source-${index}`, start: 7 + index * 14, end: 20 + index * 14, quote: 'Fictional SQL' }],
      comparison: { status: 'unchanged', target_claim_id: skill.id, candidate_claim_ids: [skill.id] } }));
    const { api, state } = server(batch); render(<ResumeImport api={api} profile={saved} disabled={false} onProfile={() => undefined} />); await open(user);
    const preview = within(screen.getByRole('region', { name: 'Assembled profile preview' }));
    expect(preview.getAllByRole('article')).toHaveLength(1);
    expect(preview.getByText('Fictional SQL', { selector: 'dd' })).toBeVisible();
    await edit(user, 2);
    expect(screen.getByRole('group', { name: 'Edit entry 2 · Skills' })).toBeVisible();
    await user.click(screen.getByText('View original source and provenance'));
    expect(screen.getByText('Source part source-1')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Previous entry' }));
    expect(screen.getByRole('group', { name: 'Edit entry 1 · Skills' })).toBeVisible();
    await user.click(screen.getByText('View original source and provenance'));
    expect(screen.getByText('Source part source-0')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Exclude entry' }));
    expect(preview.getByText('Fictional SQL', { selector: 'dd' })).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Include preview entry 1' }));
    expect(preview.getAllByRole('article')).toHaveLength(1);
    expect(attest()).toBeEnabled(); await user.click(attest()); await user.click(approve());
    expect(state.submissions[0]?.body.decisions.map(decision => ({ action: decision.action, claim_id: decision.claim_id, source_ids: decision.source_ids }))).toEqual([
      { action: 'unchanged', claim_id: skill.id, source_ids: ['source-0'] }, { action: 'unchanged', claim_id: skill.id, source_ids: ['source-1'] },
    ]);
  });


  it('invalidates an edited unchanged value immediately without requiring Keep after a valid correction', async () => {
    const user = userEvent.setup(); const batch = staged();
    batch.entries = [{ ...batch.entries[0]!, payload: { ...oldPayload }, comparison: { status: 'unchanged', target_claim_id: oldClaim.id, candidate_claim_ids: [oldClaim.id] } }];
    const { api, state } = server(batch); render(<Harness api={api} />); await open(user);
    await user.click(attest()); await edit(user); await user.type(screen.getByLabelText('Description'), ' Corrected by owner.');
    expect(attest()).not.toBeChecked(); expect(attest()).toBeEnabled();
    expect(screen.getByLabelText('Existing profile entry')).toHaveValue('replace');
    expect(within(screen.getByRole('article', { name: 'Preview entry 1' })).getByText('Ready to update')).toBeVisible();
    await user.click(attest()); await user.click(approve());
    expect(state.submissions[0]?.body.decisions[0]).toMatchObject({ action: 'replace', claim_id: oldClaim.id, payload: { text: 'Existing fictional work. Corrected by owner.' } });
  });

  it('preserves original inert Unicode source independently from edits and keeps buffers across navigation and reload', async () => {
    const user = userEvent.setup(); const { api, state } = server(); const { container } = render(<Harness api={api} />); await open(user);
    await edit(user); await user.clear(screen.getByLabelText('Description')); await user.type(screen.getByLabelText('Description'), 'Owner correction.');
    await user.click(screen.getByText('View original source and provenance'));
    expect(screen.getByText(quote, { selector: 'blockquote' })).toBeVisible();
    expect(screen.getByText(`Exact source quote (Unicode span ${start}–${start + Array.from(quote).length}, end exclusive):`)).toBeVisible();
    await user.click(screen.getByText('Read complete normalized source text'));
    expect(screen.getByText((_text, element) => element?.tagName === 'PRE' && element.textContent === source)).toBeVisible(); expect(container.querySelector('script')).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Next entry' })); await user.click(screen.getByRole('button', { name: 'Previous entry' }));
    await user.click(screen.getByRole('button', { name: 'Toggle import visibility' })); await user.click(screen.getByRole('button', { name: 'Toggle import visibility' }));
    await user.click(screen.getByRole('button', { name: 'Reload selected import' }));
    expect(screen.getByLabelText('Description')).toHaveValue('Owner correction.');
    expect(within(screen.getByRole('article', { name: 'Preview entry 1' })).getByText('Owner correction.', { selector: 'dd' })).toBeVisible();
    expect(attest()).toBeEnabled(); await user.click(attest());
    const unload = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(unload); expect(unload.defaultPrevented).toBe(true);
    expect(screen.getByRole('button', { name: 'Other profile editor' })).toBeDisabled(); expect(screen.getByLabelText('Saved import')).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Toggle competing editor' })); expect(approve()).toBeDisabled();
    expect(state.submissions).toHaveLength(0);
  });

  it('blocks ambiguous placement until corrected and explicitly confirmed, or excluded', async () => {
    const user = userEvent.setup(); const batch = staged();
    batch.entries[0] = { ...batch.entries[0]!, kind: null, payload: { text: quote }, issues: ['needs_placement'], comparison: { status: 'needs_attention', target_claim_id: null, candidate_claim_ids: [] } };
    const { api, state } = server(batch); render(<Harness api={api} />); await open(user);
    expect(screen.getByRole('region', { name: 'Needs placement' })).toBeVisible(); expect(attest()).toBeDisabled();
    await edit(user); expect(screen.getByRole('button', { name: 'Confirm extraction' })).toBeDisabled();
    await user.selectOptions(screen.getByLabelText('Save to'), 'education');
    await user.type(screen.getByLabelText('School or institution'), 'Fictional College'); await user.type(screen.getByLabelText('Qualification'), 'BSc');
    await user.click(screen.getByRole('button', { name: 'Confirm extraction' }));
    await user.type(screen.getByLabelText('Qualification'), ' Honours');
    expect(attest()).toBeEnabled(); await user.click(attest()); await user.click(approve());
    expect(state.submissions[0]?.body.decisions[0]).toMatchObject({ kind: 'education', source_ids: ['source-work'], payload: { text: quote, organization: 'Fictional College', role: 'BSc Honours' } });
  });

  it('rejects all-excluded batches and preserves PDF layout warnings', async () => {
    const user = userEvent.setup(); const batch = staged(); batch.media_type = 'application/pdf'; batch.warnings.push('pdf_layout_requires_review');
    const { api, state } = server(batch); render(<Harness api={api} />); await open(user);
    expect(screen.getByRole('list', { name: 'Import warnings' })).toHaveTextContent('PDF layout');
    await exclude(user, 1); await exclude(user, 2);
    expect(attest()).toBeDisabled(); expect(approve()).toBeDisabled(); expect(state.submissions).toHaveLength(0);
  });

  it('combines entries without losing their complete original-source partition or regrouped buffer on reload', async () => {
    const user = userEvent.setup(); const { api, state } = server(); render(<Harness api={api} />); await open(user);
    await edit(user); await user.click(screen.getByRole('button', { name: 'Combine with next entry' }));
    expect(attest()).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Reload selected import' })); await edit(user);
    expect(screen.getByLabelText('Save to')).toHaveValue('');
    await user.selectOptions(screen.getByLabelText('Save to'), 'other'); await user.click(screen.getByRole('button', { name: 'Confirm extraction' }));
    await user.click(attest()); await user.click(approve());
    expect(state.submissions[0]?.body.decisions).toHaveLength(1);
    expect(state.submissions[0]?.body.decisions[0]).toMatchObject({ source_ids: ['source-work', 'source-skill'], kind: 'other', action: 'add', payload: { text: `${quote}\nFictional SQL` } });
  });

  it('splits into original source parts and can exclude one without dropping its decision', async () => {
    const user = userEvent.setup(); const batch = staged();
    batch.entries = [{ ...batch.entries[0]!, sources: [...batch.entries[0]!.sources, ...batch.entries[1]!.sources] }];
    const { api, state } = server(batch); render(<Harness api={api} />); await open(user);
    await edit(user); await user.click(screen.getByRole('button', { name: 'Split into original source parts' }));
    await edit(user); await user.click(screen.getByRole('button', { name: 'Confirm extraction' }));
    await exclude(user, 2); await user.click(attest()); await user.click(approve());
    expect(state.submissions[0]?.body.decisions.map(decision => ({ source_ids: decision.source_ids, action: decision.action }))).toEqual([
      { source_ids: ['source-work'], action: 'add' }, { source_ids: ['source-skill'], action: 'skip' },
    ]);
  });

  it('blocks impossible dates, reversed ranges and unsafe project URLs while valid edits are ready immediately', async () => {
    const user = userEvent.setup(); const { api } = server(); render(<Harness api={api} />); await open(user); await edit(user);
    await user.selectOptions(screen.getByLabelText('Save to'), 'project');
    await user.clear(screen.getByLabelText('Start date')); await user.type(screen.getByLabelText('Start date'), '2024-02-30'); expect(attest()).toBeDisabled();
    await user.clear(screen.getByLabelText('Start date')); await user.type(screen.getByLabelText('Start date'), '2024-02-29');
    await user.type(screen.getByLabelText('End date'), '2023'); expect(attest()).toBeDisabled();
    await user.clear(screen.getByLabelText('End date')); await user.type(screen.getByLabelText('End date'), '2024');
    await user.type(screen.getByLabelText('Project URL'), 'javascript:alert(1)'); expect(attest()).toBeDisabled();
    await user.clear(screen.getByLabelText('Project URL')); await user.type(screen.getByLabelText('Project URL'), 'https://user:secret@example.test/project'); expect(attest()).toBeDisabled();
    await user.clear(screen.getByLabelText('Project URL')); await user.type(screen.getByLabelText('Project URL'), 'https://example.test/project'); expect(attest()).toBeEnabled();
  });
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
  it('preselects suggested targets, retains current preview fields until chosen and preserves typed edits through a 412 rebase', async () => {
    const user = userEvent.setup(); const batch = staged();
    batch.entries[0] = { ...batch.entries[0]!, payload: { ...oldPayload, text: quote }, comparison: { status: 'conflict', target_claim_id: oldClaim.id, candidate_claim_ids: [oldClaim.id] } };
    const { api, state } = server(batch); const acceptedProfiles = vi.fn(); render(<Harness api={api} onProfile={acceptedProfiles} />);
    await open(user);
    expect(within(screen.getByRole('article', { name: 'Preview entry 1' })).getByText(oldPayload.text)).toBeVisible();
    expect(attest()).toBeDisabled(); await edit(user);
    expect(screen.getByLabelText('Replacement target')).toHaveValue(oldClaim.id);
    await user.clear(screen.getByLabelText('Description')); await user.type(screen.getByLabelText('Description'), 'My retained typed statement.');
    expect(attest()).toBeEnabled(); await user.click(attest()); state.failures.push(new ApiError(412, 'version_conflict')); await user.click(approve());
    expect(await screen.findByRole('alert')).toHaveTextContent('A newer revision exists');
    const newerClaim = { ...oldClaim, revision: { ...oldClaim.revision, payload: { ...oldPayload, role: 'Senior engineer' } } };
    state.currentProfile = { ...profile, version: 2, current_revision: { ...profile.current_revision, id: 'profile-r2', revision_no: 2, claims: [newerClaim] } };
    state.batch = { ...state.batch, comparison_profile_version: 2 };
    await user.click(screen.getByRole('button', { name: 'Load current profile for comparison' }));
    await screen.findByRole('heading', { name: 'Current server profile revision 2' });
    await user.click(screen.getByRole('button', { name: 'Use latest profile and retain review' }));
    expect(screen.getByLabelText('Description')).toHaveValue('My retained typed statement.');
    expect(screen.getByLabelText('Replacement target')).toHaveValue(oldClaim.id);
    expect(attest()).not.toBeChecked(); expect(attest()).toBeDisabled();
    expect(within(screen.getByRole('article', { name: 'Preview entry 1' })).getByText('Senior engineer')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Keep current job title' })); await user.click(attest()); await user.click(approve());
    expect(await screen.findByText(/Reviewed merge is pending/)).toBeVisible();
    expect(screen.getByRole('button', { name: 'Other profile editor' })).toBeDisabled(); expect(screen.getByLabelText('Loaded profile revision')).toHaveTextContent('2');
    expect(state.submissions[1]?.version).toBe(2); expect(state.submissions[1]?.key).not.toBe(state.submissions[0]?.key);
    expect(state.submissions[1]?.body.decisions[0]).toMatchObject({ action: 'replace', claim_id: oldClaim.id, payload: { text: 'My retained typed statement.', role: 'Senior engineer' } });
    state.currentProfile = { ...state.currentProfile, version: 3, current_revision: { ...state.currentProfile.current_revision, id: 'profile-r3', revision_no: 3 } };
    state.batch = { ...state.batch, state: 'succeeded', version: 7 };
    await user.click(screen.getByRole('button', { name: 'Reload selected import' }));
    expect(await screen.findByText(/Import completed. Loaded profile revision 3/)).toBeVisible();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Other profile editor' })).toBeEnabled());
  });

  it('keeps populated current fields when incoming values are blank and offers an explicit clear', async () => {
    const user = userEvent.setup(); const batch = staged();
    batch.entries = [{ ...batch.entries[0]!, payload: { text: quote, organization: 'Fictional North', role: 'Engineer', start_date: '2020' }, comparison: { status: 'conflict', target_claim_id: oldClaim.id, candidate_claim_ids: [oldClaim.id] } }];
    const { api, state } = server(batch); render(<Harness api={api} />); await open(user); await edit(user);
    expect(screen.getByLabelText('Organization')).toHaveValue('Fictional North'); expect(screen.getByLabelText('Job title')).toHaveValue('Engineer');
    expect(screen.getByLabelText('Start date')).toHaveValue('2020'); expect(screen.getByLabelText('End date')).toHaveValue('2022'); expect(attest()).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Use imported description' })); expect(attest()).toBeEnabled();
    await user.click(screen.getByRole('button', { name: 'Explicitly clear end date' }));
    expect(screen.getByLabelText('End date')).toHaveValue('');
    await user.click(attest()); await user.click(approve());
    expect(state.submissions[0]?.body.decisions[0]?.payload).toEqual({ text: quote, organization: 'Fictional North', role: 'Engineer', start_date: '2020' });
  });

  it('blocks stale comparison versions and invalidates previously unchanged targets after loading the latest profile', async () => {
    const user = userEvent.setup(); const batch = staged(); batch.comparison_profile_version = 2;
    batch.entries = [{ ...batch.entries[0]!, payload: { ...oldPayload }, comparison: { status: 'unchanged', target_claim_id: oldClaim.id, candidate_claim_ids: [oldClaim.id] } }];
    const { api, state } = server(batch); render(<Harness api={api} />); await open(user);
    expect(attest()).toBeDisabled(); expect(approve()).toBeDisabled();
    state.currentProfile = { ...profile, version: 2, current_revision: { ...profile.current_revision, revision_no: 2, claims: [{ ...oldClaim, revision: { ...oldClaim.revision, payload: { ...oldPayload, role: 'Changed saved role' } } }] } };
    state.batch = { ...batch, entries: [{ ...batch.entries[0]!, comparison: { status: 'conflict', target_claim_id: oldClaim.id, candidate_claim_ids: [oldClaim.id] } }] };
    await user.click(screen.getByRole('button', { name: 'Load current profile for comparison' })); await user.click(await screen.findByRole('button', { name: 'Use latest profile and retain review' }));
    expect(attest()).toBeDisabled(); await edit(user); expect(screen.getByLabelText('Existing profile entry')).toHaveValue('replace');
    await user.click(screen.getByRole('button', { name: 'Keep current job title' })); expect(attest()).toBeEnabled();
    await user.click(attest()); await user.click(approve()); expect(state.submissions[0]?.version).toBe(2);
  });

  it('completes an all-unchanged import and releases other editors without requiring a new profile revision', async () => {
    const user = userEvent.setup(); const batch = staged();
    batch.entries = [{ ...batch.entries[0]!, payload: { ...oldPayload }, comparison: { status: 'unchanged', target_claim_id: oldClaim.id, candidate_claim_ids: [oldClaim.id] } }];
    const { api, state } = server(batch); render(<Harness api={api} />); await open(user); await user.click(attest()); await user.click(approve());
    expect(screen.getByRole('button', { name: 'Other profile editor' })).toBeDisabled();
    state.batch = { ...state.batch, state: 'succeeded' };
    await user.click(screen.getByRole('button', { name: 'Reload selected import' }));
    expect(await screen.findByText(/Import completed. Loaded profile revision 1/)).toBeVisible();
    expect(screen.getByLabelText('Loaded profile revision')).toHaveTextContent('1');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Other profile editor' })).toBeEnabled());
  });

  it('retains review and retry identity across session/network failures, changing identity only when the payload changes', async () => {
    const user = userEvent.setup(); const { api, state } = server(); render(<Harness api={api} />); await open(user); await exclude(user);
    state.failures.push(new ApiError(401, 'session_required'), new TypeError('offline'), new TypeError('offline'));
    await user.click(attest()); await user.click(approve()); expect(await screen.findByRole('alert')).toHaveTextContent('session is unavailable');
    expect(attest()).toBeChecked();
    await user.click(screen.getByRole('button', { name: 'Reload selected import' }));
    expect(attest()).toBeChecked();
    await user.click(approve()); expect(await screen.findByRole('alert')).toHaveTextContent('server could not be reached');
    expect(state.submissions[1]?.key).toBe(state.submissions[0]?.key);
    await edit(user);
    await user.type(screen.getByLabelText('Description'), ' Owner correction.'); expect(attest()).not.toBeChecked();
    await user.click(attest()); await user.click(approve());
    expect(await screen.findByRole('alert')).toHaveTextContent('server could not be reached');
    expect(state.submissions[2]?.key).not.toBe(state.submissions[1]?.key);
    expect(state.submissions[2]?.body.decisions[0]?.payload.text).toBe(`${quote} Owner correction.`);
  });

  it('separately confirms replace-all and cancels the real workflow without dropping review or refreshing the profile', async () => {
    const user = userEvent.setup(); const { api, state } = server(); const acceptedProfiles = vi.fn(); render(<Harness api={api} onProfile={acceptedProfiles} />);
    await open(user); await exclude(user); await user.click(attest());
    await user.selectOptions(screen.getByLabelText('Import mode'), 'replace_all');
    expect(attest()).not.toBeChecked(); expect(attest()).toBeDisabled(); expect(approve()).toBeDisabled();
    await user.click(screen.getByLabelText('I separately confirm replacing all non-identity facts and archiving displaced sources'));
    await user.click(attest()); await user.click(approve()); expect(await screen.findByText(/Reviewed merge is pending/)).toBeVisible();
    expect(state.submissions[0]?.body.mode).toBe('replace_all'); expect(state.submissions[0]?.body.replace_all_confirmed).toBe(true);
    await user.click(screen.getByRole('button', { name: 'Cancel import' }));
    expect(await screen.findByText(/State: cancelled/)).toBeVisible(); expect(state.cancelVersions).toEqual([5]);
    expect(within(screen.getByRole('article', { name: 'Preview entry 1' })).getByText(quote)).toBeVisible(); expect(acceptedProfiles).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Discard review choices' }));
    expect(screen.getByRole('button', { name: 'Other profile editor' })).toBeEnabled();
  });

  it('retains review when atomic merge fails and never claims completion', async () => {
    const user = userEvent.setup(); const { api, state } = server(); const acceptedProfiles = vi.fn(); render(<Harness api={api} onProfile={acceptedProfiles} />);
    await open(user); await exclude(user); await user.click(attest()); await user.click(approve());
    state.batch = { ...state.batch, state: 'permanent_failure', version: 6, error_code: 'profile_version_conflict' };
    await user.click(screen.getByRole('button', { name: 'Reload selected import' }));
    expect(await screen.findByText(/Import stopped \(profile_version_conflict\)/)).toBeVisible();
    expect(within(screen.getByRole('article', { name: 'Preview entry 1' })).getByText(quote)).toBeVisible(); expect(acceptedProfiles).not.toHaveBeenCalled();
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
    expect(reads()).toBe(2); expect(within(screen.getByRole('region', { name: 'Work experience' })).getByText(quote)).toBeVisible();
    expect(attest()).toBeEnabled();
    await act(async () => { await vi.advanceTimersByTimeAsync(9000); }); expect(reads()).toBe(2);
  });
});

describe('personal identity review', () => {
  it('requires a choice for differing identity, invalidates it after editing and submits no claim target', async () => {
    const user = userEvent.setup(); const batch = staged();
    batch.entries = [{ ...batch.entries[0]!, kind: 'identity', identity_field: 'display_name', payload: { text: 'Fictional Candidate' }, comparison: { status: 'conflict', target_claim_id: null, candidate_claim_ids: [] } }];
    const { api, state } = server(batch); render(<Harness api={api} />); await open(user);
    expect(within(screen.getByRole('article', { name: 'Preview entry 1' })).getByText('Mira')).toBeVisible(); expect(attest()).toBeDisabled();
    await edit(user); expect(screen.getByLabelText('Full name')).toHaveAttribute('maxlength', '120');
    await user.click(screen.getByLabelText('Replace my current full name with this value')); await user.click(attest());
    await user.type(screen.getByLabelText('Full name'), ' Jr');
    expect(screen.getByLabelText('Replace my current full name with this value')).not.toBeChecked(); expect(attest()).not.toBeChecked(); expect(attest()).toBeDisabled();
    await user.click(screen.getByLabelText('Replace my current full name with this value')); await user.click(attest()); await user.click(approve());
    expect(state.submissions[0]?.body.decisions[0]).toEqual({ source_ids: ['source-work'], action: 'replace', kind: 'identity', identity_field: 'display_name', payload: { text: 'Fictional Candidate Jr' } });
  });

  it('needs no identity checkbox for equal values and submits unchanged without a claim id', async () => {
    const user = userEvent.setup(); const batch = staged();
    batch.entries = [{ ...batch.entries[0]!, kind: 'identity', identity_field: 'display_name', payload: { text: 'Mira' }, comparison: { status: 'unchanged', target_claim_id: null, candidate_claim_ids: [] } }];
    const { api, state } = server(batch); render(<Harness api={api} />); await open(user); await edit(user);
    expect(screen.queryByLabelText('Replace my current full name with this value')).not.toBeInTheDocument(); expect(attest()).toBeEnabled();
    await user.click(attest()); await user.click(approve());
    expect(state.submissions[0]?.body.decisions[0]).toEqual({ source_ids: ['source-work'], action: 'unchanged', kind: 'identity', identity_field: 'display_name', payload: { text: 'Mira' } });
  });

  it('adds an empty personal field directly without a professional replacement target', async () => {
    const user = userEvent.setup(); const batch = staged(); batch.entries = [{ ...batch.entries[0]!, kind: 'identity', identity_field: 'headline', payload: { text: 'Fictional engineer' } }];
    const { api, state } = server(batch);
    const empty = { ...profile, current_revision: { ...profile.current_revision, snapshot: { ...profile.current_revision.snapshot, headline: '' } } };
    render(<ResumeImport api={api} profile={empty} disabled={false} onProfile={() => undefined} />); await open(user);
    expect(attest()).toBeEnabled(); await user.click(attest()); await user.click(approve());
    expect(state.submissions[0]?.body.decisions[0]).toMatchObject({ action: 'add', kind: 'identity', identity_field: 'headline' });
    expect(state.submissions[0]?.body.decisions[0]).not.toHaveProperty('claim_id');
  });

  it('blocks duplicate personal destinations and resets identity confirmation when saved values change on rebase', async () => {
    const user = userEvent.setup(); const { api, state } = server(); render(<Harness api={api} />); await open(user);
    for (let index = 0; index < 2; index += 1) {
      await edit(user, index + 1); await user.selectOptions(screen.getByLabelText('Save to'), 'headline');
      await user.click(screen.getByLabelText('Replace my current highlight / tagline with this value'));
    }
    expect(attest()).toBeDisabled(); await exclude(user, 2);
    await user.click(attest()); state.failures.push(new ApiError(412, 'version_conflict')); await user.click(approve());
    state.currentProfile = { ...profile, version: 2, current_revision: { ...profile.current_revision, revision_no: 2, snapshot: { ...profile.current_revision.snapshot, headline: 'Changed current headline' } } };
    state.batch = { ...state.batch, comparison_profile_version: 2 };
    await user.click(screen.getByRole('button', { name: 'Load current profile for comparison' }));
    await user.click(await screen.findByRole('button', { name: 'Use latest profile and retain review' })); await edit(user);
    expect(screen.getByText('Current: Changed current headline')).toBeVisible();
    expect(screen.getByLabelText('Replace my current highlight / tagline with this value')).not.toBeChecked(); expect(attest()).toBeDisabled();
    await user.click(screen.getByLabelText('Replace my current highlight / tagline with this value')); await user.click(attest()); await user.click(approve());
    expect(state.submissions[1]?.version).toBe(2); expect(state.submissions[1]?.body.decisions[0]?.payload.text).toBe(quote);
  });
});
