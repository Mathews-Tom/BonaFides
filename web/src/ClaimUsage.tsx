// SPDX-License-Identifier: MIT OR Apache-2.0
import { useEffect, useRef, useState } from 'react';
import { errorText, type Api, type ApplicationClaims, type ClaimUsage, type ClaimUsageItem } from './api';

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
    catch (failure) { if (!active.signal.aborted) setError(errorText(failure)); }
    finally { if (!active.signal.aborted) setLoading(false); }
  }
  return { value, error, loading, load };
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
