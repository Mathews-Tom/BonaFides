# SPDX-License-Identifier: MIT OR Apache-2.0
"""Synthetic OCI layouts cover the local two-platform release index."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools.release_index import (
    MAX_METADATA_BYTES,
    OCI_INDEX,
    OCI_MANIFEST,
    LayoutError,
    Release,
    main,
)

RELEASE = Release("0.1.0", "a" * 40, "https://example.invalid/bonafides")
RELEASE_REVISION = "org.opencontainers.image.revision"


def put(layout: Path, content: bytes, media_type: str) -> dict[str, object]:
    digest = hashlib.sha256(content).hexdigest()
    (layout / "blobs" / "sha256").mkdir(parents=True, exist_ok=True)
    (layout / "blobs" / "sha256" / digest).write_bytes(content)
    return {"mediaType": media_type, "digest": f"sha256:{digest}", "size": len(content)}


def saved_layout(
    root: Path, architecture: str, *, labels: dict[str, str] | None = None, nested: bool = False
) -> Path:
    """Write the shape `docker save` emits: a direct manifest, or a nested index with attestation."""
    layout = root / architecture
    layer = put(layout, f"layer-{architecture}".encode(), "application/vnd.oci.image.layer.v1.tar")
    config = {
        "os": "linux",
        "architecture": architecture,
        "config": {"Labels": RELEASE.labels if labels is None else labels},
    }
    config_descriptor = put(
        layout, json.dumps(config).encode(), "application/vnd.oci.image.config.v1+json"
    )
    manifest = {
        "schemaVersion": 2,
        "mediaType": OCI_MANIFEST,
        "config": config_descriptor,
        "layers": [layer],
    }
    descriptor = put(layout, json.dumps(manifest).encode(), OCI_MANIFEST)
    if nested:
        attestation = put(layout, b"{}", OCI_MANIFEST)
        attestation["annotations"] = {"vnd.docker.reference.type": "attestation-manifest"}
        inner = {"schemaVersion": 2, "mediaType": OCI_INDEX, "manifests": [descriptor, attestation]}
        descriptor = put(layout, json.dumps(inner).encode(), OCI_INDEX)
    top = {"schemaVersion": 2, "mediaType": OCI_INDEX, "manifests": [descriptor]}
    (layout / "index.json").write_text(json.dumps(top))
    (layout / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}')
    return layout


def run(command: str, output: Path, *layouts: Path) -> int:
    arguments = [
        command,
        "--version",
        RELEASE.version,
        "--revision",
        RELEASE.revision,
        "--source",
        RELEASE.source,
        "--output",
        str(output),
    ]
    for layout in layouts:
        arguments += ["--layout", f"{layout.name}={layout}"]
    return main(arguments)


def test_assemble_two_saved_layouts_tags_one_index_with_both_platforms(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    amd64 = saved_layout(tmp_path, "amd64")
    arm64 = saved_layout(tmp_path, "arm64", nested=True)
    output = tmp_path / "index"

    assert run("assemble", output, amd64, arm64) == 0

    report = json.loads(capsys.readouterr().out)
    assert report["tags"] == ["0.1.0", "sha-aaaaaaaaaaaa"]
    assert sorted(report["platforms"]) == ["amd64", "arm64"]
    top = json.loads((output / "index.json").read_text())
    assert {item["digest"] for item in top["manifests"]} == {report["index_digest"]}
    assert run("inspect", output) == 0


def test_assemble_mislabelled_image_refuses_without_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    amd64 = saved_layout(tmp_path, "amd64", labels={**RELEASE.labels, RELEASE_REVISION: "b" * 40})
    output = tmp_path / "index"

    assert run("assemble", output, amd64, saved_layout(tmp_path, "arm64")) == 1

    assert "org.opencontainers.image.revision" in capsys.readouterr().err
    assert not output.exists()


def test_assemble_wrong_architecture_layout_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    arm64 = saved_layout(tmp_path, "arm64")
    mislabelled = tmp_path / "amd64"
    arm64.rename(mislabelled)

    assert run("assemble", tmp_path / "index", mislabelled, saved_layout(tmp_path, "arm64")) == 1

    assert "expected linux/amd64" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["strip", "assemble"])
@pytest.mark.parametrize("corruption", ["digest", "size", "missing"])
def test_runner_rejects_invalid_layers_before_upload(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], command: str, corruption: str
) -> None:
    amd64 = saved_layout(tmp_path, "amd64")
    layer = amd64 / "blobs" / "sha256" / hashlib.sha256(b"layer-amd64").hexdigest()
    if corruption == "missing":
        layer.unlink()
    elif corruption == "size":
        layer.write_bytes(b"layer-amd64-extra")
    else:
        layer.write_bytes(b"tamper!!!!!")
    layouts = [amd64]
    if command == "assemble":
        layouts.append(saved_layout(tmp_path, "arm64"))
    output = tmp_path / "index"

    assert run(command, output, *layouts) == 1
    assert not output.exists()
    assert "blob" in capsys.readouterr().err or corruption == "missing"


def test_full_and_layer_free_inputs_have_identical_index_and_manifest_digests(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    originals = [saved_layout(tmp_path / "full", arch, nested=True) for arch in ("amd64", "arm64")]
    full_output = tmp_path / "full-index"
    assert run("assemble", full_output, *originals) == 0
    full_report = json.loads(capsys.readouterr().out)
    stripped = []
    for original in originals:
        output = tmp_path / "stripped" / original.name
        assert run("strip", output, original) == 0
        report = json.loads(capsys.readouterr().out)
        assert report["layer_blobs_uploaded"] == 0
        assert len(report["layers_verified_on_runner"]) == 1
        assert len(list((output / "blobs" / "sha256").iterdir())) == 2
        stripped.append(output)
    stripped_output = tmp_path / "stripped-index"

    assert run("assemble", stripped_output, *stripped) == 0
    report = json.loads(capsys.readouterr().out)

    assert report["index_digest"] == full_report["index_digest"]
    assert report["platforms"] == full_report["platforms"]
    assert report["layer_blobs_present"] == 0
    for layers in report["layers"].values():
        assert layers[0]["verification"] == "unverified-here-but-verified-on-runner"
    assert len(list((stripped_output / "blobs" / "sha256").iterdir())) == 5
    assert sum(path.stat().st_size for path in stripped_output.rglob("*") if path.is_file()) < (
        MAX_METADATA_BYTES
    )
    assert run("inspect", stripped_output) == 0


@pytest.mark.parametrize("blob_kind", ["layer", "extra", "oversized"])
def test_metadata_only_inspection_refuses_layer_extra_or_oversized_artifact(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], blob_kind: str
) -> None:
    output = tmp_path / "index"
    assert (
        run("assemble", output, saved_layout(tmp_path, "amd64"), saved_layout(tmp_path, "arm64"))
        == 0
    )
    if blob_kind == "layer":
        put(output, b"layer-amd64", "application/vnd.oci.image.layer.v1.tar")
    elif blob_kind == "extra":
        put(output, b"unexpected blob", "application/octet-stream")
    else:
        with (output / "oci-layout").open("a") as stream:
            stream.write(" " * MAX_METADATA_BYTES)
    capsys.readouterr()
    assert (
        main(
            [
                "inspect",
                "--version",
                RELEASE.version,
                "--revision",
                RELEASE.revision,
                "--source",
                RELEASE.source,
                "--output",
                str(output),
                "--metadata-only",
            ]
        )
        == 1
    )
    assert "metadata artifact" in capsys.readouterr().err


def test_inspect_corrupted_present_layer_reports_digest_mismatch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "index"
    assert (
        run("assemble", output, saved_layout(tmp_path, "amd64"), saved_layout(tmp_path, "arm64"))
        == 0
    )
    layer = hashlib.sha256(b"layer-arm64").hexdigest()
    (output / "blobs" / "sha256" / layer).write_bytes(b"tampered!!!")
    capsys.readouterr()

    assert run("inspect", output) == 1
    assert "does not match its descriptor" in capsys.readouterr().err


@pytest.mark.parametrize("blob_kind", ["manifest", "config"])
def test_assemble_layer_free_input_requires_manifest_and_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], blob_kind: str
) -> None:
    layouts = []
    for arch in ("amd64", "arm64"):
        original = saved_layout(tmp_path / "full", arch)
        output = tmp_path / "stripped" / arch
        assert run("strip", output, original) == 0
        layouts.append(output)
    top = json.loads((layouts[0] / "index.json").read_text())
    manifest_path = layouts[0] / "blobs" / "sha256" / top["manifests"][0]["digest"].split(":")[1]
    manifest = json.loads(manifest_path.read_text())
    path = (
        manifest_path
        if blob_kind == "manifest"
        else (layouts[0] / "blobs" / "sha256" / manifest["config"]["digest"].split(":")[1])
    )
    path.unlink()
    capsys.readouterr()

    assert run("assemble", tmp_path / "index", *layouts) == 1
    assert "missing blob" in capsys.readouterr().err


def test_inspect_missing_release_tag_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "index"
    run("assemble", output, saved_layout(tmp_path, "amd64"), saved_layout(tmp_path, "arm64"))
    top = json.loads((output / "index.json").read_text())
    top["manifests"] = top["manifests"][:1]
    (output / "index.json").write_text(json.dumps(top))
    capsys.readouterr()

    assert run("inspect", output) == 1

    assert "expected ['0.1.0', 'sha-aaaaaaaaaaaa']" in capsys.readouterr().err


def test_assemble_existing_output_is_never_overwritten(tmp_path: Path) -> None:
    output = tmp_path / "index"
    output.mkdir()

    assert (
        run("assemble", output, saved_layout(tmp_path, "amd64"), saved_layout(tmp_path, "arm64"))
        == 1
    )

    assert list(output.iterdir()) == []


@pytest.mark.parametrize(
    ("version", "revision", "source"),
    [
        ("v0.1.0", "a" * 40, RELEASE.source),
        ("0.1.0", "abc", RELEASE.source),
        ("0.1.0", "a" * 40, ""),
    ],
)
def test_release_invalid_identity_raises_layout_error(
    version: str, revision: str, source: str
) -> None:
    with pytest.raises(LayoutError):
        Release(version, revision, source)
