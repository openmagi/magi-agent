"""Operator wiring: tie a concrete channel provider to a gateway poll watcher.

This is the composition layer the daemon's ``build_default_watchers`` calls to
add live channel watchers.  It is the ONLY place that:

  * reads the per-channel live gate (``MAGI_CHANNEL_LIVE_TELEGRAM``) and the
    bot token (``MAGI_TELEGRAM_BOT_TOKEN``) from the environment, and
  * constructs the concrete (network-bearing) provider.

Fail-closed discipline
----------------------
``build_telegram_channel_watcher`` returns ``None`` unless BOTH the live gate is
on AND a token is present.  When the gate is on but the token is absent it logs
an explicit warning and returns ``None`` (no provider, no watcher) — never a
half-configured live path.

Self-host only
--------------
The hosted deployment already runs a separate ``TelegramPoller`` in chat-proxy
(getUpdates long-poll).  Running this daemon channel watcher alongside it would
cause Telegram 409 conflicts on ``getUpdates``.  This watcher is therefore for
SELF-HOST deployments only; do not enable it on the hosted path.  Startup calls
``delete_webhook`` once to clear any stale webhook left by an onboarding flow.

Import-clean: this module imports the concrete httpx provider lazily (inside the
default factory) so importing the wiring does not pull ``httpx`` until an
operator actually builds the live watcher.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any

from magi_agent.channels.telegram_adapter import TelegramInboundUpdate
from magi_agent.channels.telegram_live import (
    TelegramLivePollState,
    is_live_telegram_enabled,
)
from magi_agent.gateway.daemon import GatewayWatcher
from magi_agent.gateway.watchers import build_channel_poll_watcher
from magi_agent.harness.scheduler_delivery import is_silent_output

_log = logging.getLogger(__name__)

# Per-cycle poll interval (seconds) between getUpdates cycles.  The provider's
# own long-poll timeout dominates latency; this is the gap after a cycle errors.
DEFAULT_TELEGRAM_POLL_INTERVAL_SECONDS = 1.0

ProviderFactory = Callable[[str], Any]
OnInbound = Callable[[TelegramInboundUpdate], None]


# ---------------------------------------------------------------------------
# Token / provider construction (fail-closed)
# ---------------------------------------------------------------------------

def _telegram_bot_token_from_env() -> str | None:
    """Return the configured bot token or None (never logs the value)."""
    for key in ("MAGI_TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_TOKEN"):
        raw = os.environ.get(key, "")
        if raw.strip():
            return raw.strip()
    return None


def _default_telegram_provider_factory(token: str) -> Any:
    """Construct the concrete httpx provider (lazy import keeps wiring clean)."""
    from magi_agent.channels.providers.telegram_httpx import TelegramHttpxProvider

    return TelegramHttpxProvider(token=token)


# ---------------------------------------------------------------------------
# poll_once closure
# ---------------------------------------------------------------------------

def build_telegram_poll_once(
    *,
    provider: Any,
    on_inbound: OnInbound,
    state: TelegramLivePollState | None = None,
) -> Callable[[], int]:
    """Build a single-cycle poll closure over an injected provider.

    The first invocation deletes any stale webhook (one-time startup action).
    Each cycle calls ``provider.poll_updates`` with the current offset,
    normalises raw updates into ``TelegramInboundUpdate``s, deduplicates against
    the shared poll state, advances the offset, and calls ``on_inbound`` per new
    message.  Returns the count of newly dispatched messages.

    No agent turn is started here — ``on_inbound`` is the turn-dispatch seam.
    """
    poll_state = state or TelegramLivePollState()
    started = {"webhook_cleared": False}

    def _normalise(raw: list[Any]) -> list[TelegramInboundUpdate]:
        normaliser = getattr(provider, "normalise_updates", None)
        if callable(normaliser):
            return list(normaliser(raw))
        # Fallback: project via the shared boundary helper.
        from magi_agent.channels.telegram_adapter import _project_update

        out: list[TelegramInboundUpdate] = []
        for update in raw:
            projected = _project_update(update)
            if projected is not None:
                out.append(projected)
        return out

    def poll_once() -> int:
        if not started["webhook_cleared"]:
            try:
                provider.delete_webhook()
            except Exception:  # noqa: BLE001 — startup webhook clear is best-effort
                _log.warning("telegram delete_webhook failed on startup", exc_info=True)
            started["webhook_cleared"] = True

        class _PollReq:
            offset = poll_state.offset

        raw = list(provider.poll_updates(_PollReq()))
        new_count = 0
        max_update_id = poll_state.offset - 1
        for raw_update, inbound in zip(raw, _normalise(raw), strict=False):
            uid = raw_update.get("update_id") if isinstance(raw_update, dict) else None
            if isinstance(uid, int):
                max_update_id = max(max_update_id, uid)
                if poll_state.is_seen(uid):
                    continue
                poll_state.mark_seen(uid)
            on_inbound(inbound)
            new_count += 1
        # Advance offset past the highest update_id seen this cycle.
        if max_update_id >= poll_state.offset:
            poll_state.advance(max_update_id + 1)
        return new_count

    return poll_once


# ---------------------------------------------------------------------------
# Live outbound deliver (records receipt — honest, in the wiring layer)
# ---------------------------------------------------------------------------

def live_deliver(
    provider: Any,
    chat_id: str,
    text: str,
    *,
    receipt: dict[str, object] | None = None,
    reply_to_message_id: str | None = None,
) -> bool:
    """Send ``text`` to ``chat_id`` via the injected live provider.

    Records a truthful receipt (``provider_called`` / ``channel_delivery_performed``)
    in ``receipt`` — this is the live wiring layer, NOT the audit boundary, so it
    may honestly report that a real send occurred.  Respects the ``[SILENT]``
    contract (exact match suppresses the send) and the live gate.

    Returns True if the message was sent or intentionally suppressed; False if
    gated off or the send errored.
    """
    rcpt = receipt if receipt is not None else {}
    rcpt.setdefault("provider_called", False)
    rcpt.setdefault("channel_delivery_performed", False)
    rcpt.setdefault("suppressed", False)

    if not is_live_telegram_enabled():
        rcpt["skipped"] = True
        rcpt["skip_reason"] = "gate_off"
        return False

    if is_silent_output(text):
        rcpt["suppressed"] = True
        rcpt["suppress_reason"] = "silent_marker"
        return True

    class _SendReq:
        def __init__(self) -> None:
            self.chat_id = chat_id
            self.text = text
            self.reply_to_message_id = reply_to_message_id

    try:
        result = provider.send_message(_SendReq())
    except Exception:  # noqa: BLE001 — transient send failure is reported, not raised
        _log.warning("telegram live send failed", exc_info=True)
        rcpt["error"] = "send_failed"
        return False

    rcpt["provider_called"] = bool(getattr(provider, "provider_called", True))
    rcpt["channel_delivery_performed"] = True
    pmid = result.get("providerMessageId") if isinstance(result, dict) else None
    rcpt["provider_message_id_present"] = pmid is not None
    return True


# ---------------------------------------------------------------------------
# Watcher builder (fail-closed)
# ---------------------------------------------------------------------------

def build_telegram_channel_watcher(
    *,
    provider_factory: ProviderFactory | None = None,
    on_inbound: OnInbound | None = None,
    interval_seconds: float = DEFAULT_TELEGRAM_POLL_INTERVAL_SECONDS,
) -> GatewayWatcher | None:
    """Build the live Telegram channel watcher, or None if not fully configured.

    Returns None (fail-closed) when:
      * the live gate ``MAGI_CHANNEL_LIVE_TELEGRAM`` is off, OR
      * no bot token is configured (logged explicitly).

    Otherwise constructs the concrete provider via ``provider_factory`` (default:
    the httpx provider) and wraps a ``poll_once`` closure in
    ``build_channel_poll_watcher`` (whose gate is ``is_live_telegram_enabled``).
    """
    if not is_live_telegram_enabled():
        return None

    token = _telegram_bot_token_from_env()
    if token is None:
        _log.warning(
            "telegram live gate is ON but no bot token is configured "
            "(set MAGI_TELEGRAM_BOT_TOKEN) — skipping telegram channel watcher"
        )
        return None

    factory = provider_factory or _default_telegram_provider_factory
    provider = factory(token)
    dispatch = on_inbound or _default_on_inbound
    poll_once = build_telegram_poll_once(provider=provider, on_inbound=dispatch)

    return build_channel_poll_watcher(
        channel_type="telegram",
        poll_once=poll_once,
        is_enabled=is_live_telegram_enabled,
        interval_seconds=interval_seconds,
    )


def _default_on_inbound(update: TelegramInboundUpdate) -> None:
    """Default inbound sink — logs receipt only (turn dispatch wired by operator).

    The full turn-dispatch path (channels.dispatcher → agent turn) is injected by
    the operator wiring; with no dispatcher provided we record an audit log line
    (chat-id digest only, never raw text) so the watcher is observable.
    """
    _log.info(
        "telegram inbound update received (chat digest only)",
        extra={"messageId": update.message_id},
    )


__all__ = [
    "DEFAULT_TELEGRAM_POLL_INTERVAL_SECONDS",
    "build_telegram_channel_watcher",
    "build_telegram_poll_once",
    "live_deliver",
]
