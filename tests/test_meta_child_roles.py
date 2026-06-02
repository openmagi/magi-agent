from __future__ import annotations

import pytest
from pydantic import ValidationError

from openmagi_core_agent.meta_orchestration.child_roles import (
    MetaChildRoleDefinition,
    MetaChildRoleRegistry,
)


def _role(**updates: object) -> MetaChildRoleDefinition:
    payload: dict[str, object] = {
        "roleRef": "role:generic-reviewer",
        "displayName": "Generic reviewer",
        "domain": "generic",
        "allowedToolRefs": ("tool:readonly-kb", "tool:readonly-search"),
        "deniedToolRefs": ("tool:mutable-workspace-write",),
        "contextPolicyRef": "context:summary-only",
        "completionContractRef": "contract:evidence-envelope",
        "maxSpawnDepth": 1,
    }
    payload.update(updates)
    return MetaChildRoleDefinition.model_validate(payload)


def test_role_registry_rejects_duplicate_role_refs() -> None:
    duplicate_a = _role(roleRef="role:duplicate")
    duplicate_b = _role(roleRef="role:duplicate", displayName="Duplicate reviewer")

    with pytest.raises(ValueError, match="duplicate roleRef"):
        MetaChildRoleRegistry((duplicate_a, duplicate_b))


def test_role_grants_are_explicit_and_deterministic() -> None:
    role = _role(
        allowedToolRefs=("tool:zeta-readonly", "tool:alpha-readonly"),
        deniedToolRefs=("tool:delta-muted", "tool:beta-muted"),
    )
    registry = MetaChildRoleRegistry((role,))

    resolved = registry.require("role:generic-reviewer")

    assert resolved.allowed_tool_refs == ("tool:alpha-readonly", "tool:zeta-readonly")
    assert resolved.denied_tool_refs == ("tool:beta-muted", "tool:delta-muted")
    assert resolved.default_off is True
    assert registry.allowed_tool_refs_for("role:generic-reviewer") == (
        "tool:alpha-readonly",
        "tool:zeta-readonly",
    )

    with pytest.raises(ValidationError, match="allowedToolRefs must include"):
        _role(allowedToolRefs=())

    with pytest.raises(ValidationError, match="must not overlap"):
        _role(
            allowedToolRefs=("tool:readonly-kb",),
            deniedToolRefs=("tool:readonly-kb",),
        )


@pytest.mark.parametrize(
    "forbidden_ref",
    (
        "tool:Bash",
        "tool:shell.exec",
        "tool:browser.live-web",
        "tool:workspace-write",
        "tool:memory-write",
        "tool:channel-send.telegram",
        "tool:k8s.deploy",
        "tool:secrets.read",
        "tool:env.read",
        "tool:provisioning-worker",
        "tool:supabase.admin",
        "tool:frontend.deploy",
        "tool:chat-proxy.route",
        "tool:prod-route",
        "tool:workspace_write",
        "tool:memoryWrite",
        "tool:channelSend.telegram",
        "tool:liveWeb",
        "tool:supabaseAdmin",
        "tool:bashCommand",
        "tool:prodRoute",
    ),
)
def test_role_cannot_grant_forbidden_global_tools(forbidden_ref: str) -> None:
    with pytest.raises(ValidationError, match="forbidden global tool"):
        _role(allowedToolRefs=("tool:readonly-kb", forbidden_ref))


def test_role_domain_is_closed_to_supported_classifications() -> None:
    for domain in ("generic", "research", "coding", "backoffice", "custom"):
        assert _role(domain=domain).domain == domain

    with pytest.raises(ValidationError, match="domain must be"):
        _role(domain="sales")


def test_domain_specific_role_examples_live_in_harness_config_not_generic_core() -> None:
    harness_config_roles = MetaChildRoleRegistry(
        (
            _role(
                roleRef="role:research-scout",
                displayName="Research scout",
                domain="research",
                allowedToolRefs=("tool:readonly-web-snippet",),
                contextPolicyRef="context:research-claims-only",
            ),
            _role(
                roleRef="role:coding-reviewer",
                displayName="Coding reviewer",
                domain="coding",
                allowedToolRefs=("tool:readonly-repo-files",),
                contextPolicyRef="context:diff-and-tests-only",
            ),
            _role(
                roleRef="role:finance-reconciler",
                displayName="Finance reconciler",
                domain="backoffice",
                allowedToolRefs=("tool:readonly-ledger",),
                contextPolicyRef="context:records-only",
            ),
        )
    )

    assert harness_config_roles.role_refs() == (
        "role:coding-reviewer",
        "role:finance-reconciler",
        "role:research-scout",
    )
    assert MetaChildRoleRegistry().role_refs() == ()


def test_role_public_projection_is_digest_safe() -> None:
    role = _role(
        allowedToolRefs=("tool:readonly-alpha", "tool:readonly-zeta"),
        deniedToolRefs=("tool:internal-muted",),
    )

    projection = role.public_projection()
    projection_text = repr(projection)

    assert projection["roleRef"] == "role:generic-reviewer"
    assert projection["allowedToolCount"] == 2
    assert projection["deniedToolCount"] == 1
    assert projection["maxSpawnDepthDescriptiveOnly"] is True
    assert str(projection["toolGrantDigest"]).startswith("sha256:")
    assert "tool:readonly-alpha" not in projection_text
    assert "tool:readonly-zeta" not in projection_text
    assert "allowedToolRefs" not in projection
    assert "deniedToolRefs" not in projection
