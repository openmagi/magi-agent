from __future__ import annotations

import asyncio
import sys
from typing import AsyncGenerator

import pytest
from google.adk.models import BaseLlm, LlmResponse
from google.genai import types

import magi_agent.cli.real_runner as real_runner
from magi_agent.cli.local_runner import LocalCliRunner
from magi_agent.cli.providers import ProviderConfig
from magi_agent.cli.real_runner import (
    CliModelRunner,
    CliProviderDependencyError,
    build_cli_model_runner,
)
from magi_agent.cli.wiring import _build_default_runner, _build_first_party_adk_tools

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


def test_missing_litellm_raises_actionable_error(monkeypatch) -> None:
    # Force ``import google.adk.models.lite_llm`` to fail.
    monkeypatch.setitem(sys.modules, "google.adk.models.lite_llm", None)
    with pytest.raises(CliProviderDependencyError) as excinfo:
        real_runner._build_litellm_model(_config())
    assert "litellm" in str(excinfo.value)
    assert "magi-agent[providers]" in str(excinfo.value)


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


def test_first_party_cli_tools_run_mutations_with_per_invocation_scope(
    tmp_path,
) -> None:
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
            "not installed. Install it with: pip install 'magi-agent[providers]'"
        )

    monkeypatch.setattr(real_runner, "_build_litellm_model", _boom)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    runner = _build_default_runner()
    assert isinstance(runner, LocalCliRunner)
    assert runner.notice is not None
    assert "litellm" in runner.notice
