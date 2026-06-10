"""Guard test: the dead ops trace stack must stay deleted (16-PR1).

These ops/ submodules had zero non-test consumers or were folded into metrics,
collapsing runtime ops onto the single ``telemetry/`` trace seam:

  - magi_agent.ops.recorder
  - magi_agent.ops.traces
  - magi_agent.ops.contracts
  - magi_agent.ops.scheduler_metrics
  - magi_agent.ops.runtime_events

The live ops surface (safety/health/metrics/job_queue/otel_noise) is preserved;
this test asserts only that the dead modules and their lazy re-exports are gone,
so they cannot silently reappear.
"""

from __future__ import annotations

import importlib

import pytest


DEAD_OPS_MODULES = (
    "magi_agent.ops.recorder",
    "magi_agent.ops.traces",
    "magi_agent.ops.contracts",
    "magi_agent.ops.scheduler_metrics",
    "magi_agent.ops.runtime_events",
)

# Symbols that used to be lazy re-exported from magi_agent.ops via the dead
# modules. They must no longer resolve off the package namespace.
DEAD_OPS_REEXPORTS = (
    "InMemoryRuntimeOpsRecorder",
    "InMemoryOpsRecorder",
    "RuntimeOperationReceipt",
    "RuntimeTraceSnapshot",
    "build_runtime_trace_snapshot",
)

# Live re-exports that must keep resolving (regression guard for preserved ops
# surface; runtime operation events now live in metrics).
LIVE_OPS_REEXPORTS = (
    "RuntimeMetricRecord",
    "RuntimeMetricsSnapshot",
    "RuntimeOperationEvent",
    "RuntimeOpsAttachmentFlags",
    "build_runtime_metrics_snapshot",
    "default_runtime_ops_health_metadata",
    "safe_metadata",
)


@pytest.mark.parametrize("module_name", DEAD_OPS_MODULES)
def test_dead_ops_module_is_gone(module_name: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_name)


@pytest.mark.parametrize("symbol", DEAD_OPS_REEXPORTS)
def test_dead_ops_reexport_is_gone(symbol: str) -> None:
    ops = importlib.import_module("magi_agent.ops")
    with pytest.raises(AttributeError):
        getattr(ops, symbol)
    assert symbol not in ops.__all__


@pytest.mark.parametrize("symbol", LIVE_OPS_REEXPORTS)
def test_live_ops_reexport_still_resolves(symbol: str) -> None:
    ops = importlib.import_module("magi_agent.ops")
    assert getattr(ops, symbol) is not None
    assert symbol in ops.__all__
