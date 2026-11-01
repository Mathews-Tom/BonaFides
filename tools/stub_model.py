# SPDX-License-Identifier: MIT OR Apache-2.0
"""Deterministic Ollama-compatible stub model for tests, CI and local browser proofs.

This is a development tool, never part of the shipped image or request path, and never
enabled by default. It answers the existing local-model adapter through an ordinary
route file. Its output is derived only from the projection it receives:

- ``valid``: every claim becomes a factual block citing it, worded as the claim's text.
- ``absent-claim``: one factual block cites a claim id that is not in the profile.
- ``posting-only-skill``: one factual block asserts a term found only in the posting.
- ``malformed-once``: schema-invalid until the request carries a correction.
- ``malformed``: always schema-invalid.
- ``truncated``: a length-limited, incomplete response.

``serve`` uses only the standard library so it runs as its own process or container.
``route`` writes a route file whose qualification receipt is fixture-only: it attests
nothing about any real model.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import re
import sys
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import NAMESPACE_URL, uuid5

MODEL = "bonafides-stub:fixture"
MODEL_SHA256 = hashlib.sha256(b"bonafides-stub-model").hexdigest()
RUNTIME_VERSION = "bonafides-stub-1"
SCENARIOS = (
    "valid",
    "absent-claim",
    "posting-only-skill",
    "malformed-once",
    "malformed",
    "truncated",
)
ABSENT_CLAIM_ID = str(uuid5(NAMESPACE_URL, "bonafides-stub-model/absent-claim"))
FIXTURE_REVIEWER = "fixture-only-stub-route-not-a-qualification"


class StubFailure(Exception):
    """The projection cannot express the requested scenario."""


def _block(key: int, kind: str, text: str, claim: str | None) -> dict[str, object]:
    return {"block_key": f"b-{key}", "kind": kind, "text": text, "claim_id": claim}


def _pair(projection: dict[str, object]) -> dict[str, Any]:
    claims = _items(projection, "claims")
    posting = projection["posting"]
    assert claims and isinstance(posting, dict)
    resume = [
        _block(index, "factual", str(claim["text"]), str(claim["id"]))
        for index, claim in enumerate(claims, start=1)
    ]
    key = len(resume)
    letter = [_block(key + 1, "connective", "Dear Hiring team,", None)]
    if posting.get("title") and posting.get("organization"):
        letter.append(
            _block(
                key + 2,
                "connective",
                f"I am applying for the {posting['title']} role at {posting['organization']}.",
                None,
            )
        )
    for claim in claims[:2]:
        letter.append(
            _block(key + len(letter) + 1, "factual", str(claim["text"]), str(claim["id"]))
        )
    letter.append(_block(key + len(letter) + 1, "connective", "Sincerely,", None))
    return {
        "schema_version": "2",
        "stage": "generate_documents",
        "documents": [
            {"kind": "resume", "sections": [{"heading": "Experience", "blocks": resume}]},
            {"kind": "cover_letter", "sections": [{"heading": "Letter", "blocks": letter}]},
        ],
        "gaps": [],
        "warnings": [],
    }


def _items(projection: dict[str, object], name: str) -> list[dict[str, object]]:
    values = projection[name]
    assert isinstance(values, list) and all(isinstance(item, dict) for item in values)
    return values


def _posting_only_term(projection: dict[str, object]) -> str:
    """A capitalised posting term that no claim or visible evidence text contains."""
    posting = projection["posting"]
    assert isinstance(posting, dict)
    owned = " ".join(
        str(item.get("text", ""))
        for name in ("claims", "evidence")
        for item in _items(projection, name)
    ).casefold()
    # Requirement wording comes first: it is the posting text a model is tempted to echo.
    sources = [str(item["text"]) for item in _items(projection, "requirements")]
    for source in [*sources, str(posting["source_text"])]:
        for match in re.finditer(r"\b[A-Z][A-Za-z0-9+#]{2,}\b", source):
            if match.group(0).casefold() not in owned:
                return match.group(0)
    raise StubFailure("posting has no term absent from the owner's claims and evidence")


def respond(scenario: str, request: dict[str, object]) -> dict[str, object]:
    """The complete /api/chat envelope for one request under one scenario."""
    messages = request["messages"]
    assert isinstance(messages, list) and len(messages) in {2, 3}
    projection: dict[str, object] = json.loads(messages[1]["content"])
    corrected = len(messages) == 3
    done_reason = "stop"
    if scenario == "malformed" or (scenario == "malformed-once" and not corrected):
        content = json.dumps(
            {"schema_version": "2", "stage": "generate_documents", "documents": [], "gaps": []}
        )
    elif scenario == "truncated":
        done_reason = "length"
        content = json.dumps(_pair(projection))[:200]
    else:
        pair = _pair(projection)
        if scenario == "absent-claim":
            pair["documents"][0]["sections"][0]["blocks"][0]["claim_id"] = ABSENT_CLAIM_ID
        elif scenario == "posting-only-skill":
            claim = str(_items(projection, "claims")[0]["id"])
            statement = f"Hands-on experience with {_posting_only_term(projection)}."
            pair["documents"][0]["sections"][0]["blocks"].append(
                _block(900, "factual", statement, claim)
            )
        content = json.dumps(pair)
    return {
        "model": MODEL,
        "done": True,
        "done_reason": done_reason,
        "message": {"role": "assistant", "content": content},
    }


class StubModel:
    """Scenario selection and an in-memory log of every chat request received."""

    def __init__(self, scenario: str = "valid", control: Path | None = None) -> None:
        self.scenario = scenario
        self.control = control
        self.requests: list[dict[str, Any]] = []
        self.on_chat: Callable[[int], None] | None = None
        self._guard = Lock()
        self.current()

    def current(self) -> str:
        selected = (
            self.control.read_text(encoding="utf-8").strip() if self.control else self.scenario
        )
        if selected not in SCENARIOS:
            raise StubFailure(f"unknown scenario {selected!r}")
        return selected

    def chat(self, request: dict[str, Any]) -> tuple[int, dict[str, object]]:
        selected = self.current()
        with self._guard:
            self.requests.append(request)
            number = len(self.requests)
        corrected = str(len(request["messages"]) == 3).lower()
        print(
            f"stub-model chat call={number} scenario={selected} correction={corrected}", flush=True
        )
        if self.on_chat is not None:
            self.on_chat(number)
        try:
            return 200, respond(selected, request)
        except StubFailure as exc:
            print(f"stub-model chat call={number} failed: {exc}", flush=True)
            return 500, {"error": "stub scenario unavailable"}


def make_server(host: str, port: int, stub: StubModel) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, value: object) -> None:
            encoded = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self) -> None:
            if self.path == "/api/tags":
                self._send(200, {"models": [{"name": MODEL, "digest": MODEL_SHA256}]})
            elif self.path == "/api/version":
                self._send(200, {"version": RUNTIME_VERSION})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self) -> None:
            if self.path != "/api/chat":
                self._send(404, {"error": "not found"})
                return
            length = int(self.headers["Content-Length"])
            self._send(*stub.chat(json.loads(self.rfile.read(length))))

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def serve(host: str, port: int, scenario: str, control: Path | None) -> None:
    stub = StubModel(scenario, control)
    server = make_server(host, port, stub)
    print(f"stub-model listening on {host}:{port} scenario={stub.current()}", flush=True)
    server.serve_forever()


def route(endpoint: str, address: str, output: Path, deadline: int) -> None:
    """Write a 0600 route file with a fixture-only receipt for document generation."""
    from bonafides.local_model import PROMPT_VERSIONS, load_route

    data: dict[str, object] = {
        "version": 1,
        "endpoint": endpoint,
        "approved_ips": [address],
        "model": MODEL,
        "model_sha256": MODEL_SHA256,
        "runtime_version": RUNTIME_VERSION,
        "model_license": "fixture-only-stub-not-a-model",
        "operator_controlled": True,
        "authorized": True,
        "cloud_disabled": True,
        "context_window": 32768,
        "max_input_tokens": 12000,
        "max_output_tokens": 6000,
        "max_request_bytes": 131072,
        "max_response_bytes": 262144,
        "metadata_authorized": True,
        "private_network_http_authorized": not ipaddress.ip_address(address).is_loopback,
        "deadline_seconds": deadline,
        "framing_token_overhead": 512,
        "qualification_receipts": {},
        "ca_file": None,
    }

    def write() -> None:
        output.write_text(json.dumps(data, indent=2), encoding="utf-8")
        output.chmod(0o600)

    write()
    initial = load_route(output)
    assert initial is not None
    stage = "generate_documents"
    data["qualification_receipts"] = {
        stage: {
            "status": "passed",
            "capability_sha256": initial.capability_sha256,
            "receipt_sha256": hashlib.sha256(
                f"fixture-only:{initial.capability_sha256}:{PROMPT_VERSIONS[stage]}".encode()
            ).hexdigest(),
            "reviewed_by": FIXTURE_REVIEWER,
            "reviewed_at": "2026-11-01T00:00:00Z",
        }
    }
    write()
    loaded = load_route(output)
    assert loaded is not None and loaded.qualified_for(stage)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic stub model for BonaFides tests")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("serve", help="serve the stub model")
    run.add_argument("--host", default="127.0.0.1")
    run.add_argument("--port", type=int, required=True)
    run.add_argument("--scenario", choices=SCENARIOS, default="valid")
    run.add_argument("--control-file", type=Path, help="file holding the current scenario name")
    write = commands.add_parser("route", help="write a fixture-only route file for the stub")
    write.add_argument("--endpoint", required=True)
    write.add_argument("--approved-ip", required=True)
    write.add_argument("--output", type=Path, required=True)
    write.add_argument("--deadline-seconds", type=int, default=60)
    arguments = parser.parse_args(argv)
    if arguments.command == "serve":
        serve(arguments.host, arguments.port, arguments.scenario, arguments.control_file)
    else:
        route(
            arguments.endpoint, arguments.approved_ip, arguments.output, arguments.deadline_seconds
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
