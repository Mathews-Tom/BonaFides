# Fabrication benchmark dataset

Synthetic, local cases for `bonafides benchmark run`; they contain no owner data. The run reads exactly `honest.jsonl`, `inflated.jsonl` and `untargeted.jsonl`, and the dataset digest covers only those files, so editing this README does not change it.

`honest.jsonl` (1,175 cases) and `inflated.jsonl` (165 cases) are frozen byte-identical copies of `tests/fixtures/inflation/` as of checker `claim-evidence-v6`; the inflation sets may keep evolving as regression fixtures without moving this digest. Their format and text-shape counts are documented in `tests/fixtures/inflation/README.md`.

`untargeted.jsonl` holds the classes the inflation rules do not target, 12 cases each:

| Class | Citation | Shape |
| --- | --- | --- |
| `invented_claim` | `absent` | The statement cites a claim id missing from the profile. |
| `posting_only_skill` | `own` | A skill named only by a posting, cited to a real skill claim; in six cases the evidence mentions it in another context. |
| `cross_claim_borrowing` | `own` | Wording from an `others` claim joined to the cited claim; six share one evidence text. |
| `uncited_statement` | `uncited` (6), `connective` (6) | Factual text citing nothing, or disguised as a non-factual block. |
| `paraphrase_inflation` | `own` | Synonyms that strengthen the claim (8) or reordering that promotes a subordinate clause (4). |
| `dropped_qualifier` | `own` | A limiting trailing qualifier cut at a declared boundary word — the accepted limitation, expected to pass the checker. |

Every claim, including each `others` claim, is contained in its evidence; the loader rejects any case that is not.
