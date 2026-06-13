"""Gate5B serving-path governance wiring (cli/engine parity).

The gate5b user-visible serving path
(``transport.chat_routes.run_gate5b_user_visible_chat_response`` ->
``_run_live_chat_runner`` -> ``shadow.gate5b4c3_live_runner_boundary``) drives its
OWN ADK ``Agent`` + ``Runner`` and BYPASSES ``cli.engine.MagiEngineDriver``. As a
result none of the cli/engine governance reaches the canary/hosted serve: no
control-plane controls (loop-guard / compaction / edit-retry / self-review /
max-steps / tool-synthesis / GA reminder) and no pre-final evidence /
fact-grounding gate.

This module is the SHARED seam that lets the gate5b path run the SAME governance
the local CLI runs, reusing the existing first-party builders rather than
duplicating logic:

* :func:`build_gate5b_control_plane_plugins` reuses the EXACT
  ``adk_bridge.control_plane.build_default_plugin`` factory ``cli.real_runner``
  uses, so every control stays behind its OWN existing env flag.
* :func:`gate5b_pre_final_grounding_status` reuses the EXACT
  ``evidence.claim_grounding.FactGroundingEvidenceProducer`` /
  ``research.grounded_answer_guard.evaluate_answer_grounding`` detector
  ``cli.engine._fact_grounding_matched_requirement_labels`` uses.

Master gate
-----------
Everything here is inert unless ``MAGI_GATE5B_GOVERNANCE_ENABLED`` is truthy
(:func:`magi_agent.config.env.is_gate5b_governance_enabled`). With the flag OFF
the helpers return an empty plugin list / a no-op grounding status, so the
gate5b serving path is byte-identical to today. The INDIVIDUAL controls remain
behind their own flags even when this master flag is ON, so flag-unset behavior
for each control is also unchanged.

What reaches gate5b vs what stays cli-engine-only
-------------------------------------------------
REACHES gate5b when the flag is ON:
* The full control-plane plugin (``_ExtendedControlPlanePlugin``) attached to the
  gate5b ADK runner — i.e. every ``on_before_model`` / ``on_after_tool`` /
  ``on_tool_error`` / ``on_model_error`` / ``after_run`` control: loop-guard,
  context-compaction, edit-retry reflection, tool-exception reflection,
  schema-feedback, max-steps brake, self-review-after-turn, GA constraint
  reminder, facts-replan, tool-synthesis nudge. Each is still individually
  flag-gated inside ``build_default_plane``.
* A pre-final fact-grounding/evidence check over the turn's collected tool
  evidence (the public-event corpus reachable at this seam). An ungrounded
  guess answer blocks the user-visible response.

REMAINS cli-engine-only (NOT reachable on gate5b, by ADK-architecture
constraint, documented in ``adk_bridge.control_plane``):
* Hard turn-cap counting / max-steps *iteration* tracking (needs the outer
  driver's per-invocation loop counter; the plane registers the brake seam but
  the gate5b boundary does not feed it a live iteration count).
* stop-hook-deny -> re-iteration and stop-on-goal re-entry after end_turn
  (ADK has no force-loop-re-entry callback).
* The recipe-materialized ``RunnerPolicyAssembly`` pre-final *verifier-bus*
  (required_evidence / required_validators / document-coverage / GA-deliverable /
  coding-repair loop): the gate5b boundary has no recipe materialization and no
  per-turn ``EvidenceLedger`` collector, so the rich multi-validator bus stays a
  cli/engine concern. The fact-grounding check here is the reachable subset of
  that gate (the same producer, over the reachable corpus).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from magi_agent.config.env import is_gate5b_governance_enabled


def gate5b_governance_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Whether the gate5b-governance master switch is ON (strict default-OFF)."""
    return is_gate5b_governance_enabled(env)


def build_gate5b_control_plane_plugins(
    *,
    general_automation_receipts: object | None = None,
    contract_required: object | None = None,
    agent_role: str = "general",
    tool_synthesis_model_label: str | None = None,
    env: Mapping[str, str] | None = None,
) -> list[object]:
    """Build the control-plane plugin list for the gate5b runner, or ``[]``.

    Returns ``[]`` (the no-op) when the gate5b-governance master flag is OFF, so
    the caller passes no ``plugins`` to the gate5b ``Runner`` and construction is
    byte-identical to today. When ON, returns a single-element list holding the
    EXACT ``_ExtendedControlPlanePlugin`` that ``cli.real_runner`` attaches to the
    local CLI runner — built via the shared ``build_default_plugin`` so the two
    paths cannot drift. Every control inside it is still gated by its own env
    flag, so a bare ``MAGI_GATE5B_GOVERNANCE_ENABLED=1`` with no per-control flags
    set registers an empty plane (the plugin runs but every callback is a no-op).

    Fail-open: if building the plugin raises (e.g. a pack-discovery error), this
    returns ``[]`` so governance wiring can never break a serve turn — the gate5b
    runner just runs without the plane, exactly as today.
    """
    if not is_gate5b_governance_enabled(env):
        return []
    try:
        # Imported lazily: control_plane pulls in google.adk at module top, and
        # this module must stay importable on the no-ADK transport import graph.
        from magi_agent.adk_bridge.control_plane import build_default_plugin

        plugin = build_default_plugin(
            os_environ=dict(env) if env is not None else None,
            general_automation_receipts=general_automation_receipts,
            contract_required=contract_required,
            agent_role=agent_role,
            tool_synthesis_model_label=tool_synthesis_model_label,
        )
    except Exception:
        return []
    return [plugin]


# Public-event types that carry TOOL/EVIDENCE content (the corpus the agent
# collected) — as opposed to the model's OWN answer text (``text_delta``), which
# is the thing being grounded and must NOT count as its own supporting evidence.
_TOOL_EVIDENCE_EVENT_TYPES = frozenset(
    {"tool_start", "tool_progress", "tool_end", "source_inspected", "research_artifact_delta"}
)


def corpus_from_public_events(events: Sequence[Mapping[str, object]]) -> tuple[str, ...]:
    """Harvest the grounding corpus from the gate5b turn's public events.

    The gate5b toolhost retains only output DIGESTS on its receipts (no preview
    text), so the reachable "tool/evidence corpus the agent actually collected"
    at this serving seam is the bounded public-event stream emitted during the
    turn — specifically the TOOL-evidence events (``tool_*`` / ``source_inspected``
    / ``research_artifact_delta``): their ``output_preview`` / progress ``message``
    / ``label`` / ``detail`` strings plus any ``receipt_refs``.

    The model's OWN answer ``text_delta`` is deliberately EXCLUDED: it is the text
    being grounded, so counting it as corpus would let any answer trivially ground
    itself. This mirrors how the cli/engine fact-grounding satisfier grounds the
    ``final_text`` against the COLLECTED evidence records (not against the answer
    itself). Pure / never raises.
    """
    corpus: list[str] = []
    for event in events:
        if not isinstance(event, Mapping):
            continue
        if event.get("type") not in _TOOL_EVIDENCE_EVENT_TYPES:
            continue
        for key in ("output_preview", "outputPreview", "message", "detail", "label"):
            value = event.get(key)
            if isinstance(value, str) and value.strip():
                corpus.append(value)
        refs = event.get("receipt_refs") or event.get("receiptRefs")
        if isinstance(refs, Sequence) and not isinstance(refs, (str, bytes, bytearray)):
            for ref in refs:
                if isinstance(ref, str) and ref.strip():
                    corpus.append(ref)
    return tuple(dict.fromkeys(item for item in corpus if item.strip()))


def gate5b_pre_final_grounding_status(
    *,
    final_text: str,
    public_events: Sequence[Mapping[str, object]],
    env: Mapping[str, str] | None = None,
) -> str | None:
    """Pre-final fact-grounding decision over the gate5b turn's evidence.

    Returns:
        * ``None`` — the gate did not run (governance master flag OFF, or no
          corpus to ground against). The caller leaves the response untouched.
        * ``"grounded"`` — the answer is grounded (or carries no specific value to
          ground, the G4 boundary). The caller emits the response unchanged.
        * ``"ungrounded_guess"`` — the answer asserts a specific numeric/identifier
          value NOT supported anywhere in the collected corpus. The caller BLOCKS
          the user-visible response.

    Reuses the EXACT deterministic detector the cli/engine pre-final gate uses
    (``evaluate_answer_grounding`` via ``FactGroundingEvidenceProducer``), so the
    grounding DECISION is identical to local-CLI behavior. Pure: no I/O, no model
    call. Fail-open: any error returns ``None`` so it can only ever REMOVE a turn
    (block an ungrounded guess), never wedge one on an internal fault.
    """
    if not is_gate5b_governance_enabled(env):
        return None
    if not final_text or not final_text.strip():
        return None
    corpus = corpus_from_public_events(public_events)
    if not corpus:
        # No reachable evidence corpus at this seam — do not block (the gate5b
        # path may legitimately answer from the model's own knowledge; the
        # grounding guard only fires when there IS a corpus to contradict).
        return None
    try:
        from magi_agent.research.grounded_answer_guard import evaluate_answer_grounding

        verdict = evaluate_answer_grounding(final_text, corpus)
    except Exception:
        return None
    return "grounded" if verdict.status == "grounded" else "ungrounded_guess"


__all__ = [
    "build_gate5b_control_plane_plugins",
    "corpus_from_public_events",
    "gate5b_governance_enabled",
    "gate5b_pre_final_grounding_status",
]
