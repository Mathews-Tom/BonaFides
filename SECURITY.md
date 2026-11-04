# Security policy

BonaFides holds some of the most personal information people have: their work history, contact details, job applications and private notes. This page explains how to report a security problem, which versions get fixes, what BonaFides protects against and what it doesn't, and how to keep your own installation safe.

## Reporting a vulnerability

**Report security problems privately, not in a public issue.**

Use GitHub's private reporting form: open the repository's **Security** tab and select **Report a vulnerability**, or go straight to <https://github.com/Mathews-Tom/BonaFides/security/advisories/new>. Only you and the repository's maintainers can see the report.

Please include:

- the commit or version you tested, and how it was installed (`install.sh`, the developer setup, or something else),
- which part is affected, for example sign-in, résumé import, PDF rendering, job-board reads, the AI model connection or backups,
- the steps to reproduce it, using made-up data,
- what you expected and what happened, and what an attacker could gain,
- whether any real person's data was involved.

Please don't:

- include real personal data, passwords, setup codes, keys, backups or anything from a `secrets` folder in your report;
- test against an installation that isn't yours, or against job boards, model services or other third-party systems;
- attempt denial of service, send applications or email, or run up costs on anyone's services to demonstrate a problem.

### What happens next

Maintainer time is limited, so reports are handled on a **best-effort basis, with no guaranteed response time**. Every report is read and dealt with as soon as practical. You'll get a reply through the private report, where we can discuss the problem, agree on a fix and choose a disclosure date together. Once a fix is available, the advisory is published with the affected versions and what to do. If you'd like to be credited, say so in your report.

This policy doesn't authorize access to any system and isn't a legal safe-harbor promise.

## Supported versions

There are no published releases yet. Security fixes go to the latest commit on the `main` branch only. To get them, [update your installation](docs/user-guide/manage-installation.md#update-to-a-newer-version).

## What BonaFides protects against

BonaFides is a single-person application that runs on your own computer and is reached only through `http://localhost`. Its defenses are built around that setup:

- **Access to your workspace.** There is exactly one account per installation, created with a one-time setup code that only someone with access to the computer can read. Passwords are 15 to 128 characters and stored as Argon2id hashes. Repeated failed sign-ins are throttled. Sessions use `__Host-` cookies with exact Origin and CSRF checks. Deleting or replacing data needs a recent password confirmation, a preview and a typed confirmation, and each approval covers only that exact action.
- **Reaching BonaFides from elsewhere.** The web interface is published only on `127.0.0.1`. The database is never published to the host.
- **Untrusted files.** Uploaded résumés are read, and PDFs are produced, in separate containers with no network, no database access, no keys and read-only file systems. Attachments are stored without being opened or previewed.
- **Unwanted outbound connections.** The main services have no general internet access. Job-board reads go through a separate relay that can make only the official Greenhouse job-list request, for boards the owner has reviewed and enabled. An AI model can be used only after the operator has configured and qualified it; public internet addresses are refused and there is no fallback to a hosted service. Sources marked confidential or employer-owned are never sent to the model.
- **Made-up or inflated document content.** Every factual statement in a generated document must match one of the owner's evidence-backed profile entries, or review and export are blocked. See the [known gap](docs/user-guide/ai-tailoring.md#how-bonafides-keeps-the-ai-honest).
- **Stored secrets and backups.** Internal credentials are generated per installation and kept in private files and volumes. Installation backups are encrypted with AES-256-GCM, with keys wrapped for RSA-OAEP-SHA256 recipients.

The [architecture overview](docs/architecture/README.md) describes these boundaries in detail.

## What BonaFides does not protect against

- **Anyone who controls your computer account or Docker.** They can read the `secrets` folder, the database and every container. Keys loaded into a running process don't protect against that process being compromised.
- **Network exposure.** BonaFides is not designed to be reached from other computers. Publishing its port on your network, or putting it behind a reverse proxy or tunnel, is unsupported and removes the protections above.
- **Files you take out of BonaFides.** Data exports are unencrypted ZIP files, and downloaded PDFs contain your personal details. Once saved elsewhere, they're your responsibility.
- **The AI model you choose.** A model on your own computer or private network sees everything in the preview you approve. Its operator controls what it keeps.
- **The truth of your profile.** BonaFides checks that documents match your entries; it doesn't verify that the entries are true.

**These protections have not been independently audited or penetration-tested.** The [ASVS register](docs/compliance/asvs-register.md) tracks requirements against the OWASP Application Security Verification Standard; its rows are still pending assessment, and it is not a certification.

## Keeping your installation safe

- Open BonaFides only at the `http://localhost` address that `start.sh` prints, and don't expose its port to other devices.
- Keep the installation's `secrets` folder private. Store a copy of `secrets/keyring` somewhere safe, away from the computer. See [Your data and privacy](docs/user-guide/your-data.md#the-secrets-folder).
- Never share the one-time setup code, your password or anything from `secrets`, including in screenshots and support requests.
- Use a long, unique password.
- Store data exports and PDFs somewhere private, and delete copies you no longer need.
- Keep BonaFides and Docker up to date.

## If you think your installation has been compromised

1. Stop it with `sh stop.sh`. Don't uninstall, purge or reinstall over it; that destroys the evidence and possibly your only copy of the data.
2. Deal with the cause on your computer first, for example malware or an account someone else has access to.
3. Start BonaFides again and set a new password with the [password recovery command](docs/user-guide/account-and-settings.md#forgotten-your-password). This signs out every session and cancels pending approvals. Then disable any job-board sources (**Disable … board**) and, if an AI model is connected, remove its route as described in the [developer guide](docs/development/README.md#optional-operator-controlled-local-model-route).
4. If you doubt the data is intact, restore from a backup you've verified, using [encrypted installation backup and recovery](docs/development/README.md#encrypted-installation-backup-and-recovery). Rotate keys with the documented [offline credential-key rotation](docs/development/README.md#offline-credential-key-rotation).
5. If the cause may be a flaw in BonaFides, [report it privately](#reporting-a-vulnerability).
