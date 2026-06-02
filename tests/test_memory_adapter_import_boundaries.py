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

importlib.import_module("openmagi_core_agent.memory.contracts")
importlib.import_module("openmagi_core_agent.memory.policy")
importlib.import_module("openmagi_core_agent.memory.adapters.hipocampus_readonly")

forbidden_prefixes = (
    "google.adk",
    "openmagi_core_agent.adk_bridge.runner_adapter",
    "openmagi_core_agent.adk_bridge.local_runner",
    "openmagi_core_agent.adk_bridge.tool_adapter",
    "openmagi_core_agent.tools.dispatcher",
    "openmagi_core_agent.tools.registry",
    "openmagi_core_agent.plugins.agentmemory",
    "openmagi_core_agent.services.memory",
    "openmagi_core_agent.hipocampus",
    "openmagi_core_agent.qmd",
    "openmagi_core_agent.app",
    "openmagi_core_agent.transport.chat",
    "openmagi_core_agent.routes",
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
