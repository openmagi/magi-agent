"""Meta-test: the ``config/flags.py`` ⇄ ``config/env.py`` managed import cycle
must stay broken (I-3).

Why this exists
---------------
Before I-3, ``config/flags.py`` eagerly imported ``_is_true`` and
``_runtime_feature_enabled`` from ``config/env.py`` at the top level, and
``config/env.py`` had to *defer* ``from .flags import …`` inside ~13 function
bodies to dodge the resulting ``ImportError``. Any unguarded top-level
``from .flags import …`` line landing in ``env.py`` during the I-1 sweep would
have re-triggered the cycle and shipped silently as long as nobody imported
``env`` strictly first.

I-3 fixes this by promoting the shared truthy convention into a tiny,
dependency-free leaf ``config/_truthy.py``. This test enforces that contract
structurally so it cannot regress:

1. ``config/_truthy.py`` imports nothing from ``magi_agent`` — pure stdlib.
2. ``config/flags.py`` no longer imports from ``magi_agent.config.env``.
3. Both ``config/env.py`` and ``config/flags.py`` can be imported in either
   order from a clean ``sys.modules`` snapshot without ``ImportError`` and
   without the "partially initialized module" runtime warning.
"""

from __future__ import annotations

import ast
import importlib
import subprocess
import sys
from pathlib import Path

import pytest


CONFIG_DIR = Path(__file__).resolve().parent.parent / "magi_agent" / "config"


def _module_imports(path: Path) -> list[ast.AST]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]


# ---------------------------------------------------------------------------
# Leaf purity — AST scan of magi_agent/config/_truthy.py
# ---------------------------------------------------------------------------
def test_truthy_leaf_has_no_magi_agent_imports() -> None:
    """``_truthy.py`` must not import any ``magi_agent`` subpackage.

    Importing from ``magi_agent.*`` (including ``magi_agent.config.env`` or
    ``magi_agent.config.flags``) would reintroduce the I-3 cycle the leaf was
    extracted to break. Only stdlib / third-party imports are permitted.
    """
    leaf = CONFIG_DIR / "_truthy.py"
    assert leaf.exists(), (
        "magi_agent/config/_truthy.py is missing — the I-3 leaf must exist."
    )

    offenders: list[str] = []
    for node in _module_imports(leaf):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("magi_agent"):
                    offenders.append(f"import {alias.name} (line {node.lineno})")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            # Disallow both absolute (``magi_agent.…``) and relative
            # (``from . import …`` / ``from .env import …``) imports that
            # could leak the leaf back into the config package graph.
            if module.startswith("magi_agent") or node.level > 0:
                names = ", ".join(alias.name for alias in node.names)
                offenders.append(
                    f"from {'.' * node.level}{module} import {names} (line {node.lineno})"
                )

    assert not offenders, (
        "_truthy.py must be a dependency-free leaf (stdlib only). "
        f"Offending imports: {offenders}"
    )


# ---------------------------------------------------------------------------
# Cycle edge removed — AST scan of magi_agent/config/flags.py
# ---------------------------------------------------------------------------
def test_flags_no_longer_imports_from_env() -> None:
    """``flags.py`` must not import from ``config.env`` at any level.

    This was the cycle edge before I-3: ``from .env import _is_true,
    _runtime_feature_enabled``. Both helpers now live in ``config/_truthy.py``;
    re-introducing a ``.env`` import here would resurrect the managed cycle.
    """
    flags = CONFIG_DIR / "flags.py"
    offenders: list[str] = []
    for node in _module_imports(flags):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "env" and node.level == 1:
                names = ", ".join(alias.name for alias in node.names)
                offenders.append(
                    f"from .env import {names} (line {node.lineno})"
                )
            if module in {
                "magi_agent.config.env",
                "magi_agent.config.env.",
            }:
                names = ", ".join(alias.name for alias in node.names)
                offenders.append(
                    f"from {module} import {names} (line {node.lineno})"
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "magi_agent.config.env":
                    offenders.append(
                        f"import {alias.name} (line {node.lineno})"
                    )

    assert not offenders, (
        "config/flags.py must not import from config.env (I-3 cycle edge). "
        f"Offending imports: {offenders}"
    )


# ---------------------------------------------------------------------------
# Runtime import-order proof — fresh subprocess, both directions
# ---------------------------------------------------------------------------
_IMPORT_FLAGS_FIRST = (
    "import warnings\n"
    "warnings.filterwarnings('error')\n"
    "import magi_agent.config.flags  # noqa: F401\n"
    "import magi_agent.config.env  # noqa: F401\n"
    "print('OK')\n"
)

_IMPORT_ENV_FIRST = (
    "import warnings\n"
    "warnings.filterwarnings('error')\n"
    "import magi_agent.config.env  # noqa: F401\n"
    "import magi_agent.config.flags  # noqa: F401\n"
    "print('OK')\n"
)


@pytest.mark.parametrize(
    ("label", "snippet"),
    [
        ("flags_first", _IMPORT_FLAGS_FIRST),
        ("env_first", _IMPORT_ENV_FIRST),
    ],
)
def test_config_modules_import_cleanly_in_either_order(
    label: str, snippet: str
) -> None:
    """A fresh interpreter must import flags and env in either order without
    ``ImportError`` or any "module not fully initialized" warning.

    Run in a subprocess so we get a genuinely cold ``sys.modules`` and so the
    ``warnings.filterwarnings('error')`` upgrade catches the
    ``partially-initialized-module`` RuntimeWarning Python emits when an
    import cycle gets papered over by a lazy-import shim.
    """
    repo_root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, "-c", snippet],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"[{label}] subprocess failed (cycle reintroduced?).\n"
        f"stdout: {result.stdout!r}\n"
        f"stderr: {result.stderr!r}"
    )
    assert "OK" in result.stdout, (
        f"[{label}] expected OK marker. stdout={result.stdout!r}"
    )
    # Defensive: even on returncode 0, surface any warning text.
    assert "partially initialized" not in result.stderr, (
        f"[{label}] partially-initialized-module warning seen: {result.stderr!r}"
    )
    assert "ImportError" not in result.stderr, (
        f"[{label}] unexpected ImportError in stderr: {result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Runtime tie-back — env/flags resolve to the same truthy primitives
# ---------------------------------------------------------------------------
def test_env_and_flags_share_one_truthy_leaf() -> None:
    """The ``_is_true`` re-exported from env.py and the truthy helper used
    inside flags.py must be the very same callable object from
    ``config/_truthy.py``. If a parallel implementation sneaks back in, this
    breaks immediately.
    """
    truthy = importlib.import_module("magi_agent.config._truthy")
    env = importlib.import_module("magi_agent.config.env")
    # The historic private alias must now be the leaf function.
    assert env._is_true is truthy.is_true
    assert env._runtime_feature_enabled is truthy.runtime_feature_enabled
    assert env._runtime_profile_default_enabled is truthy.runtime_profile_default_enabled
    assert env._env_bool_default_true is truthy.env_bool_default_true
