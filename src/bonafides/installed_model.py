# SPDX-License-Identifier: MIT OR Apache-2.0
"""Image-only attachment of an already installed model; never pulls or infers."""

from __future__ import annotations

import argparse
import http.client
import json
import os
import socket
import sys
import tempfile
from contextlib import suppress
from pathlib import Path
from threading import Event, Thread, Timer

from bonafides.local_model import (
    LocalRoute,
    ModelFailure,
    _decode,
    _local_ip,
    _PinnedConnection,
    _response,
    _verify_manifest,
    load_route,
)

_HOST = "source-egress"
_PORT = 11434


def _addresses() -> list[str]:
    done = Event()
    addresses: list[str] = []

    def resolve() -> None:
        try:
            addresses.extend(
                _local_ip(str(item[4][0]))
                for item in socket.getaddrinfo(_HOST, _PORT, type=socket.SOCK_STREAM)
            )
        except (OSError, ModelFailure):
            addresses.clear()
        finally:
            done.set()

    Thread(target=resolve, daemon=True).start()
    if not done.wait(10) or not addresses:
        raise ModelFailure("model_gateway_unavailable")
    return sorted(set(addresses))


def _metadata(route: LocalRoute, path: str) -> dict[str, object]:
    expired = Event()
    connection = _PinnedConnection(route, 10, expired)

    def expire() -> None:
        expired.set()
        sock = connection.sock
        if sock is not None:
            with suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)
        connection.close()

    timer = Timer(10, expire)
    timer.daemon = True
    timer.start()
    try:
        connection.connect()
        if expired.is_set():
            raise ModelFailure("model_gateway_unavailable")
        connection.request(
            "GET",
            path,
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "Connection": "close",
            },
        )
        return _decode(_response(connection, 262144))
    finally:
        timer.cancel()
        connection.close()


def attach(route_path: Path, model: str, model_license: str, *, authorized: bool) -> None:
    """Publish a validated, unqualified route without replacing any existing entry."""
    if not authorized:
        raise ModelFailure("local_model_authorization_required")
    if route_path.exists() or route_path.is_symlink():
        raise ModelFailure("model_route_already_exists")
    data: dict[str, object] = {
        "version": 1,
        "endpoint": f"http://{_HOST}:{_PORT}",
        "approved_ips": _addresses(),
        "model": model,
        "model_sha256": "0" * 64,
        "runtime_version": "pending-metadata",
        "model_license": model_license,
        "operator_controlled": True,
        "authorized": True,
        "cloud_disabled": True,
        "metadata_authorized": True,
        "private_network_http_authorized": True,
        "ca_file": None,
        "context_window": 32768,
        "max_input_tokens": 12000,
        "max_output_tokens": 6000,
        "max_request_bytes": 131072,
        "max_response_bytes": 262144,
        "deadline_seconds": 300,
        "framing_token_overhead": 2048,
        "qualification_receipts": {},
    }
    # A private staging file lets the existing validator enforce the entire schema,
    # including model/tag/license policy, before any metadata request or publication.
    directory = os.open(route_path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temporary: Path | None = None
    try:
        fd, name = tempfile.mkstemp(prefix=".model-route-", dir=route_path.parent)
        temporary = Path(name)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:

            def validate() -> LocalRoute:
                stream.seek(0)
                stream.truncate()
                json.dump(data, stream)
                stream.flush()
                result = load_route(temporary)
                assert result is not None
                return result

            draft = validate()
            tags = _metadata(draft, "/api/tags")
            models = tags.get("models")
            if not isinstance(models, list) or any(not isinstance(item, dict) for item in models):
                raise ModelFailure("model_metadata_invalid")
            selected = [
                item for item in models if isinstance(item, dict) and item.get("name") == model
            ]
            if not selected:
                raise ModelFailure(
                    "model_not_installed: install the exact selected model in your local "
                    "runtime first; attach never downloads models"
                )
            if len(selected) != 1:
                raise ModelFailure("model_metadata_invalid")
            data["model_sha256"] = selected[0].get("digest")
            data["runtime_version"] = _metadata(draft, "/api/version").get("version")
            complete = validate()
            _verify_manifest(complete, tags)
            os.fsync(stream.fileno())
        # link is atomic and fails for every existing entry, including dangling symlinks.
        os.link(
            temporary.name,
            route_path.name,
            src_dir_fd=directory,
            dst_dir_fd=directory,
            follow_symlinks=False,
        )
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        os.close(directory)


def verify_result(route_path: Path, original: Path, stage: str, result: int) -> None:
    """A process exit is not evidence that a new qualification receipt was published."""
    route = load_route(route_path)
    if route is None or route_path.read_bytes() == original.read_bytes():
        raise ModelFailure("qualification_result_not_published")
    data = _decode(route_path.read_bytes())
    receipts = data.get("qualification_receipts")
    receipt = receipts.get(stage) if isinstance(receipts, dict) else None
    expected = "passed" if result == 0 else "failed"
    if result not in {0, 1} or not isinstance(receipt, dict) or receipt.get("status") != expected:
        raise ModelFailure("qualification_result_mismatch")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("attach", help="attach an already installed exact local model")
    command.add_argument("--route", required=True, type=Path)
    command.add_argument("--model", required=True)
    command.add_argument("--model-license", required=True)
    command.add_argument("--authorize-local-model", action="store_true")
    check = commands.add_parser("verify-result", help="verify a completed qualification result")
    check.add_argument("--route", required=True, type=Path)
    check.add_argument("--original", required=True, type=Path)
    check.add_argument(
        "--stage", required=True, choices=("generate_documents", "regenerate_section")
    )
    check.add_argument("--result", required=True, type=int, choices=(0, 1))
    args = parser.parse_args(argv)
    try:
        if args.command == "attach":
            attach(
                args.route, args.model, args.model_license, authorized=args.authorize_local_model
            )
        else:
            verify_result(args.route, args.original, args.stage, args.result)
    except (ModelFailure, OSError, http.client.HTTPException) as error:
        message = (
            error.code if isinstance(error, ModelFailure) else "model_gateway_or_route_unavailable"
        )
        print(message, file=sys.stderr)
        return 2
    if args.command == "attach":
        print(f"{args.model}: attached but unqualified; run each qualification stage before use.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
