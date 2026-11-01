<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
# OWASP ASVS 5.0.0 applicability and evidence register

## Status and source

This is an **unverified implementation register**, not a certification, penetration test, completed security review, or assertion of ASVS Level 2 conformance. Row-level requirement and evidence states remain pending assessment; partial execution reported in [architecture proof evidence](architecture-proof-evidence.md) does not automatically promote a row to verified. References identify implementation and intended coverage, while observed outcomes must be recorded with their scope and limits. There is no reviewer signature or approved exception implied by this document.

The public register is [`compliance/asvs-5.0.0.csv`](../../compliance/asvs-5.0.0.csv), with source provenance in [`compliance/asvs-source.json`](../../compliance/asvs-source.json). It contains all **345 requirements**, including Level 3 requirements, from the official English CSV published with [OWASP ASVS 5.0.0, tag `v5.0.0_release`](https://github.com/OWASP/ASVS/releases/tag/v5.0.0_release), published 2025-05-30. It does not use the moving `latest` release.

- [Official released machine-readable CSV](https://github.com/OWASP/ASVS/releases/download/v5.0.0_release/OWASP_Application_Security_Verification_Standard_5.0.0_en.csv)
- Tag commit: `5cf9b032440be53ce345ab3c130fda46ba1ce7a2`
- Retrieved: **2026-10-24**
- Exact downloaded source size: **105446 bytes**
- Exact downloaded source SHA-256: `6124dba176dc563f66363a11ae0c47f9b86b8a4a84c66a793670bd196ed86cd5`

The hash applies to downloaded upstream bytes, not the augmented register. Source columns `chapter_id`, `chapter_name`, `section_id`, `section_name`, `req_id`, `req_description`, and `L` are retained. The source has no individual requirement-title field: official chapter/section names and exact requirement text supply the title/context without invented titles. Version-bound identifiers follow OWASP notation, for example source `V1.1.1` becomes `v5.0.0-1.1.1`; the original ID is retained alongside it.

## Scope and decision semantics

BonaFides architecture proof is a proof of architecture for one owner of a self-hosted installation. It must establish real local-password identity, opaque revocable sessions, exact-origin browser protections, owner-scoped SQL and durable workflows, private typed PDF generation, configuration validation and disposable recovery of a known, matched, nonempty fixture database and blob set. Encrypted product-backup functionality belongs to private alpha, is not implemented, and is not required for the architecture proof. Disposable fixture recovery is not production backup qualification or a waiver of release obligations. Private content and every browser-supplied identifier remain untrusted. A host administrator can access process memory; encryption does not erase that limitation.

The intended verification profile is risk-based ASVS 5.0.0 Level 2 for applicable web controls, with stronger parser/render, secret and recovery boundaries. Inclusion of Level 3 rows preserves the complete standard; it is not a claim that all Level 3 mechanisms exist. Requirements that exceed current implementation remain explicit gaps rather than being silently excluded.

Every row names a **responsible role**, not a fictitious assignee or reviewer. A role is accountable for collecting proof and resolving gaps; it does not establish review approval. The row-level reason is specific to the relevant requirement or feature boundary.

- `applicable`: assess in architecture proof. Missing implementation is an unresolved gap, never an exclusion or pass.
- `deferred`: disabled feature whose controls must be verified before its named milestone enables it. Private alpha reviewed PDF/plaintext import, metadata handling and future format-specific exports are distinct from account integrations provider OAuth delegation. DOCX/archive expansion and image/SVG acceptance remain separately qualified future work.
- `outofscope`: the condition/technology is absent from the declared product boundary. The parent must confirm that absence; enabling it requires reclassification. Examples include LDAP/JNDI, GraphQL, WebSockets, WebRTC, self-contained JWT sessions, OAuth authorization/resource-server roles and multi-tenant operation. These are not security exceptions for an enabled feature.

No external source/model provider, arbitrary fetcher, automatic email/send capability or OAuth client is enabled by this register. Private alpha provider/source activation requires its own consent, legal review, egress, SSRF and budgeting proof; architecture proof renderer URL/file denial and configuration egress restrictions remain applicable now. Account integrations OAuth client controls are deferred, while operating an OAuth authorization server or OpenID Provider is outside the product boundary. Local password login is not federated login.

MFA is not claimed. The authentication-strength requirement remains applicable and unresolved; a proposed VPN/access gateway or discouraged public exposure does not itself satisfy or approve relaxation of it. Likewise, a bounded common-password denylist does not prove the ASVS policy-matching top-3000 or breached-password requirements. Full-memory encryption, isolated cryptographic-module execution, HSTS preload and internal TLS requirements are retained as pending assessments rather than silently equated with mounted keys, private networking or loopback transport.

## Architecture-proof acceptance that must be observed

The identity acceptance boundary requires:

1. Concurrent bootstrap requests create exactly one owner, and the short-lived one-use setup secret cannot be replayed.
2. Login throttles persist; password handling, session rotation, expiry, logout and password-change revocation have negative tests.
3. Missing CSRF and wrong/missing/null Origin reject mutations without side effects; cookie/TLS/proxy assumptions are inspected in the actual deployment.
4. Local operator recovery invalidates old sessions and cannot be reached through a public reset bypass.
5. Unauthenticated profile, blob and task access is denied; an actual browser signs in and signs out and demonstrates denied access after revocation.
6. Authentication logs contain no password, bootstrap token, cookie or private content. Internet exposure stays disabled until the boundary review.

The implementation's declared bootstrap policy uses a 32-byte CSPRNG token with only a verifier stored and a 15-minute expiry. Its declared password policy accepts 15–128 characters without silent normalization/truncation, uses Argon2id, and requires offline reviewed password screening. Its declared session policy uses 32-byte opaque random tokens, stored verifiers, server-authoritative 12-hour idle/seven-day absolute expiry and `__Host-bonafides_session; Secure; HttpOnly; SameSite=Lax; Path=/` without Domain. Recent authentication is required for sensitive recovery/credential operations. These policies still require artifact-bound verification; selected runtime observations do not establish complete control conformance.

Browser proof includes CSP without unsafe inline/eval exceptions, exact Host/Origin and trusted-proxy handling, no credentialed foreign-origin CORS, JSON content types, attachment disposition and sanitized names, `nosniff`, no-referrer and no-store private responses. HSTS requires a qualified TLS domain and cannot be inferred from a loopback-only development run.

SQL/workflow proof includes parameterized statements, server-derived owner scope/composite relations, atomic bootstrap, durable idempotency, claim/lease/fencing, crash/reclaim, cancellation and stale-commit rejection. Renderer proof must include escaped typed text, no arbitrary templates, denied URL/file fetches and real Linux process/network/filesystem/resource isolation. Architecture proof restore proof must recover a known, matched, nonempty fixture database/blob set and verify the recovered records and private blobs together. Private alpha encrypted product-backup verification must separately exercise encryption/key custody, manifest integrity, quiescence, corruption refusal and independent recovery checks; merely serializing a bundle is insufficient.

The integration owner observed native arm64 application, browser, worker-fencing, sealed-renderer and matched disposable-recovery acceptance before the runtime migration. The Alpine remediation separately passed the unchanged pinned HIGH/CRITICAL image vulnerability/secret gate for both built architectures; a deliberately vulnerable synthetic APK control demonstrated active Alpine advisory lookup despite the scanner's lifecycle-table warning. [architecture proof evidence](architecture-proof-evidence.md) is the authoritative checkpoint for the current exact-artifact runtime receipts, commands and remaining qualification limits; historical receipts do not qualify replacement images. Native amd64 execution remains unavailable, containment limits have not been weakened, and independent security/contribution/rights/legal decisions remain unresolved. Neither a passing scan nor a runtime receipt verifies every ASVS control, constitutes independent review or promotes any row.

## Evidence references and promotion

Rows point to symbols communicated by the implementation owners, including:

- Identity/configuration: `src/bonafides/auth.py` (`check_password`, `hash_password`, `verify_password`, `require_owner`, `init_bootstrap`, `recover_password`, `build_router`); `src/bonafides/config.py` (`Settings.from_env`, `read_secret`); `tests/test_identity.py` and `tests/test_config.py`.
- Cryptography: `src/bonafides/crypto.py` (`Keyring`, `EnvelopeContext`, `encrypt`, `decrypt`).
- Documents: `src/bonafides/documents.py` (`DocumentBody`, `prepare_render`); `src/bonafides/statement_check.py` (`check_statements`); `src/bonafides/storage.py` (`BlobStore`, `atomic_file`, `directory`); `src/bonafides/render_service.py` (`deny_url_fetcher`, `render_html`, `render_pdf`, `RenderClient`, `_supervise`, `isolation_canary`); `tests/test_documents.py`.
- Persistence/workflows: `src/bonafides/commands.py::require_if_match`; `src/bonafides/workflows.py` (`claim`, `heartbeat`, `assert_fence`, `publish`, `reap`, `cancel`); `migrations/sql/001_initial.up.sql::assert_workflow_fence`; `tests/test_database.py` and `tests/test_workflows.py`.
- Integrated deployment, containment and recovery: `deploy`, `Dockerfile` and `tools/runtime_proof.py`; actual browser and target TLS observations must be recorded separately.

An intended test file can cover only part of a requirement; its presence is not complete evidence. Before promotion, the integration owner and responsible reviewer must bind each claim to the observed code revision, environment, exact commands/cases and outputs, including failure-path and browser/OS-level observations where required. Missing or failing proof stays unverified with the gap recorded. Exclusions require explicit scope review; a pass in an unrelated unit test cannot approve one. This documentation update did not itself run test, build, lint or formatting commands; outcomes above are attributed to the integration owner's actual observations, not independent reruns or reviewer approval.

The register and this document are self-contained public compliance artifacts. Application startup, CI and evidence evaluation must not depend on private design materials.

## Attribution and license

Requirement text and source structure are reproduced from **OWASP Foundation and OWASP ASVS contributors, Application Security Verification Standard 5.0.0**, licensed under [Creative Commons Attribution-ShareAlike 4.0 International](https://creativecommons.org/licenses/by-sa/4.0/); see the [official tagged license](https://github.com/OWASP/ASVS/blob/v5.0.0_release/LICENSE.md). The CSV adds BonaFides-specific scope, role and pending-evidence columns; the original requirements are not rewritten. This register adaptation and accompanying register documentation are distributed under **CC-BY-SA-4.0**, independently of application-code licensing. No OWASP endorsement is implied.
