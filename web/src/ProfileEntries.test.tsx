// SPDX-License-Identifier: MIT OR Apache-2.0
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { Api, type Profile } from './api';
import { ProfileEntries } from './ProfileEntries';

const metadata = { revision_no: 1, content_sha256: 'a'.repeat(64), created_at: '2026-01-01T00:00:00Z', author_owner_id: 'synthetic-owner' };
const source = { id: 'source', version: 1, archived: false, current_revision_id: 'source-revision', revision: { ...metadata, id: 'source-revision', payload: { text: 'Synthetic workshop notes.', source: 'Owner attestation' } } };
const profile: Profile = {
  id: 'synthetic-profile', version: 1,
  current_revision: {
    ...metadata, id: 'profile-revision', reason: 'Synthetic fixture',
    snapshot: { display_name: 'Synthetic Person', headline: '', claim_revision_ids: ['skill-revision'] },
    claims: [{ id: 'skill', kind: 'skill', version: 1, archived: false, current_revision_id: 'skill-revision', revision: {
      ...metadata, id: 'skill-revision', payload: { text: 'Synthetic workshop skill.' },
      evidence_links: [{ evidence_revision_id: source.revision.id, support_kind: 'user_asserted', excerpt: 'Synthetic workshop notes.', evidence: source.revision }],
    } }],
  },
  evidence: [source], quality_warnings: [], experience_coverage: { minimum_days: 0, maximum_days: 0, undated_or_open_entries: 0 },
};
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
});
