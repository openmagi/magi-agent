from __future__ import annotations

from magi_agent.browser.provider_boundary import (
    BrowserProviderConfig,
    BrowserRequest,
    LocalBrowserProviderRuntime,
)
from magi_agent.browser.source_tools import LocalBrowserSourceToolBoundary
from magi_agent.tools.context import ToolContext
from magi_agent.tools.result import ToolResult


class _LocalBrowserProvider:
    openmagi_local_fake_provider = True

    def run(self, request: BrowserRequest) -> dict[str, object]:
        url = request.url or "https://example.com/"
        return {
            "url": url,
            "title": "Local browser observation",
            "visibleText": f"Local first-party browser observation for {url}. Configure a browser provider for live browsing.",
            "metadata": {"provider": "local", "publicSafe": True},
        }


def _boundary() -> LocalBrowserSourceToolBoundary:
    runtime = LocalBrowserProviderRuntime(
        BrowserProviderConfig(enabled=True, localFakeProviderEnabled=True),
        provider=_LocalBrowserProvider(),
    )
    return LocalBrowserSourceToolBoundary(runtime=runtime)


async def browser(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    action = str(arguments.get("action") or "BrowserSnapshot")
    if action in {"open", "browser.open"}:
        action = "BrowserOpen"
    elif action in {"scrape", "browser.scrape"}:
        action = "BrowserScrape"
    elif action in {"screenshot", "browser.screenshot"}:
        action = "BrowserScreenshot"
    elif action not in {"BrowserOpen", "BrowserSnapshot", "BrowserScrape", "BrowserScreenshot"}:
        action = "BrowserSnapshot"
    result = await _boundary().execute_tool(action, arguments, context)
    return ToolResult(
        status=result.status,
        output=result.output,
        metadata={**result.metadata, "toolName": "Browser", "browserAction": action},
    )


async def social_browser(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    safe_args = {**arguments, "action": arguments.get("action") or "BrowserSnapshot"}
    return await browser(safe_args, context)
