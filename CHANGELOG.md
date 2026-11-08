# Changelog

All notable changes to BonaFides are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Root `install.sh`, `start.sh`, `stop.sh` and `uninstall.sh` separate source-checkout preparation from runtime management; installed management copies work without the checkout using saved settings.
- `bonafides demo load` loads synthetic profile, evidence and claims into an existing, empty owner profile; reruns are no-ops and any real or changed state is refused without writes.
- `tools/core_loop_proof.py` proves posting, checked résumé and letter, exact-revision review, PDF export and saved tracking on the release image in production mode with the explicit fixture model; unsupported and ownership-inflated output blocks review/export. The full tier runs it on both architectures. `tools/demo_capture.py` rebuilds the README workflow recording.
- `deploy/install.sh` prepares a stopped installation from a locally loaded application image without starting services; its `bonafides-lifecycle-1` contract and read-only preflight require the sibling `lifecycle.sh` helper. Separate startup runs migrations and renews bootstrap only while ownerless, without printing the token.
- `tools/gate_controls.py` proves clean-to-blocking transitions for source secrets, license headers, NOTICE, unapproved rights and pinned Trivy secrets with synthetic input and redacted findings; the full tier runs it on both architectures.
- `tools/release_candidate.py` binds a candidate to version, source revision, metadata-only index, native platform digests and exact gate evidence, validates scanner, inventory and application receipt image subjects, and signs/verifies with pinned cosign 3.1.4 and an explicitly trusted public key. `tools/candidate_proof.py` proves signing and verification with a disposable non-production key and rejection of altered bytes, a wrong key, and missing, altered, mismatched or changed-source inputs; tool failures cannot count as successful negative controls. Passing automation never authorizes a release; production signing identity and human approvals remain pending.

### Changed

- The README is written for people who use BonaFides: what it does and won't do, its current readiness, installation and first sign-in, and the measured checker results in plain language. Developer material moved behind links.
- A new [user guide](docs/user-guide/README.md) covers every page of the workspace step by step: installation, profile and sources, résumé import, postings and requirements, documents and PDF export, application tracking, background tasks, account settings, data export and deletion, optional AI tailoring, installation management and troubleshooting. A [documentation index](docs/README.md) separates user and developer material.
- The developer guide is reorganized into setup and checks, installation and releases, feature contracts, model route and checker, data and recovery, and project history, with a contents list. Section headings and their link anchors are unchanged. The architecture overview opens with a plain summary of each container's role.
- `SECURITY.md` replaces the design-stage placeholder: private reports go through GitHub private vulnerability reporting with best-effort response, only the latest `main` receives fixes until releases exist, and the policy states what BonaFides protects against, what it doesn't, how to keep an installation safe and what to do after a suspected compromise.
- `CONTRIBUTING.md` is reorganized for first-time contributors: ways to contribute, the licensing statement and sign-off the `contribution` check requires, provenance and dependency rules, local checks, pull-request expectations and the review process. Licensing, provenance and review policies are unchanged; it now also asks for Conventional Commit titles and changelog entries, matching existing practice.
- Installation ends prepared, not running, and refuses running targets instead of restarting them. Normal uninstall confirms runtime removal while retaining data, configuration and keys; purge requires separate exact-project consent and removes only owned managed resources.
- Install and start are noninteractive: fresh installs use defaults or explicit `--dir`, `--port` and `--project` settings, while reinstalls reuse saved settings and image unless `--image` or `--build` is supplied. Conflicts fail rather than prompting or stopping/adopting resources; existing project and port settings remain fixed.
- Start opens a browser only with `--open-browser` and prints a private-terminal token retrieval command, never the token. It explains the 15-minute ownerless setup window and renewal by rerunning start without reinstalling or losing data; after bootstrap, the owner uses normal password sign-in. The obsolete install consent and start browser-suppression flags are removed; uninstall consent and purge rules are unchanged.
- Demo loading honors installation maintenance and workspace mutation locks, and advances the workspace revision only when it seeds data so stale destructive approvals cannot authorize the new data.
- Proof teardown removes a swap-image tag only when that run successfully built it, preserving unrelated tags during core-loop and recording cleanup.
- Image inventories bind classic Docker config IDs or containerd platform manifests through the unstarted, network-free inventory container, without requiring the newer `docker image inspect --platform` CLI flag.
- Credential generation moved from `tools/dev_setup.py` to the image-shipped `python -m bonafides.local_setup`; the development commands use the new module path.

### Fixed

- Literal profile saves and reviewed résumé imports require whole-phrase source grounding, so `SQL` cannot be accepted from `PostgreSQL` alone; refusals name the entry. Unsupported saved entries show **Needs attention**, and base résumés retain supported entries while persistently naming omissions without rewriting profile or document history. Source edits never silently repin entries. Unedited grouped imports validate each emitted statement, including across excluded contact lines; compatible queued base tasks remain usable when no omissions are required.
- Lifecycle commands report a missing managed installation with setup instructions instead of presenting the missing `.installer-state` as a file-type or symlink problem.
- Equivalent decimal port parameters reuse the saved installation rather than being rejected before normalization; explicit-directory lifecycle wrappers also work without `HOME`.
- Failed asset publication removes only its own temporary file before propagating the I/O error, so retry is not blocked by that partial copy; pre-planted files and links remain untouched.
- Installation proof receipts identify the served static SPA shell rather than claiming browser setup rendering, and the stop deadline accommodates sequential service grace periods.

## [0.1.0]

The first release line of the self-hosted, single-owner career workspace, in which every factual claim in a generated résumé or letter is backed by the owner's own evidence.

### Added

- Owner bootstrap, sessions, CSRF protection and sign-in throttling for one owner per installation.
- Evidence-linked profile with immutable revisions and restore; evidence records carry source type, origin and an append-only privacy classification, and confidential or employer-owned evidence is withheld from every model call.
- Structured claims (claim schema v1) with an English renderer, a claim editor and strict grounding: a claim is supported only when its evidence contains its rendered sentence.
- Preview-first PDF and plain-text résumé import in a sealed, network-free parser.
- Manual postings, a reviewed Greenhouse board reader behind the fixed-route relay, catalog search, dispositions and duplicate review, and deterministic requirement coverage.
- Application tracking with stages, history, notes, contacts and appointments, and a per-claim usage ledger across application documents and the base résumé.
- Claim-structured résumé and letter generation and section regeneration on a local model route or a deterministic stub route; every factual statement cites one claim and must be that claim's text or a phrase-bounded prefix of it, and unsupported statements block export.
- Local model route qualification with a hash-bound measured receipt (`bonafides models qualify`), a fabrication benchmark (`bonafides benchmark run`) and claim coverage and inflation reports (`bonafides claims`).
- Durable tasks with leases and cancellation, and single-action approvals bound to exact intent.
- Owner-data portability through the published `bonafides-workspace/1` archive contract, owner-data erasure, and encrypted, scheduled installation backups (manifest format 2 carries a verified workspace archive) with isolated restore.
- `bonafides version` prints the release version; `/health/live` and `/health/ready` report it.

### Changed

- **Breaking:** the 30-migration development chain is replaced by one baseline migration, proven schema-equal to the chain by `tools/schema_equivalence.py` against `migrations/reference/schema-030.sql`. From this release migrations are append-only.

### Removed

- **Breaking:** databases and encrypted backups created before the baseline are refused (`pre_baseline_database`, `pre_baseline_backup`) and never upgraded in place. Export a `bonafides-workspace/1` archive from the last pre-baseline build (commit `771776e`) and import it into a fresh install.
- **Breaking:** import of legacy `bonafides-portable` v1 and v2 archives (`legacy_portable_format`). Convert one by importing it into the last pre-baseline build (commit `771776e`) and exporting a `bonafides-workspace/1` archive.

[Unreleased]: https://github.com/Mathews-Tom/BonaFides/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Mathews-Tom/BonaFides/releases/tag/v0.1.0
