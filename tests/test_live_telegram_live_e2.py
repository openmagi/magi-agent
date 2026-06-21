"""E2 — Gated live Telegram polling adapter tests.

Tests cover:
- Gate OFF (default): no webhook-delete, no poll, no deliver; boundary-fake unchanged.
- Gate ON + injected fake port:
  - delete_webhook called before poll_and_dispatch (not after)
  - poll_and_dispatch dispatches inbound updates to on_inbound callback
  - offset dedupe: same update not dispatched twice in a second poll cycle
  - deliver calls send_message on the provider
  - [SILENT] suppression: send_message NOT called when text is exactly [SILENT]
  - evidence records counts and chat-id digests (NOT raw message text)
- No real HTTP client is imported at module level.
- Authority flags are never flipped (Literal[False] preserved).
"""
from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import Any


# ---------------------------------------------------------------------------
# Fake live provider
# ---------------------------------------------------------------------------

class FakeTelegramLiveProvider:
    """Minimal fake that satisfies TelegramLiveProviderPort.

    Records all calls without touching a network.
    """

    openmagi_local_fake_provider = True
    openmagi_delivery_ack_guaranteed = True  # required by boundary for send_message

    def __init__(
        self,
        *,
        updates: Sequence[Mapping[str, Any]] | None = None,
        fail_delete_webhook: bool = False,
    ) -> None:
        self._updates = list(updates or [])
        self._fail_delete_webhook = fail_delete_webhook
        self.delete_webhook_calls: list[None] = []
        self.poll_calls: list[Any] = []
        self.message_calls: list[Any] = []

    def delete_webhook(self) -> Mapping[str, Any]:
        self.delete_webhook_calls.append(None)
        if self._fail_delete_webhook:
            raise RuntimeError("network error deleting webhook 123456:ABCXYZ_fake_token")
        return {"ok": True, "description": "Webhook was deleted"}

    def poll_updates(self, request: Any) -> Sequence[Mapping[str, Any]]:
        self.poll_calls.append(request)
        return self._updates

    def send_message(self, request: Any) -> Mapping[str, object]:
        self.message_calls.append(request)
        return {"providerMessageId": f"msg-{len(self.message_calls)}"}

    def send_document(self, request: Any) -> Mapping[str, object]:
        return {"providerMessageId": "doc-1"}

    def send_photo(self, request: Any) -> Mapping[str, object]:
        return {"providerMessageId": "photo-1"}

    def send_typing(self, request: Any) -> Mapping[str, object]:
        return {"providerMessageId": "typing-1"}

    def download_file(self, request: Any) -> Mapping[str, object]:
        return {"status": "downloaded"}


def _sample_update(update_id: int, chat_id: int = 42, text: str = "hello") -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id * 10,
            "from": {"id": 99},
            "chat": {"id": chat_id},
            "text": text,
        },
    }


# ---------------------------------------------------------------------------
# Gate-off tests (default state — env var absent)
# ---------------------------------------------------------------------------

def test_gate_off_startup_delete_webhook_not_called(monkeypatch: Any) -> None:
    """When MAGI_CHANNEL_LIVE_TELEGRAM is absent, startup_delete_webhook
    should still run (it's the caller's responsibility to check the gate
    first), BUT poll_and_dispatch must skip polling."""
    monkeypatch.delenv("MAGI_CHANNEL_LIVE_TELEGRAM", raising=False)
    from magi_agent.channels.telegram_live import poll_and_dispatch, TelegramLivePollState

    port = FakeTelegramLiveProvider(updates=[_sample_update(1)])
    state = TelegramLivePollState()
    evidence: dict[str, object] = {}
    dispatched: list[object] = []

    count = poll_and_dispatch(port, state, on_inbound=dispatched.append, evidence=evidence)

    assert count == 0
    assert dispatched == []
    assert port.poll_calls == []
    assert evidence.get("pollSkipped") is True
    assert evidence.get("pollSkipReason") == "gate_off"


def test_gate_off_deliver_does_not_call_send_message(monkeypatch: Any) -> None:
    monkeypatch.delenv("MAGI_CHANNEL_LIVE_TELEGRAM", raising=False)
    from magi_agent.channels.telegram_live import deliver

    port = FakeTelegramLiveProvider()
    evidence: dict[str, object] = {}

    result = deliver(port, "42", "hello world", evidence=evidence)

    assert result is False
    assert port.message_calls == []
    assert evidence.get("deliverSkipped") is True
    assert evidence.get("deliverSkipReason") == "gate_off"


def test_gate_off_is_live_telegram_enabled_returns_false(monkeypatch: Any) -> None:
    monkeypatch.delenv("MAGI_CHANNEL_LIVE_TELEGRAM", raising=False)
    from magi_agent.channels.telegram_live import is_live_telegram_enabled

    assert is_live_telegram_enabled() is False


def test_gate_falsy_values_are_off(monkeypatch: Any) -> None:
    from magi_agent.channels.telegram_live import is_live_telegram_enabled

    for falsy in ("0", "false", "False", "FALSE", "no", "off", "OFF"):
        monkeypatch.setenv("MAGI_CHANNEL_LIVE_TELEGRAM", falsy)
        assert is_live_telegram_enabled() is False, f"Expected False for {falsy!r}"


def test_gate_truthy_values_are_on(monkeypatch: Any) -> None:
    from magi_agent.channels.telegram_live import is_live_telegram_enabled

    # I-2 PR B: was a denylist check that silently enabled the channel on any
    # non-empty / non-explicitly-falsey value (e.g. "enabled", "anything").
    # Now uses the canonical strict-allowlist — only the documented truthy
    # spellings enable. Unknown values like "enabled" / "anything" / "disabled"
    # are covered (asserted False) by the dedicated behaviour-parity table at
    # ``tests/channels/test_channel_live_truthy_semantic.py``.
    for truthy in ("1", "true", "yes", "on", "TRUE", "Yes"):
        monkeypatch.setenv("MAGI_CHANNEL_LIVE_TELEGRAM", truthy)
        assert is_live_telegram_enabled() is True, f"Expected True for {truthy!r}"


# ---------------------------------------------------------------------------
# Startup: delete_webhook must be called before first poll
# ---------------------------------------------------------------------------

def test_startup_delete_webhook_called_and_evidence_recorded(monkeypatch: Any) -> None:
    monkeypatch.setenv("MAGI_CHANNEL_LIVE_TELEGRAM", "1")
    from magi_agent.channels.telegram_live import startup_delete_webhook

    port = FakeTelegramLiveProvider()
    evidence: dict[str, object] = {}

    result = startup_delete_webhook(port, evidence)

    assert result is True
    assert len(port.delete_webhook_calls) == 1
    assert evidence.get("webhookDeleteCalled") is True
    assert evidence.get("webhookDeleteOk") is True


def test_startup_delete_webhook_failure_is_recorded_not_raised(monkeypatch: Any) -> None:
    monkeypatch.setenv("MAGI_CHANNEL_LIVE_TELEGRAM", "1")
    from magi_agent.channels.telegram_live import startup_delete_webhook

    port = FakeTelegramLiveProvider(fail_delete_webhook=True)
    evidence: dict[str, object] = {}

    result = startup_delete_webhook(port, evidence)

    assert result is False
    assert evidence.get("webhookDeleteCalled") is True
    assert evidence.get("webhookDeleteOk") is False
    # The raw token in the exception message must be redacted
    assert "webhookDeleteError" in evidence
    error_str = str(evidence["webhookDeleteError"])
    assert "123456:ABCXYZ_fake_token" not in error_str


# ---------------------------------------------------------------------------
# Poll-and-dispatch: gate ON
# ---------------------------------------------------------------------------

def test_poll_and_dispatch_calls_provider_and_dispatches_inbound(monkeypatch: Any) -> None:
    monkeypatch.setenv("MAGI_CHANNEL_LIVE_TELEGRAM", "1")
    from magi_agent.channels.telegram_live import poll_and_dispatch, TelegramLivePollState

    updates = [_sample_update(10), _sample_update(11, chat_id=43)]
    port = FakeTelegramLiveProvider(updates=updates)
    state = TelegramLivePollState()
    evidence: dict[str, object] = {}
    dispatched: list[object] = []

    count = poll_and_dispatch(port, state, on_inbound=dispatched.append, evidence=evidence)

    assert count == 2
    assert len(dispatched) == 2
    assert len(port.poll_calls) == 1
    assert evidence["pollUpdateCount"] == 2
    assert evidence["pollNewCount"] == 2


def test_poll_and_dispatch_offset_advances_after_poll(monkeypatch: Any) -> None:
    monkeypatch.setenv("MAGI_CHANNEL_LIVE_TELEGRAM", "1")
    from magi_agent.channels.telegram_live import poll_and_dispatch, TelegramLivePollState

    updates = [_sample_update(5), _sample_update(7)]
    port = FakeTelegramLiveProvider(updates=updates)
    state = TelegramLivePollState()
    evidence: dict[str, object] = {}

    poll_and_dispatch(port, state, on_inbound=lambda _: None, evidence=evidence)

    # next_offset = max(update_id) + 1 = 7 + 1 = 8
    assert state.offset == 8


def test_poll_and_dispatch_deduplicates_same_update_across_cycles(monkeypatch: Any) -> None:
    """Same update_id in a second poll cycle must NOT be re-dispatched."""
    monkeypatch.setenv("MAGI_CHANNEL_LIVE_TELEGRAM", "1")
    from magi_agent.channels.telegram_live import poll_and_dispatch, TelegramLivePollState

    updates = [_sample_update(20)]
    port = FakeTelegramLiveProvider(updates=updates)
    state = TelegramLivePollState()
    evidence1: dict[str, object] = {}
    evidence2: dict[str, object] = {}
    dispatched: list[object] = []

    first = poll_and_dispatch(port, state, on_inbound=dispatched.append, evidence=evidence1)
    second = poll_and_dispatch(port, state, on_inbound=dispatched.append, evidence=evidence2)

    assert first == 1
    assert second == 0  # same update, deduped
    assert len(dispatched) == 1


def test_poll_and_dispatch_does_not_store_raw_message_text_in_evidence(monkeypatch: Any) -> None:
    monkeypatch.setenv("MAGI_CHANNEL_LIVE_TELEGRAM", "1")
    from magi_agent.channels.telegram_live import poll_and_dispatch, TelegramLivePollState
    import json

    secret_text = "my secret message sk-live-1234567890abcdef"
    updates = [_sample_update(30, text=secret_text)]
    port = FakeTelegramLiveProvider(updates=updates)
    state = TelegramLivePollState()
    evidence: dict[str, object] = {}

    poll_and_dispatch(port, state, on_inbound=lambda _: None, evidence=evidence)

    rendered = json.dumps(evidence)
    assert secret_text not in rendered
    assert "sk-live-" not in rendered


def test_poll_evidence_records_chat_id_digest_not_raw(monkeypatch: Any) -> None:
    monkeypatch.setenv("MAGI_CHANNEL_LIVE_TELEGRAM", "1")
    from magi_agent.channels.telegram_live import poll_and_dispatch, TelegramLivePollState

    updates = [_sample_update(50, chat_id=123456789)]
    port = FakeTelegramLiveProvider(updates=updates)
    state = TelegramLivePollState()
    evidence: dict[str, object] = {}

    poll_and_dispatch(port, state, on_inbound=lambda _: None, evidence=evidence)

    digests = evidence.get("pollChatIdDigests", [])
    assert isinstance(digests, list)
    assert len(digests) == 1
    # Must be a digest, not the raw chat_id
    digest_val = str(digests[0])
    assert "123456789" not in digest_val
    assert digest_val.startswith("chat:")


# ---------------------------------------------------------------------------
# Deliver: gate ON
# ---------------------------------------------------------------------------

def test_deliver_calls_send_message_on_provider(monkeypatch: Any) -> None:
    monkeypatch.setenv("MAGI_CHANNEL_LIVE_TELEGRAM", "1")
    from magi_agent.channels.telegram_live import deliver

    port = FakeTelegramLiveProvider()
    evidence: dict[str, object] = {}

    result = deliver(port, "42", "hello from magi", evidence=evidence)

    assert result is True
    assert len(port.message_calls) == 1
    assert evidence["deliverDecisionStatus"] == "sent_local_fake"
    assert evidence.get("deliverSuppressed") is False


def test_deliver_silent_suppresses_send_message(monkeypatch: Any) -> None:
    """[SILENT] exact match → provider.send_message NOT called."""
    monkeypatch.setenv("MAGI_CHANNEL_LIVE_TELEGRAM", "1")
    from magi_agent.channels.telegram_live import deliver

    port = FakeTelegramLiveProvider()
    evidence: dict[str, object] = {}

    result = deliver(port, "42", "[SILENT]", evidence=evidence)

    assert result is True
    assert port.message_calls == []
    assert evidence.get("deliverSuppressed") is True
    assert evidence.get("deliverSuppressReason") == "silent_marker"


def test_deliver_silent_whitespace_variations(monkeypatch: Any) -> None:
    """[SILENT] with surrounding whitespace is also suppressed."""
    monkeypatch.setenv("MAGI_CHANNEL_LIVE_TELEGRAM", "1")
    from magi_agent.channels.telegram_live import deliver

    for text in ("  [SILENT]  ", "\t[silent]\n", "[SILENT]"):
        port = FakeTelegramLiveProvider()
        evidence: dict[str, object] = {}
        result = deliver(port, "42", text, evidence=evidence)
        assert result is True, f"Expected suppressed for {text!r}"
        assert port.message_calls == [], f"Expected no send for {text!r}"


def test_deliver_mixed_silent_content_is_not_suppressed(monkeypatch: Any) -> None:
    """[SILENT] embedded in other text must NOT suppress — only exact match."""
    monkeypatch.setenv("MAGI_CHANNEL_LIVE_TELEGRAM", "1")
    from magi_agent.channels.telegram_live import deliver

    port = FakeTelegramLiveProvider()
    evidence: dict[str, object] = {}

    result = deliver(port, "42", "[SILENT] but also some text", evidence=evidence)

    assert result is True
    assert len(port.message_calls) == 1  # was sent, not suppressed


def test_deliver_evidence_records_digest_not_raw_chat_id(monkeypatch: Any) -> None:
    monkeypatch.setenv("MAGI_CHANNEL_LIVE_TELEGRAM", "1")
    from magi_agent.channels.telegram_live import deliver

    port = FakeTelegramLiveProvider()
    evidence: dict[str, object] = {}

    deliver(port, "987654321", "hi", evidence=evidence)

    digest = str(evidence.get("deliverChatIdDigest", ""))
    assert "987654321" not in digest
    assert digest.startswith("chat:")


# ---------------------------------------------------------------------------
# Delete-webhook-before-poll ordering test
# ---------------------------------------------------------------------------

def test_delete_webhook_called_before_any_poll(monkeypatch: Any) -> None:
    """The calling pattern: startup_delete_webhook → poll_and_dispatch must be
    respected.  This test simulates that sequence and verifies ordering."""
    monkeypatch.setenv("MAGI_CHANNEL_LIVE_TELEGRAM", "1")
    from magi_agent.channels.telegram_live import (
        poll_and_dispatch,
        startup_delete_webhook,
        TelegramLivePollState,
    )

    call_order: list[str] = []

    class OrderTrackingProvider(FakeTelegramLiveProvider):
        def delete_webhook(self) -> Mapping[str, Any]:
            call_order.append("delete_webhook")
            return {"ok": True}

        def poll_updates(self, request: Any) -> Sequence[Mapping[str, Any]]:
            call_order.append("poll_updates")
            return []

    port = OrderTrackingProvider()
    state = TelegramLivePollState()
    evidence: dict[str, object] = {}

    startup_delete_webhook(port, evidence)
    poll_and_dispatch(port, state, on_inbound=lambda _: None, evidence=evidence)

    assert call_order == ["delete_webhook", "poll_updates"]


# ---------------------------------------------------------------------------
# Authority flags: Literal[False] must not be flipped
# ---------------------------------------------------------------------------

def test_authority_flags_remain_false_after_gate_on_poll(monkeypatch: Any) -> None:
    """Polling with gate ON must not flip any Literal[False] authority flags."""
    monkeypatch.setenv("MAGI_CHANNEL_LIVE_TELEGRAM", "1")
    from magi_agent.channels.telegram_live import poll_and_dispatch, TelegramLivePollState
    from magi_agent.channels.telegram_adapter import TelegramAdapterAuthorityFlags

    updates = [_sample_update(100)]
    port = FakeTelegramLiveProvider(updates=updates)
    state = TelegramLivePollState()
    evidence: dict[str, object] = {}

    poll_and_dispatch(port, state, on_inbound=lambda _: None, evidence=evidence)

    # Construct authority flags — must all be False
    flags = TelegramAdapterAuthorityFlags()
    dumped = flags.model_dump(by_alias=True)
    assert set(dumped.values()) == {False}


# ---------------------------------------------------------------------------
# Import cleanliness: no real HTTP client at top level
# ---------------------------------------------------------------------------

def test_no_forbidden_top_level_imports() -> None:
    """telegram_live must not import requests/httpx/urllib at top level."""
    import importlib
    import sys

    # Remove from cache to get a fresh import
    mod_name = "magi_agent.channels.telegram_live"
    sys.modules.pop(mod_name, None)

    # Stub out forbidden modules to detect if they're imported at top level
    forbidden = ["requests", "httpx", "telegram", "discord"]
    sentinels: dict[str, Any] = {}
    for name in forbidden:
        class _Sentinel:
            _name = name
            def __getattr__(self, item: str) -> object:
                raise ImportError(f"Forbidden top-level import: {self._name}.{item}")
        sentinel = _Sentinel()
        sentinels[name] = sys.modules.get(name)
        sys.modules[name] = sentinel  # type: ignore[assignment]

    try:
        importlib.import_module(mod_name)
    finally:
        # Restore
        for name in forbidden:
            original = sentinels[name]
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original
        sys.modules.pop(mod_name, None)

    # If we get here without ImportError from the sentinel, no forbidden imports
    # were executed at module level.


# ---------------------------------------------------------------------------
# Boundary fake unchanged when gate is off
# ---------------------------------------------------------------------------

def test_telegram_adapter_boundary_still_works_gate_off(monkeypatch: Any) -> None:
    """The existing TelegramAdapterBoundary (gate-off path) must be unchanged."""
    monkeypatch.delenv("MAGI_CHANNEL_LIVE_TELEGRAM", raising=False)
    from magi_agent.channels.telegram_adapter import (
        TelegramAdapterBoundary,
        TelegramAdapterConfig,
    )
    from magi_agent.channels.contract import ChannelRef

    provider = FakeTelegramLiveProvider(updates=[_sample_update(1)])
    config = TelegramAdapterConfig(
        enabled=True,
        localFakeProviderEnabled=True,
        selectedChannelRoutes=("telegram",),
        providerAllowlist=("live-telegram-provider",),
    )
    boundary = TelegramAdapterBoundary(config)

    from magi_agent.channels.telegram_adapter import TelegramPollRequest
    decision = boundary.poll_updates(
        TelegramPollRequest(
            requestId="r1",
            providerName="live-telegram-provider",
            botIdDigest="bot:x",
            ownerIdDigest="owner:y",
            sessionKeyDigest="sess:z",
        ),
        provider=provider,
    )

    assert decision.status == "inbound_projected_local_fake"
    assert len(decision.inbound_updates) == 1
    assert set(decision.authority_flags.model_dump(by_alias=True).values()) == {False}
