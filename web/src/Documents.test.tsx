// SPDX-License-Identifier: MIT OR Apache-2.0
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent, { type UserEvent } from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { Documents } from './Documents';
import { Api, ApiError, type ApplicationDocuments, type DocumentDefaults, type DocumentDefaultsPatch, type DocumentRecord, type DocumentSaveRequest, type GenerationContext, type Profile, type SectionProposal, type SectionRegenerationContext, type Workflow } from './api';
import type { ApplicationSummary } from './tracking-api';

const profile: Profile = { id: 'profile', version: 3, current_revision: { id: 'profile-rev-3', revision_no: 3, content_sha256: 'a'.repeat(64), created_at: '2026-10-25T12:00:00Z', author_owner_id: 'owner', reason: 'Accepted facts', claims: [], snapshot: { display_name: 'Mira Vale', headline: '', claim_revision_ids: ['claim-1', 'claim-2'], contact: { email: 'mira@example.test', phone: '', location: '', postal_address: '' } } }, evidence: [], quality_warnings: [], experience_coverage: { minimum_days: 0, maximum_days: 0, undated_or_open_entries: 0 } };
const saved: DocumentRecord = {
  id: 'base-doc', version: 4, scope: 'base', kind: 'resume', application_id: null, reviewed: false, unsupported_claims: [], stale: false, stale_reason: null,
  artifacts: [{ id: 'old-artifact', revision_id: 'old-revision', format: 'pdf' }],
  current_revision: { id: 'doc-rev-2', revision_no: 2, content_sha256: 'b'.repeat(64), profile_revision_id: 'profile-rev-3', template_version: 'bonafides-evidence-v1', origin: 'deterministic', created_at: '2026-10-25T12:30:00Z', reason: 'Base composition', validation_issues: [],
    body: { title: 'Base résumé', sections: [{ key: 'experience', heading: 'Experience', blocks: [{ block_key: 'b-1', text: 'Maintained Linux service checks.', claim_kind: 'factual', claim_revision_ids: ['claim-1'], evidence_revision_ids: ['evidence-1'] }] }, { key: 'skills', heading: 'Skills', blocks: [{ block_key: 'b-2', text: 'Wrote SQL queries.', claim_kind: 'factual', claim_revision_ids: ['claim-2'], evidence_revision_ids: ['evidence-2'] }] }] },
    provenance_snapshot: { schema_version: 2, mode: 'deterministic_base', model_run: null, frame: { display_name: 'Mira Vale', contact: { email: 'mira@example.test' } }, facts: [{ claim_revision_id: 'claim-1', evidence_revision_id: 'evidence-1', claim_text: 'Maintained Linux service checks.', evidence_text: 'Fictional service notes.', source: 'Owner notebook', claim_sha256: 'c'.repeat(64), evidence_sha256: 'd'.repeat(64), status: 'user_asserted' }, { claim_revision_id: 'claim-2', evidence_revision_id: 'evidence-2', claim_text: 'Wrote SQL queries.', evidence_text: 'Fictional SQL work sample.', source: 'Work sample', claim_sha256: 'e'.repeat(64), evidence_sha256: 'f'.repeat(64), status: 'supported' }] },
  },
};
const application: ApplicationSummary = { id: 'application-1', version: 7, attempt_no: 1, posting_id: 'posting-1', opportunity_id: 'opportunity-1', title: 'Engineer', company: 'Fictional North', current_stage: 'preparing', archived_at: null, created_at: '2026-10-25T12:00:00Z', updated_at: '2026-10-25T12:00:00Z', next_action: null };
const tailored: DocumentRecord = { ...saved, id: 'tailored-resume', scope: 'tailored', application_id: application.id };
const route = { status: 'unqualified' as const, reason: 'generate_documents qualification has not been authorized', identity: { model: 'unqualified-local' } };
const pair: ApplicationDocuments = { application_id: application.id, version: 7, items: [tailored], generation_route: route, requirements_revision_id: 'requirements-1' };
const context: GenerationContext = { application_id: application.id, application_version: 7, profile_revision_id: 'profile-rev-3', profile_version: 3, requirements_revision_id: 'requirements-1', template_version: 'bonafides-evidence-v1', tone: 'professional', language: 'en', context_sha256: '1'.repeat(64), model_projection: { facts: [{ text: 'Maintained Linux service checks.' }], requirements: [{ text: 'Linux' }] }, frame: { display_name: 'Mira Vale', contact: { email: 'mira@example.test' } }, generation_route: route, withheld_evidence: [] };
const workflow: Workflow = {
  id: 'compose-1', version: 1, state: 'pending', kind: 'document_generate', mode: 'deterministic_base', attempt: 0, max_attempts: 3, last_error_code: null, result: null,
  created_at: '2026-10-25T12:00:00Z', updated_at: '2026-10-25T12:00:00Z', deadline_at: '2030-01-01T00:00:00Z', next_attempt_at: null,
  cancel_requested_at: null, lease_expires_at: null, last_heartbeat_at: null, queue_age_seconds: 0, remaining_attempts: 3,
  cancelable: true, retryable: false, retry_block_reason: 'state_not_retryable', delivery_state: 'not_attempted',
  cancellation: { requested: false, local_fence_settled: false, provider_stopped: null, completed_effects_preserved: true, reason: null },
  usage: { status: 'not_applicable', model_runs: 0, inference_post_attempts: 0, inference_post_limit: null, input_tokens: null, output_tokens: null, token_budget: 0, tokens_used: 0, cost_budget_microunits: 0, cost_used_microunits: null, currency: 'USD' },
};
const remembered: DocumentDefaults = { version: 8, template_version: 'bonafides-evidence-v1', tone: 'professional', language: 'en', revision_retention: 'until_owner_erasure', task_input_retention_seconds: 86400, temporary_export_retention_seconds: 86400, extension_supported: false };

type Handler = (path: string, method: string, body: unknown, version: number | undefined, key: boolean | string | undefined) => Promise<unknown>;
function fixture(document: DocumentRecord | null = saved, handler?: Handler, defaultsHandler?: Handler) {
  const api = new Api();
  const request = vi.spyOn(api, 'request').mockImplementation(async <T,>(path: string, method = 'GET', body?: unknown, version?: number, key?: boolean | string) => {
    if (path === '/document-templates') return { items: [{ version: 'bonafides-evidence-v1', label: 'Evidence', language: 'en', paper: 'A4' }, { version: 'bonafides-compact-v1', label: 'Compact', language: 'en', paper: 'A4' }, { version: 'bonafides-evidence-letter-v1', label: 'Evidence', language: 'en', paper: 'Letter' }, { version: 'bonafides-compact-letter-v1', label: 'Compact', language: 'en', paper: 'Letter' }] } as T;
    if (path === '/document-defaults') {
      if (defaultsHandler) return await defaultsHandler(path, method, body, version, key) as T;
      if (method === 'GET') return remembered as T;
    }
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

describe('remembered document defaults and current operation isolation', () => {
  it('persists only the edited presentation while retaining a dirty saved document and current choices, then uses it in a new workspace', async () => {
    const user = userEvent.setup(); let stored: DocumentDefaults = { ...remembered, template_version: 'bonafides-evidence-letter-v1', tone: 'concise' };
    const { api, request } = fixture(saved, async (path, method) => {
      if (path === '/applications') return { items: [application], next_cursor: null };
      if (path === '/applications/application-1/documents') return pair;
      if (path.startsWith('/applications/application-1/generation-context?')) {
        const query = new URL(path, 'https://bonafides.test').searchParams;
        return { ...context, template_version: query.get('template_version'), tone: query.get('tone'), generation_route: { ...route, status: 'qualified', identity: { endpoint: 'http://local-model.test:11434/api/chat', model: 'default-consumer-fixture' } } };
      }
      if (path === '/applications/application-1/generation' && method === 'POST') return { ...workflow, mode: 'tailored_ai' };
      throw new ApiError(404, 'not_found');
    }, async (_path, method, body, version) => {
      if (method === 'PATCH') {
        expect(version).toBe(stored.version);
        stored = { ...stored, ...body as DocumentDefaultsPatch, version: stored.version + 1 };
      }
      return stored;
    });
    const view = render(<Documents api={api} profile={profile} />); const input = await ready();
    const defaultsDisclosure = screen.getByText('Remembered defaults and retention').closest('details');
    expect(defaultsDisclosure).not.toHaveAttribute('open');
    expect(screen.getByLabelText('Document state')).toHaveTextContent('Saved · review required');
    expect(screen.getByRole('button', { name: 'Accept this exact revision' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Render reviewed PDF' })).toBeDisabled();
    await user.click(screen.getByText('Remembered defaults and retention'));
    expect(screen.getByLabelText('Built-in presentation')).toHaveValue('bonafides-evidence-letter-v1');
    expect(screen.getByLabelText('Remembered writing tone')).toHaveValue('concise');
    expect(within(screen.getByLabelText('Built-in presentation')).getByRole('option', { name: 'Evidence · English · Letter' })).toBeInTheDocument();
    fireEvent.change(input, { target: { value: 'Dirty words retained while defaults change.' } });
    await user.selectOptions(screen.getByLabelText('Remembered built-in presentation'), 'bonafides-compact-letter-v1');
    await user.click(screen.getByRole('button', { name: 'Save remembered defaults' }));
    await screen.findByText(/Remembered defaults saved for future work/);
    const patch = request.mock.calls.find(call => call[1] === 'PATCH');
    expect(patch?.[2]).toEqual({ template_version: 'bonafides-compact-letter-v1' }); expect(patch?.[4]).toBeTypeOf('string');
    expect(stored.tone).toBe('concise'); expect(stored.revision_retention).toBe('until_owner_erasure');
    expect(input).toHaveValue('Dirty words retained while defaults change.');
    expect(screen.getByLabelText('Built-in presentation')).toHaveValue('bonafides-evidence-letter-v1');
    expect(request.mock.calls.some(call => call[1] === 'PUT' || call[0].startsWith('/documents/'))).toBe(false);
    view.unmount(); render(<Documents api={api} profile={profile} />); await ready();
    await user.click(screen.getByText('Remembered defaults and retention'));
    expect(screen.getByLabelText('Built-in presentation')).toHaveValue('bonafides-compact-letter-v1');
    expect(screen.getByLabelText('Block text: b-1')).toHaveValue(saved.current_revision.body.sections[0]!.blocks[0]!.text);
    await openTailored(user);
    expect(screen.getByLabelText('Writing tone')).toHaveValue('concise');
    expect(screen.getByLabelText('Section writing tone')).toHaveValue('concise');
    await user.click(screen.getByRole('button', { name: 'Preview reviewed generation context' }));
    const confirm = await screen.findByRole('checkbox', { name: /I reviewed this exact projection/ });
    expect(screen.getByRole('button', { name: 'Generate résumé and cover letter pair' })).toBeDisabled();
    expect(screen.getByText('http://local-model.test:11434/api/chat', { selector: 'code' })).toBeVisible();
    await user.click(confirm);
    await user.click(screen.getByRole('button', { name: 'Generate résumé and cover letter pair' }));
    await screen.findByRole('heading', { name: 'Document task: pending' });
    expect(request.mock.calls.find(call => call[0] === '/applications/application-1/generation' && call[1] === 'POST')?.[2]).toEqual({ profile_revision_id: profile.current_revision.id, requirements_revision_id: context.requirements_revision_id, template_version: 'bonafides-compact-letter-v1', tone: 'concise', language: 'en', context_sha256: context.context_sha256 });
  });

  it('keeps defaults loading failures visible and retries without replacing explicit choices or dirty text', async () => {
    const user = userEvent.setup(); let reads = 0;
    const { api, request } = fixture(saved, undefined, async () => {
      if (++reads === 1) throw new TypeError('offline');
      return { ...remembered, template_version: 'bonafides-evidence-letter-v1', tone: 'concise' };
    });
    render(<Documents api={api} profile={profile} />); const input = await ready();
    expect(await screen.findByText(/Remembered defaults could not be loaded/)).toBeVisible();
    expect(screen.getByText('Remembered defaults and retention').closest('details')).not.toHaveAttribute('open');
    await user.click(screen.getByText('Remembered defaults and retention'));
    expect(screen.getByLabelText('Built-in presentation')).toHaveValue('');
    expect(screen.queryByRole('button', { name: 'Save remembered defaults' })).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText('Built-in presentation'), 'bonafides-compact-v1');
    fireEvent.change(input, { target: { value: 'Retained while settings recover.' } });
    await user.click(screen.getByRole('button', { name: 'Reload remembered defaults' }));
    await waitFor(() => expect(screen.getByLabelText('Remembered writing tone')).toHaveValue('concise'));
    expect(screen.queryByText(/Remembered defaults could not be loaded/)).not.toBeInTheDocument();
    expect(screen.getByLabelText('Built-in presentation')).toHaveValue('bonafides-compact-v1');
    expect(input).toHaveValue('Retained while settings recover.');
    expect(request.mock.calls.filter(call => call[1] !== 'GET' && call[1] !== undefined)).toHaveLength(0);
  });

  it('requires reloading a concurrent defaults version and retains only locally changed fields before explicit resave', async () => {
    const user = userEvent.setup(); let stored: DocumentDefaults = { ...remembered, tone: 'concise' }; let writes = 0;
    const { api, request } = fixture(saved, undefined, async (_path, method, body) => {
      if (method === 'PATCH') {
        if (++writes === 1) { stored = { ...stored, version: 12, tone: 'professional' }; throw new ApiError(412, 'version_conflict'); }
        stored = { ...stored, ...body as DocumentDefaultsPatch, version: 13 };
      }
      return stored;
    });
    render(<Documents api={api} profile={profile} />); await ready();
    await user.click(screen.getByText('Remembered defaults and retention'));
    await user.selectOptions(screen.getByLabelText('Remembered built-in presentation'), 'bonafides-compact-letter-v1');
    await user.click(screen.getByRole('button', { name: 'Save remembered defaults' }));
    await screen.findByText(/The saved version changed/);
    expect(screen.getByRole('button', { name: 'Save remembered defaults' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Reload remembered defaults' }));
    await waitFor(() => expect(screen.getByLabelText('Remembered writing tone')).toHaveValue('professional'));
    expect(screen.getByLabelText('Remembered built-in presentation')).toHaveValue('bonafides-compact-letter-v1');
    expect(writes).toBe(1);
    await user.click(screen.getByRole('button', { name: 'Save remembered defaults' }));
    await screen.findByText(/Remembered defaults saved for future work/);
    const patches = request.mock.calls.filter(call => call[1] === 'PATCH');
    expect(patches.map(call => call[2])).toEqual([{ template_version: 'bonafides-compact-letter-v1' }, { template_version: 'bonafides-compact-letter-v1' }]);
    expect(patches.map(call => call[3])).toEqual([8, 12]); expect(patches[1]?.[4]).not.toBe(patches[0]?.[4]);
    expect(stored.tone).toBe('professional');
    expect(screen.getByLabelText('Built-in presentation')).toHaveValue('bonafides-evidence-v1');
  });

  it('retains an offline defaults draft and reuses the same key only on explicit identical retry', async () => {
    const user = userEvent.setup(); let writes = 0;
    const { api, request } = fixture(saved, undefined, async (_path, method, body) => {
      if (method === 'PATCH') {
        if (++writes === 1) throw new TypeError('offline');
        return { ...remembered, ...body as DocumentDefaultsPatch, version: 9 };
      }
      return remembered;
    });
    render(<Documents api={api} profile={profile} />); await ready();
    await user.click(screen.getByText('Remembered defaults and retention'));
    await user.selectOptions(screen.getByLabelText('Remembered writing tone'), 'concise');
    await user.click(screen.getByRole('button', { name: 'Save remembered defaults' }));
    await screen.findByText(/Remembered defaults were not confirmed saved/);
    expect(screen.getByLabelText('Remembered writing tone')).toHaveValue('concise');
    expect(writes).toBe(1);
    await user.click(screen.getByRole('button', { name: 'Save remembered defaults' }));
    await screen.findByText(/Remembered defaults saved for future work/);
    const patches = request.mock.calls.filter(call => call[1] === 'PATCH');
    expect(patches).toHaveLength(2); expect(patches[0]?.[2]).toEqual({ tone: 'concise' });
    expect(patches[1]?.[4]).toBe(patches[0]?.[4]);
  });
});

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
    const renderRequest = request.mock.calls.find(call => call[0] === '/documents/base-doc/renders');
    expect(renderRequest?.[2]).toEqual({ revision_id: document.current_revision.id, content_sha256: document.current_revision.content_sha256, format: 'pdf' });
    expect(renderRequest?.[3]).toBe(document.version);
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
    await user.selectOptions(screen.getByLabelText('Built-in presentation'), 'bonafides-compact-v1');
    await user.click(screen.getByRole('button', { name: 'Create base résumé' }));
    expect(await screen.findByText('Document task: pending')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Cover letter' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Tracked application')).not.toBeInTheDocument();
    const call = request.mock.calls.find(item => item[1] === 'POST');
    expect(call?.[2]).toEqual({ profile_revision_id: 'profile-rev-3', template_version: 'bonafides-compact-v1' }); expect(call?.[3]).toBe(3);
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
    expect(within(projection!).getByText(/Destination from this operation/)).toHaveTextContent('not anonymization');
    expect(request.mock.calls.filter(call => call[1] === 'POST')).toHaveLength(0);
  });

  it('explains employer-owned evidence that the exact projection withholds', async () => {
    const user = userEvent.setup();
    const withheld: GenerationContext = { ...context,
      model_projection: { claims: [{ id: 'claim-rev-9', text: 'Kept the payroll batch inside its nightly window', evidence_revision_ids: ['evidence-rev-9'] }], evidence: [{ id: 'evidence-rev-9', withheld: true }] },
      withheld_evidence: [{ evidence_revision_id: 'evidence-rev-9', source: 'Quarterly review', reasons: ['employer_owned'], claim_revision_ids: ['claim-rev-9'] }] };
    const { api } = fixture(saved, async path => {
      if (path === '/applications') return { items: [application], next_cursor: null, query_elapsed_ms: 1, as_of: application.updated_at };
      if (path === '/applications/application-1/documents') return pair;
      if (path.startsWith('/applications/application-1/generation-context?')) return withheld;
      throw new ApiError(404, 'not_found');
    });
    render(<Documents api={api} profile={profile} />); await ready();
    await user.click(screen.getByRole('button', { name: 'Tracked application documents' }));
    await user.click(screen.getByRole('button', { name: 'Reload tracked applications' }));
    await user.selectOptions(await screen.findByLabelText('Tracked application'), 'application-1');
    await user.click(await screen.findByRole('button', { name: 'Preview reviewed generation context' }));
    const explanation = (await screen.findByRole('heading', { name: 'Evidence withheld from the model' })).parentElement!;
    expect(within(explanation).getByText('Quarterly review')).toBeVisible();
    expect(within(explanation).getByText('Content withheld because the source is owned by your employer.')).toBeVisible();
    expect(within(explanation).getByText('Claims still sent: “Kept the payroll batch inside its nightly window”')).toBeVisible();
    const projection = screen.getByRole('heading', { name: 'Exact minimized model projection' }).parentElement!;
    expect(JSON.parse(projection.querySelector('pre')!.textContent!)).toEqual(withheld.model_projection);
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

const sectionWorkflow: Workflow = { ...workflow, id: 'section-task-1', mode: 'section_regeneration' };
const sectionProposal: SectionProposal = {
  id: 'proposal-1', document_id: tailored.id, target_revision_id: tailored.current_revision.id,
  target_document_version: tailored.version, target_content_sha256: tailored.current_revision.content_sha256,
  section_key: 'experience', original_section: tailored.current_revision.body.sections[0]!,
  proposed_section: { ...tailored.current_revision.body.sections[0]!, blocks: [{ ...tailored.current_revision.body.sections[0]!.blocks[0]!, block_key: 'proposal-1-block-0', text: 'Led an unsupported global team.' }] },
  validation_issues: [{ path: '/sections/0/blocks/0', code: 'unestablished_factual_text' }],
  model_run: { id: 'section-run-1', stage: 'regenerate_section' }, created_at: '2026-10-25T14:00:00Z', applicable: true, historical: false,
};
const completedSectionWorkflow: Workflow = {
  ...sectionWorkflow, version: 2, state: 'succeeded',
  result: { document_id: tailored.id, proposal_id: sectionProposal.id, target_revision_id: tailored.current_revision.id, section_key: 'experience', model_run_id: 'section-run-1' },
};
function sectionPreview(path: string, status: 'qualified' | 'unqualified' | 'disabled' = 'qualified'): SectionRegenerationContext {
  const query = new URL(path, 'https://bonafides.test').searchParams;
  const section = tailored.current_revision.body.sections.find(item => item.key === query.get('section_key'))!;
  const tone = query.get('tone') === 'concise' ? 'concise' : 'professional';
  return {
    document_id: tailored.id, document_version: tailored.version, revision_id: tailored.current_revision.id,
    content_sha256: tailored.current_revision.content_sha256, section_key: section.key, tone, context_sha256: '2'.repeat(64),
    model_projection: { requirements: [{ text: 'Linux service care' }], tone, target: { kind: tailored.kind, section_key: section.key, heading: section.heading, blocks: section.blocks.map(({ text, claim_kind, claim_revision_ids, evidence_revision_ids }) => ({ text, claim_kind, claim_revision_ids, evidence_revision_ids })) } },
    generation_route: { status, reason: `${status} regenerate_section stage`, identity: { endpoint: 'http://section-model.test:11434/api/chat', model: 'synthetic-section-route' } },
  };
}
function proposalFixture(handler: Handler, members: DocumentRecord[] = [tailored]) {
  return fixture(saved, async (path, method, body, version, key) => {
    if (path === '/applications') return { items: [application], next_cursor: null, query_elapsed_ms: 1, as_of: application.updated_at };
    if (path === '/applications/application-1/documents') return { ...pair, items: members, generation_route: { ...route, status: 'qualified' } };
    return handler(path, method, body, version, key);
  });
}
async function openTailored(user: UserEvent) {
  await ready();
  await user.click(screen.getByRole('button', { name: 'Tracked application documents' }));
  await user.click(screen.getByRole('button', { name: 'Reload tracked applications' }));
  await user.selectOptions(await screen.findByLabelText('Tracked application'), application.id);
  await waitFor(() => expect(screen.getByLabelText('Section to regenerate')).toBeEnabled());
}
async function inspectSection(user: UserEvent) {
  await user.click(screen.getByRole('button', { name: 'Preview selected-section model projection' }));
  await screen.findByRole('region', { name: 'Exact selected-section model projection' });
}

describe('saved tailored section proposals', () => {
  it('exposes no model rewrite surface or model requests for a base document, including its dirty editor', async () => {
    const user = userEvent.setup(); const { api, request } = fixture();
    render(<Documents api={api} profile={profile} />); const input = await ready();
    expect(screen.queryByRole('heading', { name: 'Selected-section proposals' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Section to regenerate')).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/^(Section )?writing tone$/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /projection|proposal|Generate résumé and cover letter/ })).not.toBeInTheDocument();
    await user.type(input, ' More owner wording.');
    expect(screen.queryByRole('button', { name: /projection|proposal|Generate résumé and cover letter/ })).not.toBeInTheDocument();
    expect(request.mock.calls.some(call => call[0].includes('regeneration') || call[0].includes('generation-context') || call[1] === 'POST')).toBe(false);
  });

  it('binds an explicit projection confirmation to the selected saved section and tone, invalidating it on another selection', async () => {
    const user = userEvent.setup(); const { api, request } = proposalFixture(async (path, method) => {
      if (path.includes('/regeneration-context?')) return sectionPreview(path);
      if (path === '/documents/tailored-resume/regeneration-proposals' && method === 'POST') return sectionWorkflow;
      if (path === '/workflows/section-task-1') return sectionWorkflow;
      throw new ApiError(404, 'not_found');
    });
    render(<Documents api={api} profile={profile} />); await openTailored(user);
    await user.selectOptions(screen.getByLabelText('Section to regenerate'), 'skills');
    await user.selectOptions(screen.getByLabelText('Section writing tone'), 'concise');
    await inspectSection(user);
    const projection = screen.getByRole('region', { name: 'Exact selected-section model projection' });
    const previewCall = request.mock.calls.find(call => call[0].includes('/regeneration-context?'))!;
    const expected = sectionPreview(previewCall[0]);
    expect(JSON.parse(projection.querySelector('pre')!.textContent!)).toEqual(expected.model_projection);
    expect(projection.querySelector('pre')).not.toHaveTextContent('mira@example.test');
    expect(projection.querySelector('pre')).not.toHaveTextContent('Maintained Linux service checks.');
    expect(within(projection).getByText('http://section-model.test:11434/api/chat', { selector: 'code' })).toBeVisible();
    expect(within(projection).getByText(/24 hours after terminal completion/)).toBeVisible();
    expect(screen.getByRole('button', { name: 'Request section proposal' })).toBeDisabled();
    expect(request.mock.calls.filter(call => call[1] === 'POST')).toHaveLength(0);
    await user.click(screen.getByRole('checkbox', { name: /I reviewed this exact selected-section projection/ }));
    await user.selectOptions(screen.getByLabelText('Section to regenerate'), 'experience');
    expect(screen.queryByRole('region', { name: 'Exact selected-section model projection' })).not.toBeInTheDocument();
    await inspectSection(user);
    expect(screen.getByRole('checkbox', { name: /I reviewed this exact selected-section projection/ })).not.toBeChecked();
    await user.click(screen.getByRole('checkbox', { name: /I reviewed this exact selected-section projection/ }));
    await user.click(screen.getByRole('button', { name: 'Request section proposal' }));
    await screen.findByRole('heading', { name: 'Document task: pending' });
    const command = request.mock.calls.find(call => call[1] === 'POST')!;
    expect(command[0]).toBe('/documents/tailored-resume/regeneration-proposals');
    expect(command[2]).toEqual({ revision_id: tailored.current_revision.id, content_sha256: tailored.current_revision.content_sha256, section_key: 'experience', tone: 'concise', context_sha256: '2'.repeat(64) });
    expect(command[3]).toBe(tailored.version); expect(command[4]).toBeTypeOf('string');
    expect(screen.getByLabelText('Block text: b-1')).toHaveValue('Maintained Linux service checks.');
  });

  it.each(['unqualified', 'disabled'] as const)('allows inspection but cannot inherit pair qualification for a %s section route', async status => {
    const user = userEvent.setup(); const { api, request } = proposalFixture(async path => {
      if (path.includes('/regeneration-context?')) return sectionPreview(path, status);
      throw new ApiError(404, 'not_found');
    });
    render(<Documents api={api} profile={profile} />); await openTailored(user);
    await inspectSection(user);
    expect(screen.getByText(`Section regeneration route: ${status}.`)).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: /I reviewed this exact selected-section projection/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Request section proposal' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Block text: b-1'), { target: { value: 'Keep my local wording.' } });
    expect(screen.getByRole('button', { name: 'Preview selected-section model projection' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Request section proposal' })).not.toBeInTheDocument();
    expect(request.mock.calls.filter(call => call[1] === 'POST')).toHaveLength(0);
    expect(screen.getByLabelText('Block text: b-1')).toHaveValue('Keep my local wording.');
  });

  it('stores task output without mutating words, then explicitly applies just the compared section as a blocked editable draft', async () => {
    const user = userEvent.setup();
    const applied: DocumentRecord = { ...tailored, version: 5, reviewed: false, unsupported_claims: ['/sections/0/blocks/0'], current_revision: { ...tailored.current_revision, id: 'applied-section-revision', revision_no: 3, origin: 'manual', content_sha256: '3'.repeat(64), body: { ...tailored.current_revision.body, sections: [sectionProposal.proposed_section, tailored.current_revision.body.sections[1]!] }, validation_issues: sectionProposal.validation_issues } };
    const { api, request } = proposalFixture(async (path, method) => {
      if (path.includes('/regeneration-context?')) return sectionPreview(path);
      if (path === '/documents/tailored-resume/regeneration-proposals' && method === 'POST') return sectionWorkflow;
      if (path === '/workflows/section-task-1') return completedSectionWorkflow;
      if (path === '/documents/tailored-resume/regeneration-proposals/proposal-1') return sectionProposal;
      if (path === '/documents/tailored-resume/regeneration-proposals/proposal-1/apply') return applied;
      throw new ApiError(404, 'not_found');
    });
    render(<Documents api={api} profile={profile} />); await openTailored(user); await inspectSection(user);
    await user.click(screen.getByRole('checkbox', { name: /I reviewed this exact selected-section projection/ }));
    vi.useFakeTimers();
    try {
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Request section proposal' })); });
      await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
      expect(screen.getByRole('region', { name: 'Section proposal comparison' })).toBeInTheDocument();
      expect(screen.getByLabelText('Block text: b-1')).toHaveValue('Maintained Linux service checks.');
      expect(within(screen.getByRole('article', { name: 'Proposed section' })).getByText('Led an unsupported global team.')).toBeInTheDocument();
      expect(screen.getByText('Proposed draft has review/export blockers.')).toBeVisible();
      expect(request.mock.calls.filter(call => call[1] === 'POST')).toHaveLength(1);
      expect(request.mock.calls.some(call => call[0] === '/documents/tailored-resume')).toBe(false);
      expect(screen.getByRole('button', { name: 'Apply proposal as new draft revision' })).toBeDisabled();
      await act(async () => { fireEvent.click(screen.getByRole('checkbox', { name: /I compared the original and proposal/ })); });
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Apply proposal as new draft revision' })); });
      expect(screen.getByLabelText('Block text: proposal-1-block-0')).toHaveValue('Led an unsupported global team.');
      expect(screen.getByLabelText('Block text: b-2')).toHaveValue('Wrote SQL queries.');
      expect(screen.getByRole('button', { name: 'Accept this exact revision' })).toBeDisabled();
      expect(screen.getByRole('button', { name: 'Render reviewed PDF' })).toBeDisabled();
      expect(screen.getByLabelText('Block text: proposal-1-block-0')).toBeEnabled();
      const command = request.mock.calls.find(call => call[0].endsWith('/proposal-1/apply'))!;
      expect(command[2]).toMatchObject({ revision_id: tailored.current_revision.id, content_sha256: tailored.current_revision.content_sha256 });
      expect(command[3]).toBe(tailored.version); expect(command[4]).toBeTypeOf('string');
      expect(screen.queryByRole('link', { name: /Download rendered PDF/ })).not.toBeInTheDocument();
    } finally { vi.useRealTimers(); }
  });

  it('reloads immutable paginated proposals after remount and retains a dirty buffer through an offline list read and recovery', async () => {
    const user = userEvent.setup(); const older = { ...sectionProposal, id: 'older-proposal', created_at: '2026-10-24T12:00:00Z' };
    let offline = true;
    const { api, request } = proposalFixture(async path => {
      if (path === '/documents/tailored-resume/regeneration-proposals?limit=20') {
        if (offline) { offline = false; throw new TypeError('offline'); }
        return { items: [sectionProposal], next_cursor: sectionProposal.id };
      }
      if (path === '/documents/tailored-resume/regeneration-proposals?limit=20&before=proposal-1') return { items: [older], next_cursor: null };
      if (path === '/documents/tailored-resume/regeneration-proposals/older-proposal') return older;
      throw new ApiError(404, 'not_found');
    });
    const first = render(<Documents api={api} profile={profile} />); await openTailored(user); first.unmount();
    render(<Documents api={api} profile={profile} />); await openTailored(user);
    fireEvent.change(screen.getByLabelText('Block text: b-1'), { target: { value: 'Retained while recovering saved proposals.' } });
    await user.click(screen.getByRole('button', { name: 'Reload saved section proposals' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('server could not be reached');
    expect(screen.getByLabelText('Block text: b-1')).toHaveValue('Retained while recovering saved proposals.');
    await user.click(screen.getByRole('button', { name: 'Reload saved section proposals' }));
    await screen.findByRole('button', { name: /Compare proposal for Experience/ });
    await user.click(screen.getByRole('button', { name: 'Load older section proposals' }));
    await waitFor(() => expect(screen.getAllByRole('button', { name: /Compare proposal for Experience/ })).toHaveLength(2));
    await user.click(screen.getByRole('button', { name: /Compare proposal for Experience · 2026-10-24/ }));
    await screen.findByRole('region', { name: 'Section proposal comparison' });
    expect(screen.getByLabelText('Block text: b-1')).toHaveValue('Retained while recovering saved proposals.');
    expect(screen.getByRole('button', { name: 'Apply proposal as new draft revision' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Save document revision' })).toBeEnabled();
    expect(request.mock.calls.some(call => call[1] === 'POST')).toBe(false);
    expect(screen.queryByRole('heading', { name: /Document task:/ })).not.toBeInTheDocument();
  });

  it('keeps imported historical proposals readable but refuses applying them even with a matching target and applicability flag', async () => {
    const user = userEvent.setup();
    const historical: SectionProposal = { ...sectionProposal, historical: true, applicable: true };
    const { api, request } = proposalFixture(async path => {
      if (path === '/documents/tailored-resume/regeneration-proposals?limit=20') return { items: [historical], next_cursor: null };
      if (path === '/documents/tailored-resume/regeneration-proposals/proposal-1') return historical;
      throw new ApiError(404, 'not_found');
    });
    render(<Documents api={api} profile={profile} />); await openTailored(user);
    await user.click(screen.getByRole('button', { name: 'Reload saved section proposals' }));
    await user.click(await screen.findByRole('button', { name: /Compare proposal for Experience/ }));
    await screen.findByRole('region', { name: 'Section proposal comparison' });
    expect(screen.getByText(/Imported historical proposal/)).toBeVisible();
    expect(screen.getByRole('checkbox', { name: /I compared the original and proposal/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Apply proposal as new draft revision' })).toBeDisabled();
    expect(screen.getByLabelText('Block text: b-1')).toHaveValue('Maintained Linux service checks.');
    expect(request.mock.calls.some(call => call[1] === 'POST')).toBe(false);
  });

  it('retains recoverable edits after apply 412 and never retargets an old proposal onto a newer or restored revision', async () => {
    const user = userEvent.setup(); const newer: DocumentRecord = { ...tailored, version: 5, current_revision: { ...tailored.current_revision, id: 'restored-same-words', revision_no: 3, body: { ...tailored.current_revision.body, sections: [tailored.current_revision.body.sections[0]!, { ...tailored.current_revision.body.sections[1]!, blocks: [{ ...tailored.current_revision.body.sections[1]!.blocks[0]!, text: 'Newer server skills wording.' }] }] } } };
    const { api, request } = proposalFixture(async (path, method) => {
      if (path === '/documents/tailored-resume/regeneration-proposals?limit=20') return { items: [sectionProposal], next_cursor: null };
      if (path === '/documents/tailored-resume/regeneration-proposals/proposal-1') return sectionProposal;
      if (path.endsWith('/proposal-1/apply')) throw new ApiError(412, 'version_conflict');
      if (path === '/documents/tailored-resume' && method === 'GET') return newer;
      throw new ApiError(404, 'not_found');
    });
    render(<Documents api={api} profile={profile} />); await openTailored(user);
    await user.click(screen.getByRole('button', { name: 'Reload saved section proposals' }));
    await user.click(await screen.findByRole('button', { name: /Compare proposal for Experience/ }));
    await user.click(await screen.findByRole('checkbox', { name: /I compared the original and proposal/ }));
    await user.click(screen.getByRole('button', { name: 'Apply proposal as new draft revision' }));
    expect(await screen.findByRole('alert')).toHaveFocus();
    expect(screen.getByLabelText('Block text: b-1')).toHaveValue('Maintained Linux service checks.');
    fireEvent.change(screen.getByLabelText('Block text: b-1'), { target: { value: 'My recovered local wording.' } });
    await user.click(screen.getByRole('button', { name: 'Compare current document revision' }));
    await screen.findByText('Current server document revision 3');
    expect(screen.getByLabelText('Block text: b-1')).toHaveValue('My recovered local wording.');
    await user.click(screen.getByRole('checkbox', { name: /I compared both revisions/ }));
    await user.click(screen.getByRole('button', { name: 'Rebase my edits onto compared revision' }));
    expect(screen.getByLabelText('Block text: b-1')).toHaveValue('My recovered local wording.');
    expect(screen.getByLabelText('Block text: b-2')).toHaveValue('Newer server skills wording.');
    expect(screen.getByRole('button', { name: 'Save document revision' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Apply proposal as new draft revision' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Discard document changes' }));
    expect(screen.getByRole('button', { name: 'Apply proposal as new draft revision' })).toBeDisabled();
    const applications = request.mock.calls.filter(call => call[0].endsWith('/proposal-1/apply'));
    expect(applications).toHaveLength(1); expect(applications[0]?.[3]).toBe(tailored.version);
    expect(applications[0]?.[2]).toMatchObject({ revision_id: tailored.current_revision.id, content_sha256: tailored.current_revision.content_sha256 });
    expect(screen.getByText(/This proposal cannot be applied to the displayed saved target/)).toBeInTheDocument();
  });

  it.each([{ other: false, dirty: true }, { other: true, dirty: false }, { other: true, dirty: true }])('keeps selection and local edits on proposal reconnect (%j)', async ({ other, dirty }) => {
    const user = userEvent.setup(); const onDirtyChange = vi.fn();
    const letter: DocumentRecord = { ...tailored, id: 'tailored-letter', kind: 'cover_letter', current_revision: { ...tailored.current_revision, id: 'letter-revision', body: { ...tailored.current_revision.body, title: 'Saved selected cover letter' } } };
    let reads = 0;
    const { api, request } = proposalFixture(async (path, method) => {
      if (path.includes('/regeneration-context?')) return sectionPreview(path);
      if (path === '/documents/tailored-resume/regeneration-proposals' && method === 'POST') return sectionWorkflow;
      if (path === '/workflows/section-task-1') return completedSectionWorkflow;
      if (path === '/documents/tailored-resume/regeneration-proposals/proposal-1') { if (++reads === 1) throw new TypeError('offline proposal read'); return sectionProposal; }
      throw new ApiError(404, 'not_found');
    }, [tailored, letter]);
    render(<Documents api={api} profile={profile} onDirtyChange={onDirtyChange} />); await openTailored(user); await inspectSection(user);
    await user.click(screen.getByRole('checkbox', { name: /I reviewed this exact selected-section projection/ }));
    vi.useFakeTimers();
    try {
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Request section proposal' })); });
      await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
      expect(screen.getByText(/Task status\/result could not be loaded/)).toBeVisible();
      if (other) await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Cover letter' })); });
      const expected = dirty ? 'Keep my current unsaved edits during reconnect.' : 'Maintained Linux service checks.';
      if (dirty) await act(async () => { fireEvent.change(screen.getByLabelText('Block text: b-1'), { target: { value: expected } }); });
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Reconnect document task' })); });
      await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
      expect(reads).toBe(2);
      expect(screen.getByLabelText('Document title')).toHaveValue(other ? 'Saved selected cover letter' : 'Base résumé');
      expect(screen.getByLabelText('Block text: b-1')).toHaveValue(expected);
      expect(screen.getByRole('button', { name: other ? 'Cover letter' : 'Résumé' })).toHaveAttribute('aria-pressed', 'true');
      expect(screen.queryByRole('button', { name: 'Reconnect document task' })).not.toBeInTheDocument();
      if (other) expect(screen.queryByRole('region', { name: 'Section proposal comparison' })).not.toBeInTheDocument();
      else expect(screen.getByRole('region', { name: 'Section proposal comparison' })).toBeInTheDocument();
      expect(onDirtyChange).toHaveBeenLastCalledWith(dirty);
      expect(request.mock.calls.filter(call => call[1] === 'POST')).toHaveLength(1);
      expect(request.mock.calls.some(call => call[0] === '/documents/tailored-resume')).toBe(false);
      if (dirty) expect(screen.getByRole('button', { name: 'Save document revision' })).toBeEnabled();
    } finally { vi.useRealTimers(); }
  });

  it('clears confirmation after a changed-context rejection instead of silently queueing a different professional projection', async () => {
    const user = userEvent.setup(); const { api, request } = proposalFixture(async (path, method) => {
      if (path.includes('/regeneration-context?')) return sectionPreview(path);
      if (path === '/documents/tailored-resume/regeneration-proposals' && method === 'POST') throw new ApiError(409, 'regeneration_context_changed');
      throw new ApiError(404, 'not_found');
    });
    render(<Documents api={api} profile={profile} />); await openTailored(user); await inspectSection(user);
    await user.click(screen.getByRole('checkbox', { name: /I reviewed this exact selected-section projection/ }));
    await user.click(screen.getByRole('button', { name: 'Request section proposal' }));
    expect(await screen.findByRole('alert')).toHaveFocus();
    expect(screen.queryByRole('region', { name: 'Exact selected-section model projection' })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: /Document task:/ })).not.toBeInTheDocument();
    expect(screen.getByLabelText('Block text: b-1')).toHaveValue('Maintained Linux service checks.');
    expect(request.mock.calls.filter(call => call[1] === 'POST')).toHaveLength(1);
    await inspectSection(user);
    expect(screen.getByRole('checkbox', { name: /I reviewed this exact selected-section projection/ })).not.toBeChecked();
  });
  it('requires fresh explicit confirmation after an offline enqueue and reuses only its identical payload-bound command key', async () => {
    const user = userEvent.setup(); let writes = 0;
    const { api, request } = proposalFixture(async (path, method) => {
      if (path.includes('/regeneration-context?')) return sectionPreview(path);
      if (path === '/documents/tailored-resume/regeneration-proposals' && method === 'POST') {
        if (++writes === 1) throw new TypeError('offline enqueue');
        return sectionWorkflow;
      }
      if (path === '/workflows/section-task-1') return sectionWorkflow;
      throw new ApiError(404, 'not_found');
    });
    render(<Documents api={api} profile={profile} />); await openTailored(user); await inspectSection(user);
    await user.click(screen.getByRole('checkbox', { name: /I reviewed this exact selected-section projection/ }));
    await user.click(screen.getByRole('button', { name: 'Request section proposal' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('server could not be reached');
    expect(screen.getByRole('checkbox', { name: /I reviewed this exact selected-section projection/ })).not.toBeChecked();
    expect(screen.getByRole('button', { name: 'Request section proposal' })).toBeDisabled();
    expect(screen.queryByRole('heading', { name: /Document task:/ })).not.toBeInTheDocument();
    expect(screen.getByLabelText('Block text: b-1')).toHaveValue('Maintained Linux service checks.');
    expect(writes).toBe(1);
    await user.click(screen.getByRole('checkbox', { name: /I reviewed this exact selected-section projection/ }));
    await user.click(screen.getByRole('button', { name: 'Request section proposal' }));
    await screen.findByRole('heading', { name: 'Document task: pending' });
    const commands = request.mock.calls.filter(call => call[1] === 'POST');
    expect(commands).toHaveLength(2);
    expect(commands[1]?.[2]).toEqual(commands[0]?.[2]);
    expect(commands[0]?.[4]).toBeTypeOf('string'); expect(commands[1]?.[4]).toBe(commands[0]?.[4]);
  });
});
