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
});
