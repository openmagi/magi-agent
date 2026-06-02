from __future__ import annotations

from datetime import UTC, datetime
import json
import subprocess
import sys

import pytest
from pydantic import BaseModel


DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
POLICY_DIGEST = "sha256:" + "c" * 64


def _guard(
    *,
    guard_id: str = "guard:tool-write",
    verdict: str = "deny",
    hard: bool = True,
    configured_mode: str = "enforce",
) -> dict[str, object]:
    return {
        "guardId": guard_id,
        "stage": "before_tool_call",
        "hardInvariant": hard,
        "deterministicVerdict": verdict,
        "configuredMode": configured_mode,
        "reasonCodes": ("workspace_mutation_blocked",),
        "evidenceRefs": ("evidence:permission-policy",),
    }


def _request(**overrides: object) -> object:
    from openmagi_core_agent.permissions.auto_control import AutoPermissionDecisionRequest

    data = {
        "requestId": "auto-permission:request-1",
        "actionRef": "tool:FileWrite",
        "actionDigest": DIGEST_A,
        "requestedPermissionRefs": ("permission:workspace-write",),
        "policySnapshotDigest": POLICY_DIGEST,
        "guardDecisions": (_guard(),),
        "adminPolicyRef": "admin-policy:default",
        "adminPolicyDigest": DIGEST_B,
        "metadata": {"safeRef": "permission:request"},
    }
    data.update(overrides)
    return AutoPermissionDecisionRequest.model_validate(data)


def _self_review(**overrides: object) -> object:
    from openmagi_core_agent.permissions.auto_control import AutoPermissionSelfReviewRecord

    data = {
        "reviewId": "auto-permission-review:1",
        "actionDigest": DIGEST_A,
        "recommendation": "allow",
        "confidence": "high",
        "reasonCodes": ("agent_review_allow",),
        "evidenceRefs": ("evidence:agent-self-review",),
        "policySnapshotDigest": POLICY_DIGEST,
        "createdAt": datetime(2026, 5, 26, tzinfo=UTC),
    }
    data.update(overrides)
    return AutoPermissionSelfReviewRecord.model_validate(data)


def test_auto_permission_default_off_has_no_authority_flags() -> None:
    from openmagi_core_agent.permissions.auto_control import (
        AutoPermissionConfig,
        evaluate_auto_permission,
    )

    decision = evaluate_auto_permission(_request(), config=AutoPermissionConfig())

    assert decision.status == "disabled"
    assert decision.allowed is False
    assert decision.reason_codes == ("auto_permission_control_disabled",)
    assert decision.authority_flags.model_dump(by_alias=True) == {
        "adkCallbackAttached": False,
        "toolHostBypassAllowed": False,
        "productionPolicyWrite": False,
        "frontendAdminAttached": False,
        "userVisibleAuthority": False,
        "routeAttached": False,
    }


def test_hard_guard_denial_is_non_bypassable_by_admin_or_self_review() -> None:
    from openmagi_core_agent.permissions.auto_control import (
        AutoPermissionConfig,
        evaluate_auto_permission,
    )

    decision = evaluate_auto_permission(
        _request(
            guardDecisions=(
                _guard(hard=True, verdict="deny", configured_mode="enforce"),
            ),
        ),
        config=AutoPermissionConfig(enabled=True),
        self_review=_self_review(recommendation="allow"),
    )
    projection = decision.public_projection()
    encoded = json.dumps(projection, sort_keys=True)

    assert decision.status == "denied"
    assert decision.allowed is False
    assert decision.requires_approval is False
    assert "hard_guard_denied" in decision.reason_codes
    assert projection["decisionDigest"].startswith("sha256:")
    assert projection["selfReviewDigest"].startswith("sha256:")
    assert "raw" not in encoded.lower()


def test_hard_invariant_configured_as_log_only_or_disabled_blocks_policy() -> None:
    from openmagi_core_agent.permissions.auto_control import (
        AutoPermissionConfig,
        evaluate_auto_permission,
    )

    log_only = evaluate_auto_permission(
        _request(guardDecisions=(_guard(hard=True, verdict="pass", configured_mode="log_only"),)),
        config=AutoPermissionConfig(enabled=True),
    )
    disabled = evaluate_auto_permission(
        _request(guardDecisions=(_guard(hard=True, verdict="pass", configured_mode="disabled"),)),
        config=AutoPermissionConfig(enabled=True),
    )

    assert log_only.status == "blocked_invalid_policy"
    assert "hard_invariant_mode_downgrade" in log_only.reason_codes
    assert disabled.status == "blocked_invalid_policy"
    assert "hard_invariant_mode_downgrade" in disabled.reason_codes


def test_hard_invariant_deny_blocks_even_in_uncertain_fail_passthrough_mode() -> None:
    from openmagi_core_agent.permissions.auto_control import (
        AutoPermissionConfig,
        evaluate_auto_permission,
    )

    decision = evaluate_auto_permission(
        _request(
            actionRef="tool:Clock",
            requestedPermissionRefs=("permission:readonly-status",),
            guardDecisions=(
                _guard(
                    hard=True,
                    verdict="deny",
                    configured_mode="uncertain_fail_passthrough",
                ),
            ),
        ),
        config=AutoPermissionConfig(
            enabled=True,
            autoAllowPermissionRefs=("permission:readonly-status",),
        ),
    )

    assert decision.status == "denied"
    assert decision.allowed is False
    assert decision.requires_approval is False
    assert "hard_guard_denied" in decision.reason_codes


def test_non_hard_uncertain_guard_can_passthrough_only_when_configured() -> None:
    from openmagi_core_agent.permissions.auto_control import (
        AutoPermissionConfig,
        evaluate_auto_permission,
    )

    passthrough = evaluate_auto_permission(
        _request(
            guardDecisions=(
                _guard(
                    guard_id="guard:non-hard-confidence",
                    hard=False,
                    verdict="uncertain",
                    configured_mode="uncertain_fail_passthrough",
                ),
            ),
        ),
        config=AutoPermissionConfig(enabled=True),
    )
    enforced = evaluate_auto_permission(
        _request(
            guardDecisions=(
                _guard(
                    guard_id="guard:non-hard-confidence",
                    hard=False,
                    verdict="uncertain",
                    configured_mode="enforce",
                ),
            ),
        ),
        config=AutoPermissionConfig(enabled=True),
    )

    assert passthrough.status == "uncertain_fail_passthrough"
    assert passthrough.allowed is False
    assert passthrough.requires_approval is True
    assert passthrough.reason_codes == ("non_hard_uncertain_fail_passthrough",)
    assert enforced.status == "approval_required"
    assert "uncertain_guard_requires_approval" in enforced.reason_codes


def test_admin_policy_cannot_bypass_hard_guard_or_forbidden_permissions() -> None:
    from openmagi_core_agent.permissions.auto_control import (
        AutoPermissionConfig,
        evaluate_auto_permission,
    )

    forbidden = evaluate_auto_permission(
        _request(
            requestedPermissionRefs=("permission:root-shell",),
            guardDecisions=(_guard(hard=False, verdict="pass"),),
        ),
        config=AutoPermissionConfig(
            enabled=True,
            forbiddenPermissionRefs=("permission:root-shell",),
        ),
        self_review=_self_review(recommendation="allow"),
    )

    assert forbidden.status == "denied"
    assert forbidden.allowed is False
    assert "forbidden_permission_ref" in forbidden.reason_codes


def test_passing_guards_still_require_approval_for_mutating_permissions() -> None:
    from openmagi_core_agent.permissions.auto_control import (
        AutoPermissionConfig,
        evaluate_auto_permission,
    )

    decision = evaluate_auto_permission(
        _request(guardDecisions=(_guard(hard=True, verdict="pass"),)),
        config=AutoPermissionConfig(enabled=True),
        self_review=_self_review(recommendation="allow"),
    )

    assert decision.status == "approval_required"
    assert decision.allowed is False
    assert decision.requires_approval is True
    assert "permission_requires_explicit_approval" in decision.reason_codes


def test_passing_guards_still_require_approval_for_mutating_action_ref() -> None:
    from openmagi_core_agent.permissions.auto_control import (
        AutoPermissionConfig,
        evaluate_auto_permission,
    )

    decision = evaluate_auto_permission(
        _request(
            actionRef="tool:FileWrite",
            requestedPermissionRefs=("permission:readonly-status",),
            guardDecisions=(_guard(hard=True, verdict="pass"),),
        ),
        config=AutoPermissionConfig(
            enabled=True,
            autoAllowPermissionRefs=("permission:readonly-status",),
        ),
        self_review=_self_review(recommendation="allow"),
    )

    assert decision.status == "approval_required"
    assert decision.allowed is False
    assert decision.requires_approval is True
    assert "permission_requires_explicit_approval" in decision.reason_codes


def test_passing_guards_still_require_approval_for_workspace_authority_refs() -> None:
    from openmagi_core_agent.permissions.auto_control import (
        AutoPermissionConfig,
        evaluate_auto_permission,
    )

    decision = evaluate_auto_permission(
        _request(
            actionRef="tool:Workspace",
            requestedPermissionRefs=("permission:workspace",),
            guardDecisions=(_guard(hard=True, verdict="pass"),),
        ),
        config=AutoPermissionConfig(
            enabled=True,
            autoAllowPermissionRefs=("permission:workspace",),
        ),
    )

    assert decision.status == "approval_required"
    assert decision.allowed is False
    assert decision.requires_approval is True
    assert "permission_requires_explicit_approval" in decision.reason_codes


def test_passing_guards_still_require_approval_for_mutating_action_aliases() -> None:
    from openmagi_core_agent.permissions.auto_control import (
        AutoPermissionConfig,
        evaluate_auto_permission,
    )

    decision = evaluate_auto_permission(
        _request(
            actionRef="tool:FileModify",
            requestedPermissionRefs=("permission:readonly-status",),
            guardDecisions=(_guard(hard=True, verdict="pass"),),
        ),
        config=AutoPermissionConfig(
            enabled=True,
            autoAllowPermissionRefs=("permission:readonly-status",),
        ),
    )

    assert decision.status == "approval_required"
    assert decision.allowed is False
    assert decision.requires_approval is True
    assert "permission_requires_explicit_approval" in decision.reason_codes


def test_explicit_read_permission_can_be_allowed_when_all_guards_pass() -> None:
    from openmagi_core_agent.permissions.auto_control import (
        AutoPermissionConfig,
        evaluate_auto_permission,
    )

    decision = evaluate_auto_permission(
        _request(
            actionRef="tool:Clock",
            requestedPermissionRefs=("permission:readonly-status",),
            guardDecisions=(_guard(hard=True, verdict="pass"),),
        ),
        config=AutoPermissionConfig(
            enabled=True,
            autoAllowPermissionRefs=("permission:readonly-status",),
        ),
    )

    assert decision.status == "allowed"
    assert decision.allowed is True
    assert decision.requires_approval is False
    assert decision.reason_codes == ("all_guards_passed",)


def test_auto_permission_rejects_private_refs_and_raw_self_review() -> None:
    from openmagi_core_agent.permissions.auto_control import (
        AutoPermissionConfig,
        AutoPermissionDecision,
        AutoPermissionDecisionRequest,
        AutoPermissionSelfReviewRecord,
        evaluate_auto_permission,
    )

    with pytest.raises(ValueError):
        AutoPermissionDecisionRequest.model_validate(
            _request().model_dump(by_alias=True)
            | {"actionRef": "/Users/private/tool"}
        )
    with pytest.raises(ValueError):
        AutoPermissionSelfReviewRecord(
            reviewId="auto-permission-review:bad",
            actionDigest=DIGEST_A,
            recommendation="allow",
            confidence="high",
            reasonCodes=("agent_review_allow",),
            evidenceRefs=("evidence:agent-self-review",),
            policySnapshotDigest=POLICY_DIGEST,
            createdAt=datetime(2026, 5, 26, tzinfo=UTC),
            metadata={"raw" + "Prompt": "unsafe"},
        )
    with pytest.raises(ValueError, match="model_construct"):
        AutoPermissionDecision.model_construct(
            status="allowed",
            allowed=True,
            requiresApproval=False,
            requestId="auto-permission:request-1",
            actionRef="tool:Clock",
            actionDigest=DIGEST_A,
            requestedPermissionRefs=("permission:readonly-status",),
            policySnapshotDigest=POLICY_DIGEST,
            adminPolicyDigest=DIGEST_B,
            reasonCodes=("all_guards_passed",),
            guardDecisionDigests=(DIGEST_A,),
            decisionDigest="sha256:" + "0" * 64,
            decidedAt=datetime(2026, 5, 26, tzinfo=UTC),
        )
    with pytest.raises(ValueError, match="issued by evaluate_auto_permission"):
        AutoPermissionDecision(
            status="allowed",
            allowed=True,
            requiresApproval=False,
            requestId="auto-permission:request-1",
            actionRef="tool:Clock",
            actionDigest=DIGEST_A,
            requestedPermissionRefs=("permission:readonly-status",),
            policySnapshotDigest=POLICY_DIGEST,
            adminPolicyDigest=DIGEST_B,
            reasonCodes=("all_guards_passed",),
            guardDecisionDigests=(DIGEST_A,),
            decisionDigest="sha256:" + "0" * 64,
            decidedAt=datetime(2026, 5, 26, tzinfo=UTC),
        )
    decision = evaluate_auto_permission(
        _request(),
        config=AutoPermissionConfig(enabled=True),
    )
    with pytest.raises(ValueError, match="model_copy update"):
        decision.model_copy(update={"allowed": True})


def test_auto_permission_metadata_is_immutable_after_validation() -> None:
    request = _request(metadata={"safeRef": "permission:request"})
    self_review = _self_review(metadata={"safeRef": "permission:review"})

    with pytest.raises(TypeError):
        request.metadata["rawPrompt"] = "unsafe"  # type: ignore[index]
    with pytest.raises(TypeError):
        self_review.metadata["privatePath"] = "/Users/private"  # type: ignore[index]
    with pytest.raises(TypeError, match="immutable"):
        setattr(request.metadata, "_value", {"rawPrompt": "unsafe"})

    assert not hasattr(request.metadata, "_value")
    assert not hasattr(self_review.metadata, "_value")

    assert request.model_dump(by_alias=True, mode="json")["metadata"] == {
        "safeRef": "permission:request",
    }
    assert self_review.model_dump(by_alias=True, mode="json")["metadata"] == {
        "safeRef": "permission:review",
    }


def test_auto_permission_decision_cannot_be_forged_by_public_constructor() -> None:
    from openmagi_core_agent.permissions.auto_control import (
        AutoPermissionConfig,
        AutoPermissionDecision,
        evaluate_auto_permission,
    )
    import openmagi_core_agent.permissions.auto_control as auto_control_module

    legitimate = evaluate_auto_permission(
        _request(
            actionRef="tool:Clock",
            requestedPermissionRefs=("permission:readonly-status",),
            guardDecisions=(_guard(hard=True, verdict="pass"),),
        ),
        config=AutoPermissionConfig(
            enabled=True,
            autoAllowPermissionRefs=("permission:readonly-status",),
        ),
    )
    forged = legitimate.model_dump(by_alias=True, mode="json") | {
        "requestId": "auto-permission:forged",
        "actionRef": "tool:FileWrite",
        "requestedPermissionRefs": ("permission:workspace-write",),
    }

    assert not hasattr(auto_control_module, "_DECISION_ISSUER_TOKEN")
    assert not hasattr(auto_control_module, "_DECISION_STATES")
    with pytest.raises(ValueError, match="issued by evaluate_auto_permission"):
        AutoPermissionDecision(**legitimate.model_dump(by_alias=True, mode="json"))
    with pytest.raises(ValueError, match="issued by evaluate_auto_permission"):
        AutoPermissionDecision.model_validate(forged)
    with pytest.raises(AttributeError):
        BaseModel.model_construct.__func__(AutoPermissionDecision, **forged)  # type: ignore[attr-defined]
    forged_object = object.__new__(AutoPermissionDecision)
    with pytest.raises(AttributeError):
        object.__setattr__(forged_object, "status", "allowed")
    with pytest.raises(ValueError, match="issued by evaluate_auto_permission"):
        forged_object.public_projection()
    assert not hasattr(auto_control_module, "_issue_auto_permission_decision")
    assert not hasattr(auto_control_module, "_issue_auto_permission_decision_payload")
    assert not hasattr(auto_control_module, "_create_auto_permission_decision")
    assert not hasattr(auto_control_module, "_build_auto_permission_evaluator")
    assert evaluate_auto_permission.__closure__ is None
    assert AutoPermissionDecision.public_projection.__closure__ is None


def test_auto_permission_projection_rejects_privately_forged_mutating_authority() -> None:
    import openmagi_core_agent.permissions.auto_control as auto_control_module
    from openmagi_core_agent.permissions.auto_control import AutoPermissionDecision

    issued_at = datetime(2026, 5, 26, tzinfo=UTC)
    forged_state = auto_control_module._DecisionState(  # noqa: SLF001
        status="allowed",
        allowed=True,
        requires_approval=False,
        request_id="auto-permission:forged",
        action_ref="tool:FileWrite",
        action_digest=DIGEST_A,
        requested_permission_refs=("permission:workspace-write",),
        policy_snapshot_digest=POLICY_DIGEST,
        admin_policy_digest=DIGEST_B,
        reason_codes=("all_guards_passed",),
        guard_decision_digests=(DIGEST_A,),
        self_review_digest=None,
        decision_digest="sha256:" + "0" * 64,
        decided_at=issued_at,
    )
    forged_state = forged_state._replace(
        decision_digest=auto_control_module._decision_state_digest(forged_state),  # noqa: SLF001
    )
    forged = object.__new__(AutoPermissionDecision)
    object.__setattr__(forged, "_AutoPermissionDecision__state", forged_state)
    object.__setattr__(forged, "_sealed", True)

    assert forged.allowed is True
    with pytest.raises(ValueError, match="mutating permissions"):
        forged.model_dump(by_alias=True, mode="json")
    with pytest.raises(ValueError, match="mutating permissions"):
        forged.public_projection()

    forged_with_readonly_permission = object.__new__(AutoPermissionDecision)
    forged_state = forged_state._replace(
        requested_permission_refs=("permission:readonly-status",),
        decision_digest="sha256:" + "0" * 64,
    )
    forged_state = forged_state._replace(
        decision_digest=auto_control_module._decision_state_digest(forged_state),  # noqa: SLF001
    )
    object.__setattr__(forged_with_readonly_permission, "_AutoPermissionDecision__state", forged_state)
    object.__setattr__(forged_with_readonly_permission, "_sealed", True)

    assert forged_with_readonly_permission.allowed is True
    with pytest.raises(ValueError, match="mutating permissions"):
        forged_with_readonly_permission.model_dump(by_alias=True, mode="json")
    with pytest.raises(ValueError, match="mutating permissions"):
        forged_with_readonly_permission.public_projection()

    forged_with_workspace_authority = object.__new__(AutoPermissionDecision)
    workspace_state = forged_state._replace(
        action_ref="tool:Workspace",
        requested_permission_refs=("permission:workspace",),
        decision_digest="sha256:" + "0" * 64,
    )
    workspace_state = workspace_state._replace(
        decision_digest=auto_control_module._decision_state_digest(workspace_state),  # noqa: SLF001
    )
    object.__setattr__(
        forged_with_workspace_authority,
        "_AutoPermissionDecision__state",
        workspace_state,
    )
    object.__setattr__(forged_with_workspace_authority, "_sealed", True)

    assert forged_with_workspace_authority.allowed is True
    with pytest.raises(ValueError, match="mutating permissions"):
        forged_with_workspace_authority.model_dump(by_alias=True, mode="json")
    with pytest.raises(ValueError, match="mutating permissions"):
        forged_with_workspace_authority.public_projection()


def test_auto_permission_decision_issuer_resists_module_global_rebinding() -> None:
    import openmagi_core_agent.permissions.auto_control as auto_control_module
    from openmagi_core_agent.permissions.auto_control import (
        AutoPermissionConfig,
        evaluate_auto_permission,
    )

    original_decision_builder = auto_control_module._decision  # noqa: SLF001
    original_hard_policy = auto_control_module._hard_invariant_policy_error  # noqa: SLF001
    original_mutating_check = auto_control_module._is_mutating_permission  # noqa: SLF001

    def forged_decision_builder(*_args: object, **_kwargs: object) -> dict[str, object]:
        return original_decision_builder(
            _request(),
            status="allowed",
            allowed=True,
            requires_approval=False,
            reason_codes=("all_guards_passed",),
            self_review=None,
            now=datetime(2026, 5, 26, tzinfo=UTC),
        )

    try:
        auto_control_module._decision = forged_decision_builder  # type: ignore[attr-defined]  # noqa: SLF001
        auto_control_module._hard_invariant_policy_error = lambda *_args, **_kwargs: None  # type: ignore[method-assign]  # noqa: SLF001
        auto_control_module._is_mutating_permission = lambda *_args, **_kwargs: False  # type: ignore[method-assign]  # noqa: SLF001

        decision = evaluate_auto_permission(
            _request(),
            config=AutoPermissionConfig(
                enabled=True,
                autoAllowPermissionRefs=("permission:workspace-write",),
            ),
        )
    finally:
        auto_control_module._decision = original_decision_builder  # type: ignore[attr-defined]  # noqa: SLF001
        auto_control_module._hard_invariant_policy_error = original_hard_policy  # type: ignore[method-assign]  # noqa: SLF001
        auto_control_module._is_mutating_permission = original_mutating_check  # type: ignore[method-assign]  # noqa: SLF001

    assert decision.status == "denied"
    assert decision.allowed is False
    assert "hard_guard_denied" in decision.reason_codes


def test_auto_permission_records_have_no_mutable_public_dict_backing() -> None:
    from openmagi_core_agent.permissions.auto_control import (
        AutoPermissionConfig,
        evaluate_auto_permission,
    )

    request = _request()
    decision = evaluate_auto_permission(
        _request(
            actionRef="tool:Clock",
            requestedPermissionRefs=("permission:readonly-status",),
            guardDecisions=(_guard(hard=True, verdict="pass"),),
        ),
        config=AutoPermissionConfig(
            enabled=True,
            autoAllowPermissionRefs=("permission:readonly-status",),
        ),
    )

    for record in (request, request.guard_decisions[0], decision, decision.authority_flags):
        assert not hasattr(record, "__dict__")
        with pytest.raises(TypeError, match="immutable"):
            setattr(record, "allowed", True)


def test_require_approval_guard_mode_blocks_auto_allow_even_when_guard_passes() -> None:
    from openmagi_core_agent.permissions.auto_control import (
        AutoPermissionConfig,
        evaluate_auto_permission,
    )

    decision = evaluate_auto_permission(
        _request(
            actionRef="tool:Clock",
            requestedPermissionRefs=("permission:readonly-status",),
            guardDecisions=(
                _guard(
                    hard=True,
                    verdict="pass",
                    configured_mode="require_approval",
                ),
            ),
        ),
        config=AutoPermissionConfig(
            enabled=True,
            autoAllowPermissionRefs=("permission:readonly-status",),
        ),
    )

    assert decision.status == "approval_required"
    assert decision.allowed is False
    assert decision.requires_approval is True
    assert decision.reason_codes == ("guard_requires_approval",)


def test_non_hard_enforce_deny_blocks_auto_allow() -> None:
    from openmagi_core_agent.permissions.auto_control import (
        AutoPermissionConfig,
        evaluate_auto_permission,
    )

    decision = evaluate_auto_permission(
        _request(
            actionRef="tool:Clock",
            requestedPermissionRefs=("permission:readonly-status",),
            guardDecisions=(
                _guard(
                    guard_id="guard:non-hard-policy-deny",
                    hard=False,
                    verdict="deny",
                    configured_mode="enforce",
                ),
            ),
        ),
        config=AutoPermissionConfig(
            enabled=True,
            autoAllowPermissionRefs=("permission:readonly-status",),
        ),
    )

    assert decision.status == "denied"
    assert decision.allowed is False
    assert decision.requires_approval is False
    assert decision.reason_codes == ("guard_denied",)


def test_auto_permission_import_boundary_has_no_live_runtime_imports() -> None:
    script = """
import sys
import openmagi_core_agent.permissions.auto_control
for name in (
    'google.adk.runners',
    'google.adk.agents',
    'openmagi_core_agent.transport',
    'openmagi_core_agent.deploy',
    'kubernetes',
    'requests',
    'httpx',
    'stripe',
    'supabase',
):
    if name in sys.modules:
        raise SystemExit(name)
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
