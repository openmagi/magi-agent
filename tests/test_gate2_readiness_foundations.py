from __future__ import annotations

import errno
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from fastapi.testclient import TestClient
import pytest

from openmagi_core_agent.app import create_app
from openmagi_core_agent.config.env import parse_runtime_env
from openmagi_core_agent.runtime.openmagi_runtime import OpenMagiRuntime
from openmagi_core_agent.transport.chat import (
    build_gate2_sandbox_workspace_canary_config_from_env,
)


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _gate2_profile_digest() -> str:
    from openmagi_core_agent.shadow.gate2_recipe_profile_resolver import (
        resolve_gate2_recipe_profile,
    )

    return resolve_gate2_recipe_profile(
        "openmagi.gate2.workspace-canary.v1"
    ).profile_digest


def _base_env(**overrides: str) -> dict[str, str]:
    env = {
        "BOT_ID": "bot-gate2",
        "USER_ID": "owner-gate2",
        "GATEWAY_TOKEN": "gateway-token",
        "CORE_AGENT_API_PROXY_URL": "http://api-proxy.local",
        "CORE_AGENT_CHAT_PROXY_URL": "http://chat-proxy.local",
        "CORE_AGENT_REDIS_URL": "redis://redis.local:6379/0",
        "CORE_AGENT_MODEL": "gpt-5.2",
    }
    env.update(overrides)
    return env


def _gate2_readiness_env() -> dict[str, str]:
    return {
        "CORE_AGENT_PYTHON_GATE2_READINESS_ENABLED": "1",
        "CORE_AGENT_PYTHON_GATE2_READINESS_KILL_SWITCH": "0",
        "CORE_AGENT_PYTHON_GATE2_READINESS_LOCAL_SANDBOX_HARNESS": "1",
        "CORE_AGENT_PYTHON_GATE2_READINESS_SELECTED_BOT_DIGEST": _digest("bot-gate2"),
        "CORE_AGENT_PYTHON_GATE2_READINESS_TRUSTED_OWNER_USER_ID_DIGEST": _digest(
            "owner-gate2"
        ),
        "CORE_AGENT_PYTHON_GATE2_READINESS_ENVIRONMENT": "staging",
        "CORE_AGENT_PYTHON_GATE2_READINESS_ENV_ALLOWLIST": "staging",
        "CORE_AGENT_PYTHON_GATE2_READINESS_PROFILE_REF": (
            "openmagi.gate2.workspace-canary.v1"
        ),
        "CORE_AGENT_PYTHON_GATE2_READINESS_PROFILE_DIGEST": _gate2_profile_digest(),
    }


def _gate2_activation_env(tmp_path: Path, **overrides: str) -> dict[str, str]:
    env = {
        "CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_ENABLED": "1",
        "CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_KILL_SWITCH": "0",
        "CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_SELECTED_BOT_DIGEST": _digest(
            "bot-gate2"
        ),
        "CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_TRUSTED_OWNER_USER_ID_DIGEST": _digest(
            "owner-gate2"
        ),
        "CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_ENVIRONMENT": "staging",
        "CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_ENV_ALLOWLIST": "staging",
        "CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_SELECTED_PROVIDER_ENABLED": "1",
        "CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_ROOT": str(
            tmp_path / "gate2-sandboxes" / "bot-gate2" / "gate2-sandbox"
        ),
    }
    env.update(overrides)
    return env


def _runtime_with_gate2_canary_config(env: dict[str, str]) -> OpenMagiRuntime:
    runtime = OpenMagiRuntime(config=parse_runtime_env(env))
    runtime.gate2_sandbox_workspace_canary_config = (
        build_gate2_sandbox_workspace_canary_config_from_env(env, runtime.config)
    )
    return runtime


def _assert_no_gate2_root_raw_leak(encoded: str, tmp_path: Path) -> None:
    assert str(tmp_path) not in encoded
    assert "gate2-sandboxes" not in encoded
    assert "bot-gate2" not in encoded
    assert "gate2-sandbox" not in encoded
    assert "/Users/private" not in encoded
    assert "token=secret" not in encoded


def test_gate2_readiness_disabled_by_default_and_healthz_has_no_authority() -> None:
    runtime = OpenMagiRuntime(config=parse_runtime_env(_base_env()))
    client = TestClient(create_app(runtime))

    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    gate2 = body["gate2Readiness"]
    assert gate2["enabled"] is False
    assert gate2["status"] == "disabled"
    assert gate2["readinessReady"] is False
    assert gate2["selectedScopeMatched"] is False
    assert gate2["productionWorkspaceMutationAllowed"] is False
    assert gate2["userVisibleOutputAllowed"] is False
    assert gate2["routeAttached"] is False
    assert gate2["toolHostDispatchAllowed"] is False
    assert gate2["reasonCodes"] == ["gate_disabled"]
    assert body["workspaceMutationAllowed"] is False
    assert body["userVisibleOutputAllowed"] is False
    assert body["canaryRoutingAllowed"] is False
    assert body["adk"]["invoked"] is False
    assert body["activeTools"] == []


def test_gate2_readiness_requires_selected_bot_owner_env_and_sandbox_harness() -> None:
    env = _base_env(**_gate2_readiness_env())
    runtime = OpenMagiRuntime(config=parse_runtime_env(env))
    client = TestClient(create_app(runtime))

    response = client.get("/healthz")

    assert response.status_code == 200
    gate2 = response.json()["gate2Readiness"]
    assert gate2["enabled"] is True
    assert gate2["status"] == "ready"
    assert gate2["readinessReady"] is True
    assert gate2["selectedScopeMatched"] is True
    assert gate2["profileRef"] == "openmagi.gate2.workspace-canary.v1"
    assert gate2["policyMode"] == "sandbox_fake_workspace"
    assert gate2["allowedSandboxActions"] == ["FileCreate", "FileEdit", "PatchApply"]
    assert gate2["forbiddenActionCount"] >= 10
    assert gate2["productionWorkspaceMutationAllowed"] is False
    assert gate2["writeMutationAuthorityAllowed"] is False
    assert gate2["userVisibleOutputAllowed"] is False
    assert gate2["routeAttached"] is False
    assert gate2["toolHostDispatchAllowed"] is False
    assert gate2["reasonCodes"] == ["selected_sandbox_readiness_ready"]


def test_gate2_readiness_checks_selected_sandbox_root_before_open(
    tmp_path: Path,
) -> None:
    env = _base_env(
        **_gate2_readiness_env(),
        **_gate2_activation_env(tmp_path),
    )
    runtime = _runtime_with_gate2_canary_config(env)
    client = TestClient(create_app(runtime))

    response = client.get("/healthz")

    assert response.status_code == 200
    gate2 = response.json()["gate2Readiness"]
    encoded = json.dumps(gate2, sort_keys=True)
    assert gate2["enabled"] is True
    assert gate2["status"] == "ready"
    assert gate2["readinessReady"] is True
    assert gate2["sandboxRootReady"] is True
    assert gate2["sandboxRootStatus"] == "ready"
    assert gate2["sandboxRootDiagnostics"] is None
    assert gate2["productionWorkspaceMutationAllowed"] is False
    assert gate2["writeMutationAuthorityAllowed"] is False
    assert gate2["toolHostDispatchAllowed"] is False
    assert "selected_sandbox_readiness_ready" in gate2["reasonCodes"]
    assert Path(env["CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_ROOT"]).is_dir()
    _assert_no_gate2_root_raw_leak(encoded, tmp_path)


def test_gate2_readiness_blocks_selected_sandbox_when_parent_cannot_be_created(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import openmagi_core_agent.shadow.gate2_activation_loop_a as gate2_module

    original_mkdir = gate2_module.os.mkdir

    def _raise_sandbox_parent_mkdir_error(
        path: str,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> None:
        if path == "gate2-sandboxes":
            raise PermissionError(
                errno.EACCES,
                "raw /Users/private token=secret must not leak",
            )
        return original_mkdir(path, mode, dir_fd=dir_fd)

    monkeypatch.setattr(gate2_module.os, "mkdir", _raise_sandbox_parent_mkdir_error)
    env = _base_env(
        **_gate2_readiness_env(),
        **_gate2_activation_env(tmp_path),
    )
    runtime = _runtime_with_gate2_canary_config(env)

    gate2 = TestClient(create_app(runtime)).get("/healthz").json()["gate2Readiness"]

    encoded = json.dumps(gate2, sort_keys=True)
    assert gate2["status"] == "blocked"
    assert gate2["readinessReady"] is False
    assert gate2["selectedScopeMatched"] is True
    assert gate2["sandboxRootReady"] is False
    assert gate2["sandboxRootStatus"] == "blocked"
    assert "sandbox_root_unavailable" in gate2["reasonCodes"]
    assert "sandbox_write_parent_create_failed" in gate2["reasonCodes"]
    assert "mkdir_permission_denied" in gate2["reasonCodes"]
    diagnostics = gate2["sandboxRootDiagnostics"]
    assert diagnostics["sandboxRootShapeKind"] == "approved_namespaced_root"
    assert diagnostics["componentRole"] == "sandbox_parent"
    assert diagnostics["parentCreateStage"] == "root_component_mkdir"
    assert diagnostics["parentCreateDeniedReason"] == "mkdir_permission_denied"
    assert diagnostics["mkdirAttempted"] is True
    assert diagnostics["mkdirFailed"] is True
    assert diagnostics["openNoFollowFailed"] is False
    assert gate2["productionWorkspaceMutationAllowed"] is False
    assert gate2["writeMutationAuthorityAllowed"] is False
    assert gate2["toolHostDispatchAllowed"] is False
    assert not Path(env["CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_ROOT"]).exists()
    _assert_no_gate2_root_raw_leak(encoded, tmp_path)


def test_gate2_readiness_does_not_create_sandbox_root_when_activation_kill_switch_on(
    tmp_path: Path,
) -> None:
    env = _base_env(
        **_gate2_readiness_env(),
        **_gate2_activation_env(
            tmp_path,
            CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_KILL_SWITCH="1",
        ),
    )
    runtime = _runtime_with_gate2_canary_config(env)

    gate2 = TestClient(create_app(runtime)).get("/healthz").json()["gate2Readiness"]

    assert gate2["status"] == "ready"
    assert gate2["readinessReady"] is True
    assert gate2["sandboxRootReady"] is None
    assert gate2["sandboxRootStatus"] == "not_checked"
    assert gate2["sandboxRootDiagnostics"] is None
    assert not Path(env["CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_ROOT"]).exists()


def test_gate2_readiness_does_not_create_sandbox_root_when_profile_digest_blocks(
    tmp_path: Path,
) -> None:
    env = _base_env(
        **{
            **_gate2_readiness_env(),
            "CORE_AGENT_PYTHON_GATE2_READINESS_PROFILE_DIGEST": _digest(
                "wrong-profile"
            ),
        },
        **_gate2_activation_env(tmp_path),
    )
    runtime = _runtime_with_gate2_canary_config(env)

    gate2 = TestClient(create_app(runtime)).get("/healthz").json()["gate2Readiness"]

    assert gate2["status"] == "blocked"
    assert gate2["readinessReady"] is False
    assert "profile_digest_mismatch" in gate2["reasonCodes"]
    assert gate2["sandboxRootReady"] is None
    assert gate2["sandboxRootStatus"] == "not_checked"
    assert gate2["sandboxRootDiagnostics"] is None
    assert not Path(env["CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_ROOT"]).exists()


def test_gate2_readiness_non_selected_and_malformed_config_fail_closed() -> None:
    non_selected = OpenMagiRuntime(
        config=parse_runtime_env(
            _base_env(
                CORE_AGENT_PYTHON_GATE2_READINESS_ENABLED="1",
                CORE_AGENT_PYTHON_GATE2_READINESS_KILL_SWITCH="0",
                CORE_AGENT_PYTHON_GATE2_READINESS_LOCAL_SANDBOX_HARNESS="1",
                CORE_AGENT_PYTHON_GATE2_READINESS_SELECTED_BOT_DIGEST=_digest("other-bot"),
                CORE_AGENT_PYTHON_GATE2_READINESS_TRUSTED_OWNER_USER_ID_DIGEST=_digest(
                    "owner-gate2"
                ),
                CORE_AGENT_PYTHON_GATE2_READINESS_ENVIRONMENT="staging",
                CORE_AGENT_PYTHON_GATE2_READINESS_ENV_ALLOWLIST="staging",
                CORE_AGENT_PYTHON_GATE2_READINESS_PROFILE_REF=(
                    "openmagi.gate2.workspace-canary.v1"
                ),
                CORE_AGENT_PYTHON_GATE2_READINESS_PROFILE_DIGEST=_gate2_profile_digest(),
            )
        )
    )
    malformed = OpenMagiRuntime(
        config=parse_runtime_env(
            _base_env(
                CORE_AGENT_PYTHON_GATE2_READINESS_ENABLED="1",
                CORE_AGENT_PYTHON_GATE2_READINESS_KILL_SWITCH="0",
                CORE_AGENT_PYTHON_GATE2_READINESS_LOCAL_SANDBOX_HARNESS="1",
                CORE_AGENT_PYTHON_GATE2_READINESS_SELECTED_BOT_DIGEST="bot-gate2",
                CORE_AGENT_PYTHON_GATE2_READINESS_TRUSTED_OWNER_USER_ID_DIGEST=_digest(
                    "owner-gate2"
                ),
                CORE_AGENT_PYTHON_GATE2_READINESS_ENVIRONMENT="production",
                CORE_AGENT_PYTHON_GATE2_READINESS_ENV_ALLOWLIST="production",
                CORE_AGENT_PYTHON_GATE2_READINESS_PROFILE_REF=(
                    "openmagi.gate2.workspace-canary.v1"
                ),
            )
        )
    )

    non_selected_gate2 = TestClient(create_app(non_selected)).get("/healthz").json()[
        "gate2Readiness"
    ]
    malformed_gate2 = TestClient(create_app(malformed)).get("/healthz").json()[
        "gate2Readiness"
    ]

    assert non_selected_gate2["status"] == "blocked"
    assert non_selected_gate2["readinessReady"] is False
    assert "bot_not_selected" in non_selected_gate2["reasonCodes"]
    assert non_selected_gate2["productionWorkspaceMutationAllowed"] is False
    assert malformed_gate2["status"] == "blocked"
    assert malformed_gate2["readinessReady"] is False
    assert "malformed_selected_scope" in malformed_gate2["reasonCodes"]
    assert malformed_gate2["productionWorkspaceMutationAllowed"] is False


def test_gate2_healthz_redacts_unsafe_profile_ref_from_malformed_config() -> None:
    unsafe_profile_ref = "sk-secret-token"
    runtime = OpenMagiRuntime(
        config=parse_runtime_env(
            _base_env(
                CORE_AGENT_PYTHON_GATE2_READINESS_ENABLED="1",
                CORE_AGENT_PYTHON_GATE2_READINESS_KILL_SWITCH="0",
                CORE_AGENT_PYTHON_GATE2_READINESS_LOCAL_SANDBOX_HARNESS="1",
                CORE_AGENT_PYTHON_GATE2_READINESS_SELECTED_BOT_DIGEST=_digest("bot-gate2"),
                CORE_AGENT_PYTHON_GATE2_READINESS_TRUSTED_OWNER_USER_ID_DIGEST=_digest(
                    "owner-gate2"
                ),
                CORE_AGENT_PYTHON_GATE2_READINESS_ENVIRONMENT="staging",
                CORE_AGENT_PYTHON_GATE2_READINESS_ENV_ALLOWLIST="staging",
                CORE_AGENT_PYTHON_GATE2_READINESS_PROFILE_REF=unsafe_profile_ref,
                CORE_AGENT_PYTHON_GATE2_READINESS_PROFILE_DIGEST=_digest(
                    "unsafe-profile"
                ),
            )
        )
    )

    gate2 = TestClient(create_app(runtime)).get("/healthz").json()["gate2Readiness"]

    assert gate2["status"] == "blocked"
    assert gate2["profileRef"] == "invalid_profile_ref"
    assert "profile_ref_malformed" in gate2["reasonCodes"]
    assert unsafe_profile_ref not in str(gate2)
    assert gate2["productionWorkspaceMutationAllowed"] is False


def test_gate2_readiness_blocks_profile_digest_mismatch() -> None:
    runtime = OpenMagiRuntime(
        config=parse_runtime_env(
            _base_env(
                CORE_AGENT_PYTHON_GATE2_READINESS_ENABLED="1",
                CORE_AGENT_PYTHON_GATE2_READINESS_KILL_SWITCH="0",
                CORE_AGENT_PYTHON_GATE2_READINESS_LOCAL_SANDBOX_HARNESS="1",
                CORE_AGENT_PYTHON_GATE2_READINESS_SELECTED_BOT_DIGEST=_digest("bot-gate2"),
                CORE_AGENT_PYTHON_GATE2_READINESS_TRUSTED_OWNER_USER_ID_DIGEST=_digest(
                    "owner-gate2"
                ),
                CORE_AGENT_PYTHON_GATE2_READINESS_ENVIRONMENT="staging",
                CORE_AGENT_PYTHON_GATE2_READINESS_ENV_ALLOWLIST="staging",
                CORE_AGENT_PYTHON_GATE2_READINESS_PROFILE_REF=(
                    "openmagi.gate2.workspace-canary.v1"
                ),
                CORE_AGENT_PYTHON_GATE2_READINESS_PROFILE_DIGEST=_digest(
                    "arbitrary-profile-digest"
                ),
            )
        )
    )

    gate2 = TestClient(create_app(runtime)).get("/healthz").json()["gate2Readiness"]

    assert gate2["status"] == "blocked"
    assert gate2["readinessReady"] is False
    assert gate2["selectedScopeMatched"] is True
    assert "profile_digest_mismatch" in gate2["reasonCodes"]
    assert gate2["productionWorkspaceMutationAllowed"] is False


def test_gate2_healthz_redacts_secret_shaped_profile_refs() -> None:
    unsafe_profile_ref = ("gh" + "p_") + ("x" * 24)
    runtime = OpenMagiRuntime(
        config=parse_runtime_env(
            _base_env(
                CORE_AGENT_PYTHON_GATE2_READINESS_ENABLED="1",
                CORE_AGENT_PYTHON_GATE2_READINESS_KILL_SWITCH="0",
                CORE_AGENT_PYTHON_GATE2_READINESS_LOCAL_SANDBOX_HARNESS="1",
                CORE_AGENT_PYTHON_GATE2_READINESS_SELECTED_BOT_DIGEST=_digest("bot-gate2"),
                CORE_AGENT_PYTHON_GATE2_READINESS_TRUSTED_OWNER_USER_ID_DIGEST=_digest(
                    "owner-gate2"
                ),
                CORE_AGENT_PYTHON_GATE2_READINESS_ENVIRONMENT="staging",
                CORE_AGENT_PYTHON_GATE2_READINESS_ENV_ALLOWLIST="staging",
                CORE_AGENT_PYTHON_GATE2_READINESS_PROFILE_REF=unsafe_profile_ref,
                CORE_AGENT_PYTHON_GATE2_READINESS_PROFILE_DIGEST=_digest(
                    "unsafe-profile"
                ),
            )
        )
    )

    gate2 = TestClient(create_app(runtime)).get("/healthz").json()["gate2Readiness"]

    assert gate2["profileRef"] == "invalid_profile_ref"
    assert "profile_ref_malformed" in gate2["reasonCodes"]
    assert unsafe_profile_ref not in str(gate2)


def test_gate2_profile_resolver_uses_recipe_layer_not_core_runtime() -> None:
    from openmagi_core_agent.shadow.gate2_recipe_profile_resolver import (
        resolve_gate2_recipe_profile,
    )

    profile = resolve_gate2_recipe_profile("openmagi.gate2.workspace-canary.v1")

    assert profile.status == "ready"
    assert profile.profile_ref == "openmagi.gate2.workspace-canary.v1"
    assert profile.profile_digest.startswith("sha256:")
    assert "openmagi.context-safety" in profile.selected_pack_ids
    assert "openmagi.evidence" in profile.selected_pack_ids
    assert profile.tools_policy == "sandbox_readwrite_diagnostic"
    assert profile.resolution_source == "recipe_compiler"
    assert profile.core_runtime_owns_workflow_policy is False
    assert profile.production_workspace_mutation_allowed is False
    assert profile.live_tool_refs == ()
    assert profile.runner_route_refs == ()

    with pytest.raises(ValueError, match="live tool refs"):
        profile.model_copy(update={"liveToolRefs": ("live-tool",)})


def test_gate2_shadow_policy_denies_real_actions_and_emits_digest_receipts() -> None:
    from openmagi_core_agent.shadow.gate2_shadow_tool_policy import (
        Gate2SandboxMutationProvider,
        Gate2ShadowWorkspaceToolPolicy,
    )

    policy = Gate2ShadowWorkspaceToolPolicy.default()
    request_digest = _digest("gate2-request")

    file_create = policy.evaluate_action(
        action="FileCreate",
        requestDigest=request_digest,
        idempotencyKey="idem-create",
        relativePath="src/example.py",
        content="print('hello')\n",
    )
    duplicate = policy.evaluate_action(
        action="FileCreate",
        requestDigest=request_digest,
        idempotencyKey="idem-create",
        relativePath="src/example.py",
        content="print('hello')\n",
    )
    conflict = policy.evaluate_action(
        action="FileCreate",
        requestDigest=request_digest,
        idempotencyKey="idem-create",
        relativePath="src/example.py",
        content="print('changed')\n",
    )
    bash = policy.evaluate_action(
        action="Bash",
        requestDigest=request_digest,
        idempotencyKey="idem-bash",
        command="echo unsafe",
    )

    assert file_create.status == "simulated"
    assert file_create.production_workspace_mutation_allowed is False
    assert file_create.receipt.request_digest == request_digest
    assert file_create.receipt.action == "FileCreate"
    assert file_create.receipt.content_digest.startswith("sha256:")
    assert file_create.receipt.public_metadata == {
        "pathDigest": file_create.receipt.path_digest,
        "contentDigest": file_create.receipt.content_digest,
    }
    with pytest.raises(TypeError):
        file_create.receipt.public_metadata["rawPath"] = _digest("src/example.py")  # type: ignore[index]
    assert "hello" not in str(file_create.model_dump(by_alias=True, mode="json"))
    assert duplicate.status == "duplicate"
    assert duplicate.receipt.receipt_digest == file_create.receipt.receipt_digest
    assert conflict.status == "conflict"
    assert conflict.receipt.status == "conflict"
    assert bash.status == "denied"
    assert bash.reason == "forbidden_gate2_action"
    assert bash.handler_called is False

    provider = Gate2SandboxMutationProvider(policy=policy)
    simulated = provider.simulate_mutation(
        action="PatchApply",
        requestDigest=request_digest,
        idempotencyKey="idem-patch",
        relativePath="src/example.py",
        patchDigest=_digest("patch"),
    )
    rolled_back = provider.rollback(
        mutationReceiptDigest=simulated.receipt.receipt_digest,
        requestDigest=request_digest,
        rollbackAction="delete",
        postRollbackDigest=_digest("missing"),
    )

    assert simulated.status == "simulated"
    assert rolled_back.status == "rolled_back"
    assert rolled_back.rollback_receipt is not None
    assert rolled_back.rollback_receipt.mutation_receipt_digest == (
        simulated.receipt.receipt_digest
    )
    assert rolled_back.rollback_receipt.rollback_digest.startswith("sha256:")
    assert rolled_back.rollback_receipt.rollback_action == "delete"
    assert rolled_back.rollback_receipt.rollback_verified is True
    assert rolled_back.rollback_receipt.post_rollback_digest == _digest("missing")
    assert "src/example.py" not in str(rolled_back.model_dump(by_alias=True, mode="json"))


def test_gate2_shadow_policy_hard_denies_forbidden_actions_even_if_overridden() -> None:
    from openmagi_core_agent.shadow.gate2_shadow_tool_policy import (
        Gate2MutationReceipt,
        Gate2SandboxMutationProvider,
        Gate2ShadowWorkspaceToolPolicy,
    )

    policy = Gate2ShadowWorkspaceToolPolicy(
        allowed_actions=("Bash", "FileCreate"),
        forbidden_actions=(),
    )
    denied = policy.evaluate_action(
        action="Bash",
        requestDigest=_digest("gate2-request"),
        idempotencyKey="idem-bash",
        relativePath="script.sh",
        content="echo should-not-run",
    )
    malformed = policy.evaluate_action(
        action="Bash;rm -rf /",
        requestDigest=_digest("gate2-request"),
        idempotencyKey="idem-malformed",
        relativePath="script.sh",
    )
    protected_path = policy.evaluate_action(
        action="FileCreate",
        requestDigest=_digest("gate2-request"),
        idempotencyKey="idem-protected-path",
        relativePath=".env",
        content="SECRET=not-stored",
    )
    arbitrary_action = policy.evaluate_action(
        action="DeployProduction",
        requestDigest=_digest("gate2-request"),
        idempotencyKey="idem-arbitrary-action",
        relativePath="src/example.py",
    )

    assert denied.status == "denied"
    assert denied.handler_called is False
    assert denied.reason == "forbidden_gate2_action"
    assert malformed.status == "denied"
    assert malformed.handler_called is False
    assert malformed.receipt.action == "InvalidAction"
    assert malformed.reason == "malformed_gate2_action"
    assert protected_path.status == "denied"
    assert protected_path.reason == "path_policy_denied"
    assert "SECRET" not in str(protected_path.model_dump(by_alias=True, mode="json"))
    assert arbitrary_action.status == "denied"
    assert arbitrary_action.handler_called is False
    assert arbitrary_action.reason == "forbidden_gate2_action"

    with pytest.raises(ValueError, match="public metadata keys"):
        Gate2MutationReceipt(
            requestDigest=_digest("request"),
            attemptDigest=_digest("attempt"),
            idempotencyKeyDigest=_digest("idem"),
            action="FileCreate",
            status="simulated",
            pathDigest=_digest("path"),
            contentDigest=_digest("content"),
            receiptDigest=_digest("receipt"),
            publicMetadata={"rawPath": _digest("path")},
        )
    with pytest.raises(ValueError, match="denied reason"):
        Gate2MutationReceipt(
            requestDigest=_digest("request"),
            attemptDigest=_digest("attempt"),
            idempotencyKeyDigest=_digest("idem"),
            action="FileCreate",
            status="denied",
            pathDigest=_digest("path"),
            receiptDigest=_digest("receipt"),
            deniedReason="secret=/Users/private",
            publicMetadata={"pathDigest": _digest("path")},
        )

    provider = Gate2SandboxMutationProvider()
    simulated = provider.simulate_mutation(
        action="FileCreate",
        requestDigest=_digest("gate2-request"),
        idempotencyKey="idem-create-once",
        relativePath="src/example.py",
        content="safe",
    )
    first_rollback = provider.rollback(
        mutationReceiptDigest=simulated.receipt.receipt_digest,
        requestDigest=_digest("gate2-request"),
        rollbackAction="delete",
        postRollbackDigest=_digest("missing"),
    )
    second_rollback = provider.rollback(
        mutationReceiptDigest=simulated.receipt.receipt_digest,
        requestDigest=_digest("different-request"),
        rollbackAction="delete",
        postRollbackDigest=_digest("missing"),
    )
    duplicate_rollback = provider.rollback(
        mutationReceiptDigest=simulated.receipt.receipt_digest,
        requestDigest=_digest("gate2-request"),
        rollbackAction="delete",
        postRollbackDigest=_digest("missing"),
    )

    assert first_rollback.status == "rolled_back"
    assert second_rollback.status == "denied"
    assert second_rollback.reason == "rollback_request_mismatch"
    assert second_rollback.handler_called is False
    assert duplicate_rollback.status == "duplicate"
    assert duplicate_rollback.handler_called is False
    assert duplicate_rollback.rollback_receipt == first_rollback.rollback_receipt

    with pytest.raises(ValueError):
        simulated.receipt.model_copy(
            update={"publicMetadata": {"rawPath": _digest("src/example.py")}}
        )
    with pytest.raises(ValueError):
        first_rollback.rollback_receipt.model_copy(
            update={"productionWorkspaceMutationAllowed": True}
        )
    with pytest.raises(ValueError):
        simulated.model_copy(update={"productionWorkspaceMutationAllowed": True})


def test_gate2_shadow_policy_accepts_only_synthetic_loop_a_env_wiring_path() -> None:
    from openmagi_core_agent.shadow.gate2_shadow_tool_policy import (
        Gate2ShadowWorkspaceToolPolicy,
    )

    policy = Gate2ShadowWorkspaceToolPolicy.default()
    allowed = policy.evaluate_action(
        action="FileCreate",
        requestDigest=_digest("gate2-request"),
        idempotencyKey="gate2-loop-a-env-wiring",
        relativePath="gate2-loop-a/src/env-wiring.txt",
        content="safe synthetic env wiring canary",
    )

    assert allowed.status == "simulated"
    assert allowed.reason == "sandbox_mutation_simulated"
    assert allowed.handler_called is True

    for relative_path in (
        "src/env-wiring.txt",
        "gate2-loop-a/env-wiring.txt",
        "gate2-loop-a/src/.env",
        "gate2-loop-a/src/.env.local",
        "gate2-loop-a/src/key.txt",
        "gate2-loop-a/src/secret-env-wiring.txt",
        "gate2-loop-a/src/env-wiring.sh",
    ):
        denied = policy.evaluate_action(
            action="FileCreate",
            requestDigest=_digest("gate2-request"),
            idempotencyKey=f"denied:{relative_path}",
            relativePath=relative_path,
            content="must not write",
        )

        assert denied.status == "denied"
        assert denied.reason == "path_policy_denied"
        assert denied.handler_called is False


def test_gate2_import_boundary_does_not_load_live_runtime_surfaces() -> None:
    script = r"""
import sys
import openmagi_core_agent.shadow.gate2_recipe_profile_resolver
import openmagi_core_agent.shadow.gate2_shadow_tool_policy

forbidden = {
    "google.adk.runners",
    "google.adk.models",
    "google.genai",
    "openmagi_core_agent.transport.chat",
    "openmagi_core_agent.workspace.adoption_boundary",
    "openmagi_core_agent.memory.write_boundary",
    "openmagi_core_agent.browser.live_provider_pack",
    "openmagi_core_agent.channels.dispatcher",
}
loaded = sorted(name for name in forbidden if name in sys.modules)
if loaded:
    raise SystemExit("forbidden imports loaded: " + ",".join(loaded))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
