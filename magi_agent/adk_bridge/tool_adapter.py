from __future__ import annotations

import os
from collections.abc import Callable

from google.adk.tools import FunctionTool, LongRunningFunctionTool

from magi_agent.tools.concurrency import ConcurrencyConfig
from magi_agent.tools.concurrent_dispatcher import ConcurrentToolDispatcher
from magi_agent.tools.context import ToolContext
from magi_agent.tools.deferred import DeferredToolRegistry, InitialToolSet
from magi_agent.tools.dispatcher import ToolDispatcher
from magi_agent.tools.manifest import RuntimeMode, ToolManifest
from magi_agent.tools.registry import ToolRegistry


ToolContextFactory = Callable[[object], ToolContext]
AdkLocalTool = FunctionTool | LongRunningFunctionTool


def _build_openmagi_tool_callable(
    manifest: ToolManifest,
    dispatcher: ToolDispatcher,
    *,
    mode: RuntimeMode,
    tool_context_factory: ToolContextFactory,
    exposed_tool_names: tuple[str, ...] | None,
) -> Callable[[dict[str, object], object], object]:
    async def invoke_openmagi_tool(
        arguments: dict[str, object],
        tool_context: object,
    ) -> dict[str, object]:
        openmagi_context = tool_context_factory(tool_context)
        result = await dispatcher.dispatch(
            manifest.name,
            arguments,
            openmagi_context,
            mode=mode,
            exposed_tool_names=exposed_tool_names,
        )
        return result.model_dump(by_alias=True)

    invoke_openmagi_tool.__name__ = manifest.name
    invoke_openmagi_tool.__doc__ = manifest.description
    return invoke_openmagi_tool


def build_adk_tool_for_manifest(
    manifest: ToolManifest,
    dispatcher: ToolDispatcher,
    *,
    mode: RuntimeMode,
    tool_context_factory: ToolContextFactory,
    exposed_tool_names: tuple[str, ...] | None = None,
) -> AdkLocalTool:
    invoke_openmagi_tool = _build_openmagi_tool_callable(
        manifest,
        dispatcher,
        mode=mode,
        tool_context_factory=tool_context_factory,
        exposed_tool_names=exposed_tool_names,
    )
    if manifest.adk_tool_type == "FunctionTool":
        return FunctionTool(invoke_openmagi_tool, require_confirmation=False)
    if manifest.adk_tool_type == "LongRunningFunctionTool":
        return LongRunningFunctionTool(invoke_openmagi_tool)
    raise ValueError(f"unsupported ADK tool type: {manifest.adk_tool_type}")


def build_adk_function_tool(
    manifest: ToolManifest,
    dispatcher: ToolDispatcher,
    *,
    mode: RuntimeMode,
    tool_context_factory: ToolContextFactory,
    exposed_tool_names: tuple[str, ...] | None = None,
) -> FunctionTool:
    if manifest.adk_tool_type != "FunctionTool":
        raise ValueError(
            "build_adk_function_tool only supports FunctionTool manifests; "
            f"got {manifest.adk_tool_type}"
        )
    tool = build_adk_tool_for_manifest(
        manifest,
        dispatcher,
        mode=mode,
        tool_context_factory=tool_context_factory,
        exposed_tool_names=exposed_tool_names,
    )
    if not isinstance(tool, FunctionTool):
        raise TypeError(f"expected FunctionTool for manifest {manifest.name}")
    return tool


def build_adk_function_tools_for_registry(
    registry: ToolRegistry,
    dispatcher: ToolDispatcher,
    *,
    mode: RuntimeMode,
    tool_context_factory: ToolContextFactory,
    attach_enabled: bool = False,
    exposed_tool_names: tuple[str, ...] | None = None,
    exclude_names: frozenset[str] | tuple[str, ...] | set[str] | None = None,
) -> list[AdkLocalTool]:
    if not attach_enabled:
        return []
    available = registry.list_available(mode=mode)
    if exposed_tool_names is not None:
        exposed = set(exposed_tool_names)
        available = [manifest for manifest in available if manifest.name in exposed]
    if exclude_names is not None:
        excluded = set(exclude_names)
        available = [manifest for manifest in available if manifest.name not in excluded]
    return [
        build_adk_tool_for_manifest(
            manifest,
            dispatcher,
            mode=mode,
            tool_context_factory=tool_context_factory,
            exposed_tool_names=exposed_tool_names,
        )
        for manifest in available
    ]


def build_adk_function_tools_for_granted_names(
    registry: ToolRegistry,
    dispatcher: ToolDispatcher,
    *,
    mode: RuntimeMode,
    tool_context_factory: ToolContextFactory,
    granted_tool_names: tuple[str, ...],
    attach_enabled: bool = False,
) -> list[AdkLocalTool]:
    if not attach_enabled:
        return []
    granted = tuple(dict.fromkeys(granted_tool_names))
    tools: list[AdkLocalTool] = []
    for tool_name in granted:
        manifest = registry.resolve_enabled(tool_name)
        if manifest is None or mode not in manifest.available_in_modes:
            continue
        tools.append(
            build_adk_tool_for_manifest(
                manifest,
                dispatcher,
                mode=mode,
                tool_context_factory=tool_context_factory,
                exposed_tool_names=granted,
            )
        )
    return tools


class DeferredToolManager:
    def __init__(
        self,
        registry: ToolRegistry,
        deferred_registry: DeferredToolRegistry,
        *,
        initial_tool_set: InitialToolSet | None = None,
        threshold: int | None = None,
        exposed_tool_names: tuple[str, ...] | None = None,
    ) -> None:
        self._registry = registry
        self._deferred_registry = deferred_registry
        self._exposed_tool_names = (
            tuple(dict.fromkeys(exposed_tool_names))
            if exposed_tool_names is not None
            else None
        )
        self._initial_tool_set = initial_tool_set or deferred_registry.get_initial_tools(
            threshold=threshold if threshold is not None else build_deferred_tool_threshold()
        )

    @property
    def deferred_names(self) -> frozenset[str]:
        return self._deferred_registry.deferred_names

    @property
    def exclude_names(self) -> frozenset[str]:
        return self._deferred_registry.deferred_names

    @property
    def hint_text(self) -> str | None:
        return self._initial_tool_set.hint_text

    def materialize_tools(
        self,
        names: list[str],
        dispatcher: ToolDispatcher,
        *,
        mode: RuntimeMode,
        tool_context_factory: ToolContextFactory,
        adk_tools_list: list[object],
    ) -> list[AdkLocalTool]:
        pending_names = [
            name
            for name in names
            if name in self.exclude_names
            and (self._exposed_tool_names is None or name in self._exposed_tool_names)
        ]
        manifests = self._deferred_registry.load_deferred(pending_names)
        materialized: list[AdkLocalTool] = []
        for manifest in manifests:
            tool = build_adk_tool_for_manifest(
                manifest,
                dispatcher,
                mode=mode,
                tool_context_factory=tool_context_factory,
                exposed_tool_names=self._exposed_tool_names,
            )
            materialized.append(tool)
            adk_tools_list.append(tool)
        return materialized


def build_deferred_tool_threshold() -> int:
    raw = os.environ.get("MAGI_DEFERRED_TOOL_THRESHOLD", "30")
    try:
        threshold = int(raw)
    except ValueError:
        return 30
    return max(1, threshold)


def build_deferred_adk_tools(
    registry: ToolRegistry,
    *,
    threshold: int | None = None,
    exposed_tool_names: tuple[str, ...] | None = None,
) -> DeferredToolManager | None:
    if os.environ.get("MAGI_DEFERRED_TOOLS_ENABLED", "0") != "1":
        return None
    deferred_registry = DeferredToolRegistry(registry)
    initial_tool_set = deferred_registry.get_initial_tools(
        threshold=threshold if threshold is not None else build_deferred_tool_threshold()
    )
    if not initial_tool_set.deferred_names:
        return None
    return DeferredToolManager(
        registry,
        deferred_registry,
        initial_tool_set=initial_tool_set,
        exposed_tool_names=exposed_tool_names,
    )


# ---------------------------------------------------------------------------
# Concurrency helpers
# ---------------------------------------------------------------------------


def build_concurrency_config() -> ConcurrencyConfig:
    """Build a ``ConcurrencyConfig`` from environment variables.

    Environment variables
    ---------------------
    MAGI_TOOL_CONCURRENCY_ENABLED
        Set to ``"1"`` to enable concurrent tool dispatch.  Defaults to
        ``"0"`` (disabled).
    MAGI_MAX_TOOL_CONCURRENCY
        Maximum number of tool calls that may run simultaneously when
        concurrency is enabled.  Must be a positive integer.  Defaults to
        ``"8"``.

    Returns
    -------
    ConcurrencyConfig
        Frozen configuration instance derived from the current environment.
    """
    return ConcurrencyConfig(
        enabled=os.environ.get("MAGI_TOOL_CONCURRENCY_ENABLED", "0") == "1",
        max_concurrency=int(os.environ.get("MAGI_MAX_TOOL_CONCURRENCY", "8")),
    )


def build_concurrent_dispatcher(
    base_dispatcher: ToolDispatcher,
    config: ConcurrencyConfig | None = None,
) -> ConcurrentToolDispatcher:
    """Wrap *base_dispatcher* with a ``ConcurrentToolDispatcher``.

    The returned dispatcher is a drop-in replacement for the plain
    ``ToolDispatcher`` when used with ``build_adk_tool_for_manifest`` and
    ``build_adk_function_tools_for_registry`` — single ``dispatch()`` calls
    delegate transparently to the base dispatcher.  The additional
    ``dispatch_batch()`` method is available for callers (such as a
    ``RunnerSessionBoundary``) that want to fan-out concurrent-safe tool calls
    in parallel.

    ADK native parallel tool execution
    -----------------------------------
    Google ADK (as of the version bundled in this project) does **not** expose
    a parallel tool execution API at the ``FunctionTool`` layer.  The ADK
    ``Runner`` calls ``FunctionTool.run_async()`` one tool at a time during a
    single agent turn.  Our adapter-level ``ConcurrentToolDispatcher`` fills
    this gap: code that sits between the Runner and our tools can accumulate a
    batch of tool calls (from a single model response that emits multiple
    ``function_call`` parts) and submit them via ``dispatch_batch()`` to fan
    them out concurrently, while the individual ADK ``FunctionTool`` wrappers
    continue to work unchanged for single-call invocations.

    Parameters
    ----------
    base_dispatcher:
        The plain ``ToolDispatcher`` to wrap.
    config:
        Optional concurrency configuration.  If ``None``, ``build_concurrency_config()``
        is called to derive configuration from environment variables.

    Returns
    -------
    ConcurrentToolDispatcher
        Configured dispatcher with both ``dispatch()`` and ``dispatch_batch()``
        methods.
    """
    return ConcurrentToolDispatcher(
        base_dispatcher=base_dispatcher,
        config=config if config is not None else build_concurrency_config(),
    )
