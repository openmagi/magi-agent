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
        "magi_agent.shadow.gate3b_bundle",
        "magi_agent.shadow.gate3b_ingest",
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
    "magi_agent.transport.chat",
    "magi_agent.transport.tools",
    "magi_agent.transport.plugins",
    "magi_agent.channels",
    "magi_agent.runtime.openmagi_runtime",
    "magi_agent.routing",
    "magi_agent.tools.dispatcher",
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
    "magi_agent.adk_bridge.runner_adapter",
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
        root / "magi_agent" / "shadow" / "gate3b_bundle.py",
        root / "magi_agent" / "shadow" / "gate3b_ingest.py",
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

from magi_agent.shadow.gate3b_bundle import load_gate3b_live_duplicate_bundle
from magi_agent.shadow.gate3b_ingest import (
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
    "magi_agent.adk_bridge.runner_adapter",
    "google.adk.runners",
)
forbidden_prefixes = (
    "magi_agent.shadow.gate3a",
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
