from __future__ import annotations

import asyncio
import importlib
import os
from pathlib import Path
import subprocess
import sys

import pytest
from google.adk.agents import Agent
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.artifacts import InMemoryArtifactService
from google.adk.memory import InMemoryMemoryService
from google.adk.models import BaseLlm, LlmRequest, LlmResponse
from google.adk.runners import Runner
from google.adk.tools import FunctionTool
from google.genai import types

from magi_agent.adk_bridge.runner_adapter import (
    ADK_RUNNER_KWARG_ALLOWLIST,
    OpenMagiRunnerAdapter,
    RunnerTurnInput,
)
from magi_agent.adk_bridge.session_service import WorkspaceSessionService
from magi_agent.harness.resolved import build_default_resolved_harness_state


class ProviderLikeLlm(BaseLlm):
    async def generate_content_async(
        self,
        llm_request: LlmRequest,
        stream: bool = False,
    ):
        if False:
            yield LlmResponse()


class DelegatingRunnerSpy:
    def __init__(self, runner: Runner) -> None:
        self.runner = runner
        self.calls: list[dict[str, object]] = []

    async def run_async(self, **kwargs: object):
        self.calls.append(kwargs)
        async for event in self.runner.run_async(**kwargs):
            yield event


def _run_fresh_python(script: str) -> subprocess.CompletedProcess[str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        in {
            "PATH",
            "PYTHONHOME",
            "PYTHONPATH",
            "VIRTUAL_ENV",
        }
    }
    env["CORE_AGENT_PYTHON_LOCAL_ADK_RUNNER"] = "1"
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_factory_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CORE_AGENT_PYTHON_LOCAL_ADK_RUNNER", raising=False)

    local_runner = importlib.import_module("magi_agent.adk_bridge.local_runner")

    with pytest.raises(local_runner.LocalAdkRunnerDisabled):
        local_runner.build_local_adk_runner()


@pytest.mark.parametrize("flag_value", ("1", "true", "TRUE", "yes", "on"))
def test_trueish_flag_values_enable_local_runner_construction(
    monkeypatch: pytest.MonkeyPatch,
    flag_value: str,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_LOCAL_ADK_RUNNER", flag_value)

    local_runner = importlib.import_module("magi_agent.adk_bridge.local_runner")
    bundle = local_runner.build_local_adk_runner()

    assert isinstance(bundle.agent, Agent)
    assert isinstance(bundle.runner, Runner)
    assert isinstance(bundle.session_service, WorkspaceSessionService)
    assert isinstance(bundle.memory_service, InMemoryMemoryService)
    assert isinstance(bundle.artifact_service, InMemoryArtifactService)
    assert bundle.local_only is True
    assert bundle.traffic_attached is False
    assert bundle.production_attached is False
    assert bundle.canary_attached is False
    assert bundle.route_attached is False
    assert bundle.deploy_attached is False
    assert bundle.telegram_attached is False


def test_enabled_local_runner_rejects_arbitrary_official_function_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_LOCAL_ADK_RUNNER", "1")
    calls: list[dict[str, object]] = []

    async def local_receipt(arguments: dict[str, object], tool_context: object) -> dict[str, object]:
        calls.append(arguments)
        return {"status": "ok", "output": {"localOnly": True}}

    tool = FunctionTool(local_receipt, require_confirmation=False)

    local_runner = importlib.import_module("magi_agent.adk_bridge.local_runner")

    with pytest.raises(TypeError, match="LocalToolHostAdkBundle"):
        local_runner.build_local_adk_runner(tools=(tool,))

    assert calls == []


def test_default_factory_agent_model_is_local_inert_adk_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_LOCAL_ADK_RUNNER", "1")

    local_runner = importlib.import_module("magi_agent.adk_bridge.local_runner")
    bundle = local_runner.build_local_adk_runner()

    assert isinstance(bundle.agent.model, BaseLlm)
    assert not isinstance(bundle.agent.model, str)
    assert bundle.agent.model.model != ""
    assert bundle.agent.model.model != "gemini-2.5-flash"
    assert type(bundle.agent.model).__name__ == "LocalInertLlm"


def test_factory_rejects_model_identifier_override_before_agent_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_LOCAL_ADK_RUNNER", "1")

    local_runner = importlib.import_module("magi_agent.adk_bridge.local_runner")

    with pytest.raises(TypeError, match="model"):
        local_runner.build_local_adk_runner(model="gemini-2.5-flash")  # type: ignore[arg-type]


def test_factory_rejects_base_llm_model_override_before_agent_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_LOCAL_ADK_RUNNER", "1")

    local_runner = importlib.import_module("magi_agent.adk_bridge.local_runner")

    with pytest.raises(TypeError, match="model"):
        local_runner.build_local_adk_runner(model=ProviderLikeLlm(model="provider-backed"))


def test_default_local_inert_model_generation_fails_without_provider_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_LOCAL_ADK_RUNNER", "1")

    local_runner = importlib.import_module("magi_agent.adk_bridge.local_runner")
    bundle = local_runner.build_local_adk_runner()

    async def collect_generation() -> list[object]:
        responses: list[object] = []
        async for response in bundle.agent.model.generate_content_async(LlmRequest()):
            responses.append(response)
        return responses

    with pytest.raises(local_runner.LocalAdkRunnerExecutionBlocked, match="local-only inert"):
        asyncio.run(collect_generation())


def test_adapter_with_official_local_runner_uses_adk_runner_and_blocks_provider_traffic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_LOCAL_ADK_RUNNER", "1")
    fake_secret_env = {
        "GOOGLE_API_KEY": "fake-google-provider-key",
        "GEMINI_API_KEY": "fake-gemini-provider-key",
        "OPENAI_API_KEY": "fake-openai-provider-key",
        "ANTHROPIC_API_KEY": "fake-anthropic-provider-key",
    }
    for key, value in fake_secret_env.items():
        monkeypatch.setenv(key, value)

    local_runner = importlib.import_module("magi_agent.adk_bridge.local_runner")
    bundle = local_runner.build_local_adk_runner()
    runner_spy = DelegatingRunnerSpy(bundle.runner)
    adapter = OpenMagiRunnerAdapter(runner=runner_spy)
    turn_input = RunnerTurnInput(
        user_id="user-1",
        session_id="local-session-6e9f5a41",
        turn_id="turn-1",
        invocation_id="turn-1",
        new_message=types.Content(role="user", parts=[types.Part(text="hello")]),
        state_delta={
            "openmagi.currentTurnId": "turn-1",
            "evidenceContracts": ["coding-basic"],
            "control": {"trafficAttached": False, "executionAttached": False},
        },
        run_config={
            "openmagi": {
                "control": "must-not-pass",
                "trafficAttached": True,
                "executionAttached": True,
            }
        },
        harness_state=build_default_resolved_harness_state(agent_role="coding", spawn_depth=1),
    )

    async def collect_with_official_runner() -> list[object]:
        await bundle.session_service.create_session(
            app_name=bundle.runner.app_name,
            user_id=turn_input.user_id,
            session_id=turn_input.session_id,
        )
        return await adapter.collect_events(turn_input)

    with pytest.raises(local_runner.LocalAdkRunnerExecutionBlocked, match="local-only inert"):
        asyncio.run(collect_with_official_runner())

    assert len(runner_spy.calls) == 1
    assert set(runner_spy.calls[0]) <= ADK_RUNNER_KWARG_ALLOWLIST | {"run_config"}
    assert ADK_RUNNER_KWARG_ALLOWLIST <= set(runner_spy.calls[0])
    assert "openmagi.currentTurnId" not in runner_spy.calls[0]
    assert "evidenceContracts" not in runner_spy.calls[0]
    assert "control" not in runner_spy.calls[0]
    assert "trafficAttached" not in runner_spy.calls[0]
    assert "executionAttached" not in runner_spy.calls[0]
    if "run_config" in runner_spy.calls[0]:
        assert isinstance(runner_spy.calls[0]["run_config"], RunConfig)
        assert runner_spy.calls[0]["run_config"].streaming_mode == StreamingMode.SSE
    assert bundle.traffic_attached is False
    assert bundle.production_attached is False
    assert bundle.route_attached is False
    assert bundle.deploy_attached is False
    observable_local_objects = repr(
        {
            "agent": bundle.agent,
            "runner_call": runner_spy.calls[0],
            "bundle_flags": {
                "traffic_attached": bundle.traffic_attached,
                "production_attached": bundle.production_attached,
                "route_attached": bundle.route_attached,
                "deploy_attached": bundle.deploy_attached,
            },
        }
    )
    assert "/data/" not in observable_local_objects
    assert "/workspace" not in observable_local_objects
    for value in fake_secret_env.values():
        assert value not in observable_local_objects


def test_local_runner_construction_does_not_touch_cwd_or_expose_workspace_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_LOCAL_ADK_RUNNER", "1")
    monkeypatch.chdir(tmp_path)

    local_runner = importlib.import_module("magi_agent.adk_bridge.local_runner")
    bundle = local_runner.build_local_adk_runner()

    assert list(tmp_path.iterdir()) == []

    workspace_path_attrs = (
        "workspace_path",
        "workspace_dir",
        "workspace_root",
        "pvc_path",
        "pvc_root",
        "volume_path",
        "bot_workspace_path",
        "bot_pvc_path",
        "path_attached",
        "workspace_attached",
        "pvc_attached",
    )
    exposed_path_attrs = {
        attr: getattr(bundle, attr)
        for attr in workspace_path_attrs
        if hasattr(bundle, attr) and getattr(bundle, attr)
    }
    assert exposed_path_attrs == {}


@pytest.mark.parametrize("flag_value", ("off", "0", "false", "no", "   ", "False", "OFF"))
def test_false_flag_values_keep_factory_disabled(
    monkeypatch: pytest.MonkeyPatch,
    flag_value: str,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_LOCAL_ADK_RUNNER", flag_value)

    local_runner = importlib.import_module("magi_agent.adk_bridge.local_runner")

    with pytest.raises(local_runner.LocalAdkRunnerDisabled):
        local_runner.build_local_adk_runner()


def test_fresh_process_build_stays_production_runtime_route_and_deploy_free() -> None:
    completed = _run_fresh_python(
        """
import importlib
import os
import sys

module = importlib.import_module("magi_agent.adk_bridge.local_runner")
bundle = module.build_local_adk_runner()
assert bundle.local_only is True

forbidden_prefixes = (
    "magi_agent.api",
    "magi_agent.app",
    "magi_agent.dashboard",
    "magi_agent.database",
    "magi_agent.db",
    "magi_agent.runtime.openmagi_runtime",
    "magi_agent.routing",
    "magi_agent.supabase",
    "magi_agent.transport.chat",
    "magi_agent.transport.api",
    "magi_agent.transport.tools",
    "magi_agent.transport.plugins",
    "magi_agent.workspace",
    "magi_agent.web",
    "magi_agent.deploy",
    "magi_agent.canary",
    "magi_agent.proxy",
    "magi_agent.provisioning",
    "magi_agent.k8s",
    "magi_agent.telegram",
    "magi_agent.runtime_selector",
    "src.",
)
loaded = [
    module_name
    for module_name in sys.modules
    if any(
        module_name == prefix or module_name.startswith(prefix)
        for prefix in forbidden_prefixes
    )
]
if loaded:
    raise AssertionError(f"local runner build loaded forbidden modules: {loaded}")
for forbidden_env in (
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "SUPABASE_SERVICE_ROLE_KEY",
    "DATABASE_URL",
    "CHAT_PROXY_TOKEN",
    "KUBECONFIG",
    "HTTPS_PROXY",
):
    if forbidden_env in os.environ:
        raise AssertionError(f"fresh process inherited forbidden env: {forbidden_env}")
"""
    )

    assert completed.returncode == 0, completed.stderr


def test_fresh_process_helper_scrubs_provider_and_production_env_before_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_env = {
        "GOOGLE_API_KEY": "fake-google-provider-key",
        "GEMINI_API_KEY": "fake-gemini-provider-key",
        "OPENAI_API_KEY": "fake-openai-provider-key",
        "ANTHROPIC_API_KEY": "fake-anthropic-provider-key",
        "SUPABASE_SERVICE_ROLE_KEY": "fake-supabase-service-role-key",
        "DATABASE_URL": "postgres://fake-prod-database",
        "CHAT_PROXY_TOKEN": "fake-chat-proxy-token",
        "KUBECONFIG": "/fake/prod/kubeconfig",
        "HTTPS_PROXY": "http://fake-prod-proxy",
    }
    for key, value in fake_env.items():
        monkeypatch.setenv(key, value)

    completed = _run_fresh_python(
        """
import importlib
import os

for forbidden_env in (
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "SUPABASE_SERVICE_ROLE_KEY",
    "DATABASE_URL",
    "CHAT_PROXY_TOKEN",
    "KUBECONFIG",
    "HTTPS_PROXY",
):
    if forbidden_env in os.environ:
        raise AssertionError(f"fresh process inherited forbidden env before import: {forbidden_env}")

module = importlib.import_module("magi_agent.adk_bridge.local_runner")
bundle = module.build_local_adk_runner()
assert bundle.local_only is True

for forbidden_env in (
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "SUPABASE_SERVICE_ROLE_KEY",
    "DATABASE_URL",
    "CHAT_PROXY_TOKEN",
    "KUBECONFIG",
    "HTTPS_PROXY",
):
    if forbidden_env in os.environ:
        raise AssertionError(f"fresh process inherited forbidden env after build: {forbidden_env}")
"""
    )

    assert completed.returncode == 0, completed.stderr


def test_fresh_process_disabled_build_stays_runtime_route_and_infra_free() -> None:
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        in {
            "PATH",
            "PYTHONHOME",
            "PYTHONPATH",
            "VIRTUAL_ENV",
        }
    }
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

module = importlib.import_module("magi_agent.adk_bridge.local_runner")
try:
    module.build_local_adk_runner()
except module.LocalAdkRunnerDisabled:
    pass
else:
    raise AssertionError("local runner build unexpectedly succeeded while disabled")

forbidden_prefixes = (
    "magi_agent.api",
    "magi_agent.app",
    "magi_agent.dashboard",
    "magi_agent.database",
    "magi_agent.db",
    "magi_agent.runtime.openmagi_runtime",
    "magi_agent.routing",
    "magi_agent.supabase",
    "magi_agent.transport.chat",
    "magi_agent.transport.api",
    "magi_agent.transport.tools",
    "magi_agent.transport.plugins",
    "magi_agent.workspace",
    "magi_agent.web",
    "magi_agent.deploy",
    "magi_agent.canary",
    "magi_agent.proxy",
    "magi_agent.provisioning",
    "magi_agent.k8s",
    "magi_agent.telegram",
    "magi_agent.runtime_selector",
    "src.",
)
loaded = [
    module_name
    for module_name in sys.modules
    if any(
        module_name == prefix or module_name.startswith(prefix)
        for prefix in forbidden_prefixes
    )
]
if loaded:
    raise AssertionError(f"disabled local runner import/build loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert completed.returncode == 0, completed.stderr
