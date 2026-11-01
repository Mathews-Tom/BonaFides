# SPDX-License-Identifier: MIT OR Apache-2.0
"""Deterministic Ollama-compatible stub model for tests, CI and local browser proofs.

This is a development tool, never part of the shipped image or request path, and never
enabled by default. It answers the existing local-model adapter through an ordinary
route file, for pair generation and for section regeneration (a projection with a
``target``). Its output is derived only from the projection it receives:

- ``valid``: every claim becomes a factual block citing it, worded as the claim's text;
  a regenerated section rewords each cited target block as its claim's text and keeps
  connective target blocks.
- ``absent-claim``: one factual block cites a claim id that is not in the profile.
- ``posting-only-skill``: one factual block asserts a term found only in the posting.
- ``inflated``: one factual block is a whole-phrase cut of a claim's own text that starts
  at a later, stronger ownership verb ("Assisted the team that led X" -> "Led X"), so it is
  part of its claim and only the verb rule blocks it.
- ``malformed-once``: schema-invalid until the request carries a correction.
- ``malformed``: always schema-invalid.
- ``truncated``: a length-limited, incomplete response.

``serve`` uses only the standard library except for ``inflated``, which reads the
installed application's ownership-verb ranking. ``route`` writes a route file whose
qualification receipts for both document stages are fixture-only: they attest nothing
about any real model.
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
from typing import Any, cast
from uuid import NAMESPACE_URL, uuid5

MODEL = "bonafides-stub:fixture"
MODEL_SHA256 = hashlib.sha256(b"bonafides-stub-model").hexdigest()
RUNTIME_VERSION = "bonafides-stub-1"
SCENARIOS = (
    "valid",
    "absent-claim",
    "posting-only-skill",
    "inflated",
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


def _section(projection: dict[str, object]) -> dict[str, Any]:
    target = projection["target"]
    assert isinstance(target, dict) and isinstance(target["blocks"], list)
    claims = {str(claim["id"]): str(claim["text"]) for claim in _items(projection, "claims")}
    blocks = []
    for index, block in enumerate(target["blocks"], start=1):
        cited = [str(value) for value in block["claim_revision_ids"]]
        if block["claim_kind"] == "factual" and cited:
            blocks.append(_block(index, "factual", claims[cited[0]], cited[0]))
        else:
            blocks.append(_block(index, "connective", str(block["text"]), None))
    return {
        "schema_version": "2",
        "stage": "regenerate_section",
        "blocks": blocks,
        "warnings": [],
    }


def _statements(output: dict[str, Any]) -> list[dict[str, object]]:
    """The first statement list a fabrication scenario edits."""
    if output["stage"] == "regenerate_section":
        return cast(list[dict[str, object]], output["blocks"])
    return cast(list[dict[str, object]], output["documents"][0]["sections"][0]["blocks"])


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


def _inflated(projection: dict[str, object]) -> tuple[str, str]:
    """A claim and the cut of its own text that starts at a later, stronger verb."""
    from bonafides.statement_check import verb_ranks

    for claim in _items(projection, "claims"):
        text = str(claim["text"])
        claim_ranks = verb_ranks(text)
        if not claim_ranks:
            continue
        words = text.rstrip(".;").split()
        for index in range(1, len(words)):
            cut = " ".join(words[index:])
            ranks = verb_ranks(cut)
            # The cut starts at the verb: dropping its first word loses that verb.
            if (
                ranks
                and ranks[0] < claim_ranks[0]
                and verb_ranks(" ".join(words[index + 1 :])) != ranks
            ):
                return str(claim["id"]), cut[0].upper() + cut[1:]
    raise StubFailure("no claim's text holds a later verb stronger than its own")


def respond(scenario: str, request: dict[str, object]) -> dict[str, object]:
    """The complete /api/chat envelope for one request under one scenario."""
    messages = request["messages"]
    assert isinstance(messages, list) and len(messages) in {2, 3}
    projection: dict[str, object] = json.loads(messages[1]["content"])
    corrected = len(messages) == 3
    done_reason = "stop"
    output = _section(projection) if "target" in projection else _pair(projection)
    if scenario == "malformed" or (scenario == "malformed-once" and not corrected):
        invalid: dict[str, object] = {"schema_version": "2", "stage": output["stage"]}
        if output["stage"] == "regenerate_section":
            invalid["blocks"] = []
        else:
            invalid.update({"documents": [], "gaps": []})
        content = json.dumps(invalid)
    elif scenario == "truncated":
        done_reason = "length"
        content = json.dumps(output)[:200]
    else:
        statements = _statements(output)
        if scenario == "absent-claim":
            factual = next(item for item in statements if item["kind"] == "factual")
            factual["claim_id"] = ABSENT_CLAIM_ID
        elif scenario == "posting-only-skill":
            claim = str(_items(projection, "claims")[0]["id"])
            statement = f"Hands-on experience with {_posting_only_term(projection)}."
            statements.append(_block(900, "factual", statement, claim))
        elif scenario == "inflated":
            claim, statement = _inflated(projection)
            statements.append(_block(900, "factual", statement, claim))
        content = json.dumps(output)
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
    """Write a 0600 route file with fixture-only receipts for both document stages."""
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
    stages = ("generate_documents", "regenerate_section")
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
        for stage in stages
    }
    write()
    loaded = load_route(output)
    assert loaded is not None and all(loaded.qualified_for(stage) for stage in stages)


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
