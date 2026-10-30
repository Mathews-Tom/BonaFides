# Release compliance checklist

Status: **not executed; no public release authorized by this design session**. Each future release needs an owner, reviewer, date, exact commit and artifact/image digests, reports, exceptions and verdict. Checkboxes below are requirements, not assertions.

The owner separately authorized the [identified source-only review PR](licensing-policy.md#scoped-source-publication-authorization) on 2026-10-26. That scoped sequencing exception permits its branch push and PR creation without claiming the independent reviews below have occurred. It authorizes no merge, tag, binary/image/package release or broader public-distribution clearance. All checkboxes retain their actual unexecuted/pending status; actual rights, notices and confidentiality obligations are not waived.

## Architecture proof — repository initialization

- [ ] Full canonical MIT and Apache-2.0 texts, NOTICE, CONTRIBUTING, SECURITY, canonical ADR and six compliance policies present.
- [ ] All applicable original-source and package/publication declarations say `MIT OR Apache-2.0`; third-party headers remain intact.
- [ ] Inbound dual-license authorization plus DCO policy established before external acceptance; implementation CI checks them.
- [ ] Provenance ledger and dependency/asset inventory started; locked versions and scan/report formats defined.
- [ ] Secret scanning and license/notice checks configured and demonstrated failing on synthetic violations.

## Private alpha — private usable alpha

- [ ] Direct and transitive Python/frontend dependencies, native libraries, OS/base images and build dependencies reviewed for exact deployed platform.
- [ ] Runtime/source/font/dictionary attribution and source/relink conditions demonstrably satisfied, including psycopg and renderer chains.
- [ ] Security/vulnerability scans run on actual artifacts; failures/unknown coverage are blocking, not silently skipped.
- [ ] Data egress/source terms recorded per configured employer board and model; no weights/data/assets assumed project-licensed.
- [ ] Import/export and backup design prevent secrets/data leakage into release artifacts or evidence reports.

## Public beta — before any public beta or other public distribution

- [ ] SPDX/CycloneDX SBOM, license scan, transitive graph, vulnerability and secret reports bound to exact final artifacts.
- [ ] Notices and full applicable license texts actually included in source/binary/images; corresponding source/relink obligations and retention are satisfied.
- [ ] Contributors' rights, employer authorization if relevant, DCOs and explicit grants under both licenses reviewed; no disputed/unresolved material incorporated.
- [ ] Independent-authorship provenance reviewed; potentially contaminated material excluded or resolved through qualified review without disclosing restricted details.
- [ ] Qualified legal review completed for eligibility, uncertain compatibility, model/data/service terms and public claims. Unresolved rights concerns block publication.
- [ ] Monitored private security reporting channel, supported-version statement and accountable responders established.
- [ ] Release candidate privacy/security/a11y/restore/load gates completed; scan tooling is not claimed as a penetration test or legal certification.

## Stable distribution — stable self-hosted distribution

- [ ] Repeat all public-distribution gates on final source, browser bundle and per-platform image digests, after any packaging change.
- [ ] Reproducible build evidence, provenance attestation, checksums/signatures and verification instructions supplied; signing identity/trust policy documented.
- [ ] Upgrade/restore tested from supported prior release, licensing/notice inventory updated, known issues and time-limited security dispositions disclosed.
- [ ] No unapproved strong-copyleft/custom/unknown component, missing attribution, expired review or unresolved independence concern remains.
- [ ] Named release owner and independent reviewer sign the artifact-bound verdict; no version/tag/publication occurs without separate authorization.

No generic waiver can grant missing rights. Legal ambiguity must be resolved or the component removed/replaced before distribution. Critical exploitable security findings require remediation or an evidence-backed non-applicability decision; operational convenience is insufficient. Reports must distinguish automated detection, human review, unreviewed scope, and qualified legal conclusions.
