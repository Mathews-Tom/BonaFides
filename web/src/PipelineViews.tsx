// SPDX-License-Identifier: MIT OR Apache-2.0
import { useCallback, useEffect, useRef, useState } from 'react';
import { Api, errorText } from './api';
import { stageName, stages, type ApplicationPage, type CalendarPage, type PipelineInsights, type Stage } from './tracking-api';

type OpenApplication = (id: string, editStage?: boolean) => void;
// Scope is part of the result identity, including mutation invalidation. Aborts are
// backed by identity checks because an already-resolved request can still arrive late.
function useView<T>(api: Api, path: string, revision: number) {
  const scope = `${revision}:${path}`;
  const live = useRef(scope); live.current = scope;
  const serial = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const [state, setState] = useState<{ scope: string; data: T | null; error: string; loading: boolean }>({ scope, data: null, error: '', loading: true });
  const load = useCallback(async (suffix = '', merge?: (old: T, next: T) => T) => {
    const token = ++serial.current;
    controller.current?.abort(); const pending = new AbortController(); controller.current = pending;
    setState(old => ({ scope, data: old.scope === scope ? old.data : null, error: '', loading: true }));
    try {
      const next = await api.request<T>(`${path}${suffix}`, 'GET', undefined, undefined, false, pending.signal);
      if (pending.signal.aborted || live.current !== scope || serial.current !== token) return;
      setState(old => ({ scope, data: merge && old.scope === scope && old.data ? merge(old.data, next) : next, error: '', loading: false }));
    } catch (failure) {
      if (!pending.signal.aborted && live.current === scope && serial.current === token) setState(old => ({ ...old, scope, error: errorText(failure), loading: false }));
    }
  }, [api, path, scope]);
  useEffect(() => { void load(); return () => { controller.current?.abort(); }; }, [load]);
  return { data: state.scope === scope ? state.data : null, error: state.scope === scope ? state.error : '', loading: state.scope !== scope || state.loading, load };
}
function ViewStatus({ error, loading }: { error: string; loading: boolean }) {
  return <>{loading && <p role="status">Loading view… Previously loaded rows may be stale.</p>}{error && <p role="status" className="error">View unavailable, not empty. {error} Previously loaded rows may be stale. Retry with Refresh.</p>}</>;
}
function BoardColumn({ api, revision, q, archived, stage, locked, open }: {
  api: Api; revision: number; q: string; archived: string; stage: Stage; locked: boolean; open: OpenApplication;
}) {
  const query = new URLSearchParams({ stage, archived }); if (q) query.set('q', q);
  const view = useView<ApplicationPage>(api, `/applications?${query}`, revision);
  return <section aria-label={`${stageName(stage)} board column`}><h4>{stageName(stage)}</h4><ViewStatus {...view} />
    <button className="secondary" disabled={locked || view.loading} onClick={() => { void view.load(); }}>Refresh {stageName(stage)} column</button>
    {view.data && <><p>{view.data.items.length} loaded; {view.data.next_cursor ? 'more available' : 'end of this stage'}. Snapshot {view.data.as_of}.</p>
      <ul>{view.data.items.map(item => <li key={item.id}><button className="secondary" disabled={locked} onClick={() => open(item.id)}>Open {item.title} at {item.company}</button><p>Attempt {item.attempt_no} · {item.archived_at ? 'Archived' : 'Active'}</p><button className="secondary" disabled={locked} onClick={() => open(item.id, true)}>Change stage for {item.title}</button></li>)}</ul>
      {!view.data.items.length && !view.error && !view.loading && <p>No applications in this stage and scope.</p>}
      {view.data.next_cursor && <button className="secondary" disabled={locked || view.loading} onClick={() => { void view.load(`&cursor=${encodeURIComponent(view.data!.next_cursor!)}`, (old, next) => ({ ...next, items: [...old.items, ...next.items.filter(item => !old.items.some(prior => prior.id === item.id))] })); }}>Load more {stageName(stage)} applications</button>}</>}
  </section>;
}
export function PipelineBoard({ api, revision, q, archived, locked, open }: {
  api: Api; revision: number; q: string; archived: string; locked: boolean; open: OpenApplication;
}) {
  return <><p>All eight stages, independently paginated. Search: {q || 'not restricted'}; archive scope: {archived}. Tab to select an application; Enter or Space opens it or its stage editor. No drag operation writes a stage.</p>
    <div className="tracking-board" aria-label="Application pipeline board">{stages.map(stage => <BoardColumn key={stage} {...{ api, revision, q, archived, locked, open, stage }} />)}</div></>;
}
function preference(key: string, choices: readonly string[], fallback: string) {
  const value = localStorage.getItem(key); return value && choices.includes(value) ? value : fallback;
}
export function PipelineReports({ api, revision, locked, open }: { api: Api; revision: number; locked: boolean; open: OpenApplication }) {
  const [initial] = useState(() => {
    try { return { cohort: preference('bonafides.pipeline.cohort.v1', ['all', '30', '90'], 'all'), quiet: preference('bonafides.pipeline.quiet.v1', ['3', '5', '7', '10', '14', '21', '30'], '7'), warning: '' }; }
    catch { return { cohort: 'all', quiet: '7', warning: 'Browser preferences are unavailable; reporting choices last only for this open page.' }; }
  });
  const [cohort, setCohort] = useState(initial.cohort);
  const [quiet, setQuiet] = useState(initial.quiet);
  const [preferenceWarning, setPreferenceWarning] = useState(initial.warning);
  useEffect(() => {
    try { localStorage.setItem('bonafides.pipeline.cohort.v1', cohort); localStorage.setItem('bonafides.pipeline.quiet.v1', quiet); setPreferenceWarning(''); }
    catch { setPreferenceWarning('Browser preferences are unavailable; reporting choices last only for this open page.'); }
  }, [cohort, quiet]);
  const view = useView<PipelineInsights>(api, `/applications/insights?cohort=${cohort}&quiet_days=${quiet}`, revision);
  const data = view.data; const counts = data?.cohort_counts;
  return <section aria-label="Pipeline insights"><h3>Pipeline insights</h3><div className="catalog-filters">
    <label>Creation cohort<select disabled={locked} value={cohort} onChange={event => setCohort(event.target.value)}><option value="all">All time</option><option value="30">Created in last 30 days</option><option value="90">Created in last 90 days</option></select></label>
    <label>Quiet application threshold<select disabled={locked} value={quiet} onChange={event => setQuiet(event.target.value)}>{[3, 5, 7, 10, 14, 21, 30].map(days => <option key={days} value={days}>{days} days</option>)}</select></label>
    </div>{preferenceWarning && <p role="status" className="error">{preferenceWarning}</p>}<button className="secondary" disabled={locked || view.loading} onClick={() => { void view.load(); }}>Refresh pipeline insights</button><ViewStatus {...view} />
    {data && counts && <><p>Snapshot {data.as_of}. Creation cohort: {data.cohort === 'all' ? 'all time' : `last ${data.cohort} days`}; {counts.application_count} attempts, including {counts.archived_application_count} archived. Repeat attempts count separately.</p>
      <h4>Historical funnel</h4><p>Ever reached, not current stage. Backward moves retain history; corrections and undo can reduce it. Recorded Applied is not delivery. Direct rejection implies Applied only ({counts.inferred_applied_from_rejection} inferred).</p>
      <div className="pipeline-table"><table><caption>Historical progression within creation cohort</caption><thead><tr><th>Stage</th><th>Reached / cohort</th><th>Rate</th></tr></thead><tbody>{counts.stages.map(item => <tr key={item.stage}><th scope="row">{stageName(item.stage)}</th><td>{item.reached_count}/{counts.application_count}</td><td>{item.rate === null ? 'Unavailable (no cohort)' : `${(item.rate * 100).toFixed(1)}%`}</td></tr>)}</tbody></table></div>
      <p>No progression recorded: {counts.no_progression_count}.</p><h4>Current stages in this creation cohort</h4><dl className="tracking-counts">{stages.map(stage => <div key={stage}><dt>{stageName(stage)}</dt><dd>{counts.current_by_stage[stage]}</dd></div>)}</dl>
      <details><summary>52-week activity (UTC)</summary><p>Monday-start UTC weeks, independent of the creation-cohort filter. Recorded Applied uses the earliest valid positive stage or rejection effective date, never delivery.</p><div className="pipeline-table"><table><caption>Weekly created and recorded Applied</caption><thead><tr><th>Week starting</th><th>Created</th><th>Recorded Applied</th></tr></thead><tbody>{data.weeks.map(week => <tr key={week.week_start}><th scope="row">{week.week_start}</th><td>{week.created}</td><td>{week.recorded_applied}</td></tr>)}</tbody></table></div></details>
      <h4>What's next</h4><p>Active applications only, independent of creation cohort. Quiet threshold: {data.quiet_days} days. Local prompts, not mail or inferred deadlines.</p>
      <ul>{data.next.items.map(item => <li key={item.application_id}><button className="secondary" disabled={locked} onClick={() => open(item.application_id)}>Open {item.title} at {item.company}</button><p>{item.reason}{item.starts_at ? ` · ${item.starts_at}` : ''}</p></li>)}</ul>
      {!data.next.items.length && !view.error && !view.loading && <p>No next actions in this snapshot.</p>}{data.next.has_more && <p>More applications need attention than the 50 shown. Open the application list for the rest.</p>}</>}
  </section>;
}
const shiftDate = (day: string, days: number) => { const date = new Date(`${day}T12:00:00Z`); date.setUTCDate(date.getUTCDate() + days); return date.toISOString().slice(0, 10); };
function todayIn(zone: string) {
  const parts = new Intl.DateTimeFormat('en', { timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date());
  return ['year', 'month', 'day'].map(type => parts.find(part => part.type === type)!.value).join('-');
}
export function PipelineCalendar({ api, revision, locked, open }: { api: Api; revision: number; locked: boolean; open: OpenApplication }) {
  const [day, setDay] = useState(() => todayIn('UTC'));
  const [month, setMonth] = useState(() => day.slice(0, 7));
  const [zone, setZone] = useState('UTC'); const [zoneInput, setZoneInput] = useState('UTC');
  const [zoneError, setZoneError] = useState(''); const [mode, setMode] = useState('month');
  const calendar = useRef<HTMLDivElement>(null); const moveFocus = useRef(false);
  const query = new URLSearchParams({ month, timezone: zone, limit: '100' });
  const view = useView<CalendarPage>(api, `/applications/calendar?${query}`, revision);
  useEffect(() => { if (moveFocus.current) { calendar.current?.querySelector<HTMLButtonElement>(`[data-day="${day}"]`)?.focus(); moveFocus.current = false; } }, [day]);
  function chooseDay(value: string, focus = false) { setDay(value); setMonth(value.slice(0, 7)); moveFocus.current = focus; }
  function shiftMonth(amount: number) { const date = new Date(`${month}-01T12:00:00Z`); date.setUTCMonth(date.getUTCMonth() + amount); chooseDay(date.toISOString().slice(0, 10)); }
  const days = Array.from({ length: new Date(Date.UTC(Number(month.slice(0, 4)), Number(month.slice(5, 7)), 0)).getUTCDate() }, (_, index) => `${month}-${String(index + 1).padStart(2, '0')}`);
  const items = view.data?.items.filter(item => mode === 'month' || item.display_start.slice(0, 10) === day) ?? [];
  return <section aria-label="Local application calendar"><h3>Local application calendar</h3><p>Local-only appointments; no external calendar, invitation or sending. Display timezone changes never move stored instants. Completed and cancelled records remain labelled.</p>
    <form onSubmit={event => { event.preventDefault(); try { todayIn(zoneInput); setZone(zoneInput); setZoneError(''); } catch { setZoneError('Enter a supported IANA timezone, such as America/New_York or UTC.'); } }}><label>Calendar IANA timezone<input required maxLength={200} disabled={locked} value={zoneInput} onChange={event => setZoneInput(event.target.value)} /></label><button disabled={locked}>Apply calendar timezone</button></form>{zoneError && <p role="status" className="error">{zoneError}</p>}
    <div className="catalog-filters"><label>Calendar month<input type="month" required disabled={locked} value={month} onChange={event => { if (/^\d{4}-\d{2}$/.test(event.target.value)) chooseDay(`${event.target.value}-01`); }} /></label><label>Calendar view<select disabled={locked} value={mode} onChange={event => setMode(event.target.value)}><option value="month">Month</option><option value="day">Day</option></select></label></div>
    <div className="actions"><button className="secondary" disabled={locked} onClick={() => shiftMonth(-1)}>Previous month</button><button className="secondary" disabled={locked} onClick={() => chooseDay(todayIn(zone))}>Today</button><button className="secondary" disabled={locked} onClick={() => shiftMonth(1)}>Next month</button><button className="secondary" disabled={locked || view.loading} onClick={() => { void view.load(); }}>Refresh calendar</button></div>
    <p>Display: {month}, {zone}. Selected day: {day}. Arrow keys move one day; Up/Down move one week. Enter or Space selects a day.</p>
    <div className="pipeline-days" ref={calendar} role="group" aria-label="Calendar days" onKeyDown={event => {
      if (locked || !(event.target instanceof HTMLElement) || !event.target.dataset.day) return;
      const moves: Record<string, number> = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 }; const delta = moves[event.key];
      if (delta !== undefined) { event.preventDefault(); chooseDay(shiftDate(day, delta), true); }
    }}>{['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map(label => <span key={label} aria-hidden="true">{label}</span>)}{days.map((value, index) => <button key={value} data-day={value} style={index === 0 ? { gridColumnStart: (new Date(`${value}T12:00:00Z`).getUTCDay() + 6) % 7 + 1 } : undefined} className="secondary" disabled={locked} tabIndex={value === day ? 0 : -1} aria-pressed={value === day} aria-label={`Select ${value}`} onClick={() => chooseDay(value)}>{Number(value.slice(8))}</button>)}</div>
    <ViewStatus {...view} />{view.data && <><p>Snapshot {view.data.as_of}. {view.data.items.length} appointments loaded for {view.data.month} in {view.data.timezone}; {view.data.next_cursor ? 'more month appointments available' : 'end of month results'}.</p>
      <ol className="pipeline-agenda">{items.map(item => <li key={item.appointment_id}><p><time dateTime={item.starts_at}>{item.display_start}</time>{item.display_end ? ` to ${item.display_end}` : ''} ({view.data!.timezone})</p><strong>{item.appointment_title}</strong><p>{item.kind === 'interview' ? 'Interview' : 'Follow-up'} · {item.status}{Date.parse(item.starts_at) < Date.parse(view.data!.as_of) ? ' · Passed' : ''}</p><button className="secondary" disabled={locked} onClick={() => open(item.application_id)}>Open {item.title} at {item.company}</button></li>)}</ol>
      {!items.length && !view.error && !view.loading && <p>{view.data.next_cursor ? 'No appointments in this loaded selection; load more month appointments before concluding it is empty.' : `No local appointments for this ${mode}.`}</p>}
      {view.data.next_cursor && <button className="secondary" disabled={locked || view.loading} onClick={() => { const cursor = view.data!.next_cursor!; void view.load(`&after_start=${encodeURIComponent(cursor.starts_at)}&after_id=${encodeURIComponent(cursor.id)}`, (old, next) => ({ ...next, items: [...old.items, ...next.items.filter(item => !old.items.some(prior => prior.appointment_id === item.appointment_id))] })); }}>Load more month appointments</button>}</>}
  </section>;
}
