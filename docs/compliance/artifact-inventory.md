<!-- SPDX-License-Identifier: MIT OR Apache-2.0 -->
# Artifact inventory collection

`tools/inventory.py` creates **observed artifact evidence**, not a license approval
or permission to distribute. It does not replace `compliance/components.json` or
`compliance/provenance.json`: those remain the provisional component-family and
original-work provenance records. Never describe the provisional families as an
exact shipped graph.

The collector uses Python 3.12 standard-library APIs, the installed `pnpm` JSON
interface, and Docker's existing local image/export APIs. No YAML package, scanner
service, network fetch, image pull, package install, or container startup is
performed. Docker export reads image files without executing image programs.

## Collect actual inputs

First install the frozen Python and frontend dependency graphs, build the frontend
bundle, and build the application image using the repository's development/build
instructions. Collection requires all of these actual artifacts; a lockfile alone
is insufficient. From the repository root:

```sh
uv run --frozen python -m tools.inventory \
  --uv-lock uv.lock \
  --python-root .venv \
  --web-root web \
  --image bonafides:local \
  --output compliance/locked-components.json

uv run --frozen python -m tools.compliance sbom \
  --inventory compliance/locked-components.json \
  --output compliance/artifact-sbom.cdx.json

uv run --frozen python -m tools.compliance check \
  --inventory compliance/locked-components.json \
  --output compliance/artifact-review.json
```

A deliberately narrower source/build-workspace collection must explicitly omit
image coverage:

```sh
uv run --frozen python -m tools.inventory \
  --uv-lock uv.lock --python-root .venv --web-root web \
  --source-only --output compliance/locked-components.json
```

`--source-only` still requires installed dependencies and `web/dist`; it means
**image excluded**, not "locks only". It cannot be combined with `--image`.
The coverage label and `source_only` field preserve this distinction in the output.
The generated inventory is not committed by the collector. Regenerate it against
the exact target environment and image before making distribution decisions.

### Exit behavior

- **0:** the requested collectors completed and the graph has no dangling edges.
  This says nothing about release eligibility: the report remains blocked.
- **2:** absent inputs, malformed/unsupported locks, incomplete installed graphs,
  unavailable Docker/pnpm, invalid digests, unsupported image package databases,
  or other collection errors. The report includes partial observations and named
  `collection_errors`; do not publish such a report as complete.
- Argument errors also exit **2**. Existing reports are overwritten only after
  collection; an argument error does not produce a new snapshot.

An absent `.venv/pyvenv.cfg`, site-packages, `web/node_modules/.pnpm`, built bundle,
lockfile, ASVS attribution record, or local image is a named actionable error,
never an empty successful inventory. Review `collection_errors` before consuming
a report produced by another process. A nonempty report is not evidence that its
collection command succeeded.

`tools/compliance.py check` is expected to return **1** for freshly collected
metadata. CI may validate collection and create a machine SBOM without pretending
that a rights review has passed. SBOM generation alone is not a release gate.

### Observed snapshot and freshness

The current remediation inventory is regenerated from the frozen host Python environment, installed frontend graph and built bundle, declared build/service/scanner pins, isolated ASVS documentation adaptation and actual final arm64 application rootfs. [M0 evidence](M0-evidence.md#exact-local-commands-and-artifact-identities) is the single public checkpoint for the observed image, inventory digest, counts and gate outcomes. Do not reuse a previous run's counts, backend-closure assumptions or image identity after a runtime change.

`compliance/locked-components.json` itself supplies the machine-readable component categories, inspected input hashes, collection errors, coverage blockers, platform-candidate distinctions and Docker descriptor. Collection exit zero means the requested collectors completed without dangling graph references; it does not clear any rights or release obligation. All unresolved source, notices, license-branch, native replacement/relink, font/dictionary and contribution-authority decisions remain explicit review blockers.

Docker's reported OCI index identity and the exported rootfs tar SHA256 identify different bytes. A temporary export's file URI is a collection locator, not a durable download URL; repeated exports can differ and are not claimed reproducible archives. Preserve a particular export separately when replay requires its exact bytes. Reconcile the final saved-image index/manifest/config and scanner ImageID/DiffIDs separately, rather than calling an OCI index a config digest.


## What the graph records

### Python

- The complete `uv.lock` package graph, including optional/development dependency
  groups and marker-qualified edge declarations, is retained. This is an
  all-platform lock graph, not an assertion every candidate is installed/shipped.
- Every locked wheel/sdist has its own component and exact SHA256 declaration.
  `digest_basis: lock-declared-not-downloaded` and `sha256_verified: false` prevent
  confusing a lock assertion with a downloaded/verified archive.
- Installed `.venv` distribution metadata contributes declared `License-Expression`,
  legacy license text/classifiers, project URLs and `Requires-Dist`. Installed
  `RECORD` file entries are hashed from actual bytes; retained SHA256 RECORD hashes
  are checked. Missing/unowned/external listed files produce blockers.
- Package SHA256 values for installed packages identify a **canonical JSON file
  manifest**, not a selected wheel. File entries contain their own byte SHA256 and
  size, and manifests include relative paths and symlink targets where applicable.
- License/notice files include their actual decoded text, byte SHA256 and file URI.
  Fonts, dictionary data and native binaries each get file-byte components and
  owner metadata evidence. Owner licenses are **not** inherited as asset rights.
- The application's `[build-system].requires` is read separately from `uv.lock`.
  Exactly pinned PEP517 requirements missing from that lock get explicit records
  with unknown artifact digests and a separate-build-lock blocker. Dynamic backend
  requirements, each source archive's own build backend, transitive build tools
  and compiler/source provenance are explicitly unresolved. A runtime lock or a
  cached backend never silently certifies the source-build toolchain.

### Frontend

- A deliberately strict reader accepts canonical pnpm lockfile version 9.0
  registry-integrity declarations. Unsupported YAML escaping, duplicate keys,
  nonregistry resolution shapes or missing SRI fail closed rather than guessing.
  It is not a general YAML parser.
- Every registry candidate is recorded, including platform candidates not observed
  installed. Dependency edges for installed instances come from
  `pnpm list --json --depth Infinity`; versions are reconciled against actual
  package.json files and registry lock entries. Missing declared nonoptional
  installed dependency edges fail collection. Peer-context instances remain
  distinct through their installed path identity.
- pnpm may emit a `deduped` reference before its expanded occurrence. The collector
  indexes expanded occurrences by installed path before walking dependency edges;
  an unresolvable deduplicated reference still fails collection.
- pnpm may also emit uninstalled platform optionals under ordinary `dependencies`.
  Only an explicitly optional declaration in the owning package manifest permits
  a genuinely absent package directory. Its lock candidate retains
  `absent_optional_graph_paths` and a review blocker; no installed file manifest is
  fabricated. Missing required packages or existing package directories without
  `package.json` still fail collection.
- SRI SHA256/SHA384/SHA512 is decoded to algorithm-labelled hexadecimal, with exact
  decoded length checks. SHA512 is never mislabeled as SHA256. Registry SRI remains
  **unverified against tarball bytes**; installed files and package/license metadata
  supply separate actual SHA256 evidence.
- `web/dist` is a separate hashed built-bundle component, not conflated with the
  development dependency tree. A tree-shaken module/asset attribution map, source
  map verification and final notice reconciliation still require review. Bundled
  bytes alone cannot prove which development dependencies reached the bundle.
- Only a single workspace with registry packages inside its `node_modules` is
  supported. External workspace links and unsupported lock shapes require a
  dedicated extension, not a fallback to an incomplete graph.

### Actual image

- Docker image inspection binds the input reference to the Docker-reported image
  ID, preserving its descriptor when available. Docker's containerd image store
  can report an OCI index digest as `Id`; the collector records `docker_image_id`
  without relabeling it as a config digest. Collection creates an unstarted,
  network-disabled container, exports the rootfs, and removes the temporary
  container. No image commands are executed.
- The shipped Alpine image uses a dedicated strict legacy `/lib/apk/db/installed` collector. Package name/version/architecture, declared license, origin/commit, archive/file checksums and raw database records remain evidence, not verified archive signatures or concluded SPDX. Repeated `F` directory/`R` basename records establish ownership; actual regular-file bytes receive SHA256. Virtual metadata-only packages such as `.python-rundeps` remain visible.
- APK dependency records preserve direct names, `so:`/`cmd:` virtual providers, executable-path ownership, exact-version candidates and negative conflicts. Conflicts never become positive dependency edges. Missing or ambiguous providers, unavailable paths, version mismatches and unchecked non-equality version constraints remain explicit blockers; the collector does not approximate APK version ordering with Python packaging rules.
- The existing Debian collector separately reads `/var/lib/dpkg/status` and `/var/lib/dpkg/info/*.list`, retaining installed package identities, dependency candidates, ownership and actual file-byte hashes. Missing ownership lists or unresolved providers remain blockers. Both formats share native/font/dictionary/notice byte collection; copyright and license declarations are not automatically normalized legal conclusions.
- Missing or unsupported/binary package databases, ambiguous mixed databases, duplicate package/file ownership declarations and unsafe metadata/archive paths fail collection instead of producing an empty green inventory. Rootfs links remain links; unowned native content and absent declared file bytes stay explicitly review-blocked. Fonts, dictionaries and native binaries receive their own byte-hashed components independently of owner-license assumptions.
- The exported tar's SHA256 is distinct from the Docker-reported image identity
  and any independently established config or registry manifest digest.
  Temporary exports are deleted; their file URIs are collection
  locators, not durable download URLs. The observed digest and image identity are
  retained in the JSON; persist the exact export separately if audit retention
  requires archive replay. Docker cleanup failures are nonzero collection errors.
- Rootfs symlink/hardlink declarations are retained without following/extracting
  them. Unknown package ownership and image language/native components are not
  magically reconciled to host development packages. Static native embedded code,
  language graph/platform differences, source/relink requirements, and font or
  dictionary redistribution permissions remain review blockers.

### Build, CI and service image pins

`deploy/images.json` supplies separately labelled OCI index and amd64/arm64
platform manifest declarations for Python, Node, PostgreSQL, uv and Trivy.
Their declared SHA256 values are validated syntactically, not relabeled as hashes
of installed tools. The manifest evidence file itself is byte-hashed.
No container-license metadata or permission is invented from an image tag.
These entries carry blockers because their package graphs have not been inspected
by this input. In particular, passing the application image does not inventory
the PostgreSQL service or the Node/uv/Trivy build/CI images.
GitHub Actions source archives and host executables are not collected; their
workflow commit pins alone do not establish archive/native/transitive rights.

### OWASP ASVS documentation adaptation

`compliance/asvs-source.json`, the adapted `compliance/asvs-5.0.0.csv` and
`docs/compliance/asvs-register.md` are collected separately as third-party
**CC-BY-SA-4.0** documentation adaptations, not as original dual-licensed source.
The metadata preserves OWASP attribution and the declared upstream CSV digest.
Each actual adapted file has its own independently computed byte SHA256; that
value must not be confused with the upstream CSV digest. Attribution, change
indication and ShareAlike review obligations remain pending; collecting the
register does not imply public-release permission or ASVS conformance.

## Review boundary and limitations

All collected records start with `status: pending`, `rights_confirmed: false`,
null reviewer/review date and null concluded/selected license. Detection does not
invent authorship, permissions, notice satisfaction, modifications history,
license reconciliation, source offers or relinking rights. Unknown native/asset
licenses stay unknown and distribution stays blocked even when an owner's
package metadata happens to say MIT.

The graph mixes explicitly labelled lock candidates, installed development
artifacts, the built frontend bundle, image OS/assets, source-build omissions and
third-party documentation. It is **not** a claim that every record is shipped or
that every embedded component has been independently identified. Read each
`scope`, `artifact_kind`, `digest_basis`, raw dependency declaration and blocker.
CycloneDX output from the existing SBOM generator is a graph snapshot; the full
JSON retains the detailed file evidence and coverage distinctions.

Collection does not contact upstreams to authenticate license claims, fetch
archives, prove reproducible builds, infer unlisted embedded third parties, or
provide legal certification. A qualified review must reconcile these observations
with the exact intended distribution, satisfy attribution/source obligations,
record artifact-specific rights evidence and conclusions, and remove actual
blockers before any release eligibility claim.
