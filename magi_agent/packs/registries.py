"""Typed primitive registries (D3/D4). One keyed registry for all 8 provides types.

First-party and user impls register through the IDENTICAL path (§1 "no privilege"):
``origin`` is metadata only and never blocks override/forbid or grants capability.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from itertools import count
from typing import TYPE_CHECKING, Any, Literal

from magi_agent.packs.context import PrimitiveType

if TYPE_CHECKING:  # pragma: no cover - typing only
    from magi_agent.packs.loader import LoadedPrimitive

logger = logging.getLogger(__name__)

Origin = Literal["first_party", "user"]
PrimitiveImpl = Callable[..., Any]
_FIRST_PARTY_PACK_ID_PREFIX = "open" "magi."


class ForbiddenRefError(KeyError):
    """Raised by ``resolve`` when a ref has been explicitly forbidden by a pack."""


@dataclass(frozen=True)
class RegistryEntry:
    ptype: PrimitiveType
    ref: str
    impl: PrimitiveImpl
    priority: int
    phase: str | None
    gate_position: Literal["before", "after"] | None
    origin: Origin
    _seq: int  # registration order tiebreaker (ascending)


class PrimitiveRegistry:
    """Keyed registry over ``(ptype, ref)``."""

    def __init__(self) -> None:
        self._entries: dict[tuple[PrimitiveType, str], RegistryEntry] = {}
        self._forbidden: set[tuple[PrimitiveType, str]] = set()
        self._seq = count()

    def register(self, ref: str, impl: PrimitiveImpl, *, ptype: PrimitiveType,
                 priority: int = 0, phase: str | None = None,
                 gate_position: Literal["before", "after"] | None = None,
                 origin: Origin = "user", override: bool = False) -> None:
        key = (ptype, ref)
        if key in self._entries and not override:
            raise ValueError(f"primitive already registered: {ptype.value}:{ref} "
                             f"(pass override=True to replace)")
        self._forbidden.discard(key)
        self._entries[key] = RegistryEntry(
            ptype=ptype, ref=ref, impl=impl, priority=priority, phase=phase,
            gate_position=gate_position, origin=origin, _seq=next(self._seq),
        )

    def forbid(self, ref: str, *, ptype: PrimitiveType) -> None:
        key = (ptype, ref)
        self._entries.pop(key, None)
        self._forbidden.add(key)

    def resolve(self, ref: str, *, ptype: PrimitiveType) -> PrimitiveImpl:
        key = (ptype, ref)
        if key in self._forbidden:
            raise ForbiddenRefError(f"{ptype.value}:{ref} forbidden by a loaded pack")
        entry = self._entries.get(key)
        if entry is None:
            raise KeyError(f"unknown primitive: {ptype.value}:{ref}")
        return entry.impl

    def resolve_entry(self, ref: str, *, ptype: PrimitiveType) -> RegistryEntry:
        key = (ptype, ref)
        if key in self._forbidden:
            raise ForbiddenRefError(f"{ptype.value}:{ref} forbidden by a loaded pack")
        entry = self._entries.get(key)
        if entry is None:
            raise KeyError(f"unknown primitive: {ptype.value}:{ref}")
        return entry

    def list(self, *, ptype: PrimitiveType | None = None) -> list[RegistryEntry]:
        entries = [e for e in self._entries.values()
                   if ptype is None or e.ptype is ptype]
        # ordered types are sorted by (priority asc, registration order asc);
        # unordered types share the same stable key (priority defaults to 0).
        return sorted(entries, key=lambda e: (e.priority, e._seq))


class RegistryRegistrationSink:
    """Bridge a :class:`PrimitiveRegistry` onto the loader's ``RegistrationSink``.

    The loader (``magi_agent/packs/loader.py``) forwards every resolved
    ``LoadedPrimitive`` to ``sink.register(primitive)`` in resolved pack order.
    This adapter maps each onto the keyed registry's ``register(ref, impl, *,
    ptype, ...)`` call, applying **last-wins override** (D1) so a later pack
    (e.g. a user pack ordered after the bundled first-party dir) replaces an
    earlier same-``(type, ref)`` impl with NO first-party privilege (§1).

    Impl-less primitives (declarative ``recipe`` specs) are skipped — they are
    not callable registry entries; their refs already flow into the catalog via
    ``catalog_build`` and their spec paths via the loader's ``LoadResult``.
    """

    def __init__(self, registry: PrimitiveRegistry) -> None:
        self._registry = registry

    def register(self, primitive: "LoadedPrimitive") -> None:
        if primitive.impl is None:
            return
        ptype = PrimitiveType(primitive.type)
        gate_position = primitive.gate_position
        if gate_position not in ("before", "after", None):
            gate_position = None
        self._registry.register(
            primitive.ref,
            primitive.impl,
            ptype=ptype,
            priority=primitive.priority or 0,
            phase=primitive.phase,
            gate_position=gate_position,
            origin="first_party" if primitive.pack_id.startswith(_FIRST_PARTY_PACK_ID_PREFIX) else "user",
            override=True,  # last-wins: a later pack replaces an earlier same-ref impl
        )


class KeyedRefRegistry:
    """A tiny keyed registry for declarative-object provides types whose primitive
    is data (``evidence_producer``/``recipe``/``connector``/``harness``), not a
    live runtime class. add/override/remove with NO first-party privilege."""

    def __init__(self) -> None:
        self._entries: dict[str, Any] = {}

    def register(self, ref: str, value: Any) -> None:
        self._entries[ref] = value

    def replace(self, ref: str, value: Any) -> None:
        self._entries[ref] = value

    def remove(self, ref: str) -> None:
        self._entries.pop(ref, None)

    def resolve(self, ref: str) -> Any | None:
        return self._entries.get(ref)

    def list_refs(self) -> tuple[str, ...]:
        return tuple(self._entries.keys())


@dataclass(frozen=True)
class LoadReport:
    """Result of projecting loaded primitives into the live registries.

    ``registered`` are the refs that reached a live registry; ``removed`` are the
    refs forbidden by a ``"-"``-prefixed pack entry.
    """

    registered: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()


class PackRegistries:
    """Container of the live primitive registries the provider impls register into.

    ``tools`` / ``hooks`` are the EXISTING runtime registries (their removal is
    ``unregister``); the four keyed registries are greenfield (removal is
    ``remove``). A parallel ``_hook_handlers`` map carries each callback's handler
    (the ``HookRegistry`` stores only the manifest)."""

    def __init__(self) -> None:
        from magi_agent.hooks.registry import HookRegistry
        from magi_agent.tools.registry import ToolRegistry

        self.tools = ToolRegistry()
        self.hooks = HookRegistry()
        self.evidence_producers = KeyedRefRegistry()
        # Validator impls (Callable[[ValidatorCtx], None]) keyed by validator ref.
        # The engine's pre-final gate runs these to OBSERVE a passing ref (PR2).
        self.validators = KeyedRefRegistry()
        self.recipes = KeyedRefRegistry()
        self.roles = KeyedRefRegistry()
        self.connectors = KeyedRefRegistry()
        self.harnesses = KeyedRefRegistry()
        # Pack C policy registries (same KeyedRefRegistry shape as harnesses).
        self.loop_policies = KeyedRefRegistry()
        self.schedule_policies = KeyedRefRegistry()
        self.memory_strategies = KeyedRefRegistry()
        # C1: gate5b workspace tool handlers, keyed by TOOL NAME (not provides ref).
        self.workspace_tool_handlers = KeyedRefRegistry()
        # PR6: plain inline tool handlers ``(args, ToolCtx) -> output``, keyed by
        # TOOL NAME. These need no WorkspaceHostView (a vanilla third-party tool).
        self.tool_inline_handlers = KeyedRefRegistry()
        self._hook_handlers: dict[str, Any] = {}

    @classmethod
    def empty(cls) -> "PackRegistries":
        return cls()

    def hooks_handler(self, name: str) -> Any | None:
        return self._hook_handlers.get(name)


def _provide_tool(registries: PackRegistries, ref: str) -> Callable[[Any], None]:
    def register(manifest: Any) -> None:
        if registries.tools.resolve(manifest.name) is None:
            registries.tools.register(manifest)
        else:
            registries.tools.replace(manifest)
    return register


def _provide_evidence(registries: PackRegistries) -> Callable[..., None]:
    def register(ref: str, spec: Any) -> None:
        registries.evidence_producers.replace(ref, spec)
    return register


def _provide_recipe(registries: PackRegistries) -> Callable[..., None]:
    def register(ref: str, manifest: Any) -> None:
        registries.recipes.replace(ref, manifest)
    return register


def _register_code_recipe(
    registries: PackRegistries, ref: str, callable_impl: Any, *, pack_id: str
) -> bool:
    """Invoke a code-recipe ``spec_callable`` ONCE and register its manifest (PR4).

    Determinism contract: ``callable_impl`` is invoked exactly once here, at
    registration time (never during a turn); it must be idempotent and side-effect
    free. It returns a :class:`RecipePackManifest` or a dict (validated through
    ``model_validate``). Untrusted (non first-party) packs additionally pass the
    SAME compose-only external trust boundary as declarative recipe specs
    (``validate_external_recipe_pack``: R1 ext-namespace / R4 no-hardSafety / R6
    no-ownership / R7 no-defaultEnabled) AND the SAME R2 ref-closure: their refs
    must resolve in the first-party recipe-pack ref universe, else the dangling
    code recipe is dropped. Fail-closed: any failure drops the pack with a warning
    and returns ``False`` (never raises). Returns ``True`` when the manifest
    reached ``registries.recipes``.
    """
    from magi_agent.recipes.compiler import PackRegistry, RecipePackManifest
    from magi_agent.recipes.kernel_recipe_packs import (
        build_recipe_ref_universe,
        recipe_pack_ref_closure_reason,
        validate_external_recipe_pack,
    )

    try:
        result = callable_impl()
    except Exception:  # noqa: BLE001 - a broken publisher never crashes the run
        logger.warning("code recipe %r dropped (spec_callable raised)", ref)
        return False

    if isinstance(result, RecipePackManifest):
        manifest = result
    elif isinstance(result, dict):
        try:
            manifest = RecipePackManifest.model_validate(result)
        except Exception:  # noqa: BLE001 - malformed manifest drops, never raises
            logger.warning("code recipe %r dropped (invalid manifest)", ref)
            return False
    else:
        logger.warning(
            "code recipe %r dropped (spec_callable returned %s, expected "
            "RecipePackManifest or dict)",
            ref,
            type(result).__name__,
        )
        return False

    # Trust by provenance, mirroring kernel_recipe_packs: bundled first-party
    # packs register as-is; user/ext packs must pass the compose-only boundary.
    trusted = pack_id.startswith(_FIRST_PARTY_PACK_ID_PREFIX)
    if not trusted:
        reason = validate_external_recipe_pack(manifest)
        if reason:
            logger.warning("code recipe %r dropped (%s)", ref, reason)
            return False
        # R2 ref-closure for code recipes: resolve against the trusted first-party
        # recipe-pack universe ONLY (the candidate cannot contribute to its own
        # universe; see _drop_unresolved_external_recipe_packs). Code recipes
        # register one at a time into a keyed registry (no post-load pass like the
        # declarative PackRegistry), so the universe is the stable first-party
        # baseline.
        universe = build_recipe_ref_universe(
            PackRegistry.with_first_party_packs().values()
        )
        closure_reason = recipe_pack_ref_closure_reason(manifest, universe)
        if closure_reason:
            logger.warning("code recipe %r dropped (%s)", ref, closure_reason)
            return False

    registries.recipes.replace(ref, manifest)
    return True


def _provide_connector(registries: PackRegistries) -> Callable[..., None]:
    def register(ref: str, spec: Any) -> None:
        registries.connectors.replace(ref, spec)
    return register


def _provide_harness(registries: PackRegistries) -> Callable[..., None]:
    def register(ref: str, pack: Any) -> None:
        registries.harnesses.replace(ref, pack)
    return register


def _provide_callback(registries: PackRegistries) -> Callable[..., None]:
    def register(manifest: Any, handler: Any) -> None:
        if registries.hooks.resolve(manifest.name) is None:
            registries.hooks.register(manifest)
        else:
            registries.hooks.replace(manifest)
        registries._hook_handlers[manifest.name] = handler
    return register


def _provide_keyed(registry: KeyedRefRegistry) -> Callable[..., None]:
    def register(ref: str, value: Any) -> None:
        registry.replace(ref, value)
    return register


def _provide_workspace_handler(registries: PackRegistries) -> Callable[[str, Any], None]:
    def register(tool_name: str, handler: Any) -> None:
        registries.workspace_tool_handlers.replace(tool_name, handler)
    return register


def _provide_tool_handler(registries: PackRegistries) -> Callable[[Any, Any], None]:
    """PR6: register a manifest AND a plain inline handler in one call.

    The handler ``(args: Mapping[str, object], tool_ctx: ToolCtx) -> output`` is
    keyed by ``manifest.name`` so the CLI merge can bind it directly without a
    WorkspaceHostView."""

    register_manifest = _provide_tool(registries, ref="")

    def register(manifest: Any, handler: Any) -> None:
        register_manifest(manifest)
        registries.tool_inline_handlers.replace(manifest.name, handler)
    return register


def project_into_registries(
    primitives: "tuple[LoadedPrimitive, ...] | list[LoadedPrimitive]",
    registries: PackRegistries,
) -> LoadReport:
    """Call each provider impl with its typed D5 provide-context so the declared
    primitive reaches the matching live registry (the per-type live seam).

    Removal/forbid is NOT a ``"-"``-prefix entry (the kernel manifest schema
    rejects an entry without ``impl``/``spec``); it is ``config.toml [packs]
    disable = ["<pack_id>"]`` applied upstream by ``resolve_enabled_packs`` (the
    real Phase-3 convention), so a disabled pack's primitives never reach here.
    First-party and user provider impls flow through the IDENTICAL path
    (§1 no privilege)."""
    from magi_agent.packs import context as _ctx

    registered: list[str] = []
    removed: list[str] = []
    # Deduplicate by (type, ref) keeping the LAST primitive (last-wins override:
    # a user pack ordered after first-party replaces the same-ref impl). Iterating
    # the raw list would double-register and let the first-party impl win.
    deduped: dict[tuple[str, str], Any] = {}
    for primitive in primitives:
        deduped[(primitive.type, primitive.ref)] = primitive
    for primitive in deduped.values():
        ptype = primitive.type
        ref = primitive.ref
        impl = primitive.impl
        # Declarative recipe: no impl, a ``spec`` relpath resolved by the loader.
        # Read it, validate as a RecipePackManifest, and register (D3 declarative).
        if impl is None:
            spec_path = getattr(primitive, "spec_path", None)
            if ptype == "recipe" and spec_path is not None:
                import tomllib as _tomllib

                from magi_agent.recipes.compiler import RecipePackManifest

                with open(spec_path, "rb") as _fh:
                    _raw = _tomllib.load(_fh)
                registries.recipes.replace(ref, RecipePackManifest.model_validate(_raw))
                registered.append(ref)
            elif ptype == "role" and spec_path is not None:
                # Declarative scope label: parse the RoleManifest via the shared
                # harness-side parser (same schema the harness reader uses).
                from magi_agent.harness.kernel_roles import parse_role_manifest

                manifest = parse_role_manifest(spec_path)
                if manifest is not None:
                    registries.roles.replace(ref, manifest)
                    registered.append(ref)
            continue
        if ptype == "tool":
            impl(_ctx.ToolProvideContext(
                register=_provide_tool(registries, ref),
                register_workspace_handler=_provide_workspace_handler(registries),
                register_handler=_provide_tool_handler(registries),
            ))
            registered.append(ref)
        elif ptype == "evidence_producer":
            impl(_ctx.EvidenceProducerProvideContext(register=_provide_evidence(registries)))
            registered.append(ref)
        elif ptype == "validator":
            # A ``validator`` primitive's ``impl`` IS the validator callable
            # ``(ValidatorCtx) -> ValidatorVerdict | None`` (D5); there is no
            # ``provide`` indirection (unlike tool/evidence_producer/...). Register
            # the impl directly so the engine's pre-final gate can run it.
            registries.validators.replace(ref, impl)
            registered.append(ref)
        elif ptype == "recipe":
            # Code-computed recipe-as-code (PR4): the loader imported the
            # ``spec_callable`` into ``impl``. Invoke it ONCE here, at
            # registration (NOT during a turn) — the callable must be idempotent
            # and side-effect free (determinism contract). Fail-closed: a
            # callable that raises, returns the wrong type, or fails the external
            # trust boundary drops the pack with a warning and never crashes the
            # run. Reaching this branch requires MAGI_RECIPE_AS_CODE_ENABLED (the
            # loader skips spec_callable entries when OFF), so OFF never gets here.
            if _register_code_recipe(registries, ref, impl, pack_id=primitive.pack_id):
                registered.append(ref)
        elif ptype == "connector":
            impl(_ctx.ConnectorProvideContext(register=_provide_connector(registries)))
            registered.append(ref)
        elif ptype == "harness":
            impl(_ctx.HarnessProvideContext(register=_provide_harness(registries)))
            registered.append(ref)
        elif ptype == "callback":
            impl(_ctx.CallbackProvideContext(register=_provide_callback(registries)))
            registered.append(ref)
        elif ptype == "loop_policy":
            impl(_ctx.LoopPolicyProvideContext(
                register=_provide_keyed(registries.loop_policies)))
            registered.append(ref)
        elif ptype == "schedule_policy":
            impl(_ctx.SchedulePolicyProvideContext(
                register=_provide_keyed(registries.schedule_policies)))
            registered.append(ref)
        elif ptype == "memory_strategy":
            impl(_ctx.MemoryStrategyProvideContext(
                register=_provide_keyed(registries.memory_strategies)))
            registered.append(ref)
    return LoadReport(registered=tuple(registered), removed=tuple(removed))


def load_into_registries(
    bases: "list[Any]", registries: PackRegistries | None = None
) -> "tuple[PackRegistries, LoadReport]":
    """Full discover -> config -> load -> project pipeline for the provide types.

    Mirrors the Phase-3 validator wiring (``discover_pack_files`` ->
    ``resolve_enabled_packs`` -> ``load_packs``) and then projects each loaded
    primitive into its live registry via :func:`project_into_registries`.
    Returns ``(registries, report)``; ``report.registered`` lists every ref that
    reached a live registry."""
    from magi_agent.packs.discovery import (
        discover_pack_files,
        load_packs_config,
        resolve_enabled_packs,
    )
    from magi_agent.packs.loader import RecordingSink, load_packs

    if registries is None:
        registries = PackRegistries()
    discovered = discover_pack_files(list(bases))
    enabled = resolve_enabled_packs(discovered, load_packs_config())
    sink = RecordingSink()
    result = load_packs(enabled, sink)
    report = project_into_registries(result.primitives, registries)
    return registries, report


def build_control_plane_from_packs(
    bases: "list[Any] | None" = None,
    *,
    os_environ: "dict[str, str] | None" = None,
    general_automation_receipts: Any | None = None,
    contract_required: Any | None = None,
    agent_role: str = "general",
    self_review_fork_runner: Any | None = None,
    self_review_candidate_sink: Any | None = None,
    self_review_config: Any | None = None,
    self_review_now: Any | None = None,
    self_review_scheduler: Any | None = None,
    tool_synthesis_model_label: str | None = None,
    extra_controls: "list[Any] | None" = None,
) -> Any:
    """Assemble a live ``ControlPlane`` from the loaded ``control_plane`` packs (D7
    keystone). The de-privileging seam: first-party controls flow in from the
    bundled ``control_plane`` pack through the SAME loader a user
    ``~/.magi/packs`` control_plane pack uses — no hardcoded ``plane.register``.

    Each loaded ``control_plane`` provider impl is invoked with a typed
    :class:`ControlPlaneProvideContext` (carrying ``env`` + the same collaborators
    ``build_default_plane`` accepts) and registers whatever ``LoopControl``s it
    builds into the shared plane. Provider order is the loader's resolved
    ``(priority, registration)`` order; last-wins override on a colliding
    ``(type, ref)`` means a user pack that re-declares ``control_plane:default@1``
    fully replaces the bundled one (§1 remove/override). ``extra_controls`` are
    registered after the packs, in parallel, for direct in-process injection.

    Returns a ``ControlPlane`` ready to wrap in ``_ExtendedControlPlanePlugin``.
    """
    from magi_agent.adk_bridge.control_plane import ControlPlane
    from magi_agent.packs.context import ControlPlaneProvideContext
    from magi_agent.packs.discovery import (
        default_search_bases,
        discover_pack_files,
        load_packs_config,
        resolve_enabled_packs,
    )
    from magi_agent.packs.loader import RecordingSink, load_packs

    import os as _os

    env = os_environ if os_environ is not None else dict(_os.environ)
    search_bases = list(bases) if bases is not None else default_search_bases()

    discovered = discover_pack_files(search_bases)
    enabled = resolve_enabled_packs(discovered, load_packs_config())
    # Filter to packs that statically declare a control_plane provides entry
    # BEFORE load_packs. load_packs lazily imports EVERY enabled pack's impl, so a
    # broken/missing-dependency NON-control pack (e.g. a tool pack a user installed
    # whose impl imports a package they lack) would raise here and break the whole
    # control-plane assembly — even though it contributes no controls. The filter
    # is manifest-level (no impl import for non-control packs); base/last-wins
    # order is preserved since resolve_enabled_packs already ordered ``enabled``.
    control_enabled = [
        disc
        for disc in enabled
        if any(p.type == "control_plane" for p in disc.manifest.provides)
    ]
    sink = RecordingSink()
    result = load_packs(control_enabled, sink)

    # Resolve the registry-ordered control_plane provider impls. The keyed
    # PrimitiveRegistry applies last-wins override + (priority, registration)
    # ordering exactly as the runtime fan-out expects — identical to how the
    # ContextDispatcher resolves control entries.
    registry = PrimitiveRegistry()
    sink_adapter = RegistryRegistrationSink(registry)
    for primitive in result.primitives:
        # phase="tool_host" entries are gate5b dispatch ctx-callables, not
        # LoopControl providers — they load via build_tool_host_runtime_from_packs.
        if primitive.type == "control_plane" and primitive.phase != "tool_host":
            sink_adapter.register(primitive)

    plane = ControlPlane()
    provide_ctx = ControlPlaneProvideContext(
        register=plane.register,
        env=env,
        general_automation_receipts=general_automation_receipts,
        contract_required=contract_required,
        agent_role=agent_role,
        self_review_fork_runner=self_review_fork_runner,
        self_review_candidate_sink=self_review_candidate_sink,
        self_review_config=self_review_config,
        self_review_now=self_review_now,
        self_review_scheduler=self_review_scheduler,
        tool_synthesis_model_label=tool_synthesis_model_label,
    )
    for entry in registry.list(ptype=PrimitiveType.CONTROL_PLANE):
        entry.impl(provide_ctx)

    for control in extra_controls or ():
        plane.register(control)
    return plane


def build_tool_host_runtime_from_packs(
    bases: "list[Any] | None" = None,
) -> "tuple[dict[str, Any], tuple[Any, ...]]":
    """Load the gate5b workspace runtime from packs (C1 keystone).

    Returns ``(workspace_handlers_by_tool_name, dispatch_policy_impls)``:
    - handlers from every loaded ``tool`` entry that bound a workspace handler;
    - ``control_plane`` entries with ``phase == "tool_host"`` as ctx-callables,
      ordered by ``(priority, registration)`` via the keyed PrimitiveRegistry
      (last-wins override — a user pack replaces a first-party ref, §1).

    Mirrors ``build_control_plane_from_packs``: only packs that statically
    declare a relevant entry are impl-imported, so a broken unrelated user pack
    cannot break the tool host.
    """
    from magi_agent.packs.discovery import (
        default_search_bases,
        discover_pack_files,
        load_packs_config,
        resolve_enabled_packs,
    )
    from magi_agent.packs.loader import RecordingSink, load_packs

    search_bases = list(bases) if bases is not None else default_search_bases()
    discovered = discover_pack_files(search_bases)
    enabled = resolve_enabled_packs(discovered, load_packs_config())
    relevant = [
        disc
        for disc in enabled
        if any(
            p.type == "tool" or (p.type == "control_plane" and p.phase == "tool_host")
            for p in disc.manifest.provides
        )
    ]
    sink = RecordingSink()
    result = load_packs(relevant, sink)

    registries = PackRegistries()
    tool_primitives = [p for p in result.primitives if p.type == "tool"]
    project_into_registries(tool_primitives, registries)
    handlers = {
        name: registries.workspace_tool_handlers.resolve(name)
        for name in registries.workspace_tool_handlers.list_refs()
    }

    registry = PrimitiveRegistry()
    adapter = RegistryRegistrationSink(registry)
    for primitive in result.primitives:
        if primitive.type == "control_plane" and primitive.phase == "tool_host":
            adapter.register(primitive)
    policies = tuple(
        entry.impl for entry in registry.list(ptype=PrimitiveType.CONTROL_PLANE)
    )
    return handlers, policies
