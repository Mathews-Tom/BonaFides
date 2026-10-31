# SPDX-License-Identifier: MIT OR Apache-2.0
"""What an evidence source is, how it entered BonaFides and who may read its content.

Every revision records immutable source type and origin. Current privacy and employer
ownership belong to the record, independently of content revisions and claim pins.
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
    """Immutable source information supplied for a content revision."""

    source_type: SourceType


class EvidenceClassification(StrictModel):
    """The owner's current classification of an evidence record."""

    privacy_class: PrivacyClass
    employer_owned: StrictBool


class InitialEvidenceProvenance(EvidenceProvenance, EvidenceClassification):
    """Source information and initial classification when creating a record."""


def provenance(row: RowMapping) -> dict[str, object]:
    """Revision provenance with the evidence record's current classification."""
    return {
        "source_type": row["source_type"],
        "origin": row["origin"],
        "privacy_class": row["privacy_class"],
        "employer_owned": row["employer_owned"],
    }
