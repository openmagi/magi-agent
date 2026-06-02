from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _run_fresh_python(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )


def test_priority_a_model_routing_import_is_metadata_only() -> None:
    completed = _run_fresh_python(
        """
import importlib
import sys

module = importlib.import_module("magi_agent.runtime.model_routing")
assert hasattr(module, "build_turn_model_routing_decision")

forbidden_exact = (
    "google.adk.runners",
    "google.adk.agents",
    "google.adk.sessions",
    "google.adk.tools",
    "openai",
    "anthropic",
    "requests",
    "httpx",
    "urllib.request",
    "http.client",
    "socket",
    "fastapi",
    "starlette.routing",
    "magi_agent.adk_bridge.runner_adapter",
    "magi_agent.adk_bridge.local_runner",
    "magi_agent.tools.dispatcher",
)
forbidden_prefixes = (
    "magi_agent.tools",
    "magi_agent.memory",
    "magi_agent.workspace",
    "magi_agent.transport",
    "magi_agent.channels",
    "magi_agent.shadow.gate4c1_runner_shadow_invoker",
    "magi_agent.children",
    "magi_agent.missions",
    "magi_agent.scheduler",
)
loaded = [
    loaded_name
    for loaded_name in sys.modules
    if loaded_name in forbidden_exact
    or any(loaded_name.startswith(f"{name}.") for name in forbidden_exact)
    or any(
        loaded_name == prefix or loaded_name.startswith(f"{prefix}.")
        for prefix in forbidden_prefixes
    )
]
if loaded:
    raise AssertionError(f"model routing import loaded forbidden modules: {loaded}")
"""
    )

    assert completed.returncode == 0, completed.stderr


def test_priority_a_model_routing_source_forbids_runtime_side_effect_imports() -> None:
    root = Path(__file__).parents[1]
    module_path = root / "magi_agent" / "runtime" / "model_routing.py"
    source = module_path.read_text(encoding="utf-8")
    forbidden_imports = (
        "google.adk",
        "openai",
        "anthropic",
        "requests",
        "httpx",
        "urllib",
        "http.client",
        "socket",
        "subprocess",
        "asyncio",
        "fastapi",
        "starlette",
        "magi_agent.adk_bridge.runner_adapter",
        "magi_agent.adk_bridge.local_runner",
        "magi_agent.tools",
        "magi_agent.memory",
        "magi_agent.workspace",
        "magi_agent.transport",
        "magi_agent.channels",
        "magi_agent.children",
        "magi_agent.missions",
    )

    for forbidden in forbidden_imports:
        assert f"import {forbidden}" not in source
        assert f"from {forbidden}" not in source
    assert "Runner(" not in source
    assert "run_async" not in source
    assert "Agent(" not in source
    assert "ToolDispatcher" not in source
    assert "ToolHost" not in source
    assert "FunctionTool(" not in source
    assert "LongRunningFunctionTool(" not in source
    assert "APIRouter" not in source
    assert "FastAPI" not in source
    assert "add_api_route" not in source
    assert "@app." not in source
    assert "MemoryService" not in source
    assert "AgentMemory" not in source
    assert "WorkspaceIsolation" not in source
    assert "ChildAgent" not in source
    assert "Mission" not in source
    assert "os.system" not in source
    assert "exec(" not in source
    assert "eval(" not in source
