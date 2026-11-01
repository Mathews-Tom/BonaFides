# Claim-scoped inflation case sets

Each JSONL record has `evidence`, `claim`, `statement` and `kind`; optional `fields`
hold reviewed structured-field lines for the exact pinned printable text. A claim is
literal text or a schema-v1 structured claim pinned as its rendering and verb. The
sets are synthetic and local; they do not contain owner data.

## Honest set

`honest.jsonl` has **1,135 cases**. These text categories are disjoint:

| Statement shape | Count |
| --- | ---: |
| Exact claim text | 314 |
| Exact printable text, including reviewed field lines | 6 |
| Normalized whole-phrase prefix of claim text | 269 |
| Complete post-verb remainder | 297 |
| Shorter normalized whole-phrase prefix of post-verb remainder | 247 |
| Intentionally blocked pre-existing mid-claim cut | 1 |
| Intentionally blocked vocabulary-leading post-verb remainder | 1 |
| **Total** | **1,135** |

Only **314/1,135** statements equal their claim text; even counting the six exact
printable texts gives **320/1,135**, below the maximum of one half. All original
875 records remain in their original order, rather than removing honest cuts that
the stricter shape rule blocks. The 260 additions cover shorter post-verb prefixes,
structured pinned verb phrases, normalized whitespace/case and hyphen-joined words.

Under `claim-evidence-v3`, the two deliberately retained costs are expected to be:

- **Line 875:** `the billing migration`, cut from `Assisted the team that led the
  billing migration`. This begins inside the claim, not at its start or immediately
  after its pinned leading verb phrase.
- **Line 1131:** `automated tests for the API`, cut from `Wrote automated tests for
  the API`. The post-verb remainder begins with the vocabulary verb `automated` and
  is guarded without lexical exceptions. The set also retains the accepted prefix
  `Wrote automated tests`.

Both expect `statement_cut_inside_claim`. The expected honest false-block rate is
**2/1,135**, conservatively displayed as **0.2%**, below the unchanged **5%** ceiling.
The former multi-verb prefix cases at lines 698 and 700 now pass: prefixes are no
longer restricted to whole multi-verb claims.

Post-verb cuts remove only the renderer's actual pinned leading verb wording for a
structured claim, and the longest leading vocabulary phrase for a literal claim.
A vocabulary verb optionally preceded by `and`, `&` or `,` cannot begin the remainder.
Hyphen-joined words are not vocabulary verbs, and cuts inside them are not allowed.
Exact printable text remains accepted; normalization applies to the permitted
whole-phrase prefix shapes, not to arbitrary mid-claim fragments.

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
Honest: 2 of 1135 blocked (false-block 0.2%)
honest line 875: statement_cut_inside_claim
honest line 1131: statement_cut_inside_claim
Inflated stronger_verb: 15 of 15 blocked (100.0%)
Inflated longer_tenure: 16 of 16 blocked (100.0%)
Inflated larger_metric: 74 of 74 blocked (100.0%)
Inflated wider_scope: 18 of 18 blocked (100.0%)
False-block rate within the 5% limit
```

Stop and report if the honest false-block rate exceeds 5%; do not remove honest
cases to improve the rate.
