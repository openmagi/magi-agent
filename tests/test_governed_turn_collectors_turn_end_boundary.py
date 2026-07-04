"""Real-trace multi-attempt boundary semantics for the ``governed_turn``
sibling collectors (PR-U follow-up to PR-T).

The four collectors covered here share the same flat aggregation the child
collector had before PR-U, so they record ``"prelim + final"`` (or the
observed hosted ``"22"`` / ``"2\\n2"`` shape) in bookend / audit
``result_text`` on the same real trace. This module pins the deferred
``turn_end`` + ``tool_end`` boundary semantic into each of them.

Instantiation strategy
----------------------
The collectors are normally built by ``maybe_create`` behind a master flag.
The tests here bypass ``maybe_create`` and drive ``observe`` directly on a
freshly-constructed instance, which is legitimate: ``observe`` is the pure
aggregation surface (no I/O, no async), and the master flag gate is a
separate concern already covered by
``tests/customize_firing/`` and the collector's own maybe_create tests.
"""

from __future__ import annotations

from magi_agent.runtime.governed_turn import (
    _AfterTurnEndCollector,
    _BookendCollector,
    _OnTaskCompleteCollector,
    _SubagentStopCollector,
)
from magi_agent.runtime.events import RuntimeEvent
from magi_agent.runtime.turn_context import TurnContext


def _make_ctx(*, depth: int = 0) -> TurnContext:
    return TurnContext(
        prompt="p",
        session_id="s-1",
        turn_id="t-1",
        depth=depth,
    )


def _emit(collector, events: list[RuntimeEvent]) -> None:
    for ev in events:
        collector.observe(ev)


def _prelim_boundary_final_events() -> list[RuntimeEvent]:
    """Kevin's real-trace shape: preliminary block, ``turn_end committed``,
    final block. Both blocks emit ``text_delta`` events; nothing else
    separates them.
    """
    return [
        RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "prelim"}),
        RuntimeEvent(
            type="status",
            payload={
                "type": "turn_end",
                "turnId": "turn:x",
                "status": "committed",
                "stopReason": "end_turn",
                "expectReceipt": False,
            },
        ),
        RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "final"}),
    ]


def test_bookend_collector_turn_end_boundary_resets_result_text():
    """``_BookendCollector._result_text`` must reflect the FINAL block only
    after a ``turn_end`` boundary. Before PR-U it was ``"prelimfinal"`` on
    the Kevin trace shape.
    """
    ctx = _make_ctx(depth=0)
    collector = _BookendCollector(ctx)
    _emit(collector, _prelim_boundary_final_events())
    assert collector._result_text == "final"


def test_subagent_stop_collector_turn_end_boundary_resets():
    """``_SubagentStopCollector`` runs the ``on_subagent_stop`` audit with the
    aggregated final text. PR-U's deferred reset must apply so multi-attempt
    child turns feed the FINAL block into the audit judge, not
    ``prelim + final``.
    """
    ctx = _make_ctx(depth=1)  # child depth so the class is semantically live
    collector = _SubagentStopCollector(ctx)
    _emit(collector, _prelim_boundary_final_events())
    assert collector._result_text == "final"


def test_after_turn_end_collector_turn_end_boundary_resets():
    """``_AfterTurnEndCollector`` runs the top-level ``after_turn_end`` audit
    with the aggregated final text. Same deferred reset semantic as the
    child collector so operators authoring at this slot judge the FINAL
    response block, not the concatenation.
    """
    ctx = _make_ctx(depth=0)
    collector = _AfterTurnEndCollector(ctx)
    _emit(collector, _prelim_boundary_final_events())
    assert collector._result_text == "final"


def test_on_task_complete_collector_turn_end_boundary_resets():
    """``_OnTaskCompleteCollector`` accumulates the top-level final text to
    check for the ``<task_done>`` marker. Multi-attempt turns must feed the
    FINAL block into the marker check, not the concatenation.
    """
    ctx = _make_ctx(depth=0)
    collector = _OnTaskCompleteCollector(ctx)
    _emit(collector, _prelim_boundary_final_events())
    assert collector._result_text == "final"
