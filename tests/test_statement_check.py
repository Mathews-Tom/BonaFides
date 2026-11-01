# SPDX-License-Identifier: MIT OR Apache-2.0
"""Each rule of the claim-structured statement checker, with synthetic facts only."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from bonafides.claims import contains_statement
from bonafides.documents import DocumentBlock, DocumentBody, DocumentSection
from bonafides.statement_check import CONNECTIVES, check_statements, connectives

CLAIM = UUID("00000000-0000-4000-8000-000000000001")
OTHER_CLAIM = UUID("00000000-0000-4000-8000-000000000002")
EVIDENCE = UUID("00000000-0000-4000-8000-000000000011")
SECOND_EVIDENCE = UUID("00000000-0000-4000-8000-000000000012")
OTHER_EVIDENCE = UUID("00000000-0000-4000-8000-000000000013")
FRAME = {"role": "Platform Engineer", "organization": "Example Systems"}
FACTS: list[dict[str, object]] = [
    {
        "claim_revision_id": str(CLAIM),
        "evidence_revision_id": str(EVIDENCE),
        "claim_text": "Maintained PostgreSQL clusters",
        "evidence_text": "In 2024 I maintained PostgreSQL clusters for the billing team.",
    },
    {
        "claim_revision_id": str(CLAIM),
        "evidence_revision_id": str(SECOND_EVIDENCE),
        "claim_text": "Maintained PostgreSQL clusters",
        # Withheld from model context, still read in full by the local checker.
        "evidence_text": "Confidential: maintained PostgreSQL clusters across three regions.",
    },
    {
        "claim_revision_id": str(OTHER_CLAIM),
        "evidence_revision_id": str(OTHER_EVIDENCE),
        "claim_text": "Wrote Terraform modules",
        "evidence_text": "Wrote Terraform modules and ran Kubernetes upgrades.",
    },
]


def _factual(text: str, claims: list[UUID], evidence: list[UUID]) -> DocumentBlock:
    return DocumentBlock(
        block_key="b-1",
        text=text,
        claim_kind="factual",
        claim_revision_ids=claims,
        evidence_revision_ids=evidence,
    )


def _check(block: DocumentBlock, kind: str = "cover_letter") -> list[dict[str, str]]:
    body = DocumentBody(
        title="Cover letter" if kind == "cover_letter" else "Résumé",
        sections=[DocumentSection(key="section-0", heading="Letter", blocks=[block])],
    )
    return check_statements(body, FACTS, FRAME, kind)


def _codes(block: DocumentBlock) -> list[str]:
    return [issue["code"] for issue in _check(block)]


def test_check_statements_contained_in_own_evidence_returns_no_issue() -> None:
    block = _factual("maintained PostgreSQL clusters", [CLAIM], [EVIDENCE, SECOND_EVIDENCE])
    assert _check(block) == []


def test_check_statements_contained_only_in_withheld_evidence_is_supported() -> None:
    block = _factual("across three regions", [CLAIM], [EVIDENCE, SECOND_EVIDENCE])
    assert _check(block) == []


def test_check_statements_claim_absent_from_context_names_the_statement() -> None:
    block = _factual("maintained PostgreSQL clusters", [uuid4()], [])
    assert _check(block) == [
        {
            "path": "/sections/0/blocks/0",
            "code": "claim_not_in_profile",
            "statement": "maintained PostgreSQL clusters",
        }
    ]


def test_check_statements_text_outside_claim_evidence_is_blocked() -> None:
    block = _factual("maintained Oracle clusters", [CLAIM], [EVIDENCE, SECOND_EVIDENCE])
    assert _codes(block) == ["statement_not_in_claim_evidence"]


def test_check_statements_other_claims_evidence_is_never_consulted() -> None:
    # The wording exists in another claim's evidence, not in the cited claim's own.
    block = _factual("ran Kubernetes upgrades", [CLAIM], [EVIDENCE, SECOND_EVIDENCE])
    assert _codes(block) == ["statement_not_in_claim_evidence"]


def test_check_statements_uncited_factual_statement_is_blocked() -> None:
    assert _codes(_factual("I know Kubernetes.", [], [])) == ["uncited_factual_statement"]


def test_check_statements_more_than_one_claim_is_blocked() -> None:
    block = _factual("maintained PostgreSQL clusters", [CLAIM, OTHER_CLAIM], [EVIDENCE])
    assert _codes(block) == ["statement_cites_multiple_claims"]


def test_check_statements_partial_evidence_links_are_blocked() -> None:
    block = _factual("maintained PostgreSQL clusters", [CLAIM], [EVIDENCE])
    assert _codes(block) == ["evidence_not_linked_to_claim"]


def test_check_statements_foreign_evidence_link_is_blocked() -> None:
    block = _factual("maintained PostgreSQL clusters", [CLAIM], [EVIDENCE, OTHER_EVIDENCE])
    assert _codes(block) == ["evidence_not_linked_to_claim"]


@pytest.mark.parametrize(
    "text",
    [
        "Dear Example Systems hiring team,",
        "I am applying for the Platform Engineer role at Example Systems.",
        "Sincerely,",
    ],
)
def test_check_statements_declared_connective_returns_no_issue(text: str) -> None:
    block = DocumentBlock(
        block_key="b-1",
        text=text,
        claim_kind="intent",
        claim_revision_ids=[],
        evidence_revision_ids=[],
    )
    assert _check(block) == []


@pytest.mark.parametrize(
    ("text", "claims"),
    [
        ("I led the Kubernetes migration.", []),
        ("I am applying for the Staff Engineer role at Example Systems.", []),
        ("Sincerely,", [CLAIM]),
    ],
)
def test_check_statements_undeclared_connective_is_treated_as_factual(
    text: str, claims: list[UUID]
) -> None:
    block = DocumentBlock(
        block_key="b-1",
        text=text,
        claim_kind="greeting",
        claim_revision_ids=claims,
        evidence_revision_ids=[],
    )
    assert _codes(block) == ["connective_not_declared"]


def test_connectives_missing_slot_value_omits_template() -> None:
    rendered = connectives({"role": "Platform Engineer", "organization": None})
    assert "Dear Hiring team," in rendered
    assert not any("{" in text or "None" in text for text in rendered)
    assert len(rendered) < sum(len(items) for items in CONNECTIVES.values())


def test_check_statements_title_heading_and_markers_name_their_text() -> None:
    body = DocumentBody(
        title="Letter of intent",
        sections=[
            DocumentSection(
                key="section-0",
                heading="Achievements",
                blocks=[
                    _factual(
                        "maintained PostgreSQL clusters [add metric]",
                        [CLAIM],
                        [EVIDENCE, SECOND_EVIDENCE],
                    )
                ],
            )
        ],
    )
    assert [
        (issue["code"], issue["statement"])
        for issue in check_statements(body, FACTS, FRAME, "cover_letter")
    ] == [
        ("unestablished_heading", "Letter of intent"),
        ("unestablished_heading", "Achievements"),
        ("unresolved_omission_marker", "maintained PostgreSQL clusters [add metric]"),
        ("statement_not_in_claim_evidence", "maintained PostgreSQL clusters [add metric]"),
    ]


@pytest.mark.parametrize(
    ("statement", "excerpt", "contained"),
    [
        ("Maintained PostgreSQL clusters.", "I maintained postgresql   clusters daily", True),
        ("SQL", "PostgreSQL administration", False),
        ("reduced cost by 3", "reduced cost by 30%", False),
        ("", "anything", False),
    ],
)
def test_contains_statement_uses_structured_containment_boundaries(
    statement: str, excerpt: str, contained: bool
) -> None:
    assert contains_statement(statement, excerpt) is contained


ROLE_CLAIM = UUID("00000000-0000-4000-8000-000000000003")
ROLE_EVIDENCE = UUID("00000000-0000-4000-8000-000000000014")
SKILL_CLAIM = UUID("00000000-0000-4000-8000-000000000004")
SKILL_EVIDENCE = UUID("00000000-0000-4000-8000-000000000015")
# The pinned printable text is the claim text plus that claim revision's own fields.
ROLE_PRINTABLE = (
    "Maintained Linux batch runbooks\nOperations Analyst\nJuniper Relay\n2022-02\n2025-05"
)
FIELD_FACTS: list[dict[str, object]] = [
    {
        "claim_revision_id": str(ROLE_CLAIM),
        "evidence_revision_id": str(ROLE_EVIDENCE),
        "claim_text": "Maintained Linux batch runbooks",
        "evidence_text": (
            "Operations Analyst, Juniper Relay (02/2022 to 05/2025)\n"
            "Maintained Linux batch runbooks."
        ),
        "printable_text": ROLE_PRINTABLE,
    },
    {
        "claim_revision_id": str(SKILL_CLAIM),
        "evidence_revision_id": str(SKILL_EVIDENCE),
        "claim_text": "Python",
        "evidence_text": "Skills: Python, Bash",
        "printable_text": "Python\nAdvanced",
    },
]


def _base_codes(text: str, claim: UUID, evidence: UUID, kind: str = "base") -> list[str]:
    body = DocumentBody(
        title="Base résumé" if kind == "base" else "Résumé",
        sections=[
            DocumentSection(
                key="experience",
                heading="Experience",
                blocks=[_factual(text, [claim], [evidence])],
            )
        ],
    )
    return [issue["code"] for issue in check_statements(body, FIELD_FACTS, FRAME, kind)]


def test_check_statements_literal_claim_text_returns_no_issue() -> None:
    assert _base_codes("Maintained Linux batch runbooks", ROLE_CLAIM, ROLE_EVIDENCE) == []


def test_check_statements_exact_pinned_printable_text_with_fields_returns_no_issue() -> None:
    # The structured lines are not literal evidence wording; only the claim text is checked.
    assert not contains_statement(ROLE_PRINTABLE, str(FIELD_FACTS[0]["evidence_text"]))
    assert _base_codes(ROLE_PRINTABLE, ROLE_CLAIM, ROLE_EVIDENCE) == []
    assert _base_codes("Python\nAdvanced", SKILL_CLAIM, SKILL_EVIDENCE) == []


def test_check_statements_model_output_reproducing_printable_text_returns_no_issue() -> None:
    assert _base_codes(ROLE_PRINTABLE, ROLE_CLAIM, ROLE_EVIDENCE, "resume") == []


@pytest.mark.parametrize(
    "text",
    [
        ROLE_PRINTABLE.replace("Operations Analyst", "Operations Director"),
        ROLE_PRINTABLE.replace("2025-05", "2026-05"),
        ROLE_PRINTABLE + "\nPython",
        ROLE_PRINTABLE + "\nAdvanced",
        "Maintained Linux batch runbooks\nJuniper Relay\nOperations Analyst\n2022-02\n2025-05",
        ROLE_PRINTABLE.rsplit("\n", 1)[0],
    ],
    ids=["altered", "altered_date", "added", "other_claim_field", "reordered", "dropped"],
)
def test_check_statements_altered_printable_text_is_statement_not_in_claim_evidence(
    text: str,
) -> None:
    assert _base_codes(text, ROLE_CLAIM, ROLE_EVIDENCE) == ["statement_not_in_claim_evidence"]


def test_check_statements_other_claims_printable_text_is_statement_not_in_claim_evidence() -> None:
    assert _base_codes("Python\nAdvanced", ROLE_CLAIM, ROLE_EVIDENCE) == [
        "statement_not_in_claim_evidence"
    ]


def test_check_statements_printable_text_of_ungrounded_claim_is_not_supported() -> None:
    facts = [{**FIELD_FACTS[0], "evidence_text": "Operations Analyst, Juniper Relay"}]
    body = DocumentBody(
        title="Base résumé",
        sections=[
            DocumentSection(
                key="experience",
                heading="Experience",
                blocks=[_factual(ROLE_PRINTABLE, [ROLE_CLAIM], [ROLE_EVIDENCE])],
            )
        ],
    )
    assert [issue["code"] for issue in check_statements(body, facts, FRAME, "base")] == [
        "statement_not_in_claim_evidence"
    ]


def test_check_statements_base_resume_declares_no_connectives() -> None:
    body = DocumentBody(
        title="Base résumé",
        sections=[
            DocumentSection(
                key="experience",
                heading="Experience",
                blocks=[
                    DocumentBlock(
                        block_key="b-1",
                        text="Sincerely,",
                        claim_kind="greeting",
                        claim_revision_ids=[],
                        evidence_revision_ids=[],
                    )
                ],
            )
        ],
    )
    assert [issue["code"] for issue in check_statements(body, FIELD_FACTS, FRAME, "base")] == [
        "connective_not_declared"
    ]
