# SPDX-License-Identifier: MIT OR Apache-2.0
from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from greenhouse_fixture import FixtureUpstream, Scenario
from sqlalchemy import text
from test_identity import IdentityContext, _bootstrap, _headers
from test_identity import identity as identity

from bonafides import __version__, operations
from bonafides.db import SCHEMA_VERSION
from bonafides.execution_guard import ExecutionGuard
from bonafides.greenhouse import GreenhouseClient, SourceFailure, _Budget
from bonafides.installation_restore import _revoke_restored_authority
from bonafides.release_updates import MAX_BYTES, RELEASE_ROOT, Release, ReleaseClient
from bonafides.source_gateway import SourceGatewayServer

ROOT = "/api/v1/operations"


def _release(tag: str = "v99.0.0", **changes: object) -> bytes:
    return json.dumps(
        {
            "tag_name": tag,
            "html_url": RELEASE_ROOT + tag,
            "draft": False,
            "prerelease": False,
            **changes,
        }
    ).encode()


@pytest.fixture
def network() -> Iterator[tuple[FixtureUpstream, ReleaseClient]]:
    with FixtureUpstream(certificate_host="api.github.com") as upstream:
        upstream.scenario = Scenario(body=_release())
        direct = ReleaseClient(
            resolver=lambda host, port: ("8.8.8.8",),
            ssl_context=upstream.context,
            address_map=lambda address, port: upstream.address,
        )
        with SourceGatewayServer(
            ("127.0.0.1", 0), GreenhouseClient(), release_client=direct
        ) as server:
            thread = threading.Thread(
                target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
            )
            thread.start()
            relay = ReleaseClient(
                relay=True,
                resolver=lambda host, port: ("172.30.0.2",),
                address_map=lambda address, port: ("127.0.0.1", server.server_address[1]),
            )
            try:
                yield upstream, relay
            finally:
                server.shutdown()
                thread.join(timeout=2)


@pytest.fixture
def updates(
    identity: IdentityContext,
    network: tuple[FixtureUpstream, ReleaseClient],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[IdentityContext]:
    monkeypatch.setattr(operations, "ReleaseClient", lambda **kwargs: network[1])
    reset = text("""UPDATE bonafides.installation_updates SET enabled=false,version=1,
        last_checked_at=NULL,automatic_day=NULL,outcome='never',latest_version=NULL,
        latest_url=NULL,dismissed_version=NULL,error_code=NULL,lease_id=NULL,lease_until=NULL""")
    with identity.admin.transaction() as conn:
        conn.execute(reset)
    try:
        yield identity
    finally:
        with identity.admin.transaction() as conn:
            conn.execute(reset)


def test_real_tls_fixed_release_route_no_credentials_or_proxy(
    network: tuple[FixtureUpstream, ReleaseClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upstream, client = network
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9")
    assert client.read() == Release("99.0.0", RELEASE_ROOT + "v99.0.0")
    assert upstream.server_names == ["api.github.com"]
    request = upstream.requests[0]
    assert (request.method, request.path) == ("GET", "/repos/Mathews-Tom/BonaFides/releases/latest")
    assert request.headers["Host"] == "api.github.com"
    assert not {name.lower() for name in request.headers} & {
        "authorization",
        "cookie",
        "proxy-authorization",
    }


@pytest.mark.parametrize(
    "body",
    [
        _release("v99.0.0-rc.1"),
        _release("v099.0.0"),
        _release("v99.0.0", draft=True),
        _release("v99.0.0", prerelease=True),
        _release(html_url="https://example.com/release"),
        _release(html_url=RELEASE_ROOT + "v98.0.0"),
        b"null",
        b"not JSON",
    ],
)
def test_invalid_release_cannot_become_notice(
    network: tuple[FixtureUpstream, ReleaseClient], body: bytes
) -> None:
    network[0].scenario = Scenario(body=body)
    with pytest.raises(SourceFailure):
        network[1].read()


@pytest.mark.parametrize(
    "scenario",
    [
        Scenario(status=302, headers={"Location": "https://example.com"}),
        Scenario(status=500),
        Scenario(body=b" " * (MAX_BYTES + 1)),
    ],
)
def test_release_redirect_http_and_size_bounds(
    network: tuple[FixtureUpstream, ReleaseClient], scenario: Scenario
) -> None:
    network[0].scenario = scenario
    with pytest.raises(SourceFailure):
        network[1].read()
    assert len(network[0].requests) == 1


def test_release_deadline_closes_real_pending_relay(
    network: tuple[FixtureUpstream, ReleaseClient],
) -> None:
    network[0].scenario = Scenario(body=_release(), delay_seconds=1)
    with pytest.raises(SourceFailure, match="source_timeout"):
        network[1].read(budget=_Budget(None, seconds=0.15))
    assert network[0].request_received.is_set()


@pytest.mark.postgres
def test_owner_readonly_opt_in_daily_and_exact_dismissal(
    updates: IdentityContext,
    network: tuple[FixtureUpstream, ReleaseClient],
) -> None:
    upstream = network[0]
    with updates.client() as client:
        assert client.get(ROOT).status_code == 401
        for method, path, body in [
            ("PUT", "/updates", {"enabled": True, "version": 1}),
            ("POST", "/updates/check", {"automatic": False}),
            ("POST", "/updates/dismiss", {"version": "99.0.0"}),
        ]:
            assert (
                client.request(
                    method, ROOT + path, json=body, headers={"Origin": updates.settings.origin}
                ).status_code
                == 401
            )
        csrf = _bootstrap(updates, client).json()["csrf_token"]
        headers = _headers(updates, csrf)
        before = client.get(ROOT).json()
        assert (before["version"], before["schema_version"], before["database"]) == (
            __version__,
            SCHEMA_VERSION,
            "ready",
        )
        assert not before["updates"]["enabled"]
        assert (
            client.post(
                ROOT + "/updates/check", json={"automatic": False}, headers=headers
            ).status_code
            == 409
        )
        assert not upstream.requests
        assert (
            client.put(ROOT + "/updates", json={"enabled": True, "version": 1}).status_code == 403
        )
        with updates.admin.transaction() as conn:
            settings = conn.execute(
                text("SELECT version FROM bonafides.owner_settings")
            ).scalar_one()
            session_seen = conn.execute(
                text("""UPDATE bonafides.sessions
                SET last_seen_at=now()-interval '2 minutes' RETURNING last_seen_at""")
            ).scalar_one()
        for _ in range(2):
            assert client.get(ROOT).json() == before
        with updates.admin.transaction() as conn:
            assert (
                conn.execute(text("SELECT last_seen_at FROM bonafides.sessions")).scalar_one()
                == session_seen
            )
            career_sql = text("""SELECT (SELECT count(*) FROM bonafides.profile_revisions),
                (SELECT count(*) FROM bonafides.model_runs),(SELECT count(*) FROM bonafides.workflow_runs)""")
            career = conn.execute(career_sql).one()
        enabled = client.put(
            ROOT + "/updates", json={"enabled": True, "version": 1}, headers=headers
        )
        assert enabled.json()["version"] == 2
        assert (
            client.put(
                ROOT + "/updates", json={"enabled": False, "version": 1}, headers=headers
            ).status_code
            == 409
        )
        first = client.post(
            ROOT + "/updates/check", json={"automatic": True}, headers=headers
        ).json()
        assert first["outcome"] == "newer" and first["notice"]
        assert (
            client.post(ROOT + "/updates/check", json={"automatic": True}, headers=headers).json()
            == first
        )
        assert len(upstream.requests) == 1
        assert (
            client.post(
                ROOT + "/updates/dismiss", json={"version": "98.0.0"}, headers=headers
            ).status_code
            == 409
        )
        assert not client.post(
            ROOT + "/updates/dismiss", json={"version": "99.0.0"}, headers=headers
        ).json()["notice"]
        upstream.scenario = Scenario(body=_release("v99.0.1"))
        later = client.post(
            ROOT + "/updates/check", json={"automatic": False}, headers=headers
        ).json()
        assert later["notice"] and later["dismissed_version"] == "99.0.0"
        assert (
            client.post(
                ROOT + "/updates/dismiss", json={"version": "99.0.0"}, headers=headers
            ).status_code
            == 409
        )
        upstream.scenario = Scenario(status=503)
        failed = client.post(
            ROOT + "/updates/check", json={"automatic": False}, headers=headers
        ).json()
        assert failed["outcome"] == "unknown" and failed["notice"] and failed["error_code"]
        assert (failed["latest_version"], failed["latest_url"]) == (
            later["latest_version"],
            later["latest_url"],
        )
        with updates.admin.transaction() as conn:
            assert (
                conn.execute(text("SELECT version FROM bonafides.owner_settings")).scalar_one()
                == settings
            )
            assert conn.execute(career_sql).one() == career


@pytest.mark.postgres
@pytest.mark.parametrize(
    "scenario,outcome",
    [
        (Scenario(status=404), "no_release"),
        (Scenario(body=_release(__version__)), "current"),
        (Scenario(body=_release(html_url="https://example.com/update")), "unknown"),
        (Scenario(body=_release("v99.0.0-rc.1")), "unknown"),
        (Scenario(status=503), "unknown"),
    ],
)
def test_release_outcomes_remain_truthful(
    updates: IdentityContext,
    network: tuple[FixtureUpstream, ReleaseClient],
    scenario: Scenario,
    outcome: str,
) -> None:
    network[0].scenario = scenario
    with updates.client() as client:
        headers = _headers(updates, _bootstrap(updates, client).json()["csrf_token"])
        client.put(ROOT + "/updates", json={"enabled": True, "version": 1}, headers=headers)
        result = client.post(ROOT + "/updates/check", json={"automatic": False}, headers=headers)
        assert result.status_code == 200 and result.json()["outcome"] == outcome
        assert result.json()["notice"] is False
        assert (result.json()["error_code"] is not None) == (outcome == "unknown")


@pytest.mark.postgres
def test_overlap_disable_reenable_and_expired_lease_fence(
    updates: IdentityContext,
    network: tuple[FixtureUpstream, ReleaseClient],
) -> None:
    upstream = network[0]
    upstream.scenario = Scenario(body=_release(), delay_seconds=0.8)
    with updates.client() as client, ThreadPoolExecutor(max_workers=1) as pool:
        headers = _headers(updates, _bootstrap(updates, client).json()["csrf_token"])
        client.put(ROOT + "/updates", json={"enabled": True, "version": 1}, headers=headers)
        for disable in (True, False):
            upstream.request_received.clear()
            pending = pool.submit(
                client.post, ROOT + "/updates/check", json={"automatic": True}, headers=headers
            )
            assert upstream.request_received.wait(3)
            assert client.get(ROOT).json()["updates"]["outcome"] == "checking"
            assert (
                client.post(
                    ROOT + "/updates/check", json={"automatic": False}, headers=headers
                ).status_code
                == 409
            )
            if disable:
                assert (
                    client.put(
                        ROOT + "/updates", json={"enabled": False, "version": 2}, headers=headers
                    ).status_code
                    == 200
                )
                assert (
                    client.put(
                        ROOT + "/updates", json={"enabled": True, "version": 3}, headers=headers
                    ).status_code
                    == 200
                )
                assert (
                    client.post(
                        ROOT + "/updates/check", json={"automatic": False}, headers=headers
                    ).status_code
                    == 409
                )
            else:
                with updates.admin.transaction() as conn:
                    conn.execute(
                        text(
                            "UPDATE bonafides.installation_updates SET lease_until=clock_timestamp()-interval '1 second'"
                        )
                    )
            assert pending.result(timeout=4).status_code == 409
            state = client.get(ROOT).json()["updates"]
            assert state["latest_version"] is None
            assert state["outcome"] != "checking"
            with updates.admin.transaction() as conn:
                conn.execute(text("UPDATE bonafides.installation_updates SET automatic_day=NULL"))
        # Even an abandoned check reserved its UTC day before I/O.
        upstream.scenario = Scenario(status=503)
        client.post(ROOT + "/updates/check", json={"automatic": True}, headers=headers)
        count = len(upstream.requests)
        assert (
            client.post(
                ROOT + "/updates/check", json={"automatic": True}, headers=headers
            ).status_code
            == 200
        )
        assert len(upstream.requests) == count


@pytest.mark.postgres
def test_unready_database_is_not_green(updates: IdentityContext) -> None:
    with updates.client() as client:
        _bootstrap(updates, client)
        with updates.admin.transaction() as conn:
            checksum = conn.execute(
                text(
                    "DELETE FROM bonafides.schema_migrations WHERE version=5 RETURNING source_sha256"
                )
            ).scalar_one()
        try:
            assert client.get(ROOT).status_code == 503
        finally:
            with updates.admin.transaction() as conn:
                conn.execute(
                    text(
                        "INSERT INTO bonafides.schema_migrations(version,source_sha256) VALUES(5,:sha)"
                    ),
                    {"sha": checksum},
                )


@pytest.mark.postgres
def test_native_restore_revokes_opt_in_and_pending_publication_without_erasing_cache(
    updates: IdentityContext,
) -> None:
    with updates.client() as client:
        headers = _headers(updates, _bootstrap(updates, client).json()["csrf_token"])
        client.put(ROOT + "/updates", json={"enabled": True, "version": 1}, headers=headers)
        cached = client.post(
            ROOT + "/updates/check", json={"automatic": False}, headers=headers
        ).json()
    with (
        ExecutionGuard(updates.settings.blob_root, exclusive=True, create=False) as guard,
        updates.admin.engine.connect() as conn,
    ):
        transaction = conn.begin()
        conn.info["execution_guard"] = guard
        try:
            conn.execute(
                text("""UPDATE bonafides.installation_updates
                SET lease_id=:lease,lease_until=clock_timestamp()+interval '10 seconds'"""),
                {"lease": uuid4()},
            )
            _revoke_restored_authority(conn, uuid4(), uuid4())
            row = operations._state(operations._row(conn))
            assert row["enabled"] is False and row["outcome"] == "unknown"
            assert row["version"] == cached["version"] + 1 and not row["notice"]
            assert (row["latest_version"], row["latest_url"]) == (
                cached["latest_version"],
                cached["latest_url"],
            )
            assert (
                conn.execute(
                    text("SELECT lease_id FROM bonafides.installation_updates")
                ).scalar_one()
                is None
            )
        finally:
            transaction.rollback()
            conn.info.pop("execution_guard")


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("checked_version", "running_version", "outcome"),
    [("0.1.0", "0.2.0", "current"), ("0.2.0", "0.1.0", "newer")],
)
def test_cached_release_compares_current_installation_after_version_change(
    updates: IdentityContext,
    network: tuple[FixtureUpstream, ReleaseClient],
    monkeypatch: pytest.MonkeyPatch,
    checked_version: str,
    running_version: str,
    outcome: str,
) -> None:
    upstream, _ = network
    upstream.scenario = Scenario(body=_release("v0.2.0"))
    monkeypatch.setattr(operations, "__version__", checked_version)
    with updates.client() as client:
        headers = _headers(updates, _bootstrap(updates, client).json()["csrf_token"])
        client.put(ROOT + "/updates", json={"enabled": True, "version": 1}, headers=headers)
        client.post(ROOT + "/updates/check", json={"automatic": True}, headers=headers)
        count = len(upstream.requests)
        monkeypatch.setattr(operations, "__version__", running_version)
        state = client.post(
            ROOT + "/updates/check", json={"automatic": True}, headers=headers
        ).json()
        assert state["outcome"] == outcome
        assert state["notice"] is (outcome == "newer")
        assert len(upstream.requests) == count
