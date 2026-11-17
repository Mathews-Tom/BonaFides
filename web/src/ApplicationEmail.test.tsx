// SPDX-License-Identifier: MIT OR Apache-2.0
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Api, ApiError, type DocumentRecord, type Workflow } from './api';
import { ApplicationEmail, emailMailto } from './ApplicationEmail';

const email: DocumentRecord = {
  id: 'email-1', application_id: 'application-1', kind: 'application_email', scope: 'tailored', version: 2,
  reviewed: false, unsupported_claims: [], stale: false, stale_reason: null, artifacts: [],
  current_revision: { id: 'revision-2', revision_no: 2, profile_revision_id: 'profile-1', content_sha256: 'a'.repeat(64),
    template_version: 'email-v1', origin: 'generated', created_at: '2026-11-17T12:00:00Z', validation_issues: [],
    provenance_snapshot: { email: { recipient: '', subject: 'Application: Engineer' } },
    body: { title: 'Application email', sections: [{ key: 'email', heading: 'Application email', blocks: [
      { block_key: 'first', text: 'Dear Hiring team,', claim_kind: 'greeting', claim_revision_ids: [], evidence_revision_ids: [] },
      { block_key: 'second', text: 'Maintained Linux checks.', claim_kind: 'factual', claim_revision_ids: ['claim-1'], evidence_revision_ids: ['evidence-1'] },
    ] }] },
  },
};
const attachments: DocumentRecord[] = (['resume', 'cover_letter'] as const).map(kind => ({ ...email, id: kind, kind,
  artifacts: [{ id: `${kind}-pdf`, format: 'pdf', revision_id: email.current_revision.id }, { id: 'old', format: 'pdf', revision_id: 'old-revision' }],
}));
const workflow: Workflow = {
  id: 'writing-1', version: 1, state: 'pending', mode: 'application_email', kind: 'document_generate', attempt: 0, max_attempts: 3, next_attempt_at: null, last_error_code: null, result: null,
  created_at: '2026-11-17T12:00:00Z', updated_at: '2026-11-17T12:00:00Z', deadline_at: '2030-01-01T00:00:00Z', cancel_requested_at: null, lease_expires_at: null, last_heartbeat_at: null, queue_age_seconds: 0,
  remaining_attempts: 3, cancelable: true, retryable: false, retry_block_reason: null, delivery_state: 'not_attempted',
  cancellation: { requested: false, local_fence_settled: false, provider_stopped: null, completed_effects_preserved: true, reason: null },
  usage: { status: 'not_applicable', model_runs: 0, inference_post_attempts: 0, inference_post_limit: null, input_tokens: null, output_tokens: null, token_budget: 0, tokens_used: 0, cost_budget_microunits: 0, cost_used_microunits: null, currency: 'USD' },
};
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(yes => { resolve = yes; }); return { promise, resolve }; }
function fixture(initial: DocumentRecord | null = email) {
  const api = new Api(); let stored = initial; let applicationVersion = 7;
  const get = vi.spyOn(api, 'getApplicationEmail').mockImplementation(async () => { if (!stored) throw new ApiError(404, 'application_email_not_found'); return stored; });
  const draft = vi.spyOn(api, 'draftApplicationEmail').mockResolvedValue(workflow);
  const rewrite = vi.spyOn(api, 'rewriteApplicationEmail').mockResolvedValue(workflow);
  const save = vi.spyOn(api, 'saveApplicationEmail').mockImplementation(async (_id, body) => {
    stored = { ...email, version: (stored?.version ?? 0) + 1, current_revision: { ...email.current_revision, id: 'manual-revision', body: body.body, provenance_snapshot: { email: body.email } } }; return stored;
  });
  const restore = vi.spyOn(api, 'restoreApplicationEmail').mockResolvedValue({ ...email, version: 6 });
  const poll = deferred<Workflow>();
  const request = vi.spyOn(api, 'request').mockImplementation(async <T,>(path: string) => (path.endsWith('/documents') ? { version: applicationVersion } : path.endsWith('/retry') ? workflow : await poll.promise) as T);
  const onDirty = vi.fn(); const onBusy = vi.fn();
  const props = { api, applicationId: 'application-1', documents: attachments, onDirty, onBusy };
  return { api, get, draft, rewrite, save, restore, request, poll, props, onDirty, onBusy, store: (value: DocumentRecord) => { stored = value; }, applicationVersion: (value: number) => { applicationVersion = value; } };
}
async function ready() { const field = await screen.findByRole('textbox', { name: 'Email subject' }); await waitFor(() => expect(field).toBeEnabled()); return field; }
async function rewrite() {
  await userEvent.click(screen.getByRole('checkbox', { name: 'I want to replace the email subject and body' }));
  await userEvent.click(screen.getByRole('button', { name: 'Rewrite application email' }));
}
const changed = (restoreId: string | null = 'revision-2'): DocumentRecord => ({ ...email, version: 5, current_revision: { ...email.current_revision, id: 'rewrite-revision', provenance_snapshot: { email: { recipient: 'typed@example.test', subject: 'Application: Engineer' }, email_restore_revision_id: restoreId } } });
afterEach(() => { vi.restoreAllMocks(); Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined }); });

describe('application email editor and handoff', () => {
  it('drafts from an honest empty state, shows progress and loads the saved email', async () => {
    const f = fixture(null); render(<ApplicationEmail {...f.props} />);
    const draft = await screen.findByRole('button', { name: 'Draft application email' }); await waitFor(() => expect(draft).toBeEnabled());
    expect(screen.getByText(/No application email yet/)).toBeVisible();
    await userEvent.click(draft);
    expect(f.draft).toHaveBeenCalledWith('application-1', 7, expect.any(String), expect.any(AbortSignal));
    expect(screen.getByRole('status', { name: 'Email writing progress' })).toHaveTextContent('pending'); expect(draft).toBeDisabled();
    f.store(email); await act(async () => f.poll.resolve({ ...workflow, state: 'succeeded' })); await ready();
    expect(screen.getByRole('textbox', { name: 'Email body' })).toHaveValue('Dear Hiring team,');
    expect(screen.queryByRole('button', { name: 'Undo email rewrite' })).not.toBeInTheDocument();
  });

  it('requires a saved cover letter before drafting and keeps an unavailable load distinct from no draft', async () => {
    const f = fixture(null); f.get.mockRejectedValueOnce(new ApiError(503, 'database_unavailable'));
    render(<ApplicationEmail {...f.props} documents={[]} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('database_unavailable');
    expect(screen.queryByRole('button', { name: 'Draft application email' })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Reload email' }));
    expect(await screen.findByRole('button', { name: 'Draft application email' })).toBeDisabled();
  });

  it('saves separate fields with a revision precondition and preserves factual references across reload', async () => {
    const f = fixture(); const view = render(<ApplicationEmail {...f.props} />); await ready();
    fireEvent.change(screen.getByLabelText('Email recipient'), { target: { value: 'typed@example.test' } });
    fireEvent.change(screen.getByLabelText('Email subject'), { target: { value: 'My subject' } });
    fireEvent.change(screen.getByLabelText('Email body passage 2'), { target: { value: 'Edited checked passage.' } });
    await userEvent.click(screen.getByRole('button', { name: 'Save email changes' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save email changes' })).toBeDisabled());
    expect(f.save).toHaveBeenCalledWith('email-1', expect.objectContaining({ base_revision_id: 'revision-2', email: { recipient: 'typed@example.test', subject: 'My subject' }, body: expect.objectContaining({ sections: [expect.objectContaining({ blocks: [email.current_revision.body.sections[0]!.blocks[0], expect.objectContaining({ text: 'Edited checked passage.', claim_kind: 'factual', claim_revision_ids: ['claim-1'], evidence_revision_ids: ['evidence-1'] })] })] }) }), 2, expect.any(String), expect.any(AbortSignal));
    view.unmount(); render(<ApplicationEmail {...f.props} />); await ready();
    expect(screen.getByLabelText('Email recipient')).toHaveValue('typed@example.test'); expect(screen.getByLabelText('Email subject')).toHaveValue('My subject'); expect(screen.getByLabelText('Email body passage 2')).toHaveValue('Edited checked passage.');
  });

  it.each([new ApiError(412, 'version_conflict'), new ApiError(409, 'revision_mismatch')])('retains attempted edits after %s and requires explicit comparison/replacement', async failure => {
    const f = fixture(); f.save.mockRejectedValueOnce(failure); render(<ApplicationEmail {...f.props} />); await ready();
    fireEvent.change(screen.getByLabelText('Email subject'), { target: { value: 'Keep my attempt' } });
    await userEvent.click(screen.getByRole('button', { name: 'Save email changes' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Reload email to compare'); expect(screen.getByLabelText('Email subject')).toHaveValue('Keep my attempt');
    expect(screen.getByRole('button', { name: 'Save email changes' })).toBeDisabled();
    f.store(changed(null)); await userEvent.click(screen.getByRole('button', { name: 'Reload email' }));
    expect(await screen.findByRole('region', { name: 'Saved email comparison' })).toHaveTextContent('Application: Engineer');
    expect(screen.getByLabelText('Email subject')).toHaveValue('Keep my attempt'); expect(f.save).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole('button', { name: 'Use saved email instead of my changes' }));
    expect(screen.getByLabelText('Email subject')).toHaveValue('Application: Engineer');
  });

  it('shows an empty recipient as missing and never fills one for mail handoff', async () => {
    const f = fixture(); render(<ApplicationEmail {...f.props} />); await ready();
    expect(screen.getByLabelText('Email recipient')).toHaveValue(''); expect(screen.getByText(/No recipient yet/)).toBeVisible();
    expect(screen.getByRole('link', { name: 'Open in mail app' })).toHaveAttribute('href', expect.stringMatching(/^mailto:\?subject=/));
  });

  it('explains replacement, keeps a typed recipient and offers undo only after a rewrite', async () => {
    const f = fixture(); render(<ApplicationEmail {...f.props} />); await ready();
    expect(screen.getByText(/Rewrite replaces the subject and body/)).toHaveTextContent('keeps your typed recipient');
    expect(screen.getByRole('button', { name: 'Rewrite application email' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Undo email rewrite' })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Email recipient'), { target: { value: 'typed@example.test' } });
    await userEvent.click(screen.getByRole('button', { name: 'Save email changes' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save email changes' })).toBeDisabled()); await rewrite();
    expect(f.rewrite).toHaveBeenCalledWith('email-1', 3, expect.any(String), expect.any(AbortSignal));
    f.store(changed()); await act(async () => f.poll.resolve({ ...workflow, state: 'succeeded' }));
    const undo = await screen.findByRole('button', { name: 'Undo email rewrite' }); await waitFor(() => expect(undo).toBeEnabled());
    expect(screen.getByLabelText('Email recipient')).toHaveValue('typed@example.test');
    await userEvent.click(undo); await waitFor(() => expect(f.restore).toHaveBeenCalledWith('email-1', 'revision-2', 5, expect.any(String), expect.any(AbortSignal)));
    expect(screen.queryByRole('button', { name: 'Undo email rewrite' })).not.toBeInTheDocument();
  });

  it('removes the undo offer after saving another edit', async () => {
    const f = fixture(changed()); render(<ApplicationEmail {...f.props} />); await ready();
    expect(screen.getByRole('button', { name: 'Undo email rewrite' })).toBeEnabled();
    fireEvent.change(screen.getByLabelText('Email subject'), { target: { value: 'Later subject' } });
    await userEvent.click(screen.getByRole('button', { name: 'Save email changes' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Undo email rewrite' })).not.toBeInTheDocument()); expect(f.restore).not.toHaveBeenCalled();
  });

  it.each([new ApiError(412, 'version_conflict'), new ApiError(409, 'email_restore_conflict')])('reloads the current email and explains undo refusal (%s)', async failure => {
    const f = fixture(changed()); f.restore.mockRejectedValueOnce(failure);
    render(<ApplicationEmail {...f.props} />); await ready();
    const later = { ...changed(null), version: 6, current_revision: { ...changed(null).current_revision, id: 'later-edit', provenance_snapshot: { email: { recipient: 'typed@example.test', subject: 'Later saved subject' } } } };
    f.store(later); await userEvent.click(screen.getByRole('button', { name: 'Undo email rewrite' }));
    expect(await screen.findByRole('alert')).toHaveTextContent("Undo isn't available because the email changed after the rewrite");
    expect(screen.getByLabelText('Email recipient')).toHaveValue('typed@example.test'); expect(screen.getByLabelText('Email subject')).toHaveValue('Later saved subject');
    expect(screen.queryByRole('button', { name: 'Undo email rewrite' })).not.toBeInTheDocument(); expect(f.restore).toHaveBeenCalledTimes(1); expect(f.get).toHaveBeenCalledTimes(2);
  });

  it.each([new ApiError(412, 'version_conflict'), new ApiError(409, 'email_restore_conflict')])('offers the newer rewrite undo after a refused stale undo (%s)', async failure => {
    const f = fixture(changed()); f.restore.mockRejectedValueOnce(failure);
    render(<ApplicationEmail {...f.props} />); await ready();
    const newer: DocumentRecord = { ...changed('newer-before-revision'), version: 9, current_revision: {
      ...changed('newer-before-revision').current_revision, id: 'newer-rewrite-revision',
      provenance_snapshot: { email: { recipient: 'newer@example.test', subject: 'Newer rewrite subject' }, email_restore_revision_id: 'newer-before-revision' },
    } };
    f.store(newer); await userEvent.click(screen.getByRole('button', { name: 'Undo email rewrite' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('The email was rewritten again');
    expect(screen.getByLabelText('Email subject')).toHaveValue('Newer rewrite subject');
    expect(screen.getByLabelText('Email recipient')).toHaveValue('newer@example.test');
    expect(screen.getByRole('button', { name: 'Undo email rewrite' })).toBeEnabled();
    expect(f.restore).toHaveBeenCalledTimes(1);
    f.restore.mockResolvedValueOnce({ ...email, version: 10 });
    await userEvent.click(screen.getByRole('button', { name: 'Undo email rewrite' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Undo email rewrite' })).not.toBeInTheDocument());
    expect(f.restore).toHaveBeenLastCalledWith('email-1', 'newer-before-revision', 9, expect.any(String), expect.any(AbortSignal));
  });

  it('offers the mid-run saved edit back through whole-email undo', async () => {
    const f = fixture(); render(<ApplicationEmail {...f.props} />); await ready(); await rewrite();
    fireEvent.change(screen.getByLabelText('Email body', { exact: true }), { target: { value: 'Body saved while writing.' } });
    await userEvent.click(screen.getByRole('button', { name: 'Save email changes' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save email changes' })).toBeDisabled());
    const midrun = f.save.mock.results[0]; expect(midrun?.type).toBe('return'); const saved = await midrun!.value;
    f.restore.mockResolvedValueOnce({ ...saved, version: 6 }); f.store(changed('manual-revision'));
    await act(async () => f.poll.resolve({ ...workflow, state: 'succeeded' }));
    expect(await screen.findByText(/It may have replaced edits saved while writing/)).toBeVisible();
    expect(screen.getByLabelText('Email body', { exact: true })).toHaveValue('Dear Hiring team,');
    await userEvent.click(screen.getByRole('button', { name: 'Undo email rewrite' }));
    await waitFor(() => expect(screen.getByLabelText('Email body', { exact: true })).toHaveValue('Body saved while writing.'));
    expect(f.restore.mock.calls[0]?.slice(0, 3)).toEqual(['email-1', 'manual-revision', 5]);
  });

  it('retains unsaved mid-run text for explicit comparison rather than losing it', async () => {
    const f = fixture(); render(<ApplicationEmail {...f.props} />); await ready(); await rewrite();
    fireEvent.change(screen.getByLabelText('Email body', { exact: true }), { target: { value: 'Not yet saved.' } }); f.store(changed());
    await act(async () => f.poll.resolve({ ...workflow, state: 'succeeded' }));
    expect(await screen.findByRole('region', { name: 'Saved email comparison' })).toBeVisible(); expect(screen.getByLabelText('Email body', { exact: true })).toHaveValue('Not yet saved.');
    await userEvent.click(screen.getByRole('button', { name: 'Use saved email instead of my changes' }));
    expect(screen.getByRole('button', { name: 'Undo email rewrite' })).toBeEnabled();
  });

  it('copies each displayed field exactly, including all body passages and unsaved changes', async () => {
    const f = fixture(changed()); render(<ApplicationEmail {...f.props} />); await ready();
    const writeText = vi.fn().mockResolvedValue(undefined); Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
    fireEvent.change(screen.getByLabelText('Email subject'), { target: { value: 'Café & résumé' } });
    for (const part of ['recipient', 'subject', 'body']) await userEvent.click(screen.getByRole('button', { name: `Copy ${part}` }));
    expect(writeText.mock.calls).toEqual([['typed@example.test'], ['Café & résumé'], ['Dear Hiring team,\n\nMaintained Linux checks.']]);
    expect(screen.getByText('Email body copied.')).toBeVisible();
  });

  it.each(['missing', 'rejected'])('offers manual copy guidance when clipboard is %s', async mode => {
    const f = fixture(); render(<ApplicationEmail {...f.props} />); await ready();
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: mode === 'missing' ? undefined : { writeText: vi.fn().mockRejectedValue(new Error('permission denied')) } });
    fireEvent.click(screen.getByRole('button', { name: 'Copy body' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Select the email body and copy it manually, one passage at a time');
    expect(screen.getByLabelText('Email body', { exact: true })).toHaveFocus();
  });

  it('encodes unicode, reserved characters and all line endings exactly for RFC 6068', async () => {
    expect(emailMailto('hire+é@example.test', 'Résumé & role?', 'Café\nLine 2\r\nLast\rfin')).toBe('mailto:hire%2B%C3%A9@example.test?subject=R%C3%A9sum%C3%A9%20%26%20role%3F&body=Caf%C3%A9%0D%0ALine%202%0D%0ALast%0D%0Afin');
    expect(emailMailto('', "Hi! ('role')*", 'text')).toBe('mailto:?subject=Hi%21%20%28%27role%27%29%2A&body=text');
    const f = fixture(changed()); render(<ApplicationEmail {...f.props} />); await ready();
    fireEvent.change(screen.getByLabelText('Email subject'), { target: { value: 'Résumé & role?' } });
    expect(screen.getByRole('link', { name: 'Open in mail app' })).toHaveAttribute('href', 'mailto:typed@example.test?subject=R%C3%A9sum%C3%A9%20%26%20role%3F&body=Dear%20Hiring%20team%2C%0D%0A%0D%0AMaintained%20Linux%20checks.');
  });

  it('permits exactly 2,000 encoded characters, disables longer links and never shortens copied text', async () => {
    const f = fixture(); render(<ApplicationEmail {...f.props} />); await ready();
    const overhead = emailMailto('', email.current_revision.provenance_snapshot.email!.subject, '\n\nMaintained Linux checks.').length;
    const body = 'x'.repeat(2000 - overhead);
    fireEvent.change(screen.getByLabelText('Email body', { exact: true }), { target: { value: body } });
    expect(screen.getByRole('link', { name: 'Open in mail app' }).getAttribute('href')).toHaveLength(2000);
    fireEvent.change(screen.getByLabelText('Email body', { exact: true }), { target: { value: body + 'é' } });
    expect(screen.getByRole('link', { name: 'Open in mail app' })).toHaveAttribute('aria-disabled', 'true'); expect(screen.getByRole('link', { name: 'Open in mail app' })).not.toHaveAttribute('href');
    expect(screen.getByText(/Nothing has been shortened/)).toBeVisible();
    const writeText = vi.fn().mockResolvedValue(undefined); Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
    await userEvent.click(screen.getByRole('button', { name: 'Copy body' })); expect(writeText).toHaveBeenCalledWith(body + 'é\n\nMaintained Linux checks.');
  });

  it('offers current résumé/letter PDF downloads only, with no send, mark-sent or attach control', async () => {
    const f = fixture(); render(<ApplicationEmail {...f.props} documents={[...attachments, { ...email, artifacts: [{ id: 'not-printable', format: 'pdf', revision_id: email.current_revision.id }] }]} />); await ready();
    const pdfs = within(screen.getByRole('region', { name: 'Email PDF attachments' }));
    expect(pdfs.getByRole('link', { name: 'Download résumé PDF' })).toHaveAttribute('href', '/api/v1/artifacts/resume-pdf');
    expect(pdfs.getByRole('link', { name: 'Download cover letter PDF' })).toHaveAttribute('download'); expect(pdfs.getAllByRole('link')).toHaveLength(2);
    expect(screen.queryByRole('link', { name: /email PDF/i })).not.toBeInTheDocument(); expect(screen.queryByRole('button', { name: /send|mark.*sent|attach/i })).not.toBeInTheDocument();
  });

  it('keeps content after a failed rewrite and retries a due task with its original version', async () => {
    const f = fixture(); render(<ApplicationEmail {...f.props} />); await ready(); await rewrite();
    await act(async () => f.poll.resolve({ ...workflow, version: 3, state: 'retryable_failure', retryable: true, last_error_code: 'model_unavailable' }));
    const retry = await screen.findByRole('button', { name: 'Retry email writing' }); await waitFor(() => expect(retry).toBeEnabled());
    expect(screen.getByLabelText('Email body', { exact: true })).toHaveValue('Dear Hiring team,'); expect(screen.getByText(/Your existing email is kept/)).toBeVisible();
    await userEvent.click(retry); expect(f.request).toHaveBeenCalledWith('/workflows/writing-1/retry', 'POST', {}, 3, expect.any(String), expect.any(AbortSignal));
  });

  it('keeps an empty state after failed drafting and permits a fresh request after terminal failure', async () => {
    const f = fixture(null); render(<ApplicationEmail {...f.props} />);
    const draft = await screen.findByRole('button', { name: 'Draft application email' }); await waitFor(() => expect(draft).toBeEnabled()); await userEvent.click(draft);
    await act(async () => f.poll.resolve({ ...workflow, state: 'permanent_failure', last_error_code: 'model_output_limit' }));
    const retry = await screen.findByRole('button', { name: 'Retry email writing' }); await waitFor(() => expect(retry).toBeEnabled()); await userEvent.click(retry);
    expect(f.draft).toHaveBeenCalledTimes(2); expect(screen.queryByLabelText('Email subject')).not.toBeInTheDocument();
  });

  it('requires renewed replacement confirmation to retry a terminal failed rewrite', async () => {
    const f = fixture(); render(<ApplicationEmail {...f.props} />); await ready(); await rewrite();
    await act(async () => f.poll.resolve({ ...workflow, state: 'permanent_failure' }));
    const retry = await screen.findByRole('button', { name: 'Retry email writing' }); expect(retry).toBeDisabled();
    await userEvent.click(screen.getByRole('checkbox', { name: 'I want to replace the email subject and body' })); await userEvent.click(retry); expect(f.rewrite).toHaveBeenCalledTimes(2);
  });

  it('respects future retry time, attempt/deadline limits and unknown delivery', async () => {
    const f = fixture(); render(<ApplicationEmail {...f.props} />); await ready(); await rewrite();
    await act(async () => f.poll.resolve({ ...workflow, state: 'retryable_failure', retryable: true, remaining_attempts: 0, next_attempt_at: '2031-01-01T00:00:00Z', delivery_state: 'unknown' }));
    expect(await screen.findByRole('button', { name: 'Retry email writing' })).toBeDisabled(); expect(f.request.mock.calls.some(call => call[0].endsWith('/retry'))).toBe(false);
  });

  it('retains the exact save key across an uncertain failure and exposes disabled archive writing', async () => {
    const f = fixture(); f.save.mockRejectedValueOnce(new TypeError('offline')); const view = render(<ApplicationEmail {...f.props} />); await ready();
    fireEvent.change(screen.getByLabelText('Email subject'), { target: { value: 'Retained attempt' } }); await userEvent.click(screen.getByRole('button', { name: 'Save email changes' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Retry or copy'); await userEvent.click(screen.getByRole('button', { name: 'Save email changes' }));
    await waitFor(() => expect(f.save).toHaveBeenCalledTimes(2)); expect(f.save.mock.calls[0]?.slice(0, 4)).toEqual(f.save.mock.calls[1]?.slice(0, 4));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save email changes' })).toBeDisabled()); view.rerender(<ApplicationEmail {...f.props} archived />);
    expect(screen.getByRole('checkbox', { name: 'I want to replace the email subject and body' })).toBeDisabled(); expect(screen.getByRole('button', { name: 'Copy body' })).toBeEnabled();
  });
});

describe('email review regressions', () => {
  it.each([false, true])('keeps unsaved edits savable after unchanged reload (version-only 412: %s)', async bumped => {
    const f = fixture(); render(<ApplicationEmail {...f.props} />); await ready();
    fireEvent.change(screen.getByLabelText('Email recipient'), { target: { value: 'owner@example.test' } });
    fireEvent.change(screen.getByLabelText('Email subject'), { target: { value: 'Keep this subject' } });
    fireEvent.change(screen.getByLabelText('Email body', { exact: true }), { target: { value: 'Keep this body' } });
    if (bumped) {
      f.save.mockRejectedValueOnce(new ApiError(412, 'version_conflict'));
      await userEvent.click(screen.getByRole('button', { name: 'Save email changes' }));
      expect(await screen.findByRole('alert')).toHaveTextContent('Reload email to compare');
      f.store({ ...email, version: 8 });
    }
    await userEvent.click(screen.getByRole('button', { name: 'Reload email' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save email changes' })).toBeEnabled());
    expect(screen.queryByRole('region', { name: 'Saved email comparison' })).not.toBeInTheDocument();
    expect(screen.getByLabelText('Email recipient')).toHaveValue('owner@example.test');
    expect(screen.getByLabelText('Email subject')).toHaveValue('Keep this subject');
    expect(screen.getByLabelText('Email body', { exact: true })).toHaveValue('Keep this body');
    expect(f.save).toHaveBeenCalledTimes(bumped ? 1 : 0);
    await userEvent.click(screen.getByRole('button', { name: 'Save email changes' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save email changes' })).toBeDisabled());
    expect(f.save.mock.calls.at(-1)?.[2]).toBe(bumped ? 8 : 2);
    expect(screen.getByLabelText('Email subject')).toHaveValue('Keep this subject');
  });

  it('reads the current application version before drafting after another workspace changes it', async () => {
    const f = fixture(null); render(<ApplicationEmail {...f.props} />);
    const button = await screen.findByRole('button', { name: 'Draft application email' }); await waitFor(() => expect(button).toBeEnabled());
    f.applicationVersion(12); await userEvent.click(button);
    expect(f.request).toHaveBeenCalledWith('/applications/application-1/documents', 'GET', undefined, undefined, false, expect.any(AbortSignal));
    expect(f.draft).toHaveBeenCalledWith('application-1', 12, expect.any(String), expect.any(AbortSignal));
    expect(screen.getByRole('status', { name: 'Email writing progress' })).toHaveTextContent('pending');
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('refreshes only the version after a version-only undo 412 and requires an explicit retry', async () => {
    const original = changed(); const f = fixture(original); f.restore.mockRejectedValueOnce(new ApiError(412, 'version_conflict'));
    render(<ApplicationEmail {...f.props} />); await ready(); f.store({ ...original, version: 8 });
    await userEvent.click(screen.getByRole('button', { name: 'Undo email rewrite' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('its words are unchanged');
    expect(screen.getByRole('button', { name: 'Undo email rewrite' })).toBeEnabled(); expect(f.restore).toHaveBeenCalledTimes(1);
    f.restore.mockResolvedValueOnce({ ...email, version: 9 });
    await userEvent.click(screen.getByRole('button', { name: 'Undo email rewrite' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Undo email rewrite' })).not.toBeInTheDocument());
    expect(f.restore.mock.calls.at(-1)?.[2]).toBe(8);
  });

  it('clears a previous copied notice when a later clipboard write fails', async () => {
    const f = fixture(); render(<ApplicationEmail {...f.props} />); await ready();
    const writeText = vi.fn().mockResolvedValueOnce(undefined).mockRejectedValueOnce(new Error('permission denied'));
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
    await userEvent.click(screen.getByRole('button', { name: 'Copy subject' }));
    expect(screen.getByRole('status', { name: 'Email notice' })).toHaveTextContent('Email subject copied.');
    await userEvent.click(screen.getByRole('button', { name: 'Copy body' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Clipboard copy failed');
    expect(screen.queryByText('Email subject copied.')).not.toBeInTheDocument();
  });
});
