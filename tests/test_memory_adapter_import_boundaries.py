from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_memory_contract_import_stays_adk_runner_toolhost_route_and_provider_free() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("magi_agent.memory.contracts")
importlib.import_module("magi_agent.memory.policy")
importlib.import_module("magi_agent.memory.adapters.hipocampus_readonly")

forbidden_prefixes = (
    "google.adk",
    "magi_agent.adk_bridge.runner_adapter",
    "magi_agent.adk_bridge.local_runner",
    "magi_agent.adk_bridge.tool_adapter",
    "magi_agent.tools.dispatcher",
    "magi_agent.tools.registry",
    "magi_agent.plugins.agentmemory",
    "magi_agent.services.memory",
    "magi_agent.hipocampus",
    "magi_agent.qmd",
    "magi_agent.app",
    "magi_agent.transport.chat",
    "magi_agent.routes",
    "subprocess",
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
    raise AssertionError(f"memory adapter import loaded forbidden modules: {loaded}")
""",
        ],
        check=False,
        cwd=Path(__file__).parents[1],
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
