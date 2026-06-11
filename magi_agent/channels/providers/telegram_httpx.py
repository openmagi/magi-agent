"""Concrete live Telegram provider over ``httpx`` (B17).

This is the ONLY module in the Telegram channel that imports a real HTTP client.
It implements the injected ``TelegramLiveProviderPort`` protocol (getUpdates /
sendMessage / deleteWebhook) against the Telegram Bot API.  The boundary and
``telegram_live`` modules stay import-clean; the operator wiring
(``magi_agent.gateway.channel_watchers``) constructs and injects this provider
only when the live gate AND a bot token are both present (fail-closed).

Honesty / receipts
------------------
``openmagi_local_fake_provider`` is ``False`` — this provider IS live and never
masquerades as the audit fake.  The audit ``TelegramAdapterBoundary`` (which
only trusts the fake sentinel) is therefore NOT the dispatch path for this
provider; the live wiring projects raw updates via ``normalise_updates`` and
records its own receipt.  ``provider_called`` flips to ``True`` the first time a
real ``sendMessage`` is issued.

Token redaction
---------------
The bot token is held only for URL construction; it is never logged or returned
in any receipt.  Endpoints are built as ``/bot<token>/<method>``.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from magi_agent.channels.telegram_adapter import (
    TelegramInboundUpdate,
    _project_update,
)

# Default Telegram getUpdates long-poll timeout (seconds). Kept modest so the
# watcher loop checks the stop_event regularly.
_DEFAULT_POLL_TIMEOUT_SECONDS = 25
_API_BASE = "https://api.telegram.org"


class TelegramHttpxProvider:
    """Live Telegram Bot API provider.

    Parameters
    ----------
    token : str
        Telegram bot token.  Used only for URL construction; never logged.
    client : httpx.Client | None
        Optional injected client (tests pass an ``httpx.MockTransport`` client).
        When omitted a real client bound to the Telegram API base is built.
    poll_timeout_seconds : int
        getUpdates long-poll timeout.
    """

    # This provider is LIVE — it must NOT claim the audit-fake sentinel.
    openmagi_local_fake_provider: bool = False

    def __init__(
        self,
        *,
        token: str,
        client: httpx.Client | None = None,
        poll_timeout_seconds: int = _DEFAULT_POLL_TIMEOUT_SECONDS,
    ) -> None:
        if not token or not token.strip():
            raise ValueError("telegram bot token required")
        self._token = token
        self._poll_timeout_seconds = poll_timeout_seconds
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=f"{_API_BASE}/bot{token}",
            timeout=httpx.Timeout(poll_timeout_seconds + 10),
        )
        # Receipt: flips True once a real send is issued.
        self.provider_called: bool = False

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    # -- port methods ------------------------------------------------------

    def delete_webhook(self) -> Mapping[str, Any]:
        """Clear any stale webhook so ``getUpdates`` does not 409."""
        resp = self._client.post("/deleteWebhook", json={"drop_pending_updates": False})
        resp.raise_for_status()
        return resp.json()

    def poll_updates(self, request: Any) -> Sequence[Mapping[str, Any]]:
        """Call ``getUpdates`` with the request offset; return the raw result list."""
        offset = getattr(request, "offset", 0) or 0
        resp = self._client.get(
            "/getUpdates",
            params={
                "offset": int(offset),
                "timeout": self._poll_timeout_seconds,
            },
        )
        resp.raise_for_status()
        body = resp.json()
        result = body.get("result") if isinstance(body, Mapping) else None
        return list(result) if isinstance(result, list) else []

    def normalise_updates(
        self, raw: Sequence[Mapping[str, Any]]
    ) -> list[TelegramInboundUpdate]:
        """Project raw getUpdates entries into ``TelegramInboundUpdate``s.

        Reuses the existing boundary projection helper so redaction and shape
        stay consistent with the audit path; non-message updates project to
        ``None`` and are dropped.
        """
        out: list[TelegramInboundUpdate] = []
        for update in raw:
            projected = _project_update(update)
            if projected is not None:
                out.append(projected)
        return out

    def send_message(self, request: Any) -> Mapping[str, object]:
        """Send a message; record that the provider was actually called."""
        payload: dict[str, Any] = {
            "chat_id": getattr(request, "chat_id", None),
            "text": getattr(request, "text", "") or "",
        }
        reply_to = getattr(request, "reply_to_message_id", None)
        if reply_to is not None:
            payload["reply_to_message_id"] = reply_to
        resp = self._client.post("/sendMessage", json=payload)
        self.provider_called = True
        resp.raise_for_status()
        body = resp.json()
        result = body.get("result") if isinstance(body, Mapping) else None
        message_id = (
            result.get("message_id") if isinstance(result, Mapping) else None
        )
        return {
            "providerMessageId": None if message_id is None else str(message_id)
        }

    # The audit boundary's wider port (send_document/photo/typing/download) is
    # not exercised by the live poll/deliver path in PR1; provide safe stubs so
    # the provider still satisfies the structural protocol if reused.
    def send_document(self, request: Any) -> Mapping[str, object]:
        return self.send_message(request)

    def send_photo(self, request: Any) -> Mapping[str, object]:
        return self.send_message(request)

    def send_typing(self, request: Any) -> Mapping[str, object]:
        chat_id = getattr(request, "chat_id", None)
        resp = self._client.post(
            "/sendChatAction", json={"chat_id": chat_id, "action": "typing"}
        )
        self.provider_called = True
        resp.raise_for_status()
        return {"providerMessageId": None}

    def download_file(self, request: Any) -> Mapping[str, object]:  # pragma: no cover
        raise NotImplementedError("telegram file download not enabled in PR1")


__all__ = ["TelegramHttpxProvider"]
