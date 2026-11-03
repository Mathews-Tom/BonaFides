# SPDX-License-Identifier: MIT OR Apache-2.0
"""Demo load seeds only an empty owner profile and never disturbs owner-changed state."""

from __future__ import annotations

from typing import Any

import pytest
from test_identity import IdentityContext, _bootstrap
from test_identity import identity as identity
from test_profiles import _attest, _json, _mutate
from test_profiles import profile_owner as profile_owner

from bonafides.demo import load_demo
from bonafides.problems import Problem
from bonafides.profiles import SYNTHETIC_FACTS


def _profile(client: Any) -> dict[str, Any]:
    return _json(client.get("/api/v1/profile"))


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

        assert load_demo(profile_owner.admin) is True
        loaded = _profile(client)
        assert load_demo(profile_owner.admin) is False

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

        with pytest.raises(Problem) as refused:
            load_demo(profile_owner.admin)

        assert refused.value.code == "demo_requires_empty_profile"
        assert _profile(client) == before


@pytest.mark.postgres
def test_load_refuses_real_evidence_without_writing(profile_owner: IdentityContext) -> None:
    with profile_owner.client() as client:
        csrf = str(_json(_bootstrap(profile_owner, client))["csrf_token"])
        _attest(client, profile_owner, csrf)
        before = _profile(client)

        with pytest.raises(Problem) as refused:
            load_demo(profile_owner.admin)

        assert refused.value.code == "demo_requires_empty_profile"
        assert _profile(client) == before
