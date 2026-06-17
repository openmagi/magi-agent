"""Tests for spawn_agent live-child-runner wiring (Task C).

Coverage:
- T1: Default (gate OFF) — honest not_attached payload (liveChildRunnerAttached=False).
- T2: Gate ON + injected fake-backed runner via monkeypatch — liveChildRunnerAttached=True.
- T3: Gate ON but child degrades (no key) — non-crashing blocked result, never raises.
- T4: tools=[] enforcement — runner constructed with empty toolset.
- T5: Import-boundary — importing subagents does NOT pull litellm/google.adk/child_runner_live.
"""

from __future__ import annotations

import asyncio
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
# T1: Default (gate OFF) — legacy local-fake payload with blocked tool status
# ---------------------------------------------------------------------------


def test_spawn_agent_default_gate_off_byte_identical(monkeypatch) -> None:
    """When is_live_child_runner_enabled() is False the payload is the honest
    not-attached receipt (07-PR2 D4 fix) while preserving every legacy key."""
    # Ensure the live gate is off (no env var set).
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)

    from magi_agent.plugins.native._common import digest
    from magi_agent.plugins.native.subagents import spawn_agent

    arguments: dict[str, object] = {"prompt": "Hello world", "persona": "researcher"}
    ctx = _context(spawnDepth=2)

    result = asyncio.run(spawn_agent(arguments, ctx))

    assert result.status == "blocked"
    assert result.error_code == "live_child_runner_disabled"
    output = result.output

    # Honest status — no longer a success-implying "queued_locally".
    assert output["status"] == "not_attached"
    assert output["reason"] == "live_child_runner_disabled"
    assert output["persona"] == "researcher"
    assert output["promptDigest"] == digest("Hello world")
    assert output["spawnDepth"] == 2
    assert output["liveChildRunnerAttached"] is False
    # Legacy keys preserved; reason/hint added by the honesty fix.
    assert set(output.keys()) == {
        "status",
        "reason",
        "hint",
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

    result = asyncio.run(spawn_agent({}, _context()))

    assert result.status == "blocked"
    assert result.error_code == "live_child_runner_disabled"
    assert result.output["status"] == "not_attached"
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

    result = asyncio.run(spawn_agent({"task": "do something"}, _context()))

    assert result.status == "blocked"
    assert result.error_code == "live_child_runner_disabled"
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

    result = asyncio.run(spawn_agent(arguments, ctx))

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


def test_spawn_agent_forwards_child_runner_progress_events(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", "1")
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)

    import magi_agent.runtime.child_runner_live as _live_mod

    class _ProgressRunner:
        openmagi_live_provider = True

        def __init__(self, **kwargs: object) -> None:
            self._progress_sink = kwargs.get("progress_sink")

        async def run_child(self, request: object) -> Mapping[str, object]:
            if callable(self._progress_sink):
                result = self._progress_sink(
                    {
                        "type": "child_progress",
                        "detail": "Child model streamed output chunk (12 chars)",
                    }
                )
                if hasattr(result, "__await__"):
                    await result
            return {
                "childExecutionId": "child-exec-progress",
                "status": "completed",
                "summary": "Fake child completed the subtask.",
                "evidenceRefs": (),
                "artifactRefs": (),
                "auditEventRefs": (),
            }

    monkeypatch.setattr(_live_mod, "RealLocalChildRunner", _ProgressRunner)

    from magi_agent.plugins.native.subagents import spawn_agent

    events: list[dict[str, object]] = []
    result = asyncio.run(
        spawn_agent(
            {"prompt": "Summarise the attached doc"},
            _context(
                toolUseId="call-spawn-progress",
                emitAgentEvent=lambda event: events.append(dict(event)),
            ),
        )
    )

    assert result.status == "ok"
    progress = [event for event in events if event.get("type") == "child_progress"]
    assert progress == [
        {
            "type": "child_progress",
            "taskId": "call-spawn-progress",
            "detail": "Running delegated child",
            "childReceiptRef": progress[0]["childReceiptRef"],
        },
        {
            "type": "child_progress",
            "taskId": "call-spawn-progress",
            "detail": "Child model streamed output chunk (12 chars)",
            "childReceiptRef": progress[0]["childReceiptRef"],
        },
    ]
    assert str(progress[0]["childReceiptRef"]).startswith("receipt:sha256:")


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

    result = asyncio.run(spawn_agent({"prompt": "do it"}, _context()))

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
    """If the live runner returns a blocked mapping, spawn_agent returns blocked."""
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
    result = asyncio.run(spawn_agent({"prompt": "do it"}, _context()))

    assert result.status == "blocked"
    assert result.error_code == "child_provider_key_missing"
    assert result.output is not None
    assert result.output["status"] == "blocked"
    assert result.output["summary"] == "child_provider_key_missing"
    assert result.output["liveChildRunnerAttached"] is True


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
    result = asyncio.run(spawn_agent({"prompt": "do it"}, _context()))

    assert result.status == "error"
    assert result.error_code == "live_child_runner_error"
    # The boundary catches the runner exception internally and returns status="error";
    # spawn_agent translates that to its output dict.  The output must be valid.
    assert result.output is not None
    assert result.output["status"] == "error"
    assert "persona" in result.output
    assert "promptDigest" in result.output
    assert "spawnDepth" in result.output


# ---------------------------------------------------------------------------
# T4: toolset gate — runner gets the gate-resolved profile, no caller escalation
# ---------------------------------------------------------------------------


class _CapturingProfileRunner:
    """Captures the construction kwargs the live path passes (PR1, doc 07)."""

    captured: dict[str, object] = {}
    openmagi_live_provider = True

    def __init__(
        self,
        *,
        tools: list[object] | None = None,
        toolset_profile: str = "none",
        workspace_root: str | None = None,
        **kwargs: object,
    ) -> None:
        type(self).captured = {
            "tools": list(tools) if tools is not None else None,
            "toolset_profile": toolset_profile,
            "workspace_root": workspace_root,
        }

    async def run_child(self, request: object) -> Mapping[str, object]:
        return {
            "childExecutionId": "child-exec-cap",
            "status": "completed",
            "summary": "done",
            "evidenceRefs": (),
            "artifactRefs": (),
            "auditEventRefs": (),
        }


def test_spawn_agent_default_toolset_gate_unset_is_none_profile(monkeypatch) -> None:
    """With MAGI_CHILD_RUNNER_TOOLSET unset the live runner is constructed with
    the text-only ``none`` profile (no caller ``tools`` escalation)."""
    monkeypatch.setenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", "1")
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)
    monkeypatch.delenv("MAGI_CHILD_RUNNER_TOOLSET", raising=False)

    import magi_agent.runtime.child_runner_live as _live_mod

    monkeypatch.setattr(_live_mod, "RealLocalChildRunner", _CapturingProfileRunner)

    from magi_agent.plugins.native.subagents import spawn_agent

    asyncio.run(spawn_agent({"prompt": "test", "tools": ["some_tool"]}, _context()))

    captured = _CapturingProfileRunner.captured
    # No caller-supplied tools escalation; default text-only profile.
    assert captured["toolset_profile"] == "none"
    assert captured["tools"] is None
    # ``none`` profile shares the parent cwd (no isolated workspace needed).
    assert captured["workspace_root"] is None


def test_spawn_agent_readonly_toolset_gate_uses_context_workspace_root(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """MAGI_CHILD_RUNNER_TOOLSET=readonly forwards the ``readonly`` profile and
    uses the caller workspace root. Hosted selected pods run with a read-only
    root filesystem, so relying on the process default tempdir prevents child
    runner attachment before the child even starts."""
    monkeypatch.setenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", "1")
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)
    monkeypatch.setenv("MAGI_CHILD_RUNNER_TOOLSET", "readonly")

    import magi_agent.runtime.child_runner_live as _live_mod

    monkeypatch.setattr(_live_mod, "RealLocalChildRunner", _CapturingProfileRunner)

    from magi_agent.plugins.native.subagents import spawn_agent

    asyncio.run(spawn_agent({"prompt": "review this"}, _context(workspaceRoot=str(tmp_path))))

    captured = _CapturingProfileRunner.captured
    assert captured["toolset_profile"] == "readonly"
    assert captured["workspace_root"] == str(tmp_path)


def test_spawn_agent_readonly_toolset_falls_back_to_hosted_workspace_env(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Hosted Gate5B selected chat sets the workspace root in the deployment
    environment. SpawnAgent must use that writable workspace instead of the
    process default tempdir, which is read-only in the production container."""
    monkeypatch.setenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", "1")
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)
    monkeypatch.setenv("MAGI_CHILD_RUNNER_TOOLSET", "readonly")
    monkeypatch.setenv(
        "CORE" + "_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_WORKSPACE_ROOT",
        str(tmp_path),
    )

    import magi_agent.runtime.child_runner_live as _live_mod

    monkeypatch.setattr(_live_mod, "RealLocalChildRunner", _CapturingProfileRunner)

    from magi_agent.plugins.native.subagents import spawn_agent

    asyncio.run(spawn_agent({"prompt": "review this"}, _context()))

    captured = _CapturingProfileRunner.captured
    assert captured["toolset_profile"] == "readonly"
    assert captured["workspace_root"] == str(tmp_path)


def test_spawn_agent_readonly_toolset_reports_workspace_unavailable(
    monkeypatch,
) -> None:
    """If neither context/env workspace nor default tempdir is writable, return
    a precise blocked reason instead of the generic attach-failed reason."""
    monkeypatch.setenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", "1")
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)
    monkeypatch.setenv("MAGI_CHILD_RUNNER_TOOLSET", "readonly")
    monkeypatch.delenv(
        "CORE" + "_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_WORKSPACE_ROOT",
        raising=False,
    )
    monkeypatch.delenv("MAGI_AGENT_WORKSPACE", raising=False)
    monkeypatch.delenv("MAGI_WORKSPACE_ROOT", raising=False)
    monkeypatch.delenv("MAGI_WORKSPACE", raising=False)

    import tempfile

    def _raise_no_tempdir(*args: object, **kwargs: object) -> str:
        raise FileNotFoundError("no usable temporary directory")

    monkeypatch.setattr(tempfile, "mkdtemp", _raise_no_tempdir)

    import magi_agent.runtime.child_runner_live as _live_mod

    monkeypatch.setattr(_live_mod, "RealLocalChildRunner", _CapturingProfileRunner)

    from magi_agent.plugins.native.subagents import spawn_agent

    result = asyncio.run(spawn_agent({"prompt": "review this"}, _context()))

    assert result.status == "blocked"
    assert result.error_code == "child_workspace_unavailable"
    assert result.output["status"] == "blocked"
    assert result.output["liveChildRunnerAttached"] is False


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


# ---------------------------------------------------------------------------
# T6: Kill-switch — ENABLED=1 + KILL_SWITCH=1 → blocked gate-OFF payload
# ---------------------------------------------------------------------------


def test_spawn_agent_kill_switch_overrides_enabled_returns_gate_off_payload(
    monkeypatch,
) -> None:
    """MAGI_CHILD_RUNNER_LIVE_ENABLED=1 AND MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH=1
    → spawn_agent must return the gate-OFF honest not-attached payload
    (liveChildRunnerAttached=False).
    """
    monkeypatch.setenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", "1")
    monkeypatch.setenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", "1")

    from magi_agent.plugins.native._common import digest
    from magi_agent.plugins.native.subagents import spawn_agent

    arguments: dict[str, object] = {"prompt": "Hello kill switch", "persona": "tester"}
    ctx = _context(spawnDepth=1)

    result = asyncio.run(spawn_agent(arguments, ctx))

    assert result.status == "blocked"
    assert result.error_code == "live_child_runner_disabled"
    output = result.output

    # Kill-switch routes through the gate-OFF branch → honest not-attached payload.
    assert output["status"] == "not_attached"
    assert output["reason"] == "live_child_runner_disabled"
    assert output["persona"] == "tester"
    assert output["promptDigest"] == digest("Hello kill switch")
    assert output["spawnDepth"] == 1
    assert output["liveChildRunnerAttached"] is False
    assert set(output.keys()) == {
        "status",
        "reason",
        "hint",
        "persona",
        "promptDigest",
        "spawnDepth",
        "liveChildRunnerAttached",
    }


# ---------------------------------------------------------------------------
# T7: Depth-cap boundary — spawn_depth=2 → child depth=3 > max_spawn_depth=2
# ---------------------------------------------------------------------------


def test_spawn_agent_live_depth_cap_blocks_without_running_child(monkeypatch) -> None:
    """A ToolContext with spawn_depth=2 passes child depth=3 to the boundary,
    which exceeds the default max_spawn_depth=2.  The fake runner must NOT be
    called and the result must be a non-crashing blocked/degraded payload.
    """
    monkeypatch.setenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", "1")
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)

    import magi_agent.runtime.child_runner_live as _live_mod

    runner_called = {"called": False}

    class _DepthCapFakeRunner:
        openmagi_live_provider = True

        def __init__(self, *, tools: list[object] | None = None, **kwargs: object) -> None:
            pass

        async def run_child(self, request: object) -> dict[str, object]:
            runner_called["called"] = True
            return {
                "childExecutionId": "child-exec-depth",
                "status": "completed",
                "summary": "should not reach here",
                "evidenceRefs": (),
                "artifactRefs": (),
                "auditEventRefs": (),
            }

    monkeypatch.setattr(_live_mod, "RealLocalChildRunner", _DepthCapFakeRunner)

    from magi_agent.plugins.native.subagents import spawn_agent

    # spawn_depth=2 → child metadata spawnDepth=3 > max_spawn_depth=2 (default)
    ctx = _context(spawnDepth=2)
    result = asyncio.run(spawn_agent({"prompt": "deep nested task"}, ctx))

    # The result must not raise and must be a valid blocked ToolResult.
    assert result.status == "blocked"
    assert result.error_code == "child_spawn_depth_exceeded"
    assert result.output is not None
    # The runner must NOT have been called (boundary blocked it at the depth cap).
    assert runner_called["called"] is False
    # The output status reflects a blocked/degraded condition.
    assert result.output.get("status") in {"blocked", "error", "disabled"}


# ---------------------------------------------------------------------------
# T8: String budget_ms — "5000" must produce budget_ms=5000 in the request
# ---------------------------------------------------------------------------


def test_spawn_agent_live_string_budget_ms_parsed_correctly(monkeypatch) -> None:
    """arguments={"budget_ms":"5000", ...} with the live gate on must not crash
    and must build a ChildTaskRequest with budgetMs=5000 (not 0).
    """
    monkeypatch.setenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", "1")
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)

    import magi_agent.runtime.child_runner_live as _live_mod

    captured_request: dict[str, object] = {}

    class _CaptureBudgetRunner:
        openmagi_live_provider = True

        def __init__(self, *, tools: list[object] | None = None, **kwargs: object) -> None:
            pass

        async def run_child(self, request: object) -> dict[str, object]:
            # Capture budget_ms from the request for assertion.
            # ChildTaskRequest.budget_ms is the Python attribute; budgetMs is the
            # alias used by the LLM-facing JSON schema.
            captured_request["budget_ms"] = getattr(request, "budget_ms", None)
            return {
                "childExecutionId": "child-exec-budget",
                "status": "completed",
                "summary": "budget parsed ok",
                "evidenceRefs": (),
                "artifactRefs": (),
                "auditEventRefs": (),
            }

    monkeypatch.setattr(_live_mod, "RealLocalChildRunner", _CaptureBudgetRunner)

    from magi_agent.plugins.native.subagents import spawn_agent

    # Pass budget_ms as a string (as an LLM tool call would).
    arguments: dict[str, object] = {
        "prompt": "budget string test",
        "budget_ms": "5000",
    }
    result = asyncio.run(spawn_agent(arguments, _context()))

    # Must not crash.
    assert result.status == "ok"
    assert result.output is not None
    # The request must have received budget_ms=5000, not 0.
    assert captured_request.get("budget_ms") == 5000


# ---------------------------------------------------------------------------
# T9: Running-loop dispatch — the child must actually run when spawn_agent is
# invoked the way the tool dispatcher invokes it (awaited on the live event
# loop), NOT degrade to blocked. Regression for the async fix: the prior
# sync + asyncio.run() implementation raised RuntimeError on the running loop
# and silently returned liveChildRunnerAttached=False for every production call.
# ---------------------------------------------------------------------------


def test_spawn_agent_runs_child_inside_running_event_loop(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", "1")
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)

    import magi_agent.runtime.child_runner_live as _live_mod

    monkeypatch.setattr(_live_mod, "RealLocalChildRunner", _FakeLiveChildRunner)

    from magi_agent.plugins.native.subagents import spawn_agent

    async def _dispatch_like_production() -> object:
        # Mirror the tool dispatcher's normal path: call the handler, then await
        # it if it returned a coroutine — all on the already-running loop.
        result = spawn_agent({"prompt": "run inside loop"}, _context(spawnDepth=1))
        if asyncio.iscoroutine(result):
            result = await result
        return result

    result = asyncio.run(_dispatch_like_production())

    assert result.status == "ok"
    # The child actually ran — NOT the blocked degrade path.
    assert result.output["liveChildRunnerAttached"] is True
    assert "Fake child" in str(result.output.get("summary", ""))


# ---------------------------------------------------------------------------
# T10: parent_tool_names producer (Task 2B.2) — ToolContext carrying
# parent_tool_names flows through spawn_agent into ChildTaskRequest.metadata
# ---------------------------------------------------------------------------


def test_spawn_agent_parent_tool_names_carried_to_child_request_gate_off(
    monkeypatch,
) -> None:
    """Gate OFF path: parent_tool_names on ToolContext lands in the output
    payload's spawnDepth field and does not crash.  The not-attached blocked
    result is byte-identical to before (no new output keys from the gate-OFF
    branch) — the producer only writes to the ChildTaskRequest metadata on the
    live path."""
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)

    from magi_agent.plugins.native.subagents import spawn_agent

    ctx = _context(spawnDepth=0, parentToolNames=("Bash", "FileRead"))
    result = asyncio.run(spawn_agent({"prompt": "gate-off parent-cap test"}, ctx))

    assert result.status == "blocked"
    assert result.error_code == "live_child_runner_disabled"
    # Gate-OFF output is byte-identical — no parentToolNames key in the output dict.
    assert "parentToolNames" not in result.output


def test_spawn_agent_parent_tool_names_carried_to_child_request_gate_on(
    monkeypatch,
) -> None:
    """Gate ON: ToolContext.parent_tool_names is forwarded into the ChildTaskRequest
    metadata as ``parentToolNames`` — mirroring how spawnDepth flows.

    Uses an object-form monkeypatch on the imported module object (not string-form
    setattr) to avoid the PEP 562 __getattr__ guard on magi_agent.runtime.
    """
    monkeypatch.setenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", "1")
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)

    import magi_agent.runtime.child_runner_live as _live_mod

    captured_request: list[object] = []

    class _CapturingParentNamesRunner:
        openmagi_live_provider = True

        def __init__(self, *, tools: list[object] | None = None, **kwargs: object) -> None:
            pass

        async def run_child(self, request: object) -> dict[str, object]:
            captured_request.append(request)
            return {
                "childExecutionId": "child-exec-parent-names",
                "status": "completed",
                "summary": "parent names captured",
                "evidenceRefs": (),
                "artifactRefs": (),
                "auditEventRefs": (),
            }

    # Patch the imported module object directly (avoids PEP 562 __getattr__ guard).
    monkeypatch.setattr(_live_mod, "RealLocalChildRunner", _CapturingParentNamesRunner)

    from magi_agent.plugins.native.subagents import spawn_agent

    ctx = _context(spawnDepth=0, parentToolNames=("FileRead", "Bash"))
    asyncio.run(spawn_agent({"prompt": "parent-tool-names test"}, ctx))

    assert len(captured_request) == 1
    req = captured_request[0]
    metadata = getattr(req, "metadata", {})
    assert "parentToolNames" in metadata
    # The value must match what was set on the ToolContext (order-preserving).
    assert tuple(metadata["parentToolNames"]) == ("FileRead", "Bash")


# ---------------------------------------------------------------------------
# T10c/T10d: build_cli_tool_runtime / build_cli_adk_tools path (Task 2B.2 fix)
# The third live tool-factory site must populate parent_tool_names on the
# ToolContext it produces.  These tests use object-form monkeypatch on the
# imported module objects to avoid PEP 562 __getattr__ guards.
# ---------------------------------------------------------------------------


def test_build_cli_tool_runtime_populates_parent_tool_names(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """T10c: build_cli_tool_runtime must close parent_tool_names over the
    tool_context_factory it returns — the snapshot is non-empty (core tools
    are always registered) and every name in it is a non-empty string.

    Uses object-form monkeypatch on magi_agent.cli.tool_runtime to intercept
    the ToolRegistry.list_available call without pulling google.adk.
    """
    import magi_agent.cli.tool_runtime as _rt_mod

    # Stub _build_first_party_adk_tools dependency only if present in scope.
    # The key seam is build_cli_tool_runtime itself — just call it and inspect.
    runtime = _rt_mod.build_cli_tool_runtime(workspace_root=str(tmp_path))

    # Call the factory with a plain object standing in for the ADK tool context.
    ctx = runtime.tool_context_factory(object())

    # parent_tool_names must be a non-empty tuple of strings (core tools always
    # registered) — the third factory is now populated like the other two sites.
    assert isinstance(ctx.parent_tool_names, tuple), (
        "parent_tool_names must be a tuple"
    )
    assert len(ctx.parent_tool_names) > 0, (
        "build_cli_tool_runtime must produce a non-empty parent_tool_names "
        "(core tools are always registered)"
    )
    for name in ctx.parent_tool_names:
        assert isinstance(name, str) and name, (
            f"every parent_tool_names entry must be a non-empty string; got {name!r}"
        )


def test_build_cli_tool_runtime_parent_tool_names_sorted(
    tmp_path: Path,
) -> None:
    """T10d: parent_tool_names snapshot must be sorted (mirrors wiring.py).

    The intersection in Task 2B.3 relies on stable ordering; a non-sorted
    tuple would not break correctness but would diverge from the contract
    established by the other two producer sites.
    """
    import magi_agent.cli.tool_runtime as _rt_mod

    runtime = _rt_mod.build_cli_tool_runtime(workspace_root=str(tmp_path))
    ctx = runtime.tool_context_factory(object())

    names = list(ctx.parent_tool_names)
    assert names == sorted(names), (
        "parent_tool_names must be sorted (mirrors wiring.py contract)"
    )


# ---------------------------------------------------------------------------
# T11: parentMemoryMode producer (Task F1) — ToolContext.memory_mode flows
# into ChildTaskRequest.metadata["parentMemoryMode"] as the .value string.
# ---------------------------------------------------------------------------


def test_spawn_agent_parent_memory_mode_carried_to_child_request_gate_on(
    monkeypatch,
) -> None:
    """Gate ON: ToolContext.memory_mode is forwarded into ChildTaskRequest.metadata
    as ``parentMemoryMode`` (the enum's .value string, e.g. ``"normal"``).

    Mirrors how ``parentToolNames``/``spawnDepth`` flow (Task 2B.2/T10).
    Uses object-form monkeypatch on the imported module object to avoid the
    PEP 562 __getattr__ guard on magi_agent.runtime.
    """
    monkeypatch.setenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", "1")
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)

    import magi_agent.runtime.child_runner_live as _live_mod

    captured_request: list[object] = []

    class _CapturingMemoryModeRunner:
        openmagi_live_provider = True

        def __init__(self, *, tools: list[object] | None = None, **kwargs: object) -> None:
            pass

        async def run_child(self, request: object) -> dict[str, object]:
            captured_request.append(request)
            return {
                "childExecutionId": "child-exec-memory-mode",
                "status": "completed",
                "summary": "memory mode captured",
                "evidenceRefs": (),
                "artifactRefs": (),
                "auditEventRefs": (),
            }

    # Patch the imported module object directly (avoids PEP 562 __getattr__ guard).
    monkeypatch.setattr(_live_mod, "RealLocalChildRunner", _CapturingMemoryModeRunner)

    from magi_agent.runtime.session_identity import MemoryMode
    from magi_agent.plugins.native.subagents import spawn_agent

    # ToolContext with memory_mode = NORMAL (the most important case to verify).
    ctx = _context(spawnDepth=0, memoryMode=MemoryMode.NORMAL)
    asyncio.run(spawn_agent({"prompt": "memory-mode producer test"}, ctx))

    assert len(captured_request) == 1
    req = captured_request[0]
    metadata = getattr(req, "metadata", {})
    assert "parentMemoryMode" in metadata, (
        f"expected 'parentMemoryMode' key in metadata; got keys: {list(metadata.keys())}"
    )
    # Must be the .value string ("normal"), NOT "MemoryMode.NORMAL"
    assert metadata["parentMemoryMode"] == "normal", (
        f"expected 'normal' (the .value), got {metadata['parentMemoryMode']!r}"
    )
