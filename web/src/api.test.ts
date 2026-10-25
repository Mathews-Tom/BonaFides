// SPDX-License-Identifier: MIT OR Apache-2.0
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Api, ApiError } from './api';
afterEach(() => vi.unstubAllGlobals());
describe('same-origin API transport', () => {
  it('does not turn a failed network response into successful data', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 'version_conflict' }), { status: 412 })));
    await expect(new Api().request('/profile', 'PUT', {}, 1)).rejects.toEqual(new ApiError(412, 'version_conflict'));
  });
});
