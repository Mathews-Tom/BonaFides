// SPDX-License-Identifier: MIT OR Apache-2.0
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { Documents } from './Documents';
import { Api, ApiError, type ApplicationDocuments, type DocumentRecord, type DocumentSaveRequest, type GenerationContext, type Profile, type Workflow } from './api';
import type { ApplicationSummary } from './tracking-api';

const profile: Profile = { id: 'profile', version: 3, current_revision: { id: 'profile-rev-3', revision_no: 3, content_sha256: 'a'.repeat(64), created_at: '2026-10-25T12:00:00Z', author_owner_id: 'owner', reason: 'Accepted facts', claims: [], snapshot: { display_name: 'Mira Vale', headline: '', claim_revision_ids: ['claim-1', 'claim-2'], contact: { email: 'mira@example.test', phone: '', location: '', postal_address: '' } } }, evidence: [], quality_warnings: [] };
const saved: DocumentRecord = {
  id: 'base-doc', version: 4, scope: 'base', kind: 'resume', application_id: null, reviewed: false, unsupported_claims: [], stale: false, stale_reason: null,
  artifacts: [{ id: 'old-artifact', revision_id: 'old-revision', format: 'pdf' }],
  current_revision: { id: 'doc-rev-2', revision_no: 2, content_sha256: 'b'.repeat(64), profile_revision_id: 'profile-rev-3', template_version: 'offerloom-evidence-v1', origin: 'deterministic', created_at: '2026-10-25T12:30:00Z', reason: 'Base composition', validation_issues: [],
    body: { title: 'Base résumé', sections: [{ key: 'experience', heading: 'Experience', blocks: [{ block_key: 'b-1', text: 'Maintained Linux service checks.', claim_kind: 'factual', claim_revision_ids: ['claim-1'], evidence_revision_ids: ['evidence-1'] }] }, { key: 'skills', heading: 'Skills', blocks: [{ block_key: 'b-2', text: 'Wrote SQL queries.', claim_kind: 'factual', claim_revision_ids: ['claim-2'], evidence_revision_ids: ['evidence-2'] }] }] },
    provenance_snapshot: { schema_version: 2, mode: 'deterministic_base', model_run: null, frame: { display_name: 'Mira Vale', contact: { email: 'mira@example.test' } }, facts: [{ claim_revision_id: 'claim-1', evidence_revision_id: 'evidence-1', claim_text: 'Maintained Linux service checks.', evidence_text: 'Fictional service notes.', source: 'Owner notebook', claim_sha256: 'c'.repeat(64), evidence_sha256: 'd'.repeat(64), status: 'user_asserted' }, { claim_revision_id: 'claim-2', evidence_revision_id: 'evidence-2', claim_text: 'Wrote SQL queries.', evidence_text: 'Fictional SQL work sample.', source: 'Work sample', claim_sha256: 'e'.repeat(64), evidence_sha256: 'f'.repeat(64), status: 'supported' }] },
  },
};
const application: ApplicationSummary = { id: 'application-1', version: 7, attempt_no: 1, posting_id: 'posting-1', opportunity_id: 'opportunity-1', title: 'Engineer', company: 'Fictional North', current_stage: 'preparing', archived_at: null, created_at: '2026-10-25T12:00:00Z', updated_at: '2026-10-25T12:00:00Z', next_action: null };
const tailored: DocumentRecord = { ...saved, id: 'tailored-resume', scope: 'tailored', application_id: application.id };
const route = { status: 'unqualified' as const, reason: 'generate_documents qualification has not been authorized', identity: { model: 'unqualified-local' } };
const pair: ApplicationDocuments = { application_id: application.id, version: 7, items: [tailored], generation_route: route, requirements_revision_id: 'requirements-1' };
const context: GenerationContext = { application_id: application.id, application_version: 7, profile_revision_id: 'profile-rev-3', profile_version: 3, requirements_revision_id: 'requirements-1', template_version: 'offerloom-evidence-v1', tone: 'professional', language: 'en', context_sha256: '1'.repeat(64), model_projection: { facts: [{ text: 'Maintained Linux service checks.' }], requirements: [{ text: 'Linux' }] }, frame: { display_name: 'Mira Vale', contact: { email: 'mira@example.test' } }, generation_route: route };
const workflow: Workflow = { id: 'compose-1', version: 1, state: 'pending', kind: 'document_generate', mode: 'deterministic_base', attempt: 0, max_attempts: 3, last_error_code: null, result: null };

type Handler = (path: string, method: string, body: unknown, version: number | undefined, key: boolean | string | undefined) => Promise<unknown>;
function fixture(document: DocumentRecord | null = saved, handler?: Handler) {
  const api = new Api();
  const request = vi.spyOn(api, 'request').mockImplementation(async <T,>(path: string, method = 'GET', body?: unknown, version?: number, key?: boolean | string) => {
    if (path === '/document-templates') return { items: [{ version: 'offerloom-evidence-v1', label: 'Evidence', language: 'en', paper: 'A4' }, { version: 'offerloom-compact-v1', label: 'Compact', language: 'en', paper: 'A4' }] } as T;
    if (path === '/profile/base-resume' && method === 'GET') { if (document) return document as T; throw new ApiError(404, 'base_resume_not_found'); }
    if (handler) return await handler(path, method, body, version, key) as T;
    throw new ApiError(404, 'not_found');
  });
  return { api, request };
}
async function ready() {
  const input = await screen.findByLabelText('Block text: b-1');
  await waitFor(() => expect(input).toBeEnabled());
  return input;
}

describe('saved document editing and recovery', () => {
  it('retains attempted wording on 412, explicitly compares/rebases and preserves unrelated newer server blocks', async () => {
    const user = userEvent.setup();
    const newer = { ...saved, version: 5, current_revision: { ...saved.current_revision, id: 'doc-rev-3', revision_no: 3, body: { ...saved.current_revision.body, sections: saved.current_revision.body.sections.map(section => section.key === 'skills' ? { ...section, blocks: section.blocks.map(block => ({ ...block, text: 'Newer server SQL wording.' })) } : section) } } };
    let writes = 0;
    const { api, request } = fixture(saved, async (path, method, body) => {
      if (path === '/documents/base-doc' && method === 'GET') return newer;
      if (path === '/documents/base-doc' && method === 'PUT') { writes++; if (writes === 1) throw new ApiError(412, 'version_conflict'); return { ...newer, version: 6, current_revision: { ...newer.current_revision, id: 'doc-rev-4', revision_no: 4, body: (body as DocumentSaveRequest).body } }; }
      throw new ApiError(404, 'not_found');
    });
    render(<Documents api={api} profile={profile} />); const input = await ready();
    fireEvent.change(input, { target: { value: 'My retained Linux wording.' } });
    await user.click(screen.getByRole('button', { name: 'Save document revision' }));
    expect(await screen.findByRole('alert')).toHaveFocus(); expect(input).toHaveValue('My retained Linux wording.');
    expect(screen.getByRole('button', { name: 'Save document revision' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Compare current document revision' }));
    expect(await screen.findByText('Current server document revision 3')).toBeInTheDocument();
    expect(input).toHaveValue('My retained Linux wording.');
    await user.click(screen.getByRole('checkbox', { name: /I compared both revisions/ }));
    await user.click(screen.getByRole('button', { name: 'Rebase my edits onto compared revision' }));
    expect(screen.getByLabelText('Block text: b-2')).toHaveValue('Newer server SQL wording.');
    await user.click(screen.getByRole('button', { name: 'Save document revision' }));
    await screen.findByText(/Saved document revision 4/);
    const calls = request.mock.calls.filter(call => call[1] === 'PUT');
    expect(calls).toHaveLength(2); expect(calls[1]?.[3]).toBe(5);
    expect((calls[1]?.[2] as DocumentSaveRequest).base_revision_id).toBe('doc-rev-3');
    expect((calls[1]?.[2] as DocumentSaveRequest).body.sections[1]).toEqual(newer.current_revision.body.sections[1]);
    expect((calls[1]?.[2] as DocumentSaveRequest).body.sections[0]?.blocks[0]?.claim_revision_ids).toEqual(['claim-1']);
  });

  it('keeps an offline buffer and reuses the payload-bound key only for an explicitly reconfirmed identical retry', async () => {
    const user = userEvent.setup(); const { api, request } = fixture(saved, async (path, method) => {
      if (path === '/documents/base-doc' && method === 'GET') return saved;
      if (path === '/documents/base-doc' && method === 'PUT') throw new TypeError('offline');
      throw new ApiError(404, 'not_found');
    });
    render(<Documents api={api} profile={profile} />); const input = await ready();
    fireEvent.change(input, { target: { value: 'Retained during offline save.' } });
    await user.click(screen.getByRole('button', { name: 'Save document revision' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('server could not be reached');
    expect(input).toHaveValue('Retained during offline save.');
    await user.click(screen.getByRole('button', { name: 'Compare current document revision' }));
    await screen.findByText('Current server document revision 2');
    await user.click(screen.getByRole('checkbox', { name: /I compared both revisions/ }));
    await user.click(screen.getByRole('button', { name: 'Rebase my edits onto compared revision' }));
    await user.click(screen.getByRole('button', { name: 'Save document revision' }));
    await waitFor(() => expect(request.mock.calls.filter(call => call[1] === 'PUT')).toHaveLength(2));
    const calls = request.mock.calls.filter(call => call[1] === 'PUT');
    expect(calls[0]?.[4]).toBeTypeOf('string'); expect(calls[1]?.[4]).toBe(calls[0]?.[4]);
    expect(screen.getByLabelText('Block text: b-1')).toHaveValue('Retained during offline save.');
    expect(screen.queryByText(/Saved document revision/)).not.toBeInTheDocument();
  });
  it('does not delete a newly added server block when rebasing removal of the original section content', async () => {
    const user = userEvent.setup();
    const originalSkills = saved.current_revision.body.sections[1]!;
    const added = { ...originalSkills.blocks[0]!, block_key: 'b-3', text: 'A newer independently saved block.' };
    const newer: DocumentRecord = { ...saved, version: 5, current_revision: { ...saved.current_revision, id: 'doc-rev-3', revision_no: 3, body: { ...saved.current_revision.body, sections: [saved.current_revision.body.sections[0]!, { ...originalSkills, blocks: [...originalSkills.blocks, added] }] } } };
    const { api } = fixture(saved, async (path, method) => {
      if (path === '/documents/base-doc' && method === 'PUT') throw new ApiError(412, 'version_conflict');
      if (path === '/documents/base-doc' && method === 'GET') return newer;
      throw new ApiError(404, 'not_found');
    });
    render(<Documents api={api} profile={profile} />); await ready();
    await user.click(screen.getByRole('button', { name: 'Remove block b-2' }));
    await user.click(screen.getByRole('button', { name: 'Save document revision' }));
    await screen.findByRole('alert');
    await user.click(screen.getByRole('button', { name: 'Compare current document revision' }));
    await screen.findByText('Current server document revision 3');
    await user.click(screen.getByRole('checkbox', { name: /I compared both revisions/ }));
    await user.click(screen.getByRole('button', { name: 'Rebase my edits onto compared revision' }));
    expect(screen.queryByLabelText('Block text: b-2')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Block text: b-3')).toHaveValue('A newer independently saved block.');
    expect(screen.getByLabelText('Block text: b-1')).toHaveValue('Maintained Linux service checks.');
  });

  it('does not replace a dirty editor on upstream profile updates or a newly selected benchmark result', async () => {
    const onDirtyChange = vi.fn(); const { api, request } = fixture();
    const view = render(<Documents api={api} profile={profile} onDirtyChange={onDirtyChange} />); const input = await ready();
    fireEvent.change(input, { target: { value: 'Keep this unsaved text.' } });
    const nextProfile = { ...profile, version: 4, current_revision: { ...profile.current_revision, id: 'profile-rev-4', revision_no: 4 } };
    view.rerender(<Documents api={api} profile={nextProfile} externalDocumentId="other-document" externalRequestKey="new-task" onDirtyChange={onDirtyChange} />);
    expect(input).toHaveValue('Keep this unsaved text.');
    expect(screen.getByRole('button', { name: 'Tracked application documents' })).toBeDisabled();
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(true));
    expect(request.mock.calls.some(call => call[0] === '/documents/other-document')).toBe(false);
    expect(screen.getByText('Outdated frozen inputs.')).toBeInTheDocument();
  });

  it('previews immutable history read-only and restores only after exact explicit confirmation', async () => {
    const user = userEvent.setup(); const historical = { ...saved.current_revision, id: 'historical', revision_no: 1, profile_revision_id: 'older-profile', body: { ...saved.current_revision.body, title: 'Historical frozen résumé' } };
    const { api, request } = fixture(saved, async (path, method) => {
      if (path === '/documents/base-doc/revisions?limit=20') return { items: [historical, saved.current_revision], next_cursor: null };
      if (path === '/documents/base-doc/revisions/historical') return historical;
      if (path === '/documents/base-doc/restore' && method === 'POST') return { ...saved, version: 5, current_revision: { ...historical, id: 'restored', revision_no: 3, origin: 'restore' }, reviewed: false, artifacts: [] };
      throw new ApiError(404, 'not_found');
    });
    render(<Documents api={api} profile={profile} />); await ready();
    await user.click(screen.getByRole('button', { name: 'Load document history' }));
    await user.click(await screen.findByRole('button', { name: 'Preview revision 1 — deterministic' }));
    expect(await screen.findByRole('heading', { name: 'Historical revision 1 — read-only' })).toBeInTheDocument();
    expect(screen.queryByLabelText('Block text: b-1')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Render reviewed PDF' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Restore historical revision' })).toBeDisabled();
    expect(request.mock.calls.some(call => call[1] === 'POST')).toBe(false);
    await user.click(screen.getByRole('checkbox', { name: /Restore this historical revision/ }));
    await user.click(screen.getByRole('button', { name: 'Restore historical revision' }));
    expect(await screen.findByLabelText('Document title')).toHaveValue('Historical frozen résumé');
    expect(screen.getByRole('button', { name: 'Render reviewed PDF' })).toBeDisabled();
    const call = request.mock.calls.find(item => item[0] === '/documents/base-doc/restore');
    expect(call?.[2]).toEqual({ revision_id: 'historical', reason: 'Owner explicitly restored historical document revision' }); expect(call?.[3]).toBe(4);
  });
});

describe('review, export and generation boundaries', () => {
  it('requires every factual source-block review and exposes only an actual artifact for the selected saved revision', async () => {
    const user = userEvent.setup(); let document = saved;
    const { api, request } = fixture(saved, async (path, method) => {
      if (path === '/documents/base-doc/review' && method === 'POST') { document = { ...document, version: 5, reviewed: true }; return document; }
      if (path === '/documents/base-doc/renders') return { ...workflow, id: 'render-1', kind: 'pdf_render' };
      if (path === '/workflows/render-1') { document = { ...document, artifacts: [...document.artifacts, { id: 'real-pdf', revision_id: document.current_revision.id, format: 'pdf' }] }; return { ...workflow, id: 'render-1', state: 'succeeded', result: { document_id: document.id, artifact_id: 'real-pdf' } }; }
      if (path === '/documents/base-doc') return document;
      throw new ApiError(404, 'not_found');
    });
    render(<Documents api={api} profile={profile} />); await ready();
    expect(screen.queryByRole('link', { name: /Download rendered PDF/ })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Accept this exact revision' })).toBeDisabled();
    await user.click(screen.getByRole('checkbox', { name: /I checked block b-1/ }));
    expect(screen.getByRole('button', { name: 'Accept this exact revision' })).toBeDisabled();
    await user.click(screen.getByRole('checkbox', { name: /I checked block b-2/ }));
    await user.click(screen.getByRole('button', { name: 'Accept this exact revision' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Render reviewed PDF' })).toBeEnabled());
    const review = request.mock.calls.find(call => call[0] === '/documents/base-doc/review');
    expect(review?.[2]).toEqual({ revision_id: 'doc-rev-2', content_sha256: 'b'.repeat(64), decision: 'accepted', reviewed_block_keys: ['b-1', 'b-2'] });
    await user.click(screen.getByRole('button', { name: 'Render reviewed PDF' }));
    expect(screen.queryByRole('link', { name: /Download rendered PDF/ })).not.toBeInTheDocument();
    expect(await screen.findByRole('link', { name: 'Download rendered PDF for revision 2' }, { timeout: 4000 })).toHaveAttribute('href', '/api/v1/artifacts/real-pdf');
    expect(request.mock.calls.some(call => /stage|send|generation/.test(call[0]))).toBe(false);
  });

  it('keeps unsupported manual text editable but blocks review/export even if a server review flag is true', async () => {
    const user = userEvent.setup(); const blocked = { ...saved, reviewed: true, unsupported_claims: ['/sections/0/blocks/0'], current_revision: { ...saved.current_revision, validation_issues: [{ path: '/sections/0/blocks/0', code: 'unestablished_factual_text' }] } };
    const { api, request } = fixture(blocked, async (path, method, body) => {
      if (path === '/documents/base-doc' && method === 'PUT') return { ...blocked, version: 5, reviewed: false, current_revision: { ...blocked.current_revision, id: 'manual', revision_no: 3, body: (body as DocumentSaveRequest).body } };
      throw new ApiError(404, 'not_found');
    });
    render(<Documents api={api} profile={profile} />); const input = await ready();
    expect(screen.getByRole('button', { name: 'Exact revision reviewed' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Render reviewed PDF' })).toBeDisabled();
    fireEvent.change(input, { target: { value: 'Unsupported wording remains inspectable.' } });
    await user.click(screen.getByRole('button', { name: 'Save document revision' }));
    await screen.findByText(/Saved document revision 3/);
    expect(screen.getByLabelText('Block text: b-1')).toHaveValue('Unsupported wording remains inspectable.');
    expect(screen.getByRole('button', { name: 'Render reviewed PDF' })).toBeDisabled();
    expect(request.mock.calls.filter(call => call[1] === 'POST')).toHaveLength(0);
  });

  it('creates a profile-only base with the chosen template and shows durable cancellation without inventing a document', async () => {
    const user = userEvent.setup(); const { api, request } = fixture(null, async (path, method) => {
      if (path === '/profile/base-resume' && method === 'POST') return workflow;
      if (path === '/workflows/compose-1/cancel') return { ...workflow, version: 2, state: 'cancelled' };
      if (path === '/workflows/compose-1') return workflow;
      throw new ApiError(404, 'not_found');
    });
    render(<Documents api={api} profile={profile} />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Create base résumé' })).toBeEnabled());
    await user.selectOptions(screen.getByLabelText('Built-in presentation'), 'offerloom-compact-v1');
    await user.click(screen.getByRole('button', { name: 'Create base résumé' }));
    expect(await screen.findByText('Document task: pending')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Cover letter' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Tracked application')).not.toBeInTheDocument();
    const call = request.mock.calls.find(item => item[1] === 'POST');
    expect(call?.[2]).toEqual({ profile_revision_id: 'profile-rev-3', template_version: 'offerloom-compact-v1' }); expect(call?.[3]).toBe(3);
    await user.click(screen.getByRole('button', { name: 'Cancel document task' }));
    expect(await screen.findByText('Document task: cancelled')).toBeInTheDocument();
    expect(screen.queryByLabelText('Block text: b-1')).not.toBeInTheDocument();
    expect(request.mock.calls.some(item => item[0].startsWith('/applications') || item[0].includes('generation-context'))).toBe(false);
  });

  it('independently loads an incomplete tracked pair, displays the exact minimized context and cannot run an unqualified route', async () => {
    const user = userEvent.setup(); const { api, request } = fixture(saved, async path => {
      if (path === '/applications') return { items: [application], next_cursor: null, query_elapsed_ms: 1, as_of: application.updated_at };
      if (path === '/applications/application-1/documents') return pair;
      if (path.startsWith('/applications/application-1/generation-context?')) return context;
      throw new ApiError(404, 'not_found');
    });
    render(<Documents api={api} profile={profile} />); await ready();
    await user.click(screen.getByRole('button', { name: 'Tracked application documents' }));
    await user.click(screen.getByRole('button', { name: 'Reload tracked applications' }));
    await user.selectOptions(await screen.findByLabelText('Tracked application'), 'application-1');
    expect(await screen.findByRole('button', { name: 'Cover letter unavailable' })).toBeDisabled();
    expect(screen.getByLabelText('Block text: b-1')).toHaveValue('Maintained Linux service checks.');
    await user.click(screen.getByRole('button', { name: 'Preview reviewed generation context' }));
    expect(await screen.findByRole('heading', { name: 'Exact minimized model projection' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Generate résumé and cover letter pair' })).toBeDisabled();
    expect(screen.getByRole('checkbox', { name: /I reviewed this exact projection/ })).toBeDisabled();
    const projection = screen.getByRole('heading', { name: 'Exact minimized model projection' }).parentElement;
    expect(projection).not.toBeNull();
    const firstPre = projection!.querySelector('pre'); expect(JSON.parse(firstPre!.textContent!)).toEqual(context.model_projection);
    expect(firstPre).not.toHaveTextContent('mira@example.test');
    expect(within(projection!).getByText(/Only this model projection/)).toHaveTextContent('not anonymization');
    expect(request.mock.calls.filter(call => call[1] === 'POST')).toHaveLength(0);
  });
  it.each([false, true])('keeps the selected application document on task reconnect after an earlier result read failed (dirty: %s)', async keepDirty => {
    const onDirtyChange = vi.fn();
    const selected: DocumentRecord = { ...tailored, current_revision: { ...tailored.current_revision, id: 'selected-application-revision', body: { ...tailored.current_revision.body, title: 'Current application résumé' } } };
    const generated: DocumentRecord = { ...saved, version: 5, current_revision: { ...saved.current_revision, id: 'generated-base-revision', revision_no: 3, body: { ...saved.current_revision.body, title: 'Earlier completed base result' } } };
    let resultReads = 0;
    const { api, request } = fixture(saved, async (path, method) => {
      if (path === '/profile/base-resume' && method === 'POST') return workflow;
      if (path === '/workflows/compose-1') return { ...workflow, version: 2, state: 'succeeded', result: { document_id: generated.id, revision_id: generated.current_revision.id } };
      if (path === '/documents/base-doc') {
        resultReads++;
        if (resultReads === 1) throw new TypeError('offline result read');
        return generated;
      }
      if (path === '/applications') return { items: [application], next_cursor: null, query_elapsed_ms: 1, as_of: application.updated_at };
      if (path === '/applications/application-1/documents') return { ...pair, items: [selected] };
      throw new ApiError(404, 'not_found');
    });
    render(<Documents api={api} profile={profile} onDirtyChange={onDirtyChange} />); await ready();
    vi.useFakeTimers();
    try {
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Rebuild base from selected profile' })); });
      await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
      expect(screen.getByRole('heading', { name: 'Document task: succeeded' })).toBeVisible();
      expect(screen.getByText(/Task status\/result could not be loaded/)).toBeVisible();
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Tracked application documents' })); });
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Reload tracked applications' })); });
      await act(async () => { fireEvent.change(screen.getByLabelText('Tracked application'), { target: { value: application.id } }); });
      expect(screen.getByLabelText('Document title')).toHaveValue('Current application résumé');
      const expectedText = keepDirty ? 'Keep these unsaved application edits.' : 'Maintained Linux service checks.';
      if (keepDirty) await act(async () => { fireEvent.change(screen.getByLabelText('Block text: b-1'), { target: { value: expectedText } }); });
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Reconnect document task' })); });
      await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
      expect(request.mock.calls.filter(call => call[0] === '/documents/base-doc')).toHaveLength(2);
      expect(screen.queryByRole('button', { name: 'Reconnect document task' })).not.toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Tracked application documents' })).toHaveAttribute('aria-pressed', 'true');
      expect(screen.getByRole('button', { name: 'Base résumé' })).toHaveAttribute('aria-pressed', 'false');
      expect(screen.getByLabelText('Tracked application')).toHaveValue(application.id);
      expect(screen.getByRole('heading', { name: 'Application résumé · saved revision 2' })).toBeVisible();
      expect(screen.getByRole('button', { name: 'Résumé' })).toHaveAttribute('aria-pressed', 'true');
      expect(screen.getByLabelText('Document title')).toHaveValue('Current application résumé');
      expect(screen.getByLabelText('Block text: b-1')).toHaveValue(expectedText);
      expect(onDirtyChange).toHaveBeenLastCalledWith(keepDirty);
      if (keepDirty) {
        expect(screen.getByRole('button', { name: 'Save document revision' })).toBeEnabled();
        expect(screen.getByRole('button', { name: 'Discard document changes' })).toBeEnabled();
        expect(screen.getByRole('button', { name: 'Tracked application documents' })).toBeDisabled();
      } else {
        expect(screen.getByRole('button', { name: 'Save document revision' })).toBeDisabled();
        expect(screen.queryByRole('button', { name: 'Discard document changes' })).not.toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Tracked application documents' })).toBeEnabled();
      }
    } finally {
      vi.useRealTimers();
    }
  });

  it('does not replace saved documents when a completed task returns only half the requested pair', async () => {
    const user = userEvent.setup(); const onBusyChange = vi.fn();
    const qualified = { status: 'qualified' as const, reason: 'Synthetic fixture route only', identity: { qualification_scope: 'synthetic_conformance' } };
    const { api, request } = fixture(saved, async (path, method) => {
      if (path === '/applications') return { items: [application], next_cursor: null, query_elapsed_ms: 1, as_of: application.updated_at };
      if (path === '/applications/application-1/documents') return { ...pair, generation_route: qualified };
      if (path.startsWith('/applications/application-1/generation-context?')) return { ...context, generation_route: qualified };
      if (path === '/applications/application-1/generation' && method === 'POST') return { ...workflow, mode: 'tailored_ai' };
      if (path === '/workflows/compose-1') return { ...workflow, state: 'succeeded', result: { application_id: 'application-1', documents: [{ kind: 'resume', document_id: 'new-half', revision_id: 'new-half-revision' }], model_run_id: 'synthetic-run' } };
      throw new ApiError(404, 'not_found');
    });
    render(<Documents api={api} profile={profile} onBusyChange={onBusyChange} />); await ready();
    await user.click(screen.getByRole('button', { name: 'Tracked application documents' }));
    await user.click(screen.getByRole('button', { name: 'Reload tracked applications' }));
    await user.selectOptions(await screen.findByLabelText('Tracked application'), 'application-1');
    await screen.findByRole('button', { name: 'Cover letter unavailable' });
    await user.click(screen.getByRole('button', { name: 'Preview reviewed generation context' }));
    await user.click(await screen.findByRole('checkbox', { name: /I reviewed this exact projection/ }));
    await user.click(screen.getByRole('button', { name: 'Generate résumé and cover letter pair' }));
    expect(await screen.findByText(/The task returned an incomplete pair/, {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.getByLabelText('Block text: b-1')).toHaveValue('Maintained Linux service checks.');
    expect(request.mock.calls.some(call => call[0] === '/documents/new-half')).toBe(false);
    await waitFor(() => expect(onBusyChange).toHaveBeenLastCalledWith(false));
    const command = request.mock.calls.find(call => call[0] === '/applications/application-1/generation');
    expect(command?.[2]).toEqual({ profile_revision_id: context.profile_revision_id, requirements_revision_id: context.requirements_revision_id, template_version: context.template_version, tone: 'professional', language: 'en', context_sha256: context.context_sha256 });
    expect(command?.[3]).toBe(7); expect(command?.[4]).toBeTypeOf('string');
  });
});
