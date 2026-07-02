"""N-31 lock: ``_SPECIAL_TOOL_METADATA`` alias groups carry the same surface.

``plugins/tool_projection._SPECIAL_TOOL_METADATA`` is a hand-maintained sidecar
that overlays projected metadata onto ``(plugin_id, tool_name)`` keys. Several
native tools are exposed under multiple names that share ONE ``entrypoint``
(e.g. ``WebSearch`` / ``web-search`` / ``web_search`` all resolve to
``magi_agent.plugins.native.web:web_search``). If one alias in such a group
carries metadata and a sibling does not, the sibling silently falls back to the
generic fail-closed default (a quiet capability downgrade).

Rather than pin the three known groups by hand, this test DERIVES the alias
groups from the native catalog (tools sharing an entrypoint within a plugin) so
that any future alias is covered automatically. Two defences:

1. Table parity: every alias in a derived group is either all-absent from
   ``_SPECIAL_TOOL_METADATA`` or all-present with the same overlay value.
2. Projection parity: the projected ``ToolManifest`` for each alias in a group
   is identical modulo the tool ``name``.
"""

from __future__ import annotations

from collections import defaultdict

from magi_agent.plugins.manager import resolve_plugin_state
from magi_agent.plugins.native_catalog import native_plugin_manifests
from magi_agent.plugins.tool_projection import (
    _SPECIAL_TOOL_METADATA,
    project_native_plugin_tool_manifests,
)


def _alias_groups() -> list[tuple[str, tuple[str, ...]]]:
    """Return (plugin_id, tool_names) for every intra-plugin group of tools
    that share one entrypoint and has at least two members."""
    groups: list[tuple[str, tuple[str, ...]]] = []
    for plugin in native_plugin_manifests():
        by_entrypoint: dict[str, list[str]] = defaultdict(list)
        for tool in plugin.tools:
            by_entrypoint[tool.entrypoint].append(tool.name)
        for names in by_entrypoint.values():
            if len(names) >= 2:
                groups.append((plugin.plugin_id, tuple(names)))
    return groups


def test_derived_alias_groups_are_non_empty() -> None:
    # Guard: if the catalog stops sharing entrypoints the parity test would
    # vacuously pass. The web/knowledge/okf groups keep this above zero.
    assert len(_alias_groups()) >= 3


def test_special_tool_metadata_alias_groups_have_consistent_overlay() -> None:
    failures: list[str] = []
    for plugin_id, names in _alias_groups():
        overlays = {
            name: _SPECIAL_TOOL_METADATA.get((plugin_id, name)) for name in names
        }
        present = {name for name, value in overlays.items() if value is not None}
        absent = {name for name, value in overlays.items() if value is None}
        if present and absent:
            failures.append(
                f"{plugin_id}: alias group {names} is split — "
                f"{sorted(present)} carry _SPECIAL_TOOL_METADATA but "
                f"{sorted(absent)} do not (silent fail-closed downgrade)."
            )
            continue
        if present:
            values = [overlays[name] for name in names]
            first = values[0]
            if any(value != first for value in values[1:]):
                failures.append(
                    f"{plugin_id}: alias group {names} carries divergent "
                    "_SPECIAL_TOOL_METADATA overlays."
                )
    assert not failures, "\n  - " + "\n  - ".join(failures)


def test_projected_manifests_match_within_alias_group() -> None:
    state = resolve_plugin_state(native_plugin_manifests())
    projected = {m.name: m for m in project_native_plugin_tool_manifests(state)}

    failures: list[str] = []
    for plugin_id, names in _alias_groups():
        present = [name for name in names if name in projected]
        if len(present) < 2:
            continue
        dumps = []
        for name in present:
            dump = projected[name].model_dump()
            dump.pop("name", None)
            dumps.append((name, dump))
        first_name, first_dump = dumps[0]
        for name, dump in dumps[1:]:
            if dump != first_dump:
                failures.append(
                    f"{plugin_id}: projected manifests for {first_name} and "
                    f"{name} differ beyond their tool name."
                )
    assert not failures, "\n  - " + "\n  - ".join(failures)
