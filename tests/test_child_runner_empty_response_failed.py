"""When the ADK runner finishes with ZERO text + ZERO tool calls, the child
turn is a silent no-op — the genuine root cause of the bug Kevin chased for
days. Pre-fix, ``_collect_turn_text`` returned ``("", ())`` in that case and
the SpawnAgent envelope ended up as ``status="ok"`` + ``summary=""``.

That ok-but-empty envelope is what triggered the parent agent's chaotic
filesystem / SQLite / JSONL spelunking in his 10:33 PM repro — the model
sees "spawn succeeded" but no answer, so it tries to "find" the output
that doesn't exist anywhere. PR #827 (``ChildLlmTurnError`` on ADK
``error_code`` events) caught the ERROR-EVENT shape of this failure but
not the SILENT-EMPTY shape — some provider+model combinations (notably
anthropic / gemini in the 100ms repro) emit NO events at all, not even
an error event, so #827's branch never fires.

Fix: when the async-for loop completes with empty ``texts`` AND no tool
events were produced, raise the same typed exception so the outer
``run_child`` returns ``status="failed"`` with a sanitized reason
(``child_llm_empty_response``). The parent agent sees an honest failure
and can escalate to the user instead of inventing a recovery path.
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
# empty-response failure these tests assert. Pin the flag OFF so the legacy
# path (which honors the injected runner) is exercised; the governed path has
# its own coverage (test_child_runner_governed_empty_response,
# test_child_runner_collector_status_guard).
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
def _isolate_env(monkeypatch, tmp_path) -> None:
    for name in _PROVIDER_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv(LIVE_CHILD_RUNNER_ENABLED_ENV, raising=False)
    monkeypatch.delenv(LIVE_CHILD_RUNNER_KILL_SWITCH_ENV, raising=False)
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "absent.toml"))


def _request() -> ChildTaskRequest:
    return ChildTaskRequest(
        parentExecutionId="parent-exec-empty",
        turnId="turn-empty",
        taskId="task-empty",
        objective="Compute 1+1 and reply with just the result.",
        role="general",
        delivery="return",
    )


def _provider_config() -> object:
    from magi_agent.cli.providers import ProviderConfig

    return ProviderConfig(
        provider="anthropic", model="claude-sonnet-4-6", api_key="sk-test"
    )


class _EmptyStreamRunner:
    """ADK ``run_async`` that yields ZERO events — the silent-no-op shape.

    No content, no error_code, nothing. This is what some provider/model
    combinations actually emit (the 100ms anthropic/gemini repro).
    """

    def __init__(self) -> None:
        self.calls = 0

    async def run_async(self, **kwargs: Any) -> AsyncGenerator[object, None]:
        self.calls += 1
        return
        yield  # pragma: no cover — generator marker.


class _FakePart:
    def __init__(self, text: str | None) -> None:
        self.text = text


class _FakeContent:
    def __init__(self, parts: list[_FakePart] | None) -> None:
        self.parts = parts or []


class _FakeTextEvent:
    def __init__(self, text: str) -> None:
        self.error_code = None
        self.error_message = None
        self.content = _FakeContent(parts=[_FakePart(text)])


class _TextOnlyRunner:
    """Emits a real text event — must keep working (regression guard)."""

    async def run_async(self, **kwargs: Any) -> AsyncGenerator[object, None]:
        yield _FakeTextEvent("ANSWER: 2")


class _ThoughtOnlyEvent:
    """An event whose content has parts but no extractable ``text`` (e.g.
    a thinking-only event with ``thought=True`` / no ``text`` field). The
    loop collects no text from it — must still trip the empty-response
    failure path the same way zero-events does."""

    def __init__(self) -> None:
        self.error_code = None
        self.error_message = None
        self.content = _FakeContent(parts=[_FakePart(None)])


class _ThoughtOnlyRunner:
    async def run_async(self, **kwargs: Any) -> AsyncGenerator[object, None]:
        yield _ThoughtOnlyEvent()


@pytest.mark.asyncio
async def test_zero_events_surfaces_as_failed_with_empty_response_reason() -> None:
    child = RealLocalChildRunner(
        env=_GOVERNED_OFF_ENV,
        provider_config=_provider_config(),
        runner=_EmptyStreamRunner(),
    )
    result = await child.run_child(_request())
    assert result["status"] == "failed", (
        "ADK runner yielding zero events must NOT silently project as "
        f"status=ok summary='' — got {dict(result)}"
    )
    summary = str(result.get("summary", ""))
    # The sanitized reason must surface "empty_response" so the parent agent
    # (and any downstream observability consumer) can act on the actual class
    # of failure instead of inventing a recovery path.
    assert "empty_response" in summary or "child_llm_empty" in summary, summary


@pytest.mark.asyncio
async def test_thought_only_events_also_fail_with_empty_response() -> None:
    # Some adaptive-thinking model envelopes yield ONLY thought parts (no
    # text). The current text-only collector skips them — same end result as
    # zero events — so this case must also surface as failed.
    child = RealLocalChildRunner(
        env=_GOVERNED_OFF_ENV,
        provider_config=_provider_config(),
        runner=_ThoughtOnlyRunner(),
    )
    result = await child.run_child(_request())
    assert result["status"] == "failed", dict(result)


@pytest.mark.asyncio
async def test_real_text_events_still_succeed() -> None:
    # Regression guard: a child that produces actual text must still complete.
    child = RealLocalChildRunner(
        env=_GOVERNED_OFF_ENV,
        provider_config=_provider_config(),
        runner=_TextOnlyRunner(),
    )
    result = await child.run_child(_request())
    assert result["status"] == "completed", dict(result)
    assert result["summary"] == "ANSWER: 2"
