"""Matrix enumeration over the ``_LEGAL`` (kind, slot, action) table.

Single source of truth for "which (kind, slot, action) combinations are legal
to author" lives in :mod:`magi_agent.customize.custom_rules._LEGAL`. This
module re-projects that table into iterators / counts the e2e harness
parametrizes against.

Tested via the parametrized matrix in
``tests/e2e/customize/test_matrix_tool_use.py``. Adding a kind or slot to
``_LEGAL`` automatically extends F-QA coverage — no test list to update.
"""

from __future__ import annotations

from collections.abc import Iterable

from magi_agent.customize.custom_rules import _LEGAL

# F-QA1 scope axis: only slots the runtime fan-out can drive in this PR.
# F-QA2-5 extend to the remaining 14 lifecycle slots. Keeping the filter
# here (rather than in ``test_matrix_tool_use.py``) makes the F-QA stack's
# scope progression auditable from a single import site.
F_QA1_SLOTS: frozenset[str] = frozenset(
    {"pre_final", "before_tool_use", "after_tool_use"}
)

# F-QA2 scope axis: turn-boundary slots driven through ``run_governed_turn``.
# These four slots all funnel through the canonical CLI/serve/child
# governed-turn entry point — ``before_turn_start`` / ``on_user_prompt_submit``
# are GATE slots (block short-circuits the engine stream); ``after_turn_end``
# fires in the finally block on TOP-LEVEL turns; ``on_subagent_stop`` fires
# in the finally block on CHILD turns. F-LIFE1's authorability-lift makes
# ``on_subagent_stop`` accept block/ask_approval at the validator even
# though runtime parent-surfacing is not yet wired (TODO).
F_QA2_SLOTS: frozenset[str] = frozenset(
    {
        "before_turn_start",
        "after_turn_end",
        "on_user_prompt_submit",
        "on_subagent_stop",
    }
)

# F-QA3 scope axis: per-LLM-call slots driven through the ADK plugin
# ``LifecycleLlmCallAuditControl`` (on_before_model / on_after_model).
# v1 ``_LEGAL`` only accepts ``llm_criterion`` rules at these slots;
# F-LIFE4a lifted the action set from audit-only to ``{audit, block}``
# — block at before_llm_call SUPPRESSES the outbound model call by
# returning a synthetic policy-blocked LlmResponse; block at
# after_llm_call REPLACES the just-emitted response with the synthetic
# refusal. The same per-turn critic budget
# (``MAGI_CUSTOMIZE_LLM_CALL_AUDIT_BUDGET``, default 3) applies to both
# slots — the budget is shared so a single misbehaving rule cannot
# exceed the cost ceiling across before/after combined.
F_QA3_SLOTS: frozenset[str] = frozenset(
    {
        "before_llm_call",
        "after_llm_call",
    }
)

# F-QA4 scope axis: late-lifecycle slots driven through their respective
# runtime chokepoints —
# * ``before_compaction`` / ``after_compaction`` →
#   :meth:`MagiContextCompactionPlugin._apply_tail_trim`
#   (audit + ``before_compaction`` gate consult).
# * ``on_task_checkpoint`` →
#   :meth:`WorkQueueDriver.run_once` (audit at every transition; gate
#   honored at ``claimed`` only — see F-LIFE4a review pass NOTE).
# * ``on_artifact_created`` →
#   :meth:`FileDeliveryBoundary.execute` (audit + ``ask_approval`` gate;
#   block is honestly impossible — the artifact was already written).
# * ``on_task_complete`` →
#   :class:`_OnTaskCompleteCollector.run_audit` inside
#   ``run_governed_turn`` (audit; block/ask are validator-accepted but
#   the compensating-action wire is deferred per F-LIFE4b review).
# * ``on_session_start`` →
#   :meth:`LifecycleSessionControl.on_before_model` (first-fire-per-
#   session; block returns a synthetic policy-blocked LlmResponse).
# * ``spawn`` →
#   :func:`magi_agent.customize.capability_scope.apply_capability_scope`
#   (only legal slot for ``capability_scope`` rules — subtracts the
#   denied tools from the resolved child toolset).
#
# ``on_session_end`` is in ``_LEGAL`` but explicitly EXCLUDED from
# :data:`F_QA4_SLOTS` because F-LIFE4b ships no transport-side emit wire
# in v1 — the slot is wizard-authored only, the runtime audit ledger
# stays silent until a follow-up adds the emit. The matrix test file
# auto-skips ``on_session_end`` rows via ``pytest.mark.skipif`` keyed on
# the slot value so a future enablement only needs to flip one constant.
F_QA4_SLOTS: frozenset[str] = frozenset(
    {
        "before_compaction",
        "after_compaction",
        "on_task_checkpoint",
        "on_artifact_created",
        "on_task_complete",
        "on_session_start",
        "spawn",
    }
)


def iter_legal_combinations() -> Iterable[tuple[str, str, str]]:
    """Yield every ``(kind, fires_at, action)`` tuple from the full ``_LEGAL`` matrix.

    Order is stable (Python dict + frozenset iteration is deterministic per
    interpreter run within a single process). Callers that need a sorted
    test-id projection can ``sorted()`` the result.
    """
    for kind, slot_map in _LEGAL.items():
        for slot, actions in slot_map.items():
            for action in actions:
                yield (kind, slot, action)


def iter_legal_combinations_for_slots(
    slots: Iterable[str],
) -> Iterable[tuple[str, str, str]]:
    """Yield only the ``(kind, slot, action)`` tuples whose slot is in ``slots``.

    F-QA1 wraps this with :data:`F_QA1_SLOTS`. F-QA2 will reuse this helper
    with the turn-boundary slot set.
    """
    slot_filter = frozenset(slots)
    for kind, slot, action in iter_legal_combinations():
        if slot in slot_filter:
            yield (kind, slot, action)


def enumerate_kind_slots(kind: str) -> list[tuple[str, str]]:
    """Return the list of ``(slot, action)`` pairs legal for ``kind``.

    Convenience helper for per-kind matrix slices (e.g. building a wizard's
    "What can I author at slot X?" inventory). Returns an empty list when
    ``kind`` is not in :data:`_LEGAL`.
    """
    slot_map = _LEGAL.get(kind, {})
    pairs: list[tuple[str, str]] = []
    for slot, actions in slot_map.items():
        for action in actions:
            pairs.append((slot, action))
    return pairs


def get_tool_bearing_slots() -> frozenset[str]:
    """Return the slot set whose fan-out runs through the tool dispatcher.

    Used by :mod:`tests.e2e.customize.triggers` to route a ``(kind, slot)``
    combo to the right driver. ``before_tool_use`` and ``after_tool_use``
    fire inside :func:`magi_agent.facades.execute_tool_with_hooks`; every
    other slot fires elsewhere (governed turn, ADK callback, work queue,
    artifact boundary, etc.).
    """
    return frozenset({"before_tool_use", "after_tool_use"})


def get_audit_only_slots() -> frozenset[str]:
    """Return the slot set whose ``_LEGAL`` actions are exclusively ``audit``.

    Useful for narrower coverage runs that skip block / ask_approval semantics
    (e.g. a smoke pass that only confirms the audit ledger captured the
    verdict). Computed dynamically so additions to ``_LEGAL`` are picked up
    automatically.
    """
    audit_only: set[str] = set()
    seen: set[str] = set()
    for _kind, slot_map in _LEGAL.items():
        for slot, actions in slot_map.items():
            seen.add(slot)
            if actions == frozenset({"audit"}):
                audit_only.add(slot)
            else:
                audit_only.discard(slot)
                audit_only.add("__never__")  # sentinel; cleaned below
    # Drop the sentinel + any slot that ever appeared with a non-audit action.
    audit_only.discard("__never__")
    return frozenset(s for s in audit_only if s in seen)


def count_legal_combos(slots: Iterable[str] | None = None) -> int:
    """Return the number of legal combos (optionally filtered by ``slots``).

    Reported in the F-QA README as a coverage gauge so an operator can see
    "F-QA1 covers N of M total ``_LEGAL`` rows" at a glance.
    """
    if slots is None:
        return sum(1 for _ in iter_legal_combinations())
    return sum(1 for _ in iter_legal_combinations_for_slots(slots))

F_QA5_SHELL_SLOTS: frozenset[str] = frozenset(
    {
        # shell_command + shell_check overlap (gate-honored on both)
        "pre_final",
        "before_tool_use",
        # shell_command-only or audit-only on shell_check
        "after_tool_use",
        # turn-boundary + subagent stop
        "on_user_prompt_submit",
        "on_subagent_stop",
        "before_turn_start",
        "after_turn_end",
        # compaction + work-queue + artifact
        "before_compaction",
        "after_compaction",
        "on_task_checkpoint",
        "on_artifact_created",
    }
)

