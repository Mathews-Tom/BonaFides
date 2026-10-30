# SPDX-License-Identifier: MIT OR Apache-2.0
"""Collect artifact evidence, never license approval; see artifact-inventory.md."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import importlib.metadata
import json
import re
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import cast
from urllib.parse import quote

type Json = None | bool | int | float | str | list[Json] | dict[str, Json]
type Object = dict[str, Json]


class CollectionError(ValueError):
    """A required input is absent, inconsistent, or unsupported."""


def object_value(value: object, context: str) -> Object:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise CollectionError(f"{context}: expected an object")
    return cast(Object, value)


def array_value(value: Json, context: str) -> list[Json]:
    if not isinstance(value, list):
        raise CollectionError(f"{context}: expected an array")
    return value


def text(value: Json, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CollectionError(f"{context}: expected a nonempty string")
    return value


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def exact_sha256(value: Json, context: str) -> str:
    digest = text(value, context)
    if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
        raise CollectionError(f"{context}: requires exact sha256:<64 hexadecimal digits>")
    return digest[7:].lower()


def normalized_integrity(value: str) -> list[Object]:
    """Decode SRI without relabeling SHA512 as SHA256 or accepting truncated hashes."""
    hashes: list[Object] = []
    for token in value.split():
        match = re.fullmatch(r"(sha256|sha384|sha512)-([A-Za-z0-9+/]+={0,2})", token)
        if not match:
            raise CollectionError("pnpm integrity: unsupported or malformed SRI")
        algorithm, encoded = match.groups()
        try:
            digest = base64.b64decode(encoded, validate=True)
        except binascii.Error as exc:
            raise CollectionError("pnpm integrity: invalid base64") from exc
        if len(digest) != {"sha256": 32, "sha384": 48, "sha512": 64}[algorithm]:
            raise CollectionError("pnpm integrity: incorrect digest byte length")
        hashes.append({"algorithm": algorithm, "hex": digest.hex(), "sri": token})
    if not hashes:
        raise CollectionError("pnpm integrity: missing SRI")
    return hashes


def canonical_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def purl(ecosystem: str, name: str, version: str) -> str:
    encoded = quote(name, safe="/")
    return f"pkg:{ecosystem}/{encoded}@{quote(version, safe='')}"


def evidence(url: str, digest: str, kind: str) -> Object:
    return {
        "url": url,
        "sha256": digest,
        "kind": kind,
        "date": datetime.now(UTC).date().isoformat(),
    }


def component(identity: str, name: str, version: str, scope: str) -> Object:
    return {
        "id": identity,
        "name": name,
        "purl": identity,
        "version": version,
        "sha256": None,
        "upstream_url": None,
        "license_url": None,
        "detected_license": None,
        "concluded_license": None,
        "selected_license": None,
        "copyright": None,
        "notices": [],
        "scope": scope,
        "dependencies": [],
        "modifications": None,
        "obligations": [],
        "evidence": [],
        "reviewer": None,
        "review_date": None,
        "status": "pending",
        "rights_confirmed": False,
        "distribution_status": "blocked",
        "digest_basis": None,
    }


def append_value(record: Object, key: str, value: Json) -> None:
    array_value(record[key], key).append(value)


def manifest_digest(files: list[Object]) -> str:
    """Hash a canonical file manifest, NOT an archive or concatenation of file bytes."""
    ordered = sorted(files, key=lambda entry: text(entry.get("path"), "manifest path"))
    return sha256(json.dumps(ordered, sort_keys=True, separators=(",", ":")).encode())


def asset_kind(path: str, prefix: bytes = b"") -> str | None:
    lower = path.lower()
    if lower.endswith((".ttf", ".otf", ".woff", ".woff2", ".ttc", ".pfa", ".pfb")):
        return "font"
    if lower.endswith((".dic", ".aff", ".dict", ".bdic", ".hyph")) or any(
        part in lower for part in ("/hyph_", "/hyphen/", "/dictionaries/")
    ):
        return "dictionary"
    if (
        re.search(r"\.(?:so(?:\.[0-9.]+)?|dylib|dll|node|exe|a)$", lower)
        or prefix.startswith((b"\x7fELF", b"MZ", b"!<arch>\n"))
        or prefix[:4] in (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe")
    ):
        return "native-binary"
    return None


def is_notice(path: str) -> bool:
    name = PurePosixPath(path).name.lower()
    return name.startswith(("license", "licence", "copying", "copyright", "notice"))


@dataclass
class Inventory:
    components: dict[str, Object] = field(default_factory=dict)
    inputs: list[Object] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def add(self, record: Object) -> None:
        identity = text(record.get("id"), "component identity")
        if identity in self.components:
            raise CollectionError(f"duplicate component identity: {identity}")
        self.components[identity] = record

    def input(self, path: Path, kind: str) -> None:
        if not path.is_file():
            raise CollectionError(f"missing {kind}: {path}")
        self.inputs.append({"path": str(path), "kind": kind, "sha256": file_sha256(path)})

    def graph_errors(self) -> list[str]:
        errors: list[str] = []
        for identity, record in self.components.items():
            for edge in array_value(record.get("dependencies"), f"{identity} dependencies"):
                if not isinstance(edge, str) or edge not in self.components:
                    errors.append(f"{identity}: unresolved dependency {edge}")
        return errors

    def snapshot(self, source_only: bool) -> Object:
        return {
            "schema_version": 1,
            "generator": "bonafides-artifact-inventory/1",
            "recorded_date": datetime.now(UTC).date().isoformat(),
            "distribution_status": "blocked",
            "release_eligible": False,
            "coverage": "source locks and installed development artifacts; image EXCLUDED"
            if source_only
            else "source locks, installed development artifacts and exported image filesystem",
            "source_only": source_only,
            "artifacts_inspected": cast(list[Json], self.inputs),
            "blockers": cast(
                list[Json],
                [
                    "Metadata detection is not rights approval; independent review, notices, modifications, "
                    "license conclusions and source/relink obligations remain unresolved.",
                    *self.blockers,
                    *self.errors,
                ],
            ),
            "collection_errors": cast(list[Json], self.errors),
            "components": cast(list[Json], list(self.components.values())),
        }

    def asset(self, owner: Object, path: str, digest: str, kind: str, url: str) -> None:
        owner_id = text(owner.get("id"), "asset owner")
        identity = f"pkg:generic/{quote(path, safe='')}@{digest}?owner={quote(owner_id, safe='')}"
        asset = component(identity, path, digest, f"bundled {kind}")
        asset.update(
            {
                "sha256": digest,
                "digest_basis": "file-bytes",
                "artifact_kind": kind,
                "owner": owner_id,
                "evidence": [evidence(url, digest, "file-bytes")],
                "metadata_evidence": owner.get("evidence", []),
            }
        )
        self.add(asset)
        append_value(owner, "dependencies", identity)
        self.blockers.append(
            f"{identity}: {kind} license/embedded-component rights not inferred from owner"
        )


def run_json(command: list[str], cwd: Path | None = None) -> Json:
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise CollectionError(f"cannot invoke {command[0]}: {exc}") from exc
    if result.returncode:
        raise CollectionError(f"{' '.join(command)} failed: {result.stderr.strip()[:1000]}")
    try:
        return cast(Json, json.loads(result.stdout))
    except json.JSONDecodeError as exc:
        raise CollectionError(f"{command[0]} emitted invalid JSON") from exc


def installed_files(
    root: Path, paths: Iterable[tuple[Path, str]], owner: Object, inventory: Inventory
) -> list[Object]:
    files: list[Object] = []
    boundary = root.resolve()
    for path, relative in sorted(paths, key=lambda pair: pair[1]):
        resolved = path.resolve()
        if not resolved.is_relative_to(boundary):
            inventory.blockers.append(f"{owner['id']}: external file excluded: {relative}")
            continue
        if not path.is_file():
            inventory.blockers.append(f"{owner['id']}: listed file missing: {relative}")
            continue
        digest = file_sha256(path)
        entry: Object = {"path": relative, "sha256": digest, "size": path.stat().st_size}
        if path.is_symlink():
            entry["symlink_target"] = path.readlink().as_posix()
        files.append(entry)
        if is_notice(relative):
            append_value(owner, "notices", relative)
            notice = evidence(path.absolute().as_uri(), digest, "license-or-notice-file")
            notice["text"] = path.read_text(errors="replace")
            append_value(owner, "evidence", notice)
            if owner.get("license_url") is None:
                owner["license_url"] = path.absolute().as_uri()
        with path.open("rb") as stream:
            kind = asset_kind(relative, stream.read(8))
        if kind:
            inventory.asset(owner, relative, digest, kind, path.absolute().as_uri())
    return files


def uv_packages(lock: Path, inventory: Inventory) -> dict[tuple[str, str], Object]:
    inventory.input(lock, "uv lock")
    document = object_value(tomllib.loads(lock.read_text()), str(lock))
    if document.get("version") != 1:
        raise CollectionError(f"{lock}: unsupported uv lock version")
    packages = array_value(document.get("package"), "uv package list")
    if not packages:
        raise CollectionError(f"{lock}: empty package graph")
    index: dict[tuple[str, str], Object] = {}
    raw_index: dict[tuple[str, str], Object] = {}
    for raw in packages:
        package = object_value(raw, "uv package")
        name = canonical_name(text(package.get("name"), "uv name"))
        version = text(package.get("version"), f"{name} version")
        key = (name, version)
        if key in index:
            raise CollectionError(f"{lock}: ambiguous duplicate name/version {key}")
        record = component(purl("pypi", name, version), name, version, "locked Python package")
        record["source"] = package.get("source")
        record["lock_evidence"] = evidence(lock.absolute().as_uri(), file_sha256(lock), "uv-lock")
        source = object_value(package.get("source"), f"{name} source")
        record["upstream_url"] = source.get("registry") or source.get("git")
        record["artifact_kind"] = "locked-package-not-an-archive"
        inventory.add(record)
        index[key], raw_index[key] = record, package
        artifacts = array_value(package.get("wheels", []), f"{name} wheels").copy()
        if package.get("sdist"):
            artifacts.append(package["sdist"])
        if not artifacts and not ("editable" in source or "virtual" in source):
            inventory.blockers.append(f"{record['id']}: no SHA256-locked distribution artifact")
        for raw_artifact in artifacts:
            artifact = object_value(raw_artifact, f"{name} distribution")
            digest = exact_sha256(artifact.get("hash"), f"{name} distribution hash")
            url = text(artifact.get("url"), f"{name} distribution URL")
            identity = f"{record['id']}?artifact={digest}"
            child = component(
                identity, PurePosixPath(url).name, version, "locked distribution candidate"
            )
            child.update(
                {
                    "sha256": digest,
                    "upstream_url": url,
                    "digest_basis": "lock-declared-not-downloaded",
                    "sha256_verified": False,
                    "artifact_kind": "wheel" if url.endswith(".whl") else "sdist",
                    "evidence": [
                        evidence(lock.absolute().as_uri(), file_sha256(lock), "lock-declaration")
                    ],
                    "download": artifact,
                }
            )
            inventory.add(child)
            append_value(record, "dependencies", identity)
    for key, record in index.items():
        package = raw_index[key]
        edge_groups = [array_value(package.get("dependencies", []), "uv dependencies")]
        for group_key in ("optional-dependencies", "dev-dependencies"):
            groups = object_value(package.get(group_key, {}), group_key)
            edge_groups.extend(array_value(value, group_key) for value in groups.values())
        lock_edges: list[Json] = []
        for edges in edge_groups:
            for raw_edge in edges:
                edge = object_value(raw_edge, "uv dependency")
                name = canonical_name(text(edge.get("name"), "uv dependency name"))
                candidates = [
                    target
                    for (target_name, target_version), target in index.items()
                    if target_name == name
                    and (not edge.get("version") or target_version == edge["version"])
                ]
                if not candidates:
                    raise CollectionError(f"{record['id']}: uv dependency absent from lock: {name}")
                # Marker alternatives stay explicit; this is the all-platform lock graph.
                for target in candidates:
                    if target["id"] not in array_value(record["dependencies"], "dependencies"):
                        append_value(record, "dependencies", target["id"])
                lock_edges.append(edge)
        record["lock_dependency_declarations"] = lock_edges
    return index


def collect_python(lock: Path, root: Path, inventory: Inventory) -> None:
    packages = uv_packages(lock, inventory)
    config = root / "pyvenv.cfg"
    inventory.input(config, "installed Python environment (run uv sync --frozen)")
    sites = sorted(root.glob("lib/python*/site-packages")) + sorted(root.glob("Lib/site-packages"))
    if not sites:
        raise CollectionError(f"{root}: no installed site-packages")
    distributions = list(importlib.metadata.distributions(path=[str(site) for site in sites]))
    if not distributions:
        raise CollectionError(f"{root}: no installed distribution metadata")
    installed: set[tuple[str, str]] = set()
    for distribution in distributions:
        raw_name = distribution.metadata.get("Name")
        if not raw_name:
            raise CollectionError(f"{root}: installed metadata missing Name")
        name = canonical_name(raw_name)
        key = (name, distribution.version)
        record = packages.get(key)
        if record is None:
            record = component(
                purl("pypi", name, distribution.version),
                name,
                distribution.version,
                "installed Python package absent from uv lock",
            )
            inventory.add(record)
            inventory.blockers.append(f"{record['id']}: installed package absent from uv lock")
        installed.add(key)
        record["scope"] = "installed Python environment (development; not asserted shipped)"
        record["artifact_kind"] = "installed-file-manifest"
        record["detected_license"] = distribution.metadata.get("License-Expression")
        record["declared_license_text"] = distribution.metadata.get("License")
        record["declared_license_classifiers"] = cast(
            list[Json],
            [
                value
                for value in distribution.metadata.get_all("Classifier", [])
                if value.startswith("License ::")
            ],
        )
        record["declared_project_urls"] = cast(
            list[Json], distribution.metadata.get_all("Project-URL", [])
        )
        paths = distribution.files
        if paths is None:
            raise CollectionError(f"{name}: installed RECORD/file list missing")
        file_pairs: list[tuple[Path, str]] = []
        for package_path in paths:
            located_path = distribution.locate_file(package_path)
            if not isinstance(located_path, Path):
                raise CollectionError(
                    f"{name}: installed file is not a concrete filesystem path: {package_path}"
                )
            file_pairs.append((located_path, str(package_path)))
        files = installed_files(root, file_pairs, record, inventory)
        metadata_file = next(
            (
                path
                for path, _ in file_pairs
                if path.name == "METADATA" and path.resolve().is_relative_to(root.resolve())
            ),
            None,
        )
        if metadata_file is None:
            raise CollectionError(f"{name}: METADATA file missing")
        append_value(
            record,
            "evidence",
            evidence(
                metadata_file.absolute().as_uri(),
                file_sha256(metadata_file),
                "installed-core-metadata",
            ),
        )
        record["files"] = cast(list[Json], files)
        record["sha256"] = manifest_digest(files)
        record["digest_basis"] = "canonical-installed-file-manifest-v1"
        record["installed_requires_dist"] = cast(list[Json], distribution.requires or [])
        # RECORD hashes verify installed content where the installer retained one.
        for package_path, (path, _) in zip(paths, file_pairs, strict=True):
            declared = package_path.hash
            if (
                declared
                and declared.mode == "sha256"
                and path.is_file()
                and path.resolve().is_relative_to(root.resolve())
            ):
                encoded = declared.value.replace("-", "+").replace("_", "/")
                expected = normalized_integrity(f"sha256-{encoded}{'=' * (-len(encoded) % 4)}")
                if expected[0]["hex"] != file_sha256(path):
                    inventory.blockers.append(
                        f"{name}: installed RECORD SHA256 mismatch: {package_path}"
                    )
    for key, record in packages.items():
        if key not in installed:
            record["installation_status"] = "not installed on this platform/environment"
    inventory.blockers.append(
        "uv artifact SHA256 values are lock declarations, not proof of the selected downloaded wheel/sdist; "
        "installed files are hashed separately and do not establish reproducible archive equivalence"
    )
    collect_build_requirements(lock.parent / "pyproject.toml", packages, inventory)


def collect_build_requirements(
    project: Path, packages: dict[tuple[str, str], Object], inventory: Inventory
) -> None:
    inventory.input(project, "PEP517 build requirements")
    document = object_value(tomllib.loads(project.read_text()), str(project))
    build = object_value(document.get("build-system"), "build-system")
    requirements = array_value(build.get("requires"), "build-system.requires")
    inventory.inputs.append(
        {
            "kind": "PEP517 build-system",
            "backend": build.get("build-backend"),
            "requires": requirements,
        }
    )
    for requirement in requirements:
        raw = text(requirement, "PEP517 requirement")
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s;]+)", raw)
        if match is None:
            inventory.blockers.append(f"PEP517 build requirement not exactly pinned: {raw}")
            continue
        name, version = canonical_name(match[1]), match[2]
        record = packages.get((name, version))
        if record is None:
            identity = purl("pypi", name, version) + "?scope=pep517"
            record = component(
                identity, name, version, "PEP517 source-build tool; separate lock required"
            )
            inventory.add(record)
            inventory.blockers.append(
                f"{raw}: PEP517 backend/tool artifact and transitive build graph not covered by uv.lock; "
                "supply a separately hash-locked build environment and review its artifacts"
            )
        else:
            record["build_tool_requirement"] = raw
    inventory.blockers.append(
        "Source builds may add dynamic PEP517 requirements: source archive build-system declarations, "
        "get_requires_for_build_wheel outputs, compilers and transitive build tool hashes require separate evidence"
    )


def pnpm_integrities(content: str) -> dict[str, list[Object]]:
    """Read only registry integrity declarations in canonical pnpm v9 YAML.

    Deliberately not a permissive YAML parser: unsupported resolution shapes fail
    closed. Dependency edges come from pnpm's installed JSON graph, not this reader.
    """
    if not re.search(r"(?m)^lockfileVersion: ['\"]?9\.0['\"]?\s*$", content):
        raise CollectionError("pnpm-lock.yaml: only canonical pnpm lockfileVersion 9.0 supported")
    active = False
    current: str | None = None
    declarations: dict[str, str | None] = {}
    for line in content.splitlines():
        if line == "packages:":
            active = True
            continue
        if active and line and not line.startswith(" "):
            break
        if not active or not line.strip():
            continue
        match = re.fullmatch(r"  (\S.*):", line)
        if match:
            key = match[1]
            if key[:1] in ("'", '"'):
                if key[-1:] != key[:1] or "\\" in key or "''" in key[1:-1]:
                    raise CollectionError("pnpm package key uses unsupported YAML escaping")
                key = key[1:-1]
            if key in declarations:
                raise CollectionError(f"pnpm duplicate package declaration: {key}")
            current = key
            declarations[key] = None
            continue
        if current and (line.startswith("    resolution:") or line.startswith("      integrity:")):
            match = re.fullmatch(r"    resolution: \{integrity: ([^{}]+)\}", line)
            if match is None:
                match = re.fullmatch(r"      integrity: (.+)", line)
            if line == "    resolution:":
                continue
            if match is None:
                raise CollectionError(
                    f"{current}: unsupported pnpm resolution (registry integrity required)"
                )
            value = match[1].strip().strip("'\"")
            if declarations[current] is not None:
                raise CollectionError(f"{current}: duplicate pnpm integrity")
            declarations[current] = value
    if not declarations:
        raise CollectionError("pnpm-lock.yaml: no package integrity declarations")
    result: dict[str, list[Object]] = {}
    for key, value in declarations.items():
        if value is None:
            raise CollectionError(f"{key}: missing pnpm registry integrity")
        result[key] = normalized_integrity(value)
    return result


def package_files(directory: Path) -> Iterable[tuple[Path, str]]:
    # Do not absorb another package through node_modules or follow directory links.
    for path in sorted(directory.rglob("*")):
        relative = path.relative_to(directory)
        if "node_modules" in relative.parts or any(
            parent.is_symlink()
            for parent in path.parents
            if parent != directory and parent.is_relative_to(directory)
        ):
            continue
        if path.is_file():
            yield path, relative.as_posix()


def collect_web(root: Path, inventory: Inventory) -> None:
    lock = root / "pnpm-lock.yaml"
    inventory.input(lock, "pnpm lock (run pnpm install --frozen-lockfile)")
    integrities = pnpm_integrities(lock.read_text())
    locked: dict[str, Object] = {}
    for key, hashes in integrities.items():
        name, separator, version = key.rpartition("@")
        if not separator or not name or not version:
            raise CollectionError(f"pnpm unsupported package key: {key}")
        identity = purl("npm", name, version) + "?scope=registry-lock"
        record = component(
            identity, name, version, "locked frontend registry distribution candidate"
        )
        record.update(
            {
                "registry_integrity": cast(list[Json], hashes),
                "registry_integrity_verified": False,
                "installation_status": "not observed in installed graph",
                "artifact_kind": "registry-lock-not-an-installed-artifact",
                "evidence": [
                    evidence(lock.absolute().as_uri(), file_sha256(lock), "pnpm-lock-declaration")
                ],
            }
        )
        inventory.add(record)
        locked[key] = record
    inventory.input(root / "package.json", "web package manifest")
    if not (root / "node_modules/.pnpm").is_dir():
        raise CollectionError(
            f"{root}/node_modules/.pnpm: installed pnpm graph missing; run pnpm install --frozen-lockfile"
        )
    graph = array_value(
        run_json(["pnpm", "list", "--json", "--depth", "Infinity"], root), "pnpm graph"
    )
    if len(graph) != 1:
        raise CollectionError(
            "pnpm graph: expected exactly one workspace root; inventory each workspace separately"
        )
    root_node = object_value(graph[0], "pnpm workspace")
    root_manifest = object_value(
        json.loads((root / "package.json").read_text()), "web package manifest"
    )
    root_children = {
        alias
        for field_name in ("dependencies", "devDependencies", "optionalDependencies")
        for alias in object_value(root_node.get(field_name, {}), "workspace graph dependencies")
    }
    root_required = set(
        object_value(root_manifest.get("dependencies", {}), "workspace dependencies")
    )
    root_required.update(
        object_value(root_manifest.get("devDependencies", {}), "workspace devDependencies")
    )
    root_optional = set(
        object_value(
            root_manifest.get("optionalDependencies", {}), "workspace optionalDependencies"
        )
    )
    missing_root = root_required - root_optional - root_children
    if missing_root:
        raise CollectionError(
            f"pnpm workspace: incomplete installed dependency graph: {', '.join(sorted(missing_root))}"
        )
    root_id = purl(
        "npm",
        text(root_manifest.get("name"), "web name"),
        text(root_manifest.get("version"), "web version"),
    )
    root_record = component(
        root_id + "?scope=workspace",
        text(root_manifest["name"], "web name"),
        text(root_manifest["version"], "web version"),
        "frontend source/build workspace",
    )
    inventory.add(root_record)
    expanded_nodes: dict[Path, Object] = {}

    def index_nodes(node: Object) -> None:
        for field_name in ("dependencies", "devDependencies", "optionalDependencies"):
            for child_alias, value in object_value(
                node.get(field_name, {}), f"pnpm {field_name}"
            ).items():
                child = object_value(value, f"pnpm {child_alias}")
                directory = Path(
                    text(child.get("path"), f"pnpm {child_alias} installed path")
                ).resolve()
                if child.get("deduped") is not True:
                    expanded_nodes.setdefault(directory, child)
                index_nodes(child)

    index_nodes(root_node)
    seen: dict[Path, Object] = {}

    def visit(node: Object, alias: str, optional: bool = False) -> Object:
        directory = Path(text(node.get("path"), f"pnpm {alias} installed path")).resolve()
        if directory in seen:
            return seen[directory]
        if not directory.is_relative_to(root.resolve() / "node_modules"):
            raise CollectionError(
                f"pnpm {alias}: linked/external package outside installed root: {directory}"
            )
        if node.get("deduped") is True:
            expanded = expanded_nodes.get(directory)
            if expanded is None:
                raise CollectionError(
                    f"pnpm {alias}: deduplicated dependency has no expanded graph occurrence"
                )
            if node.get("version") != expanded.get("version"):
                raise CollectionError(f"pnpm {alias}: deduplicated graph version mismatch")
            node = expanded
        manifest_path = directory / "package.json"
        if not manifest_path.is_file():
            # pnpm list includes uninstalled platform optionals as ordinary dependency nodes.
            if optional and not directory.exists():
                key = (
                    f"{text(node.get('from', alias), 'pnpm optional package name')}"
                    f"@{text(node.get('version'), 'pnpm optional package version')}"
                )
                if key not in locked:
                    raise CollectionError(
                        f"pnpm {key}: absent optional package has no registry integrity lock"
                    )
                record = locked[key]
                references = array_value(
                    record.setdefault("absent_optional_graph_paths", []), "optional graph paths"
                )
                instance = directory.relative_to(root.resolve()).as_posix()
                if instance not in references:
                    references.append(instance)
                    inventory.blockers.append(
                        f"{record['id']}: optional graph reference absent from installed filesystem: "
                        f"{instance}; retained as a pending lock candidate, not an installed artifact"
                    )
                return record
            raise CollectionError(f"pnpm {alias}: missing installed package.json: {manifest_path}")
        manifest = object_value(json.loads(manifest_path.read_text()), str(manifest_path))
        name = text(manifest.get("name"), "npm name")
        version = text(manifest.get("version"), "npm version")
        if node.get("version") != version:
            raise CollectionError(f"pnpm {name}: graph/installed package version mismatch")
        key = f"{name}@{version}"
        if key not in integrities:
            raise CollectionError(
                f"pnpm {key}: installed package absent from registry integrity lock"
            )
        locked[key]["installation_status"] = "installed instance observed"
        instance = directory.relative_to(root.resolve()).as_posix()
        identity = purl("npm", name, version) + "?instance=" + sha256(instance.encode())[:16]
        record = component(
            identity, name, version, "installed frontend development/build dependency"
        )
        record.update(
            {
                "artifact_kind": "installed-file-manifest",
                "installed_path": instance,
                "registry_integrity": cast(list[Json], integrities[key]),
                "registry_integrity_verified": False,
                "detected_license": manifest.get("license")
                if isinstance(manifest.get("license"), str)
                else None,
                "declared_license_metadata": manifest.get("license"),
                "upstream_url": manifest.get("homepage"),
                "evidence": [
                    evidence(
                        manifest_path.as_uri(), file_sha256(manifest_path), "installed-package-json"
                    )
                ],
            }
        )
        record["dependencies"] = [locked[key]["id"]]
        inventory.add(record)
        seen[directory] = record
        files = installed_files(directory, package_files(directory), record, inventory)
        record["files"] = cast(list[Json], files)
        record["sha256"] = manifest_digest(files)
        record["digest_basis"] = "canonical-installed-file-manifest-v1"
        declared_dependencies = object_value(
            manifest.get("dependencies", {}), f"{name} declared dependencies"
        )
        declared_optional = object_value(
            manifest.get("optionalDependencies", {}), f"{name} optional dependencies"
        )
        graph_children = {
            alias
            for field_name in ("dependencies", "devDependencies", "optionalDependencies")
            for alias in object_value(node.get(field_name, {}), f"{name} graph dependencies")
        }
        missing = set(declared_dependencies) - set(declared_optional) - graph_children
        if missing:
            raise CollectionError(
                f"pnpm {key}: incomplete installed dependency graph: {', '.join(sorted(missing))}"
            )
        for field_name in ("dependencies", "devDependencies", "optionalDependencies"):
            children = object_value(node.get(field_name, {}), f"pnpm {field_name}")
            for child_alias, child in children.items():
                target = visit(
                    object_value(child, f"pnpm {child_alias}"),
                    child_alias,
                    child_alias in declared_optional,
                )
                if target["id"] not in array_value(record["dependencies"], "dependencies"):
                    append_value(record, "dependencies", target["id"])
        return record

    for field_name in ("dependencies", "devDependencies", "optionalDependencies"):
        for alias, child in object_value(root_node.get(field_name, {}), field_name).items():
            target = visit(object_value(child, alias), alias, alias in root_optional)
            if target["id"] not in array_value(root_record["dependencies"], "dependencies"):
                append_value(root_record, "dependencies", target["id"])
    if not seen:
        raise CollectionError("pnpm: empty installed dependency graph")
    inventory.blockers.append(
        "pnpm registry SRI is lock-declared, not revalidated against registry tarballs; "
        "installed SHA256 file manifests are distinct from tarball integrity"
    )
    dist = root / "dist"
    if not dist.is_dir() or not any(dist.rglob("*")):
        raise CollectionError(
            f"{dist}: built frontend bundle missing; run pnpm build before inventory collection"
        )
    bundle_id = root_id + "?scope=built-bundle"
    bundle = component(
        bundle_id,
        text(root_manifest["name"], "web name") + " bundle",
        text(root_manifest["version"], "web version"),
        "actual frontend distribution bundle",
    )
    bundle["dependencies"] = [root_record["id"]]
    inventory.add(bundle)
    files = installed_files(dist, package_files(dist), bundle, inventory)
    bundle.update(
        {
            "files": cast(list[Json], files),
            "sha256": manifest_digest(files),
            "digest_basis": "canonical-installed-file-manifest-v1",
            "artifact_kind": "built-bundle",
        }
    )
    inventory.blockers.append(
        "Frontend installed development graph is not a tree-shaken bundle attribution map; "
        "bundler module/asset provenance and notice reconciliation require review"
    )


def debian_fields(content: str) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    previous = ""
    for line in [*content.splitlines(), ""]:
        if not line:
            if current:
                records.append(current)
            current, previous = {}, ""
        elif line.startswith((" ", "\t")) and previous:
            current[previous] += "\n" + line[1:]
        else:
            key, separator, value = line.partition(":")
            if (
                not separator
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", key)
                or key in current
            ):
                raise CollectionError("malformed Debian package database paragraph")
            current[key], previous = value.lstrip(" \t"), key
    return records


def apk_path(value: str, *, directory: bool = False) -> str:
    """Accept canonical rootfs-relative ownership paths, never normalize traversal away."""
    if directory and value == "":
        return ""
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or any(part in ("", ".", "..") for part in value.split("/"))
    ):
        raise CollectionError(f"unsafe APK ownership path: {value!r}")
    return value


def apk_checksum(value: str) -> None:
    """Validate legacy checksum syntax; declarations are not actual-file SHA256 evidence."""
    lengths = {"Q1": 20, "Q2": 32}
    if value[:2] not in lengths:
        raise CollectionError("unsupported APK checksum declaration")
    try:
        decoded = base64.b64decode(value[2:], validate=True)
    except binascii.Error as exc:
        raise CollectionError("malformed APK checksum declaration") from exc
    if len(decoded) != lengths[value[:2]]:
        raise CollectionError("malformed APK checksum length")


def apk_records(database: bytes) -> list[tuple[dict[str, str], list[Object], str]]:
    """Read only the legacy text installed database, retaining each raw record."""
    try:
        content = database.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CollectionError("unsupported binary APK installed database") from exc
    if "\x00" in content or content.startswith("ADB"):
        raise CollectionError("unsupported binary APK installed database")
    records: list[tuple[dict[str, str], list[Object], str]] = []
    for paragraph in content.split("\n\n"):
        if not paragraph.strip("\n"):
            continue
        metadata: dict[str, str] = {}
        files: list[Object] = []
        paths: set[str] = set()
        directory: str | None = None
        current_file: Object | None = None
        directory_mode = False
        for line in paragraph.splitlines():
            if len(line) < 2 or line[1] != ":" or "\r" in line:
                raise CollectionError("malformed or unsupported legacy APK installed database")
            key, value = line[0], line[2:]
            if key == "F":
                directory = apk_path(value, directory=True)
                current_file, directory_mode = None, False
            elif key == "R":
                if directory is None or "/" in value:
                    raise CollectionError("APK file record requires F directory and R basename")
                basename = apk_path(value)
                path = f"{directory}/{basename}" if directory else basename
                if path in paths:
                    raise CollectionError(f"duplicate APK file declaration: {path}")
                paths.add(path)
                current_file = {"path": path, "directory": directory, "filename": basename}
                files.append(current_file)
            elif key in ("Z", "a"):
                if current_file is None or key in current_file:
                    raise CollectionError(f"malformed or duplicate APK file field: {key}")
                if key == "Z":
                    apk_checksum(value)
                elif not re.fullmatch(r"\d+:\d+:[0-7]+", value):
                    raise CollectionError("malformed APK file ownership/mode")
                current_file[key] = value
            elif key == "M":
                if (
                    directory is None
                    or current_file is not None
                    or directory_mode
                    or not re.fullmatch(r"\d+:\d+:[0-7]+", value)
                ):
                    raise CollectionError("malformed or duplicate APK directory mode")
                directory_mode = True
            else:
                if key not in "CPVASITULomtcDpirkq" or key in metadata or directory is not None:
                    raise CollectionError(f"unsupported or duplicate APK package metadata: {key}")
                metadata[key] = value
        if any(key not in metadata for key in ("P", "V", "A", "L", "U", "C")):
            raise CollectionError("APK package missing P/V/A/L/U/C metadata")
        if (
            not re.fullmatch(r"[A-Za-z0-9_.+-]+", metadata["P"])
            or not re.fullmatch(r"[A-Za-z0-9_.+~-]+", metadata["V"])
            or not re.fullmatch(r"[A-Za-z0-9_+-]+", metadata["A"])
        ):
            raise CollectionError("malformed APK package name/version/architecture")
        apk_checksum(metadata["C"])
        if "c" in metadata and not re.fullmatch(r"[0-9a-f]{40}", metadata["c"]):
            raise CollectionError("malformed APK source commit declaration")
        if any("Z" not in entry for entry in files):
            raise CollectionError("APK file declaration missing Z checksum")
        records.append((metadata, files, paragraph))
    if not records:
        raise CollectionError("APK installed database contains no packages")
    return records


def apk_dependency(value: str) -> tuple[str, str, str, bool]:
    match = re.fullmatch(r"(!?)([^<>=~!\s]+)([<>=~]*)([^<>=~!\s]*)", value)
    if match is None:
        raise CollectionError(f"malformed APK dependency/provider declaration: {value!r}")
    conflict, name, operator, version = match.groups()
    if bool(operator) != bool(version) or operator not in ("", "=", "<", ">", "<=", ">=", "~"):
        raise CollectionError(f"unsupported APK dependency/provider declaration: {value!r}")
    if name.startswith("/"):
        apk_path(name[1:])
    return name, operator, version, bool(conflict)


def collect_apk_packages(
    database: bytes,
    image_id: str,
    image: Object,
    inventory: Inventory,
    members: dict[str, tarfile.TarInfo],
) -> tuple[dict[str, Object], dict[str, Object]]:
    owners: dict[str, Object] = {}
    records: dict[str, Object] = {}
    declarations: dict[str, dict[str, str]] = {}
    providers: dict[str, dict[str, tuple[str | None, str]]] = {}
    for fields, files, raw_record in apk_records(database):
        name, version, architecture = fields["P"], fields["V"], fields["A"]
        if name in records:
            raise CollectionError(f"duplicate APK package metadata: {name}")
        identity = purl("apk", "alpine/" + name, version) + "?arch=" + quote(architecture)
        record = component(identity, name, version, "installed image OS/native package")
        record.update(
            {
                "artifact_kind": "installed-file-manifest",
                "upstream_url": fields["U"] or None,
                "declared_license_metadata": fields["L"],
                "apk_metadata": cast(Object, dict(fields)),
                "apk_database_record": raw_record,
                "apk_file_declarations": cast(list[Json], files),
                "apk_checksum_verification": "raw declarations retained; not verified against bytes",
                "files": [],
                "evidence": [
                    evidence(
                        "image://" + image_id + "/lib/apk/db/installed",
                        sha256(database),
                        "apk-installed-database",
                    )
                ],
            }
        )
        inventory.add(record)
        records[name], declarations[name] = record, fields
        append_value(image, "dependencies", identity)
        providers.setdefault(name, {})[name] = (version, "package name")
        for declaration in fields.get("p", "").split():
            provided, operator, declared_version, conflict = apk_dependency(declaration)
            if conflict or operator not in ("", "="):
                raise CollectionError(f"unsupported APK provider declaration: {declaration}")
            provider_candidates = providers.setdefault(provided, {})
            candidate = (declared_version or None, "p:" + declaration)
            if name in provider_candidates and provider_candidates[name][0] != candidate[0]:
                raise CollectionError(f"duplicate inconsistent APK provider: {declaration}")
            provider_candidates[name] = candidate
        for entry in files:
            path = text(entry["path"], "APK file path")
            if path in owners:
                raise CollectionError(f"image file has multiple APK owners: {path}")
            owners[path] = record
            member = members.get(path)
            if member is None:
                inventory.blockers.append(
                    f"{identity}: APK declared file missing from rootfs: /{path}"
                )
            elif not (member.isfile() or member.issym() or member.islnk()):
                raise CollectionError(f"APK file declaration is not a file/link: /{path}")
        if not files:
            record["apk_file_ownership_status"] = "no F/R file declarations (metadata-only package)"
        inventory.blockers.append(
            f"{identity}: APK license/checksum/origin/commit declarations are unverified evidence; "
            "not concluded SPDX, archive authentication or rights approval"
        )
    # File-path dependencies are ownership candidates, not an invitation to execute a shell.
    for path, owner in owners.items():
        member = members.get(path)
        if member is not None and (member.issym() or (member.isfile() and member.mode & 0o111)):
            owner_name = text(owner["name"], "APK owner")
            providers.setdefault("/" + path, {}).setdefault(
                owner_name, (None, "installed executable ownership")
            )
    for name, fields in declarations.items():
        record = records[name]
        edges: list[Json] = []
        for declaration in fields.get("D", "").split():
            required, operator, version, conflict = apk_dependency(declaration)
            candidates: list[Json] = []
            for provider_name, (provided_version, basis) in sorted(
                providers.get(required, {}).items()
            ):
                status = "satisfied"
                if operator == "=":
                    if provided_version is None:
                        status = "version-unchecked"
                    elif provided_version != version:
                        status = "version-mismatch"
                elif operator:
                    # Do not approximate apk's version ordering with Python/PEP440 ordering.
                    status = "version-unchecked"
                if required.startswith("/"):
                    path = required[1:]
                    member = members.get(path)
                    if (
                        owners.get(path) is not records[provider_name]
                        or member is None
                        or not (member.issym() or (member.isfile() and member.mode & 0o111))
                    ):
                        status = "path-unavailable"
                candidates.append(
                    {
                        "id": records[provider_name]["id"],
                        "provided_version": provided_version,
                        "basis": basis,
                        "constraint_status": status,
                    }
                )
            viable = [
                object_value(candidate, "APK candidate")
                for candidate in candidates
                if object_value(candidate, "APK candidate")["constraint_status"]
                not in ("version-mismatch", "path-unavailable")
            ]
            if conflict:
                status = "conflict-present-or-unchecked" if viable else "conflict-absent"
            elif not viable:
                status = "unresolved"
            elif len(viable) > 1:
                status = "ambiguous"
            else:
                status = text(viable[0]["constraint_status"], "APK constraint status")
            edges.append(
                {
                    "declaration": declaration,
                    "kind": "conflict" if conflict else "dependency",
                    "installed_candidates": candidates,
                    "resolution": status,
                }
            )
            if not conflict and status == "satisfied":
                target = viable[0]["id"]
                if target not in array_value(record["dependencies"], "dependencies"):
                    append_value(record, "dependencies", target)
            elif status != "conflict-absent":
                inventory.blockers.append(
                    f"{record['id']}: APK dependency/provider {status}: {declaration}"
                )
        record["os_dependency_declarations"] = edges
    return owners, records


def collect_image_tar(archive: Path, image_id: str, inventory: Inventory) -> None:
    """Read exported rootfs without extracting or executing untrusted image content."""
    inventory.input(archive, "exported image filesystem")
    image = component(
        "pkg:oci/bonafides@" + quote(image_id, safe=""),
        "BonaFides image",
        image_id,
        "actual exported container filesystem",
    )
    image.update(
        {
            "sha256": file_sha256(archive),
            "digest_basis": "docker-export-tar-bytes-not-image-id",
            "artifact_kind": "exported-rootfs",
            "docker_image_id": image_id,
            "evidence": [
                evidence(archive.absolute().as_uri(), file_sha256(archive), "docker-export")
            ],
        }
    )
    inventory.add(image)
    with tarfile.open(archive) as tar:
        members: dict[str, tarfile.TarInfo] = {}
        for member in tar:
            archive_path = PurePosixPath(member.name)
            if archive_path.is_absolute() or ".." in archive_path.parts:
                raise CollectionError(f"image tar contains unsafe path: {member.name}")
            normalized = archive_path.as_posix().removeprefix("./")
            if normalized in members:
                raise CollectionError(f"image tar contains duplicate path: {normalized}")
            members[normalized] = member

        def read_member(path: str) -> bytes:
            member = members.get(path)
            if member is None or not member.isfile():
                raise CollectionError(f"image filesystem missing regular file: {path}")
            stream = tar.extractfile(member)
            if stream is None:
                raise CollectionError(f"image file unreadable: {path}")
            with stream:
                return stream.read()

        owners: dict[str, Object] = {}
        os_records: dict[str, Object] = {}
        declarations: dict[str, dict[str, str]] = {}
        packages: list[dict[str, str]] = []
        status = b""
        if "lib/apk/db/installed" in members and "var/lib/dpkg/status" in members:
            raise CollectionError("image has both APK and dpkg databases; ambiguous OS ownership")
        if "lib/apk/db/installed" in members:
            owners, os_records = collect_apk_packages(
                read_member("lib/apk/db/installed"), image_id, image, inventory, members
            )
        elif "var/lib/dpkg/status" in members:
            status = read_member("var/lib/dpkg/status")
            packages = debian_fields(status.decode("utf-8"))
        else:
            raise CollectionError("image lacks supported dpkg or legacy APK installed database")
        for fields in packages:
            if fields.get("Status") != "install ok installed":
                continue
            name, version = fields.get("Package"), fields.get("Version")
            if not name or not version:
                raise CollectionError("installed dpkg record missing Package/Version")
            architecture = fields.get("Architecture", "unknown")
            identity = purl("deb", "debian/" + name, version) + "?arch=" + quote(architecture)
            record = component(identity, name, version, "installed image OS/native package")
            record.update(
                {
                    "artifact_kind": "installed-file-manifest",
                    "upstream_url": fields.get("Homepage"),
                    "dpkg_metadata": cast(Object, dict(fields)),
                    "files": [],
                    "evidence": [
                        evidence(
                            "image://" + image_id + "/var/lib/dpkg/status",
                            sha256(status),
                            "dpkg-database",
                        )
                    ],
                }
            )
            inventory.add(record)
            os_records[name] = record
            declarations[name] = fields
            append_value(image, "dependencies", identity)
            list_candidates = [
                f"var/lib/dpkg/info/{name}.list",
                f"var/lib/dpkg/info/{name}:{architecture}.list",
            ]
            list_path = next(
                (candidate for candidate in list_candidates if candidate in members), None
            )
            if list_path is None:
                inventory.blockers.append(f"{identity}: dpkg file ownership list absent")
                continue
            for declared_path in read_member(list_path).decode().splitlines():
                normalized = declared_path.removeprefix("/").removeprefix("./")
                # Alternatives/shared directories may have multiple owners; regular files should not.
                if (
                    normalized in owners
                    and members.get(normalized)
                    and members[normalized].isfile()
                ):
                    inventory.blockers.append(f"image file has multiple dpkg owners: {normalized}")
                owners[normalized] = record
        if not os_records:
            raise CollectionError("image dpkg database contains no installed packages")
        for name, fields in declarations.items():
            record = os_records[name]
            raw_edges: list[Json] = []
            for declaration in (fields.get("Pre-Depends", ""), fields.get("Depends", "")):
                for clause in declaration.split(","):
                    if not clause.strip():
                        continue
                    alternatives = [
                        re.split(r"[\s(:]", option.strip(), maxsplit=1)[0]
                        for option in clause.split("|")
                    ]
                    targets = [
                        os_records[option]["id"] for option in alternatives if option in os_records
                    ]
                    raw_edges.append(
                        {"declaration": clause.strip(), "installed_candidates": targets}
                    )
                    for target in targets:
                        if target not in array_value(record["dependencies"], "dependencies"):
                            append_value(record, "dependencies", target)
                    if not targets:
                        inventory.blockers.append(
                            f"{record['id']}: dpkg dependency/provider unresolved: {clause.strip()}"
                        )
            record["os_dependency_declarations"] = raw_edges
        for member_name, member in sorted(members.items()):
            if not member.isfile():
                if member.issym() or member.islnk():
                    if "symlinks" not in image:
                        image["symlinks"] = []
                    append_value(
                        image,
                        "symlinks",
                        {"path": member_name, "target": member.linkname, "kind": "image-link"},
                    )
                    owner = owners.get(member_name)
                    if owner:
                        owner.setdefault("symlinks", [])
                        append_value(
                            owner,
                            "symlinks",
                            {
                                "path": member_name,
                                "target": member.linkname,
                                "kind": "symlink" if member.issym() else "hardlink",
                            },
                        )
                    elif link_kind := asset_kind(member_name):
                        inventory.blockers.append(
                            f"image unowned {link_kind} link: /{member_name}; "
                            "link target bytes/provenance are not inferred"
                        )
                continue
            data = read_member(member_name)
            digest = sha256(data)
            owner = owners.get(member_name)
            kind = asset_kind(member_name, data[:8])
            if owner:
                append_value(
                    owner, "files", {"path": member_name, "sha256": digest, "size": len(data)}
                )
                if is_notice(member_name):
                    append_value(owner, "notices", member_name)
                    notice = evidence(
                        "image://" + image_id + "/" + member_name, digest, "license-or-notice-file"
                    )
                    notice["text"] = data.decode("utf-8", errors="replace")
                    append_value(owner, "evidence", notice)
                    if owner.get("license_url") is None:
                        owner["license_url"] = "image://" + image_id + "/" + member_name
                    if member_name.endswith("/copyright") and "apk_metadata" not in owner:
                        declarations_in_file = sorted(
                            set(
                                re.findall(
                                    r"(?m)^License: (.+)$", data.decode("utf-8", errors="replace")
                                )
                            )
                        )
                        owner["declared_license_metadata"] = cast(list[Json], declarations_in_file)
            elif kind:
                inventory.blockers.append(
                    f"image unowned {kind}: /{member_name}; OS ownership cannot establish provenance/rights"
                )
            if kind:
                inventory.asset(
                    owner or image,
                    member_name,
                    digest,
                    kind,
                    "image://" + image_id + "/" + member_name,
                )
        for record in os_records.values():
            files = [
                object_value(value, "image file")
                for value in array_value(record["files"], "image files")
            ]
            if not files:
                inventory.blockers.append(f"{record['id']}: no package file bytes collected")
            record["sha256"] = manifest_digest(files)
            record["digest_basis"] = "canonical-installed-file-manifest-v1"
    inventory.blockers.append(
        "OS license/copyright declarations are evidence only, not machine-concluded SPDX; "
        "image language packages/unowned files require cross-reconciliation with source/installed graphs; "
        "native embedded code, virtual providers, source/relink and font/dictionary rights remain review-blocked"
    )


def collect_image(image: str, inventory: Inventory) -> None:
    inspection = array_value(
        run_json(["docker", "image", "inspect", image]), "docker image inspect"
    )
    if len(inspection) != 1:
        raise CollectionError(f"{image}: expected one local image")
    metadata = object_value(inspection[0], "docker image metadata")
    identity = text(metadata.get("Id"), "Docker-reported image identity")
    exact_sha256(identity, "Docker-reported image identity")
    safe_metadata: Object = {
        key: metadata.get(key)
        for key in ("Id", "Descriptor", "RepoDigests", "Architecture", "Os", "RootFS")
    }
    inventory.inputs.append(
        {"kind": "docker-image-inspect", "reference": image, "metadata": safe_metadata}
    )
    if object_value(metadata.get("Config", {}), "image Config").get("Volumes"):
        inventory.blockers.append(
            f"{image}: declared volumes are not inspected as live volume content; "
            "this is an image rootfs inventory only"
        )
    with tempfile.TemporaryDirectory(prefix="bonafides-inventory-") as temporary:
        # No bind mounts, network, image startup or package-manager execution.
        try:
            created = subprocess.run(
                ["docker", "create", "--network", "none", identity],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            raise CollectionError(f"cannot invoke docker: {exc}") from exc
        if created.returncode:
            raise CollectionError(f"docker create {image} failed: {created.stderr.strip()}")
        container = created.stdout.strip()
        if not re.fullmatch(r"[0-9a-f]{64}", container):
            raise CollectionError("docker create returned invalid container identity")
        try:
            archive = Path(temporary) / "rootfs.tar"
            exported = subprocess.run(
                ["docker", "export", "--output", str(archive), container],
                capture_output=True,
                text=True,
                check=False,
            )
            if exported.returncode:
                raise CollectionError(f"docker export failed: {exported.stderr.strip()}")
            collect_image_tar(archive, identity, inventory)
        finally:
            removed = subprocess.run(
                ["docker", "rm", "--volumes", container],
                capture_output=True,
                text=True,
                check=False,
            )
            if removed.returncode:
                inventory.errors.append(
                    f"temporary container cleanup failed; run docker rm --volumes {container}"
                )


def collect_build_images(root: Path, inventory: Inventory) -> None:
    path = root / "deploy/images.json"
    inventory.input(path, "pinned build/CI/service image manifest evidence")
    document = object_value(json.loads(path.read_text()), "image pin evidence")
    for name, value in object_value(document.get("images"), "image declarations").items():
        declaration = object_value(value, f"{name} image pin")
        reference = text(declaration.get("reference"), f"{name} image reference")
        location, separator, raw_digest = reference.rpartition("@")
        if not separator:
            raise CollectionError(f"{name}: image must be digest-pinned")
        digest = exact_sha256(raw_digest, f"{name} image index")
        record = component(
            purl("oci", name, raw_digest),
            name + " image pin",
            raw_digest,
            "declared build/CI/service image; NOT an inspected package inventory",
        )
        record.update(
            {
                "sha256": digest,
                "digest_basis": "manifest-declared-not-fetched",
                "sha256_verified": False,
                "artifact_kind": "declared-oci-index",
                "upstream_url": "oci://" + location,
                "image_reference": reference,
                "evidence": [
                    evidence(
                        path.absolute().as_uri(), file_sha256(path), "recorded-registry-inspection"
                    )
                ],
            }
        )
        inventory.add(record)
        for architecture in ("amd64", "arm64"):
            raw_platform = text(declaration.get(architecture), f"{name} {architecture} manifest")
            platform_digest = exact_sha256(raw_platform, f"{name} {architecture} manifest")
            identity = text(record["id"], "image pin identity") + "?arch=" + architecture
            platform = component(
                identity,
                name + " " + architecture + " manifest",
                raw_platform,
                "declared build/CI/service platform image; package inventory absent",
            )
            platform.update(
                {
                    "sha256": platform_digest,
                    "digest_basis": "manifest-declared-not-fetched",
                    "sha256_verified": False,
                    "artifact_kind": "declared-oci-platform-manifest",
                    "evidence": record["evidence"],
                }
            )
            inventory.add(platform)
            append_value(record, "dependencies", identity)
        inventory.blockers.append(
            f"{record['id']}: declared image pin does not inventory its build/CI/service "
            "packages, executables or licenses; independently collect each intended artifact"
        )
    inventory.blockers.append(
        "GitHub Actions source archives and host pnpm/Node/uv executable/native/transitive "
        "artifact hashes are not inventoried by image pin declarations"
    )


def collect_asvs(root: Path, inventory: Inventory) -> None:
    metadata_path = root / "compliance/asvs-source.json"
    inventory.input(metadata_path, "third-party ASVS attribution manifest")
    metadata = object_value(json.loads(metadata_path.read_text()), "ASVS source manifest")
    version = text(metadata.get("version"), "ASVS version")
    declared_license = text(metadata.get("license"), "ASVS declared license")
    upstream_digest = text(metadata.get("source_sha256"), "ASVS upstream CSV digest")
    exact_sha256("sha256:" + upstream_digest, "ASVS upstream CSV digest")
    for relative, label in (
        ("compliance/asvs-5.0.0.csv", "ASVS requirement register adaptation"),
        ("docs/compliance/asvs-register.md", "ASVS register documentation"),
    ):
        path = root / relative
        inventory.input(path, label)
        digest = file_sha256(path)
        identity = purl("generic", "owasp/asvs-" + path.name, version)
        record = component(
            identity,
            label,
            version,
            "third-party documentation adaptation; NOT original application license",
        )
        record.update(
            {
                "sha256": digest,
                "digest_basis": "file-bytes",
                "artifact_kind": "documentation-adaptation",
                "detected_license": declared_license,
                "license_url": metadata.get("license_url"),
                "upstream_url": metadata.get("source_url"),
                "copyright": metadata.get("attribution"),
                "adaptation": metadata.get("adaptation"),
                "upstream_source_sha256": upstream_digest,
                "upstream_source_digest_basis": "asvs-source.json declaration; not adaptation file digest",
                "evidence": [
                    evidence(path.absolute().as_uri(), digest, "actual-adaptation-file"),
                    evidence(
                        metadata_path.absolute().as_uri(),
                        file_sha256(metadata_path),
                        "declared-license-and-attribution",
                    ),
                ],
                "obligations": [
                    {
                        "description": "Preserve OWASP attribution, CC-BY-SA-4.0 license and change indication; "
                        "qualified reviewer must assess ShareAlike adaptation/distribution obligations separately from original source.",
                        "status": "pending",
                        "evidence": metadata_path.as_posix(),
                    }
                ],
            }
        )
        inventory.add(record)
        inventory.blockers.append(
            f"{identity}: CC-BY-SA attribution/ShareAlike legal review pending; no public-release clearance"
        )


class InventoryArgs(argparse.Namespace):
    uv_lock: Path
    python_root: Path
    web_root: Path
    image: str
    source_only: bool
    output: Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uv-lock", type=Path, required=True)
    parser.add_argument("--python-root", type=Path, required=True)
    parser.add_argument("--web-root", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--image", help="actual local Docker image reference; never pulls")
    mode.add_argument(
        "--source-only", action="store_true", help="explicitly EXCLUDE built image coverage"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv, namespace=InventoryArgs())
    inventory = Inventory()
    # Keep failures independent so the report names every missing prerequisite.
    for name in ("python", "web", "image", "asvs", "build-images"):
        if name == "image" and args.source_only:
            continue
        try:
            if name == "python":
                collect_python(args.uv_lock, args.python_root, inventory)
            elif name == "web":
                collect_web(args.web_root, inventory)
            elif name == "image":
                collect_image(args.image, inventory)
            elif name == "asvs":
                collect_asvs(args.uv_lock.parent, inventory)
            else:
                collect_build_images(args.uv_lock.parent, inventory)
        except (CollectionError, OSError, ValueError, tarfile.TarError) as exc:
            inventory.errors.append(f"{name} collection incomplete: {exc}")
    inventory.errors.extend(inventory.graph_errors())
    if not inventory.components:
        inventory.errors.append(
            "no actual components collected; install/build the named inputs before retrying"
        )
    output = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(inventory.snapshot(bool(args.source_only)), indent=2, sort_keys=True) + "\n"
    )
    if inventory.errors:
        print("\n".join(inventory.errors), file=sys.stderr)
        return 2
    print(
        f"Collected {len(inventory.components)} components into {output}; rights/release remain BLOCKED"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
