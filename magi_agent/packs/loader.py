"""Pack loader (D3/D6): discovery -> lazy impl import -> registry registration.

This phase OWNS the loader->registry seam:
  * ``LoadedPrimitive`` — one resolved provides entry (code symbol or spec path).
  * ``RegistrationSink`` — the minimal protocol a registry must satisfy.
  * ``RecordingSink`` — an in-memory sink used by tests (and a fallback).

Phase 2's ``magi_agent/packs/registries.py`` supplies a concrete sink that also
satisfies ``RegistrationSink``; nothing here is thrown away by Phase 2.

Impls are imported LAZILY here (at registration time) — never during manifest
parse (D3). ``recipe`` entries carry a ``spec`` relpath resolved against the
pack dir and are registered WITHOUT importing anything.
"""
from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from magi_agent.packs.discovery import DiscoveredPack
from magi_agent.packs.manifest import ProvidesType

if TYPE_CHECKING:  # pragma: no cover - typing only
    from magi_agent.packs.types import CompileRecipePackCatalog


def lazy_import_symbol(impl: str, *, search_root: Path | None = None) -> Any:
    """Resolve a ``"module.path:symbol"`` string to the live object.

    Imports the module (lazily, at call time) and returns the attribute. Raises
    ``ValueError`` for a malformed ref and ``ImportError`` if the module or
    symbol cannot be resolved.

    ``search_root`` (B0, zero-setup disk packs): when the top-level module is
    not importable AND a matching package/module exists directly under
    ``search_root`` (the discovered pack's parent dir), the root is APPENDED to
    ``sys.path`` (append, not prepend — installed packages keep winning on a
    name collision) and the import retried once. ``importlib.invalidate_caches``
    is required because pack dirs are typically created after interpreter start.
    The appended entry is left in place (other entries of the same pack resolve
    through it); pack directory names should be unique across pack roots —
    ``sys.modules`` is keyed by top-level name, so the first pack imported under
    a given dir name wins.
    """
    if impl.count(":") != 1:
        raise ValueError(f"impl must be 'module.path:symbol', got {impl!r}")
    module_path, _, symbol = impl.partition(":")
    if not module_path or not symbol:
        raise ValueError(f"impl must be 'module.path:symbol', got {impl!r}")
    try:
        module = importlib.import_module(module_path)
    except ModuleNotFoundError as exc:
        top = module_path.split(".", 1)[0]
        if (
            search_root is None
            or exc.name != top
            or not (
                (search_root / top).is_dir()
                or (search_root / f"{top}.py").is_file()
            )
        ):
            raise
        root = str(search_root)
        if root not in sys.path:
            sys.path.append(root)
        importlib.invalidate_caches()
        module = importlib.import_module(module_path)
    try:
        return getattr(module, symbol)
    except AttributeError as exc:
        raise ImportError(f"symbol {symbol!r} not found in module {module_path!r}") from exc


@dataclass(frozen=True)
class LoadedPrimitive:
    """A resolved provides entry ready for registration.

    Exactly one of ``impl`` (code object) or ``spec_path`` (declarative recipe
    file) is set. Ordered-type metadata (priority/phase) and control_plane
    ``gate_position`` are carried through for Phase-2 ordering.
    """

    type: ProvidesType
    ref: str
    pack_id: str
    impl: Any | None = None
    spec_path: Path | None = None
    priority: int | None = None
    phase: str | None = None
    gate_position: str | None = None


@runtime_checkable
class RegistrationSink(Protocol):
    """Minimal interface the loader registers into.

    Phase 2's typed registries satisfy this protocol. Keeping it tiny is the
    whole point: the loader has no knowledge of how a registry stores/dispatches.
    """

    def register(self, primitive: LoadedPrimitive) -> None: ...


@dataclass
class RecordingSink:
    """In-memory ``RegistrationSink`` for tests and as a no-op fallback."""

    registered: list[LoadedPrimitive] = field(default_factory=list)

    def register(self, primitive: LoadedPrimitive) -> None:
        self.registered.append(primitive)


@dataclass(frozen=True)
class LoadResult:
    """Outcome of a load pass.

    ``overridden`` maps a colliding ``(type, ref)`` to the ``(loser, winner)``
    pack ids (last pack in resolved order wins). Phase 2 consumes this to honor
    ``config.toml [packs].override``.
    """

    primitives: tuple[LoadedPrimitive, ...]
    overridden: dict[tuple[str, str], tuple[str, str]]


def load_packs(
    discovered: list[DiscoveredPack], sink: RegistrationSink
) -> LoadResult:
    """Resolve every provides entry and register it into ``sink``.

    Code entries are lazily imported; recipe entries resolve their spec path.
    On a colliding ``(type, ref)`` the later pack wins (and the collision is
    recorded). All registrations are still forwarded to the sink in order so a
    Phase-2 registry can apply its own last-wins replacement.
    """
    primitives: list[LoadedPrimitive] = []
    overridden: dict[tuple[str, str], tuple[str, str]] = {}
    winners: dict[tuple[str, str], str] = {}

    for disc in discovered:
        pack_id = disc.manifest.pack_id
        for entry in disc.manifest.provides:
            key = (entry.type, entry.ref)
            if key in winners and winners[key] != pack_id:
                overridden[key] = (winners[key], pack_id)
            winners[key] = pack_id

            if entry.spec is not None:
                primitive = LoadedPrimitive(
                    type=entry.type,
                    ref=entry.ref,
                    pack_id=pack_id,
                    spec_path=(disc.pack_dir / entry.spec).resolve(),
                    priority=entry.priority,
                    phase=entry.phase,
                    gate_position=entry.gate_position,
                )
            else:
                assert entry.impl is not None  # manifest validator guarantees this
                primitive = LoadedPrimitive(
                    type=entry.type,
                    ref=entry.ref,
                    pack_id=pack_id,
                    impl=lazy_import_symbol(entry.impl, search_root=disc.pack_dir.parent),
                    priority=entry.priority,
                    phase=entry.phase,
                    gate_position=entry.gate_position,
                )
            primitives.append(primitive)
            sink.register(primitive)

    return LoadResult(primitives=tuple(primitives), overridden=overridden)


def load_from_bases(
    bases: list[Path], sink: RegistrationSink
) -> tuple[LoadResult, "CompileRecipePackCatalog"]:
    """Full D1->D3->D4 pipeline: discover -> config -> load+register -> catalog.

    ``build_catalog`` is imported locally to avoid a circular import
    (``catalog_build`` imports ``loader`` for ``LoadedPrimitive``).
    """
    from magi_agent.packs.catalog_build import build_catalog
    from magi_agent.packs.discovery import (
        discover_pack_files,
        load_packs_config,
        resolve_enabled_packs,
    )

    discovered = discover_pack_files(bases)
    config = load_packs_config()
    enabled = resolve_enabled_packs(discovered, config)
    result = load_packs(enabled, sink)
    catalog = build_catalog(result.primitives)
    return result, catalog
