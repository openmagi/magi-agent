"""First-party default control-plane providers (no privilege, typed-ctx only).

Each provider receives ONLY the narrow ``ControlPlaneProvideContext`` (D5) and
registers the ``LoopControl``s its manifest entry declares. Every provider
delegates to a single-source builder in ``adk_bridge/control_plane.py`` — the
exact legacy env-gated assembly used by both runners — so this migration is a
MOVE, not a rewrite: the controls, their env gates (default-OFF semantics
preserved exactly), their order, and their collaborators are byte-identical to
the pre-Phase-6 hand-assembly (the Phase-0 golden regression stays green).

The de-privileging: these controls are discovered+loaded from ``pack.toml``
through the same loader a user ``~/.magi/packs`` control_plane pack uses. The
main-side features (loop-resilience 6b7cd40e, facts-replan #510, F-LIFE2
per-LLM-call audit, tool-synthesis nudge #512) are SEPARATE ``provides``
entries — ordered by the manifest ``priority`` field (the nudge entry is
highest = registered LAST so edit-retry / resilience overrides win the
after-tool fan-out) — so a user pack can override or forbid each one
individually with no first-party-only handle and no hardcoded
``plane.register`` inside ``build_default_plugin``.
"""
from __future__ import annotations

from magi_agent.packs.context import ControlPlaneProvideContext


def provide_default_controls(context: ControlPlaneProvideContext) -> None:
    """Core entry (``control_plane:default@1``): the 6 long-standing controls
    (edit-retry, resilience, compaction, max-steps brake, self-review, GA
    reminder). The 3 pack-migrated features are NOT registered here — they load
    through their own manifest entries below (no dual-load)."""
    # Local import to avoid a context<->control_plane import cycle at module load.
    from magi_agent.adk_bridge.control_plane import build_core_default_plane

    plane = build_core_default_plane(
        os_environ=dict(context.env),
        general_automation_receipts=context.general_automation_receipts,
        contract_required=context.contract_required,
        agent_role=context.agent_role,
        self_review_fork_runner=context.self_review_fork_runner,
        self_review_candidate_sink=context.self_review_candidate_sink,
        self_review_config=context.self_review_config,
        self_review_now=context.self_review_now,
        self_review_scheduler=context.self_review_scheduler,
    )
    for control in plane._controls:
        context.register(control)


def provide_loop_resilience_controls(context: ControlPlaneProvideContext) -> None:
    """``control_plane:loop-resilience@1``: tool-exception reflection +
    schema-invalid argument feedback (6b7cd40e), strict default-OFF
    (``MAGI_TOOL_EXCEPTION_REFLECTION_ENABLED`` /
    ``MAGI_TOOL_SCHEMA_FEEDBACK_ENABLED``)."""
    from magi_agent.adk_bridge.control_plane import build_loop_resilience_controls

    for control in build_loop_resilience_controls(dict(context.env)):
        context.register(control)


def provide_facts_replan_control(context: ControlPlaneProvideContext) -> None:
    """``control_plane:facts-replan@1``: interval-based facts-survey replanning
    (#510), strict default-OFF (``MAGI_FACTS_REPLAN_ENABLED``)."""
    from magi_agent.adk_bridge.control_plane import build_facts_replan_controls

    for control in build_facts_replan_controls(dict(context.env)):
        context.register(control)


def provide_lifecycle_llm_call_audit_controls(
    context: ControlPlaneProvideContext,
) -> None:
    """``control_plane:lifecycle-llm-call-audit@1``: PR-F-LIFE2 per-LLM-call
    audit fan-out, strict default-OFF (``MAGI_CUSTOMIZE_LLM_CALL_HOOKS_ENABLED``).

    Production wiring keystone: ``build_default_plugin`` →
    ``build_control_plane_from_packs`` is the live runner path (cli/real_runner
    + transport/gate5b_governance). Without this pack entry the
    :class:`LifecycleLlmCallAuditControl` would only register through the
    legacy/compat ``build_default_plane`` composition surface, so authored
    ``before_llm_call`` / ``after_llm_call`` rules would silently never fire
    on operator-facing serve/REPL/child paths. Delegates to the same
    single-source builder used by ``build_default_plane`` so the controls,
    env gates, and per-turn budget defaults are byte-identical between both
    composition paths.
    """
    from magi_agent.adk_bridge.control_plane import (
        build_lifecycle_llm_call_audit_controls,
    )

    for control in build_lifecycle_llm_call_audit_controls(dict(context.env)):
        context.register(control)


def provide_tool_synthesis_nudge_control(context: ControlPlaneProvideContext) -> None:
    """``control_plane:tool-synthesis-nudge@1``: Live-SWE tool-synthesis
    reflection nudge (#512), default-OFF + frontier-tier gated via the runner's
    ``tool_synthesis_model_label`` on the provide context. Its manifest entry
    carries the highest priority so it registers LAST."""
    from magi_agent.adk_bridge.control_plane import build_tool_synthesis_nudge_controls

    for control in build_tool_synthesis_nudge_controls(
        dict(context.env),
        tool_synthesis_model_label=context.tool_synthesis_model_label,
    ):
        context.register(control)
