# SPDX-License-Identifier: MIT OR Apache-2.0
"""Demo load seeds only an empty owner profile and never disturbs owner-changed state."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from test_config import environment
from test_identity import IdentityContext, _bootstrap
from test_identity import identity as identity
from test_profiles import _attest, _json, _mutate
from test_profiles import profile_owner as profile_owner

from bonafides.demo import load_demo
from bonafides.problems import Problem
from bonafides.profiles import SYNTHETIC_FACTS


def _profile(client: Any) -> dict[str, Any]:
    return _json(client.get("/api/v1/profile"))


def _workspace(context: IdentityContext) -> dict[str, Any]:
    with context.admin.transaction() as conn:
        return dict(
            conn.execute(text("SELECT workspace_version,updated_at FROM bonafides.owner_settings"))
            .mappings()
            .one()
        )


def _snapshot(context: IdentityContext) -> dict[str, Any]:
    """Include row identities so even an UPDATE to identical values is observable."""
    with context.admin.transaction() as conn:
        return {
            table: conn.execute(
                text(
                    "SELECT jsonb_agg(jsonb_build_object('row',to_jsonb(r),'xmin',r.xmin::text) "
                    f"ORDER BY to_jsonb(r)::text) FROM bonafides.{table} r"
                )
            ).scalar_one()
            for table in (
                "installation_state",
                "owners",
                "owner_settings",
                "profiles",
                "profile_revisions",
                "professional_claims",
                "claim_revisions",
                "evidence_records",
                "evidence_revisions",
                "evidence_classification_changes",
                "claim_evidence_links",
                "profile_revision_claims",
                "profile_revision_evidence",
                "outbox_events",
                "audit_events",
            )
        }


@pytest.mark.postgres
def test_load_without_owner_refuses_before_any_setup(identity: IdentityContext) -> None:
    with pytest.raises(Problem) as refused:
        load_demo(identity.admin)

    assert refused.value.code == "owner_setup_required"


@pytest.mark.postgres
def test_load_seeds_empty_profile_and_identical_rerun_changes_nothing(
    profile_owner: IdentityContext,
) -> None:
    with profile_owner.client() as client:
        csrf = str(_json(_bootstrap(profile_owner, client))["csrf_token"])
        before = _workspace(profile_owner)

        assert load_demo(profile_owner.admin) is True
        loaded = _profile(client)
        seeded = _workspace(profile_owner)
        assert seeded["workspace_version"] == before["workspace_version"] + 1
        assert seeded["updated_at"] > before["updated_at"]
        before_rerun = _snapshot(profile_owner)
        assert load_demo(profile_owner.admin) is False
        assert _snapshot(profile_owner) == before_rerun
        assert _workspace(profile_owner) == seeded

        assert _profile(client) == loaded
        claims = loaded["current_revision"]["claims"]
        assert sorted(claim["rendering"] for claim in claims) == sorted(SYNTHETIC_FACTS)
        edit = _mutate(client, profile_owner, csrf, "PUT", "/profile", loaded["version"], {})
        assert edit.status_code == 200, edit.text


@pytest.mark.postgres
def test_load_refuses_after_owner_edit_without_writing(profile_owner: IdentityContext) -> None:
    with profile_owner.client() as client:
        csrf = str(_json(_bootstrap(profile_owner, client))["csrf_token"])
        assert load_demo(profile_owner.admin) is True
        loaded = _profile(client)
        edited = _mutate(
            client,
            profile_owner,
            csrf,
            "PUT",
            "/profile",
            loaded["version"],
            {"headline": "Owner rewrote the headline"},
        )
        assert edited.status_code == 200, edited.text
        before = _profile(client)
        before_rows = _snapshot(profile_owner)

        with pytest.raises(Problem) as refused:
            load_demo(profile_owner.admin)

        assert refused.value.code == "demo_requires_empty_profile"
        assert _profile(client) == before
        assert _snapshot(profile_owner) == before_rows


@pytest.mark.postgres
def test_load_refuses_real_evidence_without_writing(profile_owner: IdentityContext) -> None:
    with profile_owner.client() as client:
        csrf = str(_json(_bootstrap(profile_owner, client))["csrf_token"])
        _attest(client, profile_owner, csrf)
        before = _profile(client)
        before_rows = _snapshot(profile_owner)

        with pytest.raises(Problem) as refused:
            load_demo(profile_owner.admin)

        assert refused.value.code == "demo_requires_empty_profile"
        assert _profile(client) == before
        assert _snapshot(profile_owner) == before_rows


@pytest.mark.postgres
def test_demo_seed_invalidates_empty_workspace_deletion_preview(
    profile_owner: IdentityContext,
) -> None:
    with profile_owner.client() as client:
        csrf = str(_json(_bootstrap(profile_owner, client))["csrf_token"])
        workspace = _json(client.get("/api/v1/portable-previews"))
        response = _mutate(
            client,
            profile_owner,
            csrf,
            "POST",
            "/portable-previews/deletion",
            workspace["workspace_version"],
            {"scope": "workspace"},
        )
        assert response.status_code == 201, response.text
        preview = _json(response)
        assert preview["plan"]["record_counts"]["professional_claims"] == 0
        assert load_demo(profile_owner.admin) is True
        loaded = _profile(client)
        approval = preview["approval"]

        stale = _mutate(
            client,
            profile_owner,
            csrf,
            "POST",
            f"/workflows/{preview['workflow_run_id']}/approval/approve",
            preview["version"],
            {
                "payload_sha256": approval["payload_sha256"],
                "destination_sha256": approval["destination_sha256"],
                "revision_id": approval["revision_id"],
                "target_version": approval["target_version"],
                "confirmation_phrase": approval["confirmation_phrase"],
                "confirmed": True,
            },
        )

        assert stale.status_code == 409, stale.text
        assert _json(stale)["code"] == "portable_preview_stale"
        assert _profile(client) == loaded
        inspected = _json(client.get(f"/api/v1/workflows/{preview['workflow_run_id']}/approval"))
        assert inspected["approved_at"] is None


@pytest.mark.postgres
def test_demo_cli_refuses_maintenance_without_writing(
    profile_owner: IdentityContext, tmp_path: Path
) -> None:
    with profile_owner.client() as client:
        assert _bootstrap(profile_owner, client).status_code == 201
    cli_root = tmp_path / "cli"
    cli_root.mkdir()
    cli_env = environment(cli_root)
    Path(cli_env["BONAFIDES_DATABASE_DSN_FILE"]).write_text(profile_owner.settings.database_dsn)
    with profile_owner.admin.transaction() as conn:
        conn.execute(
            text("UPDATE bonafides.installation_state SET maintenance_active=true WHERE singleton")
        )
    before = _snapshot(profile_owner)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "bonafides.cli", "demo", "load"],
            env={**os.environ, **cli_env, "BONAFIDES_MODE": "production"},
            capture_output=True,
            text=True,
            timeout=20,
        )

        assert result.returncode == 2, result.stdout + result.stderr
        assert "installation_maintenance" in result.stderr
        assert _snapshot(profile_owner) == before
    finally:
        with profile_owner.admin.transaction() as conn:
            conn.execute(
                text(
                    "UPDATE bonafides.installation_state SET maintenance_active=false WHERE singleton"
                )
            )


@pytest.mark.postgres
@pytest.mark.parametrize("relation", ["installation_state", "owner_settings"])
def test_demo_load_waits_for_admission_and_workspace_writers(
    profile_owner: IdentityContext, relation: str
) -> None:
    with profile_owner.client() as client:
        assert _bootstrap(profile_owner, client).status_code == 201
    before = _snapshot(profile_owner)
    workspace = _workspace(profile_owner)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with profile_owner.admin.transaction() as writer:
                writer_pid = writer.execute(text("SELECT pg_backend_pid()")).scalar_one()
                # Both relations contain only this fixture's singleton workspace.
                writer.execute(text(f"SELECT 1 FROM bonafides.{relation} FOR UPDATE"))
                loading = pool.submit(load_demo, profile_owner.admin)
                deadline = time.monotonic() + 3
                blocked = False
                while time.monotonic() < deadline and not loading.done():
                    with profile_owner.admin.transaction() as observer:
                        blocked = bool(
                            observer.execute(
                                text("""SELECT EXISTS(SELECT 1 FROM pg_stat_activity
                                WHERE :writer=ANY(pg_blocking_pids(pid)))"""),
                                {"writer": writer_pid},
                            ).scalar_one()
                        )
                    if blocked:
                        break
                    time.sleep(0.01)
                assert blocked, "Demo load bypassed the in-flight workspace/admission writer"
                if relation == "installation_state":
                    writer.execute(
                        text("""UPDATE bonafides.installation_state
                        SET maintenance_active=true WHERE singleton""")
                    )
            if relation == "installation_state":
                with pytest.raises(Problem) as refused:
                    loading.result(timeout=10)
                assert refused.value.code == "installation_maintenance"
            else:
                assert loading.result(timeout=10) is True
        if relation == "installation_state":
            with profile_owner.admin.transaction() as conn:
                conn.execute(
                    text("""UPDATE bonafides.installation_state
                    SET maintenance_active=false WHERE singleton""")
                )
            # Ignore the test's own maintenance flag updates, not the demo's rows.
            after = _snapshot(profile_owner)
            assert {k: v for k, v in after.items() if k != "installation_state"} == {
                k: v for k, v in before.items() if k != "installation_state"
            }
        else:
            assert (
                _workspace(profile_owner)["workspace_version"] == workspace["workspace_version"] + 1
            )
    finally:
        with profile_owner.admin.transaction() as conn:
            conn.execute(
                text(
                    "UPDATE bonafides.installation_state SET maintenance_active=false WHERE singleton"
                )
            )
