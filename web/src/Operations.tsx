// SPDX-License-Identifier: MIT OR Apache-2.0
import { useCallback, useEffect, useRef, useState } from 'react';
import { Api, errorText, type OperationsHealth, type UpdateState } from './api';

type Action = 'load' | 'check' | 'preference' | 'dismiss';
const outcomes: Record<UpdateState['outcome'], string> = {
  never: 'Updates have not been checked.', checking: 'A release check is in progress. Refresh health for its result.',
  newer: 'A newer release is available.', current: 'No newer stable release was found.',
  no_release: 'No published stable release was found.', unknown: 'The latest check could not determine release availability.',
};

export function Operations({ api, active, disabled }: { api: Api; active: boolean; disabled: boolean }) {
  const [health, setHealth] = useState<OperationsHealth | null>(null);
  const [busy, setBusy] = useState<Action | null>(null);
  const [error, setError] = useState('');
  const current = useRef<OperationsHealth | null>(null);
  const generation = useRef(0);
  const abort = useRef<AbortController | null>(null);
  const automaticConsidered = useRef(false);
  const run = useCallback(async (action: Action, value?: boolean | string): Promise<UpdateState | null> => {
    const ticket = generation.current + 1;
    generation.current = ticket;
    abort.current?.abort();
    const controller = new AbortController(); abort.current = controller;
    const isCurrent = () => ticket === generation.current;
    const accept = (result: OperationsHealth) => {
      if (!isCurrent() || result.updates.version < (current.current?.updates.version ?? -1)) return false;
      current.current = result; setHealth(result); return true;
    };
    setBusy(action); setError('');
    if (action === 'load') setHealth(null);
    try {
      let result: OperationsHealth;
      if (action === 'load') result = await api.getOperations(controller.signal);
      else {
        const before = current.current;
        if (!before) return null;
        const updates = action === 'preference' ? await api.setUpdatePreference(value === true, before.updates.version, controller.signal)
          : action === 'dismiss' ? await api.dismissUpdate(String(value), controller.signal)
          : await api.checkUpdates(value === true, controller.signal);
        result = { ...before, updates };
      }
      return accept(result) ? result.updates : null;
    } catch (failure) {
      if (!isCurrent()) return null;
      setError(errorText(failure));
      // A rejected/uncertain write is not an optimistic preference or dismissal.
      if (action !== 'load') {
        try { accept(await api.getOperations(controller.signal)); }
        catch { if (isCurrent()) setHealth(null); }
      }
      return null;
    } finally { if (isCurrent()) setBusy(null); }
  }, [api]);
  useEffect(() => {
    current.current = null; automaticConsidered.current = false; setHealth(null); setError('');
  }, [api]);
  useEffect(() => {
    if (!active || disabled) { setBusy(null); return; }
    void run('load').then(updates => {
      if (!updates || automaticConsidered.current) return;
      automaticConsidered.current = true;
      if (updates.enabled) void run('check', true);
    });
    return () => { generation.current++; abort.current?.abort(); };
  }, [active, disabled, run]);
  const updates = health?.updates;
  const blocked = disabled || !!api.writeBlock;
  return <section className="panel" aria-labelledby="operations-title">
    <h2 id="operations-title">Operations</h2>
    <p>Application version, schema and database readiness only. This does not test PDF rendering, model routes or mail delivery.</p>
    {health && <dl><dt>Application version</dt><dd>{health.version}</dd><dt>Schema version</dt><dd>{health.schema_version}</dd><dt>Database</dt><dd>{health.database === 'ready' ? 'Ready' : 'Unavailable'}</dd></dl>}
    {busy === 'load' && <p role="status">Loading operations health…</p>}
    {error && <p role="alert" className="error">{error}</p>}
    <button type="button" className="secondary" disabled={disabled || busy !== null} onClick={() => { void run('load'); }}>Refresh operations health</button>
    <h3>Release notices</h3>
    <p>Disabled by default. Opting in checks official GitHub release metadata; GitHub sees network metadata such as your public IP address, but no profile data, credentials or installation identifier is sent. Automatic checks run when Settings first opens, at most once per UTC day. Nothing is installed or downloaded.</p>
    {updates && <>
      <label><input type="checkbox" checked={updates.enabled} disabled={blocked || busy === 'load' || busy === 'preference' || busy === 'dismiss'} onChange={event => { void run('preference', event.currentTarget.checked); }} />Enable release checks</label>
      <p role="status">{busy === 'check' ? 'Checking release metadata…' : outcomes[updates.outcome]}</p>
      {updates.last_checked_at && <p>Last checked: {new Date(updates.last_checked_at).toLocaleString()}</p>}
      {updates.error_code && <p>The release check failed ({updates.error_code}). Any previously recognized release below is retained; it is not a fresh result.</p>}
      {updates.latest_version && <p>Last recognized release: {updates.latest_version}{updates.latest_url && <> · <a href={updates.latest_url} target="_blank" rel="noopener noreferrer">View release notes</a></>}{updates.dismissed_version === updates.latest_version && ' · Notice dismissed for this version.'}</p>}
      <div className="actions"><button type="button" disabled={blocked || !updates.enabled || busy !== null || updates.outcome === 'checking'} onClick={() => { void run('check', false); }}>Check for updates</button>
        {updates.notice && updates.latest_version && <button type="button" className="secondary" disabled={blocked || busy !== null} onClick={() => { void run('dismiss', updates.latest_version!); }}>Dismiss update</button>}</div>
    </>}
  </section>;
}
