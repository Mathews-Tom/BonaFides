# ADR-0003: Name the project BonaFides and use functional names

- Status: Accepted
- Date: 2026-10-30
- Decision owner: BonaFides maintainers; this record makes no trademark, rights or legal-clearance claim.
- Scope: product name, Python package, CLI, environment variables, database, schemas, roles, images, Compose project, CI names, web title and package, public documentation, and the vocabulary used for features, stages and migrations.
- Supersedes: none. ADR-0001's licensing decision is unchanged; only its product name was updated.

## Context

The repository was first published under the working name Offerloom. The project now ships as BonaFides: a self-hosted career workspace whose generated documents are grounded in evidence the owner has reviewed. The working name was spread across the package (`src/offerloom`), the `offerloom` CLI, `OFFERLOOM_*` environment variables, the `offerloom` database and schema, the `offerloom_migrator` and `offerloom_runtime` roles, stage-suffixed `offerloom` images, the default Compose project, CI names and every document.

Code, tests, CI and documentation also carried internal planning codes: milestone codes (the letter M and a stage number), story codes (a project prefix and a three-digit number), a migration revision named after a milestone, a runtime proof module named after a milestone, and a persisted origin label on saved-evidence assembly revisions. These codes mean nothing to an operator or contributor without the private planning material, and they go stale when plans change.

No deployed database or external user depends on the old names. The owner's private runtime under the old name is left untouched and does not need to stay compatible.

## Decision

Use **BonaFides** as the product name and `bonafides` as the technical identifier everywhere:

| Surface | Previous | Now |
| --- | --- | --- |
| Python package and CLI | `offerloom` | `bonafides` (`python -m bonafides`, `bonafides` console script) |
| Environment variables | `OFFERLOOM_*` | `BONAFIDES_*` |
| Database and schema | `offerloom` | `bonafides` (`bonafides_migrations` for the migration ledger) |
| Database roles | `offerloom_migrator`, `offerloom_runtime` | `bonafides_migrator`, `bonafides_runtime` |
| Images | stage-suffixed `offerloom` images | `bonafides:*` |
| Default Compose project | `offerloom` | `bonafides` |
| Disposable proof projects | `offerloom-proof-*` | `bonafides-proof-*` |
| Runtime proof | milestone-named probe module | `tools/runtime_proof.py` |
| Web title and package | `Offerloom`, `offerloom-web` | `BonaFides`, `bonafides-web` |

Existing migrations are edited in place because no deployed database exists. Persisted identifiers that carried the old name, such as built-in template versions, the theme storage key and the owner-data download name, use the new name with their existing version suffixes.

Describe features, stages and migrations by what they do. Story codes become feature names (for example "evidence-linked profiles" or "encrypted backup and recovery"), milestone codes become stage names ("architecture proof", "private alpha", "public beta", "stable distribution"), and contributions reference a functional feature and design contract instead of a story ID.

## Consequences

- An install from a clean checkout creates the `bonafides` database, roles, images and Compose project. Installations created under the old names are not upgraded; they keep running on their own images and are not migrated.
- Historical evidence in `docs/` was rewritten with the new names. Image tags, Compose projects and `.local/` receipt paths recorded there had the `offerloom` prefix when they were observed; the receipts themselves were not renamed.
- The bootstrap commit attestations keep their original wording because they certify exact historical commits.
- Naming checks in review look for the previous name and for planning-code patterns. This record and the bootstrap attestations are the only expected matches for the previous name.

## Alternatives considered

| Alternative | Why not selected |
| --- | --- |
| Keep the working name internally and rename only the user-facing title | Leaves two names in operator commands, logs and configuration and makes support harder |
| Add compatibility aliases for `OFFERLOOM_*` variables and the old roles | No deployed installation needs them; aliases would keep the old name in code indefinitely |
| Add a new migration that renames the schema and roles | Only needed for existing databases, and none are deployed |
