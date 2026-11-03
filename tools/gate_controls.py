# SPDX-License-Identifier: MIT OR Apache-2.0
"""Run the existing secret, license/notice and rights gates against deliberate synthetic violations.

Each control first runs a clean synthetic repository through the real gate (exit 0), then adds one
violation and requires the gate to fail. Source and rights gates are the repository's own
`python -m tools.compliance` commands; the secret scanner is the pinned Trivy image the full tier
already uses. No new scanner verdict is invented: the report records each gate's actual exit
status and only redacted rule, file and line identifiers. Synthetic repositories and raw scanner
output stay under `--scratch`, which must never be uploaded; the synthetic secret value is checked
to be absent from the report before it is written.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_SECRET = "AK" + "IA" + "Z" * 16
SPDX = "# SPDX-License-Identifier: MIT OR Apache-2.0\n"


class ControlError(Exception):
    """A gate accepted a deliberate violation or rejected a clean baseline."""


def run(command: list[str], *, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)


def baseline_repository(root: Path) -> None:
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for name in ("LICENSE-MIT", "LICENSE-APACHE", "NOTICE", "CONTRIBUTING.md"):
        (root / name).write_text("synthetic control fixture\n")
    (root / "compliance").mkdir()
    components = {"schema_version": 1, "components": [{"id": "recorded", "status": "pending"}]}
    (root / "compliance/components.json").write_text(json.dumps(components))
    (root / "app.py").write_text(SPDX)
    (root / "pyproject.toml").write_text('[project]\nlicense = "MIT OR Apache-2.0"\n')


def source_gate(root: Path) -> tuple[int, list[str]]:
    result = run([sys.executable, "-m", "tools.compliance", "source", "--root", str(root)])
    report = json.loads(result.stdout)
    return result.returncode, [str(item) for item in report["blockers"]]


def source_control(scratch: Path, name: str, violation: dict[str, str | None]) -> dict[str, object]:
    root = scratch / name
    baseline_repository(root)
    clean, blockers = source_gate(root)
    if clean != 0 or blockers:
        raise ControlError(f"{name}: clean synthetic repository was rejected: {blockers}")
    for filename, text in violation.items():
        if text is None:
            (root / filename).unlink()
        else:
            (root / filename).write_text(text)
    status, blockers = source_gate(root)
    if status != 1 or not blockers:
        raise ControlError(f"{name}: source gate exited {status} on a deliberate violation")
    return {
        "gate": "tools.compliance source",
        "violation": name,
        "clean_exit": clean,
        "violation_exit": status,
        "blockers": blockers,
    }


def rights_control(scratch: Path) -> dict[str, object]:
    inventory, output = scratch / "rights-inventory.json", scratch / "rights-check.json"
    # This assertion is synthetic checker input, never approval of an actual dependency.
    component = {
        "id": "synthetic-fixture",
        "name": "synthetic-fixture",
        "purl": "pkg:pypi/synthetic-fixture@1.0",
        "version": "1.0",
        "sha256": "a" * 64,
        "upstream_url": "https://example.invalid/source",
        "license_url": "https://example.invalid/LICENSE",
        "detected_license": "MIT",
        "concluded_license": "MIT",
        "selected_license": None,
        "copyright": "Synthetic test author",
        "notices": [],
        "scope": "test fixture only",
        "dependencies": [],
        "modifications": "none",
        "obligations": [
            {"description": "retain notice", "status": "satisfied", "evidence": "fixture assertion"}
        ],
        "evidence": [{"url": "https://example.invalid/LICENSE", "date": "2026-11-03"}],
        "reviewer": "synthetic test role",
        "review_date": "2026-11-03",
        "status": "approved",
        "rights_confirmed": True,
    }
    exits: list[int] = []
    report: dict[str, object] = {}
    for status in ("approved", "pending"):
        component["status"] = status
        inventory.write_text(json.dumps({"schema_version": 1, "components": [component]}))
        result = run(
            [
                sys.executable,
                "-m",
                "tools.compliance",
                "check",
                "--inventory",
                str(inventory),
                "--output",
                str(output),
            ]
        )
        report = json.loads(output.read_text())
        eligible = status == "approved"
        if (
            result.returncode != (0 if eligible else 1)
            or report["release_eligible"] is not eligible
        ):
            raise ControlError(f"release check exited {result.returncode} for {status} fixture")
        exits.append(result.returncode)
    blockers = report["blockers"]
    if not isinstance(blockers, list):
        raise ControlError("release check returned invalid blockers")
    return {
        "gate": "tools.compliance check",
        "violation": "unapproved synthetic component",
        "clean_exit": exits[0],
        "violation_exit": exits[1],
        "release_eligible": False,
        "blocker_count": len(blockers),
    }


def scanner_run(scanner: str, source: Path, raw: Path) -> int:
    raw.mkdir(parents=True)
    result = run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{source}:/source:ro",
            "-v",
            f"{raw}:/output",
            scanner,
            "fs",
            "--scanners",
            "secret",
            "--exit-code",
            "1",
            "--format",
            "json",
            "--output",
            "/output/result.json",
            "/source",
        ]
    )
    if result.returncode not in (0, 1):
        raise ControlError(f"secret scanner failed to run (exit {result.returncode})")
    return result.returncode


def scanner_control(scratch: Path, scanner: str) -> dict[str, object]:
    clean, leaky = scratch / "scanner-clean", scratch / "scanner-leak"
    clean.mkdir()
    (clean / "notes.txt").write_text("no credentials here\n")
    leaky.mkdir()
    (leaky / "leak.txt").write_text(f"aws_access_key_id = {SYNTHETIC_SECRET}\n")
    clean_exit = scanner_run(scanner, clean, scratch / "scanner-clean-raw")
    leak_exit = scanner_run(scanner, leaky, scratch / "scanner-leak-raw")
    if clean_exit != 0 or leak_exit != 1:
        raise ControlError(f"secret scanner exits clean={clean_exit} violation={leak_exit}")
    raw = json.loads((scratch / "scanner-leak-raw/result.json").read_text())
    findings = [
        {
            "target": result["Target"],
            "rule": secret["RuleID"],
            "severity": secret["Severity"],
            "line": secret["StartLine"],
        }
        for result in raw.get("Results", [])
        for secret in result.get("Secrets", [])
    ]
    if not findings:
        raise ControlError("secret scanner exited 1 but reported no secret finding")
    return {
        "gate": "trivy fs --scanners secret",
        "scanner": scanner,
        "violation": "synthetic key",
        "clean_exit": clean_exit,
        "violation_exit": leak_exit,
        "findings": findings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch", type=Path, required=True, help="New directory; never upload.")
    parser.add_argument("--scanner", required=True, help="Pinned Trivy image reference.")
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        arguments.scratch.mkdir(parents=True)
        controls = [
            source_control(arguments.scratch, "secret in source", {"leak.txt": SYNTHETIC_SECRET}),
            source_control(
                arguments.scratch,
                "missing original-source license header",
                {"app.py": "print('no header')\n"},
            ),
            source_control(arguments.scratch, "missing NOTICE", {"NOTICE": None}),
            rights_control(arguments.scratch),
            scanner_control(arguments.scratch, arguments.scanner),
        ]
        text = json.dumps({"controls": controls, "raw_matches_recorded": False}, indent=2) + "\n"
        if SYNTHETIC_SECRET in text:
            raise ControlError("raw synthetic secret reached the report")
        arguments.output.write_text(text)
    except (ControlError, OSError, subprocess.CalledProcessError, ValueError, KeyError) as error:
        print(f"gate controls: {error}", file=sys.stderr)
        return 1
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
