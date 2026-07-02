"""rem2/F3 (deep-review N-08): cli/contracts -> engine/contracts pure move.

The engine contracts are the runtime-neutral, zero-heavy-dep interface
surface. They move to the new ``magi_agent.engine`` kernel package, with a
``sys.modules`` self-alias shim left at the old ``magi_agent.cli.contracts``
path so old and new import paths yield the SAME module object: the frozen
``__all__`` surface, underscore-private names, ``is`` identity, and
``monkeypatch`` targets are all preserved byte-compatibly.
"""

from __future__ import annotations

import subprocess
import sys

# Frozen surface snapshot (order matters; copied from the module's __all__).
_FROZEN_ALL = [
    "Terminal",
    "RuntimeEvent",
    "EngineResult",
    "EngineDriver",
    "TurnInput",
    "RuleVerdict",
    "PermissionUpdate",
    "PermissionDecision",
    "PromptSink",
    "PermissionGate",
    "NullPermissionGate",
    "RenderNode",
    "ToolRenderer",
    "ToolRendererRegistry",
    "CommandSurface",
    "CommandContext",
    "EmitFn",
    "ContentBlock",
    "LocalResult",
    "Text",
    "Compact",
    "Skip",
    "PromptCommand",
    "LocalCommand",
    "WidgetCommand",
    "WidgetDone",
    "Command",
    "CommandExecutor",
    "CommandRegistry",
    "ControlRequest",
]


def _run_fresh_python(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", script, *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_engine_contracts_module_exists() -> None:
    import magi_agent.engine.contracts as new

    assert new is not None


def test_old_and_new_paths_are_same_module() -> None:
    import magi_agent.cli.contracts as old
    import magi_agent.engine.contracts as new

    assert old is new


def test_frozen_all_surface_is_byte_compatible() -> None:
    import magi_agent.cli.contracts as old
    import magi_agent.engine.contracts as new

    assert list(old.__all__) == _FROZEN_ALL
    assert list(new.__all__) == _FROZEN_ALL
    for name in _FROZEN_ALL:
        assert getattr(old, name) is getattr(new, name)


def test_contracts_import_stays_heavy_dep_free() -> None:
    completed = _run_fresh_python(
        """
import importlib
import sys

importlib.import_module("magi_agent.engine.contracts")
forbidden = [m for m in ("textual", "rich", "google.adk") if m in sys.modules]
assert not forbidden, forbidden
"""
    )

    assert completed.returncode == 0, completed.stderr
