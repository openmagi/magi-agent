"""Concrete out-of-box Slack provider over stdlib ``urllib`` (B1).

This is the default ``SlackProviderPort`` implementation so that setting
``SLACK_BOT_TOKEN`` (or ``MAGI_SLACK_BOT_TOKEN``) plus the existing channel
live gate ``MAGI_CHANNEL_LIVE_SLACK`` is ALL an operator needs to send real
Slack messages — no code injection required.  Operators who inject their own
``SlackProviderPort`` keep full precedence; this module is only the fallback.

Dependencies / import cleanliness
---------------------------------
Stdlib only (``urllib.request`` + ``json`` + ``ssl``).  No ``slack_sdk``,
``requests``, ``httpx``, ``urllib3`` or ``aiohttp`` — the third-party
network-lib forbidden list of the channel boundary stays intact.

Fail-soft discipline
--------------------
``send`` NEVER raises into the channel path.  Slack API errors, network
errors, and malformed responses all return ``{"ok": False, "error": <safe
string>}``.  Error strings pass through the shared long-token redaction
pattern so the bot token (or any other credential-shaped value) can never
leak into receipts or evidence.

Token redaction
---------------
The bot token is held only for the ``Authorization: Bearer`` header; it is
never logged, never included in error strings, and never persisted to
receipts/ledgers (the ``slack_live`` boundary stores only channel digests and
text lengths).

Egress proxy (fail-closed)
--------------------------
Outbound egress honours the shared ``EgressProxyConfig`` conventions (the
channel analogue of ``gate5b_full_toolhost._build_bash_env``):

* proxy disabled  → direct ``urllib.request.urlopen`` (https URL fixed).
* proxy enabled   → a ``ProxyHandler`` + proxy-CA ``ssl`` context opener; the
  direct path is NEVER used.
* proxy enabled but invalid → the send fails soft.  An invalid proxy config
  must not silently fall back to direct egress.

Honesty / receipts
------------------
``openmagi_local_fake_provider`` is ``False`` — this provider IS live and
never masquerades as the audit fake.  ``provider_called`` flips ``True`` the
first time a real ``chat.postMessage`` egress attempt is issued.
"""
from __future__ import annotations

import json
import os
import re
import ssl
import urllib.request
from collections.abc import Mapping
from typing import Any

from magi_agent.egress_proxy.config import EgressProxyConfig

_SLACK_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"
_DEFAULT_TIMEOUT_SECONDS = 15.0

# Same long-token redaction convention as slack_live.deliver: any 20+ char
# credential-shaped run is replaced before an error string leaves this module.
_SECRET_RE = re.compile(r"\b[A-Za-z0-9_-]{20,}\b")


def _redact(text: str) -> str:
    """Redact credential-shaped substrings from a short error excerpt."""
    return _SECRET_RE.sub("[redacted]", text[:200])


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class SlackUrllibProvider:
    """Live Slack Web API provider (``chat.postMessage``) over stdlib urllib.

    Parameters
    ----------
    token : str
        Slack bot token.  Used only for the Authorization header; never logged.
    timeout_seconds : float
        Per-request timeout.
    egress_config : EgressProxyConfig | None
        Optional injected egress-proxy config (tests).  Default: read from env
        via ``EgressProxyConfig.from_env()``.
    """

    # This provider is LIVE — it must NOT claim the audit-fake sentinel.
    openmagi_local_fake_provider: bool = False

    def __init__(
        self,
        *,
        token: str,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        egress_config: EgressProxyConfig | None = None,
    ) -> None:
        if not token or not token.strip():
            raise ValueError("slack bot token required")
        self._token = token.strip()
        self._timeout = timeout_seconds
        self._egress_config = (
            egress_config if egress_config is not None else EgressProxyConfig.from_env()
        )
        # Receipt: flips True once a real egress attempt is issued.
        self.provider_called: bool = False

    # -- egress (direct or via proxy, fail-closed) --------------------------

    def _open(self, request: urllib.request.Request) -> Any:
        """Issue the request — direct urlopen, or via the egress proxy opener.

        When the egress proxy is enabled the config is validated fail-closed:
        an invalid proxy config raises (caught by ``send`` → fail-soft) rather
        than silently bypassing the proxy with direct egress.
        """
        cfg = self._egress_config
        if not cfg.enabled:
            self.provider_called = True
            return urllib.request.urlopen(request, timeout=self._timeout)  # noqa: S310

        cfg.validate()  # raises on bad config — never bypass the proxy
        proxy = cfg.proxy_url
        assert proxy is not None  # guaranteed by validate()
        if cfg.proxy_auth:
            # Credentials are carried in the opener only — never in env, logs
            # or error strings.
            scheme, sep, rest = proxy.partition("://")
            proxy = f"{scheme}{sep}{cfg.proxy_auth}@{rest}"
        context = ssl.create_default_context(cafile=cfg.ca_cert_path)
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}),
            urllib.request.HTTPSHandler(context=context),
        )
        self.provider_called = True
        return opener.open(request, timeout=self._timeout)

    # -- port method ---------------------------------------------------------

    def send(self, *, channel: str, text: str, **kwargs: Any) -> dict[str, object]:
        """Post a message via ``chat.postMessage``.  Never raises (fail-soft).

        Returns a Slack Web API style mapping: ``{"ok": True, "ts": ...}`` on
        success, ``{"ok": False, "error": <redacted string>}`` on any failure.
        Only scalar kwargs (e.g. ``thread_ts``) are forwarded to the API.
        """
        body: dict[str, Any] = {"channel": channel, "text": text}
        for key, value in kwargs.items():
            if value is None or isinstance(value, (str, int, float, bool)):
                body[key] = value

        try:
            request = urllib.request.Request(
                _SLACK_POST_MESSAGE_URL,
                data=json.dumps(body).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                method="POST",
            )
            with self._open(request) as response:
                raw = response.read()
            parsed = json.loads(raw.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 — fail-soft into the channel path
            return {"ok": False, "error": _redact(f"{type(exc).__name__}: {exc}")}

        if not isinstance(parsed, Mapping):
            return {"ok": False, "error": "slack_response_not_object"}

        ok = bool(parsed.get("ok", False))
        result: dict[str, object] = {"ok": ok}
        ts = parsed.get("ts")
        if ts is not None:
            result["ts"] = str(ts)
        if not ok:
            result["error"] = _redact(str(parsed.get("error", "unknown_error")))
        return result


# ---------------------------------------------------------------------------
# Out-of-box construction (fail-closed: gate AND token, else None)
# ---------------------------------------------------------------------------

def slack_bot_token_from_env(env: Mapping[str, str] | None = None) -> str | None:
    """Return the configured Slack bot token or None (never logs the value)."""
    source = os.environ if env is None else env
    for key in ("MAGI_SLACK_BOT_TOKEN", "SLACK_BOT_TOKEN"):
        raw = source.get(key, "")
        if raw.strip():
            return raw.strip()
    return None


def build_default_slack_provider(
    env: Mapping[str, str] | None = None,
) -> SlackUrllibProvider | None:
    """Build the out-of-box provider, or None if not fully configured.

    Fail-closed: returns None unless BOTH the existing channel live gate
    (``MAGI_CHANNEL_LIVE_SLACK``) is on AND a bot token is present.  Without
    both, the channel stays exactly as today (shadow/receipt-only).
    """
    from magi_agent.channels.slack_live import is_live_slack_enabled

    if not is_live_slack_enabled():
        return None
    token = slack_bot_token_from_env(env)
    if token is None:
        return None
    return SlackUrllibProvider(token=token)


__all__ = [
    "SlackUrllibProvider",
    "build_default_slack_provider",
    "slack_bot_token_from_env",
]
