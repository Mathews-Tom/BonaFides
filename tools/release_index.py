# SPDX-License-Identifier: MIT OR Apache-2.0
"""Assemble and inspect a local two-platform OCI image index; never contacts a registry.

Each input is one architecture's `docker save` output extracted as an OCI image layout. The
assembled layout holds a single image index tagged with the package version and `sha-<12 hex>`
whose platform manifests and blobs are copied verbatim, so its digests describe this local
layout only: a registry push would compress layers and produce different manifest digests.
Docker attestation manifests are not carried over.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

OCI_INDEX = "application/vnd.oci.image.index.v1+json"
OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
INDEX_TYPES = frozenset({OCI_INDEX, "application/vnd.docker.distribution.manifest.list.v2+json"})
MANIFEST_TYPES = frozenset({OCI_MANIFEST, "application/vnd.docker.distribution.manifest.v2+json"})
ARCHITECTURES = ("amd64", "arm64")
LABEL_PREFIX = "org.opencontainers.image."
REF_NAME = LABEL_PREFIX + "ref.name"
DIGEST = re.compile(r"sha256:([0-9a-f]{64})")
CHUNK = 1 << 20


class LayoutError(Exception):
    """An input or assembled layout is incomplete, inconsistent or mislabelled."""


@dataclass(frozen=True)
class Release:
    version: str
    revision: str
    source: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"\d+\.\d+\.\d+", self.version):
            raise LayoutError(f"version must be MAJOR.MINOR.PATCH, got {self.version!r}")
        if not re.fullmatch(r"[0-9a-f]{40}", self.revision):
            raise LayoutError(f"revision must be a 40-hex commit, got {self.revision!r}")
        if not re.fullmatch(r"https://[^\s]+", self.source):
            raise LayoutError(f"source must be an https URL, got {self.source!r}")

    @property
    def tags(self) -> tuple[str, str]:
        return self.version, f"sha-{self.revision[:12]}"

    @property
    def labels(self) -> dict[str, str]:
        return {
            LABEL_PREFIX + "version": self.version,
            LABEL_PREFIX + "revision": self.revision,
            LABEL_PREFIX + "source": self.source,
        }


@dataclass(frozen=True)
class PlatformImage:
    architecture: str
    descriptor: dict[str, object]
    blobs: tuple[str, ...]


def blob_path(layout: Path, digest: object) -> Path:
    match = DIGEST.fullmatch(digest) if isinstance(digest, str) else None
    if match is None:
        raise LayoutError(f"{layout}: unsupported digest {digest!r}")
    return layout / "blobs" / "sha256" / match.group(1)


def verify_blob(layout: Path, descriptor: dict[str, object]) -> Path:
    """Stream-hash one referenced blob against its descriptor's digest and size."""
    digest, size = descriptor.get("digest"), descriptor.get("size")
    path = blob_path(layout, digest)
    if not path.is_file():
        raise LayoutError(f"{layout}: missing blob {digest}")
    hasher, length = hashlib.sha256(), 0
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK):
            hasher.update(chunk)
            length += len(chunk)
    if f"sha256:{hasher.hexdigest()}" != digest or length != size:
        raise LayoutError(f"{layout}: blob {digest} does not match its descriptor")
    return path


def json_object(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise LayoutError(f"{context}: expected a JSON object")
    return value


def json_blob(layout: Path, descriptor: dict[str, object]) -> dict[str, object]:
    path = verify_blob(layout, descriptor)
    return json_object(json.loads(path.read_bytes()), f"{layout}: {descriptor.get('digest')}")


def descriptors(document: dict[str, object], context: str) -> list[dict[str, object]]:
    manifests = document.get("manifests")
    if not isinstance(manifests, list) or not manifests:
        raise LayoutError(f"{context}: no manifests")
    return [json_object(item, context) for item in manifests]


def read_layout_index(layout: Path) -> dict[str, object]:
    marker = json.loads((layout / "oci-layout").read_text())
    if json_object(marker, f"{layout}/oci-layout").get("imageLayoutVersion") != "1.0.0":
        raise LayoutError(f"{layout}: not an OCI image layout 1.0.0")
    return json_object(json.loads((layout / "index.json").read_text()), f"{layout}/index.json")


def image_descriptors(layout: Path, document: dict[str, object]) -> list[dict[str, object]]:
    """Flatten nested indexes to image manifests, skipping Docker attestation manifests."""
    found: list[dict[str, object]] = []
    for descriptor in descriptors(document, str(layout)):
        annotations = json_object(descriptor.get("annotations", {}), str(layout))
        if annotations.get("vnd.docker.reference.type") == "attestation-manifest":
            continue
        media_type = descriptor.get("mediaType")
        if media_type in INDEX_TYPES:
            found.extend(image_descriptors(layout, json_blob(layout, descriptor)))
        elif media_type in MANIFEST_TYPES:
            found.append(descriptor)
        else:
            raise LayoutError(f"{layout}: unsupported manifest media type {media_type!r}")
    return found


def platform_image(
    layout: Path, descriptor: dict[str, object], architecture: str, release: Release
) -> PlatformImage:
    """Verify one image manifest's blobs, platform and release labels."""
    manifest = json_blob(layout, descriptor)
    config_descriptor = json_object(manifest.get("config"), f"{layout}: manifest config")
    layers = manifest.get("layers")
    if not isinstance(layers, list) or not layers:
        raise LayoutError(f"{layout}: manifest has no layers")
    layer_descriptors = [json_object(layer, f"{layout}: layer") for layer in layers]
    for layer in layer_descriptors:
        verify_blob(layout, layer)
    config = json_blob(layout, config_descriptor)
    found = (config.get("os"), config.get("architecture"))
    if found != ("linux", architecture):
        raise LayoutError(
            f"{layout}: image is {found[0]}/{found[1]}, expected linux/{architecture}"
        )
    labels = json_object(
        json_object(config.get("config"), f"{layout}: config").get("Labels"), f"{layout}: labels"
    )
    for key, expected in release.labels.items():
        if labels.get(key) != expected:
            raise LayoutError(
                f"{layout}: label {key} is {labels.get(key)!r}, expected {expected!r}"
            )
    return PlatformImage(
        architecture,
        {
            "mediaType": manifest.get("mediaType", descriptor.get("mediaType")),
            "digest": descriptor.get("digest"),
            "size": descriptor.get("size"),
            "platform": {"architecture": architecture, "os": "linux"},
        },
        tuple(str(item["digest"]) for item in (descriptor, config_descriptor, *layer_descriptors)),
    )


def input_image(layout: Path, architecture: str, release: Release) -> PlatformImage:
    found = image_descriptors(layout, read_layout_index(layout))
    if len(found) != 1:
        raise LayoutError(f"{layout}: expected one image manifest, found {len(found)}")
    return platform_image(layout, found[0], architecture, release)


def write_blob(output: Path, content: bytes) -> dict[str, object]:
    digest = hashlib.sha256(content).hexdigest()
    (output / "blobs" / "sha256" / digest).write_bytes(content)
    return {"mediaType": OCI_INDEX, "digest": f"sha256:{digest}", "size": len(content)}


def assemble(layouts: dict[str, Path], release: Release, output: Path) -> None:
    """Write a new OCI layout holding one two-platform index tagged with the release tags."""
    if sorted(layouts) != sorted(ARCHITECTURES):
        raise LayoutError(f"expected one layout per architecture {ARCHITECTURES}")
    images = [input_image(layouts[arch], arch, release) for arch in ARCHITECTURES]
    output.mkdir(parents=True)
    (output / "blobs" / "sha256").mkdir(parents=True)
    for image in images:
        for digest in image.blobs:
            shutil.copyfile(
                blob_path(layouts[image.architecture], digest), blob_path(output, digest)
            )
    index = {
        "schemaVersion": 2,
        "mediaType": OCI_INDEX,
        "manifests": [image.descriptor for image in images],
        "annotations": release.labels,
    }
    descriptor = write_blob(output, json.dumps(index, indent=2, sort_keys=True).encode())
    tagged = [{**descriptor, "annotations": {REF_NAME: tag}} for tag in release.tags]
    top = {"schemaVersion": 2, "mediaType": OCI_INDEX, "manifests": tagged}
    (output / "index.json").write_text(json.dumps(top, indent=2, sort_keys=True) + "\n")
    (output / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}\n')


def inspect(layout: Path, release: Release) -> dict[str, object]:
    """Re-read an assembled layout from disk and report its tags and platform digests."""
    tagged = descriptors(read_layout_index(layout), str(layout))
    names = [json_object(item.get("annotations"), str(layout)).get(REF_NAME) for item in tagged]
    if sorted(map(str, names)) != sorted(release.tags) or len(names) != len(release.tags):
        raise LayoutError(f"{layout}: tags {names}, expected {list(release.tags)}")
    if len({item.get("digest") for item in tagged}) != 1 or tagged[0].get("mediaType") != OCI_INDEX:
        raise LayoutError(f"{layout}: release tags must name one image index")
    index = json_blob(layout, tagged[0])
    platforms: dict[str, object] = {}
    for descriptor in descriptors(index, str(layout)):
        platform = json_object(descriptor.get("platform"), f"{layout}: platform")
        architecture = str(platform.get("architecture"))
        if architecture in platforms or architecture not in ARCHITECTURES:
            raise LayoutError(f"{layout}: unexpected platform {platform}")
        platforms[architecture] = platform_image(
            layout, descriptor, architecture, release
        ).descriptor["digest"]
    if sorted(platforms) != sorted(ARCHITECTURES):
        raise LayoutError(f"{layout}: platforms {sorted(platforms)}, expected {ARCHITECTURES}")
    return {
        "index_digest": tagged[0]["digest"],
        "tags": list(release.tags),
        "platforms": platforms,
        "labels": release.labels,
    }


def layout_argument(value: str) -> tuple[str, Path]:
    architecture, separator, path = value.partition("=")
    if not separator or architecture not in ARCHITECTURES:
        raise argparse.ArgumentTypeError(f"expected ARCH=DIR with ARCH in {ARCHITECTURES}")
    return architecture, Path(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("assemble", "inspect"))
    parser.add_argument("--version", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", type=Path, required=True, help="Assembled layout directory.")
    parser.add_argument("--layout", type=layout_argument, action="append", default=[])
    arguments = parser.parse_args(argv)
    try:
        release = Release(arguments.version, arguments.revision, arguments.source)
        if arguments.command == "assemble":
            layouts = dict(arguments.layout)
            if len(layouts) != len(arguments.layout):
                raise LayoutError("each architecture may be given once")
            assemble(layouts, release, arguments.output)
        print(json.dumps(inspect(arguments.output, release), indent=2, sort_keys=True))
    except (LayoutError, OSError, json.JSONDecodeError) as error:
        print(f"release index: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
