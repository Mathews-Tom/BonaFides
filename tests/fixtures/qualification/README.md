# Synthetic local-model qualification cases

Original synthetic fixtures, distributed under `MIT OR Apache-2.0`. No real person,
organization, résumé or employer data is included. No model weights are distributed.

Each stage file contains 20 cases across ten professional roles. Document stages
cover professional, concise and friendly tones (the first case is friendly), with
two separately cited claims and their evidence, a posting, reviewed requirements
and a pinned existing section. The posting includes a preferred skill
not established by the claims, so it cannot be borrowed into factual statements.
`generate_documents.jsonl` exercises complete résumé/letter pairs;
`regenerate_section.jsonl` exercises replacement blocks for the pinned section, with
`base_body` retaining the whole original document for production's shared aggregate
replacement-limit validation. Non-target sections are never sent to the model.
`write_application_email.jsonl` adapts the same hand-authored synthetic role/claim
material into 20 email cases, replacing `target` with the current `letter_body` AST.
The letter is context, not an additional factual source. The first case is friendly;
all three tones occur, with two cases per role and an unsupported preferred skill.
The last two cases withhold one evidence item's text while retaining its checker facts.
Cases 04, 08, 12 and 16 also contain a declared connective, legacy greeting/closing
blocks, and an unsupported Kubernetes assertion in the letter. Splitting must remove
the legacy frame; the unsupported sentence remains context but must not be copied
into a valid email. These are adversarial inputs, not approved example outputs.
Only body text is projected: legacy salutations/closings are split out, and no
sender, contact, signature, recipient, frame, portrait or private notes are sent.
Email replies use a closed neutral subject (`Application` or `Application enquiry`)
and checked statement blocks, with no salutation/closing or address fields.
Unsupported body assertions trigger the same single corrective attempt as malformed
output; a second failure is invalid. Existing stage files are not changed.

Generation/rewrite JSONL records have `id`, `posting` (title, company, description_text), `requirements`,
`claims`, `evidence`, checker `facts`, `tone`, `language` and `target`. UUIDs identify
synthetic immutable claim/evidence revisions; no dedicated identity/contact data enters
the projection. The runner uses the same pure projection builders and one-correction
loop as production. Every schema-valid result also runs through the unchanged checker.

The digest is `fabrication_benchmark.digest` over the selected stage file's relative
name and exact bytes. The other stage file and this README are not read by that run.

```sh
bonafides models qualify --route /protected/route.json --stage generate_documents \
  --dataset tests/fixtures/qualification --reviewed-by 'Operator name'
```

All 20 cases count in the valid-rate denominator, including pre-check failures.
A passed receipt requires at least 19 valid cases (95%) after no more than one
corrective retry. The usable-statement rate counts factual statements without checker
blockers; it is reported, not a qualification threshold. A passed schema receipt is
not factual approval, a semantic-entailment guarantee or permission to export an
unsupported statement. Real-model results and durations belong in the observed run
report, not in these input fixtures. Do not tune prompts or cases to manufacture a pass.
