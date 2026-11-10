# SPDX-License-Identifier: MIT OR Apache-2.0
"""Real git/API/PostgreSQL boundaries: capture is not approval."""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path
from threading import Event
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_evidence_capture_sources import _GitHubGateway
from test_evidence_capture_sources import github_gateway as github_gateway
from test_identity import IdentityContext, _bootstrap
from test_identity import identity as identity
from test_profiles import _json, _mutate

from bonafides import evidence_capture
from bonafides.evidence_capture_config import CaptureSource
from bonafides.evidence_capture_sources import CaptureCandidate, collect_commits
from bonafides.portable_data import collect_owner_data, quarantine_records
from bonafides.problems import Problem
from bonafides.worker import Worker
from bonafides.workflows import claim


def _configure(identity: IdentityContext, path: Path, kind: str = "local_git") -> Path:
    repository = path / "repository"
    if kind == "local_git":
        repository.mkdir()
        for args in (
            ("init",),
            (
                "-c",
                "user.name=Synthetic",
                "-c",
                "user.email=synthetic@example.invalid",
                "commit",
                "--allow-empty",
                "-m",
                "Built the synthetic capture example",
            ),
        ):
            subprocess.run(["git", "-C", str(repository), *args], check=True, capture_output=True)
    config = path / "capture.json"
    config.write_text(
        json.dumps(
            {
                "version": 1,
                "sources": [
                    {
                        "id": "example",
                        "label": "Synthetic repository",
                        "kind": kind,
                        "repository": str(repository) if kind == "local_git" else "Example/Career",
                        "enabled": True,
                        "privacy_class": "private",
                        "employer_owned": False,
                        **(
                            {"token_file": str(path / "gateway-only-token")}
                            if kind == "github"
                            else {}
                        ),
                    }
                ],
            }
        )
    )
    config.chmod(0o600)
    identity.settings = replace(identity.settings, evidence_capture_config_file=config)
    return config


def _disable(config: Path) -> None:
    value = json.loads(config.read_text())
    value["sources"][0]["enabled"] = False
    config.write_text(json.dumps(value))


@pytest.mark.postgres
@pytest.mark.parametrize("kind", ["local_git", "github"])
def test_capture_requires_exact_owner_approval_and_preserves_history(
    identity: IdentityContext,
    tmp_path: Path,
    github_gateway: _GitHubGateway,
    kind: str,
) -> None:
    config = _configure(identity, tmp_path, kind)
    with identity.client() as client:
        boot = _json(_bootstrap(identity, client))
        csrf, owner = str(boot["csrf_token"]), UUID(str(boot["owner_id"]))
        public = client.get("/api/v1/evidence-capture/sources").json()["items"][0]
        assert "repository" not in public
        assert "token_file" not in public
        queued = _mutate(
            client, identity, csrf, "POST", "/evidence-capture/sources/example/scan", 1, {}
        )
        assert queued.status_code == 202
        workflow = UUID(queued.json()["workflow_id"])
        with identity.admin.transaction() as conn:
            task = claim(conn, uuid4())
        assert task is not None and task.id == workflow
        evidence_capture.execute_capture(identity.admin, identity.settings, task, Event())
        detail = client.get(f"/api/v1/evidence-capture/scans/{workflow}").json()
        candidate = detail["proposals"][0]
        if kind == "github":
            assert candidate["payload"] == {
                "text": github_gateway.commits[0]["message"],
                "source": "https://github.com/example/career/commit/"
                + github_gateway.commits[0]["sha"],
            }
            assert github_gateway.requests == ["/v1/github/example/career/commits?per_page=20"]
        assert not candidate["already_approved"] and detail["profile_version"] == 1
        untouched = client.get("/api/v1/profile").json()
        assert untouched["evidence"] == [] and untouched["current_revision"]["claims"] == []
        with identity.admin.transaction() as conn:
            assert (
                conn.execute(
                    text("SELECT count(*) FROM bonafides.model_runs WHERE owner_id=:owner"),
                    {"owner": owner},
                ).scalar_one()
                == 0
            )
        approval = {
            "candidate_id": candidate["id"],
            "privacy_class": "confidential",
            "employer_owned": True,
        }
        path = f"/evidence-capture/scans/{workflow}/approve"
        assert _mutate(client, identity, csrf, "POST", path, 9, approval).status_code == 412
        altered = {**approval, "candidate_id": "f" * 40}
        assert _mutate(client, identity, csrf, "POST", path, 1, altered).status_code == 404
        modified_payload = {**approval, "payload": {"text": "Replacement", "source": "Replacement"}}
        assert _mutate(client, identity, csrf, "POST", path, 1, modified_payload).status_code == 422
        key = uuid4().hex
        accepted = _mutate(client, identity, csrf, "POST", path, 1, approval, key)
        assert accepted.status_code == 200
        profile = accepted.json()
        evidence = profile["evidence"][0]
        assert evidence["revision"]["provenance"]["origin"] == "evidence_capture"
        assert evidence["revision"]["provenance"]["privacy_class"] == "confidential"
        assert evidence["revision"]["provenance"]["employer_owned"] is True
        expected_source = (
            f"local-git:example@{candidate['id']}"
            if kind == "local_git"
            else f"https://github.com/example/career/commit/{candidate['id']}"
        )
        assert evidence["revision"]["payload"]["source"] == expected_source
        assert evidence["revision"]["payload"] == candidate["payload"]
        assert profile["current_revision"]["claims"] == []
        replay = _mutate(client, identity, csrf, "POST", path, 1, approval, key)
        assert replay.status_code == 200 and replay.json() == profile
        assert _mutate(client, identity, csrf, "POST", path, 1, altered, key).status_code == 409
        edited = _mutate(
            client,
            identity,
            csrf,
            "PUT",
            f"/evidence/{evidence['id']}",
            profile["version"],
            {
                "payload": {
                    "text": "Owner revised the description",
                    "source": "Owner-entered source",
                },
                "provenance": {"source_type": "work_sample"},
            },
        )
        assert edited.status_code == 200
        assert edited.json()["evidence"][0]["revision"]["provenance"]["origin"] == "owner_entry"
        archived = _mutate(
            client,
            identity,
            csrf,
            "DELETE",
            f"/evidence/{evidence['id']}",
            edited.json()["version"],
        )
        assert archived.status_code == 200
        duplicate = _mutate(
            client, identity, csrf, "POST", path, archived.json()["version"], approval
        )
        assert (
            duplicate.status_code == 409 and duplicate.json()["code"] == "capture_already_approved"
        )
        detail = client.get(f"/api/v1/evidence-capture/scans/{workflow}").json()
        assert detail["proposals"][0]["already_approved"]
        assert detail["profile_version"] == archived.json()["version"]
        repeated = _mutate(
            client,
            identity,
            csrf,
            "POST",
            "/evidence-capture/sources/example/scan",
            archived.json()["version"],
            {},
        )
        assert repeated.status_code == 202
        with identity.admin.transaction() as conn:
            repeated_task = claim(conn, uuid4())
        assert repeated_task is not None
        evidence_capture.execute_capture(identity.admin, identity.settings, repeated_task, Event())
        repeated_path = f"/evidence-capture/scans/{repeated_task.id}/approve"
        assert (
            _mutate(
                client, identity, csrf, "POST", repeated_path, archived.json()["version"], approval
            ).json()["code"]
            == "capture_already_approved"
        )
        if kind == "github":
            configured = json.loads(config.read_text())
            configured["sources"][0].update(id="renamed", repository="EXAMPLE/CAREER")
            config.write_text(json.dumps(configured))
            renamed = _mutate(
                client,
                identity,
                csrf,
                "POST",
                "/evidence-capture/sources/renamed/scan",
                archived.json()["version"],
                {},
            )
            assert renamed.status_code == 202
            with identity.admin.transaction() as conn:
                renamed_task = claim(conn, uuid4())
            assert renamed_task is not None
            evidence_capture.execute_capture(
                identity.admin, identity.settings, renamed_task, Event()
            )
            renamed_detail = client.get(f"/api/v1/evidence-capture/scans/{renamed_task.id}").json()
            assert renamed_detail["proposals"][0]["already_approved"] is True
            assert renamed_detail["proposals"][0]["payload"] == candidate["payload"]
            denied = _mutate(
                client,
                identity,
                csrf,
                "POST",
                f"/evidence-capture/scans/{renamed_task.id}/approve",
                archived.json()["version"],
                approval,
            )
            assert (denied.status_code, denied.json()["code"]) == (409, "capture_already_approved")
        with identity.admin.transaction() as conn:
            conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
            archive = collect_owner_data(conn, identity.settings, owner)
        assert archive.manifest.source_schema_version == 1
        with identity.admin.transaction() as conn:
            quarantine_records(conn, archive)
        revisions = archive.manifest.records["evidence_revisions"]
        assert any(
            row["origin"] == "evidence_capture" and row["payload"] == candidate["payload"]
            for row in revisions
        )


@pytest.mark.postgres
@pytest.mark.parametrize("kind", ["local_git", "github"])
@pytest.mark.parametrize("boundary", ["config", "cancel", "fence", "namespace"])
def test_changed_authority_cannot_publish_proposals(
    identity: IdentityContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
    github_gateway: _GitHubGateway,
    kind: str,
) -> None:
    config = _configure(identity, tmp_path, kind)
    with identity.client() as client:
        csrf = str(_json(_bootstrap(identity, client))["csrf_token"])
        queued = _mutate(
            client, identity, csrf, "POST", "/evidence-capture/sources/example/scan", 1, {}
        )
        workflow = UUID(queued.json()["workflow_id"])
        with identity.admin.transaction() as conn:
            task = claim(conn, uuid4())
            original_namespace = conn.execute(
                text("SELECT namespace_id FROM bonafides.installation_state WHERE singleton")
            ).scalar_one()
        assert task is not None
        collect = collect_commits

        def change_after_read(source: CaptureSource, interrupted: Event) -> list[CaptureCandidate]:
            candidates = collect(source, interrupted)
            if boundary == "config":
                _disable(config)
            else:
                with identity.admin.transaction() as conn:
                    if boundary == "namespace":
                        conn.execute(
                            text(
                                "UPDATE bonafides.installation_state SET namespace_id=:id WHERE singleton"
                            ),
                            {"id": uuid4()},
                        )
                    elif boundary == "cancel":
                        conn.execute(
                            text(
                                "UPDATE bonafides.workflow_runs SET cancel_requested_at=clock_timestamp() WHERE id=:id"
                            ),
                            {"id": workflow},
                        )
                    else:
                        conn.execute(
                            text("UPDATE bonafides.workflow_runs SET fence=fence+1 WHERE id=:id"),
                            {"id": workflow},
                        )
            return candidates

        monkeypatch.setattr(evidence_capture, "collect_commits", change_after_read)
        try:
            with pytest.raises((Problem, DBAPIError)):
                evidence_capture.execute_capture(identity.admin, identity.settings, task, Event())
            with identity.admin.transaction() as conn:
                row = conn.execute(
                    text("SELECT state,result FROM bonafides.workflow_runs WHERE id=:id"),
                    {"id": workflow},
                ).one()
                assert row.state != "succeeded" and row.result is None
                assert (
                    conn.execute(
                        text("SELECT count(*) FROM bonafides.evidence_records")
                    ).scalar_one()
                    == 0
                )
        finally:
            with identity.admin.transaction() as conn:
                conn.execute(
                    text(
                        "UPDATE bonafides.installation_state SET namespace_id=:id WHERE singleton"
                    ),
                    {"id": original_namespace},
                )


@pytest.mark.postgres
@pytest.mark.parametrize("kind", ["local_git", "github"])
def test_completed_scan_rejects_changed_configuration_and_unknown_workflow(
    identity: IdentityContext,
    tmp_path: Path,
    github_gateway: _GitHubGateway,
    kind: str,
) -> None:
    config = _configure(identity, tmp_path, kind)
    with identity.client() as client:
        csrf = str(_json(_bootstrap(identity, client))["csrf_token"])
        queued = _mutate(
            client, identity, csrf, "POST", "/evidence-capture/sources/example/scan", 1, {}
        )
        workflow = UUID(queued.json()["workflow_id"])
        approval = {"candidate_id": "a" * 40, "privacy_class": "private", "employer_owned": False}
        path = f"/evidence-capture/scans/{workflow}/approve"
        assert (
            _mutate(client, identity, csrf, "POST", path, 1, approval).json()["code"]
            == "capture_scan_not_succeeded"
        )
        assert (
            _mutate(
                client,
                identity,
                csrf,
                "POST",
                f"/evidence-capture/scans/{uuid4()}/approve",
                1,
                approval,
            ).status_code
            == 404
        )
        with identity.admin.transaction() as conn:
            task = claim(conn, uuid4())
        assert task is not None
        evidence_capture.execute_capture(identity.admin, identity.settings, task, Event())
        detail = client.get(f"/api/v1/evidence-capture/scans/{workflow}").json()
        approval["candidate_id"] = detail["proposals"][0]["id"]
        _disable(config)
        changed = client.get(f"/api/v1/evidence-capture/scans/{workflow}").json()
        assert changed["source_changed"] is True
        denied = _mutate(client, identity, csrf, "POST", path, 1, approval)
        assert denied.status_code == 409 and denied.json()["code"] == "capture_source_changed"
        assert client.get("/api/v1/profile").json()["evidence"] == []


@pytest.mark.postgres
@pytest.mark.parametrize(
    "field,value",
    [
        ("repository", "other/repository"),
        ("token_file", "/private/changed-token-file"),
        ("privacy_class", "confidential"),
        ("employer_owned", True),
        ("label", "Changed source label"),
    ],
)
def test_github_approval_rejects_changed_frozen_configuration(
    identity: IdentityContext,
    tmp_path: Path,
    github_gateway: _GitHubGateway,
    field: str,
    value: object,
) -> None:
    config = _configure(identity, tmp_path, "github")
    with identity.client() as client:
        csrf = str(_json(_bootstrap(identity, client))["csrf_token"])
        queued = _mutate(
            client, identity, csrf, "POST", "/evidence-capture/sources/example/scan", 1, {}
        )
        workflow = UUID(queued.json()["workflow_id"])
        with identity.admin.transaction() as conn:
            task = claim(conn, uuid4())
        assert task is not None
        evidence_capture.execute_capture(identity.admin, identity.settings, task, Event())
        original = client.get(f"/api/v1/evidence-capture/scans/{workflow}").json()
        configured = json.loads(config.read_text())
        configured["sources"][0][field] = value
        config.write_text(json.dumps(configured))
        approval = {
            "candidate_id": original["proposals"][0]["id"],
            "privacy_class": "private",
            "employer_owned": False,
        }
        denied = _mutate(
            client,
            identity,
            csrf,
            "POST",
            f"/evidence-capture/scans/{workflow}/approve",
            1,
            approval,
        )
        assert (denied.status_code, denied.json()["code"]) == (409, "capture_source_changed")
        assert client.get("/api/v1/profile").json()["evidence"] == []
        assert github_gateway.requests == ["/v1/github/example/career/commits?per_page=20"]


@pytest.mark.postgres
def test_disabled_github_source_cannot_enqueue_or_contact_gateway(
    identity: IdentityContext, tmp_path: Path, github_gateway: _GitHubGateway
) -> None:
    config = _configure(identity, tmp_path, "github")
    _disable(config)
    with identity.client() as client:
        csrf = str(_json(_bootstrap(identity, client))["csrf_token"])
        denied = _mutate(
            client, identity, csrf, "POST", "/evidence-capture/sources/example/scan", 1, {}
        )
        assert (denied.status_code, denied.json()["code"]) == (409, "capture_source_disabled")
        assert client.get("/api/v1/evidence-capture/scans").json()["items"] == []
        assert github_gateway.requests == []


@pytest.mark.postgres
@pytest.mark.parametrize(
    "code", ["source_rate_limited", "source_forbidden", "source_invalid_response"]
)
def test_github_source_failure_is_durable_and_manual_rescan_recovers(
    identity: IdentityContext, tmp_path: Path, github_gateway: _GitHubGateway, code: str
) -> None:
    _configure(identity, tmp_path, "github")
    github_gateway.status = 502
    github_gateway.raw = json.dumps({"code": code, "retry_after_seconds": 60}).encode()
    with identity.client() as client:
        csrf = str(_json(_bootstrap(identity, client))["csrf_token"])
        queued = _mutate(
            client, identity, csrf, "POST", "/evidence-capture/sources/example/scan", 1, {}
        )
        workflow = UUID(queued.json()["workflow_id"])
        with identity.admin.transaction() as conn:
            task = claim(conn, uuid4())
        assert task is not None
        worker = Worker(identity.admin, identity.settings)
        worker._execute_active(task, Event())
        detail = client.get(f"/api/v1/evidence-capture/scans/{workflow}").json()
        assert detail["workflow"]["state"] == "permanent_failure"
        assert detail["workflow"]["last_error_code"] == code
        assert detail["proposals"] == []
        assert client.get("/api/v1/profile").json()["evidence"] == []
        with identity.admin.transaction() as conn:
            assert claim(conn, uuid4()) is None
        github_gateway.status, github_gateway.raw = 200, None
        retried = _mutate(
            client, identity, csrf, "POST", "/evidence-capture/sources/example/scan", 1, {}
        )
        assert retried.status_code == 202
        with identity.admin.transaction() as conn:
            retry_task = claim(conn, uuid4())
        assert retry_task is not None
        worker._execute_active(retry_task, Event())
        recovered = client.get(f"/api/v1/evidence-capture/scans/{retry_task.id}").json()
        assert recovered["workflow"]["state"] == "succeeded"
        assert recovered["proposals"][0]["payload"] == {
            "text": github_gateway.commits[0]["message"],
            "source": "https://github.com/example/career/commit/"
            + github_gateway.commits[0]["sha"],
        }
