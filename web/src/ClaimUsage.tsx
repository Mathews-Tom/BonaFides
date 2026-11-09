// SPDX-License-Identifier: MIT OR Apache-2.0
import { useEffect, useRef, useState } from 'react';
import { errorText, type Api, type ApplicationClaims, type ClaimOutcomes, type ClaimUsage, type ClaimUsageItem } from './api';
import { stageName } from './tracking-api';

export const USAGE_RULE = 'Only factual statements that passed the evidence check count as usage. Blocked statements, greetings and statements of intent are not listed.';
const kindName = (kind: ClaimUsageItem['kind']) => kind === 'resume' ? 'Résumé' : 'Cover letter';

function Pin({ item }: { item: ClaimUsageItem }) {
  return <>Claim revision {item.claim_revision_no}{item.superseded ? ' · superseded by a later edit' : ' · current'}{item.archived ? ' · claim archived' : ''}</>;
}

function Statements({ item }: { item: ClaimUsageItem }) {
  return <ul>{item.statements.map(statement => <li key={statement}><q>{statement}</q></li>)}</ul>;
}

function placeName(item: ClaimUsageItem): string {
  if (!item.application) return 'Base résumé';
  const { title, company, attempt_no: attempt, archived } = item.application;
  return `${title} at ${company} · attempt ${attempt}${archived ? ' · application archived' : ''} · ${kindName(item.kind)}`;
}

function UsageList({ items }: { items: ClaimUsageItem[] }) {
  return <ul>{items.map(item => <li key={`${item.document_revision_id}-${item.claim_revision_id}`}>
    <p><strong>{placeName(item)}</strong> · document revision {item.document_revision_no} · <Pin item={item} /></p><Statements item={item} />
  </li>)}</ul>;
}

function useLoader<T>() {
  const [value, setValue] = useState<T | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  async function load(read: (signal: AbortSignal) => Promise<T>) {
    controller.current?.abort();
    const active = new AbortController(); controller.current = active;
    setLoading(true); setError('');
    try { const result = await read(active.signal); if (!active.signal.aborted) setValue(result); }
    catch (failure) { if (!active.signal.aborted) { setValue(null); setError(errorText(failure)); } }
    finally { if (!active.signal.aborted) setLoading(false); }
  }
  return { value, error, loading, load };
}

function ApplicationOutcomes({ outcomes }: { outcomes: ClaimOutcomes }) {
  return <section aria-label="Application outcomes"><h5>Application outcomes</h5>
    <p>Application attempts: {outcomes.application_count} · Archived attempts included: {outcomes.archived_application_count} · No positive progress: {outcomes.no_progression_count} · Applied inferred from rejection: {outcomes.inferred_applied_from_rejection}.</p>
    <p className="hint">Small-sample threshold: {outcomes.small_sample_threshold} application attempts.</p>
    {outcomes.small_sample && <p className="disclosure">Small sample: fewer than {outcomes.small_sample_threshold} application attempts. Treat these counts cautiously.</p>}
    <table aria-label="Application outcomes">
      <thead><tr><th scope="col">Stage</th><th scope="col">Furthest stage</th><th scope="col">Reached at least</th><th scope="col">Reach (k/N)</th></tr></thead>
      <tbody>{outcomes.stages.map(stage => <tr key={stage.stage}>
        <th scope="row">{stageName(stage.stage)}</th><td>{stage.furthest_count}</td><td>{stage.reached_count}</td>
        <td>{stage.reached_count}/{outcomes.application_count} ({stage.rate === null ? 'Unavailable' : stage.rate.toLocaleString('en', { style: 'percent', maximumFractionDigits: 1 })})</td>
      </tr>)}</tbody>
    </table>
    <p className="hint">Distinct application attempts whose current, nondeleted checked résumé or cover-letter revisions use this claim, including older pinned claim revisions. Résumé and letter count once per attempt; repeats count separately. Base résumés, blocked statements and earlier document revisions do not add attempts. Expanding earlier document history does not change this cohort.</p>
    <p className="hint">Archived and Preparing attempts are included in every denominator. Progress uses all recorded valid events, including future-dated events; corrections replace prior events. Ordinary backward moves retain the furthest stage.</p>
    <p className="hint">Furthest stage counts each attempt at its peak only. Reached at least infers earlier stages from that peak; it does not mean every stage was recorded. Rejection without a positive stage infers Applied. Preparing or Withdrawn alone is no positive progress.</p>
    <p className="hint">Manually recorded, unverified tracking: not proof of delivery or verified employer response. Reach is not a response rate and does not show that this claim caused an outcome.</p>
  </section>;
}

export function ClaimUsedIn({ api, claimId, label }: { api: Api; claimId: string; label: string }) {
  const usage = useLoader<ClaimUsage>();
  const read = (history: boolean) => usage.load(signal => api.request<ClaimUsage>(`/claims/${encodeURIComponent(claimId)}/usage${history ? '?history=true' : ''}`, 'GET', undefined, undefined, false, signal));
  return <div className="claim-usage">
    <button type="button" className="secondary" disabled={usage.loading} onClick={() => { void read(false); }}>{usage.value ? `Refresh where ${label} is used` : `Show where ${label} is used`}</button>
    {usage.loading && <p role="status">Loading usage…</p>}
    {usage.error && <p className="error" role="status">Usage unavailable, not empty. {usage.error}</p>}
    {usage.value && <section aria-label={`Used in: ${label}`}><h4>Used in</h4><p className="hint">{USAGE_RULE} Each entry shows the claim revision the document pinned.</p>
      {usage.value.items.length ? <UsageList items={usage.value.items} /> : <p>Not used by any current document revision.</p>}
      <ApplicationOutcomes outcomes={usage.value.outcomes} />
      {usage.value.history ? <><h5>Earlier document revisions</h5>{usage.value.history.length ? <UsageList items={usage.value.history} /> : <p>No earlier document revisions used this claim.</p>}</>
        : <button type="button" className="secondary" disabled={usage.loading} onClick={() => { void read(true); }}>Include earlier document revisions</button>}
    </section>}
  </div>;
}

export function ApplicationClaimsUsed({ api, applicationId }: { api: Api; applicationId: string }) {
  const claims = useLoader<ApplicationClaims>();
  const read = () => claims.load(signal => api.request<ApplicationClaims>(`/applications/${encodeURIComponent(applicationId)}/claims`, 'GET', undefined, undefined, false, signal));
  return <section aria-labelledby="application-claims-title"><h4 id="application-claims-title">Claims used</h4>
    <p className="hint">Claims cited by the current revision of this application’s résumé and cover letter. {USAGE_RULE} The base résumé is listed on each claim, not here.</p>
    <button type="button" className="secondary" disabled={claims.loading} onClick={() => { void read(); }}>{claims.value ? 'Refresh claims used' : 'Show claims used'}</button>
    {claims.loading && <p role="status">Loading claims used…</p>}
    {claims.error && <p className="error" role="status">Claims used unavailable, not empty. {claims.error}</p>}
    {claims.value && (claims.value.items.length ? (['resume', 'cover_letter'] as const).map(kind => {
      const items = claims.value?.items.filter(item => item.kind === kind) ?? [];
      const [first] = items;
      return first && <section key={kind} aria-label={`Claims used: ${kindName(kind)}`}><h5>{kindName(kind)} · document revision {first.document_revision_no}</h5>
        <ul>{items.map(item => <li key={item.claim_revision_id}><p><strong>{item.claim_text}</strong> · <Pin item={item} /></p><Statements item={item} /></li>)}</ul></section>;
    }) : <p>No saved document of this application cites a claim.</p>)}
  </section>;
}
