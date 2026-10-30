# SPDX-License-Identifier: MIT OR Apache-2.0
"""Bounded local import transport; the sealed container is the security boundary.

The daemon never parses uploaded files. It supervises one resource-limited child,
with no inherited application environment, and publishes only verified receipts.
Run the daemon with ``python -m bonafides.import_parser serve /import-spool``;
its child command is ``python -m bonafides.import_parser --child SPOOL JOB_ID``.
Neither entry point imports application settings or reads credentials. Deployment
must additionally deny network/secrets/database access, use a read-only root and
bound container memory, processes and the dedicated writable spool volume.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import io
import logging
import math
import os
import re
import signal
import stat
import subprocess
import sys
import threading
import time
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import TYPE_CHECKING, Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from bonafides.spool_guard import SpoolBusy, acknowledge_pause, shared_spool
from bonafides.storage import atomic_file, directory, read_file

if TYPE_CHECKING:
    from pypdf.generic import DictionaryObject
    from pypdf.generic._font import Font

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_TEXT_SCALARS = 80_000
MAX_TEXT_BYTES = 256 * 1024
MAX_PDF_PAGES = 50
MAX_PAGE_CONTENT_BYTES = 2 * 1024 * 1024
MAX_TOTAL_CONTENT_BYTES = 16 * 1024 * 1024
MAX_QUEUED_JOBS = 16
PARSER_TIMEOUT = 30.0
_REQUEST_BYTES = 2048
_RECEIPT_BYTES = 4096
_SPOOL_ENTRIES = 256
_RETENTION_SECONDS = 3600
_POLL_SECONDS = 0.05
_JOB = re.compile(r"[0-9a-f]{64}\Z")
_FILE = re.compile(
    r"([0-9a-f]{64})\.(active|input|request\.json|child\.json|text|result\.json|cancel)\Z"
)
_SUFFIXES = ("input", "request.json", "child.json", "text", "result.json", "cancel", "active")

ErrorCode = Literal[
    "import_invalid_input",
    "import_invalid_media_type",
    "import_too_large",
    "import_empty_text",
    "import_invalid_utf8",
    "import_invalid_text",
    "import_invalid_pdf",
    "import_encrypted_pdf",
    "import_page_limit",
    "import_content_limit",
    "import_text_limit",
    "import_no_selectable_text",
    "import_parser_busy",
    "import_timeout",
    "import_interrupted",
    "import_output_invalid",
    "import_failed",
]
_ERRORS: frozenset[str] = frozenset(
    (
        "import_invalid_input",
        "import_invalid_media_type",
        "import_too_large",
        "import_empty_text",
        "import_invalid_utf8",
        "import_invalid_text",
        "import_invalid_pdf",
        "import_encrypted_pdf",
        "import_page_limit",
        "import_content_limit",
        "import_text_limit",
        "import_no_selectable_text",
        "import_parser_busy",
        "import_timeout",
        "import_interrupted",
        "import_output_invalid",
        "import_failed",
    )
)
WarningCode = Literal["pdf_pages_without_selectable_text", "pdf_layout_requires_review"]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


@dataclass(frozen=True)
class ParsedImport:
    text: str
    source_sha256: str
    warnings: list[str]


class ImportFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        # Even unexpected parser/library exceptions cannot put document content in errors.
        self.code = code if code in _ERRORS else "import_failed"
        super().__init__(self.code)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class _Request(_StrictModel):
    schema_version: Literal[1]
    media_type: Literal["application/pdf", "text/plain"]
    input_sha256: Digest
    input_bytes: Annotated[int, Field(ge=1, le=MAX_UPLOAD_BYTES)]


class _Receipt(_StrictModel):
    schema_version: Literal[1]
    job_id: Digest
    request_sha256: Digest
    input_sha256: Digest
    output_sha256: Digest | None
    output_bytes: Annotated[int, Field(ge=0, le=MAX_TEXT_BYTES)]
    output_scalars: Annotated[int, Field(ge=0, le=MAX_TEXT_SCALARS)]
    warnings: list[WarningCode] = Field(max_length=2)
    error_code: ErrorCode | None

    @model_validator(mode="after")
    def consistent_outcome(self) -> _Receipt:
        if self.error_code is None:
            if self.output_sha256 is None or not self.output_bytes or not self.output_scalars:
                raise ValueError("invalid_import_receipt")
        elif (
            self.output_sha256 is not None
            or self.output_bytes
            or self.output_scalars
            or self.warnings
        ):
            raise ValueError("invalid_import_receipt")
        return self


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _snapshot(fd: int) -> list[str]:
    names: list[str] = []
    with os.scandir(fd) as entries:
        for entry in entries:
            if len(names) >= _SPOOL_ENTRIES:
                raise ImportFailure("import_parser_busy")
            names.append(entry.name)
    return names


@contextlib.contextmanager
def _lock(fd: int, name: str, *, create: bool = True) -> Iterator[int]:
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
    if create:
        flags |= os.O_CREAT
    lock = os.open(name, flags, 0o600, dir_fd=fd)
    try:
        if not stat.S_ISREG(os.fstat(lock).st_mode):
            raise ValueError("invalid_import_lock")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield lock
    finally:
        os.close(lock)


def _remove_job(fd: int, job_id: str) -> None:
    for suffix in _SUFFIXES:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(f"{job_id}.{suffix}", dir_fd=fd)


def _sweep(fd: int, names: list[str]) -> None:
    """Bounded and called under admission lock; never unlink a live child's files."""
    cutoff = time.time() - _RETENTION_SECONDS
    jobs = {match.group(1) for name in names if (match := _FILE.fullmatch(name))}
    active_jobs = False
    for job_id in jobs:
        present = [f"{job_id}.{suffix}" for suffix in _SUFFIXES if f"{job_id}.{suffix}" in names]
        try:
            try:
                with _lock(fd, f"{job_id}.active", create=False):
                    if all(
                        os.stat(name, dir_fd=fd, follow_symlinks=False).st_mtime <= cutoff
                        for name in present
                    ):
                        _remove_job(fd, job_id)
            except FileNotFoundError:
                if all(
                    os.stat(name, dir_fd=fd, follow_symlinks=False).st_mtime <= cutoff
                    for name in present
                ):
                    _remove_job(fd, job_id)
        except BlockingIOError:
            active_jobs = True
        except (OSError, ValueError):
            # Never follow unsafe files or log their contents.
            continue
    # Staging names are intentionally opaque, so they cannot be assigned to a
    # job. Leave them alone whenever any child/client is actively using the spool.
    if active_jobs:
        return
    for name in names:
        if not re.fullmatch(r"\.staging-[0-9a-f]{32}", name):
            continue
        with contextlib.suppress(OSError):
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISREG(info.st_mode) and info.st_mtime <= cutoff:
                os.unlink(name, dir_fd=fd)


def _cancelled(fd: int, job_id: str) -> bool:
    try:
        return read_file(fd, f"{job_id}.cancel", 0) == b""
    except FileNotFoundError:
        return False


def _check_interrupted(interrupted: threading.Event | None) -> None:
    if interrupted is not None and interrupted.is_set():
        raise ImportFailure("import_interrupted")


def _pause(interrupted: threading.Event | None) -> None:
    if interrupted is None:
        time.sleep(_POLL_SECONDS)
    else:
        interrupted.wait(_POLL_SECONDS)


class ImportParserClient:
    def __init__(self, spool: Path, *, timeout: float = 40.0) -> None:
        if not 0 < timeout <= 60:
            raise ValueError("invalid_import_parser_timeout")
        self.spool = spool
        self.timeout = timeout

    def parse(
        self, data: bytes, media_type: str, interrupted: threading.Event | None = None
    ) -> ParsedImport:
        _check_interrupted(interrupted)
        if media_type not in ("application/pdf", "text/plain"):
            raise ImportFailure("import_invalid_media_type")
        if not isinstance(data, bytes):
            raise ImportFailure("import_invalid_input")
        if len(data) > MAX_UPLOAD_BYTES:
            raise ImportFailure("import_too_large")
        if not data:
            raise ImportFailure("import_empty_text")
        request = _Request(
            schema_version=1,
            media_type="application/pdf" if media_type == "application/pdf" else "text/plain",
            input_sha256=_hash(data),
            input_bytes=len(data),
        )
        payload = request.model_dump_json().encode()
        digest = _hash(payload)
        job_id = _hash(uuid4().bytes + bytes.fromhex(digest))
        deadline = time.monotonic() + self.timeout
        try:
            with directory(self.spool, create=True) as fd, shared_spool(fd):
                while True:
                    _check_interrupted(interrupted)
                    try:
                        with _lock(fd, ".admission-lock"):
                            names = _snapshot(fd)
                            _sweep(fd, names)
                            jobs = {
                                m.group(1) for name in _snapshot(fd) if (m := _FILE.fullmatch(name))
                            }
                            if len(jobs) >= MAX_QUEUED_JOBS:
                                raise ImportFailure("import_parser_busy")
                            # The active anchor is published first; the request is last.
                            with _lock(fd, f"{job_id}.active"):
                                try:
                                    atomic_file(fd, f"{job_id}.input", data)
                                    atomic_file(fd, f"{job_id}.request.json", payload)
                                except BaseException:
                                    _remove_job(fd, job_id)
                                    raise
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise ImportFailure("import_parser_busy") from None
                        _pause(interrupted)
                try:
                    while time.monotonic() < deadline:
                        _check_interrupted(interrupted)
                        try:
                            raw = read_file(fd, f"{job_id}.result.json", _RECEIPT_BYTES)
                        except FileNotFoundError:
                            _pause(interrupted)
                            continue
                        receipt = _Receipt.model_validate_json(raw, strict=True)
                        if (
                            receipt.job_id != job_id
                            or receipt.request_sha256 != digest
                            or receipt.input_sha256 != request.input_sha256
                        ):
                            raise ImportFailure("import_output_invalid")
                        if receipt.error_code is not None:
                            raise ImportFailure(receipt.error_code)
                        output = read_file(fd, f"{job_id}.text", MAX_TEXT_BYTES)
                        text = _verified_text(output, receipt)
                        _check_interrupted(interrupted)
                        return ParsedImport(text, _hash(output), list(receipt.warnings))
                    raise ImportFailure("import_timeout")
                finally:
                    # Cancellation is durable. Never remove input while a child may
                    # still be reading it; the daemon kills/reaps before cleanup.
                    atomic_file(fd, f"{job_id}.cancel", b"")
                    try:
                        with _lock(fd, f"{job_id}.active", create=False):
                            if _has_receipt(fd, job_id):
                                _remove_job(fd, job_id)
                    except (OSError, ValueError):
                        pass
        except SpoolBusy:
            raise ImportFailure("import_parser_busy") from None
        except ImportFailure:
            raise
        except (OSError, ValueError):
            raise ImportFailure("import_output_invalid") from None


def _has_receipt(fd: int, job_id: str) -> bool:
    try:
        read_file(fd, f"{job_id}.result.json", _RECEIPT_BYTES)
        return True
    except FileNotFoundError:
        return False


def _normalized(text: str, *, empty_code: str = "import_empty_text") -> str:
    normalized = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    if "\x00" in normalized:
        raise ImportFailure("import_invalid_text")
    if len(normalized) > MAX_TEXT_SCALARS:
        raise ImportFailure("import_text_limit")
    try:
        output = normalized.encode("utf-8", errors="strict")
    except UnicodeError:
        raise ImportFailure("import_invalid_utf8") from None
    if len(output) > MAX_TEXT_BYTES:
        raise ImportFailure("import_text_limit")
    if not normalized.strip():
        raise ImportFailure(empty_code)
    return normalized


def _verified_text(output: bytes, receipt: _Receipt) -> str:
    try:
        text = output.decode("utf-8", errors="strict")
        if (
            len(output) != receipt.output_bytes
            or _hash(output) != receipt.output_sha256
            or len(text) != receipt.output_scalars
            or _normalized(text) != text
        ):
            raise ImportFailure("import_output_invalid")
        return text
    except (UnicodeError, ImportFailure):
        raise ImportFailure("import_output_invalid") from None


class _PdfDiagnostics(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.failed = False

    def emit(self, record: logging.LogRecord) -> None:
        # Do not format or retain a record: library messages can contain source text.
        self.failed = True


@dataclass
class _ContentBudget:
    total: int = 0
    forms: int = 0

    def add(self, amount: int) -> None:
        if amount > MAX_PAGE_CONTENT_BYTES:
            raise ImportFailure("import_content_limit")
        self.total += amount
        if self.total > MAX_TOTAL_CONTENT_BYTES:
            raise ImportFailure("import_content_limit")


def _form_contents(
    resources: DictionaryObject, budget: _ContentBudget, seen: set[int], ancestors: set[int]
) -> None:
    from pypdf.generic import DictionaryObject, StreamObject

    objects = resources.get("/XObject")
    if objects is None:
        return
    resolved = objects.get_object()
    if not isinstance(resolved, DictionaryObject):
        raise ImportFailure("import_invalid_pdf")
    for item in resolved.values():
        form = item.get_object()
        if not isinstance(form, StreamObject) or form.get("/Subtype") != "/Form":
            continue
        identity = id(form)
        if identity in ancestors or len(ancestors) >= 16:
            raise ImportFailure("import_content_limit")
        if identity in seen:
            continue
        seen.add(identity)
        budget.forms += 1
        if budget.forms > 128:
            raise ImportFailure("import_content_limit")
        budget.add(len(form.get_data()))
        nested = form.get("/Resources")
        if nested is not None:
            nested_resources = nested.get_object()
            if not isinstance(nested_resources, DictionaryObject):
                raise ImportFailure("import_invalid_pdf")
            _form_contents(nested_resources, budget, seen, ancestors | {identity})


@dataclass(slots=True)
class _PdfLine:
    text: str
    x: float
    y: float
    end: float
    size: float
    bold: bool
    joinable: bool = True


class _PdfLayout:
    """Recover bounded text flow, never coordinate-sized whitespace or guessed facts."""

    def __init__(self) -> None:
        self.fragments: list[_PdfLine] = []
        self.fonts: dict[int, tuple[Font, dict[str, str]]] = {}
        self.ambiguous = False

    def visit(
        self,
        text: str,
        cm: list[float],
        tm: list[float],
        resource: DictionaryObject | None,
        size: float,
    ) -> None:
        from pypdf.errors import LimitReachedError
        from pypdf.generic._font import Font

        literal = text.strip()
        if not literal:
            return
        # A visitor can report opaque multiline/form text or unsupported transforms.
        # Preserve plain extraction with an explicit warning instead of flattening it.
        if (
            resource is None
            or len(literal.splitlines()) != 1
            or not all(math.isfinite(value) for value in (*cm, *tm, size))
        ):
            self.ambiguous = True
            return
        a = cm[0] * tm[0] + cm[2] * tm[1]
        b = cm[1] * tm[0] + cm[3] * tm[1]
        c = cm[0] * tm[2] + cm[2] * tm[3]
        d = cm[1] * tm[2] + cm[3] * tm[3]
        if a <= 0 or d <= 0 or abs(b) > 1e-6 or abs(c) > 1e-6 or size <= 0:
            self.ambiguous = True
            return
        try:
            identity = id(resource)
            if identity not in self.fonts:
                # pypdf is pinned: its font metrics handle embedded/CID fonts as well as
                # core fonts. Widths are keyed by source glyphs, not decoded Unicode.
                font = Font.from_font_resource(resource)
                inverse = {
                    value: key
                    for key, value in font.character_map.items()
                    if isinstance(key, str) and isinstance(value, str)
                }
                self.fonts[identity] = font, inverse
            font, inverse = self.fonts[identity]
            width = (
                font.get_text_width("".join(inverse.get(char, char) for char in literal))
                * size
                * a
                / 1000
            )
            bold = "bold" in font.name.casefold()
        except (LimitReachedError, MemoryError, RecursionError):
            raise
        except Exception:
            # Metrics are optional, unlike plain extraction. Discard reconstruction
            # for the entire page, including any fragments already collected.
            self.ambiguous = True
            return
        x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
        y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
        end = x + width
        height = size * d
        if not all(math.isfinite(value) for value in (x, y, end, height)) or height <= 0:
            self.ambiguous = True
            return
        self.fragments.append(_PdfLine(literal, x, y, end, height, bold))

    def reconstruct(self, original: str) -> tuple[str, bool]:
        if self.ambiguous:
            return original, True
        groups: list[list[_PdfLine]] = []
        for fragment in self.fragments:
            if not groups or abs(groups[-1][0].y - fragment.y) > (
                min(groups[-1][0].size, fragment.size) * 0.25
            ):
                groups.append([])
            groups[-1].append(fragment)
        lines: list[_PdfLine] = []
        for group in groups:
            group.sort(key=lambda fragment: fragment.x)
            first = group[0]
            line = _PdfLine(first.text, first.x, first.y, first.end, first.size, first.bold)
            for fragment in group[1:]:
                scale = min(line.size, fragment.size)
                gap = fragment.x - line.end
                if gap > 4 * scale:
                    # Separate columns/cells, not one phrase with giant padding.
                    line.joinable = False
                    lines.append(line)
                    line = _PdfLine(
                        fragment.text,
                        fragment.x,
                        fragment.y,
                        fragment.end,
                        fragment.size,
                        fragment.bold,
                        False,
                    )
                    continue
                if gap < -0.25 * scale:
                    line.joinable = False
                line.text += (" " if gap > 0.12 * scale else "") + fragment.text
                line.end = max(line.end, fragment.end)
                line.size = max(line.size, fragment.size)
                line.bold = line.bold and fragment.bold
            lines.append(line)
        # Sorting/visitors must never lose, duplicate or reorder source characters.
        # Only whitespace reconstruction is permitted, including for nested forms.
        source = "".join(character for character in original if not character.isspace())
        rebuilt = "".join(
            character for line in lines for character in line.text if not character.isspace()
        )
        if rebuilt != source:
            return original, True
        right = max((line.end for line in lines), default=0)
        paragraphs: list[str] = []
        previous: _PdfLine | None = None
        for line in lines:
            scale = min(previous.size, line.size) if previous is not None else 0
            wraps = (
                previous is not None
                and previous.joinable
                and line.joinable
                and 0 < previous.y - line.y < 1.8 * scale
                and max(previous.size, line.size) < 1.35 * scale
                and abs(previous.x - line.x) < 2 * scale
                and previous.end - previous.x >= 20 * scale
                and previous.end >= right - 4 * scale
                and not previous.bold
                and not line.bold
                and not previous.text.endswith(":")
                and re.match(r"^(?:[•●▪◦*\-]|[0-9]+[.)])\s*", line.text) is None
            )
            if wraps:
                paragraphs[-1] += " " + line.text
            else:
                paragraphs.append(line.text)
            previous = line
        return "\n".join(paragraphs), False


def _pdf_text(data: bytes) -> tuple[str, list[WarningCode]]:
    from pypdf import PdfReader, apply_configuration
    from pypdf.errors import FileNotDecryptedError, LimitReachedError
    from pypdf.generic import DictionaryObject

    diagnostics = _PdfDiagnostics()
    logger = logging.getLogger("pypdf")
    logger.handlers = [diagnostics]
    logger.propagate = False
    logger.setLevel(logging.WARNING)
    budget = _ContentBudget()
    fragments = 0

    layout = _PdfLayout()

    def visit(
        text: str,
        cm: list[float],
        tm: list[float],
        font: DictionaryObject | None,
        size: float,
    ) -> None:
        nonlocal fragments
        fragments += len(text)
        # NFC can compose canonical sequences, so the final exact limit is checked
        # after normalization. This larger early bound limits extraction allocation.
        if fragments > MAX_TEXT_SCALARS * 4:
            raise ImportFailure("import_text_limit")
        layout.visit(text, cm, tm, font, size)

    try:
        if not data.startswith(b"%PDF-"):
            raise ImportFailure("import_invalid_pdf")
        with apply_configuration(
            disable_legacy_handling=True,
            maximum_declared_stream_length=MAX_PAGE_CONTENT_BYTES,
            array_based_stream_maximum_output_length=MAX_PAGE_CONTENT_BYTES,
            zlib_maximum_output_length=MAX_PAGE_CONTENT_BYTES,
            lzw_maximum_output_length=MAX_PAGE_CONTENT_BYTES,
            run_length_maximum_output_length=MAX_PAGE_CONTENT_BYTES,
            brotli_maximum_output_length=MAX_PAGE_CONTENT_BYTES,
            jbig2_maximum_output_length=MAX_PAGE_CONTENT_BYTES,
            image_maximum_buffer_size=MAX_PAGE_CONTENT_BYTES,
            stream_decoding_work_maximum_length=MAX_PAGE_CONTENT_BYTES * 4,
            stream_filters_maximum_length=4,
            stream_parsing_work_maximum_count=200_000,
            array_maximum_element_count=4096,
            dictionary_maximum_element_count=4096,
            page_tree_maximum_entries=512,
            page_tree_maximum_depth=32,
            xform_maximum_invocations_per_extraction=128,
            jbig2dec_binary=None,
        ):
            reader = PdfReader(io.BytesIO(data), strict=True, root_object_recovery_limit=0)
            try:
                if reader.is_encrypted:
                    raise ImportFailure("import_encrypted_pdf")
                if len(reader.pages) > MAX_PDF_PAGES:
                    raise ImportFailure("import_page_limit")
                pages: list[str] = []
                empty_pages = False
                uncertain_layout = False
                seen_forms: set[int] = set()
                for page in reader.pages:
                    content = page.get_contents()
                    if content is not None:
                        budget.add(len(content.get_data()))
                    resources = page.get("/Resources")
                    if resources is not None:
                        resolved = resources.get_object()
                        if not isinstance(resolved, DictionaryObject):
                            raise ImportFailure("import_invalid_pdf")
                        _form_contents(resolved, budget, seen_forms, set())
                    layout = _PdfLayout()
                    text = page.extract_text(visitor_text=visit, extraction_mode="plain")
                    if diagnostics.failed:
                        # pypdf sometimes reports skipped malformed forms as warnings.
                        # Never turn partial/truncated extraction into silent success.
                        raise ImportFailure("import_invalid_pdf")
                    empty_pages = empty_pages or not text.strip()
                    text, uncertain = layout.reconstruct(text)
                    uncertain_layout = uncertain_layout or uncertain
                    pages.append(text)
                result = _normalized("\n".join(pages), empty_code="import_no_selectable_text")
                warnings: list[WarningCode] = []
                if empty_pages:
                    warnings.append("pdf_pages_without_selectable_text")
                if uncertain_layout:
                    warnings.append("pdf_layout_requires_review")
                return result, warnings
            finally:
                reader.close()
    except ImportFailure:
        raise
    except FileNotDecryptedError:
        raise ImportFailure("import_encrypted_pdf") from None
    except (LimitReachedError, MemoryError, RecursionError):
        raise ImportFailure("import_content_limit") from None
    except Exception:
        raise ImportFailure("import_invalid_pdf") from None


def _child_limits() -> None:
    import resource

    os.umask(0o077)
    resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
    resource.setrlimit(resource.RLIMIT_AS, (384 * 1024 * 1024, 384 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_TEXT_BYTES, MAX_TEXT_BYTES))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    signal.signal(signal.SIGALRM, signal.SIG_DFL)
    signal.alarm(30)
    # The container's pids.max=32 bounds processes without a cross-container,
    # per-UID NPROC limit that can deny unrelated application threads.


def _failure_receipt(job_id: str, digest: str, input_digest: str, code: str) -> _Receipt:
    return _Receipt.model_validate(
        {
            "schema_version": 1,
            "job_id": job_id,
            "request_sha256": digest,
            "input_sha256": input_digest,
            "output_sha256": None,
            "output_bytes": 0,
            "output_scalars": 0,
            "warnings": [],
            "error_code": ImportFailure(code).code,
        },
        strict=True,
    )


def _parse_child(spool: Path, job_id: str) -> None:
    _child_limits()
    if not _JOB.fullmatch(job_id):
        raise ImportFailure("import_invalid_input")
    with directory(spool) as fd:
        raw = read_file(fd, f"{job_id}.request.json", _REQUEST_BYTES)
        digest = _hash(raw)
        input_digest = "0" * 64
        try:
            request = _Request.model_validate_json(raw, strict=True)
            input_digest = request.input_sha256
            data = read_file(fd, f"{job_id}.input", MAX_UPLOAD_BYTES)
            if len(data) != request.input_bytes or _hash(data) != request.input_sha256:
                raise ImportFailure("import_invalid_input")
            if _cancelled(fd, job_id):
                raise ImportFailure("import_interrupted")
            warnings: list[WarningCode] = []
            if request.media_type == "application/pdf":
                text, warnings = _pdf_text(data)
            else:
                try:
                    text = _normalized(data.decode("utf-8", errors="strict"))
                except UnicodeError:
                    raise ImportFailure("import_invalid_utf8") from None
            output = text.encode("utf-8")
            atomic_file(fd, f"{job_id}.text", output)
            receipt = _Receipt(
                schema_version=1,
                job_id=job_id,
                request_sha256=digest,
                input_sha256=input_digest,
                output_sha256=_hash(output),
                output_bytes=len(output),
                output_scalars=len(text),
                warnings=warnings,
                error_code=None,
            )
        except ImportFailure as error:
            receipt = _failure_receipt(job_id, digest, input_digest, error.code)
        except (MemoryError, RecursionError):
            receipt = _failure_receipt(job_id, digest, input_digest, "import_content_limit")
        except Exception:
            receipt = _failure_receipt(job_id, digest, input_digest, "import_invalid_input")
        atomic_file(fd, f"{job_id}.child.json", receipt.model_dump_json().encode())


def _supervise(
    spool: Path, fd: int, job_id: str, active: int, stopping: threading.Event | None = None
) -> None:
    with shared_spool(fd) as guard:
        _supervise_active(spool, fd, job_id, active, stopping, guard)


def _supervise_active(
    spool: Path,
    fd: int,
    job_id: str,
    active: int,
    stopping: threading.Event | None,
    guard: int,
) -> None:
    raw = read_file(fd, f"{job_id}.request.json", _REQUEST_BYTES)
    digest = _hash(raw)
    input_digest = "0" * 64
    try:
        request = _Request.model_validate_json(raw, strict=True)
        input_digest = request.input_sha256
        _check_interrupted(stopping)
        if _cancelled(fd, job_id):
            raise ImportFailure("import_interrupted")
        environment = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "HOME": "/tmp",
            "TMPDIR": "/tmp",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        child = subprocess.Popen(
            [sys.executable, "-m", "bonafides.import_parser", "--child", str(spool), job_id],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
            start_new_session=True,
            close_fds=True,
            pass_fds=(active, guard),
        )
        try:
            deadline = time.monotonic() + PARSER_TIMEOUT
            while child.poll() is None:
                _check_interrupted(stopping)
                if _cancelled(fd, job_id):
                    raise ImportFailure("import_interrupted")
                if time.monotonic() >= deadline:
                    raise ImportFailure("import_timeout")
                with contextlib.suppress(subprocess.TimeoutExpired):
                    child.wait(timeout=_POLL_SECONDS)
            if child.returncode == -signal.SIGALRM:
                raise ImportFailure("import_timeout")
            if child.returncode == -signal.SIGXCPU:
                raise ImportFailure("import_content_limit")
            if child.returncode != 0:
                raise ImportFailure("import_failed")
        finally:
            # Always kill the entire session, even after its leader exits. The
            # leader is reaped on timeout, cancellation, daemon signal and success.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(child.pid, signal.SIGKILL)
            child.wait()
        _check_interrupted(stopping)
        if _cancelled(fd, job_id):
            raise ImportFailure("import_interrupted")
        receipt = _Receipt.model_validate_json(
            read_file(fd, f"{job_id}.child.json", _RECEIPT_BYTES), strict=True
        )
        if (
            receipt.job_id != job_id
            or receipt.request_sha256 != digest
            or receipt.input_sha256 != input_digest
        ):
            raise ImportFailure("import_output_invalid")
        if receipt.error_code is None:
            _verified_text(read_file(fd, f"{job_id}.text", MAX_TEXT_BYTES), receipt)
    except ImportFailure as error:
        receipt = _failure_receipt(job_id, digest, input_digest, error.code)
    except (OSError, ValueError):
        receipt = _failure_receipt(job_id, digest, input_digest, "import_output_invalid")
    if receipt.error_code is not None:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(f"{job_id}.text", dir_fd=fd)
    atomic_file(fd, f"{job_id}.result.json", receipt.model_dump_json().encode())
    # These bytes are no longer needed after the bounded child is reaped. Keep the
    # tiny request until the client consumes the receipt or bounded retention wins.
    for suffix in ("input", "child.json"):
        with contextlib.suppress(FileNotFoundError):
            os.unlink(f"{job_id}.{suffix}", dir_fd=fd)


def run_parser(spool: Path) -> None:
    stopping = threading.Event()

    def stop(signum: int, frame: FrameType | None) -> None:
        del signum, frame
        stopping.set()

    previous = [
        (signum, signal.getsignal(signum))
        for signum in (signal.SIGTERM, signal.SIGINT)
        if threading.current_thread() is threading.main_thread()
    ]
    for signum, _handler in previous:
        signal.signal(signum, stop)
    try:
        with directory(spool, create=True) as fd, _lock(fd, ".parser-lock"):
            while not stopping.is_set():
                try:
                    if acknowledge_pause(fd):
                        time.sleep(_POLL_SECONDS)
                        continue
                    with shared_spool(fd), _lock(fd, ".admission-lock"):
                        names = _snapshot(fd)
                        _sweep(fd, names)
                        names = _snapshot(fd)
                    for name in names:
                        if stopping.is_set():
                            break
                        match = re.fullmatch(r"([0-9a-f]{64})\.request\.json", name)
                        if match is None:
                            continue
                        job_id = match.group(1)
                        try:
                            with (
                                shared_spool(fd),
                                _lock(fd, f"{job_id}.active", create=False) as active,
                            ):
                                if _has_receipt(fd, job_id):
                                    if _cancelled(fd, job_id):
                                        _remove_job(fd, job_id)
                                    continue
                                _supervise(spool, fd, job_id, active, stopping)
                                if _cancelled(fd, job_id):
                                    _remove_job(fd, job_id)
                        except (OSError, ValueError, ImportFailure):
                            # No document bytes, filenames or library exception
                            # messages enter logs; clients have a bounded deadline.
                            continue
                except (OSError, ValueError, ImportFailure):
                    pass
                time.sleep(_POLL_SECONDS)
    except KeyboardInterrupt:
        return
    finally:
        for signum, handler in previous:
            signal.signal(signum, handler)


def main() -> None:
    try:
        if len(sys.argv) == 4 and sys.argv[1] == "--child":
            _parse_child(Path(sys.argv[2]), sys.argv[3])
        elif len(sys.argv) == 3 and sys.argv[1] == "serve":
            run_parser(Path(sys.argv[2]))
        else:
            raise ImportFailure("import_invalid_input")
    except Exception:
        # The standalone child must not emit even a traceback containing source.
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
