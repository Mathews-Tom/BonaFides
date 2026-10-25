// SPDX-License-Identifier: MIT OR Apache-2.0
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { App } from './App';
import { Api, ApiError, type Profile, type Session } from './api';
const session: Session = { owner_id: 'owner', login_name: 'mira', csrf_token: 'session-csrf', expires_at: '2030-01-01' };
const profile: Profile = { id: 'profile', version: 1, current_revision: { id: 'revision-1', revision_no: 1, content_sha256: 'a'.repeat(64), created_at: '2026-01-01T00:00:00Z', author_owner_id: 'owner', reason: 'Initial profile', claims: [], snapshot: { display_name: 'Mira', headline: 'Engineer', claim_revision_ids: [] } }, evidence: [], quality_warnings: [] };
function mockRequest(api: Api, handler: (path: string, method: string) => Promise<unknown>) {
  return vi.spyOn(api, 'request').mockImplementation(async <T,>(path: string, method = 'GET') => await handler(path, method) as T);
}
function connectedApi(saveFailure?: Error) {
  const api = new Api();
  const request = mockRequest(api, async (path, method) => {
    if (path === '/session') return session;
    if (path === '/profile' && method === 'GET') return profile;
    if (path === '/profile' && method === 'PUT') { if (saveFailure) throw saveFailure; return { ...profile, version: 2, current_revision: { ...profile.current_revision, revision_no: 2 } }; }
    throw new ApiError(404, 'not_found');
  });
  return { api, request };
}
describe('profile editing', () => {
  it('retains typed edits and focuses conflict recovery without claiming success', async () => {
    const user = userEvent.setup(); const { api } = connectedApi(new ApiError(412, 'version_conflict'));
    render(<App api={api} />);
    const input = await screen.findByLabelText('Professional headline');
    await user.clear(input); await user.type(input, 'My retained revision'); await user.click(screen.getByRole('button', { name: 'Save profile' }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('A newer revision exists'); expect(alert).toHaveFocus();
    expect(input).toHaveValue('My retained revision'); expect(screen.queryByText('Saved as revision 2.')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Compare current revision' }));
    expect(await screen.findByText('Current server revision 1')).toBeInTheDocument(); expect(input).toHaveValue('My retained revision');
  });
  it('keeps the buffer on network failure and never publishes a saved message', async () => {
    const user = userEvent.setup(); const { api, request } = connectedApi(new TypeError('network'));
    render(<App api={api} />); const input = await screen.findByLabelText('Display name');
    await user.clear(input); await user.type(input, 'Draft identity'); await user.click(screen.getByRole('button', { name: 'Save profile' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('server could not be reached'); expect(input).toHaveValue('Draft identity');
    expect(screen.queryByText(/Saved as revision/)).not.toBeInTheDocument();
    const firstSave = request.mock.calls.find(call => call[1] === 'PUT');
    await user.click(screen.getByRole('button', { name: 'Save profile' }));
    const saves = request.mock.calls.filter(call => call[1] === 'PUT');
    expect(saves).toHaveLength(2);
    expect(saves[1]?.[4]).toBe(firstSave?.[4]);
    await user.type(input, ' changed');
    await user.click(screen.getByRole('button', { name: 'Save profile' }));
    const changedSave = request.mock.calls.filter(call => call[1] === 'PUT')[2];
    expect(changedSave?.[4]).not.toBe(firstSave?.[4]);
  });
  it('protects a source buffer from identity saves and benchmark replacement until explicit discard', async () => {
    const user = userEvent.setup(); const { api } = connectedApi();
    render(<App api={api} />);
    await user.click(await screen.findByRole('button', { name: 'Add source or owner attestation' }));
    await user.type(screen.getByLabelText('Source text'), 'Fictional project notes retained in this editor.');
    expect(screen.getByRole('button', { name: 'Save profile' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Load SYN-CORE benchmark evidence' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeDisabled();
    expect(screen.getByLabelText('Display name')).toBeDisabled();
    await user.selectOptions(screen.getByLabelText('Theme'), 'dark');
    expect(screen.getByLabelText('Source text')).toHaveValue('Fictional project notes retained in this editor.');
    await user.click(screen.getByRole('button', { name: 'Discard source changes' }));
    expect(screen.getByRole('button', { name: 'Save profile' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Load SYN-CORE benchmark evidence' })).toBeEnabled();
    expect(screen.getByLabelText('Display name')).toHaveValue('Mira');
  });
  it('honestly removes benchmark task controls when the server returns production 404', async () => {
    const user = userEvent.setup(); const { api } = connectedApi(); render(<App api={api} />);
    await user.click(await screen.findByRole('button', { name: 'Load SYN-CORE benchmark evidence' }));
    expect(await screen.findByText(/Benchmark endpoints are disabled here/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Start synthetic proof task' })).not.toBeInTheDocument();
  });
});
describe('owner authentication', () => {
  it('loads a challenge and retains credentials on failed sign in', async () => {
    const user = userEvent.setup(); const api = new Api();
    mockRequest(api, async (path, method) => {
      if (path === '/auth/challenge') return { csrf_token: 'pre-session', bootstrap_available: false };
      throw new ApiError(401, method === 'POST' ? 'invalid_credentials' : 'session_required');
    });
    render(<App api={api} />); await user.type(await screen.findByLabelText('Login name'), 'owner');
    await user.type(screen.getByLabelText('Password'), 'a long fictional password'); await user.click(screen.getByRole('button', { name: 'Sign in' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('session is unavailable'));
    expect(screen.getByLabelText('Password')).toHaveValue('a long fictional password'); expect(api.csrf).toBe('pre-session');
  });
});
describe('evidence and document lifecycle', () => {
  it('waits for completed work, reviews the exact hash and exposes only the rendered artifact', async () => {
    const user = userEvent.setup(); const api = new Api();
    const evidenceProfile = { ...profile, current_revision: { ...profile.current_revision, snapshot: { ...profile.current_revision.snapshot, claim_revision_ids: ['claim-1'] } } };
    const document = { id: 'document-1', version: 4, current_revision: { id: 'document-revision', content_sha256: 'exact-hash', body: { title: 'Mira dossier', sections: [{ key: 'facts', heading: 'Supported facts', blocks: [{ block_key: 'linux', text: 'Maintained Linux service checks.', claim_kind: 'factual', claim_revision_ids: ['claim-1'], evidence_revision_ids: ['evidence-1'] }] }] } }, reviewed: false, unsupported_claims: [], stale: false, stale_reason: null };
    mockRequest(api, async (path, method) => {
      if (path === '/session') return session;
      if (path === '/profile') return evidenceProfile;
      if (path === '/benchmarks/tasks') return { id: 'task-1', version: 1, state: 'pending', attempt: 0, result: null, last_error_code: null };
      if (path === '/workflows/task-1') return { id: 'task-1', version: 2, state: 'succeeded', attempt: 1, result: { document_id: 'document-1' }, last_error_code: null };
      if (path === '/documents/document-1') return document;
      if (path === '/documents/document-1/review' && method === 'POST') return { ...document, reviewed: true, version: 5 };
      if (path === '/documents/document-1/renders') return { id: 'render-1', version: 1, state: 'pending', attempt: 0, result: null, last_error_code: null };
      if (path === '/workflows/render-1') return { id: 'render-1', version: 2, state: 'succeeded', attempt: 1, result: { artifact_id: 'pdf-1' }, last_error_code: null };
      throw new ApiError(404, 'not_found');
    });
    render(<App api={api} />);
    await user.click(await screen.findByRole('button', { name: 'Start synthetic proof task' }));
    expect(screen.queryByText('Mira dossier')).not.toBeInTheDocument();
    expect(await screen.findByText('Mira dossier', {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Render reviewed PDF' })).toBeDisabled();
    await user.click(screen.getByText('Statement sources (factual)'));
    expect(screen.getByText('Evidence revisions: evidence-1')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Accept this exact revision' }));
    await user.click(screen.getByRole('button', { name: 'Render reviewed PDF' }));
    expect(screen.queryByRole('link', { name: 'Download rendered PDF' })).not.toBeInTheDocument();
    expect(await screen.findByRole('link', { name: 'Download rendered PDF' }, { timeout: 4000 })).toHaveAttribute('href', '/api/v1/artifacts/pdf-1');
  });
});
