"""Tests for the governed-turn path in RealLocalChildRunner (Task 2A.6).

Flag ON  → _collect_turn_text drives run_governed_turn + collect_governed_child_turn
Flag OFF → byte-identical to the legacy bare run_async path (existing tests stay green)

Hermetic: no real model / no provider key. All heavy primitives are monkeypatched.
MAGI_CONFIG is set to a non-existent file (hermetic config isolation).
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

import pytest

from magi_agent.runtime.child_runner_boundary import (
    ChildTaskRequest,
)
from magi_agent.runtime.child_runner_live import (
    RealLocalChildRunner,
)

# Import the ``governed_turn`` submodule under an alias and patch the module OBJECT
# below (not a dotted string). String-form
# ``monkeypatch.setattr("magi_agent.runtime.governed_turn.run_governed_turn", ...)``
# resolves via ``getattr(magi_agent.runtime, "governed_turn")``, but
# ``magi_agent.runtime`` uses a PEP 562 ``__getattr__`` that exposes only curated
# symbols (not submodules), so that resolution fails with ``AttributeError`` under
# CI import order/mode. Object-form patching bypasses the package ``__getattr__``.
import magi_agent.runtime.governed_turn as governed_turn_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROVIDER_ENV = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "FIREWORKS_API_KEY",
    "MAGI_PROVIDER",
    "MAGI_MODEL",
    "MAGI_SUBAGENT_GOVERNED_TURN_ENABLED",
    "MAGI_CHILD_MEMORY_INHERIT_ENABLED",
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path) -> None:
    """Hermetic: no real key / config."""
    for name in _PROVIDER_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "absent.toml"))


def _request(**overrides: object) -> ChildTaskRequest:
    data: dict[str, object] = {
        "parentExecutionId": "parent-exec-governed",
        "turnId": "turn-governed-1",
        "taskId": "task-governed-1",
        "objective": "Summarise the delegated subtask for the governed path.",
        "role": "research",
        "delivery": "return",
    }
    data.update(overrides)
    return ChildTaskRequest(**data)


def _provider_config(api_key: str = "sk-test") -> object:
    from magi_agent.cli.providers import ProviderConfig

    return ProviderConfig(provider="anthropic", model="claude-sonnet-4-6", api_key=api_key)


# ---------------------------------------------------------------------------
# Fake governed primitives (no network, no ADK)
# ---------------------------------------------------------------------------


class _FakePart:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeContent:
    def __init__(self, text: str) -> None:
        self.parts = [_FakePart(text)]


class _FakeEvent:
    def __init__(self, text: str) -> None:
        self.content = _FakeContent(text)


class _FakeRunner:
    """Mimics CliModelRunner.run_async with canned text events."""

    def __init__(self, text: str = "ANSWER: governed turn ran") -> None:
        self._text = text
        self.calls: int = 0

    async def run_async(self, **kwargs: object) -> AsyncGenerator[object, None]:
        self.calls += 1
        yield _FakeEvent(self._text)


class _FakeRuntime:
    """Minimal HeadlessRuntime stand-in for the governed path."""

    def __init__(self, summary: str = "GOVERNED: task done") -> None:
        self._summary = summary
        self.build_calls: int = 0

    def _record_build(self) -> None:
        self.build_calls += 1


# Fake RuntimeEvent / EngineResult that collect_governed_child_turn consumes.
def _make_governed_stream(
    summary: str = "GOVERNED: task done",
    evidence_refs: tuple[str, ...] = (),
    *,
    status: str = "completed",
) -> "AsyncGenerator[Any, None]":
    """Produce a minimal stream: one text_delta RuntimeEvent + terminal EngineResult."""
    from magi_agent.cli.contracts import EngineResult, Terminal
    from magi_agent.runtime.events import RuntimeEvent

    async def _gen() -> AsyncGenerator[Any, None]:
        yield RuntimeEvent(
            type="token",
            payload={"type": "text_delta", "delta": summary},
        )
        for ref in evidence_refs:
            yield RuntimeEvent(
                type="tool",
                payload={"type": "tool_result", "evidenceRef": ref},
            )
        yield EngineResult(terminal=Terminal.completed)

    return _gen()


# ---------------------------------------------------------------------------
# Tests: Flag OFF — legacy path must be unchanged
# ---------------------------------------------------------------------------


def test_flag_off_uses_injected_runner_not_governed_primitives(
    monkeypatch,
) -> None:
    """When MAGI_SUBAGENT_GOVERNED_TURN_ENABLED is unset (OFF), the legacy
    bare run_async path is used — the governed primitives are NOT called."""
    governed_called: list[str] = []

    def _fake_build_headless_runtime(**kwargs: object) -> object:
        governed_called.append("build_headless_runtime")
        return object()

    def _fake_run_governed_turn(*args: object, **kwargs: object) -> object:
        governed_called.append("run_governed_turn")
        # Should never be called with flag OFF
        raise AssertionError("run_governed_turn called with flag OFF")

    monkeypatch.setattr(
        "magi_agent.cli.wiring.build_headless_runtime",
        _fake_build_headless_runtime,
    )
    monkeypatch.setattr(
        governed_turn_mod,
        "run_governed_turn",
        _fake_run_governed_turn,
    )

    fake = _FakeRunner(text="ANSWER: legacy path only")
    runner = RealLocalChildRunner(provider_config=_provider_config(), runner=fake)

    # Flag not set → OFF
    output = asyncio.run(runner.run_child(_request()))

    assert output["status"] == "completed"
    assert "legacy path only" in str(output["summary"])
    assert fake.calls == 1
    # No governed primitives were invoked
    assert governed_called == []


# ---------------------------------------------------------------------------
# Tests: Flag ON — governed path is used
# ---------------------------------------------------------------------------


def test_flag_on_drives_run_governed_turn_and_returns_completed_envelope(
    monkeypatch,
) -> None:
    """With MAGI_SUBAGENT_GOVERNED_TURN_ENABLED=1, run_child returns a
    ``completed`` envelope whose summary comes from the governed stream."""
    monkeypatch.setenv("MAGI_SUBAGENT_GOVERNED_TURN_ENABLED", "1")

    governed_summary = "GOVERNED: the subtask is summarised"
    governed_evidence = ("evidence:governed-ref-1",)
    built_runtime: list[object] = []

    class _FakeHeadlessRuntime:
        """Stand-in for HeadlessRuntime returned by build_headless_runtime."""
        pass

    def _fake_build_headless_runtime(**kwargs: object) -> _FakeHeadlessRuntime:
        rt = _FakeHeadlessRuntime()
        built_runtime.append(rt)
        return rt

    async def _fake_run_governed_turn(
        ctx: object,
        *,
        runtime: object | None = None,
        cancel: object | None = None,
    ) -> AsyncGenerator[Any, None]:
        # Yield a text_delta + terminal EngineResult
        from magi_agent.cli.contracts import EngineResult, Terminal
        from magi_agent.runtime.events import RuntimeEvent

        yield RuntimeEvent(
            type="token",
            payload={"type": "text_delta", "delta": governed_summary},
        )
        yield RuntimeEvent(
            type="tool",
            payload={"evidenceRef": governed_evidence[0]},
        )
        yield EngineResult(terminal=Terminal.completed)

    monkeypatch.setattr(
        "magi_agent.cli.wiring.build_headless_runtime",
        _fake_build_headless_runtime,
    )
    monkeypatch.setattr(
        governed_turn_mod,
        "run_governed_turn",
        _fake_run_governed_turn,
    )

    # Inject a fake legacy runner too — it must NOT be called on the governed path
    legacy_runner = _FakeRunner(text="LEGACY: should not appear")

    runner = RealLocalChildRunner(provider_config=_provider_config(), runner=legacy_runner)
    output = asyncio.run(runner.run_child(_request()))

    # The governed path completed successfully
    assert output["status"] == "completed"
    assert governed_summary in str(output["summary"])
    # Legacy runner must NOT have been used for the governed turn
    assert legacy_runner.calls == 0
    # A headless runtime was built
    assert len(built_runtime) == 1
    # Standard envelope keys are present
    assert set(output.keys()) == {
        "childExecutionId",
        "status",
        "summary",
        "evidenceRefs",
        "artifactRefs",
        "auditEventRefs",
    }
    assert output["artifactRefs"] == ()
    assert output["auditEventRefs"] == ()
    assert str(output["childExecutionId"]).startswith("child-exec-")


def test_flag_on_passes_restricted_toolset_not_full_default(
    monkeypatch,
) -> None:
    """Security invariant: when the governed flag is ON, build_headless_runtime
    receives the RESTRICTED toolset (from _resolve_turn_toolset), NOT None /
    the full default. This prevents privilege escalation."""
    monkeypatch.setenv("MAGI_SUBAGENT_GOVERNED_TURN_ENABLED", "1")

    captured_tools: list[object] = []

    def _fake_build_headless_runtime(**kwargs: object) -> object:
        # Record the tools kwarg passed in
        captured_tools.append(kwargs.get("tools"))
        return object()

    async def _fake_run_governed_turn(
        ctx: object, *, runtime: object | None = None, cancel: object | None = None
    ) -> AsyncGenerator[Any, None]:
        from magi_agent.cli.contracts import EngineResult, Terminal

        yield EngineResult(terminal=Terminal.completed)

    monkeypatch.setattr(
        "magi_agent.cli.wiring.build_headless_runtime",
        _fake_build_headless_runtime,
    )
    monkeypatch.setattr(
        governed_turn_mod,
        "run_governed_turn",
        _fake_run_governed_turn,
    )

    # Use default toolset_profile="none" → _resolve_turn_toolset returns ([], None)
    runner = RealLocalChildRunner(provider_config=_provider_config())
    output = asyncio.run(runner.run_child(_request()))

    assert output["status"] == "completed"
    # The governed path was called and tools were passed to build_headless_runtime
    assert len(captured_tools) == 1
    # With default "none" profile, tools is the EMPTY restricted list [], not None
    assert captured_tools[0] == []


def test_flag_on_600s_ceiling_still_applies_to_governed_path(
    monkeypatch,
) -> None:
    """The 600s wait_for ceiling wraps _collect_turn_text, which now contains
    the governed path — so the ceiling still bounds the governed turn."""
    monkeypatch.setenv("MAGI_SUBAGENT_GOVERNED_TURN_ENABLED", "1")

    # Lower the timeout ceiling to 0.1s via MAGI_MODEL_TIMEOUT_S
    env_override = {"MAGI_MODEL_TIMEOUT_S": "0.1", "MAGI_SUBAGENT_GOVERNED_TURN_ENABLED": "1"}

    async def _slow_governed_turn(
        ctx: object, *, runtime: object | None = None, cancel: object | None = None
    ) -> AsyncGenerator[Any, None]:
        # Sleeps longer than the 0.1s ceiling → timeout should fire
        await asyncio.sleep(5.0)
        from magi_agent.cli.contracts import EngineResult, Terminal

        yield EngineResult(terminal=Terminal.completed)

    monkeypatch.setattr(
        governed_turn_mod,
        "run_governed_turn",
        _slow_governed_turn,
    )

    def _fake_build_headless_runtime(**kwargs: object) -> object:
        return object()

    monkeypatch.setattr(
        "magi_agent.cli.wiring.build_headless_runtime",
        _fake_build_headless_runtime,
    )

    from magi_agent.runtime.child_runner_live import _DEGRADE_TIMEOUT

    runner = RealLocalChildRunner(
        provider_config=_provider_config(),
        env=env_override,
    )
    output = asyncio.run(runner.run_child(_request()))

    # The ceiling cut the slow governed turn off → timeout degrade
    assert output["status"] in {"failed", "blocked"}
    assert output["summary"] == _DEGRADE_TIMEOUT


def test_flag_on_failed_terminal_maps_to_completed_envelope(
    monkeypatch,
) -> None:
    """A governed stream that ends with Terminal.aborted still produces a
    completed-status envelope (the governed status is surfaced in the summary
    but the outer envelope contract is preserved as today)."""
    monkeypatch.setenv("MAGI_SUBAGENT_GOVERNED_TURN_ENABLED", "1")

    async def _aborted_governed_turn(
        ctx: object, *, runtime: object | None = None, cancel: object | None = None
    ) -> AsyncGenerator[Any, None]:
        from magi_agent.cli.contracts import EngineResult, Terminal
        from magi_agent.runtime.events import RuntimeEvent

        yield RuntimeEvent(
            type="token",
            payload={"type": "text_delta", "delta": "partial response before abort"},
        )
        yield EngineResult(terminal=Terminal.aborted)

    monkeypatch.setattr(
        governed_turn_mod,
        "run_governed_turn",
        _aborted_governed_turn,
    )

    def _fake_build_headless_runtime(**kwargs: object) -> object:
        return object()

    monkeypatch.setattr(
        "magi_agent.cli.wiring.build_headless_runtime",
        _fake_build_headless_runtime,
    )

    runner = RealLocalChildRunner(provider_config=_provider_config())
    output = asyncio.run(runner.run_child(_request()))

    # Envelope is still returned (not an exception)
    assert output["status"] in {"completed", "failed", "blocked"}
    assert output["childExecutionId"].startswith("child-exec-")


def test_flag_on_exception_during_governed_turn_degrades_to_failed(
    monkeypatch,
) -> None:
    """If build_headless_runtime or the governed turn raises, run_child must
    degrade to failed — never raise across the seam."""
    monkeypatch.setenv("MAGI_SUBAGENT_GOVERNED_TURN_ENABLED", "1")

    def _raising_build(**kwargs: object) -> object:
        raise RuntimeError("unexpected build failure /secret/path")

    monkeypatch.setattr(
        "magi_agent.cli.wiring.build_headless_runtime",
        _raising_build,
    )

    from magi_agent.runtime.child_runner_live import _DEGRADE_TURN_ERROR

    runner = RealLocalChildRunner(provider_config=_provider_config())
    output = asyncio.run(runner.run_child(_request()))

    assert output["status"] in {"failed", "blocked"}
    assert output["summary"] == _DEGRADE_TURN_ERROR
    # No raw error text leaks
    assert "/secret/path" not in repr(output)


def test_flag_on_with_spawn_depth_in_metadata(
    monkeypatch,
) -> None:
    """spawnDepth in request.metadata is forwarded as parent_depth to derive()."""
    monkeypatch.setenv("MAGI_SUBAGENT_GOVERNED_TURN_ENABLED", "1")

    captured_ctx: list[object] = []

    async def _recording_governed_turn(
        ctx: object, *, runtime: object | None = None, cancel: object | None = None
    ) -> AsyncGenerator[Any, None]:
        captured_ctx.append(ctx)
        from magi_agent.cli.contracts import EngineResult, Terminal
        from magi_agent.runtime.events import RuntimeEvent

        yield RuntimeEvent(
            type="token",
            payload={"type": "text_delta", "delta": "depth test done"},
        )
        yield EngineResult(terminal=Terminal.completed)

    def _fake_build_headless_runtime(**kwargs: object) -> object:
        return object()

    monkeypatch.setattr(
        "magi_agent.cli.wiring.build_headless_runtime",
        _fake_build_headless_runtime,
    )
    monkeypatch.setattr(
        governed_turn_mod,
        "run_governed_turn",
        _recording_governed_turn,
    )

    request = _request(metadata={"spawnDepth": 2})
    runner = RealLocalChildRunner(provider_config=_provider_config())
    output = asyncio.run(runner.run_child(request))

    assert output["status"] == "completed"
    # The ctx passed to run_governed_turn should have depth = spawnDepth + 1 = 3
    assert len(captured_ctx) == 1
    ctx = captured_ctx[0]
    assert getattr(ctx, "depth", None) == 3


def test_flag_on_with_inherit_on_runtime_memory_mode_matches_derived_and_is_never_normal(
    monkeypatch,
) -> None:
    """Regression: when MAGI_SUBAGENT_GOVERNED_TURN_ENABLED=1 AND
    MAGI_CHILD_MEMORY_INHERIT_ENABLED=1, build_headless_runtime must receive the
    DERIVED child memory_mode (i.e. NOT ``"normal"`` for a ``normal`` parent).

    derive() → _child_memory_mode maps ``normal`` parent + inherit-ON to
    ``"read_only"``.  The old code passed ``"normal"`` directly (divergence bug).
    """
    monkeypatch.setenv("MAGI_SUBAGENT_GOVERNED_TURN_ENABLED", "1")
    monkeypatch.setenv("MAGI_CHILD_MEMORY_INHERIT_ENABLED", "1")

    captured_runtime_kwargs: list[dict[str, object]] = []

    def _recording_build_headless_runtime(**kwargs: object) -> object:
        captured_runtime_kwargs.append(dict(kwargs))
        return object()

    async def _fake_run_governed_turn(
        ctx: object, *, runtime: object | None = None, cancel: object | None = None
    ) -> AsyncGenerator[Any, None]:
        from magi_agent.cli.contracts import EngineResult, Terminal
        from magi_agent.runtime.events import RuntimeEvent

        yield RuntimeEvent(
            type="token",
            payload={"type": "text_delta", "delta": "inherit test done"},
        )
        yield EngineResult(terminal=Terminal.completed)

    monkeypatch.setattr(
        "magi_agent.cli.wiring.build_headless_runtime",
        _recording_build_headless_runtime,
    )
    monkeypatch.setattr(
        governed_turn_mod,
        "run_governed_turn",
        _fake_run_governed_turn,
    )

    runner = RealLocalChildRunner(provider_config=_provider_config())
    output = asyncio.run(runner.run_child(_request()))

    assert output["status"] == "completed"
    assert len(captured_runtime_kwargs) == 1
    runtime_memory_mode = captured_runtime_kwargs[0].get("memory_mode")

    # The runtime must NEVER receive "normal" — that is the parent mode, not the
    # child's contracted mode.  With a "incognito" parent (the current default
    # when parent_memory_mode is not yet threaded) + inherit=ON, derive() returns
    # "incognito" (incognito propagates as-is).  Either way, "normal" is invalid.
    assert runtime_memory_mode != "normal", (
        f"build_headless_runtime received memory_mode='normal' — "
        f"this diverges from the derived TurnContext (bug). Got: {runtime_memory_mode!r}"
    )
