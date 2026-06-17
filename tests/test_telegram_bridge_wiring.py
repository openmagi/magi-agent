"""Telegram <-> shared turn bridge wiring (PR1).

Proves the composition: a TelegramInboundUpdate flows through the projection +
shared bridge + telegram deliver adapter, driving an (injected) turn and sending
the reply back via the live provider — all behind the live gate.
"""
from __future__ import annotations

from typing import Any

import pytest

from magi_agent.channels.telegram_adapter import TelegramInboundUpdate
from magi_agent.channels.turn_bridge import ChannelInbound
from magi_agent.gateway.channel_watchers import (
    _default_on_inbound,
    _resolve_dispatch,
    build_telegram_bridge_on_inbound,
)

_LIVE_ENV = "MAGI_CHANNEL_LIVE_TELEGRAM"


class _FakeTelegramProvider:
    provider_called = True

    def __init__(self) -> None:
        self.sent: list[Any] = []

    def send_message(self, request: Any) -> dict[str, object]:
        self.sent.append(request)
        return {"providerMessageId": "srv-1"}


def _update(text: str = "ping") -> TelegramInboundUpdate:
    return TelegramInboundUpdate(
        chatId="42", userId="u9", text=text, messageId="m1", rawUpdateRef="r1"
    )


def test_bridge_drives_turn_and_sends_reply_via_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_LIVE_ENV, "1")
    provider = _FakeTelegramProvider()
    captured: dict[str, object] = {}

    def run_turn(session_key: str, inbound: ChannelInbound) -> str:
        captured["session_key"] = session_key
        captured["text"] = inbound.text
        return "pong"

    on_inbound = build_telegram_bridge_on_inbound(provider=provider, run_turn=run_turn)
    on_inbound(_update("ping"))

    assert captured["session_key"] == "agent:main:telegram:42"
    assert captured["text"] == "ping"
    assert len(provider.sent) == 1
    assert provider.sent[0].chat_id == "42"
    assert provider.sent[0].text == "pong"
    assert provider.sent[0].reply_to_message_id == "m1"


def test_bridge_does_not_send_when_gate_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_LIVE_ENV, raising=False)
    provider = _FakeTelegramProvider()

    on_inbound = build_telegram_bridge_on_inbound(
        provider=provider, run_turn=lambda _k, _i: "pong"
    )
    on_inbound(_update())

    assert provider.sent == []


def test_resolve_dispatch_prefers_explicit_on_inbound() -> None:
    def sentinel(_u: TelegramInboundUpdate) -> None:
        return None

    resolved = _resolve_dispatch(provider=object(), on_inbound=sentinel, run_turn=None)
    assert resolved is sentinel


def test_resolve_dispatch_defaults_to_log_only_when_nothing_given() -> None:
    resolved = _resolve_dispatch(provider=object(), on_inbound=None, run_turn=None)
    assert resolved is _default_on_inbound


def test_resolve_dispatch_builds_bridge_when_run_turn_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_LIVE_ENV, "1")
    provider = _FakeTelegramProvider()
    captured: dict[str, object] = {}

    def run_turn(session_key: str, inbound: ChannelInbound) -> str:
        captured["session_key"] = session_key
        return "pong"

    dispatch = _resolve_dispatch(provider=provider, on_inbound=None, run_turn=run_turn)
    dispatch(_update("ping"))

    assert captured["session_key"] == "agent:main:telegram:42"
    assert provider.sent[0].text == "pong"
