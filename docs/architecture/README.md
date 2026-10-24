# Architecture documentation

Offerloom is at the architecture/design stage. The application, production deployment and runtime security controls are not implemented. Detailed specifications, prototypes, contracts and experiment evidence are local, gitignored planning material under `.docs/design/`, not public repository contents or dependencies of this overview.

## Provisional architecture

- Python 3.12 API and workers using FastAPI/Pydantic, with SQLAlchemy/Alembic and psycopg planned for persistence.
- React/TypeScript/Vite browser UI, using pnpm; Python dependencies use uv.
- PostgreSQL for normalized records, full-text search, durable work leases and the transactional outbox. No Redis, graph/vector database or microservice topology is selected.
- Private content-addressed filesystem storage for user documents; separate database, blob and encryption-key persistence boundaries.
- Isolated typed PDF rendering, one secured owner, opt-in model egress and deliberate document review/export. Autonomous submissions and external email/calendar writes are outside MVP.
- Docker Compose deployment with separate API, worker and database services; supported-runtime, least-privilege, recovery and resource qualification remain implementation gates.

These are provisional design choices, not measured production guarantees. Canonical licensing governance is [ADR-0001](../decisions/ADR-0001-dual-licensing.md), distribution obligations are covered by the [compliance checklist](../compliance/release-compliance-checklist.md), and implementation entry points are in the [development guide](../development/README.md).
