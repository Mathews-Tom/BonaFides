# SPDX-License-Identifier: MIT OR Apache-2.0
"""Fail-closed inventory review and local source hygiene; not legal certification."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal, NotRequired, TypedDict

type JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
type JsonObject = dict[str, JsonValue]
type Decision = Literal["allow", "review", "deny"]
type Expression = (
    tuple[Literal["license"], str]
    | tuple[Literal["WITH"], str, str]
    | tuple[Literal["AND", "OR"], "Expression", "Expression"]
)


class Policy(TypedDict, total=False):
    schema_version: JsonValue
    allow: list[str]
    review: list[str]
    deny: list[str]
    exceptions: dict[str, Decision]
    note: JsonValue


class ComponentReport(TypedDict):
    id: JsonValue
    policy: Decision
    blockers: list[str]


class InventoryReport(TypedDict):
    release_eligible: bool
    blockers: list[str]
    components: list[ComponentReport]


class SourceReport(TypedDict):
    source_eligible: bool
    blockers: list[str]
    note: str


class SbomLicense(TypedDict):
    expression: str


class SbomProperty(TypedDict):
    name: str
    value: str


class SbomHash(TypedDict):
    alg: str
    content: str


SbomComponent = TypedDict(
    "SbomComponent",
    {
        "type": str,
        "bom-ref": str,
        "name": str,
        "version": NotRequired[JsonValue],
        "purl": NotRequired[JsonValue],
        "hashes": NotRequired[list[SbomHash]],
        "licenses": NotRequired[list[SbomLicense]],
        "properties": list[SbomProperty],
    },
)


class SbomDependency(TypedDict):
    ref: str
    dependsOn: list[str]


class SbomMetadata(TypedDict):
    timestamp: str


class Sbom(TypedDict):
    bomFormat: str
    specVersion: str
    serialNumber: str
    version: int
    metadata: SbomMetadata
    components: list[SbomComponent]
    dependencies: list[SbomDependency]


class ComplianceArgs(argparse.Namespace):
    command: str
    root: Path
    inventory: Path
    output: Path


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "compliance/policy.json"
ORIGINAL_LICENSE = "MIT OR Apache-2.0"


class ExpressionError(ValueError):
    """Invalid SPDX expression syntax."""


def parse_expression(expression: object) -> Expression:
    """Parse SPDX's precedence (WITH, AND, OR), without guessing license IDs.

    License identifiers are classified separately, so syntactically valid but
    unknown identifiers can never acquire permission from this parser.
    """
    if not isinstance(expression, str) or not expression.strip():
        raise ExpressionError("missing expression")
    tokens = re.findall(r"\(|\)|[A-Za-z0-9][A-Za-z0-9.\-+:]*", expression)
    if re.sub(r"\s+", "", expression) != "".join(tokens):
        raise ExpressionError("invalid expression characters")
    position = 0

    def atom() -> Expression:
        node: Expression
        nonlocal position
        if position >= len(tokens):
            raise ExpressionError("expected license")
        token = tokens[position]
        position += 1
        if token == "(":
            node = binary("OR")
            if position >= len(tokens) or tokens[position] != ")":
                raise ExpressionError("unclosed parenthesis")
            position += 1
            return node
        if token in {"AND", "OR", "WITH", ")"}:
            raise ExpressionError("expected license identifier")
        if not re.fullmatch(
            r"(?:[A-Za-z0-9][A-Za-z0-9.-]*\+?|DocumentRef-[A-Za-z0-9.-]+:LicenseRef-[A-Za-z0-9.-]+)",
            token,
        ):
            raise ExpressionError("invalid license identifier")
        node = ("license", token)
        if position < len(tokens) and tokens[position] == "WITH":
            position += 1
            if position >= len(tokens) or tokens[position] in {"AND", "OR", "WITH", "(", ")"}:
                raise ExpressionError("expected exception identifier")
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", tokens[position]):
                raise ExpressionError("invalid exception identifier")
            node = ("WITH", token, tokens[position])
            position += 1
        return node

    def binary(operator: Literal["AND", "OR"]) -> Expression:
        nonlocal position
        node = binary("AND") if operator == "OR" else atom()
        while position < len(tokens) and tokens[position] == operator:
            position += 1
            right = binary("AND") if operator == "OR" else atom()
            node = (operator, node, right)
        return node

    result = binary("OR")
    if position != len(tokens):
        raise ExpressionError("unexpected token")
    return result


def _load_json_object(text: str) -> JsonObject:
    value: JsonValue = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


def load_policy(path: str | Path = POLICY_PATH) -> Policy:
    raw = _load_json_object(Path(path).read_text())
    policy: Policy = {}
    for kind in ("allow", "review", "deny"):
        values = raw.get(kind, [])
        if not isinstance(values, list):
            raise ValueError(f"invalid policy {kind} list")
        licenses: list[str] = []
        for value in values:
            if not isinstance(value, str):
                raise ValueError(f"invalid policy {kind} license")
            licenses.append(value)
        if kind == "allow":
            policy["allow"] = licenses
        elif kind == "review":
            policy["review"] = licenses
        else:
            policy["deny"] = licenses
    exceptions = raw.get("exceptions", {})
    if not isinstance(exceptions, dict):
        raise ValueError("invalid policy exceptions")
    decisions: dict[str, Decision] = {}
    for expression, decision in exceptions.items():
        if decision not in ("allow", "review", "deny"):
            raise ValueError("invalid policy exception decision")
        if decision == "allow":
            decisions[expression] = "allow"
        elif decision == "review":
            decisions[expression] = "review"
        else:
            decisions[expression] = "deny"
    policy["exceptions"] = decisions
    for field in ("schema_version", "note"):
        if field in raw:
            if field == "schema_version":
                policy["schema_version"] = raw[field]
            else:
                policy["note"] = raw[field]
    return policy


def evaluate_expression(expression: str, policy: Policy | None = None) -> Decision:
    """Return allow/review/deny; OR permits a branch, AND requires all."""
    active_policy = load_policy() if policy is None else policy
    ranks: dict[Decision, int] = {"allow": 0, "review": 1, "deny": 2}

    def evaluate(node: Expression) -> Decision:
        if node[0] == "license":
            if node[1] in active_policy.get("deny", []):
                return "deny"
            if node[1] in active_policy.get("review", []):
                return "review"
            if node[1] in active_policy.get("allow", []):
                return "allow"
            return "review"
        if node[0] == "WITH":
            # An exception can materially change rights; never ignore it or
            # inherit the base classification without an exact policy entry.
            return active_policy.get("exceptions", {}).get(f"{node[1]} WITH {node[2]}", "review")
        values = [evaluate(node[1]), evaluate(node[2])]
        return (min if node[0] == "OR" else max)(values, key=ranks.__getitem__)

    return evaluate(parse_expression(expression))


def _choices(node: Expression) -> set[Expression]:
    if node[0] == "license" or node[0] == "WITH":
        return {node}
    left, right = _choices(node[1]), _choices(node[2])
    if node[0] == "OR":
        return left | right
    return {("AND", a, b) for a in left for b in right}


def check_inventory(inventory: JsonObject, policy: Policy | None = None) -> InventoryReport:
    """Validate evidence completeness separately from policy permissibility."""
    blockers: list[str] = []
    records = inventory.get("components")
    if inventory.get("schema_version") != 1 or not isinstance(records, list) or not records:
        return {
            "release_eligible": False,
            "blockers": ["missing or invalid component inventory"],
            "components": [],
        }
    ids = [c.get("id") for c in records if isinstance(c, dict)]
    results: list[ComponentReport] = []
    required = (
        "id",
        "name",
        "purl",
        "version",
        "sha256",
        "upstream_url",
        "license_url",
        "detected_license",
        "concluded_license",
        "copyright",
        "scope",
        "modifications",
        "reviewer",
        "review_date",
    )
    for index, component in enumerate(records):
        issues: list[str] = []
        if not isinstance(component, dict):
            blockers.append(f"component {index}: invalid record")
            continue
        label = component.get("id") or str(index)
        for field in required:
            value = component.get(field)
            if not isinstance(value, str) or not value.strip():
                issues.append(f"missing {field}")
        digest = component.get("sha256", "")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            issues.append("invalid artifact sha256")
        if not str(component.get("purl", "")).startswith("pkg:"):
            issues.append("invalid package URL")
        if ids.count(label) != 1:
            issues.append("duplicate component identity")
        try:
            review_date = component.get("review_date") or ""
            if not isinstance(review_date, str):
                raise TypeError("review date must be a string")
            date.fromisoformat(review_date)
        except (ValueError, TypeError):
            issues.append("invalid review date")
        for field in ("notices", "dependencies", "obligations", "evidence"):
            if not isinstance(component.get(field), list):
                issues.append(f"missing {field} list")
        dependencies = component.get("dependencies", [])
        if isinstance(dependencies, list):
            for dependency in dependencies:
                if dependency not in ids:
                    issues.append(f"unknown dependency {dependency}")
        evidence = component.get("evidence")
        if not evidence:
            issues.append("missing artifact license evidence")
        elif isinstance(evidence, list):
            for entry in evidence:
                try:
                    if not isinstance(entry, dict) or not entry.get("url"):
                        raise ValueError
                    evidence_date = entry.get("date", "")
                    if not isinstance(evidence_date, str):
                        raise TypeError("evidence date must be a string")
                    date.fromisoformat(evidence_date)
                except (ValueError, TypeError):
                    issues.append("invalid evidence entry")
        obligations = component.get("obligations", [])
        if isinstance(obligations, list):
            for obligation in obligations:
                if (
                    not isinstance(obligation, dict)
                    or not obligation.get("description")
                    or obligation.get("status") != "satisfied"
                    or not obligation.get("evidence")
                ):
                    issues.append("unresolved obligation")
        if component.get("status") != "approved" or component.get("rights_confirmed") is not True:
            issues.append("rights review unresolved or no rights")
        classification: Decision = "review"
        try:
            detected_expression = component.get("detected_license")
            concluded_expression = component.get("concluded_license")
            if not isinstance(detected_expression, str) or not isinstance(
                concluded_expression, str
            ):
                raise ExpressionError("missing expression")
            detected = parse_expression(detected_expression)
            concluded = parse_expression(concluded_expression)
            selected = component.get("selected_license")
            if selected:
                if not isinstance(selected, str):
                    raise ExpressionError("invalid selected expression")
                chosen = parse_expression(selected)
                if chosen not in _choices(concluded):
                    issues.append("selected license is not a permitted complete branch")
                classification = evaluate_expression(selected, policy)
            else:
                classification = evaluate_expression(concluded_expression, policy)
                if len(_choices(concluded)) > 1:
                    issues.append("OR expression requires explicit branch selection")
            if detected != concluded and not component.get("license_reconciliation"):
                issues.append("detected/concluded mismatch requires reconciliation evidence")
            if classification != "allow":
                issues.append(f"license policy {classification}")
        except (ExpressionError, TypeError):
            issues.append("missing or malformed license expression")
        results.append({"id": label, "policy": classification, "blockers": issues})
        blockers.extend(f"{label}: {issue}" for issue in issues)
    inventory_blockers = inventory.get("blockers", [])
    if isinstance(inventory_blockers, list):
        blockers.extend(str(item) for item in inventory_blockers)
    else:
        blockers.append("invalid inventory blockers list")
    return {"release_eligible": not blockers, "blockers": blockers, "components": results}


def make_sbom(inventory: JsonObject) -> Sbom:
    """CycloneDX 1.6 inventory snapshot; absent licenses remain absent.

    This is not a built-image graph or an assertion of approval/completeness.
    """
    records = inventory.get("components")
    if not isinstance(records, list) or not records:
        raise ValueError("SBOM requires a nonempty component inventory")
    identities: set[str] = set()
    validated_records: list[tuple[JsonObject, str, str]] = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("SBOM component requires a name and stable identity")
        identity = record.get("id")
        name = record.get("name")
        if not isinstance(identity, str) or not identity or not isinstance(name, str) or not name:
            raise ValueError("SBOM component requires a name and stable identity")
        if identity in identities:
            raise ValueError("SBOM contains duplicate component identities")
        identities.add(identity)
        validated_records.append((record, identity, name))
    dependencies: list[SbomDependency] = []
    for record, identity, _ in validated_records:
        edge_values = record.get("dependencies", [])
        if not isinstance(edge_values, list):
            raise ValueError("SBOM dependency edge references a missing component")
        edges: list[str] = []
        for edge in edge_values:
            if not isinstance(edge, str) or edge not in identities:
                raise ValueError("SBOM dependency edge references a missing component")
            edges.append(edge)
        dependencies.append({"ref": identity, "dependsOn": edges})
    components: list[SbomComponent] = []
    for record, identity, name in validated_records:
        item: SbomComponent = {
            "type": "library",
            "bom-ref": identity,
            "name": name,
            "properties": [],
        }
        if record.get("version"):
            item["version"] = record["version"]
        if record.get("purl"):
            item["purl"] = record["purl"]
        digest = record.get("sha256")
        if digest:
            if not isinstance(digest, str):
                raise TypeError("SBOM artifact sha256 must be a string")
            if re.fullmatch(r"[a-fA-F0-9]{64}", digest):
                item["hashes"] = [{"alg": "SHA-256", "content": digest}]
        expression = record.get("concluded_license")
        if expression:
            if not isinstance(expression, str):
                raise ExpressionError("invalid license expression")
            parse_expression(expression)
            item["licenses"] = [{"expression": expression}]
        item["properties"] = [
            {"name": "bonafides:review-status", "value": str(record.get("status", "unknown"))}
        ]
        components.append(item)
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {"timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z")},
        "components": components,
        "dependencies": dependencies,
    }


_SECRET_PATTERNS = (
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|password|secret|access[_-]?token)\s*[:=]\s*['\"][A-Za-z0-9_+/=-]{20,}['\"]"
    ),
)


def scan_secrets(text: str) -> list[str]:
    """Return redacted finding types, never credential contents."""
    return [
        f"potential secret pattern {index + 1}"
        for index, pattern in enumerate(_SECRET_PATTERNS)
        if pattern.search(text)
    ]


def check_source(root: str | Path) -> SourceReport:
    root = Path(root).resolve()
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        capture_output=True,
        check=True,
    )
    blockers: list[str] = []
    excluded = {".docs", ".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build"}
    source_suffixes = {".py", ".ts", ".tsx", ".js", ".jsx", ".css", ".sh", ".sql", ".yaml", ".yml"}
    for name in sorted(set(result.stdout.decode().split("\0")) - {""}):
        path = root / name
        if excluded.intersection(Path(name).parts):
            continue
        if path.is_symlink():
            blockers.append(f"{name}: symbolic link requires explicit source review")
            continue
        if not path.is_file():
            continue
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        for finding in scan_secrets(text):
            blockers.append(f"{name}: {finding}")
        if (
            path.suffix in source_suffixes
            and path.name != "pnpm-lock.yaml"
            and f"SPDX-License-Identifier: {ORIGINAL_LICENSE}"
            not in "\n".join(text.splitlines()[:10])
        ):
            blockers.append(f"{name}: missing original-source SPDX header")
        if path.name == "pyproject.toml":
            project: object = tomllib.loads(text).get("project", {})
            if not isinstance(project, dict):
                raise ValueError("invalid project metadata")
            if project.get("license") != ORIGINAL_LICENSE:
                blockers.append(f"{name}: incorrect project license declaration")
        if (
            path.name == "package.json"
            and _load_json_object(text).get("license") != ORIGINAL_LICENSE
        ):
            blockers.append(f"{name}: incorrect package license declaration")
    for filename in ("LICENSE-MIT", "LICENSE-APACHE", "NOTICE", "CONTRIBUTING.md"):
        if not (root / filename).is_file() or not (root / filename).read_text().strip():
            blockers.append(f"missing license/governance artifact {filename}")
    inventory_path = root / "compliance/components.json"
    if not inventory_path.is_file():
        blockers.append("missing component inventory")
    else:
        inventory = _load_json_object(inventory_path.read_text())
        if inventory.get("schema_version") != 1 or not inventory.get("components"):
            blockers.append("missing structured component inventory")
    return {
        "source_eligible": not blockers,
        "blockers": blockers,
        "note": "Recorded unresolved obligations do not block local source work; release check remains separate.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("check", "sbom"):
        child = commands.add_parser(command)
        child.add_argument("--inventory", type=Path, required=True)
        child.add_argument("--output", type=Path, required=True)
    commands.add_parser("source").add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv, namespace=ComplianceArgs())
    try:
        if args.command == "source":
            source_report = check_source(args.root)
            print(json.dumps(source_report, indent=2))
            return 0 if source_report["source_eligible"] else 1
        inventory = _load_json_object(args.inventory.read_text())
        report: InventoryReport | Sbom
        if args.command == "check":
            inventory_report = check_inventory(inventory)
            report = inventory_report
            exit_code = 0 if inventory_report["release_eligible"] else 1
        else:
            report = make_sbom(inventory)
            exit_code = 0
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        return exit_code
    except (OSError, ValueError, TypeError, KeyError, subprocess.CalledProcessError) as error:
        print(f"compliance: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
