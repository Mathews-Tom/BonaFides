# SPDX-License-Identifier: MIT OR Apache-2.0
"""Qualify a private model route against committed synthetic, claim-structured cases."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from threading import Event
from time import monotonic
from typing import Literal, cast
from uuid import UUID, uuid4

from pydantic import ValidationError

from bonafides import document_generation as generation
from bonafides import documents, local_model, model_calls, model_projections, section_regeneration
from bonafides.commands import canonical_json
from bonafides.config import ConfigurationError, read_secret
from bonafides.fabrication_benchmark import digest
from bonafides.problems import Problem
from bonafides.statement_check import check_statements

STAGES = ("generate_documents", "regenerate_section")


def load_cases(directory: Path, stage: str) -> tuple[list[dict[str, object]], str]:
    raw = (directory / f"{stage}.jsonl").read_bytes()
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("qualification_dataset_too_large")
    cases: list[dict[str, object]] = []
    for line in raw.splitlines():
        case = json.loads(line)
        if not isinstance(case, dict) or set(case) != {
            "id",
            "posting",
            "requirements",
            "claims",
            "evidence",
            "facts",
            "tone",
            "language",
            "target",
        } | ({"base_body"} if stage == "regenerate_section" else set()):
            raise ValueError("qualification_case_invalid")
        if stage == "regenerate_section":
            body = documents.parse_body(case["base_body"])
            target = case["target"]
            if not isinstance(target, dict) or not any(
                section.key == target.get("section_key") for section in body.sections
            ):
                raise ValueError("qualification_target_missing")
        cases.append(case)
    if not 20 <= len(cases) <= 1000 or len({str(case["id"]) for case in cases}) != len(cases):
        raise ValueError("qualification_case_count_invalid")
    return cases, digest({f"{stage}.jsonl": raw})


def projection(case: dict[str, object], stage: str) -> dict[str, object]:
    args = (
        cast(dict[str, object], case["posting"]),
        cast(list[dict[str, object]], case["requirements"]),
        cast(list[dict[str, object]], case["claims"]),
        cast(list[dict[str, object]], case["evidence"]),
        str(case["tone"]),
        str(case["language"]),
    )
    if stage == "generate_documents":
        return model_projections.generation_projection(*args)
    return model_projections.regeneration_projection(*args, cast(dict[str, object], case["target"]))


def checked_bodies(
    output: dict[str, object],
    stage: str,
    plan: dict[str, object],
    target: dict[str, object],
    base_body: documents.DocumentBody | None = None,
) -> list[tuple[str, documents.DocumentBody]]:
    if stage == "generate_documents":
        pair = generation.validate_pair(output, plan)
        return [(item.kind, generation._candidate_body(item, plan)) for item in pair.documents]
    if base_body is None:
        raise ValueError("qualification_base_body_required")
    original = next(
        section for section in base_body.sections if section.key == target["section_key"]
    )
    candidate = section_regeneration.validate_replacement(
        output, base_body, {"original_section": original.model_dump(mode="json")}, plan, UUID(int=0)
    )
    try:
        body = generation._candidate_body(
            generation.CandidateDocument(
                kind=cast(Literal["resume", "cover_letter"], target["kind"]),
                sections=[
                    generation.CandidateSection(
                        heading=str(target["heading"]), blocks=candidate.blocks
                    )
                ],
            ),
            plan,
        )
    except ValidationError as exc:
        raise generation._mismatch(exc) from exc
    return [(str(target["kind"]), body)]


def statement_counts(
    bodies: list[tuple[str, documents.DocumentBody]], plan: dict[str, object]
) -> tuple[int, int]:
    factual = usable = 0
    for kind, body in bodies:
        issues = check_statements(
            body,
            cast(list[dict[str, object]], plan["facts"]),
            cast(dict[str, object], plan["frame"]),
            kind,
        )
        paths = {issue["path"] for issue in issues}
        for si, section in enumerate(body.sections):
            for bi, block in enumerate(section.blocks):
                if block.claim_kind == "factual":
                    factual += 1
                    if not paths.intersection(
                        {"/title", f"/sections/{si}/heading", f"/sections/{si}/blocks/{bi}"}
                    ):
                        usable += 1
    return factual, usable


def qualify(
    route: local_model.LocalRoute,
    stage: str,
    cases: list[dict[str, object]],
    dataset_sha256: str,
    reviewed_by: str,
) -> dict[str, object]:
    client = local_model.OllamaClient(route, qualification_stage=stage)
    interrupted = Event()
    counts = dict.fromkeys(
        (
            "valid_first",
            "valid_after_correction",
            "invalid_after_correction",
            "failed_before_check",
        ),
        0,
    )
    factual = usable = 0
    started = monotonic()
    for case in cases:
        context = projection(case, stage)
        posting = cast(dict[str, object], context["posting"])
        plan = {
            "context": context,
            "facts": case["facts"],
            "frame": {"role": posting["title"], "organization": posting["organization"]},
        }
        target = cast(dict[str, object], case["target"])
        try:
            bodies, _, corrected = model_calls.checked_call(
                partial(
                    client.call, stage, context, local_model.PROMPT_VERSIONS[stage], interrupted
                ),
                partial(
                    checked_bodies,
                    stage=stage,
                    plan=plan,
                    target=target,
                    base_body=documents.parse_body(case["base_body"])
                    if stage == "regenerate_section"
                    else None,
                ),
            )
        except local_model.ModelFailure as exc:
            if exc.code not in {
                "model_incomplete",
                "model_timeout",
                "model_response_limit",
                "model_usage_limit",
                "model_refused",
            }:
                raise
            counts["failed_before_check"] += 1
            continue
        except Problem as exc:
            if exc.code != "model_output_invalid_after_correction":
                raise
            counts["invalid_after_correction"] += 1
            continue
        counts["valid_after_correction" if corrected else "valid_first"] += 1
        total, supported = statement_counts(bodies, plan)
        factual += total
        usable += supported
    valid = counts["valid_first"] + counts["valid_after_correction"]
    return local_model.qualification_receipt(
        {
            "status": "passed" if valid * 100 >= len(cases) * 95 else "failed",
            "capability_sha256": route.capability_sha256,
            "reviewed_by": reviewed_by,
            "reviewed_at": datetime.now(UTC).isoformat(),
            "counts": counts,
            "valid_rate_per_mille": valid * 1000 // len(cases),
            "usable_statement_rate_per_mille": usable * 1000 // factual if factual else 0,
            "dataset_sha256": dataset_sha256,
            "case_count": len(cases),
            "duration_ms": int((monotonic() - started) * 1000),
        }
    )


def write_receipt(path: Path, original: str, stage: str, receipt: dict[str, object]) -> None:
    """Publish only this stage, without following a route symlink or overwriting edits."""
    data = json.loads(original)
    data["qualification_receipts"][stage] = receipt
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical_json(data).encode() + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        local_model.load_route(temporary)
        if read_secret(path, "local_model_route") != original:
            raise ValueError("qualification_route_changed")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bonafides models")
    parser.add_argument("command", choices=("qualify",))
    parser.add_argument("--route", type=Path, required=True)
    parser.add_argument("--stage", choices=STAGES, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--reviewed-by", required=True)
    args = parser.parse_args(argv)
    try:
        local_model.validate_reviewer(args.reviewed_by)
        original = read_secret(args.route, "local_model_route")
        route = local_model.load_route(args.route)
        if route is None:
            raise ValueError("qualification_route_required")
        cases, dataset_sha256 = load_cases(args.dataset, args.stage)
        receipt = qualify(route, args.stage, cases, dataset_sha256, args.reviewed_by)
        write_receipt(args.route, original, args.stage, receipt)
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        ConfigurationError,
        local_model.ModelFailure,
        Problem,
    ) as exc:
        print(f"Qualification failed: {getattr(exc, 'code', str(exc))}", file=sys.stderr)
        return 2
    print(canonical_json({"model": route.model, "stage": args.stage, **receipt}))
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
