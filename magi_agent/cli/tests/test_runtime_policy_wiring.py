from __future__ import annotations

import asyncio
from typing import AsyncIterator

from google.adk.models import BaseLlm, LlmResponse
from google.genai import types

import magi_agent.cli.engine as engine_module
import magi_agent.cli.real_runner as real_runner
from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.cli.engine import MagiEngineDriver, RunnerPolicyAssembly
from magi_agent.cli.providers import ProviderConfig
from magi_agent.cli.real_runner import build_cli_model_runner
from magi_agent.cli.wiring import build_headless_runtime
from magi_agent.runtime.events import RuntimeEvent


class _FakeLlm(BaseLlm):
    async def generate_content_async(self, llm_request, stream: bool = False):
        yield LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part(text="ok")],
            )
        )


class _NoopRunner:
    async def run_async(self, **kwargs: object) -> AsyncIterator[object]:
        if False:
            yield kwargs


class _FakePart:
    def __init__(self, *, text: str) -> None:
        self.text = text


class _FakeContent:
    def __init__(self, *, role: str, parts: list[object]) -> None:
        self.role = role
        self.parts = parts


class _FakeTypes:
    Content = _FakeContent
    Part = _FakePart


class _CapturedRunnerInput:
    captured: list["_CapturedRunnerInput"] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.harness_state = kwargs.get("harnessState")
        self.__class__.captured.append(self)


class _FakeAdapter:
    def __init__(self, *, runner: object) -> None:
        self.runner = runner

    async def run_turn(self, runner_input: object) -> AsyncIterator[object]:
        del runner_input
        if False:
            yield object()


class _FakeBridge:
    def __init__(self, *, live_compatible: bool) -> None:
        self.live_compatible = live_compatible

    def project_adk_event(self, adk_event: object, *, turn_id: str) -> object:
        del adk_event, turn_id
        return type("Projection", (), {"agent_events": []})()


def _config() -> ProviderConfig:
    return ProviderConfig(
        provider="anthropic",
        model="claude-sonnet-4-5",
        api_key="sk-test",
    )


def _fake_model_factory(config: ProviderConfig) -> BaseLlm:
    return _FakeLlm(model="fake")


def _fake_engine_deps() -> dict[str, object]:
    return {
        "types": _FakeTypes,
        "OpenMagiEventBridge": _FakeBridge,
        "OpenMagiRunnerAdapter": _FakeAdapter,
        "RunnerTurnInput": _CapturedRunnerInput,
        "sanitize_agent_event": lambda event: event,
    }


def _callback_names(agent: object) -> list[str]:
    callback = getattr(agent, "before_tool_callback", None)
    if callback is None:
        return []
    callbacks = callback if isinstance(callback, list) else [callback]
    return [getattr(item, "__name__", "") for item in callbacks]


def _coding_policy_assembly() -> RunnerPolicyAssembly:
    return RunnerPolicyAssembly(
        modelProvider="anthropic",
        modelLabel="anthropic/claude-opus-4-1",
        selectedPackIds=("openmagi.dev-coding",),
        evidenceRequirements=("evidence:git-diff",),
        requiredValidators=("verifier:dev-coding:test-evidence",),
        missingEvidenceAction="repair_required",
        repairPolicy={"action": "repair_required", "source": "recipe-materializer"},
        attachmentFlags={
            "providerCalled": False,
            "routeAttached": False,
            "adkRunnerInvoked": False,
            "productionWriteAllowed": False,
            "userVisibleOutputAllowed": False,
            "livePolicyCallbackAttached": True,
        },
    )


def test_cli_model_runner_attaches_first_party_policy_callback_by_default(
    tmp_path,
) -> None:
    runner = build_cli_model_runner(
        _config(),
        model_factory=_fake_model_factory,
        workspace_root=str(tmp_path),
    )

    assert "magi_first_party_policy_before_tool" in _callback_names(runner.agent)
    assembly = runner.runner_policy_assembly
    assert assembly is not None
    assert "openmagi.dev-coding" in assembly.selected_pack_ids
    assert "openmagi.research" in assembly.selected_pack_ids
    assert "openmagi.superpowers-compat" in assembly.selected_pack_ids
    assert "evidence:git-diff" in assembly.evidence_requirements
    assert "verifier:dev-coding:test-evidence" in assembly.required_validators
    assert assembly.attachment_flags["productionWriteAllowed"] is False
    assert assembly.attachment_flags["livePolicyCallbackAttached"] is True
    payload = assembly.to_public_payload()
    assert "source_acquisition" in payload["phaseRouting"]["phaseRoutes"]
    assert "test_interpretation" in payload["phaseRouting"]["phaseRoutes"]


def test_cli_model_runner_materializes_task_profile_phase_routing(tmp_path) -> None:
    runner = build_cli_model_runner(
        _config(),
        model_factory=_fake_model_factory,
        workspace_root=str(tmp_path),
        task_profile={"taskType": "research"},
    )

    assembly = runner.runner_policy_assembly
    assert assembly is not None
    assert "openmagi.research" in assembly.selected_pack_ids
    assert "openmagi.dev-coding" not in assembly.selected_pack_ids

    payload = assembly.to_public_payload()
    assert payload["taskProfile"] == {"taskType": "research"}
    phase_routes = payload["phaseRouting"]["phaseRoutes"]
    assert "source_acquisition" in phase_routes
    assert "source_extraction" in phase_routes
    assert phase_routes["source_acquisition"]["provider"] == "anthropic"


def test_headless_runtime_threads_default_policy_to_engine(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "absent.toml"))
    monkeypatch.setattr(
        real_runner,
        "_build_litellm_model",
        lambda _config: _FakeLlm(model="fake"),
    )

    runtime = build_headless_runtime(
        cwd=tmp_path,
        session_id="sid-policy",
        model="claude-opus-4-1",
    )

    runner = runtime.engine.runner
    assert getattr(runner, "model_provider") == "anthropic"
    assert getattr(runner, "model_label") == "anthropic/claude-opus-4-1"
    assert runtime.engine.runner_policy_assembly is runner.runner_policy_assembly
    assert "openmagi.dev-coding" in runtime.engine.runner_policy_assembly.selected_pack_ids


def test_engine_blocks_completed_turn_when_policy_evidence_is_missing(
    monkeypatch,
) -> None:
    _CapturedRunnerInput.captured = []
    monkeypatch.setattr(engine_module, "_lazy_engine_deps", _fake_engine_deps)
    assembly = _coding_policy_assembly()
    driver = MagiEngineDriver(runner=_NoopRunner(), runner_policy_assembly=assembly)

    async def _drive() -> list[object]:
        return [
            item
            async for item in driver.run_turn_stream(
                runtime=object(),
                turn_input={
                    "prompt": "finish the patch",
                    "session_id": "s1",
                    "turn_id": "t1",
                },
                cancel=asyncio.Event(),
            )
        ]

    items = asyncio.run(_drive())
    events = [item for item in items if isinstance(item, RuntimeEvent)]
    terminal = items[-1]

    assert isinstance(terminal, EngineResult)
    assert terminal.terminal == Terminal.error
    assert terminal.error == "pre_final_evidence_gate_blocked"
    assert _CapturedRunnerInput.captured[0].harness_state["runnerPolicyAssembly"][
        "selectedPackIds"
    ] == ["openmagi.dev-coding"]
    assert events[0].payload["type"] == "runner_policy_assembly"
    gate_event = events[-1].payload
    assert gate_event["type"] == "pre_final_evidence_gate"
    assert gate_event["decision"] == "block"
    assert gate_event["missingEvidence"] == ["evidence:git-diff"]
    assert gate_event["missingValidators"] == ["verifier:dev-coding:test-evidence"]
    assert gate_event["repairPolicy"]["action"] == "repair_required"
    assert gate_event["repairDecision"]["type"] == "coding_repair_decision"
    assert gate_event["repairDecision"]["action"] == "continue_repair"
    assert gate_event["repairDecision"]["attemptCount"] == 1
    assert "missing_evidence" in gate_event["repairDecision"]["reasonCodes"]
    assert gate_event["verifierBus"]["metadataOnly"] is True
    assert gate_event["verifierBus"]["decision"] == "block"
    assert gate_event["verifierBus"]["trafficAttached"] is False
    assert gate_event["verifierBus"]["executionAttached"] is False
    verifier_results = gate_event["verifierBus"]["results"]
    assert {
        result["verifierId"]: result["status"] for result in verifier_results
    } == {
        "tool-evidence-contract": "missing",
        "dev-coding-verification-audit": "missing",
    }
    assert gate_event["attachmentFlags"]["productionWriteAllowed"] is False


def test_engine_does_not_block_general_chat_when_coding_policy_is_available(
    monkeypatch,
) -> None:
    _CapturedRunnerInput.captured = []
    monkeypatch.setattr(engine_module, "_lazy_engine_deps", _fake_engine_deps)
    driver = MagiEngineDriver(
        runner=_NoopRunner(),
        runner_policy_assembly=_coding_policy_assembly(),
    )

    async def _drive() -> list[object]:
        return [
            item
            async for item in driver.run_turn_stream(
                runtime=object(),
                turn_input={
                    "prompt": "hi",
                    "session_id": "s-general",
                    "turn_id": "t-general",
                },
                cancel=asyncio.Event(),
            )
        ]

    items = asyncio.run(_drive())
    events = [item for item in items if isinstance(item, RuntimeEvent)]
    terminal = items[-1]

    assert isinstance(terminal, EngineResult)
    assert terminal.terminal == Terminal.completed
    assert terminal.error is None
    gate_events = [
        event.payload
        for event in events
        if event.payload.get("type") == "pre_final_evidence_gate"
    ]
    assert gate_events == []
