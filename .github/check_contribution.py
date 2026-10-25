# SPDX-License-Identifier: MIT OR Apache-2.0
"""Check real pull-request metadata and each contributed commit; never fabricate grants."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

GRANT = (
    "I have the right to submit this contribution and authorize its distribution under both the MIT License "
    "and Apache License, Version 2.0, with recipients entitled to choose either license (MIT OR Apache-2.0). "
    "I have disclosed third-party material and relevant provenance, including AI assistance, and have obtained "
    "any necessary employer or other rights-holder authorization."
)
HISTORICAL = {
    "934f6f7b9c22a951a5c443a8573b71a6c1359269",
    "dcbc7c3477c06d1a8e17c952d16b0bf181800e17",
}


def git(*arguments: str) -> str:
    return subprocess.check_output(["git", *arguments], text=True).strip()


def main() -> int:
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    request = event.get("pull_request")
    if not isinstance(request, dict):
        raise SystemExit(
            "This gate requires pull-request metadata; it does not infer submission authorization."
        )
    if GRANT not in " ".join((request.get("body") or "").split()):
        raise SystemExit(
            "Pull request is missing the exact explicit dual-license grant from CONTRIBUTING.md."
        )
    base, head = request["base"]["sha"], request["head"]["sha"]
    if not all(re.fullmatch(r"[0-9a-f]{40}", sha) for sha in (base, head)):
        raise SystemExit("Invalid pull-request commit identity")
    commits = git("rev-list", f"{base}..{head}").splitlines()
    if not commits:
        raise SystemExit("No contributed commits found")
    failures = []
    for sha in commits:
        if sha in HISTORICAL:
            continue
        name, email, body = git("show", "-s", "--format=%an%x00%ae%x00%B", sha).split("\x00", 2)
        trailers = subprocess.check_output(
            ["git", "interpret-trailers", "--parse", "--no-divider"], input=body, text=True
        )
        if not re.search(
            rf"^Signed-off-by: {re.escape(name)} <{re.escape(email)}>\s*$",
            trailers,
            re.MULTILINE | re.IGNORECASE,
        ):
            failures.append(sha)
    if failures:
        raise SystemExit("Missing author DCO sign-off on commit(s): " + ", ".join(failures))
    print(
        f"Explicit submission grant and author DCO trailers present for {len(commits)} commits; not legal title certification."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
