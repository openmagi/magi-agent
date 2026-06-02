from __future__ import annotations

from importlib import import_module

__all__ = [
    "SandboxAuthorityFlags",
    "SandboxDecision",
    "SandboxPolicy",
    "evaluate_browser_request",
    "evaluate_child_workspace_request",
    "evaluate_filesystem_access",
    "evaluate_network_access",
    "evaluate_process_request",
]

_LAZY_EXPORTS = {
    "SandboxAuthorityFlags": (".policy", "SandboxAuthorityFlags"),
    "SandboxDecision": (".policy", "SandboxDecision"),
    "SandboxPolicy": (".policy", "SandboxPolicy"),
    "evaluate_browser_request": (".browser", "evaluate_browser_request"),
    "evaluate_child_workspace_request": (
        ".child_workspace",
        "evaluate_child_workspace_request",
    ),
    "evaluate_filesystem_access": (".filesystem", "evaluate_filesystem_access"),
    "evaluate_network_access": (".network", "evaluate_network_access"),
    "evaluate_process_request": (".process", "evaluate_process_request"),
}


def __getattr__(name: str) -> object:
    if name not in _LAZY_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = _LAZY_EXPORTS[name]
    module = import_module(module_name, __name__)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
