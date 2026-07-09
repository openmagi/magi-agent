"""U4 -- Heartbeat watcher: periodic quiet self-check agent turn.

A heartbeat turn is a lightweight agent call (no tools, no memory commit) on a
configurable interval.  The agent is asked to confirm system nominal.  If the
reply contains the suppress token (default ``HEARTBEAT_OK``), the output is
dropped silently.  If the reply does NOT contain the token, the output is
delivered via the injected ``deliver`` sink (log warning by default) so the
operator knows the agent flagged something.

Import purity: no real engine, no ADK, no network is constructed here.  The
engine callable and deliver sink are injected; the default engine is the governed
turn engine imported lazily inside the loop so this module stays import-clean.

Gates and config
----------------
MAGI_HEARTBEAT_ENABLED              bool, default OFF (strict bool).
MAGI_HEARTBEAT_INTERVAL_SECONDS     int, default 1800, floor 60.
MAGI_HEARTBEAT_SUPPRESS_TOKEN       str, default "HEARTBEAT_OK".

All three are registered in ``magi_agent.config.flags``.

Additive-only: with the gate OFF the watcher is included in
``build_default_watchers()`` but ``is_enabled()`` returns False, so the daemon
never starts it.  Byte-identical to the pre-U4 watcher list when the flag is
unset.
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable

from magi_agent.gateway.daemon import GatewayWatcher

_log = logging.getLogger(__name__)

# Default heartbeat prompt.  Deliberately minimal and deterministic.
_DEFAULT_HEARTBEAT_PROMPT = (
    "Heartbeat self-check: confirm system status nominal. "
    "If all is well, reply with only HEARTBEAT_OK. "
    "If you detect any concern worth escalating, describe it briefly."
)

_HEARTBEAT_INTERVAL_DEFAULT = 1800
_HEARTBEAT_INTERVAL_FLOOR = 60
_HEARTBEAT_SUPPRESS_TOKEN_DEFAULT = "HEARTBEAT_OK"


# ---------------------------------------------------------------------------
# Config readers
# ---------------------------------------------------------------------------

def is_heartbeat_enabled(env: dict[str, str] | None = None) -> bool:
    """Return True iff MAGI_HEARTBEAT_ENABLED is set and truthy."""
    from magi_agent.config.flags import flag_bool  # noqa: PLC0415

    return flag_bool("MAGI_HEARTBEAT_ENABLED", env=env)


def heartbeat_interval_seconds(env: dict[str, str] | None = None) -> int:
    """Return the configured heartbeat interval in seconds.

    Reads MAGI_HEARTBEAT_INTERVAL_SECONDS.  Falls back to 1800 on missing or
    invalid values.  Values below the 60-second floor are clamped to 60.
    """
    _env = env if env is not None else os.environ
    raw = _env.get("MAGI_HEARTBEAT_INTERVAL_SECONDS")
    if raw is None:
        return _HEARTBEAT_INTERVAL_DEFAULT
    try:
        value = int(raw.strip())
    except (ValueError, AttributeError):
        return _HEARTBEAT_INTERVAL_DEFAULT
    return max(_HEARTBEAT_INTERVAL_FLOOR, value)


def heartbeat_suppress_token(env: dict[str, str] | None = None) -> str:
    """Return the configured suppression token (default HEARTBEAT_OK)."""
    _env = env if env is not None else os.environ
    raw = _env.get("MAGI_HEARTBEAT_SUPPRESS_TOKEN", "").strip()
    return raw if raw else _HEARTBEAT_SUPPRESS_TOKEN_DEFAULT


# ---------------------------------------------------------------------------
# Tick primitive (testable without a full watcher)
# ---------------------------------------------------------------------------

async def _run_heartbeat_tick(
    *,
    engine: Callable[[str], Awaitable[str]],
    suppress_token: str,
    deliver: Callable[[str], None],
    prompt: str = _DEFAULT_HEARTBEAT_PROMPT,
) -> None:
    """Run one heartbeat turn and deliver the output if not suppressed.

    Best-effort: any exception from the engine is caught and logged; it must
    never propagate (the watcher loop must stay alive).
    """
    try:
        output = await engine(prompt)
    except Exception:  # noqa: BLE001 - heartbeat is best-effort
        _log.warning("heartbeat engine tick failed", exc_info=True)
        return

    if suppress_token in output:
        _log.debug("heartbeat: nominal (suppressed)")
        return

    _log.warning("heartbeat: non-nominal output received -- forwarding to deliver sink")
    try:
        deliver(output)
    except Exception:  # noqa: BLE001 - delivery error must not kill the tick
        _log.warning("heartbeat: deliver sink raised", exc_info=True)


# ---------------------------------------------------------------------------
# Default engine: the governed turn engine (lazy import)
# ---------------------------------------------------------------------------

async def _default_engine(prompt: str) -> str:
    """Drive one governed turn with the heartbeat prompt and return the reply."""
    import uuid  # noqa: PLC0415

    from magi_agent.runtime.governed_turn import run_governed_turn  # noqa: PLC0415
    from magi_agent.runtime.turn_context import TurnContext  # noqa: PLC0415
    from magi_agent.runtime.child_governed_collector import (  # noqa: PLC0415
        collect_governed_child_turn,
    )

    ctx = TurnContext(
        prompt=prompt,
        session_id="heartbeat:local",
        turn_id=uuid.uuid4().hex,
        memory_mode="normal",
    )
    summary, _refs, _status = await collect_governed_child_turn(run_governed_turn(ctx))
    return summary


def _default_deliver(output: str) -> None:
    """Log the non-suppressed heartbeat output as a warning."""
    _log.warning("heartbeat non-nominal output: %s", output[:500])


# ---------------------------------------------------------------------------
# Watcher builder
# ---------------------------------------------------------------------------

def build_heartbeat_watcher(
    *,
    interval_seconds: float | None = None,
    engine: Callable[[str], Awaitable[str]] | None = None,
    deliver: Callable[[str], None] | None = None,
) -> GatewayWatcher:
    """Build the heartbeat watcher for inclusion in the gateway daemon fleet.

    Parameters
    ----------
    interval_seconds:
        Override for the tick interval (for test injection only).  Production
        code leaves this ``None`` and the interval is read from
        ``MAGI_HEARTBEAT_INTERVAL_SECONDS`` at each tick via
        ``heartbeat_interval_seconds()``.
    engine:
        Async callable ``(prompt: str) -> str``.  Defaults to
        ``_default_engine`` (the governed turn engine) when ``None``.
    deliver:
        Sync callable called with the output string when it is NOT suppressed.
        Defaults to ``_default_deliver`` (warning log) when ``None``.

    Gate
    ----
    ``is_enabled`` returns ``is_heartbeat_enabled()`` evaluated fresh each
    call (reads from ``os.environ`` at runtime).  With the gate OFF the daemon
    never starts this watcher; the fleet is byte-identical to pre-U4.
    """
    _engine = engine if engine is not None else _default_engine
    _deliver = deliver if deliver is not None else _default_deliver

    async def run(stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            # Evaluate interval fresh each tick so env changes are respected.
            tick_interval = interval_seconds if interval_seconds is not None else float(heartbeat_interval_seconds())
            token = heartbeat_suppress_token()
            await _run_heartbeat_tick(
                engine=_engine,
                suppress_token=token,
                deliver=_deliver,
            )
            if stop_event.is_set():
                break
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=tick_interval)
            except asyncio.TimeoutError:
                continue

    return GatewayWatcher(
        name="heartbeat",
        run=run,
        is_enabled=is_heartbeat_enabled,
    )


__all__ = [
    "build_heartbeat_watcher",
    "heartbeat_interval_seconds",
    "heartbeat_suppress_token",
    "is_heartbeat_enabled",
    "_run_heartbeat_tick",
]
