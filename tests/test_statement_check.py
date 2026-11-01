# SPDX-License-Identifier: MIT OR Apache-2.0
"""Each rule of the claim-structured statement checker, with synthetic facts only."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from bonafides.claims import (
    OWNERSHIP_VERBS,
    OwnershipVerb,
    StructuredClaim,
    contains_statement,
    render_claim,
)
from bonafides.documents import DocumentBlock, DocumentBody, DocumentSection
from bonafides.statement_check import (
    CONNECTIVES,
    check_statements,
    connectives,
)

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


def test_check_statements_claim_contained_only_in_withheld_evidence_is_supported() -> None:
    facts = [
        {**FACTS[0], "evidence_text": "In 2024 I ran PostgreSQL backups."},
        FACTS[1],
    ]
    block = _factual("maintained PostgreSQL clusters", [CLAIM], [EVIDENCE, SECOND_EVIDENCE])
    body = DocumentBody(
        title="Cover letter",
        sections=[DocumentSection(key="section-0", heading="Letter", blocks=[block])],
    )
    assert check_statements(body, facts, FRAME, "cover_letter") == []


def test_check_statements_evidence_wording_outside_the_claim_is_not_in_claim() -> None:
    # Contained in the claim's own withheld evidence, but not part of the claim's text.
    block = _factual("across three regions", [CLAIM], [EVIDENCE, SECOND_EVIDENCE])
    assert _codes(block) == ["statement_not_in_claim"]


def test_check_statements_claim_text_outside_its_evidence_is_blocked() -> None:
    facts = [{**fact, "claim_text": "Maintained Oracle clusters"} for fact in FACTS[:2]]
    block = _factual("Maintained Oracle clusters", [CLAIM], [EVIDENCE, SECOND_EVIDENCE])
    body = DocumentBody(
        title="Cover letter",
        sections=[DocumentSection(key="section-0", heading="Letter", blocks=[block])],
    )
    assert [issue["code"] for issue in check_statements(body, facts, FRAME, "cover_letter")] == [
        "statement_not_in_claim_evidence"
    ]


def test_check_statements_claim_absent_from_context_names_the_statement() -> None:
    block = _factual("maintained PostgreSQL clusters", [uuid4()], [])
    assert _check(block) == [
        {
            "path": "/sections/0/blocks/0",
            "code": "claim_not_in_profile",
            "statement": "maintained PostgreSQL clusters",
        }
    ]


def test_check_statements_text_outside_its_claim_is_blocked() -> None:
    block = _factual("maintained Oracle clusters", [CLAIM], [EVIDENCE, SECOND_EVIDENCE])
    assert _codes(block) == ["statement_not_in_claim"]


def test_check_statements_other_claims_evidence_is_never_consulted() -> None:
    # The wording exists in another claim's evidence, not in the cited claim's own.
    block = _factual("ran Kubernetes upgrades", [CLAIM], [EVIDENCE, SECOND_EVIDENCE])
    assert _codes(block) == ["statement_not_in_claim"]


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
        ("statement_not_in_claim", "maintained PostgreSQL clusters [add metric]"),
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
def test_check_statements_altered_printable_text_is_statement_not_in_claim(
    text: str,
) -> None:
    assert _base_codes(text, ROLE_CLAIM, ROLE_EVIDENCE) == ["statement_not_in_claim"]


def test_check_statements_other_claims_printable_text_is_statement_not_in_claim() -> None:
    assert _base_codes("Python\nAdvanced", ROLE_CLAIM, ROLE_EVIDENCE) == ["statement_not_in_claim"]


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


LEAD_CLAIM = UUID("00000000-0000-4000-8000-000000000005")
LEAD_EVIDENCE = UUID("00000000-0000-4000-8000-000000000015")
LEAD_TEXT = "Assisted the team that led the billing migration (2021–2022), reducing cost by 20%"
LEAD_EVIDENCE_TEXT = (
    f"{LEAD_TEXT}. The programme ran 2019–2023, reducing cost by 45%, and was adopted company-wide."
)


def _lead_codes(statement: str, claim_verb: str | None) -> list[str]:
    facts = [
        {
            "claim_revision_id": str(LEAD_CLAIM),
            "evidence_revision_id": str(LEAD_EVIDENCE),
            "claim_text": LEAD_TEXT,
            "evidence_text": LEAD_EVIDENCE_TEXT,
            "claim_verb": claim_verb,
        }
    ]
    body = DocumentBody(
        title="Résumé",
        sections=[
            DocumentSection(
                key="experience",
                heading="Experience",
                blocks=[_factual(statement, [LEAD_CLAIM], [LEAD_EVIDENCE])],
            )
        ],
    )
    return [issue["code"] for issue in check_statements(body, facts, FRAME, "resume")]


@pytest.mark.parametrize("claim_verb", ["assisted", None], ids=["structured", "literal"])
@pytest.mark.parametrize(
    "statement",
    [
        "led the billing migration (2021–2022), reducing cost by 20%",
        "Led the billing migration",
        "the billing migration",
    ],
)
def test_check_statements_mid_claim_cut_is_blocked(statement: str, claim_verb: str | None) -> None:
    assert _lead_codes(statement, claim_verb) == ["statement_cut_inside_claim"]


@pytest.mark.parametrize("claim_verb", ["assisted", None], ids=["structured", "literal"])
@pytest.mark.parametrize(
    "statement",
    [
        "The programme ran 2019–2023",
        "reducing cost by 45%",
        "was adopted company-wide",
    ],
    ids=["longer_period", "larger_figure", "wider_scope"],
)
def test_check_statements_evidence_fragment_outside_the_claim_is_not_in_claim(
    statement: str, claim_verb: str | None
) -> None:
    assert contains_statement(statement, LEAD_EVIDENCE_TEXT)
    assert _lead_codes(statement, claim_verb) == ["statement_not_in_claim"]


@pytest.mark.parametrize("claim_verb", ["assisted", None], ids=["structured", "literal"])
@pytest.mark.parametrize("statement", [LEAD_TEXT, f"{LEAD_TEXT}.", LEAD_TEXT.upper()])
def test_check_statements_whole_claim_text_passes(statement: str, claim_verb: str | None) -> None:
    assert _lead_codes(statement, claim_verb) == []


@pytest.mark.parametrize("claim_verb", ["assisted", None], ids=["structured", "literal"])
@pytest.mark.parametrize(
    "statement",
    [
        "Assisted the team",
        "Assisted the team that led the billing migration",
        "the team that led the billing migration",
    ],
)
def test_check_statements_prefix_passes_even_with_later_verbs(
    statement: str, claim_verb: str | None
) -> None:
    assert _lead_codes(statement, claim_verb) == []


def _claim_codes(claim_text: str, statement: str, claim_verb: str | None = None) -> list[str]:
    facts = [
        {
            "claim_revision_id": str(CLAIM),
            "evidence_revision_id": str(EVIDENCE),
            "claim_text": claim_text,
            "evidence_text": claim_text,
            "claim_verb": claim_verb,
        }
    ]
    body = DocumentBody(
        title="Résumé",
        sections=[
            DocumentSection(
                key="experience",
                heading="Experience",
                blocks=[_factual(statement, [CLAIM], [EVIDENCE])],
            )
        ],
    )
    return [issue["code"] for issue in check_statements(body, facts, FRAME, "resume")]


@pytest.mark.parametrize(
    ("claim_text", "statement", "claim_verb"),
    [
        ("Co-led the billing migration with the platform team", "Led the billing migration", None),
        ("Was part of the team that led the billing migration", "Led the billing migration", None),
        (
            "Assisted the engineers who rewrote the billing engine",
            "Rewrote the billing engine",
            "assisted",
        ),
        (
            "Designed and implemented a self-service onboarding flow",
            "implemented a self-service onboarding flow",
            None,
        ),
        (
            "Designed & implemented a self-service onboarding flow",
            "implemented a self-service onboarding flow",
            "designed",
        ),
        (
            "Designed and implemented a self-service onboarding flow",
            "and implemented a self-service onboarding flow",
            None,
        ),
        (
            "Designed & implemented a self-service onboarding flow",
            "& implemented a self-service onboarding flow",
            "designed",
        ),
        # Space before the comma keeps this statement contained at its left boundary.
        (
            "Designed , implemented a self-service onboarding flow",
            ", implemented a self-service onboarding flow",
            None,
        ),
        (
            "Assisted implemented a self-service onboarding flow",
            "implemented a self-service onboarding flow",
            "assisted",
        ),
        ("Maintained custom-built schedulers", "Maintained custom", None),
        ("Maintained custom–built schedulers", "Maintained custom", "maintained"),
        ("Co-led the billing migration", "Co", None),
        ("Custom-built the billing engine", "the billing engine", None),
        ("Was responsible for the billing platform", "responsible for the billing platform", None),
        ("Assisted with the data warehouse migration", "with the data warehouse", "maintained"),
        # Strict remainder rule: "automated" is vocabulary even in this adjectival use.
        ("Wrote automated tests for the API", "automated tests for the API", None),
        (
            "Assisted with the data warehouse migration",
            "Assisted with the data warehouse",
            "assisted",
        ),
        ("Assisted with the data warehouse migration", "with the data warehouse", "assisted"),
        ("Assisted with the data warehouse migration", "the data warehouse", "assisted_with"),
        (
            "Designed and implemented a self-service onboarding flow",
            "Designed and implemented",
            None,
        ),
        ("Assisted custom-built scheduler work", "custom-built scheduler", "assisted"),
        ("Built the “Ｂilling” self–service platform", 'built the "billing" self-service', "built"),
        (
            "Assisted with the data warehouse migration",
            "Assisted with the data warehouse",
            "maintained",
        ),
    ],
)
def test_check_statements_rejects_non_prefix_or_ambiguous_post_verb_cuts(
    claim_text: str, statement: str, claim_verb: str | None
) -> None:
    assert contains_statement(statement, claim_text)
    assert _claim_codes(claim_text, statement, claim_verb) == ["statement_cut_inside_claim"]


@pytest.mark.parametrize(
    ("claim_text", "statement", "claim_verb"),
    [
        (
            "Assisted with the data warehouse migration",
            "Assisted with the data warehouse migration",
            "assisted",
        ),
        ("Wrote automated tests for the API", "Wrote automated tests", None),
        ("Wrote regression tests for the API", "regression tests for the API", None),
        ("Wrote regression tests for the API", "regression tests", "wrote"),
        (
            "Designed and implemented a self-service onboarding flow",
            "Designed and implemented a self-service onboarding flow",
            None,
        ),
        ("Maintained Linux batch runbooks for the payments team", "Linux batch runbooks", None),
        ("Contributed to the custom-built scheduler", "the custom-built scheduler", None),
        ("Was responsible for the billing platform", "the billing platform", None),
        ("SQL skills for analysts", "SQL skills", None),
        # A mismatched pinned verb disables only post-verb cuts, not valid claim prefixes.
        ("Assisted with the data warehouse migration", "Assisted", "maintained"),
    ],
)
def test_check_statements_accepts_claim_and_unambiguous_prefixes(
    claim_text: str, statement: str, claim_verb: str | None
) -> None:
    assert _claim_codes(claim_text, statement, claim_verb) == []


@pytest.mark.parametrize(
    ("claim_text", "statement"),
    [
        ("Built a 20% reduction", "Built a 20"),
        ("Built a $20M service", "Built a $20"),
        ("Built a 20.5M service", "Built a 20"),
        ("Built a 20,500-user service", "Built a 20"),
    ],
)
def test_check_statements_prefix_keeps_numeric_phrase_boundaries(
    claim_text: str, statement: str
) -> None:
    assert _claim_codes(claim_text, statement) == ["statement_not_in_claim"]


@pytest.mark.parametrize("claim_verb", OWNERSHIP_VERBS)
@pytest.mark.parametrize("structured", [False, True], ids=["literal", "structured"])
def test_check_statements_exact_claim_text_passes_for_every_rendered_verb(
    claim_verb: OwnershipVerb, structured: bool
) -> None:
    claim = StructuredClaim(schema_version=1, verb=claim_verb, subject="the billing migration")
    text = render_claim(claim)
    assert _claim_codes(text, text, claim_verb if structured else None) == []


@pytest.mark.parametrize("structured", [False, True], ids=["literal", "structured"])
@pytest.mark.parametrize("drop_verb", [False, True], ids=["claim", "post_verb"])
@pytest.mark.parametrize(
    ("claim_text", "statement"),
    [
        ("Managed the team's on-call rotation", "Managed the team"),
        ("Managed the team’s on-call rotation", "Managed the team"),
        ("Managed the teams' on-call rotation", "Managed the teams"),
        ("Managed the teams’ on-call rotation", "Managed the teams"),
        ("Owned the QA/release checklist", "Owned the QA"),
        ("Built the Node.js gateway", "Built the Node"),
        ("Led the migration planning but not delivery", "Led the migration"),
        ("Saved $2 million in fees", "Saved $2"),
        ("Reduced errors to 2 per 1,000 orders", "Reduced errors to 2"),
        ("Led the migration jointly with the platform lead", "Led the migration"),
    ],
)
def test_check_statements_blocks_mid_noun_figure_and_qualifier_endings(
    claim_text: str, statement: str, structured: bool, drop_verb: bool
) -> None:
    claim_verb = claim_text.split()[0].lower() if structured else None
    if drop_verb:
        statement = statement.partition(" ")[2]
    assert contains_statement(statement, claim_text)
    assert _claim_codes(claim_text, statement, claim_verb) == ["statement_cut_inside_claim"]


@pytest.mark.parametrize(
    "boundary",
    [
        "for",
        "by",
        "to",
        "on",
        "with",
        "in",
        "across",
        "from",
        "through",
        "during",
        "at",
        "that",
        ",",
        ";",
        ":",
        "(",
    ],
)
@pytest.mark.parametrize("claim_verb", [None, "built"], ids=["literal", "structured"])
@pytest.mark.parametrize("statement", ["Built the API", "the API"], ids=["claim", "post_verb"])
def test_check_statements_accepts_only_declared_prefix_end_boundaries(
    boundary: str, claim_verb: str | None, statement: str
) -> None:
    text = f"Built the API {boundary.upper()} the billing team"
    assert _claim_codes(text, statement, claim_verb) == []


@pytest.mark.parametrize("suffix", ["forward", "within", "throughput", "that-team", "for-profit"])
@pytest.mark.parametrize("statement", ["Built the API", "the API"])
def test_check_statements_boundary_words_must_be_whole_non_hyphenated_words(
    suffix: str, statement: str
) -> None:
    assert _claim_codes(f"Built the API {suffix}", statement, "built") == [
        "statement_cut_inside_claim"
    ]


@pytest.mark.parametrize("claim_verb", [None, "reduced"])
def test_check_statements_accepts_boundary_word_qualifier_drop_as_documented_limitation(
    claim_verb: str | None,
) -> None:
    assert (
        _claim_codes(
            "Reduced latency by 30% for one pilot region", "Reduced latency by 30%", claim_verb
        )
        == []
    )
    assert _claim_codes("Built the API for the billing team", "Built the API", "built") == []
    assert _claim_codes("Built the API ＦＯＲ the billing team", "the API", "built") == []
    assert _claim_codes("Built the API.", "Built the API.", "built") == []


@pytest.mark.parametrize(
    "word",
    ["for", "by", "to", "on", "with", "in", "across", "from", "through", "during", "at", "that"],
)
@pytest.mark.parametrize("claim_verb", [None, "supported"], ids=["literal", "structured"])
@pytest.mark.parametrize("statement", ["Supported 10", "10"], ids=["claim", "post_verb"])
def test_check_statements_boundary_words_cannot_split_numeric_ranges(
    word: str, claim_verb: str | None, statement: str
) -> None:
    claim_text = f"Supported 10 {word.upper()} 20 clients"
    assert contains_statement(statement, claim_text)
    assert _claim_codes(claim_text, statement, claim_verb) == ["statement_cut_inside_claim"]


@pytest.mark.parametrize(
    ("claim_text", "statement"),
    [
        ("Cut costs by 10 to 15%", "Cut costs by 10"),
        ("Supported 10 to 20 clients", "Supported 10"),
        ("Managed $10 to $20 budgets", "Managed $10"),
        ("Managed € 10 to € 20 budgets", "Managed € 10"),
        ("Managed 10 € to 20 € budgets", "Managed 10 €"),
        ("Managed $1,000.50 to $2,000.75 budgets", "Managed $1,000.50"),
        ("Reduced latency by 30% for 1 pilot region", "Reduced latency by 30%"),
        ("Measured -10.5 to +20.5 degrees", "Measured -10.5"),
    ],
)
def test_check_statements_number_and_currency_ranges_are_not_prefix_endpoints(
    claim_text: str, statement: str
) -> None:
    assert contains_statement(statement, claim_text)
    assert _claim_codes(claim_text, statement) == ["statement_cut_inside_claim"]


@pytest.mark.parametrize("claim_verb", [None, "supported"])
@pytest.mark.parametrize("statement", ["Supported 10 clients", "10 clients"])
def test_check_statements_number_followed_by_noun_still_has_a_boundary(
    claim_verb: str | None, statement: str
) -> None:
    assert _claim_codes("Supported 10 clients to 20 projects", statement, claim_verb) == []


@pytest.mark.parametrize("separator", [". ", " - ", " – ", " — ", ".\n", "\t—\t"])
@pytest.mark.parametrize("claim_verb", [None, "led"])
@pytest.mark.parametrize("statement", ["Led the migration", "the migration"])
def test_check_statements_clause_full_stop_and_spaced_dash_are_prefix_endpoints(
    separator: str, claim_verb: str | None, statement: str
) -> None:
    assert (
        _claim_codes(f"Led the migration{separator}Cut costs by 10%", statement, claim_verb) == []
    )


@pytest.mark.parametrize("separator", [".", "-", " —", "— "])
def test_check_statements_unspaced_clause_punctuation_does_not_split_compounds(
    separator: str,
) -> None:
    assert _claim_codes(f"Led the migration{separator}cutting costs", "Led the migration") == [
        "statement_cut_inside_claim"
    ]


@pytest.mark.parametrize(
    "ending",
    [
        " – 15%",
        " — 15%",
        " - 15%",
        " (15%)",
        ", 15% overall",
        ". 15% overall",
        "; 15% overall",
        ": 15% overall",
    ],
)
@pytest.mark.parametrize("claim_verb", [None, "cut"], ids=["literal", "structured"])
@pytest.mark.parametrize("statement", ["Cut costs by 10", "costs by 10"])
def test_check_statements_every_endpoint_kind_preserves_numeric_ranges(
    ending: str, claim_verb: str | None, statement: str
) -> None:
    claim_text = f"Cut costs by 10{ending}"
    assert contains_statement(statement, claim_text)
    assert _claim_codes(claim_text, statement, claim_verb) == ["statement_cut_inside_claim"]


@pytest.mark.parametrize("ending", [" – 15%", " (15%)", ", 15% overall", ". 15% overall"])
def test_check_statements_punctuation_endpoints_preserve_currency_ranges(ending: str) -> None:
    claim_text = f"Managed $10{ending}"
    assert _claim_codes(claim_text, "Managed $10", "managed") == ["statement_cut_inside_claim"]
