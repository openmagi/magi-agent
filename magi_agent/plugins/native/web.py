from __future__ import annotations

from magi_agent.tools.context import ToolContext
from magi_agent.tools.result import ToolResult
from magi_agent.web_acquisition.provider_boundary import (
    WebAcquisitionConfig,
    WebAcquisitionRequest,
    LocalWebAcquisitionRuntime,
)
from magi_agent.web_acquisition.research_tools import LocalWebResearchToolBoundary


class _LocalWebProvider:
    openmagi_local_fake_provider = True

    def search(self, request: WebAcquisitionRequest) -> dict[str, object]:
        query = request.query or "local web search"
        return {
            "results": (
                {
                    "url": f"search://{query}",
                    "title": f"Local result for {query}",
                    "snippet": f"Local first-party web search stub for {query}. Configure a provider for live search.",
                    "metadata": {"provider": "local", "publicSafe": True},
                },
            )
        }

    def fetch(self, request: WebAcquisitionRequest) -> dict[str, object]:
        url = request.url or "https://example.com/"
        return {
            "url": url,
            "title": "Local fetched source",
            "content": f"Local first-party fetch stub for {url}. Configure a provider for live fetch.",
            "metadata": {"provider": "local", "publicSafe": True},
        }


def _boundary() -> LocalWebResearchToolBoundary:
    runtime = LocalWebAcquisitionRuntime(
        WebAcquisitionConfig(enabled=True, localFakeProviderEnabled=True),
        provider=_LocalWebProvider(),
    )
    return LocalWebResearchToolBoundary(runtime=runtime)


async def web_search(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    return await _boundary().execute_tool("WebSearch", arguments, context)


async def web_fetch(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    return await _boundary().execute_tool("WebFetch", arguments, context)
