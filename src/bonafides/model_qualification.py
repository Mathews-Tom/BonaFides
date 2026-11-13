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
from bonafides import (
    documents,
    import_structuring,
    local_model,
    model_calls,
    model_projections,
    section_regeneration,
)
from bonafides.commands import canonical_json
from bonafides.config import ConfigurationError, read_secret
from bonafides.conversation_protocol import ConversationReply, validate_reply
from bonafides.fabrication_benchmark import digest
from bonafides.problems import Problem
from bonafides.statement_check import check_statements

STAGES = local_model.QUALIFICATION_STAGES


def load_cases(directory: Path, stage: str) -> tuple[list[dict[str, object]], str]:
    if stage not in STAGES:
        raise ValueError("qualification_stage_invalid")
    raw = (directory / f"{stage}.jsonl").read_bytes()
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("qualification_dataset_too_large")
    cases: list[dict[str, object]] = []
    for line in raw.splitlines():
        case = json.loads(line)
        if stage == "refine_documents":
            if not isinstance(case, dict) or set(case) != {
                "id",
                "base_contexts",
                "request",
                "turns",
                "targets",
                "documents",
                "expected",
            }:
                raise ValueError("qualification_case_invalid")
            projection(case, stage)
            if not isinstance(case["id"], str) or not case["id"].strip():
                raise ValueError("qualification_case_invalid")
            expected = case["expected"]
            if (
                not isinstance(expected, dict)
                or set(expected) != {"declined", "minimum_edits"}
                or type(expected["declined"]) is not bool
                or type(expected["minimum_edits"]) is not int
                or not 0 <= expected["minimum_edits"] <= len(case["targets"])
            ):
                raise ValueError("qualification_case_invalid")
            for document in case["documents"]:
                documents.parse_body(document["body"])
            cases.append(case)
            continue
        if stage == "structure_import":
            if not isinstance(case, dict) or set(case) != {
                "id",
                "source_sha256",
                "units",
                "expected",
            }:
                raise ValueError("qualification_case_invalid")
            if not isinstance(case["id"], str) or not case["id"].strip():
                raise ValueError("qualification_case_invalid")
            context = projection(case, stage)
            expected = import_structuring.checked_units(
                {"schema_version": "1", "stage": stage, "items": case["expected"]}, context
            )
            if not expected:
                raise ValueError("qualification_case_invalid")
            cases.append(case)
            continue
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
    if stage == "structure_import":
        if len(cases) != 20:
            raise ValueError("qualification_case_count_invalid")
        distinct = {
            canonical_json(
                [
                    {"text": item["text"], "kind": item["kind"]}
                    for item in cast(list[dict[str, object]], case["units"])
                ]
            )
            for case in cases
        }
        if len(distinct) != len(cases):
            raise ValueError("qualification_cases_not_distinct")
    if stage == "refine_documents" and len(
        {
            canonical_json({key: value for key, value in case.items() if key != "id"})
            for case in cases
        }
    ) != len(cases):
        raise ValueError("qualification_cases_not_distinct")
    return cases, digest({f"{stage}.jsonl": raw})


def projection(case: dict[str, object], stage: str) -> dict[str, object]:
    if stage == "refine_documents":
        return model_projections.refinement_projection(
            cast(list[dict[str, object]], case["base_contexts"]),
            cast(str, case["request"]),
            cast(list[dict[str, object]], case["turns"]),
            cast(list[dict[str, object]], case["targets"]),
        )
    if stage == "structure_import":
        return import_structuring.projection(
            cast(str, case["source_sha256"]), cast(list[dict[str, object]], case["units"])
        )
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


def checked_refinement(output: dict[str, object], case: dict[str, object]) -> ConversationReply:
    reply = validate_reply(output, case)
    expected = cast(dict[str, object], case["expected"])
    targets = {
        (str(item["document_id"]), item["section_key"], item["block_key"]): item
        for item in cast(list[dict[str, object]], case["targets"])
    }
    changed = sum(
        edit.replacement.model_dump(mode="json") != target["before"]
        for edit in reply.edits
        if (target := targets.get((str(edit.document_id), edit.section_key, edit.block_key)))
        is not None
    )
    minimum = cast(int, expected["minimum_edits"])
    if (
        reply.declined != expected["declined"]
        or changed < minimum
        or (minimum == 0 and (reply.edits or reply.tone is not None))
    ):
        raise model_calls.OutputMismatch(["conversation: unexpected_outcome"])
    return reply


def qualify(
    route: local_model.LocalRoute,
    stage: str,
    cases: list[dict[str, object]],
    dataset_sha256: str,
    reviewed_by: str,
) -> dict[str, object]:
    local_model.validate_reviewer(reviewed_by)
    if stage == "structure_import" and len(cases) != 20:
        raise ValueError("qualification_case_count_invalid")
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
    unit_count = correct_kind_count = 0
    factual = usable = 0
    started = monotonic()
    for case in cases:
        context = projection(case, stage)
        if stage == "structure_import":
            expected = import_structuring.checked_units(
                {"schema_version": "1", "stage": stage, "items": case["expected"]}, context
            )
            unit_count += len(expected)
            validate = partial(import_structuring.checked_units, context=context)
        elif stage == "refine_documents":
            validate_refinement = partial(checked_refinement, case=case)
        else:
            posting = cast(dict[str, object], context["posting"])
            plan = {
                "context": context,
                "facts": case["facts"],
                "frame": {"role": posting["title"], "organization": posting["organization"]},
            }
            target = cast(dict[str, object], case["target"])
        try:
            call = partial(
                client.call, stage, context, local_model.PROMPT_VERSIONS[stage], interrupted
            )
            if stage == "structure_import":
                kinds, _, corrected = model_calls.checked_call(call, validate)
            elif stage == "refine_documents":
                reply, _, corrected = model_calls.checked_call(call, validate_refinement)
            else:
                bodies, _, corrected = model_calls.checked_call(
                    call,
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
            } and not (stage == "structure_import" and exc.code == "model_output_limit"):
                raise
            counts["failed_before_check"] += 1
            continue
        except Problem as exc:
            if exc.code != "model_output_invalid_after_correction":
                raise
            counts["invalid_after_correction"] += 1
            continue
        counts["valid_after_correction" if corrected else "valid_first"] += 1
        if stage == "structure_import":
            correct_kind_count += sum(kind == expected[key] for key, kind in kinds.items())
        elif stage == "refine_documents":
            selected = {
                (str(item["document_id"]), item["section_key"], item["block_key"])
                for item in cast(list[dict[str, object]], case["targets"])
            }
            by_id = {
                str(document["document_id"]): document
                for document in cast(list[dict[str, object]], case["documents"])
            }
            for edit in reply.edits:
                if (str(edit.document_id), edit.section_key, edit.block_key) not in selected:
                    continue
                document = by_id.get(str(edit.document_id))
                if document is None:
                    continue
                snapshot = cast(dict[str, object], document["provenance_snapshot"])
                body = documents.DocumentBody(
                    title="Cover letter" if document["kind"] == "cover_letter" else "Résumé",
                    sections=[
                        documents.DocumentSection(
                            key=edit.section_key, heading="Experience", blocks=[edit.replacement]
                        )
                    ],
                )
                total, supported = statement_counts(
                    [(str(document["kind"]), body)],
                    {"facts": snapshot["facts"], "frame": snapshot["frame"]},
                )
                factual += total
                usable += supported
        else:
            total, supported = statement_counts(bodies, plan)
            factual += total
            usable += supported
    valid = counts["valid_first"] + counts["valid_after_correction"]
    metrics: dict[str, object] = (
        {
            "unit_count": unit_count,
            "correct_kind_count": correct_kind_count,
            "correct_kind_rate_per_mille": correct_kind_count * 1000 // unit_count,
        }
        if stage == "structure_import"
        else {"usable_statement_rate_per_mille": usable * 1000 // factual if factual else 0}
    )
    return local_model.qualification_receipt(
        {
            "status": "passed"
            if (
                valid * 100 >= len(cases) * 95
                and (stage != "structure_import" or correct_kind_count * 100 >= unit_count * 95)
            )
            else "failed",
            "capability_sha256": route.capability_sha256,
            "reviewed_by": reviewed_by,
            "reviewed_at": datetime.now(UTC).isoformat(),
            "counts": counts,
            "valid_rate_per_mille": valid * 1000 // len(cases),
            **metrics,
            "dataset_sha256": dataset_sha256,
            "case_count": len(cases),
            "duration_ms": int((monotonic() - started) * 1000),
        },
        stage=stage,
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
