"""Equivalence tests for the shared dispatch-kernel helpers (module-diet PR5).

``ToolDispatcher`` (tools/dispatcher.py) and ``ToolExecutionKernel``
(tools/kernel.py) are deliberately separate dispatch boundaries, but they must
agree on tool availability for the same input. These tests pin:

1. both kernels resolve availability through the SAME shared helper
   (``tools/dispatch_shared.py``), so the former byte-identical copies can
   never drift apart again; and
2. the two public dispatch paths project identical ``availableTools`` metadata
   for the same registry / mode / exposure input.
"""
from __future__ import annotations

import asyncio

from magi_agent.tools.context import ToolContext
from magi_agent.tools.manifest import ToolManifest, ToolSource
from magi_agent.tools.registry import ToolRegistry


def _manifest(name: str) -> ToolManifest:
    return ToolManifest(
        name=name,
        description=f"{name} test tool",
        kind="native",
        source=ToolSource(kind="native-plugin", package="test"),
        permission="read",
        inputSchema={"type": "object"},
        timeoutMs=1000,
        enabled_by_default=True,
    )


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_manifest("Echo"))
    registry.register(_manifest("Lookup"))
    return registry


def _context() -> ToolContext:
    return ToolContext(botId="bot-test", sessionId="session-test", turnId="turn-test")


def test_both_kernels_use_the_same_shared_available_tool_names_helper() -> None:
    from magi_agent.tools import dispatch_shared, dispatcher, kernel

    assert dispatcher._available_tool_names is dispatch_shared._available_tool_names
    assert kernel._available_tool_names is dispatch_shared._available_tool_names


def test_available_tool_names_dedupes_and_sorts_exposed_names() -> None:
    from magi_agent.tools.dispatch_shared import _available_tool_names

    names = _available_tool_names(
        _registry(),
        ("Lookup", "Echo", "Lookup"),
        mode="act",
    )
    assert names == ("Echo", "Lookup")


def test_available_tool_names_falls_back_to_registry_when_not_exposed() -> None:
    from magi_agent.tools.dispatch_shared import _available_tool_names

    registry = _registry()
    names = _available_tool_names(registry, None, mode="act")
    assert names == tuple(tool.name for tool in registry.list_available(mode="act"))
    assert set(names) == {"Echo", "Lookup"}


def test_dispatch_paths_project_identical_available_tools_for_same_input() -> None:
    """Same input -> same tool availability through both public dispatch paths.

    The dispatcher path resolves an unknown tool (tool_not_found); the kernel
    path stays on its default-off blocked branch. Both project the
    ``availableTools`` metadata from the shared helper and must agree.
    """
    from magi_agent.tools.dispatcher import ToolDispatcher
    from magi_agent.tools.kernel import ToolExecutionKernel, ToolExecutionRequest

    registry = _registry()
    context = _context()
    exposed = ("Lookup", "Echo", "Lookup")

    dispatcher_result = asyncio.run(
        ToolDispatcher(registry).dispatch(
            "Missing",
            {},
            context,
            mode="act",
            exposed_tool_names=exposed,
        )
    )
    kernel_outcome = asyncio.run(
        ToolExecutionKernel(registry).execute(
            ToolExecutionRequest(
                toolName="Missing",
                arguments={},
                context=context,
                mode="act",
                exposedToolNames=exposed,
            )
        )
    )

    dispatcher_available = dispatcher_result.metadata["availableTools"]
    kernel_available = kernel_outcome.result.metadata["availableTools"]
    assert tuple(dispatcher_available) == tuple(kernel_available) == ("Echo", "Lookup")

    registry_dispatcher_result = asyncio.run(
        ToolDispatcher(registry).dispatch("Missing", {}, context, mode="act")
    )
    registry_kernel_outcome = asyncio.run(
        ToolExecutionKernel(registry).execute(
            ToolExecutionRequest(
                toolName="Missing",
                arguments={},
                context=context,
                mode="act",
            )
        )
    )
    assert tuple(registry_dispatcher_result.metadata["availableTools"]) == tuple(
        registry_kernel_outcome.result.metadata["availableTools"]
    )
