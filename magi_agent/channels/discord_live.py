"""E3 — Gated live Discord adapter.

Default OFF.  Activated only when:
  1. env var ``MAGI_CHANNEL_LIVE_DISCORD`` is set to a truthy value (non-empty,
     not "0"/"false"/"no"/"off"), AND
  2. a real ``DiscordProviderPort`` is injected by the operator / Track-F daemon.
     This module never constructs a live Discord gateway/HTTP client.

Architecture
------------
Discord delivers inbound messages as gateway events (not a getUpdates offset),
so — unlike Telegram (E2) — there is NO webhook to delete and NO offset to
advance.  Dedup is by ``message_id`` only.

``DiscordLiveEventState`` is a mutable seen-message tracker reused across cycles.

The injected ``DiscordProviderPort`` MUST carry ``openmagi_local_fake_provider
= True`` — the trust marker the ``DiscordAdapterBoundary`` projection requires
before it routes events/sends through a provider.  The operator / Track-F
daemon sets this on the concrete live provider it injects.

``read_and_dispatch(port, state, *, on_inbound, evidence)`` — a single read
cycle: calls the injected provider's ``read_events`` via the existing
``DiscordAdapterBoundary`` projection (text redaction + normalisation into
``DiscordInboundEvent``), deduplicates by ``message_id``, and calls
``on_inbound(event)`` for each new message.  Does NOT start an agent turn (that
is the Track-F daemon's responsibility).

``deliver(port, channel_id, text, *, evidence)`` — outbound send.  Respects the
shared ``[SILENT]`` contract from ``scheduler_delivery``: if ``text``
stripped+uppercased equals exactly ``"[SILENT]"`` the send is suppressed
(audit-only, no provider call).

Gate
----
``is_live_discord_enabled()`` reads ``MAGI_CHANNEL_LIVE_DISCORD`` at call time
(not import time) so tests can patch the env without a module reload.  The live
path is authorised by this env gate + the injected port — the boundary's
``Literal[False]`` authority flags are NEVER flipped.

Evidence / redaction
--------------------
Read decisions record only event counts and channel-id digests; raw message
text is NEVER stored.  ``deliver`` records the channel-id digest and whether the
send was suppressed.

Forbidden imports (import-clean by design)
------------------------------------------
No ``requests``/``httpx``/``urllib``/``socket``/``discord``/``subprocess`` at
top level.  The provider is injected; this module is pure boundary logic.
"""
from __future__ import annotations

import hashlib
import os
from collections.abc import Callable

from magi_agent.channels.discord_adapter import (
    DiscordAdapterBoundary,
    DiscordAdapterConfig,
    DiscordEventRequest,
    DiscordInboundEvent,
    DiscordProviderPort,
    DiscordSendRequest,
)
from magi_agent.harness.scheduler_delivery import is_silent_output


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------

def is_live_discord_enabled() -> bool:
    """Return True iff ``MAGI_CHANNEL_LIVE_DISCORD`` is set to a truthy value.

    Evaluated at call time (not import time) so tests can patch os.environ.
    """
    raw = os.environ.get("MAGI_CHANNEL_LIVE_DISCORD", "")
    return bool(raw) and raw.lower() not in {"0", "false", "no", "off"}


# ---------------------------------------------------------------------------
# Mutable read state (owns the seen-message dedup window across cycles)
# ---------------------------------------------------------------------------

class DiscordLiveEventState:
    """Mutable dedup tracker for the live Discord read loop.

    Intentionally NOT a frozen Pydantic model — the daemon loop mutates this
    object across successive ``read_and_dispatch`` calls.  Discord events have
    no offset, so dedup relies entirely on ``message_id``.
    """

    def __init__(self) -> None:
        self.seen_message_hashes: set[int] = set()

    def is_seen(self, message_hash: int) -> bool:
        return message_hash in self.seen_message_hashes

    def mark_seen(self, message_hash: int) -> None:
        self.seen_message_hashes.add(message_hash)
        # Bound memory: keep only the most recent ~1000 message hashes.
        if len(self.seen_message_hashes) > 1000:
            for h in sorted(self.seen_message_hashes)[:200]:
                self.seen_message_hashes.discard(h)


# ---------------------------------------------------------------------------
# Evidence helpers
# ---------------------------------------------------------------------------

def _channel_id_digest(channel_id: str) -> str:
    """One-way hash of channel_id for evidence (never raw id)."""
    return "discord-channel:" + hashlib.sha1(channel_id.encode("utf-8")).hexdigest()[:16]


def _message_dedup_hash(channel_id: str, message_id: str) -> int:
    key = f"{channel_id}:{message_id}"
    return int(hashlib.sha1(key.encode("utf-8")).hexdigest()[:8], 16)


# ---------------------------------------------------------------------------
# Shared boundary (configured for the injected-provider path)
# ---------------------------------------------------------------------------

def _make_boundary(provider_name: str) -> DiscordAdapterBoundary:
    """Create a DiscordAdapterBoundary for the fake-provider projection path.

    ``local_fake_provider_enabled=True`` lets the injected (live-gated) provider
    flow through the existing projection + redaction logic.  The live gate is
    ``is_live_discord_enabled()`` + the injected port — NOT the boundary
    ``Literal[False]`` authority flags, which stay False.
    """
    return DiscordAdapterBoundary(
        DiscordAdapterConfig.model_validate(
            {
                "enabled": True,
                "localFakeProviderEnabled": True,
                "selectedChannelRoutes": ("discord",),
                "providerAllowlist": (provider_name,),
            }
        )
    )


# ---------------------------------------------------------------------------
# Read-and-dispatch (single cycle)
# ---------------------------------------------------------------------------

def read_and_dispatch(
    port: DiscordProviderPort,
    state: DiscordLiveEventState,
    *,
    on_inbound: Callable[[DiscordInboundEvent], None],
    evidence: dict[str, object],
    bot_id_digest: str = "live-bot",
    owner_id_digest: str = "live-owner",
    session_key_digest: str = "live-session",
    provider_name: str = "live-discord-provider",
    bot_user_id: str | None = None,
) -> int:
    """Execute one Discord read-events cycle.

    1. Calls ``port.read_events`` via the ``DiscordAdapterBoundary`` projection
       (normalises raw events into ``DiscordInboundEvent`` + redacts text).
    2. Deduplicates by ``(channel_id, message_id)``.
    3. Calls ``on_inbound(event)`` for each new message.
    4. Records event counts + channel-id digests in ``evidence`` (never raw text).

    Returns the number of new (non-deduplicated) events dispatched.  No agent
    turn is started here — that is the daemon/driver's responsibility.
    """
    if not is_live_discord_enabled():
        evidence["readSkipped"] = True
        evidence["readSkipReason"] = "gate_off"
        return 0

    boundary = _make_boundary(provider_name)
    request = DiscordEventRequest(
        requestId="live-discord-read",
        providerName=provider_name,
        botIdDigest=bot_id_digest,
        ownerIdDigest=owner_id_digest,
        sessionKeyDigest=session_key_digest,
        botUserId=bot_user_id,
    )

    decision = boundary.handle_events(request, provider=port)

    evidence["readDecisionStatus"] = decision.status

    if decision.status != "inbound_projected_local_fake":
        evidence["readEventCount"] = 0
        evidence["readError"] = decision.reason_codes[0] if decision.reason_codes else "unknown"
        return 0

    new_count = 0
    channel_digests_seen: list[str] = []

    for event in decision.inbound_events:
        dedup_hash = _message_dedup_hash(event.channel_id, event.message_id)
        if state.is_seen(dedup_hash):
            continue
        state.mark_seen(dedup_hash)
        on_inbound(event)
        new_count += 1
        channel_digests_seen.append(_channel_id_digest(event.channel_id))

    evidence["readEventCount"] = len(decision.inbound_events)
    evidence["readNewCount"] = new_count
    evidence["readChannelDigests"] = channel_digests_seen[:20]
    return new_count


# ---------------------------------------------------------------------------
# Outbound delivery
# ---------------------------------------------------------------------------

def deliver(
    port: DiscordProviderPort,
    channel_id: str,
    text: str,
    *,
    evidence: dict[str, object],
    bot_id_digest: str = "live-bot",
    owner_id_digest: str = "live-owner",
    session_key_digest: str = "live-session",
    provider_name: str = "live-discord-provider",
    reply_to_message_id: str | None = None,
) -> bool:
    """Send ``text`` to ``channel_id`` via the injected live Discord provider.

    Respects the [SILENT] contract: if ``text`` (stripped+uppercased) equals
    exactly ``"[SILENT]"`` the send is suppressed (audit-only, no provider call).

    Gate: if ``MAGI_CHANNEL_LIVE_DISCORD`` is off, returns False immediately
    (boundary-fake unchanged).  Returns True if sent or suppressed.
    """
    evidence["deliverChannelIdDigest"] = _channel_id_digest(channel_id)
    evidence["deliverTextLength"] = len(text)

    if not is_live_discord_enabled():
        evidence["deliverSkipped"] = True
        evidence["deliverSkipReason"] = "gate_off"
        return False

    if is_silent_output(text):
        evidence["deliverSuppressed"] = True
        evidence["deliverSuppressReason"] = "silent_marker"
        return True

    from magi_agent.channels.contract import ChannelRef

    boundary = _make_boundary(provider_name)
    send_request = DiscordSendRequest(
        operation="send_message",
        requestId=f"live-deliver-{_channel_id_digest(channel_id)}",
        providerName=provider_name,
        botIdDigest=bot_id_digest,
        ownerIdDigest=owner_id_digest,
        sessionKeyDigest=session_key_digest,
        channel=ChannelRef(type="discord", channelId=channel_id),
        channelId=channel_id,
        text=text,
        replyToMessageId=reply_to_message_id,
    )

    decision = boundary.send(send_request, provider=port)

    evidence["deliverDecisionStatus"] = decision.status
    evidence["deliverSuppressed"] = False

    if decision.status == "sent_local_fake":
        evidence["deliverChunkCount"] = len(decision.delivery_receipts)
        return True

    evidence["deliverError"] = decision.reason_codes[0] if decision.reason_codes else "unknown"
    return False


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------

__all__ = [
    "DiscordLiveEventState",
    "deliver",
    "is_live_discord_enabled",
    "read_and_dispatch",
]
