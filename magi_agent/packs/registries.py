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
