"""ComputerTask tool: manifest, gated binding, and async handler.

Drives the autonomous macOS computer-use engine via the external ``cua-driver``
MCP binary. The ``mcp`` SDK and the binary are required only at call time; the
handler degrades with ``status="blocked"`` when they are absent rather than
failing at import.
"""

from __future__ import annotations

import shutil
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING

from magi_agent.tools.catalog import CORE_TOOL_SOURCE
from magi_agent.tools.context import ToolContext
from magi_agent.tools.manifest import ToolManifest
from magi_agent.tools.result import ToolResult

if TYPE_CHECKING:
    from magi_agent.tools.registry import ToolRegistry

COMPUTER_TOOL_NAME = "ComputerTask"

_INPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "task": {"type": "string"},
        "max_steps": {"type": "integer"},
    },
    "required": ["task"],
}

_DESCRIPTION = (
    "Autonomously control the local macOS desktop (screenshots + mouse/keyboard) "
    "to accomplish a natural-language goal. Requires explicit user consent."
)

_APPROVAL_WORDS = {"yes", "approve", "allow", "true", "y"}


def register_computer_tool_manifest(registry: "ToolRegistry") -> None:
    """Register the ComputerTask manifest (no handler bound yet)."""
    manifest = ToolManifest(
        name=COMPUTER_TOOL_NAME,
        description=_DESCRIPTION,
        kind="core",
        source=CORE_TOOL_SOURCE,
        permission="computer",
        input_schema=_INPUT_SCHEMA,
        dangerous=True,
        timeoutMs=600_000,
    )
    registry.register(manifest)


def _consent_from_context(context: ToolContext) -> Callable[[str], Awaitable[bool]]:
    """Build an async consent callable from the tool context.

    Fail-closed: with no ``ask_user`` callback (non-interactive), deny all.
    """
    ask = getattr(context, "ask_user", None)

    async def _consent(description: str) -> bool:
        if ask is None:
            return False
        prompt = f"Allow computer-use to perform this action? {description}"
        result = ask(prompt=prompt)
        if hasattr(result, "__await__"):
            result = await result
        if isinstance(result, str):
            return result.strip().casefold() in _APPROVAL_WORDS
        return bool(result)

    return _consent


async def _computer_task_handler(
    arguments: Mapping[str, object], context: ToolContext
) -> ToolResult:
    if shutil.which("cua-driver") is None:
        return ToolResult(
            status="blocked",
            error_code="cua_driver_missing",
            error_message="Install with: magi computer-use install",
        )

    task = str(arguments.get("task", "")).strip()
    if not task:
        return ToolResult(
            status="error", error_code="missing_task", error_message="task is required"
        )

    from magi_agent.cli import providers as _providers  # noqa: PLC0415
    from magi_agent.computer.autonomous.config import ComputerToolConfig  # noqa: PLC0415
    from magi_agent.computer.autonomous.engine import ComputerEngine  # noqa: PLC0415
    from magi_agent.computer.autonomous.provider_bridge import (  # noqa: PLC0415
        BridgeError,
        build_chat_step,
    )

    provider_config = _providers.resolve_provider_config()
    try:
        chat_step = build_chat_step(provider_config)
    except BridgeError as exc:
        return ToolResult(
            status="blocked", error_code="no_provider", error_message=str(exc)
        )

    max_steps = int(arguments.get("max_steps") or ComputerToolConfig().max_steps)

    from magi_agent.computer.autonomous.cua_backend import CuaDriverBackend  # noqa: PLC0415

    # The session() context manager owns the cua-driver subprocess + anyio scopes;
    # the whole engine run happens inside one frame so teardown is scope-safe.
    async with CuaDriverBackend.session() as backend:
        run = await ComputerEngine(
            backend=backend, chat_step=chat_step, consent=_consent_from_context(context)
        ).run(task=task, max_steps=max_steps)

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


def bind_computer_toolhost_handler(registry: "ToolRegistry") -> tuple[str, ...]:
    """Bind the ComputerTask handler if its manifest is registered."""
    if registry.resolve_registration(COMPUTER_TOOL_NAME) is None:
        return ()
    registry.bind_handler(
        COMPUTER_TOOL_NAME, _computer_task_handler, enabled_by_registry_policy=True
    )
    return (COMPUTER_TOOL_NAME,)


__all__ = [
    "COMPUTER_TOOL_NAME",
    "register_computer_tool_manifest",
    "bind_computer_toolhost_handler",
    "_computer_task_handler",
    "_consent_from_context",
]
