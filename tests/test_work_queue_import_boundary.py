"""Import-boundary guard: magi_agent.missions.work_queue.store must not pull in
ADK, dispatcher, transport, or agent-spawn modules at import time.

Pattern mirrors test_goal_state_b1.test_goal_state_import_boundary_does_not_load_adk_or_network_modules
and test_resolved_harness_import_stays_runner_route_and_dispatcher_free in
test_evidence_harness_boundary.py — use subprocess to run a fresh interpreter
and check sys.modules after the import.

Note: stdlib modules (socket, subprocess, urllib) are transitively loaded by
pydantic and are excluded from this check, consistent with the repo-wide pattern
(see goal_state and gate3a boundary tests).  The guard here focuses on ADK and
magi_agent-level seams that must never be imported by pure-state modules.
"""
from __future__ import annotations

import subprocess
import sys


def _run_fresh_python(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )


def test_work_queue_store_import_does_not_pull_in_forbidden_modules() -> None:
    """Importing store.py must not transitively load ADK, dispatcher, or transport modules."""
    completed = _run_fresh_python(
        """
import importlib
import sys

importlib.import_module("magi_agent.missions.work_queue.store")

forbidden_prefixes = ("google.adk",)
forbidden_modules = (
    "magi_agent.adk_bridge.runner_adapter",
    "magi_agent.adk_bridge.tool_adapter",
    "magi_agent.transport.chat",
    "magi_agent.transport.tools",
    "magi_agent.tools.dispatcher",
    "magi_agent.hooks.bus",
    "requests",
)

loaded = [
    module
    for module in sys.modules
    if module.startswith(forbidden_prefixes) or module in forbidden_modules
]
if loaded:
    raise AssertionError(
        f"work_queue.store import loaded forbidden modules: {loaded}"
    )
"""
    )
    assert completed.returncode == 0, completed.stderr
