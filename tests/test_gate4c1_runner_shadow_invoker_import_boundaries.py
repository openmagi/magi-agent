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


def test_gate4c1_runner_invoker_import_is_lazy_and_shadow_only() -> None:
    completed = _run_fresh_python(
        """
import importlib
import sys

module = importlib.import_module("openmagi_core_agent.shadow.gate4c1_runner_shadow_invoker")
assert module is not None

forbidden_exact = (
    "google.adk.runners",
    "openmagi_core_agent.adk_bridge.runner_adapter",
    "openmagi_core_agent.tools.dispatcher",
    "openai",
)
forbidden_prefixes = (
    "openmagi_core_agent.transport.chat",
    "openmagi_core_agent.transport.tools",
    "openmagi_core_agent.channels",
    "openmagi_core_agent.runtime.openmagi_runtime",
    "openmagi_core_agent.routing",
    "openmagi_core_agent.workspace",
    "openmagi_core_agent.deploy",
    "openmagi_core_agent.provisioning",
    "openmagi_core_agent.k8s",
    "openmagi_core_agent.telegram",
    "openmagi_core_agent.api",
    "openmagi_core_agent.proxy",
    "openmagi_core_agent.dashboard",
    "openmagi_core_agent.database",
    "openmagi_core_agent.billing",
    "openmagi_core_agent.auth",
    "openmagi_core_agent.model_routing",
    "openmagi_core_agent.missions",
    "openmagi_core_agent.scheduler",
    "openmagi_core_agent.children",
    "openmagi_core_agent.memory.providers",
    "openmagi_core_agent.agentmemory",
    "openmagi_core_agent.hipocampus",
    "openmagi_core_agent.qmd",
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
    raise AssertionError(f"Gate 4C-1 invoker import loaded forbidden modules: {loaded}")
"""
    )

    assert completed.returncode == 0, completed.stderr


def test_gate4c1_runner_invoker_source_limits_runner_and_forbids_live_surfaces() -> None:
    root = Path(__file__).parents[1]
    module_path = (
        root / "openmagi_core_agent" / "shadow" / "gate4c1_runner_shadow_invoker.py"
    )
    source = module_path.read_text(encoding="utf-8")
    forbidden_imports = (
        "openmagi_core_agent.adk_bridge.runner_adapter",
        "openmagi_core_agent.tools.dispatcher",
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
    assert "ToolDispatcher" not in source
    assert "ToolHost" not in source
    assert "FunctionTool(" not in source
    assert "LongRunningFunctionTool(" not in source
    assert "os.system" not in source
    assert "exec(" not in source
    assert "eval(" not in source
    assert "state_delta" not in source
    assert "run_config" not in source
    assert "responses.create" not in source
    assert "chat.completions" not in source
    assert "generate_content(" not in source
    assert source.count("google.adk") <= 3
