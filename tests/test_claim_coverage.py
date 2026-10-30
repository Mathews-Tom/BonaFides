# SPDX-License-Identifier: MIT OR Apache-2.0
"""`bonafides claims coverage` reports the representable share of bullets in a local file."""

from __future__ import annotations

from pathlib import Path

import pytest

from bonafides import cli
from bonafides.claim_coverage import coverage

CORPUS = Path(__file__).parent / "fixtures" / "claim-corpus.txt"


def test_claims_coverage_committed_corpus_reports_its_measured_share(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The corpus ends with a held-out block of ordinary bullets written before any mapper change.
    # The share is pinned so a mapper change that alters it is a visible, reviewed decision.
    assert cli.main(["claims", "coverage", str(CORPUS)]) == 0
    first = capsys.readouterr().out.splitlines()[0]
    report = coverage(CORPUS.read_text(encoding="utf-8"))
    assert report.total >= 200
    assert first == "Representable: 228 of 305 bullets (74.7%)"


def test_claims_coverage_counts_non_blank_lines_and_lists_reasons(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "bullets.txt"
    source.write_text(
        "- Reduced checkout latency by 35% across 4 regions\n\n"
        "- Exceeded the annual quota by 18%\n"
        "- Built the billing service; wrote its runbook\n",
        encoding="utf-8",
    )
    assert cli.main(["claims", "coverage", str(source)]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "Representable: 1 of 3 bullets (33.3%)",
        "Unparsed reasons: multiple_statements 1, unknown_verb 1",
        "line 3: unknown_verb",
        "line 4: multiple_statements",
    ]


def test_claims_coverage_share_display_never_rounds_up_to_a_threshold() -> None:
    lines = ["Built the billing service"] * 1899 + ["Exceeded the quota"] * 101
    assert coverage("\n".join(lines)).lines()[0] == "Representable: 1899 of 2000 bullets (94.9%)"


@pytest.mark.parametrize("content", [None, "\n  \n", b"\xff\xfe"])
def test_claims_coverage_missing_empty_or_undecodable_file_exits_with_usage_error(
    tmp_path: Path, content: str | bytes | None
) -> None:
    source = tmp_path / "bullets.txt"
    if isinstance(content, str):
        source.write_text(content, encoding="utf-8")
    elif isinstance(content, bytes):
        source.write_bytes(content)
    with pytest.raises(SystemExit) as raised:
        cli.main(["claims", "coverage", str(source)])
    assert raised.value.code == 2


def test_claims_coverage_reads_a_byte_order_mark_as_no_part_of_line_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "bullets.txt"
    source.write_text("Reduced checkout latency by 35%\n", encoding="utf-8-sig")
    assert source.read_bytes().startswith(b"\xef\xbb\xbf")
    assert cli.main(["claims", "coverage", str(source)]) == 0
    assert capsys.readouterr().out.splitlines()[0] == "Representable: 1 of 1 bullets (100.0%)"
