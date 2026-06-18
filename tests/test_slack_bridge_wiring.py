"""Slack live wiring -> shared turn bridge (PR3).

Inbound is a Socket Mode read-cycle (drained queue) projected straight into
ChannelInbound; outbound reuses the Web API provider with thread_ts threading.
Tested with in-memory providers, no slack_sdk, no network.
"""
from __future__ import annotations

from typing import Any

import pytest

from magi_agent.channels.slack_live import _project_slack_event
from magi_agent.channels.turn_bridge import ChannelInbound
from magi_agent.gateway.channel_watchers import (
    build_slack_bridge_on_inbound,
    build_slack_read_once,
)

_LIVE_ENV = "MAGI_CHANNEL_LIVE_SLACK"


def _msg(ts: str, *, channel: str = "C1", text: str = "hi", thread: str | None = None) -> dict[str, Any]:
    return {
        "type": "message",
        "channel": channel,
        "user": "U1",
        "text": text,
        "ts": ts,
        "thread_ts": thread,
    }


class _FakeSocket:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = list(events)

    def read_events(self, request: Any = None) -> list[dict[str, Any]]:
        out = list(self._events)
        self._events = []
        return out


class _FakeSend:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, dict[str, Any]]] = []

    def send(self, *, channel: str, text: str, **kwargs: Any) -> dict[str, object]:
        self.sent.append((channel, text, kwargs))
        return {"ok": True, "ts": "srv-1"}


def test_read_once_projects_and_dedups(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_LIVE_ENV, "1")
    socket = _FakeSocket([_msg("169.1"), _msg("169.1"), _msg("169.2")])
    seen: list[str] = []

    read_once = build_slack_read_once(
        provider=socket, on_inbound=lambda ci: seen.append(ci.message_id)
    )
    new_count = read_once()

    assert new_count == 2
    assert seen == ["169.1", "169.2"]


def test_read_once_inert_when_gate_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_LIVE_ENV, raising=False)
    socket = _FakeSocket([_msg("169.1")])
    calls: list[Any] = []

    read_once = build_slack_read_once(provider=socket, on_inbound=lambda ci: calls.append(ci))
    assert read_once() == 0
    assert calls == []


def test_bridge_drives_turn_and_sends_threaded_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_LIVE_ENV, "1")
    send = _FakeSend()
    captured: dict[str, object] = {}

    def run_turn(session_key: str, inbound: ChannelInbound) -> str:
        captured["session_key"] = session_key
        captured["text"] = inbound.text
        return "pong"

    on_inbound = build_slack_bridge_on_inbound(send_provider=send, run_turn=run_turn)
    on_inbound(_project_slack_event(_msg("169.5", channel="C7", text="ping", thread="169.1")))

    assert captured["session_key"] == "agent:main:slack:C7"
    assert captured["text"] == "ping"
    assert len(send.sent) == 1
    channel, text, kwargs = send.sent[0]
    assert channel == "C7"
    assert text == "pong"
    assert kwargs.get("thread_ts") == "169.1"


def test_watcher_fail_closed_when_slack_extra_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # slack_sdk is not installed in the test env -> even with gate ON and both
    # tokens set, the watcher must fail closed (return None), not crash.
    monkeypatch.setenv(_LIVE_ENV, "1")
    monkeypatch.setenv("MAGI_SLACK_APP_TOKEN", "xapp-1")
    monkeypatch.setenv("MAGI_SLACK_BOT_TOKEN", "xoxb-1")
    from magi_agent.gateway.channel_watchers import build_slack_channel_watcher

    assert build_slack_channel_watcher(run_turn=lambda _k, _i: "x") is None


def test_watcher_none_when_tokens_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_LIVE_ENV, "1")
    for key in ("MAGI_SLACK_APP_TOKEN", "MAGI_SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "SLACK_BOT_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    from magi_agent.gateway.channel_watchers import build_slack_channel_watcher

    assert build_slack_channel_watcher(run_turn=lambda _k, _i: "x") is None


def test_watcher_none_when_gate_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_LIVE_ENV, raising=False)
    from magi_agent.gateway.channel_watchers import build_slack_channel_watcher

    assert build_slack_channel_watcher(run_turn=lambda _k, _i: "x") is None
