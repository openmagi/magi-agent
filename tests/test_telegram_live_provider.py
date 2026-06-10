"""PR1 (B17) — concrete Telegram live provider over httpx.

The concrete ``TelegramHttpxProvider`` is the ONLY place a real HTTP client is
constructed for the Telegram channel.  These tests drive it with a fake httpx
transport (``httpx.MockTransport``) so no socket is ever opened.

Coverage:
  - getUpdates → raw update list (offset passed through), satisfying the
    ``TelegramProviderPort`` shape the boundary projects.
  - sendMessage → provider message id, and records ``provider_called``.
  - delete_webhook → maps to the Telegram ``deleteWebhook`` endpoint.
  - provider exposes ``openmagi_local_fake_provider = False`` (it IS live) but
    is still injected — the live gate, not this flag, controls activation.
  - the boundary projection layer normalises a getUpdates response end-to-end.
"""
from __future__ import annotations

from typing import Any

import httpx


def _make_provider(handler: Any, *, token: str = "111111:test_token_value") -> Any:
    from magi_agent.channels.providers.telegram_httpx import TelegramHttpxProvider

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url=f"https://api.telegram.org/bot{token}")
    return TelegramHttpxProvider(token=token, client=client)


def test_provider_is_not_a_fake_provider() -> None:
    """The concrete provider is live — its sentinel flag is False, NOT True."""
    provider = _make_provider(lambda req: httpx.Response(200, json={"ok": True, "result": []}))
    assert provider.openmagi_local_fake_provider is False


def test_poll_updates_calls_get_updates_with_offset() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["offset"] = httpx.QueryParams(request.url.query).get("offset")
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": [
                    {
                        "update_id": 7,
                        "message": {
                            "message_id": 70,
                            "from": {"id": 99},
                            "chat": {"id": 42},
                            "text": "hi",
                        },
                    }
                ],
            },
        )

    provider = _make_provider(handler)

    class _Req:
        offset = 5

    result = provider.poll_updates(_Req())
    assert "getUpdates" in seen["url"]
    assert seen["offset"] == "5"
    assert isinstance(result, list)
    assert result[0]["update_id"] == 7


def test_send_message_calls_send_message_endpoint_and_records_called() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 555}})

    provider = _make_provider(handler)

    class _Send:
        chat_id = "42"
        text = "hello world"
        reply_to_message_id = None

    out = provider.send_message(_Send())
    assert "sendMessage" in seen["url"]
    assert out["providerMessageId"] == "555"
    # Receipt: the live provider records that it actually called out.
    assert provider.provider_called is True


def test_delete_webhook_calls_delete_webhook_endpoint() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"ok": True, "description": "Webhook was deleted"})

    provider = _make_provider(handler)
    out = provider.delete_webhook()
    assert "deleteWebhook" in seen["url"]
    assert out["ok"] is True


def test_provider_normalises_updates_to_inbound() -> None:
    """The concrete provider exposes ``normalise_updates`` reusing the existing
    projection so the live wiring need not re-derive the inbound shape."""
    from magi_agent.channels.telegram_adapter import TelegramInboundUpdate

    provider = _make_provider(
        lambda req: httpx.Response(200, json={"ok": True, "result": []})
    )
    raw = [
        {
            "update_id": 11,
            "message": {
                "message_id": 110,
                "from": {"id": 7},
                "chat": {"id": 42},
                "text": "do a thing",
            },
        }
    ]
    inbound = provider.normalise_updates(raw)
    assert len(inbound) == 1
    assert isinstance(inbound[0], TelegramInboundUpdate)
    assert inbound[0].text == "do a thing"
    assert inbound[0].chat_id == "42"
