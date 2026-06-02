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


def test_gate4_bridge_does_not_import_adk_runner_or_live_surfaces() -> None:
    completed = _run_fresh_python(
        """
import importlib
import sys

module = importlib.import_module("magi_agent.shadow.gate4_bridge")
assert module is not None

forbidden_exact = (
    "google.adk.runners",
    "magi_agent.adk_bridge.runner_adapter",
    "magi_agent.shadow.gate3a_replay",
    "magi_agent.tools.dispatcher",
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
    "magi_agent.typescript_runtime",
    "magi_agent.ts_runtime",
    "magi_agent.signed_ack",
    "magi_agent.evidence.extractors",
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
    raise AssertionError(f"Gate 4 bridge loaded forbidden modules: {loaded}")
"""
    )

    assert completed.returncode == 0, completed.stderr


def test_gate4_bridge_source_has_no_shell_or_code_execution_helpers() -> None:
    root = Path(__file__).parents[1]
    module_paths = (
        root / "magi_agent" / "shadow" / "gate4_bridge.py",
        root / "magi_agent" / "shadow" / "gate3b_local_consumer.py",
        root / "magi_agent" / "shadow" / "gate3b_local_report.py",
        root / "magi_agent" / "shadow" / "gate3b_metrics.py",
    )
    forbidden_imports = (
        "subprocess",
        "asyncio.subprocess",
        "pexpect",
        "shlex",
        "runpy",
        "code",
        "codeop",
        "pip",
        "npm",
    )

    for module_path in module_paths:
        source = module_path.read_text(encoding="utf-8")
        for forbidden in forbidden_imports:
            assert f"import {forbidden}" not in source
            assert f"from {forbidden}" not in source
        assert "os.system" not in source
        assert "exec(" not in source
        assert "eval(" not in source
