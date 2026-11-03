# SPDX-License-Identifier: MIT OR Apache-2.0
"""Assemble and inspect a local two-platform OCI image index; never contacts a registry.

Each native runner verifies every layer in its extracted `docker save` output, then `strip`
copies only manifests and configs for upload. `assemble` accepts full or layer-free inputs
and always emits metadata only. Manifest/index digests are unchanged by omitting layer bytes.
`inspect` verifies present blobs and explicitly reports absent layers as unverified here.
No layer bytes are uploaded; these incomplete layouts cannot be used to run the image.
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
LAYER_VERIFICATION = "io.bonafides.layers-verified-before-omission"
MAX_METADATA_BYTES = 1 << 20


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
    layers: tuple[dict[str, object], ...]
    absent_layers: tuple[str, ...]


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
    layout: Path,
    descriptor: dict[str, object],
    architecture: str,
    release: Release,
    *,
    require_layers: bool = False,
) -> PlatformImage:
    """Verify manifest/config and every present layer; require all layers before stripping."""
    manifest = json_blob(layout, descriptor)
    config_descriptor = json_object(manifest.get("config"), f"{layout}: manifest config")
    layers = manifest.get("layers")
    if not isinstance(layers, list) or not layers:
        raise LayoutError(f"{layout}: manifest has no layers")
    layer_descriptors = [json_object(layer, f"{layout}: layer") for layer in layers]
    absent: list[str] = []
    for layer in layer_descriptors:
        path = blob_path(layout, layer.get("digest"))
        size = layer.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise LayoutError(f"{layout}: invalid layer size {size!r}")
        if require_layers or path.exists():
            verify_blob(layout, layer)
        else:
            absent.append(str(layer["digest"]))
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
        tuple(str(item["digest"]) for item in (descriptor, config_descriptor)),
        tuple(layer_descriptors),
        tuple(absent),
    )


def input_image(
    layout: Path, architecture: str, release: Release, *, require_layers: bool = False
) -> PlatformImage:
    found = image_descriptors(layout, read_layout_index(layout))
    if len(found) != 1:
        raise LayoutError(f"{layout}: expected one image manifest, found {len(found)}")
    return platform_image(layout, found[0], architecture, release, require_layers=require_layers)


def write_blob(output: Path, content: bytes) -> dict[str, object]:
    digest = hashlib.sha256(content).hexdigest()
    (output / "blobs" / "sha256" / digest).write_bytes(content)
    return {"mediaType": OCI_INDEX, "digest": f"sha256:{digest}", "size": len(content)}


def metadata_only(layout: Path, images: list[PlatformImage], indexes: tuple[str, ...] = ()) -> None:
    """Fail closed before upload: only known manifest/config/index blobs, never layer blobs."""
    allowed = {"oci-layout", "index.json"}
    for digest in (*indexes, *(digest for image in images for digest in image.blobs)):
        allowed.add(blob_path(layout, digest).relative_to(layout).as_posix())
    for image in images:
        for layer in image.layers:
            if blob_path(layout, layer["digest"]).exists():
                raise LayoutError(f"{layout}: metadata artifact contains layer {layer['digest']}")
    files = {path.relative_to(layout).as_posix() for path in layout.rglob("*") if path.is_file()}
    if files != allowed or any(path.is_symlink() for path in layout.rglob("*")):
        raise LayoutError(f"{layout}: metadata artifact contains missing or unexpected files")
    if sum((layout / name).stat().st_size for name in files) > MAX_METADATA_BYTES:
        raise LayoutError(f"{layout}: metadata artifact exceeds {MAX_METADATA_BYTES} bytes")


def strip(layouts: dict[str, Path], release: Release, output: Path) -> dict[str, object]:
    """Verify all saved layers locally, then export only the platform manifest and config."""
    if len(layouts) != 1:
        raise LayoutError("strip requires exactly one architecture layout")
    architecture, layout = next(iter(layouts.items()))
    image = input_image(layout, architecture, release, require_layers=True)
    output.mkdir(parents=True)
    (output / "blobs" / "sha256").mkdir(parents=True)
    for digest in image.blobs:
        shutil.copyfile(blob_path(layout, digest), blob_path(output, digest))
    top = {
        "schemaVersion": 2,
        "mediaType": OCI_INDEX,
        "manifests": [image.descriptor],
        "annotations": {LAYER_VERIFICATION: "sha256-and-size"},
    }
    (output / "index.json").write_text(json.dumps(top, indent=2, sort_keys=True) + "\n")
    (output / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}\n')
    metadata_only(output, [image])
    return {
        "platform": f"linux/{architecture}",
        "manifest_digest": image.descriptor["digest"],
        "layers_verified_on_runner": list(image.layers),
        "layer_blobs_uploaded": 0,
    }


def assemble(layouts: dict[str, Path], release: Release, output: Path) -> None:
    """Write a new OCI layout holding one two-platform index tagged with the release tags."""
    if sorted(layouts) != sorted(ARCHITECTURES):
        raise LayoutError(f"expected one layout per architecture {ARCHITECTURES}")
    images = [input_image(layouts[arch], arch, release) for arch in ARCHITECTURES]
    for image in images:
        if image.absent_layers:
            annotations = json_object(
                read_layout_index(layouts[image.architecture]).get("annotations", {}),
                f"{image.architecture}: layout annotations",
            )
            if annotations.get(LAYER_VERIFICATION) != "sha256-and-size":
                raise LayoutError(f"{image.architecture}: absent layers lack runner verification")
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
    top["annotations"] = {LAYER_VERIFICATION: "sha256-and-size"}
    (output / "index.json").write_text(json.dumps(top, indent=2, sort_keys=True) + "\n")
    (output / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}\n')
    metadata_only(output, images, (str(descriptor["digest"]),))


def inspect(
    layout: Path, release: Release, *, require_metadata_only: bool = False
) -> dict[str, object]:
    """Verify available bytes and report digests plus explicit layer verification scope."""
    top = read_layout_index(layout)
    tagged = descriptors(top, str(layout))
    names = [json_object(item.get("annotations"), str(layout)).get(REF_NAME) for item in tagged]
    if sorted(map(str, names)) != sorted(release.tags) or len(names) != len(release.tags):
        raise LayoutError(f"{layout}: tags {names}, expected {list(release.tags)}")
    if len({item.get("digest") for item in tagged}) != 1 or tagged[0].get("mediaType") != OCI_INDEX:
        raise LayoutError(f"{layout}: release tags must name one image index")
    index = json_blob(layout, tagged[0])
    platforms: dict[str, object] = {}
    images: list[PlatformImage] = []
    layer_reports: dict[str, object] = {}
    for descriptor in descriptors(index, str(layout)):
        platform = json_object(descriptor.get("platform"), f"{layout}: platform")
        architecture = str(platform.get("architecture"))
        if architecture in platforms or architecture not in ARCHITECTURES:
            raise LayoutError(f"{layout}: unexpected platform {platform}")
        image = platform_image(layout, descriptor, architecture, release)
        images.append(image)
        platforms[architecture] = image.descriptor["digest"]
        runner_verified = (
            json_object(top.get("annotations", {}), str(layout)).get(LAYER_VERIFICATION)
            == "sha256-and-size"
        )
        layer_reports[architecture] = [
            {
                **layer,
                "verification": (
                    "unverified-here-but-verified-on-runner"
                    if runner_verified
                    else "unverified-here"
                )
                if layer["digest"] in image.absent_layers
                else "verified-here",
            }
            for layer in image.layers
        ]
    if sorted(platforms) != sorted(ARCHITECTURES):
        raise LayoutError(f"{layout}: platforms {sorted(platforms)}, expected {ARCHITECTURES}")
    if require_metadata_only:
        metadata_only(layout, images, (str(tagged[0]["digest"]),))
    return {
        "index_digest": tagged[0]["digest"],
        "tags": list(release.tags),
        "platforms": platforms,
        "labels": release.labels,
        "layers": layer_reports,
        "layer_blobs_present": sum(
            len(image.layers) - len(image.absent_layers) for image in images
        ),
    }


def layout_argument(value: str) -> tuple[str, Path]:
    architecture, separator, path = value.partition("=")
    if not separator or architecture not in ARCHITECTURES:
        raise argparse.ArgumentTypeError(f"expected ARCH=DIR with ARCH in {ARCHITECTURES}")
    return architecture, Path(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("strip", "assemble", "inspect"))
    parser.add_argument("--version", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", type=Path, required=True, help="Assembled layout directory.")
    parser.add_argument("--layout", type=layout_argument, action="append", default=[])
    parser.add_argument("--metadata-only", action="store_true", help="Reject layer/extra bytes.")
    arguments = parser.parse_args(argv)
    try:
        release = Release(arguments.version, arguments.revision, arguments.source)
        layouts = dict(arguments.layout)
        if len(layouts) != len(arguments.layout):
            raise LayoutError("each architecture may be given once")
        if arguments.command == "strip":
            report = strip(layouts, release, arguments.output)
        else:
            if arguments.command == "assemble":
                assemble(layouts, release, arguments.output)
            report = inspect(
                arguments.output, release, require_metadata_only=arguments.metadata_only
            )
        print(json.dumps(report, indent=2, sort_keys=True))
    except (LayoutError, OSError, json.JSONDecodeError) as error:
        print(f"release index: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
