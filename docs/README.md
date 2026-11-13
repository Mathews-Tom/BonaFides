# BonaFides documentation

Choose the part that fits what you want to do.

## I want to use BonaFides

The [user guide](user-guide/README.md) explains how BonaFides works and how to use it, step by step. No technical background is assumed.

| Guide | Covers |
|---|---|
| [Getting started](user-guide/getting-started.md) | Installing, starting and creating your account. |
| [Build your profile](user-guide/profile.md) | Personal details, sources, optional local Git/GitHub capture and profile entries. |
| [Import an existing résumé](user-guide/resume-import.md) | Turning a résumé you already have into profile entries. |
| [Find and review jobs](user-guide/opportunities.md) | Adding postings, deciding what to pursue and reviewing requirements. |
| [Create résumés and cover letters](user-guide/documents.md) | Building, checking, approving and exporting documents. |
| [Track your applications](user-guide/applications.md) | Stages, notes, contacts, follow-ups and interviews. |
| [Activity and background tasks](user-guide/activity.md) | Following, cancelling and retrying background work. |
| [Account and settings](user-guide/account-and-settings.md) | Signing in, passwords, unsaved changes and lost connections. |
| [Your data and privacy](user-guide/your-data.md) | What's stored, what can leave your computer, exports and deletion. |
| [Optional AI tailoring](user-guide/ai-tailoring.md) | What the AI does, what it receives and how it's kept honest. |
| [Manage your installation](user-guide/manage-installation.md) | Updating, running a second copy and uninstalling. |
| [Troubleshooting](user-guide/troubleshooting.md) | Common messages and what to do. |

## I want to develop, test or operate BonaFides

| Document | Covers |
|---|---|
| [Developer guide](development/README.md) | Pinned toolchain, local development, quality gates, installer internals, release images, feature contracts, the AI model route, data portability, encrypted backup and recovery, and project history. |
| [Architecture](architecture/README.md) | Container roles and isolation boundaries, the sealed PDF renderer, network ingress and egress, images, and updating an existing installation. |
| [Contributing](../CONTRIBUTING.md) | Licensing authorization, provenance, dependency review and change quality. |
| [Security policy](../SECURITY.md) | Private vulnerability reporting, supported versions, what BonaFides protects against and what it doesn't. |
| [Changelog](../CHANGELOG.md) | Notable changes by release. |
| [Current workspace archive schema](schemas/bonafides-workspace-3.schema.json) | The JSON schema for new `bonafides-workspace/3` data exports; immutable [version 1](schemas/bonafides-workspace-1.schema.json) and [version 2](schemas/bonafides-workspace-2.schema.json) remain supported for imports. |

### Decisions

- [ADR-0001: Dual licensing](decisions/ADR-0001-dual-licensing.md)
- [ADR-0003: The BonaFides name](decisions/ADR-0003-bonafides-name.md)
- ADR-0002, the sealed renderer deployment, is recorded in the [architecture overview](architecture/README.md#adr-0002--sealed-renderer-deployment-revision-1).

### Compliance

Licensing, provenance and release-rights records. These govern what can be distributed; they aren't needed for everyday use or development.

- [Licensing policy](compliance/licensing-policy.md) and [contributor licensing](compliance/contributor-licensing.md)
- [Clean-room provenance policy](compliance/clean-room-provenance-policy.md) and [bootstrap commit attestations](compliance/bootstrap-commit-attestations.md)
- [Dependency license policy](compliance/dependency-license-policy.md) and [third-party attribution policy](compliance/third-party-attribution-policy.md)
- [Artifact inventory](compliance/artifact-inventory.md)
- [ASVS register](compliance/asvs-register.md)
- [Architecture proof evidence](compliance/architecture-proof-evidence.md)
- [Release compliance checklist](compliance/release-compliance-checklist.md)

## Keeping the documentation current

When a change alters what a user sees or does, update the matching user-guide page in the same change. Quote screen labels exactly as they appear in the interface. Contracts and measured evidence belong in the developer guide; the user guide links to them rather than repeating them.
