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
    "RuntimeOperationEvent": (".runtime_events", "RuntimeOperationEvent"),
    "RuntimeOpsAttachmentFlags": (".metrics", "RuntimeOpsAttachmentFlags"),
    "build_runtime_metrics_snapshot": (".metrics", "build_runtime_metrics_snapshot"),
    "default_runtime_ops_health_metadata": (".health", "default_runtime_ops_health_metadata"),
    "scheduler_executor_health_projection": (".health", "scheduler_executor_health_projection"),
    "safe_metadata": (".safety", "safe_metadata"),
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
