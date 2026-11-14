// SPDX-License-Identifier: MIT OR Apache-2.0
import { useState } from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { Api, ApiError, type ApplicationConversation, type ConversationTurn, type DocumentBlock, type Workflow } from './api';
import { Conversation, type ConversationDraft } from './Conversation';

const turn: ConversationTurn = {
  id: 'turn-1', application_id: 'application-1', created_at: '2026-11-14T12:00:00Z', request_text: 'Help me review this application.',
  reply: { text: 'Review the saved evidence before approving your documents.', declined: false, applied: [], skipped: [], tones: [], revisions: [] },
  undone_at: null, workflow_run_id: null, model_run_id: null, historical: false, workflow_state: null, can_undo: false,
};
const view: ApplicationConversation = { application_id: turn.application_id, application_version: 7, context_sha256: 'a'.repeat(64), turns: [], documents: [], active_workflow_id: null, route_status: { status: 'qualified', reason: 'Synthetic fixture route', identity: null } };
const unit = { document_id: 'resume', section_key: 'experience', block_key: 'block-1' };
const documentView: ApplicationConversation = { ...view, documents: [
  { document_id: 'resume', kind: 'resume', revision_id: 'resume-revision', version: 3, tone: 'professional', units: [{ ...unit, heading: 'Experience', text: 'Saved résumé wording.', previous_sha256: 'c'.repeat(64) }] },
  { document_id: 'letter', kind: 'cover_letter', revision_id: 'letter-revision', version: 2, tone: 'professional', units: [{ ...unit, document_id: 'letter', heading: 'Letter', text: 'Saved letter wording.', previous_sha256: 'd'.repeat(64) }] },
] };
const block: DocumentBlock = { block_key: unit.block_key, text: 'Before wording.', claim_kind: 'intent', claim_revision_ids: [], evidence_revision_ids: [] };
const editedTurn: ConversationTurn = { ...turn, can_undo: true, reply: { ...turn.reply!, applied: [{ ...unit, before: block, after: { ...block, text: 'After wording.' } }], skipped: [{ ...unit, document_id: 'removed', reason: 'target_changed' }], tones: [{ document_id: 'resume', before: 'professional', after: 'concise' }] } };
const workflow: Workflow = {
  id: 'refinement-1', version: 2, state: 'pending', mode: 'document_conversation', attempt: 0, max_attempts: 3, next_attempt_at: null, last_error_code: null, result: null,
  created_at: turn.created_at, updated_at: turn.created_at, deadline_at: '2030-01-01T00:00:00Z', cancel_requested_at: null, lease_expires_at: null, last_heartbeat_at: null, queue_age_seconds: 0,
  remaining_attempts: 3, cancelable: true, retryable: false, retry_block_reason: null, delivery_state: 'not_attempted',
  cancellation: { requested: false, local_fence_settled: false, provider_stopped: null, completed_effects_preserved: true, reason: null },
  usage: { status: 'not_applicable', model_runs: 0, inference_post_attempts: 0, inference_post_limit: null, input_tokens: null, output_tokens: null, token_budget: 0, tokens_used: 0, cost_budget_microunits: 0, cost_used_microunits: null, currency: 'USD' },
};
function fixture(archived = false, initialDraft: ConversationDraft = { message: '' }) {
  const api = new Api();
  const get = vi.spyOn(api, 'getConversation').mockResolvedValue(view);
  const send = vi.spyOn(api, 'sendConversation').mockResolvedValue({ turn, workflow: null, application_version: 8, context_sha256: 'b'.repeat(64) });
  const undo = vi.spyOn(api, 'undoConversation').mockResolvedValue({ turn: { ...editedTurn, undone_at: '2026-11-14T14:00:00Z', can_undo: false } });
  const refreshDocuments = vi.fn();
  const request = vi.spyOn(api, 'request').mockResolvedValue(workflow);
  function Workspace() {
    const [draft, setDraft] = useState<ConversationDraft>(initialDraft);
    return <Conversation api={api} applicationId={view.application_id} disabled={false} archived={archived} dirtyDocument={true} draft={draft} onDraft={setDraft} onBusy={() => {}} onSaved={() => {}} onRefreshDocuments={refreshDocuments} />;
  }
  return { get, send, undo, request, refreshDocuments, Workspace };
}
async function ready() {
  const refresh = await screen.findByRole('button', { name: 'Refresh conversation' });
  await waitFor(() => expect(refresh).toBeEnabled());
  return screen.getByLabelText('Conversation message');
}

describe('persistent local application conversation', () => {
  it('shows oldest-first saved and imported history, then saves trimmed local guidance without model controls', async () => {
    const user = userEvent.setup(); const { get, send, Workspace } = fixture();
    get.mockResolvedValue({ ...view, turns: [
      { ...turn, id: 'newer', created_at: '2026-11-14T13:00:00Z', request_text: 'Newer request', reply: null, historical: true, workflow_run_id: 'historical-workflow', model_run_id: 'historical-model' },
      { ...turn, id: 'older', request_text: 'Older request' },
    ] });
    render(<Workspace />); const input = await ready();
    const history = screen.getByRole('list', { name: 'Saved conversation' });
    const items = within(history).getAllByRole('listitem');
    expect(items[0]).toHaveTextContent('Older request'); expect(items[1]).toHaveTextContent('Newer request');
    expect(screen.getByText('Imported history, shown for reference only.')).toBeVisible();
    expect(screen.getByText('No saved reply is available.')).toBeVisible();
    expect(screen.queryByRole('button', { name: /undo|retry.*task|cancel.*task/i })).not.toBeInTheDocument();
    await user.type(input, '  Help me review this application.  ');
    await user.click(screen.getByRole('button', { name: 'Send conversation message' }));
    await waitFor(() => expect(input).toHaveValue(''));
    expect(send).toHaveBeenCalledWith('application-1', { message: turn.request_text, context_sha256: view.context_sha256, document_ids: [], units: [] }, 7, expect.any(String), expect.any(AbortSignal));
    expect(screen.getByText('Local guidance saved. No model was called. No document changes were applied.')).toBeVisible();
    expect(screen.getByText(/Your unsaved document text is retained/)).toBeVisible();
    send.mockResolvedValueOnce({ turn: { ...turn, id: 'turn-2', request_text: 'Another request' }, workflow: null, application_version: 9, context_sha256: 'c'.repeat(64) });
    await user.type(input, 'Another request');
    await user.click(screen.getByRole('button', { name: 'Send conversation message' }));
    await waitFor(() => expect(input).toHaveValue(''));
    expect(send).toHaveBeenNthCalledWith(2, 'application-1', { message: 'Another request', context_sha256: 'b'.repeat(64), document_ids: [], units: [] }, 8, expect.any(String), expect.any(AbortSignal));
    expect(within(history).getAllByRole('listitem')).toHaveLength(4);
    expect(get).toHaveBeenCalledTimes(1);
  });

  it('retains the draft and exact idempotency key after an uncertain failure', async () => {
    const user = userEvent.setup(); const { get, send, Workspace } = fixture();
    send.mockRejectedValueOnce(new ApiError(502, 'conversation_unavailable'));
    render(<Workspace />); const input = await ready();
    await user.type(input, turn.request_text); await user.click(screen.getByRole('button', { name: 'Send conversation message' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('conversation_unavailable'); expect(input).toHaveValue(turn.request_text);
    get.mockResolvedValueOnce({ ...view, application_version: 8, context_sha256: 'b'.repeat(64), turns: [turn] });
    await user.click(screen.getByRole('button', { name: 'Refresh conversation' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Send conversation message' })).toBeEnabled());
    await user.click(screen.getByRole('button', { name: 'Send conversation message' }));
    await waitFor(() => expect(input).toHaveValue(''));
    expect(send.mock.calls[1]?.slice(0, 4)).toEqual(send.mock.calls[0]?.slice(0, 4));
    expect(within(screen.getByRole('list', { name: 'Saved conversation' })).getAllByRole('listitem')).toHaveLength(1);
  });

  it.each([new ApiError(412, 'version_conflict'), new ApiError(409, 'conversation_context_changed')])('requires explicit refresh and resubmission for stale context (%s)', async failure => {
    const user = userEvent.setup(); const { get, send, Workspace } = fixture();
    send.mockRejectedValueOnce(failure); render(<Workspace />); const input = await ready();
    await user.type(input, turn.request_text); await user.click(screen.getByRole('button', { name: 'Send conversation message' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Refresh the conversation');
    expect(input).toHaveValue(turn.request_text); expect(screen.getByRole('button', { name: 'Send conversation message' })).toBeDisabled();
    expect(get).toHaveBeenCalledTimes(1);
    get.mockResolvedValueOnce({ ...view, application_version: 8, context_sha256: 'b'.repeat(64) });
    await user.click(screen.getByRole('button', { name: 'Refresh conversation' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Send conversation message' })).toBeEnabled());
    expect(send).toHaveBeenCalledTimes(1); expect(input).toHaveValue(turn.request_text);
    await user.click(screen.getByRole('button', { name: 'Send conversation message' }));
    await waitFor(() => expect(input).toHaveValue(''));
    expect(send.mock.calls[1]?.[1]).toEqual({ message: turn.request_text, context_sha256: 'b'.repeat(64), document_ids: [], units: [] });
    expect(send.mock.calls[1]?.[2]).toBe(8); expect(send.mock.calls[1]?.[3]).not.toBe(send.mock.calls[0]?.[3]);
  });

  it('enforces trimmed nonempty messages and the 2,000 character maximum', async () => {
    const { send, Workspace } = fixture(); render(<Workspace />); const input = await ready();
    const button = screen.getByRole('button', { name: 'Send conversation message' });
    expect(input).not.toHaveAttribute('maxlength');
    fireEvent.change(input, { target: { value: '   ' } }); expect(button).toBeDisabled();
    fireEvent.change(input, { target: { value: 'x'.repeat(2000) + '😀' } }); expect(button).toBeDisabled();
    fireEvent.change(input, { target: { value: 'x'.repeat(1999) + '😀' } }); expect(button).toBeEnabled();
    fireEvent.click(button); await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    expect([...(send.mock.calls[0]?.[1].message ?? '')]).toHaveLength(2000);
  });

  it('loads archived history read-only and recovers a failed load through explicit refresh', async () => {
    const user = userEvent.setup(); const { get, send, Workspace } = fixture(true);
    get.mockResolvedValue({ ...view, turns: [turn] }).mockRejectedValueOnce(new ApiError(503, 'conversation_history_unavailable')); render(<Workspace />); await ready();
    expect(screen.getByRole('alert')).toHaveTextContent('conversation_history_unavailable');
    expect(screen.getByLabelText('Conversation message')).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Refresh conversation' }));
    expect(await screen.findByRole('list', { name: 'Saved conversation' })).toBeVisible();
    expect(within(screen.getByRole('list', { name: 'Saved conversation' })).getByText(turn.request_text)).toBeVisible();
    expect(screen.getByText(turn.reply!.text)).toBeVisible();
    expect(screen.getByRole('button', { name: 'Send conversation message' })).toBeDisabled();
    expect(screen.getByText('Archived application conversation is read-only.')).toBeVisible(); expect(send).not.toHaveBeenCalled();
  });

  it('routes get/send/undo through exact encoded endpoints with version and request key', async () => {
    const api = new Api(); const signal = new AbortController().signal;
    const request = vi.spyOn(api, 'request').mockResolvedValue({ turn, application_version: 8, context_sha256: 'b'.repeat(64) });
    await api.getConversation('application/one', signal);
    const body = { message: turn.request_text, context_sha256: view.context_sha256, document_ids: ['resume'], units: [unit] };
    await api.sendConversation('application/one', body, 7, 'exact-request', signal);
    await api.undoConversation('application/one', 'turn/one', 8, 'exact-undo', signal);
    expect(request.mock.calls).toEqual([
      ['/applications/application%2Fone/conversation', 'GET', undefined, undefined, false, signal],
      ['/applications/application%2Fone/conversation', 'POST', body, 7, 'exact-request', signal],
      ['/applications/application%2Fone/conversation/turns/turn%2Fone/undo', 'POST', {}, 8, 'exact-undo', signal],
    ]);
  });

  it.each(['letter', 'resume'])('preserves a selected %s part unless its own document is deselected', async selectedDocument => {
    const user = userEvent.setup(); const { get, send, Workspace } = fixture();
    get.mockResolvedValue(documentView); render(<Workspace />); const input = await ready();
    expect(screen.getByRole('checkbox', { name: 'Résumé' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'Cover letter' })).toBeChecked();
    await user.selectOptions(screen.getByLabelText('Optional saved part'), JSON.stringify([selectedDocument, unit.section_key, unit.block_key]));
    await user.click(screen.getByRole('checkbox', { name: 'Cover letter' }));
    expect(screen.getByLabelText('Optional saved part')).toHaveValue(selectedDocument === 'letter' ? '' : JSON.stringify(Object.values(unit)));
    if (selectedDocument === 'letter') await user.selectOptions(screen.getByLabelText('Optional saved part'), JSON.stringify(Object.values(unit)));
    expect(screen.getByText('Saved résumé wording.')).toBeVisible();
    await user.type(input, 'Shorten this part.'); await user.click(screen.getByRole('button', { name: 'Send conversation message' }));
    await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    expect(send.mock.calls[0]?.[1]).toEqual({ message: 'Shorten this part.', context_sha256: view.context_sha256, document_ids: ['resume'], units: [unit] });
  });

  it('coordinates qualified defaults but sends local guidance by default without a qualified route', async () => {
    const user = userEvent.setup(); const { get, send, Workspace } = fixture();
    get.mockResolvedValue(documentView); render(<Workspace />); const input = await ready();
    await user.type(input, 'Coordinate the résumé and letter.'); await user.click(screen.getByRole('button', { name: 'Send conversation message' }));
    await waitFor(() => expect(input).toHaveValue(''));
    expect(send.mock.calls[0]?.[1]).toMatchObject({ document_ids: ['resume', 'letter'], units: [] });
    get.mockResolvedValueOnce({ ...documentView, route_status: { status: 'unqualified', reason: 'No refine_documents receipt', identity: null } });
    await user.click(screen.getByRole('button', { name: 'Refresh conversation' })); await user.type(input, 'Local notes.');
    expect(screen.getByRole('button', { name: 'Send conversation message' })).toBeEnabled();
    expect(screen.getByRole('checkbox', { name: 'Résumé' })).not.toBeChecked(); expect(screen.getByRole('checkbox', { name: 'Cover letter' })).not.toBeChecked();
    await user.click(screen.getByRole('button', { name: 'Send conversation message' }));
    await waitFor(() => expect(send).toHaveBeenCalledTimes(2));
    expect(send.mock.calls[1]?.[1]).toMatchObject({ document_ids: [], units: [] });
  });

  it('retains an exact scoped uncertain-send envelope despite changed text, selection, context and active status', async () => {
    const user = userEvent.setup(); const { get, send, Workspace } = fixture();
    get.mockResolvedValue(documentView); send.mockRejectedValueOnce(new ApiError(502, 'conversation_unavailable'));
    render(<Workspace />); const input = await ready();
    await user.type(input, 'Original request.'); await user.click(screen.getByRole('button', { name: 'Send conversation message' }));
    await screen.findByRole('alert');
    await user.click(screen.getByRole('checkbox', { name: 'Cover letter' }));
    fireEvent.change(input, { target: { value: 'Keep my new unsent request.' } });
    get.mockResolvedValueOnce({ ...documentView, application_version: 9, context_sha256: 'e'.repeat(64), active_workflow_id: workflow.id, route_status: { ...view.route_status, status: 'unqualified' } });
    await user.click(screen.getByRole('button', { name: 'Refresh conversation' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Send conversation message' })).toBeEnabled());
    await user.click(screen.getByRole('button', { name: 'Send conversation message' }));
    await waitFor(() => expect(send).toHaveBeenCalledTimes(2));
    expect(send.mock.calls[1]?.slice(0, 4)).toEqual(send.mock.calls[0]?.slice(0, 4));
    expect(input).toHaveValue('Keep my new unsent request.');
  });

  it('retains a disappeared unit selection and requires a new explicit selection after refresh', async () => {
    const user = userEvent.setup(); const { get, send, Workspace } = fixture(false, { message: 'Retained request.', unit: JSON.stringify(Object.values(unit)) });
    get.mockResolvedValue(documentView); render(<Workspace />); await ready();
    get.mockResolvedValueOnce({ ...documentView, documents: [documentView.documents[1]!] });
    await user.click(screen.getByRole('button', { name: 'Refresh conversation' }));
    expect(await screen.findByRole('option', { name: /Previously selected part/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Send conversation message' })).toBeDisabled();
    expect(screen.getByLabelText('Conversation message')).toHaveValue('Retained request.'); expect(send).not.toHaveBeenCalled();
  });

  it.each([false, true])('discloses applied/skipped/tone results and undoes an exact live reply (tone-only: %s)', async toneOnly => {
    const user = userEvent.setup(); const { get, undo, refreshDocuments, Workspace } = fixture();
    const replyTurn = { ...editedTurn, reply: { ...editedTurn.reply!, applied: toneOnly ? [] : editedTurn.reply!.applied } };
    get.mockResolvedValueOnce({ ...documentView, turns: [replyTurn] }).mockResolvedValue({ ...documentView, application_version: 8, turns: [{ ...replyTurn, undone_at: '2026-11-14T14:00:00Z', can_undo: false }] });
    render(<Workspace />); await ready();
    expect(screen.getByRole('list', { name: 'Skipped units' })).toHaveTextContent('target_changed');
    expect(screen.getByRole('list', { name: 'Writing tone changes' })).toHaveTextContent('professional → concise');
    if (!toneOnly) expect(screen.getByRole('list', { name: 'Applied units' })).toHaveTextContent('Before wording.After wording.');
    await user.click(screen.getByRole('button', { name: 'Undo this reply’s document edits' }));
    expect(await screen.findByText(/Already reversed at/)).toBeVisible();
    expect(undo).toHaveBeenCalledWith(view.application_id, turn.id, 7, expect.any(String), expect.any(AbortSignal));
    expect(screen.queryByRole('button', { name: /Undo this reply/ })).not.toBeInTheDocument();
    expect(refreshDocuments).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Refresh saved documents for comparison' })); expect(refreshDocuments).toHaveBeenCalledOnce();
  });

  it.each([
    ['conversation_undo_conflict', 'Nothing was reversed'],
    ['conversation_already_undone', 'already reversed'],
    ['conversation_not_undoable', 'no reversible document edits'],
  ])('explains %s without losing the reply or local request', async (code, text) => {
    const user = userEvent.setup(); const { get, undo, Workspace } = fixture(false, { message: 'Keep my request.' });
    get.mockResolvedValue({ ...documentView, turns: [editedTurn] }); undo.mockRejectedValueOnce(new ApiError(409, code));
    render(<Workspace />); await ready(); await user.click(screen.getByRole('button', { name: /Undo this reply/ }));
    expect(await screen.findByRole('alert')).toHaveTextContent(text);
    expect(screen.getByLabelText('Conversation message')).toHaveValue('Keep my request.');
    expect(screen.getByRole('list', { name: 'Applied units' })).toBeVisible(); expect(get).toHaveBeenCalledOnce();
  });

  it('never offers undo for no-edit, declined or imported replies, and keeps archived mutations disabled', async () => {
    const { get, Workspace } = fixture(true);
    get.mockResolvedValue({ ...documentView, turns: [editedTurn, { ...turn, id: 'no-edit', can_undo: true }, { ...turn, id: 'declined', can_undo: true, reply: { ...turn.reply!, declined: true } }, { ...editedTurn, id: 'imported', historical: true }] });
    render(<Workspace />); await ready();
    expect(screen.getAllByRole('button', { name: /Undo this reply/ })).toHaveLength(1);
    expect(screen.getByRole('button', { name: /Undo this reply/ })).toBeDisabled();
    expect(screen.getByRole('checkbox', { name: 'Résumé' })).toBeDisabled();
    expect(screen.getByText('Request declined. No document text or tone changed.')).toBeVisible();
  });

  it('shows pending status then refreshes the completed reply without replacing editor text', async () => {
    const user = userEvent.setup(); const { get, send, refreshDocuments, Workspace } = fixture();
    get.mockResolvedValue(documentView);
    send.mockResolvedValueOnce({ turn: { ...turn, reply: null, workflow_run_id: workflow.id, workflow_state: 'pending' }, workflow, application_version: 8, context_sha256: 'b'.repeat(64) });
    render(<Workspace />); const input = await ready(); await user.type(input, 'Refine saved text.');
    await user.click(screen.getByRole('button', { name: 'Send conversation message' }));
    expect(await screen.findByText(/Task: pending/)).toBeVisible();
    fireEvent.change(input, { target: { value: 'Keep this next request.' } });
    expect(screen.getByRole('button', { name: 'Send conversation message' })).toBeDisabled();
    get.mockResolvedValueOnce({ ...documentView, application_version: 9, turns: [editedTurn] });
    await user.click(screen.getByRole('button', { name: 'Refresh conversation' }));
    expect(await screen.findByRole('list', { name: 'Applied units' })).toBeVisible();
    expect(screen.queryByText(/One refinement is unfinished/)).not.toBeInTheDocument();
    expect(input).toHaveValue('Keep this next request.');
    expect(screen.getByRole('button', { name: 'Send conversation message' })).toBeEnabled();
    expect(refreshDocuments).not.toHaveBeenCalled();
  });

  it('loads failed workflow status and retries the frozen request with its workflow version', async () => {
    const user = userEvent.setup(); const { get, request, Workspace } = fixture();
    const failed = { ...workflow, state: 'retryable_failure' as const, retryable: true, last_error_code: 'model_unavailable' };
    get.mockResolvedValue({ ...documentView, active_workflow_id: workflow.id, turns: [{ ...turn, reply: null, workflow_run_id: workflow.id, workflow_state: 'retryable_failure' }] });
    request.mockResolvedValue(failed); render(<Workspace />); await ready();
    expect(screen.getByText(/Task: retryable failure/)).toBeVisible(); expect(screen.getByText('Failure: model_unavailable')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Retry saved refinement request' }));
    await waitFor(() => expect(request).toHaveBeenCalledWith(`/workflows/${workflow.id}/retry`, 'POST', {}, workflow.version, expect.any(String), expect.any(AbortSignal)));
    expect(screen.getByText(/original saved request and frozen context/)).toBeVisible();
  });

  it('cancels a live task with a local-publication warning and retains status when the task lookup fails', async () => {
    const user = userEvent.setup(); const { get, request, Workspace } = fixture();
    get.mockResolvedValue({ ...documentView, active_workflow_id: workflow.id, turns: [{ ...turn, reply: null, workflow_run_id: workflow.id, workflow_state: 'running' }] });
    render(<Workspace />); await ready();
    await user.click(screen.getByRole('button', { name: 'Cancel refinement task' }));
    await waitFor(() => expect(request).toHaveBeenCalledWith(`/workflows/${workflow.id}/cancel`, 'POST', {}, workflow.version, expect.any(String), expect.any(AbortSignal)));
    expect(screen.getByText(/Local publication is fenced; provider stopping is not confirmed/)).toBeVisible();
    request.mockRejectedValueOnce(new ApiError(503, 'workflow_status_unavailable'));
    await user.click(screen.getByRole('button', { name: 'Refresh conversation' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('workflow_status_unavailable');
    expect(screen.getByText(/Task: running/)).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Cancel refinement task' })).not.toBeInTheDocument();
  });
});
