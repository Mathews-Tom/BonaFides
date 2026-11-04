# BonaFides

**Your job search. Your data. Your AI.**

BonaFides is a self-hosted tool that turns evidence you own into résumés and cover letters you can check, then tracks the application. Every factual sentence cites a claim from your profile. Wording the claims do not support blocks review and export, except a dropped trailing qualifier, which the checker does not catch (see [measured results](#measured-results-and-limits)). Nothing is ever sent for you.

![A 30-second walk through BonaFides: demo profile, posting requirements, a checked résumé reviewed and exported as PDF, a blocked unsupported statement, and a tracked application](docs/assets/bonafides-workflow.gif)

The recording is the real interface on a release-image installation with synthetic data and the deterministic fixture model described below. Captions are added over the live page.

1. **Own your evidence.** Facts and their sources live in an immutable-revision profile on your machine.
2. **Paste a posting.** Confirm the requirements it states; missing evidence is never read as a missing skill.
3. **Generate a checked pair.** A résumé and letter are built only from linked claims. An unsupported or inflated statement is shown as a blocker.
4. **Review the exact revision, export the PDF.** Review is bound to one revision and its digest; a changed revision needs a new review.
5. **Track the application.** Stage changes, notes and follow-ups are manual records.

## Install from source

Start with **Git and a working Docker runtime**: Docker Desktop on macOS, or Docker Engine on Linux, with Compose v2 and Buildx. Start Docker before running setup. The installer uses the Docker server's native Linux arm64 or amd64 platform; it does not install Docker or change its context. No host Python, uv or Node installation is needed.

```sh
git clone https://github.com/Mathews-Tom/BonaFides.git
cd BonaFides
sh install.sh
sh start.sh
```

Already have the checkout? Run `sh install.sh`, then `sh start.sh` from its root. Install and start never prompt. A fresh installation defaults to `~/bonafides`, loopback port `8000` and Compose project `bonafides`; reinstall uses the saved settings and image. Installation builds the application image locally when needed and prepares private credentials and management files, ending **prepared, not running**. Start uses the saved settings, runs preparation and migrations, and waits for healthy services before printing the sign-in URL. It does not open a browser unless you run `sh start.sh --open-browser`. Start, stop and uninstall require a successfully completed installation.

For a separate fresh installation, explicitly choose its directory, free port and unique project:

```sh
sh install.sh --dir "$HOME/bonafides-other" --port 8001 --project bonafides-other
sh start.sh --dir "$HOME/bonafides-other"
```

`--image IMAGE` selects an already-loaded native application image; `--build` rebuilds the current checkout instead of reusing a saved image. These two flags cannot be combined. An existing installation's project and port cannot be changed with flags. Conflicts fail with guidance to choose settings using `--dir`, `--port` and `--project`; other installations and occupied ports are never stopped or adopted.

Source acquisition and the first build are preparation, not a measured “under ten minutes” install. Docker may download pinned base images and dependencies; the application image is never pulled or published. There is currently no published application-image archive to download.

After start, create the owner at the printed URL using the bootstrap token, a login name and a strong 15–128-character password. Start prints a retrieval command, never the token itself. Run that command only in a private local terminal; for the default directory it is:

```sh
cat "$HOME/bonafides/secrets/bootstrap_token"
```

Use your chosen directory instead if it differs. Never capture the token in logs, CI or screenshots. The ownerless setup window expires after 15 minutes; run `sh start.sh` again (with `--dir` for a custom directory) to renew it without reinstalling or losing data. Once the owner is created, sign in normally with the owner's password; the bootstrap token is no longer used. Installation and startup never create an owner, demo profile or model route automatically.

To see the workflow with synthetic data, load the demo profile after creating the owner. For the default installation directory, use the saved local image identity:

```sh
BONAFIDES_IMAGE="$(cat "$HOME/bonafides/.installer-image")" \
  docker compose --project-directory "$HOME/bonafides" \
  --env-file "$HOME/bonafides/.env" -f "$HOME/bonafides/compose.yaml" \
  exec api bonafides demo load
```

Use your chosen directory in all paths if it differs. `demo load` needs the owner to exist first and the profile, evidence and claims to be empty. It loads two synthetic claims with their evidence, repeats as a no-op, and refuses without writing anything when the profile holds your own data or any edit, reclassification or archive of the demo data. Ordinary startup never creates demo data, an owner or a model route.

Tailored generation needs a model route that you authorize explicitly. Without one, the base résumé, review, PDF export and tracking all work. [`tools/core_loop_proof.py`](tools/core_loop_proof.py) runs this exact path on the release image in production mode with a deterministic fixture model; see the [development guide](docs/development/README.md#core-loop-proof-and-workflow-recording).

## Measured results and limits

- **Observed real-model qualification (stage `generate_documents` only).** On 2026-11-02 on macOS Apple Silicon, host Ollama `0.40.13` with `qwen2.5:7b-instruct` produced valid résumé/letter JSON on the first response for **20/20 cases (100%)**, with **98.0% usable factual statements**, in **1,318.184 seconds**; `gemma4:12b-mlx` produced valid first-response JSON in **0/20 cases** and failed qualification. Real `regenerate_section` runs were not performed, so that stage is unqualified. This measures JSON shape and checker usability, not semantic correctness. Details: [observed local schema qualification](docs/development/README.md#observed-local-schema-qualification--2026-11-02).
- **Deterministic fixture proof, not qualification.** The core-loop proof and its recording use `tools/stub_model.py`, whose qualification receipts are fixture-only. They show the workflow and the blocking behavior, and attest nothing about any real model.
- **Checker benchmark on synthetic sets (deterministic, no model).** Checker `claim-evidence-v6` blocked all targeted inflation classes and every untargeted fabrication class except one, and falsely blocked 2 of 1,175 honest statements (0.2%). The `dropped_qualifier` class (a limiting trailing qualifier cut away) is **not caught: 0 of 12**, an accepted limitation. Strict claim coverage of the 305-bullet synthetic corpus is **90.1%**, below the 95% target. Details: [structured claim evidence matching](docs/development/README.md#structured-claim-evidence-matching).

## Implemented scope

BonaFides is a self-hosted career workspace in development. The implemented **Architecture proof** covers a single owner, immutable profile/evidence revisions, durable synthetic workflows, reviewed grounded documents and isolated PDF rendering. **Evidence-linked profiles** adds editable evidence-linked profiles, immutable history/restore and independent light/dark/system preferences. **Reviewed résumé import** adds reviewed local PDF/plaintext résumé import without AI, OCR or a provider. **Job sources** adds manual postings and a disabled-by-default, board-specific reviewed Greenhouse read mechanism. **Job catalog** adds full-text catalog search/filtering, signed cursor pagination, independent save/dismiss/archive decisions, nonmerging duplicate review and history-preserving source retirement. **Suitability review** adds source-pinned requirement review, evidence-linked coverage, independent met/not-met/unknown hard constraints, stale snapshots and an explicitly authorized local-model proposal adapter. **Application tracking** adds schema-10 manual application tracking: duplicate-safe saved-posting creation, explicit repeat attempts, immutable stage corrections, archive/counts, private notes/contacts, inert bounded attachments and timezone-aware local follow-ups/interviews. Tracking a stage never sends an application or proves delivery; no model, mail or calendar connection is required. Manual review works without a model; installing a runtime is not consent or route qualification. Real-board collection still requires specific terms review and owner authorization. Synthetic transport fixtures do not qualify a real board or model. Local-model schema qualification is reported separately above; this is not complete private alpha or a production-qualified release. See the [deployed contracts and observed checks](docs/development/README.md).

**Document generation local implementation** adds a first-class model-free base résumé and one shared document workspace: fixed English templates, frozen printable contacts, evidence-linked review, saved-revision editing, immutable history/restore and explicit PDF export. Tailoring uses a separately qualified bounded local-provider route, freezes minimized context and publishes a résumé/letter pair atomically. Unsupported assertions remain visible export blockers; review cannot waive them. Every installation still needs its own route authorization and receipt; genuine human factual qualification remains outstanding. See the [document contract](docs/development/README.md#document-workspace-contract).

**Document editing and export local implementation** adds saved-section regeneration proposals with separate route qualification, exact transmitted-context preview and explicit application to an unchanged target revision. Proposals never replace document text automatically. PDF requests bind the saved revision, content digest and explicit PDF format; rendering rejects missing painted glyphs or lost/reordered selectable text rather than publishing degraded artifacts. Section regeneration requires its own stage receipt; genuine human-review qualification remains outstanding.

**Task and approval control local implementation** adds one durable task workspace with bounded history, actual attempt/heartbeat observations, safe due retry, explicit local cancellation checkpoints and unknown usage/cost. Exact single-action approvals bind the reviewed owner, payload, destination, projection, revisions, budgets and expiry; changed or replayed intent cannot grant another execution. Hosted transmission controls reserve each attempt without enabling a hosted adapter. Approval is not delivery, factual review, blanket consent or permission to execute an unavailable capability.

**Data portability, erasure and encrypted backup and recovery (local implementation)** adds owner-data portability and erasure, plus operator-controlled encrypted installation backup and isolated restore. Each new backup also carries a verified `bonafides-workspace/1` archive built from the same database snapshot, which imports into a fresh installation. Daily scheduling, verified local copies, retention and explicit restored-target opening are implemented. Local recovery evidence does not establish off-host survival or key custody, genuine human qualification, production readiness or independent rights clearance.

**Development baseline:** macOS on Apple Silicon with the existing Linux arm64 Docker runtime. The observed architecture proof technical checkpoint permits private-alpha and later implementation in dependency order. Native Linux amd64 qualification is **deferred**, not passed and not a prerequisite for that private work. Independent rights review is a separate pending public-distribution gate; it is not tied to amd64 or waived by this platform decision. See the [development dispositions](docs/development/README.md#private-milestone-progression).

**Schema baseline (0.1.0):** a fresh install runs one baseline migration. Databases and encrypted backups created before the baseline, and legacy `bonafides-portable` archives, are refused; export a `bonafides-workspace/1` archive from the last pre-baseline build (commit `771776e`) and import it into a fresh install. See [Schema baseline and migration policy](docs/development/README.md#schema-baseline-and-migration-policy) and [CHANGELOG.md](CHANGELOG.md).

## Run the local proof

Install Docker Engine/Desktop with Compose v2 and uv **0.13.0**. The image builds its own pinned Python/Node dependencies; no ignored design files are required.

The runtime uses pinned Python 3.12.15 on supported Alpine 3.24. System libpq, Pango/HarfBuzz and DejaVu fonts are included; `libpq-dev` and binutils provide CPython's normal libpq discovery without adding a compiler or changing the frozen Python graph. Native packages and third-party rights remain separately inventoried and review-blocked.

```sh
uv python install 3.12.15
uv sync --frozen --python 3.12.15 --only-group build --no-build
uv sync --frozen --python 3.12.15 --group build --no-build-isolation
uv run --frozen python -m bonafides.local_setup --benchmark
docker compose up --build --detach --wait
docker compose exec api python -m bonafides init-bootstrap
```

Open **http://localhost:8000** (not a replacement remote host). Reveal the one-use token deliberately in a private local terminal, then enter it in the browser with a login name and a strong 15–128-character password:

```sh
uv run --frozen python -m bonafides.local_setup --show-bootstrap-token
```

Do not capture that command's output in CI, logs or screenshots. Setup creates private local credential files and `.env` containing configuration paths, not passwords. It refuses to overwrite existing credentials. The bootstrap verifier expires after 15 minutes; the host-local `init-bootstrap` command can renew setup while no owner exists. Ordinary startup does not seed any owner or profile.

The benchmark button loads the synthetic **SYN-CORE** evidence fixture and enables normal/slow/poison/staging-pause workflows. These are deterministic architecture exercises, not an AI provider or measured model benchmark. Omit `--benchmark` for production-mode endpoint refusal; architecture proof still does not become a production-ready application.

## Start, stop, reinstall and uninstall

From the checkout, management commands use the saved installation settings:

```sh
sh start.sh
sh stop.sh
sh uninstall.sh
```

`start.sh` uses the saved local image without rebuilding or resetting credentials. `stop.sh` stops only this installation's containers and preserves containers, volumes, configuration and keys. Use `--dir "/your/custom/path"` on each command only if you chose a nondefault installation directory; project and port come from saved settings.

Copies of `start.sh`, `stop.sh`, `uninstall.sh` and their shared helper are also installed in the installation directory. They default to that directory, so you can manage an installation without the source checkout:

```sh
sh "$HOME/bonafides/start.sh"
sh "$HOME/bonafides/stop.sh"
sh "$HOME/bonafides/uninstall.sh"
```

Normal uninstall asks for confirmation before removing only the target installation's containers and networks. It **keeps data volumes, configuration, keys and management scripts**. Reinstall, then start, to use the retained data with the same owner. `--yes` skips only the normal uninstall confirmation. Purge requires separate consent and deletes only owned managed volumes and files:

```sh
# Prompts you to type the saved project name.
sh uninstall.sh --purge
# Unattended: both normal removal and purge consent are explicit.
sh uninstall.sh --yes --purge --confirm-purge bonafides
```

Replace `bonafides` with the exact saved project name. `--yes` alone never authorizes purge. Neither uninstall mode removes shared images, Docker, the source checkout, unrelated files or external backups.

Installation requires a stopped target: it never stops or implicitly restarts a running installation. To reinstall the default installation with its saved image, run `sh stop.sh`, `sh install.sh`, then `sh start.sh`. Reruns reuse the saved project, port and immutable local image identity; they do not rebuild just because the checkout changed. Choose `sh install.sh --build` to build and install the current checkout deliberately, or `sh install.sh --image bonafides:local` for an already-loaded native application image. These options are mutually exclusive. No application image is pulled.

Installation and startup also run unattended with the same defaults and no consent flag:

```sh
sh install.sh
sh start.sh
```

A separate installation needs a different directory, free port and unique project; an existing installation's saved project and port cannot be changed by rerunning installation. Conflicts fail with flag guidance rather than prompting or stopping/adopting resources. A complete credential set is reused unchanged; a partial or invalid set is refused and never deleted or rotated. Keep `secrets/` private and escrow the keyring separately.

Advanced operators with an already-loaded image can use the image-only [`deploy/install.sh` path](docs/development/README.md#install-from-a-local-image), followed by the installed `start.sh`; it is not a published-image download service. See the [noninteractive installation details](docs/development/README.md#noninteractive-application-installation) for build identity and timing boundaries.

## Boundaries and evidence

- Same-origin React/TypeScript UI; Python/FastAPI/Pydantic API; PostgreSQL 18 durable queue/outbox; private blobs.
- API and worker use a restricted runtime role. A separate one-shot migrator owns DDL. PostgreSQL is not published to the host by the ordinary Compose file.
- A credential-free fixed TCP ingress publishes the API on loopback only; API/worker/PostgreSQL have no general outbound network. The worker also joins an internal source network, reaching a separate secret-free fixed-route Greenhouse transport relay on an outbound bridge. That relay has no database network, keyring, blobs, work spools or host publication. Separate renderer and import-parser services have **no network, database, keyring or blob mount** and read-only roots; untrusted parsing never runs in the API or worker.
- Secure `__Host-` session cookies, exact Origin/CSRF checks, explicit review of the current document revision, and owner-authorized artifact access are part of the proof.
- No mail/calendar adapters, arbitrary website fetching, autonomous employer actions, job submissions, remote TLS deployment or multi-user support are included. The opt-in local-model adapter requires separate explicit endpoint/model/data/cap authorization and genuine qualification; no hosted fallback is provided. The source relay permits only the official configured-board list GET route; manual paste never fetches its reference URL.

See the self-contained [development guide](docs/development/README.md) for all gates, runtime proof commands and recovery limitations, [architecture and renderer decision](docs/architecture/README.md), and [security policy](SECURITY.md). CI and the probe must actually run before their target is called qualified; checked-in automation and image manifests are not execution evidence. Native and emulated results must be reported separately. Detailed local planning under `.docs/` is deliberately absent from clean checkouts and image context.

## Licensing

Project licensing intent: **MIT OR Apache-2.0**, recipient's choice. Read [LICENSE-MIT](LICENSE-MIT), [LICENSE-APACHE](LICENSE-APACHE), the [dual-license decision](docs/decisions/ADR-0001-dual-licensing.md), [contribution policy](CONTRIBUTING.md) and [compliance checklist](docs/compliance/release-compliance-checklist.md). Development CI does not clear contribution authority, native/font obligations or public-distribution rights; the explicit release-compliance gate remains blocking until qualified review.
