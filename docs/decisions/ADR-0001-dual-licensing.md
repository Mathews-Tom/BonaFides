# ADR-0001: Offer original BonaFides work under MIT OR Apache-2.0

- Status: Accepted as the project licensing direction required by the brief; distribution eligibility and legal review remain open release gates.
- Date: 2026-10-24
- Decision owner: BonaFides maintainers; this design records no stakeholder or counsel approval.
- Scope: independently authored BonaFides code and documentation; third-party components, user data, model weights, datasets, and service terms remain separate.
- Supersedes: none.

## Context

BonaFides should support self-hosting, commercial use, modification, redistribution, and proprietary derivatives. Recipients need a familiar permissive choice while contributors need a clear inbound authorization. The product must be independently implemented from approved neutral specifications, not copied protected expression. Clean-room practices reduce risk but do not prove legal eligibility or eliminate patent, trademark, contract, privacy, or data-rights issues.

The architecture proposes components with their own licenses, including weak-copyleft database client/native rendering dependencies. No lockfile, complete dependency graph, production image, bundled font collection, model, or dataset has been audited in this design session. A top-level permissive license cannot erase those obligations or confer rights the contributor does not own.

## Decision

Offer independently authored BonaFides material under **`MIT OR Apache-2.0`**, so a recipient may choose either license. Include canonical `LICENSE-MIT` and `LICENSE-APACHE`, an accurate informational `NOTICE`, and consistent manifest/SPDX declarations. Do not use `MIT AND Apache-2.0` to describe recipient obligations.

Require explicit authorization under both licenses, DCO 1.1 sign-offs, and provenance disclosure before accepting contributions. Do not require a CLA at this stage. A DCO certifies origin/authority under its actual terms; it is not a copyright assignment, broad relicensing grant, or replacement for the explicit dual-license authorization. Do not claim every inherited third-party contribution can be reissued under both licenses.

## Alternatives considered

| Alternative | Benefits | Why not selected |
| --- | --- | --- |
| MIT only | Short, familiar, permissive; simple notices | Does not provide Apache-2.0's express contributor patent grant/termination mechanism to recipients choosing that protection |
| Apache-2.0 only | Express scoped patent license; modification and NOTICE rules | Removes the brief's required recipient choice and simpler MIT path |
| GPL-3.0 | Strong reciprocal distribution requirements | Different product adoption/derivative policy; cannot be advertised as the required permissive dual-license original work |
| AGPL-3.0 | Extends reciprocity to some network use | Hosted-use reciprocity conflicts with chosen permissive strategy and adds unwanted adoption friction |
| BSD-3-Clause or ISC | Familiar permissive alternatives | No material benefit over the required MIT option and no Apache-style express patent mechanism |
| Proprietary or dual commercial/AGPL licensing | Central commercial leverage | Conflicts with required permissive public licensing; would require separate rights/governance analysis |
| DCO only, no explicit dual authorization | Lower form burden | DCO alone does not unambiguously capture authorization under both proposed licenses |
| Broad CLA/assignment now | Could grant future relicensing rights centrally | Unneeded for current goals; adds trust and legal overhead without a confirmed rights holder or commercial requirement |

## Patent and attribution trade-offs

Apache-2.0 section 3 provides a contributor patent grant limited to necessarily infringed contributor claims described there and terminates that license for specified patent litigation. It is not a patent clearance or indemnity. A recipient choosing MIT does not automatically receive the distinct express Apache patent license by this policy's assertion; interpretation of combined grants and litigation is for qualified counsel. MIT includes no explicit patent-grant section. Neither choice grants general trademark permission. Preserve applicable notices and Apache modification notices when operating under Apache terms. NOTICE is informational, not an additional license condition invented by the project.

## Dependency and implementation conditions

Review each exact shipped component separately for rights, chosen expression branch, notices, source/relink obligations, patent terms, and bundling. MIT/Apache/BSD/PostgreSQL licensing is a starting point, not a universal compatibility proof. LGPL/MPL may be usable with documented conditions; GPL/AGPL combined-work questions, custom/noncommercial/model terms, unknown licenses, and incomplete attribution block incorporation until reviewed. Prefer an independently licensed alternative when obligations cannot be satisfied.

Keep a provenance ledger recording neutral requirement IDs, decisions, independent research, contributors/tool assistance, and material origins. Quarantine suspected contamination without reproducing it publicly. Do not access restricted reference code to resolve a concern. Qualified legal review of independence, dependency distribution, and inbound authorizations is mandatory before any public claim that the implementation is eligible for the selected licenses.

## Consequences

Positive: recipients can choose a permissive route; Apache recipients have an express scoped patent grant; one consistent source/manifest expression; no central copyright assignment barrier. Negative: two license texts and nuanced patent explanations must be maintained; inbound authorization must cover both; future relicensing is not assured; transitive/native assets still create compliance work. Neutral: contributors retain ownership; paid services and proprietary derivatives are allowed subject to applicable terms; trademark rights and users' résumé/data rights are not granted away.

## Reconsideration triggers and unresolved questions

Reconsider prospectively through a new ADR if governance changes, a commercial plan needs rights unavailable today, a required component cannot be distributed compliantly, a patent/ownership claim arises, or counsel rejects a proposed combination. Existing grants cannot simply be revoked by a new ADR. Open release blockers: confirm actual authors/rights and any employer authorization; review design-to-code provenance; select exact dependency versions and distribution form; qualify native/dictionary/font licensing; approve public legal statements. No actual CLA execution, DCO CI, scan, or legal opinion is claimed here.

## References

Reviewed 2026-10-24: [MIT official SPDX text](https://spdx.org/licenses/MIT.html); [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0.txt), especially sections 3–6; [DCO 1.1](https://developercertificate.org/). Operational policies: [licensing](../compliance/licensing-policy.md), [dependencies](../compliance/dependency-license-policy.md), [contributors](../compliance/contributor-licensing.md), [provenance](../compliance/clean-room-provenance-policy.md), [attribution](../compliance/third-party-attribution-policy.md), [release gate](../compliance/release-compliance-checklist.md).
