"""E3 — gated live Discord adapter tests.

Covers: gate-off inertness, gate-on read→dispatch, message dedup, outbound
deliver, [SILENT] suppression, evidence redaction, import cleanliness.
"""
from __future__ import annotations

import subprocess
import sys
from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from magi_agent.channels.discord_adapter import (
    DiscordEventRequest,
    DiscordInboundEvent,
    DiscordProviderSendRequest,
)
from magi_agent.channels.discord_live import (
    DiscordLiveEventState,
    deliver,
    is_live_discord_enabled,
    read_and_dispatch,
)

_LIVE_ENV = "MAGI_CHANNEL_LIVE_DISCORD"


def _msg(message_id: str, channel_id: str = "chan-1", text: str = "hello") -> dict[str, Any]:
    return {
        "type": "message_create",
        "author": {"id": "user-1", "bot": False},
        "content": text,
        "id": message_id,
        "channel_id": channel_id,
        "is_dm": True,
    }


class _FakeDiscordProvider:
    """In-memory fake implementing the DiscordProviderPort contract.

    Carries ``openmagi_local_fake_provider = True`` — the trust marker the
    ``DiscordAdapterBoundary`` projection requires before it will route through
    a provider.  The operator's real injected provider must set the same marker.
    """

    openmagi_local_fake_provider = True

    def __init__(self, events: Sequence[Mapping[str, Any]] | None = None) -> None:
        self._events = list(events or [])
        self.read_calls = 0
        self.sent: list[DiscordProviderSendRequest] = []

    def read_events(self, request: DiscordEventRequest) -> Sequence[Mapping[str, Any]]:
        self.read_calls += 1
        return list(self._events)

    def send_message(self, request: DiscordProviderSendRequest) -> Mapping[str, object]:
        self.sent.append(request)
        return {"providerMessageId": f"ack-{len(self.sent)}"}

    def send_file(self, request: DiscordProviderSendRequest) -> Mapping[str, object]:
        return {"providerMessageId": "ack-file"}

    def send_typing(self, request: DiscordProviderSendRequest) -> Mapping[str, object]:
        return {"ok": True}


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------

def test_gate_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_LIVE_ENV, raising=False)
    assert is_live_discord_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE"])
def test_gate_truthy_values(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(_LIVE_ENV, value)
    assert is_live_discord_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
def test_gate_falsy_values(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(_LIVE_ENV, value)
    assert is_live_discord_enabled() is False


# ---------------------------------------------------------------------------
# read_and_dispatch — gate-off
# ---------------------------------------------------------------------------

def test_read_gate_off_does_not_call_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_LIVE_ENV, raising=False)
    provider = _FakeDiscordProvider([_msg("m1")])
    state = DiscordLiveEventState()
    received: list[DiscordInboundEvent] = []
    evidence: dict[str, object] = {}
    count = read_and_dispatch(provider, state, on_inbound=received.append, evidence=evidence)
    assert count == 0
    assert provider.read_calls == 0
    assert received == []
    assert evidence["readSkipReason"] == "gate_off"


# ---------------------------------------------------------------------------
# read_and_dispatch — gate-on
# ---------------------------------------------------------------------------

def test_read_dispatches_new_events(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_LIVE_ENV, "1")
    provider = _FakeDiscordProvider([_msg("m1", text="hi"), _msg("m2", text="yo")])
    state = DiscordLiveEventState()
    received: list[DiscordInboundEvent] = []
    evidence: dict[str, object] = {}
    count = read_and_dispatch(provider, state, on_inbound=received.append, evidence=evidence)
    assert count == 2
    assert provider.read_calls == 1
    assert {e.message_id for e in received} == {"m1", "m2"}
    assert evidence["readEventCount"] == 2
    assert evidence["readNewCount"] == 2


def test_read_dedup_same_message_not_redispatched(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_LIVE_ENV, "1")
    provider = _FakeDiscordProvider([_msg("m1")])
    state = DiscordLiveEventState()
    received: list[DiscordInboundEvent] = []
    # First cycle dispatches m1
    read_and_dispatch(provider, state, on_inbound=received.append, evidence={})
    # Second cycle returns the same m1 — must NOT re-dispatch
    second_count = read_and_dispatch(provider, state, on_inbound=received.append, evidence={})
    assert second_count == 0
    assert len(received) == 1


def test_read_evidence_has_no_raw_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_LIVE_ENV, "1")
    secret_text = "my-secret-channel-message-xyz"
    provider = _FakeDiscordProvider([_msg("m1", channel_id="chan-secret", text=secret_text)])
    state = DiscordLiveEventState()
    evidence: dict[str, object] = {}
    read_and_dispatch(provider, state, on_inbound=lambda e: None, evidence=evidence)
    dumped = repr(evidence)
    assert secret_text not in dumped
    assert "chan-secret" not in dumped  # raw channel id never in evidence
    assert any(str(v).startswith("discord-channel:") for v in evidence.get("readChannelDigests", []))


# ---------------------------------------------------------------------------
# deliver
# ---------------------------------------------------------------------------

def test_deliver_gate_off_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_LIVE_ENV, raising=False)
    provider = _FakeDiscordProvider()
    evidence: dict[str, object] = {}
    ok = deliver(provider, "chan-1", "hello", evidence=evidence)
    assert ok is False
    assert provider.sent == []
    assert evidence["deliverSkipReason"] == "gate_off"


def test_deliver_sends_when_gated_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_LIVE_ENV, "1")
    provider = _FakeDiscordProvider()
    evidence: dict[str, object] = {}
    ok = deliver(provider, "chan-1", "hello there", evidence=evidence)
    assert ok is True
    assert len(provider.sent) == 1
    assert evidence["deliverDecisionStatus"] == "sent_local_fake"


def test_deliver_silent_marker_suppressed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_LIVE_ENV, "1")
    provider = _FakeDiscordProvider()
    evidence: dict[str, object] = {}
    ok = deliver(provider, "chan-1", "[SILENT]", evidence=evidence)
    assert ok is True
    assert provider.sent == []  # provider never called
    assert evidence["deliverSuppressed"] is True
    assert evidence["deliverSuppressReason"] == "silent_marker"


def test_deliver_silent_mixed_content_not_suppressed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_LIVE_ENV, "1")
    provider = _FakeDiscordProvider()
    evidence: dict[str, object] = {}
    ok = deliver(provider, "chan-1", "[SILENT] and more", evidence=evidence)
    assert ok is True
    assert len(provider.sent) == 1  # NOT suppressed — mixed content delivered


def test_deliver_evidence_has_no_raw_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_LIVE_ENV, "1")
    provider = _FakeDiscordProvider()
    evidence: dict[str, object] = {}
    deliver(provider, "chan-secret-99", "secret-body", evidence=evidence)
    dumped = repr(evidence)
    assert "secret-body" not in dumped
    assert "chan-secret-99" not in dumped
    assert str(evidence["deliverChannelIdDigest"]).startswith("discord-channel:")


# ---------------------------------------------------------------------------
# Import boundary
# ---------------------------------------------------------------------------

def test_module_has_no_live_network_imports() -> None:
    # Forbids genuine live-network/Discord libs. urllib/socket/subprocess are
    # excluded: they are pulled transitively by pydantic across the whole repo
    # (a pre-existing condition, same exclusion as the other channel boundary
    # tests) — discord_live.py imports none of them directly.
    code = (
        "import sys\n"
        "import magi_agent.channels.discord_live  # noqa: F401\n"
        "forbidden = {'requests','httpx','aiohttp','discord'}\n"
        "loaded = forbidden & set(sys.modules)\n"
        "assert not loaded, f'forbidden live imports: {sorted(loaded)}'\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
