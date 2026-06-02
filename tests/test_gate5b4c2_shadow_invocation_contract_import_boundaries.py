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


def test_gate5b4c2_shadow_contract_import_is_schema_only_and_runtime_free() -> None:
    completed = _run_fresh_python(
        """
import importlib
import sys

module = importlib.import_module(
    "magi_agent.shadow.gate5b4c2_shadow_invocation_contract"
)
assert module is not None

forbidden_exact = (
    "google.adk.runners",
    "google.adk.agents",
    "magi_agent.adk_bridge.runner_adapter",
    "magi_agent.shadow.gate4c1_runner_shadow_invoker",
    "magi_agent.tools.dispatcher",
    "openai",
    "anthropic",
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
    raise AssertionError(f"Gate 5B-4c-2 contract loaded forbidden modules: {loaded}")
"""
    )

    assert completed.returncode == 0, completed.stderr


def test_gate5b4c2_shadow_contract_source_forbids_live_surfaces() -> None:
    root = Path(__file__).parents[1]
    module_paths = (
        root
        / "magi_agent"
        / "shadow"
        / "gate5b4c2_shadow_invocation_contract.py",
        root
        / "magi_agent"
        / "transport"
        / "shadow_invocations.py",
    )
    forbidden_imports = (
        "google.adk",
        "magi_agent.adk_bridge.runner_adapter",
        "magi_agent.shadow.gate4c1_runner_shadow_invoker",
        "magi_agent.tools.dispatcher",
        "magi_agent.memory",
        "magi_agent.routing",
        "magi_agent.workspace",
        "magi_agent.deploy",
        "magi_agent.provisioning",
        "magi_agent.k8s",
        "magi_agent.telegram",
        "magi_agent.database",
        "magi_agent.api",
        "magi_agent.dashboard",
        "subprocess",
        "asyncio.subprocess",
        "pexpect",
        "shlex",
        "runpy",
        "codeop",
        "openai",
        "anthropic",
        "requests",
        "httpx",
    )

    for module_path in module_paths:
        source = module_path.read_text(encoding="utf-8")
        for forbidden in forbidden_imports:
            assert f"import {forbidden}" not in source
            assert f"from {forbidden}" not in source
        assert "Runner(" not in source
        assert "ToolDispatcher" not in source
        assert "ToolHost" not in source
        assert "FunctionTool(" not in source
        assert "LongRunningFunctionTool(" not in source
        assert "MemoryService" not in source
        assert "AgentMemory" not in source
        assert "Hipocampus" not in source
        assert "qmd" not in source.lower()
        assert "os.system" not in source
        assert "exec(" not in source
        assert "eval(" not in source
