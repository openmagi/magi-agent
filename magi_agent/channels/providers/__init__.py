"""Concrete channel provider implementations (the ONLY place a real network
client is constructed for live channels).

The boundary modules under ``magi_agent.channels`` (``telegram_adapter``,
``telegram_live``, ``discord_live`` …) are import-clean: they declare injected
``ProviderPort`` protocols and never construct an HTTP/SMTP client.  This
package holds the concrete ports an operator injects when running self-hosted.

Every module here is gated behind its channel's ``MAGI_CHANNEL_LIVE_*`` flag at
the wiring layer (``magi_agent.gateway.channel_watchers``) — importing a
provider class does NOT activate any network authority.
"""
from __future__ import annotations

__all__: list[str] = []
