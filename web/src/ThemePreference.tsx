// SPDX-License-Identifier: MIT OR Apache-2.0
import { useEffect, useId, useLayoutEffect, useState } from 'react';

type Theme = 'light' | 'dark';
type Preference = 'system' | Theme;
type ThemeState = { preference: Preference; expiresAt: number | null; storageUnavailable: boolean };

const STORAGE_KEY = 'bonafides.theme';
// One year means exactly 365 days, measured from the explicit selection, not reload.
const PREFERENCE_LIFETIME = 365 * 24 * 60 * 60 * 1000;
const MAX_TIMEOUT = 2_147_483_647;

const THEME_OPTIONS = [
  { value: 'system', label: 'System theme', icon: <svg aria-hidden="true" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="13" rx="2" /><path d="M8 21h8m-4-4v4" /></svg> },
  { value: 'light', label: 'Light theme', icon: <svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="12" cy="12" r="4" /><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5" /></svg> },
  { value: 'dark', label: 'Dark theme', icon: <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M20.8 13.1A9 9 0 0 1 10.9 3.2a9 9 0 1 0 9.9 9.9Z" /></svg> },
] as const;

function readPreference(): ThemeState {
  const system: ThemeState = { preference: 'system', expiresAt: null, storageUnavailable: false };
  let raw: string | null;
  try {
    raw = window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return { ...system, storageUnavailable: true };
  }
  if (raw === null) return system;
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return system;
  }
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return system;
  const stored = value as Record<string, unknown>;
  if (stored.version !== 1 || (stored.theme !== 'light' && stored.theme !== 'dark') ||
    typeof stored.expiresAt !== 'number' || !Number.isSafeInteger(stored.expiresAt) || stored.expiresAt <= Date.now()) return system;
  return { preference: stored.theme, expiresAt: stored.expiresAt, storageUnavailable: false };
}

export function ThemePreference() {
  const [state, setState] = useState(readPreference);
  const [systemQuery] = useState(() => typeof window.matchMedia === 'function' ? window.matchMedia('(prefers-color-scheme: dark)') : null);
  const [systemDark, setSystemDark] = useState(() => systemQuery?.matches ?? false);
  const helpId = useId();
  const statusId = useId();
  const theme = state.preference === 'system' ? (systemDark ? 'dark' : 'light') : state.preference;

  useLayoutEffect(() => {
    document.documentElement.dataset.theme = theme;
    return () => { document.documentElement.removeAttribute('data-theme'); };
  }, [theme]);

  useEffect(() => {
    if (!systemQuery) return;
    const update = () => { setSystemDark(systemQuery.matches); };
    update();
    systemQuery.addEventListener('change', update);
    return () => { systemQuery.removeEventListener('change', update); };
  }, [systemQuery]);

  useEffect(() => {
    const synchronize = (event: StorageEvent) => {
      if (event.key !== null && event.key !== STORAGE_KEY) return;
      if (event.storageArea !== null) {
        try {
          if (event.storageArea !== window.localStorage) return;
        } catch {
          setState(current => ({ ...current, storageUnavailable: true }));
          return;
        }
      }
      const stored = readPreference();
      setState(current => stored.storageUnavailable ? { ...current, storageUnavailable: true } : stored);
    };
    window.addEventListener('storage', synchronize);
    return () => { window.removeEventListener('storage', synchronize); };
  }, []);

  useEffect(() => {
    const expiresAt = state.expiresAt;
    if (expiresAt === null) return;
    let timer: number | undefined;
    const checkExpiry = () => {
      window.clearTimeout(timer);
      const remaining = expiresAt - Date.now();
      if (remaining > 0) {
        // Browser timers cannot safely represent a whole year in one timeout.
        timer = window.setTimeout(checkExpiry, Math.min(remaining, MAX_TIMEOUT));
      } else {
        setState(current => current.expiresAt === expiresAt ? { preference: 'system', expiresAt: null, storageUnavailable: current.storageUnavailable } : current);
      }
    };
    checkExpiry();
    window.addEventListener('focus', checkExpiry);
    document.addEventListener('visibilitychange', checkExpiry);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener('focus', checkExpiry);
      document.removeEventListener('visibilitychange', checkExpiry);
    };
  }, [state.expiresAt]);

  function selectPreference(preference: Preference) {
    const expiresAt = preference === 'system' ? null : Date.now() + PREFERENCE_LIFETIME;
    try {
      if (preference === 'system') window.localStorage.removeItem(STORAGE_KEY);
      else window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ version: 1, theme: preference, expiresAt }));
      setState({ preference, expiresAt, storageUnavailable: false });
    } catch {
      setState({ preference, expiresAt, storageUnavailable: true });
    }
  }

  return <div className="theme-preference">
    <div className="theme-options" role="group" aria-label="Theme" aria-describedby={state.storageUnavailable ? `${helpId} ${statusId}` : helpId}>
      {THEME_OPTIONS.map(({ value, label, icon }) => <button key={value} type="button" className="theme-option" aria-label={label} title={label} aria-pressed={state.preference === value} onClick={() => { selectPreference(value); }}>{icon}</button>)}
    </div>
    <p id={helpId} className="visually-hidden">System follows your device. Light and dark choices are remembered in this browser for one year (365 days) from selection. System removes the saved choice. Documents and PDFs do not change.</p>
    {state.storageUnavailable && <p id={statusId} className="theme-storage-status" role="status">Browser storage is unavailable. This theme choice applies only to this visit; a previously saved choice may return after reload.</p>}
  </div>;
}
