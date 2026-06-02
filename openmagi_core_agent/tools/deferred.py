"""DeferredToolRegistry — threshold-based lazy tool loading.

When the total tool count exceeds a configurable threshold, tools marked with
``should_defer=True`` are held back from the initial tool pool. The LLM
receives only the non-deferred tools plus a system prompt hint listing the
deferred tool names. The model can load deferred tools on demand via
ToolSearchTool.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .manifest import ToolManifest
from .registry import ToolRegistry


@dataclass(frozen=True)
class InitialToolSet:
    active_manifests: tuple[ToolManifest, ...]
    deferred_names: tuple[str, ...]
    hint_text: str | None = None


class DeferredToolRegistry:
    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry
        self._deferred_names: set[str] = set()

    def get_initial_tools(self, *, threshold: int = 30) -> InitialToolSet:
        all_tools = self._registry.list_all()

        if len(all_tools) <= threshold:
            self._deferred_names = set()
            return InitialToolSet(
                active_manifests=tuple(all_tools),
                deferred_names=(),
                hint_text=None,
            )

        active: list[ToolManifest] = []
        deferred: list[str] = []

        for manifest in all_tools:
            if _must_keep_active(manifest):
                active.append(manifest)
            else:
                deferred.append(manifest.name)

        self._deferred_names = set(deferred)

        hint_text = _build_hint_text(deferred) if deferred else None

        return InitialToolSet(
            active_manifests=tuple(active),
            deferred_names=tuple(sorted(deferred)),
            hint_text=hint_text,
        )

    def load_deferred(self, names: list[str]) -> list[ToolManifest]:
        results: list[ToolManifest] = []
        for name in names:
            manifest = self._registry.resolve(name)
            if manifest is not None:
                results.append(manifest)
                self._deferred_names.discard(name)
        return results

    @property
    def deferred_names(self) -> frozenset[str]:
        return frozenset(self._deferred_names)


def _must_keep_active(manifest: ToolManifest) -> bool:
    if not manifest.should_defer:
        return True
    if manifest.dangerous:
        return True
    return False


def _build_hint_text(deferred_names: list[str]) -> str:
    names_list = ", ".join(sorted(deferred_names))
    return (
        f"Deferred tools available (use ToolSearch to load): {names_list}"
    )
