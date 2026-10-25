# SPDX-License-Identifier: MIT OR Apache-2.0
"""Typed spool transport to a separately deployed, no-network PDF renderer.

The container is the OS security boundary, not the URL fetcher or Python child.
The renderer daemon has no database, keyring, blob mount, or production debug API.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import Field, model_validator

from offerloom.document_frame import TEMPLATES, PrintableFrame, TemplateVersion, template_digest
from offerloom.documents import TEMPLATE_VERSION, DocumentBody, StrictModel, canonical
from offerloom.storage import MAX_PDF_BYTES, atomic_file, directory, read_file

MAX_REQUEST_BYTES = 1024 * 1024
RENDER_TIMEOUT = 30
_JOB = re.compile(r"[0-9a-f]{64}\Z")


class RenderFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class RenderRequest(StrictModel):
    schema_version: Literal[1]
    template_version: TemplateVersion
    body: DocumentBody
    frame: PrintableFrame | None = None
    template_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None

    @model_validator(mode="after")
    def pinned_frame(self) -> RenderRequest:
        if (self.template_version == TEMPLATE_VERSION) != (self.frame is None):
            raise ValueError("render_frame_template_mismatch")
        if self.template_version == TEMPLATE_VERSION:
            if self.template_sha256 is not None:
                raise ValueError("render_frame_template_mismatch")
        elif self.template_sha256 != template_digest(self.template_version):
            raise ValueError("render_template_digest_mismatch")
        return self


class RenderReceipt(StrictModel):
    schema_version: Literal[1]
    job_id: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    request_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    output_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None
    output_bytes: Annotated[int, Field(ge=0, le=MAX_PDF_BYTES)]
    error_code: (
        Literal["render_timeout", "render_failed", "render_invalid_input", "render_output_invalid"]
        | None
    )


def deny_url_fetcher(url: str, *args: object, **kwargs: object) -> object:
    del url, args, kwargs
    raise ValueError("renderer_url_denied")


def render_html(
    body: DocumentBody,
    template_version: str = TEMPLATE_VERSION,
    frame: PrintableFrame | None = None,
) -> str:
    from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape

    environment = Environment(
        loader=PackageLoader("offerloom", "templates"),
        autoescape=select_autoescape(default=True),
        undefined=StrictUndefined,
    )
    if template_version == TEMPLATE_VERSION:
        if frame is not None:
            raise RenderFailure("render_invalid_input")
        return environment.get_template("base-v1.html").render(document=body)
    if template_version not in TEMPLATES or frame is None:
        raise RenderFailure("render_invalid_input")
    return environment.get_template(TEMPLATES[template_version][1]).render(
        document=body, frame=frame
    )


def render_pdf(
    body: DocumentBody,
    template_version: str = TEMPLATE_VERSION,
    frame: PrintableFrame | None = None,
) -> bytes:
    """Only called by the bounded child in the sealed renderer container."""
    from weasyprint import HTML  # type: ignore[import-untyped]

    logger = logging.getLogger("weasyprint")
    logger.handlers = [logging.NullHandler()]
    logger.propagate = False
    data = HTML(
        string=render_html(body, template_version, frame), url_fetcher=deny_url_fetcher
    ).write_pdf(pdf_forms=False, attachments=[], custom_metadata=False)
    if (
        not isinstance(data, bytes)
        or not data.startswith(b"%PDF-")
        or not 0 < len(data) <= MAX_PDF_BYTES
    ):
        raise RenderFailure("render_output_invalid")
    return data


class RenderClient:
    def __init__(self, spool: Path, *, timeout: float = 40.0) -> None:
        if not 0 < timeout <= 60:
            raise ValueError("invalid_renderer_timeout")
        self.spool = spool
        self.timeout = timeout

    def render(
        self,
        body: DocumentBody,
        template_version: str = TEMPLATE_VERSION,
        frame: PrintableFrame | None = None,
        template_sha256: str | None = None,
    ) -> bytes:
        request = RenderRequest.model_validate(
            {
                "schema_version": 1,
                "template_version": template_version,
                "template_sha256": template_sha256,
                "body": body,
                "frame": frame,
            }
        )
        payload = canonical(request.model_dump(mode="json", exclude_none=True)).encode()
        if len(payload) > MAX_REQUEST_BYTES:
            raise RenderFailure("render_invalid_input")
        digest = hashlib.sha256(payload).hexdigest()
        job_id = hashlib.sha256(uuid4().bytes + bytes.fromhex(digest)).hexdigest()
        with directory(self.spool, create=True) as fd:
            # Admission is bounded even if a renderer is stopped. Only opaque IDs occur.
            queued = sum(1 for name in os.listdir(fd) if name.endswith(".request.json"))
            if queued >= 32:
                raise RenderFailure("renderer_busy")
            atomic_file(fd, f"{job_id}.request.json", payload)
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                try:
                    raw = read_file(fd, f"{job_id}.result.json", 4096)
                except FileNotFoundError:
                    time.sleep(0.1)
                    continue
                try:
                    receipt = RenderReceipt.model_validate_json(raw, strict=True)
                    if receipt.job_id != job_id or receipt.request_sha256 != digest:
                        raise RenderFailure("render_output_invalid")
                    if receipt.error_code:
                        raise RenderFailure(receipt.error_code)
                    output = read_file(fd, f"{job_id}.pdf", MAX_PDF_BYTES)
                    if (
                        len(output) != receipt.output_bytes
                        or hashlib.sha256(output).hexdigest() != receipt.output_sha256
                        or not output.startswith(b"%PDF-")
                    ):
                        raise RenderFailure("render_output_invalid")
                    return output
                finally:
                    for suffix in ("request.json", "result.json", "pdf"):
                        with contextlib.suppress(FileNotFoundError):
                            os.unlink(f"{job_id}.{suffix}", dir_fd=fd)
            # Do not race an active child by unlinking its input. The daemon's bounded
            # retention sweep removes abandoned request/result pairs after one hour.
            raise RenderFailure("render_timeout")


def _child_limits() -> None:
    import resource

    os.umask(0o077)
    resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_PDF_BYTES, MAX_PDF_BYTES))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    # The container's pids.max=32 bounds this renderer, unlike cross-container UID NPROC.
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _render_child(spool: Path, job_id: str) -> None:
    _child_limits()
    if not _JOB.fullmatch(job_id):
        raise RenderFailure("render_invalid_input")
    with directory(spool) as fd:
        raw = read_file(fd, f"{job_id}.request.json", MAX_REQUEST_BYTES)
        request = RenderRequest.model_validate_json(raw, strict=True)
        output = render_pdf(request.body, request.template_version, request.frame)
        atomic_file(fd, f"{job_id}.pdf", output)


def _supervise(spool: Path, fd: int, job_id: str) -> None:
    raw = read_file(fd, f"{job_id}.request.json", MAX_REQUEST_BYTES)
    digest = hashlib.sha256(raw).hexdigest()
    error: (
        Literal["render_timeout", "render_failed", "render_invalid_input", "render_output_invalid"]
        | None
    ) = None
    output_digest: str | None = None
    output_size = 0
    try:
        RenderRequest.model_validate_json(raw, strict=True)
    except ValueError:
        error = "render_invalid_input"
    if error is None:
        # No inherited DSN/token/keyring/environment. The deployment additionally
        # supplies read-only root, no secrets mounts, network:none and cgroup limits.
        environment = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "HOME": "/tmp",
            "XDG_CACHE_HOME": "/tmp/font-cache",
            "TMPDIR": "/tmp",
        }
        child = subprocess.Popen(
            [sys.executable, "-m", "offerloom.render_service", "child", str(spool), job_id],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
            start_new_session=True,
            close_fds=True,
        )
        try:
            try:
                child.wait(timeout=RENDER_TIMEOUT)
            except subprocess.TimeoutExpired:
                error = "render_timeout"
            if error is None and child.returncode != 0:
                error = "render_failed"
        finally:
            # Kill the group even if its leader exited: no descendant may survive.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(child.pid, signal.SIGKILL)
            child.wait()
        if error is None:
            try:
                output = read_file(fd, f"{job_id}.pdf", MAX_PDF_BYTES)
                if not output.startswith(b"%PDF-") or not output:
                    raise ValueError("invalid_pdf")
                output_digest = hashlib.sha256(output).hexdigest()
                output_size = len(output)
            except (OSError, ValueError):
                error = "render_output_invalid"
    receipt = RenderReceipt(
        schema_version=1,
        job_id=job_id,
        request_sha256=digest,
        output_sha256=output_digest,
        output_bytes=output_size,
        error_code=error,
    )
    atomic_file(fd, f"{job_id}.result.json", canonical(receipt.model_dump(mode="json")).encode())


def run_renderer(spool: Path) -> None:
    import fcntl

    with directory(spool, create=True) as fd:
        lock = os.open(".renderer-lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        try:
            # Exactly one renderer child across daemon restarts/replicas.
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            while True:
                names = os.listdir(fd)
                for name in names:
                    match = re.fullmatch(r"([0-9a-f]{64})\.request\.json", name)
                    if not match:
                        continue
                    job_id = match.group(1)
                    try:
                        if (
                            time.time() - os.stat(name, dir_fd=fd, follow_symlinks=False).st_mtime
                            > 3600
                        ):
                            for suffix in ("request.json", "result.json", "pdf"):
                                with contextlib.suppress(FileNotFoundError):
                                    os.unlink(f"{job_id}.{suffix}", dir_fd=fd)
                            continue
                        try:
                            read_file(fd, f"{job_id}.result.json", 4096)
                        except FileNotFoundError:
                            _supervise(spool, fd, job_id)
                    except (OSError, ValueError):
                        # Content/path details are deliberately not logged. Invalid
                        # files cannot become database artifact references.
                        continue
                time.sleep(0.1)
        finally:
            os.close(lock)


def isolation_canary(forbidden_file: Path, host: str, port: int) -> dict[str, bool]:
    """Operator-only OS test: intentionally bypass the application URL deny policy.

    True means blocked. Use a mounted secret canary and a reachable listener on a
    different container. This does not claim an absent file proves kernel policy.
    """
    try:
        fd = os.open(forbidden_file, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        file_blocked = True
    else:
        os.close(fd)
        file_blocked = False
    try:
        with socket.create_connection((host, port), timeout=2):
            network_blocked = False
    except OSError:
        network_blocked = True
    return {"file_blocked": file_blocked, "network_blocked": network_blocked}


def main() -> None:
    if len(sys.argv) == 4 and sys.argv[1] == "child":
        _render_child(Path(sys.argv[2]), sys.argv[3])
    elif len(sys.argv) == 5 and sys.argv[1] == "canary":
        print(
            json.dumps(
                isolation_canary(Path(sys.argv[2]), sys.argv[3], int(sys.argv[4])), sort_keys=True
            )
        )
    elif len(sys.argv) == 2 and sys.argv[1] == "serve":
        run_renderer(Path(os.environ.get("OFFERLOOM_RENDERER_SPOOL", "/spool")))
    else:
        raise SystemExit("invalid_renderer_command")


if __name__ == "__main__":
    main()
