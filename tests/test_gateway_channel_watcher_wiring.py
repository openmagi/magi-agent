"""PR1 (B5+B17 seam) — operator wiring that ties the concrete Telegram provider
to a gateway channel poll watcher.

``gateway.channel_watchers`` is the ONLY composition layer that reads the
token/gate from the environment and constructs the live provider.  It is
fail-closed: with the gate OFF (or the token absent) it builds NO provider and
returns ``None`` so the daemon starts nothing for that channel.

These tests inject a fake provider factory (no network) to exercise the wiring
without httpx.  The concrete httpx provider itself is covered in
``test_telegram_live_provider``.
"""
from __future__ import annotations

from typing import Any

import pytest

from magi_agent.gateway.daemon import GatewayWatcher


class _FakeProvider:
    openmagi_local_fake_provider = False

    def __init__(self) -> None:
        self.provider_called = False
        self.delete_webhook_calls = 0
        self.poll_calls: list[Any] = []
        self.message_calls: list[Any] = []
        self._updates: list[dict[str, Any]] = []

    def queue(self, updates: list[dict[str, Any]]) -> None:
        self._updates = updates

    def delete_webhook(self) -> dict[str, Any]:
        self.delete_webhook_calls += 1
        return {"ok": True}

    def poll_updates(self, request: Any) -> list[dict[str, Any]]:
        self.poll_calls.append(getattr(request, "offset", None))
        out, self._updates = self._updates, []
        return out

    def normalise_updates(self, raw: list[dict[str, Any]]) -> list[Any]:
        from magi_agent.channels.telegram_adapter import TelegramInboundUpdate

        out = []
        for u in raw:
            msg = u["message"]
            out.append(
                TelegramInboundUpdate(
                    chatId=str(msg["chat"]["id"]),
                    userId=str(msg["from"]["id"]),
                    text=msg.get("text", ""),
                    messageId=str(msg["message_id"]),
                    rawUpdateRef=f"ref-{u['update_id']}",
                )
            )
        return out

    def send_message(self, request: Any) -> dict[str, object]:
        self.message_calls.append(request)
        self.provider_called = True
        return {"providerMessageId": "msg-1"}


# ---------------------------------------------------------------------------
# Gate / fail-closed
# ---------------------------------------------------------------------------

def test_gate_off_builds_no_watcher(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAGI_CHANNEL_LIVE_TELEGRAM", raising=False)
    monkeypatch.setenv("MAGI_TELEGRAM_BOT_TOKEN", "111111:secret")
    from magi_agent.gateway.channel_watchers import build_telegram_channel_watcher

    watcher = build_telegram_channel_watcher(provider_factory=lambda token: _FakeProvider())
    assert watcher is None


def test_gate_on_no_token_fail_closed(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("MAGI_CHANNEL_LIVE_TELEGRAM", "1")
    monkeypatch.delenv("MAGI_TELEGRAM_BOT_TOKEN", raising=False)
    from magi_agent.gateway.channel_watchers import build_telegram_channel_watcher

    with caplog.at_level("WARNING"):
        watcher = build_telegram_channel_watcher(provider_factory=lambda token: _FakeProvider())
    assert watcher is None
    assert any("token" in r.message.lower() for r in caplog.records)


def test_gate_on_with_token_builds_watcher(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_CHANNEL_LIVE_TELEGRAM", "1")
    monkeypatch.setenv("MAGI_TELEGRAM_BOT_TOKEN", "111111:secret")
    from magi_agent.gateway.channel_watchers import build_telegram_channel_watcher

    watcher = build_telegram_channel_watcher(provider_factory=lambda token: _FakeProvider())
    assert isinstance(watcher, GatewayWatcher)
    assert watcher.name == "channel_telegram"
    assert watcher.is_enabled() is True


# ---------------------------------------------------------------------------
# poll_once closure behaviour
# ---------------------------------------------------------------------------

def test_poll_once_dispatches_inbound_and_advances_offset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_CHANNEL_LIVE_TELEGRAM", "1")
    monkeypatch.setenv("MAGI_TELEGRAM_BOT_TOKEN", "111111:secret")
    from magi_agent.gateway.channel_watchers import build_telegram_poll_once

    provider = _FakeProvider()
    dispatched: list[Any] = []
    poll_once = build_telegram_poll_once(provider=provider, on_inbound=dispatched.append)

    provider.queue([
        {
            "update_id": 5,
            "message": {"message_id": 50, "from": {"id": 7}, "chat": {"id": 42}, "text": "hi"},
        }
    ])
    poll_once()
    assert len(dispatched) == 1
    assert dispatched[0].text == "hi"

    # Second cycle with no new updates dispatches nothing; same update not re-fired.
    poll_once()
    assert len(dispatched) == 1


def test_poll_once_startup_deletes_webhook_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_CHANNEL_LIVE_TELEGRAM", "1")
    monkeypatch.setenv("MAGI_TELEGRAM_BOT_TOKEN", "111111:secret")
    from magi_agent.gateway.channel_watchers import build_telegram_poll_once

    provider = _FakeProvider()
    poll_once = build_telegram_poll_once(provider=provider, on_inbound=lambda u: None)
    poll_once()
    poll_once()
    # delete_webhook is a one-time startup action, not per-cycle.
    assert provider.delete_webhook_calls == 1


# ---------------------------------------------------------------------------
# Live deliver receipt
# ---------------------------------------------------------------------------

def test_live_deliver_records_provider_called(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_CHANNEL_LIVE_TELEGRAM", "1")
    from magi_agent.gateway.channel_watchers import live_deliver

    provider = _FakeProvider()
    receipt: dict[str, object] = {}
    sent = live_deliver(provider, "42", "hello", receipt=receipt)
    assert sent is True
    assert provider.provider_called is True
    assert receipt["provider_called"] is True
    assert receipt["channel_delivery_performed"] is True


def test_live_deliver_silent_suppressed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_CHANNEL_LIVE_TELEGRAM", "1")
    from magi_agent.gateway.channel_watchers import live_deliver

    provider = _FakeProvider()
    receipt: dict[str, object] = {}
    sent = live_deliver(provider, "42", "[SILENT]", receipt=receipt)
    assert sent is True  # handled
    assert provider.provider_called is False  # provider NOT called
    assert receipt["suppressed"] is True


def test_live_deliver_gate_off_no_send(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAGI_CHANNEL_LIVE_TELEGRAM", raising=False)
    from magi_agent.gateway.channel_watchers import live_deliver

    provider = _FakeProvider()
    receipt: dict[str, object] = {}
    sent = live_deliver(provider, "42", "hello", receipt=receipt)
    assert sent is False
    assert provider.provider_called is False


# ---------------------------------------------------------------------------
# build_default_watchers integration (telegram watcher joins when gated on)
# ---------------------------------------------------------------------------

def test_default_watchers_omits_telegram_when_gate_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MAGI_CHANNEL_LIVE_TELEGRAM", raising=False)
    from magi_agent.gateway.watchers import build_default_watchers

    names = {w.name for w in build_default_watchers()}
    assert "channel_telegram" not in names


def test_default_watchers_includes_telegram_when_gated_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_CHANNEL_LIVE_TELEGRAM", "1")
    monkeypatch.setenv("MAGI_TELEGRAM_BOT_TOKEN", "111111:secret")
    # Avoid constructing a real httpx provider in this wiring test.
    import magi_agent.gateway.channel_watchers as cw

    monkeypatch.setattr(cw, "_default_telegram_provider_factory", lambda token: _FakeProvider())
    from magi_agent.gateway.watchers import build_default_watchers

    names = {w.name for w in build_default_watchers()}
    assert "channel_telegram" in names
