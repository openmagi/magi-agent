from __future__ import annotations

from importlib import import_module
from typing import Any

from .manifest import (
    PluginCapability,
    PluginHookRef,
    PluginKind,
    PluginManifest,
    PluginRuntime,
    PluginSecretRef,
    PluginToolRef,
    parse_plugin_manifest,
)
from .sandbox_policy import (
    FilesystemAccess,
    NetworkAccess,
    PluginSandboxDecision,
    PluginSandboxPolicy,
    PluginTrustLevel,
    ProcessAccess,
    ProtectedBindingAccess,
    SandboxMode,
    evaluate_plugin_sandbox,
)

__all__ = [
    "FilesystemAccess",
    "NetworkAccess",
    "PluginCapability",
    "PluginHookRef",
    "PluginKind",
    "PluginManifest",
    "PluginRuntime",
    "PluginSandboxDecision",
    "PluginSandboxPolicy",
    "PluginSecretRef",
    "PluginToolRef",
    "PluginTrustLevel",
    "ProcessAccess",
    "ProtectedBindingAccess",
    "SandboxMode",
    "PluginAuditEntry",
    "PluginAuditSecret",
    "PluginAuditSnapshot",
    "PluginAuditSummary",
    "ShellCommandSafetyDecision",
    "ShellOutputBudget",
    "ShellTestRunAuthorityFlags",
    "ShellTestRunDecision",
    "ShellTestRunMaterialization",
    "ShellTestRunSafeSubsetBinding",
    "ShellTestRunSafeSubsetConfig",
    "ShellTestRunSafeSubsetRequest",
    "build_plugin_audit_snapshot",
    "evaluate_plugin_sandbox",
    "parse_plugin_manifest",
]

_LAZY_EXPORTS = {
    "PluginAuditEntry": (".audit", "PluginAuditEntry"),
    "PluginAuditSecret": (".audit", "PluginAuditSecret"),
    "PluginAuditSnapshot": (".audit", "PluginAuditSnapshot"),
    "PluginAuditSummary": (".audit", "PluginAuditSummary"),
    "ShellCommandSafetyDecision": (
        ".shell_testrun_safe_subset",
        "ShellCommandSafetyDecision",
    ),
    "ShellOutputBudget": (".shell_testrun_safe_subset", "ShellOutputBudget"),
    "ShellTestRunAuthorityFlags": (
        ".shell_testrun_safe_subset",
        "ShellTestRunAuthorityFlags",
    ),
    "ShellTestRunDecision": (".shell_testrun_safe_subset", "ShellTestRunDecision"),
    "ShellTestRunMaterialization": (
        ".shell_testrun_safe_subset",
        "ShellTestRunMaterialization",
    ),
    "ShellTestRunSafeSubsetBinding": (
        ".shell_testrun_safe_subset",
        "ShellTestRunSafeSubsetBinding",
    ),
    "ShellTestRunSafeSubsetConfig": (
        ".shell_testrun_safe_subset",
        "ShellTestRunSafeSubsetConfig",
    ),
    "ShellTestRunSafeSubsetRequest": (
        ".shell_testrun_safe_subset",
        "ShellTestRunSafeSubsetRequest",
    ),
    "build_plugin_audit_snapshot": (".audit", "build_plugin_audit_snapshot"),
}


def __getattr__(name: str) -> Any:
    if name not in _LAZY_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = _LAZY_EXPORTS[name]
    module = import_module(module_name, __name__)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
