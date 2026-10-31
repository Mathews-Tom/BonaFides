# SPDX-License-Identifier: MIT OR Apache-2.0
"""What an evidence source is, how it entered BonaFides and who may read its content.

Every evidence revision records its source type, origin, privacy class and whether
its employer owns the content. The owner chooses the source type, privacy class and
employer ownership; BonaFides sets the origin from the path that saved the revision.
"""

from __future__ import annotations

from typing import Literal

from pydantic import StrictBool
from sqlalchemy.engine import RowMapping

from bonafides.auth import StrictModel

SourceType = Literal[
    "owner_statement", "document", "work_sample", "web_page", "credential", "other"
]
Origin = Literal["owner_entry", "resume_import"]
PrivacyClass = Literal["public", "private", "confidential"]


class EvidenceProvenance(StrictModel):
    """The owner's classification of one evidence revision."""

    source_type: SourceType
    privacy_class: PrivacyClass
    employer_owned: StrictBool


def provenance(row: RowMapping) -> dict[str, object]:
    """The provenance columns of one evidence revision row."""
    return {
        "source_type": row["source_type"],
        "origin": row["origin"],
        "privacy_class": row["privacy_class"],
        "employer_owned": row["employer_owned"],
    }
