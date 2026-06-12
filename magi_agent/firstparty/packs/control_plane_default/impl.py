"""First-party default control-plane provider (no privilege, typed-ctx only).

Receives ONLY the narrow ``ControlPlaneProvideContext`` (D5) and registers each
first-party ``LoopControl`` it builds. It delegates to ``build_default_plane`` —
the exact legacy env-gated assembly used by both runners — so this migration is a
MOVE, not a rewrite: the controls, their env gates, their order, and their
collaborators are byte-identical to the pre-Phase-6 hand-assembly (the Phase-0
golden regression stays green).

The de-privileging: these controls are now discovered+loaded from ``pack.toml``
through the same loader a user ``~/.magi/packs`` control_plane pack uses. A user
control_plane provider would receive this same context and register its own
controls in parallel — no first-party-only handle, no hardcoded ``plane.register``
inside ``build_default_plugin``.
"""
from __future__ import annotations

from magi_agent.packs.context import ControlPlaneProvideContext


def provide_default_controls(context: ControlPlaneProvideContext) -> None:
    # Local import to avoid a context<->control_plane import cycle at module load.
    from magi_agent.adk_bridge.control_plane import build_default_plane

    plane = build_default_plane(
        os_environ=dict(context.env),
        general_automation_receipts=context.general_automation_receipts,
        contract_required=context.contract_required,
        agent_role=context.agent_role,
        self_review_fork_runner=context.self_review_fork_runner,
        self_review_candidate_sink=context.self_review_candidate_sink,
        self_review_config=context.self_review_config,
        self_review_now=context.self_review_now,
        self_review_scheduler=context.self_review_scheduler,
        tool_synthesis_model_label=context.tool_synthesis_model_label,
    )
    for control in plane._controls:
        context.register(control)
