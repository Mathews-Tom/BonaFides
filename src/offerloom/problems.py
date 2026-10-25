# SPDX-License-Identifier: MIT OR Apache-2.0
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse


class Problem(Exception):
    def __init__(self, status: int, code: str, detail: str | None = None) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.detail = detail


def problem_response(
    request: Request, problem: Problem, *, fields: list[dict[str, object]] | None = None
) -> JSONResponse:
    body: dict[str, object] = {
        "type": f"urn:offerloom:problem:{problem.code}",
        "title": problem.code.replace("_", " "),
        "status": problem.status,
        "code": problem.code,
        "request_id": str(getattr(request.state, "request_id", "")),
    }
    if problem.detail is not None:
        body["detail"] = problem.detail
    if fields is not None:
        body["fields"] = fields
    return JSONResponse(
        body,
        status_code=problem.status,
        media_type="application/problem+json",
        headers={"Cache-Control": "no-store"},
    )
