# SPDX-License-Identifier: MIT OR Apache-2.0
"""Candidate manifest binding, real cosign signing and the synthetic gate controls."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from tools import candidate_proof, gate_controls, release_candidate
from tools.release_candidate import (
    SBOM_IMAGE_ID,
    CandidateError,
    build_manifest,
    evidence_names,
    image_bindings,
    main,
    render,
    require_same,
)
from tools.release_index import (
    ARCHITECTURES,
    OCI_INDEX,
    OCI_MANIFEST,
    LayoutError,
    Release,
    assemble,
)

RELEASE = Release("0.1.0", "a" * 40, "https://example.invalid/bonafides")
HAS_COSIGN = shutil.which("cosign") is not None
needs_cosign = pytest.mark.skipif(not HAS_COSIGN, reason="requires the pinned cosign binary")


def put(layout: Path, content: bytes, media_type: str) -> dict[str, object]:
    digest = hashlib.sha256(content).hexdigest()
    (layout / "blobs" / "sha256").mkdir(parents=True, exist_ok=True)
    (layout / "blobs" / "sha256" / digest).write_bytes(content)
    return {"mediaType": media_type, "digest": f"sha256:{digest}", "size": len(content)}


def saved_layout(root: Path, architecture: str) -> Path:
    layout = root / "saved" / architecture
    layer = put(layout, f"layer-{architecture}".encode(), "application/vnd.oci.image.layer.v1.tar")
    config = {"os": "linux", "architecture": architecture, "config": {"Labels": RELEASE.labels}}
    config_descriptor = put(layout, json.dumps(config).encode(), "application/json")
    manifest = {
        "schemaVersion": 2,
        "mediaType": OCI_MANIFEST,
        "config": config_descriptor,
        "layers": [layer],
    }
    descriptor = put(layout, json.dumps(manifest).encode(), OCI_MANIFEST)
    top = {"schemaVersion": 2, "mediaType": OCI_INDEX, "manifests": [descriptor]}
    (layout / "index.json").write_text(json.dumps(top))
    (layout / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}')
    return layout


def write_evidence(root: Path, architecture: str, config_digest: str) -> Path:
    for name in evidence_names(architecture):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        document: dict[str, object] = {"synthetic": name}
        if name == release_candidate.IMAGE_VULNERABILITIES:
            document = {"Metadata": {"ImageID": config_digest}}
        if name == release_candidate.IMAGE_SBOM:
            properties = [{"name": SBOM_IMAGE_ID, "value": config_digest}]
            document = {"metadata": {"component": {"properties": properties}}}
        path.write_text(json.dumps(document, indent=2) + "\n")
    return root


class Candidate:
    def __init__(self, root: Path) -> None:
        layouts = {arch: saved_layout(root, arch) for arch in ARCHITECTURES}
        self.index = root / "index"
        assemble(layouts, RELEASE, self.index)
        platforms = image_bindings(self.index, RELEASE)["platforms"]
        assert isinstance(platforms, dict)
        self.evidence = {
            arch: write_evidence(root / f"evidence-{arch}", arch, platforms[arch]["config_digest"])
            for arch in ARCHITECTURES
        }
        self.root = root

    def manifest(self) -> dict[str, object]:
        return build_manifest(RELEASE, self.index, self.evidence)

    def arguments(self, command: str, *extra: str) -> list[str]:
        arguments = [
            command,
            "--version",
            RELEASE.version,
            "--revision",
            RELEASE.revision,
            "--source",
            RELEASE.source,
            "--index",
            str(self.index),
        ]
        for arch, path in self.evidence.items():
            arguments += ["--evidence", f"{arch}={path}"]
        return [*arguments, *extra]


@pytest.fixture
def candidate(tmp_path: Path) -> Candidate:
    return Candidate(tmp_path)


def test_manifest_binds_identity_native_images_and_every_evidence_hash(
    candidate: Candidate,
) -> None:
    manifest = candidate.manifest()

    platforms = manifest["platforms"]
    evidence = manifest["evidence"]
    assert isinstance(platforms, dict) and isinstance(evidence, dict)
    assert (manifest["version"], manifest["revision"]) == ("0.1.0", "a" * 40)
    assert sorted(platforms) == ["amd64", "arm64"]
    assert all(
        sorted(platforms[arch]) == ["config_digest", "manifest_digest"] for arch in platforms
    )
    assert sorted(evidence["amd64"]) == sorted(evidence_names("amd64"))
    assert manifest["release_authorized"] is False


def test_missing_evidence_file_is_refused(candidate: Candidate) -> None:
    (candidate.evidence["arm64"] / evidence_names("arm64")[2]).unlink()

    with pytest.raises(CandidateError, match="missing evidence file"):
        candidate.manifest()


@pytest.mark.parametrize(
    "name", [release_candidate.IMAGE_VULNERABILITIES, release_candidate.IMAGE_SBOM]
)
def test_scanner_report_for_another_image_is_refused(candidate: Candidate, name: str) -> None:
    path = candidate.evidence["amd64"] / name
    path.write_text(path.read_text().replace("sha256:", "sha256:0", 1))

    with pytest.raises(CandidateError, match="reports image"):
        candidate.manifest()


def test_changed_evidence_or_revision_differs_from_the_recorded_manifest(
    candidate: Candidate,
) -> None:
    recorded = candidate.manifest()
    (candidate.evidence["amd64"] / evidence_names("amd64")[3]).write_text("{}\n")

    with pytest.raises(CandidateError, match="differs from current inputs"):
        require_same(recorded, candidate.manifest())
    with pytest.raises(LayoutError, match="expected \\[.*sha-bbbbbbbbbbbb"):
        build_manifest(
            Release("0.1.0", "b" * 40, RELEASE.source), candidate.index, candidate.evidence
        )


def test_create_cli_writes_manifest_and_refuses_overwrite(
    candidate: Candidate, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = tmp_path / "candidate.json"

    assert main(candidate.arguments("create", "--manifest", str(manifest))) == 0
    assert manifest.read_text() == render(candidate.manifest())
    assert main(candidate.arguments("create", "--manifest", str(manifest))) == 1
    assert "already exists" in capsys.readouterr().err


def sign_manifest(candidate: Candidate, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    monkeypatch.setenv("COSIGN_PASSWORD", "synthetic-proof-passphrase")
    private = candidate.root / "private"
    private.mkdir()
    key, public = candidate_proof.generate_key_pair("cosign", private / "proof")
    _, wrong = candidate_proof.generate_key_pair("cosign", private / "wrong")
    manifest = candidate.root / "candidate.json"
    assert main(candidate.arguments("create", "--manifest", str(manifest))) == 0
    bundle = candidate.root / "candidate.sigstore.json"
    assert (
        main(
            [
                "sign",
                "--manifest",
                str(manifest),
                "--private-key",
                str(key),
                "--bundle",
                str(bundle),
            ]
        )
        == 0
    )
    return {"manifest": manifest, "bundle": bundle, "public": public, "wrong": wrong}


def verify(candidate: Candidate, files: dict[str, Path], *, key: str = "public") -> int:
    return main(
        candidate.arguments(
            "verify",
            "--manifest",
            str(files["manifest"]),
            "--bundle",
            str(files["bundle"]),
            "--trusted-public-key",
            str(files[key]),
        )
    )


@needs_cosign
def test_cosign_signature_verifies_only_with_the_trusted_key_and_exact_bytes(
    candidate: Candidate, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    files = sign_manifest(candidate, monkeypatch)

    assert verify(candidate, files) == 0
    assert "no release is authorized" in capsys.readouterr().out
    assert verify(candidate, files, key="wrong") == 1
    assert "cosign verify-blob" in capsys.readouterr().err
    files["manifest"].write_text(files["manifest"].read_text().replace("false", "true", 1))
    assert verify(candidate, files) == 1
    assert "cosign verify-blob" in capsys.readouterr().err


@needs_cosign
def test_signed_candidate_rejects_evidence_changed_after_signing(
    candidate: Candidate, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    files = sign_manifest(candidate, monkeypatch)
    (candidate.evidence["arm64"] / evidence_names("arm64")[4]).write_text("[]\n")

    assert verify(candidate, files) == 1
    assert "differs from current inputs" in capsys.readouterr().err


@needs_cosign
def test_candidate_proof_rejects_every_tampered_case_and_uploads_no_private_key(
    candidate: Candidate, tmp_path: Path
) -> None:
    output = tmp_path / "public"

    report = candidate_proof.prove(
        RELEASE, candidate.index, candidate.evidence, tmp_path / "private-proof", output
    )

    cases = report["negative_cases"]
    assert isinstance(cases, list)
    assert [case["result"] for case in cases] == ["rejected"] * 6
    assert sorted(path.name for path in output.iterdir()) == [
        "candidate-proof.json",
        "proof-public-key.pub",
        "release-candidate.json",
        "release-candidate.sigstore.json",
    ]
    assert "PRIVATE KEY" not in "".join(path.read_text() for path in output.iterdir())
    assert os.environ["COSIGN_PASSWORD"] not in (output / "candidate-proof.json").read_text()


def test_sign_without_passphrase_variable_fails_before_cosign_prompts(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("COSIGN_PASSWORD", raising=False)

    status = main(["sign", "--manifest", "m", "--private-key", "k", "--bundle", "b"])

    assert status == 1
    assert "COSIGN_PASSWORD" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("violation", "expected"),
    [
        ({"leak.txt": gate_controls.SYNTHETIC_SECRET}, "potential secret pattern 1"),
        ({"app.py": "print('x')\n"}, "missing original-source SPDX header"),
    ],
)
def test_source_gate_controls_reject_violations_with_redacted_findings(
    tmp_path: Path, violation: dict[str, str], expected: str
) -> None:
    control = gate_controls.source_control(tmp_path, "case", violation)

    assert (control["clean_exit"], control["violation_exit"]) == (0, 1)
    assert expected in json.dumps(control)
    assert gate_controls.SYNTHETIC_SECRET not in json.dumps(control)


def test_source_gate_control_fails_when_the_gate_accepts_the_violation(tmp_path: Path) -> None:
    with pytest.raises(gate_controls.ControlError, match="exited 0"):
        gate_controls.source_control(tmp_path, "harmless", {"notes.txt": "harmless\n"})


def test_rights_control_keeps_the_unapproved_component_blocking(tmp_path: Path) -> None:
    control = gate_controls.rights_control(tmp_path)

    assert control["violation_exit"] == 1
    assert control["release_eligible"] is False
