"""E5 TDD tests — channel-live readiness ladder + platform-routing dispatch.

Covers:
- ChannelLiveReadinessConfig: Literal[False] lock, env-off→disabled
- readiness ladder: disabled / shadow / live promotion
- kill-switch: env gate MAGI_CHANNEL_LIVE_KILL_SWITCH_ENABLED→disabled
- env allowlist: environment not allowlisted → disabled
- safetyInvariantsAsserted set (mandatory invariants declared)
- per-platform live-gate states in metadata
- dispatch_live routing to each platform (telegram/discord/slack/email)
- dispatch_live gate-off → each platform returns False
- dispatch_live [SILENT] → suppressed via platform's deliver()
- dispatch_live unknown channel_type → raises ValueError
- import-clean: channel_live_readiness must not pull in live transport libs
"""
from __future__ import annotations

import subprocess
import sys
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(**overrides: object) -> object:
    from magi_agent.gates.channel_live_readiness import ChannelLiveReadinessConfig
    defaults: dict[str, object] = {
        "enabled": False,
        "kill_switch_enabled": True,
        "shadow_mode_enabled": False,
        "selected_bot_digest": "",
        "selected_owner_user_id_digest": "",
        "environment": "local",
        "environment_allowlist": (),
        "promoted_gate": 0,
        "canary_promotion_confirmed": False,
    }
    defaults.update(overrides)
    return ChannelLiveReadinessConfig(**defaults)


def _live_config(env: str = "staging") -> object:
    """Config that should pass shadow+live with a specific bot/user."""
    import hashlib
    bot_id = "bot-canary-123"
    user_id = "user-canary-456"

    def sha256(v: str) -> str:
        return "sha256:" + hashlib.sha256(v.encode()).hexdigest()

    from magi_agent.gates.channel_live_readiness import ChannelLiveReadinessConfig
    return ChannelLiveReadinessConfig(
        enabled=True,
        kill_switch_enabled=False,
        shadow_mode_enabled=True,
        selected_bot_digest=sha256(bot_id),
        selected_owner_user_id_digest=sha256(user_id),
        environment=env,
        environment_allowlist=(env,),
        promoted_gate=5,
        canary_promotion_confirmed=True,
    )


class FakeSlackProvider:
    openmagi_local_fake_provider = True
    calls: list[dict[str, Any]]

    def __init__(self) -> None:
        self.calls = []

    def send(self, *, channel: str, text: str, **kw: Any) -> dict[str, object]:
        self.calls.append({"channel": channel, "text": text})
        return {"ok": True, "ts": "ts-1"}


class FakeEmailProvider:
    openmagi_local_fake_provider = True
    calls: list[dict[str, Any]]

    def __init__(self) -> None:
        self.calls = []

    def send(self, *, to: str, subject: str, body: str, **kw: Any) -> dict[str, object]:
        self.calls.append({"to": to, "subject": subject, "body": body})
        return {"messageId": "msg-1"}


# ---------------------------------------------------------------------------
# E5-1: ChannelLiveReadinessConfig structure + Literal[False] lock
# ---------------------------------------------------------------------------

def test_channel_live_readiness_config_live_execution_locked_false() -> None:
    from magi_agent.gates.channel_live_readiness import ChannelLiveReadinessConfig
    cfg = ChannelLiveReadinessConfig()
    assert cfg.live_execution_allowed is False


def test_channel_live_readiness_config_cannot_set_live_execution_true() -> None:
    from magi_agent.gates.channel_live_readiness import ChannelLiveReadinessConfig
    # Forging live_execution_allowed=True must be silently coerced to False
    cfg = ChannelLiveReadinessConfig(live_execution_allowed=True)  # type: ignore[call-arg]
    assert cfg.live_execution_allowed is False


def test_channel_live_readiness_config_defaults_are_safe() -> None:
    from magi_agent.gates.channel_live_readiness import ChannelLiveReadinessConfig
    cfg = ChannelLiveReadinessConfig()
    assert cfg.enabled is False
    assert cfg.kill_switch_enabled is True
    assert cfg.shadow_mode_enabled is False
    assert cfg.promoted_gate == 0
    assert cfg.canary_promotion_confirmed is False


# ---------------------------------------------------------------------------
# E5-2: Readiness health metadata — env gate OFF → disabled
# ---------------------------------------------------------------------------

def test_readiness_env_gate_off_platform_states_all_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """With all platform env gates off, platformGateStates must show all False."""
    monkeypatch.delenv("MAGI_CHANNEL_LIVE_TELEGRAM", raising=False)
    monkeypatch.delenv("MAGI_CHANNEL_LIVE_DISCORD", raising=False)
    monkeypatch.delenv("MAGI_CHANNEL_LIVE_SLACK", raising=False)
    monkeypatch.delenv("MAGI_CHANNEL_LIVE_EMAIL", raising=False)
    monkeypatch.delenv("MAGI_CHANNEL_LIVE_KILL_SWITCH_ENABLED", raising=False)

    from magi_agent.gates.channel_live_readiness import channel_live_readiness_health_metadata
    cfg = _make_config(enabled=True, kill_switch_enabled=False)
    meta = channel_live_readiness_health_metadata(cfg, bot_id="b", user_id="u")

    # All env gates are off — each platform gate state should be False
    gate_states: dict[str, object] = meta.get("platformGateStates", {})  # type: ignore[assignment]
    assert isinstance(gate_states, dict)
    for platform in ("telegram", "discord", "slack", "email"):
        assert gate_states.get(platform) is False, f"{platform} gate should be False when env not set"


def test_readiness_gate_off_config_disabled_never_live(monkeypatch: pytest.MonkeyPatch) -> None:
    """Config with enabled=False always returns disabled, regardless of env gates."""
    monkeypatch.delenv("MAGI_CHANNEL_LIVE_KILL_SWITCH_ENABLED", raising=False)

    from magi_agent.gates.channel_live_readiness import channel_live_readiness_health_metadata
    cfg = _make_config(enabled=False)
    meta = channel_live_readiness_health_metadata(cfg, bot_id="b", user_id="u")

    assert meta["status"] == "disabled"
    assert meta["executionMode"] == "disabled"
    # All env gates are off — each platform gate state should indicate disabled
    gate_states = meta.get("platformGateStates", {})
    assert isinstance(gate_states, dict)


def test_readiness_gate_disabled_returns_disabled() -> None:
    from magi_agent.gates.channel_live_readiness import channel_live_readiness_health_metadata
    cfg = _make_config(enabled=False)
    meta = channel_live_readiness_health_metadata(cfg, bot_id="b", user_id="u")
    assert meta["status"] == "disabled"
    assert "gate_disabled" in meta["reasonCodes"]


# ---------------------------------------------------------------------------
# E5-3: Kill-switch → disabled
# ---------------------------------------------------------------------------

def test_readiness_kill_switch_forces_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_CHANNEL_LIVE_KILL_SWITCH_ENABLED", "1")

    from magi_agent.gates.channel_live_readiness import channel_live_readiness_health_metadata
    cfg = _make_config(enabled=True, kill_switch_enabled=True)
    meta = channel_live_readiness_health_metadata(cfg, bot_id="b", user_id="u")

    assert meta["executionMode"] == "disabled"
    assert "kill_switch_enabled" in meta["reasonCodes"]


# ---------------------------------------------------------------------------
# E5-4: Environment allowlist gate
# ---------------------------------------------------------------------------

def test_readiness_environment_not_allowlisted_returns_disabled() -> None:
    from magi_agent.gates.channel_live_readiness import channel_live_readiness_health_metadata
    cfg = _make_config(
        enabled=True,
        kill_switch_enabled=False,
        environment="production",
        environment_allowlist=("staging",),
    )
    meta = channel_live_readiness_health_metadata(cfg, bot_id="b", user_id="u")
    assert meta["executionMode"] == "disabled"
    assert "environment_not_allowlisted" in meta["reasonCodes"]


# ---------------------------------------------------------------------------
# E5-5: Shadow readiness
# ---------------------------------------------------------------------------

def test_readiness_shadow_mode_returns_shadow() -> None:
    import hashlib

    def sha256(v: str) -> str:
        return "sha256:" + hashlib.sha256(v.encode()).hexdigest()

    from magi_agent.gates.channel_live_readiness import channel_live_readiness_health_metadata, ChannelLiveReadinessConfig
    cfg = ChannelLiveReadinessConfig(
        enabled=True,
        kill_switch_enabled=False,
        shadow_mode_enabled=True,
        selected_bot_digest=sha256("bot-1"),
        selected_owner_user_id_digest=sha256("user-1"),
        environment="staging",
        environment_allowlist=("staging",),
        promoted_gate=0,
        canary_promotion_confirmed=False,
    )
    meta = channel_live_readiness_health_metadata(cfg, bot_id="bot-1", user_id="user-1")
    assert meta["executionMode"] == "shadow"
    assert meta["readinessReady"] is True


# ---------------------------------------------------------------------------
# E5-6: Live (canary) promotion
# ---------------------------------------------------------------------------

def test_readiness_live_promotion_at_canary_gate() -> None:
    import hashlib

    def sha256(v: str) -> str:
        return "sha256:" + hashlib.sha256(v.encode()).hexdigest()

    from magi_agent.gates.channel_live_readiness import channel_live_readiness_health_metadata, ChannelLiveReadinessConfig, _CANARY_LIVE_GATE
    cfg = ChannelLiveReadinessConfig(
        enabled=True,
        kill_switch_enabled=False,
        shadow_mode_enabled=True,
        selected_bot_digest=sha256("bot-canary"),
        selected_owner_user_id_digest=sha256("user-canary"),
        environment="staging",
        environment_allowlist=("staging",),
        promoted_gate=_CANARY_LIVE_GATE,
        canary_promotion_confirmed=True,
    )
    meta = channel_live_readiness_health_metadata(
        cfg, bot_id="bot-canary", user_id="user-canary"
    )
    assert meta["executionMode"] == "live"
    assert meta["liveExecutionAllowed"] is True


def test_readiness_no_live_without_canary_confirmation() -> None:
    import hashlib

    def sha256(v: str) -> str:
        return "sha256:" + hashlib.sha256(v.encode()).hexdigest()

    from magi_agent.gates.channel_live_readiness import channel_live_readiness_health_metadata, ChannelLiveReadinessConfig, _CANARY_LIVE_GATE
    cfg = ChannelLiveReadinessConfig(
        enabled=True,
        kill_switch_enabled=False,
        shadow_mode_enabled=True,
        selected_bot_digest=sha256("bot-canary"),
        selected_owner_user_id_digest=sha256("user-canary"),
        environment="staging",
        environment_allowlist=("staging",),
        promoted_gate=_CANARY_LIVE_GATE,
        canary_promotion_confirmed=False,  # not confirmed!
    )
    meta = channel_live_readiness_health_metadata(
        cfg, bot_id="bot-canary", user_id="user-canary"
    )
    # Without confirmation, should be shadow not live
    assert meta["executionMode"] in {"shadow", "disabled"}
    assert meta["executionMode"] != "live"


# ---------------------------------------------------------------------------
# E5-7: Safety invariants surface
# ---------------------------------------------------------------------------

def test_readiness_safety_invariants_asserted_set() -> None:
    from magi_agent.gates.channel_live_readiness import channel_live_readiness_health_metadata
    cfg = _make_config()
    meta = channel_live_readiness_health_metadata(cfg, bot_id="b", user_id="u")
    invariants = meta.get("safetyInvariantsAsserted", set())
    assert "default_off" in invariants
    assert "injected_provider_only" in invariants
    assert "silent_suppression" in invariants
    assert "redacted_evidence" in invariants
    assert "no_core_literal_edit" in invariants


# ---------------------------------------------------------------------------
# E5-8: Per-platform gate states
# ---------------------------------------------------------------------------

def test_readiness_platform_gate_states_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_CHANNEL_LIVE_TELEGRAM", "1")
    monkeypatch.delenv("MAGI_CHANNEL_LIVE_DISCORD", raising=False)
    monkeypatch.delenv("MAGI_CHANNEL_LIVE_SLACK", raising=False)
    monkeypatch.delenv("MAGI_CHANNEL_LIVE_EMAIL", raising=False)

    from magi_agent.gates.channel_live_readiness import channel_live_readiness_health_metadata
    cfg = _make_config(enabled=True)
    meta = channel_live_readiness_health_metadata(cfg, bot_id="b", user_id="u")

    gate_states: dict[str, object] = meta.get("platformGateStates", {})  # type: ignore[assignment]
    assert "telegram" in gate_states
    assert "discord" in gate_states
    assert "slack" in gate_states
    assert "email" in gate_states
    assert gate_states["telegram"] is True
    assert gate_states["discord"] is False
    assert gate_states["slack"] is False
    assert gate_states["email"] is False


# ---------------------------------------------------------------------------
# E5-14: platformGateStates uses permissive truthy-env (mirrors adapter gating)
# ---------------------------------------------------------------------------

def test_readiness_platform_gate_state_permissive_matches_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MAGI_CHANNEL_LIVE_SLACK='enabled' must make platformGateStates['slack'] True.

    This mirrors ``is_live_slack_enabled()``'s permissive check: any non-empty
    value that is not one of {"0","false","no","off"} is treated as ON.
    Without this alignment the health dashboard can misreport while the adapter
    actually sends (ops-consistency bug I1).
    """
    monkeypatch.setenv("MAGI_CHANNEL_LIVE_SLACK", "enabled")
    monkeypatch.delenv("MAGI_CHANNEL_LIVE_TELEGRAM", raising=False)
    monkeypatch.delenv("MAGI_CHANNEL_LIVE_DISCORD", raising=False)
    monkeypatch.delenv("MAGI_CHANNEL_LIVE_EMAIL", raising=False)

    from magi_agent.gates.channel_live_readiness import channel_live_readiness_health_metadata
    from magi_agent.channels.slack_live import is_live_slack_enabled

    cfg = _make_config(enabled=True)
    meta = channel_live_readiness_health_metadata(cfg, bot_id="b", user_id="u")
    gate_states: dict[str, object] = meta.get("platformGateStates", {})  # type: ignore[assignment]

    # The adapter says True for "enabled" — the readiness gate must agree.
    adapter_says = is_live_slack_enabled()
    assert adapter_says is True, "adapter should treat 'enabled' as truthy"
    assert gate_states.get("slack") is True, (
        "platformGateStates['slack'] must match is_live_slack_enabled() for 'enabled' value"
    )


# ---------------------------------------------------------------------------
# E5-9: dispatch_live routing — gate OFF, all platforms return False
# ---------------------------------------------------------------------------

def test_dispatch_live_telegram_gate_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAGI_CHANNEL_LIVE_TELEGRAM", raising=False)

    # Use a minimal fake telegram port
    class FakeTgPort:
        openmagi_local_fake_provider = True
        openmagi_delivery_ack_guaranteed = True
        def delete_webhook(self) -> dict[str, Any]: return {"ok": True}
        def poll_updates(self, r: Any) -> list: return []
        def send_message(self, r: Any) -> dict[str, object]: return {"providerMessageId": "m"}

    from magi_agent.gates.channel_live_readiness import dispatch_live
    evidence: dict[str, object] = {}
    result = dispatch_live("telegram", FakeTgPort(), "chat-1", "hello", evidence=evidence)
    assert result is False
    assert evidence.get("deliverSkipReason") == "gate_off"


def test_dispatch_live_discord_gate_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAGI_CHANNEL_LIVE_DISCORD", raising=False)

    class FakeDcPort:
        openmagi_local_fake_provider = True
        def read_events(self, r: Any) -> list: return []
        def send_message(self, r: Any) -> dict[str, object]: return {"providerMessageId": "m"}
        def send_file(self, r: Any) -> dict[str, object]: return {"providerMessageId": "f"}
        def send_typing(self, r: Any) -> dict[str, object]: return {"ok": True}

    from magi_agent.gates.channel_live_readiness import dispatch_live
    evidence: dict[str, object] = {}
    result = dispatch_live("discord", FakeDcPort(), "ch-1", "hello", evidence=evidence)
    assert result is False
    assert evidence.get("deliverSkipReason") == "gate_off"


def test_dispatch_live_slack_gate_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAGI_CHANNEL_LIVE_SLACK", raising=False)

    from magi_agent.gates.channel_live_readiness import dispatch_live
    evidence: dict[str, object] = {}
    result = dispatch_live("slack", FakeSlackProvider(), "#general", "hello", evidence=evidence)
    assert result is False
    assert evidence.get("deliverSkipReason") == "gate_off"


def test_dispatch_live_email_gate_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAGI_CHANNEL_LIVE_EMAIL", raising=False)

    from magi_agent.gates.channel_live_readiness import dispatch_live
    evidence: dict[str, object] = {}
    result = dispatch_live("email", FakeEmailProvider(), "u@example.com", "hello", evidence=evidence)
    assert result is False
    assert evidence.get("deliverSkipReason") == "gate_off"


# ---------------------------------------------------------------------------
# E5-10: dispatch_live routing — gate ON, routes to correct platform
# ---------------------------------------------------------------------------

def test_dispatch_live_slack_gate_on_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_CHANNEL_LIVE_SLACK", "1")

    from magi_agent.gates.channel_live_readiness import dispatch_live
    provider = FakeSlackProvider()
    evidence: dict[str, object] = {}
    result = dispatch_live("slack", provider, "#general", "hello", evidence=evidence)

    assert result is True
    assert len(provider.calls) == 1
    assert provider.calls[0]["channel"] == "#general"


def test_dispatch_live_email_gate_on_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_CHANNEL_LIVE_EMAIL", "1")

    from magi_agent.gates.channel_live_readiness import dispatch_live
    provider = FakeEmailProvider()
    evidence: dict[str, object] = {}
    result = dispatch_live("email", provider, "u@example.com", "hello", evidence=evidence)

    assert result is True
    assert len(provider.calls) == 1


# ---------------------------------------------------------------------------
# E5-11: dispatch_live [SILENT] suppression via platform deliver
# ---------------------------------------------------------------------------

def test_dispatch_live_slack_silent_suppressed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_CHANNEL_LIVE_SLACK", "1")

    from magi_agent.gates.channel_live_readiness import dispatch_live
    provider = FakeSlackProvider()
    evidence: dict[str, object] = {}
    result = dispatch_live("slack", provider, "#general", "[SILENT]", evidence=evidence)

    assert result is True
    assert provider.calls == []
    assert evidence.get("deliverSuppressed") is True


# ---------------------------------------------------------------------------
# E5-12: dispatch_live unknown channel_type → ValueError
# ---------------------------------------------------------------------------

def test_dispatch_live_unknown_channel_type_raises() -> None:
    from magi_agent.gates.channel_live_readiness import dispatch_live

    with pytest.raises(ValueError, match="unknown channel_type"):
        dispatch_live("fax", object(), "target", "text", evidence={})


# ---------------------------------------------------------------------------
# E5-13: Import cleanliness (channel_live_readiness must stay network-free)
# ---------------------------------------------------------------------------

def test_channel_live_readiness_import_no_network_libs() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("magi_agent.gates.channel_live_readiness")
forbidden = ("requests", "httpx", "slack_sdk", "smtplib", "urllib3", "aiohttp", "telegram", "discord")
loaded = [m for m in forbidden if m in sys.modules]
if loaded:
    raise AssertionError(f"channel_live_readiness loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
