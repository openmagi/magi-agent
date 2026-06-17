"""Discord live wiring -> shared turn bridge (PR2).

Mirrors the telegram real path: the real provider read/deliver lives in the
wiring layer and BYPASSES the fake-only DiscordAdapterBoundary, reusing
discord_adapter._project_event for normalisation/redaction.  Tested with an
in-memory provider, no discord.py, no network.
"""
from __future__ import annotations

from typing import Any

import pytest

from magi_agent.channels.turn_bridge import ChannelInbound
from magi_agent.gateway.channel_watchers import (
    build_discord_bridge_on_inbound,
    build_discord_read_once,
)

_LIVE_ENV = "MAGI_CHANNEL_LIVE_DISCORD"


def _raw(message_id: str, channel_id: str = "c1", text: str = "hi") -> dict[str, Any]:
    return {
        "type": "message_create",
        "author": {"id": "u1", "bot": False},
        "content": text,
        "id": message_id,
        "channel_id": channel_id,
        "is_dm": True,
    }


class _FakeDiscordProvider:
    openmagi_local_fake_provider = False

    def __init__(self, events: list[dict[str, Any]] | None = None) -> None:
        self._events = list(events or [])
        self.sent: list[Any] = []

    def read_events(self, request: Any) -> list[dict[str, Any]]:
        return list(self._events)

    def send_message(self, request: Any) -> dict[str, object]:
        self.sent.append(request)
        return {"providerMessageId": "d-1"}

    def send_typing(self, request: Any) -> dict[str, object]:
        return {}


def test_read_once_projects_and_dedups(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_LIVE_ENV, "1")
    provider = _FakeDiscordProvider([_raw("m1"), _raw("m1"), _raw("m2")])
    seen: list[tuple[str, str, str]] = []

    def on_inbound(event: Any) -> None:
        seen.append((event.channel_id, event.message_id, event.text))

    read_once = build_discord_read_once(provider=provider, on_inbound=on_inbound)
    new_count = read_once()

    # m1 appears twice in one batch -> deduped to one dispatch; m2 once.
    assert new_count == 2
    assert seen == [("c1", "m1", "hi"), ("c1", "m2", "hi")]


def test_read_once_inert_when_gate_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_LIVE_ENV, raising=False)
    provider = _FakeDiscordProvider([_raw("m1")])
    calls: list[Any] = []

    read_once = build_discord_read_once(
        provider=provider, on_inbound=lambda e: calls.append(e)
    )

    assert read_once() == 0
    assert calls == []


def test_bridge_drives_turn_and_sends_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_LIVE_ENV, "1")
    provider = _FakeDiscordProvider()
    captured: dict[str, object] = {}

    def run_turn(session_key: str, inbound: ChannelInbound) -> str:
        captured["session_key"] = session_key
        captured["text"] = inbound.text
        return "pong"

    on_inbound = build_discord_bridge_on_inbound(provider=provider, run_turn=run_turn)
    # feed a projected inbound event the way read_once would
    from magi_agent.channels.discord_adapter import _project_event

    on_inbound(_project_event(_raw("m9", channel_id="c7", text="ping"), None))

    assert captured["session_key"] == "agent:main:discord:c7"
    assert captured["text"] == "ping"
    assert len(provider.sent) == 1
    assert provider.sent[0].channel_id == "c7"
    assert provider.sent[0].text == "pong"


def test_watcher_fail_closed_when_discord_extra_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # discord.py is not installed in the test env -> even with the gate ON and a
    # token configured, the watcher must fail closed (return None), not crash.
    monkeypatch.setenv(_LIVE_ENV, "1")
    monkeypatch.setenv("MAGI_DISCORD_BOT_TOKEN", "tok-123")

    from magi_agent.gateway.channel_watchers import build_discord_channel_watcher

    assert build_discord_channel_watcher(run_turn=lambda _k, _i: "x") is None


def test_watcher_none_when_gate_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_LIVE_ENV, raising=False)
    from magi_agent.gateway.channel_watchers import build_discord_channel_watcher

    assert build_discord_channel_watcher(run_turn=lambda _k, _i: "x") is None
