# Synthetic local-model qualification cases

Original synthetic fixtures, distributed under `MIT OR Apache-2.0`. No real person,
organization, résumé or employer data is included. No model weights are distributed.

Each original stage file contains 20 cases across ten professional roles. Document stages
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
Email v2 replies copy the language's fixed application subject label and contain
checked statement blocks, with no model-authored salutation/closing or address
fields. The server adds deterministic localized correspondence for German, French
and Spanish; English email body bytes stay unchanged.
Unsupported body assertions trigger the same single corrective attempt as malformed
output; a second failure is invalid. Existing stage files are not changed.

Generation/rewrite JSONL records have `id`, `posting` (title, company, description_text), `requirements`,
`claims`, `evidence`, checker `facts`, `tone`, `language` and `target`. UUIDs identify
synthetic immutable claim/evidence revisions; no dedicated identity/contact data enters
the projection. The runner uses the same pure projection builders and one-correction
loop as production. Every schema-valid result also runs through the unchanged checker.

The CLI discovers the selected stage's original file and sorted `<stage>_*.jsonl`
supplements. The digest is `fabrication_benchmark.digest` over all selected relative
filenames and their exact bytes; unrelated stages and this README are not included.
The four `*_languages.jsonl` supplements each add six cases: German, French and
Spanish, each with a passing context and a `withheld_text` paraphrase negative
control. Pinned structures localize only fixed renderer wording; owner prose stays
literal. The runner verifies that every negative control is blocked by the production
checker before asking the model for the supported answer. A negative control does
not instruct the model to fabricate, and does not count as a failed model response.

Generation, regeneration and refinement therefore run 26 model cases each; email
and import structuring remain at 20. The original JSONL files are byte-unchanged.
After upgrading, old receipts lose qualification because their shared capability
fingerprint covers the changed prompts. `models.sh qualify` can renew five stages:
`generate_documents`, `regenerate_section`, `refine_documents`,
`write_application_email`, and `structure_import` if enabled. Existing
`extract_posting` and `match_requirements` receipts also become stale; the shipped
qualification CLI cannot renew them, so those stages remain unqualified on this route.
`load_cases(..., include_languages=False)` retains the original corpus and digest for
historical baseline tests; the shipped CLI always enables supplement discovery.
The development stub's `qualification` scenario follows the original refinement
fixtures' exact refusal/no-change expectations and copies pinned text in every
language. `valid` remains the ordinary fixture-route scenario; `paraphrase` supplies
unsupported wording to exercise withholding. None qualifies a real model.
For mounted stubs, qualification uses the shipped `/app/qualification` dataset.
From a source checkout it can instead use `tests/fixtures/qualification` under the
working directory. Set `BONAFIDES_QUALIFICATION_DATASET` to explicitly select a
dataset directory; a missing dataset fails loudly rather than guessing from the
stub script's mount path.

```sh
bonafides models qualify --route /protected/route.json --stage generate_documents \
  --dataset tests/fixtures/qualification --reviewed-by 'Operator name'
```

All discovered cases count in the valid-rate denominator, including pre-check failures.
A passed receipt requires at least 95% valid cases (19/20 or 25/26) after no more than
one corrective retry. The usable-statement rate counts factual statements without checker
blockers; it is reported, not a qualification threshold. A passed schema receipt is
not factual approval, a semantic-entailment guarantee or permission to export an
unsupported statement. Real-model results and durations belong in the observed run
report, not in these input fixtures. Do not tune prompts or cases to manufacture a pass.
