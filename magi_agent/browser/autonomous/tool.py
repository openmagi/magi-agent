"""BrowserTask tool: manifest, gated toolhost binding, and async handler.

The handler drives the autonomous browser engine (``BrowserEngine``) using a
vision-capable browser-use Agent. The optional ``browser`` extra (``browser_use``)
is imported *lazily* inside the handler so this module can always be imported
even when the extra is absent -- a missing extra is surfaced at call-time as a
``status="blocked"`` result rather than an import-time error.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Mapping
from typing import TYPE_CHECKING

from magi_agent.tools.catalog import CORE_TOOL_SOURCE
from magi_agent.tools.context import ToolContext
from magi_agent.tools.manifest import ToolManifest
from magi_agent.tools.result import ToolResult

if TYPE_CHECKING:
    from magi_agent.tools.registry import ToolRegistry

BROWSER_TOOL_NAME = "BrowserTask"

_INPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "task": {"type": "string"},
        "start_url": {"type": "string"},
        "max_steps": {"type": "integer"},
    },
    "required": ["task"],
}

_DESCRIPTION = (
    "Autonomously browse the web to accomplish a natural-language goal using a "
    "vision browser agent."
)


def register_browser_tool_manifest(registry: "ToolRegistry") -> None:
    """Register the BrowserTask manifest (no handler bound yet)."""
    manifest = ToolManifest(
        name=BROWSER_TOOL_NAME,
        description=_DESCRIPTION,
        kind="core",
        source=CORE_TOOL_SOURCE,
        permission="net",
        input_schema=_INPUT_SCHEMA,
        timeoutMs=300_000,
    )
    registry.register(manifest)


def context_profile_dir(context: ToolContext) -> str:
    """Per-workspace browser profile directory."""
    return f"{context.workspace_root or '/tmp'}/.magi-browser-profile"


async def _browser_task_handler(
    arguments: Mapping[str, object], context: ToolContext
) -> ToolResult:
    if importlib.util.find_spec("browser_use") is None:
        return ToolResult(
            status="blocked",
            error_code="browser_extra_missing",
            error_message=(
                "Install with: uv sync --extra browser && "
                "uv run playwright install chromium"
            ),
        )

    task = str(arguments.get("task", "")).strip()
    if not task:
        return ToolResult(
            status="error",
            error_code="missing_task",
            error_message="task is required",
        )

    # Lazy imports: keep module import cheap and avoid pulling the optional extra
    # transitively at import time.
    from magi_agent.browser.autonomous.config import BrowserToolConfig  # noqa: PLC0415
    from magi_agent.browser.autonomous.engine import BrowserEngine  # noqa: PLC0415
    from magi_agent.browser.autonomous.provider_bridge import (  # noqa: PLC0415
        BridgeError,
        build_chat_model,
    )
    # Import the providers MODULE (not the symbol) so monkeypatching
    # ``magi_agent.cli.providers.resolve_provider_config`` is honored.
    from magi_agent.cli import providers as _providers  # noqa: PLC0415

    start_url = arguments.get("start_url")
    max_steps = int(arguments.get("max_steps") or BrowserToolConfig().max_steps)

    try:
        chat_model = build_chat_model(_providers.resolve_provider_config())
    except BridgeError as exc:
        return ToolResult(
            status="blocked",
            error_code="no_provider",
            error_message=str(exc),
        )

    run = await BrowserEngine().run(
        task=task,
        chat_model=chat_model,
        max_steps=max_steps,
        profile_dir=context_profile_dir(context),
        start_url=str(start_url) if start_url else None,
    )

    if run.status != "ok":
        return ToolResult(
            status=run.status,
            error_code=run.error_code,
            error_message=run.summary or None,
        )

    return ToolResult(
        status="ok",
        output={"summary": run.summary, "steps_used": run.steps_used},
        llm_output=run.summary,
    )


def bind_browser_toolhost_handler(registry: "ToolRegistry") -> tuple[str, ...]:
    """Bind the BrowserTask handler if its manifest is registered.

    Returns the bound tool names, or an empty tuple when the manifest was never
    registered (so callers can gate registration upstream).
    """
    if registry.resolve_registration(BROWSER_TOOL_NAME) is None:
        return ()
    registry.bind_handler(
        BROWSER_TOOL_NAME,
        _browser_task_handler,
        enabled_by_registry_policy=True,
    )
    return (BROWSER_TOOL_NAME,)


__all__ = [
    "BROWSER_TOOL_NAME",
    "register_browser_tool_manifest",
    "bind_browser_toolhost_handler",
    "_browser_task_handler",
    "context_profile_dir",
]
