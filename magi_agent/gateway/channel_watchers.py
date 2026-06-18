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

import asyncio
import importlib.util
import logging
import os
from collections.abc import Callable
from typing import Any

from magi_agent.channels.discord_adapter import (
    DiscordEventRequest,
    DiscordProviderSendRequest,
    _project_event,
)
from magi_agent.channels.discord_live import (
    DiscordLiveEventState,
    _message_dedup_hash,
    is_live_discord_enabled,
)
from magi_agent.channels.discord_live import (
    to_channel_inbound as discord_to_channel_inbound,
)
from magi_agent.channels.slack_live import (
    _project_slack_event,
    is_live_slack_enabled,
)
from magi_agent.channels.telegram_adapter import TelegramInboundUpdate
from magi_agent.channels.telegram_live import (
    TelegramLivePollState,
    is_live_telegram_enabled,
    to_channel_inbound,
)
from magi_agent.channels.turn_bridge import (
    ChannelInbound,
    Deliver,
    RunTurn,
    make_inbound_handler,
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
# Dashboard-managed Telegram (hot-reload supervisor)
# ---------------------------------------------------------------------------

def is_dashboard_telegram_enabled(env: dict[str, str] | None = None) -> bool:
    """Master gate for dashboard-managed Telegram (default OFF).

    When ON, the daemon runs a long-lived supervisor watcher that picks up a
    token connected via the dashboard (stored in the vault) without a restart.
    Mutually exclusive with the legacy env-only ``build_telegram_channel_watcher``
    to avoid double-polling (Telegram 409 on getUpdates).
    """
    raw = (os.environ if env is None else env).get("MAGI_DASHBOARD_TELEGRAM_ENABLED", "")
    return raw.strip().lower() not in ("", "0", "false", "no", "off")


def _default_resolve_telegram_token() -> str | None:
    from magi_agent.channels.telegram_credentials import resolve_telegram_bot_token

    return resolve_telegram_bot_token()


class TelegramSupervisor:
    """Re-resolves the bot token each tick and runs/idles the poll loop.

    The token source (vault → env) is re-read on every :meth:`tick`. When a
    token appears the concrete provider is built and polled; when it changes the
    old provider is closed and rebuilt; when it disappears the provider is closed
    and the supervisor idles. This is what makes dashboard connect/disconnect
    take effect without restarting the gateway daemon.
    """

    def __init__(
        self,
        *,
        resolve_token: Callable[[], str | None],
        provider_factory: ProviderFactory | None = None,
        on_inbound: OnInbound | None = None,
        run_turn: RunTurn | None = None,
        poll_once_factory: Callable[[Any], Callable[[], Any]] | None = None,
    ) -> None:
        self._resolve_token = resolve_token
        self._provider_factory = provider_factory or _default_telegram_provider_factory
        # Stored raw (not coerced): the dispatcher is resolved per provider build
        # so a run_turn-backed bridge binds to the CURRENT (hot-reloaded) provider.
        self._on_inbound = on_inbound
        self._run_turn = run_turn
        self._poll_once_factory = poll_once_factory or self._build_poll_once
        self._token: str | None = None
        self._provider: Any | None = None
        self._poll_once: Callable[[], Any] | None = None

    def _build_poll_once(self, provider: Any) -> Callable[[], Any]:
        dispatch = _resolve_dispatch(
            provider=provider, on_inbound=self._on_inbound, run_turn=self._run_turn
        )
        return build_telegram_poll_once(provider=provider, on_inbound=dispatch)

    def tick(self) -> str:
        token = self._resolve_token()
        if token != self._token:
            self._teardown()
            self._token = token
            if token:
                self._provider = self._provider_factory(token)
                self._poll_once = self._poll_once_factory(self._provider)
        if self._poll_once is None:
            return "idle"
        self._poll_once()
        return "polled"

    def _teardown(self) -> None:
        provider = self._provider
        if provider is not None:
            close = getattr(provider, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001 — close failure must not wedge the loop
                    _log.warning("telegram provider close failed", exc_info=True)
        self._provider = None
        self._poll_once = None

    def close(self) -> None:
        self._teardown()
        self._token = None


def build_telegram_supervisor_watcher(
    *,
    resolve_token: Callable[[], str | None] | None = None,
    provider_factory: ProviderFactory | None = None,
    on_inbound: OnInbound | None = None,
    run_turn: RunTurn | None = None,
    interval_seconds: float = DEFAULT_TELEGRAM_POLL_INTERVAL_SECONDS,
) -> GatewayWatcher:
    """Long-lived supervisor watcher for dashboard-managed Telegram.

    Always startable (gated by ``is_dashboard_telegram_enabled``); the run loop
    idles until a token is resolvable, so it is safe to add even before any token
    is connected.
    """
    supervisor = TelegramSupervisor(
        resolve_token=resolve_token or _default_resolve_telegram_token,
        provider_factory=provider_factory,
        on_inbound=on_inbound,
        run_turn=run_turn,
    )

    async def run(stop_event: asyncio.Event) -> None:
        try:
            while not stop_event.is_set():
                try:
                    await asyncio.to_thread(supervisor.tick)
                except Exception:  # noqa: BLE001 — transient tick error must not stop loop
                    _log.warning("telegram supervisor tick failed", exc_info=True)
                if stop_event.is_set():
                    break
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
                except asyncio.TimeoutError:
                    continue
        finally:
            supervisor.close()

    return GatewayWatcher(
        name="channel_telegram",
        run=run,
        is_enabled=is_dashboard_telegram_enabled,
    )


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
# Turn-bridge wiring (inbound update -> agent turn -> live deliver)
# ---------------------------------------------------------------------------

def make_telegram_deliver(provider: Any) -> Deliver:
    """Adapt the telegram ``live_deliver`` to the shared bridge ``Deliver`` shape."""

    def deliver(channel_id: str, text: str, reply_to_message_id: str | None) -> bool:
        return live_deliver(
            provider, channel_id, text, reply_to_message_id=reply_to_message_id
        )

    return deliver


def build_telegram_bridge_on_inbound(
    *,
    provider: Any,
    run_turn: RunTurn,
    evidence: dict[str, object] | None = None,
) -> OnInbound:
    """Compose the full telegram inbound path: project the update into the shared
    ``ChannelInbound``, drive the (injected) turn, and deliver the reply via the
    live provider.  This replaces the log-only ``_default_on_inbound`` whenever an
    operator supplies a ``run_turn``."""
    handler = make_inbound_handler(
        channel_type="telegram",
        run_turn=run_turn,
        deliver=make_telegram_deliver(provider),
        evidence=evidence if evidence is not None else {},
    )

    def on_inbound(update: TelegramInboundUpdate) -> None:
        handler(to_channel_inbound(update))

    return on_inbound


def _resolve_dispatch(
    *,
    provider: Any,
    on_inbound: OnInbound | None,
    run_turn: RunTurn | None,
) -> OnInbound:
    """Pick the inbound dispatcher for a provider.

    Precedence: an explicit ``on_inbound`` (operator override) wins; otherwise a
    ``run_turn`` builds the full engine-backed bridge; otherwise we fall back to
    the log-only sink (no agent turn) so the watcher stays observable but inert.
    """
    if on_inbound is not None:
        return on_inbound
    if run_turn is not None:
        return build_telegram_bridge_on_inbound(provider=provider, run_turn=run_turn)
    return _default_on_inbound


# ---------------------------------------------------------------------------
# Watcher builder (fail-closed)
# ---------------------------------------------------------------------------

def build_telegram_channel_watcher(
    *,
    provider_factory: ProviderFactory | None = None,
    on_inbound: OnInbound | None = None,
    run_turn: RunTurn | None = None,
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
    dispatch = _resolve_dispatch(
        provider=provider, on_inbound=on_inbound, run_turn=run_turn
    )
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


# ---------------------------------------------------------------------------
# Discord live wiring (real provider; bypasses the fake-only boundary, exactly
# like the telegram real path — reuses discord_adapter._project_event for
# normalisation/redaction so no trust marker is abused).
# ---------------------------------------------------------------------------

DEFAULT_DISCORD_READ_INTERVAL_SECONDS = 1.0

_DiscordOnInbound = Callable[[Any], None]


def _discord_bot_token_from_env() -> str | None:
    """Return the configured Discord bot token or None (never logs the value).

    Vault first (dashboard-connected token, hot-reloaded each tick), then env
    (``MAGI_DISCORD_BOT_TOKEN`` / ``DISCORD_BOT_TOKEN``).
    """
    from magi_agent.channels.channel_credentials import resolve_channel_credential

    return resolve_channel_credential(
        service="discord",
        auth_scheme="bot_token",
        env_keys=("MAGI_DISCORD_BOT_TOKEN", "DISCORD_BOT_TOKEN"),
    )


def _default_discord_provider_factory(token: str) -> Any:
    """Construct the concrete gateway provider (lazy import keeps wiring clean)."""
    from magi_agent.channels.providers.discord_gateway import DiscordGatewayProvider

    return DiscordGatewayProvider(token=token)


def discord_live_deliver(
    provider: Any,
    channel_id: str,
    text: str,
    *,
    receipt: dict[str, object] | None = None,
    reply_to_message_id: str | None = None,
) -> bool:
    """Send ``text`` to ``channel_id`` via the injected live Discord provider.

    Real wiring layer (not the audit boundary): records a truthful receipt,
    respects the ``[SILENT]`` contract and the live gate.
    """
    rcpt = receipt if receipt is not None else {}
    rcpt.setdefault("provider_called", False)
    rcpt.setdefault("channel_delivery_performed", False)
    rcpt.setdefault("suppressed", False)

    if not is_live_discord_enabled():
        rcpt["skipped"] = True
        rcpt["skip_reason"] = "gate_off"
        return False

    if is_silent_output(text):
        rcpt["suppressed"] = True
        rcpt["suppress_reason"] = "silent_marker"
        return True

    request = DiscordProviderSendRequest(
        operation="send_message",
        requestId="live-discord-deliver",
        channelId=channel_id,
        text=text,
        replyToMessageId=reply_to_message_id,
    )
    try:
        result = provider.send_message(request)
    except Exception:  # noqa: BLE001 — transient send failure is reported, not raised
        _log.warning("discord live send failed", exc_info=True)
        rcpt["error"] = "send_failed"
        return False

    rcpt["provider_called"] = bool(getattr(provider, "provider_called", True))
    rcpt["channel_delivery_performed"] = True
    pmid = result.get("providerMessageId") if isinstance(result, dict) else None
    rcpt["provider_message_id_present"] = pmid is not None
    return True


def make_discord_deliver(provider: Any) -> Deliver:
    """Adapt ``discord_live_deliver`` to the shared bridge ``Deliver`` shape."""

    def deliver(channel_id: str, text: str, reply_to_message_id: str | None) -> bool:
        return discord_live_deliver(
            provider, channel_id, text, reply_to_message_id=reply_to_message_id
        )

    return deliver


def build_discord_bridge_on_inbound(
    *,
    provider: Any,
    run_turn: RunTurn,
    evidence: dict[str, object] | None = None,
) -> _DiscordOnInbound:
    """Compose the full Discord inbound path: project the event into the shared
    ``ChannelInbound``, drive the (injected) turn, and deliver the reply via the
    live provider."""
    handler = make_inbound_handler(
        channel_type="discord",
        run_turn=run_turn,
        deliver=make_discord_deliver(provider),
        evidence=evidence if evidence is not None else {},
    )

    def on_inbound(event: Any) -> None:
        handler(discord_to_channel_inbound(event))

    return on_inbound


def build_discord_read_once(
    *,
    provider: Any,
    on_inbound: _DiscordOnInbound,
    state: DiscordLiveEventState | None = None,
    bot_user_id: str | None = None,
) -> Callable[[], int]:
    """Build a single-cycle Discord read closure over an injected provider.

    Calls ``provider.read_events`` directly (NOT via the fake-only boundary),
    normalises each raw event with the shared ``_project_event`` (redaction +
    mention/DM filtering), deduplicates by ``(channel_id, message_id)`` and calls
    ``on_inbound`` per new event.  Returns the count of newly dispatched events.
    """
    read_state = state or DiscordLiveEventState()

    def read_once() -> int:
        if not is_live_discord_enabled():
            return 0
        request = DiscordEventRequest(
            requestId="live-discord-read",
            providerName="live-discord-provider",
            botIdDigest="live-bot",
            ownerIdDigest="live-owner",
            sessionKeyDigest="live-session",
            botUserId=bot_user_id,
        )
        new_count = 0
        for raw in provider.read_events(request):
            event = _project_event(raw, bot_user_id)
            if event is None:
                continue
            dedup_hash = _message_dedup_hash(event.channel_id, event.message_id)
            if read_state.is_seen(dedup_hash):
                continue
            read_state.mark_seen(dedup_hash)
            on_inbound(event)
            new_count += 1
        return new_count

    return read_once


def build_discord_channel_watcher(
    *,
    provider_factory: ProviderFactory | None = None,
    on_inbound: _DiscordOnInbound | None = None,
    run_turn: RunTurn | None = None,
    bot_user_id: str | None = None,
    interval_seconds: float = DEFAULT_DISCORD_READ_INTERVAL_SECONDS,
) -> GatewayWatcher | None:
    """Build the live Discord channel watcher, or None if not fully configured.

    Fail-closed: returns None when the ``MAGI_CHANNEL_LIVE_DISCORD`` gate is off
    or no bot token is configured (logged explicitly).
    """
    if not is_live_discord_enabled():
        return None

    token = _discord_bot_token_from_env()
    if token is None:
        _log.warning(
            "discord live gate is ON but no bot token is configured "
            "(set MAGI_DISCORD_BOT_TOKEN) — skipping discord channel watcher"
        )
        return None

    # Fail-closed when the optional extra is missing (only when using the default
    # gateway provider; an injected provider_factory may not need discord.py).
    if provider_factory is None and importlib.util.find_spec("discord") is None:
        _log.warning(
            "discord live gate is ON but the 'discord' extra is not installed "
            "(pip install magi-agent[discord]) — skipping discord channel watcher"
        )
        return None

    factory = provider_factory or _default_discord_provider_factory
    provider = factory(token)
    if on_inbound is not None:
        dispatch: _DiscordOnInbound = on_inbound
    elif run_turn is not None:
        dispatch = build_discord_bridge_on_inbound(provider=provider, run_turn=run_turn)
    else:
        dispatch = _default_discord_on_inbound
    read_once = build_discord_read_once(
        provider=provider, on_inbound=dispatch, bot_user_id=bot_user_id
    )

    return build_channel_poll_watcher(
        channel_type="discord",
        poll_once=read_once,
        is_enabled=is_live_discord_enabled,
        interval_seconds=interval_seconds,
    )


def _default_discord_on_inbound(event: Any) -> None:
    """Default inbound sink — logs receipt only (turn dispatch wired by operator)."""
    _log.info(
        "discord inbound event received (digest only)",
        extra={"messageId": getattr(event, "message_id", None)},
    )


# ---------------------------------------------------------------------------
# Slack live wiring (Socket Mode inbound + Web API outbound; bypasses the
# fake-only boundary like telegram/discord — projection is _project_slack_event).
# ---------------------------------------------------------------------------

DEFAULT_SLACK_READ_INTERVAL_SECONDS = 1.0

_SlackOnInbound = Callable[[ChannelInbound], None]


def _slack_app_token_from_env() -> str | None:
    """Slack app-level token (xapp-) for the Socket Mode websocket.

    Vault first (dashboard-connected), then env.
    """
    from magi_agent.channels.channel_credentials import resolve_channel_credential

    return resolve_channel_credential(
        service="slack",
        auth_scheme="app_token",
        env_keys=("MAGI_SLACK_APP_TOKEN", "SLACK_APP_TOKEN"),
    )


def _slack_bot_token_from_env() -> str | None:
    """Slack bot token (xoxb-) for outbound chat.postMessage.

    Vault first (dashboard-connected), then env.
    """
    from magi_agent.channels.channel_credentials import resolve_channel_credential

    return resolve_channel_credential(
        service="slack",
        auth_scheme="bot_token",
        env_keys=("MAGI_SLACK_BOT_TOKEN", "SLACK_BOT_TOKEN"),
    )


def slack_live_deliver(
    send_provider: Any,
    channel: str,
    text: str,
    *,
    thread_ts: str | None = None,
    receipt: dict[str, object] | None = None,
) -> bool:
    """Send ``text`` to ``channel`` (optionally in-thread) via the Web API provider."""
    rcpt = receipt if receipt is not None else {}
    rcpt.setdefault("provider_called", False)
    rcpt.setdefault("channel_delivery_performed", False)
    rcpt.setdefault("suppressed", False)

    if not is_live_slack_enabled():
        rcpt["skipped"] = True
        rcpt["skip_reason"] = "gate_off"
        return False

    if is_silent_output(text):
        rcpt["suppressed"] = True
        rcpt["suppress_reason"] = "silent_marker"
        return True

    kwargs: dict[str, Any] = {}
    if thread_ts:
        kwargs["thread_ts"] = thread_ts
    try:
        result = send_provider.send(channel=channel, text=text, **kwargs)
    except Exception:  # noqa: BLE001 — transient send failure is reported, not raised
        _log.warning("slack live send failed", exc_info=True)
        rcpt["error"] = "send_failed"
        return False

    rcpt["provider_called"] = bool(getattr(send_provider, "provider_called", True))
    ok = bool(result.get("ok", True)) if isinstance(result, dict) else True
    rcpt["channel_delivery_performed"] = ok
    return ok


def make_slack_deliver(send_provider: Any) -> Deliver:
    """Adapt ``slack_live_deliver`` to the shared bridge ``Deliver`` shape
    (``reply_to_message_id`` becomes the Slack ``thread_ts`` so replies thread)."""

    def deliver(channel_id: str, text: str, reply_to_message_id: str | None) -> bool:
        return slack_live_deliver(
            send_provider, channel_id, text, thread_ts=reply_to_message_id
        )

    return deliver


def build_slack_bridge_on_inbound(
    *,
    send_provider: Any,
    run_turn: RunTurn,
    evidence: dict[str, object] | None = None,
) -> _SlackOnInbound:
    """Bridge handler for Slack inbound (read_once already projects to
    ChannelInbound, so this is the make_inbound_handler directly)."""
    return make_inbound_handler(
        channel_type="slack",
        run_turn=run_turn,
        deliver=make_slack_deliver(send_provider),
        evidence=evidence if evidence is not None else {},
    )


def build_slack_read_once(
    *,
    provider: Any,
    on_inbound: _SlackOnInbound,
    seen: set[str] | None = None,
) -> Callable[[], int]:
    """Single-cycle Slack read closure: drain the Socket Mode queue, dedup by
    ``ts``, project each event with ``_project_slack_event`` and dispatch."""
    seen_ts: set[str] = seen if seen is not None else set()

    def read_once() -> int:
        if not is_live_slack_enabled():
            return 0
        new_count = 0
        for raw in provider.read_events():
            ts = raw.get("ts")
            if isinstance(ts, str):
                if ts in seen_ts:
                    continue
                seen_ts.add(ts)
                if len(seen_ts) > 1000:
                    for old in list(seen_ts)[:200]:
                        seen_ts.discard(old)
            inbound = _project_slack_event(raw)
            if inbound is None:
                continue
            on_inbound(inbound)
            new_count += 1
        return new_count

    return read_once


def build_slack_channel_watcher(
    *,
    read_provider: Any | None = None,
    send_provider: Any | None = None,
    on_inbound: _SlackOnInbound | None = None,
    run_turn: RunTurn | None = None,
    interval_seconds: float = DEFAULT_SLACK_READ_INTERVAL_SECONDS,
) -> GatewayWatcher | None:
    """Build the live Slack channel watcher, or None if not fully configured.

    Fail-closed: returns None when the gate is off, either token is missing, or
    the ``slack`` extra is absent.  Needs BOTH an app token (xapp-, Socket Mode
    inbound) and a bot token (xoxb-, outbound replies).
    """
    if not is_live_slack_enabled():
        return None

    app_token = _slack_app_token_from_env()
    bot_token = _slack_bot_token_from_env()
    if app_token is None or bot_token is None:
        _log.warning(
            "slack live gate is ON but tokens are missing (set MAGI_SLACK_APP_TOKEN "
            "for inbound + MAGI_SLACK_BOT_TOKEN for replies) — skipping slack watcher"
        )
        return None

    if read_provider is None and importlib.util.find_spec("slack_sdk") is None:
        _log.warning(
            "slack live gate is ON but the 'slack' extra is not installed "
            "(pip install magi-agent[slack]) — skipping slack channel watcher"
        )
        return None

    if read_provider is None:
        from magi_agent.channels.providers.slack_socketmode import SlackSocketModeProvider

        read_provider = SlackSocketModeProvider(app_token, bot_token=bot_token)
    if send_provider is None:
        from magi_agent.channels.providers.slack_urllib import SlackUrllibProvider

        send_provider = SlackUrllibProvider(token=bot_token)

    if on_inbound is not None:
        dispatch: _SlackOnInbound = on_inbound
    elif run_turn is not None:
        dispatch = build_slack_bridge_on_inbound(
            send_provider=send_provider, run_turn=run_turn
        )
    else:
        dispatch = _default_slack_on_inbound
    read_once = build_slack_read_once(provider=read_provider, on_inbound=dispatch)

    return build_channel_poll_watcher(
        channel_type="slack",
        poll_once=read_once,
        is_enabled=is_live_slack_enabled,
        interval_seconds=interval_seconds,
    )


def _default_slack_on_inbound(inbound: ChannelInbound) -> None:
    """Default inbound sink — logs receipt only (turn dispatch wired by operator)."""
    _log.info("slack inbound event received (digest only)")


__all__ = [
    "DEFAULT_DISCORD_READ_INTERVAL_SECONDS",
    "DEFAULT_SLACK_READ_INTERVAL_SECONDS",
    "DEFAULT_TELEGRAM_POLL_INTERVAL_SECONDS",
    "TelegramSupervisor",
    "build_discord_bridge_on_inbound",
    "build_discord_channel_watcher",
    "build_discord_read_once",
    "build_slack_bridge_on_inbound",
    "build_slack_channel_watcher",
    "build_slack_read_once",
    "build_telegram_bridge_on_inbound",
    "build_telegram_channel_watcher",
    "build_telegram_poll_once",
    "build_telegram_supervisor_watcher",
    "discord_live_deliver",
    "is_dashboard_telegram_enabled",
    "live_deliver",
    "make_discord_deliver",
    "make_slack_deliver",
    "make_telegram_deliver",
    "slack_live_deliver",
]
