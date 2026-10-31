# Contributing to BonaFides

BonaFides contains a local architecture-proof implementation, not a production-qualified application. Start with the public [development guide](docs/development/README.md) for frozen installation, checks and the disposable proof, and the [architecture overview](docs/architecture/README.md) for boundaries and limitations. Detailed implementation planning is maintained locally and is not included in repository clones. Distinguish commands and implemented controls from observed qualification evidence. External contributions must satisfy the policies below before acceptance.

## Licensing authorization

Original contributions are accepted for distribution under **both** the MIT License and Apache License, Version 2.0, allowing recipients to choose **`MIT OR Apache-2.0`**. This is not `MIT AND Apache-2.0` for recipients. You retain your copyright; no assignment or general future-relicensing power is requested. Third-party work retains its original terms and must be explicitly identified rather than represented as your original dual-licensed work.

Include this exact authorization in each contribution submission:

> I have the right to submit this contribution and authorize its distribution under both the MIT License and Apache License, Version 2.0, with recipients entitled to choose either license (MIT OR Apache-2.0). I have disclosed third-party material and relevant provenance, including AI assistance, and have obtained any necessary employer or other rights-holder authorization.

Also certify the unmodified [Developer Certificate of Origin 1.1](https://developercertificate.org/) by adding a `Signed-off-by: Name <email>` trailer to each contributed commit (`git commit -s` does not create a cryptographic signature). Use an identity you are authorized to publish; sign-off details become part of public history. Maintainers must verify both the submission authorization and DCO trailers. A DCO sign-off alone does not grant both licenses or resolve questionable ownership. No CLA is required under [ADR-0001](docs/decisions/ADR-0001-dual-licensing.md); any future CLA is a separate prospective governance decision, not an implied right to relicense existing work.

The two original owner bootstrap commits have an explicitly authorized, [hash-bound retroactive certification](docs/compliance/bootstrap-commit-attestations.md). Their published commit metadata remains unchanged. This historical disposition does not exempt subsequent contributions from sign-offs, submission authorization or provenance review.

## Independent authorship and dependencies

Work from approved neutral specifications and independently researched public documentation. Do not submit restricted reference source, decompiled/source-like traces, copied prompts, distinctive UI, screenshots, employer data, real résumés, secrets, or unlicensed assets. Renaming or paraphrasing protected code does not establish independence. Do not seek restricted materials to prove provenance; record the neutral requirement, decision, independent sources, author/tool assistance, and changed paths. Quarantine uncertain contributions and contact the maintainer privately without posting the suspect content. See [clean-room policy](docs/compliance/clean-room-provenance-policy.md).

Before adding a dependency, asset, font, model, or dataset, provide its exact version/digest, official terms URL, license expression, scope (build/runtime/bundled/service), transitive/native dependencies, distribution obligations, and proposed notices. Lockfile metadata alone is not legal review. No component is automatically relicensed by BonaFides's license choice. See [dependency policy](docs/compliance/dependency-license-policy.md).

## Change quality

Keep changes scoped; name the BonaFides feature you change by what it does (for example "encrypted backup and recovery") and cite the relevant design contract. Do not use planning, milestone or story codes in branches, commits, code, tests or documentation; see [ADR-0003](docs/decisions/ADR-0003-bonafides-name.md). Use synthetic fixtures only. Explain behavior, failure paths, migration/rollback impact, privacy/egress changes, accessibility, and checks actually executed with their results. Never claim an unrun scan, stub-only provider test, or automatic accessibility scan proves production readiness. New adapters require conformance tests and an approved real local smoke before enabling them; external writes and paid calls need separate explicit authorization.

Original source files should carry `SPDX-License-Identifier: MIT OR Apache-2.0` where the format permits, without replacing third-party headers. Application package manifests and publication metadata must use the same expression. Do not insert comments into JSON, generated lock metadata or binary files; record their provenance separately. The [development guide](docs/development/README.md#development-gates) defines the application checks and their disposable PostgreSQL prerequisites.

## Review and reporting

Maintainers gate acceptance on authorization, provenance, dependency review, contract correctness and application CI. Every pull request runs the `contribution` check on each event (including description edits) and the fast CI tier on each push; maintainers apply the `full-ci` label to run the full amd64 and arm64 tier, with image scans and runtime proof, before merging, and the full tier runs again on `main` after merge. See the [development guide](docs/development/README.md#dependency-contribution-and-release-gates). Report vulnerabilities using [SECURITY.md](SECURITY.md), not public payloads containing personal data. Release compliance is blocking, not optional housekeeping. Policies are under [docs/compliance](docs/compliance/).
