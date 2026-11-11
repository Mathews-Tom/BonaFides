// SPDX-License-Identifier: MIT OR Apache-2.0
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { Operations } from './Operations';
import { Api, ApiError, type OperationsHealth, type UpdateState } from './api';

const base: UpdateState = { enabled: false, version: 1, last_checked_at: null, outcome: 'never', latest_version: null, latest_url: null, dismissed_version: null, notice: false, error_code: null };
const release: UpdateState = { ...base, enabled: true, outcome: 'newer', latest_version: '0.7.0', latest_url: 'https://github.com/Mathews-Tom/BonaFides/releases/tag/v0.7.0', notice: true };
function server(initial = base) {
  const api = new Api();
  const state: OperationsHealth = { version: '0.6.0', schema_version: 5, database: 'ready', updates: initial };
  const request = vi.spyOn(api, 'request').mockImplementation(async <T,>(path: string, method = 'GET', body?: unknown): Promise<T> => {
    if (path === '/operations') return { ...state } as T;
    if (path === '/operations/updates' && method === 'PUT') {
      const preference = body as { enabled: boolean }; // Synthetic request from the typed API helper.
      state.updates = { ...state.updates, enabled: preference.enabled, version: state.updates.version + 1 };
    } else if (path === '/operations/updates/dismiss') {
      const dismissal = body as { version: string }; // Synthetic request from the typed API helper.
      state.updates = { ...state.updates, dismissed_version: dismissal.version, notice: false };
    } else if (path !== '/operations/updates/check') throw new Error(path);
    return state.updates as T;
  });
  return { api, state, request };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  // This frontend targets ES2022, before Promise.withResolvers.
  let reject!: (failure: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('loads only on Settings admission and requires opt-in before manual checks', async () => {
  const { api, request } = server();
  const { rerender } = render(<Operations api={api} active={false} disabled={false} />);
  expect(request).not.toHaveBeenCalled();
  rerender(<Operations api={api} active disabled={false} />);
  expect(await screen.findByLabelText('Enable release checks')).not.toBeChecked();
  expect(screen.getByRole('button', { name: 'Check for updates' })).toBeDisabled();
  expect(request.mock.calls.map(call => call[0])).toEqual(['/operations']);
  fireEvent.click(screen.getByLabelText('Enable release checks'));
  await waitFor(() => expect(screen.getByLabelText('Enable release checks')).toBeChecked());
  expect(screen.getByRole('button', { name: 'Check for updates' })).toBeEnabled();
});

it('automatically checks an enabled installation only once across Settings reopenings', async () => {
  const { api, request } = server({ ...base, enabled: true });
  const { rerender } = render(<Operations api={api} active disabled={false} />);
  await waitFor(() => expect(request).toHaveBeenCalledTimes(2));
  rerender(<Operations api={api} active={false} disabled={false} />);
  rerender(<Operations api={api} active disabled={false} />);
  await waitFor(() => expect(request).toHaveBeenCalledTimes(3));
  expect(request.mock.calls.filter(call => call[0].endsWith('/check'))).toHaveLength(1);
});

it('requires confirmed exact dismissal and retains a later release after a conflict', async () => {
  const { api, state, request } = server(release);
  render(<Operations api={api} active disabled={false} />);
  await waitFor(() => expect(request).toHaveBeenCalledTimes(2));
  const pending = deferred<UpdateState>();
  vi.spyOn(api, 'dismissUpdate').mockReturnValueOnce(pending.promise);
  fireEvent.click(screen.getByRole('button', { name: 'Dismiss update' }));
  expect(api.dismissUpdate).toHaveBeenCalledWith('0.7.0', expect.any(AbortSignal));
  expect(screen.getByRole('button', { name: 'Dismiss update' })).toBeDisabled();
  state.updates = { ...release, latest_version: '0.8.0', latest_url: 'https://github.com/Mathews-Tom/BonaFides/releases/tag/v0.8.0' };
  await act(async () => pending.reject(new ApiError(409, 'updates_version_conflict')));
  expect(await screen.findByRole('alert')).toBeInTheDocument();
  expect(screen.getByRole('link')).toHaveAttribute('href', state.updates.latest_url);
  expect(screen.getByRole('button', { name: 'Dismiss update' })).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: 'Dismiss update' }));
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Dismiss update' })).not.toBeInTheDocument());
});

it.each(['resolve', 'reject'] as const)('fences a late check %s after confirmed disable', async settlement => {
  const { api, state } = server({ ...base, enabled: true });
  const pending = deferred<UpdateState>();
  vi.spyOn(api, 'checkUpdates').mockReturnValueOnce(pending.promise);
  render(<Operations api={api} active disabled={false} />);
  await waitFor(() => expect(api.checkUpdates).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByLabelText('Enable release checks'));
  await waitFor(() => expect(screen.getByLabelText('Enable release checks')).not.toBeChecked());
  await act(async () => { if (settlement === 'resolve') pending.resolve(release); else pending.reject(new Error('late')); });
  expect(state.updates.version).toBe(2);
  expect(screen.getByLabelText('Enable release checks')).not.toBeChecked();
  expect(screen.queryByRole('link')).not.toBeInTheDocument();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

it('reloads rejected preferences without optimistic enabled state', async () => {
  const { api, request } = server();
  render(<Operations api={api} active disabled={false} />);
  await screen.findByLabelText('Enable release checks');
  const pending = deferred<UpdateState>();
  vi.spyOn(api, 'setUpdatePreference').mockReturnValueOnce(pending.promise);
  fireEvent.click(screen.getByLabelText('Enable release checks'));
  expect(screen.getByLabelText('Enable release checks')).not.toBeChecked();
  await act(async () => pending.reject(new ApiError(409, 'updates_version_conflict')));
  await waitFor(() => expect(request).toHaveBeenCalledTimes(2));
  expect(screen.getByLabelText('Enable release checks')).not.toBeChecked();
  expect(screen.getByRole('alert')).toBeInTheDocument();
});

it('does not publish a prior API lifecycle or accept a lower preference revision', async () => {
  const old = server(); const next = server({ ...base, version: 3 });
  const pending = deferred<OperationsHealth>();
  vi.spyOn(old.api, 'getOperations').mockReturnValueOnce(pending.promise);
  const { rerender } = render(<Operations api={old.api} active disabled={false} />);
  rerender(<Operations api={next.api} active disabled={false} />);
  await screen.findByLabelText('Enable release checks');
  await act(async () => pending.resolve({ ...old.state, updates: release }));
  expect(screen.getByLabelText('Enable release checks')).not.toBeChecked();
  vi.spyOn(next.api, 'setUpdatePreference').mockResolvedValueOnce({ ...base, enabled: true, version: 2 });
  fireEvent.click(screen.getByLabelText('Enable release checks'));
  await waitFor(() => expect(screen.getByLabelText('Enable release checks')).toBeEnabled());
  expect(screen.getByLabelText('Enable release checks')).not.toBeChecked();
});

it.each(['current', 'no_release', 'unknown'] as const)('does not manufacture a release link for %s', async outcome => {
  const { api, request } = server({ ...base, enabled: true, outcome });
  render(<Operations api={api} active disabled={false} />);
  await waitFor(() => expect(request).toHaveBeenCalledTimes(2));
  expect(screen.queryByRole('link')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Dismiss update' })).not.toBeInTheDocument();
});

it('does not show readiness when the health endpoint fails', async () => {
  const { api } = server();
  vi.spyOn(api, 'getOperations').mockRejectedValueOnce(new ApiError(503, 'database_unavailable'));
  render(<Operations api={api} active disabled={false} />);
  await screen.findByRole('alert');
  expect(screen.queryByRole('definition')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Enable release checks')).not.toBeInTheDocument();
});

it('withholds readiness while the connection is paused and until fresh health succeeds', async () => {
  const { api, state } = server();
  const { rerender } = render(<Operations api={api} active disabled={false} />);
  await screen.findByText('Ready');
  rerender(<Operations api={api} active disabled />);
  expect(screen.queryByText('Ready')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Refresh operations health' })).toBeDisabled();
  const pending = deferred<OperationsHealth>();
  vi.spyOn(api, 'getOperations').mockReturnValueOnce(pending.promise);
  rerender(<Operations api={api} active disabled={false} />);
  expect(screen.queryByText('Ready')).not.toBeInTheDocument();
  await act(async () => pending.resolve(state));
  expect(screen.getByText('Ready')).toBeInTheDocument();
});

it('retains the recognized release after an indeterminate check and fences a closed Settings request', async () => {
  const { api, request } = server({ ...release, outcome: 'unknown', error_code: 'release_unavailable' });
  const { rerender } = render(<Operations api={api} active disabled={false} />);
  await waitFor(() => expect(request).toHaveBeenCalledTimes(2));
  expect(screen.getByRole('link')).toHaveAttribute('href', release.latest_url);
  expect(screen.getByRole('button', { name: 'Dismiss update' })).toBeEnabled();
  const pending = deferred<UpdateState>();
  vi.spyOn(api, 'checkUpdates').mockReturnValueOnce(pending.promise);
  fireEvent.click(screen.getByRole('button', { name: 'Check for updates' }));
  rerender(<Operations api={api} active={false} disabled={false} />);
  await act(async () => pending.resolve({ ...base, enabled: true, outcome: 'current' }));
  expect(screen.getByRole('link')).toHaveAttribute('href', release.latest_url);
});
