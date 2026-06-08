from __future__ import annotations

import os
from typing import Literal


def _truthy_env(name: str) -> bool:
    value = os.environ.get(name, "").strip().lower()
    return value in {"1", "true", "yes", "on"}


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
    readiness_execution_mode: Literal["disabled", "shadow", "live"] | None = None,
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
    kill_switch_enabled = _truthy_env("MAGI_SCHEDULER_KILL_SWITCH_ENABLED")
    live_authorized = (
        executor_enabled
        and not cfg.shadow
        and not kill_switch_enabled
        and readiness_execution_mode == "live"
    )
    # shadow_enabled: only meaningful (and True) when executor is enabled.
    shadow_enabled: bool = (
        (cfg.shadow or kill_switch_enabled or not live_authorized)
        if executor_enabled
        else False
    )

    if not executor_enabled:
        status = "disabled"
    elif shadow_enabled:
        status = "shadow"
    else:
        status = "live"

    projection: dict[str, object] = {
        "executorEnabled": executor_enabled,
        "shadowEnabled": shadow_enabled,
        "killSwitchEnabled": kill_switch_enabled,
        "status": status,
        "liveExecutionAllowed": live_authorized,
    }

    if tick_summary is not None:
        # Merge caller-supplied tick summary (do not overwrite core fields).
        for key, value in tick_summary.items():
            if key not in projection:
                projection[key] = value

    return projection


def gateway_daemon_health_projection(
    *,
    watcher_states: dict[str, dict[str, object]] | None = None,
) -> dict[str, object]:
    """Return a redacted health projection for the Track-F gateway daemon.

    Pure projection — reports state WITHOUT enabling anything.  ``daemonEnabled``
    is derived from ``MAGI_GATEWAY_DAEMON_ENABLED`` (the same gate the daemon
    reads) so the surface and the runtime can never diverge.

    Per-watcher states are surfaced under ``watchers`` keyed by watcher name.
    Only the whitelisted fields ``state`` and ``restarts`` survive per watcher —
    any other key (e.g. a stray token) is dropped so no raw secret leaks into the
    health surface (redaction invariant).

    A convenience ``cronTicker`` field mirrors the ``scheduler_cron`` watcher's
    state (or ``"absent"`` when no cron watcher is registered) so dashboards can
    surface cron-ticker health without parsing the watcher map.

    Args:
        watcher_states: Map of ``watcher_name -> {state, restarts, ...}``.  The
            extra keys are intentionally ignored (redaction).
    """
    # Lazy import: avoids circular import (gateway.daemon lazily imports this
    # module in health_projection()) and keeps health.py's top-level graph clean.
    from magi_agent.gateway.daemon import is_gateway_daemon_enabled

    enabled = is_gateway_daemon_enabled()
    raw_states = watcher_states or {}

    watchers: dict[str, dict[str, object]] = {}
    for name, raw in raw_states.items():
        state = raw.get("state", "unknown") if isinstance(raw, dict) else "unknown"
        restarts = raw.get("restarts", 0) if isinstance(raw, dict) else 0
        watchers[name] = {"state": state, "restarts": restarts}

    cron = watchers.get("scheduler_cron")
    cron_ticker = {"state": cron["state"]} if cron is not None else {"state": "absent"}

    if not enabled:
        status = "disabled"
    elif any(w["state"] == "running" for w in watchers.values()):
        status = "running"
    elif watchers and all(w["state"] in {"failed", "disabled"} for w in watchers.values()):
        status = "degraded"
    else:
        status = "idle"

    return {
        "schemaVersion": "openmagi.ops.gateway.health.v1",
        "daemonEnabled": enabled,
        "status": status,
        "watchers": watchers,
        "cronTicker": cron_ticker,
    }


__all__ = [
    "default_runtime_ops_health_metadata",
    "gateway_daemon_health_projection",
    "scheduler_executor_health_projection",
]
