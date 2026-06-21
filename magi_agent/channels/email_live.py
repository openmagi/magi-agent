"""E4 — Gated live email adapter.

Default OFF.  Activated only when:
  1. env var ``MAGI_CHANNEL_LIVE_EMAIL`` is set to a truthy value (non-empty,
     not "0", not "false", not "no", not "off"), AND
  2. a real ``EmailProviderPort`` is injected by the operator / Track-F daemon.
     This module never constructs a live SMTP client or HTTP mailer.

Architecture
------------
Email is outbound-only for E4 scope.  Inbound (IMAP polling, webhook parsing)
is out of scope — no incoming boundary exists yet.

``EmailProviderPort`` is a Protocol with a single ``send(*, to, subject, body,
**kwargs)`` method.  The operator injects a concrete implementation; no
``smtplib``, ``sendgrid``, or HTTP mailer is imported at module level.

``deliver(port, to, text, *, evidence)`` — outbound send.  ``to`` is the
recipient email address.  Respects the shared ``[SILENT]`` contract from
``scheduler_delivery``: if ``text`` stripped and uppercased equals exactly
``"[SILENT]"`` the send is suppressed (audit-only, no provider call).

Registration
------------
This module registers ``"email"`` in the default ``PlatformRegistry`` at import
time — the extensibility proof required by E4.  No edit to ``contract.py``'s
``ChannelType`` Literal is needed; the registry is the seam.

Gate
----
``is_live_email_enabled()`` reads ``MAGI_CHANNEL_LIVE_EMAIL`` at call time (not
import time) so tests can patch the env without a module reload.

Evidence / redaction
--------------------
Deliver records only a recipient digest and text length; raw email addresses and
message bodies are NEVER stored.

Forbidden imports (import-clean by design)
------------------------------------------
No ``requests``, ``httpx``, ``smtplib``, ``sendgrid``, ``urllib3``,
``aiohttp``, ``subprocess`` at top level.  The provider is injected; this module
is pure boundary logic.
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import Any, Protocol

from magi_agent.harness.scheduler_delivery import is_silent_output
from magi_agent.channels.platform_registry import PlatformEntry, get_default_registry


# ---------------------------------------------------------------------------
# Self-registration in the default registry (E4 extensibility proof)
# ---------------------------------------------------------------------------
# This call is the ONLY change needed to add email — no edits to contract.py,
# dispatcher.py, or any existing adapter.  The registry is the extensibility seam.

get_default_registry().register(
    PlatformEntry(
        channel_type="email",
        display_name="Email",
        supports_inbound=False,   # inbound is out of scope for E4
        supports_outbound=True,
        supports_cron_delivery=True,
        default_enabled=False,
        cron_deliver_env_var="MAGI_EMAIL_CRON_TARGET",
    )
)


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------

def is_live_email_enabled() -> bool:
    """Return True iff ``MAGI_CHANNEL_LIVE_EMAIL`` is set to a truthy value.

    Evaluated at call time (not import time) so tests can patch os.environ.

    I-2 PR B: was a denylist check (``bool(raw) and raw.lower() not in
    {"0","false","no","off"}``) which silently ENABLED the channel on any
    unknown non-empty value (e.g. ``MAGI_CHANNEL_LIVE_EMAIL="disabled"``).
    Now uses the canonical strict-allowlist semantics — only the documented
    truthy spellings (``1``/``true``/``yes``/``on``) enable the channel;
    everything else (including the previously-enabling typos) keeps it OFF.
    Stage-3 live side-effect: see PR body behaviour-change notice.
    """
    from magi_agent.config._truthy import env_bool  # noqa: PLC0415

    return env_bool(os.environ, "MAGI_CHANNEL_LIVE_EMAIL", default=False)


# ---------------------------------------------------------------------------
# Provider port (injected by operator — never constructed here)
# ---------------------------------------------------------------------------

class EmailProviderPort(Protocol):
    """Injected provider interface for the email outbound channel.

    Concrete implementations must NOT be constructed inside this module.
    The operator / Track-F daemon builds the real mailer and injects it.

    send(...)
        Send an email to a recipient.  Must return a mapping with at least
        ``{"messageId": str}`` or raise on error.
    """

    def send(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        **kwargs: Any,
    ) -> dict[str, object]: ...


# ---------------------------------------------------------------------------
# Evidence helpers
# ---------------------------------------------------------------------------

def _recipient_digest(to: str) -> str:
    """One-way hash of the recipient address for evidence (never raw)."""
    return "email-recipient:" + hashlib.sha1(to.encode("utf-8")).hexdigest()[:16]


_DEFAULT_SUBJECT = "Magi Agent notification"


# ---------------------------------------------------------------------------
# Outbound delivery
# ---------------------------------------------------------------------------

def deliver(
    port: EmailProviderPort,
    to: str,
    text: str,
    *,
    evidence: dict[str, object],
    subject: str = _DEFAULT_SUBJECT,
) -> bool:
    """Send ``text`` to ``to`` (email address) via the injected email provider.

    Respects the [SILENT] delivery contract: if ``text`` (stripped+uppercased)
    equals exactly ``"[SILENT]"`` the send is suppressed (audit-only, no
    provider call).

    Gate: if ``MAGI_CHANNEL_LIVE_EMAIL`` is off, returns False immediately.

    Parameters
    ----------
    port : EmailProviderPort
        The injected live email provider.  Never constructed here.
    to : str
        Recipient email address.
    text : str
        Outbound message body.
    evidence : dict[str, object]
        Audit accumulator.  Recipient digest and text length are stored;
        raw email addresses and message bodies are NEVER stored.
    subject : str
        Email subject line (default: ``"Magi Agent notification"``).

    Returns
    -------
    bool
        True if the message was sent (or suppressed by [SILENT]).
        False if gated or if the provider raised an error.
    """
    # Always record the recipient digest and text length — never raw address.
    evidence["deliverRecipientDigest"] = _recipient_digest(to)
    evidence["deliverTextLength"] = len(text)

    if not is_live_email_enabled():
        evidence["deliverSkipped"] = True
        evidence["deliverSkipReason"] = "gate_off"
        return False

    # [SILENT] contract: exact match only (mixed content is NOT suppressed).
    if is_silent_output(text):
        evidence["deliverSuppressed"] = True
        evidence["deliverSuppressReason"] = "silent_marker"
        return True  # suppressed = successfully handled, no provider call

    try:
        result = port.send(to=to, subject=subject, body=text)
        evidence["deliverSuppressed"] = False
        evidence["deliverMessageId"] = (
            str(result.get("messageId", ""))[:64]
            if isinstance(result.get("messageId"), str)
            else None
        )
        return True
    except Exception as exc:
        # Redact: store only a safe excerpt, never raw exception with PII.
        safe_err = re.sub(
            r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", "[redacted-email]",
            str(exc)[:120]
        )
        evidence["deliverError"] = safe_err
        return False


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------

__all__ = [
    "EmailProviderPort",
    "deliver",
    "is_live_email_enabled",
]
