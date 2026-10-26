# SPDX-License-Identifier: MIT OR Apache-2.0
"""Password confirmation renews owner authority, not workspace data or old intents."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from test_identity import NEW_PASSWORD, PASSWORD, IdentityContext, _bootstrap, _headers
from test_identity import identity as identity

from offerloom import auth
from offerloom.auth import SESSION_COOKIE, digest, recover_password

_ROUTE = "/api/v1/session/reauthenticate"


@pytest.mark.postgres
def test_password_confirmation_rotates_stale_session_without_changing_workspace(
    identity: IdentityContext,
) -> None:
    with identity.client() as client, identity.client() as old_client:
        boot = _bootstrap(identity, client)
        assert boot.status_code == 201
        csrf = boot.json()["csrf_token"]
        old_client.cookies.update(client.cookies)
        old_token = client.cookies.get(SESSION_COOKIE)
        assert old_token is not None
        with identity.admin.transaction() as conn:
            conn.execute(
                text(
                    "UPDATE offerloom.sessions SET authenticated_at=clock_timestamp()-interval '6 minutes' WHERE token_sha256=:token"
                ),
                {"token": digest(old_token)},
            )
            before = conn.execute(
                text(
                    "SELECT o.auth_epoch,s.workspace_version,p.version,p.current_revision_id FROM offerloom.owners o JOIN offerloom.owner_settings s ON s.owner_id=o.id JOIN offerloom.profiles p ON p.owner_id=o.id"
                )
            ).one()
            old_auth = conn.execute(
                text("SELECT authenticated_at FROM offerloom.sessions WHERE token_sha256=:token"),
                {"token": digest(old_token)},
            ).scalar_one()
        rejected = client.post(
            _ROUTE, headers=_headers(identity, csrf), json={"current_password": "Wrong phrase"}
        )
        assert rejected.status_code == 403
        assert rejected.json()["code"] == "reauthentication_failed"
        assert client.get("/api/v1/session").status_code == 200
        with identity.admin.transaction() as conn:
            assert (
                conn.execute(
                    text(
                        "SELECT authenticated_at FROM offerloom.sessions WHERE token_sha256=:token AND revoked_at IS NULL"
                    ),
                    {"token": digest(old_token)},
                ).scalar_one()
                == old_auth
            )
        confirmed = client.post(
            _ROUTE, headers=_headers(identity, csrf), json={"current_password": PASSWORD}
        )
        assert confirmed.status_code == 201
        assert confirmed.json()["owner_id"] == boot.json()["owner_id"]
        assert confirmed.json()["csrf_token"] != csrf
        assert client.cookies.get(SESSION_COOKIE) != old_token
        assert old_client.get("/api/v1/session").status_code == 401
        assert client.get("/api/v1/session").status_code == 200
        assert client.delete("/api/v1/session", headers=_headers(identity, csrf)).status_code == 403
        with identity.admin.transaction() as conn:
            assert (
                conn.execute(
                    text(
                        "SELECT o.auth_epoch,s.workspace_version,p.version,p.current_revision_id FROM offerloom.owners o JOIN offerloom.owner_settings s ON s.owner_id=o.id JOIN offerloom.profiles p ON p.owner_id=o.id"
                    )
                ).one()
                == before
            )
            sessions = conn.execute(
                text(
                    "SELECT revoked_at IS NOT NULL,authenticated_at>clock_timestamp()-interval '5 minutes' FROM offerloom.sessions ORDER BY created_at,id"
                )
            ).all()
            assert [tuple(row) for row in sessions] == [(True, False), (False, True)]
        assert (
            client.delete(
                "/api/v1/session", headers=_headers(identity, confirmed.json()["csrf_token"])
            ).status_code
            == 204
        )


@pytest.mark.postgres
def test_password_confirmation_requires_owner_csrf_and_persistent_attempt_budget(
    identity: IdentityContext,
) -> None:
    with identity.client() as client:
        assert (
            client.post(
                _ROUTE,
                headers={"Origin": identity.settings.origin},
                json={"current_password": PASSWORD},
            ).status_code
            == 401
        )
        boot = _bootstrap(identity, client)
        assert boot.status_code == 201
        csrf = boot.json()["csrf_token"]
        assert client.post(_ROUTE, json={"current_password": PASSWORD}).status_code == 403
        assert (
            client.post(
                _ROUTE,
                headers={**_headers(identity, csrf), "Origin": "https://foreign.invalid"},
                json={"current_password": PASSWORD},
            ).status_code
            == 403
        )
        for _ in range(5):
            rejected = client.post(
                _ROUTE, headers=_headers(identity, csrf), json={"current_password": "Wrong phrase"}
            )
            assert rejected.status_code == 403
            assert "Wrong phrase" not in rejected.text
        with identity.client() as restarted:
            restarted.cookies.update(client.cookies)
            assert (
                restarted.post(
                    _ROUTE, headers=_headers(identity, csrf), json={"current_password": PASSWORD}
                ).status_code
                == 429
            )
        assert client.get("/api/v1/session").status_code == 200
        with identity.admin.transaction() as conn:
            assert (
                conn.execute(
                    text("SELECT count(*) FROM offerloom.sessions WHERE revoked_at IS NULL")
                ).scalar_one()
                == 1
            )


@pytest.mark.postgres
def test_password_rotation_during_confirmation_cannot_mint_a_session(
    identity: IdentityContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = auth.verify_password

    def rotate_after_verification(encoded: str, password: str) -> bool:
        valid = original(encoded, password)
        recover_password(identity.admin, NEW_PASSWORD)
        return valid

    with identity.client() as client:
        boot = _bootstrap(identity, client)
        assert boot.status_code == 201
        monkeypatch.setattr(auth, "verify_password", rotate_after_verification)
        response = client.post(
            _ROUTE,
            headers=_headers(identity, boot.json()["csrf_token"]),
            json={"current_password": PASSWORD},
        )
        assert response.status_code == 401
        assert client.get("/api/v1/session").status_code == 401
        with identity.admin.transaction() as conn:
            assert conn.execute(text("SELECT count(*) FROM offerloom.sessions")).scalar_one() == 1
            assert (
                conn.execute(
                    text("SELECT count(*) FROM offerloom.sessions WHERE revoked_at IS NULL")
                ).scalar_one()
                == 0
            )
