"""J-7 — ``email_live.deliver`` accepts ``port=None`` (signature parity).

The pre-J-7 ``deliver`` required a non-None port while ``slack_live`` /
``telegram_live`` accept ``None`` (with an env-built fallback). A caller
using the uniform "pass None when you don't have a hand-injected port"
idiom would ``TypeError`` on email.

J-7 harmonizes the signature: ``deliver(port: EmailProviderPort | None,
...)``. When ``port is None``, ``_default_port_from_env()`` is consulted
(today returns ``None`` because no out-of-box email provider exists),
and the delivery is recorded as ``no_provider`` (fail-soft, no
exception). Mirrors the ``slack_live`` contract exactly.
"""

from __future__ import annotations

import pytest

from magi_agent.channels import email_live


@pytest.fixture(autouse=True)
def _enable_email_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    # The gate must be ON for the delivery code path to reach the
    # provider resolution; otherwise the early ``gate_off`` short-circuit
    # fires before ``port`` is consulted.
    monkeypatch.setenv("MAGI_CHANNEL_LIVE_EMAIL", "1")


def test_deliver_accepts_port_none_without_typeerror() -> None:
    """The pre-J-7 signature ``port: EmailProviderPort`` raised
    ``TypeError`` on a Slack/telegram-style ``deliver(None, ...)`` call.
    Now it returns ``False`` with a structured evidence reason."""

    evidence: dict[str, object] = {}
    result = email_live.deliver(
        None, "user@example.com", "hello", evidence=evidence
    )
    assert result is False
    assert evidence["deliverSkipped"] is True
    assert evidence["deliverSkipReason"] == "no_provider"


def test_deliver_with_none_port_records_recipient_digest() -> None:
    """The recipient digest and text length are still recorded even
    when no provider is available (audit-only path)."""

    evidence: dict[str, object] = {}
    email_live.deliver(None, "user@example.com", "hello", evidence=evidence)
    assert "deliverRecipientDigest" in evidence
    assert evidence["deliverTextLength"] == 5


def test_default_port_from_env_returns_none_today() -> None:
    """No out-of-box SMTP provider exists yet, so the env fallback
    resolves to ``None``. Locked in as a structural guarantee — a future
    provider can be wired by changing this function alone."""

    assert email_live._default_port_from_env() is None


def test_deliver_with_explicit_port_still_works() -> None:
    """Back-compat: a hand-injected port still delivers normally."""

    class _StubPort:
        sent: list[tuple[str, str, str]] = []

        def send(self, *, to: str, subject: str, body: str) -> dict[str, object]:
            self.sent.append((to, subject, body))
            return {"messageId": "msg-123"}

    port = _StubPort()
    evidence: dict[str, object] = {}
    result = email_live.deliver(
        port, "user@example.com", "hello", evidence=evidence
    )
    assert result is True
    assert port.sent == [("user@example.com", "Magi Agent notification", "hello")]
    assert evidence["deliverMessageId"] == "msg-123"


def test_deliver_silent_marker_short_circuits_before_provider_check() -> None:
    """The ``[SILENT]`` marker fires BEFORE port resolution — a None
    port with a silent payload is still ``deliverSuppressed=True``, not
    ``no_provider``."""

    evidence: dict[str, object] = {}
    result = email_live.deliver(
        None, "user@example.com", "[SILENT]", evidence=evidence
    )
    assert result is True
    assert evidence["deliverSuppressed"] is True
    assert evidence["deliverSuppressReason"] == "silent_marker"


def test_deliver_gate_off_skips_before_provider_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``MAGI_CHANNEL_LIVE_EMAIL`` is OFF, the gate fires before
    port resolution — same shape as today."""

    monkeypatch.delenv("MAGI_CHANNEL_LIVE_EMAIL", raising=False)
    evidence: dict[str, object] = {}
    result = email_live.deliver(
        None, "user@example.com", "hello", evidence=evidence
    )
    assert result is False
    assert evidence["deliverSkipReason"] == "gate_off"
