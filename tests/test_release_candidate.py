# SPDX-License-Identifier: MIT OR Apache-2.0
"""Candidate manifest binding, real cosign signing and the synthetic gate controls."""

from __future__ import annotations

import hashlib
import json
import os
import re
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
        if name == "artifacts/locked-components.json":
            document = {
                "artifacts_inspected": [
                    {
                        "kind": "docker-image-inspect",
                        "metadata": {
                            "Id": config_digest,
                            "platform_id": config_digest,
                            "Architecture": architecture,
                            "Os": "linux",
                        },
                    }
                ],
                "components": [{"docker_image_id": config_digest}],
            }
        if name.endswith("/receipt.json"):
            identity = {
                "id": config_digest,
                "architecture": f"linux/{architecture}",
                "version": RELEASE.version,
                "revision": RELEASE.revision,
            }
            document = {"platform": f"linux/{architecture}"}
            if f"-install-{architecture}/" in name:
                document["images"] = {"first": identity}
            elif f"-core-{architecture}/" in name:
                document["image"] = identity
            else:
                document["containers"] = {
                    role: {"image": config_digest}
                    for role in ("api", "worker", "renderer", "parser", "ingress", "source-egress")
                }
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


def test_missing_evidence_file_is_refused(candidate: Candidate) -> None:
    (candidate.evidence["arm64"] / evidence_names("arm64")[2]).unlink()

    with pytest.raises(CandidateError, match="missing evidence file"):
        candidate.manifest()


@pytest.mark.parametrize(
    "name", [release_candidate.IMAGE_VULNERABILITIES, release_candidate.IMAGE_SBOM]
)
def test_scanner_report_for_another_image_is_refused(candidate: Candidate, name: str) -> None:
    path = candidate.evidence["amd64"] / name
    path.write_text(re.sub(r"sha256:[0-9a-f]{64}", "sha256:" + "b" * 64, path.read_text()))

    with pytest.raises(CandidateError, match="reports image"):
        candidate.manifest()


def test_changed_evidence_or_revision_differs_from_the_recorded_manifest(
    candidate: Candidate,
) -> None:
    recorded = candidate.manifest()
    path = candidate.evidence["amd64"] / evidence_names("amd64")[3]
    path.write_text(path.read_text().replace("synthetic", "different"))

    with pytest.raises(CandidateError, match="differs from current inputs"):
        require_same(recorded, candidate.manifest())
    with pytest.raises(LayoutError, match="expected \\[.*sha-bbbbbbbbbbbb"):
        build_manifest(
            Release("0.1.0", "b" * 40, RELEASE.source), candidate.index, candidate.evidence
        )


def test_create_cli_writes_manifest_and_refuses_overwrite(
    candidate: Candidate, tmp_path: Path
) -> None:
    manifest = tmp_path / "candidate.json"

    assert main(candidate.arguments("create", "--manifest", str(manifest))) == 0
    original = manifest.read_bytes()
    assert main(candidate.arguments("create", "--manifest", str(manifest))) == 1
    assert manifest.read_bytes() == original


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
    path = candidate.evidence["arm64"] / evidence_names("arm64")[3]
    path.write_text(path.read_text().replace("synthetic", "different"))

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
        ({"NOTICE": None}, "NOTICE"),
    ],
)
def test_source_gate_controls_reject_violations_with_redacted_findings(
    tmp_path: Path, violation: dict[str, str | None], expected: str
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
    assert control["clean_exit"] == 0
    assert control["release_eligible"] is False


@pytest.mark.parametrize("extra", [None, {}, 0])
def test_manifest_rejects_added_null_empty_object_and_changed_boolean(
    candidate: Candidate, extra: object
) -> None:
    expected = candidate.manifest()
    recorded = dict(expected)
    recorded["release_authorized" if extra == 0 else "unexpected"] = extra
    with pytest.raises(CandidateError):
        require_same(recorded, expected)


@pytest.mark.parametrize("kind", ["inventory", "install", "core", "ci"])
def test_foreign_image_subject_is_refused(candidate: Candidate, kind: str) -> None:
    name = (
        "artifacts/locked-components.json"
        if kind == "inventory"
        else f".local/bonafides-proof-{kind}-amd64/receipt.json"
    )
    path = candidate.evidence["amd64"] / name
    path.write_text(re.sub(r"sha256:[0-9a-f]{64}", "sha256:" + "b" * 64, path.read_text()))
    with pytest.raises(CandidateError):
        candidate.manifest()


def test_cosign_startup_failure_is_not_counted_as_a_rejected_candidate(tmp_path: Path) -> None:
    cases: list[dict[str, str]] = []

    def unavailable() -> None:
        release_candidate.run_cosign(str(tmp_path / "missing-cosign"), ["version"])

    with pytest.raises(release_candidate.CosignError):
        candidate_proof.expect_rejected(
            cases,
            "tool unavailable",
            unavailable,
        )
    assert cases == []


@needs_cosign
def test_invalid_trusted_key_is_a_tool_failure_not_a_successful_negative_control(
    candidate: Candidate, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = sign_manifest(candidate, monkeypatch)
    files["public"].write_text("not a public key")
    cases: list[dict[str, str]] = []
    with pytest.raises(release_candidate.CosignError):
        candidate_proof.expect_rejected(
            cases,
            "invalid key",
            lambda: release_candidate.verify_signature(
                files["manifest"], files["public"], files["bundle"]
            ),
        )
    assert cases == []
