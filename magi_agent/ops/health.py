from __future__ import annotations

import os

_TRUE_STRINGS = frozenset({"1", "true", "yes", "on"})
_FALSE_STRINGS = frozenset({"0", "false", "no", "off"})


def default_runtime_ops_health_metadata() -> dict[str, object]:
    return {
        "schemaVersion": "openmagi.ops.health.v1",
        "enabled": False,
        "source": "local_in_memory",
        "publicProjectionAvailable": True,
        "liveToolExecutionAttached": False,
        "productionStorageAttached": False,
        "productionQueueAttached": False,
    }


def scheduler_executor_health_projection(
    *,
    tick_summary: dict[str, object] | None = None,
) -> dict[str, object]:
    """Return a health projection for the OSS scheduler executor.

    Reports state WITHOUT enabling anything — a pure projection layer.  All
    values are derived from environment variables (same source as
    ``JobExecutionConfig.from_env()`` in scheduler_job_execution.py) so that
    the health surface reflects the actual runtime configuration without
    importing the harness (boundary isolation).

    When ``tick_summary`` is provided the last-tick counts (fired, suppressed,
    skipped, etc.) are merged into the projection.  When absent those fields
    are omitted so the caller can distinguish "never ticked" from "ticked with
    zero counts".

    Args:
        tick_summary: Optional dict with last-tick stats. Expected keys:
            lastTickUtcIso (str), fired (int), suppressed_silent (int),
            skipped (int), timed_out (int), lease_rejected (int).
    """
    executor_enabled = os.environ.get("MAGI_SCHEDULER_EXECUTOR_ENABLED", "").lower() in _TRUE_STRINGS
    # Shadow: mirrors _env_flag("MAGI_SCHEDULER_SHADOW", default=True) in
    # scheduler_job_execution.JobExecutionConfig.from_env() exactly.
    # When the env var is absent → default True (shadow-first).
    # When present → truthy only if in _TRUE_STRINGS; any other value (incl.
    # garbage like "xyz") → False (same as _env_flag's raw.strip().lower() check).
    shadow_raw = os.environ.get("MAGI_SCHEDULER_SHADOW")
    shadow_enabled: bool
    if executor_enabled:
        if shadow_raw is None:
            shadow_enabled = True  # default=True matches _env_flag default
        else:
            shadow_enabled = shadow_raw.strip().lower() in _TRUE_STRINGS
    else:
        shadow_enabled = False

    if not executor_enabled:
        status = "disabled"
    elif shadow_enabled:
        status = "shadow"
    else:
        status = "live"

    projection: dict[str, object] = {
        "executorEnabled": executor_enabled,
        "shadowEnabled": shadow_enabled,
        "status": status,
        # Authority is always False in this projection layer.
        "liveExecutionAllowed": False,
    }

    if tick_summary is not None:
        # Merge caller-supplied tick summary (do not overwrite core fields).
        for key, value in tick_summary.items():
            if key not in projection:
                projection[key] = value

    return projection


__all__ = ["default_runtime_ops_health_metadata", "scheduler_executor_health_projection"]
