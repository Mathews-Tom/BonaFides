// SPDX-License-Identifier: MIT OR Apache-2.0
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { App } from './App';
import { Api, ApiError, type DocumentRecord, type Profile, type RequirementsView, type Session, type Snapshot, type Workflow } from './api';
import { stages } from './tracking-api';
const session: Session = { owner_id: 'owner', login_name: 'mira', csrf_token: 'session-csrf', expires_at: '2030-01-01' };
const profile: Profile = { id: 'profile', version: 1, current_revision: { id: 'revision-1', revision_no: 1, content_sha256: 'a'.repeat(64), created_at: '2026-01-01T00:00:00Z', author_owner_id: 'owner', reason: 'Initial profile', claims: [], snapshot: { display_name: 'Mira', headline: 'Engineer', claim_revision_ids: [] } }, evidence: [], quality_warnings: [] };
afterEach(() => { window.history.replaceState(null, '', '/'); });
function mockRequest(api: Api, handler: (path: string, method: string, body?: unknown) => Promise<unknown>, baseDocument: DocumentRecord | null = null) {
  return vi.spyOn(api, 'request').mockImplementation(async <T,>(path: string, method = 'GET', body?: unknown) => {
    if (method === 'GET' && path === '/document-templates') return { items: [{ version: 'offerloom-evidence-v1', label: 'Evidence', language: 'en', paper: 'A4' }, { version: 'offerloom-compact-v1', label: 'Compact', language: 'en', paper: 'A4' }] } as T;
    if (method === 'GET' && path === '/profile/base-resume') { if (baseDocument) return baseDocument as T; throw new ApiError(404, 'base_resume_not_found'); }
    if (method === 'GET' && path === '/applications') return { items: [], next_cursor: null, query_elapsed_ms: 1, as_of: '2026-10-25T12:00:00Z' } as T;
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
describe('printable profile contact', () => {
  it('saves optional contact with the ordinary immutable profile command and retains claim/evidence pins', async () => {
    const user = userEvent.setup(); const api = new Api(); let current = profile;
    const request = mockRequest(api, async (path, method, body) => {
      if (path === '/session') return session;
      if (path === '/profile' && method === 'GET') return current;
      if (path === '/profile' && method === 'PUT') {
        current = { ...current, version: 2, current_revision: { ...current.current_revision, id: 'revision-2', revision_no: 2, snapshot: body as Snapshot } };
        return current;
      }
      throw new ApiError(404, 'not_found');
    });
    render(<App api={api} />);
    await user.type(await screen.findByLabelText('Printable email (optional)'), 'mira@example.test');
    await user.type(screen.getByLabelText('Printable phone (optional)'), '+1 555 0100');
    await user.type(screen.getByLabelText('Printable location (optional)'), 'Fictional North');
    await user.type(screen.getByLabelText('Printable postal address (optional)'), '12 Example Street');
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Save profile' }));
    await screen.findByText('Saved as revision 2.');
    expect(screen.getByLabelText('Printable email (optional)')).toHaveValue('mira@example.test');
    const command = request.mock.calls.find(call => call[0] === '/profile' && call[1] === 'PUT');
    expect(command?.[2]).toEqual({ ...profile.current_revision.snapshot, contact: { email: 'mira@example.test', phone: '+1 555 0100', location: 'Fictional North', postal_address: '12 Example Street' } });
    expect(command?.[3]).toBe(1); expect(command?.[4]).toBeTypeOf('string');
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
    let document: DocumentRecord = { id: 'document-1', version: 4, kind: 'resume', scope: 'base', application_id: null, artifacts: [], current_revision: { id: 'document-revision', revision_no: 1, profile_revision_id: 'revision-1', template_version: 'offerloom-base-v1', origin: 'deterministic', created_at: '2026-10-25T12:00:00Z', validation_issues: [], provenance_snapshot: { mode: 'deterministic_base', model_run: null, facts: [{ claim_revision_id: 'claim-1', evidence_revision_id: 'evidence-1', claim_text: 'Maintained Linux service checks.', evidence_text: 'Synthetic service notes', source: 'SYN-CORE', status: 'supported', claim_sha256: 'a'.repeat(64), evidence_sha256: 'b'.repeat(64) }] }, content_sha256: 'exact-hash', body: { title: 'Mira dossier', sections: [{ key: 'facts', heading: 'Supported facts', blocks: [{ block_key: 'linux', text: 'Maintained Linux service checks.', claim_kind: 'factual', claim_revision_ids: ['claim-1'], evidence_revision_ids: ['evidence-1'] }] }] } }, reviewed: false, unsupported_claims: [], stale: false, stale_reason: null };
    mockRequest(api, async (path, method) => {
      if (path === '/session') return session;
      if (path === '/profile') return evidenceProfile;
      if (path === '/benchmarks/tasks') return { id: 'task-1', version: 1, state: 'pending', attempt: 0, result: null, last_error_code: null };
      if (path === '/workflows/task-1') return { id: 'task-1', version: 2, state: 'succeeded', attempt: 1, result: { document_id: 'document-1' }, last_error_code: null };
      if (path === '/documents/document-1') return document;
      if (path === '/documents/document-1/review' && method === 'POST') { document = { ...document, reviewed: true, version: 5 }; return document; }
      if (path === '/documents/document-1/renders') return { id: 'render-1', version: 1, state: 'pending', attempt: 0, result: null, last_error_code: null };
      if (path === '/workflows/render-1') { document = { ...document, artifacts: [{ id: 'pdf-1', revision_id: 'document-revision', format: 'pdf' }] }; return { id: 'render-1', version: 2, state: 'succeeded', attempt: 1, result: { document_id: 'document-1', artifact_id: 'pdf-1' }, last_error_code: null }; }
      throw new ApiError(404, 'not_found');
    });
    render(<App api={api} />);
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
    const document: DocumentRecord = { id: 'base', version: 1, scope: 'base', kind: 'resume', application_id: null, reviewed: false, unsupported_claims: [], stale: false, stale_reason: null, artifacts: [], current_revision: { id: 'saved-document-revision', revision_no: 1, profile_revision_id: profile.current_revision.id, template_version: 'offerloom-evidence-v1', origin: 'deterministic', created_at: '2026-10-25T12:00:00Z', content_sha256: 'a'.repeat(64), provenance_snapshot: { mode: 'deterministic_base', facts: [] }, validation_issues: [], body: { title: 'Base résumé', sections: [{ key: 'facts', heading: 'Experience', blocks: [{ block_key: 'b-1', text: 'Fictional accepted source text.', claim_kind: 'factual', claim_revision_ids: ['claim-1'], evidence_revision_ids: [] }] }] } } };
    mockRequest(api, async path => { if (path === '/session') return session; if (path === '/profile') return profile; throw new ApiError(404, 'not_found'); }, document);
    render(<App api={api} />); const input = await screen.findByLabelText('Block text: b-1');
    await waitFor(() => expect(input).toBeEnabled());
    fireEvent.change(input, { target: { value: 'Private unsaved document text.' } });
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Load SYN-CORE benchmark evidence' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Save profile' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Tracked application documents' })).toBeDisabled();
    await user.selectOptions(screen.getByLabelText('Theme'), 'dark');
    expect(input).toHaveValue('Private unsaved document text.');
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
    workflow: { id: 'task-one', version: 1, state: 'running', attempt: 1, max_attempts: 2, kind: 'posting_extract', result: { posting_id: 'posting-one', source_sha256: 'a'.repeat(64) }, last_error_code: null } as Workflow,
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
    fireEvent.click(await screen.findByRole('button', { name: 'Read Engineer at Synthetic North' }));
    await screen.findByText('Current snapshot');
    fireEvent.click(screen.getByRole('button', { name: 'Review evidence coverage manually' }));
    fireEvent.change(screen.getByLabelText('Explanation of evidence and gaps'), { target: { value: 'Do not lose my ongoing coverage review.' } });
    fireEvent.change(screen.getByLabelText('Professional headline'), { target: { value: 'Updated identity' } });
    expect(screen.getByRole('button', { name: 'Save profile' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: 'Save profile' }));
    expect(await screen.findByText('Stale historical snapshot — not a current eligibility assertion')).toBeVisible();
    expect(screen.getByLabelText('Explanation of evidence and gaps')).toHaveValue('Do not lose my ongoing coverage review.');
    expect(screen.getByRole('button', { name: 'Save owner-reviewed assessment' })).toBeDisabled();
    expect(await screen.findByRole('region', { name: 'Current profile comparison' })).toHaveTextContent('revision-2');
    expect(screen.getByLabelText('Professional headline')).toBeEnabled();
    expect(state.reviewReads).toBe(1);
    expect(state.review.latest_assessment?.stale).toBe(false);
  });

  it('keeps manual profile editing available during durable model work while updating visible assessment freshness', async () => {
    const { api, state } = connectedSuitabilityApi();
    render(<App api={api} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Read Engineer at Synthetic North' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Request extraction proposal' }));
    await waitFor(() => expect(screen.getByRole('status', { name: 'Suitability task status' })).toHaveTextContent('Task running.'));
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeDisabled();
    expect(screen.getByLabelText('Professional headline')).toBeEnabled();
    fireEvent.change(screen.getByLabelText('Professional headline'), { target: { value: 'Updated identity' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save profile' }));
    expect(await screen.findByText('Stale historical snapshot — not a current eligibility assertion')).toBeVisible();
    expect(screen.getByRole('region', { name: 'Current suitability task' })).toHaveTextContent('task-one');
    expect(state.workflow.state).toBe('running');
    expect(screen.getByLabelText('Professional headline')).toBeEnabled();
  });

  it('preserves unsaved identity text when an explicit citation reload observes a newer profile', async () => {
    const { api, state } = connectedSuitabilityApi();
    render(<App api={api} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Read Engineer at Synthetic North' }));
    await screen.findByText('Current snapshot');
    fireEvent.change(screen.getByLabelText('Professional headline'), { target: { value: 'My unsaved identity wording.' } });
    state.profile = { ...state.profile, version: 2, current_revision: { ...state.profile.current_revision, id: 'revision-2', revision_no: 2 } };
    fireEvent.click(screen.getByRole('button', { name: 'Reload profile citations' }));
    expect(await screen.findByText('Current server revision 2')).toBeVisible();
    expect(screen.getByLabelText('Professional headline')).toHaveValue('My unsaved identity wording.');
    expect(screen.getByText('Stale historical snapshot — not a current eligibility assertion')).toBeVisible();
  });
});
