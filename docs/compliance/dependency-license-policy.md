# Dependency license policy

## Inventory before incorporation

Every framework, SDK, direct/transitive package, native library, container/base-image OS package, build tool, bundled font/icon/image/template, model weight, tokenizer, evaluation dataset and remote service needs a record. Minimum fields: component and package URL, exact version and source/artifact digest, official upstream and license/terms URL, detected and concluded SPDX expression (or `LicenseRef` plus text), expression branch selected if applicable, copyright/notices, use and distribution scope, dependency edges, modifications, source/relink obligations, decision owner/date/status, and evidence location. A remote service is subject to terms/privacy review even if no software is redistributed.

Resolve backend, frontend and platform-specific graphs from locked clean installs; inspect actual wheels/bundles/images, not just top-level metadata. Native libraries bundled inside wheels can differ from OS-linked versions. Review development dependencies and whether their code/assets enter runtime output. Re-run when version, extras, platform, packaging, model or terms change. No complete graph exists in this design session.

## Decision classes

| Class | Treatment |
| --- | --- |
| MIT, Apache-2.0, BSD variants, ISC, PostgreSQL | Candidate routine review: verify exact text, copyright, notices, modifications and patent conditions; not blanket automatic approval |
| LGPL, MPL or other weak copyleft | Conditional legal review of combined work, file boundaries, source availability, modifications, replacement/relink/debugging rights and distribution form; obligations must be demonstrably satisfiable |
| GPL, AGPL, strong/network copyleft combined with project work | Do not incorporate into distributable product by default; require qualified analysis or select an alternative; separate process is not an automatic exemption |
| OFL/fonts and other asset terms | Review exact font/asset, reserved names, bundling/subsetting/embedding and required notices separately from document output rights |
| Model/dataset/custom, noncommercial, research-only or field-of-use terms | Separate rights/privacy/acceptable-use review; not presumed open source or compatible; no weights bundled by default |
| Missing/ambiguous/disputed/no license | Quarantine; block incorporation and release until resolved or replace |

A component with `A OR B` permits an appropriate branch choice; `A AND B` requires both. Do not infer a branch from the shortest scanner label. Detection confidence is not legal permission. A dependency under its own terms need not and generally cannot be relabeled as original `MIT OR Apache-2.0`.

## Future CI and evidence

At architecture proof define locked dependencies, license declaration/header checks and secret scanning. Generate SPDX or CycloneDX SBOMs for source dependencies and built images; scan licenses with an evidence-preserving tool such as ScanCode and reconcile with lockfiles/OS inventory. Scan vulnerabilities separately (e.g. OSV/pip-audit and image Trivy) and retain scanner versions/database timestamps, exact artifact digests, reports, notices and human dispositions. Unknown coverage or scanner crash fails closed. High/critical exploitable findings block release unless a documented time-limited security disposition justifies non-applicability; licensing uncertainty requires rights resolution, not risk acceptance alone.

Psycopg's LGPLv3, WeasyPrint's native Pango libraries, Pyphen's alternative licenses and bundled dictionaries require specific review. Prefer system-linked replaceable libraries with complete source/license availability over opaque static bundles when practical, but do not claim this alone satisfies LGPL. A qualified fallback client/renderer may be selected only after contract, security and performance qualification. Record exact component reviews and unresolved obligations through the [release compliance checklist](release-compliance-checklist.md); private design notes do not confer distribution clearance.

Sources reviewed 2026-10-24: [SPDX expressions](https://spdx.github.io/spdx-spec/v2.3/SPDX-license-expressions/), [Psycopg license](https://raw.githubusercontent.com/psycopg/psycopg/master/LICENSE.txt), [WeasyPrint dependencies](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html), [Pyphen terms](https://pyphen.org/).

## pypdf security update — 2026-10-31

The runtime dependency is pinned to **pypdf 6.21.3** in `pyproject.toml` and
`uv.lock`, replacing 6.21.2 to address CVE-2026-119206, CVE-2026-119207,
CVE-2026-119208 and CVE-2026-119209. Package identity:
`pkg:pypi/pypdf@6.21.3`; [official upstream](https://github.com/py-pdf/pypdf/tree/6.21.3),
[license text](https://github.com/py-pdf/pypdf/blob/6.21.3/LICENSE).
The upstream declaration is BSD-3-Clause, not an application dual-license grant.
The locked wheel SHA-256 is
`c8ca4d9a360dbd5d4b9bb7a8389e2a9ae6a106bee722b496f918040ba2daa17e`;
the source archive SHA-256 is
`11e1ca6d6cc8ca322558a3a974078d5ebc0f3a2104b8e673eee25621ee97abd8`.
This update adds no dependency edges or local upstream modifications.
Source/binary redistribution must retain the BSD copyright, conditions and
disclaimer; exact-artifact notice reconciliation and concluded license review
remain pending with the dependency reviewer, not approved by this security update.

The [6.21.3 changelog](https://github.com/py-pdf/pypdf/blob/6.21.3/CHANGELOG.md)
adds validated decrypted page counts, cross-reference stream entry limits and
bounded error reporting. Import rejects encrypted PDFs and already maps
`LimitReachedError` to its content-limit failure. The CID font indirect-width
fix affects layout metrics used by `import_parser.py`; import and render PDF
regressions cover that integration. Page-label, named-destination and outline
performance changes do not change the import API. No parser adaptation is needed.

The committed `compliance/locked-components.json` remains the historical
exact-image collection, not a current 6.21.3 inventory. CI generates a fresh
inventory under `artifacts/`; the historical image and its records are not
rewritten by this source-only dependency update.
