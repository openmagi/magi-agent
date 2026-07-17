"""PR #854 closed the silent-empty hole in ``_collect_turn_text_legacy`` —
but the governed branch (``_collect_turn_text_governed``, activated by
``MAGI_SUBAGENT_GOVERNED_TURN_ENABLED=1`` which lab profile auto-enables)
has the SAME hole. Kevin's 0.1.74 repro hit it: anthropic / google
children completed in 83-229ms with status=ok and empty summary, and the
agent surfaced "Anthropic 빈 응답, Google 빈 응답, Cross-Validation 1/3".

PR #854's guard fires at the end of the legacy collector when
``not texts and not evidence_refs``; the governed collector
(:func:`collect_governed_child_turn`) returns ``(summary, evidence_refs,
status)`` and is then handed back to the caller as
``(summary, evidence_refs)`` — empty summary + empty refs flows through
silently as a successful turn.

Fix: apply the same fail-fast guard in the governed branch's return
path. The typed ``_ChildLlmTurnError`` propagates to ``run_child``'s
existing catch (PR #827 + PR #854) and surfaces as
``status="failed"`` reason ``child_llm_empty_response`` — agent stops
narrating "Anthropic returned empty" and instead reports a real failure
class downstream consumers can act on.
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
    # The bug requires the governed branch to run.
    monkeypatch.setenv("MAGI_SUBAGENT_GOVERNED_TURN_ENABLED", "1")
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "absent.toml"))


def _request() -> ChildTaskRequest:
    return ChildTaskRequest(
        parentExecutionId="parent-exec-governed-empty",
        turnId="turn-governed-empty",
        taskId="task-governed-empty",
        objective="Compute 1+1 via the governed branch and reply with the result.",
        role="general",
        delivery="return",
    )


def _provider_config() -> object:
    from magi_agent.cli.providers import ProviderConfig

    return ProviderConfig(
        provider="anthropic", model="claude-sonnet-4-6", api_key="sk-test"
    )


class _EmptyStreamRunner:
    """Yields zero events — the silent-no-op shape Kevin saw on 0.1.74."""

    async def run_async(self, **kwargs: Any) -> AsyncGenerator[object, None]:
        return
        yield  # pragma: no cover — generator marker.


@pytest.mark.asyncio
async def test_governed_branch_empty_stream_surfaces_as_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The governed path must reuse PR #854's empty-response guard.

    We patch ``collect_governed_child_turn`` to simulate the empty-stream
    response shape the governed collector would return on a 100ms
    silent-empty provider call. The child runner MUST NOT silently project
    that as a successful empty turn.
    """
    import magi_agent.runtime.child_runner_live as crl

    async def _fake_governed_collector(_stream: object, **_kw: object) -> tuple[str, tuple[str, ...], str, str | None]:
        # (summary, evidence_refs, status) — empty/empty/"completed" is the
        # silent-no-op shape: governed runner said "all good, nothing came back".
        return "", (), "completed", None

    monkeypatch.setattr(
        "magi_agent.runtime.child_governed_collector.collect_governed_child_turn",
        _fake_governed_collector,
    )
    # build_headless_runtime + run_governed_turn pull heavy ADK imports; we
    # short-circuit them since the collector itself is what we are testing.
    monkeypatch.setattr(
        "magi_agent.cli.wiring.build_headless_runtime",
        lambda **_kw: object(),
    )
    monkeypatch.setattr(
        "magi_agent.runtime.governed_turn.run_governed_turn",
        lambda *_a, **_kw: object(),
    )

    child = crl.RealLocalChildRunner(
        provider_config=_provider_config(),
        runner=_EmptyStreamRunner(),
    )
    result = await child.run_child(_request())
    assert result["status"] == "failed", (
        "Governed branch with empty governed-collector result must surface as "
        f"status=failed (PR #854 parity). Got: {dict(result)}"
    )
    summary = str(result.get("summary", ""))
    assert "empty_response" in summary or "child_llm_empty" in summary, summary


@pytest.mark.asyncio
async def test_governed_non_completed_with_text_returns_best_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A governed child that ends non-completed but PRODUCED usable text must
    return that text as ``partialSummary`` (best-effort), while still surfacing
    the failure status + reason. Pre-fix the text was thrown away: a slow child
    that actually answered looked like a total failure to the parent.
    """
    import magi_agent.runtime.child_runner_live as crl

    async def _fake_governed_collector(_stream: object, **_kw: object) -> tuple[str, tuple[str, ...], str, str | None]:
        # Non-completed terminal (e.g. hit an internal cap) but the child DID
        # produce a real answer before the terminal.
        return "The answer is 2.", (), "failed", None

    monkeypatch.setattr(
        "magi_agent.runtime.child_governed_collector.collect_governed_child_turn",
        _fake_governed_collector,
    )
    monkeypatch.setattr(
        "magi_agent.cli.wiring.build_headless_runtime",
        lambda **_kw: object(),
    )
    monkeypatch.setattr(
        "magi_agent.runtime.governed_turn.run_governed_turn",
        lambda *_a, **_kw: object(),
    )

    child = crl.RealLocalChildRunner(
        provider_config=_provider_config(),
        runner=_EmptyStreamRunner(),
    )
    result = await child.run_child(_request())

    # Still a failure (no silent ship-as-completed) with the real reason...
    assert result["status"] == "failed", dict(result)
    assert "collector_status_failed" in str(result.get("summary", "")), dict(result)
    # ...but the child's actual answer is preserved on partialSummary.
    assert result.get("partialSummary") == "The answer is 2.", dict(result)


@pytest.mark.asyncio
async def test_governed_turn_timeout_returns_streamed_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A governed child that STREAMS real text then hangs past the turn budget
    must still return that text as ``partialSummary``. The #1458 best-effort
    seam is unreachable on the timeout path (``asyncio.wait_for`` CANCELS the
    collector coroutine before it builds a terminal, discarding its local
    accumulator); the runner's live partial accumulator recovers the work.
    """
    import asyncio

    import magi_agent.runtime.child_runner_live as crl

    async def _streaming_then_hangs_collector(
        _stream: object, *, partial_sink: "list[str] | None" = None, **_kw: object
    ) -> tuple[str, tuple[str, ...], str, str | None]:
        # Stream real text into the live accumulator (as the real collector
        # would per text_delta), then block past the turn budget so
        # ``asyncio.wait_for`` cancels this coroutine before it can return.
        if partial_sink is not None:
            partial_sink.append("Partial work: the plan is ")
            partial_sink.append("step 1, step 2, step 3.")
        await asyncio.sleep(3600)
        return "unreached", (), "completed", None  # pragma: no cover

    monkeypatch.setattr(
        "magi_agent.runtime.child_governed_collector.collect_governed_child_turn",
        _streaming_then_hangs_collector,
    )
    monkeypatch.setattr(
        "magi_agent.cli.wiring.build_headless_runtime",
        lambda **_kw: object(),
    )
    monkeypatch.setattr(
        "magi_agent.runtime.governed_turn.run_governed_turn",
        lambda *_a, **_kw: object(),
    )
    # Tight per-turn budget so the timeout fires immediately.
    monkeypatch.setenv("MAGI_CHILD_TURN_TIMEOUT_S", "0.05")

    child = crl.RealLocalChildRunner(
        provider_config=_provider_config(),
        runner=_EmptyStreamRunner(),
    )
    result = await child.run_child(_request())

    # Timed out -> failed with the timeout reason...
    assert result["status"] == "failed", dict(result)
    assert "child_turn_timeout" in str(result.get("summary", "")), dict(result)
    # ...but the streamed partial answer is preserved for the parent.
    assert (
        result.get("partialSummary")
        == "Partial work: the plan is step 1, step 2, step 3."
    ), dict(result)


@pytest.mark.asyncio
async def test_governed_turn_timeout_with_no_text_has_no_fabricated_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A child that streamed NOTHING before the timeout must return the plain
    timeout reason with NO ``partialSummary`` key (never fabricate an answer).
    """
    import asyncio

    import magi_agent.runtime.child_runner_live as crl

    async def _silent_then_hangs_collector(
        _stream: object, *, partial_sink: "list[str] | None" = None, **_kw: object
    ) -> tuple[str, tuple[str, ...], str, str | None]:
        # No text streamed at all, then hang past the budget.
        await asyncio.sleep(3600)
        return "unreached", (), "completed", None  # pragma: no cover

    monkeypatch.setattr(
        "magi_agent.runtime.child_governed_collector.collect_governed_child_turn",
        _silent_then_hangs_collector,
    )
    monkeypatch.setattr(
        "magi_agent.cli.wiring.build_headless_runtime",
        lambda **_kw: object(),
    )
    monkeypatch.setattr(
        "magi_agent.runtime.governed_turn.run_governed_turn",
        lambda *_a, **_kw: object(),
    )
    monkeypatch.setenv("MAGI_CHILD_TURN_TIMEOUT_S", "0.05")

    child = crl.RealLocalChildRunner(
        provider_config=_provider_config(),
        runner=_EmptyStreamRunner(),
    )
    result = await child.run_child(_request())

    assert result["status"] == "failed", dict(result)
    assert "child_turn_timeout" in str(result.get("summary", "")), dict(result)
    # No streamed text -> no partialSummary key (no fabrication).
    assert "partialSummary" not in result, dict(result)
