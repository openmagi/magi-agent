"""Discovery first-party recipe pack — metadata-only, default-OFF.

Mirrors ``recipes/first_party/learning_usage.py`` exactly: a metadata-only pack
carrying a single NEW instruction ref, selected only when a task profile asks for
``discovery``. It carries metadata refs ONLY (no live tool/callback/runner refs),
in keeping with ``RecipePackManifest`` invariants — the iterative-discovery
*execution* lives in :mod:`magi_agent.discovery` and is invoked separately
behind its own default-OFF env gate.

Registered in ``recipes/compiler.py:_first_party_packs`` via
``_build_discovery_pack()``, mirroring the ``learning_usage`` sibling, so it is
selected whenever a task profile asks for ``discovery``.
"""
from __future__ import annotations

from magi_agent.recipes.compiler import RecipePackManifest

#: The single NEW instruction ref introduced by the discovery pack.
DISCOVERY_INSTRUCTION_REF: str = "instruction:discovery:iterative"

#: The pack id (dotted, safe recipe id).
DISCOVERY_PACK_ID: str = "openmagi.discovery"

#: Common ADK primitive-ownership tuple shared by every first-party pack (see
#: ``_first_party_packs`` in ``recipes/compiler.py``). Duplicated verbatim here
#: so the discovery pack carries the same catalog-consistency metadata as its
#: siblings (metadata-only — no live refs).
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
#: ``instruction:discovery:iterative`` ref. Kept here (next to the pack that
#: owns the ref) so a future prompt-text resolver / dashboard can surface it
#: without reaching into core. No provider/model names — fully model-agnostic.
DISCOVERY_INSTRUCTION_TEXT: str = (
    "When asked to proactively surface problems across a corpus, work in "
    "cumulative rounds: each round, find only NEW problems not already "
    "discovered, ground each in specific evidence, and label it with the "
    "matching problem template. Stop when a round surfaces nothing new."
)


def build_discovery_pack() -> RecipePackManifest:
    """Build the default-OFF discovery recipe pack manifest."""
    return RecipePackManifest(
        packId=DISCOVERY_PACK_ID,
        displayName="Discovery",
        description=(
            "Default-off first-party recipe metadata that instructs the agent "
            "to proactively surface multiple grounded problems across a corpus "
            "in cumulative rounds, labeled by problem template."
        ),
        whenToUse=(
            "When the user wants problems proactively surfaced across a "
            "repository or corpus rather than a single targeted answer."
        ),
        taskProfileSelectors=("discovery",),
        instructionRefs=(DISCOVERY_INSTRUCTION_REF,),
        auditRefs=("audit:discovery:recipe-boundary",),
        adkPrimitiveOwnership=_COMMON_ADK_OWNERS,
        openmagiBoundaryOwnership=_COMMON_OPENMAGI_OWNERS,
    )


__all__ = [
    "DISCOVERY_INSTRUCTION_REF",
    "DISCOVERY_INSTRUCTION_TEXT",
    "DISCOVERY_PACK_ID",
    "build_discovery_pack",
]
