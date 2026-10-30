# SPDX-License-Identifier: MIT OR Apache-2.0
"""Text admission and safe failures, using the sealed Linux renderer on macOS."""

from __future__ import annotations

import io
import os
import subprocess
import sys
import threading
import time
import types
import unicodedata
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
)

from bonafides import render_service
from bonafides.document_frame import PrintableFrame, ProfileContact, template_digest
from bonafides.documents import DocumentBlock, DocumentBody, DocumentSection
from bonafides.render_service import RenderClient, RenderFailure, RenderReceipt, render_pdf
from bonafides.storage import MAX_PDF_BYTES, atomic_file, directory, read_file


def _body(*texts: str) -> DocumentBody:
    return DocumentBody(
        title="Resume",
        sections=[
            DocumentSection(
                key="facts",
                heading="Evidence",
                blocks=[
                    DocumentBlock(
                        block_key=f"block-{index}",
                        text=text,
                        claim_kind="greeting",
                        claim_revision_ids=[],
                        evidence_revision_ids=[],
                    )
                    for index, text in enumerate(texts)
                ],
            )
        ],
    )


def _pdf(*page_texts: str) -> bytes:
    """Real selectable PDF objects, independent of host-native font libraries."""
    writer = PdfWriter()
    for text in page_texts:
        page = writer.add_blank_page(width=595, height=842)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 30 800 Td <" + text.encode().hex().encode() + b"> Tj ET")
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _engine(monkeypatch: pytest.MonkeyPatch, output: bytes, *, missing: bool = False) -> None:
    class Painted:
        fonts = {"font": types.SimpleNamespace(missing={0x378: 65535} if missing else {})}

        def write_pdf(self, **kwargs: object) -> bytes:
            return output

    class HTML:
        def __init__(self, **kwargs: object) -> None:
            assert kwargs["url_fetcher"] is render_service.deny_url_fetcher

        def render(self) -> Painted:
            return Painted()

    module = types.ModuleType("weasyprint")
    module.HTML = HTML  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "weasyprint", module)


def test_exact_selectable_text_admission_retains_pages_and_literal_markup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    literal = '<script>fetch("file:///secret")</script>{{7*7}}'
    body = _body("First fact", literal)
    data = _pdf("Resume Evidence First fact", literal)
    _engine(monkeypatch, data)
    assert render_pdf(body) == data
    reader = PdfReader(io.BytesIO(data))
    assert len(reader.pages) == 2
    assert literal == reader.pages[1].extract_text()


def test_pdf_admission_allows_long_word_wrapping_but_preserves_original_spaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    long_word = "unbrokenword" * 80
    body = _body(f"Linux SQL {long_word} final words")
    data = _pdf(
        f"Resume Evidence Linux SQL {long_word[:300]}",
        f"{long_word[300:600]}",
        f"{long_word[600:]} final words",
    )
    _engine(monkeypatch, data)
    assert render_pdf(body) == data


@pytest.mark.parametrize(
    "extracted",
    [
        "Resume Evidence First fact",  # Whole final fact silently dropped.
        "Resume Evidence Second fact First fact",  # Both facts present, wrong order.
        "Resume Evidence First fact Second fat",  # No U+FFFD, but a missing letter.
        "Resume Evidence First fact Second fact extra",  # Fabricated visible words.
        "Resume Evidence Firstfact Second fact",  # Lost original word boundary.
        "Resume Evidence First factSecond fact",  # Lost adjacent block boundary.
    ],
)
def test_pdf_admission_rejects_missing_reordered_and_extra_text_without_replacement_glyph(
    extracted: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = _pdf(extracted)
    assert "\ufffd" not in PdfReader(io.BytesIO(data)).pages[0].extract_text()
    _engine(monkeypatch, data)
    with pytest.raises(RenderFailure, match="^render_text_unsupported$"):
        render_pdf(_body("First fact", "Second fact"))


def test_visible_missing_glyph_fails_even_when_unicode_extraction_is_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A ToUnicode map can preserve characters while the painted glyph is tofu.
    _engine(monkeypatch, _pdf("Resume Evidence First fact"), missing=True)
    with pytest.raises(RenderFailure, match="^render_text_unsupported$"):
        render_pdf(_body("First fact"))


def test_frozen_frame_contact_is_part_of_exact_text_postcondition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _engine(monkeypatch, _pdf("Resume Evidence First fact"))
    frame = PrintableFrame(display_name="Synthetic Name", contact=ProfileContact(phone="123456"))
    with pytest.raises(RenderFailure, match="^render_text_unsupported$"):
        render_pdf(_body("First fact"), "bonafides-evidence-v1", frame)


@pytest.mark.parametrize("data", [b"", b"%PDF-1.7\nnot a PDF", b"%PDF-" + b"x" * MAX_PDF_BYTES])
def test_invalid_or_oversize_engine_output_is_never_admitted(
    data: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    _engine(monkeypatch, data)
    with pytest.raises(RenderFailure, match="^render_output_invalid$"):
        render_pdf(_body("First fact"))


def test_child_does_not_publish_rejected_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = "a" * 64
    request = render_service.RenderRequest(
        schema_version=1, template_version="bonafides-base-v1", body=_body("First fact")
    )
    _engine(monkeypatch, _pdf("Resume Evidence First fat"))
    monkeypatch.setattr(render_service, "_child_limits", lambda: None)
    with directory(tmp_path) as fd:
        atomic_file(fd, f"{job_id}.request.json", request.model_dump_json().encode())
    with pytest.raises(RenderFailure, match="^render_text_unsupported$"):
        render_service._render_child(tmp_path, job_id)
    assert not list(tmp_path.glob("*.pdf"))


@pytest.mark.parametrize(
    "exit_code,expected",
    [(65, "render_text_unsupported"), (66, "render_output_invalid"), (1, "render_failed")],
)
def test_safe_child_error_crosses_supervisor_and_client_without_ready_output(
    exit_code: int, expected: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    actual_popen = subprocess.Popen
    receipts: list[RenderReceipt] = []
    failures: list[BaseException] = []

    def child(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        return actual_popen([sys.executable, "-c", f"raise SystemExit({exit_code})"], **kwargs)

    monkeypatch.setattr(subprocess, "Popen", child)

    def respond() -> None:
        try:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                requests = list(tmp_path.glob("*.request.json"))
                if requests:
                    job_id = requests[0].name.split(".")[0]
                    with directory(tmp_path) as fd:
                        # Even stray valid PDF bytes must not become a ready artifact on error.
                        atomic_file(fd, f"{job_id}.pdf", _pdf("Resume Evidence First fact"))
                        render_service._supervise(tmp_path, fd, job_id)
                        receipts.append(
                            RenderReceipt.model_validate_json(
                                read_file(fd, f"{job_id}.result.json", 4096)
                            )
                        )
                    return
                time.sleep(0.01)
            raise AssertionError("renderer request not published")
        except BaseException as error:
            failures.append(error)

    responder = threading.Thread(target=respond)
    responder.start()
    try:
        with pytest.raises(RenderFailure, match=f"^{expected}$"):
            RenderClient(tmp_path, timeout=5).render(_body("First fact"))
    finally:
        responder.join(timeout=5)
    assert not responder.is_alive() and not failures
    assert receipts[0].error_code == expected
    assert receipts[0].output_sha256 is None and receipts[0].output_bytes == 0
    assert not list(tmp_path.glob("*.pdf"))
    assert not list(tmp_path.glob("*.result.json"))
    assert not list(tmp_path.glob("*.request.json"))


def test_child_main_exposes_only_safe_numeric_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def rejected(*args: object) -> None:
        raise RenderFailure("render_text_unsupported")

    monkeypatch.setattr(render_service, "_render_child", rejected)
    monkeypatch.setattr(sys, "argv", ["renderer", "child", "/unused", "a" * 64])
    with pytest.raises(SystemExit) as result:
        render_service.main()
    assert result.value.code == 65
    output = capsys.readouterr()
    assert output.out == output.err == ""


@pytest.fixture(scope="module")
def actual_renderer(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Callable[[DocumentBody, str, PrintableFrame | None], bytes]]:
    # Glyph support is a property of the shipped image's font set, not the test
    # host's: CI runners carry extra CJK/Unifont fonts. Always paint through the
    # sealed production renderer image; never fall back to host fonts.
    image = os.environ.get("BONAFIDES_TEST_PARSER_IMAGE")
    if not image:
        pytest.fail("Actual renderer tests require BONAFIDES_TEST_PARSER_IMAGE and Docker.")
    spool = tmp_path_factory.mktemp("actual-renderer")
    container = "bonafides-renderer-test-" + uuid4().hex
    process = subprocess.Popen(
        [
            "docker",
            "run",
            "--pull=never",
            "--name",
            container,
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--pids-limit",
            "32",
            "--memory",
            "512m",
            "--cpus",
            "1",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=67108864,mode=1777",
            "--mount",
            f"type=bind,src={spool},dst=/spool",
            "--env",
            "BONAFIDES_RENDERER_SPOOL=/spool",
            "--entrypoint",
            "python",
            image,
            "-m",
            "bonafides.render_service",
            "serve",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 20
        while (
            not (spool / ".renderer-lock").exists()
            and process.poll() is None
            and time.monotonic() < deadline
        ):
            time.sleep(0.05)
        assert process.poll() is None, "Required sealed renderer did not start."
        assert (spool / ".renderer-lock").exists(), "Required sealed renderer startup timed out."
        client = RenderClient(spool)

        def paint(body: DocumentBody, template: str, frame: PrintableFrame | None) -> bytes:
            return client.render(
                body, template, frame, template_digest(template) if frame is not None else None
            )

        yield paint
    finally:
        if process.poll() is None:
            subprocess.run(
                ["docker", "stop", "--time", "5", container],
                check=True,
                capture_output=True,
            )
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate(timeout=5)
            pytest.fail("Required sealed renderer did not stop.")
        finally:
            subprocess.run(["docker", "rm", container], check=True, capture_output=True)
        assert stdout == b""
        assert stderr == b""


@pytest.mark.parametrize(
    "template",
    [
        "bonafides-base-v1",
        "bonafides-evidence-v1",
        "bonafides-compact-v1",
        "bonafides-evidence-letter-v1",
        "bonafides-compact-letter-v1",
    ],
)
def test_actual_renderer_preserves_supported_unicode_long_literal_text_and_order(
    template: str,
    actual_renderer: Callable[[DocumentBody, str, PrintableFrame | None], bytes],
) -> None:
    long_word = "longunbrokenword" * 35
    texts = [
        f"Block{index:02d} José Zöe Noe\u0308l Δέσποινα Иванова 😀 𝒜 {long_word} "
        '<img src="file:///secret"><script>fetch("http://metadata/")</script>{{7*7}}'
        for index in range(24)
    ]
    body = _body(*texts)
    frame = (
        None
        if template == "bonafides-base-v1"
        else PrintableFrame(
            display_name="José Noe\u0308l Δέσποινα Иванова",
            contact=ProfileContact(email="synthetic@example.invalid", location="München"),
        )
    )
    data = actual_renderer(body, template, frame)
    reader = PdfReader(io.BytesIO(data))
    assert len(reader.pages) > 1
    extracted = "".join(page.extract_text() for page in reader.pages)
    assert long_word not in extracted, "Fixture must exercise selectable long-word wrapping."
    compact = "".join(unicodedata.normalize("NFC", extracted).split())
    positions = [
        compact.index("".join(unicodedata.normalize("NFC", text).split())) for text in texts
    ]
    assert positions == sorted(positions)
    assert "\ufffd" not in extracted
    assert data.startswith(b"%PDF-") and len(data) <= MAX_PDF_BYTES


@pytest.mark.parametrize(
    ("template", "width", "height"),
    [
        ("bonafides-evidence-v1", 210 / 25.4 * 72, 297 / 25.4 * 72),
        ("bonafides-compact-v1", 210 / 25.4 * 72, 297 / 25.4 * 72),
        ("bonafides-evidence-letter-v1", 612, 792),
        ("bonafides-compact-letter-v1", 612, 792),
    ],
)
def test_actual_renderer_uses_selected_paper_geometry_and_preserves_frame_and_body(
    template: str,
    width: float,
    height: float,
    actual_renderer: Callable[[DocumentBody, str, PrintableFrame | None], bytes],
) -> None:
    body = _body("First supported professional fact.", "Final supported professional fact.")
    frame = PrintableFrame(
        display_name="Synthetic Page Owner",
        contact=ProfileContact(email="page-owner@example.invalid"),
    )
    data = actual_renderer(body, template, frame)
    reader = PdfReader(io.BytesIO(data))
    assert reader.pages
    for page in reader.pages:
        assert float(page.mediabox.width) == pytest.approx(width, abs=0.01)
        assert float(page.mediabox.height) == pytest.approx(height, abs=0.01)
    extracted = " ".join(page.extract_text() for page in reader.pages)
    normalized = " ".join(extracted.split())
    for value in (
        frame.display_name,
        "page-owner@example.invalid",
        "First supported professional fact.",
        "Final supported professional fact.",
    ):
        assert value in normalized


@pytest.mark.parametrize("text", ["王小明", "unassigned \u0378", "noncharacter \U0010ffff"])
@pytest.mark.parametrize("template", ["bonafides-evidence-v1", "bonafides-compact-v1"])
def test_actual_renderer_rejects_unsupported_glyphs_without_silent_text_loss(
    text: str,
    template: str,
    actual_renderer: Callable[[DocumentBody, str, PrintableFrame | None], bytes],
) -> None:
    with pytest.raises(RenderFailure, match="^render_text_unsupported$"):
        actual_renderer(_body(text), template, PrintableFrame(display_name="Synthetic Name"))
