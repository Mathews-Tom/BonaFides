# Synthetic local-model qualification cases

Original synthetic fixtures, distributed under `MIT OR Apache-2.0`. No real person,
organization, résumé or employer data is included. No model weights are distributed.

Each stage file contains 20 cases: ten professional roles in professional and concise
tones, with two separately cited claims and their evidence, a posting, reviewed
requirements and a pinned existing section. The posting includes a preferred skill
not established by the claims, so it cannot be borrowed into factual statements.
`generate_documents.jsonl` exercises complete résumé/letter pairs;
`regenerate_section.jsonl` exercises replacement blocks for the pinned section, with
`base_body` retaining the whole original document for production's shared aggregate
replacement-limit validation. Non-target sections are never sent to the model.

Each JSONL record has `id`, `posting` (title, company, description_text), `requirements`,
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
