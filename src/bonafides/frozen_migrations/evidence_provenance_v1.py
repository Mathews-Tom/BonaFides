# SPDX-License-Identifier: MIT OR Apache-2.0
"""Release-fixed provenance of evidence revisions saved before schema 29.

Imports of portable archives written before schema 29 use this standalone copy of the
values the schema 29 upgrade assigns. Later application changes must never change it.
No live application modules may be imported here.
"""

from __future__ import annotations

from collections.abc import Mapping

EVIDENCE_PROVENANCE_SCHEMA = 29
PROVENANCE_COLUMNS = ("source_type", "origin", "privacy_class", "employer_owned")


def legacy_provenance(payload: Mapping[str, object]) -> dict[str, str | bool]:
    """Literal résumé imports name their import; every other revision is an owner entry.

    None was classified, so each stays private and not employer-owned and reaches model
    context exactly as it did before the upgrade.
    """
    imported = "import_id" in payload
    return {
        "source_type": "document" if imported else "owner_statement",
        "origin": "resume_import" if imported else "owner_entry",
        "privacy_class": "private",
        "employer_owned": False,
    }
