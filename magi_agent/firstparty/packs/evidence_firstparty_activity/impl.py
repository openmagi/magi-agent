"""First-party activity evidence producers (no privilege, typed-ctx only).

Each provider receives ONLY the narrow ``EvidenceProducerProvideContext`` (D5)
and registers one ``ProducerSpec``. ``public_ref`` carries the ``evidence:``
prefix so the refs reach the live ``harness/verifier_bus`` required-evidence
vocabulary; ``producer_surfaces=("tool_dispatch",)`` names the kernel seam that
emits the records (``ToolDispatcher.dispatch``).

``ref`` and ``public_ref`` are intentionally identical for all three producers
here: the first-party gate reads ``entry.ref`` from the manifest while emitted
activity records carry ``public_ref``; keeping them equal ensures gate resolution
and the verifier-bus ``evidence:`` prefix check align without a translation step.
The ``evidence_gitdiff`` pack used to split ``ref="evidence:gitdiff@1"`` vs
``public_ref="evidence:gitDiff@1"`` — F-4 unified both to ``evidence:git-diff@1``
so the producer's contributed ref matches every consumer."""

from __future__ import annotations

from magi_agent.packs.context import EvidenceProducerProvideContext, ProducerSpec


def provide_tool_call_producer(context: EvidenceProducerProvideContext) -> None:
    context.register(
        "evidence:toolCall@1",
        ProducerSpec(
            evidence_type="ToolCall",
            public_ref="evidence:toolCall@1",
            producer_surfaces=("tool_dispatch",),
        ),
    )


def provide_skill_load_producer(context: EvidenceProducerProvideContext) -> None:
    context.register(
        "evidence:skillLoad@1",
        ProducerSpec(
            evidence_type="SkillLoad",
            public_ref="evidence:skillLoad@1",
            producer_surfaces=("tool_dispatch",),
        ),
    )


def provide_subagent_spawn_producer(context: EvidenceProducerProvideContext) -> None:
    context.register(
        "evidence:subagentSpawn@1",
        ProducerSpec(
            evidence_type="SubagentSpawn",
            public_ref="evidence:subagentSpawn@1",
            producer_surfaces=("tool_dispatch",),
        ),
    )
