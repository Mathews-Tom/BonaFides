# Contributing to BonaFides

Thank you for helping. BonaFides is a self-hosted job-search workspace whose users are mostly not developers, so clear bug reports and documentation fixes are as valuable as code.

BonaFides is at an early stage: a local implementation, not a production-qualified application. This guide covers the ways to contribute, the rules every pull request must meet, and how changes are reviewed.

## Ways to contribute

| You want to… | Do this |
|---|---|
| Report a bug or confusing behavior | Open a [GitHub issue](https://github.com/Mathews-Tom/BonaFides/issues). Say what you did, what you expected and what happened, and include the commit you're running. Use made-up data only. |
| Report a security problem | **Don't open an issue.** Follow the [security policy](SECURITY.md) to report it privately. |
| Suggest an improvement | Open an issue describing the problem it solves before writing code, so we can agree on the approach. |
| Fix the documentation | Open a pull request. User-facing pages are in [`docs/user-guide/`](docs/user-guide/README.md). |
| Change the code | Read the rest of this guide, then open a pull request. |

Never post real résumés, personal profiles, passwords, setup codes, keys, backups or anything from a `secrets` folder in an issue, pull request or screenshot.

## Before your first pull request

Every pull request must include a licensing statement in its description, and every commit must be signed off. The automated `contribution` check rejects pull requests without them.

### 1. Include the licensing statement

Contributions are accepted under **both** the MIT License and the Apache License, Version 2.0, so recipients can choose either (**`MIT OR Apache-2.0`**; not `MIT AND Apache-2.0`). You keep your copyright; no assignment or general right to relicense in the future is requested. Third-party work keeps its original terms and must be identified as such, not presented as your own dual-licensed work.

Paste this exact text into your pull request description:

> I have the right to submit this contribution and authorize its distribution under both the MIT License and Apache License, Version 2.0, with recipients entitled to choose either license (MIT OR Apache-2.0). I have disclosed third-party material and relevant provenance, including AI assistance, and have obtained any necessary employer or other rights-holder authorization.

### 2. Sign off every commit

Certify the unmodified [Developer Certificate of Origin 1.1](https://developercertificate.org/) by adding a `Signed-off-by: Name <email>` trailer to each commit, matching the commit's author. `git commit -s` adds it for you; it isn't a cryptographic signature. Use a name and email you're authorized to publish, because sign-offs become part of public history.

A sign-off alone doesn't grant both licenses or settle questionable ownership; maintainers check both the statement and the sign-offs. No contributor license agreement is required under [ADR-0001](docs/decisions/ADR-0001-dual-licensing.md). Any future agreement would be a separate decision, not an implied right to relicense existing work.

The two original owner bootstrap commits have an explicitly authorized, [hash-bound retroactive certification](docs/compliance/bootstrap-commit-attestations.md). Their published commit metadata is unchanged. This doesn't exempt later contributions from sign-offs, the licensing statement or provenance review.

### 3. Write it yourself

Work from approved neutral specifications and independently researched public documentation. Don't submit:

- source you aren't allowed to use, decompiled code or source-like traces;
- copied prompts, distinctive interface designs or screenshots from other products;
- employer data, real résumés, secrets or unlicensed assets.

Renaming or paraphrasing protected code doesn't make it independent work. Don't go looking for restricted material to prove where something came from. Instead, record the neutral requirement, your decision, the independent sources you used, any author or tool assistance (including AI), and the paths you changed. If you're unsure about a contribution's origin, don't post the suspect content; contact the maintainer privately. See the [clean-room provenance policy](docs/compliance/clean-room-provenance-policy.md).

### 4. Ask before adding dependencies

Before adding a dependency, asset, font, model or dataset, provide its exact version or digest, official terms URL, license expression, scope (build, runtime, bundled or service), transitive and native dependencies, distribution obligations and proposed notices. Lockfile metadata alone isn't legal review, and BonaFides's license choice never relicenses anyone else's component. See the [dependency license policy](docs/compliance/dependency-license-policy.md).

## Set up and check your changes

The [developer guide](docs/development/README.md) has everything you need:

- [Requirements and frozen installs](docs/development/README.md#requirements-and-frozen-installs) sets up the pinned Python, uv, Node and pnpm toolchain.
- [Local setup and lifecycle](docs/development/README.md#local-setup-and-lifecycle) runs a development stack.
- [Development gates](docs/development/README.md#development-gates) lists the checks CI runs: Ruff lint and format, strict mypy and pytest for the backend; TypeScript, ESLint, tests, build and audit for the web interface. Backend tests need a disposable PostgreSQL 18 database; never point them at a personal or production database.

Run the checks that cover your change before opening a pull request. The [architecture overview](docs/architecture/README.md) explains the isolation boundaries your change must keep, and the [documentation index](docs/README.md) lists everything else.

## What a good pull request looks like

- **Scoped.** One logical change. Name the feature you change by what it does, for example "encrypted backup and recovery", and link the relevant contract in the developer guide.
- **No planning codes.** Don't use planning, milestone or story codes in branches, commits, code, tests or documentation; see [ADR-0003](docs/decisions/ADR-0003-bonafides-name.md).
- **Conventional Commit titles.** Pull request titles and commit subjects follow [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/), for example `fix(import): keep review choices after a conflict`. Mark breaking changes with `!`.
- **Synthetic data only** in fixtures, tests and examples.
- **Explained.** Describe the behavior, failure paths, migration and rollback impact, privacy or network-egress changes, accessibility, and the checks you actually ran with their results.
- **Honest about evidence.** Never present an unrun scan, a stub-only model test or an automated accessibility scan as proof of production readiness. New adapters need conformance tests and an approved real local smoke test before they're enabled. Anything that writes to external services or costs money needs separate explicit authorization.
- **Documented.** The README and the [user guide](docs/user-guide/README.md) are written for people who use BonaFides, most of whom aren't developers. If your change alters what a user sees or does, update the matching user-guide page in the same pull request and quote interface labels exactly. Contracts, contributor commands and measured evidence belong in the developer guide. Add an entry under **Unreleased** in the [changelog](CHANGELOG.md) for notable changes.
- **Licensed source files.** Original source files carry `SPDX-License-Identifier: MIT OR Apache-2.0` where the format allows, without replacing third-party headers. Package manifests and publication metadata use the same expression. Don't add comments to JSON, generated lock files or binary files; record their provenance separately.

## How pull requests are reviewed

Maintainers accept a change only when its authorization, provenance, dependency review, contract correctness and CI all pass.

- The **`contribution` check** runs whenever a pull request is opened, reopened, edited or labelled, or receives new commits. It verifies the licensing statement and every commit's sign-off.
- The **fast CI tier** runs on every push. The required `fast (amd64)` result waits for the lint, format and strict-mypy job, the web job, the source-compliance job and all five backend pytest shards; every one must pass. Each shard builds the sealed parser and renderer image and uses its own disposable PostgreSQL database, without concurrent pytest workers. Backend-only and web-only pushes run the same complete tier.
- **Adding or renaming a test file** anywhere under `tests/`? Update `.github/backend_shards.json` and run `uv run --frozen python .github/backend_shards.py`. The partition guard discovers files with pytest's configured filename patterns (by default `test_*.py` and `*_test.py`) and rejects missing, duplicate and nonexistent test paths.
- Before merging, maintainers apply the **`full-ci`** label to run the full amd64 and arm64 tier, including image scans and runtime proof. The full tier runs again on `main` after the merge. See [dependency, contribution and release gates](docs/development/README.md#dependency-contribution-and-release-gates).

Release compliance is a blocking gate, not optional housekeeping. The policies behind it are in [`docs/compliance/`](docs/compliance/).
