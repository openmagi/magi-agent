from __future__ import annotations


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
    values are derived from ``JobExecutionConfig.from_env()`` (lazy import) so
    that the health surface and the execution config can NEVER diverge — a single
    source of truth for executor-enabled and shadow-enabled resolution.

    When ``tick_summary`` is provided the last-tick counts (fired, suppressed,
    skipped, etc.) are merged into the projection.  When absent those fields
    are omitted so the caller can distinguish "never ticked" from "ticked with
    zero counts".

    Args:
        tick_summary: Optional dict with last-tick stats. Expected keys:
            lastTickUtcIso (str), fired (int), suppressed_silent (int),
            skipped (int), timed_out (int), lease_rejected (int).
    """
    # Lazy import: preserves boundary isolation at top-level while guaranteeing
    # that health and config use the exact same env-resolution logic.
    from magi_agent.harness.scheduler_job_execution import JobExecutionConfig

    cfg = JobExecutionConfig.from_env()
    executor_enabled: bool = cfg.executor_enabled
    # shadow_enabled: only meaningful (and True) when executor is enabled.
    shadow_enabled: bool = cfg.shadow if executor_enabled else False

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
