"""Cross-family description-based recipe routing — generalizes the GA-only
progressive-disclosure listing (harness/general_automation/recipe_disclosure.py)
to ALL non-hard_safety packs. A pure listing builder, the on-demand
``select_recipe`` tool handler, and the gated runtime registration/dispatch seam
(:func:`register_select_recipe_tool`) that attaches the tool to a CLI/serve tool
registry. The resolver-drain wiring (reading the accumulated selections back into
a ``ProfileResolutionRequest``) lands in a later task."""
from __future__ import annotations

from collections.abc import Iterable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
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


@dataclass(frozen=True)
class RecipeToolScope:
    """The recipe-exclusive tool model the per-call enforcement (HB-3) consumes.

    "Recipe-exclusive scoping": a tool listed in any non-``hard_safety`` pack's
    ``granted_tool_names`` becomes *scoped* — it is allowed only when at least
    one of the packs that grant it has been selected. Every other tool is
    *base-free* (always allowed). ``hard_safety`` packs never scope tools.

    Pure data + a pure predicate; no I/O, no flag gating (gating is HB-3's job).

    * ``scoped_tools`` — the union of ``granted_tool_names`` across all
      non-``hard_safety`` packs.
    * ``owning_packs`` — ``scoped_tool -> tuple(pack_ids that grant it)``,
      order-preserving over registry iteration.
    * ``_granted_by_pack`` — internal ``pack_id -> frozenset(granted tools)``
      used by :meth:`is_allowed` to compute the union of the selected packs.
    """

    owning_packs: Mapping[str, tuple[str, ...]]
    scoped_tools: frozenset[str]
    _granted_by_pack: Mapping[str, frozenset[str]]

    def is_allowed(self, tool_name: str, *, selected_pack_ids: Iterable[str]) -> bool:
        """Whether ``tool_name`` is permitted given the currently selected packs.

        * no selection (restriction inactive before any selection) → ``True``
        * ``tool_name`` not scoped (base-free) → ``True``
        * otherwise → ``True`` iff ``tool_name`` is in the union of
          ``granted_tool_names`` of the selected packs.
        """
        selected = tuple(selected_pack_ids)
        if not selected:
            return True
        if tool_name not in self.scoped_tools:
            return True
        for pack_id in selected:
            if tool_name in self._granted_by_pack.get(pack_id, frozenset()):
                return True
        return False


def build_recipe_tool_scope(registry: PackRegistry) -> RecipeToolScope:
    """Compute the recipe-scoped tool model from the pack registry.

    Iterates ``registry.values()`` in order, skipping ``hard_safety`` packs
    (which never scope tools), and accumulates the scoped-tool set, the
    order-preserving owning-pack map, and the per-pack granted-tool map. Pure:
    no I/O, no flag gating.
    """
    owning_packs: dict[str, list[str]] = {}
    granted_by_pack: dict[str, frozenset[str]] = {}
    for pack in registry.values():
        if pack.hard_safety:
            continue
        granted_by_pack[pack.pack_id] = frozenset(pack.granted_tool_names)
        for tool_name in pack.granted_tool_names:
            owners = owning_packs.setdefault(tool_name, [])
            if pack.pack_id not in owners:
                owners.append(pack.pack_id)
    return RecipeToolScope(
        owning_packs={tool: tuple(packs) for tool, packs in owning_packs.items()},
        scoped_tools=frozenset(owning_packs),
        _granted_by_pack=granted_by_pack,
    )


_DEV_CODING_PACK_ID = "openmagi.dev-coding"
_DEV_CODING_EVIDENCE_VALIDATOR = "verifier:dev-coding:test-evidence"


@dataclass(frozen=True)
class RecipeObligationScope:
    """Maps each routable pack to the completion-gate obligations it imposes.

    Completion gates read the model's live ``select_recipe`` choices from the
    session state and enforce the selected packs' obligations on the turn
    output: validators (raters) + evidence (required answer structure).

    "Obligation" = a (validator_ref, evidence_ref) pair the completion gate
    asserts must be satisfied when a pack is selected. Each pack contributes
    its authored ``validator_refs`` + ``evidence_refs`` (except ``hard_safety``
    packs, which stay in the profile baseline, never routed). The dev-coding
    pack has a special case: ``verifier:dev-coding:test-evidence`` is appended
    outside the pack's static validator_refs to mirror real_runner.py behavior.

    Pure data + a pure predicate; no I/O, no flag gating.
    """

    validators_by_pack: Mapping[str, frozenset[str]]
    evidence_by_pack: Mapping[str, frozenset[str]]

    def obligations_for(
        self, selected_pack_ids: Sequence[str]
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Return the union of (validators, evidence) from selected packs.

        Args:
            selected_pack_ids: sequence of pack IDs (those in the registry).
                Unknown packs are silently ignored.

        Returns:
            A tuple ``(validators, evidence)`` where each is a sorted,
            deduplicated tuple of refs. Empty selection yields ``((), ())``.
        """
        validators: set[str] = set()
        evidence: set[str] = set()
        for pid in selected_pack_ids:
            validators |= self.validators_by_pack.get(pid, frozenset())
            evidence |= self.evidence_by_pack.get(pid, frozenset())
            if pid == _DEV_CODING_PACK_ID:
                # Mirror real_runner.py: dev-coding's test-evidence validator is
                # appended outside the pack's static validator_refs.
                validators.add(_DEV_CODING_EVIDENCE_VALIDATOR)
        return tuple(sorted(validators)), tuple(sorted(evidence))


def build_recipe_obligation_scope(registry: PackRegistry) -> RecipeObligationScope:
    """Compute the completion-gate obligations from the pack registry.

    Iterates ``registry.values()`` in order, skipping ``hard_safety`` packs
    (which never impose routing obligations, staying in the profile baseline),
    and accumulates the per-pack validator and evidence ref mappings. Pure:
    no I/O, no flag gating.
    """
    validators_by_pack: dict[str, frozenset[str]] = {}
    evidence_by_pack: dict[str, frozenset[str]] = {}
    for pack in registry.values():
        if pack.hard_safety:
            continue  # always-on floor stays in the profile baseline, never routed
        validators_by_pack[pack.pack_id] = frozenset(pack.validator_refs)
        evidence_by_pack[pack.pack_id] = frozenset(pack.evidence_refs)
    return RecipeObligationScope(
        validators_by_pack=validators_by_pack,
        evidence_by_pack=evidence_by_pack,
    )


def normalize_pinned_recipe_pack_ids(
    pack_ids: "Sequence[str]", registry: "PackRegistry"
) -> tuple[str, ...]:
    """Keep only known, routable (non-hard_safety) pack ids; dedupe, preserve order.

    Fail-open: unknown / non-str / hard_safety entries are dropped silently so a
    bad pin degrades to "no pin" rather than an error.

    Args:
        pack_ids: sequence of pack IDs to validate and normalize.
        registry: the pack registry to check membership and hard_safety flag.

    Returns:
        A tuple of valid, routable pack IDs in input order, de-duped.
    """
    out: list[str] = []
    for pid in pack_ids:
        if not isinstance(pid, str) or not pid.strip():
            continue
        pid = pid.strip()
        if pid in out:
            continue
        try:
            pack = registry.get(pid)
        except KeyError:
            continue
        if getattr(pack, "hard_safety", False):
            continue
        out.append(pid)
    return tuple(out)


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
        _emit_recipe_route_decided(
            context, pack_id=None, status="error", selected_count=_selected_count(context)
        )
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
        _emit_recipe_route_decided(
            context, pack_id=pack_id, status="error", selected_count=_selected_count(context)
        )
        return ToolResult(
            status="error",
            errorCode="recipe_select_unknown",
            errorMessage="unknown recipe pack id",
            metadata={**base_metadata, "reason": "recipe_select_unknown", "packId": pack_id},
        )

    if pack.hard_safety:
        # Hard-safety packs are always-on and never routed; selecting one is a
        # no-op so the always-on invariant cannot be subverted via the tool.
        _emit_recipe_route_decided(
            context, pack_id=pack_id, status="blocked", selected_count=_selected_count(context)
        )
        return ToolResult(
            status="blocked",
            metadata={**base_metadata, "reason": "recipe_select_hard_safety", "packId": pack_id},
        )

    _accumulate_selected_pack_id(context, pack_id)

    _emit_recipe_route_decided(
        context, pack_id=pack_id, status="ok", selected_count=_selected_count(context)
    )

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

    from magi_agent.recipes.kernel_recipe_packs import build_runtime_pack_registry

    resolved_registry = pack_registry or build_runtime_pack_registry()

    def _handler(arguments: dict[str, object], context: ToolContext) -> ToolResult:
        return select_recipe_handler(
            arguments,
            context,
            registry=resolved_registry,
        )

    registry.register(select_recipe_manifest(), handler=_handler)
    registry.enable(SELECT_RECIPE_TOOL_NAME)
    return True


def project_recipe_route_decided_event(
    *,
    pack_id: str | None,
    status: str,
    selected_count: int,
) -> dict[str, object]:
    """Project a recipe-route decision into a public-safe ADVISORY event dict.

    Mirrors the established projection seam (``coding/repair_loop`` /
    ``evidence/event_projection``): a pure function returning a JSON-safe dict
    that a caller emits via a ``context.emit_*_event`` callback.  Carries only the
    chosen ``packId`` (an opaque pack identifier — never a path or secret), the
    handler ``status`` (``ok``/``error``/``blocked``), and the running count of
    selections accumulated so far.  Never gating — purely for debuggability.
    """
    return {
        "type": "recipe_route_decided",
        "packId": pack_id,
        "status": status,
        "selectedCount": selected_count,
    }


def _emit_recipe_route_decided(
    context: ToolContext,
    *,
    pack_id: str | None,
    status: str,
    selected_count: int,
) -> None:
    """Best-effort emit of the advisory ``recipe_route_decided`` event.

    Fail-safe: when no ``emit_control_event`` callback is threaded onto the
    ToolContext (e.g. non-ADK runners or test harnesses) this is a silent no-op,
    and any emitter exception is swallowed so event emission can never raise or
    alter the selection result.  The handler itself only runs when recipe routing
    is enabled, so this is inert when the flag is OFF.
    """
    emitter = context.emit_control_event
    if not callable(emitter):
        return
    try:
        emitter(
            project_recipe_route_decided_event(
                pack_id=pack_id, status=status, selected_count=selected_count
            )
        )
    except Exception:
        # Advisory only — never let telemetry break routing.
        return


def _selected_count(context: ToolContext) -> int:
    """Count accumulated selections (running total) for the event payload."""
    state = _adk_state(context)
    if state is None:
        return 0
    existing = state.get(SELECTED_RECIPE_PACK_IDS_STATE_KEY)
    if isinstance(existing, (tuple, list)):
        return len(existing)
    return 0


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
    "RecipeObligationScope",
    "RecipeToolScope",
    "build_recipe_listing_section",
    "build_recipe_obligation_scope",
    "build_recipe_tool_scope",
    "normalize_pinned_recipe_pack_ids",
    "project_recipe_route_decided_event",
    "register_select_recipe_tool",
    "select_recipe_body",
    "select_recipe_handler",
    "select_recipe_manifest",
]
