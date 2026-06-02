from __future__ import annotations

import json
import subprocess
import sys

import pytest
from pydantic import ValidationError


def _request(
    *,
    permission: str = "web.fetch",
    resource_ref: str = "docs:adk",
    request_id: str = "perm:req:1",
):
    from magi_agent.recipes.opencode_permission_patterns import (
        OpenCodeResearchPermissionRequest,
    )

    return OpenCodeResearchPermissionRequest(
        requestId=request_id,
        sessionKey="session:research-permission-fixture",
        permission=permission,
        resourceRef=resource_ref,
    )


def _profile(*, soft_rules=(), hard_denies=()):
    from magi_agent.recipes.opencode_permission_patterns import (
        OpenCodeResearchPermissionProfile,
    )

    return OpenCodeResearchPermissionProfile(
        enabled=True,
        softRules=soft_rules,
        hardDenies=hard_denies,
    )


def test_research_permission_pattern_soft_wildcard_last_match_wins() -> None:
    from magi_agent.recipes.opencode_permission_patterns import (
        OpenCodeResearchPermissionRule,
        decide_opencode_research_permission,
    )

    request = _request(permission="web.fetch", resource_ref="docs:adk")
    deny_last = _profile(
        soft_rules=(
            OpenCodeResearchPermissionRule(
                permissionPattern="web.*",
                resourcePattern="docs:*",
                action="allow",
                reasonCode="broad_web_fixture_allow",
            ),
            OpenCodeResearchPermissionRule(
                permissionPattern="web.fetch",
                resourcePattern="docs:*",
                action="deny",
                reasonCode="fetch_fixture_denied",
            ),
        )
    )
    allow_last = _profile(
        soft_rules=(
            OpenCodeResearchPermissionRule(
                permissionPattern="web.fetch",
                resourcePattern="docs:*",
                action="deny",
                reasonCode="fetch_fixture_denied",
            ),
            OpenCodeResearchPermissionRule(
                permissionPattern="web.*",
                resourcePattern="docs:*",
                action="allow",
                reasonCode="broad_web_fixture_allow",
            ),
        )
    )

    denied = decide_opencode_research_permission(request, deny_last)
    allowed = decide_opencode_research_permission(request, allow_last)

    assert denied.action == "deny"
    assert denied.reason_codes == ("fetch_fixture_denied",)
    assert denied.matched_rule_index == 1
    assert allowed.action == "allow"
    assert allowed.reason_codes == ("broad_web_fixture_allow",)
    assert allowed.matched_rule_index == 1


def test_hard_safety_deny_beats_wildcards_and_always_approval() -> None:
    from magi_agent.recipes.opencode_permission_patterns import (
        OpenCodeResearchHardDeny,
        OpenCodeResearchPermissionRule,
        add_opencode_research_always_approval,
        decide_opencode_research_permission,
    )

    request = _request(permission="repo.clone", resource_ref="repo:blocked/project")
    profile = _profile(
        soft_rules=(
            OpenCodeResearchPermissionRule(
                permissionPattern="repo.*",
                resourcePattern="repo:*",
                action="allow",
                reasonCode="wildcard_repo_allow",
            ),
        ),
        hard_denies=(
            OpenCodeResearchHardDeny(
                permissionPattern="repo.clone",
                resourcePattern="repo:blocked/*",
                reasonCode="hard_repo_clone_denied",
            ),
        ),
    )

    denied = decide_opencode_research_permission(request, profile)
    approved_profile = add_opencode_research_always_approval(profile, request)
    still_denied = decide_opencode_research_permission(request, approved_profile)

    assert denied.action == "deny"
    assert denied.hard_deny_applied is True
    assert denied.reason_codes == ("hard_repo_clone_denied",)
    assert still_denied.action == "deny"
    assert still_denied.hard_deny_applied is True
    assert still_denied.reason_codes == ("hard_repo_clone_denied",)


def test_always_approval_adds_scoped_future_allow_only() -> None:
    from magi_agent.recipes.opencode_permission_patterns import (
        add_opencode_research_always_approval,
        decide_opencode_research_permission,
    )

    profile = _profile()
    request = _request(permission="repo.clone", resource_ref="repo:openmagi/magi")
    other_repo = _request(
        permission="repo.clone",
        resource_ref="repo:other/project",
        request_id="perm:req:other",
    )

    before = decide_opencode_research_permission(request, profile)
    approved_profile = add_opencode_research_always_approval(profile, request)
    after = decide_opencode_research_permission(request, approved_profile)
    unrelated = decide_opencode_research_permission(other_repo, approved_profile)

    assert before.action == "ask"
    assert after.action == "allow"
    assert after.reason_codes == ("always_approval_scoped_allow",)
    assert unrelated.action == "ask"
    assert unrelated.reason_codes == ("approval_required",)


def test_repo_clone_scope_and_external_repo_narrower_than_external_directory() -> None:
    from magi_agent.recipes.opencode_permission_patterns import (
        OpenCodeResearchPermissionRule,
        decide_opencode_research_permission,
    )

    profile = _profile(
        soft_rules=(
            OpenCodeResearchPermissionRule(
                permissionPattern="repo.clone",
                resourcePattern="repo:openmagi/magi",
                action="allow",
                reasonCode="single_repo_clone_allow",
            ),
            OpenCodeResearchPermissionRule(
                permissionPattern="read.external_repo",
                resourcePattern="repo:openmagi/magi",
                action="allow",
                reasonCode="single_external_repo_read_allow",
            ),
        )
    )

    clone_same = decide_opencode_research_permission(
        _request(permission="repo.clone", resource_ref="repo:openmagi/magi"),
        profile,
    )
    clone_other = decide_opencode_research_permission(
        _request(
            permission="repo.clone",
            resource_ref="repo:openmagi/other",
            request_id="perm:req:clone-other",
        ),
        profile,
    )
    external_repo = decide_opencode_research_permission(
        _request(
            permission="read.external_repo",
            resource_ref="repo:openmagi/magi",
            request_id="perm:req:external-repo",
        ),
        profile,
    )
    external_directory = decide_opencode_research_permission(
        _request(
            permission="read.external_directory",
            resource_ref="repo:openmagi/magi",
            request_id="perm:req:external-directory",
        ),
        profile,
    )

    assert clone_same.action == "allow"
    assert clone_other.action == "ask"
    assert external_repo.action == "allow"
    assert external_directory.action == "ask"


def test_rejecting_permission_request_cancels_sibling_pending_requests_in_session() -> None:
    from magi_agent.recipes.opencode_permission_patterns import (
        reject_opencode_research_permission_request,
    )
    from magi_agent.runtime.control import ControlRequestStore

    store = ControlRequestStore()
    first = store.create_tool_permission_request(
        session_key="session:alpha",
        turn_id="turn:1",
        channel_name=None,
        source="turn",
        prompt="approve repo clone",
        proposed_input={"repo": "repo:openmagi/magi"},
        idempotency_key="session-alpha:first",
        now=1,
        timeout_ms=100,
    ).record
    sibling = store.create_tool_permission_request(
        session_key="session:alpha",
        turn_id="turn:1",
        channel_name=None,
        source="turn",
        prompt="approve web fetch",
        proposed_input={"url": "https://example.test/docs"},
        idempotency_key="session-alpha:sibling",
        now=2,
        timeout_ms=100,
    ).record
    other_session = store.create_tool_permission_request(
        session_key="session:beta",
        turn_id="turn:1",
        channel_name=None,
        source="turn",
        prompt="approve repo overview",
        proposed_input={"repo": "repo:openmagi/other"},
        idempotency_key="session-beta:first",
        now=3,
        timeout_ms=100,
    ).record

    result = reject_opencode_research_permission_request(
        store,
        first.request_id,
        now=4,
    )

    assert result.rejected_request_id == first.request_id
    assert result.cancelled_request_ids == (sibling.request_id,)
    assert store.get_terminal(first.request_id).state == "denied"  # type: ignore[union-attr]
    assert store.get_terminal(sibling.request_id).state == "cancelled"  # type: ignore[union-attr]
    assert store.get_pending(other_session.request_id).state == "pending"  # type: ignore[union-attr]


def test_default_profile_has_no_production_or_broad_authority() -> None:
    from magi_agent.recipes.opencode_permission_patterns import (
        build_default_opencode_research_permission_profile,
        decide_opencode_research_permission,
    )

    profile = build_default_opencode_research_permission_profile()
    projection = profile.public_projection()
    rendered = json.dumps(projection, sort_keys=True)

    assert profile.enabled is False
    assert projection["defaultOff"] is True
    assert projection["localOnly"] is True
    assert projection["fixtureOnly"] is True
    assert projection["liveAuthorityAllowed"] is False
    assert projection["activationGate"] == "policy-fixture-only-hard-deny-preserved"
    assert projection["productionBroadAuthorityAllowed"] is False
    assert projection["productionRuleBroadGrants"] == []
    assert decide_opencode_research_permission(
        _request(permission="web.search", resource_ref="web:search"),
        profile,
    ).action == "deny"

    unsafe_fragments = (
        "productionAuthority",
        "liveToolDispatched",
        "adkRunnerInvoked",
        "modelCalled",
        "browserExecuted",
        "memoryWritten",
        "channelDelivered",
    )
    for fragment in unsafe_fragments:
        assert fragment not in rendered


def test_permission_request_rejects_globs_raw_paths_and_auth_callback_refs() -> None:
    from magi_agent.recipes.opencode_permission_patterns import (
        OpenCodeResearchPermissionRequest,
    )

    unsafe_refs = (
        "repo:*",
        "repo:foo?bar",
        "/Users/kevin/.ssh/id_rsa",
        "https://oauth.example.test/callback?code-secret-token",
        "repo:openmagi/magi?token=unsafe",
        "repo:openmagi/session-cookie",
        "repo:../../.ssh/id_rsa",
        "../.ssh/id_rsa",
        "repo:openmagi/magi/.env",
        "repo:openmagi/magi/auth.json",
        "repo:openmagi/magi/keys.json",
        "docs:private",
        "repo:openmagi/private",
        "docs:password",
        "workspace:passwd",
        "external-repo:password",
        "docs:privateKey",
        "docs:passwordHash",
        "workspace:passwdHash",
        "external-repo:credentialStore",
        "repo:openmagi/privateKey",
        "docs:env",
        "external-dir:idRsa",
        "workspace:creds",
    )
    for resource_ref in unsafe_refs:
        with pytest.raises(ValidationError):
            OpenCodeResearchPermissionRequest(
                requestId="perm:req:unsafe",
                sessionKey="session:research-permission-fixture",
                permission="repo.clone",
                resourceRef=resource_ref,
            )


def test_permission_projection_models_revalidate_copy_and_construct_updates() -> None:
    from magi_agent.recipes.opencode_permission_patterns import (
        OpenCodeResearchPermissionDecision,
        OpenCodeResearchPermissionProfile,
        build_default_opencode_research_permission_profile,
        decide_opencode_research_permission,
    )

    profile = build_default_opencode_research_permission_profile()
    decision = decide_opencode_research_permission(
        _request(permission="web.fetch", resource_ref="docs:adk"),
        profile,
    )

    with pytest.raises(ValidationError):
        profile.model_copy(update={"live_authority_allowed": True})
    with pytest.raises(ValidationError):
        OpenCodeResearchPermissionProfile.model_construct(liveAuthorityAllowed=True)
    with pytest.raises(ValidationError):
        decision.model_copy(update={"live_authority_allowed": True})
    with pytest.raises(ValidationError):
        OpenCodeResearchPermissionDecision.model_construct(
            status="allowed",
            action="allow",
            permission="web.fetch",
            resourceRef="/Users/kevin/.ssh/id_rsa",
            reasonCodes=("spoofed",),
            liveAuthorityAllowed=True,
        )


def test_research_permission_pattern_import_boundary_has_no_live_runtime_surfaces() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("magi_agent.recipes.opencode_permission_patterns")
forbidden = (
    "google.adk.runners",
    "google.adk.sessions",
    "google.adk.models",
    "magi_agent.adk_bridge.runner_adapter",
    "magi_agent.tools.dispatcher",
    "magi_agent.tools.registry",
    "magi_agent.transport.chat",
    "magi_agent.memory.adapters",
    "socket",
    "requests",
    "httpx",
)
loaded = [
    name
    for name in sys.modules
    if any(name == prefix or name.startswith(f"{prefix}.") for prefix in forbidden)
]
if loaded:
    raise AssertionError(f"OpenCode permission pattern loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
