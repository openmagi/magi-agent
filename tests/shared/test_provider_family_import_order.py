"""rem2/F1 (deep-review N-23): shared/provider_family must be a true leaf.

``shared/provider_family.py`` used to top-level import
``prompt.injection.detect_provider``, forming a real two-node top-level
cycle (shared -> prompt -> shared) that crashed a fresh interpreter with
``ImportError: cannot import name 'ProviderFamily' from partially
initialized module``. These tests lock the leaf property: the two
victim modules must import alone in a fresh process, and the moved
``detect_provider`` must be re-exported from its old ``prompt.injection``
home as the SAME function object.
"""

from __future__ import annotations

import subprocess
import sys


def _run_fresh_python(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", script, *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_shared_provider_family_imports_alone_in_fresh_process() -> None:
    completed = _run_fresh_python(
        "import importlib, sys; importlib.import_module(sys.argv[1])",
        "magi_agent.shared.provider_family",
    )

    assert completed.returncode == 0, completed.stderr


def test_adk_bridge_tool_schema_repair_imports_alone_in_fresh_process() -> None:
    completed = _run_fresh_python(
        "import importlib, sys; importlib.import_module(sys.argv[1])",
        "magi_agent.adk_bridge.tool_schema_repair",
    )

    assert completed.returncode == 0, completed.stderr


def test_detect_provider_reexport_identity() -> None:
    from magi_agent.prompt.injection import detect_provider as a
    from magi_agent.shared.provider_family import detect_provider as b

    assert a is b
