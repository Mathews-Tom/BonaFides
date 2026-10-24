# Security policy

## Current support boundary

This repository currently contains a proposed design, not a supported production application or a security-certified release. No application version is declared supported. Before any public implementation release, maintainers must publish an actual supported-version table, monitored private reporting channel, response owner, and incident process. Their absence blocks public release; this document does not invent an email address, reporting service, or response SLA.

## Reporting a concern now

Do not publish credentials, personal profiles, exploit chains against a live installation, restricted materials, or backup contents in an issue. If this repository's hosting page offers a private vulnerability-reporting feature, use that channel only after confirming it is enabled. Otherwise contact a maintainer through an existing verified private channel and ask for a secure transfer method; there is no guaranteed monitored security inbox at design stage. Public design concerns may be reported with a minimal synthetic description that cannot expose an installation. Do not upload suspected secrets to scanners or third-party services to validate them.

Include affected revision/component, deployment assumptions, a minimal synthetic reproduction, expected/observed behavior, impact, and whether any real data was accessed. Share sensitive details only after a private channel exists. Do not test third-party accounts, perform denial of service, incur provider costs, or send applications/mail to demonstrate a report. This policy is not authorization to access systems or a legal safe-harbor promise.

## Proposed implementation response contract

At M2 readiness, designate a primary and backup responder and aim to acknowledge privately within three working days, triage within seven, and agree a disclosure schedule with the reporter based on exploitability. These are proposed operating targets, not a presently staffed guarantee. Protect reporter contact data, restrict incident artifacts, and disclose affected/safe versions and mitigations without user data. Active exploitation can justify early advisories. Dependency findings require exact shipped image/package evidence, not merely a CVE name.

For an exposed deployment: restrict access, pause source/model/integration egress and worker claims, preserve redacted evidence, revoke sessions and provider credentials as appropriate, rotate keys using the documented procedure, and restore only from a verified known-good backup if integrity is doubtful. Do not delete the only evidence or silently reinstall over potentially compromised data. Reviewed incident, credential/key-rotation and recovery runbooks must accompany an implemented deployment; private planning documents are not a shipped operator interface.

## Scope of assurances

Owner authentication, SSRF containment, parser isolation, evidence-grounded generation, encrypted credentials, backup recovery, and supply-chain review are design requirements. None is asserted implemented or penetration-tested here. Root and container-host compromise remains an operator risk; encryption keys mounted in a compromised process do not protect against that process. Hosted inference is opt-in and creates provider-side retention/jurisdiction risks. Qualified legal and security review remains required before relevant release claims.
