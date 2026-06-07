"""E4 TDD tests — Slack + email via registry, gated outbound adapters.

Covers:
- slack/email PlatformEntry self-registers WITHOUT editing ChannelType Literal
- E1 drift test still passes (see note below on fixture isolation)
- gate OFF: deliver returns False, no provider call, evidence shows gate_off
- gate ON + [SILENT]: deliver returns True, provider NOT called
- gate ON + normal text: provider called, evidence redacted (digest not raw)
- evidence never contains raw recipient/target, only digests
- SlackProviderPort / EmailProviderPort: injected fakes only, never real client
- import-clean: no requests/httpx/slack_sdk/smtplib at top level in new modules
"""
from __future__ import annotations

import subprocess
import sys
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Helpers — fake providers
# ---------------------------------------------------------------------------

class FakeSlackProvider:
    """Minimal fake satisfying SlackProviderPort.send(...)."""
    openmagi_local_fake_provider = True

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def send(self, *, channel: str, text: str, **kwargs: Any) -> dict[str, object]:
        self.calls.append({"channel": channel, "text": text, **kwargs})
        return {"ok": True, "ts": f"ts-{len(self.calls)}"}


class FakeEmailProvider:
    """Minimal fake satisfying EmailProviderPort.send(...)."""
    openmagi_local_fake_provider = True

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def send(self, *, to: str, subject: str, body: str, **kwargs: Any) -> dict[str, object]:
        self.calls.append({"to": to, "subject": subject, "body": body, **kwargs})
        return {"messageId": f"msg-{len(self.calls)}"}


# ---------------------------------------------------------------------------
# E4-1: Registry extensibility — NO ChannelType edit needed
# ---------------------------------------------------------------------------

def test_slack_registered_without_editing_channel_type_literal() -> None:
    """Importing slack_live registers 'slack' in the default registry.

    The ChannelType Literal in contract.py must NOT be changed — the test
    verifies this by asserting 'slack' is NOT in the Literal's args.
    """
    from typing import get_args
    from magi_agent.channels.contract import ChannelType
    import magi_agent.channels.slack_live  # trigger registration side-effect  # noqa: F401
    from magi_agent.channels.platform_registry import get_default_registry

    literal_types = set(get_args(ChannelType))
    assert "slack" not in literal_types, "ChannelType Literal must NOT be edited to add slack"
    registry = get_default_registry()
    slack_entry = registry.lookup("slack")
    assert slack_entry is not None, "slack must be in the registry after import"
    assert slack_entry.supports_outbound is True
    assert slack_entry.default_enabled is False


def test_email_registered_without_editing_channel_type_literal() -> None:
    """Importing email_live registers 'email' in the default registry."""
    from typing import get_args
    from magi_agent.channels.contract import ChannelType
    import magi_agent.channels.email_live  # trigger registration  # noqa: F401
    from magi_agent.channels.platform_registry import get_default_registry

    literal_types = set(get_args(ChannelType))
    assert "email" not in literal_types, "ChannelType Literal must NOT be edited to add email"
    registry = get_default_registry()
    email_entry = registry.lookup("email")
    assert email_entry is not None, "email must be in the registry after import"
    assert email_entry.supports_outbound is True
    assert email_entry.default_enabled is False


def test_slack_entry_cron_deliver_env_var_set() -> None:
    import magi_agent.channels.slack_live  # noqa: F401
    from magi_agent.channels.platform_registry import get_default_registry

    entry = get_default_registry().lookup("slack")
    assert entry is not None
    assert entry.cron_deliver_env_var is not None


def test_email_entry_cron_deliver_env_var_set() -> None:
    import magi_agent.channels.email_live  # noqa: F401
    from magi_agent.channels.platform_registry import get_default_registry

    entry = get_default_registry().lookup("email")
    assert entry is not None
    assert entry.cron_deliver_env_var is not None


def test_drift_test_still_passes_after_slack_email_registration() -> None:
    """The E1 drift test must still pass even after slack/email are registered.

    This is satisfied because the drift test (after our adjustment) compares
    only the 4 built-in ChannelType Literal members against the 4 built-in
    registry entries — slack/email are extension entries, not Literal-backed.
    """
    from typing import get_args
    from magi_agent.channels.contract import ChannelType
    import magi_agent.channels.slack_live  # noqa: F401
    import magi_agent.channels.email_live  # noqa: F401
    from magi_agent.channels.platform_registry import get_default_registry

    literal_types = set(get_args(ChannelType))
    registry = get_default_registry()
    # The 4 built-in literal types must ALL be in the registry.
    # Extension types (slack, email) are in the registry but NOT in the Literal.
    # The drift test checks literal_types <= builtin_registry_types, which stays true.
    builtin_types = {e.channel_type for e in registry.list_entries()
                     if e.channel_type in {"web", "app", "telegram", "discord"}}
    assert literal_types == builtin_types


# ---------------------------------------------------------------------------
# E4-2: Slack gate
# ---------------------------------------------------------------------------

_SLACK_ENV = "MAGI_CHANNEL_LIVE_SLACK"


def test_slack_gate_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_SLACK_ENV, raising=False)
    from magi_agent.channels.slack_live import is_live_slack_enabled
    assert is_live_slack_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE", "YES"])
def test_slack_gate_on_for_truthy(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(_SLACK_ENV, value)
    from magi_agent.channels.slack_live import is_live_slack_enabled
    assert is_live_slack_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
def test_slack_gate_off_for_falsy(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(_SLACK_ENV, value)
    from magi_agent.channels.slack_live import is_live_slack_enabled
    assert is_live_slack_enabled() is False


# ---------------------------------------------------------------------------
# E4-3: Slack deliver — gate OFF
# ---------------------------------------------------------------------------

def test_slack_deliver_gate_off_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_SLACK_ENV, raising=False)
    from magi_agent.channels.slack_live import deliver

    provider = FakeSlackProvider()
    evidence: dict[str, object] = {}
    result = deliver(provider, "#general", "hello", evidence=evidence)

    assert result is False
    assert provider.calls == []
    assert evidence.get("deliverSkipped") is True
    assert evidence.get("deliverSkipReason") == "gate_off"


def test_slack_deliver_gate_off_records_channel_digest_not_raw(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_SLACK_ENV, raising=False)
    from magi_agent.channels.slack_live import deliver

    evidence: dict[str, object] = {}
    deliver(FakeSlackProvider(), "#general", "hello", evidence=evidence)
    assert "#general" not in str(evidence), "raw channel id must not appear in evidence"
    assert "deliverChannelDigest" in evidence


# ---------------------------------------------------------------------------
# E4-4: Slack deliver — gate ON, [SILENT] suppression
# ---------------------------------------------------------------------------

def test_slack_deliver_silent_suppressed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_SLACK_ENV, "1")
    from magi_agent.channels.slack_live import deliver

    provider = FakeSlackProvider()
    evidence: dict[str, object] = {}
    result = deliver(provider, "#general", "[SILENT]", evidence=evidence)

    assert result is True
    assert provider.calls == []
    assert evidence.get("deliverSuppressed") is True
    assert evidence.get("deliverSuppressReason") == "silent_marker"


def test_slack_deliver_silent_mixed_content_not_suppressed(monkeypatch: pytest.MonkeyPatch) -> None:
    """[SILENT] embedded in text is NOT suppressed — only exact match."""
    monkeypatch.setenv(_SLACK_ENV, "1")
    from magi_agent.channels.slack_live import deliver

    provider = FakeSlackProvider()
    evidence: dict[str, object] = {}
    result = deliver(provider, "#general", "hello [SILENT] world", evidence=evidence)

    assert result is True
    assert len(provider.calls) == 1


# ---------------------------------------------------------------------------
# E4-5: Slack deliver — gate ON, normal text, provider called
# ---------------------------------------------------------------------------

def test_slack_deliver_gate_on_calls_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_SLACK_ENV, "1")
    from magi_agent.channels.slack_live import deliver

    provider = FakeSlackProvider()
    evidence: dict[str, object] = {}
    result = deliver(provider, "#general", "hello world", evidence=evidence)

    assert result is True
    assert len(provider.calls) == 1
    assert provider.calls[0]["channel"] == "#general"
    assert provider.calls[0]["text"] == "hello world"


def test_slack_deliver_evidence_has_digest_not_raw_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_SLACK_ENV, "1")
    from magi_agent.channels.slack_live import deliver

    evidence: dict[str, object] = {}
    deliver(FakeSlackProvider(), "#secret-channel", "hello", evidence=evidence)
    assert "#secret-channel" not in str(evidence), "raw channel must not appear in evidence"
    assert "deliverChannelDigest" in evidence


def test_slack_deliver_evidence_text_length_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_SLACK_ENV, "1")
    from magi_agent.channels.slack_live import deliver

    evidence: dict[str, object] = {}
    deliver(FakeSlackProvider(), "#general", "hi there", evidence=evidence)
    assert evidence.get("deliverTextLength") == len("hi there")


# ---------------------------------------------------------------------------
# E4-6: Email gate
# ---------------------------------------------------------------------------

_EMAIL_ENV = "MAGI_CHANNEL_LIVE_EMAIL"


def test_email_gate_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_EMAIL_ENV, raising=False)
    from magi_agent.channels.email_live import is_live_email_enabled
    assert is_live_email_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE"])
def test_email_gate_on_for_truthy(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(_EMAIL_ENV, value)
    from magi_agent.channels.email_live import is_live_email_enabled
    assert is_live_email_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
def test_email_gate_off_for_falsy(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(_EMAIL_ENV, value)
    from magi_agent.channels.email_live import is_live_email_enabled
    assert is_live_email_enabled() is False


# ---------------------------------------------------------------------------
# E4-7: Email deliver — gate OFF
# ---------------------------------------------------------------------------

def test_email_deliver_gate_off_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_EMAIL_ENV, raising=False)
    from magi_agent.channels.email_live import deliver

    provider = FakeEmailProvider()
    evidence: dict[str, object] = {}
    result = deliver(provider, "user@example.com", "Hello email", evidence=evidence)

    assert result is False
    assert provider.calls == []
    assert evidence.get("deliverSkipped") is True
    assert evidence.get("deliverSkipReason") == "gate_off"


def test_email_deliver_gate_off_records_recipient_digest_not_raw(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_EMAIL_ENV, raising=False)
    from magi_agent.channels.email_live import deliver

    evidence: dict[str, object] = {}
    deliver(FakeEmailProvider(), "user@example.com", "hello", evidence=evidence)
    assert "user@example.com" not in str(evidence), "raw email must not appear in evidence"
    assert "deliverRecipientDigest" in evidence


# ---------------------------------------------------------------------------
# E4-8: Email deliver — gate ON, [SILENT] suppression
# ---------------------------------------------------------------------------

def test_email_deliver_silent_suppressed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_EMAIL_ENV, "1")
    from magi_agent.channels.email_live import deliver

    provider = FakeEmailProvider()
    evidence: dict[str, object] = {}
    result = deliver(provider, "user@example.com", "[SILENT]", evidence=evidence)

    assert result is True
    assert provider.calls == []
    assert evidence.get("deliverSuppressed") is True
    assert evidence.get("deliverSuppressReason") == "silent_marker"


def test_email_deliver_silent_mixed_not_suppressed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_EMAIL_ENV, "1")
    from magi_agent.channels.email_live import deliver

    provider = FakeEmailProvider()
    evidence: dict[str, object] = {}
    result = deliver(provider, "user@example.com", "note [SILENT] inline", evidence=evidence)

    assert result is True
    assert len(provider.calls) == 1


# ---------------------------------------------------------------------------
# E4-9: Email deliver — gate ON, normal text
# ---------------------------------------------------------------------------

def test_email_deliver_gate_on_calls_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_EMAIL_ENV, "1")
    from magi_agent.channels.email_live import deliver

    provider = FakeEmailProvider()
    evidence: dict[str, object] = {}
    result = deliver(provider, "user@example.com", "Hello from magi", evidence=evidence)

    assert result is True
    assert len(provider.calls) == 1
    assert provider.calls[0]["to"] == "user@example.com"
    assert provider.calls[0]["body"] == "Hello from magi"


def test_email_deliver_evidence_digest_not_raw_recipient(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_EMAIL_ENV, "1")
    from magi_agent.channels.email_live import deliver

    evidence: dict[str, object] = {}
    deliver(FakeEmailProvider(), "secret@example.com", "msg", evidence=evidence)
    assert "secret@example.com" not in str(evidence), "raw email must not appear in evidence"
    assert "deliverRecipientDigest" in evidence


def test_email_deliver_text_length_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_EMAIL_ENV, "1")
    from magi_agent.channels.email_live import deliver

    evidence: dict[str, object] = {}
    deliver(FakeEmailProvider(), "user@example.com", "short", evidence=evidence)
    assert evidence.get("deliverTextLength") == len("short")


# ---------------------------------------------------------------------------
# E4-10: Import cleanliness (slack_live / email_live)
# ---------------------------------------------------------------------------

def test_slack_live_import_no_network_libs() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("magi_agent.channels.slack_live")
forbidden = ("requests", "httpx", "slack_sdk", "slack_bolt", "urllib3", "aiohttp")
loaded = [m for m in forbidden if m in sys.modules]
if loaded:
    raise AssertionError(f"slack_live loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_email_live_import_no_network_libs() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("magi_agent.channels.email_live")
forbidden = ("requests", "httpx", "smtplib", "urllib3", "aiohttp", "sendgrid")
loaded = [m for m in forbidden if m in sys.modules]
if loaded:
    raise AssertionError(f"email_live loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
