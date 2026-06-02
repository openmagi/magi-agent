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


def test_gate4d_diagnostics_imports_no_runner_or_production_metrics_surfaces() -> None:
    completed = _run_fresh_python(
        """
import importlib
import sys

module = importlib.import_module("magi_agent.shadow.gate4d_local_shadow_diagnostics")
assert module is not None

forbidden_exact = (
    "google.adk.runners",
    "magi_agent.adk_bridge.runner_adapter",
    "magi_agent.tools.dispatcher",
    "openai",
)
forbidden_prefixes = (
    "magi_agent.transport.chat",
    "magi_agent.transport.tools",
    "magi_agent.channels",
    "magi_agent.runtime.openmagi_runtime",
    "magi_agent.routing",
    "magi_agent.workspace",
    "magi_agent.deploy",
    "magi_agent.provisioning",
    "magi_agent.k8s",
    "magi_agent.telegram",
    "magi_agent.api",
    "magi_agent.proxy",
    "magi_agent.dashboard",
    "magi_agent.database",
    "magi_agent.billing",
    "magi_agent.auth",
    "magi_agent.model_routing",
    "magi_agent.metrics.production",
    "magi_agent.missions",
    "magi_agent.scheduler",
    "magi_agent.children",
    "magi_agent.memory.providers",
    "magi_agent.agentmemory",
    "magi_agent.hipocampus",
    "magi_agent.qmd",
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
    raise AssertionError(f"Gate 4D diagnostics loaded forbidden modules: {loaded}")
"""
    )

    assert completed.returncode == 0, completed.stderr


def test_gate4d_diagnostics_source_has_no_runner_or_metrics_service_calls() -> None:
    root = Path(__file__).parents[1]
    module_path = (
        root / "magi_agent" / "shadow" / "gate4d_local_shadow_diagnostics.py"
    )
    source = module_path.read_text(encoding="utf-8")
    forbidden_imports = (
        "google.adk",
        "magi_agent.adk_bridge.runner_adapter",
        "magi_agent.tools.dispatcher",
        "subprocess",
        "asyncio.subprocess",
        "pexpect",
        "shlex",
        "runpy",
        "codeop",
        "openai",
        "requests",
        "httpx",
    )

    for forbidden in forbidden_imports:
        assert f"import {forbidden}" not in source
        assert f"from {forbidden}" not in source
    assert "Runner(" not in source
    assert "run_async" not in source
    assert "ToolDispatcher" not in source
    assert "ToolHost" not in source
    assert "FunctionTool(" not in source
    assert "LongRunningFunctionTool(" not in source
    assert "metrics.publish" not in source
    assert "os.system" not in source
    assert "exec(" not in source
    assert "eval(" not in source
