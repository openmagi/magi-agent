from __future__ import annotations

import asyncio
import sys
from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import AsyncGenerator

import pytest
from google.adk.models import BaseLlm, LlmResponse
from google.genai import types

import magi_agent.cli.real_runner as real_runner
from magi_agent.adk_bridge.control_plane import (
    CONTROL_PLANE_PLUGIN_NAME,
    SELF_REVIEW_AFTER_TURN_CONTROL_NAME,
)
from magi_agent.cli.local_runner import LocalCliRunner
from magi_agent.cli.providers import ProviderConfig
from magi_agent.cli.real_runner import (
    CliModelRunner,
    CliProviderDependencyError,
    build_cli_model_runner,
)
from magi_agent.cli.wiring import _build_default_runner, _build_first_party_adk_tools
from magi_agent.harness.general_automation.task_completion import (
    RequiredDeliverableEvidence,
    TaskCompletionVerifier,
)
from magi_agent.harness.self_review import (
    REVIEW_DISABLED_TOOLSETS,
    ReviewCandidate,
)
from magi_agent.runtime.fork_runner import ChildResult, ForkCacheShareEvidence

_PROVIDER_ENV = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "FIREWORKS_API_KEY",
    "MAGI_PROVIDER",
    "MAGI_MODEL",
)


class _FakeEchoLlm(BaseLlm):
    """A real ``BaseLlm`` that returns a canned reply (no provider traffic)."""

    async def generate_content_async(
        self, llm_request: object, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        yield LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part(text="ECHO: hi")],
            )
        )


class _FakeFunctionCall:
    def __init__(self, *, name: str, call_id: str) -> None:
        self.name = name
        self.id = call_id


class _FakeAdkToolContext:
    def __init__(self, *, invocation_id: str, tool_name: str, call_id: str) -> None:
        self.invocation_id = invocation_id
        self.function_call = _FakeFunctionCall(name=tool_name, call_id=call_id)


class _FakeSelfReviewSink:
    def __init__(self) -> None:
        self.received: list[ReviewCandidate] = []

    def receive(self, candidate: ReviewCandidate) -> None:
        self.received.append(candidate)


class _FakeSelfReviewForkRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def fork(
        self,
        *,
        parent_turn_id: str,
        system_prompt_blocks: list[dict[str, object]],
        parent_assistant_message: dict[str, object],
        child_directives: list[str],
        disabled_toolsets: tuple[str, ...] = (),
    ) -> tuple[list[ChildResult], ForkCacheShareEvidence]:
        self.calls.append(
            {
                "parent_turn_id": parent_turn_id,
                "system_prompt_blocks": system_prompt_blocks,
                "parent_assistant_message": parent_assistant_message,
                "disabled_toolsets": disabled_toolsets,
            }
        )
        return (
            [
                ChildResult(
                    directive=child_directives[0],
                    status="ok",
                    output=(
                        '{"kind":"memory","proposal":"Remember the real runner '
                        'self-review hook fired.","confidence":0.9}'
                    ),
                )
            ],
            ForkCacheShareEvidence(
                parentTurnId=parent_turn_id,
                childCount=len(child_directives),
                sharedPrefixFingerprint="fake-fp",
                disabledToolsets=disabled_toolsets,
                status="ok",
                elapsedMs=0.1,
            ),
        )


_SELF_REVIEW_NOW = datetime(2026, 6, 8, 12, 0, 0, tzinfo=UTC)


def _config() -> ProviderConfig:
    return ProviderConfig(
        provider="anthropic", model="claude-sonnet-4-5", api_key="sk-test"
    )


def _fake_model_factory(config: ProviderConfig) -> BaseLlm:
    return _FakeEchoLlm(model="fake")


@pytest.fixture(autouse=True)
def _clear_provider_env(monkeypatch, tmp_path) -> None:
    for name in _PROVIDER_ENV:
        monkeypatch.delenv(name, raising=False)
    # Point config resolution at a non-existent file so a developer's real
    # ~/.magi/config.toml cannot influence these tests.
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "absent.toml"))


def _collect_text(events: list[object]) -> list[str]:
    texts: list[str] = []
    for event in events:
        content = getattr(event, "content", None)
        for part in getattr(content, "parts", None) or []:
            text = getattr(part, "text", None)
            if isinstance(text, str) and text:
                texts.append(text)
    return texts


def _tool_by_name(tools: list[object], name: str) -> object:
    for tool in tools:
        if getattr(tool, "name", None) == name:
            return tool
    raise AssertionError(f"{name} not attached")


def _run_adk_tool(
    tool: object,
    arguments: dict[str, object],
    *,
    invocation_id: str,
    call_id: str,
) -> dict[str, object]:
    return asyncio.run(
        tool.run_async(
            args={"arguments": arguments},
            tool_context=_FakeAdkToolContext(
                invocation_id=invocation_id,
                tool_name=getattr(tool, "name", "tool"),
                call_id=call_id,
            ),
        )
    )


def test_build_returns_cli_model_runner_exposing_agent() -> None:
    runner = build_cli_model_runner(_config(), model_factory=_fake_model_factory)
    assert isinstance(runner, CliModelRunner)
    # The permission gate attaches a before_tool_callback to ``runner.agent``.
    assert runner.agent is not None


def _tool_names(agent: object) -> set[str]:
    return {getattr(tool, "name", None) for tool in getattr(agent, "tools", [])}


def test_build_cli_model_runner_attaches_real_tools(tmp_path) -> None:
    runner = build_cli_model_runner(
        _config(),
        model_factory=_fake_model_factory,
        workspace_root=str(tmp_path),
    )
    names = _tool_names(runner.agent)
    assert names  # non-empty
    assert "FileRead" in names
    # The instruction is the real system prompt, not the removed hand-written stub.
    instruction = getattr(runner.agent, "instruction", "")
    assert "<output-rules>" in instruction
    assert "<tool-preferences>" in instruction


def test_build_cli_model_runner_injects_memory_block_when_gate_on(
    monkeypatch, tmp_path
) -> None:
    """The frozen memory snapshot reaches the Agent instruction through the
    production runner factory (build_cli_model_runner), not just the helper.

    This is the same factory the hosted local-dashboard chat turn uses via
    cli.wiring.build_headless_runtime -> _build_default_runner, so this proves
    memory recall reaches the model on the live hosted SSE path.
    """
    (tmp_path / "MEMORY.md").write_text(
        "# Memory\nImportant recall data.", encoding="utf-8"
    )
    monkeypatch.setenv("MAGI_MEMORY_PROJECTION_ENABLED", "1")

    runner = build_cli_model_runner(
        _config(),
        model_factory=_fake_model_factory,
        workspace_root=str(tmp_path),
    )
    instruction = getattr(runner.agent, "instruction", "")
    assert "<memory-context" in instruction
    assert "Important recall data" in instruction


def test_build_cli_model_runner_no_memory_block_when_gate_off(
    monkeypatch, tmp_path
) -> None:
    """With the projection gate off, no memory-context reaches the Agent."""
    (tmp_path / "MEMORY.md").write_text(
        "# Memory\nImportant recall data.", encoding="utf-8"
    )
    monkeypatch.delenv("MAGI_MEMORY_PROJECTION_ENABLED", raising=False)

    runner = build_cli_model_runner(
        _config(),
        model_factory=_fake_model_factory,
        workspace_root=str(tmp_path),
    )
    instruction = getattr(runner.agent, "instruction", "")
    assert "<memory-context" not in instruction


def test_run_async_drives_real_adk_runner_and_autocreates_session() -> None:
    runner = build_cli_model_runner(_config(), model_factory=_fake_model_factory)

    async def _drive() -> list[object]:
        new_message = types.Content(role="user", parts=[types.Part(text="hi")])
        return [
            event
            async for event in runner.run_async(
                user_id="u1", session_id="s1", new_message=new_message
            )
        ]

    events = asyncio.run(_drive())
    assert any("ECHO: hi" in text for text in _collect_text(events))


def test_real_runner_self_review_after_turn_runs_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_RUNTIME_PROFILE", "safe")
    monkeypatch.setenv("MAGI_SELF_REVIEW_ENABLED", "1")
    monkeypatch.setenv("MAGI_SELF_REVIEW_SHADOW", "1")
    scheduled: list[Coroutine[object, object, None]] = []
    fork_runner = _FakeSelfReviewForkRunner()
    sink = _FakeSelfReviewSink()

    runner = build_cli_model_runner(
        _config(),
        model_factory=_fake_model_factory,
        tools=[],
        instruction="Self-review real runner instruction.",
        session_id="sid-self-review",
        self_review_fork_runner=fork_runner,
        self_review_candidate_sink=sink,
        self_review_now=_SELF_REVIEW_NOW,
        self_review_scheduler=scheduled.append,
    )
    plane_plugin = next(
        plugin
        for plugin in runner._runner.plugin_manager.plugins
        if plugin.name == CONTROL_PLANE_PLUGIN_NAME
    )

    assert SELF_REVIEW_AFTER_TURN_CONTROL_NAME in {
        control.name for control in plane_plugin._p._controls
    }

    async def _drive() -> None:
        async for _event in runner.run_async(
            user_id="u1",
            session_id="sid-self-review",
            new_message=types.Content(role="user", parts=[types.Part(text="hi")]),
        ):
            pass

    asyncio.run(_drive())

    assert len(scheduled) == 1
    asyncio.run(scheduled[0])

    assert len(fork_runner.calls) == 1
    assert fork_runner.calls[0]["disabled_toolsets"] == REVIEW_DISABLED_TOOLSETS
    assert fork_runner.calls[0]["system_prompt_blocks"] == [
        {"type": "text", "text": "Self-review real runner instruction."}
    ]
    assert fork_runner.calls[0]["parent_assistant_message"] == {
        "role": "assistant",
        "content": [{"type": "text", "text": "ECHO: hi"}],
    }
    assert len(sink.received) == 1
    assert sink.received[0].mode == "shadow"
    assert sink.received[0].acted is False


def test_real_runner_self_review_after_turn_stays_off_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_RUNTIME_PROFILE", "safe")
    monkeypatch.delenv("MAGI_SELF_REVIEW_ENABLED", raising=False)
    scheduled: list[Coroutine[object, object, None]] = []

    runner = build_cli_model_runner(
        _config(),
        model_factory=_fake_model_factory,
        tools=[],
        instruction="Self-review disabled instruction.",
        session_id="sid-self-review-off",
        self_review_fork_runner=_FakeSelfReviewForkRunner(),
        self_review_candidate_sink=_FakeSelfReviewSink(),
        self_review_now=_SELF_REVIEW_NOW,
        self_review_scheduler=scheduled.append,
    )
    plane_plugin = next(
        plugin
        for plugin in runner._runner.plugin_manager.plugins
        if plugin.name == CONTROL_PLANE_PLUGIN_NAME
    )

    assert SELF_REVIEW_AFTER_TURN_CONTROL_NAME not in {
        control.name for control in plane_plugin._p._controls
    }

    async def _drive() -> None:
        async for _event in runner.run_async(
            user_id="u1",
            session_id="sid-self-review-off",
            new_message=types.Content(role="user", parts=[types.Part(text="hi")]),
        ):
            pass

    asyncio.run(_drive())

    assert scheduled == []


def test_missing_litellm_raises_actionable_error(monkeypatch) -> None:
    # Force ``import google.adk.models.lite_llm`` to fail.
    monkeypatch.setitem(sys.modules, "google.adk.models.lite_llm", None)
    with pytest.raises(CliProviderDependencyError) as excinfo:
        real_runner._build_litellm_model(_config())
    assert "litellm" in str(excinfo.value)
    assert "default runtime dependencies" in str(excinfo.value)


def test_default_runner_is_stub_without_provider() -> None:
    runner = _build_default_runner()
    assert isinstance(runner, LocalCliRunner)
    assert runner.notice is None


def test_default_runner_is_real_when_provider_configured(monkeypatch) -> None:
    monkeypatch.setattr(real_runner, "_build_litellm_model", _fake_model_factory)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    runner = _build_default_runner()
    assert isinstance(runner, CliModelRunner)


def test_default_runner_attaches_first_party_tools_when_provider_configured(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(real_runner, "_build_litellm_model", _fake_model_factory)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")

    runner = _build_default_runner(cwd=tmp_path, session_id="sid-tools")

    assert isinstance(runner, CliModelRunner)
    tool_names = {tool.name for tool in runner.agent.tools}
    assert {
        "FileRead",
        "Grep",
        "Bash",
        "Browser",
        "DocumentWrite",
        "AgentMemorySearch",
        "SkillLoader",
    }.issubset(tool_names)
    assert "ToolSearch" not in tool_names


def test_headless_default_runner_records_ga_dispatch_receipts(
    monkeypatch,
    tmp_path,
) -> None:
    from magi_agent.cli.wiring import build_headless_runtime

    monkeypatch.setattr(real_runner, "_build_litellm_model", _fake_model_factory)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    monkeypatch.setenv("MAGI_GA_LIVE_ENABLED", "1")

    runtime = build_headless_runtime(cwd=tmp_path, session_id="sid-ga-headless")
    runner = runtime.engine.runner
    assert isinstance(runner, CliModelRunner)
    bash = _tool_by_name(runner.agent.tools, "Bash")

    result = _run_adk_tool(
        bash,
        {"command": f"rm -rf {tmp_path / 'data'}"},
        invocation_id="turn-ga",
        call_id="call-ga",
    )

    assert result["status"] == "blocked"
    assert result["metadata"]["generalAutomationReceipt"]["status"] == "blocked"
    assert set(
        result["metadata"]["generalAutomationReceipt"]["authorityFlags"].values()
    ) == {False}
    ledger = runner.general_automation_receipts.ledger_for_turn(
        session_id="sid-ga-headless",
        turn_id="turn-ga",
    )
    assert ledger is not None
    assert ledger.entries[0].metadata["generalAutomationReceipt"]["status"] == "blocked"
    collected = runtime.engine._collect_evidence("turn-ga")
    assert len(collected) == 1
    assert collected[0].metadata["generalAutomationReceipt"]["status"] == "blocked"
    assert runtime.general_automation_receipts is runner.general_automation_receipts
    assert TaskCompletionVerifier().evaluate(
        ledger,
        RequiredDeliverableEvidence(),
    ).status == "pass"


def test_first_party_cli_tools_run_mutations_with_per_invocation_scope(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("MAGI_GA_LIVE_ENABLED", "0")
    tools = _build_first_party_adk_tools(cwd=tmp_path, session_id="sid-tools")
    file_write = _tool_by_name(tools, "FileWrite")

    first = _run_adk_tool(
        file_write,
        {"path": "notes/one.txt", "content": "one\n"},
        invocation_id="turn-1",
        call_id="call-1",
    )
    second = _run_adk_tool(
        file_write,
        {"path": "notes/two.txt", "content": "two\n"},
        invocation_id="turn-2",
        call_id="call-2",
    )

    assert first["status"] == "ok"
    assert second["status"] == "ok"
    assert (tmp_path / "notes" / "one.txt").read_text(encoding="utf-8") == "one\n"
    assert (tmp_path / "notes" / "two.txt").read_text(encoding="utf-8") == "two\n"
    first_receipt = first["metadata"]["gate5bFullToolhostReceipt"]
    second_receipt = second["metadata"]["gate5bFullToolhostReceipt"]
    assert first_receipt["requestDigest"] != second_receipt["requestDigest"]


def test_first_party_cli_tools_include_callable_todowrite(tmp_path) -> None:
    tools = _build_first_party_adk_tools(cwd=tmp_path, session_id="sid-todo")

    assert "TodoWrite" in {getattr(tool, "name", None) for tool in tools}

    todo_write = _tool_by_name(tools, "TodoWrite")
    first = _run_adk_tool(
        todo_write,
        {"todos": [{"content": "Plan", "status": "in_progress"}]},
        invocation_id="turn-1",
        call_id="call-1",
    )
    second = _run_adk_tool(
        todo_write,
        {
            "todos": [
                {"content": "Plan", "status": "completed"},
                {"content": "Build", "status": "in_progress"},
            ]
        },
        invocation_id="turn-2",
        call_id="call-2",
    )

    assert first["status"] == "ok"
    assert first["output"]["todos"] == [{"content": "Plan", "status": "in_progress"}]
    # Second call replaces the list within the same CLI session.
    assert second["status"] == "ok"
    assert second["output"]["todos"][1] == {"content": "Build", "status": "in_progress"}


def test_default_runner_can_disable_first_party_tools(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(real_runner, "_build_litellm_model", _fake_model_factory)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    monkeypatch.setenv("MAGI_FIRST_PARTY_TOOLS_ENABLED", "0")

    runner = _build_default_runner(cwd=tmp_path, session_id="sid-tools-disabled")

    assert isinstance(runner, CliModelRunner)
    assert runner.agent.tools == []


def test_default_runner_falls_back_with_notice_when_dependency_missing(
    monkeypatch,
) -> None:
    def _boom(config: ProviderConfig) -> BaseLlm:
        raise CliProviderDependencyError(
            "Provider 'anthropic' is configured but the 'litellm' dependency is "
            "not installed. Reinstall magi-agent so its default runtime "
            "dependencies are present."
        )

    monkeypatch.setattr(real_runner, "_build_litellm_model", _boom)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    runner = _build_default_runner()
    assert isinstance(runner, LocalCliRunner)
    assert runner.notice is not None
    assert "litellm" in runner.notice
