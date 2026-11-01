# SPDX-License-Identifier: MIT OR Apache-2.0
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import threading
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from test_document_generation import _base, _document, _review
from test_identity import identity as identity
from test_sources import SourceContext
from test_sources import catalog as catalog
from test_suitability import _profile

from bonafides import documents
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
        ("/sections/0/blocks/0", "statement_not_in_claim_evidence")
    ]


@pytest.mark.postgres
@pytest.mark.parametrize("publication", ["synthetic_stored_revision", "benchmark"])
def test_stored_base_checker_blocks_literal_claim_substrings(
    catalog: SourceContext, publication: str
) -> None:
    """Checker regression with a synthetic excerpt, not an owner rebuild admission guarantee."""
    profile = catalog.client.get("/api/v1/profile").json()
    evidence = catalog.client.post(
        "/api/v1/evidence",
        json={
            "payload": {"text": "Tuned PostgreSQL queries", "source": "Synthetic owner notes"},
            "provenance": {
                "source_type": "owner_statement",
                "privacy_class": "private",
                "employer_owned": False,
            },
        },
        headers=catalog.headers(profile["version"]),
    )
    assert evidence.status_code == 200, evidence.text
    profile = evidence.json()
    claim = catalog.client.post(
        "/api/v1/claims",
        json={
            "kind": "skill",
            "payload": {"text": "SQL"},
            "evidence_links": [
                {
                    "evidence_revision_id": profile["evidence"][0]["revision"]["id"],
                    "support_kind": "user_asserted",
                    "excerpt": "SQL",
                }
            ],
        },
        headers=catalog.headers(profile["version"]),
    )
    assert claim.status_code == 200, claim.text
    profile = claim.json()

    if publication == "benchmark":
        with catalog.runtime.transaction() as conn:
            prepared = documents.prepare_synthetic(
                conn, catalog.owner_id, UUID(profile["current_revision"]["id"])
            )
            with pytest.raises(Problem) as refused:
                documents.publish_document(conn, catalog.owner_id, uuid4(), prepared)
            assert refused.value.status == 409
            assert refused.value.code == "unsupported_claims"
            assert refused.value.detail == (
                "Unsupported statements: SQL (statement_not_in_claim_evidence)"
            )
            assert (
                conn.execute(
                    text("SELECT count(*) FROM bonafides.document_drafts WHERE owner_id=:owner"),
                    {"owner": catalog.owner_id},
                ).scalar_one()
                == 0
            )
        return

    document = _base(catalog, profile)
    revision = document["current_revision"]
    assert [block["text"] for block in revision["body"]["sections"][0]["blocks"]] == ["SQL"]
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
