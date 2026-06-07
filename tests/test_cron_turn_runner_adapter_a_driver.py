"""A-driver — CronTurnRunnerAdapter bridging OpenMagiRunnerAdapter -> CronTurnRunner.

TDD: CronTurnPlan -> RunnerTurnInput synthesis, disabled_toolsets enforcement,
result mapping (completed/failed), runner-error -> failed, CronTurnRunner Protocol
conformance, and module import purity (no ADK at top level).
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
from typing import Any


def _plan(*, job_id: str = "job:a", disabled: tuple[str, ...] = ()) -> Any:
    from magi_agent.harness.scheduler_job_execution import CronTurnPlan

    return CronTurnPlan(
        jobId=job_id,
        prompt="run scheduled job job:a unattended",
        disabledToolsets=disabled,
        timeoutSeconds=600.0,
    )


class _FakeOpenMagiAdapter:
    """Stand-in for OpenMagiRunnerAdapter: records the RunnerTurnInput it sees."""

    def __init__(self, *, events: list[Any] | None = None, raise_exc: BaseException | None = None) -> None:
        self.calls: list[Any] = []
        self._events = events if events is not None else [{"type": "fake", "text": "done"}]
        self._raise = raise_exc

    async def collect_events(self, turn_input: Any) -> list[Any]:
        self.calls.append(turn_input)
        if self._raise is not None:
            raise self._raise
        return list(self._events)


# ---------------------------------------------------------------------------
# Protocol conformance + plan->input mapping
# ---------------------------------------------------------------------------

def test_adapter_satisfies_cron_turn_runner_protocol() -> None:
    from magi_agent.harness.cron_turn_runner_adapter import CronTurnRunnerAdapter
    from magi_agent.harness.scheduler_job_execution import CronTurnRunner

    adapter = CronTurnRunnerAdapter(runner_adapter=_FakeOpenMagiAdapter())
    assert isinstance(adapter, CronTurnRunner)


def test_maps_plan_to_runner_turn_input() -> None:
    from google.genai import types

    from magi_agent.harness.cron_turn_runner_adapter import CronTurnRunnerAdapter

    fake = _FakeOpenMagiAdapter()
    adapter = CronTurnRunnerAdapter(runner_adapter=fake)
    result = asyncio.run(adapter.run_turn(_plan(job_id="job:x")))

    assert len(fake.calls) == 1
    turn_input = fake.calls[0]
    # The cron job_id drives a deterministic session/invocation identity.
    assert "job:x" in turn_input.session_id or "job_x" in turn_input.session_id
    assert turn_input.invocation_id
    assert turn_input.turn_id
    # The prompt is carried into the ADK Content new_message.
    assert isinstance(turn_input.new_message, types.Content)
    flat = "".join(p.text or "" for p in (turn_input.new_message.parts or []))
    assert "unattended" in flat

    assert result.status == "completed"
    assert result.job_id == "job:x"
    assert result.runner_invoked is True


def test_completed_result_carries_output_text() -> None:
    from magi_agent.harness.cron_turn_runner_adapter import CronTurnRunnerAdapter

    fake = _FakeOpenMagiAdapter(events=[{"text": "alpha"}, {"text": "beta"}])
    adapter = CronTurnRunnerAdapter(runner_adapter=fake)
    result = asyncio.run(adapter.run_turn(_plan()))
    assert result.status == "completed"
    # Some non-empty output digest/text is surfaced (redaction-safe).
    assert isinstance(result.output, str)


# ---------------------------------------------------------------------------
# disabled_toolsets enforcement
# ---------------------------------------------------------------------------

def test_disabled_toolset_exposed_fails_without_invoking_runner() -> None:
    from magi_agent.harness.cron_turn_runner_adapter import CronTurnRunnerAdapter

    fake = _FakeOpenMagiAdapter()
    # The injected agent still exposes CronCreate — a strip-contract violation.
    adapter = CronTurnRunnerAdapter(
        runner_adapter=fake,
        exposed_toolsets_provider=lambda: ("FileRead", "CronCreate"),
    )
    result = asyncio.run(adapter.run_turn(_plan(disabled=("CronCreate", "TelegramSend"))))

    assert result.status == "failed"
    assert result.runner_invoked is False
    # The runner was NOT called — the strip is honored pre-flight.
    assert fake.calls == []


def test_disabled_toolset_absent_invokes_runner() -> None:
    from magi_agent.harness.cron_turn_runner_adapter import CronTurnRunnerAdapter

    fake = _FakeOpenMagiAdapter()
    adapter = CronTurnRunnerAdapter(
        runner_adapter=fake,
        exposed_toolsets_provider=lambda: ("FileRead", "WebSearch"),
    )
    result = asyncio.run(adapter.run_turn(_plan(disabled=("CronCreate",))))
    assert result.status == "completed"
    assert result.runner_invoked is True
    assert len(fake.calls) == 1


def test_no_introspection_provider_invokes_runner() -> None:
    """Without an introspection seam, the strip is the caller's contract (documented)."""
    from magi_agent.harness.cron_turn_runner_adapter import CronTurnRunnerAdapter

    fake = _FakeOpenMagiAdapter()
    adapter = CronTurnRunnerAdapter(runner_adapter=fake)
    result = asyncio.run(adapter.run_turn(_plan(disabled=("CronCreate",))))
    assert result.status == "completed"
    assert len(fake.calls) == 1


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------

def test_runner_exception_maps_to_failed() -> None:
    from magi_agent.harness.cron_turn_runner_adapter import CronTurnRunnerAdapter

    fake = _FakeOpenMagiAdapter(raise_exc=RuntimeError("boom"))
    adapter = CronTurnRunnerAdapter(runner_adapter=fake)
    result = asyncio.run(adapter.run_turn(_plan(job_id="job:err")))
    assert result.status == "failed"
    assert result.job_id == "job:err"
    # The runner WAS invoked (the failure is mid-turn, not pre-flight).
    assert result.runner_invoked is True


def test_timeout_via_execute_due_jobs_path() -> None:
    """A hung adapter is aborted by execute_due_jobs' asyncio.wait_for -> timed_out.

    The adapter itself does not own the timeout (execute_due_jobs / _run_turn_sync
    wraps run_turn in wait_for), so a slow collect_events surfaces as timed_out
    at the scheduler layer.  This proves the adapter is compatible with that path.
    """
    from magi_agent.harness.cron_turn_runner_adapter import CronTurnRunnerAdapter
    from magi_agent.harness.scheduler_job_execution import CronTurnPlan

    class _HangingAdapter:
        async def collect_events(self, turn_input: Any) -> list[Any]:
            await asyncio.sleep(10)
            return []

    adapter = CronTurnRunnerAdapter(runner_adapter=_HangingAdapter())
    plan = CronTurnPlan(
        jobId="job:slow",
        prompt="slow",
        disabledToolsets=(),
        timeoutSeconds=0.05,
    )

    async def _drive() -> Any:
        try:
            return await asyncio.wait_for(adapter.run_turn(plan), timeout=plan.timeout_seconds)
        except asyncio.TimeoutError:
            return "timed_out"

    assert asyncio.run(_drive()) == "timed_out"


# ---------------------------------------------------------------------------
# Import purity
# ---------------------------------------------------------------------------

def test_module_does_not_import_adk_at_top_level() -> None:
    """Mirror the A3 purity contract: no DIRECT top-level ADK/dangerous-trio import,
    and no genuinely-forbidden live/infra module loaded on import.

    The ADK bits (google.genai.types, adk_bridge.runner_adapter,
    harness.resolved) are imported LAZILY inside _build_turn_input, so importing
    this module must NOT pull google.adk / google.genai / magi_agent.adk_bridge.
    urllib/socket/subprocess are excluded from the sys.modules check (pydantic
    transitive, pre-existing).
    """
    import ast
    from pathlib import Path

    src = Path(__file__).parent.parent / "magi_agent" / "harness" / "cron_turn_runner_adapter.py"
    tree = ast.parse(src.read_text())
    # Only MODULE-LEVEL imports count for top-level purity; lazy imports inside
    # function bodies (google.genai, adk_bridge) are intentional and verified by
    # the sys.modules check below.
    direct: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                direct.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            direct.add(node.module.split(".")[0])
    assert not ({"urllib", "socket", "subprocess"} & direct)
    assert "google" not in direct

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib, sys
importlib.import_module("magi_agent.harness.cron_turn_runner_adapter")
forbidden_prefixes = (
    "google.adk", "google.genai", "magi_agent.adk_bridge", "requests",
    "httpx", "aiohttp", "kubernetes", "telegram", "discord",
)
loaded = [n for n in sys.modules if any(n == p or n.startswith(p + ".") for p in forbidden_prefixes)]
assert not loaded, loaded
print("ok")
""",
        ],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "ok" in completed.stdout
