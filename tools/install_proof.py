# SPDX-License-Identifier: MIT OR Apache-2.0
"""Prove the standalone installer on a disposable bonafides-proof-* namespace.

The installer script is extracted from the application image and run from a directory outside the
checkout in production mode. The proof times install to sign-in, creates an owner and synthetic data
through the production API, then reruns the installer unchanged and again after a same-version
image swap. Evidence is a redacted receipt: no token, password, secret digest or image layer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from tools.runtime_proof import API, ProbeFailure, require, require_unused_namespace, run

SIGN_IN_LIMIT_SECONDS = 600
_LABEL = "org.opencontainers.image.{}"
_STATE_SQL = """
SELECT json_build_object(
 'owners', (SELECT coalesce(json_agg(id::text ORDER BY id::text),'[]'::json) FROM bonafides.owners),
 'profile_revisions', (SELECT coalesce(json_agg(encode(sha256(convert_to(r::text,'UTF8')),'hex')
   ORDER BY r.id::text),'[]'::json) FROM bonafides.profile_revisions r),
 'ledger', (SELECT coalesce(json_agg(version::text || ':' || source_sha256 ORDER BY version),
   '[]'::json) FROM bonafides.schema_migrations),
 'alembic', (SELECT coalesce(json_agg(version_num),'[]'::json)
   FROM bonafides_migrations.alembic_version)
)
"""


class InstallProof:
    def __init__(self, project: str, port: int, platform: str, image: str, context: Path) -> None:
        self.project, self.port, self.platform, self.image = project, port, platform, image
        self.context = context
        self.origin = f"http://localhost:{port}"
        self.work = Path(tempfile.mkdtemp(prefix="bonafides-install-proof-"))
        # The database container (uid 70) must traverse this path to read the init script.
        self.work.chmod(0o755)
        self.install_dir = self.work / "install"
        self.script = self.work / "install.sh"
        self.swap_image = f"{project}:swap"
        self.swap_built = False
        self.login, self.password = "synthetic-owner", secrets.token_urlsafe(32)
        self.api = API(self.origin)
        self.evidence: dict[str, Any] = {"project": project, "platform": platform, "checks": {}}
        self.started = time.monotonic()
        self.token = ""

    def environment(self, image: str) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("BONAFIDES_", "COMPOSE_"))}
        env.update({"DOCKER_DEFAULT_PLATFORM": self.platform, "BONAFIDES_IMAGE": image})
        return env

    def compose(self, image: str, *arguments: str, data: bytes | None = None) -> bytes:
        return run(
            [
                "docker",
                "compose",
                "--project-directory",
                str(self.install_dir),
                "--env-file",
                str(self.install_dir / ".env"),
                "-f",
                str(self.install_dir / "compose.yaml"),
                *arguments,
            ],
            data=data,
            env=self.environment(image),
        )

    def checked(self, name: str, **details: Any) -> None:
        self.evidence["checks"][name] = {
            "passed": True,
            "elapsed_seconds": round(time.monotonic() - self.started, 3),
            **details,
        }

    def image_identity(self, image: str) -> dict[str, str]:
        info = json.loads(run(["docker", "image", "inspect", image]))[0]
        labels = info["Config"]["Labels"]
        identity = {"id": info["Id"], "architecture": f"{info['Os']}/{info['Architecture']}"}
        identity.update({key: labels[_LABEL.format(key)] for key in ("version", "revision")})
        return identity

    def extract_script(self, image: str) -> None:
        script = run(
            [
                "docker",
                "run",
                "--rm",
                "--pull",
                "never",
                "--network",
                "none",
                "--entrypoint",
                "cat",
                image,
                "/app/deploy/install.sh",
            ]
        )
        self.script.write_bytes(script)
        self.script.chmod(0o755)

    def install(self, image: str) -> float:
        """Run the installer from outside the checkout; return seconds until sign-in is served."""
        begun = time.monotonic()
        result = subprocess.run(
            [
                "/bin/sh",
                str(self.script),
                "--dir",
                str(self.install_dir),
                "--port",
                str(self.port),
                "--project",
                self.project,
                image,
            ],
            cwd=self.work,
            env=self.environment(image),
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        if result.returncode:
            raise ProbeFailure(
                "installer_failed:" + (result.stderr.strip().splitlines() or ["?"])[-1]
            )
        secret_dir = (self.install_dir / "secrets").resolve()
        self.token = (secret_dir / "bootstrap_token").read_text().strip()
        require(self.token not in result.stdout + result.stderr, "installer_printed_token")
        require(str(secret_dir) in result.stdout, "installer_omitted_secret_path")
        _, _, page = self.api.request("GET", "/")
        require(b'<div id="root">' in page, "sign_in_page_not_served")
        elapsed = time.monotonic() - begun
        return round(elapsed, 3)

    def state(self) -> dict[str, Any]:
        database: dict[str, Any] = json.loads(
            self.compose(
                self.image,
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
                data=_STATE_SQL.encode(),
            )
        )
        files = [*sorted((self.install_dir / "secrets").iterdir()), self.install_dir / ".env"]
        database["files"] = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in files
        }
        return database

    def create_owner(self) -> None:
        challenge, _, _ = self.api.request("GET", "/api/v1/auth/challenge")
        self.api.csrf = challenge["csrf_token"]
        session, _, _ = self.api.request(
            "POST",
            "/api/v1/bootstrap",
            {"bootstrap_token": self.token, "login_name": self.login, "password": self.password},
            expected=(201,),
        )
        self.api.csrf = session["csrf_token"]

    def create_owner_and_data(self) -> None:
        self.create_owner()
        profile, metadata, _ = self.api.request("GET", "/api/v1/profile")
        edited = dict(profile["current_revision"]["snapshot"], headline="Synthetic install owner")
        self.api.request(
            "PUT",
            "/api/v1/profile",
            edited,
            headers={"If-Match": metadata["etag"], "Idempotency-Key": secrets.token_hex(16)},
        )
        reloaded, _, _ = self.api.request("GET", "/api/v1/profile")
        require(
            reloaded["current_revision"]["snapshot"]["headline"] == "Synthetic install owner",
            "profile_not_durable",
        )

    def require_owner_intact(self) -> None:
        """The pre-existing owner session still works and a fresh sign-in succeeds."""
        profile, _, _ = self.api.request("GET", "/api/v1/profile")
        require(
            profile["current_revision"]["snapshot"]["headline"] == "Synthetic install owner",
            "owner_data_changed",
        )
        API(self.origin).login(self.login, self.password)

    def require_running(self, image: str) -> dict[str, str]:
        expected = self.image_identity(image)
        for service in ("api", "worker", "lifecycle", "ingress"):
            identifier = self.compose(image, "ps", "-q", service).decode().strip()
            require(bool(identifier), f"service_missing:{service}")
            info = json.loads(run(["docker", "inspect", identifier]))[0]
            require(info["State"]["Running"], f"service_not_running:{service}")
            require(info["Image"] == expected["id"], f"service_on_wrong_image:{service}")
            revision = info["Config"]["Labels"][_LABEL.format("revision")]
            require(revision == expected["revision"], f"service_on_wrong_revision:{service}")
        return expected

    def build_swap(self, first: dict[str, str]) -> dict[str, str]:
        """Same version, different revision label: a genuinely different image, not a retag."""
        revision = hashlib.sha1(f"{first['revision']}:swap".encode()).hexdigest()  # noqa: S324
        labels = json.loads(run(["docker", "image", "inspect", self.image]))[0]["Config"]["Labels"]
        run(
            [
                "docker",
                "build",
                "--target",
                "app",
                "--build-arg",
                f"VERSION={first['version']}",
                "--build-arg",
                f"REVISION={revision}",
                "--build-arg",
                f"SOURCE={labels[_LABEL.format('source')]}",
                "--tag",
                self.swap_image,
                str(self.context),
            ],
            env=self.environment(self.swap_image),
            timeout=1800,
        )
        self.swap_built = True
        swap = self.image_identity(self.swap_image)
        require(swap["version"] == first["version"], "swap_version_changed")
        require(swap["revision"] != first["revision"], "swap_revision_unchanged")
        require(swap["id"] != first["id"], "swap_image_identical")
        return swap

    def prove(self) -> None:
        require_unused_namespace(self.project)
        self.extract_script(self.image)
        first = self.image_identity(self.image)
        first_seconds = self.install(self.image)
        require(first_seconds < SIGN_IN_LIMIT_SECONDS, "install_to_sign_in_over_limit")
        require(self.require_running(self.image) == first, "first_image_not_running")
        before_owner = self.state()
        require(before_owner["owners"] == [] and len(before_owner["ledger"]) == 1, "fresh_state")
        self.checked(
            "fresh_install_reaches_sign_in",
            install_to_sign_in_seconds=first_seconds,
            limit_seconds=SIGN_IN_LIMIT_SECONDS,
        )
        self.create_owner_and_data()
        seeded = self.state()
        require(len(seeded["owners"]) == 1 and seeded["profile_revisions"], "owner_not_seeded")
        rerun_seconds = self.install(self.image)
        require(self.state() == seeded, "rerun_changed_credentials_data_or_ledger")
        self.require_owner_intact()
        self.checked("rerun_preserves_credentials_owner_data_ledger", seconds=rerun_seconds)
        swap = self.build_swap(first)
        self.extract_script(self.swap_image)
        swap_seconds = self.install(self.swap_image)
        require(self.require_running(self.swap_image) == swap, "swapped_image_not_running")
        require(self.state() == seeded, "swap_changed_credentials_data_or_ledger")
        self.require_owner_intact()
        self.checked(
            "same_version_image_swap_preserves_owner_data_ledger",
            seconds=swap_seconds,
            previous_revision=first["revision"],
            running_revision=swap["revision"],
        )
        self.evidence["images"] = {"first": first, "swapped": swap}
        self.evidence["ledger"] = seeded["ledger"]
        self.evidence["alembic"] = seeded["alembic"]

    def teardown(self) -> None:
        if (self.install_dir / "compose.yaml").exists():
            self.compose(self.swap_image, "down", "--volumes", "--remove-orphans")
        if self.swap_built:
            subprocess.run(
                ["docker", "image", "rm", self.swap_image], capture_output=True, check=False
            )
        shutil.rmtree(self.work, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="new bonafides-proof-[a-z0-9-]+ namespace")
    parser.add_argument("--confirm-disposable", required=True, help="must repeat exact --project")
    parser.add_argument("--platform", choices=("linux/arm64", "linux/amd64"), required=True)
    parser.add_argument("--port", type=int, default=18100)
    parser.add_argument(
        "--image", default="bonafides:local", help="locally built application image"
    )
    parser.add_argument(
        "--context", type=Path, default=Path("."), help="checkout for the swap build"
    )
    parser.add_argument("--evidence-root", type=Path, default=Path(".local"))
    args = parser.parse_args()
    if (
        not re.fullmatch(r"bonafides-proof-[a-z0-9][a-z0-9-]{0,22}", args.project)
        or args.project != args.confirm_disposable
    ):
        parser.error("exact disposable namespace confirmation required")
    if not 1024 <= args.port <= 65535:
        parser.error("port must be unprivileged")
    proof = InstallProof(args.project, args.port, args.platform, args.image, args.context.resolve())
    try:
        proof.prove()
    finally:
        proof.teardown()
    proof.evidence["total_seconds"] = round(time.monotonic() - proof.started, 3)
    receipt = args.evidence_root.resolve() / args.project / "receipt.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(proof.evidence, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"receipt": str(receipt), "checks": sorted(proof.evidence["checks"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
