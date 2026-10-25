# SPDX-License-Identifier: MIT OR Apache-2.0
from __future__ import annotations

import base64
import json
import os
import secrets
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import make_url

from offerloom.api import create_app
from offerloom.auth import (
    HASH_SLOTS,
    SESSION_COOKIE,
    check_password,
    hash_password,
    init_bootstrap,
    recover_password,
)
from offerloom.config import MigrationSettings, Settings, read_secret
from offerloom.crypto import Keyring
from offerloom.db import Database
from offerloom.problems import Problem
from offerloom.workflows import migrate

PASSWORD = "Synthetic proof phrase 817!"
NEW_PASSWORD = "Distinct recovery phrase 924!"


def _test_dsn(name: str, fallback: str | None = None) -> str:
    value = os.environ.get(name)
    path = os.environ.get(name + "_FILE")
    if path:
        return read_secret(Path(path), name)
    if value:
        return value
    if fallback:
        return _test_dsn(fallback)
    pytest.fail(
        "PostgreSQL acceptance requires a disposable offerloom_test database: set OFFERLOOM_TEST_MIGRATION_DSN and OFFERLOOM_TEST_RUNTIME_DSN (or OFFERLOOM_TEST_DSN). No SQLite or silent skip is permitted."
    )


@dataclass
class IdentityContext:
    settings: Settings
    admin: Database
    token: str
    login_name: str

    def client(self) -> TestClient:
        return TestClient(create_app(self.settings), base_url=self.settings.origin)


@pytest.fixture
def identity(tmp_path: Path) -> Iterator[IdentityContext]:
    migration_dsn = _test_dsn("OFFERLOOM_TEST_MIGRATION_DSN", "OFFERLOOM_TEST_DSN")
    runtime_dsn = _test_dsn("OFFERLOOM_TEST_RUNTIME_DSN", "OFFERLOOM_TEST_DSN")
    database = make_url(migration_dsn).database or ""
    if not (
        database == "offerloom_test" or database.startswith("test_") or database.endswith("_test")
    ):
        pytest.fail("Identity tests require an explicitly named disposable test database.")
    migrate(MigrationSettings(migration_dsn))
    admin = Database(migration_dsn)
    with admin.transaction() as conn:
        if conn.execute(text("SELECT id FROM offerloom.owners")).first() is not None:
            pytest.fail(
                "Identity singleton tests require an empty disposable test database; refusing to remove an existing owner."
            )
        conn.execute(text("DELETE FROM offerloom.auth_challenges"))
        conn.execute(text("DELETE FROM offerloom.auth_throttles"))
    blob, spool, import_spool = (
        tmp_path / "blobs",
        tmp_path / "spool",
        tmp_path / "import-spool",
    )
    for directory in (blob, spool, import_spool):
        directory.mkdir()
    token = secrets.token_urlsafe(32)
    token_file = tmp_path / "bootstrap-token"
    token_file.write_text(token)
    token_file.chmod(0o600)
    settings = Settings(
        database_dsn=runtime_dsn,
        migration_dsn=migration_dsn,
        keyring=Keyring.parse(
            json.dumps(
                {
                    "active_key_id": "test",
                    "keys": {"test": base64.b64encode(secrets.token_bytes(32)).decode()},
                }
            )
        ),
        origin="https://localhost",
        blob_root=blob,
        renderer_spool=spool,
        import_spool=import_spool,
        bootstrap_token_file=token_file,
        mode="benchmark",
        web_dist=tmp_path / "unbuilt-ui",
    )
    context = IdentityContext(settings, admin, token, "identity-" + uuid4().hex)
    init_bootstrap(admin, settings)
    try:
        yield context
    finally:
        with admin.transaction() as conn:
            # RESTRICT preserves evidence pins; remove this fixture's links before its owner.
            conn.execute(
                text("""UPDATE offerloom.local_model_reservations SET owner_id=NULL,
                workflow_run_id=NULL,fence=NULL,reserved_until='-infinity'
                WHERE owner_id=(SELECT id FROM offerloom.owners WHERE login_name=:login)"""),
                {"login": context.login_name},
            )
            for table in (
                "idempotency_records",
                "document_drafts",
                "match_assessments",
                "posting_requirement_revisions",
                "model_runs",
                "posting_duplicate_decisions",
                "applications",
                "saved_opportunities",
                "job_source_observations",
                "job_postings",
                "import_batches",
                "profile_revision_evidence",
                "profile_revision_claims",
                "document_claim_links",
                "document_artifacts",
                "claim_evidence_links",
                "evidence_revisions",
            ):
                conn.execute(
                    text(
                        f"DELETE FROM offerloom.{table} WHERE owner_id="
                        "(SELECT id FROM offerloom.owners WHERE login_name=:login)"
                    ),
                    {"login": context.login_name},
                )
            conn.execute(
                text(
                    "DELETE FROM offerloom.workflow_runs WHERE kind='source_refresh' AND owner_id="
                    "(SELECT id FROM offerloom.owners WHERE login_name=:login)"
                ),
                {"login": context.login_name},
            )
            conn.execute(
                text(
                    "UPDATE offerloom.source_configs SET current_policy_review_id=NULL,"
                    "terms_reviewed_at=NULL,enabled=false,version=version+1,"
                    "policy_version=policy_version+1 WHERE removed_at IS NULL AND owner_id="
                    "(SELECT id FROM offerloom.owners WHERE login_name=:login)"
                ),
                {"login": context.login_name},
            )
            for table in ("source_policy_reviews", "source_configs"):
                conn.execute(
                    text(
                        f"DELETE FROM offerloom.{table} WHERE owner_id="
                        "(SELECT id FROM offerloom.owners WHERE login_name=:login)"
                    ),
                    {"login": context.login_name},
                )
            conn.execute(
                text("DELETE FROM offerloom.owners WHERE login_name=:login"),
                {"login": context.login_name},
            )
            conn.execute(text("DELETE FROM offerloom.auth_challenges"))
            conn.execute(text("DELETE FROM offerloom.auth_throttles"))
            conn.execute(
                text(
                    "UPDATE offerloom.installation_state SET bootstrap_token_sha256=NULL,bootstrap_expires_at=NULL,bootstrap_consumed_at=NULL WHERE singleton"
                )
            )
        admin.close()


def _data(response: httpx.Response) -> dict[str, object]:
    return cast(dict[str, object], response.json())


def _challenge(client: TestClient) -> str:
    response = client.get("/api/v1/auth/challenge")
    assert response.status_code == 200
    return str(_data(response)["csrf_token"])


def _headers(context: IdentityContext, csrf: str) -> dict[str, str]:
    return {"Origin": context.settings.origin, "X-CSRF-Token": csrf}


def _bootstrap(context: IdentityContext, client: TestClient) -> httpx.Response:
    http_client: httpx.Client = client
    return http_client.post(
        "/api/v1/bootstrap",
        json={
            "bootstrap_token": context.token,
            "login_name": context.login_name,
            "password": PASSWORD,
        },
        headers=_headers(context, _challenge(client)),
    )


def _login(
    context: IdentityContext, client: TestClient, password: str = PASSWORD
) -> httpx.Response:
    http_client: httpx.Client = client
    return http_client.post(
        "/api/v1/session",
        json={"login_name": context.login_name, "password": password},
        headers=_headers(context, _challenge(client)),
    )


@pytest.mark.postgres
def test_bootstrap_is_atomic_single_use_with_empty_profile(identity: IdentityContext) -> None:
    with identity.client() as client:
        response = _bootstrap(identity, client)
        assert response.status_code == 201
        cookie = response.headers["set-cookie"]
        assert (
            SESSION_COOKIE in cookie
            and "Secure" in cookie
            and "HttpOnly" in cookie
            and "SameSite=lax" in cookie
        )
        assert "Domain=" not in cookie
        assert response.headers["cache-control"] == "no-store"
        session = client.get("/api/v1/session")
        assert session.status_code == 200
        assert _data(session)["owner_id"] == _data(response)["owner_id"]
        profile = client.get("/api/v1/profile")
        assert profile.status_code == 200 and profile.headers["etag"] == '"1"'
        current = cast(dict[str, object], _data(profile)["current_revision"])
        snapshot = cast(dict[str, object], current["snapshot"])
        assert snapshot["claim_revision_ids"] == []
        replay = _bootstrap(identity, client)
        assert replay.status_code == 404
    with identity.admin.transaction() as conn:
        assert conn.execute(text("SELECT count(*) FROM offerloom.owners")).scalar_one() == 1
        assert conn.execute(text("SELECT count(*) FROM offerloom.owner_settings")).scalar_one() == 1
        assert (
            conn.execute(
                text(
                    "SELECT bootstrap_token_sha256 IS NULL AND bootstrap_consumed_at IS NOT NULL FROM offerloom.installation_state WHERE singleton"
                )
            ).scalar_one()
            is True
        )


@pytest.mark.postgres
def test_actual_postgres_concurrent_bootstrap(identity: IdentityContext) -> None:
    with identity.client() as first, identity.client() as second:
        first_csrf, second_csrf = _challenge(first), _challenge(second)
        payload = {
            "bootstrap_token": identity.token,
            "login_name": identity.login_name,
            "password": PASSWORD,
        }
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    client.post, "/api/v1/bootstrap", json=payload, headers=_headers(identity, csrf)
                )
                for client, csrf in ((first, first_csrf), (second, second_csrf))
            ]
            statuses = sorted(future.result().status_code for future in futures)
        assert statuses == [201, 404]
    with identity.admin.transaction() as conn:
        assert conn.execute(text("SELECT count(*) FROM offerloom.owners")).scalar_one() == 1
        assert conn.execute(text("SELECT count(*) FROM offerloom.profiles")).scalar_one() == 1


@pytest.mark.postgres
def test_expired_bootstrap_and_wrong_token_never_create_owner(identity: IdentityContext) -> None:
    with identity.client() as client:
        csrf = _challenge(client)
        wrong = client.post(
            "/api/v1/bootstrap",
            json={
                "bootstrap_token": secrets.token_urlsafe(32),
                "login_name": identity.login_name,
                "password": PASSWORD,
            },
            headers=_headers(identity, csrf),
        )
        assert wrong.status_code == 401
        with identity.admin.transaction() as conn:
            conn.execute(
                text(
                    "UPDATE offerloom.installation_state SET bootstrap_expires_at=now()-interval '1 second'"
                )
            )
        assert _bootstrap(identity, client).status_code == 401
    with identity.admin.transaction() as conn:
        assert conn.execute(text("SELECT count(*) FROM offerloom.owners")).scalar_one() == 0


@pytest.mark.postgres
def test_origin_csrf_host_content_type_and_body_limits(identity: IdentityContext) -> None:
    with identity.client() as client:
        assert client.get("/api/v1/profile").status_code == 401
        assert client.get("/api/v1/workflows").status_code == 401
        assert client.get("/api/v1/documents/" + str(uuid4())).status_code == 401
        assert client.get("/api/v1/artifacts/" + str(uuid4())).status_code == 401
        assert client.get("/health/ready", headers={"Host": "attacker.invalid"}).status_code == 400
        assert client.post("/api/v1/bootstrap", json={}).status_code == 403
        assert (
            client.post(
                "/api/v1/bootstrap", json={}, headers={"Origin": "https://attacker.invalid"}
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/v1/bootstrap",
                content="{}",
                headers={"Origin": identity.settings.origin, "Content-Type": "text/plain"},
            ).status_code
            == 415
        )
        large = client.post(
            "/api/v1/bootstrap",
            content=b"x" * (identity.settings.max_body_bytes + 1),
            headers={"Origin": identity.settings.origin, "Content-Type": "application/json"},
        )
        assert large.status_code == 413
        assert _bootstrap(identity, client).status_code == 201
        assert (
            client.delete(
                "/api/v1/session", headers={"Origin": identity.settings.origin}
            ).status_code
            == 403
        )
        assert (
            client.delete(
                "/api/v1/session",
                headers={"Origin": identity.settings.origin, "X-CSRF-Token": "wrong"},
            ).status_code
            == 403
        )
        profile = client.get("/api/v1/profile")
        assert "frame-ancestors 'none'" in profile.headers["content-security-policy"]
        assert profile.headers["x-content-type-options"] == "nosniff"
        UUID(profile.headers["x-request-id"])
        assert client.get("/").status_code == 404  # missing built UI is not a fake UI


@pytest.mark.postgres
def test_logout_password_rotation_and_local_recovery_revoke(identity: IdentityContext) -> None:
    with identity.client() as first, identity.client() as second:
        boot = _bootstrap(identity, first)
        assert boot.status_code == 201
        assert _login(identity, second).status_code == 201
        old_cookie = first.cookies.get(SESSION_COOKIE)
        csrf = str(_data(first.get("/api/v1/session"))["csrf_token"])
        changed = first.post(
            "/api/v1/session/password",
            json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
            headers=_headers(identity, csrf),
        )
        assert changed.status_code == 201
        assert first.cookies.get(SESSION_COOKIE) != old_cookie
        assert second.get("/api/v1/session").status_code == 401
        assert first.get("/api/v1/session").status_code == 200
        recover_password(identity.admin, "Local operator recovery 819!")
        assert first.get("/api/v1/session").status_code == 401
        assert _login(identity, second, "Local operator recovery 819!").status_code == 201
        csrf = str(_data(second.get("/api/v1/session"))["csrf_token"])
        old = second.cookies.get(SESSION_COOKIE)
        assert second.delete("/api/v1/session", headers=_headers(identity, csrf)).status_code == 204
        second.cookies.set(SESSION_COOKIE, str(old))
        assert second.get("/api/v1/session").status_code == 401


@pytest.mark.postgres
@pytest.mark.parametrize("expiry", ["idle", "absolute", "revoked", "epoch"])
def test_database_authoritative_session_expiry(identity: IdentityContext, expiry: str) -> None:
    with identity.client() as client:
        assert _bootstrap(identity, client).status_code == 201
        with identity.admin.transaction() as conn:
            if expiry == "idle":
                conn.execute(
                    text("UPDATE offerloom.sessions SET last_seen_at=now()-interval '13 hours'")
                )
            elif expiry == "absolute":
                conn.execute(
                    text(
                        "UPDATE offerloom.sessions SET created_at=now()-interval '8 days',expires_at=now()-interval '1 day'"
                    )
                )
            elif expiry == "revoked":
                conn.execute(text("UPDATE offerloom.sessions SET revoked_at=now()"))
            else:
                conn.execute(text("UPDATE offerloom.owners SET auth_epoch=auth_epoch+1"))
        assert client.get("/api/v1/session").status_code == 401
        assert client.get("/api/v1/profile").status_code == 401


@pytest.mark.postgres
def test_throttles_persist_across_app_instances(identity: IdentityContext) -> None:
    with identity.client() as client:
        assert _bootstrap(identity, client).status_code == 201
        for _ in range(5):
            assert _login(identity, client, "A wrong synthetic password").status_code == 401
    with identity.client() as restarted:
        assert _login(identity, restarted).status_code == 429
    with identity.admin.transaction() as conn:
        counters = (
            conn.execute(text("SELECT scope,attempts FROM offerloom.auth_throttles"))
            .mappings()
            .all()
        )
        assert len(counters) == 2
        assert all(row["attempts"] == 5 for row in counters)
        assert all(
            row["scope"] == "global" or str(row["scope"]).startswith("source:") for row in counters
        )


@pytest.mark.postgres
def test_profile_revision_ifmatch_and_fixture_idempotency(identity: IdentityContext) -> None:
    with identity.client() as client:
        boot = _bootstrap(identity, client)
        assert boot.status_code == 201
        headers = {
            **_headers(identity, str(_data(boot)["csrf_token"])),
            "Idempotency-Key": "synthetic-profile-save-001",
        }
        snapshot: dict[str, object] = {
            "display_name": "Mira Vale",
            "headline": "Synthetic identity",
            "claim_revision_ids": [],
        }
        assert client.put("/api/v1/profile", json=snapshot, headers=headers).status_code == 428
        assert (
            client.put(
                "/api/v1/profile", json=snapshot, headers={**headers, "If-Match": 'W/"1"'}
            ).status_code
            == 400
        )
        assert (
            client.put(
                "/api/v1/profile",
                json=snapshot,
                headers={**_headers(identity, str(_data(boot)["csrf_token"])), "If-Match": '"1"'},
            ).status_code
            == 400
        )
        saved = client.put("/api/v1/profile", json=snapshot, headers={**headers, "If-Match": '"1"'})
        assert saved.status_code == 200 and saved.headers["etag"] == '"2"'
        replayed_save = client.put(
            "/api/v1/profile", json=snapshot, headers={**headers, "If-Match": '"1"'}
        )
        assert replayed_save.json() == saved.json()
        assert (
            client.put(
                "/api/v1/profile",
                json=snapshot,
                headers={
                    **headers,
                    "If-Match": '"1"',
                    "Idempotency-Key": "synthetic-stale-save-002",
                },
            ).status_code
            == 412
        )
        invalid = {**snapshot, "claim_revision_ids": [str(uuid4())]}
        assert (
            client.put(
                "/api/v1/profile",
                json=invalid,
                headers={
                    **headers,
                    "If-Match": '"2"',
                    "Idempotency-Key": "synthetic-invalid-save-003",
                },
            ).status_code
            == 422
        )
        seed_headers = {**headers, "If-Match": '"2"', "Idempotency-Key": "synthetic-seed-key-001"}
        seeded = client.post(
            "/api/v1/benchmarks/profile", json={"fixture": "SYN-CORE"}, headers=seed_headers
        )
        replay = client.post(
            "/api/v1/benchmarks/profile", json={"fixture": "SYN-CORE"}, headers=seed_headers
        )
        assert seeded.status_code == replay.status_code == 200
        assert seeded.json() == replay.json()
        changed = client.post(
            "/api/v1/benchmarks/profile",
            json={"fixture": "SYN-CORE"},
            headers={**seed_headers, "If-Match": '"3"'},
        )
        assert changed.status_code == 409
        current = cast(dict[str, object], _data(seeded)["current_revision"])
        value = cast(dict[str, object], current["snapshot"])
        assert len(cast(list[object], value["claim_revision_ids"])) == 2
    with identity.admin.transaction() as conn:
        assert (
            conn.execute(text("SELECT count(*) FROM offerloom.profile_revisions")).scalar_one() == 3
        )
        assert (
            conn.execute(text("SELECT count(*) FROM offerloom.claim_revisions")).scalar_one() == 2
        )
        assert (
            conn.execute(text("SELECT count(*) FROM offerloom.evidence_revisions")).scalar_one()
            == 2
        )


@pytest.mark.parametrize(
    "password", ["short", "x" * 129, "passwordpassword", "correct horse battery staple"]
)
def test_password_policy_rejects_explicitly(password: str) -> None:
    with pytest.raises(Problem):
        check_password(password)


def test_unicode_and_spaces_are_preserved_and_hash_work_is_bounded() -> None:
    password = "  Καλημέρα synthetic 819!  "
    check_password(password)
    HASH_SLOTS.acquire()
    HASH_SLOTS.acquire()
    try:
        with pytest.raises(Problem) as error:
            hash_password(password)
        assert error.value.status == 429
    finally:
        HASH_SLOTS.release()
        HASH_SLOTS.release()


@pytest.mark.postgres
def test_built_static_ui_and_redacted_validation_errors(
    identity: IdentityContext, tmp_path: Path
) -> None:
    from dataclasses import replace

    ui = tmp_path / "built-ui"
    ui.mkdir()
    (ui / "index.html").write_text("<!doctype html><title>Synthetic built UI fixture</title>")
    settings = replace(identity.settings, web_dist=ui)
    with TestClient(create_app(settings), base_url=settings.origin) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "Synthetic built UI fixture" in response.text
        assert response.headers["cache-control"] == "no-store"
        csrf = _challenge(client)
        invalid = client.post(
            "/api/v1/bootstrap",
            json={
                "bootstrap_token": identity.token,
                "login_name": identity.login_name,
                "password": "x" * 129,
            },
            headers=_headers(identity, csrf),
        )
        assert invalid.status_code == 422
        assert identity.token not in invalid.text
        assert "x" * 129 not in invalid.text
        assert invalid.headers["cache-control"] == "no-store"
        UUID(invalid.headers["x-request-id"])


@pytest.mark.postgres
def test_benchmark_profile_is_unavailable_in_production(identity: IdentityContext) -> None:
    from dataclasses import replace

    with identity.client() as client:
        boot = _bootstrap(identity, client)
        assert boot.status_code == 201
        with TestClient(
            create_app(replace(identity.settings, mode="production")),
            base_url=identity.settings.origin,
        ) as production:
            production.cookies.update(client.cookies)
            response = production.post(
                "/api/v1/benchmarks/profile",
                json={"fixture": "SYN-CORE"},
                headers={
                    **_headers(identity, str(_data(boot)["csrf_token"])),
                    "If-Match": '"1"',
                    "Idempotency-Key": "production-fixture-key-001",
                },
            )
            assert response.status_code == 404
