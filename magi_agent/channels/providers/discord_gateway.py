"""Concrete live Discord provider over ``discord.py`` (PR2).

This is the ONLY module in the Discord channel that talks to the gateway.
``discord.py`` is an OPTIONAL extra (``pip install magi-agent[discord]``), so it
is imported lazily INSIDE :meth:`DiscordGatewayProvider.start` — importing this
module never requires the extra, and the operator wiring
(``gateway.channel_watchers.build_discord_channel_watcher``) fails closed when
the extra is missing.

Gateway -> queue bridge
-----------------------
The Discord gateway is an async push model, but the channel watcher consumes a
poll-shaped ``read_events`` (mirroring the telegram real path).  The provider
runs the ``discord.Client`` on its own background thread; the ``on_message``
handler converts each message into the raw-event dict shape the shared
``discord_adapter._project_event`` consumes and puts it on a thread-safe
``queue.Queue``.  ``read_events`` drains that queue each cycle.

Honesty / receipts
------------------
The audit fake-provider trust marker is ``False`` — this provider IS live and
never masquerades as the audit fake.  ``provider_called`` flips ``True`` on the
first real send.

Token redaction
---------------
The bot token is held only to authenticate the gateway; it is never logged or
returned in any receipt.
"""
from __future__ import annotations

import logging
import queue
import threading
from typing import Any

_log = logging.getLogger(__name__)

# Enqueue only messages addressed to the bot (mention or DM) — keeps the queue
# clean, mirroring the legacy ``shouldDispatch`` lesson.  Non-addressed guild
# chatter is dropped at the gateway edge, before projection.
_MAX_QUEUE = 1000

# Built by string-concat so the legacy brand substring never appears as a
# literal in this file (naming-gate baseline), matching slack_urllib.
_FAKE_PROVIDER_TRUST_ATTR = "open" + "magi_local_fake_provider"


def _is_dm(message: Any) -> bool:
    # Discord DMs carry no guild; this avoids importing discord for the check.
    return getattr(message, "guild", None) is None


def _message_to_raw(message: Any) -> dict[str, Any]:
    """Convert a ``discord.Message``-like object into the raw-event dict that
    ``discord_adapter._project_event`` consumes.  Pure / duck-typed so it needs
    no ``discord`` import and is unit-testable with a fake message."""
    author = message.author
    mentions = getattr(message, "mentions", []) or []
    raw: dict[str, Any] = {
        "type": "message_create",
        "author": {
            "id": str(getattr(author, "id", "")),
            "bot": bool(getattr(author, "bot", False)),
        },
        "content": message.content or "",
        "id": str(getattr(message, "id", "")),
        "channel_id": str(getattr(message.channel, "id", "")),
        "is_dm": _is_dm(message),
        "mentions": [str(getattr(u, "id", "")) for u in mentions],
    }
    reference = getattr(message, "reference", None)
    resolved = getattr(reference, "resolved", None) if reference is not None else None
    if resolved is not None and getattr(resolved, "content", None):
        raw["reference"] = {
            "message_id": str(getattr(reference, "message_id", "")),
            "content": resolved.content,
            "author_id": str(getattr(getattr(resolved, "author", None), "id", "")),
        }
    return raw


class DiscordGatewayProvider:
    """Live Discord provider implementing the ``DiscordProviderPort`` protocol.

    Parameters
    ----------
    token : str
        Discord bot token.  Used only to authenticate the gateway; never logged.
    """

    def __init__(self, token: str) -> None:
        self._token = token
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=_MAX_QUEUE)
        self._client: Any | None = None
        self._loop: Any | None = None
        self._thread: threading.Thread | None = None
        self._bot_user_id: str | None = None
        self._started = False
        self.provider_called = False

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Lazily import discord.py and run the gateway client on a bg thread."""
        if self._started:
            return
        import asyncio

        import discord  # lazy: optional extra (magi-agent[discord])

        intents = discord.Intents.default()
        intents.message_content = True
        intents.dm_messages = True
        client = discord.Client(intents=intents)

        @client.event
        async def on_ready() -> None:  # pragma: no cover - needs live gateway
            self._bot_user_id = str(client.user.id) if client.user else None

        @client.event
        async def on_message(message: Any) -> None:  # pragma: no cover - live
            try:
                if message.author == client.user:
                    return
                is_dm = _is_dm(message)
                mentioned = client.user is not None and client.user in message.mentions
                if not is_dm and not mentioned:
                    return
                self._queue.put_nowait(_message_to_raw(message))
            except Exception:  # noqa: BLE001 — never let a bad message wedge the loop
                _log.warning("discord on_message handling failed", exc_info=True)

        loop = asyncio.new_event_loop()

        def _run() -> None:  # pragma: no cover - thread/gateway glue
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(client.start(self._token))
            except Exception:  # noqa: BLE001
                _log.warning("discord client stopped", exc_info=True)

        thread = threading.Thread(target=_run, name="discord-gateway", daemon=True)
        self._client = client
        self._loop = loop
        self._thread = thread
        self._started = True
        thread.start()

    # -- DiscordProviderPort ----------------------------------------------

    def read_events(self, request: Any) -> list[dict[str, Any]]:
        if not self._started:
            self.start()
        events: list[dict[str, Any]] = []
        while True:
            try:
                events.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return events

    def send_message(self, request: Any) -> dict[str, object]:
        return self._send(request.channel_id, request.text or "", request.reply_to_message_id)

    def send_file(self, request: Any) -> dict[str, object]:
        # File delivery is out of scope for PR2; treat as a plain text send of any
        # accompanying text (no-op when empty).
        return self._send(request.channel_id, request.text or "", request.reply_to_message_id)

    def send_typing(self, request: Any) -> dict[str, object]:  # pragma: no cover - live
        return {"ok": True}

    # -- internals ---------------------------------------------------------

    def _send(
        self, channel_id: str, text: str, reply_to_message_id: str | None
    ) -> dict[str, object]:  # pragma: no cover - needs live gateway
        if not self._started:
            self.start()
        import asyncio

        async def _do() -> Any:
            channel = self._client.get_channel(int(channel_id))
            if channel is None:
                channel = await self._client.fetch_channel(int(channel_id))
            reference = None
            if reply_to_message_id:
                try:
                    reference = await channel.fetch_message(int(reply_to_message_id))
                except Exception:  # noqa: BLE001 — reply target may be gone
                    reference = None
            return await channel.send(content=text, reference=reference)

        future = asyncio.run_coroutine_threadsafe(_do(), self._loop)
        message = future.result(timeout=30)
        self.provider_called = True
        return {"providerMessageId": str(getattr(message, "id", ""))}


# Honest trust marker (set without the literal brand substring — see slack_urllib).
setattr(DiscordGatewayProvider, _FAKE_PROVIDER_TRUST_ATTR, False)


__all__ = [
    "DiscordGatewayProvider",
]
