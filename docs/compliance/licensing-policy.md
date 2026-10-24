# Licensing policy

## Scope and authority

Original, independently authored Offerloom code and documentation is offered as **`MIT OR Apache-2.0`**: recipients choose either license. [ADR-0001](../decisions/ADR-0001-dual-licensing.md) is canonical. The full texts are [LICENSE-MIT](../../LICENSE-MIT) and [LICENSE-APACHE](../../LICENSE-APACHE); neither is modified by this policy. The project does not use `MIT AND Apache-2.0` for recipient obligations.

This direction is binding, but legal eligibility is not certified. Contributors must actually own or be authorized to grant relevant rights. Third-party code/assets, user data, fonts, datasets, model weights and provider/service contracts do not become MIT/Apache merely because used by Offerloom. User résumés and generated personal documents are not automatically contributions or project-licensed content.

## Required declarations

Use `MIT OR Apache-2.0` in applicable future manifests, SPDX publication metadata and original-source headers. Use a language-appropriate `SPDX-License-Identifier: MIT OR Apache-2.0` comment where supported. Do not inject comments into JSON or binary formats, overwrite third-party headers, or rewrite license templates. A file manifest/provenance record covers formats without headers. Preserve actual copyright holders; the collective contributor label is not an assignment.

Distribute both full project license texts and an accurate NOTICE with source and binary artifacts. Maintain artifact-specific third-party license bundles and notices independently. Apache change notices must be preserved where required. A third-party attribution is not permission to incorporate that material.

## Governance and gates

Maintainers review inbound authorization, provenance and exact shipped obligations at incorporation; the release owner checks the release checklist; qualified legal review addresses independence, uncertain compatibility and public eligibility claims. Unknown license, unclear ownership, missing consent, or unsatisfied obligations blocks acceptance/distribution, not merely a warning. Exceptions need a written component/version/distribution-specific legal rationale, approver, conditions and expiry; no exception may purport to grant rights the project lacks.

No license/dependency/secret scan or counsel approval is claimed by this design document. Automated implementation gates must begin at M0 and run again against each exact release artifact. A later license change requires a new ADR and rights review; it cannot silently revoke grants already made.

Sources reviewed 2026-10-24: [MIT](https://spdx.org/licenses/MIT.html), [Apache-2.0](https://www.apache.org/licenses/LICENSE-2.0.txt).
