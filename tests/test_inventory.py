# SPDX-License-Identifier: MIT OR Apache-2.0
"""Synthetic artifact fixtures exercise collection boundaries, not rights approval."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import compliance, inventory


def registry_lock(name: str = "alpha", dependency: str = "") -> str:
    edges = f'dependencies = [{{ name = "{dependency}" }}]\n' if dependency else ""
    return (
        "version = 1\n[[package]]\n"
        f'name = "{name}"\nversion = "1.0"\nsource = {{ registry = "https://example.invalid/simple" }}\n'
        + edges
        + f'sdist = {{ url = "https://example.invalid/{name}-1.0.tar.gz", hash = "sha256:{"a" * 64}" }}\n'
    )


def write_tar(path: Path, files: dict[str, bytes]) -> None:
    with tarfile.open(path, "w") as archive:
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))


def apk_package(
    name: str,
    *,
    version: str = "1.37.0-r31",
    dependencies: str = "",
    provides: str = "",
    files: str = "",
    license_declaration: str = "GPL-2.0-only",
) -> str:
    """Legacy installed-record syntax, including the actual busybox-binsh checksum."""
    return (
        "C:Q1ax93IqPnu5fQTo75Bv1ywGZ7pdA=\n"
        f"P:{name}\nV:{version}\nA:aarch64\nS:100\nI:200\n"
        "T:deterministic installed package fixture\nU:https://example.invalid/source\n"
        f"L:{license_declaration}\no:{name}\nm:Fixture Maintainer\n"
        "t:1768059508\nc:c3ef5d10e6ef6528852c51f0564963e2f8c1be19\n"
        f"D:{dependencies}\np:{provides}\n{files}\n"
    )


def apk_file(directory: str, name: str) -> str:
    return f"F:{directory}\nM:0:0:755\nR:{name}\na:0:0:644\nZ:Q1pcfTfDNEbNKQc2s1tia7da05M8Q=\n"


def fixture_objects(value: inventory.Json) -> list[inventory.Object]:
    return [
        inventory.object_value(entry, "fixture object")
        for entry in inventory.array_value(value, "fixture array")
    ]


def npm_lock(package_keys: tuple[str, ...]) -> str:
    encoded = base64.b64encode(bytes(range(64))).decode()
    return (
        "lockfileVersion: '9.0'\npackages:\n"
        + "".join(
            f"  '{key}':\n    resolution: {{integrity: sha512-{encoded}}}\n" for key in package_keys
        )
        + "snapshots: {}\n"
    )


class DigestTests(unittest.TestCase):
    def test_exact_sha256_rejects_prefix_suffix_and_wrong_algorithms(self) -> None:
        self.assertEqual(inventory.exact_sha256("sha256:" + "A" * 64, "fixture"), "a" * 64)
        for digest in (
            "a" * 64,
            "sha256:" + "a" * 63,
            "sha256:" + "a" * 65,
            "sha256:" + "g" * 64,
            "sha512:" + "a" * 128,
            "xsha256:" + "a" * 64,
            "sha256:" + "a" * 64 + "\n",
        ):
            with self.subTest(digest=digest), self.assertRaises(inventory.CollectionError):
                inventory.exact_sha256(digest, "fixture")

    def test_integrity_preserves_algorithm_and_validates_decoded_length(self) -> None:
        encoded = base64.b64encode(bytes(range(64))).decode()
        result = inventory.normalized_integrity("sha512-" + encoded)
        self.assertEqual(result[0]["algorithm"], "sha512")
        self.assertEqual(result[0]["hex"], bytes(range(64)).hex())
        for bad in ("sha256-" + encoded, "sha512-@@@", "md5-AAAA", "", "sha512-AAAA"):
            with self.subTest(value=bad), self.assertRaises(inventory.CollectionError):
                inventory.normalized_integrity(bad)

    def test_manifest_hash_binds_names_bytes_and_file_boundaries(self) -> None:
        first: list[inventory.Object] = [
            {"path": "a", "sha256": hashlib.sha256(b"ab").hexdigest(), "size": 2},
            {"path": "b", "sha256": hashlib.sha256(b"c").hexdigest(), "size": 1},
        ]
        second: list[inventory.Object] = [
            {"path": "a", "sha256": hashlib.sha256(b"a").hexdigest(), "size": 1},
            {"path": "b", "sha256": hashlib.sha256(b"bc").hexdigest(), "size": 2},
        ]
        self.assertNotEqual(inventory.manifest_digest(first), inventory.manifest_digest(second))
        self.assertEqual(inventory.manifest_digest(first), inventory.manifest_digest(first[::-1]))
        renamed = [dict(item) for item in first]
        renamed[0]["path"] = "renamed"
        self.assertNotEqual(inventory.manifest_digest(first), inventory.manifest_digest(renamed))


class PythonInventoryTests(unittest.TestCase):
    def test_lock_edges_artifacts_and_missing_dependency_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / "uv.lock"
            lock.write_text(registry_lock(dependency="absent"))
            with self.assertRaisesRegex(inventory.CollectionError, "absent from lock"):
                inventory.uv_packages(lock, inventory.Inventory())
            lock.write_text(
                registry_lock(dependency="beta") + registry_lock("beta").split("\n", 1)[1]
            )
            collected = inventory.Inventory()
            packages = inventory.uv_packages(lock, collected)
            alpha = packages[("alpha", "1.0")]
            self.assertIn(
                "pkg:pypi/beta@1.0", inventory.array_value(alpha["dependencies"], "edges")
            )
            artifact = collected.components["pkg:pypi/alpha@1.0?artifact=" + "a" * 64]
            self.assertFalse(artifact["sha256_verified"])
            self.assertEqual(artifact["digest_basis"], "lock-declared-not-downloaded")
            self.assertEqual(collected.graph_errors(), [])
            self.assertIsNone(alpha["sha256"])
            self.assertFalse(
                compliance.check_inventory(collected.snapshot(True))["release_eligible"]
            )
            self.assertEqual(len(compliance.make_sbom(collected.snapshot(True))["components"]), 4)

    def test_installed_metadata_license_native_and_build_backend_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock = root / "uv.lock"
            lock.write_text(registry_lock())
            (root / "pyproject.toml").write_text(
                '[build-system]\nrequires = ["builder==2.0"]\nbuild-backend = "builder.api"\n'
            )
            environment = root / ".venv"
            site = environment / "lib/python3.12/site-packages"
            metadata = site / "alpha-1.0.dist-info"
            metadata.mkdir(parents=True)
            (environment / "pyvenv.cfg").write_text("home = /fixture/python\n")
            (metadata / "METADATA").write_text(
                "Metadata-Version: 2.4\nName: alpha\nVersion: 1.0\nLicense-Expression: MIT\n\n"
            )
            (metadata / "LICENSE").write_text("Synthetic license evidence, not approval.\n")
            native = site / "alpha.so"
            native.write_bytes(b"\x7fELFsynthetic-native-bytes")
            (metadata / "RECORD").write_text(
                "alpha-1.0.dist-info/METADATA,,\nalpha-1.0.dist-info/LICENSE,,\n"
                "alpha-1.0.dist-info/RECORD,,\nalpha.so,,\n"
            )
            collected = inventory.Inventory()
            inventory.collect_python(lock, environment, collected)
            package = collected.components["pkg:pypi/alpha@1.0"]
            self.assertEqual(package["detected_license"], "MIT")
            self.assertIsNone(package["concluded_license"])
            self.assertIsNone(package["reviewer"])
            evidence = inventory.array_value(package["evidence"], "evidence")
            self.assertTrue(any("Synthetic license evidence" in str(item) for item in evidence))
            native_records = [
                value
                for value in collected.components.values()
                if value.get("artifact_kind") == "native-binary"
            ]
            self.assertEqual(len(native_records), 1)
            self.assertEqual(
                native_records[0]["sha256"], hashlib.sha256(native.read_bytes()).hexdigest()
            )
            self.assertIsNone(native_records[0]["detected_license"])
            self.assertTrue(any("builder==2.0" in value for value in collected.blockers))
            self.assertIn("pkg:pypi/builder@2.0?scope=pep517", collected.components)
            self.assertFalse(
                compliance.check_inventory(collected.snapshot(True))["release_eligible"]
            )
            self.assertEqual(collected.graph_errors(), [])

    def test_file_escape_does_not_hash_unowned_external_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            boundary = root / "package"
            boundary.mkdir()
            external = root / "secret.txt"
            external.write_bytes(b"must not become inventory evidence")
            link = boundary / "LICENSE"
            link.symlink_to(external)
            owner = inventory.component("pkg:generic/test@1", "test", "1", "fixture")
            collected = inventory.Inventory()
            files = inventory.installed_files(boundary, [(link, "LICENSE")], owner, collected)
            self.assertEqual(files, [])
            self.assertEqual(owner["evidence"], [])
            self.assertTrue(any("external file excluded" in value for value in collected.blockers))


class WebInventoryTests(unittest.TestCase):
    def test_lock_reader_rejects_unsupported_missing_and_duplicate_integrity(self) -> None:
        valid = npm_lock(("@scope/alpha@1.0",))
        result = inventory.pnpm_integrities(valid)
        self.assertEqual(result["@scope/alpha@1.0"][0]["algorithm"], "sha512")
        for malformed in (
            valid.replace("'9.0'", "'8.0'"),
            "lockfileVersion: '9.0'\npackages:\n  alpha@1:\n    resolution: {tarball: local.tgz}\n",
            "lockfileVersion: '9.0'\npackages:\n  alpha@1:\n    engines: {node: 22}\n",
            valid.replace("snapshots: {}", "  '@scope/alpha@1.0':\n"),
        ):
            with self.subTest(value=malformed), self.assertRaises(inventory.CollectionError):
                inventory.pnpm_integrities(malformed)

    def test_nested_peer_fields_are_not_package_declarations(self) -> None:
        content = npm_lock(("alpha@1", "beta@2")).replace(
            "    resolution:", "    peerDependencies:\n      peer: '*'\n    resolution:"
        )
        result = inventory.pnpm_integrities(content)
        self.assertEqual(set(result), {"alpha@1", "beta@2"})

    def test_absent_platform_optional_is_pending_not_installed_and_required_still_fails(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text(json.dumps({"name": "frontend", "version": "1.0"}))
            (root / "pnpm-lock.yaml").write_text(npm_lock(("alpha@1.0", "beta@1.0")))
            alpha = root / "node_modules/.pnpm/alpha@1.0/node_modules/alpha"
            beta = root / "node_modules/.pnpm/beta@1.0/node_modules/beta"
            alpha.mkdir(parents=True)
            manifest = {
                "name": "alpha",
                "version": "1.0",
                "dependencies": {"beta": "1.0"},
                "optionalDependencies": {"beta": "1.0"},
            }
            (alpha / "package.json").write_text(json.dumps(manifest))
            (root / "dist").mkdir()
            (root / "dist/index.html").write_text("actual fixture bundle")
            graph: inventory.Json = [
                {
                    "dependencies": {
                        "alpha": {
                            "version": "1.0",
                            "path": str(alpha),
                            "dependencies": {
                                "beta": {"from": "beta", "version": "1.0", "path": str(beta)}
                            },
                        }
                    }
                }
            ]
            collected = inventory.Inventory()
            with patch.object(inventory, "run_json", return_value=graph):
                inventory.collect_web(root, collected)
            candidate = collected.components["pkg:npm/beta@1.0?scope=registry-lock"]
            self.assertEqual(
                candidate["absent_optional_graph_paths"], [beta.relative_to(root).as_posix()]
            )
            self.assertEqual(candidate["status"], "pending")
            self.assertFalse(candidate["rights_confirmed"])
            self.assertFalse(
                any(
                    value["name"] == "beta" and value.get("installed_path")
                    for value in collected.components.values()
                )
            )
            alpha_record = next(
                value
                for value in collected.components.values()
                if value["name"] == "alpha" and value.get("installed_path")
            )
            self.assertIn(
                candidate["id"], inventory.array_value(alpha_record["dependencies"], "alpha edges")
            )
            self.assertEqual(collected.graph_errors(), [])
            del manifest["optionalDependencies"]
            (alpha / "package.json").write_text(json.dumps(manifest))
            with (
                patch.object(inventory, "run_json", return_value=graph),
                self.assertRaisesRegex(inventory.CollectionError, "missing installed package.json"),
            ):
                inventory.collect_web(root, inventory.Inventory())
            manifest["optionalDependencies"] = {"beta": "1.0"}
            (alpha / "package.json").write_text(json.dumps(manifest))
            beta.mkdir(parents=True)
            with (
                patch.object(inventory, "run_json", return_value=graph),
                self.assertRaisesRegex(inventory.CollectionError, "missing installed package.json"),
            ):
                inventory.collect_web(root, inventory.Inventory())

    def test_deduplicated_graph_node_uses_expanded_occurrence_without_losing_edges(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text(json.dumps({"name": "frontend", "version": "1.0"}))
            (root / "pnpm-lock.yaml").write_text(npm_lock(("alpha@1.0", "beta@1.0")))
            alpha = root / "node_modules/.pnpm/alpha@1.0/node_modules/alpha"
            beta = root / "node_modules/.pnpm/beta@1.0/node_modules/beta"
            alpha.mkdir(parents=True)
            beta.mkdir(parents=True)
            (alpha / "package.json").write_text(
                json.dumps({"name": "alpha", "version": "1.0", "dependencies": {"beta": "1.0"}})
            )
            (beta / "package.json").write_text(json.dumps({"name": "beta", "version": "1.0"}))
            (root / "dist").mkdir()
            (root / "dist/index.html").write_text("actual fixture bundle")
            reference: inventory.Object = {
                "version": "1.0",
                "path": str(alpha),
                "deduped": True,
                "dedupedDependenciesCount": 1,
            }
            expanded: inventory.Object = {
                "version": "1.0",
                "path": str(alpha),
                "dependencies": {"beta": {"version": "1.0", "path": str(beta)}},
            }
            workspace: inventory.Object = {
                "dependencies": {"alpha": reference},
                "devDependencies": {"alpha-alias": expanded},
            }
            collected = inventory.Inventory()
            with patch.object(inventory, "run_json", return_value=[workspace]):
                inventory.collect_web(root, collected)
            installed = {
                inventory.text(value["name"], "installed package name"): value
                for value in collected.components.values()
                if value.get("installed_path")
            }
            self.assertEqual(set(installed), {"alpha", "beta"})
            self.assertIn(
                installed["beta"]["id"],
                inventory.array_value(installed["alpha"]["dependencies"], "alpha dependencies"),
            )
            self.assertEqual(collected.graph_errors(), [])
            del workspace["devDependencies"]
            with (
                patch.object(inventory, "run_json", return_value=[workspace]),
                self.assertRaisesRegex(inventory.CollectionError, "no expanded graph occurrence"),
            ):
                inventory.collect_web(root, inventory.Inventory())

    def test_installed_graph_and_bundle_are_distinct_byte_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text(json.dumps({"name": "frontend", "version": "1.0"}))
            (root / "pnpm-lock.yaml").write_text(npm_lock(("alpha@1.0", "beta@1.0")))
            alpha = root / "node_modules/.pnpm/alpha@1.0/node_modules/alpha"
            beta = root / "node_modules/.pnpm/beta@1.0/node_modules/beta"
            alpha.mkdir(parents=True)
            beta.mkdir(parents=True)
            (alpha / "package.json").write_text(
                json.dumps(
                    {
                        "name": "alpha",
                        "version": "1.0",
                        "license": "MIT",
                        "dependencies": {"beta": "1.0"},
                    }
                )
            )
            (beta / "package.json").write_text(
                json.dumps({"name": "beta", "version": "1.0", "license": "ISC"})
            )
            (alpha / "LICENSE").write_text("Synthetic Alpha notice")
            (alpha / "alpha.js").write_bytes(b"export const value = 1;")
            bundle = root / "dist/assets/main.js"
            bundle.parent.mkdir(parents=True)
            bundle.write_bytes(b"actual-built-fixture-bytes")
            beta_node: inventory.Object = {"version": "1.0", "path": str(beta)}
            alpha_node: inventory.Object = {
                "version": "1.0",
                "path": str(alpha),
                "dependencies": {"beta": beta_node},
            }
            graph: inventory.Json = [{"dependencies": {"alpha": alpha_node}}]
            collected = inventory.Inventory()
            with patch.object(inventory, "run_json", return_value=graph):
                inventory.collect_web(root, collected)
            packages = [
                value for value in collected.components.values() if value.get("installed_path")
            ]
            self.assertEqual(len(packages), 2)
            alpha_record = next(value for value in packages if value["name"] == "alpha")
            beta_record = next(value for value in packages if value["name"] == "beta")
            self.assertIn(
                beta_record["id"], inventory.array_value(alpha_record["dependencies"], "edges")
            )
            self.assertFalse(alpha_record["registry_integrity_verified"])
            self.assertEqual(
                inventory.object_value(
                    inventory.array_value(alpha_record["registry_integrity"], "SRI")[0], "SRI"
                )["algorithm"],
                "sha512",
            )
            built = next(
                value
                for value in collected.components.values()
                if value.get("artifact_kind") == "built-bundle"
            )
            built_files = inventory.array_value(built["files"], "bundle files")
            self.assertEqual(
                inventory.object_value(built_files[0], "bundle file")["sha256"],
                hashlib.sha256(bundle.read_bytes()).hexdigest(),
            )
            self.assertNotEqual(built["sha256"], hashlib.sha256(bundle.read_bytes()).hexdigest())
            self.assertEqual(collected.graph_errors(), [])
            self.assertFalse(
                compliance.check_inventory(collected.snapshot(True))["release_eligible"]
            )
            del alpha_node["dependencies"]
            with (
                patch.object(inventory, "run_json", return_value=graph),
                self.assertRaisesRegex(
                    inventory.CollectionError, "incomplete installed dependency graph"
                ),
            ):
                inventory.collect_web(root, inventory.Inventory())


class ImageInventoryTests(unittest.TestCase):
    def test_actual_package_files_unknown_native_fonts_and_dictionaries_stay_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "rootfs.tar"
            files = {
                "var/lib/dpkg/status": b"Package: libfixture\nStatus: install ok installed\nVersion: 1.0\nArchitecture: arm64\nConffiles:\nDepends: virtual-unknown\n\n",
                "var/lib/dpkg/info/libfixture:arm64.list": b"/usr/lib/libfixture.so\n/usr/share/doc/libfixture/copyright\n/usr/share/fonts/fixture.ttf\n",
                "usr/lib/libfixture.so": b"\x7fELFsynthetic-library",
                "usr/share/doc/libfixture/copyright": b"Format: fixture\nLicense: LGPL-2.1+\nSynthetic notice\n",
                "usr/share/fonts/fixture.ttf": b"synthetic-font",
                "usr/local/share/hyph_en.dic": b"synthetic-dictionary",
                "usr/local/bin/unowned": b"\x7fELFunknown-program",
            }
            write_tar(archive, files)
            collected = inventory.Inventory()
            inventory.collect_image_tar(archive, "sha256:" + "b" * 64, collected)
            image = next(
                value
                for value in collected.components.values()
                if value.get("artifact_kind") == "exported-rootfs"
            )
            self.assertEqual(image["docker_image_id"], "sha256:" + "b" * 64)
            self.assertNotIn("image_config_digest", image)
            package = next(
                value
                for value in collected.components.values()
                if value.get("name") == "libfixture"
            )
            self.assertEqual(package["declared_license_metadata"], ["LGPL-2.1+"])
            self.assertIsNone(package["detected_license"])
            assets = [
                value
                for value in collected.components.values()
                if value.get("artifact_kind") in ("font", "dictionary", "native-binary")
            ]
            self.assertEqual(len(assets), 4)
            for asset in assets:
                path = inventory.text(asset["name"], "asset name")
                self.assertEqual(asset["sha256"], hashlib.sha256(files[path]).hexdigest())
                self.assertFalse(asset["rights_confirmed"])
                self.assertIsNone(asset["concluded_license"])
            self.assertTrue(any("unowned dictionary" in value for value in collected.blockers))
            self.assertTrue(any("provider unresolved" in value for value in collected.blockers))
            self.assertEqual(collected.graph_errors(), [])
            self.assertFalse(
                compliance.check_inventory(collected.snapshot(False))["release_eligible"]
            )
            compliance.make_sbom(collected.snapshot(False))

    def test_apk_rootfs_ownership_provenance_links_and_metadata_only_packages(self) -> None:
        database = (
            apk_package(
                "busybox",
                provides="cmd:busybox=1.37.0-r31",
                files=apk_file("bin", "busybox"),
            )
            + apk_package(
                "busybox-binsh",
                dependencies="busybox=1.37.0-r31",
                provides="/bin/sh cmd:sh=1.37.0-r31",
                files=apk_file("bin", "sh"),
            )
            + apk_package(
                "musl",
                version="1.2.5-r10",
                provides="so:libc.musl-aarch64.so.1=1",
                files=apk_file("lib", "libc.musl-aarch64.so.1"),
            )
            + apk_package(
                "render-assets",
                dependencies="/bin/sh cmd:busybox so:libc.musl-aarch64.so.1",
                license_declaration="custom-license AND (MIT OR UnknownRef)",
                files=(
                    apk_file("usr/share/fonts", "fixture.ttf")
                    + apk_file("usr/share/hyphen", "fixture.dic")
                    + apk_file("usr/share/doc/render-assets", "copyright")
                    + apk_file("usr/lib", "libalias.so")
                ),
            )
            + (
                "C:Q1/S2qWQJ78QrRHs+XgT/N821PlTc=\nP:.python-rundeps\n"
                "V:20261021.233035\nA:noarch\nS:0\nI:0\nT:virtual meta package\nU:\nL:\n"
                "D:so:libc.musl-aarch64.so.1\n\n"
            )
        ).encode()
        files = {
            "lib/apk/db/installed": database,
            "bin/busybox": b"\x7fELFbusybox-fixture",
            "lib/libc.musl-aarch64.so.1": b"\x7fELFmusl-fixture",
            "usr/share/fonts/fixture.ttf": b"font-fixture",
            "usr/share/hyphen/fixture.dic": b"dictionary-fixture",
            "usr/share/doc/render-assets/copyright": b"License: MIT\nnotice fixture\n",
            "usr/local/bin/unowned": b"\x7fELFunowned-fixture",
        }
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "rootfs.tar"
            write_tar(archive, files)
            with tarfile.open(archive, "a") as rootfs:
                for path, target, kind in (
                    ("bin/sh", "/bin/busybox", tarfile.SYMTYPE),
                    ("usr/lib/libalias.so", "lib/libc.musl-aarch64.so.1", tarfile.LNKTYPE),
                    ("usr/local/lib/unowned.so", "/not-present/native.so", tarfile.SYMTYPE),
                ):
                    link = tarfile.TarInfo(path)
                    link.type, link.linkname = kind, target
                    rootfs.addfile(link)
            collected = inventory.Inventory()
            inventory.collect_image_tar(archive, "sha256:" + "b" * 64, collected)
            snapshot = collected.snapshot(False)
            packages = {
                record["name"]: record
                for record in collected.components.values()
                if "apk_metadata" in record
            }
            self.assertEqual(len(packages), 5)
            assets = packages["render-assets"]
            self.assertEqual(
                assets["declared_license_metadata"], "custom-license AND (MIT OR UnknownRef)"
            )
            for record in packages.values():
                metadata = inventory.object_value(record["apk_metadata"], "APK metadata")
                if record["name"] != ".python-rundeps":
                    self.assertEqual(metadata["o"], record["name"])
                    self.assertEqual(metadata["c"], "c3ef5d10e6ef6528852c51f0564963e2f8c1be19")
                self.assertTrue(str(metadata["C"]).startswith("Q1"))
                self.assertIn("C:Q1", str(record["apk_database_record"]))
                self.assertIn("not verified", str(record["apk_checksum_verification"]))
                self.assertIsNone(record["detected_license"])
                self.assertIsNone(record["concluded_license"])
                self.assertFalse(record["rights_confirmed"])
                self.assertEqual(
                    fixture_objects(record["evidence"])[0]["sha256"],
                    hashlib.sha256(database).hexdigest(),
                )
                for entry in fixture_objects(record["files"]):
                    path = inventory.text(entry["path"], "owned file path")
                    self.assertEqual(entry["sha256"], hashlib.sha256(files[path]).hexdigest())
            declarations = fixture_objects(assets["apk_file_declarations"])
            self.assertEqual(declarations[0]["Z"], "Q1pcfTfDNEbNKQc2s1tia7da05M8Q=")
            self.assertEqual(len(inventory.array_value(assets["files"], "owned files")), 3)
            self.assertEqual(assets["notices"], ["usr/share/doc/render-assets/copyright"])
            self.assertEqual(fixture_objects(assets["symlinks"])[0]["kind"], "hardlink")
            shell = packages["busybox-binsh"]
            self.assertEqual(shell["files"], [])
            self.assertEqual(fixture_objects(shell["symlinks"])[0]["target"], "/bin/busybox")
            virtual = packages[".python-rundeps"]
            self.assertEqual(virtual["files"], [])
            virtual_metadata = inventory.object_value(virtual["apk_metadata"], "virtual metadata")
            self.assertEqual(virtual_metadata["A"], "noarch")
            self.assertNotIn("o", virtual_metadata)
            self.assertNotIn("c", virtual_metadata)
            self.assertIsNone(virtual["upstream_url"])
            self.assertIn(
                "metadata-only", inventory.text(virtual["apk_file_ownership_status"], "status")
            )
            self.assertEqual(virtual["dependencies"], [packages["musl"]["id"]])
            self.assertTrue(any("unowned native-binary" in value for value in collected.blockers))
            self.assertTrue(
                any("unowned native-binary link" in value for value in collected.blockers)
            )
            self.assertEqual(collected.graph_errors(), [])
            self.assertFalse(compliance.check_inventory(snapshot)["release_eligible"])
            sbom = compliance.make_sbom(snapshot)
            sbom_packages = [item for item in sbom["components"] if item["name"] in packages]
            self.assertEqual(len(sbom_packages), 5)
            self.assertTrue(all("licenses" not in item for item in sbom_packages))

    def test_apk_candidates_do_not_satisfy_ambiguity_conflicts_or_unchecked_versions(self) -> None:
        database = (
            apk_package(
                "consumer",
                dependencies=(
                    "direct=2 direct=3 direct>=1 so:shared.so cmd:tool=2 "
                    "/usr/bin/tool /bin/missing !forbidden !absent !direct=3 !direct>=1 missing"
                ),
            )
            + apk_package("direct", version="2")
            + apk_package("provider-a", provides="so:shared.so=1 cmd:tool=2 /bin/missing")
            + apk_package("provider-b", provides="so:shared.so=1")
            + apk_package("forbidden")
            + apk_package("executable-owner", files=apk_file("usr/bin", "tool"))
        ).encode()
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "rootfs.tar"
            write_tar(archive, {"lib/apk/db/installed": database})
            with tarfile.open(archive, "a") as rootfs:
                executable = tarfile.TarInfo("usr/bin/tool")
                data = b"\x7fELFexecutable-fixture"
                executable.mode, executable.size = 0o755, len(data)
                rootfs.addfile(executable, io.BytesIO(data))
            collected = inventory.Inventory()
            inventory.collect_image_tar(archive, "fixture", collected)
            packages = {
                record["name"]: record
                for record in collected.components.values()
                if "apk_metadata" in record
            }
            consumer = packages["consumer"]
            self.assertEqual(
                set(inventory.array_value(consumer["dependencies"], "dependencies")),
                {
                    packages["direct"]["id"],
                    packages["provider-a"]["id"],
                    packages["executable-owner"]["id"],
                },
            )
            edges = {
                edge["declaration"]: edge
                for edge in fixture_objects(consumer["os_dependency_declarations"])
            }
            self.assertEqual(edges["direct=2"]["resolution"], "satisfied")
            self.assertEqual(edges["direct=3"]["resolution"], "unresolved")
            self.assertEqual(
                fixture_objects(edges["direct=3"]["installed_candidates"])[0]["constraint_status"],
                "version-mismatch",
            )
            self.assertEqual(edges["direct>=1"]["resolution"], "version-unchecked")
            self.assertEqual(edges["so:shared.so"]["resolution"], "ambiguous")
            self.assertEqual(len(fixture_objects(edges["so:shared.so"]["installed_candidates"])), 2)
            self.assertEqual(edges["cmd:tool=2"]["resolution"], "satisfied")
            self.assertEqual(edges["/usr/bin/tool"]["resolution"], "satisfied")
            self.assertEqual(edges["/bin/missing"]["resolution"], "unresolved")
            self.assertEqual(
                fixture_objects(edges["/bin/missing"]["installed_candidates"])[0][
                    "constraint_status"
                ],
                "path-unavailable",
            )
            self.assertEqual(edges["!forbidden"]["kind"], "conflict")
            self.assertEqual(edges["!forbidden"]["resolution"], "conflict-present-or-unchecked")
            self.assertEqual(edges["!absent"]["resolution"], "conflict-absent")
            self.assertEqual(edges["!direct=3"]["resolution"], "conflict-absent")
            self.assertEqual(edges["!direct>=1"]["resolution"], "conflict-present-or-unchecked")
            self.assertEqual(edges["missing"]["resolution"], "unresolved")
            self.assertNotIn(
                packages["forbidden"]["id"],
                inventory.array_value(consumer["dependencies"], "APK dependencies"),
            )
            for marker in ("ambiguous", "version-unchecked", "conflict-present", "missing"):
                self.assertTrue(any(marker in blocker for blocker in collected.blockers))
            self.assertEqual(collected.graph_errors(), [])

    def test_apk_missing_owned_bytes_are_not_an_empty_success(self) -> None:
        database = apk_package("missing-files", files=apk_file("usr/lib", "missing.so")).encode()
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "rootfs.tar"
            write_tar(archive, {"lib/apk/db/installed": database})
            collected = inventory.Inventory()
            inventory.collect_image_tar(archive, "fixture", collected)
            record = next(
                record for record in collected.components.values() if "apk_metadata" in record
            )
            self.assertEqual(record["files"], [])
            self.assertNotIn("apk_file_ownership_status", record)
            self.assertEqual(
                fixture_objects(record["apk_file_declarations"])[0]["path"], "usr/lib/missing.so"
            )
            self.assertTrue(
                any("declared file missing" in blocker for blocker in collected.blockers)
            )
            self.assertFalse(
                compliance.check_inventory(collected.snapshot(False))["release_eligible"]
            )

    def test_apk_malformed_unsafe_duplicate_and_binary_databases_fail_closed(self) -> None:
        valid = apk_package("fixture", files=apk_file("usr/lib", "fixture.so"))
        malformed = {
            "binary": b"ADB\x00\xffuntrusted",
            "non-utf8": b"\xff",
            "empty": b"",
            "unknown-field": valid.replace("T:", "X:").encode(),
            "duplicate-metadata": valid.replace("P:fixture", "P:fixture\nP:other").encode(),
            "duplicate-license": valid.replace("L:GPL-2.0-only", "L:MIT\nL:Apache-2.0").encode(),
            "missing-version": valid.replace("V:1.37.0-r31\n", "").encode(),
            "missing-checksum": valid.replace("Z:Q1pcfTfDNEbNKQc2s1tia7da05M8Q=\n", "").encode(),
            "bad-checksum": valid.replace("Z:Q1", "Z:Q9").encode(),
            "truncated-checksum": valid.replace(
                "Q1pcfTfDNEbNKQc2s1tia7da05M8Q=", "Q1AAAA"
            ).encode(),
            "bad-commit": valid.replace(
                "c:c3ef5d10e6ef6528852c51f0564963e2f8c1be19", "c:abc"
            ).encode(),
            "parent-directory": valid.replace("F:usr/lib", "F:usr/../lib").encode(),
            "absolute-directory": valid.replace("F:usr/lib", "F:/usr/lib").encode(),
            "empty-path-segment": valid.replace("F:usr/lib", "F:usr//lib").encode(),
            "parent-file": valid.replace("R:fixture.so", "R:..").encode(),
            "nested-file": valid.replace("R:fixture.so", "R:sub/fixture.so").encode(),
            "backslash": valid.replace("R:fixture.so", "R:evil\\name").encode(),
            "missing-directory": valid.replace("F:usr/lib\n", "").encode(),
            "duplicate-file": (
                valid.rstrip("\n") + "\nR:fixture.so\nZ:Q1pcfTfDNEbNKQc2s1tia7da05M8Q=\n\n"
            ).encode(),
            "duplicate-package": (valid + valid).encode(),
            "duplicate-owner": (valid + valid.replace("P:fixture", "P:other")).encode(),
            "bad-provider": valid.replace("p:\n", "p:so:fixture.so>=1\n").encode(),
            "bad-dependency": valid.replace("D:\n", "D:fixture==1\n").encode(),
        }
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "rootfs.tar"
            for label, database in malformed.items():
                with self.subTest(label=label):
                    write_tar(archive, {"lib/apk/db/installed": database})
                    with self.assertRaises(inventory.CollectionError):
                        inventory.collect_image_tar(archive, "fixture", inventory.Inventory())
            write_tar(
                archive,
                {"lib/apk/db/installed": valid.encode(), "var/lib/dpkg/status": b""},
            )
            with self.assertRaisesRegex(inventory.CollectionError, "both APK and dpkg"):
                inventory.collect_image_tar(archive, "fixture", inventory.Inventory())
            write_tar(archive, {})
            with tarfile.open(archive, "a") as rootfs:
                link = tarfile.TarInfo("lib/apk/db/installed")
                link.type, link.linkname = tarfile.SYMTYPE, "/outside/database"
                rootfs.addfile(link)
            with self.assertRaisesRegex(inventory.CollectionError, "missing regular file"):
                inventory.collect_image_tar(archive, "fixture", inventory.Inventory())

    def test_unknown_distro_and_unsafe_tar_are_not_empty_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "rootfs.tar"
            write_tar(archive, {"etc/os-release": b"ID=unknown\n"})
            with self.assertRaisesRegex(inventory.CollectionError, "dpkg"):
                inventory.collect_image_tar(archive, "sha256:" + "b" * 64, inventory.Inventory())
            write_tar(archive, {"../escape": b"must not extract"})
            with self.assertRaisesRegex(inventory.CollectionError, "unsafe path"):
                inventory.collect_image_tar(archive, "sha256:" + "b" * 64, inventory.Inventory())
            self.assertFalse((Path(directory).parent / "escape").exists())


class InventoryCliTests(unittest.TestCase):
    def test_absent_inputs_return_nonzero_actionable_errors_and_explicit_image_exclusion(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "inventory.json"
            with patch("sys.stderr", new_callable=io.StringIO) as stderr:
                result = inventory.main(
                    [
                        "--uv-lock",
                        str(root / "uv.lock"),
                        "--python-root",
                        str(root / ".venv"),
                        "--web-root",
                        str(root / "web"),
                        "--source-only",
                        "--output",
                        str(output),
                    ]
                )
            self.assertEqual(result, 2)
            self.assertIn("uv.lock", stderr.getvalue())
            self.assertIn("pnpm-lock.yaml", stderr.getvalue())
            report = inventory.object_value(json.loads(output.read_text()), "report")
            self.assertTrue(report["source_only"])
            self.assertIn("image EXCLUDED", str(report["coverage"]))
            self.assertFalse(report["release_eligible"])
            self.assertTrue(report["collection_errors"])

    def test_dangling_graph_is_detected_not_silently_dropped(self) -> None:
        collected = inventory.Inventory()
        record = inventory.component("pkg:generic/parent@1", "parent", "1", "fixture")
        record["dependencies"] = ["pkg:generic/missing@1"]
        collected.add(record)
        self.assertEqual(len(collected.graph_errors()), 1)
        with self.assertRaisesRegex(ValueError, "missing component"):
            compliance.make_sbom(collected.snapshot(True))
        with self.assertRaisesRegex(inventory.CollectionError, "duplicate"):
            collected.add(record)

    def test_mode_requires_image_or_explicit_source_only(self) -> None:
        with patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit) as result:
            inventory.main(
                [
                    "--uv-lock",
                    "uv.lock",
                    "--python-root",
                    ".venv",
                    "--web-root",
                    "web",
                    "--output",
                    "unused",
                ]
            )
        self.assertEqual(result.exception.code, 2)

    def test_asvs_preserves_declared_license_and_distinguishes_source_and_adaptation_hash(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "compliance").mkdir()
            (root / "docs/compliance").mkdir(parents=True)
            metadata: inventory.Object = {
                "version": "5.0.0",
                "source_sha256": "c" * 64,
                "license": "CC-BY-SA-4.0",
                "attribution": "OWASP Foundation and contributors",
                "adaptation": "Additional evidence columns",
                "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
                "source_url": "https://example.invalid/official.csv",
            }
            (root / "compliance/asvs-source.json").write_text(json.dumps(metadata))
            register = root / "compliance/asvs-5.0.0.csv"
            register.write_text("req_id,adaptation\nV1.1.1,pending\n")
            (root / "docs/compliance/asvs-register.md").write_text("Synthetic register notice")
            collected = inventory.Inventory()
            inventory.collect_asvs(root, collected)
            record = next(
                value
                for value in collected.components.values()
                if "register adaptation" in str(value["name"])
            )
            self.assertEqual(record["detected_license"], "CC-BY-SA-4.0")
            self.assertEqual(record["sha256"], hashlib.sha256(register.read_bytes()).hexdigest())
            self.assertNotEqual(record["sha256"], record["upstream_source_sha256"])
            self.assertFalse(record["rights_confirmed"])
            self.assertTrue(any("ShareAlike" in value for value in collected.blockers))
            self.assertFalse(
                compliance.check_inventory(collected.snapshot(True))["release_eligible"]
            )

    def test_build_image_pins_have_separate_index_platform_identity_and_no_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "deploy").mkdir()
            declaration = {
                "reference": "example.invalid/tool:1@sha256:" + "a" * 64,
                "amd64": "sha256:" + "b" * 64,
                "arm64": "sha256:" + "c" * 64,
            }
            path = root / "deploy/images.json"
            path.write_text(json.dumps({"images": {"tool": declaration}}))
            collected = inventory.Inventory()
            inventory.collect_build_images(root, collected)
            self.assertEqual(len(collected.components), 3)
            index = next(
                value
                for value in collected.components.values()
                if value["artifact_kind"] == "declared-oci-index"
            )
            self.assertEqual(index["sha256"], "a" * 64)
            self.assertFalse(index["sha256_verified"])
            self.assertEqual(len(inventory.array_value(index["dependencies"], "index edges")), 2)
            self.assertIsNone(index["detected_license"])
            self.assertIsNone(index["concluded_license"])
            self.assertEqual(collected.graph_errors(), [])
            self.assertTrue(any("does not inventory" in value for value in collected.blockers))
            declaration["amd64"] = "sha256:" + "b" * 65
            path.write_text(json.dumps({"images": {"tool": declaration}}))
            with self.assertRaisesRegex(inventory.CollectionError, "exact sha256"):
                inventory.collect_build_images(root, inventory.Inventory())


if __name__ == "__main__":
    unittest.main()
