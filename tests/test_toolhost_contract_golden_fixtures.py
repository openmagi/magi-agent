from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from magi_agent.shadow.toolhost_contract import (
    ToolHostContractFixture,
    load_toolhost_contract_fixture,
    project_toolhost_contract_fixture,
)


FIXTURES = Path(__file__).parent / "fixtures" / "toolhost_contract"


def test_toolhost_contract_fixture_covers_deterministic_outcomes_without_live_dispatch() -> None:
    fixture = load_toolhost_contract_fixture(
        "contract_matrix.json",
        fixture_root=FIXTURES,
    )

    projection = project_toolhost_contract_fixture(fixture)
    cases = {case.case_id: case for case in fixture.cases}

    assert projection.fixture_id == "toolhost_contract_matrix_0001"
    assert projection.local_diagnostic is True
    assert projection.case_order == (
        "allowed_readonly",
        "denied_dangerous",
        "approval_required",
        "missing_handler",
        "timeout",
        "handler_error",
        "redaction_failure",
        "disabled_tool",
        "protected_replacement_attempt",
    )
    assert projection.by_outcome == {
        "allowed": 1,
        "denied": 1,
        "approval_required": 1,
        "missing_handler": 1,
        "timeout": 1,
        "handler_error": 1,
        "redaction_failure": 1,
        "disabled": 1,
        "protected_replacement_attempt": 1,
    }
    assert set(projection.attachment_flags.model_dump(by_alias=True).values()) == {False}
    assert projection.no_live_execution is True

    allowed = cases["allowed_readonly"]
    assert allowed.tool.name == "FileRead"
    assert allowed.tool.kind == "core"
    assert allowed.tool.source.kind == "builtin"
    assert allowed.tool.permission == "read"
    assert allowed.tool.side_effect_class == "none"
    assert allowed.tool.dangerous is False
    assert allowed.tool.mutates_workspace is False
    assert allowed.tool.parallel_safety == "readonly"
    assert allowed.policy_action == "allow"
    assert allowed.result.status == "ok"
    assert allowed.blocking is False
    assert allowed.fail_open is True
    assert allowed.fail_closed is False

    denied = cases["denied_dangerous"]
    assert denied.tool.name == "Bash"
    assert denied.tool.permission == "execute"
    assert denied.tool.dangerous is True
    assert denied.policy_action == "deny"
    assert denied.result.status == "blocked"
    assert denied.blocking is True
    assert denied.fail_closed is True

    approval = cases["approval_required"]
    assert approval.result.status == "needs_approval"
    assert approval.policy_action == "ask"
    assert approval.control_request is not None
    assert projection.control_requests["approval_required"] == {
        "requestId": "tool-permission:turn-tool-contract-1:FileWrite",
        "turnId": "turn-tool-contract-1",
        "toolName": "FileWrite",
        "reason": "workspace mutation requires approval",
    }

    assert cases["missing_handler"].result.error_code == "tool_handler_missing"
    assert cases["missing_handler"].handler_available is False
    assert cases["timeout"].result.error_code == "tool_timeout"
    assert cases["timeout"].timeout_budget_ms == 30000
    assert cases["handler_error"].result.error_code == "handler_error"
    assert cases["disabled_tool"].enabled is False
    assert cases["disabled_tool"].result.metadata["reason"] == "tool disabled"
    assert cases["protected_replacement_attempt"].replacement_attempt is not None
    assert cases["protected_replacement_attempt"].replacement_attempt.downgrade_reasons == (
        "kind",
        "source",
        "permission",
        "dangerous",
        "mutatesWorkspace",
    )

    projection_json = json.dumps(
        projection.model_dump(by_alias=True),
        sort_keys=True,
    )
    unsafe_fragments = (
        "Bearer unsafe-output",
        "ghp_contractsecret",
        "sk-handler-error-secret",
        "private tool args",
        "/data/bots/bot-secret",
        "/workspace/private",
        "rm -rf /workspace/private",
        "raw_secret",
        "pythonResponseAuthority",
    )
    for fragment in unsafe_fragments:
        assert fragment not in projection_json

    assert projection.public_previews["handler_error"] == "error=[redacted]"
    assert projection.public_previews["redaction_failure"] == "Authorization: Bearer [redacted]"
    assert projection.case_snapshots["redaction_failure"]["result"]["status"] == "blocked"
    assert projection.case_snapshots["protected_replacement_attempt"]["protected"] is True


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
            lambda payload: payload["cases"][2]["controlRequest"]["arguments"].update(
                {"command": "rm -rf /workspace/private"}
            ),
            id="unsafe-control-arguments",
        ),
    ),
)
def test_toolhost_contract_fixture_rejects_live_flags_and_unsafe_control_arguments(
    mutation: Callable[[dict[str, object]], object],
) -> None:
    payload = json.loads((FIXTURES / "contract_matrix.json").read_text(encoding="utf-8"))
    mutation(payload)

    with pytest.raises(ValidationError):
        ToolHostContractFixture.model_validate(payload)


def test_toolhost_contract_import_boundary_stays_dispatch_runner_and_route_free() -> None:
    code = """
import sys
from pathlib import Path

from magi_agent.shadow.toolhost_contract import (
    load_toolhost_contract_fixture,
    project_toolhost_contract_fixture,
)

fixture_root = Path('tests/fixtures/toolhost_contract')
fixture = load_toolhost_contract_fixture('contract_matrix.json', fixture_root=fixture_root)
project_toolhost_contract_fixture(fixture)

forbidden = (
    'google.adk.runners',
    'magi_agent.adk_bridge.local_runner',
    'magi_agent.adk_bridge.runner_adapter',
    'magi_agent.adk_bridge.tool_adapter',
    'magi_agent.tools.dispatcher',
    'magi_agent.tools.registry',
    'magi_agent.plugins.agentmemory',
    'magi_agent.memory',
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
