// SPDX-License-Identifier: MIT OR Apache-2.0
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ThemePreference } from './ThemePreference';

const STORAGE_KEY = 'offerloom.theme';
const YEAR = 365 * 24 * 60 * 60 * 1000;
const NOW = Date.parse('2026-10-25T12:00:00Z');

function installSystemTheme() {
  const query = new EventTarget();
  let dark = false;
  Object.defineProperties(query, {
    matches: { get: () => dark },
    media: { value: '(prefers-color-scheme: dark)' },
  });
  vi.stubGlobal('matchMedia', vi.fn(() => query as MediaQueryList));
  return (next: boolean) => {
    act(() => { dark = next; query.dispatchEvent(new Event('change')); });
  };
}

function storedTheme(theme = 'dark', expiresAt = NOW + YEAR) {
  return JSON.stringify({ version: 1, theme, expiresAt });
}

function storageChanged(key: string | null = STORAGE_KEY, storageArea = window.localStorage) {
  act(() => { window.dispatchEvent(new StorageEvent('storage', { key, storageArea })); });
}

let setSystemDark: (dark: boolean) => void;
beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
  window.localStorage.clear();
  setSystemDark = installSystemTheme();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  document.documentElement.removeAttribute('data-theme');
});

function themeButton(value: string) {
  return screen.getByRole('button', { name: new RegExp(`^${value} theme$`, 'i') });
}

function selectTheme(value: string) {
  fireEvent.click(themeButton(value));
}

describe('system theme and explicit preference', () => {
  it('starts with the current system theme and follows changes without storing a choice', () => {
    setSystemDark(true);
    const write = vi.spyOn(Storage.prototype, 'setItem');
    render(<ThemePreference />);
    expect(themeButton('system')).toHaveAttribute('aria-pressed', 'true');
    expect(document.documentElement).toHaveAttribute('data-theme', 'dark');
    setSystemDark(false);
    expect(document.documentElement).toHaveAttribute('data-theme', 'light');
    expect(document.documentElement).not.toHaveAttribute('style');
    expect(window.localStorage.length).toBe(0);
    expect(write).not.toHaveBeenCalled();
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('stores only a versioned theme and exact 365-day expiry, ignores system changes and survives reload without extending expiry', () => {
    const first = render(<ThemePreference />);
    selectTheme('dark');
    const saved = window.localStorage.getItem(STORAGE_KEY);
    expect(JSON.parse(saved!)).toEqual({ version: 1, theme: 'dark', expiresAt: NOW + YEAR });
    expect(window.localStorage.length).toBe(1);
    setSystemDark(true);
    setSystemDark(false);
    expect(document.documentElement).toHaveAttribute('data-theme', 'dark');
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    first.unmount();
    vi.setSystemTime(NOW + 24 * 60 * 60 * 1000);
    render(<ThemePreference />);
    expect(themeButton('dark')).toHaveAttribute('aria-pressed', 'true');
    expect(document.documentElement).toHaveAttribute('data-theme', 'dark');
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe(saved);
  });

  it.each(['light', 'dark'] as const)('loads a stored %s override rather than the opposite system theme', theme => {
    setSystemDark(theme === 'light');
    window.localStorage.setItem(STORAGE_KEY, storedTheme(theme));
    render(<ThemePreference />);
    expect(themeButton(theme)).toHaveAttribute('aria-pressed', 'true');
    expect(document.documentElement).toHaveAttribute('data-theme', theme);
  });

  it('removes an override on System and immediately follows the latest system preference', () => {
    window.localStorage.setItem(STORAGE_KEY, storedTheme());
    render(<ThemePreference />);
    selectTheme('system');
    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull();
    expect(document.documentElement).toHaveAttribute('data-theme', 'light');
    setSystemDark(true);
    expect(document.documentElement).toHaveAttribute('data-theme', 'dark');
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('expires a live override exactly one year after selection, despite the browser timeout limit', () => {
    render(<ThemePreference />);
    selectTheme('dark');
    act(() => { vi.advanceTimersByTime(YEAR - 1); });
    expect(document.documentElement).toHaveAttribute('data-theme', 'dark');
    act(() => { vi.advanceTimersByTime(1); });
    expect(themeButton('system')).toHaveAttribute('aria-pressed', 'true');
    expect(document.documentElement).toHaveAttribute('data-theme', 'light');
    setSystemDark(true);
    expect(document.documentElement).toHaveAttribute('data-theme', 'dark');
  });

  it('rechecks expiry when a sleeping tab regains focus', () => {
    window.localStorage.setItem(STORAGE_KEY, storedTheme('dark', NOW + 100));
    render(<ThemePreference />);
    vi.setSystemTime(NOW + 101);
    act(() => { window.dispatchEvent(new Event('focus')); });
    expect(themeButton('system')).toHaveAttribute('aria-pressed', 'true');
    expect(document.documentElement).toHaveAttribute('data-theme', 'light');
  });

  it.each([
    ['unknown theme', storedTheme('sepia')],
    ['unknown schema version', JSON.stringify({ version: 2, theme: 'dark', expiresAt: NOW + YEAR })],
    ['malformed JSON', '{'],
    ['empty value', ''],
    ['null', 'null'],
    ['array', '[]'],
    ['bare string', '"dark"'],
    ['missing expiry', JSON.stringify({ version: 1, theme: 'dark' })],
    ['string expiry', JSON.stringify({ version: 1, theme: 'dark', expiresAt: String(NOW + YEAR) })],
    ['fractional expiry', storedTheme('dark', NOW + 0.5)],
    ['expired preference', storedTheme('dark', NOW - 1)],
    ['expiry boundary', storedTheme('dark', NOW)],
  ])('returns to system for %s', (_description, raw) => {
    window.localStorage.setItem(STORAGE_KEY, raw);
    render(<ThemePreference />);
    expect(themeButton('system')).toHaveAttribute('aria-pressed', 'true');
    expect(document.documentElement).toHaveAttribute('data-theme', 'light');
    setSystemDark(true);
    expect(document.documentElement).toHaveAttribute('data-theme', 'dark');
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });
});

describe('cross-tab preference changes', () => {
  it('adopts another tab’s valid override, then follows system after malformed data, removal or clear', () => {
    render(<ThemePreference />);
    window.localStorage.setItem(STORAGE_KEY, storedTheme());
    storageChanged();
    expect(themeButton('dark')).toHaveAttribute('aria-pressed', 'true');
    expect(document.documentElement).toHaveAttribute('data-theme', 'dark');
    setSystemDark(true);
    window.localStorage.setItem(STORAGE_KEY, 'not-json');
    storageChanged();
    expect(themeButton('system')).toHaveAttribute('aria-pressed', 'true');
    setSystemDark(false);
    expect(document.documentElement).toHaveAttribute('data-theme', 'light');
    window.localStorage.setItem(STORAGE_KEY, storedTheme());
    storageChanged();
    window.localStorage.removeItem(STORAGE_KEY);
    storageChanged();
    expect(themeButton('system')).toHaveAttribute('aria-pressed', 'true');
    window.localStorage.setItem(STORAGE_KEY, storedTheme());
    storageChanged();
    window.localStorage.clear();
    storageChanged(null);
    expect(themeButton('system')).toHaveAttribute('aria-pressed', 'true');
    expect(document.documentElement).toHaveAttribute('data-theme', 'light');
  });

  it('ignores unrelated preferences and session storage notifications', () => {
    render(<ThemePreference />);
    window.localStorage.setItem(STORAGE_KEY, storedTheme());
    storageChanged('another.preference');
    storageChanged(STORAGE_KEY, window.sessionStorage);
    expect(themeButton('system')).toHaveAttribute('aria-pressed', 'true');
    expect(document.documentElement).toHaveAttribute('data-theme', 'light');
  });

  it('keeps the current selection but announces session-only state if a storage notification cannot be read', () => {
    window.localStorage.setItem(STORAGE_KEY, storedTheme());
    render(<ThemePreference />);
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new DOMException('Denied', 'SecurityError'); });
    storageChanged();
    expect(themeButton('dark')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('status')).toHaveTextContent('only to this visit');
  });
});

describe('unavailable browser storage', () => {
  it('reports denied access and allows a session-only override, then recovers when System can be saved', () => {
    window.localStorage.setItem(STORAGE_KEY, storedTheme());
    const denied = vi.spyOn(window, 'localStorage', 'get').mockImplementation(() => { throw new DOMException('Denied', 'SecurityError'); });
    render(<ThemePreference />);
    expect(themeButton('system')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('status')).toHaveTextContent('Browser storage is unavailable');
    selectTheme('dark');
    setSystemDark(true);
    setSystemDark(false);
    expect(document.documentElement).toHaveAttribute('data-theme', 'dark');
    expect(screen.getByRole('status')).toHaveTextContent('only to this visit');
    denied.mockRestore();
    selectTheme('system');
    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull();
    expect(document.documentElement).toHaveAttribute('data-theme', 'light');
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('reports getItem failure without treating unreadable preferences as remembered', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new DOMException('Denied', 'SecurityError'); });
    render(<ThemePreference />);
    expect(themeButton('system')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('status')).toHaveTextContent('only to this visit');
    setSystemDark(true);
    expect(document.documentElement).toHaveAttribute('data-theme', 'dark');
  });

  it('reports write failure while applying the selected theme only until reload', () => {
    const previous = storedTheme('light');
    window.localStorage.setItem(STORAGE_KEY, previous);
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('Full', 'QuotaExceededError'); });
    const first = render(<ThemePreference />);
    selectTheme('dark');
    expect(document.documentElement).toHaveAttribute('data-theme', 'dark');
    expect(screen.getByRole('status')).toHaveTextContent('a previously saved choice may return after reload');
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe(previous);
    setSystemDark(true);
    setSystemDark(false);
    expect(document.documentElement).toHaveAttribute('data-theme', 'dark');
    first.unmount();
    render(<ThemePreference />);
    expect(themeButton('light')).toHaveAttribute('aria-pressed', 'true');
    expect(document.documentElement).toHaveAttribute('data-theme', 'light');
  });

  it('reports remove failure while returning to system for this visit, without claiming the previous override was erased', () => {
    window.localStorage.setItem(STORAGE_KEY, storedTheme());
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new DOMException('Denied', 'SecurityError'); });
    const first = render(<ThemePreference />);
    selectTheme('system');
    expect(themeButton('system')).toHaveAttribute('aria-pressed', 'true');
    expect(document.documentElement).toHaveAttribute('data-theme', 'light');
    expect(screen.getByRole('status')).toHaveTextContent('only to this visit');
    setSystemDark(true);
    expect(document.documentElement).toHaveAttribute('data-theme', 'dark');
    first.unmount();
    render(<ThemePreference />);
    expect(themeButton('dark')).toHaveAttribute('aria-pressed', 'true');
  });
});
