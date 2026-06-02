from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from magi_agent.shadow.permission_arbiter_contract import (
    PermissionArbiterContractFixture,
    load_permission_arbiter_contract_fixture,
    project_permission_arbiter_contract_fixture,
)


FIXTURES = Path(__file__).parent / "fixtures" / "permission_arbiter"


def test_permission_arbiter_contract_fixture_covers_mode_source_matrix() -> None:
    fixture = load_permission_arbiter_contract_fixture(
        "policy_matrix.json",
        fixture_root=FIXTURES,
    )

    projection = project_permission_arbiter_contract_fixture(fixture)
    cases = {case.case_id: case for case in fixture.cases}

    assert projection.fixture_id == "permission_arbiter_matrix_0001"
    assert projection.local_diagnostic is True
    assert projection.case_order == (
        "plan_readonly_allow",
        "plan_patch_apply_dry_run_allow",
        "plan_patch_apply_apply_ask",
        "plan_mutating_tool_deny",
        "auto_safe_tool_allow",
        "auto_write_tool_ask",
        "default_complex_shell_ask",
        "bypass_safe_after_security_allow",
        "bypass_unsafe_shell_deny_with_status_metadata",
        "workspace_bypass_secret_workspace_path_allow_or_recorded_policy",
        "workspace_bypass_system_shell_deny",
        "child_agent_nondangerous_allow",
        "child_agent_dangerous_ask",
    )
    assert projection.by_decision == {
        "allow": 6,
        "ask": 4,
        "deny": 3,
    }
    assert set(projection.attachment_flags.model_dump(by_alias=True).values()) == {False}
    assert projection.no_live_execution is True
    assert projection.control_requests == {}
    assert projection.approval_metadata["plan_patch_apply_apply_ask"] == {
        "requestId": "tool-permission:turn-permission-arbiter-1:PatchApply",
        "turnId": "turn-permission-arbiter-1",
        "toolName": "PatchApply",
        "reason": "patch apply requires approval outside dry-run",
    }
    assert projection.approval_metadata["auto_write_tool_ask"]["reason"] == (
        "workspace write requires approval in auto mode"
    )
    assert projection.approval_metadata["child_agent_dangerous_ask"]["toolName"] == (
        "Task"
    )

    assert cases["plan_readonly_allow"].mode == "plan"
    assert cases["plan_readonly_allow"].source == "builtin"
    assert cases["plan_readonly_allow"].decision == "allow"
    assert cases["plan_readonly_allow"].permission_class == "read"

    assert cases["plan_patch_apply_dry_run_allow"].decision == "allow"
    assert cases["plan_patch_apply_dry_run_allow"].dry_run is True
    assert cases["plan_patch_apply_apply_ask"].decision == "ask"
    assert cases["plan_patch_apply_apply_ask"].approval_metadata is not None
    assert cases["plan_patch_apply_apply_ask"].control_request is None

    assert cases["plan_mutating_tool_deny"].decision == "deny"
    assert cases["plan_mutating_tool_deny"].reason_codes == ("plan_mode_mutation_blocked",)

    assert cases["auto_safe_tool_allow"].mode == "auto"
    assert cases["auto_safe_tool_allow"].decision == "allow"
    assert cases["auto_write_tool_ask"].decision == "ask"
    assert cases["default_complex_shell_ask"].mode == "default"
    assert cases["default_complex_shell_ask"].decision == "ask"

    bypass_safe = cases["bypass_safe_after_security_allow"]
    assert bypass_safe.bypass_requested is True
    assert bypass_safe.security_precheck == "passed"
    assert bypass_safe.decision == "allow"

    bypass_unsafe = cases["bypass_unsafe_shell_deny_with_status_metadata"]
    assert bypass_unsafe.decision == "deny"
    assert bypass_unsafe.bypass_requested is True
    assert bypass_unsafe.status_metadata is not None
    assert bypass_unsafe.status_metadata.model_dump(by_alias=True) == {
        "status": "blocked",
        "errorCode": "bypass_denied_hard_safety",
        "observable": True,
        "metadataOnly": True,
    }
    assert projection.bypass_status_metadata[
        "bypass_unsafe_shell_deny_with_status_metadata"
    ] == {
        "status": "blocked",
        "errorCode": "bypass_denied_hard_safety",
        "observable": True,
        "metadataOnly": True,
    }

    workspace_bypass = cases[
        "workspace_bypass_secret_workspace_path_allow_or_recorded_policy"
    ]
    assert workspace_bypass.decision == "allow"
    assert workspace_bypass.path_policy_recorded is True
    assert workspace_bypass.public_preview == "path=[workspace-secret-path-redacted]"

    assert cases["workspace_bypass_system_shell_deny"].decision == "deny"
    assert cases["workspace_bypass_system_shell_deny"].reason_codes == (
        "system_shell_denied",
    )
    assert cases["child_agent_nondangerous_allow"].source == "child_agent"
    assert cases["child_agent_nondangerous_allow"].decision == "allow"
    assert cases["child_agent_dangerous_ask"].source == "child_agent"
    assert cases["child_agent_dangerous_ask"].decision == "ask"

    projection_json = json.dumps(
        projection.model_dump(by_alias=True),
        sort_keys=True,
    )
    unsafe_fragments = (
        "/data/bots",
        "/workspace",
        "/var/lib/kubelet",
        "Bearer unsafe",
        "ghp_permissionsecret",
        "sk-permission-secret",
        "SUPABASE_SERVICE_ROLE_KEY",
        "private tool args",
        "rm -rf /",
        "pythonResponseAuthority",
        "adkRunnerInvoked\": true",
        "liveToolDispatched\": true",
        "shellOrCodeExecuted\": true",
        "controlRequest\": {",
    )
    for fragment in unsafe_fragments:
        assert fragment not in projection_json

    assert projection.public_previews["default_complex_shell_ask"] == (
        "command=python scripts/diagnose.py --workspace [redacted]"
    )
    assert projection.case_snapshots[
        "bypass_unsafe_shell_deny_with_status_metadata"
    ]["statusMetadata"] == {
        "status": "blocked",
        "errorCode": "bypass_denied_hard_safety",
        "observable": True,
        "metadataOnly": True,
    }


@pytest.mark.parametrize(
    "mutation",
    (
        pytest.param(
            lambda payload: payload["attachmentFlags"].update({"liveToolDispatched": True}),
            id="live-tool-dispatch",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["attachmentFlags"].update(
                {"adkRunnerInvoked": True}
            ),
            id="case-runner-flag",
        ),
        pytest.param(
            lambda payload: payload["cases"][5].update(
                {"controlRequest": {"requestId": "live", "turnId": "turn"}}
            ),
            id="live-control-request",
        ),
        pytest.param(
            lambda payload: payload["cases"][8].update(
                {"publicPreview": "command=rm -rf /workspace/private"}
            ),
            id="unsafe-public-command",
        ),
        pytest.param(
            lambda payload: payload["cases"][9].update(
                {"publicPreview": "/data/bots/bot-secret/workspace/.env"}
            ),
            id="unsafe-public-path",
        ),
        pytest.param(
            lambda payload: payload["cases"][12].pop("approvalMetadata"),
            id="ask-without-approval-metadata",
        ),
    ),
)
def test_permission_arbiter_contract_rejects_live_flags_unsafe_public_data_and_bad_ask(
    mutation: Callable[[dict[str, object]], object],
) -> None:
    payload = json.loads((FIXTURES / "policy_matrix.json").read_text(encoding="utf-8"))
    mutation(payload)

    with pytest.raises(ValidationError):
        PermissionArbiterContractFixture.model_validate(payload)


def test_permission_arbiter_contract_rejects_failed_security_precheck_allow() -> None:
    payload = json.loads((FIXTURES / "policy_matrix.json").read_text(encoding="utf-8"))
    payload["cases"][7].update(
        {
            "securityPrecheck": "failed",
            "decision": "allow",
        }
    )

    with pytest.raises(ValidationError):
        PermissionArbiterContractFixture.model_validate(payload)


def test_permission_arbiter_contract_import_boundary_stays_runtime_free() -> None:
    code = """
import sys
from pathlib import Path

from magi_agent.shadow.permission_arbiter_contract import (
    load_permission_arbiter_contract_fixture,
    project_permission_arbiter_contract_fixture,
)

fixture_root = Path('tests/fixtures/permission_arbiter')
fixture = load_permission_arbiter_contract_fixture('policy_matrix.json', fixture_root=fixture_root)
project_permission_arbiter_contract_fixture(fixture)

forbidden = (
    'google.adk.runners',
    'magi_agent.adk_bridge.local_runner',
    'magi_agent.adk_bridge.runner_adapter',
    'magi_agent.adk_bridge.tool_adapter',
    'magi_agent.tools.dispatcher',
    'magi_agent.tools.registry',
    'magi_agent.plugins.agentmemory',
    'magi_agent.plugins.manager',
    'magi_agent.plugins.native_catalog',
    'magi_agent.memory',
    'magi_agent.services.memory',
    'magi_agent.hipocampus',
    'magi_agent.qmd',
    'magi_agent.app',
    'magi_agent.transport.chat',
    'magi_agent.routes',
)
loaded = [name for name in forbidden if name in sys.modules]
if loaded:
    raise AssertionError(f'forbidden modules loaded: {loaded}')
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        cwd=Path(__file__).parents[1],
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
