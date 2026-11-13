# SPDX-License-Identifier: MIT OR Apache-2.0
"""Strict, independently versioned document-conversation model replies."""

from __future__ import annotations

from typing import Annotated, Literal, cast
from uuid import UUID

from pydantic import Field, model_validator

from bonafides.documents import DocumentBlock, Key, StrictModel

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ConversationEdit(StrictModel):
    document_id: UUID
    section_key: Key
    block_key: Key
    previous_sha256: Sha256
    replacement: DocumentBlock

    @model_validator(mode="after")
    def stable_key(self) -> ConversationEdit:
        if self.replacement.block_key != self.block_key:
            raise ValueError("conversation_replacement_key_changed")
        return self


class ConversationReply(StrictModel):
    schema_version: Literal["1"]
    stage: Literal["refine_documents"]
    reply_text: str = Field(min_length=1, max_length=2000)
    declined: bool
    tone: Literal["professional", "concise"] | None
    edits: list[ConversationEdit] = Field(max_length=256)

    @model_validator(mode="after")
    def coherent_reply(self) -> ConversationReply:
        if not self.reply_text.strip():
            raise ValueError("conversation_reply_empty")
        if self.declined and (self.edits or self.tone is not None):
            raise ValueError("conversation_decline_has_changes")
        seen: set[tuple[UUID, str, str]] = set()
        unique: list[ConversationEdit] = []
        for edit in self.edits:
            identity = (edit.document_id, edit.section_key, edit.block_key)
            if identity not in seen:
                seen.add(identity)
                unique.append(edit)
        object.__setattr__(self, "edits", unique)
        return self


def validate_reply(output: dict[str, object], frozen: dict[str, object]) -> ConversationReply:
    """Check selected-unit replacements against the original document's pinned facts."""
    from bonafides import document_generation as generation
    from bonafides import documents, model_calls

    candidate = generation.parse_candidate(output, ConversationReply)
    targets = {
        (str(item["document_id"]), item["section_key"], item["block_key"]): item
        for item in cast(list[dict[str, object]], frozen["targets"])
    }
    for document in cast(list[dict[str, object]], frozen["documents"]):
        replacements = {}
        for edit in candidate.edits:
            key = (str(edit.document_id), edit.section_key, edit.block_key)
            if key not in targets or key[0] != document["document_id"]:
                continue
            if edit.previous_sha256 != targets[key]["previous_sha256"]:
                raise model_calls.OutputMismatch(["edits: previous_content_mismatch"])
            before = cast(dict[str, object], targets[key]["before"])
            replacement = edit.replacement.model_dump(mode="json")
            if any(
                replacement[name] != before[name]
                for name in ("claim_kind", "claim_revision_ids", "evidence_revision_ids")
            ):
                raise model_calls.OutputMismatch(["edits: changed_source_annotations"])
            replacements[(edit.section_key, edit.block_key)] = edit.replacement
        if not replacements:
            continue
        body = documents.parse_body(document["body"])
        try:
            proposed = documents.DocumentBody(
                title=body.title,
                sections=[
                    section.model_copy(
                        update={
                            "blocks": [
                                replacements.get((section.key, block.block_key), block)
                                for block in section.blocks
                            ]
                        }
                    )
                    for section in body.sections
                ],
            )
        except ValueError as exc:
            raise model_calls.OutputMismatch(["edits: invalid_document_body"]) from exc
        paths = {
            f"/sections/{si}/blocks/{bi}"
            for si, section in enumerate(proposed.sections)
            for bi, block in enumerate(section.blocks)
            if (section.key, block.block_key) in replacements
        }
        issues = generation.validation_issues(
            proposed,
            cast(dict[str, object], document["provenance_snapshot"]),
            str(document["kind"]),
        )
        if any(issue["path"] in paths for issue in issues):
            raise model_calls.OutputMismatch(["edits: unsupported_document_statement"])
    return candidate
