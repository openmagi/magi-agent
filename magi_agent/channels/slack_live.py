"""E4 — Gated live Slack adapter.

Default OFF.  Activated only when:
  1. env var ``MAGI_CHANNEL_LIVE_SLACK`` is set to a truthy value (non-empty,
     not "0", not "false", not "no", not "off"), AND
  2. a real ``SlackProviderPort`` is available — either injected by the
     operator / Track-F daemon, OR (B1 out-of-box path) built from the
     environment when ``deliver`` receives ``port=None`` and a Slack bot token
     (``SLACK_BOT_TOKEN`` / ``MAGI_SLACK_BOT_TOKEN``) is configured.
     This module never constructs a live Slack SDK client; the stdlib-only
     default provider lives in ``channels.providers.slack_urllib`` and is
     imported lazily only on the fallback path.

Architecture
------------
Slack is outbound-only for E4 scope.  Inbound (app_mentions, slash commands,
Events API) is out of scope — no incoming boundary exists yet.

``SlackProviderPort`` is a Protocol with a single ``send(*, channel, text,
**kwargs)`` method.  An operator-injected implementation always wins; with
``port=None`` the out-of-box ``SlackUrllibProvider`` (stdlib urllib, gate AND
token required, fail-closed) is used.  No ``slack_sdk`` is imported at module
level.

``deliver(port, channel, text, *, evidence)`` — outbound send.  Respects the
shared ``[SILENT]`` contract from ``scheduler_delivery``: if ``text`` stripped
and uppercased equals exactly ``"[SILENT]"`` the send is suppressed (audit-only,
no provider call).

Registration
------------
This module registers ``"slack"`` in the default ``PlatformRegistry`` at import
time — the extensibility proof required by E4.  No edit to ``contract.py``'s
``ChannelType`` Literal is needed; the registry is the seam.

Gate
----
``is_live_slack_enabled()`` reads ``MAGI_CHANNEL_LIVE_SLACK`` at call time (not
import time) so tests can patch the env without a module reload.

Evidence / redaction
--------------------
Deliver records only a channel-id digest and text length; raw channel names and
message bodies are NEVER stored.

Forbidden imports (import-clean by design)
------------------------------------------
No ``requests``, ``httpx``, ``slack_sdk``, ``slack_bolt``, ``urllib3``,
``aiohttp``, ``subprocess`` at top level.  The provider is injected; this module
is pure boundary logic.
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import TYPE_CHECKING, Any, Protocol

from magi_agent.harness.scheduler_delivery import is_silent_output
from magi_agent.channels.platform_registry import PlatformEntry, get_default_registry

if TYPE_CHECKING:
    from magi_agent.channels.turn_bridge import ChannelInbound


# ---------------------------------------------------------------------------
# Self-registration in the default registry (E4 extensibility proof)
# ---------------------------------------------------------------------------
# This call is the ONLY change needed to add Slack — no edits to contract.py,
# dispatcher.py, or any existing adapter.  The registry is the extensibility seam.

get_default_registry().register(
    PlatformEntry(
        channel_type="slack",
        display_name="Slack",
        supports_inbound=True,    # PR3: Socket Mode inbound channel
        supports_outbound=True,
        supports_cron_delivery=True,
        default_enabled=False,
        cron_deliver_env_var="MAGI_SLACK_CRON_TARGET",
    )
)


# ---------------------------------------------------------------------------
# Inbound projection (Socket Mode raw event -> ChannelInbound)
# ---------------------------------------------------------------------------

def _project_slack_event(raw: dict[str, Any]) -> "ChannelInbound | None":
    """Project a normalised Slack message event into the channel-agnostic inbound.

    Drops non-message events, bot messages (``bot_id`` present) and empty text.
    The reply target (``message_id``) is the thread root (``thread_ts``) when the
    message is already in a thread, else its own ``ts`` — so the bot replies
    in-thread.
    """
    if raw.get("type") != "message":
        return None
    if raw.get("bot_id"):
        return None
    text = raw.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    channel = raw.get("channel")
    user = raw.get("user")
    ts = raw.get("ts")
    if not channel or not user or not ts:
        return None
    from magi_agent.channels.turn_bridge import ChannelInbound

    thread_ts = raw.get("thread_ts")
    reply_target = thread_ts if isinstance(thread_ts, str) and thread_ts else ts
    return ChannelInbound(
        channel_type="slack",
        channel_id=str(channel),
        text=text,
        message_id=str(reply_target),
        user_id=str(user),
    )


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------

def is_live_slack_enabled() -> bool:
    """Return True iff ``MAGI_CHANNEL_LIVE_SLACK`` is set to a truthy value.

    Evaluated at call time (not import time) so tests can patch os.environ.
    """
    raw = os.environ.get("MAGI_CHANNEL_LIVE_SLACK", "")
    return bool(raw) and raw.lower() not in {"0", "false", "no", "off"}


# ---------------------------------------------------------------------------
# Provider port (injected by operator — never constructed here)
# ---------------------------------------------------------------------------

class SlackProviderPort(Protocol):
    """Provider interface for the Slack outbound channel.

    Concrete implementations are NOT constructed inside this module.  The
    operator / Track-F daemon may inject one; otherwise ``deliver(None, ...)``
    falls back to the out-of-box stdlib provider built (fail-closed) from
    ``SLACK_BOT_TOKEN`` by ``channels.providers.slack_urllib``.

    send(...)
        Post a message to a Slack channel.  Must return a mapping with at
        least ``{"ok": bool}`` (Slack Web API style) or raise on error.
    """

    def send(
        self,
        *,
        channel: str,
        text: str,
        **kwargs: Any,
    ) -> dict[str, object]: ...


# ---------------------------------------------------------------------------
# Evidence helpers
# ---------------------------------------------------------------------------

def _channel_digest(channel: str) -> str:
    """One-way hash of the Slack channel id/name for evidence (never raw)."""
    return "slack-channel:" + hashlib.sha1(channel.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Outbound delivery
# ---------------------------------------------------------------------------

def _default_port_from_env() -> SlackProviderPort | None:
    """B1 out-of-box fallback — lazy, fail-closed, never raises.

    Built only when the live gate is ON and a Slack bot token is configured;
    the concrete stdlib provider lives in ``channels.providers.slack_urllib``
    (imported lazily so this boundary module stays import-clean).
    """
    try:
        from magi_agent.channels.providers.slack_urllib import (
            build_default_slack_provider,
        )

        return build_default_slack_provider()
    except Exception:  # noqa: BLE001 — fallback resolution must never raise
        return None


def deliver(
    port: SlackProviderPort | None,
    channel: str,
    text: str,
    *,
    evidence: dict[str, object],
) -> bool:
    """Send ``text`` to ``channel`` via the Slack provider.

    Respects the [SILENT] delivery contract: if ``text`` (stripped+uppercased)
    equals exactly ``"[SILENT]"`` the send is suppressed (audit-only, no
    provider call).

    Gate: if ``MAGI_CHANNEL_LIVE_SLACK`` is off, returns False immediately.

    Parameters
    ----------
    port : SlackProviderPort | None
        The injected live Slack provider (always wins when provided).  With
        ``None``, the out-of-box default provider is built from the env
        (gate ON + ``SLACK_BOT_TOKEN``/``MAGI_SLACK_BOT_TOKEN``, fail-closed);
        without a token the call stays shadow/receipt-only (``no_provider``).
    channel : str
        Slack channel id or name (e.g. ``"#general"`` or ``"C012AB3CD"``).
    text : str
        Outbound message text.
    evidence : dict[str, object]
        Audit accumulator.  Channel-id digests and text length are stored;
        raw channel ids, message bodies and tokens are NEVER stored.

    Returns
    -------
    bool
        True if the message was sent (or suppressed by [SILENT]).
        False if gated, if no provider is available, or if the provider
        raised an error.
    """
    # Always record the channel digest and text length — never raw channel id.
    evidence["deliverChannelDigest"] = _channel_digest(channel)
    evidence["deliverTextLength"] = len(text)

    if not is_live_slack_enabled():
        evidence["deliverSkipped"] = True
        evidence["deliverSkipReason"] = "gate_off"
        return False

    # [SILENT] contract: exact match only (mixed content is NOT suppressed).
    if is_silent_output(text):
        evidence["deliverSuppressed"] = True
        evidence["deliverSuppressReason"] = "silent_marker"
        return True  # suppressed = successfully handled, no provider call

    resolved = port if port is not None else _default_port_from_env()
    if resolved is None:
        evidence["deliverSkipped"] = True
        evidence["deliverSkipReason"] = "no_provider"
        return False

    try:
        result = resolved.send(channel=channel, text=text)
        evidence["deliverSuppressed"] = False
        evidence["deliverOk"] = bool(result.get("ok", True))
        if not evidence["deliverOk"]:
            # Provider error strings are pre-redacted (fail-soft contract);
            # re-redact defensively before persisting to evidence.
            raw_error = str(result.get("error", "unknown_error"))
            evidence["deliverError"] = re.sub(
                r"\b[A-Za-z0-9_-]{20,}\b", "[redacted]", raw_error[:120]
            )
        return True
    except Exception as exc:
        # Redact: store only a safe excerpt, never raw exception with credentials.
        safe_err = re.sub(
            r"\b[A-Za-z0-9_-]{20,}\b", "[redacted]", str(exc)[:120]
        )
        evidence["deliverError"] = safe_err
        return False


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------

__all__ = [
    "SlackProviderPort",
    "_project_slack_event",
    "deliver",
    "is_live_slack_enabled",
]
