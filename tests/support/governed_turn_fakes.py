"""Governed-turn fakes for the hosted serving suites (P5-M1b).

The hosted serving path routes every turn through ``run_governed_turn`` ->
``MagiEngineDriver`` and collects the result with
``collect_engine_to_boundary_result`` (the legacy ``run_gate5b4c3_live_runner_
boundary_async`` engine was retired in P5-M1b). These helpers let a test inject
a deterministic governed event stream at the SAME seam the production code uses,
so the assertions the legacy fakes used to exercise (progressive ``text_delta``
emit, tool events before final text, gate1a-error, timeout, turn registration)
carry over onto the governed engine unchanged -- only the injection seam moves.

Usage::

    from tests.support.governed_turn_fakes import install_governed_turn

    install_governed_turn(
        monkeypatch,
        events=[
            {"type": "tool_start", "toolName": "Grep"},
            {"type": "tool_end", "output_preview": "found 3"},
            {"type": "text_delta", "delta": "Here is the answer."},
        ],
        terminal="completed",
    )

The real ``collect_engine_to_boundary_result`` then drains these events, so the
resulting ``Gate5B4C3LiveRunnerBoundaryResult`` carries the aggregated
``outputTextInternal`` and every event is teed to the route's
``public_event_sink`` exactly as a live governed turn would emit them.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence

from magi_agent.engine.contracts import EngineResult, Terminal
from magi_agent.runtime.events import RuntimeEvent

# Serving module the production seam lives on (both completions + SSE reach it).
_SERVING = "magi_agent.transport.gate5b_serving"


def _terminal(name: str) -> Terminal:
    return {
        "completed": Terminal.completed,
        "aborted": Terminal.aborted,
        "max_turns": Terminal.max_turns,
        "error": Terminal.error,
    }[name]


def _event_kind(payload: Mapping[str, object]) -> str:
    """Map a public-event payload ``type`` to a coarse RuntimeEvent kind literal.

    ``collect_engine_to_boundary_result`` inspects ``payload["type"]`` (not the
    RuntimeEvent ``type`` literal) for text aggregation and forwards the whole
    payload to the sink, so the literal only needs to be a valid member of
    ``EventKind`` (``status``/``token``/``tool``/``control``/``artifact``/``error``).
    """
    ptype = str(payload.get("type") or "")
    if ptype in ("tool_start", "tool_end", "tool_progress"):
        return "tool"
    return "token"


def build_governed_event_stream(
    events: Sequence[Mapping[str, object]],
    *,
    terminal: str = "completed",
    session_id: str = "s",
    turn_id: str = "t",
    usage: dict | None = None,
    error: str | None = None,
):
    """Return a zero-arg factory producing the async event generator.

    The factory shape matches what ``run_governed_turn`` returns: an async
    generator yielding ``RuntimeEvent`` items and finally an ``EngineResult``.
    """

    async def _gen() -> AsyncIterator[object]:
        for payload in events:
            yield RuntimeEvent(
                type=_event_kind(payload),
                payload=dict(payload),
                turn_id=turn_id,
            )
        yield EngineResult(
            terminal=_terminal(terminal),
            usage=usage or {},
            error=error,
            session_id=session_id,
            turn_id=turn_id,
        )

    return _gen


def install_governed_turn(
    monkeypatch,
    *,
    events: Sequence[Mapping[str, object]] = (),
    terminal: str = "completed",
    usage: dict | None = None,
    error: str | None = None,
    serving_module: str = _SERVING,
    track: dict[str, int] | None = None,
) -> dict[str, int]:
    """Patch ``run_governed_turn`` on the serving module to a deterministic fake.

    The REAL ``collect_engine_to_boundary_result`` is left in place so text
    aggregation, terminal mapping, and public-event teeing all run exactly as in
    production. Returns a counter dict (``{"governed": N}``) so callers can assert
    the governed engine was driven.
    """
    counts = track if track is not None else {"governed": 0}
    counts.setdefault("governed", 0)

    def _fake_run_governed_turn(ctx, *, runtime, cancel=None):  # noqa: ANN001, ANN202
        counts["governed"] += 1
        return build_governed_event_stream(
            events, terminal=terminal, usage=usage, error=error
        )()

    monkeypatch.setattr(f"{serving_module}.run_governed_turn", _fake_run_governed_turn)
    return counts
