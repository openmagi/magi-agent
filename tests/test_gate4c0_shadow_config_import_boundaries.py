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


def test_gate4c0_shadow_config_does_not_import_runner_model_or_live_surfaces() -> None:
    completed = _run_fresh_python(
        """
import importlib
import sys

module = importlib.import_module("openmagi_core_agent.shadow.gate4c0_shadow_config")
assert module is not None

forbidden_exact = (
    "google.adk.runners",
    "openmagi_core_agent.adk_bridge.runner_adapter",
    "openmagi_core_agent.tools.dispatcher",
    "openai",
    "google.genai",
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
    raise AssertionError(f"Gate 4C-0 config loaded forbidden modules: {loaded}")
"""
    )

    assert completed.returncode == 0, completed.stderr


def test_gate4c0_shadow_config_source_has_no_runner_model_tool_or_prompt_execution() -> None:
    root = Path(__file__).parents[1]
    module_path = root / "openmagi_core_agent" / "shadow" / "gate4c0_shadow_config.py"
    source = module_path.read_text(encoding="utf-8")
    forbidden_imports = (
        "google.adk.runners",
        "openmagi_core_agent.adk_bridge.runner_adapter",
        "openmagi_core_agent.tools.dispatcher",
        "subprocess",
        "asyncio.subprocess",
        "pexpect",
        "shlex",
        "runpy",
        "code",
        "codeop",
        "openai",
        "google.genai",
    )

    for forbidden in forbidden_imports:
        assert f"import {forbidden}" not in source
        assert f"from {forbidden}" not in source
    assert "Runner(" not in source
    assert "run_async" not in source
    assert "run_live" not in source
    assert "generate_content" not in source
    assert "responses.create" not in source
    assert "chat.completions" not in source
    assert "FunctionTool(" not in source
    assert "LongRunningFunctionTool(" not in source
    assert "ToolDispatcher" not in source
    assert "ToolHost" not in source
    assert "prompt =" not in source
    assert "os.system" not in source
    assert "exec(" not in source
    assert "eval(" not in source
