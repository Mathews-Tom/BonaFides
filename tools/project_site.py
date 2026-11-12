# SPDX-License-Identifier: MIT OR Apache-2.0
"""Build site/dist offline; run from the pinned environment with python -m tools.project_site."""

from __future__ import annotations

import argparse
import shutil
from dataclasses import dataclass
from html import escape
from pathlib import Path

from bonafides.fabrication_benchmark import CLASSES, Report, canonical_json, run

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
DIST = SITE / "dist"
REVISION = "ebe4b5d9c78030a665d7879b8dcedeced9223a8b"
SOURCE = f"https://github.com/Mathews-Tom/BonaFides/blob/{REVISION}"
# Historical observations copied from the source pinned above, not current qualification.
MODEL = "qwen2.5:7b-instruct"
MANIFEST = "845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e"


@dataclass(frozen=True)
class Qualification:
    stage: str
    label: str
    usable: str
    duration: str
    dataset: str
    receipt: str


QUALIFICATIONS = (
    Qualification(
        "generate_documents",
        "Tailored résumé and cover-letter pair",
        "98%",
        "1,527.701 s",
        "ec097483f72417505f752a11d93932d1fdd09bdab746b215a683ad923755b7d9",
        "2eed26f598ee8d0c7c80e9eeaebacc38a98d88f2e2aa317f5b761f5cb2aa6282",
    ),
    Qualification(
        "regenerate_section",
        "Single-section rewrite",
        "100%",
        "277.283 s",
        "c1ae6e903ab8f9a199d84802d57294a7cce5ee329eb5adc3e97fec9c76609dd4",
        "00d82589ec7d81acb4feb128f77ec33e49ebcc04176140aad1e3f49b5b74b198",
    ),
)
ASSETS = {
    "styles.css": SITE / "styles.css",
    "assets/review.png": SITE / "assets/review.png",
    "assets/evidence.png": SITE / "assets/evidence.png",
    "assets/application.png": SITE / "assets/application.png",
    "assets/bonafides-workflow.gif": ROOT / "docs/assets/bonafides-workflow.gif",
}


def code(value: str) -> str:
    return f"<code>{escape(value)}</code>"


def percent(per_mille: int) -> str:
    """Format the canonical rounded rate without recalculating a score."""
    return f"{per_mille // 10}.{per_mille % 10}%"


def blocked(count: int, cases: int, per_mille: int) -> str:
    return f"{count:,} / {cases:,} ({percent(per_mille)})"


def render_benchmark(report: Report) -> str:
    honest = report["honest"]
    classes = report["classes"]
    cases = honest["cases"] + sum(row["cases"] for row in classes.values())
    rows = [
        '<tr><th scope="row">Honest statements — false blocks (lower is better)</th><td>—</td>'
        f"<td>{honest['cases']:,}</td><td>"
        + blocked(
            honest["checker_blocked"], honest["cases"], honest["checker_false_block_per_mille"]
        )
        + "</td><td>"
        + blocked(
            honest["baseline_blocked"], honest["cases"], honest["baseline_false_block_per_mille"]
        )
        + "</td></tr>"
    ]
    for name in CLASSES:
        row = classes[name]
        rows.append(
            f'<tr><th scope="row">{escape(name.replace("_", " ").capitalize())}</th>'
            f"<td>{'Yes' if row['targeted'] else 'No'}</td><td>{row['cases']:,}</td><td>"
            + blocked(row["checker_blocked"], row["cases"], row["checker_block_per_mille"])
            + "</td><td>"
            + blocked(row["baseline_blocked"], row["cases"], row["baseline_block_per_mille"])
            + "</td></tr>"
        )
    qualifier = classes["dropped_qualifier"]
    files = ", ".join(code(name) for name in report["dataset"]["files"])
    return f"""
<h2>What the honesty checks catch — and miss</h2>
<p>This built-in comparison checks {cases:,} committed synthetic statements, without calling
an AI model. It compares BonaFides with a fixed lexical baseline, which blocks wording when
its content words are not all in the cited evidence. It is not a ranking of AI models.</p>
<div class="table-scroll" role="region" aria-label="Checker comparison results" tabindex="0">
<table>
<caption>Blocked statements, by class. Higher is better for temptation classes;
lower is better for honest-statement false blocks.</caption>
<thead><tr><th scope="col">Statement class</th><th scope="col">Targeted by inflation rules</th>
<th scope="col">Cases</th><th scope="col">BonaFides checker</th>
<th scope="col">Lexical baseline</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></div>
<p>“Targeted” refers specifically to the inflation rules, not every document check.
Block rates are rounded down and false-block rates up to one decimal place by the canonical
report, so neither is presented as better than measured.</p>
<p><strong>Dropped qualifiers remain a review concern.</strong> On that class, the checker blocks
{qualifier["checker_blocked"]} of {qualifier["cases"]} statements and the baseline blocks
{qualifier["baseline_blocked"]} of {qualifier["cases"]}. Synthetic examples do not measure
human accuracy or guarantee safe wording; you still need to review each draft against evidence.</p>
<details><summary>Corpus, checker identity and exact reproduction</summary>
<dl><dt>Checker</dt><dd>{code(report["checker"])}</dd>
<dt>Lexical baseline</dt><dd>{code(report["baseline"])}</dd>
<dt>Corpus SHA-256</dt><dd>{code(report["dataset"]["sha256"])}</dd>
<dt>Corpus files</dt><dd>{files}</dd></dl>
<p>These identities and every value above are generated from the actual build inputs using
{code("bonafides.fabrication_benchmark.run")}; they are not copied historical scores.
<a href="benchmark.json">Download the exact canonical JSON report</a>.</p>
<pre><code>mkdir -p .local
uvx --from uv==0.13.0 uv run --frozen bonafides benchmark run tests/fixtures/fabrication --output .local/site-benchmark.json</code></pre>
<p>Run from the source-checkout root with the pinned environment prepared. This command is
deterministic and sends no model requests. Editing the corpus or checker changes the build's
comparison, not the authority of the historical qualification below.</p>
</details>
<h3>Real-model comparison leaderboard</h3>
<p><strong>No measured real-model comparator recordings are tracked.</strong> There are no
real-model rows to rank here. The separate recorded-answer leaderboard requires genuine
same-corpus recordings supplied explicitly with {code("--result")}; this site neither invents
recordings nor treats synthetic predictions as measured model evidence.</p>
"""


def render_qualification() -> str:
    rows = "".join(
        f'<tr><th scope="row">{escape(item.label)}<br>{code(item.stage)}</th>'
        f"<td>20 / 20</td><td>0 / 0 / 0</td><td>{item.usable}</td>"
        f"<td>{item.duration}</td><td>Passed</td></tr>"
        for item in QUALIFICATIONS
    )
    identities = "".join(
        f"<dt>{code(item.stage)} dataset SHA-256</dt><dd>{code(item.dataset)}</dd>"
        f"<dt>{code(item.stage)} receipt SHA-256</dt><dd>{code(item.receipt)}</dd>"
        for item in QUALIFICATIONS
    )
    return f"""
<h2>One historically qualified local model, two writing tasks</h2>
<p>On <strong>8 November 2026</strong>, {code(MODEL)} passed both document-stage schema tests
in one fresh disposable {code("linux/arm64")} installation on Apple silicon, using host
Ollama {code("0.40.13")} through the fixed private gateway. No model was downloaded and no
hosted model was called. These are historical observations, not your installation's status.</p>
<div class="table-scroll" role="region" aria-label="Historical qualification results" tabindex="0">
<table><caption>{escape(MODEL)} — one 20-case qualification run per stage, 8 November 2026</caption>
<thead><tr><th scope="col">Stage</th><th scope="col">First-valid / cases</th>
<th scope="col">Corrected / invalid / before-check failures</th>
<th scope="col">Usable factual statements</th><th scope="col">Total qualification duration</th>
<th scope="col">Historical result</th></tr></thead><tbody>{rows}</tbody></table></div>
<p>Each duration is the <strong>total for that stage's 20-case qualification run</strong>, not a
single-document latency, throughput or p95 measurement. “Usable” means factual statements
without checker blockers, not human-reviewed accuracy. The pass threshold was at least 95%
schema-valid outputs, allowing at most one corrective retry; neither run needed a correction.</p>
<p>A different installation, model, dataset, prompt or runtime does not inherit these passes.
Your installation needs its own genuine stage-specific receipts. Human review remains necessary.</p>
<details><summary>Recorded source, hashes, execution scope and limits</summary>
<dl><dt>Recorded observations</dt><dd><a href="{SOURCE}/docs/development/README.md?plain=1#L1432-L1441">Developer record, 8 November 2026</a></dd>
<dt>Source revision for this historical attribution</dt><dd>{code(REVISION)}</dd>
<dt>Model tag</dt><dd>{code(MODEL)}</dd>
<dt>Ollama manifest SHA-256</dt><dd>{code(MANIFEST)}</dd>
<dt>Runtime and hardware scope</dt><dd>Host Ollama 0.40.13; installed Linux arm64 containers on
Apple silicon. Chip model, memory capacity and quantization details were not recorded in this
observation; no more specific hardware performance claim is made.</dd>
<dt>Execution</dt><dd>Installed shell/Docker management commands; no host Python or uv invoked.
Synthetic committed qualification datasets; unchanged prompts, checker and schema threshold.</dd>
<dt>Installed caps</dt><dd>32,768 context tokens; 12,000 input and 6,000 output token caps;
131,072 aggregate request bytes; 262,144 aggregate response bytes; 2,048 framing tokens;
300-second per-call deadline. These are bounds, not measured token usage.</dd>
{identities}</dl>
<p>The pair dataset and model manifest refer back to the
<a href="{SOURCE}/docs/development/README.md?plain=1#L1407-L1441">2 November record and its 8 November continuation</a>;
<a href="{SOURCE}/docs/development/README.md?plain=1#L1309-L1374">the installed route contract records caps and qualification semantics</a>.
Receipt hashes are integrity references, not signatures or independent attestations. Private
raw receipts and route configuration are not published.</p>
<p>Ollama's mutable model tag does not attest immutable per-inference weights. Matching
preflight/postflight manifests cannot exclude a change-and-restore between observations;
{code("inference_model_sha256: null")} and {code("unknown_mutable_tag")} describe that limit.</p>
</details>
<details><summary>Reproduce qualification deliberately — these commands run local inference</summary>
<p>These are the existing installed-management commands, not part of this site's offline build.
They send bundled synthetic examples to an already-installed, operator-authorized local model
and restart that installation to apply completed receipts. Never run them just to view the site.</p>
<p>First review the <a href="{SOURCE}/docs/user-guide/ai-tailoring.md#setting-it-up">complete setup and privacy prerequisites</a>:
Docker and a running local Ollama, the model already installed after license review, cloud disabled,
and no model remapping during qualification/use. The computer's private Docker-network plaintext
HTTP and metadata reads require your authorization. No host Python or uv is needed.</p>
<pre><code>cd ~/bonafides
sh models.sh attach --model {escape(MODEL)} --model-license Apache-2.0 --authorize-local-model
sh models.sh qualify --stage generate_documents --reviewed-by 'Local owner'
sh models.sh qualify --stage regenerate_section --reviewed-by 'Local owner'</code></pre>
<p>Use your actual installation folder and reviewer name; for a nondefault installation, each
management command supports {code("--dir /absolute/path/to/installation")}. Attaching alone
never qualifies a task. Review each genuine result in Documents; never copy historical hashes
into a route to claim a new pass. Changed identities or limits require fresh qualification.</p>
</details>
"""


def build() -> None:
    template = (SITE / "index.html.in").read_text(encoding="utf-8")
    tokens = ("@@BENCHMARK@@", "@@QUALIFICATION@@")
    if any(template.count(token) != 1 for token in tokens):
        raise ValueError("site template must contain each expected section token exactly once")
    for source in ASSETS.values():
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"missing or symlinked site asset: {source.relative_to(ROOT)}")
    destinations = (
        DIST,
        DIST / "assets",
        DIST / "index.html",
        DIST / "benchmark.json",
        *(DIST / name for name in ASSETS),
    )
    if SITE.is_symlink() or any(path.is_symlink() for path in destinations):
        raise ValueError("site output paths must not be symlinks")
    report = run(ROOT / "tests/fixtures/fabrication")
    html = template.replace(tokens[0], render_benchmark(report)).replace(
        tokens[1], render_qualification()
    )
    if "@@" in html:
        raise ValueError("site template contains an unsupported or unresolved token")
    (DIST / "assets").mkdir(parents=True, exist_ok=True)
    (DIST / "index.html").write_text(html, encoding="utf-8")
    (DIST / "benchmark.json").write_bytes(canonical_json(report))
    for name, source in ASSETS.items():
        shutil.copyfile(source, DIST / name)


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()
    build()
    print("Built site/dist (offline; no model calls).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
