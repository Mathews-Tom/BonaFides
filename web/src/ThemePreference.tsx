// SPDX-License-Identifier: MIT OR Apache-2.0
import { useEffect, useId, useLayoutEffect, useState } from 'react';

type Theme = 'light' | 'dark';
type Preference = 'system' | Theme;
type ThemeState = { preference: Preference; expiresAt: number | null; storageUnavailable: boolean };

const STORAGE_KEY = 'offerloom.theme';
// One year means exactly 365 days, measured from the explicit selection, not reload.
const PREFERENCE_LIFETIME = 365 * 24 * 60 * 60 * 1000;
const MAX_TIMEOUT = 2_147_483_647;

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
    <label>Theme
      <select value={state.preference} aria-describedby={state.storageUnavailable ? `${helpId} ${statusId}` : helpId} onChange={event => { selectPreference(event.currentTarget.value as Preference); }}>
        <option value="system">System</option>
        <option value="light">Light</option>
        <option value="dark">Dark</option>
      </select>
    </label>
    <p id={helpId} className="visually-hidden">System follows your device. Light and dark choices are remembered in this browser for one year (365 days) from selection. System removes the saved choice. Documents and PDFs do not change.</p>
    {state.storageUnavailable && <p id={statusId} className="theme-storage-status" role="status">Browser storage is unavailable. This theme choice applies only to this visit; a previously saved choice may return after reload.</p>}
  </div>;
}
