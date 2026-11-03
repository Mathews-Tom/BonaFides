# SPDX-License-Identifier: MIT OR Apache-2.0
"""Record the README workflow GIF from the real UI on a disposable release-image installation.

The installation is built exactly as in `tools.core_loop_proof` (production mode, owner setup,
`bonafides demo load`, explicit fixture model). Sign-in happens before recording so no credential
reaches a frame. Captions are an overlay on the live page; every frame is the served application
with synthetic data. The recording is a development asset: CI never runs this and never uploads it.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from pathlib import Path

from playwright.sync_api import Locator, Page, ViewportSize, sync_playwright

from tools.core_loop_proof import CoreLoopProof
from tools.runtime_proof import ProbeFailure, require_unused_namespace

VIEWPORT: ViewportSize = {"width": 1280, "height": 800}
RENDER_TIMEOUT_MS = 240_000
VALID_TITLE = "Fictional operations analyst"
BLOCKED_TITLE = "Fictional operations analyst (fabricated skill)"
_CAPTION = """text => {
  let bar = document.getElementById('demo-caption');
  if (!bar) {
    bar = document.createElement('div');
    bar.id = 'demo-caption';
    bar.style.cssText = 'position:fixed;left:0;right:0;bottom:0;z-index:99999;padding:14px 24px;'
      + 'background:#16323f;color:#fff;font:600 20px system-ui,sans-serif;text-align:center';
    document.body.appendChild(bar);
  }
  bar.textContent = text;
}"""
_FFMPEG = (
    "fps=6,scale=720:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=64:stats_mode=diff[p];"
    "[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle"
)


class DemoCapture(CoreLoopProof):
    def scene(self, page: Page, caption: str, hold_ms: int = 2500) -> None:
        page.evaluate(_CAPTION, caption)
        page.wait_for_timeout(hold_ms)

    @staticmethod
    def show(page: Page, target: Locator) -> None:
        target.first.evaluate("element => element.scrollIntoView({block: 'center'})")

    def sign_in(self, page: Page) -> None:
        page.goto(self.origin + "/")
        page.fill("input[name=login_name]", self.login)
        page.fill("input[type=password]", self.password)
        page.get_by_role("button", name="Sign in").last.click()
        page.get_by_role("link", name="Overview").first.wait_for()

    def open_pair(self, page: Page, title: str) -> None:
        page.get_by_role("link", name="Documents").first.click()
        page.get_by_role("button", name="Tracked application documents").click()
        page.get_by_role("button", name="Reload tracked applications").click()
        selector = page.get_by_label("Tracked application")
        selector.select_option(label=f"{title} · Fictional Acme · attempt 1")
        page.get_by_role("heading", name=re.compile("Application résumé")).wait_for()

    def record(self, directory: Path) -> Path:
        directory.mkdir(parents=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            setup = browser.new_context(viewport=VIEWPORT)
            self.sign_in(setup.new_page())
            state = setup.storage_state()
            setup.close()
            context = browser.new_context(
                viewport=VIEWPORT,
                storage_state=state,
                record_video_dir=str(directory),
                record_video_size=VIEWPORT,
            )
            context.set_default_timeout(RENDER_TIMEOUT_MS)
            page = context.new_page()
            page.goto(self.origin + "/")
            self.show(page, page.get_by_text("Your foundation"))
            self.scene(page, "Demo data loaded after owner setup: two evidence-backed facts", 3000)
            page.get_by_role("link", name="Profile").first.click()
            page.get_by_role("button", name=re.compile("^Skills")).click()
            self.scene(page, "Every fact keeps the evidence it came from", 3000)
            page.get_by_role("link", name="Opportunities").first.click()
            page.get_by_role("button", name="Reload posting catalog").click()
            page.get_by_role("button", name=f"Read {VALID_TITLE} at Fictional Acme").click()
            self.show(
                page, page.get_by_role("heading", name="Requirements and explainable suitability")
            )
            self.scene(page, "A pasted posting, with the requirements you confirmed", 3500)
            self.open_pair(page, VALID_TITLE)
            self.show(page, page.get_by_text("Block text: b-1"))
            self.scene(page, "A résumé and letter built only from linked claims", 3500)
            for box in page.get_by_role("checkbox", name=re.compile("I checked block")).all():
                self.show(page, box)
                box.check()
                page.wait_for_timeout(600)
            page.get_by_role("button", name="Accept this exact revision").click()
            self.scene(page, "Review the exact revision you will export", 2500)
            page.get_by_role("button", name="Render reviewed PDF").click()
            download = page.get_by_text("Download rendered PDF for revision 1")
            self.show(page, download)
            self.scene(page, "Export the reviewed PDF", 3000)
            self.open_pair(page, BLOCKED_TITLE)
            self.show(
                page, page.get_by_text("Export blocked: unresolved factual or source issues.")
            )
            self.scene(page, "Unsupported wording blocks review and export", 4500)
            page.get_by_role("link", name="Applications").first.click()
            page.get_by_role("button", name=f"Open {VALID_TITLE} at Fictional Acme").click()
            page.get_by_role("button", name="Change stage or reopen same attempt").click()
            page.get_by_label("Recorded stage").select_option("applied")
            page.get_by_label("Stage reason").fill("Sent the reviewed résumé and letter")
            page.get_by_label(re.compile("I confirm this exact manual tracking action")).check()
            page.get_by_role("button", name="Save manual tracking change").click()
            self.show(page, page.get_by_role("heading", name="Append-only stage history"))
            self.scene(page, "Track the application and what happens next", 3500)
            video = page.video
            if video is None:
                raise ProbeFailure("no_video")
            context.close()
            browser.close()
            return Path(video.path())

    def run_capture(self, work: Path, output: Path) -> None:
        require_unused_namespace(self.project)
        self.extract_script(self.image)
        self.install(self.image)
        self.seed_demo()
        self.start_stub()
        self.scenario("valid")
        self.generate_pair(self.posting_application("", VALID_TITLE))
        self.scenario("posting-only-skill")
        blocked = self.posting_application("-blocked", BLOCKED_TITLE)
        self.generate_pair(blocked)
        video = self.record(work / "video")
        subprocess.run(
            [
                shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg",
                *("-y", "-loglevel", "error", "-i", str(video)),
                *("-vf", _FFMPEG, "-loop", "0", str(output)),
            ],
            check=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--confirm-disposable", required=True)
    parser.add_argument("--platform", choices=("linux/arm64", "linux/amd64"), required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", type=Path, required=True, help="GIF to write")
    args = parser.parse_args()
    if (
        not re.fullmatch(r"bonafides-proof-[a-z0-9][a-z0-9-]{0,22}", args.project)
        or args.project != args.confirm_disposable
    ):
        parser.error("exact disposable namespace confirmation required")
    capture = DemoCapture(args.project, args.port, args.platform, args.image, Path.cwd())
    try:
        capture.run_capture(capture.work, args.output)
    finally:
        capture.teardown()
    print(f"GIF: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
