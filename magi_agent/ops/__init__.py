from __future__ import annotations

from importlib import import_module

__all__ = [
    "RuntimeMetricRecord",
    "RuntimeMetricsSnapshot",
    "RuntimeOperationEvent",
    "RuntimeOpsAttachmentFlags",
    "build_runtime_metrics_snapshot",
    "default_runtime_ops_health_metadata",
    "scheduler_executor_health_projection",
    "safe_metadata",
]

_LAZY_EXPORTS = {
    "RuntimeMetricRecord": (".metrics", "RuntimeMetricRecord"),
    "RuntimeMetricsSnapshot": (".metrics", "RuntimeMetricsSnapshot"),
    "RuntimeOperationEvent": (".metrics", "RuntimeOperationEvent"),
    "RuntimeOpsAttachmentFlags": (".metrics", "RuntimeOpsAttachmentFlags"),
    "build_runtime_metrics_snapshot": (".metrics", "build_runtime_metrics_snapshot"),
    "default_runtime_ops_health_metadata": (".health", "default_runtime_ops_health_metadata"),
    "scheduler_executor_health_projection": (".health", "scheduler_executor_health_projection"),
    "safe_metadata": (".safety", "safe_metadata"),
}


def __getattr__(name: str) -> object:
    if name not in _LAZY_EXPORTS:
        # Test-isolation fallback: when a sibling test pops this package out of
        # ``sys.modules`` and a later submodule import re-creates a fresh parent
        # package object, submodules that were previously bound as attributes
        # are gone. Re-import the real submodule on demand so attribute access
        # (e.g. ``magi_agent.ops.child_governed_collector``) stays order/worker
        # independent. Never import eagerly; skip dunder/private names.
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


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
