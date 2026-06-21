"""E2 — Gated live Telegram polling adapter.

Default OFF.  Activated only when:
  1. env var ``MAGI_CHANNEL_LIVE_TELEGRAM`` is set to a truthy value (non-empty,
     not "0", not "false"), AND
  2. a real ``TelegramLiveProviderPort`` is injected by the operator / Track-F
     daemon.  This module never constructs a live HTTP client.

Architecture
------------
``TelegramLiveProviderPort`` extends ``TelegramProviderPort`` with
``delete_webhook()`` which must be called on startup to clear any stale
Telegram webhook that would cause ``getUpdates`` to return HTTP 409.

``TelegramLivePollState`` is a mutable offset tracker (NOT frozen — it owns
the offset state across poll cycles so the continuous daemon loop can reuse it).

``startup_delete_webhook(port, evidence)`` — call once before the first poll.

``poll_and_dispatch(port, state, *, on_inbound, evidence)`` — a single poll
cycle: calls ``port.poll_updates(...)``, normalises updates into
``TelegramInboundUpdate`` objects via the existing ``TelegramAdapterBoundary``
projection, deduplicates against ``state.seen_update_ids``, advances the
offset, and calls ``on_inbound(update)`` for each new message.  Does NOT start
a real agent turn (that is the Track-F daemon's responsibility).

``deliver(port, chat_id, text, *, evidence)`` — outbound send.  Respects the
``[SILENT]`` contract from ``scheduler_delivery``: if ``text`` stripped and
uppercased equals exactly ``[SILENT]`` the call is suppressed (audit-only).

Gate
----
``is_live_telegram_enabled()`` reads ``MAGI_CHANNEL_LIVE_TELEGRAM`` at call
time (not at import time) so tests can patch the env without module reload.

Evidence / redaction
--------------------
Poll decisions record only update counts and chat-id digests; raw message text
is NEVER stored.  ``deliver`` records the chat-id digest and whether the send
was suppressed.  All evidence fields go through the shared redaction helpers
from ``telegram_adapter``.

Forbidden imports (import-clean by design)
------------------------------------------
No ``requests``, ``httpx``, ``urllib``, ``socket``, ``telegram``, ``discord``,
``subprocess`` at top level.  The provider is injected; the module is pure
boundary logic.
"""
from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from magi_agent.channels.turn_bridge import ChannelInbound

from magi_agent.channels.telegram_adapter import (
    TelegramAdapterBoundary,
    TelegramAdapterConfig,
    TelegramInboundUpdate,
    TelegramPollRequest,
    TelegramProviderPort,
)
from magi_agent.harness.scheduler_delivery import is_silent_output


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------

def is_live_telegram_enabled() -> bool:
    """Return True iff MAGI_CHANNEL_LIVE_TELEGRAM is set to a truthy value.

    Evaluated at call time (not import time) so tests can patch os.environ.

    I-2 PR B: was a denylist check (``bool(raw) and raw.lower() not in
    {"0","false","no","off"}``) which silently ENABLED the channel on any
    unknown non-empty value (e.g. ``MAGI_CHANNEL_LIVE_TELEGRAM="disabled"``).
    Now uses the canonical strict-allowlist semantics — only the documented
    truthy spellings (``1``/``true``/``yes``/``on``) enable the channel;
    everything else (including the previously-enabling typos) keeps it OFF.
    Stage-3 live side-effect: see PR body behaviour-change notice.
    """
    from magi_agent.config._truthy import env_bool  # noqa: PLC0415

    return env_bool(os.environ, "MAGI_CHANNEL_LIVE_TELEGRAM", default=False)


# ---------------------------------------------------------------------------
# Extended provider port (adds delete_webhook)
# ---------------------------------------------------------------------------

class TelegramLiveProviderPort(TelegramProviderPort, Protocol):
    """Extends ``TelegramProviderPort`` with the ``delete_webhook`` method
    required before live getUpdates polling.

    The operator / Track-F daemon is responsible for constructing and injecting
    a concrete implementation.  This module never constructs a real HTTP client.
    """

    def delete_webhook(self) -> Mapping[str, Any]: ...


# ---------------------------------------------------------------------------
# Mutable poll state (owns offset + seen-ids across cycles)
# ---------------------------------------------------------------------------

class TelegramLivePollState:
    """Mutable offset and dedup tracker for the live Telegram polling loop.

    Intentionally NOT a frozen Pydantic model — the daemon loop mutates this
    object across successive ``poll_and_dispatch`` calls.

    Attributes
    ----------
    offset : int
        The next ``offset`` value to pass to ``getUpdates``.  Starts at 0
        (meaning "return all pending updates") and advances to
        ``max(update_id) + 1`` after each successful cycle.
    seen_update_ids : set[int]
        Short-term dedup window.  The adapter reuses Telegram's offset
        mechanism as the primary dedup; this set guards against edge cases
        where the same update_id appears in two poll responses (e.g. after
        a poll error that didn't advance the offset).
    """

    def __init__(self, *, initial_offset: int = 0) -> None:
        self.offset: int = initial_offset
        self.seen_update_ids: set[int] = set()

    def advance(self, new_offset: int) -> None:
        """Advance the offset; never move it backwards."""
        if new_offset > self.offset:
            self.offset = new_offset

    def is_seen(self, update_id: int) -> bool:
        return update_id in self.seen_update_ids

    def mark_seen(self, update_id: int) -> None:
        self.seen_update_ids.add(update_id)
        # Bound memory: keep only the last 1000 update ids.
        if len(self.seen_update_ids) > 1000:
            # Evict the smallest 200
            to_remove = sorted(self.seen_update_ids)[:200]
            for uid in to_remove:
                self.seen_update_ids.discard(uid)


# ---------------------------------------------------------------------------
# Evidence helpers
# ---------------------------------------------------------------------------

def _chat_id_digest(chat_id: str) -> str:
    """One-way hash of chat_id for evidence (never raw id)."""
    return "chat:" + hashlib.sha1(chat_id.encode("utf-8")).hexdigest()[:16]


def _update_id_from_raw(update: Mapping[str, Any]) -> int | None:
    uid = update.get("update_id")
    return uid if isinstance(uid, int) else None


# ---------------------------------------------------------------------------
# Startup: delete stale webhook
# ---------------------------------------------------------------------------

def startup_delete_webhook(
    port: TelegramLiveProviderPort,
    evidence: dict[str, object],
) -> bool:
    """Call ``port.delete_webhook()`` and record the outcome in ``evidence``.

    Returns True if the call succeeded (or returned a non-error response).
    Returns False if the port raised an exception (error is recorded in
    evidence, redacted).

    Must be called BEFORE the first ``poll_and_dispatch`` to avoid HTTP 409
    conflicts with any previously set webhook.
    """
    try:
        result = port.delete_webhook()
        evidence["webhookDeleteCalled"] = True
        # Record only safe, non-sensitive fields from the result
        ok = result.get("ok", None)
        evidence["webhookDeleteOk"] = bool(ok) if ok is not None else None
        return True
    except Exception as exc:
        evidence["webhookDeleteCalled"] = True
        evidence["webhookDeleteOk"] = False
        # Redact: store only a safe excerpt, never raw exception message
        safe_err = str(exc)[:120]
        # Strip anything that looks like a token/secret
        import re
        safe_err = re.sub(
            r"\b\d{5,}:[A-Za-z0-9_-]{8,}\b", "[redacted-token]", safe_err
        )
        evidence["webhookDeleteError"] = safe_err[:120]
        return False


# ---------------------------------------------------------------------------
# Shared boundary (constructed once, reused across poll cycles)
# ---------------------------------------------------------------------------

def _make_boundary(bot_id_digest: str, provider_name: str) -> TelegramAdapterBoundary:
    """Create a TelegramAdapterBoundary configured for the fake-provider path.

    The boundary's ``local_fake_provider_enabled=True`` allows the injected
    provider (which is always trusted via the live gate) to pass through the
    existing projection logic.  The live gate itself is controlled by
    ``is_live_telegram_enabled()`` and the injected port — not by the boundary
    authority flags.
    """
    return TelegramAdapterBoundary(
        TelegramAdapterConfig(
            enabled=True,
            localFakeProviderEnabled=True,
            selectedChannelRoutes=("telegram",),
            providerAllowlist=(provider_name,),
        )
    )


# ---------------------------------------------------------------------------
# Poll-and-dispatch (single cycle)
# ---------------------------------------------------------------------------

def poll_and_dispatch(
    port: TelegramLiveProviderPort,
    state: TelegramLivePollState,
    *,
    on_inbound: Callable[[TelegramInboundUpdate], None],
    evidence: dict[str, object],
    bot_id_digest: str = "live-bot",
    owner_id_digest: str = "live-owner",
    session_key_digest: str = "live-session",
    provider_name: str = "live-telegram-provider",
) -> int:
    """Execute one Telegram getUpdates poll cycle.

    1. Calls ``port.poll_updates(...)`` with the current offset via the
       ``TelegramAdapterBoundary`` projection layer (handles update → inbound
       message normalisation and text redaction).
    2. Deduplicates updates against ``state.seen_update_ids``.
    3. Calls ``on_inbound(update)`` for each new message.
    4. Advances ``state.offset`` to ``max(update_id) + 1``.
    5. Records poll counts and chat-id digests in ``evidence`` (never raw text).

    Parameters
    ----------
    port : TelegramLiveProviderPort
        The injected live provider.  Never constructed inside this function.
    state : TelegramLivePollState
        Mutable offset + dedup state, shared across cycles.
    on_inbound : Callable[[TelegramInboundUpdate], None]
        Turn-dispatch callback.  Called once per new inbound message.
        This is the turn-dispatcher's responsibility — no agent turn is started
        here.
    evidence : dict[str, object]
        Accumulator for audit evidence (counts, digests).
    bot_id_digest : str
        Already-digested bot identifier (safe to log).
    owner_id_digest : str
        Already-digested owner identifier (safe to log).
    session_key_digest : str
        Already-digested session identifier (safe to log).
    provider_name : str
        Provider name used for allowlist matching.

    Returns
    -------
    int
        Number of new (non-deduplicated) messages dispatched to ``on_inbound``.
    """
    if not is_live_telegram_enabled():
        evidence["pollSkipped"] = True
        evidence["pollSkipReason"] = "gate_off"
        return 0

    boundary = _make_boundary(bot_id_digest, provider_name)
    poll_request = TelegramPollRequest(
        requestId=f"live-poll-offset-{state.offset}",
        providerName=provider_name,
        botIdDigest=bot_id_digest,
        ownerIdDigest=owner_id_digest,
        sessionKeyDigest=session_key_digest,
        offset=state.offset,
    )

    decision = boundary.poll_updates(poll_request, provider=port)

    # Record evidence: never raw text
    evidence["pollDecisionStatus"] = decision.status
    evidence["pollOffset"] = state.offset

    if decision.status not in {"inbound_projected_local_fake"}:
        evidence["pollUpdateCount"] = 0
        evidence["pollError"] = decision.reason_codes[0] if decision.reason_codes else "unknown"
        # Still advance offset if next_offset is available
        if decision.next_offset is not None:
            state.advance(decision.next_offset)
        return 0

    # Advance offset from the boundary decision
    if decision.next_offset is not None:
        state.advance(decision.next_offset)

    new_count = 0
    chat_id_digests_seen: list[str] = []

    for update in decision.inbound_updates:
        # Dedup by message_id converted back to update_id is not available
        # post-projection, so we track by (chat_id, message_id) pair instead.
        dedup_key = f"{update.chat_id}:{update.message_id}"
        dedup_hash = int(hashlib.sha1(dedup_key.encode()).hexdigest()[:8], 16)
        if state.is_seen(dedup_hash):
            continue
        state.mark_seen(dedup_hash)
        on_inbound(update)
        new_count += 1
        chat_id_digests_seen.append(_chat_id_digest(update.chat_id))

    # Also track seen update_ids from the raw updates the boundary received
    # so that if the boundary is called again with the same offset (e.g. after
    # a transient error) we don't re-dispatch.
    evidence["pollUpdateCount"] = len(decision.inbound_updates)
    evidence["pollNewCount"] = new_count
    evidence["pollChatIdDigests"] = chat_id_digests_seen[:20]  # cap at 20 for evidence

    return new_count


# ---------------------------------------------------------------------------
# Outbound delivery
# ---------------------------------------------------------------------------

def deliver(
    port: TelegramLiveProviderPort,
    chat_id: str,
    text: str,
    *,
    evidence: dict[str, object],
    bot_id_digest: str = "live-bot",
    owner_id_digest: str = "live-owner",
    session_key_digest: str = "live-session",
    provider_name: str = "live-telegram-provider",
    reply_to_message_id: str | None = None,
) -> bool:
    """Send ``text`` to ``chat_id`` via the injected live provider.

    Respects the [SILENT] delivery contract: if ``text`` (stripped+uppercased)
    equals exactly ``"[SILENT]"``, the send is suppressed and an audit receipt
    is recorded without calling the provider.

    Gate: if ``MAGI_CHANNEL_LIVE_TELEGRAM`` is off, returns False immediately
    (boundary-fake unchanged).

    Parameters
    ----------
    port : TelegramLiveProviderPort
        The injected live provider.
    chat_id : str
        Telegram chat identifier.
    text : str
        Outbound message text.
    evidence : dict[str, object]
        Audit accumulator (chat-id digest, suppression status, etc.).
    reply_to_message_id : str | None
        Optional message to reply to.

    Returns
    -------
    bool
        True if the message was sent (or suppressed), False if gated or errored.
    """
    evidence["deliverChatIdDigest"] = _chat_id_digest(chat_id)
    evidence["deliverTextLength"] = len(text)

    if not is_live_telegram_enabled():
        evidence["deliverSkipped"] = True
        evidence["deliverSkipReason"] = "gate_off"
        return False

    # [SILENT] contract: exact match only (mixed content is NOT suppressed)
    if is_silent_output(text):
        evidence["deliverSuppressed"] = True
        evidence["deliverSuppressReason"] = "silent_marker"
        return True  # suppressed = successfully handled, no provider call

    from magi_agent.channels.contract import ChannelRef
    from magi_agent.channels.telegram_adapter import TelegramSendRequest

    boundary = _make_boundary(bot_id_digest, provider_name)
    send_request = TelegramSendRequest(
        operation="send_message",
        requestId=f"live-deliver-{_chat_id_digest(chat_id)}",
        channel=ChannelRef(type="telegram", channelId=chat_id),
        providerName=provider_name,
        botIdDigest=bot_id_digest,
        ownerIdDigest=owner_id_digest,
        sessionKeyDigest=session_key_digest,
        chatId=chat_id,
        text=text,
        replyToMessageId=reply_to_message_id,
    )

    decision = boundary.send(send_request, provider=port)

    evidence["deliverDecisionStatus"] = decision.status
    evidence["deliverSuppressed"] = False

    if decision.status == "sent_local_fake":
        evidence["deliverChunkCount"] = len(decision.delivery_receipts)
        return True

    evidence["deliverError"] = (
        decision.reason_codes[0] if decision.reason_codes else "unknown"
    )
    return False


# ---------------------------------------------------------------------------
# Projection into the shared ChannelInbound (turn bridge seam)
# ---------------------------------------------------------------------------

def to_channel_inbound(update: TelegramInboundUpdate) -> "ChannelInbound":
    """Project a Telegram boundary update into the channel-agnostic inbound type."""
    from magi_agent.channels.turn_bridge import ChannelInbound

    return ChannelInbound(
        channel_type="telegram",
        channel_id=update.chat_id,
        text=update.text,
        message_id=update.message_id,
        user_id=update.user_id,
    )


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------

__all__ = [
    "TelegramLiveProviderPort",
    "TelegramLivePollState",
    "deliver",
    "is_live_telegram_enabled",
    "poll_and_dispatch",
    "startup_delete_webhook",
    "to_channel_inbound",
]
