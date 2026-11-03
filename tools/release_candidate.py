# SPDX-License-Identifier: MIT OR Apache-2.0
"""Bind one local release candidate to its exact images and gate evidence, then sign and verify it.

`create` writes a manifest naming the version, full source revision, the metadata-only OCI index,
each native platform's manifest and config digest, and the hash of every required evidence file.
The scanner reports must name the same image config digest as the platform they were collected
for. `verify` first checks the cosign signature with an explicitly supplied public key, then
rebuilds the manifest from the current inputs and refuses any difference. Any changed source,
image or evidence therefore needs a new candidate and a new signature.

Signing uses `cosign sign-blob --key` with an empty signing config and verification passes
`--insecure-ignore-tlog`: no certificate authority, timestamp authority or transparency log is
contacted and nothing is submitted. A passing verification proves the manifest bytes match a
signature made by the holder of the trusted key. It authorizes no release; human approvals,
production signing identity and trust policy remain pending (see the release checklist).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from tools.release_index import (
    ARCHITECTURES,
    CHUNK,
    LayoutError,
    Release,
    blob_path,
    inspect,
    json_object,
    layout_argument,
)

SCHEMA = "bonafides-release-candidate/1"
COSIGN_VERSION = "3.1.4"
IMAGE_VULNERABILITIES = "artifacts/image-vulnerabilities.json"
IMAGE_SBOM = "artifacts/image-sbom.json"
SBOM_IMAGE_ID = "aquasecurity:trivy:ImageID"
AUTHORIZATION = (
    "Automated evidence only. Human rights, legal, provenance and release approvals remain "
    "pending; this manifest and its signature authorize no release."
)
OFFLINE_SIGNING_CONFIG = (
    "--no-default-fulcio",
    "--no-default-oidc",
    "--no-default-rekor",
    "--no-default-tsa",
)


class CandidateError(LayoutError):
    """Candidate inputs are missing, altered, mismatched or not signed by the trusted key."""


class CosignError(RuntimeError):
    """The signing tool failed, rather than rejecting a candidate."""

    def __init__(self, message: str, returncode: int | None = None, detail: str = "") -> None:
        super().__init__(message)
        self.returncode = returncode
        self.detail = detail


def evidence_names(architecture: str) -> tuple[str, ...]:
    """Relative paths required inside one platform's downloaded gate-evidence artifact."""
    receipts = tuple(
        f".local/bonafides-proof-{kind}-{architecture}/receipt.json"
        for kind in ("ci", "install", "core")
    )
    return (
        IMAGE_VULNERABILITIES,
        IMAGE_SBOM,
        "artifacts/source-secrets.json",
        "artifacts/source-sbom.json",
        "artifacts/locked-components.json",
        "artifacts/python-vulnerabilities.json",
        "artifacts/python-licenses.json",
        "artifacts/gate-controls.json",
        *receipts,
    )


def file_record(path: Path) -> dict[str, object]:
    hasher, size = hashlib.sha256(), 0
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK):
            hasher.update(chunk)
            size += len(chunk)
    return {"sha256": hasher.hexdigest(), "size": size}


def reported_image(name: str, document: object, context: str) -> object:
    """The image config digest a Trivy vulnerability report or CycloneDX SBOM says it describes."""
    root = json_object(document, context)
    if name == IMAGE_VULNERABILITIES:
        return json_object(root.get("Metadata"), f"{context}: Metadata").get("ImageID")
    component = json_object(
        json_object(root.get("metadata"), f"{context}: metadata").get("component"),
        f"{context}: component",
    )
    properties = component.get("properties")
    if not isinstance(properties, list):
        raise CandidateError(f"{context}: SBOM component has no properties")
    found = [
        item.get("value")
        for item in properties
        if isinstance(item, dict) and item.get("name") == SBOM_IMAGE_ID
    ]
    if len(found) != 1:
        raise CandidateError(
            f"{context}: expected one {SBOM_IMAGE_ID} property, found {len(found)}"
        )
    return found[0]


def evidence_records(
    architecture: str, root: Path, release: Release, manifest_digest: str, config_digest: str
) -> dict[str, object]:
    records: dict[str, object] = {}
    documents: dict[str, object] = {}
    for name in evidence_names(architecture):
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise CandidateError(f"{architecture}: missing evidence file {name}")
        content = path.read_bytes()
        try:
            document: object = json.loads(content)
        except json.JSONDecodeError as error:
            raise CandidateError(f"{architecture}: {name} is not JSON: {error}") from error
        documents[name] = document
        if name in (IMAGE_VULNERABILITIES, IMAGE_SBOM):
            subject = reported_image(name, document, f"{architecture}: {name}")
            if subject != config_digest:
                raise CandidateError(
                    f"{architecture}: {name} reports image {subject!r}, "
                    f"expected config {config_digest}"
                )
        records[name] = file_record(path)
    context = f"{architecture}: inventory"
    inventory = json_object(documents["artifacts/locked-components.json"], context)
    inspected, components = inventory.get("artifacts_inspected"), inventory.get("components")
    if not isinstance(inspected, list) or not isinstance(components, list):
        raise CandidateError(f"{context}: missing inspected artifacts or components")
    images = [
        item.get("metadata")
        for item in inspected
        if isinstance(item, dict) and item.get("kind") == "docker-image-inspect"
    ]
    if len(images) != 1:
        raise CandidateError(f"{context}: expected one inspected image")
    metadata = json_object(images[0], context)
    image_id = metadata.get("Id")
    if (
        metadata.get("platform_id") not in (manifest_digest, config_digest)
        or metadata.get("Architecture") != architecture
        or metadata.get("Os") != "linux"
        or not isinstance(image_id, str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id)
    ):
        raise CandidateError(f"{context}: reports a different image or platform")
    component_images = [
        item["docker_image_id"]
        for item in components
        if isinstance(item, dict) and "docker_image_id" in item
    ]
    if component_images != [image_id]:
        raise CandidateError(f"{context}: component image differs from inspected image")
    for kind in ("ci", "install", "core"):
        name = f".local/bonafides-proof-{kind}-{architecture}/receipt.json"
        receipt = json_object(documents[name], name)
        if receipt.get("platform") != f"linux/{architecture}":
            raise CandidateError(f"{name}: reports a different platform")
        if kind == "ci":
            containers = json_object(receipt.get("containers"), name)
            for role in ("api", "worker", "renderer", "parser", "ingress", "source-egress"):
                container = json_object(containers.get(role), f"{name}: {role}")
                if container.get("image") != image_id:
                    raise CandidateError(f"{name}: {role} reports a different image")
        else:
            subject = receipt.get("image")
            if kind == "install":
                subject = json_object(receipt.get("images"), name).get("first")
            identity = json_object(subject, name)
            expected = {
                "id": image_id,
                "architecture": f"linux/{architecture}",
                "version": release.version,
                "revision": release.revision,
            }
            if any(identity.get(key) != value for key, value in expected.items()):
                raise CandidateError(f"{name}: reports a different image, platform or release")
    return records


def image_bindings(index: Path, release: Release) -> dict[str, object]:
    report = inspect(index, release, require_metadata_only=True)
    platforms = json_object(report["platforms"], f"{index}: platforms")
    bindings: dict[str, object] = {}
    for architecture in ARCHITECTURES:
        manifest_digest = platforms[architecture]
        manifest = json_object(
            json.loads(blob_path(index, manifest_digest).read_bytes()), f"{index}: manifest"
        )
        config = json_object(manifest.get("config"), f"{index}: manifest config")
        bindings[architecture] = {
            "manifest_digest": manifest_digest,
            "config_digest": config.get("digest"),
        }
    return {"index_digest": report["index_digest"], "tags": report["tags"], "platforms": bindings}


def build_manifest(release: Release, index: Path, evidence: dict[str, Path]) -> dict[str, object]:
    if sorted(evidence) != sorted(ARCHITECTURES):
        raise CandidateError(f"expected one evidence directory per architecture {ARCHITECTURES}")
    images = image_bindings(index, release)
    platforms = json_object(images["platforms"], "platforms")
    return {
        "schema": SCHEMA,
        "version": release.version,
        "revision": release.revision,
        "source": release.source,
        **images,
        "evidence": {
            architecture: evidence_records(
                architecture,
                evidence[architecture],
                release,
                str(json_object(platforms[architecture], architecture)["manifest_digest"]),
                str(json_object(platforms[architecture], architecture)["config_digest"]),
            )
            for architecture in ARCHITECTURES
        },
        "release_authorized": False,
        "authorization": AUTHORIZATION,
    }


def render(manifest: dict[str, object]) -> str:
    return json.dumps(manifest, indent=2, sort_keys=True) + "\n"


def require_same(recorded: dict[str, object], expected: dict[str, object]) -> None:
    if render(recorded) != render(expected):
        raise CandidateError("manifest differs from current inputs")


def run_cosign(cosign: str, arguments: list[str]) -> str:
    try:
        result = subprocess.run(
            [cosign, *arguments],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except OSError as error:
        raise CosignError(f"cannot run {cosign}: {error}") from error
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()[-1:] or ["no error output"]
        raise CosignError(
            f"cosign {arguments[0]} exited {result.returncode}: {detail[0]}",
            result.returncode,
            detail[0],
        )
    return result.stdout


def require_cosign(cosign: str) -> None:
    match = re.search(r"GitVersion:\s+v?(\S+)", run_cosign(cosign, ["version"]))
    if match is None or match.group(1) != COSIGN_VERSION:
        found = match.group(1) if match else "unknown"
        raise CosignError(f"cosign {found} found, expected pinned {COSIGN_VERSION}")


def sign(manifest: Path, private_key: Path, bundle: Path, cosign: str = "cosign") -> None:
    """Sign the manifest bytes with a local private key and no network service."""
    if "COSIGN_PASSWORD" not in os.environ:
        raise CandidateError("set COSIGN_PASSWORD for the private key; cosign would prompt")
    if bundle.exists():
        raise CandidateError(f"{bundle} already exists")
    require_cosign(cosign)
    with tempfile.TemporaryDirectory() as scratch:
        config = Path(scratch) / "offline-signing-config.json"
        run_cosign(
            cosign, ["signing-config", "create", *OFFLINE_SIGNING_CONFIG, "--out", str(config)]
        )
        run_cosign(
            cosign,
            [
                "sign-blob",
                "--key",
                str(private_key),
                "--signing-config",
                str(config),
                "--bundle",
                str(bundle),
                "--yes",
                str(manifest),
            ],
        )


def verify_signature(
    manifest: Path, public_key: Path, bundle: Path, cosign: str = "cosign"
) -> None:
    """Accept only a signature by the caller-supplied key; no bundled or implicit trust."""
    for path in (manifest, public_key, bundle):
        if not path.is_file():
            raise CandidateError(f"{path} is not a file")
    require_cosign(cosign)
    try:
        run_cosign(
            cosign,
            [
                "verify-blob",
                "--key",
                str(public_key),
                "--bundle",
                str(bundle),
                "--insecure-ignore-tlog",
                str(manifest),
            ],
        )
    except CosignError as error:
        if error.returncode == 1 and error.detail.endswith(
            "invalid signature when validating ASN.1 encoded signature"
        ):
            raise CandidateError(str(error)) from error
        raise


def verify_candidate(
    manifest: Path,
    release: Release,
    index: Path,
    evidence: dict[str, Path],
    public_key: Path,
    bundle: Path,
    cosign: str = "cosign",
) -> None:
    verify_signature(manifest, public_key, bundle, cosign)
    recorded = json_object(json.loads(manifest.read_bytes()), str(manifest))
    require_same(recorded, build_manifest(release, index, evidence))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inputs = argparse.ArgumentParser(add_help=False)
    inputs.add_argument("--version", required=True)
    inputs.add_argument("--revision", required=True)
    inputs.add_argument("--source", required=True)
    inputs.add_argument("--index", type=Path, required=True, help="Metadata-only OCI index layout.")
    inputs.add_argument(
        "--evidence",
        type=layout_argument,
        action="append",
        default=[],
        help="ARCH=DIR of that platform's downloaded gate-evidence artifact.",
    )
    for name in ("create", "verify"):
        child = commands.add_parser(name, parents=[inputs])
        child.add_argument("--manifest", type=Path, required=True)
        if name == "verify":
            child.add_argument("--trusted-public-key", type=Path, required=True)
            child.add_argument("--bundle", type=Path, required=True)
    signer = commands.add_parser("sign")
    signer.add_argument("--manifest", type=Path, required=True)
    signer.add_argument("--private-key", type=Path, required=True)
    signer.add_argument("--bundle", type=Path, required=True)
    for child in commands.choices.values():
        child.add_argument("--cosign", default="cosign")
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "sign":
            sign(arguments.manifest, arguments.private_key, arguments.bundle, arguments.cosign)
            return 0
        release = Release(arguments.version, arguments.revision, arguments.source)
        evidence = dict(arguments.evidence)
        if len(evidence) != len(arguments.evidence):
            raise CandidateError("each architecture may be given once")
        if arguments.command == "create":
            if arguments.manifest.exists():
                raise CandidateError(f"{arguments.manifest} already exists")
            arguments.manifest.write_text(
                render(build_manifest(release, arguments.index, evidence))
            )
        else:
            verify_candidate(
                arguments.manifest,
                release,
                arguments.index,
                evidence,
                arguments.trusted_public_key,
                arguments.bundle,
                arguments.cosign,
            )
            print("release candidate verified; no release is authorized")
    except (CosignError, LayoutError, OSError, json.JSONDecodeError) as error:
        print(f"release candidate: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
