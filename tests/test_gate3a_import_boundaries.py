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
        "openmagi_core_agent.shadow.gate3a_bundle",
        "openmagi_core_agent.shadow.gate3a_replay",
        "openmagi_core_agent.shadow.gate3a_report",
    ),
)
def test_gate3a_modules_do_not_import_forbidden_production_surfaces(
    module_name: str,
) -> None:
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
)
loaded = [
    module_name
    for module_name in sys.modules
    if any(
        module_name == prefix or module_name.startswith(f"{{prefix}}.")
        for prefix in forbidden_prefixes
    )
]
if loaded:
    raise AssertionError(f"Gate 3A import loaded forbidden modules: {{loaded}}")
"""
    )

    assert completed.returncode == 0, completed.stderr


def test_disabled_gate3a_env_parse_does_not_import_replay_module() -> None:
    completed = _run_fresh_python(
        """
import sys
from openmagi_core_agent.config.env import parse_gate3a_recorded_replay_env

config = parse_gate3a_recorded_replay_env({})
assert config.enabled is False
assert "openmagi_core_agent.shadow.gate3a_replay" not in sys.modules
"""
    )

    assert completed.returncode == 0, completed.stderr


def test_disabled_gate3a_env_parse_ignores_dirs_without_importing_replay_module() -> None:
    completed = _run_fresh_python(
        """
import sys
from openmagi_core_agent.config.env import parse_gate3a_recorded_replay_env

config = parse_gate3a_recorded_replay_env({
    "CORE_AGENT_PYTHON_GATE3A_RECORDED_REPLAY": "false",
    "CORE_AGENT_PYTHON_GATE3A_INPUT_DIR": "/data/bots/bot-123",
    "CORE_AGENT_PYTHON_GATE3A_OUTPUT_DIR": "/workspace/bot-123",
})
assert config.enabled is False
assert config.input_dir is None
assert config.output_dir is None
assert "openmagi_core_agent.shadow.gate3a_replay" not in sys.modules
"""
    )

    assert completed.returncode == 0, completed.stderr


def test_gate3a_runtime_modules_do_not_import_shell_or_code_execution_helpers() -> None:
    root = Path(__file__).parents[1]
    module_paths = (
        root / "openmagi_core_agent" / "shadow" / "gate3a_bundle.py",
        root / "openmagi_core_agent" / "shadow" / "gate3a_replay.py",
        root / "openmagi_core_agent" / "shadow" / "gate3a_report.py",
        root / "openmagi_core_agent" / "config" / "env.py",
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
