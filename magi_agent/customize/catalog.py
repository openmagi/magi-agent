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
from magi_agent.customize.what_menu import evidence_menu, judgment_menu, what_menu
from magi_agent.customize.preset_map import scope_for_preset
from magi_agent.harness.presets import builtin_preset_catalog
from magi_agent.transport.app_api import _RUNTIME_HOOK_POINTS as _HOOK_POINTS

# Phase 3 — map the UI ``RECIPES.id`` label to the real
# :class:`magi_agent.recipes.compiler.RecipePackManifest` pack ids the
# enforcement layer can subtract evidence_refs for when the user opts a recipe
# out. Curated and conservative: a RECIPES.id without a mapping (or an empty
# mapping) is a UI-only label — disabling it is a deliberate no-op. Security-
# critical packs (``openmagi.context-safety``, ``openmagi.evidence``,
# ``openmagi.source-grounded``) are intentionally NOT mapped so a user cannot
# disable hard-safety obligations through this seam.
RECIPE_ID_TO_PACK_IDS: dict[str, tuple[str, ...]] = {
    "research": ("openmagi.research", "openmagi.research-scout"),
    "coding_evidence_gate": ("openmagi.dev-coding",),
    "coding_mutation": ("openmagi.dev-coding",),
    "general_automation": ("openmagi.agent-methodology",),
    "memory_recall": ("openmagi.memory-agentmemory",),
    # F-LIFE5 — Self Improvement now resolves to a real pack. The pack itself
    # is default-off (RecipePackManifest.defaultEnabled=False) AND the live
    # loop is additionally gated by the MAGI_LEARNING_ENABLED master flag (and
    # the optional MAGI_CUSTOMIZE_SELF_IMPROVEMENT_ENABLED sibling). The two
    # frozen policies (eval-observation-required, no-direct-mutation) remain
    # in force regardless of this toggle.
    "self_improvement": ("openmagi.self-improvement",),
}


def pack_ids_for_recipe(recipe_id: str) -> tuple[str, ...]:
    """Return the real pack ids a UI recipe id maps to; ``()`` for unmapped."""
    return RECIPE_ID_TO_PACK_IDS.get(recipe_id, ())


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
                # Phase 1 — scope classification (mirrors customize/scope.SCOPES,
                # same vocabulary the custom-rule builder uses). Lets the modal
                # group presets by scope (Phase 4 UI), and lets the engine drop
                # refs whose scope does not cover the current turn.
                "scope": list(scope_for_preset(preset.key)),
            }
        )
    return entries


# Real harness preset catalog (36 presets), built once at import.
HARNESS_PRESETS: list[dict[str, Any]] = _build_harness_presets()


def _recipe_entries() -> list[dict[str, Any]]:
    return [
        {
            **r,
            "enabled": True,
            # Phase 3 — pack ids this UI recipe id maps to. Empty list = UI-only
            # label (toggling is a no-op). The frontend can use this to disable
            # the toggle or surface "no live effect" honesty.
            "packIds": list(pack_ids_for_recipe(r["id"])),
        }
        for r in RECIPES
    ]


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
            #
            # DEPRECATED (PR-F-UX5): kept as the union of evidenceMenu +
            # judgmentMenu for back-compat with pre-PR-F-UX5 consumers
            # (existing NL compiler tests, third-party authoring surfaces).
            # New UI code should read ``evidenceMenu`` / ``judgmentMenu`` so
            # the raw-evidence vs verdict-primitive distinction is visible.
            "customRuleMenu": what_menu(),
            # PR-F-UX5 — raw-evidence ref descriptors (``evidence:*``). The
            # wizard's "Check evidence record present" picker AND the
            # field-constraint type picker read from this list.
            "evidenceMenu": evidence_menu(),
            # PR-F-UX5 — verdict-primitive ref descriptors (``verifier:*`` and
            # bare named judgments). The wizard's "Check verifier / condition
            # passed" picker and the Conditions tab (merged with user-authored
            # named conditions) read from this list.
            "judgmentMenu": judgment_menu(),
        },
        "tools": _tool_entries(runtime),
        # In-context control-plane behavior toggles (facts-survey replan, goal
        # nudge, etc.). Orthogonal to the verification gate layer above; each
        # maps to a single ``MAGI_*_ENABLED`` flag that the lab/dogfood profiles
        # seed ON, so without this surface the dashboard could not turn them off.
        "controlPlane": _control_plane_entries(),
        # User-disableable first-party (builtin) policies (verify-before-replying).
        # Each maps a builtin policy id to its master ``MAGI_*_ENABLED`` flag; a
        # toggle here projects an opt-out. Floors (source_citation) are absent by
        # design so they cannot be disabled through this surface.
        "builtinPolicies": _builtin_policy_entries(),
    }


def _control_plane_entries() -> list[dict[str, str]]:
    from magi_agent.customize.control_plane_overrides import (  # noqa: PLC0415
        control_plane_behavior_catalog,
    )

    return control_plane_behavior_catalog()


def _builtin_policy_entries() -> list[dict[str, object]]:
    from magi_agent.customize.builtin_policy_overrides import (  # noqa: PLC0415
        builtin_policy_toggle_catalog,
    )

    return builtin_policy_toggle_catalog()
