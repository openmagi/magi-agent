from __future__ import annotations

from magi_agent.tools.context import ToolContext
from magi_agent.tools.result import ToolResult

# Frozen contract shared with PR-2 (live wiring imports these).
WEB_RESEARCH_NOT_CONFIGURED_ERROR_CODE = "web_research_not_configured"

_NOT_CONFIGURED_MESSAGE = (
    "WebSearch/WebFetch is not configured. No live web provider is enabled, so "
    "the agent cannot search or fetch the web. Enable a live provider: set "
    "CORE_AGENT_PYTHON_LIVE_WEB_ACQUISITION_ENABLED=1 and one of "
    "CORE_AGENT_PYTHON_JINA_READER_ENABLED (+MAGI_JINA_API_KEY) / "
    "CORE_AGENT_PYTHON_INSANE_FETCH_ENABLED / "
    "MAGI_PLATFORM_BASE_URL+MAGI_PLATFORM_API_KEY."
)


def _not_configured_result(tool_name: str) -> ToolResult:
    """Honest not-configured error for native web tools.

    Returned when no live web provider is enabled. This replaces the previous
    fabricated ``search://`` stub that was projected as a successful result and
    caused the model to hallucinate "real" web results. PR-2 imports this helper
    and ``WEB_RESEARCH_NOT_CONFIGURED_ERROR_CODE`` for the live-wiring fallback.
    """
    return ToolResult(
        status="error",
        error_code=WEB_RESEARCH_NOT_CONFIGURED_ERROR_CODE,
        error_message=_NOT_CONFIGURED_MESSAGE,
        metadata={"tool": tool_name},
    )


async def web_search(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    return _not_configured_result("WebSearch")


async def web_fetch(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    return _not_configured_result("WebFetch")
