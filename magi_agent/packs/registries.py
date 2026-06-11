"""Typed primitive registries (D3/D4). One keyed registry for all 8 provides types.

First-party and user impls register through the IDENTICAL path (§1 "no privilege"):
``origin`` is metadata only and never blocks override/forbid or grants capability.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from itertools import count
from typing import TYPE_CHECKING, Any, Literal

from magi_agent.packs.context import PrimitiveType

if TYPE_CHECKING:  # pragma: no cover - typing only
    from magi_agent.packs.loader import LoadedPrimitive

Origin = Literal["first_party", "user"]
PrimitiveImpl = Callable[..., Any]


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
            origin="first_party" if primitive.pack_id.startswith("openmagi.") else "user",
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
        self.recipes = KeyedRefRegistry()
        self.connectors = KeyedRefRegistry()
        self.harnesses = KeyedRefRegistry()
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
        if impl is None:
            continue
        if ptype == "tool":
            impl(_ctx.ToolProvideContext(register=_provide_tool(registries, ref)))
            registered.append(ref)
        elif ptype == "evidence_producer":
            impl(_ctx.EvidenceProducerProvideContext(register=_provide_evidence(registries)))
            registered.append(ref)
        elif ptype == "recipe":
            impl(_ctx.RecipeProvideContext(register=_provide_recipe(registries)))
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
