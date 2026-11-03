# SPDX-License-Identifier: MIT OR Apache-2.0
"""Prove the core loop on the release image in production mode with an explicit fixture model.

The installer runs from outside the checkout. After owner setup the shipped `bonafides demo load`
seeds synthetic profile data, then ordinary APIs take a pasted posting to a checked résumé and
cover letter, exact-revision review, PDF export and saved tracking. The deterministic stub
(`tools.stub_model`, outside the image) answers on an isolated proof-private route; fabricated
output must block review and export. Its qualification receipts are fixture-only: this proof
attests nothing about any real model. Evidence is a redacted receipt with no HTTP bodies, token
or password.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import secrets
import subprocess
import time
from pathlib import Path
from typing import Any

from tools import stub_model
from tools.install_proof import InstallProof
from tools.runtime_proof import ProbeFailure, require, require_unused_namespace, run

ROUTE_FILE = "local_model_route.json"
CONTAINER_ROUTE = "/run/bonafides/local_model_route.json"
POSTING = {
    "title": "Fictional operations analyst",
    "company": "Fictional Acme",
    "location_text": "Remote",
    "canonical_url": "https://careers.fictional-employer.com/role/core-loop",
    "description_text": "Linux required.\nSQL required.\nKubernetes preferred.",
}
REQUIREMENTS = ("Linux required.", "SQL required.")
BLOCKED_SCENARIOS = (
    ("posting-only-skill", "statement_not_in_claim"),
    ("absent-claim", "claim_not_in_profile"),
)
FIXTURE_NOTICE = (
    "Model output is a deterministic fixture; its qualification receipts are fixture-only and "
    "attest nothing about any real model."
)


class CoreLoopProof(InstallProof):
    def __init__(self, project: str, port: int, platform: str, image: str, context: Path) -> None:
        super().__init__(project, port, platform, image, context)
        self.stub_name = f"{project}-stub"
        self.control = self.work / "stub-scenario"
        self.route_enabled = False
        self.document_pdf = b""

    def environment(self, image: str) -> dict[str, str]:
        env = super().environment(image)
        if self.route_enabled:
            env["BONAFIDES_LOCAL_MODEL_ROUTE_FILE"] = CONTAINER_ROUTE
        return env

    def mutate(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        *,
        version: object | None = None,
        expected: tuple[int, ...] = (200, 201, 202),
    ) -> dict[str, Any]:
        headers = {"Idempotency-Key": secrets.token_hex(16)}
        if version is not None:
            headers["If-Match"] = f'"{version}"'
        result, _, _ = self.api.request(method, path, body, expected=expected, headers=headers)
        return result

    def wait_workflow(self, identifier: str, *, timeout: int = 240) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            workflow, _, _ = self.api.request("GET", f"/api/v1/workflows/{identifier}")
            if workflow["state"] in {"succeeded", "cancelled", "permanent_failure"}:
                require(workflow["state"] == "succeeded", f"workflow_{workflow['state']}")
                return workflow
            time.sleep(0.5)
        raise ProbeFailure("workflow_deadline")

    def demo_load(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "docker",
                "compose",
                "--project-directory",
                str(self.install_dir),
                "--env-file",
                str(self.install_dir / ".env"),
                "-f",
                str(self.install_dir / "compose.yaml"),
                *("exec", "-T", "api", "bonafides", "demo", "load"),
            ],
            env=self.environment(self.image),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

    def seed_demo(self) -> None:
        refused = self.demo_load()
        require(
            refused.returncode == 2 and "owner_setup_required" in refused.stderr,
            "demo_load_without_owner_not_refused",
        )
        self.checked("demo_load_refuses_before_owner_setup")
        self.create_owner()
        before = self.state()
        loaded = self.demo_load()
        require(loaded.returncode == 0 and "loaded." in loaded.stdout, "demo_load_failed")
        seeded = self.state()
        require(seeded != before, "demo_load_wrote_nothing")
        again = self.demo_load()
        require(again.returncode == 0 and "nothing changed" in again.stdout, "demo_rerun_not_noop")
        require(self.state() == seeded, "demo_rerun_wrote")
        profile, _, _ = self.api.request("GET", "/api/v1/profile")
        facts = sorted(claim["rendering"] for claim in profile["current_revision"]["claims"])
        require(facts == sorted(demo_facts()), "demo_claims_unexpected")
        self.api.request(
            "POST",
            "/api/v1/benchmarks/profile",
            {"fixture": "SYN-CORE"},
            expected=(404,),
            headers={"If-Match": '"1"', "Idempotency-Key": secrets.token_hex(16)},
        )
        self.checked("demo_load_seeds_once_and_repeats_as_noop", benchmark_http_status=404)

    def start_stub(self) -> None:
        self.control.write_text("valid\n")
        self.control.chmod(0o644)
        script = (self.context / "tools" / "stub_model.py").resolve()
        run(
            [
                *("docker", "run", "--detach", "--name", self.stub_name),
                *("--network", f"{self.project}_private"),
                *("--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges"),
                *("--user", "10001:10001", "--pull", "never", "--entrypoint", "python"),
                *("--mount", f"type=bind,src={script},dst=/stub_model.py,readonly"),
                *("--mount", f"type=bind,src={self.control},dst=/scenario,readonly"),
                self.image,
                *("/stub_model.py", "serve", "--host", "0.0.0.0", "--port", "11434"),
                *("--control-file", "/scenario"),
            ]
        )
        address = (
            run(
                [
                    *("docker", "inspect", "--format"),
                    "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
                    self.stub_name,
                ]
            )
            .decode()
            .strip()
        )
        require(re.fullmatch(r"\d+\.\d+\.\d+\.\d+", address) is not None, "stub_address_missing")
        stub_model.route(
            f"http://{address}:11434",
            address,
            self.install_dir / "secrets" / ROUTE_FILE,
            90,
        )
        self.compose(self.image, "stop", "ingress", "api", "worker")
        self.compose(self.image, "run", "--rm", "--no-deps", "prepare")
        self.route_enabled = True
        self.compose(self.image, "up", "--detach", "--wait")
        self.api.login(self.login, self.password)
        self.checked("explicit_proof_private_stub_route_enabled")

    def scenario(self, name: str) -> None:
        self.control.write_text(name + "\n")

    def posting_application(
        self, suffix: str, title: str = str(POSTING["title"])
    ) -> dict[str, Any]:
        posting = self.mutate(
            "POST",
            "/api/v1/postings",
            {**POSTING, "title": title, "canonical_url": POSTING["canonical_url"] + suffix},
            expected=(201,),
        )
        posting = self.mutate(
            "PUT",
            f"/api/v1/postings/{posting['id']}/disposition",
            {"disposition": "saved"},
            version=posting["version"],
        )
        source = POSTING["description_text"]
        requirements = [
            {
                "requirement_key": f"req-{index}",
                "text": quote,
                "importance": "required",
                "span": {
                    "start": source.index(quote),
                    "end": source.index(quote) + len(quote),
                    "quote": quote,
                },
                "hard_constraint": None,
            }
            for index, quote in enumerate(REQUIREMENTS, 1)
        ]
        current, _, _ = self.api.request("GET", f"/api/v1/postings/{posting['id']}")
        self.mutate(
            "PUT",
            f"/api/v1/postings/{posting['id']}/requirements",
            {"confirmed": True, "requirements": requirements},
            version=current["version"],
        )
        return self.mutate(
            "POST", "/api/v1/applications", {"posting_id": posting["id"]}, expected=(201,)
        )

    def generate_pair(self, application: dict[str, Any]) -> dict[str, dict[str, Any]]:
        profile, _, _ = self.api.request("GET", "/api/v1/profile")
        query = (
            f"profile_revision_id={profile['current_revision']['id']}"
            "&template_version=bonafides-evidence-v1&tone=professional&language=en"
        )
        preview, _, _ = self.api.request(
            "GET", f"/api/v1/applications/{application['id']}/generation-context?{query}"
        )
        require(preview["generation_route"]["status"] == "qualified", "route_not_qualified")
        body = {
            key: preview[key]
            for key in (
                "profile_revision_id",
                "requirements_revision_id",
                "template_version",
                "tone",
                "language",
                "context_sha256",
            )
        }
        queued = self.mutate(
            "POST",
            f"/api/v1/applications/{application['id']}/generation",
            body,
            version=preview["application_version"],
        )
        self.wait_workflow(queued["id"])
        listing, _, _ = self.api.request(
            "GET", f"/api/v1/applications/{application['id']}/documents"
        )
        pair = {item["kind"]: item for item in listing["items"]}
        require(set(pair) == {"resume", "cover_letter"}, "pair_not_generated")
        return pair

    @staticmethod
    def factual_keys(document: dict[str, Any]) -> list[str]:
        return [
            block["block_key"]
            for section in document["current_revision"]["body"]["sections"]
            for block in section["blocks"]
            if block["claim_kind"] == "factual"
        ]

    def review(self, document: dict[str, Any], *, expected: tuple[int, ...]) -> dict[str, Any]:
        revision = document["current_revision"]
        return self.mutate(
            "POST",
            f"/api/v1/documents/{document['id']}/review",
            {
                "revision_id": revision["id"],
                "content_sha256": revision["content_sha256"],
                "decision": "accepted",
                "reviewed_block_keys": self.factual_keys(document),
            },
            version=document["version"],
            expected=expected,
        )

    def render(self, document: dict[str, Any], *, expected: tuple[int, ...]) -> dict[str, Any]:
        revision = document["current_revision"]
        return self.mutate(
            "POST",
            f"/api/v1/documents/{document['id']}/renders",
            {
                "revision_id": revision["id"],
                "content_sha256": revision["content_sha256"],
                "format": "pdf",
            },
            version=document["version"],
            expected=expected,
        )

    def fetch(self, identifier: str) -> dict[str, Any]:
        document, _, _ = self.api.request("GET", f"/api/v1/documents/{identifier}")
        return document

    def checked_pair_to_pdf_and_tracking(self) -> None:
        self.scenario("valid")
        application = self.posting_application("")
        started = time.monotonic()
        pair = self.generate_pair(application)
        generation_seconds = round(time.monotonic() - started, 3)
        for kind, document in pair.items():
            require(not document["unsupported_claims"], f"{kind}_unsupported_claim")
            require(document["current_revision"]["validation_issues"] == [], f"{kind}_issues")
            self.review(document, expected=(200, 201))
        self.checked(
            "posting_to_checked_resume_and_letter",
            generation_seconds=generation_seconds,
            documents=sorted(pair),
        )
        resume = self.fetch(pair["resume"]["id"])
        require(resume["reviewed"], "review_not_durable")
        queued = self.render(resume, expected=(202,))
        rendered = self.wait_workflow(queued["id"])
        _, _, raw = self.api.request(
            "GET", f"/api/v1/artifacts/{rendered['result']['artifact_id']}"
        )
        require(raw.startswith(b"%PDF-"), "artifact_not_pdf")
        from pypdf import PdfReader

        text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(raw)).pages)
        require(
            all(fact in text for fact in demo_facts()) and "Kubernetes" not in text,
            "pdf_not_grounded",
        )
        self.document_pdf = raw
        self.evidence["pdf"] = {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
        self.checked("reviewed_exact_revision_exports_pdf")
        current, _, _ = self.api.request("GET", f"/api/v1/applications/{application['id']}")
        applied = self.mutate(
            "POST",
            f"/api/v1/applications/{application['id']}/stage",
            {
                "to_stage": "applied",
                "effective_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "reason": "Submitted the reviewed synthetic résumé and letter",
            },
            version=current["version"],
        )
        self.mutate(
            "POST",
            f"/api/v1/applications/{application['id']}/notes",
            {"body": "Reviewed résumé revision exported and sent."},
            version=applied["version"],
        )
        saved, _, _ = self.api.request("GET", f"/api/v1/applications/{application['id']}")
        require(saved["current_stage"] == "applied", "tracking_stage_not_saved")
        self.checked("application_tracking_saved", stage="applied")

    def blocked_output(self) -> None:
        for index, (scenario, code) in enumerate(BLOCKED_SCENARIOS, 1):
            self.scenario(scenario)
            application = self.posting_application(f"-blocked-{index}")
            resume = self.generate_pair(application)["resume"]
            issues = [issue["code"] for issue in resume["current_revision"]["validation_issues"]]
            require(issues == [code], f"{scenario}_issue_{issues}")
            require(bool(resume["unsupported_claims"]), f"{scenario}_not_visible")
            refused = self.review(resume, expected=(409,))
            require(refused.get("code") == "unsupported_claims", f"{scenario}_review_allowed")
            blocked = self.render(self.fetch(resume["id"]), expected=(409,))
            require(blocked.get("code") == "unsupported_claims", f"{scenario}_export_allowed")
            self.checked(f"{scenario}_blocks_review_and_export", validation_issue=code)

    def prove(self) -> None:
        require_unused_namespace(self.project)
        self.extract_script(self.image)
        identity = self.image_identity(self.image)
        self.install(self.image)
        require(self.require_running(self.image) == identity, "image_not_running")
        self.checked("release_image_installed_in_production_mode")
        self.seed_demo()
        self.start_stub()
        self.checked_pair_to_pdf_and_tracking()
        self.blocked_output()
        self.evidence["image"] = identity
        self.evidence["model"] = {"kind": "deterministic_fixture", "notice": FIXTURE_NOTICE}

    def teardown(self) -> None:
        subprocess.run(
            ["docker", "rm", "--force", self.stub_name], capture_output=True, check=False
        )
        super().teardown()


def demo_facts() -> tuple[str, ...]:
    from bonafides.profiles import SYNTHETIC_FACTS

    return SYNTHETIC_FACTS


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="new bonafides-proof-[a-z0-9-]+ namespace")
    parser.add_argument("--confirm-disposable", required=True, help="must repeat exact --project")
    parser.add_argument("--platform", choices=("linux/arm64", "linux/amd64"), required=True)
    parser.add_argument("--port", type=int, default=18200)
    parser.add_argument("--image", required=True, help="locally built application image")
    parser.add_argument("--context", type=Path, default=Path("."))
    parser.add_argument("--evidence-root", type=Path, default=Path(".local"))
    args = parser.parse_args()
    if (
        not re.fullmatch(r"bonafides-proof-[a-z0-9][a-z0-9-]{0,22}", args.project)
        or args.project != args.confirm_disposable
    ):
        parser.error("exact disposable namespace confirmation required")
    if not 1024 <= args.port <= 65535:
        parser.error("port must be unprivileged")
    proof = CoreLoopProof(
        args.project, args.port, args.platform, args.image, args.context.resolve()
    )
    failure: str | None = None
    try:
        proof.prove()
    except Exception as exc:
        failure = str(exc) if isinstance(exc, ProbeFailure) else type(exc).__name__
        proof.evidence["failure"] = failure
        trace = exc.__traceback__
        while trace is not None:
            if trace.tb_frame.f_code.co_filename == __file__:
                proof.evidence["failure_line"] = trace.tb_lineno
            trace = trace.tb_next
    finally:
        proof.teardown()
    proof.evidence["passed"] = failure is None
    proof.evidence["total_seconds"] = round(time.monotonic() - proof.started, 3)
    receipt = args.evidence_root.resolve() / args.project / "receipt.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(proof.evidence, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"receipt": str(receipt), "passed": failure is None, "failure": failure}))
    return 0 if failure is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
