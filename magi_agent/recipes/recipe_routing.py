"""Cross-family description-based recipe routing — generalizes the GA-only
progressive-disclosure listing (harness/general_automation/recipe_disclosure.py)
to ALL non-hard_safety packs. A pure listing builder, the on-demand
``select_recipe`` tool handler, and the gated runtime registration/dispatch seam
(:func:`register_select_recipe_tool`) that attaches the tool to a CLI/serve tool
registry. The resolver-drain wiring (reading the accumulated selections back into
a ``ProfileResolutionRequest``) lands in a later task."""
from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from typing import TYPE_CHECKING

from magi_agent.recipes.compiler import PackRegistry, RecipePackManifest
# Import the constant from the import-boundary-safe constants module
# (magi_agent.context, an import-light package) so context/protected_tools.py can
# reference it WITHOUT importing this module — whose package __init__ eagerly
# loads magi_agent.recipes.compiler and pulls in magi_agent.tools.*. Re-exported
# here for back-compat: any code that imports
# ``from magi_agent.recipes.recipe_routing import SELECT_RECIPE_TOOL_NAME``
# continues to work unchanged (mirrors recipe_disclosure re-exporting
# LOAD_GA_RECIPE_TOOL_NAME from harness/general_automation/constants.py).
from magi_agent.context.recipe_routing_constants import SELECT_RECIPE_TOOL_NAME
from magi_agent.tools.context import ToolContext
from magi_agent.tools.result import ToolResult

if TYPE_CHECKING:
    from magi_agent.tools.manifest import ToolManifest
    from magi_agent.tools.registry import ToolRegistry

# Key under which selected pack ids accumulate on the ADK ToolContext ``state``
# mapping. ``select_recipe`` is called once per recipe, so selections must
# survive across calls within a turn/session; the ADK ``state`` dict is the
# canonical cross-call accumulator (the runner threads it onto
# ``ToolContext.adk_tool_context``). A later task drains this into the
# resolver's ``selected_pack_ids``.
SELECTED_RECIPE_PACK_IDS_STATE_KEY = "selected_recipe_pack_ids"


def build_recipe_listing_section(registry: PackRegistry) -> str:
    lines = [
        "## Available recipes (load on demand)",
        (
            "The following recipes are available. Each line is name + when to use. "
            f"Call `{SELECT_RECIPE_TOOL_NAME}` with a `pack_id` to load and select a "
            "recipe before acting. You may select MULTIPLE (call it once per recipe) "
            "or NONE if none apply."
        ),
    ]
    for pack in registry.values():
        if pack.hard_safety or not pack.when_to_use.strip():
            continue
        lines.append(
            f"- **{pack.display_name}** (`{pack.pack_id}`) — When to use: {pack.when_to_use}"
        )
    return "\n".join(lines)


def select_recipe_body(pack: RecipePackManifest) -> str:
    """Render the on-demand body for a selected recipe pack.

    Built purely from the pack manifest's existing fields (mirrors
    ``recipe_disclosure.load_ga_recipe`` / ``_render_full_body``): display name +
    id, when-to-use, description, and the instruction refs that constitute the
    pack's body so the model can follow it.
    """
    lines = [
        f"# Recipe: {pack.display_name} (`{pack.pack_id}`)",
        f"When to use: {pack.when_to_use}",
        "",
        pack.description,
    ]
    if pack.instruction_refs:
        lines.append("")
        lines.append("Instructions:")
        lines.extend(f"- {ref}" for ref in pack.instruction_refs)
    return "\n".join(lines)


def select_recipe_handler(
    arguments: Mapping[str, object],
    context: ToolContext,
    *,
    registry: PackRegistry,
) -> ToolResult:
    """Tool handler: load + select a recipe pack by ``pack_id`` (multi-call).

    Fail-safe — never raises on bad input. Mirrors
    ``recipe_disclosure.load_ga_recipe_handler`` semantics:

    * Valid routable pack (exists, ``hard_safety=False``, non-empty
      ``when_to_use``) → ``ok`` carrying the pack body; the result is marked
      ``compactionProtected`` and carries ``toolName == SELECT_RECIPE_TOOL_NAME``
      so the Tier-4/Tier-5 compaction engines preserve the loaded body. The
      pack id is accumulated (de-duped, order-preserving) onto the ADK
      ToolContext ``state`` under :data:`SELECTED_RECIPE_PACK_IDS_STATE_KEY`.
    * Unknown / malformed ``pack_id`` → ``error`` (no exception).
    * A ``hard_safety`` pack → ``blocked`` no-op (hard packs are always-on,
      never routed) — nothing accumulated.
    """
    base_metadata: dict[str, object] = {
        "toolName": SELECT_RECIPE_TOOL_NAME,
        "permissionClass": "meta",
        "dangerous": False,
        "mutatesWorkspace": False,
        "recipeSelect": True,
    }

    pack_id = arguments.get("pack_id")
    if not isinstance(pack_id, str) or not pack_id.strip():
        return ToolResult(
            status="error",
            errorCode="recipe_select_invalid",
            errorMessage="pack_id must be a non-empty string id",
            metadata={**base_metadata, "reason": "recipe_select_invalid"},
        )
    pack_id = pack_id.strip()

    try:
        pack = registry.get(pack_id)
    except KeyError:
        return ToolResult(
            status="error",
            errorCode="recipe_select_unknown",
            errorMessage="unknown recipe pack id",
            metadata={**base_metadata, "reason": "recipe_select_unknown", "packId": pack_id},
        )

    if pack.hard_safety:
        # Hard-safety packs are always-on and never routed; selecting one is a
        # no-op so the always-on invariant cannot be subverted via the tool.
        return ToolResult(
            status="blocked",
            metadata={**base_metadata, "reason": "recipe_select_hard_safety", "packId": pack_id},
        )

    _accumulate_selected_pack_id(context, pack_id)

    return ToolResult(
        status="ok",
        output=select_recipe_body(pack),
        metadata={
            **base_metadata,
            "reason": "recipe_selected",
            "packId": pack_id,
            # Recognized by microcompact/auto_compact protection (mirrors
            # OpenCode PRUNE_PROTECTED_TOOLS / recipe_disclosure load tool).
            "compactionProtected": True,
        },
    )


def select_recipe_manifest() -> "ToolManifest":
    """Manifest for the on-demand cross-family recipe-select tool.

    ``meta`` permission (no mutation, not dangerous), available in both modes,
    disabled by default at the manifest level — the live flag gate
    (:func:`magi_agent.config.env.recipe_routing_llm_enabled`) is the authority
    for activation. Mirrors ``recipe_disclosure.load_ga_recipe_manifest``.
    """
    # Deferred import: ToolManifest pulls magi_agent.tools.manifest →
    # magi_agent.transport.  Keeping it local keeps the module's cold-import
    # surface small (this module's package __init__ already loads the recipe
    # stack; the manifest type is only needed when the tool is actually wired).
    from magi_agent.tools.manifest import ToolManifest, ToolSource  # noqa: PLC0415

    return ToolManifest(
        name=SELECT_RECIPE_TOOL_NAME,
        description=(
            "Select a recipe pack by id so you can follow it. Pass the `pack_id` "
            "shown in the recipe listing. Call this once per recipe to select "
            "MULTIPLE recipes, or not at all if none apply. The loaded body is "
            "preserved across context compaction."
        ),
        kind="native",
        source=ToolSource(
            kind="builtin",
            package="magi_agent.recipes",
        ),
        permission="meta",
        inputSchema=_select_recipe_input_schema(),
        availableInModes=("plan", "act"),
        tags=("recipe", "routing", "playbook", "meta"),
        parallel_safety="readonly",
        timeoutMs=30_000,
        enabled_by_default=False,
    )


def register_select_recipe_tool(
    registry: "ToolRegistry",
    *,
    pack_registry: PackRegistry | None = None,
    env: Mapping[str, str] | None = None,
) -> bool:
    """Gated registration + dispatch wiring for the ``select_recipe`` tool.

    Mirrors the GA load-tool mechanism but attaches it at the runtime
    tool-registry seam (CLI/serve ``build_*_tool_runtime``):

    * When ``MAGI_RECIPE_ROUTING_LLM_ENABLED`` is OFF (default) this is a no-op —
      the tool is NOT registered, so it is neither advertised to the model nor
      dispatchable, and the registry stays byte-identical to ``main``. Returns
      ``False``.
    * When ON it registers :func:`select_recipe_manifest` together with a handler
      that routes the model's ``select_recipe`` calls to
      :func:`select_recipe_handler`, binding ``pack_registry`` (defaulting to
      :meth:`PackRegistry.with_first_party_packs`) via closure, and enables the
      registration so the tool is advertised. Returns ``True``.

    Idempotent: a second call with the tool already registered is a no-op
    (returns ``False``) rather than raising, so repeated runtime assembly is
    safe.
    """
    from magi_agent.config.env import recipe_routing_llm_enabled  # noqa: PLC0415

    if not recipe_routing_llm_enabled(env):
        return False
    if registry.resolve_registration(SELECT_RECIPE_TOOL_NAME) is not None:
        return False

    resolved_registry = pack_registry or PackRegistry.with_first_party_packs()

    def _handler(arguments: dict[str, object], context: ToolContext) -> ToolResult:
        return select_recipe_handler(
            arguments,
            context,
            registry=resolved_registry,
        )

    registry.register(select_recipe_manifest(), handler=_handler)
    registry.enable(SELECT_RECIPE_TOOL_NAME)
    return True


def _select_recipe_input_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "pack_id": {"type": "string", "maxLength": 120},
        },
        "required": ["pack_id"],
        "additionalProperties": False,
    }


def _accumulate_selected_pack_id(context: ToolContext, pack_id: str) -> None:
    """Append ``pack_id`` to the ADK state accumulator, de-duped + ordered.

    Fail-safe: if no ADK tool context / mutable ``state`` is threaded through
    (e.g. flag-OFF callers or non-ADK runners), this is a silent no-op so a
    selection still returns its body. Accumulation is the seam a later task
    drains into the resolver's ``selected_pack_ids``.
    """
    state = _adk_state(context)
    if state is None:
        return
    existing = state.get(SELECTED_RECIPE_PACK_IDS_STATE_KEY)
    selected: list[str] = []
    if isinstance(existing, (tuple, list)):
        selected = [str(item) for item in existing]
    if pack_id not in selected:
        selected.append(pack_id)
    try:
        state[SELECTED_RECIPE_PACK_IDS_STATE_KEY] = tuple(selected)
    except Exception:
        # A read-only / non-mapping state must not break the selection.
        return


def _adk_state(context: ToolContext) -> MutableMapping[str, object] | None:
    adk = context.adk_tool_context
    if adk is None:
        return None
    state = getattr(adk, "state", None)
    if state is None:
        return None
    # ADK exposes a mutable mapping-like ``state``; accept anything that
    # supports get + item assignment, fall back to None otherwise.
    if not hasattr(state, "get") or not hasattr(state, "__setitem__"):
        return None
    return state  # type: ignore[return-value]


__all__ = [
    "SELECTED_RECIPE_PACK_IDS_STATE_KEY",
    "SELECT_RECIPE_TOOL_NAME",
    "build_recipe_listing_section",
    "register_select_recipe_tool",
    "select_recipe_body",
    "select_recipe_handler",
    "select_recipe_manifest",
]
