# SPDX-License-Identifier: MIT OR Apache-2.0
"""Create, sign and verify one release candidate with a disposable proof key, then tamper with it.

The key pair is generated in `--private` (never uploaded) and exists only for this run: it is a
non-production proof identity and carries no owner, release or trust meaning. The public output
directory receives the manifest, its cosign bundle, the proof public key and a report of every
accepted and rejected case. Each rejection is exercised for real: altered manifest bytes, a
wrong trusted key, missing and altered evidence, a scanner report for a different image and a
different source revision must all fail verification. Nothing contacts a signing or log service.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

from tools.release_candidate import (
    IMAGE_VULNERABILITIES,
    CandidateError,
    CosignError,
    build_manifest,
    evidence_names,
    file_record,
    render,
    run_cosign,
    sign,
    verify_candidate,
)
from tools.release_index import ARCHITECTURES, LayoutError, Release, layout_argument

IDENTITY = (
    "Disposable runner-local cosign key generated for this run; non-production proof identity, "
    "not an owner, publisher or release identity. Production signing and trust policy are pending."
)


def generate_key_pair(cosign: str, prefix: Path) -> tuple[Path, Path]:
    run_cosign(cosign, ["generate-key-pair", "--output-key-prefix", str(prefix)])
    return prefix.with_suffix(".key"), prefix.with_suffix(".pub")


def copy_evidence(evidence: dict[str, Path], destination: Path) -> dict[str, Path]:
    copied: dict[str, Path] = {}
    for architecture, root in evidence.items():
        copied[architecture] = destination / architecture
        shutil.copytree(root, copied[architecture])
    return copied


def tamper_text(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    if old not in text:
        raise CandidateError(f"{path}: tamper anchor {old!r} not found")
    path.write_text(text.replace(old, new, 1))


def expect_rejected(cases: list[dict[str, str]], name: str, attempt: Callable[[], None]) -> None:
    """Run a verification that must raise; an accepted tampered input fails the whole proof."""
    try:
        attempt()
    except LayoutError as error:
        cases.append({"case": name, "result": "rejected", "reason": str(error)})
        return
    raise CandidateError(f"tampered case was accepted: {name}")


def prove(
    release: Release,
    index: Path,
    evidence: dict[str, Path],
    private: Path,
    output: Path,
    cosign: str = "cosign",
) -> dict[str, object]:
    for directory in (private, output):
        directory.mkdir(parents=True)
    os.environ["COSIGN_PASSWORD"] = secrets.token_urlsafe(24)
    key, public = generate_key_pair(cosign, private / "proof")
    _, wrong_public = generate_key_pair(cosign, private / "wrong")
    manifest, bundle = output / "release-candidate.json", output / "release-candidate.sigstore.json"
    manifest.write_text(render(build_manifest(release, index, evidence)))
    sign(manifest, key, bundle, cosign)
    shutil.copyfile(public, output / "proof-public-key.pub")
    verify_candidate(manifest, release, index, evidence, public, bundle, cosign)

    scratch = private / "tampered"
    cases: list[dict[str, str]] = []

    def verify(
        candidate: Path = manifest,
        trusted: Path = public,
        inputs: dict[str, Path] = evidence,
        identity: Release = release,
    ) -> None:
        verify_candidate(candidate, identity, index, inputs, trusted, bundle, cosign)

    altered = scratch / "altered-manifest.json"
    altered.parent.mkdir()
    shutil.copyfile(manifest, altered)
    tamper_text(altered, '"release_authorized": false', '"release_authorized": true')
    expect_rejected(cases, "altered manifest bytes", lambda: verify(candidate=altered))
    expect_rejected(cases, "wrong trusted public key", lambda: verify(trusted=wrong_public))

    missing = copy_evidence(evidence, scratch / "missing")
    (missing["amd64"] / evidence_names("amd64")[2]).unlink()
    expect_rejected(cases, "missing evidence file", lambda: verify(inputs=missing))

    changed = copy_evidence(evidence, scratch / "changed")
    (changed["arm64"] / evidence_names("arm64")[3]).write_text("{}\n")
    expect_rejected(cases, "altered evidence file", lambda: verify(inputs=changed))

    foreign = copy_evidence(evidence, scratch / "foreign")
    tamper_text(
        foreign["arm64"] / IMAGE_VULNERABILITIES, '"ImageID": "sha256:', '"ImageID": "sha256:0'
    )
    expect_rejected(cases, "scanner report for a different image", lambda: verify(inputs=foreign))

    other_revision = ("0" if release.revision[0] != "0" else "1") + release.revision[1:]
    other = Release(release.version, other_revision, release.source)
    expect_rejected(cases, "different source revision", lambda: verify(identity=other))

    report: dict[str, object] = {
        "proof_identity": IDENTITY,
        "cosign": "key-based sign-blob and verify-blob; empty signing config; tlog ignored",
        "manifest": file_record(manifest),
        "proof_public_key": file_record(output / "proof-public-key.pub"),
        "positive": "signature and manifest verified against exact images and evidence",
        "negative_cases": cases,
        "release_authorized": False,
    }
    (output / "candidate-proof.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--evidence", type=layout_argument, action="append", default=[])
    parser.add_argument("--private", type=Path, required=True, help="New directory; never upload.")
    parser.add_argument("--output", type=Path, required=True, help="New directory for artifacts.")
    parser.add_argument("--cosign", default="cosign")
    arguments = parser.parse_args(argv)
    try:
        evidence = dict(arguments.evidence)
        if len(evidence) != len(arguments.evidence):
            raise CandidateError("each architecture may be given once")
        if sorted(evidence) != sorted(ARCHITECTURES):
            raise CandidateError(
                f"expected one evidence directory per architecture {ARCHITECTURES}"
            )
        release = Release(arguments.version, arguments.revision, arguments.source)
        report = prove(
            release,
            arguments.index,
            evidence,
            arguments.private,
            arguments.output,
            arguments.cosign,
        )
    except (CosignError, LayoutError, OSError, json.JSONDecodeError) as error:
        print(f"candidate proof: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
