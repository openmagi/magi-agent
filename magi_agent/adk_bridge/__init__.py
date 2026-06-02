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
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = _LAZY_EXPORTS[name]
    module = import_module(module_name, __name__)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
