"""PR-3 (Containment hardening): the governed collector returns a
``(summary, evidence_refs, status)`` triple where ``status`` is the
collector's authoritative verdict on whether the child turn really
``completed`` or ``failed``. Pre-PR-3, ``_collect_turn_text_governed``
DROPPED that status signal: it bound the third tuple element to ``_status``
and never read it. The only protection against a silent ``("", (), "failed")``
slip-through was the AND-condition guard ``if not summary and not
evidence_refs`` two lines below the binding. That guard misses the bug shape
Kevin's 0.1.85 trace fingerprint surfaced:

  collector returns (summary="", evidence_refs=(18 refs), status="failed")

Both ``summary`` and ``evidence_refs`` exist (well, ``evidence_refs`` does),
so the AND-condition guard PASSES and the runner ships the failed turn as
``status="completed"`` with an empty answer + a pile of refs. The boundary's
``_envelope_from_output`` then projects ``status="completed"`` and the parent
agent sees a "successful" empty turn (the silent-empty bug class).

Fix (this PR): after the existing debug-log call but BEFORE the AND-condition
guard, honor the collector's failed signal by raising the same typed
``_ChildLlmTurnError`` the AND-guard raises. ``run_child`` already catches
that exception and routes to a ``status="failed"`` envelope with reason
``child_llm_collector_status_failed`` (the existing slug machinery just
prefixes ``child_llm_`` to whatever reason string we hand it).
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
    _ChildLlmTurnError,
    _DEGRADE_LLM_ERROR_PREFIX,
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
    # The bug only manifests on the governed branch.
    monkeypatch.setenv("MAGI_SUBAGENT_GOVERNED_TURN_ENABLED", "1")
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "absent.toml"))


def _request() -> ChildTaskRequest:
    return ChildTaskRequest(
        parentExecutionId="parent-exec-status-guard",
        turnId="turn-status-guard",
        taskId="task-status-guard",
        objective="Drive one governed child turn for the status-guard test.",
        role="general",
        delivery="return",
    )


def _provider_config() -> object:
    from magi_agent.cli.providers import ProviderConfig

    return ProviderConfig(provider="anthropic", model="claude-sonnet-4-6", api_key="sk-test")


class _EmptyStreamRunner:
    """Dummy ADK runner: never invoked because the collector is patched."""

    async def run_async(self, **kwargs: Any) -> AsyncGenerator[object, None]:
        return
        yield  # pragma: no cover


def _short_circuit_governed_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """``build_headless_runtime`` + ``run_governed_turn`` pull heavy ADK
    imports; the collector is the thing under test, so short-circuit them.
    """
    monkeypatch.setattr(
        "magi_agent.cli.wiring.build_headless_runtime",
        lambda **_kw: object(),
    )
    monkeypatch.setattr(
        "magi_agent.runtime.governed_turn.run_governed_turn",
        lambda *_a, **_kw: object(),
    )


@pytest.mark.asyncio
async def test_governed_guard_raises_on_failed_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Collector returns ``(summary="x", evidence_refs=(), "failed")``.

    The AND-condition guard would pass (summary is non-empty) but the
    status-aware guard added by this PR MUST raise.
    """

    async def _fake_collector(_stream: object) -> tuple[str, tuple[str, ...], str]:
        return "x", (), "failed"

    monkeypatch.setattr(
        "magi_agent.runtime.child_governed_collector.collect_governed_child_turn",
        _fake_collector,
    )
    _short_circuit_governed_runtime(monkeypatch)

    child = RealLocalChildRunner(
        provider_config=_provider_config(),
        runner=_EmptyStreamRunner(),
    )
    result = await child.run_child(_request())
    assert result["status"] == "failed", (
        f"Collector reported status=failed; runner must NOT project completed. Got: {dict(result)}"
    )
    summary = str(result.get("summary", ""))
    assert summary.startswith(_DEGRADE_LLM_ERROR_PREFIX), summary
    assert "collector_status_failed" in summary, summary


@pytest.mark.asyncio
async def test_governed_guard_passes_on_completed_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Collector returns ``(summary="x", evidence_refs=(), "completed")``.

    The status-aware guard MUST NOT fire (status is the good case)."""

    async def _fake_collector(_stream: object) -> tuple[str, tuple[str, ...], str]:
        return "x", (), "completed"

    monkeypatch.setattr(
        "magi_agent.runtime.child_governed_collector.collect_governed_child_turn",
        _fake_collector,
    )
    _short_circuit_governed_runtime(monkeypatch)

    child = RealLocalChildRunner(
        provider_config=_provider_config(),
        runner=_EmptyStreamRunner(),
    )
    result = await child.run_child(_request())
    assert result["status"] == "completed", dict(result)
    assert str(result["summary"]) == "x"


@pytest.mark.asyncio
async def test_governed_guard_raises_even_with_evidence_when_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Kevin's 0.1.85 repro shape: empty summary, 18 evidence refs,
    collector status=failed. The AND-condition guard PASSES (evidence_refs
    is non-empty) so without the status-aware guard the failed turn would
    silently ship as ``status="completed"``."""

    eighteen_refs = tuple(f"evidence:ref-{i:02d}" for i in range(18))

    async def _fake_collector(_stream: object) -> tuple[str, tuple[str, ...], str]:
        return "", eighteen_refs, "failed"

    monkeypatch.setattr(
        "magi_agent.runtime.child_governed_collector.collect_governed_child_turn",
        _fake_collector,
    )
    _short_circuit_governed_runtime(monkeypatch)

    child = RealLocalChildRunner(
        provider_config=_provider_config(),
        runner=_EmptyStreamRunner(),
    )
    result = await child.run_child(_request())
    assert result["status"] == "failed", (
        "Collector reported status=failed even with 18 evidence refs; the "
        "AND-condition guard alone would mask this as completed. "
        f"Got: {dict(result)}"
    )
    summary = str(result.get("summary", ""))
    assert "collector_status_failed" in summary, summary


def test_child_llm_turn_error_signature_is_stable() -> None:
    """Doc-grade contract test: the typed exception accepts a string reason
    and exposes it on ``.reason``. Keeps the wiring in
    ``_collect_turn_text_governed`` honest if the exception is ever
    refactored."""
    err = _ChildLlmTurnError("collector_status_failed")
    assert str(err) == "collector_status_failed"
    assert err.reason == "collector_status_failed"
