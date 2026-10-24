# Offerloom

**Your job search. Your data. Your AI.**

A self-hosted, AI-powered job search and application management platform that maintains one professional profile, discovers relevant opportunities, generates evidence-backed application materials, and tracks applications from discovery to offer.

## Current state

Architecture and implementation-design stage; the application is not implemented. Start with the public [architecture overview](docs/architecture/README.md), [development guide](docs/development/README.md) and [security policy](SECURITY.md). These describe planned boundaries, not deployed features or completed production qualification.

Detailed design, coding handoffs, prototypes and experimental evidence are local planning material under the gitignored `.docs/design/` directory; they are not included in a clone of this repository. The provisional stack is Python/FastAPI, React/TypeScript/Vite and PostgreSQL, managed with uv and pnpm. Model egress is opt-in and employer-facing automation is not in MVP.

## Licensing

Project licensing intent: **MIT OR Apache-2.0**, recipient's choice. Read [LICENSE-MIT](LICENSE-MIT), [LICENSE-APACHE](LICENSE-APACHE), the [dual-license decision](docs/decisions/ADR-0001-dual-licensing.md), [contribution policy](CONTRIBUTING.md) and [compliance checklist](docs/compliance/release-compliance-checklist.md). This intent is not legal certification: provenance, contribution authority and exact third-party/native/font/model obligations require review before public distribution.
