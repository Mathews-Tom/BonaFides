// SPDX-License-Identifier: MIT OR Apache-2.0
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Api, ApiError, type ApplicationClaims, type ClaimUsage, type ClaimUsageItem } from './api';
import { ApplicationClaimsUsed, ClaimUsedIn } from './ClaimUsage';

const application = { id: 'application-1', title: 'Synthetic Platform Engineer', company: 'Example Works', attempt_no: 1, archived: false };
function usageItem(changes: Partial<ClaimUsageItem>): ClaimUsageItem {
  return {
    scope: 'tailored', application, document_id: 'resume-1', kind: 'resume', document_revision_id: 'resume-revision-1', document_revision_no: 1,
    claim_id: 'claim-1', claim_revision_id: 'claim-revision-1', claim_revision_no: 1, claim_text: 'Built synthetic deployment tooling.',
    superseded: false, archived: false, statements: ['Built synthetic deployment tooling.'], ...changes,
  };
}

afterEach(() => { vi.restoreAllMocks(); });

describe('ClaimUsedIn', () => {
  it('lists each application and the base résumé with the pinned claim revision', async () => {
    const usage: ClaimUsage = {
      claim_id: 'claim-1', current_revision_id: 'claim-revision-2', archived: true,
      items: [
        usageItem({ scope: 'base', application: null, document_id: 'base-1', document_revision_id: 'base-revision-3', document_revision_no: 3 }),
        usageItem({ superseded: true, archived: true }),
        usageItem({ kind: 'cover_letter', document_id: 'letter-1', document_revision_id: 'letter-revision-1', superseded: true, archived: true }),
      ],
    };
    const api = new Api();
    const request = vi.spyOn(api, 'request').mockResolvedValueOnce(usage).mockResolvedValueOnce({ ...usage, history: [] });
    render(<ClaimUsedIn api={api} claimId="claim-1" label="experience entry 1" />);
    await userEvent.click(screen.getByRole('button', { name: 'Show where experience entry 1 is used' }));
    const list = await screen.findByRole('region', { name: 'Used in: experience entry 1' });
    expect(request).toHaveBeenCalledWith('/claims/claim-1/usage', 'GET', undefined, undefined, false, expect.any(AbortSignal));
    expect(list).toHaveTextContent('Only factual statements that passed the evidence check count as usage.');
    const entries = within(list).getAllByRole('listitem').filter(item => item.querySelector('strong'));
    expect(entries.map(item => item.querySelector('strong')?.textContent)).toEqual([
      'Base résumé',
      'Synthetic Platform Engineer at Example Works · attempt 1 · Résumé',
      'Synthetic Platform Engineer at Example Works · attempt 1 · Cover letter',
    ]);
    expect(entries[0]).toHaveTextContent('document revision 3 · Claim revision 1 · current');
    expect(entries[1]).toHaveTextContent('Claim revision 1 · superseded by a later edit · claim archived');
    await userEvent.click(within(list).getByRole('button', { name: 'Include earlier document revisions' }));
    expect(request).toHaveBeenLastCalledWith('/claims/claim-1/usage?history=true', 'GET', undefined, undefined, false, expect.any(AbortSignal));
    expect(await screen.findByText('No earlier document revisions used this claim.')).toBeInTheDocument();
  });

  it('reports an unused claim and a failed read differently', async () => {
    const api = new Api();
    vi.spyOn(api, 'request')
      .mockResolvedValueOnce({ claim_id: 'claim-1', current_revision_id: 'claim-revision-1', archived: false, items: [] })
      .mockRejectedValueOnce(new ApiError(503, 'database_unavailable'));
    render(<ClaimUsedIn api={api} claimId="claim-1" label="skill entry 1" />);
    await userEvent.click(screen.getByRole('button', { name: 'Show where skill entry 1 is used' }));
    expect(await screen.findByText('Not used by any current document revision.')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Refresh where skill entry 1 is used' }));
    expect(await screen.findByText(/Usage unavailable, not empty\./)).toBeInTheDocument();
    expect(screen.queryByText('Not used by any current document revision.')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Show where skill entry 1 is used' })).toBeEnabled();
  });
});

describe('ApplicationClaimsUsed', () => {
  it('groups the claims used by the current revision of each document', async () => {
    const claims: ApplicationClaims = {
      application_id: 'application-1',
      items: [
        usageItem({ document_revision_no: 2 }),
        usageItem({ claim_id: 'claim-2', claim_revision_id: 'claim-revision-7', claim_revision_no: 2, claim_text: 'Led synthetic incident reviews.', superseded: true, statements: ['Led synthetic incident reviews.'] }),
        usageItem({ kind: 'cover_letter', document_id: 'letter-1', document_revision_id: 'letter-revision-1', archived: true }),
      ],
    };
    const api = new Api();
    const request = vi.spyOn(api, 'request').mockResolvedValue(claims);
    render(<ApplicationClaimsUsed api={api} applicationId="application-1" />);
    expect(screen.getByText(/The base résumé is listed on each claim, not here\./)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Show claims used' }));
    const resume = await screen.findByRole('region', { name: 'Claims used: Résumé' });
    expect(request).toHaveBeenCalledWith('/applications/application-1/claims', 'GET', undefined, undefined, false, expect.any(AbortSignal));
    expect(resume).toHaveTextContent('Résumé · document revision 2');
    expect(resume).toHaveTextContent('Built synthetic deployment tooling. · Claim revision 1 · current');
    expect(resume).toHaveTextContent('Led synthetic incident reviews. · Claim revision 2 · superseded by a later edit');
    const letter = screen.getByRole('region', { name: 'Claims used: Cover letter' });
    expect(letter).toHaveTextContent('Claim revision 1 · current · claim archived');
  });

  it('says when no saved document cites a claim', async () => {
    const api = new Api();
    vi.spyOn(api, 'request').mockResolvedValue({ application_id: 'application-1', items: [] });
    render(<ApplicationClaimsUsed api={api} applicationId="application-1" />);
    await userEvent.click(screen.getByRole('button', { name: 'Show claims used' }));
    expect(await screen.findByText('No saved document of this application cites a claim.')).toBeInTheDocument();
  });
});
