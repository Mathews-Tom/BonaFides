# SPDX-License-Identifier: MIT OR Apache-2.0
"""Check the committed backend partition, or print one shard's test paths."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shard", type=int, choices=range(1, 5))
    arguments = parser.parse_args()
    shards: dict[str, list[str]] = json.loads((ROOT / ".github/backend_shards.json").read_text())
    expected = {path.relative_to(ROOT).as_posix() for path in ROOT.glob("tests/test_*.py")}
    counts = Counter(path for paths in shards.values() for path in paths)
    errors: list[str] = []
    if set(shards) != {"1", "2", "3", "4"} or not all(shards.values()):
        errors.append("Partition must contain four nonempty shards numbered 1 through 4")
    errors.extend(f"Unassigned test file: {path}" for path in sorted(expected - counts.keys()))
    errors.extend(
        f"Test file assigned {count} times: {path}"
        for path, count in sorted(counts.items())
        if count != 1
    )
    errors.extend(
        f"Listed path is not an existing tests/test_*.py file: {path}"
        for path in sorted(counts.keys() - expected)
    )
    if errors:
        for error in errors:
            print(error)
        return 1
    if arguments.shard:
        print("\n".join(shards[str(arguments.shard)]))
    else:
        print(
            f"Backend partition covers all {len(expected)} test files exactly once across 4 shards"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
