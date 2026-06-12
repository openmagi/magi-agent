"""Learning-usage first-party recipe pack — PR5 static injection.

Architecture:
    AgentRecipeCompiler aggregates each selected pack's ``instructionRefs`` into
    the compiled ``RecipeSnapshot.instruction_refs`` tuple.  PR5 adds a single
    NEW instruction ref — ``instruction:learning:usage`` — carried by this
    default-OFF pack.  The recipe COMPILER is the only surface that turns the
    ref into prompt-bound metadata; there are NO core / message_builder
    edits and NO raw hooks.

Default-OFF:
    The pack is NOT ``defaultEnabled`` and NOT ``hardSafety``; it is selected
    only when a task profile asks for ``learning`` (or ``learning-usage`` /
    ``self-improvement``) via ``taskProfileSelectors``, or when explicitly
    enabled via ``packs.enable``.  When no task profile selects it the compiled
    snapshot is byte-identical to pre-PR5 — proven by the OFF regression test.

This pack carries metadata refs ONLY (no live tool/callback/runner refs), in
keeping with ``RecipePackManifest`` invariants.
"""
from __future__ import annotations

from magi_agent.recipes.compiler import RecipePackManifest


#: The single NEW instruction ref introduced by PR5.
LEARNING_USAGE_INSTRUCTION_REF: str = "instruction:learning:usage"

#: The pack id (dotted, safe recipe id).
LEARNING_USAGE_PACK_ID: str = "openmagi.learning-usage"

#: Common ADK primitive-ownership tuple shared by every first-party pack (see
#: ``_first_party_packs`` in ``recipes/compiler.py``).  Duplicated verbatim here
#: so the learning-usage pack carries the same catalog-consistency metadata as
#: its siblings (metadata-only — no live refs).
_COMMON_ADK_OWNERS: tuple[str, ...] = (
    "ADK Agent owns execution shape",
    "ADK Runner owns invocation",
    "ADK Event owns event stream",
    "ADK FunctionTool owns tool call surface",
    "ADK LongRunningFunctionTool owns long tool/job calls only",
    "ADK SessionService owns session state",
    "ADK MemoryService owns memory state",
    "ADK ArtifactService owns artifact state",
    "ADK callbacks/plugins own lifecycle attachment",
    "ADK evals own evaluator execution",
)

#: Common OpenMagi boundary-ownership tuple shared by every first-party pack.
_COMMON_OPENMAGI_OWNERS: tuple[str, ...] = (
    "OpenMagi ProfileResolver owns deterministic metadata merge",
    "OpenMagi AgentRecipeCompiler owns immutable recipe metadata snapshots",
    "OpenMagi PackRegistry owns first-party pack metadata catalog",
    "OpenMagi ApprovalGate metadata owns product approval compatibility",
    "OpenMagi Evidence/Audit refs own diagnostic compatibility metadata",
    "OpenMagi redaction metadata owns public safety compatibility",
)

#: Concise, instructional, model-agnostic guidance text associated with the
#: ``instruction:learning:usage`` ref.  Kept here (next to the pack that owns
#: the ref) so a future prompt-text resolver / dashboard can surface it without
#: reaching into core.  No provider/model names — fully model-agnostic.
LEARNING_USAGE_INSTRUCTION_TEXT: str = (
    "Follow any applicable learned rules first. Treat retrieved learned "
    "examples as templates to emulate. When you discover a durable lesson, "
    "propose it via the learning mechanism — do not self-mutate."
)


def build_learning_usage_pack() -> RecipePackManifest:
    """Build the default-OFF learning-usage recipe pack manifest."""
    return RecipePackManifest(
        packId=LEARNING_USAGE_PACK_ID,
        displayName="Learning Usage",
        description=(
            "Default-off first-party recipe metadata that instructs the agent "
            "to apply active learned rules/examples and to propose new lessons "
            "via the learning mechanism rather than self-mutating."
        ),
        taskProfileSelectors=(
            "learning",
            "learning-usage",
            "self-improvement",
        ),
        instructionRefs=(LEARNING_USAGE_INSTRUCTION_REF,),
        auditRefs=("audit:learning-usage:recipe-boundary",),
        adkPrimitiveOwnership=_COMMON_ADK_OWNERS,
        openmagiBoundaryOwnership=_COMMON_OPENMAGI_OWNERS,
    )


__all__ = [
    "LEARNING_USAGE_INSTRUCTION_REF",
    "LEARNING_USAGE_INSTRUCTION_TEXT",
    "LEARNING_USAGE_PACK_ID",
    "build_learning_usage_pack",
]
