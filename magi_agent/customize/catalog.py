from __future__ import annotations

from typing import Any

# The fixed set of runtime hook-point names exposed by the live runtime shell.
# OpenMagiRuntime has no hook_registry attribute — it is a thin shell that owns
# only tool_registry. Hook manifests are not surfaced via any runtime accessor;
# the /v1/app/skills endpoint (app_api._RUNTIME_HOOK_POINTS) uses this same
# hardcoded list. We source from there via import so both surfaces stay in sync.
from magi_agent.customize.preset_map import (
    description_for,
    domain_for,
    enforcement_for,
    opt_method_for,
    supported_modes_for,
    tier_for,
)
from magi_agent.customize.what_menu import what_menu
from magi_agent.harness.presets import builtin_preset_catalog
from magi_agent.transport.app_api import _RUNTIME_HOOK_POINTS as _HOOK_POINTS

# Curated constants mirror REAL recipe modules under magi_agent/recipes/first_party/
# and the documented harness presets (docs/harness-schema.md). Phase 2 wires their
# selection to enforcement; Phase 1 surfaces them so the UI reaches parity.
RECIPES: list[dict[str, str]] = [
    {"id": "research", "title": "Research", "category": "research",
     "source": "docs/recipes.md",
     "description": "Multi-source research with grounded synthesis."},
    {"id": "coding_evidence_gate", "title": "Coding Evidence Gate", "category": "coding",
     "source": "magi_agent/recipes/first_party/coding",
     "description": "Require evidence before committing code changes."},
    {"id": "coding_mutation", "title": "Coding Mutation", "category": "coding",
     "source": "magi_agent/recipes/first_party/coding",
     "description": "Apply and verify workspace code mutations."},
    {"id": "general_automation", "title": "General Automation", "category": "task",
     "source": "magi_agent/recipes/first_party/general_automation",
     "description": "General multi-step task automation."},
    {"id": "memory_recall", "title": "Memory Recall", "category": "memory",
     "source": "magi_agent/recipes/first_party/memory_recall.py",
     "description": "Recall prior context from the memory ledger."},
    {"id": "self_improvement", "title": "Self Improvement", "category": "task",
     "source": "magi_agent/recipes/first_party/self_improvement.py",
     "description": "Gated self-improvement proposal loop."},
]

def _title_from_key(key: str) -> str:
    return key.replace("-", " ").title()


def _build_harness_presets() -> list[dict[str, Any]]:
    """Source the real harness preset catalog (hyphenated ids, 36 presets).

    Each entry carries the runtime-honest ``enforcement`` status and
    ``supportedModes`` from ``customize.preset_map`` so the UI never shows a
    toggle that does nothing.
    """
    entries: list[dict[str, Any]] = []
    for preset in builtin_preset_catalog():
        category = preset.category.value
        is_security = bool(preset.hard_safety or preset.security_critical)
        entries.append(
            {
                "id": preset.key,
                "title": _title_from_key(preset.key),
                "category": category,
                # WHEN-group + raw fire-at points so the modal can group by
                # condition rather than semantic category (spec §7).
                "domain": domain_for(category),
                "hookPoints": list(preset.hook_points),
                "defaultEnabled": bool(preset.default_on),
                "enforcement": enforcement_for(
                    preset.key, category=category, is_security=is_security
                ),
                # Badge data: enforcement mechanism + opt-out/opt-in method.
                "tier": tier_for(preset.key, is_security=is_security),
                "optMethod": opt_method_for(preset.key),
                "description": description_for(preset.key),
                "supportedModes": list(supported_modes_for(preset.key)),
            }
        )
    return entries


# Real harness preset catalog (36 presets), built once at import.
HARNESS_PRESETS: list[dict[str, Any]] = _build_harness_presets()


def _recipe_entries() -> list[dict[str, Any]]:
    return [{**r, "enabled": True} for r in RECIPES]


def _preset_entries() -> list[dict[str, Any]]:
    # ``enabled`` reflects the catalog default; the user's persisted override is
    # layered separately by the frontend from the overrides payload.
    return [{**p, "enabled": p["defaultEnabled"]} for p in HARNESS_PRESETS]


def _hook_entries(runtime: Any) -> list[dict[str, Any]]:
    # OpenMagiRuntime is a thin shell — it exposes only tool_registry; there is
    # no hook_registry attribute. Hook points are sourced from the same fixed
    # tuple that /v1/app/skills uses (_HOOK_POINTS, imported above). Each entry
    # is a builtin runtime-level hook point; none are user-opt-out-able, so all
    # are alwaysOn=True / category="security".
    entries: list[dict[str, Any]] = []
    for point_name in _HOOK_POINTS:
        entries.append(
            {
                "name": point_name,
                "point": point_name,  # already a plain camelCase string
                "title": point_name,
                "category": "security",
                "alwaysOn": True,
                "enabled": True,
            }
        )
    return entries


def _tool_entries(runtime: Any) -> list[dict[str, Any]]:
    # list_all() returns ToolManifest objects directly. The manifest only has
    # enabled_by_default; live enabled lives in ToolRegistration. We resolve
    # each registration to get the real enabled value — consistent with how
    # /api/tools (_public_tools) derives it in magi_agent/transport/tools.py.
    entries: list[dict[str, Any]] = []
    for manifest in runtime.tool_registry.list_all():
        registration = runtime.tool_registry.resolve_registration(manifest.name)
        enabled = registration.enabled if registration is not None else False
        source = manifest.source
        # source may be a ToolSource object (with .kind) or already a string
        # (e.g. in lightweight fakes). Normalise to string.
        source_str: str = source.kind if hasattr(source, "kind") else str(source)
        entries.append(
            {
                "name": manifest.name,
                "description": manifest.description if manifest.description else "",
                "enabled": bool(enabled),
                "source": source_str,
                "dangerous": bool(getattr(manifest, "dangerous", False)),
            }
        )
    return entries


def build_catalog(runtime: Any) -> dict[str, Any]:
    return {
        "verification": {
            "recipes": _recipe_entries(),
            "harnessPresets": _preset_entries(),
            "hooks": _hook_entries(runtime),
            # Producer-backed deterministic checks the custom-rule builder may
            # require (spec §9.1 / §12). Empty-safe.
            "customRuleMenu": what_menu(),
        },
        "tools": _tool_entries(runtime),
    }
