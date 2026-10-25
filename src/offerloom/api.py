# SPDX-License-Identifier: MIT OR Apache-2.0
"""Composition root. CLI imports are secret-free; app creation validates config."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from offerloom import __version__, auth, profiles
from offerloom.config import Settings
from offerloom.db import Database
from offerloom.import_parser import MAX_UPLOAD_BYTES
from offerloom.problems import Problem, problem_response

_VALIDATION_FIELDS = frozenset(
    {
        "body",
        "query",
        "path",
        "payload",
        "text",
        "source",
        "kind",
        "display_name",
        "headline",
        "claim_revision_ids",
        "evidence_revision_ids",
        "organization",
        "role",
        "start_date",
        "end_date",
        "proficiency",
        "evidence_links",
        "evidence_revision_id",
        "support_kind",
        "excerpt",
        "revision_id",
        "before",
        "claim_id",
        "evidence_id",
        "import_id",
        "decisions",
        "proposal_id",
        "action",
        "mode",
        "replace_all_confirmed",
        "title",
        "company",
        "location_text",
        "description_text",
        "canonical_url",
        "source_kind",
        "board_token",
        "terms_url",
        "terms_revision",
        "terms_review_acknowledged",
        "enabled",
        "request_limit_per_minute",
        "cursor",
        "limit",
        "source_id",
        "posting_id",
        "observation_id",
        "confirmed",
        "requirements",
        "requirement_key",
        "importance",
        "span",
        "start",
        "end",
        "quote",
        "hard_constraint",
        "operator",
        "required_value",
        "owner_value",
        "jurisdiction",
        "owner_jurisdiction",
        "currency",
        "owner_currency",
        "period",
        "owner_period",
        "owner_confirmed",
        "contradictory",
        "profile_revision_id",
        "requirements_revision_id",
        "assessments",
        "coverage",
        "explanation",
        "login_name",
        "password",
        "current_password",
        "new_password",
        "bootstrap_token",
        "to_stage",
        "effective_at",
        "reason",
        "correction_of",
        "retain_follow_up_ids",
        "archived",
        "stage",
        "period_start",
        "period_end",
        "email",
        "role_label",
        "local_start",
        "local_end",
        "timezone",
        "utc_offset_minutes",
        "end_utc_offset_minutes",
        "status",
        "payload_sha256",
        "destination_sha256",
        "target_version",
    }
)
_VALIDATION_MESSAGES = {
    "missing": "This field is required.",
    "string_too_long": "This text is too long.",
    "string_too_short": "This text is too short.",
    "string_type": "Enter text.",
    "list_type": "Provide a list.",
    "too_long": "Too many items.",
    "too_short": "At least one item is required.",
    "literal_error": "Choose a supported value.",
    "extra_forbidden": "An unrecognized field was supplied.",
    "json_invalid": "The request is not valid JSON.",
}


class BrowserBoundary:
    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        request.state.request_id = uuid4()
        request_id = str(request.state.request_id)
        started = False

        async def secured_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                headers = list(message.get("headers", []))
                protected = {
                    b"cache-control",
                    b"x-request-id",
                    b"content-security-policy",
                    b"x-content-type-options",
                    b"referrer-policy",
                    b"permissions-policy",
                }
                headers = [(k, v) for k, v in headers if k.lower() not in protected]
                headers.extend(
                    [
                        (b"cache-control", b"no-store"),
                        (b"x-request-id", request_id.encode("ascii")),
                        (b"x-content-type-options", b"nosniff"),
                        (b"referrer-policy", b"no-referrer"),
                        (
                            b"permissions-policy",
                            b"camera=(), microphone=(), geolocation=(), payment=(), usb=()",
                        ),
                        (
                            b"content-security-policy",
                            b"default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'; object-src 'none'",
                        ),
                    ]
                )
                if self.settings.origin.startswith("https://"):
                    headers.append((b"strict-transport-security", b"max-age=31536000"))
                message["headers"] = headers
            await send(message)

        async def reject(problem: Problem) -> None:
            await problem_response(request, problem)(scope, receive, secured_send)

        expected_host = urlsplit(self.settings.origin).netloc
        is_import = request.method == "POST" and scope["path"] == "/api/v1/imports"
        is_attachment = (
            request.method == "POST"
            and re.fullmatch(
                r"/api/v1/applications/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/attachments",
                scope["path"],
            )
            is not None
        )
        body_limit = (
            MAX_UPLOAD_BYTES if is_import or is_attachment else self.settings.max_body_bytes
        )
        if request.headers.get("host", "").lower() != expected_host.lower():
            await reject(Problem(400, "host_rejected"))
            return
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if request.headers.get("origin") != self.settings.origin:
                await reject(Problem(403, "origin_rejected"))
                return
            content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if request.method in {"POST", "PUT", "PATCH"}:
                allowed = (
                    {"application/pdf", "text/plain"}
                    if is_import
                    else {"application/octet-stream"}
                    if is_attachment
                    else {"application/json"}
                )
                if content_type not in allowed:
                    await reject(
                        Problem(
                            415,
                            "unsupported_import_media"
                            if is_import
                            else "unsupported_attachment_media"
                            if is_attachment
                            else "json_required",
                        )
                    )
                    return
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                length = int(content_length)
            except ValueError:
                await reject(Problem(400, "invalid_content_length"))
                return
            if length < 0 or length > body_limit:
                await reject(Problem(413, "body_too_large"))
                return
        # Bound all received chunks; Content-Length alone is not an admission check.
        chunks: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > body_limit:
                await reject(Problem(413, "body_too_large"))
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        delivered = False

        async def bounded_receive() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        try:
            await self.app(scope, bounded_receive, secured_send)
        except Exception:
            if started:
                raise
            await reject(Problem(500, "internal_error"))


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings if settings is not None else Settings.load()
    db = Database(resolved)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if not db.ready():
            raise RuntimeError("Database schema readiness failed")
        yield
        db.close()

    app = FastAPI(
        title="Offerloom M0 proof API",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/v1/openapi.json",
    )
    app.state.settings = resolved
    app.state.db = db
    app.add_middleware(BrowserBoundary, settings=resolved)

    @app.exception_handler(Problem)
    async def handle_problem(request: Request, exc: Problem) -> JSONResponse:
        return problem_response(request, exc)

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Never return input, context, validator messages, or unknown field names.
        fields: list[dict[str, object]] = []
        for error in exc.errors():
            error_type = error["type"]
            fields.append(
                {
                    "loc": [
                        part if isinstance(part, int) or part in _VALIDATION_FIELDS else "field"
                        for part in error["loc"]
                    ],
                    "code": error_type if error_type in _VALIDATION_MESSAGES else "invalid_value",
                    "message": _VALIDATION_MESSAGES.get(error_type, "Check this field's value."),
                }
            )
        return problem_response(request, Problem(422, "invalid_request"), fields=fields)

    @app.exception_handler(HTTPException)
    async def handle_http(request: Request, exc: HTTPException) -> JSONResponse:
        return problem_response(
            request,
            Problem(exc.status_code, "not_found" if exc.status_code == 404 else "http_error"),
        )

    @app.exception_handler(SQLAlchemyError)
    async def handle_database(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        return problem_response(request, Problem(503, "database_unavailable"))

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        return problem_response(request, Problem(500, "internal_error"))

    @app.get("/health/live")
    def live() -> dict[str, object]:
        return {"status": "live", "version": __version__}

    @app.get("/health/ready")
    def ready() -> Response:
        try:
            available = db.ready()
        except SQLAlchemyError:
            available = False
        return JSONResponse(
            {
                "status": "ready" if available else "unavailable",
                "schema_version": 13,
                "version": __version__,
            },
            status_code=200 if available else 503,
        )

    app.include_router(auth.build_router(db, resolved))
    app.include_router(profiles.build_router(db, resolved))
    from offerloom import approvals, documents, imports, sources, suitability, tracking, workflows

    app.include_router(workflows.build_router(db, resolved))
    app.include_router(approvals.build_router(db))
    app.include_router(documents.build_router(db, resolved))
    app.include_router(imports.build_router(db, resolved))
    app.include_router(sources.build_router(db, resolved))
    app.include_router(suitability.build_router(db, resolved))
    app.include_router(tracking.build_router(db, resolved))
    if (resolved.web_dist / "index.html").is_file():
        app.mount("/", StaticFiles(directory=resolved.web_dist, html=True), name="ui")
    return app
