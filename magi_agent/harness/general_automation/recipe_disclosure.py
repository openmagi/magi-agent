"""Track 19 PR8 — progressive-disclosure GA recipes + compaction-protected bodies.

Ports OpenCode's progressive-disclosure *skills* to the EXISTING
General-Automation pack. OpenCode injects only a skill's name + description
up front and loads the full body via a ``skill`` tool on demand; the loaded body
is then compaction-protected (``PRUNE_PROTECTED_TOOLS=["skill"]``) so the domain
instructions survive long tasks. This module is magi's equivalent, built
**entirely from the existing GA presets** — it does NOT add a new pack or a
parallel skill registry:

* :func:`build_ga_recipe_listing_section` renders a cheap up-front system-prompt
  section listing every existing
  :class:`~magi_agent.recipes.first_party.general_automation.presets.GeneralAutomationPreset`
  by **title + whenToUse only** (no full bodies). The whenToUse line is derived
  deterministically from the preset's *existing* fields (allowed permissions +
  tool categories) — not a hand-maintained parallel description list.
* :func:`load_ga_recipe` returns the FULL playbook body for a named preset,
  rendered from that same preset's existing fields (tool categories, allowed
  permissions, browser actions, approval-required categories, ADK role
  metadata). This is the on-demand body the ``load_recipe`` tool injects.
* :data:`LOAD_GA_RECIPE_TOOL_NAME` is the model-callable load tool name. Its
  tool-result is marked ``compactionProtected`` and — mirroring OpenCode's
  ``PRUNE_PROTECTED_TOOLS`` — is recognized by name in
  :mod:`magi_agent.context.microcompact` and :mod:`magi_agent.context.auto_compact`
  so the loaded body is never compacted away.

Activation requires BOTH (mirroring PR2/PR6/PR7):

* ``MAGI_GA_LIVE_ENABLED`` truthy (single-source flag, default OFF), and
* ``agent_role == "general"``.

When inactive — non-general role or flag-OFF — the listing is absent
(:func:`ga_recipe_listing_section` returns ``None``) and the load tool is inert
(:func:`load_ga_recipe_handler` returns a ``blocked`` no-op), so flag-OFF /
non-general behavior is byte-identical to ``main``.

The compaction protection in ``microcompact``/``auto_compact`` keys purely on the
load tool *name* and is therefore a harmless no-op for any non-GA tool result
(which never carries that name).

Wiring seam: like PR3/PR5/PR6/PR7, the production system-prompt assembler does
not yet splice :func:`ga_recipe_listing_section` into the ``general`` system
prompt (the prompt package's only injection point is cache-control, not a
content-section seam), and the runner does not yet route a tool call named
:data:`LOAD_GA_RECIPE_TOOL_NAME` through :func:`load_ga_recipe_handler`. The
listing builder, load tool, and manifest are declared + tested, ready to attach.
The compaction protection, by contrast, IS wired directly into the real Tier-4 /
Tier-5 engines.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from magi_agent.config.env import general_automation_live_enabled
from magi_agent.recipes.first_party.general_automation.presets import (
    GeneralAutomationPreset,
    general_automation_preset_catalog,
    get_general_automation_preset,
)
from magi_agent.tools.context import ToolContext
from magi_agent.tools.result import ToolResult

if TYPE_CHECKING:
    from magi_agent.tools.manifest import ToolManifest


#: Name of the on-demand recipe/playbook load tool (a.k.a. ``LoadGaPlaybook``).
#: Referenced by the resolved ``general`` pack ``tools`` tuple in
#: ``harness/resolved.py`` and recognized by the compaction-protection sets in
#: ``context/microcompact.py`` + ``context/auto_compact.py`` (mirroring
#: OpenCode's ``PRUNE_PROTECTED_TOOLS=["skill"]``).
LOAD_GA_RECIPE_TOOL_NAME = "LoadGaPlaybook"

_GA_ROLE = "general"


# ---------------------------------------------------------------------------
# (a) cheap up-front listing — title + whenToUse only
# ---------------------------------------------------------------------------

def build_ga_recipe_listing_section() -> str:
    """Render the cheap up-front listing of existing GA presets (no bodies).

    Pure function (no flag / role gate; that lives in
    :func:`ga_recipe_listing_section`). Lists every existing preset by
    ``title`` + a one-line ``When to use`` derived from the preset's existing
    fields. Advertises :data:`LOAD_GA_RECIPE_TOOL_NAME` so the model knows how
    to fetch a full body on demand. Deliberately omits the full body to keep the
    section cheap.
    """
    lines = [
        "## General-automation playbooks (load on demand)",
        (
            f"The following playbooks are available. Each line is name + when to "
            f"use only. Call the `{LOAD_GA_RECIPE_TOOL_NAME}` tool with a "
            f"`recipe` id to load that playbook's full body before acting on it."
        ),
    ]
    for preset in general_automation_preset_catalog():
        lines.append(
            f"- **{preset.title}** (`{preset.role_id}`) — When to use: "
            f"{_when_to_use(preset)}"
        )
    return "\n".join(lines)


def ga_recipe_listing_section(
    *,
    agent_role: str,
    env: Mapping[str, str] | None = None,
) -> str | None:
    """Flag-gated listing section, or ``None`` when inert.

    Returns ``None`` (no section / no contribution) when ``MAGI_GA_LIVE_ENABLED``
    is OFF or ``agent_role`` is not ``general`` — keeping flag-OFF / non-general
    behavior byte-identical to ``main``. Otherwise delegates to
    :func:`build_ga_recipe_listing_section`.
    """
    if not general_automation_live_enabled(env):
        return None
    if _normalize_role(agent_role) != _GA_ROLE:
        return None
    return build_ga_recipe_listing_section()


# ---------------------------------------------------------------------------
# (b) on-demand full body
# ---------------------------------------------------------------------------

def load_ga_recipe(recipe: str) -> str:
    """Return the FULL playbook body for a named existing GA preset.

    Raises :class:`KeyError` for an unknown recipe id. The body is rendered
    purely from the preset's existing fields — no parallel body store.
    """
    preset = get_general_automation_preset(recipe)
    return _render_full_body(preset)


def load_ga_recipe_handler(
    arguments: Mapping[str, object],
    context: ToolContext,
    *,
    env: Mapping[str, str] | None = None,
) -> ToolResult:
    """Tool handler: inject the full playbook body for a named recipe.

    Returns ``ok`` carrying the full body (as ``output``) when active; an
    ``error`` for an unknown / malformed recipe; and a ``blocked`` no-op when
    inert (flag-OFF / non-general) so flag-OFF behaves like ``main``. The
    successful result is marked ``compactionProtected`` and carries
    ``toolName == LOAD_GA_RECIPE_TOOL_NAME`` so the Tier-4 / Tier-5 compaction
    engines preserve the body.
    """
    base_metadata: dict[str, object] = {
        "toolName": LOAD_GA_RECIPE_TOOL_NAME,
        "permissionClass": "meta",
        "dangerous": False,
        "mutatesWorkspace": False,
        "generalAutomationRecipeLoad": True,
    }

    if not general_automation_live_enabled(env) or _agent_role(context) != _GA_ROLE:
        return ToolResult(
            status="blocked",
            metadata={**base_metadata, "reason": "general_automation_recipe_inert"},
        )

    recipe = arguments.get("recipe")
    if not isinstance(recipe, str) or not recipe.strip():
        return ToolResult(
            status="error",
            errorCode="general_automation_recipe_invalid",
            errorMessage="recipe must be a non-empty string id",
            metadata={**base_metadata, "reason": "general_automation_recipe_invalid"},
        )

    try:
        body = load_ga_recipe(recipe.strip())
    except KeyError:
        return ToolResult(
            status="error",
            errorCode="general_automation_recipe_unknown",
            errorMessage="unknown general-automation recipe id",
            metadata={**base_metadata, "reason": "general_automation_recipe_unknown"},
        )

    return ToolResult(
        status="ok",
        output=body,
        metadata={
            **base_metadata,
            "reason": "general_automation_recipe_loaded",
            "recipeId": recipe.strip(),
            # Recognized by microcompact/auto_compact protection (mirrors
            # OpenCode PRUNE_PROTECTED_TOOLS=["skill"]).
            "compactionProtected": True,
        },
    )


def load_ga_recipe_manifest() -> "ToolManifest":
    """Manifest for the on-demand recipe-load tool.

    ``meta`` permission (no mutation, not dangerous), available in both modes,
    disabled by default at the manifest level — the live flag gate
    (:func:`general_automation_live_enabled`) is the authority for activation.
    """
    # Deferred import: ToolManifest pulls magi_agent.tools.manifest →
    # magi_agent.transport.  Keeping it local lets resolved.py import the tool
    # NAME constant without paying the transport cost at module load.
    from magi_agent.tools.manifest import ToolManifest, ToolSource

    return ToolManifest(
        name=LOAD_GA_RECIPE_TOOL_NAME,
        description=(
            "Load the full body of a named general-automation playbook so you can "
            "follow it. Pass the recipe id shown in the playbook listing. The "
            "loaded body is preserved across context compaction."
        ),
        kind="native",
        source=ToolSource(
            kind="builtin",
            package="magi_agent.harness.general_automation",
        ),
        permission="meta",
        inputSchema=_load_recipe_input_schema(),
        availableInModes=("plan", "act"),
        tags=("general-automation", "recipe", "playbook", "meta"),
        parallel_safety="readonly",
        timeoutMs=30_000,
        enabled_by_default=False,
    )


# ---------------------------------------------------------------------------
# Rendering helpers (built only from existing preset fields)
# ---------------------------------------------------------------------------

_PERMISSION_PHRASES: dict[str, str] = {
    "read": "read",
    "write": "write",
    "execute": "run commands",
    "net": "access the network",
    "meta": "plan",
}


def _when_to_use(preset: GeneralAutomationPreset) -> str:
    """One-line whenToUse derived from the preset's existing fields."""
    perms = ", ".join(
        _PERMISSION_PHRASES.get(perm, perm) for perm in preset.allowed_permissions
    )
    first_categories = ", ".join(preset.tool_categories[:3])
    return f"tasks that need to {perms}; covers {first_categories}"


def _render_full_body(preset: GeneralAutomationPreset) -> str:
    """Render the full playbook body from the preset's existing fields."""
    lines = [
        f"# Playbook: {preset.title} (`{preset.role_id}`)",
        f"When to use: {_when_to_use(preset)}",
        "",
        "Tool categories: " + ", ".join(preset.tool_categories),
        "Allowed permissions: " + ", ".join(preset.allowed_permissions),
    ]
    if preset.enabled_browser_actions:
        lines.append(
            "Pre-approved browser actions: "
            + ", ".join(preset.enabled_browser_actions)
        )
    if preset.approval_required_actions:
        lines.append(
            "Browser actions requiring approval: "
            + ", ".join(preset.approval_required_actions)
        )
    if preset.approval_required_categories:
        lines.append(
            "Categories requiring approval: "
            + ", ".join(preset.approval_required_categories)
        )
    adk_primitive = preset.adk_agent_role_metadata.get("adkPrimitive")
    if isinstance(adk_primitive, str):
        lines.append(f"ADK primitive: {adk_primitive}")
    return "\n".join(lines)


def _load_recipe_input_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "recipe": {"type": "string", "maxLength": 120},
        },
        "required": ["recipe"],
        "additionalProperties": False,
    }


def _agent_role(context: ToolContext) -> str:
    contract = context.execution_contract
    if isinstance(contract, Mapping):
        for key in ("agentRole", "agent_role"):
            value = contract.get(key)
            if isinstance(value, str):
                return _normalize_role(value)
    return ""


def _normalize_role(agent_role: str) -> str:
    return agent_role.strip().casefold().replace("-", "_")


__all__ = [
    "LOAD_GA_RECIPE_TOOL_NAME",
    "build_ga_recipe_listing_section",
    "ga_recipe_listing_section",
    "load_ga_recipe",
    "load_ga_recipe_handler",
    "load_ga_recipe_manifest",
]
