"""Pack B1 acceptance — a scaffolded pack's GENERATED smoke test passes as-is.

The roadmap's B1 acceptance criterion: ``magi pack new`` output must load
through the real loader AND its generated pytest smoke test must pass with
zero ``PYTHONPATH``/``sys.path`` setup. We assert that end-to-end for the two
executable-behavior types (validator, tool) by running the generated test file
under a REAL pytest subprocess; the remaining six types are covered by the
manifest-validity self-check inside ``scaffold_pack`` plus the parametrized
real-loader load in ``test_scaffold.py``.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from magi_agent.packs.scaffold import scaffold_pack

_KEY_VARS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")


def _run_generated_smoke(test_path: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in _KEY_VARS}
    env["MAGI_CONFIG"] = str(tmp_path / "config.toml")
    return subprocess.run(
        [sys.executable, "-m", "pytest", str(test_path), "-q", "-p", "no:cacheprovider"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
        timeout=120,
    )


@pytest.mark.parametrize("ptype,name", [("validator", "accept-check"), ("tool", "accept-tool")])
def test_generated_smoke_test_passes_under_real_pytest(ptype, name, tmp_path) -> None:
    meta = scaffold_pack(ptype, name, tmp_path / "packs")

    proc = _run_generated_smoke(meta.test_path, tmp_path)

    assert proc.returncode == 0, f"generated smoke test failed:\n{proc.stdout}\n{proc.stderr}"
    assert "2 passed" in proc.stdout, proc.stdout
