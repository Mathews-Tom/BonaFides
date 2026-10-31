// SPDX-License-Identifier: MIT OR Apache-2.0
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { Api, ApiError, type ClaimRendering, type ClaimVocabulary, type Profile, type ProfileRevision, type StructuredClaim } from './api';
import { ProfileEntries } from './ProfileEntries';

const metadata = { revision_no: 1, content_sha256: 'a'.repeat(64), created_at: '2026-01-01T00:00:00Z', author_owner_id: 'synthetic-owner' };
const source = { id: 'source', version: 1, archived: false, current_revision_id: 'source-revision', revision: { ...metadata, id: 'source-revision', payload: { text: 'Synthetic workshop notes.', source: 'Owner attestation' }, provenance: { source_type: 'owner_statement' as const, origin: 'owner_entry' as const, privacy_class: 'private' as const, employer_owned: false } } };
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
const ownershipVocabulary: ClaimVocabulary = {
  language: 'en',
  verbs: [
    { key: 'built', label: 'Built' }, { key: 'led', label: 'Led' }, { key: 'reduced', label: 'Reduced' },
    { key: 'grew', label: 'Grew' }, { key: 'raised', label: 'Raised' }, { key: 'saved', label: 'Saved' },
    { key: 'assisted', label: 'Assisted' }, { key: 'assisted_with', label: 'Assisted with' }, { key: 'assisted_in', label: 'Assisted in' },
    { key: 'contributed', label: 'Contributed' }, { key: 'contributed_to', label: 'Contributed to' },
  ],
  units: ['percent', 'usd'], changes: ['increase', 'decrease', 'saving'], magnitudes: ['thousand'], rates: ['day', 'week', 'month', 'quarter', 'year'],
  rate_wordings: ['per', 'a', 'an', 'each', 'every', 'adverbial', 'annual'],
  period_styles: ['parenthetical', 'in_year', 'range'], period_separators: ['hyphen', 'en_dash', 'em_dash'],
};
function profileWithStructuredClaim(claim: StructuredClaim, rendering: string): Profile {
  const evidence = { ...source, revision: { ...source.revision, payload: { ...source.revision.payload, text: rendering } } };
  return {
    ...profile,
    current_revision: {
      ...profile.current_revision, snapshot: { ...profile.current_revision.snapshot, claim_revision_ids: ['work-revision'] },
      claims: [{
        ...profile.current_revision.claims[0]!, id: 'work', kind: 'experience', current_revision_id: 'work-revision', rendering, needs_attention: false,
        revision: {
          ...metadata, id: 'work-revision', payload: { organization: 'Synthetic Workshop', role: 'Maintainer', claim },
          evidence_links: [{ evidence_revision_id: evidence.revision.id, support_kind: 'user_asserted', excerpt: rendering, evidence: evidence.revision }],
        },
      }],
    },
    evidence: [evidence],
  };
}
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
    const vocabulary = ownershipVocabulary;
    const request = vi.spyOn(api, 'request').mockImplementation(async <T,>(path: string, method = 'GET', body?: unknown) => {
      if (path === '/claims/vocabulary') return vocabulary as T;
      if (path === '/claims/render' && method === 'POST') {
        // This request body is produced by the editor in this process.
        const payload = body as { claim: StructuredClaim };
        const claim = payload.claim;
        return { language: 'en', rendering: `${vocabulary.verbs.find(verb => verb.key === claim.verb)?.label} ${claim.subject}${claim.metric ? ` by ${claim.metric.value}%` : ''}` } as T;
      }
      if (path === '/claims' && method === 'POST') return profile as T;
      throw new ApiError(404, 'not_found');
    });
    render(<ProfileEntries api={api} profile={profile} disabled={false} onProfile={onProfile} onDirtyChange={vi.fn()} onBusyChange={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: 'Work experience' }));
    await user.click(screen.getByRole('button', { name: 'Add work experience entry' }));
    expect(screen.queryByLabelText('Entry statement')).not.toBeInTheDocument();
    const verb = await screen.findByLabelText<HTMLSelectElement>('Ownership verb');
    expect(Array.from(verb.options).map(option => option.text)).toEqual(vocabulary.verbs.map((item, index) => `${index + 1}. ${item.label}`));
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

  it('preserves each server-provided action word and preposition rather than folding ownership labels', async () => {
    const user = userEvent.setup(); const api = new Api();
    const request = vi.spyOn(api, 'request').mockImplementation(async <T,>(path: string, method = 'GET', body?: unknown) => {
      if (path === '/claims/vocabulary') return ownershipVocabulary as T;
      if (path === '/claims/render' && method === 'POST') {
        // This request body is produced by the editor in this process.
        const payload = body as { claim: StructuredClaim };
        const claim = payload.claim;
        const verb = ownershipVocabulary.verbs.find(value => value.key === claim.verb)!;
        return { language: 'en', rendering: `${verb.label} ${claim.subject}` } as T;
      }
      throw new ApiError(404, 'not_found');
    });
    render(<ProfileEntries api={api} profile={profile} disabled={false} onProfile={vi.fn()} onDirtyChange={vi.fn()} onBusyChange={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: 'Projects' }));
    await user.click(screen.getByRole('button', { name: 'Add projects entry' }));
    const select = await screen.findByLabelText('Ownership verb');
    fireEvent.change(screen.getByLabelText('Subject'), { target: { value: 'synthetic records' } });
    for (const verb of ownershipVocabulary.verbs) {
      await user.selectOptions(select, verb.key);
      expect(await screen.findByText(`${verb.label} synthetic records`)).toBeVisible();
      expect(request.mock.calls.filter(call => call[0] === '/claims/render').at(-1)?.[2]).toEqual({
        claim: { schema_version: 1, verb: verb.key, subject: 'synthetic records' },
      });
    }
  });

  it('offers only server-supplied wording choices with English labels and unchanged defaults', async () => {
    const user = userEvent.setup(); const api = new Api();
    const vocabulary: ClaimVocabulary = {
      ...ownershipVocabulary, rate_wordings: ['annual', 'per'], period_styles: ['range', 'in_year'], period_separators: ['em_dash', 'hyphen'],
    };
    vi.spyOn(api, 'request').mockImplementation(async <T,>(path: string) => {
      if (path === '/claims/vocabulary') return vocabulary as T;
      if (path === '/claims/render') return { language: 'en', rendering: 'Server preview' } as T;
      throw new ApiError(404, 'not_found');
    });
    render(<ProfileEntries api={api} profile={profile} disabled={false} onProfile={vi.fn()} onDirtyChange={vi.fn()} onBusyChange={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: 'Work experience' }));
    await user.click(screen.getByRole('button', { name: 'Add work experience entry' }));
    expect(await screen.findByLabelText('Ownership verb')).toHaveValue('built');
    fireEvent.change(screen.getByLabelText('Metric value (optional)'), { target: { value: '30' } });
    expect(screen.getByLabelText('Metric unit')).toHaveValue('percent');
    expect(screen.getByLabelText('Metric rate')).toHaveValue('');
    const rate = screen.getByLabelText('Rate wording');
    expect(rate).toHaveValue('');
    expect(within(rate).getAllByRole<HTMLOptionElement>('option').map(option => option.value)).toEqual(['', ...vocabulary.rate_wordings]);
    expect(within(rate).getByRole('option', { name: 'Per (default)' })).toBeVisible();
    expect(within(rate).getByRole('option', { name: 'Annually (year only)' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Period start (optional)'), { target: { value: '2022' } });
    fireEvent.change(screen.getByLabelText('Period end (optional)'), { target: { value: '2022' } });
    const period = screen.getByLabelText('Period wording'); const separator = screen.getByLabelText('Period separator');
    expect(period).toHaveValue(''); expect(separator).toHaveValue('');
    expect(within(period).getAllByRole<HTMLOptionElement>('option').map(option => option.value)).toEqual(['', ...vocabulary.period_styles]);
    expect(within(separator).getAllByRole<HTMLOptionElement>('option').map(option => option.value)).toEqual(['', ...vocabulary.period_separators]);
    expect(within(period).getByRole('option', { name: 'In year (in 2022)' })).toBeEnabled();
    expect(within(separator).getByRole('option', { name: 'Em dash (—)' })).toBeVisible();
    expect(within(separator).queryByRole('option', { name: 'En dash (–)' })).not.toBeInTheDocument();
  });

  it.each([
    { name: 'annually in a single year', wording: 'annual', style: 'in_year', separator: '', start: '2022', end: '2022', suffix: 'annually in 2022' },
    { name: 'per year in parentheses', wording: 'per', style: 'parenthetical', separator: 'hyphen', start: '2021', end: '2022', suffix: 'per year (2021-2022)' },
    { name: 'yearly with an unparenthesized range', wording: 'adverbial', style: 'range', separator: 'em_dash', start: '2021', end: '2022', suffix: 'yearly 2021—2022' },
  ])('previews and saves newly selected $name without losing wording fields', async ({ wording, style, separator, start, end, suffix }) => {
    const user = userEvent.setup(); const api = new Api();
    const rendering = `Reduced batch runtime by 30% ${suffix}`;
    const request = vi.spyOn(api, 'request').mockImplementation(async <T,>(path: string, method = 'GET') => {
      if (path === '/claims/vocabulary') return ownershipVocabulary as T;
      if (path === '/claims/render') return { language: 'en', rendering } as T;
      if (path === '/claims' && method === 'POST') return profile as T;
      throw new ApiError(404, 'not_found');
    });
    render(<ProfileEntries api={api} profile={profile} disabled={false} onProfile={vi.fn()} onDirtyChange={vi.fn()} onBusyChange={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: 'Work experience' }));
    await user.click(screen.getByRole('button', { name: 'Add work experience entry' }));
    await user.selectOptions(await screen.findByLabelText('Ownership verb'), 'reduced');
    fireEvent.change(screen.getByLabelText('Subject'), { target: { value: 'batch runtime' } });
    fireEvent.change(screen.getByLabelText('Metric value (optional)'), { target: { value: '30' } });
    await user.selectOptions(screen.getByLabelText('Metric change'), 'decrease');
    await user.selectOptions(screen.getByLabelText('Metric rate'), 'year');
    await user.selectOptions(screen.getByLabelText('Rate wording'), wording);
    fireEvent.change(screen.getByLabelText('Period start (optional)'), { target: { value: start } });
    fireEvent.change(screen.getByLabelText('Period end (optional)'), { target: { value: end } });
    await user.selectOptions(screen.getByLabelText('Period wording'), style);
    if (separator) await user.selectOptions(screen.getByLabelText('Period separator'), separator);
    expect(await screen.findByText(rendering)).toBeVisible();
    const expected = {
      schema_version: 1, verb: 'reduced', subject: 'batch runtime',
      metric: { value: '30', unit: 'percent', change: 'decrease', per: 'year', rate_wording: wording },
      period: { start, end, style, ...(separator ? { separator } : {}) },
    };
    expect(request.mock.calls.filter(call => call[0] === '/claims/render').at(-1)?.[2]).toEqual({ claim: expected });
    await user.click(screen.getByRole('button', { name: 'Add evidence link' }));
    await user.selectOptions(screen.getByLabelText('Evidence revision 1'), 'source-revision');
    fireEvent.change(screen.getByLabelText('Source excerpt 1'), { target: { value: rendering } });
    await user.click(screen.getByRole('button', { name: 'Save entry revision' }));
    expect(request.mock.calls.find(call => call[0] === '/claims' && call[1] === 'POST')?.[2]).toEqual({
      kind: 'experience', payload: { claim: expected },
      evidence_links: [{ evidence_revision_id: 'source-revision', support_kind: 'user_asserted', excerpt: rendering }],
    });
  });

  it.each([
    { name: 'omitted default wording', rate: 'year', wording: undefined, style: undefined, separator: undefined, start: '2022', end: '2022', suffix: 'per year (2022)' },
    { name: 'explicit per and parenthetical en dash', rate: 'year', wording: 'per', style: 'parenthetical', separator: 'en_dash', start: '2021', end: '2022', suffix: 'per year (2021–2022)' },
    { name: 'annual and in-year wording', rate: 'year', wording: 'annual', style: 'in_year', separator: undefined, start: '2022', end: '2022', suffix: 'annually in 2022' },
    { name: 'adverbial rate and hyphen range', rate: 'quarter', wording: 'adverbial', style: 'range', separator: 'hyphen', start: '2021', end: '2022', suffix: 'quarterly 2021-2022' },
    { name: 'each rate and em dash range', rate: 'week', wording: 'each', style: 'range', separator: 'em_dash', start: '2021', end: '2022', suffix: 'each week 2021—2022' },
    { name: 'every rate and parenthetical months', rate: 'day', wording: 'every', style: 'parenthetical', separator: 'hyphen', start: '2022-01', end: '2022-03', suffix: 'every day (Jan 2022-Mar 2022)' },
    { name: 'a rate', rate: 'month', wording: 'a', style: 'parenthetical', separator: undefined, start: '2022', end: '2022', suffix: 'a month (2022)' },
    { name: 'an rate', rate: 'year', wording: 'an', style: 'parenthetical', separator: undefined, start: '2022', end: '2022', suffix: 'an year (2022)' },
  ] as const)('round-trips saved $name through edit, preview and save', async ({ rate, wording, style, separator, start, end, suffix }) => {
    const user = userEvent.setup(); const api = new Api(); const onProfile = vi.fn(); const onDirtyChange = vi.fn();
    const structured: StructuredClaim = {
      schema_version: 1, verb: 'reduced', subject: 'batch runtime',
      metric: { value: '30', unit: 'percent', change: 'decrease', per: rate, ...(wording ? { rate_wording: wording } : {}) },
      period: { start, end, ...(style ? { style } : {}), ...(separator ? { separator } : {}) },
    };
    const rendering = `Reduced batch runtime by 30% ${suffix}`;
    const saved = profileWithStructuredClaim(structured, rendering);
    const request = vi.spyOn(api, 'request').mockImplementation(async <T,>(path: string, method = 'GET') => {
      if (path === '/claims/vocabulary') return ownershipVocabulary as T;
      if (path === '/claims/render') return { language: 'en', rendering } as T;
      if (path === '/claims/work' && method === 'PUT') return saved as T;
      throw new ApiError(404, 'not_found');
    });
    render(<ProfileEntries api={api} profile={saved} disabled={false} onProfile={onProfile} onDirtyChange={onDirtyChange} onBusyChange={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: 'Work experience' }));
    await user.click(screen.getByRole('button', { name: 'Edit work experience entry 1' }));
    await screen.findByLabelText('Ownership verb');
    expect(screen.getByLabelText('Rate wording')).toHaveValue(wording ?? '');
    expect(screen.getByLabelText('Period wording')).toHaveValue(style ?? '');
    expect(screen.getByLabelText('Period separator')).toHaveValue(separator ?? '');
    expect(screen.getByLabelText('Source excerpt 1')).toHaveValue(rendering);
    expect(screen.getByLabelText('Claim rendering')).toHaveTextContent(rendering);
    expect(screen.getByRole('button', { name: 'History' })).toBeDisabled();
    expect(onDirtyChange).toHaveBeenLastCalledWith(true);
    expect(request.mock.calls.filter(call => call[0] === '/claims/render').at(-1)?.[2]).toEqual({ claim: structured });
    await user.click(screen.getByRole('button', { name: 'Save entry revision' }));
    expect(request.mock.calls.find(call => call[0] === '/claims/work' && call[1] === 'PUT')?.[2]).toEqual({
      kind: 'experience', payload: { organization: 'Synthetic Workshop', role: 'Maintainer', claim: structured },
      evidence_links: [{ evidence_revision_id: 'source-revision', support_kind: 'user_asserted', excerpt: rendering }],
    });
    expect(onProfile).toHaveBeenCalledWith(saved);
  });

  it('keeps invalid wording metadata visible and blocks preview and even forced submission until corrected', async () => {
    const user = userEvent.setup(); const api = new Api();
    const structured: StructuredClaim = {
      schema_version: 1, verb: 'reduced', subject: 'batch runtime',
      metric: { value: '30', unit: 'percent', change: 'decrease', per: 'year', rate_wording: 'annual' },
      period: { start: '2022', end: '2022', style: 'in_year' },
    };
    const saved = profileWithStructuredClaim(structured, 'Reduced batch runtime by 30% annually in 2022');
    const request = vi.spyOn(api, 'request').mockImplementation(async <T,>(path: string, method = 'GET') => {
      if (path === '/claims/vocabulary') return ownershipVocabulary as T;
      if (path === '/claims/render') return { language: 'en', rendering: 'Server preview' } as T;
      if (path === '/claims/work' && method === 'PUT') return saved as T;
      throw new ApiError(404, 'not_found');
    });
    render(<ProfileEntries api={api} profile={saved} disabled={false} onProfile={vi.fn()} onDirtyChange={vi.fn()} onBusyChange={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: 'Work experience' }));
    await user.click(screen.getByRole('button', { name: 'Edit work experience entry 1' }));
    await screen.findByLabelText('Ownership verb');
    const save = screen.getByRole('button', { name: 'Save entry revision' });
    const initialPreviews = request.mock.calls.filter(call => call[0] === '/claims/render').length;
    await user.selectOptions(screen.getByLabelText('Metric rate'), 'week');
    expect(screen.getByLabelText('Rate wording')).toHaveValue('annual');
    expect(within(screen.getByLabelText('Rate wording')).getByRole('option', { name: 'Annually (year only)' })).toBeDisabled();
    expect(screen.getByText('Annually requires the year metric rate.')).toBeVisible();
    expect(screen.getByLabelText('Claim rendering')).not.toHaveTextContent('Server preview');
    expect(save).toBeDisabled();
    fireEvent.submit(save.closest('form')!);
    expect(request.mock.calls.filter(call => call[0] === '/claims/render')).toHaveLength(initialPreviews);
    expect(request.mock.calls.some(call => call[0] === '/claims/work')).toBe(false);
    await user.selectOptions(screen.getByLabelText('Metric rate'), '');
    expect(screen.getByText('Choose a metric rate for the selected rate wording.')).toBeVisible();
    expect(save).toBeDisabled();
    fireEvent.submit(save.closest('form')!);
    expect(request.mock.calls.some(call => call[0] === '/claims/work')).toBe(false);
    await user.selectOptions(screen.getByLabelText('Rate wording'), '');
    expect(save).toBeEnabled();
    await user.selectOptions(screen.getByLabelText('Metric rate'), 'year');
    await user.selectOptions(screen.getByLabelText('Rate wording'), 'annual');
    const validPreviews = request.mock.calls.filter(call => call[0] === '/claims/render').length;
    fireEvent.change(screen.getByLabelText('Period end (optional)'), { target: { value: '2023' } });
    expect(screen.getByLabelText('Period wording')).toHaveValue('in_year');
    expect(within(screen.getByLabelText('Period wording')).getByRole('option', { name: 'In year (in 2022)' })).toBeDisabled();
    expect(screen.getByText('In year wording requires matching year-only start and end dates.')).toBeVisible();
    expect(save).toBeDisabled();
    fireEvent.submit(save.closest('form')!);
    expect(request.mock.calls.filter(call => call[0] === '/claims/render')).toHaveLength(validPreviews);
    expect(request.mock.calls.some(call => call[0] === '/claims/work')).toBe(false);
    fireEvent.change(screen.getByLabelText('Period start (optional)'), { target: { value: '2022-01' } });
    fireEvent.change(screen.getByLabelText('Period end (optional)'), { target: { value: '2022-01' } });
    expect(screen.getByText('In year wording requires matching year-only start and end dates.')).toBeVisible();
    expect(save).toBeDisabled();
    fireEvent.submit(save.closest('form')!);
    expect(request.mock.calls.some(call => call[0] === '/claims/work')).toBe(false);
    await user.selectOptions(screen.getByLabelText('Period wording'), 'parenthetical');
    expect(screen.queryByText('In year wording requires matching year-only start and end dates.')).not.toBeInTheDocument();
    expect(save).toBeEnabled();
    await user.click(save);
    expect(request.mock.calls.find(call => call[0] === '/claims/work' && call[1] === 'PUT')?.[2]).toMatchObject({
      payload: { claim: { metric: structured.metric, period: { start: '2022-01', end: '2022-01', style: 'parenthetical' } } },
    });
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
    const vocabulary = ownershipVocabulary;
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
      if (path === '/claims/vocabulary') return ownershipVocabulary as T;
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

  it('saves a source only with an owner-chosen source type and privacy class', async () => {
    const user = userEvent.setup(); const api = new Api();
    const request = vi.spyOn(api, 'request').mockImplementation(async <T,>(path: string, method = 'GET') => {
      if (path === '/evidence' && method === 'POST') return profile as T;
      throw new ApiError(404, 'not_found');
    });
    render(<ProfileEntries api={api} profile={profile} disabled={false} onProfile={vi.fn()} onDirtyChange={vi.fn()} onBusyChange={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: 'Sources' }));
    await user.click(screen.getByRole('button', { name: 'Add source or owner attestation' }));
    await user.type(screen.getByLabelText('Source text'), 'Synthetic design review notes.');
    await user.type(screen.getByLabelText('Source reference'), 'Synthetic design review');
    fireEvent.submit(screen.getByRole('button', { name: 'Save source revision' }).closest('form') as HTMLFormElement);
    expect(await screen.findByText('Choose the source type and privacy class.')).toBeVisible();
    expect(request.mock.calls.some(([path]) => path === '/evidence')).toBe(false);
    await user.selectOptions(screen.getByLabelText('Source type'), 'work_sample');
    await user.selectOptions(screen.getByLabelText('Privacy class'), 'confidential');
    await user.click(screen.getByLabelText('My employer owns this content'));
    await user.click(screen.getByRole('button', { name: 'Save source revision' }));
    const call = request.mock.calls.find(([path, method]) => path === '/evidence' && method === 'POST');
    expect(call?.[2]).toEqual({
      payload: { text: 'Synthetic design review notes.', source: 'Synthetic design review' },
      provenance: { source_type: 'work_sample', privacy_class: 'confidential', employer_owned: true },
    });
  });

  it('shows source type, origin and privacy class on every saved source', async () => {
    const user = userEvent.setup();
    const owned = { ...source, revision: { ...source.revision, provenance: { source_type: 'document' as const, origin: 'resume_import' as const, privacy_class: 'confidential' as const, employer_owned: true } } };
    render(<ProfileEntries api={new Api()} profile={{ ...profile, evidence: [owned] }} disabled={false} onProfile={vi.fn()} onDirtyChange={vi.fn()} onBusyChange={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: 'Sources' }));
    expect(screen.getByText('Source type: Document · Origin: Résumé import · Privacy: Confidential · Employer-owned · Content withheld from models')).toBeVisible();
  });

  it('edits source content with source type only, without changing claim pins', async () => {
    const user = userEvent.setup(); const api = new Api(); const onProfile = vi.fn();
    const saved = { ...profile, version: 2 };
    const request = vi.spyOn(api, 'request').mockResolvedValue(saved);
    render(<ProfileEntries api={api} profile={profile} disabled={false} onProfile={onProfile} onDirtyChange={vi.fn()} onBusyChange={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: 'Sources' }));
    expect(within(screen.getByRole('region', { name: 'Sources and owner attestations' })).getByText(/Privacy: Private/)).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Edit source 1' }));
    expect(screen.getByLabelText('Source type')).toHaveValue('owner_statement');
    expect(screen.queryByLabelText('Privacy class')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('My employer owns this content')).not.toBeInTheDocument();
    await user.type(screen.getByLabelText('Source text'), ' Revised.');
    await user.selectOptions(screen.getByLabelText('Source type'), 'work_sample');
    await user.click(screen.getByRole('button', { name: 'Save source revision' }));
    expect(request).toHaveBeenCalledExactlyOnceWith('/evidence/source', 'PUT', {
      payload: { text: 'Synthetic workshop notes. Revised.', source: 'Owner attestation' },
      provenance: { source_type: 'work_sample' },
    }, profile.version, expect.any(String));
    expect(onProfile).toHaveBeenCalledWith(saved);
    expect(profile.current_revision.claims[0]!.revision.evidence_links[0]!.evidence_revision_id).toBe('source-revision');
  });

  it('changes current classification separately and preserves source revisions and claim pins', async () => {
    const user = userEvent.setup(); const api = new Api(); const onProfile = vi.fn(); const onDirtyChange = vi.fn();
    const provenance = { ...source.revision.provenance, privacy_class: 'confidential' as const, employer_owned: true };
    const classifiedSource = { ...source, revision: { ...source.revision, provenance } };
    const saved: Profile = { ...profile, version: 2, evidence: [classifiedSource], current_revision: {
      ...profile.current_revision, claims: profile.current_revision.claims.map(claim => ({ ...claim, revision: { ...claim.revision,
        evidence_links: claim.revision.evidence_links.map(link => ({ ...link, evidence: { ...link.evidence, provenance } })),
      } })),
    } };
    const request = vi.spyOn(api, 'request').mockResolvedValue(saved);
    const view = render(<ProfileEntries api={api} profile={profile} disabled={false} onProfile={onProfile} onDirtyChange={onDirtyChange} onBusyChange={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: 'Sources' }));
    await user.click(screen.getByRole('button', { name: 'Change classification 1' }));
    expect(screen.getByLabelText('Privacy class')).toHaveValue('private');
    expect(screen.getByLabelText('My employer owns this content')).not.toBeChecked();
    expect(screen.getByLabelText('Privacy class')).toHaveAttribute('aria-describedby', 'evidence-classification-hint');
    expect(screen.getByLabelText('My employer owns this content')).toHaveAttribute('aria-describedby', 'evidence-classification-hint');
    expect(screen.getByText(/Reclassification takes effect immediately/)).toHaveTextContent('No re-save or re-pin is needed.');
    expect(screen.queryByLabelText('Source text')).not.toBeInTheDocument();
    expect(onDirtyChange).toHaveBeenLastCalledWith(true);
    expect(screen.getByRole('button', { name: 'Skills' })).toBeDisabled();
    await user.selectOptions(screen.getByLabelText('Privacy class'), 'confidential');
    await user.click(screen.getByLabelText('My employer owns this content'));
    await user.click(screen.getByRole('button', { name: 'Save source classification' }));
    expect(request).toHaveBeenCalledExactlyOnceWith('/evidence/source/classification', 'PUT',
      { privacy_class: 'confidential', employer_owned: true }, profile.version, expect.any(String));
    expect(onProfile).toHaveBeenCalledWith(saved);
    expect(screen.getByText('Source classification updated. Source revisions and claim pins are unchanged.')).toBeVisible();
    view.rerender(<ProfileEntries api={api} profile={saved} disabled={false} onProfile={onProfile} onDirtyChange={onDirtyChange} onBusyChange={vi.fn()} />);
    expect(within(screen.getByRole('region', { name: 'Sources and owner attestations' })).getByText(/Privacy: Confidential/)).toBeVisible();
    expect(saved.evidence[0]!.current_revision_id).toBe(source.current_revision_id);
    expect(saved.current_revision.snapshot.claim_revision_ids).toEqual(profile.current_revision.snapshot.claim_revision_ids);
    expect(saved.current_revision.claims[0]!.revision.id).toBe('skill-revision');
    expect(saved.current_revision.claims[0]!.revision.evidence_links[0]!.evidence_revision_id).toBe('source-revision');
    await user.click(screen.getByRole('button', { name: 'Skills' }));
    await user.click(screen.getByText('Evidence and authorship · revision 1'));
    expect(within(screen.getByText('Evidence and authorship · revision 1').parentElement!).getByText(/Privacy: Confidential/)).toBeVisible();
    expect(screen.getByText(/Evidence revision source-revision/)).toBeVisible();
  });
});
