# Licensing policy

## Scope and authority

Original, independently authored BonaFides code and documentation is offered as **`MIT OR Apache-2.0`**: recipients choose either license. [ADR-0001](../decisions/ADR-0001-dual-licensing.md) is canonical. The full texts are [LICENSE-MIT](../../LICENSE-MIT) and [LICENSE-APACHE](../../LICENSE-APACHE); neither is modified by this policy. The project does not use `MIT AND Apache-2.0` for recipient obligations.

This direction is binding, but legal eligibility is not certified. Contributors must actually own or be authorized to grant relevant rights. Third-party code/assets, user data, fonts, datasets, model weights and provider/service contracts do not become MIT/Apache merely because used by BonaFides. User résumés and generated personal documents are not automatically contributions or project-licensed content.

## Required declarations

Use `MIT OR Apache-2.0` in applicable future manifests, SPDX publication metadata and original-source headers. Use a language-appropriate `SPDX-License-Identifier: MIT OR Apache-2.0` comment where supported. Do not inject comments into JSON or binary formats, overwrite third-party headers, or rewrite license templates. A file manifest/provenance record covers formats without headers. Preserve actual copyright holders; the collective contributor label is not an assignment.

Distribute both full project license texts and an accurate NOTICE with source and binary artifacts. Maintain artifact-specific third-party license bundles and notices independently. Apache change notices must be preserved where required. A third-party attribution is not permission to incorporate that material.

## Governance and gates

Maintainers review inbound authorization, provenance and exact shipped obligations at incorporation; the release owner checks the release checklist; qualified legal review addresses independence, uncertain compatibility and public eligibility claims. Unknown license, unclear ownership, missing consent, or unsatisfied obligations blocks acceptance/distribution, not merely a warning. Exceptions need a written component/version/distribution-specific legal rationale, approver, conditions and expiry; no exception may purport to grant rights the project lacks.

No license/dependency/secret scan or counsel approval is claimed by this design document. Automated implementation gates must begin at architecture proof and run again against each exact release artifact. A later license change requires a new ADR and rights review; it cannot silently revoke grants already made.

### Scoped source publication authorization

On 2026-10-26, the repository owner, Mathews-Tom, explicitly authorized an owner-approved, source-only public pull request from `feat/job-search-and-application-workflows` into `main`. The implementation scope is commit `fc8a0685b3fee0a91c30dab3a4262b40bd0cdc51`, together with the documentation/provenance changes recording this authorization. This is a narrow change to the sequencing of the project's review gates for that PR, not an independent provenance verdict, legal opinion or certification of third-party rights.

The rationale is to permit public review of the owner's authorized original-source contribution under the existing `MIT OR Apache-2.0` grant while leaving independent provenance/legal review explicitly pending. Original and third-party license texts, notices, contribution disclosures and actual license obligations remain applicable. This authorization cannot grant missing rights, excuse known unauthorized material or bypass a demonstrated secret/security blocker.

Only the source branch and PR are authorized. No merge, tag, binary/image/package release, private-data/evidence upload, model download, paid/hosted call or unqualified integration enablement is authorized. Human-review stage human validation and actual-model/real-board qualification retain their existing dispositions. Binary/image/package distribution remains blocked. This authorization ends when that PR closes and does not cover unrelated branches or material implementation changes beyond the stated scope.


Sources reviewed 2026-10-24: [MIT](https://spdx.org/licenses/MIT.html), [Apache-2.0](https://www.apache.org/licenses/LICENSE-2.0.txt).
