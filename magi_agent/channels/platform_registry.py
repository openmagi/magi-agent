"""E1 — Platform Registry: self-registration seam for channel platforms.

Design rationale
----------------
``contract.py`` uses ``ChannelType = Literal["web", "app", "telegram", "discord"]``
as the validated field type across ``ChannelRef``, ``ChannelAdapterManifest``, and
all adapter configs.  Removing or replacing that literal would cascade through six
files and break all existing tests; the risk exceeds E1's scope.

Instead E1 adds the registry *alongside* the literal as the extensibility seam:

- ``PlatformEntry`` describes a platform's metadata and capability flags.
- ``PlatformRegistry`` is the shared registry; call ``register()`` from any
  platform module at import time to make the platform discoverable.
- ``get_default_registry()`` returns the shared singleton that holds the 4
  built-ins pre-populated.
- ``is_registered_channel_type(ct)`` is the registry-backed validator that future
  platforms (E2+) use instead of the hardcoded literal.

Backward compat: the existing ``ChannelType`` literal, ``ChannelAdapterManifest``,
dispatcher, and all adapters are unchanged.  Any module that already imports from
``channels.contract`` or ``channels.dispatcher`` continues to work identically.

Adding a new platform (e.g. Slack / E4) requires:
  1. Create ``channels/slack_adapter.py`` (or similar).
  2. At module level, call ``get_default_registry().register(PlatformEntry(...))``.
  3. Validate channel_type strings via ``is_registered_channel_type()`` rather than
     the built-in literal.
  No edits to contract.py, dispatcher.py, or any existing adapter are needed.
"""
from __future__ import annotations

import threading

from pydantic import BaseModel, ConfigDict, field_validator


class PlatformEntry(BaseModel):
    """Frozen metadata + capability descriptor for a single channel platform.

    Mirrors Hermes' ``PlatformEntry`` shape so the seam is familiar to OSS
    contributors migrating from Hermes-style platform modules.

    All authority/traffic flags default to False so that registration alone
    carries no live-traffic implication (consistent with Phase 6 default-off).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)

    channel_type: str
    """Canonical identifier string (e.g. "telegram", "slack").  Must be unique
    within a registry and non-empty."""

    display_name: str
    """Human-readable label shown in dashboards and logs."""

    supports_inbound: bool = False
    """Platform can receive inbound messages / events from users."""

    supports_outbound: bool = False
    """Platform can deliver outbound messages to users."""

    supports_cron_delivery: bool = False
    """Delivery can be triggered by the cron/goal-loop subsystem."""

    default_enabled: bool = False
    """Must remain False for all Phase 6 platforms (enforcement by registry)."""

    cron_deliver_env_var: str | None = None
    """Optional env-var name that cron workers read for the delivery target.
    Mirrors Hermes' per-platform env-var hint."""

    @field_validator("channel_type")
    @classmethod
    def _reject_blank_channel_type(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("channel_type must be non-empty and non-whitespace")
        return value

    @field_validator("display_name")
    @classmethod
    def _reject_blank_display_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("display_name must be non-empty")
        return value


class PlatformRegistry:
    """Self-registration registry for channel platforms.

    Usage (platform module, called once at import time)::

        from magi_agent.channels.platform_registry import PlatformEntry, get_default_registry

        get_default_registry().register(
            PlatformEntry(
                channel_type="slack",
                display_name="Slack",
                supports_inbound=True,
                supports_outbound=True,
            )
        )

    Rules:
    - ``register()`` is idempotent when called with an identical entry.
    - ``register()`` raises ``ValueError`` when a *different* entry with the
      same ``channel_type`` is already registered.
    - ``list_entries()`` returns a defensive copy (a new tuple each call).
    - ``unregister()`` removes an entry; no-op if not present.
    """

    def __init__(self) -> None:
        self._entries: dict[str, PlatformEntry] = {}
        self._lock = threading.Lock()

    def register(self, entry: PlatformEntry) -> None:
        with self._lock:
            existing = self._entries.get(entry.channel_type)
            if existing is not None:
                if existing == entry:
                    return  # idempotent — same value
                raise ValueError(
                    f"channel_type '{entry.channel_type}' already registered with a different entry; "
                    f"existing={existing!r}, new={entry!r}"
                )
            self._entries[entry.channel_type] = entry

    def lookup(self, channel_type: str) -> PlatformEntry | None:
        return self._entries.get(channel_type)

    def list_entries(self) -> tuple[PlatformEntry, ...]:
        return tuple(self._entries.values())

    def unregister(self, channel_type: str) -> None:
        with self._lock:
            self._entries.pop(channel_type, None)


# ---------------------------------------------------------------------------
# Singleton default registry pre-populated with the 4 built-in platforms
# ---------------------------------------------------------------------------
# This is the self-registration seam.  Future platform modules call
# ``get_default_registry().register(...)`` from their own module body —
# no changes to this file are needed when adding E2/E3/E4+ platforms.

_DEFAULT_REGISTRY = PlatformRegistry()

_DEFAULT_REGISTRY.register(
    PlatformEntry(
        channel_type="web",
        display_name="Web Chat",
        supports_inbound=False,
        supports_outbound=True,
        supports_cron_delivery=False,
        default_enabled=False,
        cron_deliver_env_var=None,
    )
)

_DEFAULT_REGISTRY.register(
    PlatformEntry(
        channel_type="app",
        display_name="Mobile App",
        supports_inbound=False,
        supports_outbound=True,
        supports_cron_delivery=True,
        default_enabled=False,
        cron_deliver_env_var="MAGI_APP_CRON_TARGET",
    )
)

_DEFAULT_REGISTRY.register(
    PlatformEntry(
        channel_type="telegram",
        display_name="Telegram",
        supports_inbound=True,
        supports_outbound=True,
        supports_cron_delivery=False,
        default_enabled=False,
        cron_deliver_env_var=None,
    )
)

_DEFAULT_REGISTRY.register(
    PlatformEntry(
        channel_type="discord",
        display_name="Discord",
        supports_inbound=True,
        supports_outbound=True,
        supports_cron_delivery=False,
        default_enabled=False,
        cron_deliver_env_var=None,
    )
)


def get_default_registry() -> PlatformRegistry:
    """Return the module-level shared registry.

    The 4 built-in platforms (web, app, telegram, discord) are pre-registered.
    External platform modules call ``get_default_registry().register(entry)``
    at their own import time to extend the registry without touching this file.
    """
    return _DEFAULT_REGISTRY


def is_registered_channel_type(channel_type: str) -> bool:
    """Registry-driven validator: True iff channel_type is registered.

    Use this INSTEAD of ``channel_type in get_args(ChannelType)`` — the
    registry covers both the 4 built-ins and any platforms added by E2+
    modules, whereas ``get_args(ChannelType)`` is frozen at the literal
    definition and will not reflect dynamically registered platforms.
    """
    return _DEFAULT_REGISTRY.lookup(channel_type) is not None


__all__ = [
    "PlatformEntry",
    "PlatformRegistry",
    "get_default_registry",
    "is_registered_channel_type",
]
