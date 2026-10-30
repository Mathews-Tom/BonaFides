# AGENTS.md

## Code Review Rules

- **What failure means here:** owner data becomes accessible without owner authorization, or an exact single-action approval can be replayed or applied to changed intent (`src/bonafides/auth.py`, `src/bonafides/approvals.py`).
- Profile/evidence revisions are lost or imports overwrite unreviewed changes; unsupported assertions or a stale/unreviewed document revision become exportable (`src/bonafides/profiles.py`, `src/bonafides/imports.py`, `src/bonafides/documents.py`).
- A durable task publishes duplicate or stale results after lease loss or cancellation (`src/bonafides/worker.py`, `src/bonafides/workflows.py`).
- Quota-valid owner data cannot round-trip through portability, or recovery overwrites an existing installation or bypasses isolated-target validation (`src/bonafides/portable_archive.py`, `src/bonafides/installation_restore.py`).
- **Do not report:** style, formatting or naming nits; diagnostics already enforced by Ruff lint/format, strict mypy, ESLint (including React Hooks rules) or TypeScript `tsc --noEmit`; speculative hardening without a concrete failing scenario.
- Do not repeat findings raised on an earlier commit of the same PR. Issues in unchanged code are contextual **Pre-existing** notes, not new fix requests.
- **Paths to skip:** mechanical lockfile content in `uv.lock` and `web/pnpm-lock.yaml`, and synthetic fixture payload `tests/fixtures/syn-core.json`; review changed consumers, dependency intent or fixture contracts rather than incidental generated/data churn.
- **Evidence and format:** every finding names `file:line`, the concrete triggering input/state and observable failure, and the bug class so the author can fix every instance; do not infer a defect from a symbol name.
- Tag each finding **Important**, **Nit** or **Pre-existing**. Start the summary with the count of **Important** findings; when there are none, say plainly “Nothing to fix.” Keep optional non-style suggestions separate from required fixes.
- **Recurring rule:** keep README and `docs/development/README.md` commands executable from a clean checkout: current `bonafides` module/`BONAFIDES_` names and project-pinned prerequisites, including Playwright Chromium.
