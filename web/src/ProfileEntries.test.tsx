// SPDX-License-Identifier: MIT OR Apache-2.0
import { act, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { Api, ApiError, type ClaimRendering, type ClaimVocabulary, type Profile, type ProfileRevision } from './api';
import { ProfileEntries } from './ProfileEntries';

const metadata = { revision_no: 1, content_sha256: 'a'.repeat(64), created_at: '2026-01-01T00:00:00Z', author_owner_id: 'synthetic-owner' };
const source = { id: 'source', version: 1, archived: false, current_revision_id: 'source-revision', revision: { ...metadata, id: 'source-revision', payload: { text: 'Synthetic workshop notes.', source: 'Owner attestation' } } };
const profile: Profile = {
  id: 'synthetic-profile', version: 1,
  current_revision: {
    ...metadata, id: 'profile-revision', reason: 'Synthetic fixture',
    snapshot: { display_name: 'Synthetic Person', headline: '', claim_revision_ids: ['skill-revision'] },
    claims: [{ id: 'skill', kind: 'skill', version: 1, archived: false, current_revision_id: 'skill-revision', rendering: 'Synthetic workshop skill.', needs_attention: false, revision: {
      ...metadata, id: 'skill-revision', payload: { text: 'Synthetic workshop skill.' },
      evidence_links: [{ evidence_revision_id: source.revision.id, support_kind: 'user_asserted', excerpt: 'Synthetic workshop notes.', evidence: source.revision }],
    } }],
  },
  evidence: [source], quality_warnings: [], experience_coverage: { minimum_days: 0, maximum_days: 0, undated_or_open_entries: 0 },
};
const ownershipVocabulary: ClaimVocabulary = { language: 'en', verbs: [{ key: 'built', label: 'Built' }], units: ['percent'], changes: [], magnitudes: [], rates: [] };
function deferred<T>() {
  // The project targets ES2022, which does not include Promise.withResolvers.
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((onResolve, onReject) => { resolve = onResolve; reject = onReject; });
  return { promise, resolve, reject };
}
function setup() {
  const api = new Api();
  const onDirtyChange = vi.fn();
  render(<ProfileEntries api={api} profile={profile} disabled={false} onProfile={vi.fn()} onDirtyChange={onDirtyChange} onBusyChange={vi.fn()} />);
  return { user: userEvent.setup(), onDirtyChange };
}

describe('focused profile sections', () => {
  it('shows one category, exposes saved counts, and keeps sources and history reachable without hashes', async () => {
    const { user } = setup();
    expect(screen.getByRole('button', { name: 'Skills' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Skills' })).toHaveAccessibleDescription('1 saved entries');
    expect(screen.queryByRole('heading', { name: 'Work experience' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Work experience' }));
    expect(screen.getByRole('heading', { name: 'Work experience' })).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Edit skills entry 1' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Sources' }));
    expect(screen.getByRole('button', { name: 'Add source or owner attestation' })).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'History' }));
    expect(screen.getByRole('button', { name: 'Load profile history' })).toBeVisible();
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });

  it('blocks section switches until a source buffer is explicitly discarded', async () => {
    const { user, onDirtyChange } = setup();
    await user.click(screen.getByRole('button', { name: 'Sources' }));
    await user.click(screen.getByRole('button', { name: 'Add source or owner attestation' }));
    await user.type(screen.getByLabelText('Source text'), 'Synthetic unsaved evidence.');
    await user.click(screen.getByRole('button', { name: 'Skills' }));
    expect(screen.getByRole('button', { name: 'Skills' })).toBeDisabled();
    expect(screen.getByLabelText('Source text')).toHaveValue('Synthetic unsaved evidence.');
    expect(screen.getByText(/before switching sections/)).toBeVisible();
    expect(onDirtyChange).toHaveBeenLastCalledWith(true);
    await user.click(screen.getByRole('button', { name: 'Discard source changes' }));
    expect(onDirtyChange).toHaveBeenLastCalledWith(false);
    await user.click(screen.getByRole('button', { name: 'Skills' }));
    expect(screen.getByRole('button', { name: 'Edit skills entry 1' })).toBeVisible();
  });

  it('retains a claim buffer and keeps claim archive confirmation visible in its category', async () => {
    const { user } = setup();
    await user.click(screen.getByRole('button', { name: 'Edit skills entry 1' }));
    await user.type(screen.getByLabelText('Entry statement'), ' Retained draft.');
    await user.click(screen.getByRole('button', { name: 'History' }));
    expect(screen.getByLabelText('Entry statement')).toHaveValue('Synthetic workshop skill. Retained draft.');
    expect(screen.getByRole('button', { name: 'History' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Discard entry changes' }));
    await user.click(screen.getByRole('button', { name: 'Archive skills entry 1' }));
    expect(screen.getByRole('button', { name: 'Confirm archive' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'Sources' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Keep active record' }));
    expect(screen.getByRole('button', { name: 'Sources' })).toBeEnabled();
  });

  it('edits work experience through structured fields and previews the server rendering', async () => {
    const user = userEvent.setup(); const api = new Api(); const onProfile = vi.fn();
    const vocabulary = { language: 'en', verbs: [{ key: 'led', label: 'Led' }, { key: 'reduced', label: 'Reduced' }, { key: 'assisted', label: 'Assisted with' }], units: ['percent', 'usd'], changes: ['increase', 'decrease', 'saving'], magnitudes: ['thousand'], rates: ['week'] };
    const request = vi.spyOn(api, 'request').mockImplementation(async <T,>(path: string, method = 'GET', body?: unknown) => {
      if (path === '/claims/vocabulary') return vocabulary as T;
      if (path === '/claims/render' && method === 'POST') {
        const claim = (body as { claim: { verb: string; subject: string; metric?: { value: string } } }).claim;
        return { language: 'en', rendering: `${claim.verb === 'reduced' ? 'Reduced' : 'Led'} ${claim.subject}${claim.metric ? ` by ${claim.metric.value}%` : ''}` } as T;
      }
      if (path === '/claims' && method === 'POST') return profile as T;
      throw new ApiError(404, 'not_found');
    });
    render(<ProfileEntries api={api} profile={profile} disabled={false} onProfile={onProfile} onDirtyChange={vi.fn()} onBusyChange={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: 'Work experience' }));
    await user.click(screen.getByRole('button', { name: 'Add work experience entry' }));
    expect(screen.queryByLabelText('Entry statement')).not.toBeInTheDocument();
    const verb = await screen.findByLabelText('Ownership verb');
    expect(Array.from((verb as HTMLSelectElement).options).map(option => option.text)).toEqual(['1. Led', '2. Reduced', '3. Assisted with']);
    await user.selectOptions(verb, 'reduced');
    await user.type(screen.getByLabelText('Subject'), 'batch runtime');
    await user.type(screen.getByLabelText('Metric value (optional)'), '30');
    await user.selectOptions(screen.getByLabelText('Metric change'), 'decrease');
    expect(await screen.findByText('Reduced batch runtime by 30%')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Add evidence link' }));
    await user.selectOptions(screen.getByLabelText('Evidence revision 1'), 'source-revision');
    await user.type(screen.getByLabelText('Source excerpt 1'), 'Synthetic workshop notes.');
    await user.click(screen.getByRole('button', { name: 'Save entry revision' }));
    const saved = request.mock.calls.find(call => call[0] === '/claims' && call[1] === 'POST');
    expect(saved?.[2]).toEqual({ kind: 'experience', payload: { claim: { schema_version: 1, verb: 'reduced', subject: 'batch runtime', metric: { value: '30', unit: 'percent', change: 'decrease' } } },
      evidence_links: [{ evidence_revision_id: 'source-revision', support_kind: 'user_asserted', excerpt: 'Synthetic workshop notes.' }] });
    expect(onProfile).toHaveBeenCalledWith(profile);
  });

  it('shows rejected preview feedback beside the rendering and clears it on structured edits before recovery', async () => {
    const user = userEvent.setup(); const api = new Api();
    const recovery = deferred<ClaimRendering>();
    let renderAttempts = 0;
    const request = vi.spyOn(api, 'request').mockImplementation(async <T,>(path: string) => {
      if (path === '/claims/vocabulary') return ownershipVocabulary as T;
      if (path === '/claims/render') {
        if (++renderAttempts === 1) throw new ApiError(422, 'invalid_claim');
        return recovery.promise as Promise<T>;
      }
      throw new ApiError(404, 'not_found');
    });
    render(<ProfileEntries api={api} profile={profile} disabled={false} onProfile={vi.fn()} onDirtyChange={vi.fn()} onBusyChange={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: 'Work experience' }));
    await user.click(screen.getByRole('button', { name: 'Add work experience entry' }));
    await screen.findByLabelText('Ownership verb');
    fireEvent.change(screen.getByLabelText('Subject'), { target: { value: 'the billing service' } });
    const feedback = await screen.findByText(/Could not preview the claim:.*invalid_claim/);
    expect(feedback).toHaveAttribute('role', 'alert');
    expect(feedback.parentElement).toContainElement(screen.getByLabelText('Claim rendering'));
    expect(screen.getByLabelText('Claim rendering')).toHaveTextContent('Preview unavailable.');
    expect(screen.queryByRole('button', { name: 'Load latest profile for comparison' })).not.toBeInTheDocument();
    // A one-sided period changes the draft but is still omitted from the request.
    fireEvent.change(screen.getByLabelText('Period start (optional)'), { target: { value: '2021' } });
    expect(screen.queryByText(/Could not preview the claim:/)).not.toBeInTheDocument();
    expect(request.mock.calls.filter(call => call[0] === '/claims/render')).toHaveLength(2);
    const pendingPreview = request.mock.calls.filter(call => call[0] === '/claims/render')[1];
    expect(pendingPreview?.[2]).toEqual({ claim: { schema_version: 1, verb: 'built', subject: 'the billing service' } });
    await act(async () => { recovery.resolve({ language: 'en', rendering: 'Built the billing service.' }); });
    expect(screen.getByLabelText('Claim rendering')).toHaveTextContent('Built the billing service.');
    expect(screen.queryByText(/Could not preview the claim:/)).not.toBeInTheDocument();
  });

  it.each(['resolve', 'reject'] as const)('ignores an aborted stale preview that later %ss', async outcome => {
    const user = userEvent.setup(); const api = new Api();
    const stale = deferred<ClaimRendering>(); const current = deferred<ClaimRendering>();
    let renderAttempts = 0;
    const request = vi.spyOn(api, 'request').mockImplementation(async <T,>(path: string) => {
      if (path === '/claims/vocabulary') return ownershipVocabulary as T;
      if (path === '/claims/render') return (++renderAttempts === 1 ? stale.promise : current.promise) as Promise<T>;
      throw new ApiError(404, 'not_found');
    });
    render(<ProfileEntries api={api} profile={profile} disabled={false} onProfile={vi.fn()} onDirtyChange={vi.fn()} onBusyChange={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: 'Work experience' }));
    await user.click(screen.getByRole('button', { name: 'Add work experience entry' }));
    await screen.findByLabelText('Ownership verb');
    fireEvent.change(screen.getByLabelText('Subject'), { target: { value: 'old draft' } });
    fireEvent.change(screen.getByLabelText('Subject'), { target: { value: 'current draft' } });
    expect(request.mock.calls.find(call => call[0] === '/claims/render')?.[5]?.aborted).toBe(true);
    await act(async () => { current.resolve({ language: 'en', rendering: 'Built current draft.' }); });
    await act(async () => {
      if (outcome === 'resolve') stale.resolve({ language: 'en', rendering: 'Built old draft.' });
      else stale.reject(new ApiError(422, 'stale_claim'));
    });
    expect(screen.getByLabelText('Claim rendering')).toHaveTextContent('Built current draft.');
    expect(screen.queryByText(/Could not preview the claim:/)).not.toBeInTheDocument();
  });

  it('blocks structured submission while vocabulary is pending or failed and saves after a successful retry', async () => {
    const user = userEvent.setup(); const api = new Api(); const onProfile = vi.fn();
    const initial = deferred<ClaimVocabulary>(); const retry = deferred<ClaimVocabulary>();
    let vocabularyAttempts = 0;
    const request = vi.spyOn(api, 'request').mockImplementation(async <T,>(path: string, method = 'GET') => {
      if (path === '/claims/vocabulary') return (++vocabularyAttempts === 1 ? initial.promise : retry.promise) as Promise<T>;
      if (path === '/claims/render') return { language: 'en', rendering: 'Built the billing service.' } as T;
      if (path === '/claims' && method === 'POST') return profile as T;
      throw new ApiError(404, 'not_found');
    });
    render(<ProfileEntries api={api} profile={profile} disabled={false} onProfile={onProfile} onDirtyChange={vi.fn()} onBusyChange={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: 'Work experience' }));
    await user.click(screen.getByRole('button', { name: 'Add work experience entry' }));
    await user.type(screen.getByLabelText('Subject'), 'the billing service');
    await user.click(screen.getByRole('button', { name: 'Add evidence link' }));
    await user.selectOptions(screen.getByLabelText('Evidence revision 1'), 'source-revision');
    await user.type(screen.getByLabelText('Source excerpt 1'), 'Built the billing service.');
    const save = screen.getByRole('button', { name: 'Save entry revision' });
    expect(screen.getByText('Loading the ownership vocabulary…')).toBeVisible();
    expect(save).toBeDisabled();
    fireEvent.submit(save.closest('form')!);
    expect(request.mock.calls.some(call => call[0] === '/claims')).toBe(false);
    await act(async () => { initial.reject(new ApiError(503, 'vocabulary_unavailable')); });
    expect(screen.getByText(/Could not load the ownership vocabulary:.*vocabulary_unavailable/)).toBeVisible();
    expect(screen.queryByText('Loading the ownership vocabulary…')).not.toBeInTheDocument();
    expect(save).toBeDisabled();
    fireEvent.submit(save.closest('form')!);
    expect(request.mock.calls.some(call => call[0] === '/claims')).toBe(false);
    await user.click(screen.getByRole('button', { name: 'Retry ownership vocabulary' }));
    expect(screen.getByText('Loading the ownership vocabulary…')).toBeVisible();
    expect(save).toBeDisabled();
    await act(async () => { retry.resolve(ownershipVocabulary); });
    expect(screen.getByLabelText('Ownership verb')).toHaveValue('built');
    expect(screen.queryByText(/Could not load the ownership vocabulary:/)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Retry ownership vocabulary' })).not.toBeInTheDocument();
    expect(vocabularyAttempts).toBe(2);
    expect(save).toBeEnabled();
    await user.click(save);
    expect(onProfile).toHaveBeenCalledWith(profile);
  });

  it('states the exact complete-rendered-sentence grounding rule without the obsolete heuristic', async () => {
    const user = userEvent.setup(); const api = new Api();
    vi.spyOn(api, 'request').mockResolvedValue(ownershipVocabulary);
    render(<ProfileEntries api={api} profile={profile} disabled={false} onProfile={vi.fn()} onDirtyChange={vi.fn()} onBusyChange={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: 'Work experience' }));
    await user.click(screen.getByRole('button', { name: 'Add work experience entry' }));
    expect(screen.getByText('The source excerpt must contain the complete rendered sentence as written.', { exact: true })).toBeVisible();
    expect(screen.queryByText(/this ownership verb or a stronger one|One sentence of a linked source excerpt/)).not.toBeInTheDocument();
  });

  it('saves a claim period only with both bounds and blocks a one-sided period', async () => {
    const user = userEvent.setup(); const api = new Api();
    const vocabulary = { language: 'en', verbs: [{ key: 'led', label: 'Led' }], units: ['percent'], changes: ['decrease'], magnitudes: [], rates: [] };
    const request = vi.spyOn(api, 'request').mockImplementation(async <T,>(path: string, method = 'GET') => {
      if (path === '/claims/vocabulary') return vocabulary as T;
      if (path === '/claims/render' && method === 'POST') return { language: 'en', rendering: 'Led the billing migration' } as T;
      if (path === '/claims' && method === 'POST') return profile as T;
      throw new ApiError(404, 'not_found');
    });
    render(<ProfileEntries api={api} profile={profile} disabled={false} onProfile={vi.fn()} onDirtyChange={vi.fn()} onBusyChange={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: 'Work experience' }));
    await user.click(screen.getByRole('button', { name: 'Add work experience entry' }));
    await user.selectOptions(await screen.findByLabelText('Ownership verb'), 'led');
    await user.type(screen.getByLabelText('Subject'), 'the billing migration');
    await user.click(screen.getByRole('button', { name: 'Add evidence link' }));
    await user.selectOptions(screen.getByLabelText('Evidence revision 1'), 'source-revision');
    await user.type(screen.getByLabelText('Source excerpt 1'), 'Synthetic workshop notes.');
    const start = screen.getByLabelText('Period start (optional)'); const end = screen.getByLabelText('Period end (optional)');
    await user.type(start, '2021');
    expect(end).toBeRequired();
    await user.click(screen.getByRole('button', { name: 'Save entry revision' }));
    expect(request.mock.calls.some(call => call[0] === '/claims' && call[1] === 'POST')).toBe(false);
    await user.clear(start); await user.type(end, '2022');
    expect(start).toBeRequired();
    await user.click(screen.getByRole('button', { name: 'Save entry revision' }));
    expect(request.mock.calls.some(call => call[0] === '/claims' && call[1] === 'POST')).toBe(false);
    await user.type(start, '2021');
    await user.click(screen.getByRole('button', { name: 'Save entry revision' }));
    const saved = request.mock.calls.find(call => call[0] === '/claims' && call[1] === 'POST');
    expect((saved?.[2] as { payload: { claim: { period?: unknown } } }).payload.claim.period).toEqual({ start: '2021', end: '2022' });
  });

  it('flags a free-text experience entry as needing attention and offers structured fields to replace it', async () => {
    const user = userEvent.setup(); const api = new Api();
    vi.spyOn(api, 'request').mockImplementation(async <T,>(path: string) => {
      if (path === '/claims/vocabulary') return { language: 'en', verbs: [{ key: 'built', label: 'Built' }], units: ['percent'], changes: [], magnitudes: [], rates: [] } as T;
      throw new ApiError(404, 'not_found');
    });
    const legacy = { ...profile.current_revision.claims[0]!, id: 'work', kind: 'experience' as const, rendering: 'Did many synthetic things', needs_attention: true,
      revision: { ...profile.current_revision.claims[0]!.revision, payload: { text: 'Did many synthetic things' } } };
    const withLegacy: Profile = { ...profile, current_revision: { ...profile.current_revision, claims: [legacy] } };
    render(<ProfileEntries api={api} profile={withLegacy} disabled={false} onProfile={vi.fn()} onDirtyChange={vi.fn()} onBusyChange={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: 'Work experience' }));
    expect(screen.getByText(/Needs attention: this statement is still free text/)).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Edit work experience entry 1' }));
    expect(screen.getByText(/Saved free-text statement: “Did many synthetic things”/)).toBeVisible();
    expect(await screen.findByLabelText('Ownership verb')).toHaveValue('built');
    expect(screen.getByLabelText('Subject')).toHaveValue('');
  });

  it.each([undefined, 'Saved introduction.\nSecond paragraph.'])('previews and explicitly restores immutable profile text with description=%s', async description => {
    const user = userEvent.setup(); const api = new Api(); const onProfile = vi.fn();
    const revision: ProfileRevision = { ...profile.current_revision, snapshot: { ...profile.current_revision.snapshot, ...(description === undefined ? {} : { description }) }, evidence: [source] };
    const restored = { ...profile, version: 2, current_revision: { ...profile.current_revision, revision_no: 2, snapshot: revision.snapshot } };
    const request = vi.spyOn(api, 'request').mockImplementation(async <T,>(path: string, method = 'GET') => {
      if (path === '/profile/revisions') return { items: [revision], next_cursor: null } as T;
      if (path === '/profile/revisions/profile-revision') return revision as T;
      if (path === '/profile/restore' && method === 'POST') return restored as T;
      throw new ApiError(404, 'not_found');
    });
    render(<ProfileEntries api={api} profile={profile} disabled={false} onProfile={onProfile} onDirtyChange={vi.fn()} onBusyChange={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: 'History' }));
    await user.click(screen.getByRole('button', { name: 'Load profile history' }));
    await user.click(await screen.findByRole('button', { name: 'Preview profile revision 1' }));
    expect(await screen.findByText(`Description: ${description?.replace(/\s+/g, ' ') ?? 'Not supplied'}`, { exact: false })).toBeVisible();
    expect(screen.getByRole('button', { name: 'Restore as new profile revision' })).toBeDisabled();
    await user.click(screen.getByRole('checkbox', { name: /Restore this exact saved profile/ }));
    await user.click(screen.getByRole('button', { name: 'Restore as new profile revision' }));
    expect(onProfile).toHaveBeenCalledWith(restored);
    const restore = request.mock.calls.find(call => call[0] === '/profile/restore');
    expect(restore?.[2]).toEqual({ revision_id: 'profile-revision' });
    expect(restore?.[3]).toBe(1);
    expect(revision.snapshot.description).toBe(description);
  });

  it('shows the current saved description while retaining a conflicting source editor', async () => {
    const user = userEvent.setup(); const api = new Api(); const onProfile = vi.fn();
    const latest = { ...profile, version: 2, current_revision: { ...profile.current_revision, revision_no: 2, snapshot: { ...profile.current_revision.snapshot, description: 'Current saved biography.' } } };
    vi.spyOn(api, 'request').mockImplementation(async <T,>(path: string, method = 'GET') => {
      if (path === '/profile') return latest as T;
      if (path === '/evidence/source' && method === 'PUT') throw new ApiError(412, 'version_conflict');
      throw new ApiError(404, 'not_found');
    });
    render(<ProfileEntries api={api} profile={profile} disabled={false} onProfile={onProfile} onDirtyChange={vi.fn()} onBusyChange={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: 'Sources' }));
    await user.click(screen.getByRole('button', { name: 'Edit source 1' }));
    await user.type(screen.getByLabelText('Source text'), ' Retained source changes.');
    await user.click(screen.getByRole('button', { name: 'Save source revision' }));
    await user.click(await screen.findByRole('button', { name: 'Load latest profile for comparison' }));
    expect(await screen.findByText('Description: Current saved biography.')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Use latest version and retain my editor' }));
    expect(onProfile).toHaveBeenCalledWith(latest);
    expect(screen.getByLabelText('Source text')).toHaveValue('Synthetic workshop notes. Retained source changes.');
  });
});
