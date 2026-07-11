from __future__ import annotations

import os
from collections.abc import Callable
from typing import TYPE_CHECKING

from google.adk.tools import FunctionTool, LongRunningFunctionTool

from magi_agent.tools.concurrency import ConcurrencyConfig
from magi_agent.tools.concurrent_dispatcher import ConcurrentToolDispatcher
from magi_agent.tools.context import ToolContext
from magi_agent.tools.deferred import DeferredToolRegistry, InitialToolSet
from magi_agent.tools.dispatcher import ToolDispatcher
from magi_agent.tools.manifest import RuntimeMode, ToolManifest
from magi_agent.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from magi_agent.prompt.provider_adapter import ProviderFamily


ToolContextFactory = Callable[[object], ToolContext]


def _normalize_tool_call_args(args: object) -> object:
    """Accept BOTH the wrapped and the flat model tool-call shape.

    First-party tools are exposed through a generic
    ``invoke_openmagi_tool(arguments)`` callable, so the ADVERTISED schema is the
    wrapped shape ``{"arguments": {<real params>}}``. Some models (Claude Sonnet
    5 especially) frequently emit the real params FLAT instead
    (``{"path": ...}``). ADK's ``FunctionTool.run_async`` filters unknown keys
    against the signature BEFORE calling, so a flat call drops every real key and
    then fails the mandatory-``arguments`` check with "mandatory input parameters
    are not present: arguments", never reaching the dispatcher (a whole turn of
    tool calls dies silently).

    This normalizer wraps a flat call into ``{"arguments": {...}}`` so both
    shapes dispatch identically. When the wrapped ``arguments`` object is already
    present, sibling flat keys (if any) are merged UNDER it with the wrapped keys
    winning, so a mixed call never loses the explicit wrapped values.

    Fail-open: anything that is not a dict is returned unchanged.
    """
    if not isinstance(args, dict):
        return args
    inner = args.get("arguments")
    if isinstance(inner, dict):
        siblings = {k: v for k, v in args.items() if k != "arguments"}
        if siblings:
            return {"arguments": {**siblings, **inner}}
        return args
    # Flat shape (no dict ``arguments``): wrap all keys under ``arguments``.
    return {"arguments": dict(args)}


class _FlatArgsNormalizingMixin:
    """Mixin that normalizes a flat model tool-call into the wrapped shape.

    Applied BEFORE ADK's mandatory-arg check + unknown-key filter in
    ``FunctionTool.run_async``, so a model that sends the real params flat
    dispatches identically to one that sends the advertised wrapped shape.
    """

    async def run_async(  # type: ignore[override]
        self, *, args: dict[str, object], tool_context: object
    ) -> object:
        normalized = _normalize_tool_call_args(args)
        return await super().run_async(  # type: ignore[misc]
            args=normalized, tool_context=tool_context
        )


class OpenMagiFunctionTool(_FlatArgsNormalizingMixin, FunctionTool):
    """FunctionTool that accepts both the wrapped and the flat tool-call shape."""


class OpenMagiLongRunningFunctionTool(
    _FlatArgsNormalizingMixin, LongRunningFunctionTool
):
    """LongRunningFunctionTool that accepts both call shapes (see the mixin)."""


AdkLocalTool = FunctionTool | LongRunningFunctionTool

#: Tool names kept registered (and therefore dispatchable) for backward
#: compatibility, but NOT advertised to the model in the registry-wide toolset.
#: The bundled web plugin ships three names for the same web-search surface
#: (``WebSearch``, ``web_search``, ``web-search``). The kebab-case ``web-search``
#: is a vestigial alias with no distinct wiring role. Unlike ``web_search``,
#: which is also the canonical evidence source-kind and the name the direct
#: Brave/SerpAPI fast path rebinds onto. Hiding only the kebab alias trims the
#: model's tool catalog (fewer near-duplicate entries to choose between) while
#: leaving every name resolvable via ``ToolDispatcher.dispatch`` for any legacy
#: caller that still emits it.
_HIDDEN_MODEL_ALIAS_TOOL_NAMES: frozenset[str] = frozenset({"web-search"})


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


_JSON_TYPE_TO_GENAI = {
    "string": "STRING",
    "object": "OBJECT",
    "integer": "INTEGER",
    "boolean": "BOOLEAN",
    "number": "NUMBER",
    "array": "ARRAY",
}


def _json_schema_to_genai_schema(schema: dict[str, object]):
    """Convert a small JSON-schema dict into a ``genai_types.Schema``.

    Supports the subset used by core tool manifests: typed properties with
    descriptions, ``required``, nested ``properties``, and ``items``.
    """
    from google.genai import types as genai_types  # noqa: PLC0415

    type_name = str(schema.get("type", "object"))
    kwargs: dict[str, object] = {
        "type": getattr(genai_types.Type, _JSON_TYPE_TO_GENAI.get(type_name, "OBJECT"))
    }
    description = schema.get("description")
    if isinstance(description, str) and description:
        kwargs["description"] = description
    properties = schema.get("properties")
    if isinstance(properties, dict):
        kwargs["properties"] = {
            key: _json_schema_to_genai_schema(value)
            for key, value in properties.items()
            if isinstance(value, dict)
        }
    required = schema.get("required")
    if isinstance(required, (list, tuple)):
        kwargs["required"] = [str(item) for item in required]
    enum = schema.get("enum")
    if (
        isinstance(enum, (list, tuple))
        and enum
        and all(isinstance(item, str) for item in enum)
    ):
        # Dropping enum hides the valid values from the model, which then
        # guesses formats (live: tau-bench "one way" vs "one_way"). Only
        # string enums are valid on the typed Schema path; non-string enums
        # are handled by the provider-repair passthrough.
        kwargs["enum"] = list(enum)
    items = schema.get("items")
    if isinstance(items, dict):
        kwargs["items"] = _json_schema_to_genai_schema(items)
    return genai_types.Schema(**kwargs)


def _enrich_arguments_schema(tool: AdkLocalTool, manifest: ToolManifest) -> AdkLocalTool:
    """Surface the manifest's ``input_schema`` as the ``arguments`` object schema.

    First-party tools are exposed through a generic ``invoke_openmagi_tool(arguments)``
    callable, so ADK builds a declaration whose single ``arguments`` object has no
    inner properties — the model can't see the real parameter names and guesses
    (the root cause of the SWE-bench edit failures). When the manifest declares an
    informative ``input_schema`` (has ``properties``), wrap ``_get_declaration`` to
    set the ``arguments`` object's properties/required from it.
    """
    schema = manifest.input_schema
    if not isinstance(schema, dict) or "properties" not in schema:
        return tool
    base_get_declaration = tool._get_declaration  # type: ignore[attr-defined]

    def _enriched_get_declaration() -> object | None:
        declaration = base_get_declaration()
        if declaration is None:
            return None
        params = getattr(declaration, "parameters", None)
        props = getattr(params, "properties", None) if params is not None else None
        if isinstance(props, dict) and "arguments" in props:
            props["arguments"] = _json_schema_to_genai_schema(schema)
        return declaration

    tool._get_declaration = _enriched_get_declaration  # type: ignore[method-assign,attr-defined]
    return tool


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
        tool = _enrich_arguments_schema(
            OpenMagiFunctionTool(invoke_openmagi_tool, require_confirmation=False),
            manifest,
        )
        return apply_provider_repair(tool)
    if manifest.adk_tool_type == "LongRunningFunctionTool":
        tool = _enrich_arguments_schema(
            OpenMagiLongRunningFunctionTool(invoke_openmagi_tool), manifest
        )
        return apply_provider_repair(tool)
    raise ValueError(f"unsupported ADK tool type: {manifest.adk_tool_type}")


# ---------------------------------------------------------------------------
# Per-provider tool-schema repair (PR9)
#
# Measurement summary (see tests/adk_bridge/test_provider_repair.py): magi runs
# on Google ADK with no LiteLLM dependency. ADK 1.33.0 repairs most typed Gemini
# schema issues, but raw ``parameters_json_schema`` and selected hosted
# generation envs can still expose provider-specific gaps. The live-observed
# gaps covered here are non-string enums and additional-properties keywords.
#
# This hook is flag-gated (``MAGI_PROVIDER_REPAIR_ENABLED``, default OFF) and
# keyed on the active model's provider family. When ON for the Gemini family it
# wraps every ADK FunctionTool built by this adapter so its declaration is
# repaired at exposure time. For every other family - and when OFF - it is a
# pure identity passthrough (no boundary change).
# ---------------------------------------------------------------------------


def provider_repair_enabled() -> bool:
    """Return whether per-provider tool-schema repair is enabled.

    Reads the single-source flag defined in ``magi_agent.config.env``
    (``MAGI_PROVIDER_REPAIR_ENABLED``, default OFF).
    """
    from magi_agent.config.env import parse_provider_repair_enabled

    return parse_provider_repair_enabled(os.environ)


def active_provider_family() -> "ProviderFamily":
    """Resolve the provider family for the active ADK model.

    Hosted selected-generation runs can intentionally leave ``CORE_AGENT_MODEL``
    at a disabled sentinel while the actual ADK provider/model is supplied by
    Gate 5B shadow-generation env. Prefer concrete model labels, then explicit
    provider labels.
    """
    from magi_agent.prompt.provider_adapter import (
        ProviderFamily,
        detect_provider_family,
    )

    for key in (
        "CORE_AGENT_MODEL",
        "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_MODEL_LABEL",
    ):
        model = os.environ.get(key, "").strip()
        if not model or model == "shadow-model-disabled":
            continue
        family = detect_provider_family(model)
        if family is not ProviderFamily.DEFAULT:
            return family

    # I-4: routed through the typed flag registry.
    from magi_agent.config.flags import flag_str  #  # noqa: PLC0415

    provider_label = (
        flag_str("CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_PROVIDER_LABEL") or ""
    ).strip().lower()
    try:
        return ProviderFamily(provider_label)
    except ValueError:
        return ProviderFamily.DEFAULT


def _repair_declaration(declaration: object, family: "ProviderFamily") -> bool:
    """Repair a ``FunctionDeclaration``'s parameter schema in place.

    Returns ``True`` if a repair was applied. Only the raw-dict
    ``parameters_json_schema`` passthrough can carry non-string enums to the wire
    (the typed ``Schema`` path already rejects them), so that is the only field
    repaired here.
    """
    from magi_agent.prompt.provider_adapter import repair_tool_schema_for_provider

    raw_schema = getattr(declaration, "parameters_json_schema", None)
    if not isinstance(raw_schema, dict):
        return False
    repaired = repair_tool_schema_for_provider(raw_schema, family)
    if repaired == raw_schema:
        return False
    try:
        object.__setattr__(declaration, "parameters_json_schema", repaired)
    except (AttributeError, ValueError):
        try:
            declaration.parameters_json_schema = repaired  # type: ignore[attr-defined]
        except Exception:
            return False
    return True


def apply_provider_repair(tool: AdkLocalTool) -> AdkLocalTool:
    """Wrap *tool* so its ADK declaration is repaired for the active provider.

    No-op (returns the same object) when the repair flag is OFF or the active
    provider family has no gap to repair. When active for the Gemini family, the
    tool's ``_get_declaration`` is wrapped to normalize provider-incompatible
    schema fields on the way to the model.

    Idempotent: if this function has already been applied to *tool* (detected via
    ``_provider_repair_applied`` sentinel), the tool is returned unchanged so that
    double-materialisation of a deferred tool does not stack closures.
    """
    if not provider_repair_enabled():
        return tool

    from magi_agent.prompt.provider_adapter import ProviderFamily

    family = active_provider_family()
    if family is not ProviderFamily.GOOGLE:
        return tool

    # Idempotency guard: return immediately if already wrapped for this instance.
    if getattr(tool, "_provider_repair_applied", False):
        return tool

    base_get_declaration = tool._get_declaration  # type: ignore[attr-defined]

    def _repaired_get_declaration() -> object | None:
        declaration = base_get_declaration()
        if declaration is None:
            return None
        _repair_declaration(declaration, family)
        return declaration

    # Intentional per-instance dict override: survives only for this object
    # instance (not copies), valid for the current ADK version.
    tool._get_declaration = _repaired_get_declaration  # type: ignore[method-assign,attr-defined]
    tool._provider_repair_applied = True  # type: ignore[attr-defined]
    return tool


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
    # Drop backward-compat aliases from the advertised set. They stay registered
    # and dispatchable; they are simply not surfaced to the model here.
    available = [
        manifest
        for manifest in available
        if manifest.name not in _HIDDEN_MODEL_ALIAS_TOOL_NAMES
    ]
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
    # I-4: routed through the typed flag registry. ``flag_int`` returns
    # the registered default (30) on missing / unparseable values.
    from magi_agent.config.flags import flag_int  #  # noqa: PLC0415

    threshold = flag_int("MAGI_DEFERRED_TOOL_THRESHOLD") or 30
    return max(1, threshold)


def build_deferred_adk_tools(
    registry: ToolRegistry,
    *,
    threshold: int | None = None,
    exposed_tool_names: tuple[str, ...] | None = None,
) -> DeferredToolManager | None:
    # I-4: routed through the typed flag registry. ``MAGI_DEFERRED_TOOLS_ENABLED``
    # was already registered (``_b``); the strict-``=="1"`` check
    # widens trivially to ``flag_bool``'s canonical set.
    from magi_agent.config.flags import flag_profile_bool  #  # noqa: PLC0415

    if not flag_profile_bool("MAGI_DEFERRED_TOOLS_ENABLED"):
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

    Notes
    -----
    The flags are parsed via the single-source helpers in
    ``magi_agent.config.env`` (``tool_concurrency_enabled`` /
    ``max_tool_concurrency``) so that the live ``ToolDispatcher`` readonly
    offload and this config agree on the same truthy convention and default.
    """
    from magi_agent.config.env import max_tool_concurrency, tool_concurrency_enabled

    return ConcurrencyConfig(
        enabled=tool_concurrency_enabled(os.environ),
        max_concurrency=max_tool_concurrency(os.environ),
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
    ``dispatch_batch()`` method is available for callers (such as the runner
    integration) that want to fan-out concurrent-safe tool calls
    in parallel.

    ADK native parallel tool execution (measured, ADK 1.33.0)
    ---------------------------------------------------------
    Google ADK 1.33.0 **already** runs multiple same-turn function calls
    concurrently: ``flows/llm_flows/functions.handle_function_call_list_async``
    builds one ``asyncio.create_task`` per ``function_call`` part of a single
    model response and ``await``s ``asyncio.gather`` over them. ADK owns dispatch
    — it invokes each tool's ``FunctionTool.run_async()`` as an independent task
    and never hands magi a *batch*. Consequently ``dispatch_batch()`` is **not
    reachable on the live ADK ``Runner`` path**; it remains available only for
    non-ADK callers that explicitly accumulate a batch themselves.

    Because magi's readonly tool handlers are *synchronous* and do blocking I/O,
    ADK's gather alone yields no real overlap (a blocking sync handler holds the
    event loop until it returns). The genuinely-live seam is therefore in
    ``ToolDispatcher`` itself: when ``MAGI_TOOL_CONCURRENCY_ENABLED`` is ON it
    offloads readonly / concurrency_safe synchronous handlers via
    ``asyncio.to_thread`` (bounded by ``MAGI_MAX_TOOL_CONCURRENCY``), so ADK's
    existing gather produces real I/O overlap. Workspace-mutating / unsafe tools
    are never offloaded (write-barrier), and every concurrent call still passes
    its own permission / path-policy checks. See
    ``ToolDispatcher._should_offload``.

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
