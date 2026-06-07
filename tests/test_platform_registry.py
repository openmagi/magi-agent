"""TDD tests for E1: PlatformRegistry + PlatformEntry self-registration.

Design contract verified here:
- PlatformEntry is a frozen model with capability flags
- PlatformRegistry registers/looks up/lists entries; idempotent; rejects dupes
- The 4 built-ins (web, app, telegram, discord) are pre-registered
- A NEW platform can register WITHOUT editing core — the key extensibility test
- is_registered_channel_type() is the registry-driven validator
- No live behaviour change (all entries remain default-off)
"""
from __future__ import annotations

import subprocess
import sys

import pytest

from magi_agent.channels.platform_registry import (
    PlatformEntry,
    PlatformRegistry,
    get_default_registry,
    is_registered_channel_type,
)


# ---------------------------------------------------------------------------
# PlatformEntry construction
# ---------------------------------------------------------------------------


def test_platform_entry_is_frozen() -> None:
    from pydantic import ValidationError

    entry = PlatformEntry(channel_type="web", display_name="Web Chat")
    # Pydantic v2 frozen models raise ValidationError on mutation attempts
    with pytest.raises((AttributeError, TypeError, ValidationError)):
        entry.display_name = "Mutated"  # type: ignore[misc]


def test_platform_entry_defaults_are_default_off() -> None:
    entry = PlatformEntry(channel_type="test-platform", display_name="Test")
    assert entry.supports_inbound is False
    assert entry.supports_outbound is False
    assert entry.supports_cron_delivery is False
    assert entry.default_enabled is False
    assert entry.cron_deliver_env_var is None


def test_platform_entry_capability_flags_can_be_set() -> None:
    entry = PlatformEntry(
        channel_type="telegram",
        display_name="Telegram",
        supports_inbound=True,
        supports_outbound=True,
        supports_cron_delivery=False,
        default_enabled=False,
        cron_deliver_env_var=None,
    )
    assert entry.supports_inbound is True
    assert entry.supports_outbound is True
    assert entry.channel_type == "telegram"


def test_platform_entry_rejects_empty_channel_type() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PlatformEntry(channel_type="", display_name="Bad")


def test_platform_entry_rejects_empty_display_name() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PlatformEntry(channel_type="test", display_name="")


def test_platform_entry_rejects_whitespace_only_channel_type() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PlatformEntry(channel_type="   ", display_name="Bad")


# ---------------------------------------------------------------------------
# PlatformRegistry mechanics
# ---------------------------------------------------------------------------


def test_registry_register_and_lookup() -> None:
    registry = PlatformRegistry()
    entry = PlatformEntry(channel_type="slack", display_name="Slack")
    registry.register(entry)
    assert registry.lookup("slack") == entry


def test_registry_lookup_missing_returns_none() -> None:
    registry = PlatformRegistry()
    assert registry.lookup("nonexistent") is None


def test_registry_list_returns_registered_entries() -> None:
    registry = PlatformRegistry()
    e1 = PlatformEntry(channel_type="p1", display_name="P1")
    e2 = PlatformEntry(channel_type="p2", display_name="P2")
    registry.register(e1)
    registry.register(e2)
    listed = registry.list_entries()
    assert e1 in listed
    assert e2 in listed


def test_registry_idempotent_register_same_object() -> None:
    registry = PlatformRegistry()
    entry = PlatformEntry(channel_type="slack", display_name="Slack")
    registry.register(entry)
    registry.register(entry)  # idempotent — same object
    assert len([e for e in registry.list_entries() if e.channel_type == "slack"]) == 1


def test_registry_idempotent_register_equal_entry() -> None:
    registry = PlatformRegistry()
    e1 = PlatformEntry(channel_type="slack", display_name="Slack")
    e2 = PlatformEntry(channel_type="slack", display_name="Slack")
    registry.register(e1)
    registry.register(e2)  # equal value — idempotent
    assert len([e for e in registry.list_entries() if e.channel_type == "slack"]) == 1


def test_registry_rejects_duplicate_channel_type_with_different_entry() -> None:
    registry = PlatformRegistry()
    registry.register(PlatformEntry(channel_type="slack", display_name="Slack"))
    with pytest.raises(ValueError, match="already registered"):
        registry.register(PlatformEntry(channel_type="slack", display_name="Slack v2"))


def test_registry_list_returns_defensive_copy() -> None:
    registry = PlatformRegistry()
    entry = PlatformEntry(channel_type="test", display_name="Test")
    registry.register(entry)
    first = registry.list_entries()
    second = registry.list_entries()
    assert first == second
    assert first is not second


# ---------------------------------------------------------------------------
# Built-in 4 platforms pre-registered in default registry
# ---------------------------------------------------------------------------


def test_default_registry_has_four_builtin_platforms() -> None:
    registry = get_default_registry()
    types = {e.channel_type for e in registry.list_entries()}
    assert {"web", "app", "telegram", "discord"} <= types


def test_default_registry_web_entry_has_correct_flags() -> None:
    registry = get_default_registry()
    web = registry.lookup("web")
    assert web is not None
    assert web.display_name == "Web Chat"
    assert web.default_enabled is False


def test_default_registry_app_entry_has_correct_flags() -> None:
    registry = get_default_registry()
    app = registry.lookup("app")
    assert app is not None
    assert app.display_name == "Mobile App"
    assert app.supports_cron_delivery is True
    assert app.default_enabled is False


def test_default_registry_telegram_entry_has_correct_flags() -> None:
    registry = get_default_registry()
    tg = registry.lookup("telegram")
    assert tg is not None
    assert tg.display_name == "Telegram"
    assert tg.supports_inbound is True
    assert tg.supports_outbound is True
    assert tg.default_enabled is False


def test_default_registry_discord_entry_has_correct_flags() -> None:
    registry = get_default_registry()
    dc = registry.lookup("discord")
    assert dc is not None
    assert dc.display_name == "Discord"
    assert dc.supports_inbound is True
    assert dc.supports_outbound is True
    assert dc.default_enabled is False


def test_default_registry_all_builtin_entries_are_default_off() -> None:
    registry = get_default_registry()
    for entry in registry.list_entries():
        if entry.channel_type in {"web", "app", "telegram", "discord"}:
            assert entry.default_enabled is False, f"{entry.channel_type} must be default-off"


# ---------------------------------------------------------------------------
# Registry-driven validation (is_registered_channel_type)
# ---------------------------------------------------------------------------


def test_is_registered_channel_type_accepts_builtins() -> None:
    for ct in ("web", "app", "telegram", "discord"):
        assert is_registered_channel_type(ct) is True


def test_is_registered_channel_type_rejects_unknown() -> None:
    assert is_registered_channel_type("unknown-platform") is False
    assert is_registered_channel_type("") is False


# ---------------------------------------------------------------------------
# KEY EXTENSIBILITY TEST: new platform registers WITHOUT editing core
# ---------------------------------------------------------------------------


def test_new_platform_self_registers_without_editing_core() -> None:
    """A third-party platform can register itself using only the public API
    (PlatformEntry + PlatformRegistry().register) — no core edits required.

    The "without editing core" property is proven by using only the public
    register()/lookup() API on a FRESH registry instance, not the shared
    singleton. This avoids any risk of polluting _DEFAULT_REGISTRY if the
    test fails before cleanup.
    """
    # Use a fresh registry — not the shared singleton — to prove isolation
    registry = PlatformRegistry()

    # Simulate what a future E3/E4 platform module would do at import time:
    email_entry = PlatformEntry(
        channel_type="email",
        display_name="Email",
        supports_inbound=True,
        supports_outbound=True,
        supports_cron_delivery=True,
        default_enabled=False,
        cron_deliver_env_var="MAGI_EMAIL_CRON_TARGET",
    )

    # Not yet registered
    assert registry.lookup("email") is None

    # Self-register — the only "edit" is calling register() on the registry
    registry.register(email_entry)

    # Now discoverable via the public lookup() API
    assert registry.lookup("email") == email_entry

    # Unregister also works via public API
    registry.unregister("email")
    assert registry.lookup("email") is None


def test_default_registry_is_prepopulated_with_four_builtins() -> None:
    """The default singleton is pre-populated with exactly the 4 built-ins."""
    registry = get_default_registry()
    types = {e.channel_type for e in registry.list_entries()}
    assert {"web", "app", "telegram", "discord"} <= types


def test_registry_unregister_removes_entry() -> None:
    registry = PlatformRegistry()
    entry = PlatformEntry(channel_type="removable", display_name="Removable")
    registry.register(entry)
    assert registry.lookup("removable") is not None
    registry.unregister("removable")
    assert registry.lookup("removable") is None


def test_registry_unregister_noop_for_missing() -> None:
    registry = PlatformRegistry()
    registry.unregister("never-registered")  # should not raise


# ---------------------------------------------------------------------------
# Drift guard: ChannelType Literal must exactly match registry built-ins
# ---------------------------------------------------------------------------


def test_registry_builtins_exactly_match_channel_type_literal() -> None:
    """Guard against Literal↔registry drift introduced by E2/E3/E4 additions.

    The ChannelType Literal in contract.py and the 4 built-in registry entries
    are kept in sync only by author discipline. This test makes any desync an
    immediate CI failure.
    """
    from typing import get_args

    from magi_agent.channels.contract import ChannelType

    literal_types = set(get_args(ChannelType))
    registry_builtin_types = {e.channel_type for e in get_default_registry().list_entries()}
    assert literal_types == registry_builtin_types, (
        f"Literal-only: {literal_types - registry_builtin_types}, "
        f"Registry-only: {registry_builtin_types - literal_types}"
    )


# ---------------------------------------------------------------------------
# Import-clean: platform_registry must not pull in live traffic modules
# ---------------------------------------------------------------------------


def test_platform_registry_import_stays_traffic_free_in_fresh_process() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("magi_agent.channels.platform_registry")
forbidden_modules = (
    "google.adk",
    "magi_agent.adk_bridge",
    "magi_agent.runtime.openmagi_runtime",
    "magi_agent.transport",
    "magi_agent.tools.dispatcher",
    "magi_agent.hooks.bus",
    "magi_agent.plugins",
)
loaded = [module for module in forbidden_modules if module in sys.modules]
if loaded:
    raise AssertionError(f"platform_registry import loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
