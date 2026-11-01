// SPDX-License-Identifier: MIT OR Apache-2.0
import type { DocumentBody, DocumentFact, DocumentRecord, SectionProposal, SectionProposalPage, SectionRegenerationContext } from './api';
import { ISSUE_REASONS, linkedClaim } from './document-state';

export function proposalMatches(proposal: SectionProposal, document: DocumentRecord) {
  return proposal.document_id === document.id && proposal.target_document_version === document.version &&
    proposal.target_revision_id === document.current_revision.id && proposal.target_content_sha256 === document.current_revision.content_sha256;
}

function ProposalSection({ section, label, facts }: { section: DocumentBody['sections'][number]; label: string; facts: DocumentFact[] }) {
  return <article className="paper" aria-label={label}><h5>{label}: {section.heading}</h5>{section.blocks.map(block => <div className="evidence-seam" key={block.block_key}>
    <p className="proposal-text">{block.text}</p><small>Classification: {block.claim_kind}.</small>
    {block.claim_kind === 'factual' && <p className="claim-link">Linked claim: {linkedClaim(facts, block.claim_revision_ids)}</p>}
    <details><summary>Statement references</summary><p>Claims: {block.claim_revision_ids.join(', ') || 'None'}. Evidence: {block.evidence_revision_ids.join(', ') || 'None'}. References are not semantic proof; compare wording with the saved document’s frozen sources.</p></details>
  </div>)}</article>;
}

interface Props {
  document: DocumentRecord; dirty: boolean; locked: boolean; archived: boolean;
  sectionKey: string; tone: 'professional' | 'concise'; context: SectionRegenerationContext | null;
  confirmed: boolean; proposals: SectionProposalPage | null; proposal: SectionProposal | null; applyConfirmed: boolean;
  onSection: (key: string) => void; onTone: (tone: 'professional' | 'concise') => void;
  onPreview: () => void; onConfirm: (value: boolean) => void; onRequest: () => void;
  onReload: (before?: string) => void; onOpen: (id: string) => void;
  onApplyConfirm: (value: boolean) => void; onApply: () => void;
}

export function SectionProposals({ document, dirty, locked, archived, sectionKey, tone, context, confirmed, proposals, proposal, applyConfirmed, onSection, onTone, onPreview, onConfirm, onRequest, onReload, onOpen, onApplyConfirm, onApply }: Props) {
  const unavailable = locked || dirty || archived;
  const targetMatches = !!proposal && proposalMatches(proposal, document);
  const canApply = !locked && !dirty && !proposal?.historical && !!proposal?.applicable && targetMatches;
  return <section className="section-proposals" aria-labelledby="section-proposals-heading">
    <h3 id="section-proposals-heading">Selected-section proposals</h3>
    <p>Regenerate one saved tailored section, not the whole document. The heading, other sections, printable contact frame and template stay frozen. Completion stores a proposal only; it never replaces saved wording or your editor. Nothing is sent as an application.</p>
    {dirty && <p className="disclosure">Save or explicitly discard local edits and resolve any revision comparison before previewing, requesting or applying a proposal. Your buffer is retained.</p>}
    {archived && <p>Archived applications cannot request new model proposals. Stored proposals remain readable.</p>}
    <fieldset disabled={unavailable}><legend>Saved section regeneration</legend>
      <label>Section to regenerate<select value={sectionKey} onChange={event => onSection(event.target.value)}>{document.current_revision.body.sections.map(section => <option key={section.key} value={section.key}>{section.heading}</option>)}</select></label>
      <label>Section writing tone<select value={tone} onChange={event => onTone(event.target.value as 'professional' | 'concise')}><option value="professional">Professional</option><option value="concise">Concise</option></select></label>
      <button className="secondary" disabled={!sectionKey} onClick={onPreview}>Preview selected-section model projection</button>
      {context && <section className="disclosure" aria-label="Exact selected-section model projection"><h4>Exact selected-section model projection</h4>
        <p>This is the exact professional projection for this one section and tone. Dedicated identity/contact fields, printable frame, other document sections and private tracking data are excluded. Evidence currently classified confidential or employer-owned on its record is sent as a reference without its content. Reclassification applies immediately without re-saving or re-pinning claims. Professional prose can still contain personal information; this is not anonymization. No hosted fallback.</p>
        <p>Destination from this section’s route: <code>{typeof context.generation_route.identity?.endpoint === 'string' ? context.generation_route.identity.endpoint : 'No endpoint supplied — inspect route identity below'}</code>. Model: {typeof context.generation_route.identity?.model === 'string' ? context.generation_route.identity.model : 'Not supplied'}. A local endpoint is not a universal no-network guarantee.</p>
        <p>Included categories: frozen posting title, organization and source text; reviewed requirements with importance and source spans; accepted professional claims, structured fields and source references; evidence text and references, except that confidential or employer-owned evidence is sent as a reference without its content; tone and language; target document kind, section key, heading and this section’s text, classifications and references only. Claims citing withheld evidence are still sent with their own text and checked locally against the full source.</p>
        <p>Temporary task inputs are eligible for clearing 24 hours after terminal completion, with a daily sweep and no extension. Saved revisions and committed proposals remain until explicit, confirmed scoped erasure; local backups/downloads and external copies have independent retention.</p>
        <pre className="import-source">{JSON.stringify(context.model_projection, null, 2)}</pre>
        <p><strong>Section regeneration route: {context.generation_route.status}.</strong> {context.generation_route.reason}</p>
        {context.generation_route.status !== 'qualified' && <p>Inspecting this projection makes no model call. A separately qualified regenerate_section route is required before a task can be requested; pair-generation qualification does not qualify this stage.</p>}
        <details><summary>Exact saved target and route identity</summary><p>Revision {context.revision_id}; document version {context.document_version}; content SHA-256 {context.content_sha256}; context SHA-256 {context.context_sha256}.</p><pre className="import-source">{JSON.stringify(context.generation_route.identity, null, 2)}</pre></details>
        <p>At most two inference POST attempts across retries/reclaims. Task success is not factual or human qualification.</p>
        <label className="check"><input type="checkbox" checked={confirmed} disabled={context.generation_route.status !== 'qualified'} onChange={event => onConfirm(event.target.checked)} />I reviewed this exact selected-section projection, target and route for this one task</label>
        <button disabled={!confirmed || context.generation_route.status !== 'qualified'} onClick={onRequest}>Request section proposal</button>
      </section>}
    </fieldset>
    <section aria-label="Durable saved section proposals"><h4>Saved section proposals</h4><button className="secondary" disabled={locked} onClick={() => onReload()}>Reload saved section proposals</button>
      {proposals && <>{proposals.items.length === 0 ? <p>No saved section proposals. A missing or failed task is not a proposal.</p> : <ol>{proposals.items.map(item => <li key={item.id}><button className="secondary" disabled={locked} aria-pressed={proposal?.id === item.id} onClick={() => onOpen(item.id)}>Compare proposal for {item.original_section.heading} · {item.created_at}</button><small>Target revision {item.target_revision_id}. {item.applicable ? 'Target may be applicable; compare before applying.' : 'Not applicable to the current saved target or source state.'}</small></li>)}</ol>}{proposals.next_cursor && <button className="secondary" disabled={locked} onClick={() => onReload(proposals.next_cursor!)}>Load older section proposals</button>}</>}
    </section>
    {proposal && <section className="comparison" aria-label="Section proposal comparison"><h4>Compare original and proposed section</h4><p>Proposal {proposal.id}. Target revision {proposal.target_revision_id}; document version {proposal.target_document_version}; content SHA-256 {proposal.target_content_sha256}. The original below is the proposal’s immutable target, not a claim that your current editor is unchanged.</p>
      <div className="proposal-columns"><ProposalSection section={proposal.original_section} label="Original saved section" facts={proposal.facts} /><ProposalSection section={proposal.proposed_section} label="Proposed section" facts={proposal.facts} /></div>
      {proposal.historical && <p className="disclosure">Imported historical proposal. Original model metadata is retained for inspection, not restored invocation or consent authority. This proposal cannot be applied. Request a new proposal through a separately qualified route when needed.</p>}
      {proposal.validation_issues.length > 0 ? <div className="error"><strong>Proposed draft has review/export blockers.</strong><ul>{proposal.validation_issues.map((issue, index) => <li key={`${issue.path}-${issue.code}-${index}`}>{ISSUE_REASONS[issue.code] ?? `Unsupported (${issue.code}).`}{issue.statement !== undefined && <> Statement: <q>{issue.statement}</q></>} <small>{issue.path} · {issue.code}</small></li>)}</ul><p>Applying retains these issues in an editable draft. Correct unsupported statements or accepted sources and save/revalidate before exact-revision review or PDF. Human review cannot waive blockers.</p></div> : <p>No structural/source blocker was reported. This is not factual approval; compare every statement with the frozen sources.</p>}
      {(!targetMatches || !proposal.applicable) && <p className="disclosure">This proposal cannot be applied to the displayed saved target or current source state. A manual save or restoration changes target identity even if words match. Reload proposals and compare the current document; request a new proposal when appropriate. Nothing is automatically rebased or overwritten.</p>}
      <details><summary>Stored proposal model provenance</summary><pre className="import-source">{JSON.stringify(proposal.model_run, null, 2)}</pre></details>
      <label className="check"><input type="checkbox" checked={applyConfirmed} disabled={!canApply} onChange={event => onApplyConfirm(event.target.checked)} />I compared the original and proposal and explicitly choose to apply only this section as a new draft revision</label>
      <button disabled={!canApply || !applyConfirmed} onClick={onApply}>Apply proposal as new draft revision</button>
    </section>}
  </section>;
}
