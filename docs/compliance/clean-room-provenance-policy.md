# Clean-room provenance policy

## Authorized inputs and forbidden material

Implement from approved source-neutral functional requirements, independently authored designs, and independently researched official technical documentation. Do not inspect, request, reconstruct, import, or publish restricted reference implementation source, distinctive UI/assets, copied prompts/schemas, decompiled/source-like traces, raw reverse-engineering artifacts or identities. Sanitized neutral IDs may support requirement traceability; they are not an obligation to copy architecture or expression. Renaming, translating, reformatting, or paraphrasing protected code does not establish independence.

## Record each material decision

At architecture proof create a provenance ledger. For each major module, algorithm, interface, schema, prompt, asset or generated artifact record: neutral requirement and functional feature references (feature name and design contract); original author/reviewer and date; independently selected approach and alternatives; official research URLs and access dates; third-party material actually incorporated with license/version/digest; AI/tool assistance and human review; changed paths and commit/artifact identity when available; rights/uncertainty disposition. Record facts, not an invented author attestation. Preserve the design-stage package's own evidence boundary: it is not a legal opinion.

Only synthetic profiles and data may enter examples/tests. Prefer fresh contracts and standard architectural patterns, justified by the new product. No raw confidential input paths or private origin details should be necessary to consume the portable package. Keep sensitive provenance/legal evidence in access-controlled records; public release summaries must not leak it.

## Quarantine procedure

If a contributor encounters questionable material: stop incorporating it; do not paste it into issues, model prompts, scanner uploads or this repository. Record a neutral incident ID, affected paths and reason without reproducing content. Notify a maintainer privately, restrict access to the suspect contribution, and obtain qualified review. Reimplementing after exposure may require an independently staffed process; do not claim deletion alone removes contamination. If rights cannot be established, exclude the material and document a clean alternative. Never access restricted source to resolve uncertainty.

## Gates

A provenance reviewer must trace major implementation choices and all incorporated external assets before public release; counsel must review unresolved independence/rights concerns and eligibility claims. Absence of identifiable names, successful keyword scanning or lack of code similarity findings is not proof of independence. CI may check declaration presence and accidental identifiers/secrets but cannot certify original authorship. Keep release-specific approvals bound to exact artifacts; reopening material assumptions requires renewed review.

The owner's [scoped source publication authorization](licensing-policy.md#scoped-source-publication-authorization) changes only the review-before-publication sequencing for the identified source-only PR. Independent review remains pending, not passed; release-specific approvals and all actual rights/attribution obligations remain required. The exception does not permit publication of questionable or restricted material, private data, runtime images or packages.
