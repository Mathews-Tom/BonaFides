# SPDX-License-Identifier: MIT OR Apache-2.0
"""The equivalence normalisation never hides a schema difference."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.schema_equivalence import EquivalenceError, baseline, compare, normalise

REFERENCE = Path("migrations/reference/schema-030.sql")
DUMP = """--
-- PostgreSQL database dump
--

\\restrict AbC123

-- Dumped from database version 18.6
-- Dumped by pg_dump version 18.6

SET statement_timeout = 0;
SELECT pg_catalog.set_config('search_path', '', false);

--
-- Name: guard(); Type: FUNCTION; Schema: bonafides; Owner: bonafides_migrator
--

CREATE FUNCTION bonafides.guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ BEGIN
 -- keep this comment

 RETURN NEW;
END $$;


ALTER FUNCTION bonafides.guard() OWNER TO bonafides_migrator;

--
-- Name: owners; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator
--

CREATE TABLE bonafides.owners (
    id uuid NOT NULL,
    CONSTRAINT owners_login_check CHECK ((length(login_name) <= 120))
);


ALTER TABLE bonafides.owners OWNER TO bonafides_migrator;

--
-- Name: TABLE owners; Type: ACL; Schema: bonafides; Owner: bonafides_migrator
--

GRANT SELECT ON TABLE bonafides.owners TO bonafides_runtime;

--
-- PostgreSQL database dump complete
--

\\unrestrict AbC123
"""


def _entries(dump: str) -> list[str]:
    return dump.split("\n--\n-- Name: ")


def test_normalise_reordered_entries_and_new_restrict_key_compare_equal() -> None:
    head, *entries = _entries(DUMP)
    reordered = "\n--\n-- Name: ".join([head.replace("AbC123", "Zz9"), *reversed(entries)])

    assert compare(normalise(DUMP), normalise(reordered)) == []


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("length(login_name) <= 120", "length(login_name) <= 121"),
        ("GRANT SELECT ON", "GRANT SELECT,INSERT ON"),
        (
            "OWNER TO bonafides_migrator;\n\n--\n-- Name: TABLE",
            "OWNER TO postgres;\n\n--\n-- Name: TABLE",
        ),
        (" -- keep this comment\n\n RETURN", " -- keep this comment\n RETURN"),
        ("    id uuid NOT NULL,\n", "    id uuid,\n"),
    ],
)
def test_compare_injected_difference_reports_diff(before: str, after: str) -> None:
    assert before in DUMP

    difference = compare(normalise(DUMP), normalise(DUMP.replace(before, after, 1)))

    assert difference


def test_normalise_statement_outside_object_entry_raises() -> None:
    with pytest.raises(EquivalenceError):
        normalise("SET x = 1;\nCREATE TABLE bonafides.loose (id int);\n" + DUMP)


def test_baseline_drops_frames_and_owner_keeps_function_body() -> None:
    sql = baseline(DUMP)

    assert "OWNER TO" not in sql and "restrict" not in sql and "-- Name:" not in sql
    assert " -- keep this comment\n\n RETURN NEW;" in sql
    assert "GRANT TEMPORARY ON DATABASE" in sql


def test_baseline_foreign_owner_raises() -> None:
    with pytest.raises(EquivalenceError):
        baseline(DUMP.replace("OWNER TO bonafides_migrator", "OWNER TO postgres"))


def test_reference_injected_difference_in_committed_dump_reports_diff() -> None:
    reference = REFERENCE.read_text()
    altered = reference.replace("GRANT SELECT", "GRANT SELECT,UPDATE", 1)

    assert compare(reference, reference) == []
    assert compare(reference, altered)
