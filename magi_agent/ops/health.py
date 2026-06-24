from __future__ import annotations

import os
from collections.abc import Mapping
from math import isfinite
from typing import Literal

from magi_agent.config._truthy import env_bool
from magi_agent.ops.safety import reject_private_text, require_safe_key


def _truthy_env(name: str) -> bool:
    """Strict-allowlist env reader (I-2 PR A wrapper).

    Kept as a thin public alias because several modules import this name
    (``transport.sse``, ``transport.streaming_chat_route``, ``adk_bridge.
    event_adapter``, ``shadow.gate5b4c3_live_runner_boundary``). Body now
    delegates to :func:`magi_agent.config._truthy.env_bool` so the canonical
    truthy set lives in one place; behaviour is byte-identical.
    """
    return env_bool(os.environ, name, default=False)


def _safe_health_value(value: object) -> tuple[bool, object]:
    """A-11 lenient health-surface value scrub.

    Returns ``(ok, scrubbed_value)``. ``ok=False`` means the caller should
    drop the key. Bools, finite numerics, secret-clean strings, and
    tuples/lists composed of safe primitives pass through verbatim. Anything
    that hits the C-1 secret denylist or falls outside the recognized
    primitive set is rejected. Fail-OPEN against the ``safe_ref``/``safe_key``
    *format* check (the health surface is not a typed governance artifact)
    and fail-CLOSED against the secret denylist (the surface MUST NOT leak).

    Once C-2 lands the canonical lenient ``safe_dimensions``-style helper
    this thin wrapper can be replaced by it.
    """
    if isinstance(value, bool):
        return True, value
    if isinstance(value, int):  # bool is a bool subclass — handled above first.
        return True, value
    if isinstance(value, float):
        if not isfinite(value):
            return False, None
        return True, value
    if isinstance(value, str):
        try:
            reject_private_text(value, field_name="tickSummary")
        except ValueError:
            return False, None
        return True, value
    if isinstance(value, tuple | list):
        scrubbed: list[object] = []
        for item in value:
            ok, item_scrubbed = _safe_health_value(item)
            if not ok:
                return False, None
            scrubbed.append(item_scrubbed)
        return True, tuple(scrubbed)
    return False, None


def _safe_tick_summary(tick: Mapping[str, object]) -> dict[str, object]:
    """A-11 lenient health-surface tick-summary scrub.

    Drops keys that violate ``require_safe_key``; drops values that fail the
    lenient value scrub. Never raises — a noisy tick must not crash the
    publicly-projected health surface.
    """
    safe: dict[str, object] = {}
    for key, value in tick.items():
        if not isinstance(key, str):
            continue
        try:
            safe_key = require_safe_key(key, field_name="tickSummary")
        except ValueError:
            continue
        ok, scrubbed = _safe_health_value(value)
        if ok:
            safe[safe_key] = scrubbed
    return safe


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
    # I-1: route the registered flag through ``flag_bool`` for typed
    # discoverability; byte-identical to ``_truthy_env`` because the
    # registered default is ``False``.
    from magi_agent.config.flags import flag_bool  # noqa: PLC0415

    kill_switch_enabled = flag_bool("MAGI_SCHEDULER_KILL_SWITCH_ENABLED")
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
        # A-11: route tick rows through the lenient health-surface scrub so
        # a noisy caller cannot leak credential-shaped values into the
        # publicly-projected health dict. Core keys still win on collision.
        safe_tick = _safe_tick_summary(tick_summary)
        for key, value in safe_tick.items():
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
