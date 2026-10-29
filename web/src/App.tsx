// SPDX-License-Identifier: MIT OR Apache-2.0
import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { Api, ApiError, errorText, terminal, type Challenge, type InstallationStatus, type Profile, type ProfileContact, type Session, type Workflow } from './api';
import { ProfileEntries } from './ProfileEntries';
import { ResumeImport } from './ResumeImport';
import { JobSources } from './JobSources';
import { ThemePreference } from './ThemePreference';
import { Tracking } from './Tracking';
import { Documents } from './Documents';
import { Tasks } from './Tasks';
import { DataPortability } from './DataPortability';
import { ConnectionPanel } from './ConnectionPanel';
import { navigateWorkspace, readWorkspacePage, workspacePages, WorkspaceNavigation, WorkspaceOverview, type WorkspacePage } from './Workspace';
const emptyContact: ProfileContact = { email: '', phone: '', location: '', postal_address: '' };

export function App({ api = defaultApi }: { api?: Api }) {
  const [page, setPage] = useState<WorkspacePage>(readWorkspacePage);
  useEffect(() => {
    const change = () => {
      if (!window.location.hash.startsWith('#/')) return;
      setPage(readWorkspacePage());
      document.getElementById('workspace-page-title')?.focus();
      document.getElementById('workspace-page-title')?.scrollIntoView({ block: 'start' });
    };
    window.addEventListener('hashchange', change);
    return () => window.removeEventListener('hashchange', change);
  }, []);
  const [session, setSession] = useState<Session | null>(null);
  const [challenge, setChallenge] = useState<Challenge | null>(null);
  const [ready, setReady] = useState(false);
  const [initialStatus, setInitialStatus] = useState<InstallationStatus | null>(null);
  const [startupProblem, setStartupProblem] = useState('');
  const [startupGeneration, setStartupGeneration] = useState(0);
  const [connectionPaused, setConnectionPaused] = useState(true);
  const [workspaceGeneration, setWorkspaceGeneration] = useState(0);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [name, setName] = useState('');
  const [headline, setHeadline] = useState('');
  const [contact, setContact] = useState<ProfileContact>(emptyContact);
  const [comparison, setComparison] = useState<Profile | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [acting, setBusy] = useState(false);
  const [entriesDirty, setEntriesDirty] = useState(false);
  const [entriesBusy, setEntriesBusy] = useState(false);
  const [importDirty, setImportDirty] = useState(false);
  const [importBusy, setImportBusy] = useState(false);
  const [jobsDirty, setJobsDirty] = useState(false);
  const [jobsBusy, setJobsBusy] = useState(false);
  const [trackingDirty, setTrackingDirty] = useState(false);
  const [trackingBusy, setTrackingBusy] = useState(false);
  const [documentsDirty, setDocumentsDirty] = useState(false);
  const [documentsBusy, setDocumentsBusy] = useState(false);
  const [benchmark, setBenchmark] = useState(true);
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [tasksBusy, setTasksBusy] = useState(false);
  const [portabilityBusy, setPortabilityBusy] = useState(false);
  const busy = acting || tasksBusy || portabilityBusy;
  const [documentRequest, setDocumentRequest] = useState<{ id: string; key: string } | null>(null);
  const [scenario, setScenario] = useState('normal');
  const errorRef = useRef<HTMLDivElement>(null);
  useEffect(() => { if (error) errorRef.current?.focus(); }, [error]);
  const latestProfileVersion = useRef(0);
  const latestProfileId = useRef<string | null>(null);
  const identityEpoch = useRef(0);
  const workspaceEpoch = useRef(0);
  const acceptProfile = useCallback((value: Profile) => {
    if (latestProfileId.current && value.id !== latestProfileId.current) {
      setComparison(value); api.onUnavailable?.(new ApiError(409, 'workspace_identity_changed')); return;
    }
    if (value.version < latestProfileVersion.current) return;
    latestProfileVersion.current = value.version;
    latestProfileId.current = value.id;
    setProfile(value); setName(value.current_revision.snapshot.display_name); setHeadline(value.current_revision.snapshot.headline);
    setContact(value.current_revision.snapshot.contact ?? emptyContact); setComparison(null); identityEpoch.current++;
  }, [api]);
  const acceptEditorProfile = useCallback((value: Profile) => {
    if (workspaceGeneration === workspaceEpoch.current) acceptProfile(value);
  }, [workspaceGeneration, acceptProfile]);
  const loadProfile = useCallback(async () => {
    const epoch = identityEpoch.current; const value = await api.request<Profile>('/profile');
    if (epoch !== identityEpoch.current) return;
    acceptProfile(value);
  }, [api, acceptProfile]);
  useEffect(() => {
    let active = true;
    setStartupProblem('');
    void (async () => {
      try {
        const value = await api.request<Session>('/session');
        if (!active) return;
        api.csrf = value.csrf_token;
        const status = await api.request<InstallationStatus>('/maintenance');
        if (!active) return;
        setInitialStatus(status); setSession(value); await loadProfile();
      } catch (failure) {
        if (!active) return;
        if (!(failure instanceof ApiError && failure.status === 401)) {
          setError(errorText(failure));
          setStartupProblem(failure instanceof ApiError && failure.status === 503 ? 'Maintenance is active or requires operator recovery. Authentication and writers are not bypassed.' : 'The installation is unavailable. No owner workspace has been loaded.');
          return;
        }
        try { const value = await api.request<Challenge>('/auth/challenge'); if (active) { api.csrf = value.csrf_token; setChallenge(value); } }
        catch (failure) { if (active) { setError(errorText(failure)); setStartupProblem(failure instanceof ApiError && failure.status === 503 ? 'Maintenance is active. Sign-in remains closed until normal admission resumes.' : 'The installation could not be reached.'); } }
      } finally { if (active) setReady(true); }
    })();
    return () => { active = false; };
  }, [api, loadProfile, startupGeneration]);
  async function act(action: () => Promise<void>) {
    setBusy(true); setError(''); setNotice('');
    try { await action(); } catch (failure) { setError(errorText(failure)); } finally { setBusy(false); }
  }
  async function authenticate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = event.currentTarget; const values = new FormData(form);
    await act(async () => {
      const creatingAccount = challenge?.bootstrap_available === true;
      const fresh = await api.request<Challenge>('/auth/challenge');
      api.csrf = fresh.csrf_token; setChallenge(fresh);
      if (fresh.bootstrap_available !== creatingAccount) {
        setNotice('Account setup availability changed. Review the updated form and submit again.');
        return;
      }
      const body = { login_name: String(values.get('login_name')), password: String(values.get('password')), ...(creatingAccount ? { bootstrap_token: String(values.get('bootstrap_token')) } : {}) };
      const value = await api.request<Session>(creatingAccount ? '/bootstrap' : '/session', 'POST', body);
      api.csrf = value.csrf_token;
      const status = await api.request<InstallationStatus>('/maintenance');
      setInitialStatus(status); setSession(value); form.reset(); await loadProfile(); setNotice('Signed in.');
    });
  }
  async function reauthenticate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = event.currentTarget; const values = new FormData(form);
    await act(async () => {
      const value = await api.request<Session>('/session/reauthenticate', 'POST', { current_password: String(values.get('current_password')) });
      api.csrf = value.csrf_token; setSession(value);
      setNotice('Owner identity confirmed for five minutes. Create a new preview before approving an operation. Unsaved editor text is retained.');
    });
    form.reset();
  }
  const profileMutation = useRef<{ fingerprint: string; key: string } | null>(null);
  async function signOut() {
    await act(async () => {
      await api.request('/session', 'DELETE');
      workspaceEpoch.current++; setWorkspaceGeneration(workspaceEpoch.current);
      identityEpoch.current++; latestProfileVersion.current = 0; latestProfileId.current = null; profileMutation.current = null;
      setSession(null); setProfile(null); setName(''); setHeadline(''); setContact(emptyContact);
      setDocumentRequest(null); setWorkflow(null);
      const value = await api.request<Challenge>('/auth/challenge'); api.csrf = value.csrf_token; setChallenge(value);
    });
  }
  function reloadWorkspace(value: Profile) {
    latestProfileVersion.current = 0; latestProfileId.current = null; identityEpoch.current++;
    profileMutation.current = null; acceptProfile(value); workspaceEpoch.current++;
    setWorkspaceGeneration(workspaceEpoch.current);
    setEntriesDirty(false); setEntriesBusy(false); setImportDirty(false); setImportBusy(false);
    setJobsDirty(false); setJobsBusy(false); setTrackingDirty(false); setTrackingBusy(false);
    setDocumentsDirty(false); setDocumentsBusy(false); setTasksBusy(false); setPortabilityBusy(false);
    setDocumentRequest(null); setWorkflow(null); setError(''); setNotice('Every workspace editor was explicitly discarded and reloaded from the reviewed saved context.');
  }
  const identityDirty = !!profile && (name !== profile.current_revision.snapshot.display_name || headline !== profile.current_revision.snapshot.headline || (['email', 'phone', 'location', 'postal_address'] as const).some(field => contact[field] !== (profile.current_revision.snapshot.contact ?? emptyContact)[field]));
  const profileLocked = connectionPaused || busy || entriesBusy || entriesDirty || identityDirty || importBusy || importDirty || documentsDirty || documentsBusy;
  const observeProfile = useCallback((value: Profile) => {
    if (workspaceGeneration !== workspaceEpoch.current) return;
    if (profile && value.id !== profile.id) { acceptProfile(value); return; }
    if (profile && value.version <= profile.version) return;
    if (identityDirty) setComparison(value);
    else acceptProfile(value);
  }, [profile, identityDirty, acceptProfile, workspaceGeneration]);
  useEffect(() => {
    if (!identityDirty) return;
    const prevent = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', prevent);
    return () => window.removeEventListener('beforeunload', prevent);
  }, [identityDirty]);
  async function saveProfile(event: FormEvent) {
    event.preventDefault(); if (!profile) return;
    await act(async () => {
      const body = { ...profile.current_revision.snapshot, display_name: name, headline, ...(profile.current_revision.snapshot.contact || Object.values(contact).some(Boolean) ? { contact } : {}) };
      const fingerprint = JSON.stringify({ body, version: profile.version });
      if (profileMutation.current?.fingerprint !== fingerprint) profileMutation.current = { fingerprint, key: crypto.randomUUID() };
      const value = await api.request<Profile>('/profile', 'PUT', body, profile.version, profileMutation.current.key);
      profileMutation.current = null;
      acceptProfile(value); setNotice(`Saved as revision ${value.current_revision.revision_no}.`);
    });
  }
  const taskResultBlocked = profileLocked || jobsDirty || jobsBusy || trackingDirty || trackingBusy;
  const resultGuard = useRef(taskResultBlocked); resultGuard.current = taskResultBlocked;
  const syntheticTask = useRef(workflow); syntheticTask.current = workflow;
  const openedSynthetic = useRef<string | null>(null);
  const openTaskDocument = useCallback((id: string, key: string) => {
    if (resultGuard.current) { setNotice('The saved task result is available. Save or discard current workspace edits and finish active work before opening it; no editor was replaced.'); return; }
    setDocumentRequest({ id, key });
    navigateWorkspace('documents');
  }, []);
  const observeTask = useCallback((value: Workflow) => {
    if (syntheticTask.current?.id !== value.id) return;
    setWorkflow(previous => previous?.id === value.id && previous.version <= value.version ? value : previous);
    if (value.state !== 'succeeded' || openedSynthetic.current === value.id) return;
    openedSynthetic.current = value.id;
    const id = value.result?.document_id ?? value.result?.documents?.find(item => item.kind === 'resume')?.document_id;
    if (id) openTaskDocument(id, value.id);
  }, [openTaskDocument]);
  async function benchmarkAction(action: () => Promise<void>) {
    await act(async () => { try { await action(); } catch (failure) { if (failure instanceof ApiError && failure.status === 404) { setBenchmark(false); setNotice('Benchmark controls are unavailable on this installation. No synthetic task was started.'); } else throw failure; } });
  }
  const drafts: WorkspacePage[] = [];
  if (identityDirty || entriesDirty) drafts.push('profile');
  if (importDirty) drafts.push('profile/imports');
  if (jobsDirty) drafts.push('opportunities');
  if (trackingDirty) drafts.push('applications');
  if (documentsDirty) drafts.push('documents');
  const pageInfo = workspacePages.find(item => item.id === (page === 'profile/imports' ? 'profile' : page));
  const pageTitle = page === 'activity' ? 'Activity & reviews' : page === 'profile/imports' ? 'Résumé imports' : pageInfo?.label ?? 'Page not found';
  useEffect(() => { document.title = session ? `${pageTitle} · Offerloom` : challenge?.bootstrap_available ? 'Offerloom · Create account' : 'Offerloom · Sign in'; }, [pageTitle, session, challenge?.bootstrap_available]);
  return <div className={`app-shell${session ? ' authenticated' : ''}`}>
    <a className="skip" href="#main" onClick={event => { event.preventDefault(); document.getElementById('main')?.focus(); }}>Skip to workspace</a>
    {session && <WorkspaceNavigation page={page} drafts={drafts} />}
    <header className="app-header"><div className="brand"><svg aria-hidden="true" viewBox="0 0 32 36"><path d="M6 2v32M16 2v32M26 2v32M2 10h28M2 26h28M6 10l20 16M26 10 6 26" /></svg><span>Offerloom</span></div><span className="edition">{session ? 'Private career workspace' : 'Your career, grounded in evidence'}</span><ThemePreference />{session && <button className="secondary compact" disabled={profileLocked || jobsDirty || jobsBusy || trackingDirty || trackingBusy || !!(workflow && !terminal(workflow.state))} onClick={() => { void signOut(); }}>Sign out</button>}</header>
    <main id="main" tabIndex={-1}>
      <div ref={errorRef} className="error" role="alert" tabIndex={-1} hidden={!error}>{error}</div><p role="status" className="status">{notice}</p>
      {startupProblem && !session && <section className="panel" aria-label="Installation unavailable"><h2>Installation unavailable</h2><p role="status">{startupProblem}</p><button className="secondary" disabled={!ready || busy} onClick={() => { setError(''); setReady(false); setStartupGeneration(value => value + 1); }}>Check installation again</button></section>}
      {session && initialStatus && <details className={`connection-details${connectionPaused ? ' needs-attention' : ''}`} open={connectionPaused}><summary>{connectionPaused ? 'Workspace connection needs attention' : 'Connected to your installation'}</summary><ConnectionPanel api={api} session={session} profile={profile} initialStatus={initialStatus} reloadDisabled={busy || entriesBusy || importBusy || jobsBusy || trackingBusy || documentsBusy} onSession={setSession} onReload={reloadWorkspace} onPaused={setConnectionPaused} /></details>}
      {!ready ? <p role="status">Connecting to your installation…</p> : !session ? <section className="access">
        <div><h1>Your career, your workspace.</h1><p>Create your personal account, import your résumé and review the facts before building your next application.</p></div>
        <form onSubmit={event => { void authenticate(event); }}>
          <h2>{challenge?.bootstrap_available ? 'Create your account' : 'Sign in'}</h2>
          {challenge?.bootstrap_available && <>
            <p>Choose your own login name and password. This private installation has one account; account creation closes after setup.</p>
            <label>One-time setup code<input name="bootstrap_token" type="password" autoComplete="off" minLength={40} maxLength={128} required /></label>
            <p className="hint">Use the private setup code from your installation. It expires 15 minutes after setup is enabled and can be used only once.</p>
          </>}
          <label>Login name<input name="login_name" autoComplete="username" required maxLength={128} /></label>
          <label>Password<input name="password" type="password" autoComplete={challenge?.bootstrap_available ? 'new-password' : 'current-password'} minLength={15} maxLength={128} required /></label>
          <p className="hint">15–128 characters. Credentials stay in this form until sent to your installation; no browser token storage.</p>
          <button disabled={busy || !challenge}>{busy ? 'Connecting…' : challenge?.bootstrap_available ? 'Create account' : 'Sign in'}</button>
        </form>
      </section> : <div key={workspaceGeneration}>
      <div className="workspace-heading"><div><p className="workspace-breadcrumb">Workspace / {pageTitle}</p><h1 id="workspace-page-title" tabIndex={-1}>{pageTitle}</h1><p>{page === 'profile/imports' ? 'Bring your history into focus. Nothing becomes a profile fact without your review.' : page === 'activity' ? 'Follow saved work, inspect checkpoints and return to pending reviews.' : pageInfo?.description}</p></div>{profile && <span className="revision">Profile revision {profile.current_revision.revision_no}</span>}</div>
      {drafts.length > 0 && <aside className="draft-banner" aria-label="Retained workspace drafts"><strong>Unsaved changes are retained.</strong><span>Return to finish editing or explicitly discard them.</span>{drafts.map(draft => <a key={draft} href={`#/${draft}`}>{draft === 'profile/imports' ? 'Résumé import' : workspacePages.find(item => item.id === draft)?.label}</a>)}</aside>}
      {(page === 'profile' || page === 'profile/imports') && <nav className="page-tabs" aria-label="Profile workspace"><a href="#/profile" aria-current={page === 'profile' ? 'page' : undefined}>Profile details</a><a href="#/profile/imports" aria-current={page === 'profile/imports' ? 'page' : undefined}>Résumé imports</a></nav>}
      <div hidden={page !== 'overview'} data-workspace-screen="overview"><WorkspaceOverview api={api} profile={profile} active={page === 'overview'} /></div>
      <div hidden={page !== 'applications'} data-workspace-screen="applications"><Tracking api={api} disabled={busy || documentsDirty || documentsBusy} onDirtyChange={setTrackingDirty} onBusyChange={setTrackingBusy} /></div>
      <div hidden={page !== 'profile'} data-workspace-screen="profile"><section className="panel identity-panel"><div className="section-heading"><div><h2>Personal details</h2><p className="hint">The identity and printable contact details on your documents.</p></div></div>{profile ? <form onSubmit={event => { void saveProfile(event); }}>
        <fieldset disabled={busy || entriesBusy || entriesDirty || importBusy || importDirty || documentsDirty || documentsBusy}><legend>Profile identity</legend>
          <label>Display name<input value={name} onChange={event => { identityEpoch.current++; setName(event.target.value); }} maxLength={120} required /></label>
          <label>Professional headline<input value={headline} onChange={event => { identityEpoch.current++; setHeadline(event.target.value); }} maxLength={300} /></label>
          <p className="hint">Optional contact values are printable-only structured inputs, excluded from model projections. Existing documents retain their frozen details; saving does not rewrite them.</p>
          <label>Printable email (optional)<input type="email" autoComplete="email" maxLength={254} value={contact.email} onChange={event => { identityEpoch.current++; setContact(value => ({ ...value, email: event.target.value })); }} /></label>
          <label>Printable phone (optional)<input type="tel" autoComplete="tel" maxLength={120} value={contact.phone} onChange={event => { identityEpoch.current++; setContact(value => ({ ...value, phone: event.target.value })); }} /></label>
          <label>Printable location (optional)<input autoComplete="address-level2" maxLength={300} value={contact.location} onChange={event => { identityEpoch.current++; setContact(value => ({ ...value, location: event.target.value })); }} /></label>
          <label>Printable postal address (optional)<input autoComplete="street-address" maxLength={1000} value={contact.postal_address} onChange={event => { identityEpoch.current++; setContact(value => ({ ...value, postal_address: event.target.value })); }} /></label>
          <p className="hint">Save identity separately from entries and sources. Existing exact source links are retained. Unsaved changes prevent conflicting workspace actions.</p>
          <div className="actions"><button disabled={connectionPaused}>Save profile</button><button type="button" className="secondary" onClick={() => { void act(async () => { const value = await api.request<Profile>('/profile'); if (value.id === latestProfileId.current && value.version >= latestProfileVersion.current) setComparison(value); else if (value.id !== latestProfileId.current) { setComparison(value); api.onUnavailable?.(new ApiError(409, 'workspace_identity_changed')); } }); }}>Compare current revision</button>
            {identityDirty && <button type="button" className="secondary" onClick={() => acceptProfile(profile)}>Discard identity changes</button>}</div>
          {comparison && <div className="comparison"><h3>Current server revision {comparison.current_revision.revision_no}</h3><p>{comparison.current_revision.snapshot.display_name}</p><p>{comparison.current_revision.snapshot.headline}</p><p>Printable contact: {Object.values(comparison.current_revision.snapshot.contact ?? emptyContact).filter(Boolean).join(' · ') || 'None'}</p><p>Your editor above is unchanged.</p>
            <button type="button" className="secondary" disabled={connectionPaused || comparison.id !== profile.id} onClick={() => { latestProfileVersion.current = comparison.version; identityEpoch.current++; setProfile(comparison); setComparison(null); setNotice('Current version loaded as the save precondition. Your edited text and printable contact are retained. Review and save explicitly.'); }}>Keep my text against this version</button>
            <button type="button" className="secondary" disabled={connectionPaused || comparison.id !== profile.id} onClick={() => acceptProfile(comparison)}>Replace editor with current revision</button></div>}
          {profile.quality_warnings.length > 0 && <ul>{profile.quality_warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>}
        </fieldset></form> : <><p>Profile has not loaded. No sample data is being shown.</p><button disabled={busy} onClick={() => { void act(loadProfile); }}>Load profile</button></>}</section>
      {profile && <ProfileEntries api={api} profile={profile} disabled={busy || identityDirty || importBusy || importDirty || documentsDirty || documentsBusy} onProfile={acceptEditorProfile} onDirtyChange={setEntriesDirty} onBusyChange={setEntriesBusy} />}
      </div>
      <div hidden={page !== 'profile/imports'} data-workspace-screen="profile/imports">{profile && <ResumeImport api={api} profile={profile} disabled={busy || identityDirty || entriesBusy || entriesDirty || documentsDirty || documentsBusy} onProfile={acceptEditorProfile} onDirtyChange={setImportDirty} onBusyChange={setImportBusy} />}</div>
      <div hidden={page !== 'opportunities'} data-workspace-screen="opportunities"><JobSources api={api} currentProfile={profile} onProfileChange={observeProfile} disabled={busy || documentsDirty || documentsBusy} onDirtyChange={setJobsDirty} onBusyChange={setJobsBusy} /></div>
      <div hidden={page !== 'activity'} data-workspace-screen="activity"><Tasks api={api} session={session} requestedTask={workflow} disabled={taskResultBlocked} resultDisabled={taskResultBlocked} onSession={setSession} onWorkflowChange={observeTask} onOpenDocument={openTaskDocument} onBusyChange={setTasksBusy} /></div>
      <div hidden={page !== 'settings'} data-workspace-screen="settings"><section className="panel workspace-principles"><h2>Your workspace, your authority</h2><p>Local résumé import and base documents need no model. Tailoring requires an explicitly authorized and qualified route. Imported facts are owner assertions, not independently verified credentials. No hosted fallback, automatic rejection, application submission or sending authority is enabled.</p><details><summary>Development and review boundaries</summary><p>This is a private prototype. Structural checks do not prove semantic meaning or independently verify credentials. Real-model and human factual qualification remain outstanding. Greenhouse reads require board-specific terms review and authorization. Tracking Applied is not proof of delivery.</p></details></section>
      <details className="panel"><summary>Privacy, backup and portable data</summary><DataPortability api={api} profile={profile} disabled={taskResultBlocked || !!(workflow && !terminal(workflow.state))} onBusyChange={setPortabilityBusy} /></details>
      <details className="advanced-tools"><summary>Advanced diagnostic tools</summary>
      <section className="panel"><h2>Evidence proof</h2><p>Explicitly load SYN-CORE: fictional Mira Vale, Linux and SQL evidence. This replaces the saved proof profile; it is not imported personal history. Benchmark tasks are synthetic mechanisms, separate from production composition.</p>{benchmark ? <><button className="secondary" disabled={profileLocked || !profile || !!(workflow && !terminal(workflow.state))} onClick={() => { if (profile) void benchmarkAction(async () => { acceptProfile(await api.request<Profile>('/benchmarks/profile', 'POST', { fixture: 'SYN-CORE' }, profile.version, true)); setNotice('SYN-CORE saved by the server.'); }); }}>Load SYN-CORE benchmark evidence</button><label>Proof scenario<select value={scenario} disabled={profileLocked} onChange={event => setScenario(event.target.value)}><option value="normal">Normal task</option><option value="slow">Slow task (cancellation proof)</option><option value="poison">Poison task (bounded failure proof)</option><option value="stage_pause">Stage pause (restart proof)</option></select></label><button disabled={profileLocked || !profile || profile.current_revision.snapshot.claim_revision_ids.length === 0 || !!(workflow && !terminal(workflow.state))} onClick={() => { if (profile) void benchmarkAction(async () => { const value = await api.request<Workflow>('/benchmarks/tasks', 'POST', { profile_revision_id: profile.current_revision.id, scenario, delay_seconds: scenario === 'normal' ? 0 : 15 }, profile.version, true); setWorkflow(value); }); }}>Start synthetic proof task</button></> : <p className="hint">Benchmark endpoints are disabled here. No synthetic task was started.</p>}
      </section>
      </details>
      <details className="panel"><summary>Confirm owner identity</summary>
        <p>Confirm your password before creating a destructive preview. Confirmation rotates this browser’s session and invalidates approvals tied to its previous session. It does not approve, replace or delete data; unsaved editors remain here.</p>
        <form onSubmit={event => { void reauthenticate(event); }}>
          <label>Password to confirm owner identity<input type="password" name="current_password" autoComplete="current-password" required maxLength={128} disabled={busy} /></label>
          <button type="submit" disabled={connectionPaused || busy || entriesBusy || importBusy || jobsBusy || trackingBusy || documentsBusy}>Confirm owner identity</button>
        </form>
      </details>
      <details className="panel"><summary>Change password</summary><form onSubmit={event => { event.preventDefault(); const form = event.currentTarget; const values = new FormData(form); void act(async () => { const value = await api.request<Session>('/session/password', 'POST', { current_password: String(values.get('current_password')), new_password: String(values.get('new_password')) }); api.csrf = value.csrf_token; setSession(value); form.reset(); setNotice('Password changed. This session has been rotated.'); }); }}><label>Current password<input type="password" name="current_password" autoComplete="current-password" required maxLength={128} /></label><label>New password<input type="password" name="new_password" autoComplete="new-password" minLength={15} maxLength={128} required /></label><button disabled={connectionPaused || busy}>Change password</button></form></details></div>
      <div hidden={page !== 'documents'} data-workspace-screen="documents"><Documents api={api} profile={profile} disabled={busy || identityDirty || entriesDirty || entriesBusy || importDirty || importBusy || jobsDirty || jobsBusy || trackingDirty || trackingBusy || !!(workflow && !terminal(workflow.state))} externalDocumentId={documentRequest?.id ?? null} externalRequestKey={documentRequest?.key ?? null} onDirtyChange={setDocumentsDirty} onBusyChange={setDocumentsBusy} /></div>
      {page === 'not-found' && <section className="panel"><h2>This workspace page does not exist</h2><a href="#/overview">Return to overview</a></section>}</div>}
    </main><footer>Offerloom · Private workspace · Review before export · No sending authority</footer>
  </div>;
}
const defaultApi = new Api();
