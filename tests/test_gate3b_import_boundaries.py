from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


def _run_fresh_python(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    "module_name",
    (
        "openmagi_core_agent.shadow.gate3b_bundle",
        "openmagi_core_agent.shadow.gate3b_ingest",
    ),
)
def test_gate3b_modules_do_not_import_forbidden_production_surfaces(module_name: str) -> None:
    completed = _run_fresh_python(
        f"""
import importlib
import sys

module = importlib.import_module({module_name!r})
assert module is not None

forbidden_prefixes = (
    "openmagi_core_agent.transport.chat",
    "openmagi_core_agent.transport.tools",
    "openmagi_core_agent.transport.plugins",
    "openmagi_core_agent.channels",
    "openmagi_core_agent.runtime.openmagi_runtime",
    "openmagi_core_agent.runtime.turn_controller",
    "openmagi_core_agent.routing",
    "openmagi_core_agent.tools.dispatcher",
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
    "openmagi_core_agent.typescript_runtime",
    "openmagi_core_agent.ts_runtime",
    "openmagi_core_agent.signed_ack",
    "openmagi_core_agent.evidence.extractors",
    "openmagi_core_agent.adk_bridge.runner_adapter",
)
loaded = [
    loaded_name
    for loaded_name in sys.modules
    if any(
        loaded_name == prefix or loaded_name.startswith(f"{{prefix}}.")
        for prefix in forbidden_prefixes
    )
]
if loaded:
    raise AssertionError(f"Gate 3B import loaded forbidden modules: {{loaded}}")
"""
    )

    assert completed.returncode == 0, completed.stderr


def test_gate3b_modules_do_not_import_or_call_shell_code_execution_helpers() -> None:
    root = Path(__file__).parents[1]
    module_paths = (
        root / "openmagi_core_agent" / "shadow" / "gate3b_bundle.py",
        root / "openmagi_core_agent" / "shadow" / "gate3b_ingest.py",
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


def test_gate3b_conversion_does_not_import_gate3a_or_adk_runner() -> None:
    completed = _run_fresh_python(
        """
import sys
from pathlib import Path

from openmagi_core_agent.shadow.gate3b_bundle import load_gate3b_live_duplicate_bundle
from openmagi_core_agent.shadow.gate3b_ingest import (
    convert_gate3b_live_duplicate_to_gate3a_recorded_bundle,
)

fixture_root = Path("tests/fixtures/gate3b")
bundle = load_gate3b_live_duplicate_bundle(
    "redacted_live_duplicate_bundle.json",
    bundle_root=fixture_root,
)
handoff = convert_gate3b_live_duplicate_to_gate3a_recorded_bundle(bundle)
assert handoff.recorded_bundle_payload["schemaVersion"] == "gate3a.recordedBundle.v1"

forbidden_modules = (
    "openmagi_core_agent.adk_bridge.runner_adapter",
    "google.adk.runners",
)
forbidden_prefixes = (
    "openmagi_core_agent.shadow.gate3a",
)
loaded = [
    loaded_name
    for loaded_name in sys.modules
    if loaded_name in forbidden_modules
    or any(loaded_name.startswith(f"{module_name}.") for module_name in forbidden_modules)
    or any(
        loaded_name == prefix or loaded_name.startswith(prefix)
        for prefix in forbidden_prefixes
    )
]
if loaded:
    raise AssertionError(f"Gate 3B conversion loaded forbidden modules: {loaded}")
"""
    )

    assert completed.returncode == 0, completed.stderr
