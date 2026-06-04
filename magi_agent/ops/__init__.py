from __future__ import annotations

from importlib import import_module

__all__ = [
    "InMemoryRuntimeOpsRecorder",
    "InMemoryOpsRecorder",
    "RuntimeMetricRecord",
    "RuntimeMetricsSnapshot",
    "RuntimeOperationEvent",
    "RuntimeOperationReceipt",
    "RuntimeOpsAttachmentFlags",
    "RuntimeTraceSnapshot",
    "build_runtime_metrics_snapshot",
    "build_runtime_trace_snapshot",
    "default_runtime_ops_health_metadata",
    "project_runtime_operation_event",
    "scheduler_executor_health_projection",
    "safe_metadata",
]

_LAZY_EXPORTS = {
    "InMemoryRuntimeOpsRecorder": (".recorder", "InMemoryRuntimeOpsRecorder"),
    "InMemoryOpsRecorder": (".recorder", "InMemoryOpsRecorder"),
    "RuntimeMetricRecord": (".metrics", "RuntimeMetricRecord"),
    "RuntimeMetricsSnapshot": (".metrics", "RuntimeMetricsSnapshot"),
    "RuntimeOperationEvent": (".runtime_events", "RuntimeOperationEvent"),
    "RuntimeOperationReceipt": (".recorder", "RuntimeOperationReceipt"),
    "RuntimeOpsAttachmentFlags": (".metrics", "RuntimeOpsAttachmentFlags"),
    "RuntimeTraceSnapshot": (".traces", "RuntimeTraceSnapshot"),
    "build_runtime_metrics_snapshot": (".metrics", "build_runtime_metrics_snapshot"),
    "build_runtime_trace_snapshot": (".traces", "build_runtime_trace_snapshot"),
    "default_runtime_ops_health_metadata": (".health", "default_runtime_ops_health_metadata"),
    "project_runtime_operation_event": (".runtime_events", "project_runtime_operation_event"),
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
