# Changelog

All notable changes to BonaFides are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `bonafides demo load` loads synthetic profile, evidence and claims into an existing, empty owner profile; reruns are no-ops and any real or changed state is refused without writes.
- `tools/core_loop_proof.py` proves posting, checked résumé and letter, exact-revision review, PDF export and saved tracking on the release image in production mode with the explicit fixture model; unsupported and ownership-inflated output blocks review/export. The full tier runs it on both architectures. `tools/demo_capture.py` rebuilds the README workflow recording.
- `deploy/install.sh` installs the stack from a locally loaded application image with one command and no source checkout, generates private credentials without printing the bootstrap token, and safely reruns after a same-version image swap. `bonafides init-bootstrap-if-ownerless` renews the bootstrap verifier only while no owner exists.
- `tools/gate_controls.py` proves clean-to-blocking transitions for source secrets, license headers, NOTICE, unapproved rights and pinned Trivy secrets with synthetic input and redacted findings; the full tier runs it on both architectures.
- `tools/release_candidate.py` binds a candidate to version, source revision, metadata-only index, native platform digests and exact gate evidence, validates scanner, inventory and application receipt image subjects, and signs/verifies with pinned cosign 3.1.4 and an explicitly trusted public key. `tools/candidate_proof.py` proves signing and verification with a disposable non-production key and rejection of altered bytes, a wrong key, and missing, altered, mismatched or changed-source inputs; tool failures cannot count as successful negative controls. Passing automation never authorizes a release; production signing identity and human approvals remain pending.

### Changed

- The README opens with the workflow, the committed workflow recording, the install and demo commands, and the measured results with their limits.
- Demo loading honors installation maintenance and workspace mutation locks, and advances the workspace revision only when it seeds data so stale destructive approvals cannot authorize the new data.
- Proof teardown removes a swap-image tag only when that run successfully built it, preserving unrelated tags during core-loop and recording cleanup.
- Image inventories bind classic Docker config IDs or containerd platform manifests through the unstarted, network-free inventory container, without requiring the newer `docker image inspect --platform` CLI flag.
- Credential generation moved from `tools/dev_setup.py` to the image-shipped `python -m bonafides.local_setup`; the development commands use the new module path.

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
