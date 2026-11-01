# Claim-scoped inflation case sets

Each JSONL record has `evidence`, `claim`, `statement` and `kind`; optional `fields`
hold reviewed structured-field lines for the exact pinned printable text. A claim is
literal text or a schema-v1 structured claim pinned as its rendering and verb. The
sets are synthetic and local; they do not contain owner data.

## Honest set

`honest.jsonl` has **1,185 cases**. These text-shape categories are disjoint;
membership describes containment, not whether the endpoint is accepted:

| Statement shape | Count |
| --- | ---: |
| Exact claim text | 314 |
| Exact printable text, including reviewed field lines | 6 |
| Normalized whole-phrase prefix of claim text | 294 |
| Complete post-verb remainder | 297 |
| Shorter normalized whole-phrase prefix of post-verb remainder | 272 |
| Intentionally blocked pre-existing mid-claim cut | 1 |
| Intentionally blocked vocabulary-leading post-verb remainder | 1 |
| **Total** | **1,185** |

Only **314/1,185** statements equal their claim text; even counting the six exact
printable texts gives **320/1,185**, below the maximum of one half. All preceding
**1,135 records remain unchanged and in their original order**. The 50 endpoint
additions contain 34 accepted prefixes and 16 deliberately blocked measurements,
each covering both claim-prefix and post-verb-prefix shapes.

A normalized prefix may end only at the end of the claim, before `,`, `;`, `:`
or `(`, or before one of these whole words (case-insensitive):
`for`, `by`, `to`, `on`, `with`, `in`, `across`, `from`, `through`, `during`, `at`,
`that`. Numeric, currency and hyphen protections still apply. Exact claim text
always passes the shape rule. This closed boundary list is not semantic inference:
`Built the API for billing team` → `Built the API` and
`Reduced latency by 30% for one pilot region` → `Reduced latency by 30%` pass.
Dropping trailing qualifiers introduced by a boundary word is an accepted limitation.

Under `claim-evidence-v4`, the deliberately retained measured costs are:

- **Line 875:** `the billing migration`, cut from `Assisted the team that led the
  billing migration`. This begins inside the claim, not at its start or immediately
  after its pinned leading verb phrase.
- **Line 1131:** `automated tests for the API`, cut from `Wrote automated tests for
  the API`. The post-verb remainder begins with the vocabulary verb `automated` and
  is guarded without lexical exceptions. The set also retains the accepted prefix
  `Wrote automated tests`.
- **Lines 1170–1185:** paired cuts inside `team's`, `teams'`, `QA/release`,
  `Node.js`, `migration planning`, `$2 million`, `2 per 1000 requests` and
  `migration jointly`. These are deliberate endpoint measurement cases in the
  honest report lane (`kind: null`), **not** verb promotions and not a new public
  inflation kind. Their inclusion conservatively charges the false-block rate.

All 18 report `statement_cut_inside_claim`. The measured honest false-block rate is
**18/1,185**, conservatively displayed as **1.6%**, below the unchanged **5%** ceiling.
The original 1,135 rows retain exactly their original two blockers; no additional
endpoint costs appear among those preceding rows.

Post-verb cuts remove only the renderer's actual pinned leading verb wording for a
structured claim, and the longest leading vocabulary phrase for a literal claim.
A vocabulary verb optionally preceded by `and`, `&` or `,` cannot begin the remainder.
Hyphen-joined words are not vocabulary verbs, and cuts inside them are not allowed.
Exact printable text remains accepted; normalization applies to permitted prefixes,
not arbitrary mid-claim fragments.

## Inflated set

`inflated.jsonl` is unchanged and has **123 cases**, at least ten of each kind:

| Kind | Count | Expected blocker |
| --- | ---: | --- |
| `stronger_verb` | 15 | `statement_cut_inside_claim` |
| `longer_tenure` | 16 | `statement_not_in_claim` |
| `larger_metric` | 74 | `statement_not_in_claim` |
| `wider_scope` | 18 | `statement_not_in_claim` |

Every inflated statement is contained in its own evidence. The 15 verb-promoting
cuts are also contained in their claim text but start at a later verb; the other
three kinds borrow wording from evidence outside the pinned claim text. Evidence
containment alone would therefore pass every inflated case.

## Validation

The report tests enforce the category counts, preserved honest cases, expected
blocker codes and evidence containment of every inflated statement. Running:

```sh
uvx --from uv==0.13.0 uv run --frozen bonafides claims inflation-report tests/fixtures/inflation
```

produces:

```text
Honest: 18 of 1185 blocked (false-block 1.6%)
honest line 875: statement_cut_inside_claim
honest line 1131: statement_cut_inside_claim
honest line 1170: statement_cut_inside_claim
honest line 1171: statement_cut_inside_claim
honest line 1172: statement_cut_inside_claim
honest line 1173: statement_cut_inside_claim
honest line 1174: statement_cut_inside_claim
honest line 1175: statement_cut_inside_claim
honest line 1176: statement_cut_inside_claim
honest line 1177: statement_cut_inside_claim
honest line 1178: statement_cut_inside_claim
honest line 1179: statement_cut_inside_claim
honest line 1180: statement_cut_inside_claim
honest line 1181: statement_cut_inside_claim
honest line 1182: statement_cut_inside_claim
honest line 1183: statement_cut_inside_claim
honest line 1184: statement_cut_inside_claim
honest line 1185: statement_cut_inside_claim
Inflated stronger_verb: 15 of 15 blocked (100.0%)
Inflated longer_tenure: 16 of 16 blocked (100.0%)
Inflated larger_metric: 74 of 74 blocked (100.0%)
Inflated wider_scope: 18 of 18 blocked (100.0%)
False-block rate within the 5% limit
```

Stop and report if the honest false-block rate exceeds 5%; do not remove honest
cases to improve the rate.
