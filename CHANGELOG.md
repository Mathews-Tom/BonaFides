# Changelog

All notable changes to BonaFides are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

- **Breaking:** databases and encrypted backups created before the baseline are refused (`pre_baseline_database`, `pre_baseline_backup`) and never upgraded in place. Export a `bonafides-workspace/1` archive from the previous release and import it into a fresh install.
- **Breaking:** import of legacy `bonafides-portable` v1 and v2 archives (`legacy_portable_format`). Convert one by importing it into the previous release and exporting a `bonafides-workspace/1` archive.

[Unreleased]: https://github.com/Mathews-Tom/BonaFides/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Mathews-Tom/BonaFides/releases/tag/v0.1.0
