# Claim-scoped inflation case sets

Each synthetic, local JSONL record has `evidence`, `claim`, `statement` and `kind`;
optional `fields` hold reviewed structured-field lines for exact pinned printable
text. A claim is literal text or a schema-v1 structured claim pinned as its rendering
and verb. These sets contain no owner data.

## Honest set

`honest.jsonl` has **1,175 cases**, with these disjoint text-shape categories:

| Statement shape | Count |
| --- | ---: |
| Exact claim text | 314 |
| Exact printable text, including reviewed field lines | 6 |
| Normalized whole-phrase prefix of claim text | 289 |
| Complete post-verb remainder | 297 |
| Shorter normalized whole-phrase prefix of post-verb remainder | 267 |
| Intentionally blocked pre-existing mid-claim cut | 1 |
| Intentionally blocked vocabulary-leading post-verb remainder | 1 |
| **Total** | **1,175** |

Only **314/1,175** statements equal their claim text; including printable texts gives
**320/1,175**, below one half. Original honest rows **1–1167 remain unchanged and in
order**. By explicit owner authorization, original rows **1168–1169** (omitted pilot
region qualifiers) were removed, and original rows **1170–1185** moved to inflated
rows **124–139** as `truncated_phrase`, preserving their evidence, claim and statement.
The remaining 32 endpoint additions from original rows 1136–1167 are retained.
Eight new honest rows **1168–1175** cover full stops followed by whitespace and
spaced hyphen/en-dash/em-dash endpoints, each as a claim prefix and post-verb prefix.

Under checker **`claim-evidence-v5`**, permitted endpoints are the end of the claim;
before `,`, `;`, `:` or `(`; before a full stop followed by whitespace; before a
spaced dash (hyphen, en dash or em dash, normalized to `-`); or before a whole word
`for`, `by`, `to`, `on`, `with`, `in`, `across`, `from`, `through`, `during`, `at`,
`that` (case-insensitive). Numeric, currency and hyphen protections still apply.
A boundary word is **not** an endpoint when the prefix ends in a number/currency
figure and the word is followed by a number: `Cut costs by 10 to 15%` cannot become
`Cut costs by 10`, and `Supported 10 to 20 clients` cannot become `Supported 10`.
`Supported 10 clients to ...` is unaffected by that numeric-range guard.

The checker cannot infer the meaning of every trailing qualifier and can still
accept its omission at a permitted endpoint. This is a checker limitation, not
permission to omit it: prompt versions **`ol-generate-documents-v6`** and
**`ol-regenerate-section-v6`** instruct the model to **KEEP any qualifier limiting
scope, period, share, rate or co-ownership**. The pilot-region limitation remains
documented by a checker unit test, not by honest fixture examples.

Two original honest cuts remain measured blockers (`statement_cut_inside_claim`):

- **Line 875:** `the billing migration` from `Assisted the team that led the billing
  migration`, neither a claim prefix nor an immediate post-verb prefix.
- **Line 1131:** `automated tests for the API` from `Wrote automated tests for the
  API`; vocabulary-leading post-verb remainders are guarded without lexical
  exceptions, even when adjective-like. `Wrote automated tests` remains valid.

Measured false-block rate: **2/1,175**, conservatively displayed as **0.2%**, below
the unchanged **5%** ceiling. Exact printable text and claim text rules, structured
renderer's pinned leading wording, longest literal vocabulary phrase selection and
strict post-verb guard are unchanged. Normalization does not permit arbitrary
mid-claim fragments.

## Inflated set

`inflated.jsonl` has **149 cases**, at least ten of each kind:

| Kind | Count | Expected blocker |
| --- | ---: | --- |
| `stronger_verb` | 15 | `statement_cut_inside_claim` |
| `longer_tenure` | 16 | `statement_not_in_claim` |
| `larger_metric` | 74 | `statement_not_in_claim` |
| `wider_scope` | 18 | `statement_not_in_claim` |
| `truncated_phrase` | 26 | `statement_cut_inside_claim` |

Original inflated rows **1–123 remain unchanged and in order**. Rows **124–139**
are the 16 moved cuts inside `team's`, `teams'`, `QA/release`, `Node.js`,
`migration planning`, `$2 million`, `2 per 1000 requests` and `migration jointly`.
Rows **140–149** add ten numeric-range cuts, paired as claim/post-verb prefixes,
covering percentages, client counts, currency, latency and team counts.
Every inflated statement is contained in its own evidence. Both `stronger_verb`
and `truncated_phrase` statements are also contained in their pinned claim;
the other kinds borrow wording from elsewhere in the evidence.

## Validation

Report tests pin category counts, evidence containment and expected blocker codes.
The local report command produces:

```text
Honest: 2 of 1175 blocked (false-block 0.2%)
honest line 875: statement_cut_inside_claim
honest line 1131: statement_cut_inside_claim
Inflated stronger_verb: 15 of 15 blocked (100.0%)
Inflated longer_tenure: 16 of 16 blocked (100.0%)
Inflated larger_metric: 74 of 74 blocked (100.0%)
Inflated wider_scope: 18 of 18 blocked (100.0%)
Inflated truncated_phrase: 26 of 26 blocked (100.0%)
False-block rate within the 5% limit
```

```sh
uvx --from uv==0.13.0 uv run --frozen bonafides claims inflation-report tests/fixtures/inflation
```

Stop and report if the honest false-block rate exceeds 5%; do not remove honest
cases to improve the rate without explicit owner authorization. The moves/removals
above are the specifically authorized reclassification, not a general exception.
