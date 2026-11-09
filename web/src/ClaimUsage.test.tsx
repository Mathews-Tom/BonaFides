// SPDX-License-Identifier: MIT OR Apache-2.0
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Api, ApiError, type ApplicationClaims, type ClaimOutcomes, type ClaimUsage, type ClaimUsageItem } from './api';
import { ApplicationClaimsUsed, ClaimUsedIn } from './ClaimUsage';

const application = { id: 'application-1', title: 'Synthetic Platform Engineer', company: 'Example Works', attempt_no: 1, archived: false };
function usageItem(changes: Partial<ClaimUsageItem>): ClaimUsageItem {
  return {
    scope: 'tailored', application, document_id: 'resume-1', kind: 'resume', document_revision_id: 'resume-revision-1', document_revision_no: 1,
    claim_id: 'claim-1', claim_revision_id: 'claim-revision-1', claim_revision_no: 1, claim_text: 'Built synthetic deployment tooling.',
    superseded: false, archived: false, statements: ['Built synthetic deployment tooling.'], ...changes,
  };
}

const emptyOutcomes: ClaimOutcomes = {
  application_count: 0, archived_application_count: 0, no_progression_count: 0, inferred_applied_from_rejection: 0,
  small_sample_threshold: 5, small_sample: true,
  stages: [
    { stage: 'applied', furthest_count: 0, reached_count: 0, rate: null },
    { stage: 'screening', furthest_count: 0, reached_count: 0, rate: null },
    { stage: 'interview', furthest_count: 0, reached_count: 0, rate: null },
    { stage: 'offer', furthest_count: 0, reached_count: 0, rate: null },
    { stage: 'accepted', furthest_count: 0, reached_count: 0, rate: null },
  ],
};

afterEach(() => { vi.restoreAllMocks(); });

describe('ClaimUsedIn', () => {
  it('lists each application and the base résumé with the pinned claim revision', async () => {
    const usage: ClaimUsage = {
      claim_id: 'claim-1', current_revision_id: 'claim-revision-2', archived: true,
      items: [
        usageItem({ scope: 'base', application: null, document_id: 'base-1', document_revision_id: 'base-revision-3', document_revision_no: 3 }),
        usageItem({ document_revision_no: 2, superseded: true, archived: true }),
        usageItem({ kind: 'cover_letter', document_id: 'letter-1', document_revision_id: 'letter-revision-1', document_revision_no: 2, superseded: true, archived: true }),
      ],
      outcomes: {
        ...emptyOutcomes, application_count: 1,
        stages: emptyOutcomes.stages.map(stage => ({ ...stage, furthest_count: stage.stage === 'applied' ? 1 : 0, reached_count: stage.stage === 'applied' ? 1 : 0, rate: stage.stage === 'applied' ? 1 : 0 })),
      },
    };
    const api = new Api();
    vi.spyOn(api, 'request').mockResolvedValueOnce(usage).mockResolvedValueOnce({
      ...usage, history: [usageItem({
        application: { ...application, id: 'old-application', attempt_no: 2, archived: true },
        document_id: 'old-resume', document_revision_id: 'old-revision', superseded: true, archived: true,
      })],
    });
    render(<ClaimUsedIn api={api} claimId="claim-1" label="experience entry 1" />);
    await userEvent.click(screen.getByRole('button', { name: 'Show where experience entry 1 is used' }));
    const list = await screen.findByRole('region', { name: 'Used in: experience entry 1' });
    const entries = within(list).getAllByRole('listitem').filter(item => item.querySelector('strong'));
    expect(entries.map(item => item.querySelector('strong')?.textContent)).toEqual([
      'Base résumé',
      'Synthetic Platform Engineer at Example Works · attempt 1 · Résumé',
      'Synthetic Platform Engineer at Example Works · attempt 1 · Cover letter',
    ]);
    expect(entries[0]).toHaveTextContent('document revision 3 · Claim revision 1 · current');
    expect(entries[1]).toHaveTextContent('Claim revision 1 · superseded by a later edit · claim archived');
    await userEvent.click(within(list).getByRole('button', { name: 'Include earlier document revisions' }));
    const earlierAttempt = await within(list).findByText('Synthetic Platform Engineer at Example Works · attempt 2 · application archived · Résumé');
    expect(earlierAttempt.closest('li')).toHaveTextContent('document revision 1 · Claim revision 1 · superseded by a later edit · claim archived');
    expect(within(within(list).getByRole('table', { name: 'Application outcomes' })).getByRole('row', { name: /^Applied / })).toHaveTextContent('1/1 (100%)');
  });

  it('reports an unused claim and a failed read differently', async () => {
    const api = new Api();
    vi.spyOn(api, 'request')
      .mockResolvedValueOnce({ claim_id: 'claim-1', current_revision_id: 'claim-revision-1', archived: false, items: [], outcomes: emptyOutcomes } satisfies ClaimUsage)
      .mockRejectedValueOnce(new ApiError(503, 'database_unavailable'));
    render(<ClaimUsedIn api={api} claimId="claim-1" label="skill entry 1" />);
    await userEvent.click(screen.getByRole('button', { name: 'Show where skill entry 1 is used' }));
    expect(await screen.findByText('Not used by any current document revision.')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Refresh where skill entry 1 is used' }));
    expect(await screen.findByText(/Usage unavailable, not empty\./)).toBeInTheDocument();
    expect(screen.queryByText('Not used by any current document revision.')).not.toBeInTheDocument();
    expect(screen.queryByRole('table', { name: 'Application outcomes' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Show where skill entry 1 is used' })).toBeEnabled();
  });

  it('distinguishes unavailable reach from zero and refreshes rounded reach after corrections', async () => {
    const usage: ClaimUsage = { claim_id: 'claim-1', current_revision_id: 'claim-revision-1', archived: false, items: [], outcomes: emptyOutcomes };
    const populated: ClaimUsage = {
      ...usage, items: [
        usageItem({}),
        usageItem({ application: { ...application, id: 'application-2', attempt_no: 2 }, document_id: 'resume-2', document_revision_id: 'resume-revision-2' }),
        usageItem({ application: { ...application, id: 'application-3', attempt_no: 3 }, document_id: 'resume-3', document_revision_id: 'resume-revision-3' }),
      ],
      outcomes: {
        ...emptyOutcomes, application_count: 3,
        stages: [
          { stage: 'applied', furthest_count: 1, reached_count: 3, rate: 1 },
          { stage: 'screening', furthest_count: 0, reached_count: 2, rate: 2 / 3 },
          { stage: 'interview', furthest_count: 2, reached_count: 2, rate: 2 / 3 },
          { stage: 'offer', furthest_count: 0, reached_count: 0, rate: 0 },
          { stage: 'accepted', furthest_count: 0, reached_count: 0, rate: 0 },
        ],
      },
    };
    const corrected: ClaimUsage = {
      ...populated,
      outcomes: {
        ...populated.outcomes,
        stages: populated.outcomes.stages.map(stage => stage.stage === 'applied'
          ? { ...stage, furthest_count: 2 }
          : stage.stage === 'screening' || stage.stage === 'interview'
            ? { ...stage, furthest_count: stage.stage === 'interview' ? 1 : 0, reached_count: 1, rate: 1 / 3 }
            : stage),
      },
    };
    const api = new Api();
    vi.spyOn(api, 'request').mockResolvedValueOnce(usage).mockResolvedValueOnce(populated).mockResolvedValueOnce(corrected);
    render(<ClaimUsedIn api={api} claimId="claim-1" label="skill entry 1" />);
    await userEvent.click(screen.getByRole('button', { name: 'Show where skill entry 1 is used' }));
    const table = await screen.findByRole('table', { name: 'Application outcomes' });
    expect(within(table).getAllByRole('cell', { name: '0/0 (Unavailable)' })).toHaveLength(5);
    expect(within(table).queryByRole('cell', { name: /0%/ })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Refresh where skill entry 1 is used' }));
    expect(await within(table).findByRole('cell', { name: '3/3 (100%)' })).toBeInTheDocument();
    expect(within(table).getByRole('row', { name: /^Interview / })).toHaveTextContent('2/3 (66.7%)');
    expect(within(table).getByRole('row', { name: /^Offer / })).toHaveTextContent('0/3 (0%)');
    expect(within(table).queryByRole('cell', { name: /Unavailable/ })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Refresh where skill entry 1 is used' }));
    expect(await within(table).findByRole('row', { name: /^Interview .*1\/3 \(33\.3%\)/ })).toBeInTheDocument();
    expect(within(table).queryByRole('cell', { name: /66\.7%/ })).not.toBeInTheDocument();
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
    vi.spyOn(api, 'request').mockResolvedValue(claims);
    render(<ApplicationClaimsUsed api={api} applicationId="application-1" />);
    await userEvent.click(screen.getByRole('button', { name: 'Show claims used' }));
    const resume = await screen.findByRole('region', { name: 'Claims used: Résumé' });
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
