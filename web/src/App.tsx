// SPDX-License-Identifier: MIT OR Apache-2.0
import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { Api, ApiError, errorText, terminal, type Challenge, type Profile, type ProfileContact, type Session, type Workflow } from './api';
import { ProfileEntries } from './ProfileEntries';
import { ResumeImport } from './ResumeImport';
import { JobSources } from './JobSources';
import { ThemePreference } from './ThemePreference';
import { Tracking } from './Tracking';
import { Documents } from './Documents';
import { Tasks } from './Tasks';
const emptyContact: ProfileContact = { email: '', phone: '', location: '', postal_address: '' };

export function App({ api = defaultApi }: { api?: Api }) {
  const [session, setSession] = useState<Session | null>(null);
  const [challenge, setChallenge] = useState<Challenge | null>(null);
  const [ready, setReady] = useState(false);
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
  const busy = acting || tasksBusy;
  const [documentRequest, setDocumentRequest] = useState<{ id: string; key: string } | null>(null);
  const [scenario, setScenario] = useState('normal');
  const errorRef = useRef<HTMLDivElement>(null);
  useEffect(() => { if (error) errorRef.current?.focus(); }, [error]);
  const latestProfileVersion = useRef(0);
  const identityEpoch = useRef(0);
  const acceptProfile = useCallback((value: Profile) => {
    if (value.version < latestProfileVersion.current) return;
    latestProfileVersion.current = value.version;
    setProfile(value); setName(value.current_revision.snapshot.display_name); setHeadline(value.current_revision.snapshot.headline);
    setContact(value.current_revision.snapshot.contact ?? emptyContact); setComparison(null); identityEpoch.current++;
  }, []);
  const loadProfile = useCallback(async () => {
    const epoch = identityEpoch.current; const value = await api.request<Profile>('/profile');
    if (epoch !== identityEpoch.current) { if (value.version >= latestProfileVersion.current) setComparison(value); return; }
    acceptProfile(value);
  }, [api, acceptProfile]);
  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const value = await api.request<Session>('/session');
        if (!active) return;
        api.csrf = value.csrf_token; setSession(value); await loadProfile();
      } catch (failure) {
        if (!active) return;
        if (!(failure instanceof ApiError && failure.status === 401)) setError(errorText(failure));
        try { const value = await api.request<Challenge>('/auth/challenge'); if (active) { api.csrf = value.csrf_token; setChallenge(value); } }
        catch (failure) { if (active) setError(errorText(failure)); }
      } finally { if (active) setReady(true); }
    })();
    return () => { active = false; };
  }, [api, loadProfile]);
  async function act(action: () => Promise<void>) {
    setBusy(true); setError(''); setNotice('');
    try { await action(); } catch (failure) { setError(errorText(failure)); } finally { setBusy(false); }
  }
  async function authenticate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = event.currentTarget; const values = new FormData(form);
    await act(async () => {
      const body = { login_name: String(values.get('login_name')), password: String(values.get('password')), ...(challenge?.bootstrap_available ? { bootstrap_token: String(values.get('bootstrap_token')) } : {}) };
      const value = await api.request<Session>(challenge?.bootstrap_available ? '/bootstrap' : '/session', 'POST', body);
      api.csrf = value.csrf_token; setSession(value); form.reset(); await loadProfile(); setNotice('Signed in.');
    });
  }
  const profileMutation = useRef<{ fingerprint: string; key: string } | null>(null);
  const identityDirty = !!profile && (name !== profile.current_revision.snapshot.display_name || headline !== profile.current_revision.snapshot.headline || (['email', 'phone', 'location', 'postal_address'] as const).some(field => contact[field] !== (profile.current_revision.snapshot.contact ?? emptyContact)[field]));
  const profileLocked = busy || entriesBusy || entriesDirty || identityDirty || importBusy || importDirty || documentsDirty || documentsBusy;
  const observeProfile = useCallback((value: Profile) => {
    if (profile && value.version <= profile.version) return;
    if (identityDirty) setComparison(value);
    else acceptProfile(value);
  }, [profile, identityDirty, acceptProfile]);
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
  return <>
    <a className="skip" href="#main">Skip to workspace</a>
    <header><div className="brand"><svg aria-hidden="true" viewBox="0 0 32 36"><path d="M6 2v32M16 2v32M26 2v32M2 10h28M2 26h28M6 10l20 16M26 10 6 26" /></svg><span>Offerloom</span></div><span className="edition">Private career workspace</span><ThemePreference />{session && <button className="secondary" disabled={profileLocked || jobsDirty || jobsBusy || trackingDirty || trackingBusy || !!(workflow && !terminal(workflow.state))} onClick={() => { void act(async () => { await api.request('/session', 'DELETE'); setSession(null); setProfile(null); setName(''); setHeadline(''); setContact(emptyContact); setDocumentRequest(null); setWorkflow(null); latestProfileVersion.current = 0; identityEpoch.current++; const value = await api.request<Challenge>('/auth/challenge'); api.csrf = value.csrf_token; setChallenge(value); }); }}>Sign out</button>}</header>
    <main id="main"><aside className="disclosure"><strong>Private prototype workspace.</strong> Evidence-linked profiles, reviewed local résumé import, catalog decisions, owner-reviewed requirements/evidence coverage and manual activity tracking remain available. A base résumé needs no model or application; tailored generation requires reviewed requirements and an explicitly authorized, qualified local route. None is enabled by default. Source/structural checks do not prove semantic meaning or independently verify credentials. Real-model and human factual qualification remain outstanding. Greenhouse reads require board-specific terms review and authorization. Tracking Applied is not proof of delivery. No hosted fallback, automatic rejection, submission or sending authority.</aside>
      <div ref={errorRef} className="error" role="alert" tabIndex={-1} hidden={!error}>{error}</div><p role="status" className="status">{notice}</p>
      {!ready ? <p role="status">Connecting to your installation…</p> : !session ? <section className="access"><div><h1>A dossier built on evidence.</h1><p>Keep the statement, its source and your review together. This proof starts with a secure owner session.</p></div><form onSubmit={event => { void authenticate(event); }}><h2>{challenge?.bootstrap_available ? 'Set up the installation owner' : 'Sign in'}</h2>{challenge?.bootstrap_available && <label>One-use bootstrap token<input name="bootstrap_token" type="password" autoComplete="off" required /></label>}<label>Login name<input name="login_name" autoComplete="username" required maxLength={128} /></label><label>Password<input name="password" type="password" autoComplete={challenge?.bootstrap_available ? 'new-password' : 'current-password'} minLength={15} maxLength={128} required /></label><p className="hint">15–128 characters. Credentials stay in this form until sent to your installation; no browser token storage.</p><button disabled={busy || !challenge}>{busy ? 'Connecting…' : challenge?.bootstrap_available ? 'Create owner account' : 'Sign in'}</button></form></section> : <>
      <div className="workspace-heading"><div><h1>Facts you can stand behind</h1><p>Signed in as {session.login_name}. Work from a saved, immutable profile revision.</p></div>{profile && <span className="revision">Profile revision {profile.current_revision.revision_no}</span>}</div>
      <Tracking api={api} disabled={busy || documentsDirty || documentsBusy} onDirtyChange={setTrackingDirty} onBusyChange={setTrackingBusy} />
      <div className="workspace"><div><section className="panel"><h2>Your profile</h2>{profile ? <form onSubmit={event => { void saveProfile(event); }}>
        <fieldset disabled={busy || entriesBusy || entriesDirty || importBusy || importDirty || documentsDirty || documentsBusy}><legend>Profile identity</legend>
          <label>Display name<input value={name} onChange={event => { identityEpoch.current++; setName(event.target.value); }} maxLength={120} required /></label>
          <label>Professional headline<input value={headline} onChange={event => { identityEpoch.current++; setHeadline(event.target.value); }} maxLength={300} /></label>
          <p className="hint">Optional contact values are printable-only structured inputs, excluded from model projections. Existing documents retain their frozen details; saving does not rewrite them.</p>
          <label>Printable email (optional)<input type="email" autoComplete="email" maxLength={254} value={contact.email} onChange={event => { identityEpoch.current++; setContact(value => ({ ...value, email: event.target.value })); }} /></label>
          <label>Printable phone (optional)<input type="tel" autoComplete="tel" maxLength={120} value={contact.phone} onChange={event => { identityEpoch.current++; setContact(value => ({ ...value, phone: event.target.value })); }} /></label>
          <label>Printable location (optional)<input autoComplete="address-level2" maxLength={300} value={contact.location} onChange={event => { identityEpoch.current++; setContact(value => ({ ...value, location: event.target.value })); }} /></label>
          <label>Printable postal address (optional)<input autoComplete="street-address" maxLength={1000} value={contact.postal_address} onChange={event => { identityEpoch.current++; setContact(value => ({ ...value, postal_address: event.target.value })); }} /></label>
          <p className="hint">Save identity separately from entries and sources. Existing exact source links are retained. Unsaved changes prevent conflicting workspace actions.</p>
          <div className="actions"><button>Save profile</button><button type="button" className="secondary" onClick={() => { void act(async () => { const value = await api.request<Profile>('/profile'); if (value.version >= latestProfileVersion.current) setComparison(value); }); }}>Compare current revision</button>
            {identityDirty && <button type="button" className="secondary" onClick={() => acceptProfile(profile)}>Discard identity changes</button>}</div>
          {comparison && <div className="comparison"><h3>Current server revision {comparison.current_revision.revision_no}</h3><p>{comparison.current_revision.snapshot.display_name}</p><p>{comparison.current_revision.snapshot.headline}</p><p>Printable contact: {Object.values(comparison.current_revision.snapshot.contact ?? emptyContact).filter(Boolean).join(' · ') || 'None'}</p><p>Your editor above is unchanged.</p>
            <button type="button" className="secondary" onClick={() => { latestProfileVersion.current = comparison.version; identityEpoch.current++; setProfile(comparison); setComparison(null); setNotice('Current version loaded as the save precondition. Your edited text and printable contact are retained. Review and save explicitly.'); }}>Keep my text against this version</button>
            <button type="button" className="secondary" onClick={() => acceptProfile(comparison)}>Replace editor with current revision</button></div>}
          {profile.quality_warnings.length > 0 && <ul>{profile.quality_warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>}
        </fieldset></form> : <><p>Profile has not loaded. No sample data is being shown.</p><button disabled={busy} onClick={() => { void act(loadProfile); }}>Load profile</button></>}</section>
      {profile && <ProfileEntries api={api} profile={profile} disabled={busy || identityDirty || importBusy || importDirty || documentsDirty || documentsBusy} onProfile={acceptProfile} onDirtyChange={setEntriesDirty} onBusyChange={setEntriesBusy} />}
      {profile && <ResumeImport api={api} profile={profile} disabled={busy || identityDirty || entriesBusy || entriesDirty || documentsDirty || documentsBusy} onProfile={acceptProfile} onDirtyChange={setImportDirty} onBusyChange={setImportBusy} />}
      <JobSources api={api} currentProfile={profile} onProfileChange={observeProfile} disabled={busy || documentsDirty || documentsBusy} onDirtyChange={setJobsDirty} onBusyChange={setJobsBusy} />
      <section className="panel"><h2>Evidence proof</h2><p>Explicitly load SYN-CORE: fictional Mira Vale, Linux and SQL evidence. This replaces the saved proof profile; it is not imported personal history. Benchmark tasks are synthetic mechanisms, separate from production composition.</p>{benchmark ? <><button className="secondary" disabled={profileLocked || !profile || !!(workflow && !terminal(workflow.state))} onClick={() => { if (profile) void benchmarkAction(async () => { acceptProfile(await api.request<Profile>('/benchmarks/profile', 'POST', { fixture: 'SYN-CORE' }, profile.version, true)); setNotice('SYN-CORE saved by the server.'); }); }}>Load SYN-CORE benchmark evidence</button><label>Proof scenario<select value={scenario} disabled={profileLocked} onChange={event => setScenario(event.target.value)}><option value="normal">Normal task</option><option value="slow">Slow task (cancellation proof)</option><option value="poison">Poison task (bounded failure proof)</option><option value="stage_pause">Stage pause (restart proof)</option></select></label><button disabled={profileLocked || !profile || profile.current_revision.snapshot.claim_revision_ids.length === 0 || !!(workflow && !terminal(workflow.state))} onClick={() => { if (profile) void benchmarkAction(async () => { const value = await api.request<Workflow>('/benchmarks/tasks', 'POST', { profile_revision_id: profile.current_revision.id, scenario, delay_seconds: scenario === 'normal' ? 0 : 15 }, profile.version, true); setWorkflow(value); }); }}>Start synthetic proof task</button></> : <p className="hint">Benchmark endpoints are disabled here. No synthetic task was started.</p>}
      </section>
      <Tasks api={api} session={session} requestedTask={workflow} disabled={taskResultBlocked} resultDisabled={taskResultBlocked} onSession={setSession} onWorkflowChange={observeTask} onOpenDocument={openTaskDocument} onBusyChange={setTasksBusy} />
      <details className="panel"><summary>Change password</summary><form onSubmit={event => { event.preventDefault(); const form = event.currentTarget; const values = new FormData(form); void act(async () => { const value = await api.request<Session>('/session/password', 'POST', { current_password: String(values.get('current_password')), new_password: String(values.get('new_password')) }); api.csrf = value.csrf_token; setSession(value); form.reset(); setNotice('Password changed. This session has been rotated.'); }); }}><label>Current password<input type="password" name="current_password" autoComplete="current-password" required maxLength={128} /></label><label>New password<input type="password" name="new_password" autoComplete="new-password" minLength={15} maxLength={128} required /></label><button disabled={busy}>Change password</button></form></details></div>
      <Documents api={api} profile={profile} disabled={busy || identityDirty || entriesDirty || entriesBusy || importDirty || importBusy || jobsDirty || jobsBusy || trackingDirty || trackingBusy || !!(workflow && !terminal(workflow.state))} externalDocumentId={documentRequest?.id ?? null} externalRequestKey={documentRequest?.key ?? null} onDirtyChange={setDocumentsDirty} onBusyChange={setDocumentsBusy} /></div></>}
    </main><footer>Offerloom · Private prototype · Manual tracking and PDF export, not delivery · No sending authority</footer>
  </>;
}
const defaultApi = new Api();
