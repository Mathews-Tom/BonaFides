# SPDX-License-Identifier: MIT OR Apache-2.0
"""Policy and source hygiene acceptance tests using synthetic records only."""

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools import compliance


def approved_component() -> compliance.JsonObject:
    # Synthetic rights assertion exclusively for exercising checker branches.
    return {
        "id": "fixture",
        "name": "synthetic-fixture",
        "purl": "pkg:pypi/synthetic-fixture@1.0",
        "version": "1.0",
        "sha256": "a" * 64,
        "upstream_url": "https://example.invalid/source",
        "license_url": "https://example.invalid/LICENSE",
        "detected_license": "MIT",
        "concluded_license": "MIT",
        "selected_license": None,
        "copyright": "Synthetic test author",
        "notices": [],
        "scope": "test fixture only",
        "dependencies": [],
        "modifications": "none",
        "obligations": [
            {"description": "retain notice", "status": "satisfied", "evidence": "fixture assertion"}
        ],
        "evidence": [{"url": "https://example.invalid/artifact/LICENSE", "date": "2026-10-24"}],
        "reviewer": "synthetic test role",
        "review_date": "2026-10-24",
        "status": "approved",
        "rights_confirmed": True,
    }


class ExpressionTests(unittest.TestCase):
    def test_policy_classes(self) -> None:
        for expression, decision in [
            ("MIT", "allow"),
            ("MPL-2.0", "review"),
            ("AGPL-3.0-only", "deny"),
            ("LicenseRef-Unexamined", "review"),
            ("NOASSERTION", "review"),
            ("Unrecognized-License", "review"),
        ]:
            with self.subTest(expression=expression):
                self.assertEqual(compliance.evaluate_expression(expression), decision)

    def test_and_or_precedence(self) -> None:
        self.assertEqual(compliance.evaluate_expression("MIT OR GPL-3.0-only"), "allow")
        self.assertEqual(compliance.evaluate_expression("MIT AND GPL-3.0-only"), "deny")
        self.assertEqual(compliance.evaluate_expression("MIT OR MPL-2.0 AND GPL-3.0-only"), "allow")
        self.assertEqual(
            compliance.evaluate_expression("(MIT OR MPL-2.0) AND GPL-3.0-only"), "deny"
        )
        self.assertEqual(compliance.evaluate_expression("MIT AND MPL-2.0"), "review")

    def test_with_exception_is_not_ignored(self) -> None:
        expression = "GPL-2.0-only WITH Classpath-exception-2.0"
        self.assertEqual(compliance.evaluate_expression(expression), "review")
        policy = compliance.load_policy()
        policy["exceptions"][expression] = "allow"
        self.assertEqual(compliance.evaluate_expression(expression, policy), "allow")
        self.assertEqual(compliance.evaluate_expression("MIT WITH Unknown-exception"), "review")

    def test_malformed_expression(self) -> None:
        for expression in (
            None,
            "",
            "MIT OR",
            "AND MIT",
            "MIT Apache-2.0",
            "MIT / Apache-2.0",
            "(MIT OR Apache-2.0",
            "MIT)",
            "MIT WITH",
            "MIT WITH (Exception)",
            "MIT WITH Exception WITH Other",
            "MIT and Apache-2.0",
            "MIT++",
            "MIT:garbage",
        ):
            with self.subTest(expression=expression), self.assertRaises(compliance.ExpressionError):
                compliance.parse_expression(expression)


class InventoryTests(unittest.TestCase):
    def check(self, component: compliance.JsonObject) -> compliance.InventoryReport:
        return compliance.check_inventory({"schema_version": 1, "components": [component]})

    def test_complete_synthetic_record(self) -> None:
        self.assertTrue(self.check(approved_component())["release_eligible"])

    def test_required_identity_and_review_fields(self) -> None:
        for field in (
            "id",
            "name",
            "purl",
            "version",
            "sha256",
            "upstream_url",
            "license_url",
            "copyright",
            "scope",
            "modifications",
            "reviewer",
            "review_date",
            "evidence",
            "notices",
            "dependencies",
            "obligations",
            "detected_license",
            "concluded_license",
        ):
            component = approved_component()
            del component[field]
            with self.subTest(field=field):
                self.assertFalse(self.check(component)["release_eligible"])

    def test_unknown_no_rights_pending_and_obligations_block(self) -> None:
        blocked_changes: tuple[compliance.JsonObject, ...] = (
            {"status": "pending"},
            {"status": "no-rights"},
            {"rights_confirmed": False},
            {"concluded_license": "LicenseRef-Unknown"},
            {"concluded_license": "GPL-3.0-only"},
            {
                "obligations": [
                    {"description": "provide source", "status": "pending", "evidence": None}
                ]
            },
        )
        for changes in blocked_changes:
            component = approved_component()
            component.update(changes)
            with self.subTest(changes=changes):
                self.assertFalse(self.check(component)["release_eligible"])

    def test_or_branch_must_be_explicit_and_permitted(self) -> None:
        component = approved_component()
        component["detected_license"] = component["concluded_license"] = "MIT OR GPL-3.0-only"
        self.assertFalse(self.check(component)["release_eligible"])
        component["selected_license"] = "MIT"
        self.assertTrue(self.check(component)["release_eligible"])
        component["selected_license"] = "Apache-2.0"
        self.assertFalse(self.check(component)["release_eligible"])
        component["selected_license"] = "GPL-3.0-only"
        self.assertFalse(self.check(component)["release_eligible"])

    def test_and_cannot_be_replaced_by_one_operand(self) -> None:
        component = approved_component()
        component["detected_license"] = component["concluded_license"] = "MIT AND Apache-2.0"
        component["selected_license"] = "MIT"
        self.assertFalse(self.check(component)["release_eligible"])
        component["selected_license"] = "MIT AND Apache-2.0"
        self.assertTrue(self.check(component)["release_eligible"])

    def test_nested_branch_requires_every_conjunct(self) -> None:
        component = approved_component()
        component["detected_license"] = component["concluded_license"] = (
            "(MIT OR GPL-3.0-only) AND Apache-2.0"
        )
        component["selected_license"] = "MIT"
        self.assertFalse(self.check(component)["release_eligible"])
        component["selected_license"] = "MIT AND Apache-2.0"
        self.assertTrue(self.check(component)["release_eligible"])

    def test_no_inventory_no_release(self) -> None:
        self.assertFalse(compliance.check_inventory({})["release_eligible"])
        self.assertFalse(
            compliance.check_inventory({"schema_version": 1, "components": []})["release_eligible"]
        )

    def test_duplicate_and_unknown_edges_block(self) -> None:
        component = approved_component()
        component["dependencies"] = ["missing"]
        self.assertFalse(self.check(component)["release_eligible"])
        self.assertFalse(
            compliance.check_inventory(
                {"schema_version": 1, "components": [component, copy.deepcopy(component)]}
            )["release_eligible"]
        )

    def test_sbom_preserves_unknown_without_inventing_license(self) -> None:
        component = approved_component()
        component["concluded_license"] = None
        component["status"] = "pending"
        sbom = compliance.make_sbom({"components": [component]})
        self.assertEqual(sbom["bomFormat"], "CycloneDX")
        self.assertEqual(sbom["specVersion"], "1.6")
        self.assertNotIn("licenses", sbom["components"][0])
        self.assertEqual(sbom["components"][0]["properties"][0]["value"], "pending")
        component["concluded_license"] = "MIT"
        self.assertEqual(
            compliance.make_sbom({"components": [component]})["components"][0]["licenses"],
            [{"expression": "MIT"}],
        )

    def test_cli_report_blocks_and_writes_sbom(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = root / "inventory.json"
            inventory.write_text(
                json.dumps({"schema_version": 1, "components": [approved_component()]})
            )
            output = root / "reports/check.json"
            self.assertEqual(
                compliance.main(["check", "--inventory", str(inventory), "--output", str(output)]),
                0,
            )
            self.assertTrue(json.loads(output.read_text())["release_eligible"])
            inventory.write_text(
                json.dumps({"schema_version": 1, "components": [{"id": "unknown"}]})
            )
            self.assertEqual(
                compliance.main(["check", "--inventory", str(inventory), "--output", str(output)]),
                1,
            )
            self.assertFalse(json.loads(output.read_text())["release_eligible"])
            inventory.write_text(
                json.dumps({"schema_version": 1, "components": [approved_component()]})
            )
            self.assertEqual(
                compliance.main(["sbom", "--inventory", str(inventory), "--output", str(output)]), 0
            )
            self.assertEqual(json.loads(output.read_text())["specVersion"], "1.6")


class SourceTests(unittest.TestCase):
    def test_deliberate_synthetic_secret_is_redacted(self) -> None:
        synthetic = "AK" + "IA" + "Z" * 16
        findings = compliance.scan_secrets("token = " + synthetic)
        self.assertTrue(findings)
        self.assertNotIn(synthetic, json.dumps(findings))
        self.assertFalse(compliance.scan_secrets("password = 'short-placeholder'"))

    def test_source_gate_and_exclusions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            for filename in ("LICENSE-MIT", "LICENSE-APACHE", "NOTICE", "CONTRIBUTING.md"):
                (root / filename).write_text("synthetic fixture only")
            (root / "compliance").mkdir()
            (root / "compliance/components.json").write_text(
                json.dumps(
                    {"schema_version": 1, "components": [{"id": "recorded", "status": "pending"}]}
                )
            )
            (root / "app.py").write_text("# SPDX-License-Identifier: MIT OR Apache-2.0\n")
            (root / "pyproject.toml").write_text('[project]\nlicense = "MIT OR Apache-2.0"\n')
            (root / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")
            for excluded in (".docs", ".venv", "node_modules"):
                (root / excluded).mkdir()
                (root / excluded / "ignored.py").write_text("AK" + "IA" + "Z" * 16)
            self.assertTrue(compliance.check_source(root)["source_eligible"])
            (root / "pnpm-lock.yaml").write_text("AK" + "IA" + "Z" * 16)
            report = compliance.check_source(root)
            self.assertFalse(report["source_eligible"])
            self.assertIn("pnpm-lock.yaml", json.dumps(report))
            (root / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")
            (root / "app.py").write_text("print('missing header')\n")
            self.assertFalse(compliance.check_source(root)["source_eligible"])
            (root / "app.py").write_text("# SPDX-License-Identifier: MIT OR Apache-2.0\n")
            (root / "fixture.txt").write_text("AK" + "IA" + "Z" * 16)
            report = compliance.check_source(root)
            self.assertFalse(report["source_eligible"])
            self.assertIn("fixture.txt", json.dumps(report))
            (root / "fixture.txt").unlink()
            (root / "compliance/components.json").unlink()
            self.assertFalse(compliance.check_source(root)["source_eligible"])


if __name__ == "__main__":
    unittest.main()
