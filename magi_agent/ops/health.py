from __future__ import annotations

import os

_TRUE_STRINGS = frozenset({"1", "true", "yes", "on"})


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
    # Shadow default: ON when executor is enabled but shadow env not explicitly off.
    shadow_raw = os.environ.get("MAGI_SCHEDULER_SHADOW", "")
    shadow_enabled: bool
    if executor_enabled:
        # Shadow is on unless explicitly set to "0" / "false" / "no" / "off".
        shadow_enabled = shadow_raw.lower() not in {"0", "false", "no", "off"}
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
