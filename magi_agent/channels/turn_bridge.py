"""Shared channel turn bridge — inbound message -> agent turn -> reply (PR1).

This is the channel-agnostic seam that was missing: every channel watcher's
``on_inbound`` callback previously logged the message and stopped (no agent turn
was ever started).  This module fills that gap ONCE for all channels.

Design
------
The bridge is intentionally **pure and synchronous**: the gateway poll loop calls
``on_inbound`` from a worker thread (``asyncio.to_thread(poll_once)``), so the
handler must be sync.  The agent turn and the outbound send are injected as plain
callables:

  * ``run_turn(session_key, inbound) -> str`` — drive one agent turn and return
    the final reply text.  The real implementation wraps the async engine; the
    wrapper (sync<->async bridging) is a SEPARATE concern, tested on its own.
  * ``deliver(channel_id, text, reply_to_message_id) -> bool`` — send the reply
    on the SAME channel the message came from (source = delivery invariant).

Because both are injected, the bridge needs no engine or event loop to test.
"""
from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass

_log = logging.getLogger(__name__)


def _session_key_digest(session_key: str) -> str:
    """One-way digest of the session key for evidence (never the raw key)."""
    return "session:" + hashlib.sha1(session_key.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class ChannelInbound:
    """Normalised inbound message — every channel adapter projects its native
    event (TelegramInboundUpdate / DiscordInboundEvent / SlackInboundEvent) into
    this shape before handing it to the shared bridge."""

    channel_type: str
    channel_id: str
    text: str
    message_id: str
    user_id: str = ""
    reply_to_message_id: str | None = None


# (session_key, inbound) -> reply text.  The real implementation drives the
# async engine and returns the final reply; wiring it is a separate concern.
RunTurn = Callable[[str, ChannelInbound], str]
# (channel_id, text, reply_to_message_id) -> sent?
Deliver = Callable[[str, str, "str | None"], bool]


def make_inbound_handler(
    *,
    channel_type: str,
    run_turn: RunTurn,
    deliver: Deliver,
    evidence: dict[str, object],
) -> Callable[[ChannelInbound], None]:
    """Build the ``on_inbound`` handler for one channel.

    Per inbound message: map to a session key, drive one agent turn, and deliver
    the reply on the same channel threaded under the originating message.
    """

    def on_inbound(inbound: ChannelInbound) -> None:
        session_key = channel_session_key(channel_type, inbound.channel_id)
        evidence["sessionKeyDigest"] = _session_key_digest(session_key)
        try:
            reply = run_turn(session_key, inbound)
        except Exception:  # noqa: BLE001 — one bad turn must not abort the poll batch
            _log.warning("channel turn failed", exc_info=True)
            evidence["turnError"] = True
            return
        evidence["turnInvoked"] = True
        if not reply or not reply.strip():
            evidence["delivered"] = False
            evidence["deliverSkipReason"] = "empty_reply"
            return
        evidence["delivered"] = bool(deliver(inbound.channel_id, reply, inbound.message_id))

    return on_inbound


def channel_session_key(channel_type: str, channel_id: str) -> str:
    """Build the session key for a channel conversation.

    Format ``agent:main:{channel_type}:{channel_id}`` is the inverse of
    ``runtime.session_identity._channel_from_session_key`` so the rest of the
    runtime recovers the originating channel from the key.
    """
    return f"agent:main:{channel_type}:{channel_id}"


__all__ = [
    "ChannelInbound",
    "Deliver",
    "RunTurn",
    "channel_session_key",
    "make_inbound_handler",
]
