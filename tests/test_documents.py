# SPDX-License-Identifier: MIT OR Apache-2.0
from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys
import threading
import time
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from test_document_generation import (
    _base,
    _candidate,
    _claim,
    _document,
    _legacy_literal_profile,
    _pair_documents,
    _preview,
    _queue_pair,
    _review,
    _setup_pair,
)
from test_identity import identity as identity
from test_sources import SourceContext
from test_sources import catalog as catalog
from test_suitability import SOURCE, FixtureModel, _profile, _requirement, _route, _write
from test_tracking import _application

from bonafides import document_archives, document_generation, documents, suitability
from bonafides.documents import (
    DocumentBlock,
    DocumentBody,
    DocumentSection,
    Fact,
    RenderRequest,
    ReviewRequest,
    body_digest,
    parse_body,
    prepare_render,
)
from bonafides.problems import Problem
from bonafides.render_service import (
    RenderClient,
    RenderFailure,
    RenderReceipt,
    deny_url_fetcher,
    isolation_canary,
    render_html,
)
from bonafides.statement_check import check_statements
from bonafides.storage import BlobStore, atomic_file, directory, read_file
from bonafides.worker import Worker


def example(text: str = "Maintained Linux services.") -> tuple[DocumentBody, Fact]:
    claim, evidence = uuid4(), uuid4()
    fact = Fact(
        claim,
        evidence,
        text,
        text,
        "Synthetic owner statement",
        "a" * 64,
        "b" * 64,
        "user_asserted",
        None,
    )
    body = DocumentBody(
        title="Base résumé",
        sections=[
            DocumentSection(
                key="facts",
                heading="Profile evidence",
                blocks=[
                    DocumentBlock(
                        block_key="linux",
                        text=text,
                        claim_kind="factual",
                        claim_revision_ids=[claim],
                        evidence_revision_ids=[evidence],
                    )
                ],
            )
        ],
    )
    return body, fact


def test_ast_roundtrip_retains_exact_block_identity_and_pins() -> None:
    body, _ = example()
    parsed = parse_body(body.model_dump(mode="json"))
    assert parsed == body
    assert parsed.sections[0].blocks[0].block_key == "linux"
    assert len(body_digest(parsed)) == 64
    changed, _ = example("Maintained Linux services. ")
    assert body_digest(changed) != body_digest(body)


@pytest.mark.parametrize("field,value", [("html", "<b>owned</b>"), ("title", 42), ("sections", [])])
def test_ast_rejects_extra_fields_coercion_and_empty_document(field: str, value: object) -> None:
    body, _ = example()
    wire = body.model_dump(mode="json")
    wire[field] = value
    with pytest.raises(ValidationError):
        parse_body(wire)


def test_duplicate_block_keys_and_references_are_rejected() -> None:
    body, _ = example()
    block = body.sections[0].blocks[0]
    with pytest.raises(ValidationError, match="duplicate_document_key"):
        DocumentBody(
            title="Resume",
            sections=[DocumentSection(key="one", heading="Evidence", blocks=[block, block])],
        )
    with pytest.raises(ValidationError, match="duplicate_document_reference"):
        DocumentBody(
            title="Resume",
            sections=[
                DocumentSection(
                    key="one",
                    heading="Evidence",
                    blocks=[
                        block.model_copy(
                            update={"claim_revision_ids": block.claim_revision_ids * 2}
                        )
                    ],
                )
            ],
        )


def _base_issues(body: DocumentBody, fact: Fact) -> list[tuple[str, str]]:
    """The claim-evidence checker on a base résumé pinned to one saved fact."""
    pinned: dict[str, object] = {
        "claim_revision_id": str(fact.claim_id),
        "evidence_revision_id": str(fact.evidence_id),
        "claim_text": fact.claim_text,
        "evidence_text": fact.evidence_text,
    }
    return [
        (issue["path"], issue["code"]) for issue in check_statements(body, [pinned], {}, "base")
    ]


def test_citation_does_not_support_invented_kubernetes_metric() -> None:
    body, fact = example()
    assert _base_issues(body, fact) == []
    wire = body.model_dump(mode="json")
    wire["sections"][0]["blocks"][0]["text"] = "Led Kubernetes migration improving uptime by 99%."
    assert _base_issues(parse_body(wire), fact) == [
        ("/sections/0/blocks/0", "statement_not_in_claim")
    ]


@pytest.mark.postgres
@pytest.mark.parametrize("publication", ["synthetic_stored_revision", "benchmark"])
def test_stored_base_checker_blocks_literal_claim_substrings(
    catalog: SourceContext, publication: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Persist legacy inputs below admission so the later checker remains independently tested."""
    document = (
        _base(catalog, _profile(catalog)) if publication == "synthetic_stored_revision" else None
    )
    profile = _legacy_literal_profile(catalog, catalog.client.get("/api/v1/profile").json())
    claim = profile["current_revision"]["claims"][-1]["revision"]
    evidence = claim["evidence_links"][0]["evidence"]
    fact = Fact(
        claim_id=UUID(claim["id"]),
        evidence_id=UUID(evidence["id"]),
        claim_text="SQL",
        evidence_text=evidence["payload"]["text"],
        source=evidence["payload"]["source"],
        claim_sha256=claim["content_sha256"],
        evidence_sha256=evidence["content_sha256"],
        status="supported",
        claim_verb=None,
    )
    body = DocumentBody(
        title="Base résumé",
        sections=[
            DocumentSection(
                key="skills",
                heading="Skills",
                blocks=[
                    DocumentBlock(
                        block_key="legacy-sql",
                        text="SQL",
                        claim_kind="factual",
                        claim_revision_ids=[fact.claim_id],
                        evidence_revision_ids=[fact.evidence_id],
                    )
                ],
            )
        ],
    )
    if publication == "benchmark":
        prepared = documents.PreparedDocument(
            UUID(profile["current_revision"]["id"]), body, (fact,)
        )
        with catalog.runtime.transaction() as conn:
            with pytest.raises(Problem) as refused:
                documents.publish_document(conn, catalog.owner_id, uuid4(), prepared)
            assert refused.value.status == 409
            assert refused.value.code == "unsupported_claims"
            assert "SQL" in (refused.value.detail or "")
            assert (
                conn.execute(
                    text("SELECT count(*) FROM bonafides.document_drafts WHERE owner_id=:owner"),
                    {"owner": catalog.owner_id},
                ).scalar_one()
                == 0
            )
        return

    assert document is not None
    source = {
        **document,
        **document["current_revision"],
        "profile_revision_id": profile["current_revision"]["id"],
        "provenance_snapshot": {
            **document["current_revision"]["provenance_snapshot"],
            "facts": [
                {
                    "claim_revision_id": str(fact.claim_id),
                    "evidence_revision_id": str(fact.evidence_id),
                    "claim_text": fact.claim_text,
                    "evidence_text": fact.evidence_text,
                    "source": fact.source,
                    "claim_sha256": fact.claim_sha256,
                    "evidence_sha256": fact.evidence_sha256,
                    "status": fact.status,
                    "claim_verb": None,
                    "category": "skill",
                    "structured_fields": {},
                    "printable_text": fact.claim_text,
                }
            ],
        },
    }
    with monkeypatch.context() as patch, catalog.runtime.transaction() as conn:
        patch.setattr(document_generation, "validation_issues", lambda *_args: [])
        document_generation.append_revision(
            conn,
            catalog.owner_id,
            UUID(document["id"]),
            body,
            source,
            "manual",
            "Synthetic legacy revision",
        )
    document = _document(catalog, document["id"])
    revision = document["current_revision"]
    assert revision["validation_issues"] == [
        {
            "path": "/sections/0/blocks/0",
            "code": "statement_not_in_claim_evidence",
            "statement": "SQL",
        }
    ]
    assert document["unsupported_claims"] and not document["reviewed"]
    review = _review(catalog, document)
    assert review.status_code == 409 and review.json()["code"] == "unsupported_claims"
    render = catalog.client.post(
        f"/api/v1/documents/{document['id']}/renders",
        json={
            "revision_id": revision["id"],
            "content_sha256": revision["content_sha256"],
            "format": "pdf",
        },
        headers=catalog.headers(document["version"]),
    )
    assert render.status_code == 409 and render.json()["code"] == "unsupported_claims"


def test_facts_cannot_be_hidden_in_document_title_or_section_heading() -> None:
    body, fact = example()
    wire = body.model_dump(mode="json")
    wire["title"] = "Certified Kubernetes architect"
    wire["sections"][0]["heading"] = "Ten years of management experience"
    assert _base_issues(parse_body(wire), fact) == [
        ("/title", "unestablished_heading"),
        ("/sections/0/heading", "unestablished_heading"),
    ]


@pytest.mark.parametrize("kind", ["intent", "greeting"])
def test_relabeling_facts_as_nonfactual_cannot_bypass_gate(kind: str) -> None:
    body, fact = example()
    wire = body.model_dump(mode="json")
    wire["sections"][0]["blocks"][0]["claim_kind"] = kind
    assert _base_issues(parse_body(wire), fact) == [
        ("/sections/0/blocks/0", "connective_not_declared")
    ]


def test_base_resume_has_no_connective_text() -> None:
    body, fact = example()
    wire = body.model_dump(mode="json")
    wire["sections"][0]["blocks"].append(
        {
            "block_key": "greeting",
            "text": "Sincerely,",
            "claim_kind": "greeting",
            "claim_revision_ids": [],
            "evidence_revision_ids": [],
        }
    )
    assert _base_issues(parse_body(wire), fact) == [
        ("/sections/0/blocks/1", "connective_not_declared")
    ]


def test_foreign_missing_and_extra_evidence_are_unsupported() -> None:
    body, fact = example()
    for ids in ([], [str(uuid4())], [str(fact.evidence_id), str(uuid4())]):
        wire = body.model_dump(mode="json")
        wire["sections"][0]["blocks"][0]["evidence_revision_ids"] = ids
        assert _base_issues(parse_body(wire), fact) == [
            ("/sections/0/blocks/0", "evidence_not_linked_to_claim")
        ]


def test_http_review_payload_requires_exact_hash_and_uuid() -> None:
    identifier = uuid4()
    assert (
        ReviewRequest.model_validate(
            {"revision_id": str(identifier), "decision": "accepted", "content_sha256": "a" * 64}
        ).revision_id
        == identifier
    )
    assert (
        RenderRequest.model_validate(
            {"revision_id": str(identifier), "content_sha256": "a" * 64, "format": "pdf"}
        ).revision_id
        == identifier
    )
    with pytest.raises(ValidationError):
        ReviewRequest.model_validate(
            {"revision_id": str(identifier), "decision": "accepted", "content_sha256": "invalid"}
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"format": "docx"},
        {"format": "html"},
        {"format": None},
        {"content_sha256": "A" * 64},
        {"content_sha256": "invalid"},
        {"document_revision_id": str(uuid4())},
    ],
)
def test_public_render_request_rejects_wrong_format_hash_and_aliases(
    changes: dict[str, object],
) -> None:
    wire: dict[str, object] = {
        "revision_id": str(uuid4()),
        "content_sha256": "a" * 64,
        "format": "pdf",
    }
    with pytest.raises(ValidationError):
        RenderRequest.model_validate(wire | changes)


@pytest.mark.parametrize("missing", ["format", "content_sha256", "both"])
def test_public_render_request_rejects_old_incomplete_payloads(missing: str) -> None:
    wire: dict[str, object] = {
        "revision_id": str(uuid4()),
        "content_sha256": "a" * 64,
        "format": "pdf",
    }
    for field in ("format", "content_sha256") if missing == "both" else (missing,):
        del wire[field]
    with pytest.raises(ValidationError):
        RenderRequest.model_validate(wire)


@pytest.mark.postgres
def test_typed_pdf_export_fences_hash_format_version_and_idempotency(
    catalog: SourceContext,
) -> None:
    document = _base(catalog, _profile(catalog))
    reviewed = _review(catalog, document)
    assert reviewed.status_code == 200, reviewed.text
    document = _document(catalog, document["id"])
    revision = document["current_revision"]
    path = f"/api/v1/documents/{document['id']}/renders"
    wire = {
        "revision_id": revision["id"],
        "content_sha256": revision["content_sha256"],
        "format": "pdf",
    }
    for invalid in (
        {"revision_id": revision["id"]},
        {"revision_id": revision["id"], "content_sha256": revision["content_sha256"]},
        wire | {"format": "docx"},
    ):
        response = catalog.client.post(
            path, json=invalid, headers=catalog.headers(document["version"])
        )
        assert response.status_code == 422, response.text
    wrong = catalog.client.post(
        path,
        json=wire | {"content_sha256": "0" * 64},
        headers=catalog.headers(document["version"]),
    )
    assert wrong.status_code == 409 and wrong.json()["code"] == "document_content_changed"
    stale = catalog.client.post(path, json=wire, headers=catalog.headers(document["version"] - 1))
    assert stale.status_code == 412 and stale.json()["code"] == "version_conflict"
    foreign = catalog.client.post(
        f"/api/v1/documents/{uuid4()}/renders",
        json=wire,
        headers=catalog.headers(document["version"]),
    )
    assert foreign.status_code == 404
    with catalog.runtime.transaction() as conn:
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM bonafides.workflow_runs "
                    "WHERE owner_id=:owner AND kind='pdf_render'"
                ),
                {"owner": catalog.owner_id},
            ).scalar_one()
            == 0
        )
    headers = catalog.headers(document["version"])
    queued = catalog.client.post(path, json=wire, headers=headers)
    assert queued.status_code == 202, queued.text
    replay = catalog.client.post(path, json=wire, headers=headers)
    assert replay.status_code == 202 and replay.json() == queued.json()
    changed = catalog.client.post(path, json=wire | {"content_sha256": "0" * 64}, headers=headers)
    assert changed.status_code == 409 and changed.json()["code"] == "idempotency_payload_mismatch"
    with catalog.runtime.transaction() as conn:
        rows = (
            conn.execute(
                text(
                    "SELECT input FROM bonafides.workflow_runs "
                    "WHERE owner_id=:owner AND kind='pdf_render'"
                ),
                {"owner": catalog.owner_id},
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
    assert rows[0]["revision_id"] == revision["id"]
    assert rows[0]["content_sha256"] == revision["content_sha256"]


@pytest.mark.parametrize(
    "reviewed,unsupported,expected",
    [
        (False, [], "review_required"),
        (True, ["/sections/0/blocks/0"], "unsupported_claims"),
    ],
)
def test_workflow_output_is_not_export_permission(
    reviewed: bool, unsupported: list[str], expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bonafides import document_generation, documents

    monkeypatch.setattr(document_generation, "lock_workspace", lambda *args: None)

    body, _ = example()
    revision_id = uuid4()
    document = {
        "current_revision": {
            "id": str(revision_id),
            "body": body.model_dump(mode="json"),
            "content_sha256": body_digest(body),
        },
        "reviewed": reviewed,
        "unsupported_claims": unsupported,
    }
    monkeypatch.setattr(documents, "_document", lambda *args: document)
    connection: Any = None  # The repository boundary is replaced; no SQL is executed.
    with pytest.raises(Problem) as failure:
        prepare_render(connection, uuid4(), uuid4(), revision_id)
    assert failure.value.code == expected


def test_review_does_not_authorize_a_different_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    from bonafides import document_generation, documents

    monkeypatch.setattr(document_generation, "lock_workspace", lambda *args: None)
    monkeypatch.setattr(document_generation, "export_eligibility", lambda *args: None)

    body, _ = example()
    revision_id = uuid4()
    document = {
        "current_revision": {
            "id": str(revision_id),
            "body": body.model_dump(mode="json"),
            "content_sha256": body_digest(body),
            "template_version": "bonafides-base-v1",
            "provenance_snapshot": {"schema_version": 1},
        },
        "reviewed": True,
        "unsupported_claims": [],
    }
    monkeypatch.setattr(documents, "_document", lambda *args: document)
    connection: Any = None
    with pytest.raises(Problem) as failure:
        prepare_render(connection, uuid4(), uuid4(), uuid4())
    assert failure.value.code == "revision_mismatch"
    prepared = prepare_render(connection, uuid4(), uuid4(), revision_id)
    assert prepared.revision_id == revision_id
    assert prepared.content_sha256 == body_digest(body)


class ParsedHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[str] = []
        self.text: list[str] = []
        self.attributes: list[tuple[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        self.attributes.extend(attrs)

    def handle_data(self, data: str) -> None:
        self.text.append(data)


def test_hostile_html_and_template_expressions_are_literal_text() -> None:
    hostile = '<script>fetch("http://metadata/")</script><img src="file:///secret">{{ 7*7 }}'
    body, _ = example(hostile)
    parsed = ParsedHTML()
    parsed.feed(render_html(body))
    assert "script" not in parsed.tags
    assert "img" not in parsed.tags
    assert hostile in "".join(parsed.text)
    assert not any(name in ("src", "href", "onload") for name, _ in parsed.attributes)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://169.254.169.254/",
        "https://example.org/",
        "data:text/plain,secret",
        "../secret",
    ],
)
def test_renderer_fetcher_denies_every_url_scheme(url: str) -> None:
    with pytest.raises(ValueError, match="renderer_url_denied"):
        deny_url_fetcher(url)


def test_content_addressed_storage_roundtrip_and_owner_boundary(tmp_path: Path) -> None:
    store = BlobStore(tmp_path / "blobs")
    owner = uuid4()
    pdf = b"%PDF-1.7\nsynthetic storage boundary bytes\n%%EOF"
    stored = store.put(owner, pdf)
    assert stored.sha256 == hashlib.sha256(pdf).hexdigest()
    assert store.put(owner, pdf) == stored
    assert store.read(owner, stored.storage_key, stored.sha256, stored.byte_length) == pdf
    with pytest.raises(FileNotFoundError):
        store.read(uuid4(), stored.storage_key, stored.sha256, stored.byte_length)
    with pytest.raises(ValueError, match="invalid_storage_key"):
        store.read(owner, "../../secret", stored.sha256, stored.byte_length)


def test_storage_rejects_symlink_root_intermediate_and_final(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)
    with pytest.raises(OSError):
        BlobStore(alias / "nested").put(uuid4(), b"%PDF-1.7\n")
    owner = uuid4()
    root = tmp_path / "blobs"
    root.mkdir()
    (root / str(owner)).symlink_to(actual, target_is_directory=True)
    with pytest.raises(OSError):
        BlobStore(root).put(owner, b"%PDF-1.7\n")
    with directory(actual) as fd:
        target = tmp_path / "target"
        target.write_bytes(b"secret")
        (actual / "file").symlink_to(target)
        with pytest.raises(OSError):
            read_file(fd, "file", 100)
        atomic_file(fd, "file", b"replacement")
        assert target.read_bytes() == b"secret"
        assert read_file(fd, "file", 100) == b"replacement"


def test_storage_rejects_tampering_and_oversize_reads(tmp_path: Path) -> None:
    owner = uuid4()
    store = BlobStore(tmp_path)
    blob = store.put(owner, b"%PDF-1.7\noriginal")
    (tmp_path / str(owner) / blob.storage_key).write_bytes(b"%PDF-1.7\ntampered")
    with pytest.raises(ValueError, match="blob_integrity_failed"):
        store.read(owner, blob.storage_key, blob.sha256, blob.byte_length)
    with directory(tmp_path) as fd:
        atomic_file(fd, "large", b"12345")
        with pytest.raises(ValueError):
            read_file(fd, "large", 4)


def test_client_requires_receipt_digest_and_never_trusts_pdf_magic_alone(tmp_path: Path) -> None:
    body, _ = example()
    failures: list[BaseException] = []

    def respond() -> None:
        try:
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                requests = list(tmp_path.glob("*.request.json"))
                if requests:
                    request = requests[0]
                    job_id = request.name.split(".")[0]
                    receipt = RenderReceipt(
                        schema_version=1,
                        job_id=job_id,
                        request_sha256=hashlib.sha256(request.read_bytes()).hexdigest(),
                        output_sha256="0" * 64,
                        output_bytes=8,
                        error_code=None,
                    )
                    with directory(tmp_path) as fd:
                        atomic_file(fd, f"{job_id}.pdf", b"%PDF-1.7")
                        atomic_file(fd, f"{job_id}.result.json", receipt.model_dump_json().encode())
                    return
                time.sleep(0.01)
            raise AssertionError("request not published")
        except BaseException as error:
            failures.append(error)

    responder = threading.Thread(target=respond)
    responder.start()
    try:
        with pytest.raises(RenderFailure, match="render_output_invalid"):
            RenderClient(tmp_path, timeout=3).render(body)
    finally:
        responder.join(timeout=3)
    assert not responder.is_alive()
    assert not failures
    assert not list(tmp_path.glob("*.result.json"))


def test_supervisor_timeout_kills_and_reaps_real_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bonafides import render_service

    body, _ = example()
    request = render_service.RenderRequest(
        schema_version=1, template_version="bonafides-base-v1", body=body
    )
    job_id = "a" * 64
    actual_popen = subprocess.Popen
    children: list[subprocess.Popen[bytes]] = []

    def slow_child(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        child = actual_popen([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(subprocess, "Popen", slow_child)
    monkeypatch.setattr(render_service, "RENDER_TIMEOUT", 0.05)
    with directory(tmp_path) as fd:
        atomic_file(fd, f"{job_id}.request.json", request.model_dump_json().encode())
        render_service._supervise(tmp_path, fd, job_id)
        receipt = RenderReceipt.model_validate_json(read_file(fd, f"{job_id}.result.json", 4096))
    assert receipt.error_code == "render_timeout"
    assert receipt.output_bytes == 0
    assert children and children[0].poll() is not None
    with pytest.raises(ProcessLookupError):
        os.kill(children[0].pid, 0)


def test_canary_reports_accessible_file_not_falsely_blocked(tmp_path: Path) -> None:
    canary = tmp_path / "canary"
    canary.write_bytes(b"not a real secret")
    result = isolation_canary(canary, "127.0.0.1", 0)
    assert result["file_blocked"] is False
    assert result["network_blocked"] is True
    assert isolation_canary(tmp_path / "absent", "127.0.0.1", 0)["file_blocked"] is True


def _fit_provenance() -> dict[str, object]:
    from bonafides.document_frame import PrintableFrame, template_digest

    return {
        "schema_version": 2,
        "template_version": "bonafides-evidence-v1",
        "template_sha256": template_digest("bonafides-evidence-v1"),
        "frame": PrintableFrame(display_name="Synthetic owner").model_dump(mode="json"),
    }


@pytest.mark.parametrize("restore_order", [("b", "c"), ("c", "b")])
@pytest.mark.parametrize("reordered", [False, True])
def test_recoverable_blocks_keep_adjacent_order_exact_pins_and_edited_neighbors(
    restore_order: tuple[str, str],
    reordered: bool,
) -> None:
    from bonafides import print_fitting as fitting

    body, _ = example()
    block = body.sections[0].blocks[0]
    body = DocumentBody(
        title=body.title,
        sections=[
            DocumentSection(
                key="first",
                heading="First",
                blocks=[block.model_copy(update={"block_key": key}) for key in ("a", "e")],
            ),
            DocumentSection(
                key="hidden",
                heading="Original heading",
                blocks=[block.model_copy(update={"block_key": key}) for key in ("b", "c")],
            ),
            DocumentSection(
                key="last", heading="Last", blocks=[block.model_copy(update={"block_key": "d"})]
            ),
        ],
    )
    original = body
    fit = fitting.inventory(body, {})
    for key in ("b", "c"):
        body, fit = fitting.change_block(body, fit, "omit", key)
    edited = body.model_copy(update={"title": "Owner-edited title"})
    visible = (
        [
            section.model_copy(update={"blocks": list(reversed(section.blocks))})
            for section in reversed(body.sections)
        ]
        if reordered
        else body.sections
    )
    edited = edited.model_copy(update={"sections": visible})
    provenance: dict[str, object] = {
        **_fit_provenance(),
        "page_fit": fit.model_dump(mode="json"),
    }
    provenance = fitting.reconcile(edited, provenance)
    for key in restore_order:
        edited, fit = fitting.change_block(
            edited, fitting.inventory(edited, provenance), "restore", key
        )
        provenance = {**provenance, "page_fit": fit.model_dump(mode="json")}
    assert edited.title == "Owner-edited title"
    assert [section for section in edited.sections if section.key != "hidden"] == visible
    assert (
        next(section for section in edited.sections if section.key == "hidden")
        == original.sections[1]
    )
    with pytest.raises(Problem, match="block_not_omitted"):
        fitting.change_block(edited, fit, "restore", restore_order[0])
    assert original.sections[1].blocks == [
        block.model_copy(update={"block_key": key}) for key in ("b", "c")
    ]


def test_page_fit_uses_bounded_measured_candidates_and_preserves_above_target_floor() -> None:
    from bonafides import print_fitting as fitting

    body, _ = example()
    block = body.sections[0].blocks[0]
    body = DocumentBody(
        title=body.title,
        sections=[
            DocumentSection(
                key="facts",
                heading="Evidence",
                blocks=[block.model_copy(update={"block_key": f"b-{i}"}) for i in range(128)],
            )
        ],
    )
    provenance = _fit_provenance()
    measured: list[DocumentBody] = []

    def measure(candidate: DocumentBody) -> int:
        measured.append(candidate)
        return (len(candidate.sections[0].blocks) + 19) // 20

    reduced, fit = fitting.reduce_to_target(body, provenance, 2, measure)
    assert reduced.sections[0].blocks == body.sections[0].blocks[:40]
    assert fit.page_count == 2 and len(measured) <= 9
    assert reduced in measured and len(fit.omissions) == 88
    assert [item.block for item in fit.omissions] == body.sections[0].blocks[40:]
    stored = {**provenance, "page_fit": fit.model_dump(mode="json")}
    before = document_generation.revision_digest(reduced, "bonafides-evidence-v1", stored)
    edited = reduced.model_copy(update={"title": "Changed render input"})
    cleared = fitting.reconcile(edited, stored)
    assert fitting.inventory(edited, cleared).page_count is None
    assert fitting.inventory(reduced, fitting.reconcile(reduced, stored)).page_count == 2
    assert document_generation.revision_digest(reduced, "bonafides-evidence-v1", stored) == before
    floor, above = fitting.reduce_to_target(body, provenance, 1, lambda _: 3)
    assert len(floor.sections[0].blocks) == 1 and above.page_count == 3
    assert len(body.sections[0].blocks) == 128


def test_hidden_inventory_shares_visible_bounds_and_rejects_collisions_and_stale_measurement() -> (
    None
):
    from bonafides import print_fitting as fitting

    body, _ = example()
    block = body.sections[0].blocks[0]
    body = DocumentBody(
        title=body.title,
        sections=[
            DocumentSection(
                key="facts",
                heading="Evidence",
                blocks=[block.model_copy(update={"block_key": f"b-{i}"}) for i in range(128)],
            )
        ],
    )
    reduced, fit = fitting.change_block(body, fitting.inventory(body, {}), "omit", "b-127")
    provenance: dict[str, object] = {
        **_fit_provenance(),
        "page_fit": fit.model_dump(mode="json"),
    }
    for key in ("new", "b-127"):
        changed = DocumentBody(
            title=body.title,
            sections=[
                DocumentSection(
                    key="facts",
                    heading="Evidence",
                    blocks=[
                        *reduced.sections[0].blocks,
                        block.model_copy(update={"block_key": key}),
                    ],
                )
            ],
        )
        error_type = ValueError if key == "new" else Problem
        with pytest.raises(error_type):
            fitting.reconcile(changed, provenance)
    digest = document_generation.revision_digest(reduced, "bonafides-evidence-v1", provenance)
    hidden = fit.omissions[0].model_copy(
        update={"block": fit.omissions[0].block.model_copy(update={"text": "Exact hidden edit"})}
    )
    changed_fit = fit.model_copy(update={"omissions": [hidden]})
    assert (
        document_generation.revision_digest(
            reduced,
            "bonafides-evidence-v1",
            {**provenance, "page_fit": changed_fit.model_dump(mode="json")},
        )
        != digest
    )
    bad_measurement = fit.model_copy(update={"page_count": 1, "measured_content_sha256": "0" * 64})
    with pytest.raises(ValueError, match="page_fit_measurement_mismatch"):
        document_generation.revision_digest(
            reduced,
            "bonafides-evidence-v1",
            {**provenance, "page_fit": bad_measurement.model_dump(mode="json")},
        )
    with pytest.raises(ValueError, match="page_fit_requires_supported_revision"):
        document_generation.revision_digest(
            reduced,
            documents.TEMPLATE_VERSION,
            {"schema_version": 1, "page_fit": fit.model_dump(mode="json")},
        )


def _archive_pair(
    context: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    profile, application = _setup_pair(context)
    _route(monkeypatch, tmp_path)
    preview = _preview(context, profile, application)
    task = _queue_pair(context, preview)
    suitability.execute_model(
        context.runtime,
        context.identity.settings,
        task,
        threading.Event(),
        FixtureModel(_candidate(preview)),
    )
    records = _pair_documents(context, application)["items"]
    for record in records:
        response = _review(context, record)
        assert response.status_code == 200, response.text
    return application, records


def _queue_archive(context: SourceContext) -> Any:
    preview = context.client.get("/api/v1/document-archives/preview")
    assert preview.status_code == 200, preview.text
    value = preview.json()
    response = context.client.post(
        "/api/v1/document-archives",
        json={"manifest_sha256": value["manifest_sha256"]},
        headers=context.headers(value["workspace_version"]),
    )
    assert response.status_code == 202, response.text
    return _claim(context, response.json()["id"])


@pytest.mark.parametrize("value", ["../CON. ", "A/B\\C", "\u202eevil\x00", "  ...  ", "ＮＵＬ"])
def test_archive_components_are_portable_and_never_paths(value: str) -> None:
    component = document_archives.safe_component(value, "Company")
    assert component and component not in {".", ".."}
    assert not any(character in component for character in '/\\<>:"|?*\x00')
    assert component == component.rstrip(". ")
    assert len(component) <= 81
    assert component.casefold() not in {"con", "nul"}


@pytest.mark.postgres
def test_archive_all_current_membership_review_and_deleted_partner(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    empty = catalog.client.get("/api/v1/document-archives/preview")
    assert empty.status_code == 409 and empty.json()["code"] == "document_archive_empty"
    application, records = _archive_pair(catalog, monkeypatch, tmp_path)
    with catalog.runtime.transaction() as conn:
        frozen, renders = document_archives.manifest(conn, catalog.owner_id)
        initial_digest = document_archives.digest_manifest(frozen)
        assert len(renders) == 2
    with catalog.identity.admin.transaction() as conn:
        conn.execute(
            text(
                "UPDATE bonafides.applications SET archived_at=clock_timestamp() WHERE owner_id=:owner AND id=:id"
            ),
            {"owner": catalog.owner_id, "id": UUID(application["id"])},
        )
    preview = catalog.client.get("/api/v1/document-archives/preview")
    assert preview.status_code == 200 and preview.json()["document_count"] == 2
    assert preview.json()["manifest_sha256"] == initial_digest
    removed = records[0]
    with catalog.identity.admin.transaction() as conn:
        conn.execute(
            text(
                "UPDATE bonafides.document_drafts SET deleted_at=clock_timestamp(),version=version+1 WHERE owner_id=:owner AND id=:id"
            ),
            {"owner": catalog.owner_id, "id": UUID(removed["id"])},
        )
    preview = catalog.client.get("/api/v1/document-archives/preview")
    assert preview.status_code == 200 and preview.json()["document_count"] == 1
    assert preview.json()["manifest_sha256"] != initial_digest
    with catalog.identity.admin.transaction() as conn:
        conn.execute(
            text(
                "DELETE FROM bonafides.document_reviews WHERE owner_id=:owner AND document_revision_id=:id"
            ),
            {"owner": catalog.owner_id, "id": UUID(records[1]["current_revision"]["id"])},
        )
    refused = catalog.client.get("/api/v1/document-archives/preview")
    assert refused.status_code == 409 and refused.json()["code"] == "review_required"
    assert records[1]["id"] in refused.json()["detail"]


@pytest.mark.postgres
def test_archive_zip_success_integrity_expiry_cleanup_and_shared_blob(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, records = _archive_pair(catalog, monkeypatch, tmp_path)
    documents_before = [_document(catalog, record["id"]) for record in records]
    profile_before = catalog.client.get("/api/v1/profile").json()
    pdf = b"%PDF-1.7\nsynthetic archive boundary payload\n"
    monkeypatch.setattr(RenderClient, "render", lambda self, *args: pdf)
    shared = BlobStore(catalog.identity.settings.blob_root).put(catalog.owner_id, pdf)
    task = _queue_archive(catalog)
    with catalog.runtime.transaction() as conn:
        # The archive command bumps workspace version, not the frozen manifest digest.
        document_archives.check_manifest(conn, catalog.owner_id, task.input)
    Worker(catalog.runtime, catalog.identity.settings)._execute(task, threading.Event())
    workflow = catalog.workflow(task.id)
    assert workflow["state"] == "succeeded", workflow
    assert workflow["mode"] == "document_archive"
    assert workflow["progress"] == {"completed": 2, "total": 2}
    assert workflow["result"]["archive_document_count"] == 2
    assert [_document(catalog, record["id"]) for record in records] == documents_before
    assert catalog.client.get("/api/v1/profile").json() == profile_before
    route = f"/api/v1/document-archives/{task.id}/download"
    response = catalog.client.get(route)
    assert response.status_code == 200 and response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert len(archive.namelist()) == 2
        assert len({name.casefold() for name in archive.namelist()}) == 2
        assert all(len(Path(name).parts) == 2 for name in archive.namelist())
        assert all(archive.read(name) == pdf for name in archive.namelist())
    assert document_archives.expire_archives(catalog.runtime, catalog.identity.settings) == 0
    with catalog.identity.admin.transaction() as conn:
        conn.execute(
            text("""UPDATE bonafides.workflow_runs SET result=jsonb_set(result,
            '{archive_expires_at}',to_jsonb((clock_timestamp()-interval '1 second')::text))
            WHERE owner_id=:owner AND id=:id"""),
            {"owner": catalog.owner_id, "id": task.id},
        )
    expired = catalog.client.get(route)
    assert expired.status_code == 410 and expired.json()["code"] == "document_archive_expired"
    assert document_archives.expire_archives(catalog.runtime, catalog.identity.settings) == 1
    assert (
        BlobStore(catalog.identity.settings.blob_root).read(
            catalog.owner_id, shared.storage_key, shared.sha256, shared.byte_length
        )
        == pdf
    )
    with catalog.runtime.transaction() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM bonafides.document_artifacts WHERE owner_id=:owner"),
                {"owner": catalog.owner_id},
            ).scalar_one()
            == 0
        )


@pytest.mark.postgres
@pytest.mark.parametrize("change", ["label", "review", "cancel", "namespace", "lease"])
def test_archive_render_boundary_drift_never_publishes_partial(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, change: str
) -> None:
    application, records = _archive_pair(catalog, monkeypatch, tmp_path)
    task = _queue_archive(catalog)
    changed = False

    def render(self: RenderClient, *args: object) -> bytes:
        nonlocal changed
        if not changed:
            changed = True
            with catalog.identity.admin.transaction() as conn:
                if change == "label":
                    conn.execute(
                        text(
                            "UPDATE bonafides.job_postings SET company='Changed company',version=version+1 WHERE owner_id=:owner AND id=:id"
                        ),
                        {"owner": catalog.owner_id, "id": UUID(application["posting_id"])},
                    )
                elif change == "review":
                    conn.execute(
                        text(
                            "DELETE FROM bonafides.document_reviews WHERE owner_id=:owner AND document_revision_id=:id"
                        ),
                        {
                            "owner": catalog.owner_id,
                            "id": UUID(records[0]["current_revision"]["id"]),
                        },
                    )
                elif change == "namespace":
                    conn.execute(
                        text(
                            "UPDATE bonafides.installation_state SET namespace_id=gen_random_uuid(),maintenance_epoch=maintenance_epoch+1"
                        )
                    )
                elif change == "lease":
                    conn.execute(
                        text(
                            "UPDATE bonafides.workflow_runs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE owner_id=:owner AND id=:id"
                        ),
                        {"owner": catalog.owner_id, "id": task.id},
                    )
                else:
                    from bonafides.workflows import cancel_workflow

                    cancel_workflow(conn, catalog.owner_id, task.id)
        return b"%PDF-1.7\nsynthetic boundary payload\n"

    monkeypatch.setattr(RenderClient, "render", render)
    Worker(catalog.runtime, catalog.identity.settings)._execute(task, threading.Event())
    workflow = catalog.workflow(task.id)
    assert workflow["state"] != "succeeded"
    assert not workflow["result"]
    response = catalog.client.get(f"/api/v1/document-archives/{task.id}/download")
    assert response.status_code == 404
    folder = catalog.identity.settings.blob_root / "document_archives" / str(catalog.owner_id)
    with directory(folder) as descriptor:
        assert os.listdir(descriptor) == []


@pytest.mark.postgres
def test_archive_inventory_spans_applications_and_added_members_invalidate_snapshot(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _archive_pair(catalog, monkeypatch, tmp_path)
    with catalog.runtime.transaction() as conn:
        snapshot, _ = document_archives.manifest(conn, catalog.owner_id)
    application = _application(
        catalog,
        description_text=SOURCE,
        canonical_url="https://careers.fictional-employer.com/archive-second",
    )
    _write(catalog, {"id": application["posting_id"]}, [_requirement(1, "Linux required.")])
    profile = catalog.client.get("/api/v1/profile").json()
    preview = _preview(catalog, profile, application)
    task = _queue_pair(catalog, preview)
    suitability.execute_model(
        catalog.runtime,
        catalog.identity.settings,
        task,
        threading.Event(),
        FixtureModel(_candidate(preview)),
    )
    # A newly saved but unreviewed member blocks the whole inventory, not just that application.
    blocked = catalog.client.get("/api/v1/document-archives/preview")
    assert blocked.status_code == 409 and blocked.json()["code"] == "review_required"
    for record in _pair_documents(catalog, application)["items"]:
        assert _review(catalog, record).status_code == 200
    preview = catalog.client.get("/api/v1/document-archives/preview")
    assert preview.status_code == 200 and preview.json()["document_count"] == 4
    with catalog.runtime.transaction() as conn:
        inventory, _ = document_archives.manifest(conn, catalog.owner_id)
    paths = [str(item["path"]) for item in cast(list[dict[str, object]], inventory["documents"])]
    assert len({path.casefold() for path in paths}) == 4
    with pytest.raises(Problem) as changed, catalog.runtime.transaction() as conn:
        document_archives.check_manifest(
            conn,
            catalog.owner_id,
            {"manifest": snapshot, "manifest_sha256": document_archives.digest_manifest(snapshot)},
        )
    assert changed.value.code == "document_archive_source_changed"
    monkeypatch.setattr(document_archives, "MAX_DOCUMENTS", 3)
    capped = catalog.client.get("/api/v1/document-archives/preview")
    assert (
        capped.status_code == 409 and capped.json()["code"] == "document_archive_too_many_documents"
    )


@pytest.mark.postgres
@pytest.mark.parametrize("change", ["namespace", "label", "bytes"])
def test_archive_download_revalidates_namespace_source_and_integrity(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, change: str
) -> None:
    application, _ = _archive_pair(catalog, monkeypatch, tmp_path)
    monkeypatch.setattr(RenderClient, "render", lambda self, *args: b"%PDF-1.7\nboundary\n")
    task = _queue_archive(catalog)
    Worker(catalog.runtime, catalog.identity.settings)._execute(task, threading.Event())
    workflow = catalog.workflow(task.id)
    assert workflow["state"] == "succeeded", workflow
    if change == "bytes":
        folder = catalog.identity.settings.blob_root / "document_archives" / str(catalog.owner_id)
        with directory(folder) as descriptor:
            atomic_file(descriptor, workflow["result"]["archive_filename"], b"corrupted archive")
    else:
        with catalog.identity.admin.transaction() as conn:
            if change == "namespace":
                conn.execute(
                    text("UPDATE bonafides.installation_state SET namespace_id=gen_random_uuid()")
                )
            else:
                conn.execute(
                    text(
                        "UPDATE bonafides.job_postings SET title='Changed role',version=version+1 WHERE owner_id=:owner AND id=:id"
                    ),
                    {"owner": catalog.owner_id, "id": UUID(application["posting_id"])},
                )
    response = catalog.client.get(f"/api/v1/document-archives/{task.id}/download")
    expected = {
        "namespace": (410, "document_archive_unavailable"),
        "label": (409, "document_archive_source_changed"),
        "bytes": (409, "document_archive_integrity_failed"),
    }[change]
    assert (response.status_code, response.json()["code"]) == expected


@pytest.mark.postgres
def test_archive_size_failure_and_crash_orphan_cleanup_never_touch_shared_storage(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _archive_pair(catalog, monkeypatch, tmp_path)
    monkeypatch.setattr(RenderClient, "render", lambda self, *args: b"%PDF-1.7\nboundary\n")
    task = _queue_archive(catalog)
    monkeypatch.setattr(document_archives, "MAX_ARCHIVE_BYTES", 1)
    Worker(catalog.runtime, catalog.identity.settings)._execute(task, threading.Event())
    workflow = catalog.workflow(task.id)
    assert workflow["state"] == "permanent_failure"
    assert workflow["last_error_code"] == "document_archive_too_large"
    folder = catalog.identity.settings.blob_root / "document_archives" / str(catalog.owner_id)
    with directory(folder) as descriptor:
        assert os.listdir(descriptor) == []
        atomic_file(descriptor, f"{uuid4()}.1.zip", b"crashed completed file")
        atomic_file(descriptor, f".staging-{uuid4().hex}", b"crashed partial file")
    assert document_archives.expire_archives(catalog.runtime, catalog.identity.settings) == 2
    with directory(folder) as descriptor:
        assert os.listdir(descriptor) == []


@pytest.mark.postgres
def test_archive_private_spool_refuses_intermediate_symlink(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _archive_pair(catalog, monkeypatch, tmp_path)
    task = _queue_archive(catalog)
    outside = tmp_path / "outside-archive-storage"
    outside.mkdir()
    marker = outside / "do-not-touch"
    marker.write_bytes(b"unrelated content")
    spool = catalog.identity.settings.blob_root / "document_archives"
    spool.parent.mkdir(parents=True, exist_ok=True)
    spool.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(RenderClient, "render", lambda self, *args: b"%PDF-1.7\nboundary\n")
    Worker(catalog.runtime, catalog.identity.settings)._execute(task, threading.Event())
    workflow = catalog.workflow(task.id)
    assert workflow["state"] != "succeeded"
    assert marker.read_bytes() == b"unrelated content"
    with directory(outside) as descriptor:
        assert os.listdir(descriptor) == ["do-not-touch"]


@pytest.mark.postgres
def test_archive_queue_idempotency_and_entry_revalidation(
    catalog: SourceContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, records = _archive_pair(catalog, monkeypatch, tmp_path)
    preview = catalog.client.get("/api/v1/document-archives/preview").json()
    headers = catalog.headers(preview["workspace_version"])
    body = {"manifest_sha256": preview["manifest_sha256"]}
    first = catalog.client.post("/api/v1/document-archives", json=body, headers=headers)
    duplicate = catalog.client.post("/api/v1/document-archives", json=body, headers=headers)
    assert first.status_code == duplicate.status_code == 202
    assert first.json()["id"] == duplicate.json()["id"]
    stale_version = catalog.client.post(
        "/api/v1/document-archives",
        json=body,
        headers=catalog.headers(preview["workspace_version"]),
    )
    assert stale_version.status_code == 412
    current = catalog.client.get("/api/v1/document-archives/preview").json()
    wrong_digest = catalog.client.post(
        "/api/v1/document-archives",
        json={"manifest_sha256": "0" * 64},
        headers=catalog.headers(current["workspace_version"]),
    )
    assert wrong_digest.status_code == 409
    assert wrong_digest.json()["code"] == "document_archive_source_changed"
    task = _claim(catalog, first.json()["id"])
    with catalog.identity.admin.transaction() as conn:
        conn.execute(
            text(
                "DELETE FROM bonafides.document_reviews WHERE owner_id=:owner AND document_revision_id=:id"
            ),
            {"owner": catalog.owner_id, "id": UUID(records[0]["current_revision"]["id"])},
        )

    def forbidden_render(self: RenderClient, *args: object) -> bytes:
        raise AssertionError("An ineligible snapshot must fail before renderer admission")

    monkeypatch.setattr(RenderClient, "render", forbidden_render)
    Worker(catalog.runtime, catalog.identity.settings)._execute(task, threading.Event())
    workflow = catalog.workflow(task.id)
    assert workflow["state"] == "permanent_failure"
    assert workflow["last_error_code"] == "review_required"
    assert catalog.client.get(f"/api/v1/document-archives/{task.id}/download").status_code == 404
