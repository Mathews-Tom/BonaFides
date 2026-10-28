// SPDX-License-Identifier: MIT OR Apache-2.0
import { useEffect, useRef, useState } from 'react';
import { Api, ApiError, errorText, terminal, type InstallationStatus, type Profile, type Workflow, type WorkflowState } from './api';
import { OwnerDataControls } from './OwnerDataControls';

interface ExportCopy {
  id: string; state: WorkflowState; last_error_code: string | null; created_at: string;
  archive_sha256: string | null; manifest_sha256: string | null; byte_length: number | null;
  expires_at: string | null; downloadable: boolean; copy_state: 'pending' | 'ready' | 'expired' | null;
}

export function DataPortability({ api, profile, disabled, onBusyChange }: {
  api: Api; profile: Profile | null; disabled: boolean; onBusyChange: (busy: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<ExportCopy[]>([]);
  const [maintenance, setMaintenance] = useState<InstallationStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [controlsBusy, setControlsBusy] = useState(false);
  const callbacks = useRef(onBusyChange); callbacks.current = onBusyChange;
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [refresh, setRefresh] = useState(0);
  const pending = useRef<{ key: string; version: number } | null>(null);
  useEffect(() => {
    if (!open) return;
    let alive = true;
    const abort = new AbortController();
    async function load() {
      try {
        const status = await api.request<InstallationStatus>('/maintenance', 'GET', undefined, undefined, false, abort.signal);
        if (!alive) return;
        setMaintenance(status);
        if (!status.maintenance_active) {
          const result = await api.request<{ items: ExportCopy[] }>('/portable-exports', 'GET', undefined, undefined, false, abort.signal);
          if (alive) setItems(result.items);
        }
      } catch (failure) {
        if (alive) setError(errorText(failure));
      }
    }
    void load();
    return () => { alive = false; abort.abort(); };
  }, [api, open, refresh]);
  useEffect(() => { callbacks.current(busy || controlsBusy); }, [busy, controlsBusy]);
  useEffect(() => () => callbacks.current(false), []);
  async function create() {
    if (!profile || unavailable || api.writeBlock) return;
    pending.current ??= { key: crypto.randomUUID(), version: profile.version };
    setBusy(true); setError(''); setNotice('');
    try {
      const task = await api.request<Workflow>('/portable-exports', 'POST', {}, pending.current.version, pending.current.key);
      pending.current = null;
      setNotice(`Export task ${task.id} is saved. The dedicated worker briefly pauses writers while collecting a consistent copy.`);
      setRefresh(value => value + 1);
    } catch (failure) {
      if (failure instanceof ApiError && failure.status < 500) pending.current = null;
      setError(errorText(failure));
    } finally { setBusy(false); }
  }
  function download(copy: ExportCopy) {
    if (unavailable) return;
    setError('');
    const link = document.createElement('a');
    link.href = `/api/v1/portable-exports/${encodeURIComponent(copy.id)}/download`;
    link.download = 'offerloom-owner-data.zip';
    link.click();
    setNotice('Owner-data download requested. The server verifies the stored archive before streaming it; check browser downloads for completion or failure. Protect this unencrypted personal-data file; it is not an installation backup.');
  }
  const unavailable = disabled || busy || controlsBusy || !!maintenance?.maintenance_active;
  return <details id="owner-data-portability" className="panel" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>Owner-data portability</summary>
    <h2>Retrieve your saved data</h2>
    <p>Export saved profile and evidence history, postings, applications, notes, document revisions and referenced files. Unsupported document claims remain visible in the archive; PDF restrictions do not prevent retrieving your data.</p>
    <p>This unencrypted ZIP excludes sessions, credentials and installation secrets. It is not a full installation backup. Two unexpired copies can be retained; each download expires after 24 hours. The lifecycle worker removes expired local copies when running. Downloads you save elsewhere remain your responsibility.</p>
    {maintenance?.maintenance_active && <p role="status">{maintenance.recovery_required ? 'Maintenance requires operator recovery. Writers remain closed; elapsed time does not prove a safe restart.' : `Maintenance in progress (${maintenance.operation?.phase ?? 'draining'}). Saved work is retained; wait before retrying writes.`}</p>}
    <p role="status">{notice}</p>
    {error && <p role="alert" className="error">{error}</p>}
    <div className="actions"><button type="button" disabled={unavailable || !!api.writeBlock || !profile || items.some(item => !terminal(item.state))} onClick={() => { void create(); }}>Create owner-data export</button><button type="button" className="secondary" disabled={busy || controlsBusy} onClick={() => { setError(''); setRefresh(value => value + 1); }}>Refresh exports and maintenance status</button></div>
    {open && <ul aria-label="Recent owner-data exports">{items.map(item => <li key={item.id}>
      <p>Created {new Date(item.created_at).toLocaleString()} · {item.state.replaceAll('_', ' ')}{item.byte_length !== null ? ` · ${item.byte_length.toLocaleString()} bytes` : ''}</p>
      {item.expires_at && <p>Download expires {new Date(item.expires_at).toLocaleString()}. Local copy: {item.copy_state}.</p>}
      {item.archive_sha256 && <details><summary>Archive identity</summary><p>SHA-256: <code style={{ overflowWrap: 'anywhere' }}>{item.archive_sha256}</code></p></details>}
      {item.last_error_code && <p>Export failed ({item.last_error_code}); no successful download was published.</p>}
      {item.downloadable && <button type="button" className="secondary" disabled={unavailable} onClick={() => { void download(item); }}>Download owner data</button>}
    </li>)}</ul>}
    {open && items.length === 0 && !maintenance?.maintenance_active && <p>No recent exports. Create an export of saved data; unsaved editor text is not included.</p>}
    <OwnerDataControls api={api} active={open} disabled={disabled || busy || !!maintenance?.maintenance_active} onBusyChange={setControlsBusy} />
  </details>;
}
