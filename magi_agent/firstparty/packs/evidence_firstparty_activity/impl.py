"""First-party activity evidence producers (no privilege, typed-ctx only).

Each provider receives ONLY the narrow ``EvidenceProducerProvideContext`` (D5)
and registers one ``ProducerSpec``. ``public_ref`` carries the ``evidence:``
prefix so the refs reach the live ``harness/verifier_bus`` required-evidence
vocabulary; ``producer_surfaces=("tool_dispatch",)`` names the kernel seam that
emits the records (``ToolDispatcher.dispatch``)."""

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
