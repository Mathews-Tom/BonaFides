// SPDX-License-Identifier: MIT OR Apache-2.0
import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { Api, ApiError, errorText, type Challenge, type InstallationStatus, type Profile, type Session } from './api';

type Phase = 'checking' | 'online' | 'maintenance' | 'offline' | 'expired' | 'review';
interface Observation { status: InstallationStatus; profile: Profile; owner: string }
const context = (value: InstallationStatus) => JSON.stringify([value.namespace_id, value.maintenance_epoch, value.workspace_version]);
const identity = (value: Observation) => JSON.stringify([context(value.status), value.owner, value.profile.id, value.profile.version]);
function checkedStatus(value: InstallationStatus): InstallationStatus {
  if (typeof value.namespace_id !== 'string' || !value.namespace_id || !Number.isSafeInteger(value.maintenance_epoch) || !Number.isSafeInteger(value.workspace_version)) throw new ApiError(502, 'installation_context_unavailable');
  return value;
}

export function ConnectionPanel({ api, session, profile, initialStatus, reloadDisabled, onSession, onReload, onPaused }: {
  api: Api; session: Session; profile: Profile | null; initialStatus: InstallationStatus;
  reloadDisabled: boolean; onSession: (value: Session) => void; onReload: (value: Profile) => void; onPaused: (paused: boolean) => void;
}) {
  const [phase, setPhase] = useState<Phase>('checking');
  const [status, setStatus] = useState(initialStatus);
  const [comparison, setComparison] = useState<Observation | null>(null);
  const [discard, setDiscard] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [monitor, setMonitor] = useState(0);
  const [stopped, setStopped] = useState(false);
  const current = useRef({ session, profile, reloadDisabled, onSession, onReload, onPaused });
  current.current = { session, profile, reloadDisabled, onSession, onReload, onPaused };
  const baseline = useRef({ status: initialStatus, owner: session.owner_id, profileId: profile?.id ?? null, profileVersion: profile?.version ?? null });
  const phaseRef = useRef<Phase>('checking');
  const mounted = useRef(false);
  const operation = useRef(false);
  const generation = useRef(0);
  const controllers = useRef(new Set<AbortController>());
  const transition = useCallback((value: Phase) => {
    phaseRef.current = value;
    api.writeBlock = value === 'online' ? null : 'workspace_reconnect_required';
    current.current.onPaused(value !== 'online');
    setPhase(value);
  }, [api]);
  const unavailable = useCallback((failure: unknown) => {
    if (!mounted.current) return;
    transition(failure instanceof ApiError && failure.status === 401 ? 'expired' : failure instanceof ApiError && failure.status === 503 ? 'maintenance' : failure instanceof ApiError && failure.status === 409 ? 'review' : 'offline');
    setComparison(null); setDiscard(false);
  }, [transition]);
  useEffect(() => {
    mounted.current = true;
    api.writeBlock = 'workspace_reconnect_required'; current.current.onPaused(true);
    const previous = api.onUnavailable; api.onUnavailable = unavailable;
    const pending = controllers.current;
    return () => {
      mounted.current = false;
      for (const controller of pending) controller.abort();
      api.onUnavailable = previous; api.writeBlock = 'workspace_reconnect_required';
    };
  }, [api, unavailable]);
  const request = useCallback(async <T,>(path: string, method = 'GET', body?: unknown): Promise<T> => {
    const controller = new AbortController(); controllers.current.add(controller);
    const timer = window.setTimeout(() => controller.abort(), 10000);
    try {
      const value = await api.request<T>(path, method, body, undefined, false, controller.signal);
      if (!mounted.current || controller.signal.aborted) throw new DOMException('Request stopped', 'AbortError');
      return value;
    }
    finally { clearTimeout(timer); controllers.current.delete(controller); }
  }, [api]);
  const hasProfile = !!profile;
  useEffect(() => {
    let active = true; let timer: number | undefined; let count = 0; let polling = false;
    const started = Date.now();
    async function poll() {
      if (!active || document.hidden || polling || operation.current) return;
      if (++count > 90 || Date.now() - started >= 10 * 60 * 1000) { setStopped(true); return; }
      polling = true; const epoch = generation.current;
      try {
        const value = checkedStatus(await request<InstallationStatus>('/maintenance'));
        if (!active || epoch !== generation.current || operation.current) return;
        setStatus(value);
        const retained = current.current.profile;
        if (baseline.current.profileId === null && retained) {
          baseline.current.profileId = retained.id; baseline.current.profileVersion = retained.version;
        }
        if (!retained && phaseRef.current === 'checking' && !value.maintenance_active) return;
        if (value.maintenance_active) { transition('maintenance'); setComparison(null); setDiscard(false); }
        else if (['online', 'checking'].includes(phaseRef.current) && baseline.current.owner === current.current.session.owner_id && value.namespace_id === baseline.current.status.namespace_id && value.maintenance_epoch === baseline.current.status.maintenance_epoch && retained) {
          // Ordinary versioned writes advance workspace_version; they do not replace the namespace.
          if (phaseRef.current === 'checking' && value.workspace_version !== baseline.current.status.workspace_version) transition('review');
          else {
            baseline.current = { status: value, owner: current.current.session.owner_id, profileId: retained.id, profileVersion: retained.version };
            transition('online');
          }
        } else if (phaseRef.current !== 'expired') {
          transition('review');
          setComparison(previous => previous && context(previous.status) === context(value) ? previous : null);
        }
      } catch (failure) {
        if (active && epoch === generation.current) { unavailable(failure); setError(errorText(failure)); }
      } finally {
        polling = false;
        if (active && !document.hidden) timer = window.setTimeout(() => { void poll(); }, Math.min(1000 * 2 ** Math.min(count, 4), 15000));
      }
    }
    const visibility = () => { clearTimeout(timer); if (!document.hidden) void poll(); };
    document.addEventListener('visibilitychange', visibility);
    void poll();
    return () => { active = false; clearTimeout(timer); document.removeEventListener('visibilitychange', visibility); };
  }, [request, transition, unavailable, monitor, hasProfile]);
  async function observe(): Promise<Observation> {
    const owner = current.current.session.owner_id; const csrf = api.csrf;
    const before = checkedStatus(await request<InstallationStatus>('/maintenance'));
    if (before.maintenance_active) { setStatus(before); throw new ApiError(503, 'installation_maintenance'); }
    const value = await request<Profile>('/profile');
    const after = checkedStatus(await request<InstallationStatus>('/maintenance'));
    if (after.maintenance_active || context(before) !== context(after) || owner !== current.current.session.owner_id || csrf !== api.csrf) throw new ApiError(409, 'workspace_changed_during_comparison');
    setStatus(after);
    return { status: after, profile: value, owner };
  }
  const compatible = (value: Observation) => value.owner === baseline.current.owner && value.status.namespace_id === baseline.current.status.namespace_id && value.status.workspace_version === baseline.current.status.workspace_version && value.profile.id === baseline.current.profileId && value.profile.version === baseline.current.profileVersion;
  async function run(action: () => Promise<void>) {
    if (operation.current) return;
    operation.current = true; generation.current++; setBusy(true); setError('');
    try { await action(); }
    catch (failure) { if (mounted.current) { unavailable(failure); setError(errorText(failure)); } }
    finally { operation.current = false; if (mounted.current) setBusy(false); }
  }
  async function compare() {
    await run(async () => { transition('review'); const value = await observe(); if (mounted.current) { setComparison(value); setDiscard(false); } });
  }
  async function reconnect(replace: boolean) {
    if (!comparison || (replace && (!discard || current.current.reloadDisabled))) return;
    const reviewed = comparison;
    await run(async () => {
      const latest = await observe(); if (!mounted.current) return;
      if (identity(latest) !== identity(reviewed)) { setComparison(latest); setDiscard(false); setError('The current workspace changed since comparison. Review it again; no editor was replaced and writers remain paused.'); return; }
      if (!replace && !compatible(latest)) { setComparison(latest); setDiscard(false); setError('The saved workspace differs. Preserve your text, then explicitly discard and reload before writing.'); return; }
      if (replace) current.current.onReload(latest.profile);
      baseline.current = { status: latest.status, owner: latest.owner, profileId: latest.profile.id, profileVersion: latest.profile.version };
      setComparison(null); setDiscard(false); transition('online'); setStopped(false); setMonitor(value => value + 1);
    });
  }
  async function signIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = event.currentTarget;
    const password = String(new FormData(form).get('reconnect_password'));
    await run(async () => {
      const challenge = await request<Challenge>('/auth/challenge'); api.csrf = challenge.csrf_token;
      const value = await request<Session>('/session', 'POST', { login_name: current.current.session.login_name, password });
      if (!mounted.current) return;
      api.csrf = value.csrf_token; current.current.onSession(value); form.reset();
      transition('review'); setComparison(null); setDiscard(false);
    });
    form.reset();
  }
  if (phase === 'online' && !stopped) return null;
  return <section className="panel" aria-labelledby="connection-title">
    <h2 id="connection-title">Installation connection</h2>
    <p role="status">{phase === 'checking' ? 'Checking the saved workspace before enabling writes.' : phase === 'maintenance' ? status.recovery_required ? 'Maintenance requires operator recovery. Writers remain closed; elapsed time is not proof of completion.' : 'Maintenance is active. Writers remain closed.' : phase === 'expired' ? 'This owner session is unavailable or expired.' : phase === 'offline' ? 'The installation could not be reached.' : phase === 'online' ? 'Automatic connection monitoring reached its bound.' : 'Review the current workspace before resuming writes.'}</p>
    <p>Editors remain mounted for copying and local editing. Nothing is automatically discarded, reloaded or saved. Keep this page open to retain unsaved text. No personal-data buffer is stored in browser storage.</p>
    {error && <p role="alert" className="error">{error}</p>}
    {status.maintenance_active && <p>Operation: {status.operation?.purpose ?? 'maintenance'}; phase: {status.operation?.phase ?? 'unavailable'}. Pending actual calls: {status.drain.active_calls}; provider executions: {status.drain.provider_executions}.</p>}
    <div className="actions"><button className="secondary" disabled={busy} onClick={() => { void compare(); }}>Check connection and compare saved workspace</button><button className="secondary" disabled={busy} onClick={() => { setStopped(false); setMonitor(value => value + 1); }}>Restart bounded connection monitoring</button></div>
    {phase === 'expired' && <form onSubmit={event => { void signIn(event); }}><h3>Sign in without replacing editors</h3><p>Sign in as {session.login_name}, then explicitly compare the workspace. Signing in does not rebind an old approval or discard text.</p><label>Password to reconnect<input name="reconnect_password" type="password" autoComplete="current-password" required maxLength={128} /></label><button disabled={busy || reloadDisabled}>Sign in and retain editors</button></form>}
    {comparison && <section aria-label="Saved workspace comparison"><h3>Saved workspace comparison</h3><p>Namespace: <code style={{ overflowWrap: 'anywhere' }}>{comparison.status.namespace_id}</code>; workspace version {comparison.status.workspace_version}; maintenance epoch {comparison.status.maintenance_epoch}.</p><p>Saved profile: {comparison.profile.current_revision.snapshot.display_name}; revision {comparison.profile.current_revision.revision_no}. Your editors above and below are unchanged.</p>
      {compatible(comparison) ? <button disabled={busy || reloadDisabled} onClick={() => { void reconnect(false); }}>Resume writes and keep all editor text</button> : <p>The saved namespace or workspace version differs. Copy any unsaved text before explicitly discarding and reloading every workspace editor.</p>}
      <label className="check"><input type="checkbox" checked={discard} disabled={busy || reloadDisabled} onChange={event => setDiscard(event.target.checked)} />I have preserved the text I need and explicitly discard all unsaved workspace editors.</label><button className="secondary" disabled={busy || reloadDisabled || !discard} onClick={() => { void reconnect(true); }}>Discard all editors and load this saved workspace</button>
    </section>}
  </section>;
}
