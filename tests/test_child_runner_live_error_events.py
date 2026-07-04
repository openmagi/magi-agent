"""When the child's ADK model emits an ``error_code`` event (provider/litellm
failure with NO text parts), the child runner MUST surface it as ``failed`` —
not silently degrade to ``completed`` with an empty ``summary``.

Captured bug shape (post 0.1.62, multi-provider SpawnAgent):

  anthropic:claude-opus-4-8     → status=ok, summary="", latency 128ms
  openai:gpt-5.5                → status=ok, summary="2", latency 10134ms
  gemini:gemini-3.1-pro-preview → status=ok, summary="", latency 58ms

100ms latency means the anthropic/gemini provider call failed immediately
(provider param rejected / model id rejected / auth) and ADK emitted an
``error_code`` event WITHOUT content parts. The previous ``_collect_turn_text``
only collected ``part.text`` from event content and ignored ``event.error_code``,
so the failure was silently projected as a successful empty-text turn — the
parent LLM then waited for "async work" that already finished as a failure.

Fix: inspect ``event.error_code`` / ``event.error_message`` on every event and
raise a typed exception when the fields are populated and non-benign (mirrors
the engine-side classifier in ``adk_bridge.event_adapter``). The outer
``run_child`` catches it and returns a ``failed`` envelope with a sanitized
``child_llm_*`` reason — never status=ok with an empty summary.

A finish-signal-shaped value like ``error_code="STOP"`` is part of the normal
provider envelope (Gemini in particular) and MUST NOT trigger failure.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest

from magi_agent.runtime.child_runner_boundary import ChildTaskRequest
from magi_agent.runtime.child_runner_live import (
    LIVE_CHILD_RUNNER_ENABLED_ENV,
    LIVE_CHILD_RUNNER_KILL_SWITCH_ENV,
    RealLocalChildRunner,
)

# These tests exercise the LEGACY child turn-collection path. The governed-turn
# primitive (MAGI_SUBAGENT_GOVERNED_TURN_ENABLED) is now profile-default-ON,
# which ignores the injected ``runner=`` and returns the "no live model
# provider is configured" fallback (status=completed) — masking the
# provider-error failure these tests assert. Pin the flag OFF so the legacy
# path (which honors the injected runner) is exercised; the governed path has
# its own coverage.
_GOVERNED_OFF_ENV = {"MAGI_SUBAGENT_GOVERNED_TURN_ENABLED": "0"}

_PROVIDER_ENV = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "FIREWORKS_API_KEY",
    "MAGI_PROVIDER",
    "MAGI_MODEL",
)


@pytest.fixture(autouse=True)
def _isolate_provider_env(monkeypatch, tmp_path) -> None:
    for name in _PROVIDER_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv(LIVE_CHILD_RUNNER_ENABLED_ENV, raising=False)
    monkeypatch.delenv(LIVE_CHILD_RUNNER_KILL_SWITCH_ENV, raising=False)
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "absent.toml"))


def _request() -> ChildTaskRequest:
    return ChildTaskRequest(
        parentExecutionId="parent-exec-err",
        turnId="turn-err",
        taskId="task-err",
        objective="Verify the child surfaces provider errors instead of empty success.",
        role="general",
        delivery="return",
    )


def _provider_config() -> object:
    from magi_agent.cli.providers import ProviderConfig

    return ProviderConfig(
        provider="anthropic", model="claude-sonnet-4-6", api_key="sk-test"
    )


class _FakePart:
    def __init__(self, text: str | None) -> None:
        self.text = text


class _FakeContent:
    def __init__(self, parts: list[_FakePart] | None) -> None:
        self.parts = parts or []


class _FakeErrorEvent:
    """ADK Event shape with populated error_code / error_message and no content."""

    def __init__(self, *, error_code: str, error_message: str | None = None) -> None:
        self.error_code = error_code
        self.error_message = error_message
        # ADK events keep ``content`` even on error, but parts may be empty / None.
        self.content = _FakeContent(parts=None)


class _FakeTextEvent:
    def __init__(self, text: str) -> None:
        self.error_code = None
        self.error_message = None
        self.content = _FakeContent(parts=[_FakePart(text)])


class _FakeBenignFinishEvent:
    """Some providers populate error_code with a benign finish reason
    ("STOP", "end_turn") on the LAST event. That MUST NOT be treated
    as a failure."""

    def __init__(self, *, text: str = "ok", finish_signal: str = "STOP") -> None:
        self.error_code = finish_signal
        self.error_message = None
        self.content = _FakeContent(parts=[_FakePart(text)])


class _ErrorOnlyRunner:
    """Yields ONE error event with no text parts (the silent-no-op repro)."""

    def __init__(self, *, error_code: str, error_message: str | None = None) -> None:
        self._error_code = error_code
        self._error_message = error_message
        self.calls = 0

    async def run_async(self, **kwargs: Any) -> AsyncGenerator[object, None]:
        self.calls += 1
        yield _FakeErrorEvent(
            error_code=self._error_code, error_message=self._error_message
        )


class _BenignFinishRunner:
    """Yields a normal text event whose error_code is a benign finish signal."""

    async def run_async(self, **kwargs: Any) -> AsyncGenerator[object, None]:
        yield _FakeBenignFinishEvent(text="ANSWER: 2")


@pytest.mark.asyncio
async def test_error_event_with_no_text_surfaces_as_failed() -> None:
    runner = _ErrorOnlyRunner(
        error_code="rate_limit_exceeded",
        error_message="Anthropic API rate limit exceeded",
    )
    child = RealLocalChildRunner(
        env=_GOVERNED_OFF_ENV,
        provider_config=_provider_config(),
        runner=runner,
    )
    result = await child.run_child(_request())
    assert result["status"] == "failed", (
        "Provider-side error event with no content must NOT silently become "
        f"a successful empty turn: got {dict(result)}"
    )
    # The sanitized failure reason must SURFACE the provider error class so
    # the operator / parent can act, not the generic "child_turn_error" that
    # used to mask every provider failure into a single opaque token.
    summary = str(result.get("summary", ""))
    assert "rate_limit" in summary or "child_llm" in summary, summary


@pytest.mark.asyncio
async def test_error_event_with_only_error_message_surfaces_as_failed() -> None:
    # Some providers populate only error_message (no error_code).
    runner = _ErrorOnlyRunner(
        error_code="upstream_failure", error_message="model_invocation_failed"
    )
    child = RealLocalChildRunner(
        env=_GOVERNED_OFF_ENV,
        provider_config=_provider_config(),
        runner=runner,
    )
    result = await child.run_child(_request())
    assert result["status"] == "failed", dict(result)


@pytest.mark.asyncio
async def test_benign_finish_signal_in_error_code_is_not_a_failure() -> None:
    # Gemini in particular surfaces finish_reason ("STOP" / "completed") via
    # the error_code field on the last event. That is the normal envelope, not
    # a failure — the child must complete with the collected text.
    child = RealLocalChildRunner(
        env=_GOVERNED_OFF_ENV,
        provider_config=_provider_config(),
        runner=_BenignFinishRunner(),
    )
    result = await child.run_child(_request())
    assert result["status"] == "completed", dict(result)
    assert result["summary"] == "ANSWER: 2"
