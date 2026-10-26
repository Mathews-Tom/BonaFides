# SPDX-License-Identifier: MIT OR Apache-2.0
"""Single-owner identity, database-backed abuse limits, opaque browser sessions."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated
from uuid import UUID, uuid4

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import Connection, text

from offerloom.commands import lock_installation
from offerloom.config import Settings, read_secret
from offerloom.db import Database
from offerloom.problems import Problem

SESSION_COOKIE = "__Host-offerloom_session"
CHALLENGE_COOKIE = "__Host-offerloom_challenge"
HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=1, type=Type.ID)
HASH_SLOTS = threading.BoundedSemaphore(2)
# Offline policy v1: explicitly bounded, manually reviewed common-password denylist.
# This is not a claim to cover every breached credential. No network lookup occurs.
COMPROMISED = frozenset(
    {
        "passwordpassword",
        "passwordpasswordpassword",
        "123456789012345",
        "1234567890123456",
        "12345678901234567890",
        "qwertyuiopasdfgh",
        "qwertyuiopasdfghjkl",
        "letmeinletmeinletmein",
        "password123456789",
        "Password123456789",
        "Password123456789!",
        "iloveyouiloveyou",
        "welcome123456789",
        "administrator123",
        "correct horse battery staple",
    }
)
# A fixed valid Argon2id encoding avoids an account-existence timing shortcut.
DUMMY_HASH = "$argon2id$v=19$m=65536,t=3,p=1$MDEyMzQ1Njc4OWFiY2RlZg$CY+BJzyKraXpYT4NVXYmA5dH7+OXFVAzWPIkzJMx3jQ"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(StrictModel):
    login_name: str = Field(min_length=1, max_length=120)
    password: SecretStr = Field(min_length=1, max_length=128)


class Bootstrap(Login):
    bootstrap_token: SecretStr = Field(min_length=40, max_length=128)


class Reauthentication(StrictModel):
    current_password: SecretStr = Field(min_length=1, max_length=128)


class PasswordChange(StrictModel):
    current_password: SecretStr = Field(min_length=1, max_length=128)
    new_password: SecretStr = Field(min_length=15, max_length=128)


@dataclass(frozen=True)
class Owner:
    id: UUID
    login_name: str
    session_id: UUID
    csrf_token: str
    expires_at: datetime
    authenticated_at: datetime
    auth_epoch: int

    @property
    def owner_id(self) -> UUID:
        return self.id

    def response(self) -> dict[str, object]:
        return {
            "owner_id": str(self.id),
            "login_name": self.login_name,
            "csrf_token": self.csrf_token,
            "expires_at": self.expires_at.isoformat(),
        }


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def check_password(password: str) -> None:
    if not 15 <= len(password) <= 128:
        raise Problem(422, "password_length", "Password must contain 15–128 characters.")
    if password in COMPROMISED:
        raise Problem(
            422,
            "compromised_password",
            "Choose a password not present in the offline compromised-password list.",
        )


def _lock_identity(conn: Connection) -> None:
    # Identity writes may upgrade installation state; take the exclusive lock
    # immediately rather than deadlocking two bootstrap SHARE-to-UPDATE upgrades.
    row = (
        conn.execute(
            text(
                "SELECT maintenance_active FROM offerloom.installation_state WHERE singleton FOR UPDATE"
            )
        )
        .mappings()
        .one()
    )
    if row["maintenance_active"]:
        raise Problem(503, "installation_maintenance")


def hash_password(password: str) -> str:
    check_password(password)
    if not HASH_SLOTS.acquire(blocking=False):
        raise Problem(429, "authentication_busy")
    try:
        return HASHER.hash(password)
    finally:
        HASH_SLOTS.release()


def verify_password(encoded: str, password: str) -> bool:
    if not HASH_SLOTS.acquire(blocking=False):
        raise Problem(429, "authentication_busy")
    try:
        try:
            return HASHER.verify(encoded, password)
        except (VerificationError, InvalidHashError):
            return False
    finally:
        HASH_SLOTS.release()


def audit(
    conn: Connection,
    owner_id: UUID,
    action: str,
    request_id: UUID | None = None,
    actor: str = "owner",
) -> None:
    conn.execute(
        text(
            "INSERT INTO offerloom.audit_events(owner_id,action,actor_kind,actor_id,request_id,entity_type) VALUES(:owner,:action,:actor,:actor_id,:request,'identity')"
        ),
        {
            "owner": owner_id,
            "action": action,
            "actor": actor,
            "actor_id": str(owner_id),
            "request": request_id or uuid4(),
        },
    )


def same_origin(request: Request) -> None:
    settings: Settings = request.app.state.settings
    if request.headers.get("origin") != settings.origin:
        raise Problem(403, "origin_rejected")
    if request.headers.get("sec-fetch-site") not in {None, "same-origin", "none"}:
        raise Problem(403, "cross_site_request")


def _source(request: Request) -> str:
    settings: Settings = request.app.state.settings
    address = request.client.host if request.client else "unknown"
    try:
        normalized = str(ipaddress.ip_address(address))
    except ValueError:
        normalized = "unknown"
    return (
        "source:"
        + hmac.new(
            settings.keyring.keys[settings.keyring.active_key_id],
            normalized.encode(),
            hashlib.sha256,
        ).hexdigest()
    )


def _reserve_attempt(db: Database, source: str) -> None:
    blocked = False
    with db.transaction() as conn:
        _lock_identity(conn)
        for scope in ("global", source):
            conn.execute(
                text(
                    "DELETE FROM offerloom.auth_throttles WHERE window_started_at < now()-interval '24 hours'"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO offerloom.auth_throttles(scope) VALUES(:scope) ON CONFLICT DO NOTHING"
                ),
                {"scope": scope},
            )
            conn.execute(
                text(
                    "UPDATE offerloom.auth_throttles SET attempts=0,window_started_at=now(),blocked_until=NULL WHERE scope=:scope AND window_started_at <= now()-interval '15 minutes'"
                ),
                {"scope": scope},
            )
            row = (
                conn.execute(
                    text(
                        "SELECT attempts,blocked_until IS NOT NULL AND blocked_until>now() AS blocked FROM offerloom.auth_throttles WHERE scope=:scope FOR UPDATE"
                    ),
                    {"scope": scope},
                )
                .mappings()
                .one()
            )
            if row["blocked"] or row["attempts"] >= 5:
                blocked = True
            else:
                conn.execute(
                    text(
                        "UPDATE offerloom.auth_throttles SET attempts=attempts+1,blocked_until=CASE WHEN attempts>=4 THEN now()+interval '15 minutes' ELSE NULL END WHERE scope=:scope"
                    ),
                    {"scope": scope},
                )
    if blocked:
        raise Problem(429, "authentication_throttled")


def _reset_attempts(conn: Connection, source: str) -> None:
    conn.execute(
        text("DELETE FROM offerloom.auth_throttles WHERE scope IN ('global',:source)"),
        {"source": source},
    )


def _new_session(conn: Connection, owner_id: UUID, response: Response) -> Owner:
    raw = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    row = (
        conn.execute(
            text(
                "INSERT INTO offerloom.sessions(owner_id,token_sha256,csrf_sha256,csrf_token,auth_epoch,expires_at) SELECT id,:token,:csrf,:raw,auth_epoch,now()+interval '7 days' FROM offerloom.owners WHERE id=:owner RETURNING id,expires_at,authenticated_at,auth_epoch"
            ),
            {"owner": owner_id, "token": digest(raw), "csrf": digest(csrf), "raw": csrf},
        )
        .mappings()
        .one()
    )
    login = str(
        conn.execute(
            text("SELECT login_name FROM offerloom.owners WHERE id=:owner"), {"owner": owner_id}
        ).scalar_one()
    )
    response.set_cookie(
        SESSION_COOKIE, raw, max_age=7 * 86400, secure=True, httponly=True, samesite="lax", path="/"
    )
    response.delete_cookie(CHALLENGE_COOKIE, secure=True, httponly=True, samesite="lax", path="/")
    return Owner(
        owner_id,
        login,
        row["id"],
        csrf,
        row["expires_at"],
        row["authenticated_at"],
        int(row["auth_epoch"]),
    )


def _challenge(request: Request, conn: Connection) -> None:
    same_origin(request)
    cookie = request.cookies.get(CHALLENGE_COOKIE, "")
    csrf = request.headers.get("x-csrf-token", "")
    if not cookie or not csrf or len(cookie) > 128 or len(csrf) > 128:
        raise Problem(403, "csrf_rejected")
    valid = conn.execute(
        text(
            "DELETE FROM offerloom.auth_challenges WHERE token_sha256=:token AND csrf_sha256=:csrf AND expires_at>now() RETURNING token_sha256"
        ),
        {"token": digest(cookie), "csrf": digest(csrf)},
    ).scalar_one_or_none()
    if valid is None:
        raise Problem(403, "csrf_rejected")


def _session_owner(conn: Connection, request: Request) -> Owner:
    raw = request.cookies.get(SESSION_COOKIE, "")
    if not raw or len(raw) > 128:
        raise Problem(401, "authentication_required")
    row = (
        conn.execute(
            text(
                "SELECT o.id AS owner_id,o.login_name,s.id,s.csrf_token,s.csrf_sha256,s.expires_at,s.authenticated_at,s.auth_epoch FROM offerloom.sessions s JOIN offerloom.owners o ON o.id=s.owner_id WHERE s.token_sha256=:token AND s.revoked_at IS NULL AND s.auth_epoch=o.auth_epoch AND s.expires_at>now() AND s.last_seen_at>now()-interval '12 hours'"
            ),
            {"token": digest(raw)},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise Problem(401, "authentication_required")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        same_origin(request)
        supplied = request.headers.get("x-csrf-token", "")
        if (
            not supplied
            or len(supplied) > 128
            or not hmac.compare_digest(digest(supplied), row["csrf_sha256"])
        ):
            raise Problem(403, "csrf_rejected")
    return Owner(
        row["owner_id"],
        str(row["login_name"]),
        row["id"],
        str(row["csrf_token"]),
        row["expires_at"],
        row["authenticated_at"],
        int(row["auth_epoch"]),
    )


def require_owner(request: Request) -> Owner:
    db: Database = request.app.state.db
    with db.transaction() as conn:
        lock_installation(conn)
        owner = _session_owner(conn, request)
        conn.execute(
            text(
                "UPDATE offerloom.sessions SET last_seen_at=now() WHERE owner_id=:owner AND id=:id AND last_seen_at<now()-interval '1 minute'"
            ),
            {"owner": owner.id, "id": owner.session_id},
        )
    request.state.owner = owner
    return owner


def require_readonly_owner(request: Request) -> Owner:
    """Authenticate a read without extending a session or admitting a writer."""
    if request.method not in {"GET", "HEAD"}:
        raise Problem(405, "read_only_resource")
    db: Database = request.app.state.db
    with db.transaction() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        owner = _session_owner(conn, request)
    request.state.owner = owner
    return owner


def init_bootstrap(db: Database, settings: Settings) -> None:
    if settings.bootstrap_token_file is None:
        raise ValueError("Missing bootstrap_token_file")
    token = read_secret(settings.bootstrap_token_file, "bootstrap_token")
    if not 43 <= len(token) <= 128 or not all(c.isalnum() or c in "-_" for c in token):
        raise ValueError("Invalid bootstrap_token")
    with db.transaction() as conn:
        _lock_identity(conn)
        if conn.execute(text("SELECT id FROM offerloom.owners")).first() is not None:
            raise ValueError("Bootstrap unavailable: owner already exists")
        conn.execute(
            text(
                "UPDATE offerloom.installation_state SET bootstrap_token_sha256=:token,bootstrap_expires_at=now()+interval '15 minutes',bootstrap_consumed_at=NULL WHERE singleton"
            ),
            {"token": digest(token)},
        )


def recover_password(db: Database, password: str) -> None:
    encoded = hash_password(password)
    with db.transaction() as conn:
        _lock_identity(conn)
        owner = conn.execute(
            text(
                "UPDATE offerloom.owners SET password_hash=:encoded,auth_epoch=auth_epoch+1,version=version+1 RETURNING id"
            ),
            {"encoded": encoded},
        ).scalar_one_or_none()
        if owner is None:
            raise ValueError("No owner exists")
        _revoke(conn, owner)
        audit(conn, owner, "password_recovered", actor="operator")


def _revoke(conn: Connection, owner_id: UUID) -> None:
    conn.execute(
        text(
            "UPDATE offerloom.sessions SET revoked_at=now() WHERE owner_id=:owner AND revoked_at IS NULL"
        ),
        {"owner": owner_id},
    )
    conn.execute(
        text(
            """WITH revoked AS (
            UPDATE offerloom.action_approvals
            SET revoked_at=clock_timestamp(),version=version+1
            WHERE owner_id=:owner AND consumed_at IS NULL AND revoked_at IS NULL
            RETURNING owner_id,id,workflow_run_id)
            INSERT INTO offerloom.approval_audit_events
            (owner_id,approval_id,workflow_run_id,event,error_code)
            SELECT owner_id,id,workflow_run_id,'revoked','authentication_revoked' FROM revoked"""
        ),
        {"owner": owner_id},
    )
    conn.execute(
        text(
            "UPDATE offerloom.hosted_egress_consents SET revoked_at=clock_timestamp() WHERE owner_id=:owner AND revoked_at IS NULL"
        ),
        {"owner": owner_id},
    )
    conn.execute(
        text("DELETE FROM offerloom.oauth_transactions WHERE owner_id=:owner"), {"owner": owner_id}
    )


def build_router(db: Database, settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.get("/auth/challenge")
    def challenge(response: Response) -> dict[str, object]:
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with db.transaction() as conn:
            _lock_identity(conn)
            conn.execute(text("DELETE FROM offerloom.auth_challenges WHERE expires_at<=now()"))
            count = int(
                conn.execute(text("SELECT count(*) FROM offerloom.auth_challenges")).scalar_one()
            )
            if count >= 1000:
                raise Problem(429, "challenge_capacity")
            conn.execute(
                text(
                    "INSERT INTO offerloom.auth_challenges(token_sha256,csrf_sha256,expires_at) VALUES(:token,:csrf,now()+interval '10 minutes')"
                ),
                {"token": digest(token), "csrf": digest(csrf)},
            )
            available = bool(
                conn.execute(
                    text(
                        "SELECT bootstrap_token_sha256 IS NOT NULL AND bootstrap_consumed_at IS NULL AND bootstrap_expires_at>now() AND NOT EXISTS(SELECT 1 FROM offerloom.owners) FROM offerloom.installation_state WHERE singleton"
                    )
                ).scalar_one()
            )
        response.set_cookie(
            CHALLENGE_COOKIE,
            token,
            max_age=600,
            secure=True,
            httponly=True,
            samesite="lax",
            path="/",
        )
        return {"csrf_token": csrf, "bootstrap_available": available}

    @router.post("/bootstrap", status_code=201)
    def bootstrap(body: Bootstrap, request: Request, response: Response) -> dict[str, object]:
        same_origin(request)
        _reserve_attempt(db, _source(request))
        encoded = hash_password(body.password.get_secret_value())
        login_name = body.login_name.strip()
        if not login_name:
            raise Problem(422, "invalid_login_name")
        with db.transaction() as conn:
            _lock_identity(conn)
            _challenge(request, conn)
            installed = (
                conn.execute(
                    text(
                        "SELECT bootstrap_token_sha256,bootstrap_expires_at>now() AS valid,bootstrap_consumed_at FROM offerloom.installation_state WHERE singleton FOR UPDATE"
                    )
                )
                .mappings()
                .one()
            )
            if conn.execute(text("SELECT id FROM offerloom.owners")).first() is not None:
                raise Problem(404, "bootstrap_unavailable")
            expected = installed["bootstrap_token_sha256"]
            if (
                expected is None
                or not installed["valid"]
                or installed["bootstrap_consumed_at"] is not None
                or not hmac.compare_digest(
                    str(expected), digest(body.bootstrap_token.get_secret_value())
                )
            ):
                raise Problem(401, "authentication_failed")
            owner_id = conn.execute(
                text(
                    "INSERT INTO offerloom.owners(login_name,password_hash) VALUES(:login,:password) RETURNING id"
                ),
                {"login": login_name, "password": encoded},
            ).scalar_one()
            conn.execute(
                text("INSERT INTO offerloom.owner_settings(owner_id) VALUES(:owner)"),
                {"owner": owner_id},
            )
            from offerloom.profiles import create_empty_profile

            create_empty_profile(conn, owner_id)
            conn.execute(
                text(
                    "UPDATE offerloom.installation_state SET bootstrap_token_sha256=NULL,bootstrap_consumed_at=now() WHERE singleton"
                )
            )
            _reset_attempts(conn, _source(request))
            audit(conn, owner_id, "owner_bootstrapped", request.state.request_id)
            owner = _new_session(conn, owner_id, response)
        return owner.response()

    @router.post("/session", status_code=201)
    def login(body: Login, request: Request, response: Response) -> dict[str, object]:
        same_origin(request)
        source = _source(request)
        _reserve_attempt(db, source)
        with db.transaction() as conn:
            _lock_identity(conn)
            _challenge(request, conn)
            row = (
                conn.execute(
                    text(
                        "SELECT id,login_name,password_hash,auth_epoch FROM offerloom.owners WHERE singleton"
                    )
                )
                .mappings()
                .one_or_none()
            )
        valid = verify_password(
            str(row["password_hash"]) if row else DUMMY_HASH, body.password.get_secret_value()
        )
        if row is None or not valid or body.login_name.strip() != row["login_name"]:
            if row is not None:
                with db.transaction() as conn:
                    _lock_identity(conn)
                    audit(conn, row["id"], "authentication_failed", request.state.request_id)
            raise Problem(401, "authentication_failed")
        with db.transaction() as conn:
            _lock_identity(conn)
            current = (
                conn.execute(
                    text(
                        "SELECT password_hash,auth_epoch FROM offerloom.owners WHERE id=:owner FOR UPDATE"
                    ),
                    {"owner": row["id"]},
                )
                .mappings()
                .one()
            )
            if (
                current["password_hash"] != row["password_hash"]
                or current["auth_epoch"] != row["auth_epoch"]
            ):
                raise Problem(401, "authentication_failed")
            if HASHER.check_needs_rehash(str(current["password_hash"])):
                encoded = hash_password(body.password.get_secret_value())
                conn.execute(
                    text("UPDATE offerloom.owners SET password_hash=:encoded WHERE id=:owner"),
                    {"encoded": encoded, "owner": row["id"]},
                )
            previous = request.cookies.get(SESSION_COOKIE)
            if previous:
                conn.execute(
                    text(
                        "UPDATE offerloom.sessions SET revoked_at=now() WHERE owner_id=:owner AND token_sha256=:token"
                    ),
                    {"owner": row["id"], "token": digest(previous)},
                )
            _reset_attempts(conn, source)
            owner = _new_session(conn, row["id"], response)
            audit(conn, row["id"], "session_created", request.state.request_id)
        return owner.response()

    @router.get("/session")
    def session(owner: Annotated[Owner, Depends(require_owner)]) -> dict[str, object]:
        return owner.response()

    @router.post("/session/reauthenticate", status_code=201)
    def reauthenticate(
        body: Reauthentication,
        request: Request,
        response: Response,
        owner: Annotated[Owner, Depends(require_owner)],
    ) -> dict[str, object]:
        source = _source(request)
        _reserve_attempt(db, source)
        with db.transaction() as conn:
            previous = (
                conn.execute(
                    text("SELECT password_hash,auth_epoch FROM offerloom.owners WHERE id=:owner"),
                    {"owner": owner.id},
                )
                .mappings()
                .one()
            )
        if not verify_password(
            str(previous["password_hash"]), body.current_password.get_secret_value()
        ):
            # A wrong confirmation does not invalidate the otherwise valid session.
            raise Problem(403, "reauthentication_failed")
        with db.transaction() as conn:
            _lock_identity(conn)
            current = (
                conn.execute(
                    text(
                        "SELECT password_hash,auth_epoch FROM offerloom.owners WHERE id=:owner FOR UPDATE"
                    ),
                    {"owner": owner.id},
                )
                .mappings()
                .one()
            )
            if (
                current["password_hash"] != previous["password_hash"]
                or current["auth_epoch"] != owner.auth_epoch
            ):
                raise Problem(401, "authentication_failed")
            revoked = conn.execute(
                text("""UPDATE offerloom.sessions SET revoked_at=clock_timestamp()
                WHERE owner_id=:owner AND id=:session AND auth_epoch=:epoch
                AND authenticated_at=:authenticated AND revoked_at IS NULL
                AND expires_at>clock_timestamp()
                AND last_seen_at>clock_timestamp()-interval '12 hours'"""),
                {
                    "owner": owner.id,
                    "session": owner.session_id,
                    "epoch": owner.auth_epoch,
                    "authenticated": owner.authenticated_at,
                },
            )
            if revoked.rowcount != 1:
                raise Problem(401, "authentication_failed")
            _reset_attempts(conn, source)
            fresh = _new_session(conn, owner.id, response)
            audit(conn, owner.id, "session_reauthenticated", request.state.request_id)
        return fresh.response()

    @router.delete("/session", status_code=204)
    def logout(request: Request, owner: Annotated[Owner, Depends(require_owner)]) -> Response:
        with db.transaction() as conn:
            _lock_identity(conn)
            conn.execute(
                text(
                    "UPDATE offerloom.sessions SET revoked_at=now() WHERE owner_id=:owner AND id=:id"
                ),
                {"owner": owner.id, "id": owner.session_id},
            )
            audit(conn, owner.id, "session_revoked", request.state.request_id)
        response = Response(status_code=204)
        response.delete_cookie(SESSION_COOKIE, secure=True, httponly=True, samesite="lax", path="/")
        return response

    @router.post("/session/password", status_code=201)
    def change_password(
        body: PasswordChange,
        request: Request,
        response: Response,
        owner: Annotated[Owner, Depends(require_owner)],
    ) -> dict[str, object]:
        _reserve_attempt(db, _source(request))
        with db.transaction() as conn:
            row = (
                conn.execute(
                    text("SELECT password_hash,auth_epoch FROM offerloom.owners WHERE id=:owner"),
                    {"owner": owner.id},
                )
                .mappings()
                .one()
            )
        if not verify_password(str(row["password_hash"]), body.current_password.get_secret_value()):
            raise Problem(401, "authentication_failed")
        encoded = hash_password(body.new_password.get_secret_value())
        with db.transaction() as conn:
            _lock_identity(conn)
            current = (
                conn.execute(
                    text(
                        "SELECT password_hash,auth_epoch FROM offerloom.owners WHERE id=:owner FOR UPDATE"
                    ),
                    {"owner": owner.id},
                )
                .mappings()
                .one()
            )
            if (
                current["password_hash"] != row["password_hash"]
                or current["auth_epoch"] != owner.auth_epoch
            ):
                raise Problem(401, "authentication_failed")
            conn.execute(
                text(
                    "UPDATE offerloom.owners SET password_hash=:encoded,auth_epoch=auth_epoch+1,version=version+1 WHERE id=:owner"
                ),
                {"owner": owner.id, "encoded": encoded},
            )
            _revoke(conn, owner.id)
            _reset_attempts(conn, _source(request))
            fresh = _new_session(conn, owner.id, response)
            audit(conn, owner.id, "password_changed", request.state.request_id)
        return fresh.response()

    return router
