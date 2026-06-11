"""Tests for the env-reference drift gate (15-flag-governance.md PR4 / D4).

The gate guarantees docs/env-reference.md stays in lockstep with the
``magi_agent/config/flags.py`` registry: a new public flag registered without
regenerating the doc must fail CI.

Two surfaces are covered:

1. ``scripts/generate_env_reference.py --check`` — the drift detector. Exits 0
   when the doc matches the registry, exits 1 when a staled copy diverges.
2. ``scripts/check_env_reference.sh`` — the CI wrapper that invokes the
   detector and prints a regenerate hint on failure (mirrors
   ``scripts/check_module_map.sh``).

Pure file/registry/subprocess reads — no network, no model.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
GENERATOR = ROOT_DIR / "scripts" / "generate_env_reference.py"
WRAPPER = ROOT_DIR / "scripts" / "check_env_reference.sh"
ENV_REFERENCE = ROOT_DIR / "docs" / "env-reference.md"


# ---------------------------------------------------------------------------
# The wrapper script exists and mirrors the module-map gate convention.
# ---------------------------------------------------------------------------


def test_wrapper_script_exists() -> None:
    assert WRAPPER.is_file(), "scripts/check_env_reference.sh must exist"


def test_wrapper_invokes_check_mode() -> None:
    text = WRAPPER.read_text(encoding="utf-8")
    assert "generate_env_reference.py --check" in text
    # Failure path must point the contributor at the regenerate command.
    assert "scripts/generate_env_reference.py" in text


# ---------------------------------------------------------------------------
# Drift detector: in-sync passes, staled doc fails.
# ---------------------------------------------------------------------------


def _run_check(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GENERATOR), "--check", "--path", str(path)],
        capture_output=True,
        text=True,
        cwd=str(ROOT_DIR),
    )


def test_check_passes_when_committed_doc_in_sync() -> None:
    """The committed doc is in sync with the registry → exit 0."""
    result = _run_check(ENV_REFERENCE)
    assert result.returncode == 0, (
        f"committed env-reference.md is stale: {result.stderr}"
    )


def test_check_fails_when_doc_is_staled(tmp_path: Path) -> None:
    """A staled copy (generated section gutted) drifts → non-zero exit."""
    staled = tmp_path / "env-reference.md"
    shutil.copyfile(ENV_REFERENCE, staled)

    text = staled.read_text(encoding="utf-8")
    begin = "<!-- BEGIN GENERATED FLAGS (scripts/generate_env_reference.py) -->"
    end = "<!-- END GENERATED FLAGS -->"
    bi = text.index(begin)
    ei = text.index(end)
    # Replace the generated body with an obviously-stale placeholder while
    # keeping the markers intact (so the rewrite would re-fill it).
    staled_text = text[: bi + len(begin)] + "\n(stale)\n\n" + text[ei:]
    staled.write_text(staled_text, encoding="utf-8")

    result = _run_check(staled)
    assert result.returncode != 0, "staled env-reference.md should fail the gate"


# ---------------------------------------------------------------------------
# The CI workflow wires the gate into the lint job.
# ---------------------------------------------------------------------------


def test_ci_workflow_wires_env_reference_gate() -> None:
    ci = (ROOT_DIR / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "scripts/check_env_reference.sh" in ci, (
        "ci.yml must invoke the env-reference drift gate"
    )
