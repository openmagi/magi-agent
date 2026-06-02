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
        "magi_agent.shadow.gate3a_bundle",
        "magi_agent.shadow.gate3a_replay",
        "magi_agent.shadow.gate3a_report",
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
    "magi_agent.transport.chat",
    "magi_agent.transport.tools",
    "magi_agent.transport.plugins",
    "magi_agent.channels",
    "magi_agent.runtime.openmagi_runtime",
    "magi_agent.runtime.turn_controller",
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
from magi_agent.config.env import parse_gate3a_recorded_replay_env

config = parse_gate3a_recorded_replay_env({})
assert config.enabled is False
assert "magi_agent.shadow.gate3a_replay" not in sys.modules
"""
    )

    assert completed.returncode == 0, completed.stderr


def test_disabled_gate3a_env_parse_ignores_dirs_without_importing_replay_module() -> None:
    completed = _run_fresh_python(
        """
import sys
from magi_agent.config.env import parse_gate3a_recorded_replay_env

config = parse_gate3a_recorded_replay_env({
    "CORE_AGENT_PYTHON_GATE3A_RECORDED_REPLAY": "false",
    "CORE_AGENT_PYTHON_GATE3A_INPUT_DIR": "/data/bots/bot-123",
    "CORE_AGENT_PYTHON_GATE3A_OUTPUT_DIR": "/workspace/bot-123",
})
assert config.enabled is False
assert config.input_dir is None
assert config.output_dir is None
assert "magi_agent.shadow.gate3a_replay" not in sys.modules
"""
    )

    assert completed.returncode == 0, completed.stderr


def test_gate3a_runtime_modules_do_not_import_shell_or_code_execution_helpers() -> None:
    root = Path(__file__).parents[1]
    module_paths = (
        root / "magi_agent" / "shadow" / "gate3a_bundle.py",
        root / "magi_agent" / "shadow" / "gate3a_replay.py",
        root / "magi_agent" / "shadow" / "gate3a_report.py",
        root / "magi_agent" / "config" / "env.py",
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
