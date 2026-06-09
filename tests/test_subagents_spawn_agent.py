"""Tests for spawn_agent live-child-runner wiring (Task C).

Coverage:
- T1: Default (gate OFF) — EXACT byte-identical payload (liveChildRunnerAttached=False).
- T2: Gate ON + injected fake-backed runner via monkeypatch — liveChildRunnerAttached=True.
- T3: Gate ON but child degrades (no key) — non-crashing blocked result, never raises.
- T4: tools=[] enforcement — runner constructed with empty toolset.
- T5: Import-boundary — importing subagents does NOT pull litellm/google.adk/child_runner_live.
"""
from __future__ import annotations

import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

from magi_agent.tools.context import ToolContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _context(**overrides: object) -> ToolContext:
    """Minimal ToolContext for tests."""
    defaults: dict[str, object] = {
        "botId": "test-bot",
        "sessionId": "sess-1",
        "turnId": "turn-1",
        "spawnDepth": 0,
    }
    defaults.update(overrides)
    return ToolContext(**defaults)


# ---------------------------------------------------------------------------
# T1: Default (gate OFF) — byte-identical to original local-fake payload
# ---------------------------------------------------------------------------


def test_spawn_agent_default_gate_off_byte_identical(monkeypatch) -> None:
    """When is_live_child_runner_enabled() is False the payload is EXACT to today's."""
    # Ensure the live gate is off (no env var set).
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)

    from magi_agent.plugins.native._common import digest
    from magi_agent.plugins.native.subagents import spawn_agent

    arguments: dict[str, object] = {"prompt": "Hello world", "persona": "researcher"}
    ctx = _context(spawnDepth=2)

    result = spawn_agent(arguments, ctx)

    assert result.status == "ok"
    output = result.output

    # Exact keys and values from the original implementation.
    assert output["status"] == "queued_locally"
    assert output["persona"] == "researcher"
    assert output["promptDigest"] == digest("Hello world")
    assert output["spawnDepth"] == 2
    assert output["liveChildRunnerAttached"] is False
    # No extra keys added by the live path.
    assert set(output.keys()) == {
        "status",
        "persona",
        "promptDigest",
        "spawnDepth",
        "liveChildRunnerAttached",
    }


def test_spawn_agent_default_gate_off_empty_prompt(monkeypatch) -> None:
    """Empty prompt falls back to empty string (original behaviour)."""
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)

    from magi_agent.plugins.native._common import digest
    from magi_agent.plugins.native.subagents import spawn_agent

    result = spawn_agent({}, _context())

    assert result.output["status"] == "queued_locally"
    assert result.output["persona"] == "general"
    assert result.output["promptDigest"] == digest("")
    assert result.output["spawnDepth"] == 0
    assert result.output["liveChildRunnerAttached"] is False


def test_spawn_agent_default_gate_off_uses_task_fallback(monkeypatch) -> None:
    """'task' key is the fallback when 'prompt' is absent."""
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)

    from magi_agent.plugins.native._common import digest
    from magi_agent.plugins.native.subagents import spawn_agent

    result = spawn_agent({"task": "do something"}, _context())

    assert result.output["promptDigest"] == digest("do something")
    assert result.output["liveChildRunnerAttached"] is False


# ---------------------------------------------------------------------------
# T2: Gate ON + injected fake-backed runner (no network)
# ---------------------------------------------------------------------------


class _FakeLiveChildRunner:
    """Mimics RealLocalChildRunner's interface (openmagi_live_provider=True).

    Returns a canned envelope-shaped mapping; does NOT hit any network/API.
    """

    openmagi_live_provider = True
    constructed_tools: list[object] | None = None

    def __init__(self, *, tools: list[object] | None = None, **kwargs: object) -> None:
        _FakeLiveChildRunner.constructed_tools = list(tools) if tools is not None else []

    async def run_child(self, request: object) -> Mapping[str, object]:
        return {
            "childExecutionId": "child-exec-fake-live",
            "status": "completed",
            "summary": "Fake child completed the subtask.",
            "evidenceRefs": (),
            "artifactRefs": (),
            "auditEventRefs": (),
        }


def test_spawn_agent_live_gate_on_returns_live_attached(monkeypatch) -> None:
    """Gate ON + fake-backed runner → liveChildRunnerAttached=True + status + summary."""
    monkeypatch.setenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", "1")
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)

    # Monkeypatch RealLocalChildRunner in the subagents module's import namespace.
    import magi_agent.runtime.child_runner_live as _live_mod

    monkeypatch.setattr(_live_mod, "RealLocalChildRunner", _FakeLiveChildRunner)

    from magi_agent.plugins.native.subagents import spawn_agent

    arguments: dict[str, object] = {"prompt": "Summarise the attached doc"}
    ctx = _context(spawnDepth=1)

    result = spawn_agent(arguments, ctx)

    assert result.status == "ok"
    output = result.output

    assert output["liveChildRunnerAttached"] is True
    assert output["persona"] == "general"
    assert output["spawnDepth"] == 1
    # status comes from the boundary result (ok, blocked, etc.)
    assert "status" in output
    # summary is the sanitised envelope summary (or "")
    assert "summary" in output
    # The summary from our fake runner should be visible (sanitised but not redacted).
    assert "Fake child" in str(output.get("summary", ""))


def test_spawn_agent_live_output_sanitised(monkeypatch) -> None:
    """Summary from the child is sanitised by the boundary (no raw path/secret leak)."""
    monkeypatch.setenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", "1")
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)

    import magi_agent.runtime.child_runner_live as _live_mod

    class _LeakyRunner:
        openmagi_live_provider = True

        def __init__(self, *, tools: list[object] | None = None, **kwargs: object) -> None:
            pass

        async def run_child(self, request: object) -> Mapping[str, object]:
            return {
                "childExecutionId": "child-exec-leaky",
                "status": "completed",
                # The boundary's _envelope_from_output sanitises these:
                "summary": (
                    "Done.\n"
                    "/Users/kevin/private/secret.txt\n"
                    "Authorization: Bearer sk-live-AAABBB\n"
                    "chain_of_thought: internal reasoning"
                ),
                "evidenceRefs": (),
                "artifactRefs": (),
                "auditEventRefs": (),
            }

    monkeypatch.setattr(_live_mod, "RealLocalChildRunner", _LeakyRunner)

    from magi_agent.plugins.native.subagents import spawn_agent

    result = spawn_agent({"prompt": "do it"}, _context())

    assert result.status == "ok"
    summary = str(result.output.get("summary", ""))
    # Paths and secrets must be redacted by the boundary before reaching output.
    assert "/Users/kevin" not in summary
    assert "sk-live-AAABBB" not in summary
    # 'chain_of_thought' is a private-line marker — the whole line must be stripped.
    assert "chain_of_thought" not in summary


# ---------------------------------------------------------------------------
# T3: Gate ON but child degrades (no key / blocked) — never raises
# ---------------------------------------------------------------------------


def test_spawn_agent_live_gate_on_child_blocked_does_not_raise(monkeypatch) -> None:
    """If the live runner returns a blocked mapping, spawn_agent returns ok/blocked."""
    monkeypatch.setenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", "1")
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)

    import magi_agent.runtime.child_runner_live as _live_mod

    class _BlockedRunner:
        openmagi_live_provider = True

        def __init__(self, *, tools: list[object] | None = None, **kwargs: object) -> None:
            pass

        async def run_child(self, request: object) -> Mapping[str, object]:
            # Mimic no-key degrade: runner returns blocked mapping.
            return {
                "childExecutionId": "child-exec-blocked",
                "status": "blocked",
                "summary": "child_provider_key_missing",
                "evidenceRefs": (),
                "artifactRefs": (),
                "auditEventRefs": (),
            }

    monkeypatch.setattr(_live_mod, "RealLocalChildRunner", _BlockedRunner)

    from magi_agent.plugins.native.subagents import spawn_agent

    # Must NOT raise.
    result = spawn_agent({"prompt": "do it"}, _context())

    assert result.status == "ok"
    # The boundary returns ok-level result with blocked envelope, or a fallback.
    assert result.output is not None
    assert "status" in result.output


def test_spawn_agent_live_gate_on_runner_raises_falls_back(monkeypatch) -> None:
    """If the live runner raises, spawn_agent returns a non-crashing result, never raises.

    When the runner raises, the boundary catches the exception internally (its
    _run_live_child method is try/except'd) and returns a status="error" result.
    The boundary does NOT re-raise, so spawn_agent still returns from the live
    path with liveChildRunnerAttached=True (the live path was entered and completed
    non-exceptionally from spawn_agent's perspective).  If the OUTER try/except in
    spawn_agent is triggered (e.g. construction error), the fallback fires instead.
    Either way: no exception escapes spawn_agent.
    """
    monkeypatch.setenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", "1")
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)

    import magi_agent.runtime.child_runner_live as _live_mod

    class _RaisingRunner:
        openmagi_live_provider = True

        def __init__(self, *, tools: list[object] | None = None, **kwargs: object) -> None:
            pass

        async def run_child(self, request: object) -> Mapping[str, object]:
            raise RuntimeError("unexpected /Users/kevin/secret sk-live-ZZZZ")

    monkeypatch.setattr(_live_mod, "RealLocalChildRunner", _RaisingRunner)

    from magi_agent.plugins.native.subagents import spawn_agent

    # Must NOT raise.
    result = spawn_agent({"prompt": "do it"}, _context())

    assert result.status == "ok"
    # The boundary catches the runner exception internally and returns status="error";
    # spawn_agent translates that to its output dict.  The output must be valid.
    assert result.output is not None
    assert "status" in result.output
    assert "persona" in result.output
    assert "promptDigest" in result.output
    assert "spawnDepth" in result.output


# ---------------------------------------------------------------------------
# T4: tools=[] enforcement — runner is always constructed with empty toolset
# ---------------------------------------------------------------------------


def test_spawn_agent_live_runner_constructed_with_empty_tools(monkeypatch) -> None:
    """RealLocalChildRunner is ALWAYS constructed with tools=[] (v1 text-only)."""
    monkeypatch.setenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", "1")
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)

    import magi_agent.runtime.child_runner_live as _live_mod

    captured: dict[str, object] = {}

    class _CapturingRunner:
        openmagi_live_provider = True

        def __init__(self, *, tools: list[object] | None = None, **kwargs: object) -> None:
            captured["tools"] = list(tools) if tools is not None else []

        async def run_child(self, request: object) -> Mapping[str, object]:
            return {
                "childExecutionId": "child-exec-cap",
                "status": "completed",
                "summary": "done",
                "evidenceRefs": (),
                "artifactRefs": (),
                "auditEventRefs": (),
            }

    monkeypatch.setattr(_live_mod, "RealLocalChildRunner", _CapturingRunner)

    from magi_agent.plugins.native.subagents import spawn_agent

    spawn_agent({"prompt": "test", "tools": ["some_tool"]}, _context())

    # The runner must have been constructed with an EMPTY toolset regardless of
    # what arguments were passed to spawn_agent.
    assert captured.get("tools") == []


# ---------------------------------------------------------------------------
# T5: Import-boundary — importing subagents does NOT pull heavy modules
# ---------------------------------------------------------------------------


def test_subagents_import_does_not_pull_heavy_modules() -> None:
    """Importing magi_agent.plugins.native.subagents must NOT load litellm/google.adk/child_runner_live."""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("magi_agent.plugins.native.subagents")

forbidden_prefixes = (
    "litellm",
    "google.adk",
    "google.adk.runners",
    "google.adk.models.lite_llm",
    "magi_agent.runtime.child_runner_live",
)
loaded = [
    module_name
    for module_name in sys.modules
    if any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in forbidden_prefixes
    )
]
if loaded:
    raise AssertionError(f"subagents import loaded forbidden modules: {loaded}")
""",
        ],
        check=False,
        cwd=Path(__file__).parents[1],
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
