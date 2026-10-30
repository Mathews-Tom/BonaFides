// SPDX-License-Identifier: MIT OR Apache-2.0
import { useEffect, useState } from 'react';
import { Api, errorText, type ImportPage, type Profile, type WorkflowPage } from './api';
import type { AttentionPage } from './tracking-api';

export const workspacePages = [
  { id: 'overview', label: 'Overview', description: 'A clear place to continue your next step.', icon: 'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z' },
  { id: 'profile', label: 'Profile', description: 'Your experience, skills and the sources behind them.', icon: 'M16 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0 M4 21v-3a8 8 0 0 1 16 0v3' },
  { id: 'opportunities', label: 'Opportunities', description: 'Find the roles worth pursuing. Keep your decisions in one place.', icon: 'M21 21l-5-5 M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0' },
  { id: 'applications', label: 'Applications', description: 'Your pursuits, conversations and next actions.', icon: 'M4 7h16v14H4z M8 7V3h8v4 M4 12h16 M10 12v3h4v-3' },
  { id: 'documents', label: 'Documents', description: 'Create, review and export a résumé you can stand behind.', icon: 'M5 2h9l5 5v15H5z M14 2v6h5 M9 12h6 M9 16h6' },
  { id: 'settings', label: 'Settings', description: 'Manage your account, data and workspace controls.', icon: 'M4 6h16 M4 12h16 M4 18h16 M8 3v6 M16 9v6 M10 15v6' },
] as const;
export type WorkspacePage = typeof workspacePages[number]['id'] | 'profile/imports' | 'activity' | 'not-found';
export function readWorkspacePage(): WorkspacePage {
  const hash = window.location.hash;
  if (!hash || hash === '#' || hash === '#/') return 'overview';
  const value = hash.slice(2);
  if (hash.startsWith('#/') && (workspacePages.some(page => page.id === value) || value === 'profile/imports' || value === 'activity')) return value as WorkspacePage;
  return 'not-found';
}
export function navigateWorkspace(page: WorkspacePage) { window.location.hash = `/${page}`; }

export function WorkspaceNavigation({ page, drafts }: { page: WorkspacePage; drafts: WorkspacePage[] }) {
  return <aside className="workspace-navigation">
    <a className="brand" href="#/overview" aria-label="BonaFides overview"><svg aria-hidden="true" viewBox="0 0 32 36"><path d="M6 2v32M16 2v32M26 2v32M2 10h28M2 26h28M6 10l20 16M26 10 6 26" /></svg>BonaFides</a>
    <p className="workspace-caption">Your career workspace</p>
    <nav aria-label="Workspace">{workspacePages.map(item => <a key={item.id} href={`#/${item.id}`} aria-current={page === item.id || (item.id === 'profile' && page === 'profile/imports') ? 'page' : undefined}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d={item.icon} /></svg><span>{item.label}</span>{(drafts.includes(item.id) || (item.id === 'profile' && drafts.includes('profile/imports'))) && <span className="draft-dot" aria-label="Unsaved changes" />}
    </a>)}</nav>
    <div className="navigation-bottom"><a href="#/activity" aria-current={page === 'activity' ? 'page' : undefined}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12h4l3-8 4 16 3-8h4" /></svg>Activity & reviews</a><span>Local workspace. Your data.</span></div>
  </aside>;
}

export function WorkspaceOverview({ api, profile, active }: { api: Api; profile: Profile | null; active: boolean }) {
  const [imports, setImports] = useState<ImportPage | null>(null);
  const [tasks, setTasks] = useState<WorkflowPage | null>(null);
  const [importError, setImportError] = useState('');
  const [taskError, setTaskError] = useState('');
  const [attention, setAttention] = useState<AttentionPage | null>(null);
  const [attentionError, setAttentionError] = useState('');
  const [loading, setLoading] = useState(false);
  const [generation, setGeneration] = useState(0);
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    setLoading(true); setImportError(''); setTaskError(''); setAttentionError('');
    const first = api.request<ImportPage>('/imports', 'GET', undefined, undefined, false, controller.signal).then(value => { if (!controller.signal.aborted) setImports(value); }).catch(failure => { if (!controller.signal.aborted) setImportError(errorText(failure)); });
    const second = api.request<WorkflowPage>('/workflows?limit=30', 'GET', undefined, undefined, false, controller.signal).then(value => { if (!controller.signal.aborted) setTasks(value); }).catch(failure => { if (!controller.signal.aborted) setTaskError(errorText(failure)); });
    const third = api.request<AttentionPage>('/applications/attention', 'GET', undefined, undefined, false, controller.signal).then(value => { if (!controller.signal.aborted) setAttention(value); }).catch(failure => { if (!controller.signal.aborted) setAttentionError(errorText(failure)); });
    void Promise.all([first, second, third]).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [api, active, generation]);
  const pending = imports?.items.filter(batch => batch.state === 'waiting_for_approval') ?? [];
  const current = tasks?.items.filter(task => ['pending', 'running', 'retryable_failure', 'waiting_for_approval'].includes(task.state)) ?? [];
  return <div className="overview-layout">
    <section className="overview-intro"><div className="overview-rule" /><h2>Make your next move<br />from a stronger foundation.</h2><p>Keep your facts grounded, your opportunities organized and your next action clear.</p><a className="button-link" href="#/profile">Build your profile</a><a className="text-link" href="#/documents">Create a base résumé without a model</a></section>
    <section className="panel overview-attention" aria-labelledby="overview-attention-title"><div className="section-heading"><h2 id="overview-attention-title">Ready to continue</h2><button className="secondary compact" disabled={loading} onClick={() => setGeneration(value => value + 1)}>{loading ? 'Refreshing…' : 'Refresh overview'}</button></div>
      {importError ? <p role="status" className="error">Import status unavailable. {importError} Earlier observations are not current.</p> : imports ? <div className="overview-action"><span className="action-mark">Review</span><div><h3>{pending.length ? `${pending.length} saved ${pending.length === 1 ? 'import needs' : 'imports need'} your review` : 'No imports awaiting review in the loaded batch list'}</h3><p>Source text stays separate until you explicitly approve the facts.</p><a href="#/profile/imports">{pending.length ? 'Review résumé imports' : 'Import a résumé'}</a></div></div> : <p role="status">Loading saved import status…</p>}
      {taskError ? <p role="status" className="error">Activity status unavailable. {taskError} No empty result is assumed.</p> : tasks ? <div className="overview-action"><span className="action-mark">Activity</span><div><h3>{current.length ? `${current.length} active or review-waiting ${current.length === 1 ? 'task' : 'tasks'} in the loaded page` : 'No active work in the loaded task page'}</h3><p>Inspect saved checkpoints, outstanding reviews and safe retry options.</p><a href="#/activity">Open activity</a></div></div> : <p role="status">Loading recent activity…</p>}
      {attentionError ? <p role="status" className="error">Follow-up status unavailable. {attentionError} No empty result is assumed.</p> : attention ? <div className="overview-action"><span className="action-mark">Follow up</span><div><h3>{attention.items.length ? `${attention.items.length} due actions or upcoming interviews in the loaded response` : 'No due actions or upcoming interviews in this response'}</h3>{attention.items.slice(0, 3).map(item => <p key={item.appointment.id}><strong>{item.title} at {item.company}</strong><br />{item.appointment.title} · {item.appointment.display_start} ({item.appointment.display_timezone})</p>)}<p className="hint">Observed {attention.as_of}. Local reminders only.</p><a href="#/applications">View applications and follow-ups</a></div></div> : <p role="status">Loading due actions and upcoming interviews…</p>}
    </section>
    <section className="panel overview-profile"><span className="subtle-label">Your foundation</span><h2>{profile?.current_revision.snapshot.display_name || 'Your profile'}</h2><p>{profile?.current_revision.snapshot.headline || 'Add the experience and skills that describe your work.'}</p><div className="profile-summary"><span>Saved revision<strong>{profile?.current_revision.revision_no ?? 'Unavailable'}</strong></span><span>Active facts<strong>{profile?.current_revision.claims.filter(claim => !claim.archived).length ?? 'Unavailable'}</strong></span></div><a href="#/profile">Open profile and evidence</a></section>
    <section className="overview-path" aria-label="Career workspace flow"><h2>A workspace that follows your work</h2><ol><li><a href="#/profile">Build your profile</a><span>Review facts and sources.</span></li><li><a href="#/opportunities">Explore opportunities</a><span>Save roles worth pursuing.</span></li><li><a href="#/documents">Prepare your documents</a><span>Review before exporting.</span></li><li><a href="#/applications">Track the next step</a><span>Keep follow-ups in sight.</span></li></ol></section>
  </div>;
}
