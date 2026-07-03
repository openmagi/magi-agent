"""Tests for Task 2B.3 + 2B.4: tighten-only child toolset = profile ∩ parent cap.

2B.3 (unit): _resolve_turn_toolset directly.
2B.4 (end-to-end): driven through the full governed run_child path.

When MAGI_SUBAGENT_TOOL_TIGHTEN_ONLY_ENABLED=1 AND parentToolNames is non-empty,
the child's toolset forwarded to build_headless_runtime is intersected with
parent_cap — so a broad-profile child of a restricted parent cannot escalate.
When the flag is OFF or parentToolNames is absent/empty, the full profile is
returned unchanged (byte-identical to pre-2B.3).

Hermetic: no real model / no provider key. Object-form monkeypatching for
magi_agent.runtime submodule attrs (avoids PEP 562 __getattr__ restriction).
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import pytest

import magi_agent.runtime.child_runner_live as child_runner_live_mod
import magi_agent.cli.tool_runtime as tool_runtime_mod
from magi_agent.runtime.child_runner_boundary import ChildTaskRequest
from magi_agent.runtime.child_runner_live import RealLocalChildRunner

# ---------------------------------------------------------------------------
# Env isolation
# ---------------------------------------------------------------------------

_PROVIDER_ENV = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "FIREWORKS_API_KEY",
    "MAGI_PROVIDER",
    "MAGI_MODEL",
    "MAGI_SUBAGENT_TOOL_TIGHTEN_ONLY_ENABLED",
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path) -> None:
    """Hermetic: no real key / config / tighten flag."""
    for name in _PROVIDER_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "absent.toml"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _request(**overrides: object) -> ChildTaskRequest:
    data: dict[str, object] = {
        "parentExecutionId": "parent-exec-tighten",
        "turnId": "turn-tighten-1",
        "taskId": "task-tighten-1",
        "objective": "Complete delegated subtask.",
        "role": "research",
        "delivery": "return",
    }
    data.update(overrides)
    return ChildTaskRequest(**data)


def _provider_config(api_key: str = "sk-test") -> object:
    from magi_agent.cli.providers import ProviderConfig

    return ProviderConfig(provider="anthropic", model="claude-sonnet-4-6", api_key=api_key)


class _NamedTool:
    def __init__(self, name: str) -> None:
        self.name = name


# A "full" profile toolset: mix of readonly + mutating tools
_FULL_TOOLS = [
    _NamedTool("FileRead"),
    _NamedTool("Glob"),
    _NamedTool("Grep"),
    _NamedTool("GitDiff"),
    _NamedTool("FileWrite"),
    _NamedTool("Bash"),
    _NamedTool("Edit"),
]

# Readonly subset: what the parent would pass as parentToolNames when it only has
# read-only tools itself.
_READONLY_NAMES = frozenset({"FileRead", "Glob", "Grep", "GitDiff"})


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
    async def run_async(self, **kwargs: object) -> AsyncGenerator[object, None]:
        yield _FakeEvent("ANSWER: tighten ran")


def _patch_full_tools(monkeypatch) -> None:
    """Patch build_cli_adk_tools to return the full mixed toolset."""
    def _fake_build_tools(**kwargs: object) -> list[_NamedTool]:
        return list(_FULL_TOOLS)

    monkeypatch.setattr(tool_runtime_mod, "build_cli_adk_tools", _fake_build_tools)


def _patch_build_runner(monkeypatch) -> None:
    """Patch build_cli_model_runner to return a fake runner (no network)."""
    import magi_agent.cli.real_runner as real_runner_mod

    monkeypatch.setattr(
        real_runner_mod,
        "build_cli_model_runner",
        lambda config, **kw: _FakeRunner(),
    )


# ---------------------------------------------------------------------------
# Test 1: Flag ON + non-empty parentToolNames → intersection only
# ---------------------------------------------------------------------------


def test_tighten_only_flag_on_returns_intersection(monkeypatch) -> None:
    """Flag ON + parentToolNames = readonly subset → only intersection returned
    (no mutating tools like Bash/FileWrite/Edit)."""
    _patch_full_tools(monkeypatch)
    _patch_build_runner(monkeypatch)
    monkeypatch.setenv("MAGI_SUBAGENT_TOOL_TIGHTEN_ONLY_ENABLED", "1")

    req = _request(metadata={"parentToolNames": tuple(_READONLY_NAMES)})
    runner = RealLocalChildRunner(
        provider_config=_provider_config(),
        toolset_profile="full",
        runner=_FakeRunner(),  # injected → we call _resolve_turn_toolset directly
    )

    tools, collector = runner._resolve_turn_toolset("session-tighten-1", request=req)
    tool_names = {t.name for t in tools}

    # Only readonly tools survive the intersection
    assert tool_names == _READONLY_NAMES
    # Mutating tools are excluded
    assert "FileWrite" not in tool_names
    assert "Bash" not in tool_names
    assert "Edit" not in tool_names


# ---------------------------------------------------------------------------
# Test 2: Flag OFF → full profile returned unchanged (even with parentToolNames)
# ---------------------------------------------------------------------------


def test_tighten_only_flag_off_returns_full_profile(monkeypatch) -> None:
    """Flag OFF → full profile returned unchanged even when parentToolNames is set."""
    _patch_full_tools(monkeypatch)
    monkeypatch.setenv("MAGI_SUBAGENT_TOOL_TIGHTEN_ONLY_ENABLED", "0")

    req = _request(metadata={"parentToolNames": tuple(_READONLY_NAMES)})
    runner = RealLocalChildRunner(
        provider_config=_provider_config(),
        toolset_profile="full",
        runner=_FakeRunner(),
    )

    tools, _collector = runner._resolve_turn_toolset("session-tighten-off", request=req)
    tool_names = {t.name for t in tools}

    # Full profile: all 7 tools returned unchanged
    assert tool_names == {t.name for t in _FULL_TOOLS}


# ---------------------------------------------------------------------------
# Test 3: Flag ON + empty parentToolNames → full profile unchanged (fail-open)
# ---------------------------------------------------------------------------


def test_tighten_only_flag_on_empty_parent_cap_returns_full_profile(monkeypatch) -> None:
    """Flag ON + empty parentToolNames → no narrowing (fail-open no-op)."""
    _patch_full_tools(monkeypatch)
    monkeypatch.setenv("MAGI_SUBAGENT_TOOL_TIGHTEN_ONLY_ENABLED", "1")

    req = _request(metadata={"parentToolNames": ()})
    runner = RealLocalChildRunner(
        provider_config=_provider_config(),
        toolset_profile="full",
        runner=_FakeRunner(),
    )

    tools, _collector = runner._resolve_turn_toolset("session-tighten-empty", request=req)
    tool_names = {t.name for t in tools}

    # Empty cap → no-op, all tools returned
    assert tool_names == {t.name for t in _FULL_TOOLS}


# ---------------------------------------------------------------------------
# Test 4: Flag ON + absent parentToolNames (no metadata key) → full profile
# ---------------------------------------------------------------------------


def test_tighten_only_flag_on_absent_parent_names_returns_full_profile(monkeypatch) -> None:
    """Flag ON + parentToolNames absent → no narrowing (fail-open no-op)."""
    _patch_full_tools(monkeypatch)
    monkeypatch.setenv("MAGI_SUBAGENT_TOOL_TIGHTEN_ONLY_ENABLED", "1")

    req = _request()  # no metadata
    runner = RealLocalChildRunner(
        provider_config=_provider_config(),
        toolset_profile="full",
        runner=_FakeRunner(),
    )

    tools, _collector = runner._resolve_turn_toolset("session-tighten-absent", request=req)
    tool_names = {t.name for t in tools}

    assert tool_names == {t.name for t in _FULL_TOOLS}


# ---------------------------------------------------------------------------
# Test 5: Flag ON + parentToolNames — readonly profile, intersects with cap
# ---------------------------------------------------------------------------


def test_tighten_only_readonly_profile_intersects_with_cap(monkeypatch) -> None:
    """Tighten-only with readonly profile + cap that is a sub-subset of readonly."""
    from magi_agent.runtime.child_toolset import READONLY_TOOL_NAMES

    # Simulate a parent that only has FileRead and Glob
    small_cap = frozenset({"FileRead", "Glob"})

    def _fake_build_tools(**kwargs: object) -> list[_NamedTool]:
        # readonly profile builds these tools
        return [_NamedTool(n) for n in READONLY_TOOL_NAMES]

    _patch_build_runner(monkeypatch)
    monkeypatch.setattr(tool_runtime_mod, "build_cli_adk_tools", _fake_build_tools)
    monkeypatch.setenv("MAGI_SUBAGENT_TOOL_TIGHTEN_ONLY_ENABLED", "1")

    req = _request(metadata={"parentToolNames": tuple(small_cap)})
    runner = RealLocalChildRunner(
        provider_config=_provider_config(),
        toolset_profile="readonly",
        runner=_FakeRunner(),
    )

    tools, _collector = runner._resolve_turn_toolset("session-tighten-ro", request=req)
    tool_names = {t.name for t in tools}

    # Only the intersection of readonly profile AND small_cap
    assert tool_names == small_cap
    assert "Grep" not in tool_names
    assert "GitDiff" not in tool_names


# ===========================================================================
# Task 2B.4 — END-TO-END tests (governed run_child path)
#
# Drive the FULL run_child → _drive_one_turn → _collect_turn_text →
# _collect_turn_text_governed chain with BOTH flags ON.  Capture what
# tools= is forwarded to build_headless_runtime and assert the security
# invariant: a full-profile child spawned by a readonly-only parent gets
# ONLY the intersection, never the broader profile.
#
# Seams:
#   1. RealLocalChildRunner._build_core_tools  → return controlled full toolset
#   2. magi_agent.cli.wiring.build_headless_runtime → capture tools kwarg
#   3. governed_turn_mod.run_governed_turn → short-circuit (no model)
# ===========================================================================

import magi_agent.runtime.governed_turn as governed_turn_mod  # noqa: E402 (after helpers)


async def _noop_governed_turn(
    ctx: object,
    *,
    runtime: object | None = None,
    cancel: object | None = None,
) -> "AsyncGenerator[object, None]":
    """Minimal fake governed turn: yield one text_delta + Terminal.completed."""
    from magi_agent.cli.contracts import EngineResult, Terminal
    from magi_agent.runtime.events import RuntimeEvent

    yield RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "E2E done"})
    yield EngineResult(terminal=Terminal.completed)


# ---------------------------------------------------------------------------
# Test 6: End-to-end no-escalation — full profile + readonly parent cap
# ---------------------------------------------------------------------------


def test_e2e_tighten_only_no_escalation_full_profile_restricted_parent(
    monkeypatch,
) -> None:
    """Security proof (end-to-end).

    Both MAGI_SUBAGENT_GOVERNED_TURN_ENABLED=1 and
    MAGI_SUBAGENT_TOOL_TIGHTEN_ONLY_ENABLED=1 are ON.

    The child's toolset_profile is "full" (would normally get Bash/FileWrite/Edit
    too), but parentToolNames is a readonly set.  The tools= forwarded to
    build_headless_runtime must equal the INTERSECTION: only the readonly names.

    This proves the end-to-end path: a broad-profile child of a restricted parent
    cannot escalate, even through the full governed run_child seam.
    """
    monkeypatch.setenv("MAGI_SUBAGENT_GOVERNED_TURN_ENABLED", "1")
    monkeypatch.setenv("MAGI_SUBAGENT_TOOL_TIGHTEN_ONLY_ENABLED", "1")

    # Inject a controlled FULL toolset so _resolve_turn_toolset has something to filter.
    def _fake_build_core_tools(
        self_: object, session_id: str, collector: object | None
    ) -> list[_NamedTool]:
        return list(_FULL_TOOLS)

    monkeypatch.setattr(RealLocalChildRunner, "_build_core_tools", _fake_build_core_tools)

    # Capture the tools= kwarg passed to build_headless_runtime.
    captured_tools: list[list[object]] = []

    def _recording_build_headless_runtime(**kwargs: object) -> object:
        raw = kwargs.get("tools")
        captured_tools.append(list(raw) if raw is not None else [])
        return object()

    monkeypatch.setattr(
        "magi_agent.cli.wiring.build_headless_runtime",
        _recording_build_headless_runtime,
    )
    monkeypatch.setattr(governed_turn_mod, "run_governed_turn", _noop_governed_turn)

    req = _request(metadata={"parentToolNames": tuple(_READONLY_NAMES)})
    runner = RealLocalChildRunner(
        provider_config=_provider_config(),
        toolset_profile="full",
    )
    output = asyncio.run(runner.run_child(req))

    # Governed turn completed successfully.
    assert output["status"] == "completed", (
        f"Expected status='completed', got {output['status']!r}"
    )

    # build_headless_runtime was called exactly once.
    assert len(captured_tools) == 1, (
        f"Expected build_headless_runtime called once, got {len(captured_tools)} calls"
    )

    forwarded_names = {getattr(t, "name", None) for t in captured_tools[0]}
    forwarded_names.discard(None)

    # Core assertion: only readonly names were forwarded (intersection with parent cap).
    assert forwarded_names == _READONLY_NAMES, (
        f"Forwarded {forwarded_names!r} != readonly cap {_READONLY_NAMES!r}. "
        "Full-profile child of restricted parent escalated — bug."
    )

    # Explicit zero-mutating-tool guard.
    assert "Bash" not in forwarded_names, "Bash escaped into restricted child toolset"
    assert "FileWrite" not in forwarded_names, "FileWrite escaped into restricted child toolset"
    assert "Edit" not in forwarded_names, "Edit escaped into restricted child toolset"


# ---------------------------------------------------------------------------
# Test 7: End-to-end no-op — tighten-only OFF, full profile forwarded
# ---------------------------------------------------------------------------


def test_e2e_tighten_only_flag_off_full_profile_forwarded(
    monkeypatch,
) -> None:
    """No-op guard (end-to-end): flag tighten-only OFF, governed ON.

    parentToolNames is the readonly set, but the tighten-only flag is OFF so
    no intersection is applied.  build_headless_runtime receives the full
    profile toolset unchanged.
    """
    monkeypatch.setenv("MAGI_SUBAGENT_GOVERNED_TURN_ENABLED", "1")
    # Tighten-only flag deliberately set OFF (explicit "0").
    monkeypatch.setenv("MAGI_SUBAGENT_TOOL_TIGHTEN_ONLY_ENABLED", "0")

    def _fake_build_core_tools(
        self_: object, session_id: str, collector: object | None
    ) -> list[_NamedTool]:
        return list(_FULL_TOOLS)

    monkeypatch.setattr(RealLocalChildRunner, "_build_core_tools", _fake_build_core_tools)

    captured_tools: list[list[object]] = []

    def _recording_build_headless_runtime(**kwargs: object) -> object:
        raw = kwargs.get("tools")
        captured_tools.append(list(raw) if raw is not None else [])
        return object()

    monkeypatch.setattr(
        "magi_agent.cli.wiring.build_headless_runtime",
        _recording_build_headless_runtime,
    )
    monkeypatch.setattr(governed_turn_mod, "run_governed_turn", _noop_governed_turn)

    req = _request(metadata={"parentToolNames": tuple(_READONLY_NAMES)})
    runner = RealLocalChildRunner(
        provider_config=_provider_config(),
        toolset_profile="full",
    )
    output = asyncio.run(runner.run_child(req))

    assert output["status"] == "completed"
    assert len(captured_tools) == 1

    forwarded_names = {getattr(t, "name", None) for t in captured_tools[0]}
    forwarded_names.discard(None)

    # Flag OFF → full profile, all 7 tools forwarded.
    assert forwarded_names == {t.name for t in _FULL_TOOLS}, (
        f"Flag-OFF should forward full profile; got {forwarded_names!r}"
    )


# ---------------------------------------------------------------------------
# Test 8: End-to-end no-op — tighten-only ON + empty parentToolNames
# ---------------------------------------------------------------------------


def test_e2e_tighten_only_flag_on_empty_parent_cap_full_profile_forwarded(
    monkeypatch,
) -> None:
    """No-op guard (end-to-end): flag ON but parentToolNames empty → fail-open.

    When parentToolNames is an empty tuple, no intersection is applied and the
    full profile reaches build_headless_runtime unchanged.
    """
    monkeypatch.setenv("MAGI_SUBAGENT_GOVERNED_TURN_ENABLED", "1")
    monkeypatch.setenv("MAGI_SUBAGENT_TOOL_TIGHTEN_ONLY_ENABLED", "1")

    def _fake_build_core_tools(
        self_: object, session_id: str, collector: object | None
    ) -> list[_NamedTool]:
        return list(_FULL_TOOLS)

    monkeypatch.setattr(RealLocalChildRunner, "_build_core_tools", _fake_build_core_tools)

    captured_tools: list[list[object]] = []

    def _recording_build_headless_runtime(**kwargs: object) -> object:
        raw = kwargs.get("tools")
        captured_tools.append(list(raw) if raw is not None else [])
        return object()

    monkeypatch.setattr(
        "magi_agent.cli.wiring.build_headless_runtime",
        _recording_build_headless_runtime,
    )
    monkeypatch.setattr(governed_turn_mod, "run_governed_turn", _noop_governed_turn)

    # Empty parentToolNames → no intersection applied.
    req = _request(metadata={"parentToolNames": ()})
    runner = RealLocalChildRunner(
        provider_config=_provider_config(),
        toolset_profile="full",
    )
    output = asyncio.run(runner.run_child(req))

    assert output["status"] == "completed"
    assert len(captured_tools) == 1

    forwarded_names = {getattr(t, "name", None) for t in captured_tools[0]}
    forwarded_names.discard(None)

    # Empty cap → no-op, full profile forwarded.
    assert forwarded_names == {t.name for t in _FULL_TOOLS}, (
        f"Empty parentToolNames should forward full profile; got {forwarded_names!r}"
    )


# ---------------------------------------------------------------------------
# Test 9: End-to-end no-op — tighten-only ON + absent parentToolNames
# ---------------------------------------------------------------------------


def test_e2e_tighten_only_flag_on_absent_parent_cap_full_profile_forwarded(
    monkeypatch,
) -> None:
    """No-op guard (end-to-end): flag ON, parentToolNames absent → fail-open.

    No metadata key at all → no intersection applied, full profile forwarded.
    """
    monkeypatch.setenv("MAGI_SUBAGENT_GOVERNED_TURN_ENABLED", "1")
    monkeypatch.setenv("MAGI_SUBAGENT_TOOL_TIGHTEN_ONLY_ENABLED", "1")

    def _fake_build_core_tools(
        self_: object, session_id: str, collector: object | None
    ) -> list[_NamedTool]:
        return list(_FULL_TOOLS)

    monkeypatch.setattr(RealLocalChildRunner, "_build_core_tools", _fake_build_core_tools)

    captured_tools: list[list[object]] = []

    def _recording_build_headless_runtime(**kwargs: object) -> object:
        raw = kwargs.get("tools")
        captured_tools.append(list(raw) if raw is not None else [])
        return object()

    monkeypatch.setattr(
        "magi_agent.cli.wiring.build_headless_runtime",
        _recording_build_headless_runtime,
    )
    monkeypatch.setattr(governed_turn_mod, "run_governed_turn", _noop_governed_turn)

    # No metadata at all → parentToolNames absent.
    req = _request()
    runner = RealLocalChildRunner(
        provider_config=_provider_config(),
        toolset_profile="full",
    )
    output = asyncio.run(runner.run_child(req))

    assert output["status"] == "completed"
    assert len(captured_tools) == 1

    forwarded_names = {getattr(t, "name", None) for t in captured_tools[0]}
    forwarded_names.discard(None)

    # Absent cap → no-op, full profile forwarded.
    assert forwarded_names == {t.name for t in _FULL_TOOLS}, (
        f"Absent parentToolNames should forward full profile; got {forwarded_names!r}"
    )
