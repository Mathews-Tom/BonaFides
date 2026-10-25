# Offerloom

**Your job search. Your data. Your AI.**

Offerloom is a self-hosted career workspace in development. The implemented **M0 architecture proof** covers a single owner, immutable profile/evidence revisions, durable synthetic workflows, reviewed grounded documents and isolated PDF rendering. **OL-005** adds editable evidence-linked profiles, immutable history/restore and independent light/dark/system preferences. **OL-006** adds reviewed local PDF/plaintext résumé import without AI, OCR or a provider. **OL-007** adds manual postings and a disabled-by-default, board-specific reviewed Greenhouse read mechanism. **OL-008** adds full-text catalog search/filtering, signed cursor pagination, independent save/dismiss/archive decisions, nonmerging duplicate review and history-preserving source retirement. **OL-009** adds source-pinned requirement review, evidence-linked coverage, independent met/not-met/unknown hard constraints, stale snapshots and an explicitly authorized local-model proposal adapter. **OL-010** adds schema-10 manual application tracking: duplicate-safe saved-posting creation, explicit repeat attempts, immutable stage corrections, archive/counts, private notes/contacts, inert bounded attachments and timezone-aware local follow-ups/interviews. Tracking a stage never sends an application or proves delivery; no model, mail or calendar connection is required. Manual review works without a model; installing a runtime is not consent or route qualification. Real-board collection still requires specific terms review and owner authorization. No real board or model is qualified by synthetic fixture evidence. Real-model document generation remains unqualified; this is not complete M1 or a production-qualified release. See the [deployed contracts and observed checks](docs/development/README.md).

**OL-011 local implementation** adds a first-class model-free base résumé and one shared document workspace: fixed English templates, frozen printable contacts, evidence-linked review, saved-revision editing, immutable history/restore and explicit PDF export. Tailoring uses a separately qualified bounded local-provider route, freezes minimized context and publishes a résumé/letter pair atomically. Unsupported assertions remain visible export blockers; review cannot waive them. Real-model authorization and genuine human factual qualification remain outstanding. See the [document contract](docs/development/README.md#ol-011-document-workspace-contract).

**OL-012 local implementation** adds saved-section regeneration proposals with separate route qualification, exact transmitted-context preview and explicit application to an unchanged target revision. Proposals never replace document text automatically. PDF requests bind the saved revision, content digest and explicit PDF format; rendering rejects missing painted glyphs or lost/reordered selectable text rather than publishing degraded artifacts. Real-model and human-review qualification remain outstanding.

**Development baseline:** macOS on Apple Silicon with the existing Linux arm64 Docker runtime. The observed M0 technical checkpoint permits private M1 and later implementation in dependency order. Native Linux amd64 qualification is **deferred**, not passed and not a prerequisite for that private work. Independent rights review is a separate pending public-distribution gate; it is not tied to amd64 or waived by this platform decision. See the [development dispositions](docs/development/README.md#private-milestone-progression).

## Run the local proof

Install Docker Engine/Desktop with Compose v2 and uv **0.13.0**. The image builds its own pinned Python/Node dependencies; no ignored design files are required.

The runtime uses pinned Python 3.12.15 on supported Alpine 3.24. System libpq, Pango/HarfBuzz and DejaVu fonts are included; `libpq-dev` and binutils provide CPython's normal libpq discovery without adding a compiler or changing the frozen Python graph. Native packages and third-party rights remain separately inventoried and review-blocked.

```sh
uv python install 3.12.15
uv sync --frozen --python 3.12.15 --only-group build --no-build
uv sync --frozen --python 3.12.15 --group build --no-build-isolation
uv run --frozen python -m tools.dev_setup --benchmark
docker compose up --build --detach --wait
docker compose exec api python -m offerloom init-bootstrap
```

Open **http://localhost:8000** (not a replacement remote host). Reveal the one-use token deliberately in a private local terminal, then enter it in the browser with a login name and a strong 15–128-character password:

```sh
uv run --frozen python -m tools.dev_setup --show-bootstrap-token
```

Do not capture that command's output in CI, logs or screenshots. Setup creates private local credential files and `.env` containing configuration paths, not passwords. It refuses to overwrite existing credentials. The bootstrap verifier expires after 15 minutes; the host-local `init-bootstrap` command can renew setup while no owner exists. Ordinary startup does not seed any owner or profile.

The benchmark button loads the synthetic **SYN-CORE** evidence fixture and enables normal/slow/poison/staging-pause workflows. These are deterministic architecture exercises, not an AI provider or measured model benchmark. Omit `--benchmark` for production-mode endpoint refusal; M0 still does not become a production-ready application.

## Boundaries and evidence

- Same-origin React/TypeScript UI; Python/FastAPI/Pydantic API; PostgreSQL 18 durable queue/outbox; private blobs.
- API and worker use a restricted runtime role. A separate one-shot migrator owns DDL. PostgreSQL is not published to the host by the ordinary Compose file.
- A credential-free fixed TCP ingress publishes the API on loopback only; API/worker/PostgreSQL have no general outbound network. The worker also joins an internal source network, reaching a separate secret-free fixed-route Greenhouse transport relay on an outbound bridge. That relay has no database network, keyring, blobs, work spools or host publication. Separate renderer and import-parser services have **no network, database, keyring or blob mount** and read-only roots; untrusted parsing never runs in the API or worker.
- Secure `__Host-` session cookies, exact Origin/CSRF checks, explicit review of the current document revision, and owner-authorized artifact access are part of the proof.
- No mail/calendar adapters, arbitrary website fetching, autonomous employer actions, job submissions, M1 backup product, remote TLS deployment or multi-user support are included. The opt-in local-model adapter requires separate explicit endpoint/model/data/cap authorization and genuine qualification; no hosted fallback is provided. The source relay permits only the official configured-board list GET route; manual paste never fetches its reference URL.

See the self-contained [development guide](docs/development/README.md) for all gates, runtime proof commands and recovery limitations, [architecture and renderer decision](docs/architecture/README.md), and [security policy](SECURITY.md). CI and the probe must actually run before their target is called qualified; checked-in automation and image manifests are not execution evidence. Native and emulated results must be reported separately. Detailed local planning under `.docs/` is deliberately absent from clean checkouts and image context.

## Licensing

Project licensing intent: **MIT OR Apache-2.0**, recipient's choice. Read [LICENSE-MIT](LICENSE-MIT), [LICENSE-APACHE](LICENSE-APACHE), the [dual-license decision](docs/decisions/ADR-0001-dual-licensing.md), [contribution policy](CONTRIBUTING.md) and [compliance checklist](docs/compliance/release-compliance-checklist.md). Development CI does not clear contribution authority, native/font obligations or public-distribution rights; the explicit release-compliance gate remains blocking until qualified review.
