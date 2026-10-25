// SPDX-License-Identifier: MIT OR Apache-2.0
import type { DocumentBody } from './api';

export const copyBody = (body: DocumentBody): DocumentBody => ({ ...body, sections: body.sections.map(section => ({ ...section, blocks: section.blocks.map(block => ({ ...block, claim_revision_ids: [...block.claim_revision_ids], evidence_revision_ids: [...block.evidence_revision_ids] })) })) });
export const sameBody = (left: DocumentBody, right: DocumentBody) => JSON.stringify(left) === JSON.stringify(right);

// Rebase only the explicitly edited fields. Never replace unrelated server sections or
// resurrect a block removed by another editor. The caller requires comparison/consent.
export function rebaseBody(original: DocumentBody, edited: DocumentBody, server: DocumentBody): DocumentBody | null {
  const result = copyBody(server);
  if (edited.title !== original.title) result.title = edited.title;
  for (const before of original.sections) {
    const after = edited.sections.find(section => section.key === before.key);
    const target = result.sections.find(section => section.key === before.key);
    if (!after) {
      if (target) target.blocks = target.blocks.filter(block => !before.blocks.some(oldBlock => oldBlock.block_key === block.block_key));
      continue;
    }
    const changed = !sameBody({ title: '', sections: [before] }, { title: '', sections: [after] });
    if (!target) { if (changed) return null; continue; }
    if (after.heading !== before.heading) target.heading = after.heading;
    for (const oldBlock of before.blocks) {
      const newBlock = after.blocks.find(block => block.block_key === oldBlock.block_key);
      const targetBlock = target.blocks.find(block => block.block_key === oldBlock.block_key);
      if (!newBlock) { target.blocks = target.blocks.filter(block => block.block_key !== oldBlock.block_key); continue; }
      if (newBlock.text !== oldBlock.text) {
        if (!targetBlock) return null;
        targetBlock.text = newBlock.text;
      }
    }
  }
  result.sections = result.sections.filter(section => section.blocks.length > 0);
  return result.sections.length ? result : null;
}

export function issueAnchor(path: string, body: DocumentBody, displayed: DocumentBody = body): string {
  const match = /^\/sections\/(\d+)(?:\/blocks\/(\d+))?/.exec(path);
  if (!match) return 'document-saved-title';
  const section = body.sections[Number(match[1])];
  const block = match[2] === undefined ? undefined : section?.blocks[Number(match[2])];
  if (block) return displayed.sections.some(item => item.blocks.some(visible => visible.block_key === block.block_key)) ? `document-block-${block.block_key}` : 'document-saved-title';
  return section && displayed.sections.some(item => item.key === section.key) ? `document-section-${section.key}` : 'document-saved-title';
}
