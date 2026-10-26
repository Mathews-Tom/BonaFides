// SPDX-License-Identifier: MIT OR Apache-2.0
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Api, ApiError } from './api';
afterEach(() => vi.unstubAllGlobals());
describe('same-origin API transport', () => {
  it('does not turn a failed network response into successful data', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 'version_conflict' }), { status: 412 })));
    await expect(new Api().request('/profile', 'PUT', {}, 1)).rejects.toEqual(new ApiError(412, 'version_conflict'));
  });
  it('binds document writes to owner CSRF, quoted aggregate version and one explicit payload key without caching private responses', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: 'saved' }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetcher); const api = new Api(); api.csrf = 'owner-csrf';
    const body = { revision_id: 'immutable-revision', decision: 'accepted', content_sha256: 'a'.repeat(64), reviewed_block_keys: ['b-1'] };
    await api.request('/documents/draft/review', 'POST', body, 9, 'exact-payload-key');
    expect(fetcher).toHaveBeenCalledWith('/api/v1/documents/draft/review', {
      method: 'POST', credentials: 'same-origin', cache: 'no-store', body: JSON.stringify(body),
      headers: { Accept: 'application/json', 'Content-Type': 'application/json', 'X-CSRF-Token': 'owner-csrf', 'If-Match': '"9"', 'Idempotency-Key': 'exact-payload-key' },
    });
  });
  it('fences JSON and every binary mutation before transmission while preserving authenticated reads', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ maintenance_active: false }), { status: 200 }));
    vi.stubGlobal('fetch', fetcher);
    const api = new Api(); api.writeBlock = 'workspace_reconnect_required';
    const bytes = new Blob(['private buffer']);
    for (const operation of [
      () => api.request('/profile', 'PUT', { headline: 'unsaved' }, 9, 'saved-command'),
      () => api.uploadImport(bytes, 'text/plain', 9, 'import-command'),
      () => api.uploadPortable(bytes, 9, 'replacement-command'),
      () => api.upload('/applications/current/attachments', bytes, 'application/octet-stream', 9, 'attachment-command'),
    ]) await expect(operation()).rejects.toEqual(new ApiError(409, 'workspace_reconnect_required'));
    expect(fetcher).not.toHaveBeenCalled();
    await expect(api.request('/maintenance')).resolves.toEqual({ maintenance_active: false });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('reports real authentication loss and transport failure without automatically reopening writes', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ code: 'session_revoked' }), { status: 401 })).mockRejectedValueOnce(new TypeError('connection lost'));
    vi.stubGlobal('fetch', fetcher);
    const api = new Api(); const observed: unknown[] = [];
    api.onUnavailable = failure => { observed.push(failure); api.writeBlock = 'workspace_reconnect_required'; };
    await expect(api.request('/profile')).rejects.toEqual(new ApiError(401, 'session_revoked'));
    await expect(api.request('/maintenance')).rejects.toThrow('connection lost');
    expect(observed[0]).toEqual(new ApiError(401, 'session_revoked'));
    expect(observed[1]).toBeInstanceOf(TypeError);
    await expect(api.request('/profile', 'PUT', {}, 1)).rejects.toEqual(new ApiError(409, 'workspace_reconnect_required'));
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it.each([
    { failureKind: 'stream', binary: false }, { failureKind: 'invalid JSON', binary: false },
    { failureKind: 'stream', binary: true }, { failureKind: 'invalid JSON', binary: true },
  ])('fences subsequent JSON and binary writes after successful headers: $failureKind, binary=$binary', async ({ failureKind, binary }) => {
    const failure = new TypeError('response connection lost');
    const response = failureKind === 'stream'
      ? new Response(new ReadableStream<Uint8Array>({ start(controller) { controller.enqueue(new TextEncoder().encode('{"saved":')); }, pull(controller) { controller.error(failure); } }), { status: 200 })
      : new Response('{"saved":', { status: 200 });
    const fetcher = vi.fn().mockResolvedValue(response); vi.stubGlobal('fetch', fetcher);
    const api = new Api(); const observed: unknown[] = [];
    api.onUnavailable = cause => { observed.push(cause); api.writeBlock = 'workspace_reconnect_required'; };
    const body = { headline: 'retained edit' }; const bytes = new Blob(['retained binary buffer']);
    let rejected: unknown;
    try {
      if (binary) await api.upload('/applications/current/attachments', bytes, 'application/octet-stream', 9, 'retained-command-key');
      else await api.request('/profile', 'PUT', body, 9, 'retained-command-key');
    } catch (cause) { rejected = cause; }
    expect(rejected).toBeInstanceOf(failureKind === 'stream' ? TypeError : SyntaxError);
    if (failureKind === 'stream') expect(rejected).toBe(failure);
    expect(observed).toEqual([rejected]);
    for (const operation of [
      () => api.request('/profile', 'PUT', body, 9, 'retained-command-key'),
      () => api.uploadImport(bytes, 'text/plain', 9, 'retained-import-key'),
      () => api.uploadPortable(bytes, 9, 'retained-replacement-key'),
      () => api.upload('/applications/current/attachments', bytes, 'application/octet-stream', 9, 'retained-attachment-key'),
    ]) await expect(operation()).rejects.toEqual(new ApiError(409, 'workspace_reconnect_required'));
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0]?.[1]).toEqual(expect.objectContaining({
      body: binary ? bytes : JSON.stringify(body), headers: expect.objectContaining({ 'Idempotency-Key': 'retained-command-key' }),
    }));
    expect(body).toEqual({ headline: 'retained edit' });
  });

  it('does not report an intentional response-body abort as connection loss', async () => {
    const failure = new DOMException('Owner stopped request', 'AbortError');
    const response = new Response(new ReadableStream<Uint8Array>({ start(controller) { controller.error(failure); } }), { status: 200 });
    const fetcher = vi.fn().mockResolvedValueOnce(response).mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal('fetch', fetcher); const api = new Api(); const unavailable = vi.fn(); api.onUnavailable = unavailable;
    await expect(api.request('/profile', 'PUT', {}, 9, 'aborted-command')).rejects.toBe(failure);
    expect(unavailable).not.toHaveBeenCalled(); expect(api.writeBlock).toBeNull();
    await expect(api.request('/profile', 'PUT', {}, 9, 'next-explicit-command')).resolves.toBeUndefined();
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});
