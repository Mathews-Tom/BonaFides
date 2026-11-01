# SPDX-License-Identifier: MIT OR Apache-2.0
"""Inflation rules: each kind blocks a contained statement; its honest counterpart passes."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from bonafides.claim_coverage import main as claims_main
from bonafides.claim_mapping import map_line, statement_text
from bonafides.claims import (
    METRIC_UNITS,
    OWNERSHIP_VERBS,
    StructuredClaim,
    contains_statement,
    date_bounds,
    render_claim,
)
from bonafides.documents import DocumentBlock, DocumentBody, DocumentSection
from bonafides.inflation import CODES, Figure, inflation_kinds, read
from bonafides.inflation_report import MAX_FILE_BYTES, MAX_LINE_BYTES, load, report
from bonafides.statement_check import CHECKER_VERSION, check_statements

ROOT = Path(__file__).parent
SETS = ROOT / "fixtures" / "inflation"
CORPUS = ROOT / "fixtures" / "claim-corpus.txt"


@pytest.mark.parametrize(
    ("claim", "statement"),
    [
        ("Assisted the team that led the billing migration", "led the billing migration"),
        ("Contributed to the design of the event schema", "Designed the event schema"),
        ("Supported the manager who owned the on-call rotation", "Owned the on-call rotation"),
        ("Helped the lead who designed and led the rollout", "designed and led the rollout"),
    ],
)
def test_inflation_kinds_stronger_verb_blocks(claim: str, statement: str) -> None:
    assert inflation_kinds(statement, claim) == ["stronger_verb"]


@pytest.mark.parametrize(
    ("claim", "statement"),
    [
        ("Led the billing migration", "Led the billing migration"),
        ("Led the billing migration", "supported the finance team during the cutover"),
        ("Managed the release calendar", "contributed to the incident runbook"),
        ("Assisted the team that led the billing migration", "the billing migration"),
        # "Cut" without an amount is not an outcome verb ("Cut the 2.0 release").
        ("Assisted with the 2.0 release", "Cut the 2.0 release"),
    ],
)
def test_inflation_kinds_weaker_equal_or_absent_verb_passes(claim: str, statement: str) -> None:
    assert inflation_kinds(statement, claim) == []


@pytest.mark.parametrize(
    ("claim", "statement"),
    [
        ("employee 2021–2023 at Harbor Analytics", "Contract developer 2019–2023"),
        ("Operations lead (Mar 2019–Jun 2022)", "Operations lead (2019–2022)"),
        ("Product manager in 2022", "product team 2020–2022"),
        ("Researcher (2019–2020)", "a placement in 2018"),
        ("Support engineer (Jan 2016–Dec 2016)", "team member from 2016 to present"),
        ("Data analyst (2020-05–2021-08)", "Data analyst 05/2020 to 09/2021"),
    ],
)
def test_inflation_kinds_longer_or_outside_tenure_blocks(claim: str, statement: str) -> None:
    assert inflation_kinds(statement, claim) == ["longer_tenure"]


@pytest.mark.parametrize(
    ("claim", "statement"),
    [
        ("Platform engineer at Northwind Labs (2018–2023)", "rebuilt the API gateway in 2021"),
        ("Operations lead (Mar 2019–Jun 2022)", "Operations lead (Mar 2019–Jun 2022)"),
        ("Operations lead 2019–2022", "Operations lead (Mar 2019–Jun 2022)"),
        ("Teacher at Ridgeview School 2012–2019", "chairing the science fair in 2017 and 2018"),
        ("Led the billing migration", "Led the billing migration in 2019"),
    ],
)
def test_inflation_kinds_sub_period_or_unstated_claim_period_passes(
    claim: str, statement: str
) -> None:
    assert inflation_kinds(statement, claim) == []


@pytest.mark.parametrize(
    ("claim", "statement"),
    [
        ("Grew newsletter subscribers by 40%", "grew newsletter subscribers by 65%"),
        ("Improved test coverage by 25 points", "Improved test coverage by 25%"),
        ("Closed $3.4M in new contracts", "Closed $3.4B in new contracts"),
        ("Saved $120K for the clinic network", "Saved €120K for the clinic network"),
        ("Reduced cloud spend by $380K", "Reduced cloud spend by $380K per month"),
        ("Saved $240K per year", "Saved $240K per month"),
        ("Managed 35 key accounts", "Managed 35 key accounts out of 120 regional accounts"),
        ("Reduced handle time by 90 seconds", "Reduced handle time by 90 minutes"),
    ],
)
def test_inflation_kinds_larger_or_unit_changed_metric_blocks(claim: str, statement: str) -> None:
    assert inflation_kinds(statement, claim) == ["larger_metric"]


@pytest.mark.parametrize(
    ("claim", "statement"),
    [
        ("Reduced checkout latency by 35% across 4 regions", "Reduced checkout latency by 35%"),
        ("Reduced checkout latency by 35% across 4 regions", "35% across 2 regions"),
        ("Saved $1.2M annually", "Saved $1.2M"),
        ("Saved $1.2M annually", "Saved $1.2M annually"),
        ("Increased weekly active users by 12,000", "Increased weekly active users by 12,000"),
        ("Led the billing migration", "Led the billing migration for 40 services"),
    ],
)
def test_inflation_kinds_equal_smaller_or_unstated_metric_passes(
    claim: str, statement: str
) -> None:
    assert inflation_kinds(statement, claim) == []


@pytest.mark.parametrize(
    ("claim", "statement"),
    [
        (
            "Led incident reviews for the payments team",
            "incident reviews, later adopted company-wide",
        ),
        ("Ran onboarding for the support squad", "a model later copied department-wide"),
        ("Audited access for the finance team", "later an enterprise-wide control"),
        ("Drafted the team charter", "the team charter that informed a cross-company standard"),
        ("Organized personal study groups", "a division-wide programme"),
    ],
)
def test_inflation_kinds_wider_scope_blocks(claim: str, statement: str) -> None:
    assert inflation_kinds(statement, claim) == ["wider_scope"]


@pytest.mark.parametrize(
    ("claim", "statement"),
    [
        ("Rolled out incident reviews company-wide", "piloting it with the payments team"),
        ("Introduced trunk-based development for the mobile team", "for the mobile team"),
        ("Coordinated the organization-wide survey", "the department follow-ups"),
        ("Led the billing migration", "Led the billing migration company-wide"),
        ("Led the cross-team review", "the review for one team"),
    ],
)
def test_inflation_kinds_equal_narrower_or_unstated_scope_passes(
    claim: str, statement: str
) -> None:
    assert inflation_kinds(statement, claim) == []


@pytest.mark.parametrize("unit", METRIC_UNITS)
@pytest.mark.parametrize("magnitude", [None, "thousand", "million", "billion"])
def test_read_every_editor_metric_with_role_organization_and_dates(
    unit: str, magnitude: str | None
) -> None:
    claim = StructuredClaim.model_validate(
        {
            "schema_version": 1,
            "verb": "reduced",
            "subject": "processing time",
            "scope": "as Decision analyst at Global Payments",
            "metric": {
                "value": "2.5",
                "unit": unit,
                "magnitude": magnitude,
                "change": "decrease",
            },
            "period": {"start": "2020-03", "end": "2022-06"},
        }
    )
    reading = read(render_claim(claim))
    assert reading.verb_rank == OWNERSHIP_VERBS.index("reduced")
    assert reading.periods == ((date(2020, 3, 1), date(2022, 6, 30)),)
    assert claim.metric is not None
    assert reading.figures == (
        Figure(Decimal("2.5"), claim.metric.unit, claim.metric.magnitude, None),
    )


@pytest.mark.parametrize(
    "text",
    [
        "Engineer since 2020",
        "Engineer from 2020",
        "Engineer from 2020 to present",
    ],
)
def test_read_open_tenure_starts_without_explicit_end(text: str) -> None:
    assert read(text).periods == ((date(2020, 1, 1), date.max),)


@pytest.mark.parametrize(
    "text",
    [
        "Processed 2000 tickets",
        "Processed 2,000 tickets",
        "Processed 2000 tickets per year",
    ],
)
def test_read_count_that_looks_like_year_is_not_tenure(text: str) -> None:
    reading = read(text)
    assert reading.periods == ()
    assert reading.figures[0].value == Decimal("2000")
    assert reading.figures[0].unit == "count"


@pytest.mark.parametrize("role", ["Decision analyst", "Marketing analyst"])
def test_read_month_prefix_in_role_is_not_month_context(role: str) -> None:
    assert read(f"{role} (2019–2022)").periods == ((date(2019, 1, 1), date(2022, 12, 31)),)


@pytest.mark.parametrize(
    "text",
    [
        "Saved $10K per month",
        "Saved $10K a month",
        "Saved $10K/month",
        "Saved $10K monthly",
    ],
)
def test_read_rate_attaches_to_its_figure(text: str) -> None:
    assert read(text).figures == (Figure(Decimal("10"), "usd", "thousand", "month"),)


@pytest.mark.parametrize("separator", ["; ", ", ", " and "])
def test_read_rate_does_not_cross_clause_boundary(separator: str) -> None:
    reading = read(f"Saved $10K{separator}reviewed reports monthly")
    assert reading.figures == (Figure(Decimal("10"), "usd", "thousand", None),)


def test_read_structured_rendering_recovers_verb_period_and_metric_fields() -> None:
    mapped = [map_line(line) for line in CORPUS.read_text(encoding="utf-8").splitlines()]
    claims = [mapping.claim for mapping in mapped if mapping.claim is not None]
    assert len(claims) > 250
    for claim in claims:
        reading = read(render_claim(claim))
        assert reading.verb_rank == OWNERSHIP_VERBS.index(claim.verb)
        if claim.period is not None:
            bounds = (date_bounds(claim.period.start)[0], date_bounds(claim.period.end)[1])
            assert bounds in reading.periods
        if claim.metric is not None:
            metric = claim.metric
            assert any(
                figure.value == Decimal(metric.value)
                and figure.unit == metric.unit
                and figure.magnitude == metric.magnitude
                and (metric.per is None or figure.per == metric.per)
                for figure in reading.figures
            ), claim


def test_inflation_kinds_corpus_line_never_strengthens_itself() -> None:
    for line in CORPUS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            assert inflation_kinds(statement_text(line), statement_text(line)) == [], line


def test_read_open_ended_period_and_rate_figure() -> None:
    reading = read("Support engineer from 2016 to present, saving $10K weekly")
    assert reading.periods == ((date(2016, 1, 1), date.max),)
    assert reading.figures == (Figure(Decimal("10"), "usd", "thousand", "week"),)


CLAIM = uuid4()
EVIDENCE = uuid4()
CLAIM_TEXT = "Assisted the team that led the billing migration"
FACT: dict[str, object] = {
    "claim_revision_id": str(CLAIM),
    "evidence_revision_id": str(EVIDENCE),
    "claim_text": CLAIM_TEXT,
    "evidence_text": "In 2022 I assisted the team that led the billing migration (2021–2022).",
    "printable_text": f"{CLAIM_TEXT}\nAnalyst",
}


def _issues(text: str) -> list[dict[str, str]]:
    body = DocumentBody(
        title="Base résumé",
        sections=[
            DocumentSection(
                key="experience",
                heading="Experience",
                blocks=[
                    DocumentBlock(
                        block_key="b-1",
                        text=text,
                        claim_kind="factual",
                        claim_revision_ids=[CLAIM],
                        evidence_revision_ids=[EVIDENCE],
                    )
                ],
            )
        ],
    )
    return check_statements(body, [FACT], {}, "base")


def test_check_statements_inflated_statement_names_statement_and_kind() -> None:
    assert _issues("led the billing migration") == [
        {
            "path": "/sections/0/blocks/0",
            "code": "statement_stronger_verb",
            "statement": "led the billing migration",
        }
    ]
    assert CHECKER_VERSION == "claim-evidence-v2"


def test_check_statements_printable_text_compares_its_own_claim_text() -> None:
    assert _issues(f"{CLAIM_TEXT}\nAnalyst") == []


def test_check_statements_containment_failure_precedes_inflation_rules() -> None:
    assert [issue["code"] for issue in _issues("Led the billing migration company-wide")] == [
        "statement_not_in_claim_evidence"
    ]


def test_inflation_codes_name_each_kind() -> None:
    assert CODES == {
        "stronger_verb": "statement_stronger_verb",
        "longer_tenure": "statement_longer_tenure",
        "larger_metric": "statement_larger_metric",
        "wider_scope": "statement_wider_scope",
    }


def test_committed_sets_are_contained_in_their_evidence() -> None:
    honest = load(SETS / "honest.jsonl", honest=True)
    inflated = load(SETS / "inflated.jsonl", honest=False)
    assert len(honest) >= 100
    for kind in CODES:
        assert sum(case.kind == kind for case in inflated) >= 10
    # Every statement passes containment, so a block is the inflation rule's alone.
    for case in [*honest, *inflated]:
        assert contains_statement(case.statement, case.evidence), case


def test_inflation_report_committed_sets_within_limit_and_every_kind_blocked() -> None:
    result = report(
        load(SETS / "honest.jsonl", honest=True), load(SETS / "inflated.jsonl", honest=False)
    )
    assert result.false_block_rate <= 0.05
    assert result.misattributed == []
    assert all(hits == total for hits, total in result.inflated.values())


def test_claims_cli_inflation_report_prints_rates(capsys: pytest.CaptureFixture[str]) -> None:
    assert claims_main(["inflation-report", str(SETS)]) == 0
    output = capsys.readouterr().out.splitlines()
    assert output[0].startswith("Honest: ") and "false-block" in output[0]
    for kind in CODES:
        assert any(line.startswith(f"Inflated {kind}: ") for line in output)
    assert output[-1] == "False-block rate within the 5% limit"


def _write_sets(directory: Path, honest: Sequence[Mapping[str, object]]) -> None:
    inflated = {
        "evidence": "Assisted the team that led the billing migration.",
        "claim": "Assisted the team that led the billing migration",
        "statement": "led the billing migration",
        "kind": "stronger_verb",
    }
    (directory / "honest.jsonl").write_text(
        "".join(json.dumps(case) + "\n" for case in honest), encoding="utf-8"
    )
    (directory / "inflated.jsonl").write_text(json.dumps(inflated) + "\n", encoding="utf-8")


def test_claims_cli_inflation_report_false_block_above_limit_exits_non_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    passing = {
        "evidence": "Led the billing migration.",
        "claim": "Led the billing migration",
        "statement": "Led the billing migration",
        "kind": None,
    }
    blocked = {
        "evidence": "Led the billing migration (2021–2023), team 2019–2023.",
        "claim": "Led the billing migration (2021–2023)",
        "statement": "team 2019–2023",
        "kind": None,
    }
    _write_sets(tmp_path, [passing] * 9 + [blocked])
    assert claims_main(["inflation-report", str(tmp_path)]) == 1
    output = capsys.readouterr().out.splitlines()
    assert output[0] == "Honest: 1 of 10 blocked (false-block 10.0%)"
    assert output[1] == "honest line 10: statement_longer_tenure"
    assert output[-1] == "False-block rate exceeds the 5% limit"


def test_claims_cli_inflation_report_uncontained_claim_is_rejected(tmp_path: Path) -> None:
    _write_sets(
        tmp_path,
        [
            {
                "evidence": "Tuned PostgreSQL queries.",
                "claim": "SQL",
                "statement": "Tuned PostgreSQL queries",
                "kind": None,
            }
        ],
    )
    with pytest.raises(SystemExit) as refused:
        claims_main(["inflation-report", str(tmp_path)])
    assert refused.value.code == 2


@pytest.mark.parametrize("filename", ["honest.jsonl", "inflated.jsonl"])
@pytest.mark.parametrize(
    "raw",
    [b"not JSON", b"[" * 10_000 + b"0" + b"]" * 10_000, b"\xff"],
    ids=["malformed-json", "deeply-nested-json", "invalid-utf8"],
)
def test_claims_cli_inflation_report_bad_line_exits_with_location(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], filename: str, raw: bytes
) -> None:
    _write_sets(
        tmp_path,
        [
            {
                "evidence": "Led migration.",
                "claim": "Led migration",
                "statement": "Led migration",
                "kind": None,
            }
        ],
    )
    (tmp_path / filename).write_bytes(b"\n" + raw + b"\n")
    with pytest.raises(SystemExit) as refused:
        claims_main(["inflation-report", str(tmp_path)])
    assert refused.value.code == 2
    output = capsys.readouterr()
    assert f"{filename}:2:" in output.err
    assert "Traceback" not in output.err
    assert output.out == ""


@pytest.mark.parametrize("error", [ValueError("integer exceeds limit"), RecursionError("too deep")])
def test_claims_cli_inflation_report_parser_errors_are_case_errors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    _write_sets(tmp_path, [])
    (tmp_path / "honest.jsonl").write_bytes(b"\n{}\n")

    def reject_json(raw: str) -> object:
        raise error

    monkeypatch.setattr(json, "loads", reject_json)
    with pytest.raises(SystemExit) as refused:
        claims_main(["inflation-report", str(tmp_path)])
    assert refused.value.code == 2
    output = capsys.readouterr()
    assert f"honest.jsonl:2: {error}" in output.err
    assert "Traceback" not in output.err
    assert output.out == ""


@pytest.mark.parametrize("filename", ["honest.jsonl", "inflated.jsonl"])
def test_claims_cli_inflation_report_oversized_file_is_rejected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], filename: str
) -> None:
    _write_sets(
        tmp_path,
        [
            {
                "evidence": "Led migration.",
                "claim": "Led migration",
                "statement": "Led migration",
                "kind": None,
            }
        ],
    )
    (tmp_path / filename).write_bytes(b" " * (MAX_FILE_BYTES + 1))
    with pytest.raises(SystemExit) as refused:
        claims_main(["inflation-report", str(tmp_path)])
    assert refused.value.code == 2
    output = capsys.readouterr()
    assert f"{filename}:1: file exceeds {MAX_FILE_BYTES} bytes" in output.err
    assert "Traceback" not in output.err
    assert output.out == ""


@pytest.mark.parametrize("filename", ["honest.jsonl", "inflated.jsonl"])
@pytest.mark.parametrize(
    "raw",
    [b" " * (MAX_LINE_BYTES + 1), ("\u00e9" * (MAX_LINE_BYTES // 2) + "\n").encode()],
    ids=["ascii", "multibyte-utf8"],
)
def test_claims_cli_inflation_report_oversized_line_is_rejected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], filename: str, raw: bytes
) -> None:
    _write_sets(
        tmp_path,
        [
            {
                "evidence": "Led migration.",
                "claim": "Led migration",
                "statement": "Led migration",
                "kind": None,
            }
        ],
    )
    (tmp_path / filename).write_bytes(b"\n" + raw)
    with pytest.raises(SystemExit) as refused:
        claims_main(["inflation-report", str(tmp_path)])
    assert refused.value.code == 2
    output = capsys.readouterr()
    assert f"{filename}:2: line exceeds {MAX_LINE_BYTES} bytes" in output.err
    assert "Traceback" not in output.err
    assert output.out == ""


@pytest.mark.parametrize("ending", [b"", b"\n", b"\r\n"])
def test_inflation_report_line_at_byte_limit_is_accepted(tmp_path: Path, ending: bytes) -> None:
    raw = json.dumps(
        {
            "evidence": "Led migration.",
            "claim": "Led migration",
            "statement": "Led migration",
            "kind": None,
        }
    ).encode()
    path = tmp_path / "honest.jsonl"
    path.write_bytes(b"\n" + raw + b" " * (MAX_LINE_BYTES - len(raw) - len(ending)) + ending)
    cases = load(path, honest=True)
    assert len(cases) == 1
    assert cases[0].line == 2
    assert cases[0].claim_text == "Led migration"
