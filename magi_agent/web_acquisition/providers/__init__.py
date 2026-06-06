"""Concrete live web acquisition providers for the provider router.

Providers:
- ``PlatformEndpointProvider`` — uses the hosted platform's /v1/search,
  /v1/fetch, /v1/scrape endpoints (Serper/Brave/Firecrawl backend).
- ``ComposioMcpShimProvider`` — wraps the existing Composio MCP toolset bundle
  as a ``LiveProvider``-compatible object.
- ``FakeLiveProvider`` — hermetic stand-in for tests (moved from StubLiveProvider).

All providers carry ``openmagi_live_provider = True`` so the live-gate check in
``LiveWebAcquisitionProviderPack._live_gate_error`` passes.
"""

from __future__ import annotations

from magi_agent.web_acquisition.providers.fake_provider import FakeLiveProvider
from magi_agent.web_acquisition.providers.platform_endpoint import PlatformEndpointProvider

__all__ = [
    "FakeLiveProvider",
    "PlatformEndpointProvider",
]
