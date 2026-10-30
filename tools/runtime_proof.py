# SPDX-License-Identifier: MIT OR Apache-2.0
"""Destructive runtime proof confined to newly-created bonafides-proof-* Compose namespaces.

The command refuses existing projects/volumes. It never accepts a database DSN or
restores over a running installation. Evidence excludes HTTP bodies and secrets.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import io
import json
import os
import platform
import re
import secrets
import subprocess
import tarfile
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from tools.dev_setup import initialize, private_write

MAX_ARCHIVE = 128 * 1024 * 1024
TERMINAL = {"succeeded", "cancelled", "permanent_failure"}
_SNAPSHOT_COUNTS = """
SELECT json_object_agg(name,n) FROM (
 SELECT 'profiles' name,count(*) n FROM bonafides.profiles
 UNION ALL SELECT 'profile_revisions',count(*) FROM bonafides.profile_revisions
 UNION ALL SELECT 'documents',count(*) FROM bonafides.document_drafts
 UNION ALL SELECT 'workflow_runs',count(*) FROM bonafides.workflow_runs
 UNION ALL SELECT 'source_configs',count(*) FROM bonafides.source_configs
 UNION ALL SELECT 'source_policy_reviews',count(*) FROM bonafides.source_policy_reviews
 UNION ALL SELECT 'job_postings',count(*) FROM bonafides.job_postings
 UNION ALL SELECT 'job_source_observations',count(*) FROM bonafides.job_source_observations
 UNION ALL SELECT 'saved_opportunities',count(*) FROM bonafides.saved_opportunities
 UNION ALL SELECT 'posting_duplicate_decisions',count(*) FROM bonafides.posting_duplicate_decisions
) q
"""
_RESTORE_AUTHORIZATION_ROWS = """
SELECT json_build_object(
 'approvals', (SELECT coalesce(json_object_agg(
   owner_id::text || ':' || id::text,
   encode(sha256(convert_to((to_jsonb(a)-'revoked_at'-'version')::text,'UTF8')),'hex')
 ),'{}'::json) FROM bonafides.action_approvals a),
 'consents', (SELECT coalesce(json_object_agg(
   owner_id::text || ':' || id::text,
   encode(sha256(convert_to((to_jsonb(c)-'revoked_at')::text,'UTF8')),'hex')
 ),'{}'::json) FROM bonafides.hosted_egress_consents c),
 'audit', (SELECT coalesce(json_object_agg(
   owner_id::text || ':' || id::text,
   encode(sha256(convert_to(to_jsonb(e)::text,'UTF8')),'hex')
 ),'{}'::json) FROM bonafides.approval_audit_events e)
)
"""


class ProbeFailure(RuntimeError):
    pass


def require(condition: object, name: str) -> None:
    if not condition:
        raise ProbeFailure(name)


def run(
    arguments: list[str],
    *,
    data: bytes | None = None,
    timeout: int = 300,
    env: dict[str, str] | None = None,
) -> bytes:
    result = subprocess.run(
        arguments, input=data, capture_output=True, timeout=timeout, check=False, env=env
    )
    if result.returncode:
        # Child diagnostics may contain credentials/content. They are deliberately not echoed.
        raise ProbeFailure("subprocess_failed:" + arguments[0])
    return result.stdout


class API:
    def __init__(self, origin: str) -> None:
        self.origin = origin
        self.cookie: dict[str, str] = {}
        self.csrf = ""

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        expected: tuple[int, ...] = (200,),
        headers: dict[str, str] | None = None,
    ) -> tuple[dict[str, Any], dict[str, str], bytes]:
        target = urlsplit(self.origin)
        hostname = target.hostname
        if hostname is None:
            raise ProbeFailure("invalid_http_origin")
        connection = http.client.HTTPConnection(hostname, target.port, timeout=20)
        values = {"Origin": self.origin, "Content-Type": "application/json"}
        if self.cookie:
            values["Cookie"] = "; ".join(f"{key}={value}" for key, value in self.cookie.items())
        if self.csrf:
            values["X-CSRF-Token"] = self.csrf
        values.update(headers or {})
        try:
            connection.request(
                method, path, json.dumps(body).encode() if body is not None else None, values
            )
            response = connection.getresponse()
            raw = response.read(MAX_ARCHIVE + 1)
            require(len(raw) <= MAX_ARCHIVE, "oversized_http_response")
            require(
                response.status in expected, f"http_{method}_{path.split('?')[0]}_{response.status}"
            )
            metadata = {key.lower(): value for key, value in response.getheaders()}
            for key, value in response.getheaders():
                if key.lower() == "set-cookie":
                    parsed = SimpleCookie()
                    parsed.load(value)
                    for name, morsel in parsed.items():
                        self.cookie[name] = morsel.value
                        require(bool(morsel["secure"]), "cookie_missing_secure")
                        require(bool(morsel["httponly"]), "cookie_missing_httponly")
                        require(morsel["path"] == "/" and not morsel["domain"], "host_cookie_scope")
            result = json.loads(raw) if raw and "json" in metadata.get("content-type", "") else {}
            require(isinstance(result, dict), "non_object_response")
            return result, metadata, raw
        finally:
            connection.close()

    def login(self, login: str, password: str) -> None:
        challenge, _, _ = self.request("GET", "/api/v1/auth/challenge")
        self.csrf = challenge["csrf_token"]
        session, _, _ = self.request(
            "POST", "/api/v1/session", {"login_name": login, "password": password}, expected=(201,)
        )
        self.csrf = session["csrf_token"]


class Proof:
    document_revision_id: str
    document_content_sha256: str
    manual_posting_fixture: dict[str, Any]
    reviewed_source_fixture: dict[str, Any]
    observation_fixture: dict[str, Any]
    board_posting_fixture: dict[str, Any]
    duplicate_fixture: dict[str, Any]

    def __init__(self, project: str, port: int, target: str, image: str, directory: Path) -> None:
        self.project, self.port, self.target, self.image = project, port, target, image
        self.directory = directory
        self.env = directory / "compose.env"
        self.origin = f"http://localhost:{port}"
        self.api = API(self.origin)
        self.login = "synthetic-owner"
        self.password = secrets.token_urlsafe(32)
        self.evidence: dict[str, Any] = {"project": project, "platform": target, "checks": {}}
        self.started = time.monotonic()
        self.created = False
        self.pdf_path: Path | None = None

    def compose(self, *arguments: str, data: bytes | None = None, timeout: int = 300) -> bytes:
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("BONAFIDES_", "COMPOSE_"))
        }
        environment.update(
            {
                "BONAFIDES_IMAGE": self.image,
                "DOCKER_DEFAULT_PLATFORM": self.target,
                "BONAFIDES_SECRET_DIR": str((self.directory / "secrets").resolve()),
                "BONAFIDES_ORIGIN": self.origin,
                "BONAFIDES_MODE": "benchmark",
                "BONAFIDES_PORT": str(self.port),
            }
        )
        return run(
            [
                "docker",
                "compose",
                "--env-file",
                str(self.env),
                "--project-name",
                self.project,
                *arguments,
            ],
            data=data,
            timeout=timeout,
            env=environment,
        )

    def checked(self, name: str) -> None:
        self.evidence["checks"][name] = {
            "passed": True,
            "elapsed_seconds": round(time.monotonic() - self.started, 3),
        }

    def sql(self, statement: str) -> str:
        return (
            self.compose(
                "exec",
                "-T",
                "db",
                "psql",
                "-U",
                "postgres",
                "-d",
                "bonafides",
                "-X",
                "-q",
                "-t",
                "-A",
                "-v",
                "ON_ERROR_STOP=1",
                data=statement.encode(),
            )
            .decode()
            .strip()
        )

    def app_python(self, source: str, *arguments: str) -> bytes:
        return self.compose("exec", "-T", "api", "python", "-c", source, *arguments)

    def wait_workflow(
        self, identifier: str, *, state: str | None = None, timeout: int = 240
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            workflow, _, _ = self.api.request("GET", f"/api/v1/workflows/{identifier}")
            if workflow["state"] == state or state is None and workflow["state"] in TERMINAL:
                return workflow
            if workflow["state"] in TERMINAL:
                code = workflow.get("last_error_code")
                if code is not None and (
                    not isinstance(code, str) or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code) is None
                ):
                    code = "invalid_error_code"
                attempt = workflow.get("attempt")
                diagnostic = {
                    "state": workflow["state"],
                    "error_code": code,
                    "attempt": attempt if isinstance(attempt, int) else None,
                }
                raise ProbeFailure(
                    "workflow_unexpected_terminal:" + json.dumps(diagnostic, sort_keys=True)
                )
            time.sleep(0.5)
        raise ProbeFailure("workflow_deadline")

    def enqueue(
        self, scenario: str, delay: int = 0, *, key: str | None = None
    ) -> tuple[dict[str, Any], dict[str, str], dict[str, Any]]:
        profile, metadata, _ = self.api.request("GET", "/api/v1/profile")
        body = {
            "profile_revision_id": profile["current_revision"]["id"],
            "scenario": scenario,
            "delay_seconds": delay,
        }
        headers = {"If-Match": metadata["etag"], "Idempotency-Key": key or secrets.token_hex(16)}
        workflow, _, _ = self.api.request(
            "POST", "/api/v1/benchmarks/tasks", body, expected=(202,), headers=headers
        )
        return workflow, headers, body

    def start(self, build: bool) -> None:
        require(not self.directory.exists(), "proof_directory_already_exists")
        for kind in ("container", "volume", "network"):
            existing = run(
                [
                    "docker",
                    kind,
                    "ls",
                    "--filter",
                    f"label=com.docker.compose.project={self.project}",
                    "--quiet",
                ]
            )
            require(not existing.strip(), "proof_namespace_already_exists")
        self.directory.mkdir(parents=True, mode=0o700)
        self.created = True
        initialize(self.directory / "secrets", self.env, self.project, self.port, True)
        if build:
            self.compose("build", "--pull", timeout=1800)
        self.compose("up", "-d", "--wait", "db", timeout=180)
        # This invokes the actual configured DB schema gate before migrations exist.
        self.compose(
            "run",
            "--rm",
            "--no-deps",
            "--entrypoint",
            "python",
            "api",
            "-c",
            "from bonafides.config import Settings\nfrom bonafides.db import Database\nfrom sqlalchemy import text\nfrom sqlalchemy.exc import DBAPIError\ndb=Database(Settings.load())\nwith db.engine.connect() as c: assert c.execute(text('SELECT 1')).scalar_one()==1\ntry: result=db.ready()\nexcept DBAPIError as e:\n assert getattr(e.orig,'sqlstate',None) in ('42P01','3F000'); result=False\nassert not result\n",
        )
        self.checked("unmigrated_schema_refused")
        for variable, value in (
            ("BONAFIDES_KEYRING_FILE", "/missing-keyring"),
            ("BONAFIDES_ORIGIN", "http://remote.invalid"),
        ):
            self.compose(
                "run",
                "--rm",
                "--no-deps",
                "-e",
                f"{variable}={value}",
                "--entrypoint",
                "python",
                "api",
                "-c",
                "from bonafides.config import Settings\nfrom bonafides.config import ConfigurationError\ntry: Settings.load()\nexcept ConfigurationError: pass\nelse: raise AssertionError('invalid configuration accepted')\n",
            )
        self.checked("missing_key_and_invalid_config_refused")
        self.compose("up", "-d", "--wait", timeout=240)
        self.api.request("GET", "/health/ready")
        self.checked("cold_start_actual_readiness")
        self.compose("exec", "-T", "api", "python", "-m", "bonafides", "init-bootstrap")

    def containment(self) -> None:
        private = json.loads(run(["docker", "network", "inspect", self.project + "_private"]))[0]
        require(private["Internal"], "private_network_allows_egress")
        sources = json.loads(run(["docker", "network", "inspect", self.project + "_sources"]))[0]
        require(sources["Internal"], "source_worker_network_allows_egress")
        identities = {}
        for service in ("api", "worker", "renderer", "parser", "db", "ingress", "source-egress"):
            identifier = self.compose("ps", "-q", service).decode().strip()
            info = json.loads(run(["docker", "inspect", identifier]))[0]
            config, host = info["Config"], info["HostConfig"]
            require(config["User"] not in ("", "0", "0:0", "root"), "service_runs_as_root")
            require(
                host["ReadonlyRootfs"] and "ALL" in [item.upper() for item in host["CapDrop"]],
                "service_root_or_caps_unconfined",
            )
            require(not host.get("Privileged") and not host.get("CapAdd"), "service_privileged")
            require(
                host["Memory"] > 0 and host["NanoCpus"] > 0 and host["PidsLimit"] > 0,
                "resource_limit_missing",
            )
            require(
                any("no-new-privileges" in item for item in host["SecurityOpt"]),
                "no_new_privileges_missing",
            )
            require(
                not any("docker.sock" in mount["Destination"] for mount in info["Mounts"]),
                "docker_socket_mounted",
            )
            networks = set(info["NetworkSettings"]["Networks"])
            if service in ("api", "worker", "db"):
                expected_networks = {self.project + "_private"}
                if service == "worker":
                    expected_networks.add(self.project + "_sources")
                require(networks == expected_networks, "private_service_network_escape")
                require(not host.get("PortBindings"), "private_service_host_publication")
            if service == "ingress":
                require(not info["Mounts"], "ingress_has_mounts")
                require(
                    not any(
                        item.startswith("BONAFIDES_") and item != "BONAFIDES_WEB_DIST=/app/web/dist"
                        for item in config["Env"]
                    ),
                    "ingress_secret_environment",
                )
                require(
                    config["Entrypoint"] == ["python", "-m", "bonafides.relay"]
                    and config["Cmd"] == ["api"],
                    "ingress_not_fixed_api_relay",
                )
                require(
                    networks == {self.project + "_private", self.project + "_ingress"},
                    "ingress_network_mismatch",
                )
                require(
                    host["PortBindings"]
                    == {"8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(self.port)}]},
                    "ingress_not_loopback_only",
                )
            if service == "source-egress":
                require(not info["Mounts"], "source_gateway_has_mounts")
                require(not host.get("PortBindings"), "source_gateway_host_publication")
                require(
                    networks == {self.project + "_sources", self.project + "_source-outbound"},
                    "source_gateway_network_escape",
                )
                require(
                    not any(
                        item.startswith("BONAFIDES_") and item != "BONAFIDES_WEB_DIST=/app/web/dist"
                        for item in config["Env"]
                    ),
                    "source_gateway_secret_environment",
                )
                require(
                    config["Entrypoint"] == ["python", "-m", "bonafides"]
                    and config["Cmd"] == ["source-gateway"],
                    "source_gateway_not_fixed_transport",
                )
            if service in {"renderer", "parser"}:
                require(host["NetworkMode"] == "none", f"{service}_has_network")
                spool = "/spool" if service == "renderer" else "/import-spool"
                require(
                    {mount["Destination"] for mount in info["Mounts"]} == {spool},
                    f"{service}_mount_escape",
                )
                require(
                    not any(
                        "DSN" in item or "KEYRING" in item or "BOOTSTRAP" in item
                        for item in config["Env"]
                    ),
                    f"{service}_secret_environment",
                )
            identities[service] = {
                "image": info["Image"],
                "uid": config["User"],
                "memory_bytes": host["Memory"],
                "pids": host["PidsLimit"],
                "nano_cpus": host["NanoCpus"],
            }
            observed = (
                self.compose(
                    "exec",
                    "-T",
                    service,
                    "sh",
                    "-ec",
                    "id -u; cat /sys/fs/cgroup/memory.max; cat /sys/fs/cgroup/pids.max; cat /sys/fs/cgroup/cpu.max; cat /proc/self/status",
                )
                .decode()
                .splitlines()
            )
            uid, memory, pids, cpu = observed[:4]
            status = dict(line.split(":", 1) for line in observed[4:] if ":" in line)
            require(
                int(uid) > 0 and int(memory) <= host["Memory"] and int(pids) <= host["PidsLimit"],
                "effective_cgroup_or_uid_missing",
            )
            quota, period = cpu.split()
            require(
                quota != "max" and 0 < int(quota) / int(period) <= host["NanoCpus"] / 1_000_000_000,
                "effective_cpu_limit_missing",
            )
            require(
                int(status["CapEff"].strip(), 16) == 0 and status["NoNewPrivs"].strip() == "1",
                "effective_privilege_escape",
            )
            identities[service]["effective"] = {
                "uid": uid,
                "memory_max": memory,
                "pids_max": pids,
                "cpu_max": cpu,
                "cap_eff": status["CapEff"].strip(),
                "no_new_privileges": True,
            }
        self.evidence["containers"] = identities
        dbid = self.compose("ps", "-q", "db").decode().strip()
        dbinfo = json.loads(run(["docker", "inspect", dbid]))[0]
        db_ip = next(iter(dbinfo["NetworkSettings"]["Networks"].values()))["IPAddress"]
        for service in ("renderer", "parser", "source-egress"):
            canary = json.loads(
                self.compose(
                    "exec",
                    "-T",
                    service,
                    "python",
                    "-m",
                    "bonafides.render_service",
                    "canary",
                    "/run/bonafides/keyring",
                    db_ip,
                    "5432",
                )
            )
            require(
                canary.get("file_blocked") and canary.get("network_blocked"),
                f"{service}_os_canary_failed",
            )
        self.app_python(
            "from bonafides.config import Settings; from bonafides.db import Database; from sqlalchemy import text\ndb=Database(Settings.load())\nwith db.transaction() as c:\n assert not c.execute(text(\"SELECT has_schema_privilege(current_user,'bonafides','CREATE')\")).scalar_one()\n assert not c.execute(text(\"SELECT rolsuper OR rolcreatedb OR rolcreaterole FROM pg_roles WHERE rolname=current_user\")).scalar_one()\n"
        )
        self.app_python(
            "from bonafides.config import Settings\nfrom bonafides.db import Database\nfrom sqlalchemy import text\nfrom sqlalchemy.exc import DBAPIError\ndb=Database(Settings.load())\ntry:\n with db.transaction() as c: c.execute(text('CREATE TABLE bonafides.proof_forbidden_ddl(id integer)'))\nexcept DBAPIError as e: assert getattr(e.orig,'sqlstate',None)=='42501'\nelse: raise AssertionError('runtime DDL succeeded')\n"
        )
        self.checked("runtime_role_and_renderer_os_containment")

    def identity(self) -> None:
        self.api.request("GET", "/api/v1/session", expected=(401,))
        challenge, _, _ = self.api.request("GET", "/api/v1/auth/challenge")
        self.api.csrf = challenge["csrf_token"]
        token = (self.directory / "secrets" / "bootstrap_token").read_text().strip()
        body = {"bootstrap_token": token, "login_name": self.login, "password": self.password}
        self.api.request(
            "POST",
            "/api/v1/bootstrap",
            body,
            expected=(403,),
            headers={
                "Origin": "https://attacker.invalid",
                "X-Forwarded-Host": "localhost",
                "X-Forwarded-Proto": "https",
            },
        )
        clients = [API(self.origin), API(self.origin)]
        for client in clients:
            client.cookie, client.csrf = dict(self.api.cookie), self.api.csrf

        def bootstrap(client: API) -> tuple[dict[str, Any], dict[str, str], bytes]:
            return client.request("POST", "/api/v1/bootstrap", body, expected=(201, 401, 403, 409))

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(bootstrap, clients))
        winners = [
            (client, outcome[0])
            for client, outcome in zip(clients, outcomes, strict=True)
            if "owner_id" in outcome[0]
        ]
        require(
            len(winners) == 1 and self.sql("SELECT count(*) FROM bonafides.owners") == "1",
            "bootstrap_not_single_owner",
        )
        self.api, session = winners[0]
        self.api.csrf = session["csrf_token"]
        self.api.request("GET", "/api/v1/session")
        self.api.request(
            "PUT", "/api/v1/profile", {}, expected=(403,), headers={"X-CSRF-Token": "incorrect"}
        )
        self.checked("bootstrap_concurrency_origin_csrf_and_secure_cookie")

    def profiles_and_documents(self) -> None:
        profile, metadata, _ = self.api.request("GET", "/api/v1/profile")
        snapshot = profile["current_revision"]["snapshot"]
        for header, expected in (
            ({}, (428,)),
            ({"If-Match": "1"}, (400, 422)),
            ({"If-Match": '"999999"'}, (409, 412)),
        ):
            self.api.request(
                "PUT",
                "/api/v1/profile",
                snapshot,
                expected=expected,
                headers={**header, "Idempotency-Key": secrets.token_hex(16)},
            )
        self.api.request(
            "POST",
            "/api/v1/benchmarks/profile",
            {"fixture": "SYN-CORE"},
            expected=(200, 201),
            headers={"If-Match": metadata["etag"], "Idempotency-Key": secrets.token_hex(16)},
        )
        seeded, metadata, _ = self.api.request("GET", "/api/v1/profile")
        edited = dict(
            seeded["current_revision"]["snapshot"], headline="Synthetic platform engineer"
        )
        self.api.request(
            "PUT",
            "/api/v1/profile",
            edited,
            headers={"If-Match": metadata["etag"], "Idempotency-Key": secrets.token_hex(16)},
        )
        reloaded, _, _ = self.api.request("GET", "/api/v1/profile")
        require(
            reloaded["current_revision"]["snapshot"] == edited
            and reloaded["current_revision"]["id"] != seeded["current_revision"]["id"],
            "profile_edit_reload_not_durable",
        )
        workflow, headers, body = self.enqueue("normal")
        replay, _, _ = self.api.request(
            "POST", "/api/v1/benchmarks/tasks", body, expected=(202,), headers=headers
        )
        require(replay["id"] == workflow["id"], "lost_response_replay_duplicate")
        changed = dict(body, scenario="poison")
        self.api.request(
            "POST", "/api/v1/benchmarks/tasks", changed, expected=(409,), headers=headers
        )
        result = self.wait_workflow(workflow["id"])
        require(result["state"] == "succeeded", "normal_workflow_failed")
        self.document_id = result["result"]["document_id"]
        self.review_and_render_current()
        self.checked("profile_preconditions_idempotency_grounded_document_review_pdf")

    def review_and_render_current(self) -> None:
        document_id = self.document_id
        document, metadata, _ = self.api.request("GET", f"/api/v1/documents/{document_id}")
        revision = document["current_revision"]
        require(not document["unsupported_claims"], "unsupported_document_claim")
        if hasattr(self, "document_revision_id"):
            require(revision["id"] != self.document_revision_id, "generation_revision_not_replaced")
            require(not document["reviewed"], "new_revision_retained_old_review")
            stale_review, _, _ = self.api.request(
                "POST",
                f"/api/v1/documents/{document_id}/review",
                {
                    "revision_id": self.document_revision_id,
                    "decision": "accepted",
                    "content_sha256": self.document_content_sha256,
                },
                expected=(409,),
                headers={"If-Match": metadata["etag"], "Idempotency-Key": secrets.token_hex(16)},
            )
            require(stale_review.get("code") == "revision_mismatch", "stale_review_not_rejected")
            for revision_id, error_code in (
                (self.document_revision_id, "revision_mismatch"),
                (revision["id"], "review_required"),
            ):
                rejected, _, _ = self.api.request(
                    "POST",
                    f"/api/v1/documents/{document_id}/renders",
                    {
                        "revision_id": revision_id,
                        "content_sha256": revision["content_sha256"],
                        "format": "pdf",
                    },
                    expected=(409,),
                    headers={
                        "If-Match": metadata["etag"],
                        "Idempotency-Key": secrets.token_hex(16),
                    },
                )
                require(
                    rejected.get("code") == error_code, "stale_or_missing_review_render_accepted"
                )
        review_headers = {"If-Match": metadata["etag"], "Idempotency-Key": secrets.token_hex(16)}
        self.api.request(
            "POST",
            f"/api/v1/documents/{document_id}/review",
            {
                "revision_id": revision["id"],
                "decision": "accepted",
                "content_sha256": revision["content_sha256"],
            },
            expected=(200, 201),
            headers=review_headers,
        )
        reviewed, metadata, _ = self.api.request("GET", f"/api/v1/documents/{document_id}")
        require(
            reviewed["reviewed"] and reviewed["current_revision"] == revision,
            "current_revision_review_not_durable",
        )
        render, _, _ = self.api.request(
            "POST",
            f"/api/v1/documents/{document_id}/renders",
            {
                "revision_id": revision["id"],
                "content_sha256": revision["content_sha256"],
                "format": "pdf",
            },
            expected=(202,),
            headers={"If-Match": metadata["etag"], "Idempotency-Key": secrets.token_hex(16)},
        )
        rendered = self.wait_workflow(render["id"])
        require(rendered["state"] == "succeeded", "render_workflow_failed")
        self.save_pdf(rendered)
        self.document_revision_id = revision["id"]
        self.document_content_sha256 = revision["content_sha256"]

    def save_pdf(self, rendered: dict[str, Any]) -> None:
        _, _, pdf = self.api.request(
            "GET", f"/api/v1/artifacts/{rendered['result']['artifact_id']}"
        )
        artifact_id = rendered["result"]["artifact_id"]
        require(pdf.startswith(b"%PDF-"), "artifact_not_pdf")
        from pypdf import PdfReader

        text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)
        require(
            "Maintained Linux batch processing runbooks" in text
            and "Wrote SQL queries for incident analysis" in text,
            "pdf_missing_grounded_text",
        )
        require("Kubernetes" not in text and "45%" not in text, "pdf_invented_unsupported_fact")
        pdf_path = self.directory / "reviewed-document.pdf"
        descriptor, temporary = tempfile.mkstemp(prefix=".reviewed-document-", dir=self.directory)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(pdf)
            if self.pdf_path is None:
                # Exclusive first publication cannot replace a user-created file.
                os.link(temporary, pdf_path)
            else:
                # Only this run's controlled proof file is atomically replaced.
                os.replace(temporary, pdf_path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        self.pdf_path = pdf_path
        self.artifact_id = artifact_id
        self.evidence["pdf"] = {
            "sha256": hashlib.sha256(pdf).hexdigest(),
            "bytes": len(pdf),
            "file": pdf_path.name,
            "workflow_id": rendered["id"],
        }

    def concurrent_claims(self) -> None:
        from bonafides.worker import Worker

        self.compose("up", "-d", "--scale", "worker=2", "worker")
        require(
            len(self.compose("ps", "--status", "running", "-q", "worker").splitlines()) == 2,
            "two_worker_processes_not_running",
        )
        # Saturate one process's slots; two jobs alone need not reach two workers.
        tasks = [self.enqueue("slow", 20)[0] for _ in range(Worker.CONCURRENCY + 1)]
        identifiers = ",".join(f"'{workflow['id']}'" for workflow in tasks)
        for workflow in tasks:
            self.wait_workflow(workflow["id"], state="running")
        claims = json.loads(
            self.sql(
                "SELECT json_agg(json_build_object('id',id,'holder',lease_holder,'attempt',attempt)) "
                f"FROM bonafides.workflow_runs WHERE id IN ({identifiers}) "
                "AND state='running'"
            )
        )
        require(
            claims is not None
            and len(claims) == len(tasks)
            and len({item["holder"] for item in claims if item["holder"]}) == 2
            and all(item["attempt"] == 1 for item in claims),
            "concurrent_claims_not_distinct",
        )
        results = [self.wait_workflow(workflow["id"]) for workflow in tasks]
        require(
            all(item["state"] == "succeeded" for item in results)
            and len({item["result"]["revision_id"] for item in results}) == len(tasks)
            and all(item["result"]["document_id"] == self.document_id for item in results),
            "concurrent_work_not_published_once_per_claim",
        )
        self.review_and_render_current()
        self.compose("up", "-d", "--scale", "worker=1", "worker")
        self.checked("two_live_workers_distinct_concurrent_claims_and_grounded_publication")

    def crash_and_failure(self) -> None:
        for scenario in ("slow", "stage_pause", "blob_stage"):
            if scenario == "blob_stage":
                raw = self.app_python(
                    "import json,sys\nfrom uuid import UUID\nfrom sqlalchemy import text\nfrom bonafides.config import Settings\nfrom bonafides.db import Database\nfrom bonafides.workflows import enqueue,get_workflow\nsettings=Settings.load(); assert settings.mode=='benchmark'\ndb=Database(settings)\nwith db.transaction() as c:\n owner=c.execute(text('SELECT id FROM bonafides.owners')).scalar_one()\n identifier=enqueue(c,owner,'pdf_render',{'document_id':sys.argv[1],'revision_id':sys.argv[2],'scenario':'stage_pause','delay_seconds':10})\n print(json.dumps(get_workflow(c,owner,identifier)))\n",
                    self.document_id,
                    self.document_revision_id,
                )
                workflow = json.loads(raw)
            else:
                workflow, _, _ = self.enqueue(scenario, 10)
            identifier = workflow["id"]
            self.wait_workflow(identifier, state="running")
            if scenario in {"stage_pause", "blob_stage"}:
                phase = "blob_staged" if scenario == "blob_stage" else "prepared"
                deadline = time.monotonic() + 60
                while (
                    self.sql(
                        f"SELECT count(*) FROM bonafides.workflow_steps WHERE workflow_run_id='{identifier}' AND step_key='staged' AND state='succeeded' AND checkpoint->>'phase'='{phase}'"
                    )
                    != "1"
                ):
                    require(time.monotonic() < deadline, "staged_checkpoint_missing")
                    time.sleep(0.25)
            old = self.sql(
                f"SELECT json_build_object('owner_id',owner_id,'id',id,'holder',lease_holder,'fence',fence,'attempt',attempt,'kind',kind,'input',input) FROM bonafides.workflow_runs WHERE id='{identifier}'"
            )
            self.compose("kill", "--signal", "SIGKILL", "worker")
            self.compose("up", "-d", "worker")
            result = self.wait_workflow(identifier)
            require(
                result["state"] == "succeeded" and result["attempt"] >= 2,
                "crashed_work_not_reclaimed",
            )
            self.app_python(
                "import json,sys\nfrom uuid import UUID\nfrom bonafides.config import Settings\nfrom bonafides.db import Database\nfrom bonafides.workflows import Claim,publish\nfrom sqlalchemy.exc import DBAPIError\nv=json.loads(sys.argv[1]); task=Claim(UUID(v['owner_id']),UUID(v['id']),UUID(v['holder']),v['fence'],v['attempt'],v['kind'],v['input'])\ndb=Database(Settings.load())\ntry:\n with db.transaction() as c: publish(c,task,lambda connection: {'stale_should_never_publish':True})\nexcept DBAPIError as e:\n assert getattr(e.orig,'sqlstate',None)=='55000'\nelse: raise AssertionError('stale fence accepted')\n",
                old,
            )
            self.checked("crash_reclaim_stale_fence_" + scenario)
            if scenario == "blob_stage":
                require(
                    result["result"]["artifact_id"] == self.artifact_id
                    and self.sql(
                        f"SELECT count(*) FROM bonafides.document_artifacts WHERE document_revision_id='{self.document_revision_id}' AND format='pdf'"
                    )
                    == "1",
                    "blob_recovery_duplicate_artifact",
                )
                self.save_pdf(result)
                self.checked("blob_stage_reclaimed_single_artifact_fenced_late_write_pdf")
            else:
                require(
                    result["result"]["document_id"] == self.document_id,
                    "recovered_generation_changed_draft",
                )
                self.review_and_render_current()
                require(
                    self.document_revision_id == result["result"]["revision_id"],
                    "recovered_generation_not_current",
                )
                self.checked("crash_reclaim_current_review_grounded_pdf_" + scenario)
        workflow, _, _ = self.enqueue("slow", 10)
        current = self.wait_workflow(workflow["id"], state="running")
        self.api.request(
            "POST",
            f"/api/v1/workflows/{workflow['id']}/cancel",
            {},
            expected=(200, 202),
            headers={
                "If-Match": f'"{current["version"]}"',
                "Idempotency-Key": secrets.token_hex(16),
            },
        )
        require(self.wait_workflow(workflow["id"])["state"] == "cancelled", "cancel_not_terminal")
        poison, _, _ = self.enqueue("poison")
        require(
            self.wait_workflow(poison["id"])["state"] == "permanent_failure", "poison_not_bounded"
        )
        self.checked("cancellation_and_poison_bound")

    def outbox_duplicate(self) -> None:
        self.compose("stop", "worker")
        self.app_python(
            "from uuid import uuid4\nfrom sqlalchemy import text\nfrom bonafides.config import Settings\nfrom bonafides.db import Database\nfrom bonafides.commands import append_outbox\nfrom bonafides.workflows import claim_outbox,deliver_outbox\ndb=Database(Settings.load())\nwith db.transaction() as c:\n owner=c.execute(text('SELECT id FROM bonafides.owners')).scalar_one()\n event_id=append_outbox(c,owner,'proof.duplicate','owner',owner,{'synthetic':True})\nevent=None\nfor unused in range(1000):\n with db.transaction() as c: candidate=claim_outbox(c,uuid4())\n if candidate is None: break\n with db.transaction() as c: assert deliver_outbox(c,candidate)\n if candidate.id==event_id: event=candidate; break\nassert event is not None\nwith db.transaction() as c:\n assert not deliver_outbox(c,event)\n assert c.execute(text('SELECT count(*) FROM bonafides.event_receipts WHERE event_id=:id'),{'id':event_id}).scalar_one()==1\n assert c.execute(text('SELECT count(*) FROM bonafides.event_metadata WHERE event_id=:id'),{'id':event_id}).scalar_one()==1\n"
        )
        self.compose("start", "worker")
        self.checked("actual_outbox_duplicate_single_receipt_and_effect")

    def throttle_restart(self) -> None:
        challenger = API(self.origin)
        for _unused in range(5):
            challenge, _, _ = challenger.request("GET", "/api/v1/auth/challenge")
            challenger.csrf = challenge["csrf_token"]
            challenger.request(
                "POST",
                "/api/v1/session",
                {"login_name": self.login, "password": "Incorrect synthetic secret 9999"},
                expected=(401,),
            )
        challenge, _, _ = challenger.request("GET", "/api/v1/auth/challenge")
        challenger.csrf = challenge["csrf_token"]
        challenger.request(
            "POST",
            "/api/v1/session",
            {"login_name": self.login, "password": self.password},
            expected=(429,),
        )
        self.compose("restart", "api")
        self.compose("up", "-d", "--wait", "api", timeout=180)
        challenge, _, _ = challenger.request("GET", "/api/v1/auth/challenge")
        challenger.csrf = challenge["csrf_token"]
        challenger.request(
            "POST",
            "/api/v1/session",
            {"login_name": self.login, "password": self.password},
            expected=(429,),
            headers={"X-Forwarded-For": "203.0.113.77"},
        )
        # Advance the stored test-only clock window, not a production bypass; the
        # actual throttle survives restart and spoofed headers before this reset.
        self.sql(
            "UPDATE bonafides.auth_throttles SET window_started_at=clock_timestamp()-interval '16 minutes',blocked_until=clock_timestamp()-interval '1 second';"
        )
        self.api.login(self.login, self.password)
        self.checked("persistent_auth_throttle_restart_and_forwarded_header_rejection")

    def browser(self) -> None:
        from playwright.sync_api import expect, sync_playwright

        saved, _, _ = self.api.request("GET", "/api/v1/profile")
        snapshot = saved["current_revision"]["snapshot"]

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(base_url=self.origin)
            page = context.new_page()
            page.goto(self.origin)
            page.get_by_label("Login name", exact=True).fill(self.login)
            page.get_by_label("Password", exact=True).fill(self.password)
            page.get_by_role("button", name="Sign in", exact=True).click()
            page.get_by_role("link", name="Profile", exact=True).click()
            expect(page.get_by_label("Display name", exact=True)).to_be_visible()
            expect(page.get_by_label("Highlight / tagline (optional)", exact=True)).to_be_visible()
            expect(page.get_by_label("Display name", exact=True)).to_have_value(
                snapshot["display_name"]
            )
            expect(page.get_by_label("Highlight / tagline (optional)", exact=True)).to_have_value(
                snapshot["headline"]
            )
            expect(page.get_by_label("Description (optional)", exact=True)).to_be_visible()
            expect(page.get_by_label("Description (optional)", exact=True)).to_have_value(
                snapshot.get("description", "")
            )
            require(
                any(
                    cookie["name"] == "__Host-bonafides_session"
                    and cookie["secure"]
                    and cookie["httpOnly"]
                    for cookie in context.cookies()
                ),
                "browser_secure_session_missing",
            )
            description = "Synthetic description saved through the browser."
            page.get_by_label("Description (optional)", exact=True).fill(description)
            with page.expect_response(
                lambda response: (
                    response.url == f"{self.origin}/api/v1/profile"
                    and response.request.method == "PUT"
                )
            ) as saved_response:
                page.get_by_role("button", name="Save profile", exact=True).click()
            require(saved_response.value.status == 200, "browser_description_save_failed")
            persisted, _, _ = self.api.request("GET", "/api/v1/profile")
            require(
                persisted["current_revision"]["snapshot"].get("description") == description,
                "browser_description_not_persisted",
            )
            # The API commit can precede the editor releasing its unsaved-change guard.
            expect(
                page.get_by_role("button", name="Discard identity changes", exact=True)
            ).to_have_count(0)
            page.reload()
            expect(page.get_by_label("Display name", exact=True)).to_be_visible()
            expect(page.get_by_label("Highlight / tagline (optional)", exact=True)).to_be_visible()
            expect(page.get_by_label("Display name", exact=True)).to_have_value(
                snapshot["display_name"]
            )
            expect(page.get_by_label("Highlight / tagline (optional)", exact=True)).to_have_value(
                snapshot["headline"]
            )
            expect(page.get_by_label("Description (optional)", exact=True)).to_be_visible()
            expect(page.get_by_label("Description (optional)", exact=True)).to_have_value(
                description
            )
            browser.close()
        self.checked("actual_chromium_secure_localhost_login_and_reload")

    def restart_and_revocation(self) -> None:
        self.compose("restart", "api", "worker", "db", timeout=180)
        self.compose("up", "-d", "--wait", timeout=180)
        self.api.request("GET", "/api/v1/session")
        self.api.request("GET", "/api/v1/profile")
        old = API(self.origin)
        old.cookie, old.csrf = dict(self.api.cookie), self.api.csrf
        replacement = secrets.token_urlsafe(32)
        session, _, _ = self.api.request(
            "POST",
            "/api/v1/session/password",
            {"current_password": self.password, "new_password": replacement},
            expected=(201,),
        )
        self.password, self.api.csrf = replacement, session["csrf_token"]
        old.request("GET", "/api/v1/session", expected=(401,))
        self.sql(
            "UPDATE bonafides.sessions SET created_at=clock_timestamp()-interval '8 days',expires_at=clock_timestamp()-interval '1 second' WHERE revoked_at IS NULL;"
        )
        self.api.request("GET", "/api/v1/session", expected=(401,))
        self.api = API(self.origin)
        self.api.login(self.login, self.password)
        self.checked("restart_persistence_password_revocation_and_expiry")

    def source_backup_fixture(self) -> None:
        profile, _, _ = self.api.request("GET", "/api/v1/profile")
        self.manual_posting_fixture, posting_headers, _ = self.api.request(
            "POST",
            "/api/v1/postings",
            {
                "title": "Fictional retained restore opportunity",
                "company": "BonaFides recovery fixture",
                "location_text": "Local fictional fixture",
                "description_text": "Complete original source text retained across isolated restore.",
                "canonical_url": None,
            },
            expected=(201,),
            headers={"Idempotency-Key": secrets.token_hex(16)},
        )
        self.manual_posting_fixture, _, _ = self.api.request(
            "PUT",
            f"/api/v1/postings/{self.manual_posting_fixture['id']}/disposition",
            {"disposition": "saved"},
            headers={
                "If-Match": posting_headers["etag"],
                "Idempotency-Key": secrets.token_hex(16),
            },
        )
        self.reviewed_source_fixture, source_headers, _ = self.api.request(
            "POST",
            "/api/v1/sources",
            {
                "source_kind": "greenhouse",
                "board_token": "bonafides-recovery-fixture",
                "company": "BonaFides recovery fixture",
                "terms_url": "https://terms.bonafides-fixture.org/recovery",
                "terms_revision": "Fictional local recovery fixture only",
                "terms_review_acknowledged": True,
                "enabled": False,
                "request_limit_per_minute": 1,
            },
            expected=(201,),
            headers={"Idempotency-Key": secrets.token_hex(16)},
        )
        # Synthetic materialization only: no board is enabled or contacted by this
        # nonempty recovery fixture. Adapter transport qualification is separate.
        self.board_posting_fixture = json.loads(
            self.app_python(
                "import hashlib,json,sys\n"
                "from uuid import UUID,uuid4\n"
                "from bonafides.config import Settings\n"
                "from bonafides.db import Database\n"
                "from bonafides.catalog import get_posting\n"
                "from bonafides.sources import _source_row,_save_job\n"
                "db=Database(Settings.load())\n"
                "with db.transaction() as c:\n"
                " from sqlalchemy import text\n"
                " owner=c.execute(text('SELECT owner_id FROM bonafides.source_configs WHERE id=:id'),{'id':UUID(sys.argv[1])}).scalar_one()\n"
                " source=_source_row(c,owner,UUID(sys.argv[1]))\n"
                " anchor=get_posting(c,owner,UUID(sys.argv[2]))\n"
                " identifier=uuid4(); description='Retained synthetic cross-source recovery posting.'\n"
                " _save_job(c,owner,source['id'],'42',identifier,anchor['title'],anchor['company'],anchor['location_text'],description,None,hashlib.sha256(description.encode()).hexdigest(),{'title':anchor['title'],'content':description},'synthetic:recovery-fixture',source['policy_version'],source['current_policy_review_id'])\n"
                " print(json.dumps(get_posting(c,owner,identifier)))\n"
                "db.close()\n",
                self.reviewed_source_fixture["id"],
                self.manual_posting_fixture["id"],
            )
        )
        self.manual_posting_fixture, _, _ = self.api.request(
            "POST",
            f"/api/v1/postings/{self.manual_posting_fixture['id']}/duplicates/{self.board_posting_fixture['id']}/decision",
            {"decision": "distinct"},
            headers={
                "If-Match": f'"{self.manual_posting_fixture["version"]}"',
                "Idempotency-Key": secrets.token_hex(16),
            },
        )
        self.board_posting_fixture, _, _ = self.api.request(
            "GET", f"/api/v1/postings/{self.board_posting_fixture['id']}"
        )
        self.duplicate_fixture, _, _ = self.api.request(
            "GET", f"/api/v1/postings/{self.manual_posting_fixture['id']}/duplicates"
        )
        require(
            len(self.duplicate_fixture["items"]) == 1
            and self.duplicate_fixture["items"][0]["decision"] == "distinct",
            "duplicate_recovery_fixture_not_reviewed",
        )
        self.reviewed_source_fixture, _, _ = self.api.request(
            "DELETE",
            f"/api/v1/sources/{self.reviewed_source_fixture['id']}",
            headers={
                "If-Match": source_headers["etag"],
                "Idempotency-Key": secrets.token_hex(16),
            },
        )
        self.observation_fixture, _, _ = self.api.request(
            "GET", f"/api/v1/postings/{self.manual_posting_fixture['id']}/observations"
        )
        require(
            self.reviewed_source_fixture["terms_reviewed_at"]
            and not self.reviewed_source_fixture["enabled"]
            and self.reviewed_source_fixture["removed_at"]
            and self.manual_posting_fixture["disposition"] == "saved"
            and len(self.observation_fixture["items"]) == 1,
            "source_restore_fixture_not_populated",
        )
        unchanged, _, _ = self.api.request("GET", "/api/v1/profile")
        require(unchanged == profile, "source_fixture_mutated_profile")
        self.checked("saved_posting_retired_reviewed_source_and_profile_isolation")

    def restore(self) -> None:
        self.compose("stop", "ingress", "api", "worker", "lifecycle", "renderer", "parser")
        before = self.sql(_SNAPSHOT_COUNTS)
        authorization_before = json.loads(self.sql(_RESTORE_AUTHORIZATION_ROWS))
        dump = self.compose(
            "exec",
            "-T",
            "db",
            "pg_dump",
            "-U",
            "postgres",
            "-d",
            "bonafides",
            "--format=custom",
            "--no-owner",
            "--schema=bonafides",
            "--schema=bonafides_migrations",
        )
        require(0 < len(dump) < MAX_ARCHIVE, "dump_size_invalid")
        blob_code = "import sys,tarfile,pathlib\np=pathlib.Path('/data/blobs')\nwith tarfile.open(fileobj=sys.stdout.buffer,mode='w|') as t:\n for f in sorted(p.rglob('*')):\n  assert not f.is_symlink()\n  if f.is_file(): t.add(f,arcname=str(f.relative_to(p)),recursive=False)\n"
        blobs = self.compose(
            "run", "--rm", "--no-deps", "--entrypoint", "python", "api", "-c", blob_code
        )
        manifest = {
            "format": 1,
            "dump_sha256": hashlib.sha256(dump).hexdigest(),
            "blobs_sha256": hashlib.sha256(blobs).hexdigest(),
            "counts": json.loads(before),
        }
        validate_snapshot(dump, blobs, manifest)
        references = json.loads(
            self.sql(
                "SELECT coalesce(json_agg(json_build_object('path',owner_id::text || '/' || storage_key,'sha256',sha256,'byte_length',byte_length)),'[]') FROM bonafides.blobs WHERE state='ready'"
            )
        )
        hashes = blob_members(blobs)
        with tarfile.open(fileobj=io.BytesIO(blobs)) as archive:
            sizes = {member.name: member.size for member in archive}
        require(references, "snapshot_has_no_referenced_blobs")
        for reference in references:
            require(
                hashes.get(reference["path"]) == reference["sha256"]
                and sizes.get(reference["path"]) == reference["byte_length"],
                "snapshot_referenced_blob_missing_or_corrupt",
            )
        manifest["referenced_blob_count"] = len(references)
        try:
            validate_snapshot(dump + b"corrupt", blobs, manifest)
        except ProbeFailure:
            pass
        else:
            raise ProbeFailure("corrupt_snapshot_accepted")
        for member_name, member_type in (
            ("../escape", tarfile.REGTYPE),
            ("symlink", tarfile.SYMTYPE),
        ):
            hostile = io.BytesIO()
            with tarfile.open(fileobj=hostile, mode="w") as archive:
                member = tarfile.TarInfo(member_name)
                member.type = member_type
                member.linkname = "/run/bonafides/keyring" if member_type == tarfile.SYMTYPE else ""
                archive.addfile(member)
            try:
                blob_members(hostile.getvalue())
            except ProbeFailure:
                pass
            else:
                raise ProbeFailure("hostile_archive_member_accepted")
        target = Proof(
            self.project + "-restore",
            self.port + 1,
            self.target,
            self.image,
            self.directory / "restore",
        )
        for kind in ("container", "volume", "network"):
            require(
                not run(
                    [
                        "docker",
                        kind,
                        "ls",
                        "--filter",
                        f"label=com.docker.compose.project={target.project}",
                        "--quiet",
                    ]
                ).strip(),
                "restore_namespace_exists",
            )
        target.directory.mkdir(mode=0o700)
        initialize(target.directory / "secrets", target.env, target.project, target.port, True)
        keyring = target.directory / "secrets" / "keyring"
        keyring.unlink()
        private_write(keyring, (self.directory / "secrets" / "keyring").read_text().strip())
        target.compose("up", "-d", "--wait", "db", timeout=180)
        target.compose(
            "exec",
            "-T",
            "db",
            "pg_restore",
            "-U",
            "postgres",
            "-d",
            "bonafides",
            "--exit-on-error",
            "--single-transaction",
            "--clean",
            "--if-exists",
            "--no-owner",
            "--role=bonafides_migrator",
            data=dump,
        )
        target.sql(
            """BEGIN;
            UPDATE bonafides.owners SET auth_epoch=auth_epoch+1;
            UPDATE bonafides.sessions SET revoked_at=clock_timestamp() WHERE revoked_at IS NULL;
            DELETE FROM bonafides.auth_challenges;
            WITH revoked AS (
              UPDATE bonafides.action_approvals
              SET revoked_at=clock_timestamp(),version=version+1 WHERE revoked_at IS NULL
              RETURNING owner_id,id,workflow_run_id,intent
            )
            INSERT INTO bonafides.approval_audit_events
              (owner_id,approval_id,workflow_run_id,event,error_code)
            SELECT owner_id,id,workflow_run_id,'revoked','installation_restored'
            FROM revoked WHERE intent IS NOT NULL;
            UPDATE bonafides.hosted_egress_consents
              SET revoked_at=clock_timestamp() WHERE revoked_at IS NULL;
            DELETE FROM bonafides.oauth_transactions;
            UPDATE bonafides.workflow_runs SET state='cancelled',lease_holder=NULL,
              lease_expires_at=NULL,fence=fence+1,finished_at=clock_timestamp()
              WHERE state NOT IN ('succeeded','cancelled','permanent_failure');
            COMMIT;"""
        )
        authorization_after = json.loads(target.sql(_RESTORE_AUTHORIZATION_ROWS))
        for name in ("approvals", "consents"):
            require(
                authorization_before[name] == authorization_after[name],
                f"restore_{name}_history_changed",
            )
        require(
            authorization_before["audit"].items() <= authorization_after["audit"].items(),
            "restore_approval_audit_history_changed",
        )
        require(
            target.sql(
                "SELECT (SELECT count(*) FROM bonafides.action_approvals WHERE revoked_at IS NULL)"
                "+(SELECT count(*) FROM bonafides.hosted_egress_consents WHERE revoked_at IS NULL)"
            ).strip()
            == "0",
            "restored_approval_or_consent_not_revoked",
        )
        extract_code = "import sys,tarfile,pathlib\nroot=pathlib.Path('/data/blobs')\nwith tarfile.open(fileobj=sys.stdin.buffer,mode='r|') as t:\n for m in t:\n  p=pathlib.PurePosixPath(m.name); assert m.isfile() and not p.is_absolute() and '..' not in p.parts\n  dest=root.joinpath(*p.parts); dest.parent.mkdir(parents=True,exist_ok=True,mode=0o700)\n  incoming=t.extractfile(m); assert incoming is not None\n  with dest.open('xb') as out: out.write(incoming.read())\n  dest.chmod(0o600)\n"
        target.compose(
            "run",
            "--rm",
            "--no-deps",
            "--entrypoint",
            "python",
            "api",
            "-c",
            extract_code,
            data=blobs,
        )
        after = target.sql(_SNAPSHOT_COUNTS)
        require(json.loads(before) == json.loads(after), "restore_counts_mismatch")
        restored_blobs = target.compose(
            "run", "--rm", "--no-deps", "--entrypoint", "python", "api", "-c", blob_code
        )
        require(blob_members(blobs) == blob_members(restored_blobs), "restore_blob_hash_mismatch")
        # This explicitly disposable plaintext fixture needs a target-physical guard.
        # Do not clear the guard or weaken normal readiness to accept a copied identity.
        guard_handoff_code = """
import json
from pathlib import Path
from sqlalchemy import text
from bonafides.db import Database
from bonafides.execution_guard import ExecutionGuard
with ExecutionGuard(Path('/data/blobs'), exclusive=True, create=False) as guard:
    db = Database(Path('/run/migration/migrator_dsn').read_text().strip())
    try:
        with db.transaction() as conn:
            assert conn.execute(text('SELECT current_user')).scalar_one() == 'bonafides_migrator'
            previous = conn.execute(text(
                'SELECT execution_guard_id FROM bonafides.installation_state WHERE singleton FOR UPDATE'
            )).scalar_one()
            assert previous is not None and previous != guard.identity
            bound = conn.execute(text(
                'UPDATE bonafides.installation_state SET execution_guard_id=:guard '
                'WHERE singleton AND NOT maintenance_active AND execution_guard_id=:previous '
                'RETURNING execution_guard_id'
            ), {'guard': guard.identity, 'previous': previous}).scalar_one()
            assert bound == guard.verify()
        print(json.dumps({'offline_exclusive': True, 'source_guard_reused': False,
                          'previous': str(previous), 'target': str(guard.identity)}))
    finally:
        db.close()
"""
        guard_handoff = json.loads(
            target.compose(
                "run",
                "--rm",
                "--no-deps",
                "--volume",
                f"{target.project}_blobs:/data/blobs",
                "--entrypoint",
                "python",
                "migrate",
                "-c",
                guard_handoff_code,
            )
        )
        require(
            guard_handoff["offline_exclusive"] and not guard_handoff["source_guard_reused"],
            "restore_target_guard_not_handed_off",
        )
        target.compose("up", "-d", "--wait", "api", "ingress", timeout=180)
        stale = API(target.origin)
        stale.cookie, stale.csrf = dict(self.api.cookie), self.api.csrf
        stale.request("GET", "/api/v1/session", expected=(401,))
        target.api.login(self.login, self.password)
        target.api.request("GET", "/api/v1/profile")
        for path, expected in (
            (f"/api/v1/postings/{self.manual_posting_fixture['id']}", self.manual_posting_fixture),
            (f"/api/v1/postings/{self.board_posting_fixture['id']}", self.board_posting_fixture),
            (
                f"/api/v1/postings/{self.manual_posting_fixture['id']}/duplicates",
                self.duplicate_fixture,
            ),
            (f"/api/v1/sources/{self.reviewed_source_fixture['id']}", self.reviewed_source_fixture),
            (
                f"/api/v1/postings/{self.manual_posting_fixture['id']}/observations",
                self.observation_fixture,
            ),
        ):
            restored, _, _ = target.api.request("GET", path)
            require(restored == expected, "restored_source_posting_or_observation_differs")
        _, _, restored_pdf = target.api.request("GET", f"/api/v1/artifacts/{self.artifact_id}")
        require(
            hashlib.sha256(restored_pdf).hexdigest() == self.evidence["pdf"]["sha256"],
            "restored_authorized_pdf_differs",
        )
        network = json.loads(run(["docker", "network", "inspect", target.project + "_private"]))[0]
        require(network["Internal"], "restore_network_allows_egress")
        target.app_python(
            "import socket\ntry:\n connection=socket.create_connection(('1.1.1.1',443),timeout=2)\nexcept OSError: pass\nelse:\n connection.close(); raise AssertionError('restored API has outbound network')\n"
        )
        require(
            not target.compose("ps", "--status", "running", "-q", "worker").strip(),
            "restored_worker_started",
        )
        self.evidence["restore"] = {
            "project": target.project,
            "scope": "Disposable plaintext runtime-proof regression fixture, not an encrypted installation backup or encrypted backup and recovery drill",
            "target_guard_handoff": guard_handoff,
            "manifest": manifest,
            "authorization_counts": {
                name: len(rows) for name, rows in authorization_before.items()
            },
            "approval_and_consent_history_preserved": True,
            "approval_and_consent_revoked": True,
            "sessions_revoked": True,
            "workers_stopped": True,
            "private_network": True,
        }
        self.checked("quiesced_matched_dump_blob_isolated_restore_corrupt_refusal_and_revocation")


def blob_members(archive: bytes) -> dict[str, str]:
    require(len(archive) <= MAX_ARCHIVE, "archive_size_limit")
    result: dict[str, str] = {}
    total = 0
    with tarfile.open(fileobj=io.BytesIO(archive)) as contents:
        for member in contents:
            path = Path(member.name)
            require(
                member.isfile()
                and not path.is_absolute()
                and ".." not in path.parts
                and member.name not in result,
                "unsafe_archive_member",
            )
            total += member.size
            require(total <= MAX_ARCHIVE and len(result) < 10000, "archive_expansion_limit")
            stream = contents.extractfile(member)
            require(stream is not None, "archive_missing_member")
            assert stream is not None
            result[member.name] = hashlib.sha256(stream.read(MAX_ARCHIVE + 1)).hexdigest()
    return result


def validate_snapshot(dump: bytes, blobs: bytes, manifest: dict[str, Any]) -> None:
    require(
        manifest.get("format") == 1 and dump.startswith(b"PGDMP"), "unsupported_snapshot_format"
    )
    require(hashlib.sha256(dump).hexdigest() == manifest.get("dump_sha256"), "dump_hash_mismatch")
    require(
        hashlib.sha256(blobs).hexdigest() == manifest.get("blobs_sha256"),
        "blob_archive_hash_mismatch",
    )
    blob_members(blobs)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project", required=True, help="new namespace matching bonafides-proof-[a-z0-9-]+"
    )
    parser.add_argument(
        "--confirm-disposable",
        required=True,
        help="must repeat exact --project; permits crash/restore only there",
    )
    parser.add_argument("--platform", choices=("linux/arm64", "linux/amd64"), required=True)
    parser.add_argument("--port", type=int, default=18000)
    parser.add_argument("--image", default="bonafides:local")
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="use an already built immutable local image, still run all runtime checks",
    )
    parser.add_argument("--evidence-root", type=Path, default=Path(".local"))
    args = parser.parse_args()
    if (
        not re.fullmatch(r"bonafides-proof-[a-z0-9][a-z0-9-]{0,22}", args.project)
        or args.project != args.confirm_disposable
    ):
        parser.error("exact disposable namespace confirmation required")
    if not 1024 <= args.port <= 65534:
        parser.error("port and following restore port must be unprivileged valid ports")
    proof = Proof(
        args.project,
        args.port,
        args.platform,
        args.image,
        args.evidence_root.resolve() / args.project,
    )
    try:
        proof.start(not args.skip_build)
        engine = json.loads(run(["docker", "info", "--format", "{{json .Architecture}} "]))
        native = {"aarch64": "arm64", "x86_64": "amd64"}.get(engine, engine)
        proof.evidence["execution"] = {
            "docker_architecture": engine,
            "host_architecture": platform.machine(),
            "emulated": args.platform.split("/")[1] != native,
        }
        proof.containment()
        proof.identity()
        proof.profiles_and_documents()
        proof.concurrent_claims()
        proof.crash_and_failure()
        proof.outbox_duplicate()
        proof.browser()
        proof.restart_and_revocation()
        proof.throttle_restart()
        proof.source_backup_fixture()
        proof.restore()
        proof.evidence["passed"] = True
    except Exception as exc:
        proof.evidence["passed"] = False
        proof.evidence["failure"] = (
            str(exc) if isinstance(exc, ProbeFailure) else type(exc).__name__
        )
        trace = exc.__traceback__
        while trace is not None:
            if trace.tb_frame.f_code.co_filename == __file__:
                proof.evidence["failure_line"] = trace.tb_lineno
            trace = trace.tb_next
    if proof.created:
        receipt = proof.directory / "receipt.json"
        private_write(receipt, json.dumps(proof.evidence, indent=2))
        print(f"Redacted receipt: {receipt}")
    print(
        "Disposable runtime proof passed"
        if proof.evidence["passed"]
        else "Disposable runtime proof failed; original resources retained for inspection"
    )
    return 0 if proof.evidence["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
