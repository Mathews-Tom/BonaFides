# SPDX-License-Identifier: MIT OR Apache-2.0
"""Owner-local selections for future documents, not document or permission mutations."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import Connection, text
from sqlalchemy.engine import RowMapping

from offerloom.auth import Owner, require_owner
from offerloom.commands import canonical_json, command
from offerloom.db import Database
from offerloom.document_frame import TEMPLATES
from offerloom.problems import Problem


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    template_version: str = Field(min_length=1, max_length=100)
    tone: Literal["professional", "concise"]

    @field_validator("template_version")
    @classmethod
    def supported_template(cls, value: str) -> str:
        if value not in TEMPLATES:
            raise ValueError("unsupported_document_template")
        return value


class PatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    template_version: str | None = Field(default=None, min_length=1, max_length=100)
    tone: Literal["professional", "concise"] | None = None

    @field_validator("template_version")
    @classmethod
    def supported_template(cls, value: str | None) -> str | None:
        if value is not None and value not in TEMPLATES:
            raise ValueError("unsupported_document_template")
        return value

    @model_validator(mode="after")
    def actual_changes(self) -> PatchRequest:
        if not self.model_fields_set or any(
            getattr(self, field) is None for field in self.model_fields_set
        ):
            raise ValueError("document_defaults_changes_required")
        return self


def _settings(conn: Connection, owner: UUID) -> RowMapping:
    row = (
        conn.execute(
            text(
                "SELECT settings,workspace_version FROM offerloom.owner_settings WHERE owner_id=:owner"
            ),
            {"owner": owner},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise Problem(404, "owner_not_found")
    return row


def _selection(settings: object) -> Selection:
    if not isinstance(settings, dict):
        raise Problem(409, "invalid_document_defaults")
    if "document_defaults" not in settings:
        return Selection(template_version="offerloom-evidence-v1", tone="professional")
    try:
        return Selection.model_validate(settings["document_defaults"])
    except ValidationError as exc:
        # Corrupt or obsolete stored values require explicit repair, not a silent reset.
        raise Problem(409, "invalid_document_defaults") from exc


def _view(selection: Selection, version: int) -> dict[str, object]:
    from offerloom.task_retention import RETENTION_SECONDS

    return {
        "version": version,
        **selection.model_dump(mode="json"),
        "language": "en",
        "revision_retention": "until_owner_erasure",
        "task_input_retention_seconds": RETENTION_SECONDS,
        "temporary_export_retention_seconds": 24 * 60 * 60,
        "extension_supported": False,
    }


def _response(status: int, result: dict[str, object]) -> Response:
    return Response(
        canonical_json(result),
        status_code=status,
        media_type="application/json",
        headers={"Cache-Control": "private, no-store", "ETag": f'"{result["version"]}"'},
    )


def install_routes(router: APIRouter, db: Database) -> None:
    @router.get("/document-defaults")
    def get_defaults(owner: Annotated[Owner, Depends(require_owner)]) -> Response:
        with db.transaction() as conn:
            row = _settings(conn, owner.id)
            result = _view(_selection(row["settings"]), int(row["workspace_version"]))
        return _response(200, result)

    @router.patch("/document-defaults")
    def patch_defaults(
        body: PatchRequest, request: Request, owner: Annotated[Owner, Depends(require_owner)]
    ) -> Response:
        changes = body.model_dump(mode="json", exclude_unset=True)
        with db.transaction() as conn:

            def effect(active: Connection) -> tuple[int, dict[str, object]]:
                row = _settings(active, owner.id)
                selected = Selection.model_validate(
                    {**_selection(row["settings"]).model_dump(mode="json"), **changes}
                )
                active.execute(
                    text("""UPDATE offerloom.owner_settings
                    SET settings=jsonb_set(settings,'{document_defaults}',CAST(:selection AS jsonb))
                    WHERE owner_id=:owner"""),
                    {
                        "owner": owner.id,
                        "selection": canonical_json(selected.model_dump(mode="json")),
                    },
                )
                # command owns the workspace bump; permission/settings-policy versions stay pinned.
                return 200, _view(selected, int(row["workspace_version"]) + 1)

            status, result = command(
                conn,
                owner.id,
                "PATCH",
                "/document-defaults",
                {},
                changes,
                request.headers.get("if-match"),
                request.headers.get("idempotency-key"),
                "workspace",
                owner.id,
                effect,
            )
        return _response(status, result)
