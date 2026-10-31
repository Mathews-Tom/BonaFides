# SPDX-License-Identifier: MIT OR Apache-2.0
"""`bonafides claims coverage <file>`: the share of bullets claim schema v1 represents.

Every non-blank line of a local UTF-8 text file is one bullet. Each bullet is mapped by the
deterministic import mapper; the report prints counts and, per unrepresented bullet, its line
number and reasons. The file content is neither stored nor sent anywhere.
"""

from __future__ import annotations

import argparse
import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from bonafides.claim_mapping import map_line


@dataclass(frozen=True)
class CoverageReport:
    total: int
    representable: int
    unrepresented: list[tuple[int, list[str]]] = field(default_factory=list)

    @property
    def share(self) -> float:
        return self.representable / self.total

    def lines(self) -> list[str]:
        # Floor to one decimal so a share below a threshold never displays as reaching it.
        percent = math.floor(self.representable * 1000 / self.total) / 10
        reasons = Counter(reason for _, found in self.unrepresented for reason in found)
        output = [f"Representable: {self.representable} of {self.total} bullets ({percent:.1f}%)"]
        if reasons:
            output.append(
                "Unparsed reasons: "
                + ", ".join(f"{reason} {count}" for reason, count in sorted(reasons.items()))
            )
        output.extend(f"line {number}: {', '.join(found)}" for number, found in self.unrepresented)
        return output


def coverage(text: str) -> CoverageReport:
    total = representable = 0
    unrepresented: list[tuple[int, list[str]]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        total += 1
        mapping = map_line(line)
        if mapping.representable:
            representable += 1
        else:
            unrepresented.append((number, [part.reason for part in mapping.unparsed]))
    if total == 0:
        raise ValueError("no_bullets")
    return CoverageReport(total, representable, unrepresented)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="bonafides claims")
    actions = parser.add_subparsers(dest="action", required=True)
    report = actions.add_parser(
        "coverage",
        help="report the share of bullets in a local text file that structured claims represent",
    )
    report.add_argument("file", type=Path, help="UTF-8 text file; each non-blank line is a bullet")
    arguments = parser.parse_args(argv)
    try:
        text = arguments.file.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        parser.error(f"cannot read {arguments.file}: {exc}")
    try:
        result = coverage(text)
    except ValueError:
        parser.error(f"{arguments.file} contains no bullets")
    print("\n".join(result.lines()))
    return 0
