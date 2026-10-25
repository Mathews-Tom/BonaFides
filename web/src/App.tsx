// SPDX-License-Identifier: MIT OR Apache-2.0
import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { Api, ApiError, errorText, terminal, type Challenge, type DocumentRecord, type Profile, type Session, type Workflow } from './api';
import { ProfileEntries } from './ProfileEntries';
import { ResumeImport } from './ResumeImport';
import { JobSources } from './JobSources';
import { ThemePreference } from './ThemePreference';

export function App({ api = defaultApi }: { api?: Api }) {
  const [session, setSession] = useState<Session | null>(null);
  const [challenge, setChallenge] = useState<Challenge | null>(null);
  const [ready, setReady] = useState(false);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [name, setName] = useState('');
  const [headline, setHeadline] = useState('');
  const [comparison, setComparison] = useState<Profile | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [entriesDirty, setEntriesDirty] = useState(false);
  const [entriesBusy, setEntriesBusy] = useState(false);
  const [importDirty, setImportDirty] = useState(false);
  const [importBusy, setImportBusy] = useState(false);
  const [jobsDirty, setJobsDirty] = useState(false);
  const [jobsBusy, setJobsBusy] = useState(false);
  const [benchmark, setBenchmark] = useState(true);
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [savedTasks, setSavedTasks] = useState<Workflow[] | null>(null);
  const [doc, setDoc] = useState<DocumentRecord | null>(null);
  const [artifact, setArtifact] = useState<string | null>(null);
  const [scenario, setScenario] = useState('normal');
  const errorRef = useRef<HTMLDivElement>(null);
  useEffect(() => { if (error) errorRef.current?.focus(); }, [error]);
  const lastProfileRevision = useRef<string | null>(null);
  const acceptProfile = useCallback((value: Profile) => {
    if (lastProfileRevision.current && lastProfileRevision.current !== value.current_revision.id) {
      setDoc(current => current ? { ...current, stale: true, stale_reason: 'upstream_profile_revision_changed' } : current);
    }
    lastProfileRevision.current = value.current_revision.id;
    setProfile(value); setName(value.current_revision.snapshot.display_name); setHeadline(value.current_revision.snapshot.headline); setComparison(null);
  }, []);
  const loadProfile = useCallback(async () => { acceptProfile(await api.request<Profile>('/profile')); }, [api, acceptProfile]);
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
  const identityDirty = !!profile && (name !== profile.current_revision.snapshot.display_name || headline !== profile.current_revision.snapshot.headline);
  const profileLocked = busy || entriesBusy || entriesDirty || identityDirty || importBusy || importDirty;
  useEffect(() => {
    if (!identityDirty) return;
    const prevent = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    window.addEventListener('beforeunload', prevent);
    return () => window.removeEventListener('beforeunload', prevent);
  }, [identityDirty]);
  async function saveProfile(event: FormEvent) {
    event.preventDefault(); if (!profile) return;
    await act(async () => {
      const body = { ...profile.current_revision.snapshot, display_name: name, headline };
      const fingerprint = JSON.stringify({ body, version: profile.version });
      if (profileMutation.current?.fingerprint !== fingerprint) profileMutation.current = { fingerprint, key: crypto.randomUUID() };
      const value = await api.request<Profile>('/profile', 'PUT', body, profile.version, profileMutation.current.key);
      profileMutation.current = null;
      acceptProfile(value); setNotice(`Saved as revision ${value.current_revision.revision_no}.`);
    });
  }
  const workflowId = workflow?.id;
  const workflowState = workflow?.state;
  useEffect(() => {
    if (!session || !workflowId || !workflowState || terminal(workflowState)) return;
    let stopped = false; let timer: number | undefined; let delay = 1000;
    const poll = async () => {
      if (stopped) return;
      if (document.hidden) { timer = window.setTimeout(() => { void poll(); }, 1000); return; }
      try {
        const value = await api.request<Workflow>(`/workflows/${workflowId}`);
        if (stopped) return;
        setWorkflow(value);
        if (terminal(value.state)) {
          if (value.result?.document_id) setDoc(await api.request<DocumentRecord>(`/documents/${value.result.document_id}`));
          if (value.result?.artifact_id) setArtifact(value.result.artifact_id);
          return;
        }
      } catch (failure) { if (!stopped) setError(errorText(failure)); }
      delay = Math.min(delay + 1000, 5000); if (!stopped) timer = window.setTimeout(() => { void poll(); }, delay);
    };
    timer = window.setTimeout(() => { void poll(); }, delay);
    return () => { stopped = true; clearTimeout(timer); };
  }, [api, session, workflowId, workflowState]);
  async function benchmarkAction(action: () => Promise<void>) {
    await act(async () => { try { await action(); } catch (failure) { if (failure instanceof ApiError && failure.status === 404) { setBenchmark(false); setNotice('Benchmark controls are unavailable on this installation. No synthetic task was started.'); } else throw failure; } });
  }
  return <>
    <a className="skip" href="#main">Skip to workspace</a>
    <header><div className="brand"><svg aria-hidden="true" viewBox="0 0 32 36"><path d="M6 2v32M16 2v32M26 2v32M2 10h28M2 26h28M6 10l20 16M26 10 6 26" /></svg><span>Offerloom</span></div><span className="edition">Private career workspace</span><ThemePreference />{session && <button className="secondary" disabled={profileLocked || jobsDirty || jobsBusy} onClick={() => { void act(async () => { await api.request('/session', 'DELETE'); setSession(null); setProfile(null); setName(''); setHeadline(''); setDoc(null); setWorkflow(null); setArtifact(null); lastProfileRevision.current = null; const value = await api.request<Challenge>('/auth/challenge'); api.csrf = value.csrf_token; setChallenge(value); }); }}>Sign out</button>}</header>
    <main id="main"><aside className="disclosure"><strong>Private development workspace · OL-008.</strong> Evidence-linked profiles, reviewed local résumé import and a durable posting catalog with search, save/dismiss and explicit duplicate review are implemented. Greenhouse board reads require explicit board-specific terms review and enablement; this is not global job discovery or submission authority. Imports remain owner-asserted, not independently verified credentials. Document tasks remain synthetic M0 mechanisms, not a complete M1 product. No hosted extraction, real model runs, matching or applications. Use fictional input until the relevant live-source gates are approved.</aside>
      <div ref={errorRef} className="error" role="alert" tabIndex={-1} hidden={!error}>{error}</div><p role="status" className="status">{notice}</p>
      {!ready ? <p role="status">Connecting to your installation…</p> : !session ? <section className="access"><div><h1>A dossier built on evidence.</h1><p>Keep the statement, its source and your review together. This proof starts with a secure owner session.</p></div><form onSubmit={event => { void authenticate(event); }}><h2>{challenge?.bootstrap_available ? 'Set up the installation owner' : 'Sign in'}</h2>{challenge?.bootstrap_available && <label>One-use bootstrap token<input name="bootstrap_token" type="password" autoComplete="off" required /></label>}<label>Login name<input name="login_name" autoComplete="username" required maxLength={128} /></label><label>Password<input name="password" type="password" autoComplete={challenge?.bootstrap_available ? 'new-password' : 'current-password'} minLength={15} maxLength={128} required /></label><p className="hint">15–128 characters. Credentials stay in this form until sent to your installation; no browser token storage.</p><button disabled={busy || !challenge}>{busy ? 'Connecting…' : challenge?.bootstrap_available ? 'Create owner account' : 'Sign in'}</button></form></section> : <>
      <div className="workspace-heading"><div><h1>Facts you can stand behind</h1><p>Signed in as {session.login_name}. Work from a saved, immutable profile revision.</p></div>{profile && <span className="revision">Profile revision {profile.current_revision.revision_no}</span>}</div>
      <div className="workspace"><div><section className="panel"><h2>Your profile</h2>{profile ? <form onSubmit={event => { void saveProfile(event); }}>
        <fieldset disabled={busy || entriesBusy || entriesDirty || importBusy || importDirty}><legend>Profile identity</legend>
          <label>Display name<input value={name} onChange={event => setName(event.target.value)} maxLength={120} required /></label>
          <label>Professional headline<input value={headline} onChange={event => setHeadline(event.target.value)} maxLength={300} /></label>
          <p className="hint">Save identity separately from entries and sources. Existing exact source links are retained. Unsaved changes prevent conflicting workspace actions.</p>
          <div className="actions"><button>Save profile</button><button type="button" className="secondary" onClick={() => { void act(async () => { setComparison(await api.request<Profile>('/profile')); }); }}>Compare current revision</button>
            {identityDirty && <button type="button" className="secondary" onClick={() => acceptProfile(profile)}>Discard identity changes</button>}</div>
          {comparison && <div className="comparison"><h3>Current server revision {comparison.current_revision.revision_no}</h3><p>{comparison.current_revision.snapshot.display_name}</p><p>{comparison.current_revision.snapshot.headline}</p><p>Your editor above is unchanged.</p>
            <button type="button" className="secondary" onClick={() => { setProfile(comparison); setComparison(null); setNotice('Current version loaded as the save precondition. Your edited text is retained.'); }}>Keep my text against this version</button>
            <button type="button" className="secondary" onClick={() => acceptProfile(comparison)}>Replace editor with current revision</button></div>}
          {profile.quality_warnings.length > 0 && <ul>{profile.quality_warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>}
        </fieldset></form> : <><p>Profile has not loaded. No sample data is being shown.</p><button disabled={busy} onClick={() => { void act(loadProfile); }}>Load profile</button></>}</section>
      {profile && <ProfileEntries api={api} profile={profile} disabled={busy || identityDirty || importBusy || importDirty} onProfile={acceptProfile} onDirtyChange={setEntriesDirty} onBusyChange={setEntriesBusy} />}
      {profile && <ResumeImport api={api} profile={profile} disabled={busy || identityDirty || entriesBusy || entriesDirty} onProfile={acceptProfile} onDirtyChange={setImportDirty} onBusyChange={setImportBusy} />}
      <JobSources api={api} disabled={busy} onDirtyChange={setJobsDirty} onBusyChange={setJobsBusy} />
      <section className="panel"><h2>Evidence proof</h2><p>Explicitly load SYN-CORE: fictional Mira Vale, Linux and SQL evidence. This replaces the saved proof profile; it is not imported personal history.</p>{benchmark ? <><button className="secondary" disabled={profileLocked || !profile} onClick={() => { if (profile) void benchmarkAction(async () => { acceptProfile(await api.request<Profile>('/benchmarks/profile', 'POST', { fixture: 'SYN-CORE' }, profile.version, true)); setNotice('SYN-CORE saved by the server.'); }); }}>Load SYN-CORE benchmark evidence</button><label>Proof scenario<select value={scenario} onChange={event => setScenario(event.target.value)}><option value="normal">Normal task</option><option value="slow">Slow task (cancellation proof)</option><option value="poison">Poison task (bounded failure proof)</option><option value="stage_pause">Stage pause (restart proof)</option></select></label><button disabled={profileLocked || !profile || profile.current_revision.snapshot.claim_revision_ids.length === 0 || !!(workflow && !terminal(workflow.state))} onClick={() => { if (profile) void benchmarkAction(async () => { const value = await api.request<Workflow>('/benchmarks/tasks', 'POST', { profile_revision_id: profile.current_revision.id, scenario, delay_seconds: scenario === 'normal' ? 0 : 15 }, profile.version, true); setWorkflow(value); setDoc(null); setArtifact(null); }); }}>Start synthetic proof task</button></> : <p className="hint">Benchmark endpoints are disabled here. No production provider is configured by this proof UI.</p>}
      <div className="saved-tasks"><button className="secondary" disabled={busy} onClick={() => { void act(async () => { setSavedTasks((await api.request<{ items: Workflow[] }>('/workflows')).items); }); }}>Load saved tasks</button>{savedTasks && (savedTasks.length ? <ul>{savedTasks.map(task => <li key={task.id}><button className="secondary" disabled={busy} onClick={() => { void act(async () => { const value = await api.request<Workflow>(`/workflows/${task.id}`); setWorkflow(value); setDoc(value.result?.document_id ? await api.request<DocumentRecord>(`/documents/${value.result.document_id}`) : null); setArtifact(value.result?.artifact_id ?? null); }); }}>{task.state.replaceAll('_', ' ')} · {task.id}</button></li>)}</ul> : <p>No saved tasks were returned.</p>)}</div>
      {workflow && <div className="task" aria-live="polite"><h3>Task state: {workflow.state.replaceAll('_', ' ')}</h3><p>Attempt {workflow.attempt}. No estimated progress percentage.</p><small>Task {workflow.id}</small>{workflow.last_error_code && <p>Failure code: {workflow.last_error_code}</p>}{!terminal(workflow.state) && <button className="secondary" disabled={busy} onClick={() => { void act(async () => { setWorkflow(await api.request<Workflow>(`/workflows/${workflow.id}/cancel`, 'POST', {}, workflow.version, true)); setNotice('Cancellation requested. The task may still be stopping.'); }); }}>Cancel task</button>}</div>}</section>
      <details className="panel"><summary>Change password</summary><form onSubmit={event => { event.preventDefault(); const form = event.currentTarget; const values = new FormData(form); void act(async () => { const value = await api.request<Session>('/session/password', 'POST', { current_password: String(values.get('current_password')), new_password: String(values.get('new_password')) }); api.csrf = value.csrf_token; setSession(value); form.reset(); setNotice('Password changed. This session has been rotated.'); }); }}><label>Current password<input type="password" name="current_password" autoComplete="current-password" required maxLength={128} /></label><label>New password<input type="password" name="new_password" autoComplete="new-password" minLength={15} maxLength={128} required /></label><button disabled={busy}>Change password</button></form></details></div>
      <section className="document-panel" aria-labelledby="document-title"><h2 id="document-title">Read the words and their sources</h2>{doc ? <>
        {doc.stale && <aside className="disclosure"><strong>Outdated profile inputs.</strong> This document uses an earlier saved profile. Its words, source links, exact-revision review and existing PDF are unchanged. Starting a new synthetic task is an explicit regeneration, not an automatic rewrite.</aside>}
        <article className="paper"><h2>{doc.current_revision.body.title}</h2>{doc.current_revision.body.sections.map(section => <section key={section.key}><h3>{section.heading}</h3>{section.blocks.map(block => <div className="evidence-seam" key={block.block_key}><p>{block.text}</p><details><summary>Statement sources ({block.claim_kind})</summary><p>Claim revisions: {block.claim_revision_ids.join(', ') || 'None'}</p><p>Evidence revisions: {block.evidence_revision_ids.join(', ') || 'None'}</p><p>Source references are immutable IDs, not independent verification.</p></details></div>)}</section>)}</article><p className="hint">Revision {doc.current_revision.id}</p>{doc.unsupported_claims.length > 0 ? <div className="error"><strong>Export blocked: unsupported claims.</strong><ul>{doc.unsupported_claims.map(pointer => <li key={pointer}>{pointer}</li>)}</ul><p>Review cannot waive unsupported factual content.</p></div> : <div className="actions"><button disabled={busy || entriesBusy || entriesDirty || identityDirty || doc.reviewed} onClick={() => { void act(async () => { setDoc(await api.request<DocumentRecord>(`/documents/${doc.id}/review`, 'POST', { revision_id: doc.current_revision.id, decision: 'accepted', content_sha256: doc.current_revision.content_sha256 }, doc.version, true)); setNotice('Review saved for this exact revision and content hash.'); }); }}>{doc.reviewed ? 'Exact revision reviewed' : 'Accept this exact revision'}</button><button disabled={busy || entriesBusy || entriesDirty || identityDirty || !doc.reviewed || !!(workflow && !terminal(workflow.state))} onClick={() => { void act(async () => { setWorkflow(await api.request<Workflow>(`/documents/${doc.id}/renders`, 'POST', { revision_id: doc.current_revision.id }, doc.version, true)); setArtifact(null); }); }}>Render reviewed PDF</button></div>}{artifact && <p className="download"><a href={`/api/v1/artifacts/${encodeURIComponent(artifact)}`} download>Download rendered PDF</a><span>Actual renderer output. Nothing was sent.</span></p>}</> : <div className="empty"><p>A document will appear after a saved evidence profile completes a proof task.</p><p>No invented résumé, letter or fit score is shown while you wait.</p></div>}</section></div></>}
    </main><footer>Offerloom · OL-008 private development · Durable catalog and reviewed decisions · No sending authority</footer>
  </>;
}
const defaultApi = new Api();
