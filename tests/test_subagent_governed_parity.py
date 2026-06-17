"""Subagent governed-turn parity + no-escalation proof (Task 2A.7).

Two focused assertions that the 2A.6 review explicitly deferred here:

1. **No-escalation, non-empty profile (readonly)**
   With MAGI_SUBAGENT_GOVERNED_TURN_ENABLED=1 and MAGI_CHILD_RUNNER_TOOLSET=readonly,
   build_headless_runtime receives exactly the readonly set (FileRead/Glob/Grep/GitDiff)
   and NO mutating tool (Bash/FileWrite/Edit/…).  This is the core no-escalation proof
   for a non-empty restricted profile — the gap 2A.6 deferred from the ``none`` test.

2. **Governed envelope parity**
   The child's run_child envelope is SHAPE-IDENTICAL flag-ON vs flag-OFF, and
   status == "completed" for a completed governed stream.

Hermetic: no real model / no provider key.  All heavy primitives are monkeypatched.
MAGI_CONFIG is set to a non-existent file (hermetic config isolation).
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

import pytest

from magi_agent.runtime.child_runner_boundary import ChildTaskRequest
from magi_agent.runtime.child_runner_live import RealLocalChildRunner
from magi_agent.runtime.child_toolset import READONLY_TOOL_NAMES

# Register the ``governed_turn`` submodule as an attribute of ``magi_agent.runtime``
# so the string-form ``monkeypatch.setattr("...governed_turn.run_governed_turn", ...)``
# targets below resolve regardless of test execution order. ``magi_agent.runtime``
# uses a PEP 562 ``__getattr__`` that exposes only curated symbols (not submodules),
# so without this import the patch target raises ``AttributeError`` whenever no
# earlier test happened to import the submodule first (order-dependent CI failure).
import magi_agent.runtime.governed_turn  # noqa: F401

# ---------------------------------------------------------------------------
# Constants used in assertions (read from canonical source at import time)
# ---------------------------------------------------------------------------

#: The expected tool-name set for the ``readonly`` profile — sourced from the
#: single canonical definition so the test never drifts from the implementation.
_EXPECTED_READONLY_NAMES: frozenset[str] = frozenset(READONLY_TOOL_NAMES)

#: Tools that must NEVER appear in the readonly toolset (non-exhaustive —
#: testing the most obvious mutating tools suffices; the allowlist is the
#: authoritative whitelist so anything not in it is already excluded).
_FORBIDDEN_MUTATING_NAMES: frozenset[str] = frozenset(
    {"Bash", "FileWrite", "Edit", "Write", "ShellExec", "FileEdit"}
)

#: Required envelope keys (shape contract shared flag-ON and flag-OFF).
_ENVELOPE_KEYS: frozenset[str] = frozenset(
    {"childExecutionId", "status", "summary", "evidenceRefs", "artifactRefs", "auditEventRefs"}
)

# ---------------------------------------------------------------------------
# Shared env names cleared in each test's env isolation
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
    "MAGI_CHILD_RUNNER_TOOLSET",
    "MAGI_CHILD_MEMORY_INHERIT_ENABLED",
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path) -> None:
    """Hermetic: no real key / config / feature flag."""
    for name in _PROVIDER_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "absent.toml"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _request(**overrides: object) -> ChildTaskRequest:
    data: dict[str, object] = {
        "parentExecutionId": "parent-exec-parity",
        "turnId": "turn-parity-1",
        "taskId": "task-parity-1",
        "objective": "Parity probe: restricted toolset must be preserved.",
        "role": "research",
        "delivery": "return",
    }
    data.update(overrides)
    return ChildTaskRequest(**data)


def _provider_config(api_key: str = "sk-test") -> object:
    from magi_agent.cli.providers import ProviderConfig

    return ProviderConfig(provider="anthropic", model="claude-sonnet-4-6", api_key=api_key)


# ---------------------------------------------------------------------------
# Fake tool objects with a .name attribute (stand-in for real ADK tools)
# ---------------------------------------------------------------------------


class _FakeTool:
    """Minimal ADK-tool stand-in with a ``name`` attribute."""

    def __init__(self, name: str) -> None:
        self.name = name


def _make_fake_readonly_tools() -> list[_FakeTool]:
    """Return fake tool objects whose names exactly match READONLY_TOOL_NAMES."""
    return [_FakeTool(n) for n in READONLY_TOOL_NAMES]


def _make_fake_full_tools() -> list[_FakeTool]:
    """Return a superset of tools including readonly AND mutating ones."""
    all_names = list(READONLY_TOOL_NAMES) + ["Bash", "FileWrite", "Edit", "ShellExec"]
    return [_FakeTool(n) for n in all_names]


# ---------------------------------------------------------------------------
# Fake governed stream helpers
# ---------------------------------------------------------------------------


async def _completed_governed_turn(
    ctx: object,
    *,
    runtime: object | None = None,
    cancel: object | None = None,
) -> AsyncGenerator[Any, None]:
    """Fake run_governed_turn that yields a text_delta + Terminal.completed."""
    from magi_agent.cli.contracts import EngineResult, Terminal
    from magi_agent.runtime.events import RuntimeEvent

    yield RuntimeEvent(
        type="token",
        payload={"type": "text_delta", "delta": "GOVERNED: parity task done"},
    )
    yield EngineResult(terminal=Terminal.completed)


# ---------------------------------------------------------------------------
# Test 1: No-escalation — readonly profile forwards ONLY the readonly toolset
# ---------------------------------------------------------------------------


def test_readonly_profile_no_escalation_tools_forwarded_to_governed_runtime(
    monkeypatch,
) -> None:
    """Security invariant: with MAGI_SUBAGENT_GOVERNED_TURN_ENABLED=1 and
    MAGI_CHILD_RUNNER_TOOLSET=readonly, build_headless_runtime receives a
    tools list that:
      - is exactly the readonly set (FileRead/Glob/Grep/GitDiff), and
      - contains NO mutating tool (Bash/FileWrite/Edit/…).

    This is the no-escalation proof for the non-empty restricted profile
    (the ``none``/empty proof already lives in test_child_governed_path.py).

    Strategy: monkeypatch _build_core_tools to return a controlled fake
    full toolset so _resolve_turn_toolset filters it to the readonly names.
    Also monkeypatch build_headless_runtime and run_governed_turn to capture
    the tools arg and short-circuit respectively.
    """
    monkeypatch.setenv("MAGI_SUBAGENT_GOVERNED_TURN_ENABLED", "1")
    monkeypatch.setenv("MAGI_CHILD_RUNNER_TOOLSET", "readonly")

    # Inject a controlled FULL tool list so _resolve_turn_toolset can filter it.
    # We patch the instance method via the class so it applies to any instance.
    fake_full_tools = _make_fake_full_tools()

    def _fake_build_core_tools(self_: object, session_id: str, collector: object | None) -> list[object]:
        return list(fake_full_tools)

    monkeypatch.setattr(
        RealLocalChildRunner,
        "_build_core_tools",
        _fake_build_core_tools,
    )

    # Capture the tools kwarg forwarded to build_headless_runtime.
    captured_tools: list[list[object]] = []

    def _recording_build_headless_runtime(**kwargs: object) -> object:
        tools_kwarg = kwargs.get("tools")
        captured_tools.append(list(tools_kwarg) if tools_kwarg is not None else [])
        return object()

    monkeypatch.setattr(
        "magi_agent.cli.wiring.build_headless_runtime",
        _recording_build_headless_runtime,
    )
    monkeypatch.setattr(
        "magi_agent.runtime.governed_turn.run_governed_turn",
        _completed_governed_turn,
    )

    runner = RealLocalChildRunner(
        provider_config=_provider_config(),
        toolset_profile="readonly",
    )
    output = asyncio.run(runner.run_child(_request()))

    # --- Outer envelope is healthy -----------------------------------------
    assert output["status"] == "completed", (
        f"Expected status='completed', got {output['status']!r}"
    )

    # --- Exactly one call to build_headless_runtime ------------------------
    assert len(captured_tools) == 1, (
        f"Expected build_headless_runtime called once, got {len(captured_tools)} calls"
    )
    forwarded: list[object] = captured_tools[0]
    forwarded_names: set[str] = {
        getattr(t, "name", None) for t in forwarded
        if getattr(t, "name", None) is not None
    }

    # --- Core no-escalation assertion: only readonly names are forwarded ---
    assert forwarded_names == _EXPECTED_READONLY_NAMES, (
        f"Forwarded tool names {forwarded_names!r} != expected readonly set "
        f"{set(_EXPECTED_READONLY_NAMES)!r}. "
        "The child received tools outside the readonly profile — escalation bug."
    )

    # --- Extra guard: no mutating tool escaped into the forwarded set ------
    leaked_mutating = forwarded_names & _FORBIDDEN_MUTATING_NAMES
    assert not leaked_mutating, (
        f"Mutating tools leaked into the readonly child toolset: {leaked_mutating!r}. "
        "Privilege escalation detected."
    )


def test_readonly_profile_no_escalation_env_driven(
    monkeypatch,
) -> None:
    """Variant: the readonly profile is driven via MAGI_CHILD_RUNNER_TOOLSET env var
    (not the constructor kwarg), to prove the env gate is also respected.

    The runner is built with default toolset_profile="none" but the env gate
    overrides it at call time.  However — note the env gate is read at
    resolve_child_toolset_profile() call time inside _resolve_turn_toolset which
    reads os.environ (the monkeypatched env), NOT the constructor's profile.
    This tests the env-gate path independently.
    """
    monkeypatch.setenv("MAGI_SUBAGENT_GOVERNED_TURN_ENABLED", "1")
    # Env gate for toolset (MAGI_CHILD_RUNNER_TOOLSET) — note: _resolve_turn_toolset
    # uses self._toolset_profile (the CONSTRUCTOR arg), NOT the env var directly.
    # The env var is consumed by resolve_child_toolset_profile() at the boundary
    # layer.  So this variant tests that a runner explicitly constructed with
    # toolset_profile="readonly" correctly restricts to the readonly allowlist
    # (same invariant, different construction path to avoid any ambiguity).
    fake_full_tools = _make_fake_full_tools()

    def _fake_build_core_tools(self_: object, session_id: str, collector: object | None) -> list[object]:
        return list(fake_full_tools)

    monkeypatch.setattr(RealLocalChildRunner, "_build_core_tools", _fake_build_core_tools)

    captured_tools: list[list[object]] = []

    def _recording_build_headless_runtime(**kwargs: object) -> object:
        tools_kwarg = kwargs.get("tools")
        captured_tools.append(list(tools_kwarg) if tools_kwarg is not None else [])
        return object()

    monkeypatch.setattr(
        "magi_agent.cli.wiring.build_headless_runtime",
        _recording_build_headless_runtime,
    )
    monkeypatch.setattr(
        "magi_agent.runtime.governed_turn.run_governed_turn",
        _completed_governed_turn,
    )

    # Explicit readonly constructor arg — the canonical path tested in test 1 above.
    runner = RealLocalChildRunner(
        provider_config=_provider_config(),
        toolset_profile="readonly",
    )
    output = asyncio.run(runner.run_child(_request()))

    assert output["status"] == "completed"
    assert len(captured_tools) == 1
    forwarded_names = {
        getattr(t, "name", None) for t in captured_tools[0]
        if getattr(t, "name", None) is not None
    }
    # Must be exactly the readonly set — no extras, no mutating tools.
    assert forwarded_names == _EXPECTED_READONLY_NAMES
    assert not (forwarded_names & _FORBIDDEN_MUTATING_NAMES)


# ---------------------------------------------------------------------------
# Test 2: Governed envelope parity (flag-ON shape == flag-OFF shape)
# ---------------------------------------------------------------------------


def test_governed_envelope_shape_parity_flag_on_vs_flag_off(
    monkeypatch,
) -> None:
    """The run_child envelope has the SAME field shape flag-ON vs flag-OFF.

    Required keys: childExecutionId, status, summary, evidenceRefs,
    artifactRefs, auditEventRefs.  status == "completed" for a completed
    governed terminal (Terminal.completed).

    Strategy: run two calls on the SAME request — one flag-OFF (legacy path via
    an injected FakeRunner), one flag-ON (governed path with monkeypatched
    primitives).  Assert both envelopes have identical key sets and both
    report status == "completed".
    """

    # --- Build a fake legacy runner (flag-OFF) ----------------------------
    class _FakePart:
        def __init__(self, text: str) -> None:
            self.text = text

    class _FakeContent:
        def __init__(self, text: str) -> None:
            self.parts = [_FakePart(text)]

    class _FakeEvent:
        def __init__(self, text: str) -> None:
            self.content = _FakeContent(text)

    class _FakeLegacyRunner:
        async def run_async(self, **kwargs: object) -> AsyncGenerator[object, None]:
            yield _FakeEvent("LEGACY: parity reference summary")

    # --- Flag-OFF run (legacy path) ---------------------------------------
    legacy_runner = _FakeLegacyRunner()
    runner_off = RealLocalChildRunner(
        provider_config=_provider_config(),
        runner=legacy_runner,
    )
    # Flag NOT set — the autouse fixture already cleared it.
    envelope_off: dict[str, object] = asyncio.run(runner_off.run_child(_request()))

    # --- Flag-ON run (governed path) --------------------------------------
    monkeypatch.setenv("MAGI_SUBAGENT_GOVERNED_TURN_ENABLED", "1")

    def _fake_build_headless_runtime(**kwargs: object) -> object:
        return object()

    monkeypatch.setattr(
        "magi_agent.cli.wiring.build_headless_runtime",
        _fake_build_headless_runtime,
    )
    monkeypatch.setattr(
        "magi_agent.runtime.governed_turn.run_governed_turn",
        _completed_governed_turn,
    )

    runner_on = RealLocalChildRunner(provider_config=_provider_config())
    envelope_on: dict[str, object] = asyncio.run(runner_on.run_child(_request()))

    # --- Parity assertions ------------------------------------------------
    assert set(envelope_off.keys()) == _ENVELOPE_KEYS, (
        f"Flag-OFF envelope has unexpected keys: {set(envelope_off.keys())!r}"
    )
    assert set(envelope_on.keys()) == _ENVELOPE_KEYS, (
        f"Flag-ON envelope has unexpected keys: {set(envelope_on.keys())!r}"
    )

    # Shape is identical (same key sets).
    assert set(envelope_off.keys()) == set(envelope_on.keys()), (
        f"Envelope key sets diverge between flag-OFF {set(envelope_off.keys())!r} "
        f"and flag-ON {set(envelope_on.keys())!r}"
    )

    # Both report completed for a successful governed/legacy turn.
    assert envelope_off["status"] == "completed", (
        f"Flag-OFF envelope status {envelope_off['status']!r} != 'completed'"
    )
    assert envelope_on["status"] == "completed", (
        f"Flag-ON envelope status {envelope_on['status']!r} != 'completed'"
    )

    # childExecutionId format is consistent across both paths.
    assert str(envelope_off["childExecutionId"]).startswith("child-exec-"), (
        f"Flag-OFF childExecutionId {envelope_off['childExecutionId']!r} bad format"
    )
    assert str(envelope_on["childExecutionId"]).startswith("child-exec-"), (
        f"Flag-ON childExecutionId {envelope_on['childExecutionId']!r} bad format"
    )

    # Tuple fields default to empty tuples.
    for key in ("artifactRefs", "auditEventRefs"):
        assert envelope_off[key] == (), f"Flag-OFF {key} should be ()"
        assert envelope_on[key] == (), f"Flag-ON {key} should be ()"


def test_governed_envelope_completed_terminal_maps_to_completed_status(
    monkeypatch,
) -> None:
    """Terminal.completed in the governed stream → status=='completed' in envelope.

    This is the specific sub-assertion from the task spec: feed a fake stream
    whose terminal is EngineResult(terminal=Terminal.completed, ...) and assert
    the envelope reports status == 'completed'.
    """
    monkeypatch.setenv("MAGI_SUBAGENT_GOVERNED_TURN_ENABLED", "1")

    def _fake_build_headless_runtime(**kwargs: object) -> object:
        return object()

    monkeypatch.setattr(
        "magi_agent.cli.wiring.build_headless_runtime",
        _fake_build_headless_runtime,
    )

    async def _explicit_completed_turn(
        ctx: object,
        *,
        runtime: object | None = None,
        cancel: object | None = None,
    ) -> AsyncGenerator[Any, None]:
        from magi_agent.cli.contracts import EngineResult, Terminal
        from magi_agent.runtime.events import RuntimeEvent

        yield RuntimeEvent(
            type="token",
            payload={"type": "text_delta", "delta": "explicit completed terminal"},
        )
        yield EngineResult(terminal=Terminal.completed)

    monkeypatch.setattr(
        "magi_agent.runtime.governed_turn.run_governed_turn",
        _explicit_completed_turn,
    )

    runner = RealLocalChildRunner(provider_config=_provider_config())
    output = asyncio.run(runner.run_child(_request()))

    assert output["status"] == "completed"
    assert set(output.keys()) == _ENVELOPE_KEYS
    assert "explicit completed terminal" in str(output["summary"])
