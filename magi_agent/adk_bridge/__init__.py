from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "AdkCallbackBoundary",
    "AdkPluginBoundary",
    "AdkPrimitiveBoundary",
    "AdkToolConfirmationBoundary",
]

_LAZY_EXPORTS = {
    "AdkCallbackBoundary": (".policy_boundary", "AdkCallbackBoundary"),
    "AdkPluginBoundary": (".policy_boundary", "AdkPluginBoundary"),
    "AdkPrimitiveBoundary": (".primitives", "AdkPrimitiveBoundary"),
    "AdkToolConfirmationBoundary": (".policy_boundary", "AdkToolConfirmationBoundary"),
}


def __getattr__(name: str) -> Any:
    if name not in _LAZY_EXPORTS:
        # Test-isolation fallback: when a sibling test pops this package out of
        # ``sys.modules`` and a later submodule import re-creates a fresh parent
        # package object, submodules that were previously bound as attributes
        # are gone. Re-import the real submodule on demand so attribute access
        # (e.g. ``magi_agent.adk_bridge.lifecycle_llm_call_control``) stays
        # order/worker independent. Never import eagerly; skip dunder/private.
        if name.startswith("_"):
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
        try:
            submodule = import_module(f".{name}", __name__)
        except ImportError as exc:
            raise AttributeError(
                f"module {__name__!r} has no attribute {name!r}"
            ) from exc
        globals()[name] = submodule
        return submodule
    module_name, attr_name = _LAZY_EXPORTS[name]
    module = import_module(module_name, __name__)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
