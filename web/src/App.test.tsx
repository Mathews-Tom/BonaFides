// SPDX-License-Identifier: MIT OR Apache-2.0
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from './App';
import { Api, ApiError, type DocumentRecord, type InstallationStatus, type Profile, type RequirementsView, type Session, type Snapshot, type Workflow } from './api';
import { stages, type ApplicationDetail } from './tracking-api';
const session: Session = { owner_id: 'owner', login_name: 'mira', csrf_token: 'session-csrf', expires_at: '2030-01-01' };
const profile: Profile = { id: 'profile', version: 1, current_revision: { id: 'revision-1', revision_no: 1, content_sha256: 'a'.repeat(64), created_at: '2026-01-01T00:00:00Z', author_owner_id: 'owner', reason: 'Initial profile', claims: [], snapshot: { display_name: 'Mira', headline: 'Engineer', claim_revision_ids: [] } }, evidence: [], quality_warnings: [], experience_coverage: { minimum_days: 0, maximum_days: 0, undated_or_open_entries: 0 } };
const installation: InstallationStatus = { namespace_id: 'namespace-one', maintenance_epoch: 0, workspace_version: 1, maintenance_active: false, recovery_required: false, operation: null, drain: { active_calls: 0, provider_executions: 0 } };
function task(overrides: Partial<Workflow> = {}): Workflow {
  return {
    id: 'task-1', version: 1, state: 'pending', attempt: 0, max_attempts: 3, result: null, last_error_code: null,
    created_at: '2026-10-25T12:00:00Z', updated_at: '2026-10-25T12:00:00Z', deadline_at: '2030-01-01T00:00:00Z', next_attempt_at: null,
    cancel_requested_at: null, lease_expires_at: null, last_heartbeat_at: null, queue_age_seconds: 0, remaining_attempts: 3,
    cancelable: true, retryable: false, retry_block_reason: 'state_not_retryable', delivery_state: 'not_attempted',
    cancellation: { requested: false, local_fence_settled: false, provider_stopped: null, completed_effects_preserved: true, reason: null },
    usage: { status: 'not_applicable', model_runs: 0, inference_post_attempts: 0, inference_post_limit: null, input_tokens: null, output_tokens: null, token_budget: 0, tokens_used: 0, cost_budget_microunits: 0, cost_used_microunits: null, currency: 'USD' },
    steps: { items: [], next_cursor: null }, attempt_events: { items: [], next_cursor: null }, approval: null, ...overrides,
  };
}
beforeEach(() => { window.history.replaceState(null, '', '/#/profile'); });
async function goTo(user: ReturnType<typeof userEvent.setup>, label: string) {
  if (label === 'Activity & reviews') await user.click(screen.getByRole('link', { name: label }));
  else await user.click(within(screen.getByRole('navigation', { name: 'Workspace' })).getByRole('link', { name: new RegExp(`^${label}`) }));
  await waitFor(() => expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(label));
}
async function openDiagnostics(user: ReturnType<typeof userEvent.setup>) {
  await goTo(user, 'Settings');
  const disclosure = screen.getByText('Advanced diagnostic tools', { selector: 'summary' });
  if (!disclosure.parentElement?.hasAttribute('open')) await user.click(disclosure);
}
afterEach(() => { window.history.replaceState(null, '', '/'); });
function mockRequest(api: Api, handler: (path: string, method: string, body?: unknown) => Promise<unknown>, baseDocument: DocumentRecord | null = null, applications: ApplicationDetail[] = [], maintenance: () => Promise<InstallationStatus> = async () => installation) {
  return vi.spyOn(api, 'request').mockImplementation(async <T,>(path: string, method = 'GET', body?: unknown) => {
    if (method === 'GET' && path === '/maintenance') return await maintenance() as T;
    if (method === 'GET' && path === '/document-templates') return { items: [{ version: 'bonafides-evidence-v1', label: 'Evidence', language: 'en', paper: 'A4' }, { version: 'bonafides-compact-v1', label: 'Compact', language: 'en', paper: 'A4' }] } as T;
    if (method === 'GET' && path === '/document-defaults') return { version: 1, template_version: 'bonafides-evidence-v1', tone: 'professional', language: 'en', revision_retention: 'until_owner_erasure', task_input_retention_seconds: 86400, temporary_export_retention_seconds: 86400, extension_supported: false } as T;
    if (method === 'GET' && path === '/profile/base-resume') { if (baseDocument) return baseDocument as T; throw new ApiError(404, 'base_resume_not_found'); }
    if (method === 'GET' && path === '/applications') return { items: applications, next_cursor: null, query_elapsed_ms: 1, as_of: '2026-10-25T12:00:00Z' } as T;
    if (method === 'GET' && path.startsWith('/applications/counts?')) {
      const query = new URLSearchParams(path.split('?')[1]);
      const zero = Object.fromEntries(stages.map(stage => [stage, 0]));
      return { period_start: query.get('period_start'), period_end: query.get('period_end'), current_by_stage: zero, archived_by_stage: zero, created_in_period: 0, stage_reached_in_period: zero, query_elapsed_ms: 1 } as T;
    }
    if (method === 'GET' && path === '/applications/attention') return { items: [], query_elapsed_ms: 1, as_of: '2026-10-25T12:00:00Z' } as T;
    return await handler(path, method, body) as T;
  });
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
  it('retains description-only drafts through navigation and themes, fences source edits, and explicitly discards them', async () => {
    const user = userEvent.setup(); const { api, request } = connectedApi();
    render(<App api={api} />);
    const description = await screen.findByLabelText('Description (optional)');
    expect(description).toHaveValue('');
    expect(description).not.toBeRequired();
    expect(screen.getByLabelText('Highlight / tagline (optional)')).not.toBeRequired();
    await user.type(description, 'A personal introduction.\nA second paragraph.');
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Sources' }));
    expect(screen.getByRole('button', { name: 'Add source or owner attestation' })).toBeDisabled();
    await goTo(user, 'Documents');
    await user.click(screen.getByRole('button', { name: 'Dark theme' }));
    await goTo(user, 'Profile');
    expect(screen.getByLabelText('Description (optional)')).toBe(description);
    expect(description).toHaveValue('A personal introduction.\nA second paragraph.');
    expect(screen.getByRole('complementary', { name: 'Retained workspace drafts' })).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Discard identity changes' }));
    expect(description).toHaveValue('');
    expect(screen.getByRole('button', { name: 'Add source or owner attestation' })).toBeEnabled();
    expect(request.mock.calls.some(call => call[1] === 'PUT')).toBe(false);
  });

  it('counts pasted astral characters as codepoints and blocks oversized text without truncating it, including direct submits', async () => {
    const user = userEvent.setup(); const { api, request } = connectedApi(new ApiError(412, 'version_conflict'));
    render(<App api={api} />);
    const headline = await screen.findByLabelText('Highlight / tagline (optional)');
    const description = screen.getByLabelText('Description (optional)');
    await user.clear(headline); await user.paste('🚀'.repeat(300));
    await user.click(description); await user.paste('🌍'.repeat(2000));
    expect(headline).toHaveValue('🚀'.repeat(300)); expect(description).toHaveValue('🌍'.repeat(2000));
    expect(headline).toHaveAccessibleDescription('300 / 300 characters');
    expect(description).toHaveAccessibleDescription('2000 / 2000 characters');
    expect(screen.getByRole('button', { name: 'Save profile' })).toBeEnabled();
    await user.paste('🌍');
    expect(description).toHaveValue('🌍'.repeat(2001));
    expect(description).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByRole('button', { name: 'Save profile' })).toBeDisabled();
    await act(async () => { fireEvent.submit(description.closest('form')!); });
    expect(await screen.findByRole('alert')).toHaveTextContent('Your text has not been changed');
    expect(request.mock.calls.some(call => call[1] === 'PUT')).toBe(false);
    fireEvent.change(description, { target: { value: '🌍'.repeat(2000) } });
    fireEvent.change(headline, { target: { value: '🚀'.repeat(301) } });
    expect(headline).toHaveValue('🚀'.repeat(301));
    expect(headline).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByRole('button', { name: 'Save profile' })).toBeDisabled();
    fireEvent.change(headline, { target: { value: '🚀'.repeat(300) } });
    await user.click(screen.getByRole('button', { name: 'Save profile' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('A newer revision exists'));
    expect(request.mock.calls.find(call => call[1] === 'PUT')?.[2]).toMatchObject({ headline: '🚀'.repeat(300), description: '🌍'.repeat(2000) });
    expect(description).toHaveValue('🌍'.repeat(2000));
  });

  it('rebases without losing description text, replaces from comparison explicitly, and saves clearable optional values', async () => {
    const user = userEvent.setup(); const api = new Api();
    let saved = profile;
    const request = mockRequest(api, async (path, method, body) => {
      if (path === '/session') return session;
      if (path === '/profile' && method === 'GET') return saved;
      if (path === '/profile' && method === 'PUT') {
        saved = { ...saved, version: saved.version + 1, current_revision: { ...saved.current_revision, revision_no: saved.current_revision.revision_no + 1, snapshot: body as Snapshot } };
        return saved;
      }
      throw new ApiError(404, 'not_found');
    });
    const view = render(<App api={api} />);
    const description = await screen.findByLabelText('Description (optional)');
    await user.type(description, 'My draft introduction.');
    saved = { ...profile, version: 2, current_revision: { ...profile.current_revision, revision_no: 2, snapshot: { ...profile.current_revision.snapshot, description: 'Saved elsewhere.' } } };
    await user.click(screen.getByRole('button', { name: 'Compare current revision' }));
    expect(await screen.findByText('Description: Saved elsewhere.')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Keep my text against this version' }));
    expect(description).toHaveValue('My draft introduction.');
    await user.click(screen.getByRole('button', { name: 'Save profile' }));
    expect(await screen.findByText('Saved as revision 3.')).toBeVisible();
    expect(request.mock.calls.find(call => call[1] === 'PUT')?.[3]).toBe(2);
    expect(screen.queryByRole('button', { name: 'Discard identity changes' })).not.toBeInTheDocument();
    view.unmount(); render(<App api={api} />);
    const reloaded = await screen.findByLabelText('Description (optional)');
    expect(reloaded).toHaveValue('My draft introduction.');
    await user.type(reloaded, ' Temporary edit.');
    await user.click(screen.getByRole('button', { name: 'Compare current revision' }));
    await user.click(await screen.findByRole('button', { name: 'Replace editor with current revision' }));
    expect(reloaded).toHaveValue('My draft introduction.');
    await user.clear(reloaded); await user.clear(screen.getByLabelText('Highlight / tagline (optional)'));
    await user.click(screen.getByRole('button', { name: 'Save profile' }));
    expect(await screen.findByText('Saved as revision 4.')).toBeVisible();
    expect(reloaded).toHaveValue(''); expect(saved.current_revision.snapshot.description).toBe('');
    expect(saved.current_revision.snapshot.headline).toBe('');
  });

  it('retains typed edits and focuses conflict recovery without claiming success', async () => {
    const user = userEvent.setup(); const { api } = connectedApi(new ApiError(412, 'version_conflict'));
    render(<App api={api} />);
    const input = await screen.findByLabelText('Highlight / tagline (optional)');
    const description = screen.getByLabelText('Description (optional)');
    await user.type(description, 'My retained biography.');
    await user.clear(input); await user.type(input, 'My retained revision'); await user.click(screen.getByRole('button', { name: 'Save profile' }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('A newer revision exists'); expect(alert).toHaveFocus();
    expect(input).toHaveValue('My retained revision'); expect(screen.queryByText('Saved as revision 2.')).not.toBeInTheDocument();
    expect(description).toHaveValue('My retained biography.');
    await user.click(screen.getByRole('button', { name: 'Compare current revision' }));
    expect(await screen.findByText('Current server revision 1')).toBeInTheDocument(); expect(input).toHaveValue('My retained revision');
    expect(screen.getByText('Description: Not supplied')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Keep my text against this version' }));
    expect(description).toHaveValue('My retained biography.');
    await user.click(screen.getByRole('button', { name: 'Discard identity changes' }));
    expect(description).toHaveValue(''); expect(input).toHaveValue('Engineer');
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
    await user.click(await screen.findByRole('button', { name: 'Sources' }));
    await user.click(await screen.findByRole('button', { name: 'Add source or owner attestation' }));
    await user.type(screen.getByLabelText('Source text'), 'Fictional project notes retained in this editor.');
    expect(screen.getByRole('button', { name: 'Save profile' })).toBeDisabled();
    await openDiagnostics(user);
    expect(screen.getByRole('button', { name: 'Load SYN-CORE benchmark evidence' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeDisabled();
    await goTo(user, 'Profile');
    expect(screen.getByLabelText('Display name')).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Dark theme' }));
    expect(screen.getByLabelText('Source text')).toHaveValue('Fictional project notes retained in this editor.');
    await user.click(screen.getByRole('button', { name: 'Discard source changes' }));
    expect(screen.getByRole('button', { name: 'Save profile' })).toBeEnabled();
    await openDiagnostics(user);
    expect(screen.getByRole('button', { name: 'Load SYN-CORE benchmark evidence' })).toBeEnabled();
    await goTo(user, 'Profile');
    expect(screen.getByLabelText('Display name')).toHaveValue('Mira');
  });
  it('honestly removes benchmark task controls when the server returns production 404', async () => {
    const user = userEvent.setup(); const { api } = connectedApi(); render(<App api={api} />);
    await screen.findByLabelText('Display name'); await openDiagnostics(user);
    await user.click(await screen.findByRole('button', { name: 'Load SYN-CORE benchmark evidence' }));
    expect(await screen.findByText(/Benchmark endpoints are disabled here/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Start synthetic proof task' })).not.toBeInTheDocument();
  });
  it.each([false, true])('retains an unsaved profile while password confirmation fails=%s', async failure => {
    const user = userEvent.setup(); const api = new Api();
    mockRequest(api, async (path, method) => {
      if (path === '/session') return session;
      if (path === '/profile' && method === 'GET') return profile;
      if (path === '/session/reauthenticate') {
        if (failure) throw new ApiError(403, 'reauthentication_failed');
        return { ...session, csrf_token: 'confirmed-csrf' };
      }
      throw new ApiError(404, 'not_found');
    });
    render(<App api={api} />);
    const input = await screen.findByLabelText('Highlight / tagline (optional)');
    await user.clear(input); await user.type(input, 'Unsaved profile wording');
    await goTo(user, 'Settings');
    await user.click(screen.getByText('Confirm owner identity', { selector: 'summary' }));
    const password = screen.getByLabelText('Password to confirm owner identity');
    await user.type(password, 'Synthetic confirmation phrase 817!');
    await user.click(screen.getByRole('button', { name: 'Confirm owner identity' }));
    if (failure) {
      expect(await screen.findByRole('alert')).toHaveTextContent('reauthentication_failed');
      expect(api.csrf).toBe(session.csrf_token);
    } else {
      await screen.findByText(/Owner identity confirmed for five minutes/);
      expect(api.csrf).toBe('confirmed-csrf');
    }
    await waitFor(() => expect(password).toHaveValue(''));
    expect(input).toHaveValue('Unsaved profile wording');
    await goTo(user, 'Profile');
    expect(screen.getByRole('button', { name: 'Save profile' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeDisabled();
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
  it('allows corrected credentials after a failed attempt consumes its challenge', async () => {
    const user = userEvent.setup(); const api = new Api();
    const password = 'a private account password';
    let challengeNumber = 0; let activeChallenge: string | null = null; let authenticated = false;
    mockRequest(api, async (path, method, body) => {
      if (path === '/auth/challenge') {
        activeChallenge = `one-use-challenge-${++challengeNumber}`;
        return { csrf_token: activeChallenge, bootstrap_available: false };
      }
      if (path === '/session' && method === 'POST') {
        if (!activeChallenge || api.csrf !== activeChallenge) throw new ApiError(403, 'csrf_rejected');
        activeChallenge = null;
        if (!body || typeof body !== 'object' || !('password' in body) || body.password !== password) throw new ApiError(401, 'authentication_failed');
        authenticated = true; return session;
      }
      if (path === '/session' && authenticated) return session;
      if (path === '/session') throw new ApiError(401, 'session_required');
      if (path === '/profile' && authenticated) return profile;
      throw new ApiError(404, 'not_found');
    });
    render(<App api={api} />);
    await user.type(await screen.findByLabelText('Login name'), session.login_name);
    await user.type(screen.getByLabelText('Password'), 'an incorrect account password');
    await user.click(screen.getByRole('button', { name: 'Sign in' }));
    await waitFor(() => expect(screen.getByRole('alert')).toBeVisible());
    await user.clear(screen.getByLabelText('Password'));
    await user.type(screen.getByLabelText('Password'), password);
    await user.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByLabelText('Display name')).toHaveValue(profile.current_revision.snapshot.display_name);
  });
});
describe('evidence and document lifecycle', () => {
  it('waits for completed work, reviews the exact hash and exposes only the rendered artifact', async () => {
    const user = userEvent.setup(); const api = new Api();
    const evidenceProfile = { ...profile, current_revision: { ...profile.current_revision, snapshot: { ...profile.current_revision.snapshot, claim_revision_ids: ['claim-1'] } } };
    let document: DocumentRecord = { id: 'document-1', version: 4, kind: 'resume', scope: 'base', application_id: null, artifacts: [], current_revision: { id: 'document-revision', revision_no: 1, profile_revision_id: 'revision-1', template_version: 'bonafides-base-v1', origin: 'deterministic', created_at: '2026-10-25T12:00:00Z', validation_issues: [], provenance_snapshot: { mode: 'deterministic_base', model_run: null, facts: [{ claim_revision_id: 'claim-1', evidence_revision_id: 'evidence-1', claim_text: 'Maintained Linux service checks.', evidence_text: 'Synthetic service notes', source: 'SYN-CORE', status: 'supported', claim_sha256: 'a'.repeat(64), evidence_sha256: 'b'.repeat(64) }] }, content_sha256: 'exact-hash', body: { title: 'Mira dossier', sections: [{ key: 'facts', heading: 'Supported facts', blocks: [{ block_key: 'linux', text: 'Maintained Linux service checks.', claim_kind: 'factual', claim_revision_ids: ['claim-1'], evidence_revision_ids: ['evidence-1'] }] }] } }, reviewed: false, unsupported_claims: [], stale: false, stale_reason: null };
    let proofReads = 0;
    mockRequest(api, async (path, method) => {
      if (path === '/session') return session;
      if (path === '/profile') return evidenceProfile;
      if (path === '/benchmarks/tasks') return task();
      if (path === '/workflows/task-1') return ++proofReads === 1 ? task() : task({ version: 2, state: 'succeeded', attempt: 1, result: { document_id: 'document-1' } });
      if (path === '/documents/document-1') return document;
      if (path === '/documents/document-1/review' && method === 'POST') { document = { ...document, reviewed: true, version: 5 }; return document; }
      if (path === '/documents/document-1/renders') return task({ id: 'render-1' });
      if (path === '/workflows/render-1') { document = { ...document, artifacts: [{ id: 'pdf-1', revision_id: 'document-revision', format: 'pdf' }] }; return task({ id: 'render-1', version: 2, state: 'succeeded', attempt: 1, result: { document_id: 'document-1', artifact_id: 'pdf-1' } }); }
      throw new ApiError(404, 'not_found');
    });
    render(<App api={api} />);
    await screen.findByLabelText('Display name'); await openDiagnostics(user);
    await user.click(await screen.findByRole('button', { name: 'Start synthetic proof task' }));
    expect(screen.queryByDisplayValue('Mira dossier')).not.toBeInTheDocument();
    expect(await screen.findByDisplayValue('Mira dossier', {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Render reviewed PDF' })).toBeDisabled();
    await user.click(screen.getByText('Statement sources (factual)'));
    expect(screen.getByText('Synthetic service notes')).toBeVisible();
    await user.click(screen.getByRole('checkbox', { name: /I checked block linux/ }));
    await user.click(screen.getByRole('button', { name: 'Accept this exact revision' }));
    await user.click(screen.getByRole('button', { name: 'Render reviewed PDF' }));
    expect(screen.queryByRole('link', { name: /Download rendered PDF/ })).not.toBeInTheDocument();
    expect(await screen.findByRole('link', { name: /Download rendered PDF/ }, { timeout: 4000 })).toHaveAttribute('href', '/api/v1/artifacts/pdf-1');
  });
  it('protects dirty document text from sign-out, benchmark replacement and profile changes while allowing theme changes', async () => {
    const user = userEvent.setup(); const api = new Api();
    const document: DocumentRecord = { id: 'base', version: 1, scope: 'base', kind: 'resume', application_id: null, reviewed: false, unsupported_claims: [], stale: false, stale_reason: null, artifacts: [], current_revision: { id: 'saved-document-revision', revision_no: 1, profile_revision_id: profile.current_revision.id, template_version: 'bonafides-evidence-v1', origin: 'deterministic', created_at: '2026-10-25T12:00:00Z', content_sha256: 'a'.repeat(64), provenance_snapshot: { mode: 'deterministic_base', facts: [] }, validation_issues: [], body: { title: 'Base résumé', sections: [{ key: 'facts', heading: 'Experience', blocks: [{ block_key: 'b-1', text: 'Fictional accepted source text.', claim_kind: 'factual', claim_revision_ids: ['claim-1'], evidence_revision_ids: [] }] }] } } };
    const savedTask = task({ state: 'succeeded', result: { document_id: 'different-result' } });
    const request = mockRequest(api, async path => {
      if (path === '/session') return session;
      if (path === '/profile') return profile;
      if (path === '/workflows?limit=30') return { items: [savedTask], next_cursor: null };
      if (path === '/workflows/task-1') return savedTask;
      throw new ApiError(404, 'not_found');
    }, document);
    render(<App api={api} />); const input = await screen.findByLabelText('Block text: b-1');
    await goTo(user, 'Documents');
    await waitFor(() => expect(input).toBeEnabled());
    fireEvent.change(input, { target: { value: 'Private unsaved document text.' } });
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeDisabled();
    await openDiagnostics(user);
    expect(screen.getByRole('button', { name: 'Load SYN-CORE benchmark evidence' })).toBeDisabled();
    await goTo(user, 'Profile');
    expect(screen.getByRole('button', { name: 'Save profile' })).toBeDisabled();
    await goTo(user, 'Documents');
    expect(screen.getByRole('button', { name: 'Tracked application documents' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Dark theme' }));
    expect(input).toHaveValue('Private unsaved document text.');
    await goTo(user, 'Activity & reviews');
    await user.click(screen.getByRole('button', { name: 'Load tasks' }));
    await user.click(await screen.findByRole('button', { name: /Succeeded · Task · task-1/ }));
    await screen.findByRole('status', { name: 'Task state' });
    expect(screen.getByRole('button', { name: 'Open document result' })).toBeDisabled();
    expect(input).toHaveValue('Private unsaved document text.');
    expect(request.mock.calls.some(call => call[0] === '/documents/different-result')).toBe(false);
    await goTo(user, 'Documents');
    await user.click(screen.getByRole('button', { name: 'Discard document changes' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Sign out' })).toBeEnabled());
    expect(input).toHaveValue('Fictional accepted source text.');
  });
});

function connectedSuitabilityApi() {
  const api = new Api();
  const state = {
    profile,
    reviewReads: 0,
    review: {
      posting_id: 'posting-one', version: 1, source_text: 'SQL', source_sha256: 'a'.repeat(64), model_route: { status: 'qualified', reason: 'Explicit synthetic test route', identity: null },
      current_revision: { id: 'requirements-one', revision_no: 1, source_sha256: 'a'.repeat(64), requirements: [{ requirement_key: 'req-1', text: 'SQL', importance: 'required', span: { start: 0, end: 3, quote: 'SQL' }, hard_constraint: null }], confirmed: true, created_at: profile.current_revision.created_at, provenance: { origin: 'manual' }, stale: false },
      latest_assessment: { id: 'assessment-one', profile_revision_id: 'revision-1', requirements_revision_id: 'requirements-one', created_at: profile.current_revision.created_at, stale: false, stale_reasons: [], hard_eligibility: 'unknown', hard_constraints: [{ requirement_key: null, state: 'unknown', reason: 'no_reviewed_constraints' }], assessments: [{ requirement_key: 'req-1', coverage: 'not_evidenced', claim_revision_ids: [], evidence_revision_ids: [], explanation: 'No selected source.' }], evidence_coverage: { supported: 0, partial: 0, not_evidenced: 1, conflicting: 0, total: 1 }, provenance: { origin: 'owner_reviewed' } },
    } as RequirementsView,
    workflow: task({ id: 'task-one', state: 'running', attempt: 1, max_attempts: 2, kind: 'posting_extract', result: { posting_id: 'posting-one', source_sha256: 'a'.repeat(64) } }),
  };
  mockRequest(api, async (path, method) => {
    if (path === '/session') return session;
    if (path === '/profile' && method === 'GET') return state.profile;
    if (path === '/profile' && method === 'PUT') {
      state.profile = { ...state.profile, version: 2, current_revision: { ...state.profile.current_revision, id: 'revision-2', revision_no: 2, snapshot: { ...state.profile.current_revision.snapshot, headline: 'Updated identity' } } };
      return state.profile;
    }
    const posting = { id: 'posting-one', version: state.review.version, title: 'Engineer', company: 'Synthetic North', location_text: '', status: 'open', created_at: profile.current_revision.created_at, updated_at: profile.current_revision.created_at, description_text: 'SQL', canonical_url: null, content_sha256: 'c'.repeat(64), source_config_id: 'manual-source', source_kind: 'manual', external_id: 'one', last_observed_at: profile.current_revision.created_at, disposition: null };
    if (path === '/postings') return { items: [posting], next_cursor: null, as_of: profile.current_revision.created_at, query_elapsed_ms: 1 };
    if (path === '/postings/posting-one') return posting;
    if (path === '/postings/posting-one/requirements') { state.reviewReads += 1; return state.review; }
    if (path === '/postings/posting-one/extract') { state.review = { ...state.review, version: state.review.version + 1 }; return { posting_id: 'posting-one', workflow_id: 'task-one' }; }
    if (path === '/workflows/task-one') return state.workflow;
    if (path === '/sources') return { items: [] };
    throw new ApiError(404, 'not_found');
  });
  return { api, state };
}
describe('workspace suitability freshness propagation', () => {
  it('marks a saved assessment stale immediately after a workspace profile save and retains the local coverage buffer', async () => {
    const { api, state } = connectedSuitabilityApi();
    render(<App api={api} />);
    const user = userEvent.setup(); await screen.findByLabelText('Display name'); await goTo(user, 'Opportunities');
    fireEvent.click(await screen.findByRole('button', { name: 'Read Engineer at Synthetic North' }));
    await screen.findByText('Current snapshot');
    fireEvent.click(screen.getByRole('button', { name: 'Review evidence coverage manually' }));
    fireEvent.change(screen.getByLabelText('Explanation of evidence and gaps'), { target: { value: 'Do not lose my ongoing coverage review.' } });
    await goTo(user, 'Profile');
    fireEvent.change(screen.getByLabelText('Highlight / tagline (optional)'), { target: { value: 'Updated identity' } });
    expect(screen.getByRole('button', { name: 'Save profile' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: 'Save profile' }));
    await goTo(user, 'Opportunities');
    expect(await screen.findByText('Stale historical snapshot — not a current eligibility assertion')).toBeVisible();
    expect(screen.getByLabelText('Explanation of evidence and gaps')).toHaveValue('Do not lose my ongoing coverage review.');
    expect(screen.getByRole('button', { name: 'Save owner-reviewed assessment' })).toBeDisabled();
    expect(await screen.findByRole('region', { name: 'Current profile comparison' })).toHaveTextContent('revision-2');
    await goTo(user, 'Profile');
    expect(screen.getByLabelText('Highlight / tagline (optional)')).toBeEnabled();
    expect(state.reviewReads).toBe(1);
    expect(state.review.latest_assessment?.stale).toBe(false);
  });

  it('keeps manual profile editing available during durable model work while updating visible assessment freshness', async () => {
    const { api, state } = connectedSuitabilityApi();
    render(<App api={api} />);
    const user = userEvent.setup(); await screen.findByLabelText('Display name'); await goTo(user, 'Opportunities');
    fireEvent.click(await screen.findByRole('button', { name: 'Read Engineer at Synthetic North' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Request extraction proposal' }));
    await waitFor(() => expect(screen.getByRole('status', { name: 'Suitability task status' })).toHaveTextContent('Task running.'));
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeDisabled();
    await goTo(user, 'Profile');
    expect(screen.getByLabelText('Highlight / tagline (optional)')).toBeEnabled();
    fireEvent.change(screen.getByLabelText('Highlight / tagline (optional)'), { target: { value: 'Updated identity' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save profile' }));
    await goTo(user, 'Opportunities');
    expect(await screen.findByText('Stale historical snapshot — not a current eligibility assertion')).toBeVisible();
    expect(screen.getByRole('region', { name: 'Current suitability task' })).toHaveTextContent('task-one');
    expect(state.workflow.state).toBe('running');
    await goTo(user, 'Profile');
    expect(screen.getByLabelText('Highlight / tagline (optional)')).toBeEnabled();
  });

  it('preserves unsaved identity text when an explicit citation reload observes a newer profile', async () => {
    const { api, state } = connectedSuitabilityApi();
    render(<App api={api} />);
    const user = userEvent.setup(); await screen.findByLabelText('Display name'); await goTo(user, 'Opportunities');
    fireEvent.click(await screen.findByRole('button', { name: 'Read Engineer at Synthetic North' }));
    await screen.findByText('Current snapshot');
    await goTo(user, 'Profile');
    fireEvent.change(screen.getByLabelText('Highlight / tagline (optional)'), { target: { value: 'My unsaved identity wording.' } });
    state.profile = { ...state.profile, version: 2, current_revision: { ...state.profile.current_revision, id: 'revision-2', revision_no: 2 } };
    await goTo(user, 'Opportunities');
    fireEvent.click(screen.getByRole('button', { name: 'Reload profile citations' }));
    await goTo(user, 'Profile');
    expect(await screen.findByText('Current server revision 2')).toBeVisible();
    expect(screen.getByLabelText('Highlight / tagline (optional)')).toHaveValue('My unsaved identity wording.');
    await goTo(user, 'Opportunities');
    expect(screen.getByText('Stale historical snapshot — not a current eligibility assertion')).toBeVisible();
  });
});

describe('task navigation and workspace edit boundaries', () => {
  it('retains profile and tracking drafts through task selection and completion without opening a result', async () => {
    const user = userEvent.setup(); const api = new Api();
    const application: ApplicationDetail = {
      id: 'application-one', title: 'Engineer', company: 'Synthetic North', version: 3, attempt_no: 1,
      posting_id: 'posting-one', opportunity_id: 'opportunity-one', current_stage: 'preparing', archived_at: null,
      created_at: '2026-10-25T12:00:00Z', updated_at: '2026-10-25T12:00:00Z', next_action: null, current_stage_event_id: 'stage-one',
      notes: [{ id: 'note-one', version: 1, body: 'Saved private note', created_at: '2026-10-25T12:00:00Z', updated_at: '2026-10-25T12:00:00Z' }],
      contacts: [], appointments: [], attachments: [],
    };
    let workflow = task({ state: 'running', kind: 'document_generate' });
    const request = mockRequest(api, async path => {
      if (path === '/session') return session;
      if (path === '/profile') return profile;
      if (path === '/applications/application-one') return application;
      if (path === '/applications/application-one/stage-events') return { items: [], next_cursor: null };
      if (path === '/workflows?limit=30') return { items: [workflow], next_cursor: null };
      if (path === '/workflows/task-1') return workflow;
      throw new ApiError(404, 'not_found');
    }, null, [application]);
    render(<App api={api} />);
    await screen.findByLabelText('Display name'); await goTo(user, 'Applications');
    await user.click(await screen.findByRole('button', { name: 'Open Engineer at Synthetic North' }));
    await user.click(await screen.findByRole('button', { name: 'Edit private note 1' }));
    fireEvent.change(screen.getByLabelText(/Private note text/), { target: { value: 'Private unsaved tracking context.' } });
    await goTo(user, 'Profile');
    fireEvent.change(screen.getByLabelText('Highlight / tagline (optional)'), { target: { value: 'Private unsaved profile context.' } });
    await goTo(user, 'Activity & reviews');
    await user.click(screen.getByRole('button', { name: 'Load tasks' }));
    await user.click(await screen.findByRole('button', { name: /Running · document generate · task-1/ }));
    await screen.findByRole('status', { name: 'Task state' });
    workflow = { ...workflow, version: 2, state: 'succeeded', result: { document_id: 'retained-task-result' } };
    await user.click(screen.getByRole('button', { name: 'Reload task status' }));
    await waitFor(() => expect(screen.getByRole('status', { name: 'Task state' })).toHaveTextContent('Succeeded'));
    expect(screen.getByLabelText('Highlight / tagline (optional)')).toHaveValue('Private unsaved profile context.');
    expect(screen.getByLabelText(/Private note text/)).toHaveValue('Private unsaved tracking context.');
    expect(screen.getByRole('button', { name: 'Open document result' })).toBeDisabled();
    expect(request.mock.calls.some(call => call[0] === '/documents/retained-task-result')).toBe(false);
    expect(request.mock.calls.filter(call => call[1] === 'PUT' || call[1] === 'POST')).toHaveLength(0);
  });
});

describe('maintenance and preserved-workspace reconnection', () => {
  it('resumes writes after a transient aborted poll only when the workspace is unchanged', async () => {
    const user = userEvent.setup(); const api = new Api(); let offline = false;
    mockRequest(api, async path => {
      if (path === '/session') return session;
      if (path === '/profile') return profile;
      throw new ApiError(404, 'not_found');
    }, null, [], async () => { if (offline) throw new DOMException('Request stopped', 'AbortError'); return installation; });
    render(<App api={api} />);
    const input = await screen.findByLabelText('Highlight / tagline (optional)');
    await waitFor(() => expect(api.writeBlock).toBeNull());
    await user.clear(input); await user.type(input, 'Retained through a transient transport failure');
    offline = true; fireEvent(document, new Event('visibilitychange'));
    await screen.findByText('The installation could not be reached.');
    expect(api.writeBlock).toBe('workspace_reconnect_required');
    expect(screen.getByRole('button', { name: 'Save profile' })).toBeDisabled();
    offline = false; fireEvent(document, new Event('visibilitychange'));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save profile' })).toBeEnabled());
    expect(api.writeBlock).toBeNull();
    expect(screen.getByRole('button', { name: 'Save profile' })).toBeEnabled();
    expect(input).toHaveValue('Retained through a transient transport failure');
    expect(screen.queryByRole('button', { name: 'Check connection and compare saved workspace' })).not.toBeInTheDocument();
  });

  it('does not let a poll started before another request failed reopen writes', async () => {
    const user = userEvent.setup(); const api = new Api(); let holdPoll = false;
    let completePoll: ((value: InstallationStatus) => void) | undefined;
    mockRequest(api, async path => {
      if (path === '/session') return session;
      if (path === '/profile') return profile;
      throw new ApiError(404, 'not_found');
    }, null, [], async () => holdPoll ? new Promise<InstallationStatus>(resolve => { completePoll = resolve; }) : installation);
    render(<App api={api} />);
    const input = await screen.findByLabelText('Highlight / tagline (optional)');
    await waitFor(() => expect(api.writeBlock).toBeNull());
    await user.clear(input); await user.type(input, 'Draft retained while a stale poll settles');
    holdPoll = true; fireEvent(document, new Event('visibilitychange'));
    await waitFor(() => expect(completePoll).toBeTypeOf('function'));
    act(() => { api.onUnavailable?.(new TypeError('Failed to fetch')); });
    await screen.findByText('The installation could not be reached.');
    await act(async () => { completePoll!(installation); });
    expect(api.writeBlock).toBe('workspace_reconnect_required');
    expect(screen.getByRole('button', { name: 'Save profile' })).toBeDisabled();
    expect(screen.getByText('The installation could not be reached.')).toBeVisible();
    holdPoll = false; fireEvent(document, new Event('visibilitychange'));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save profile' })).toBeEnabled());
    expect(api.writeBlock).toBeNull();
    expect(input).toHaveValue('Draft retained while a stale poll settles');
  });

  it.each([
    ['workspace version', { workspace_version: installation.workspace_version + 1 }],
    ['namespace', { namespace_id: 'replacement-namespace' }],
    ['maintenance epoch', { maintenance_epoch: installation.maintenance_epoch + 1 }],
  ])('keeps writes closed after an offline poll when the %s changed', async (_label, changed) => {
    const user = userEvent.setup(); const api = new Api(); let offline = false; let status = installation;
    mockRequest(api, async path => {
      if (path === '/session') return session;
      if (path === '/profile') return profile;
      throw new ApiError(404, 'not_found');
    }, null, [], async () => { if (offline) throw new TypeError('Failed to fetch'); return status; });
    render(<App api={api} />);
    const input = await screen.findByLabelText('Highlight / tagline (optional)');
    await waitFor(() => expect(api.writeBlock).toBeNull());
    await user.clear(input); await user.type(input, 'Draft retained for explicit comparison');
    offline = true; fireEvent(document, new Event('visibilitychange'));
    await screen.findByText('The installation could not be reached.');
    status = { ...installation, ...changed }; offline = false;
    fireEvent(document, new Event('visibilitychange'));
    await screen.findByText('Review the current workspace before resuming writes.');
    expect(api.writeBlock).toBe('workspace_reconnect_required');
    expect(screen.getByRole('button', { name: 'Save profile' })).toBeDisabled();
    expect(input).toHaveValue('Draft retained for explicit comparison');
  });

  it('keeps a draft through maintenance and resumes only after an explicit unchanged-workspace comparison', async () => {
    const user = userEvent.setup(); const api = new Api(); let status = installation;
    mockRequest(api, async path => {
      if (path === '/session') return session;
      if (path === '/profile') return profile;
      throw new ApiError(404, 'not_found');
    }, null, [], async () => status);
    render(<App api={api} />);
    const input = await screen.findByLabelText('Highlight / tagline (optional)');
    await waitFor(() => expect(api.writeBlock).toBeNull());
    await user.clear(input); await user.type(input, 'Unsaved wording kept through the pause');
    status = { ...installation, maintenance_active: true, maintenance_epoch: 1 };
    fireEvent(document, new Event('visibilitychange'));
    await screen.findByText('Maintenance is active. Writers remain closed.');
    expect(api.writeBlock).toBe('workspace_reconnect_required');
    expect(screen.getByRole('button', { name: 'Save profile' })).toBeDisabled();
    expect(input).toBeEnabled(); expect(input).toHaveValue('Unsaved wording kept through the pause');
    status = { ...status, maintenance_active: false };
    fireEvent(document, new Event('visibilitychange'));
    await screen.findByText('Review the current workspace before resuming writes.');
    expect(api.writeBlock).toBe('workspace_reconnect_required');
    await user.click(screen.getByRole('button', { name: 'Check connection and compare saved workspace' }));
    await user.click(await screen.findByRole('button', { name: 'Resume writes and keep all editor text' }));
    await waitFor(() => expect(api.writeBlock).toBeNull());
    expect(input).toBe(screen.getByLabelText('Highlight / tagline (optional)'));
    expect(input).toHaveValue('Unsaved wording kept through the pause');
  });

  it('allows re-sign-in and compatible resume during active durable work without discarding editors', async () => {
    const user = userEvent.setup(); const api = new Api(); let revoked = false;
    mockRequest(api, async (path, method) => {
      if (path === '/session' && method === 'POST') { revoked = false; return session; }
      if (path === '/session') return session;
      if (path === '/auth/challenge') return { csrf_token: 'reconnect-challenge', bootstrap_available: false };
      if (path === '/profile') return profile;
      if (path === '/workflows') return { items: [task({ kind: 'source_refresh', state: 'running' })] };
      if (path === '/workflows/task-1') return task({ kind: 'source_refresh', state: 'running' });
      throw new ApiError(404, 'not_found');
    }, null, [], async () => { if (revoked) throw new ApiError(401, 'session_revoked'); return installation; });
    render(<App api={api} />);
    const input = await screen.findByLabelText('Highlight / tagline (optional)');
    await waitFor(() => expect(api.writeBlock).toBeNull());
    fireEvent.change(input, { target: { value: 'Unsaved wording during durable source work' } });
    await goTo(user, 'Opportunities');
    await user.click(screen.getByRole('button', { name: 'Refresh tasks' }));
    await user.click(screen.getByRole('button', { name: 'Load saved source refresh tasks' }));
    await user.click(await screen.findByRole('button', { name: 'Open source refresh task task-1' }));
    await screen.findByRole('heading', { name: 'Source refresh task task-1' });
    revoked = true; fireEvent(document, new Event('visibilitychange'));
    await user.type(await screen.findByLabelText('Password to reconnect'), 'Fictional reconnect password 817!');
    const signIn = screen.getByRole('button', { name: 'Sign in and retain editors' });
    expect(signIn).toBeEnabled();
    await user.click(signIn);
    await screen.findByText('Review the current workspace before resuming writes.');
    expect(input).toHaveValue('Unsaved wording during durable source work');
    await user.click(screen.getByRole('button', { name: 'Check connection and compare saved workspace' }));
    const resume = await screen.findByRole('button', { name: 'Resume writes and keep all editor text' });
    expect(resume).toBeEnabled();
    expect(screen.getByRole('checkbox', { name: /I have preserved the text I need/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Discard all editors and load this saved workspace' })).toBeDisabled();
    await user.click(resume);
    await waitFor(() => expect(api.writeBlock).toBeNull());
    expect(screen.getByLabelText('Highlight / tagline (optional)')).toBe(input);
    expect(input).toHaveValue('Unsaved wording during durable source work');
    expect(screen.getByRole('heading', { name: 'Source refresh task task-1' })).toBeInTheDocument();
  });

  it('retains drafts through revoked-session sign-in and rejects version aliasing until explicit full reload', async () => {
    const user = userEvent.setup(); const api = new Api(); let status = installation; let saved = { ...profile, version: 9 };
    let revoked = false; let profileReads = 0;
    const request = mockRequest(api, async (path, method) => {
      if (path === '/session' && method === 'POST') { revoked = false; return { ...session, csrf_token: 'renewed-owner-csrf' }; }
      if (path === '/session') return session;
      if (path === '/auth/challenge') return { csrf_token: 'reconnect-challenge', bootstrap_available: false };
      if (path === '/profile') { profileReads++; return saved; }
      throw new ApiError(404, 'not_found');
    }, null, [], async () => { if (revoked) throw new ApiError(401, 'session_revoked'); return status; });
    render(<App api={api} />);
    const input = await screen.findByLabelText('Highlight / tagline (optional)');
    await waitFor(() => expect(api.writeBlock).toBeNull());
    await user.clear(input); await user.type(input, 'Private text from the previous workspace');
    const description = screen.getByLabelText('Description (optional)');
    await user.type(description, 'Private introduction from the previous workspace.');
    revoked = true; fireEvent(document, new Event('visibilitychange'));
    const password = await screen.findByLabelText('Password to reconnect');
    const readsBeforeSignIn = profileReads;
    status = { ...installation, namespace_id: 'restored-namespace', workspace_version: 1, maintenance_epoch: 2 };
    saved = { ...profile, id: 'restored-profile', version: 1, current_revision: { ...profile.current_revision, snapshot: { ...profile.current_revision.snapshot, description: 'Restored saved introduction.' } } };
    await user.type(password, 'Fictional reconnect password 817!');
    await user.click(screen.getByRole('button', { name: 'Sign in and retain editors' }));
    await screen.findByText('Review the current workspace before resuming writes.');
    expect(profileReads).toBe(readsBeforeSignIn); expect(api.csrf).toBe('renewed-owner-csrf');
    expect(input).toHaveValue('Private text from the previous workspace');
    await user.click(screen.getByRole('button', { name: 'Check connection and compare saved workspace' }));
    await screen.findByRole('region', { name: 'Saved workspace comparison' });
    expect(screen.queryByRole('button', { name: 'Resume writes and keep all editor text' })).not.toBeInTheDocument();
    expect(input).toHaveValue('Private text from the previous workspace');
    expect(description).toHaveValue('Private introduction from the previous workspace.');
    const reload = screen.getByRole('button', { name: 'Discard all editors and load this saved workspace' });
    expect(reload).toBeDisabled(); expect(api.writeBlock).toBe('workspace_reconnect_required');
    await user.click(screen.getByRole('checkbox', { name: /I have preserved the text I need/ }));
    await user.click(reload);
    await waitFor(() => expect(screen.getByLabelText('Highlight / tagline (optional)')).toHaveValue('Engineer'));
    expect(screen.getByLabelText('Highlight / tagline (optional)')).not.toBe(input);
    expect(screen.getByLabelText('Description (optional)')).toHaveValue('Restored saved introduction.');
    expect(api.writeBlock).toBeNull();
    expect(request.mock.calls.filter(call => call[1] === 'PUT')).toHaveLength(0);
  });

  it('does not bypass startup maintenance with an authentication challenge and retries only on request', async () => {
    const user = userEvent.setup(); const api = new Api(); let maintenance = true;
    const request = mockRequest(api, async path => {
      if (path === '/session') { if (maintenance) throw new ApiError(503, 'installation_maintenance'); return session; }
      if (path === '/profile') return profile;
      throw new ApiError(404, 'not_found');
    });
    render(<App api={api} />);
    await screen.findByRole('region', { name: 'Installation unavailable' });
    expect(request.mock.calls.some(call => call[0] === '/auth/challenge')).toBe(false);
    expect(screen.queryByLabelText('Highlight / tagline (optional)')).not.toBeInTheDocument();
    maintenance = false;
    await user.click(screen.getByRole('button', { name: 'Check installation again' }));
    expect(await screen.findByLabelText('Highlight / tagline (optional)')).toHaveValue('Engineer');
    await waitFor(() => expect(api.writeBlock).toBeNull());
  });
});

describe('dedicated workspace navigation', () => {
  it('starts on overview and retains unsaved identity across screen navigation without a write', async () => {
    window.history.replaceState(null, '', '/');
    const user = userEvent.setup(); const { api, request } = connectedApi();
    render(<App api={api} />);
    expect(await screen.findByRole('heading', { level: 1, name: 'Overview' })).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Save profile' })).not.toBeInTheDocument();
    await goTo(user, 'Profile');
    const headline = screen.getByLabelText('Highlight / tagline (optional)');
    await user.clear(headline); await user.type(headline, 'Retained across workspace screens');
    await goTo(user, 'Documents');
    expect(headline).not.toBeVisible();
    expect(screen.getByRole('complementary', { name: 'Retained workspace drafts' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeDisabled();
    await goTo(user, 'Profile');
    expect(screen.getByLabelText('Highlight / tagline (optional)')).toBe(headline);
    expect(headline).toHaveValue('Retained across workspace screens');
    expect(window.location.hash).toBe('#/profile');
    expect(request.mock.calls.some(call => call[1] === 'PUT' || call[1] === 'POST')).toBe(false);
  });

  it('returns to overview at the root hash while retaining the profile draft', async () => {
    window.history.replaceState(null, '', '/');
    const user = userEvent.setup(); const { api, request } = connectedApi();
    render(<App api={api} />);
    await screen.findByRole('heading', { level: 1, name: 'Overview' });
    await goTo(user, 'Profile');
    fireEvent.change(screen.getByLabelText('Highlight / tagline (optional)'), { target: { value: 'Retained through root navigation' } });
    window.history.replaceState(null, '', '/');
    fireEvent(window, new HashChangeEvent('hashchange'));
    expect(await screen.findByRole('heading', { level: 1, name: 'Overview' })).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Save profile' })).not.toBeInTheDocument();
    await goTo(user, 'Profile');
    expect(screen.getByLabelText('Highlight / tagline (optional)')).toHaveValue('Retained through root navigation');
    expect(request.mock.calls.some(call => call[1] === 'PUT' || call[1] === 'POST')).toBe(false);
  });

  it('opens imports directly by URL without exposing unrelated panels', async () => {
    window.history.replaceState(null, '', '/#/profile/imports');
    const { api } = connectedApi(); render(<App api={api} />);
    expect(await screen.findByRole('heading', { level: 1, name: 'Résumé imports' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'Upload for local extraction' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Save profile' })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Application list' })).not.toBeInTheDocument();
  });
});
