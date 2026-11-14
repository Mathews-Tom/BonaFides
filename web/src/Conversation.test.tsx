// SPDX-License-Identifier: MIT OR Apache-2.0
import { useState } from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { Api, ApiError, type ApplicationConversation, type ConversationTurn } from './api';
import { Conversation, type ConversationDraft } from './Conversation';

const turn: ConversationTurn = {
  id: 'turn-1', application_id: 'application-1', created_at: '2026-11-14T12:00:00Z', request_text: 'Help me review this application.',
  reply: { text: 'Review the saved evidence before approving your documents.', declined: false, applied: [], skipped: [], tones: [], revisions: [] },
  undone_at: null, workflow_run_id: null, model_run_id: null, historical: false,
};
const view: ApplicationConversation = { application_id: turn.application_id, application_version: 7, context_sha256: 'a'.repeat(64), turns: [] };
function fixture(archived = false) {
  const api = new Api();
  const get = vi.spyOn(api, 'getConversation').mockResolvedValue(view);
  const send = vi.spyOn(api, 'sendConversation').mockResolvedValue({ turn, application_version: 8, context_sha256: 'b'.repeat(64) });
  function Workspace() {
    const [draft, setDraft] = useState<ConversationDraft>({ message: '' });
    return <Conversation api={api} applicationId={view.application_id} disabled={false} archived={archived} dirtyDocument={true} draft={draft} onDraft={setDraft} onBusy={() => {}} onSaved={() => {}} />;
  }
  return { get, send, Workspace };
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
    expect(send).toHaveBeenCalledWith('application-1', { message: turn.request_text, context_sha256: view.context_sha256 }, 7, expect.any(String), expect.any(AbortSignal));
    expect(screen.getByText('Local guidance saved. No model was called. No document changes were applied.')).toBeVisible();
    expect(screen.getByText(/Your unsaved document text is retained/)).toBeVisible();
    send.mockResolvedValueOnce({ turn: { ...turn, id: 'turn-2', request_text: 'Another request' }, application_version: 9, context_sha256: 'c'.repeat(64) });
    await user.type(input, 'Another request');
    await user.click(screen.getByRole('button', { name: 'Send conversation message' }));
    await waitFor(() => expect(input).toHaveValue(''));
    expect(send).toHaveBeenNthCalledWith(2, 'application-1', { message: 'Another request', context_sha256: 'b'.repeat(64) }, 8, expect.any(String), expect.any(AbortSignal));
    expect(within(history).getAllByRole('listitem')).toHaveLength(4);
    expect(get).toHaveBeenCalledTimes(1);
  });

  it('retains the draft and exact idempotency key after an uncertain failure', async () => {
    const user = userEvent.setup(); const { send, Workspace } = fixture();
    send.mockRejectedValueOnce(new ApiError(503, 'conversation_unavailable'));
    render(<Workspace />); const input = await ready();
    await user.type(input, turn.request_text); await user.click(screen.getByRole('button', { name: 'Send conversation message' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('conversation_unavailable'); expect(input).toHaveValue(turn.request_text);
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
    expect(send.mock.calls[1]?.[1]).toEqual({ message: turn.request_text, context_sha256: 'b'.repeat(64) });
    expect(send.mock.calls[1]?.[2]).toBe(8); expect(send.mock.calls[1]?.[3]).not.toBe(send.mock.calls[0]?.[3]);
  });

  it('enforces trimmed nonempty messages and the 2,000 character maximum', async () => {
    const { send, Workspace } = fixture(); render(<Workspace />); const input = await ready();
    const button = screen.getByRole('button', { name: 'Send conversation message' });
    expect(input).toHaveAttribute('maxlength', '2000');
    fireEvent.change(input, { target: { value: '   ' } }); expect(button).toBeDisabled();
    fireEvent.change(input, { target: { value: 'x'.repeat(2001) } }); expect(button).toBeDisabled();
    fireEvent.change(input, { target: { value: 'x'.repeat(2000) } }); expect(button).toBeEnabled();
    fireEvent.click(button); await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    expect(send.mock.calls[0]?.[1].message).toHaveLength(2000);
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

  it('routes get/send helpers through only the conversation endpoint with version and request key', async () => {
    const api = new Api(); const signal = new AbortController().signal;
    const request = vi.spyOn(api, 'request').mockResolvedValue({ turn, application_version: 8, context_sha256: 'b'.repeat(64) });
    await api.getConversation('application/one', signal);
    const body = { message: turn.request_text, context_sha256: view.context_sha256 };
    await api.sendConversation('application/one', body, 7, 'exact-request', signal);
    expect(request.mock.calls).toEqual([
      ['/applications/application%2Fone/conversation', 'GET', undefined, undefined, false, signal],
      ['/applications/application%2Fone/conversation', 'POST', body, 7, 'exact-request', signal],
    ]);
  });
});
