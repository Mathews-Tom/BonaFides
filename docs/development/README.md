# Development documentation

The repository currently provides public project, contribution, security and licensing documentation. Application source, package manifests, dependency locks and CI are not implemented; no install, test, build or startup command is claimed operational.

## Implementation baseline

The provisional layout is a uv-managed Python backend under `src/offerloom` and a pnpm-managed React/TypeScript/Vite frontend under `web`, with PostgreSQL and Docker Compose. Confirm the approved scope and [architecture](../architecture/README.md) before introducing application code.

Foundation work must preserve the root [ignore rules](../../.gitignore), add real manifests and locked dependencies, establish lint/type/test/build gates, implement validated configuration and a reviewed migration path, and prove least-privilege API/worker/database startup and failure recovery. Commit lockfiles, migrations and synthetic fixtures; exclude secrets, dependency/build caches, local data and backups. Environment examples must contain placeholders only.

Once the application and its tooling exist, configure backend Ruff, strict mypy and pytest gates; frontend TypeScript, ESLint, Vitest and production-build gates; and PostgreSQL/browser/recovery integration checks. These are required future gates, not checks that the current public documentation repository can run.

## Current documentation checks

Hosted CI is deferred until application scaffolding. The current documentation-only baseline instead requires clean-checkout validation of public local links, Git ignore/keep rules and an outgoing-commit secret scan before merging. Ignored local specifications must not be required for those checks. Passing these documentation checks does not qualify an application build, runtime, security boundary or release.

## Private planning boundary

Detailed design documents, coding-agent handoffs, prototypes and benchmark evidence are maintained locally under `.docs/design/`. The project `.gitignore` excludes the entire `.docs/` tree, so these files are not supplied by a repository clone. Public documentation must remain self-contained and must not link to ignored files. Move reviewed contracts, migrations and fixtures into tracked application directories before application code or CI depends on them; do not force-add private planning artifacts.

All contributions follow [CONTRIBUTING.md](../../CONTRIBUTING.md), the [security policy](../../SECURITY.md) and [release compliance requirements](../compliance/release-compliance-checklist.md). Public documentation does not establish application readiness or clear third-party/native/font/model distribution rights.
