"""PR4.3 — subagent / child-run inline display (REDESIGNED).

The runtime surfaces subagent (child-run) activity to the TUI as a ``status``
``RuntimeEvent`` whose payload ``type`` is one of ``child_started`` /
``child_progress`` / ``child_completed`` / ``child_cancelled`` / ``child_failed``
(engine maps these → ``status`` via ``_map_event_kind``; the SSE sanitizer lets
them through unconditionally — they are NOT gated behind a flag like thinking's
``MAGI_STREAM_THINKING`` — as long as ``taskId`` + ``childReceiptRef`` survive).
The quiet-by-default ``_fold_event`` filter drops ALL ``status`` events unless
``MAGI_TUI_VERBOSE=1`` — so subagent activity would otherwise be invisible.

This PR intercepts the child-marked ``status`` events in ``_fold_event`` *before*
the quiet drop and commits them as a DIM INDENTED ONE-LINE block
(``  ⤷ subagent <label>  <status>``), distinct in style from assistant text and
top-level tool lines. Plumbing ``status`` events (runner_policy_*, phase_route_*,
turn_end) stay hidden by default. Search fidelity: the subagent line is in the
committed snapshot. Lifecycle events for the SAME ``taskId`` coalesce into one
updating line (started → completed/failed), not a spammy line per event.

Style: this package has no ``pytest-asyncio``; async tests are SYNC functions
driving the coroutine via ``asyncio.run`` with a nested ``async def _run`` that
uses Textual's ``App.run_test()`` harness. The engine is ALWAYS mocked.
"""

from __future__ import annotations

import asyncio

from magi_agent.cli.contracts import (
    CommandSurface,
    ControlRequest,
    EngineResult,
    LocalCommand,
    PermissionDecision,
    PermissionGate,
    RuntimeEvent,
    Terminal,
    ToolRendererRegistry,
)
from magi_agent.cli.tui.app import MagiTuiApp
from magi_agent.cli.tui.tool_render import build_tool_renderers

_TUI = CommandSurface(tui=True, headless=False)


class _Reg:
    def __init__(self) -> None:
        self._c = [LocalCommand(name="compact", surface=_TUI)]

    def lookup(self, name):
        return next((c for c in self._c if c.name == name), None)

    def list_for(self, surface):
        _ = surface
        return list(self._c)


class _Allow(PermissionGate):
    async def check(self, req: ControlRequest) -> PermissionDecision:
        _ = req
        return PermissionDecision(kind="allow")


def _child_event(inner: str, task_id: str, **extra) -> RuntimeEvent:
    # Exactly what reaches the TUI: a status event whose inner payload ``type``
    # is a ``child_*`` string carrying the (already-sanitized) ``taskId``.
    payload = {"type": inner, "taskId": task_id, "childReceiptRef": "rcpt-1"}
    payload.update(extra)
    return RuntimeEvent(type="status", payload=payload, turn_id="t")


def _reasoning_event(text: str) -> RuntimeEvent:
    # A thinking_delta status event (what reaches the TUI when MAGI_STREAM_THINKING
    # let it through upstream). Renders as the dim ``● thinking <preview>`` line.
    return RuntimeEvent(
        type="status",
        payload={"type": "thinking_delta", "delta": text},
        turn_id="t",
    )


def _plumbing_event() -> RuntimeEvent:
    # A representative plumbing status event that MUST stay hidden by default.
    return RuntimeEvent(
        type="status",
        payload={"type": "runner_policy_assembly", "phase": "executing"},
        turn_id="t",
    )


class _ChildEngine:
    def __init__(self, events: list[RuntimeEvent]) -> None:
        self._events = events

    async def run_turn_stream(self, runtime, turn_input, *, cancel, gate=None):
        _ = (runtime, cancel, gate)
        turn_id = getattr(turn_input, "turn_id", "t")
        for ev in self._events:
            yield RuntimeEvent(type=ev.type, payload=ev.payload, turn_id=turn_id)
        yield EngineResult(terminal=Terminal.completed, turn_id=turn_id)


def _make_app(events: list[RuntimeEvent], *, renderers=None) -> MagiTuiApp:
    return MagiTuiApp(
        engine=_ChildEngine(events),
        gate=_Allow(),
        commands=_Reg(),
        renderers=renderers if renderers is not None else ToolRendererRegistry(),
    )


def _bash_start(command: str) -> RuntimeEvent:
    return RuntimeEvent(
        type="tool",
        payload={"type": "tool_start", "id": "c1", "name": "Bash",
                 "input": {"command": command}},
        turn_id="t",
    )


def _bash_end(stdout: str) -> RuntimeEvent:
    return RuntimeEvent(
        type="tool",
        payload={
            "type": "tool_end",
            "id": "c1",
            "name": "Bash",
            "status": "ok",
            "output_preview": {"stdout": stdout},
        },
        turn_id="t",
    )


def test_child_started_commits_a_dim_subagent_line_by_default() -> None:
    async def _run() -> None:
        app = _make_app([_child_event("child_started", "research-subtask")])
        async with app.run_test() as pilot:
            app.start_turn("go")
            await app.workers.wait_for_complete()
            await pilot.pause()
            blocks = app.controller.committed_blocks_snapshot()
            # A subagent line was committed BY DEFAULT (no MAGI_TUI_VERBOSE).
            sub = [b for b in blocks if "subagent" in b]
            assert sub, f"expected a subagent line, got {blocks!r}"
            # The task label is on the committed block (search fidelity).
            assert any("research-subtask" in b for b in sub)

    asyncio.run(_run())


def test_plumbing_status_event_stays_hidden_by_default() -> None:
    async def _run() -> None:
        app = _make_app([_plumbing_event()])
        async with app.run_test() as pilot:
            app.start_turn("go")
            await app.workers.wait_for_complete()
            await pilot.pause()
            blocks = app.controller.committed_blocks_snapshot()
            # The plumbing status event produced no committed block.
            assert not any("runner_policy_assembly" in b for b in blocks), blocks
            assert not any("subagent" in b for b in blocks), blocks

    asyncio.run(_run())


def test_subagent_line_is_styled_dim_indented_and_distinct() -> None:
    """The subagent render is a DIM INDENTED rich block (distinct from tool/text)."""

    from magi_agent.cli.tui.app import _render_subagent_node

    node = _render_subagent_node("worker-1", "running")
    # The committed/search text carries the marker + label + status (fidelity).
    assert "subagent" in node.text
    assert "worker-1" in node.text
    assert "running" in node.text
    # The displayed line is INDENTED (leading whitespace) — visually nested
    # under the parent turn, distinct from flush-left top-level tool/text lines.
    assert node.text.startswith(" "), repr(node.text)
    # The rich renderable styles the whole line dim (distinct from the teal/blue
    # tool dot styles). Every styled span on the line is dim.
    rich = node.rich
    assert rich is not None
    styles = [str(span.style) for span in rich.spans]
    assert styles, "expected styled spans on the subagent line"
    assert all("dim" in s for s in styles), styles


def test_child_lifecycle_coalesces_into_one_subagent_line() -> None:
    """started -> completed for ONE task folds into a single updating line."""

    async def _run() -> None:
        app = _make_app(
            [
                _child_event("child_started", "sub-1"),
                _child_event("child_progress", "sub-1", detail="halfway"),
                _child_event("child_completed", "sub-1"),
            ]
        )
        async with app.run_test() as pilot:
            app.start_turn("go")
            await app.workers.wait_for_complete()
            await pilot.pause()
            blocks = app.controller.committed_blocks_snapshot()
            sub = [b for b in blocks if "subagent" in b]
            # Coalesced: the lifecycle events fold into a single updating block,
            # not three separate spammy lines.
            assert len(sub) == 1, sub
            # The latest status (completed) is reflected in the coalesced line.
            assert "completed" in sub[0], sub
            assert "sub-1" in sub[0], sub

    asyncio.run(_run())


def test_distinct_tasks_get_distinct_subagent_lines() -> None:
    """Two different taskIds -> two separate coalesced subagent lines."""

    async def _run() -> None:
        app = _make_app(
            [
                _child_event("child_started", "sub-a"),
                _child_event("child_started", "sub-b"),
                _child_event("child_completed", "sub-a"),
            ]
        )
        async with app.run_test() as pilot:
            app.start_turn("go")
            await app.workers.wait_for_complete()
            await pilot.pause()
            blocks = app.controller.committed_blocks_snapshot()
            sub = [b for b in blocks if "subagent" in b]
            assert len(sub) == 2, sub
            assert any("sub-a" in b and "completed" in b for b in sub), sub
            assert any("sub-b" in b and "running" in b for b in sub), sub

    asyncio.run(_run())


def test_two_child_started_produce_two_running_lines_before_completion() -> None:
    """Two ``child_started`` events (no completion) -> TWO distinct running
    subagent lines. Hardens the keying invariant: a regression that keyed the
    coalescing dict off the truncated DISPLAY label (instead of the raw taskId)
    would let two ids sharing a long prefix collide into ONE line — caught here."""

    async def _run() -> None:
        app = _make_app(
            [
                _child_event("child_started", "sub-a"),
                _child_event("child_started", "sub-b"),
            ]
        )
        async with app.run_test() as pilot:
            app.start_turn("go")
            await app.workers.wait_for_complete()
            await pilot.pause()
            blocks = app.controller.committed_blocks_snapshot()
            sub = [b for b in blocks if "subagent" in b]
            # Two distinct lines, both still RUNNING (neither has completed).
            assert len(sub) == 2, sub
            assert any("sub-a" in b and "running" in b for b in sub), sub
            assert any("sub-b" in b and "running" in b for b in sub), sub

    asyncio.run(_run())


def test_two_taskids_sharing_long_prefix_do_not_collide() -> None:
    """Two taskIds sharing a 59-char prefix (so their truncated DISPLAY labels
    are identical) must STILL get two distinct lines — the coalescing key is the
    RAW taskId, not the truncated label. A label-keyed regression collides these
    into one line (this is the direct FIX D guard)."""

    prefix = "x" * 59
    id_one = prefix + "AAA"
    id_two = prefix + "BBB"

    async def _run() -> None:
        app = _make_app(
            [
                _child_event("child_started", id_one),
                _child_event("child_started", id_two),
            ]
        )
        async with app.run_test() as pilot:
            app.start_turn("go")
            await app.workers.wait_for_complete()
            await pilot.pause()
            blocks = app.controller.committed_blocks_snapshot()
            sub = [b for b in blocks if "subagent" in b]
            # Distinct raw ids -> two lines even though the displayed labels match.
            assert len(sub) == 2, sub

    asyncio.run(_run())


def test_child_task_label_cjk_is_cell_bounded() -> None:
    """A long Hangul ``taskId`` yields a DISPLAY label bounded by
    ``_SUBAGENT_LABEL_MAX_CHARS`` in *cells*, not codepoints. The test touches
    ONLY the label path (``_child_task_label``); the raw coalescing key
    (``_child_task_key``) is computed independently and must stay untruncated."""

    from magi_agent.cli.render.width import display_width
    from magi_agent.cli.tui.app import (
        _SUBAGENT_LABEL_MAX_CHARS,
        _child_task_key,
        _child_task_label,
    )

    payload = {"taskId": "가" * 80, "childReceiptRef": "rcpt-1"}
    label = _child_task_label(payload)
    assert display_width(label) <= _SUBAGENT_LABEL_MAX_CHARS
    assert label.endswith("…")
    # Coalescing safety: the raw key is the FULL untruncated taskId, NOT the
    # truncated label — truncation can never corrupt the dedup key.
    assert _child_task_key(payload) == "가" * 80


def test_same_turn_thinking_and_child_do_not_clobber() -> None:
    """A ``thinking_delta`` AND a ``child_started`` in the SAME turn each get
    their OWN committed line — the shared coalescing primitive
    (``commit_coalesced``) is keyed by SEPARATE state (the thinking accumulator
    vs the per-taskId subagent registry), so they never overwrite each other."""

    async def _run() -> None:
        app = _make_app(
            [
                _reasoning_event("planning the subtask"),
                _child_event("child_started", "research-subtask"),
            ]
        )
        async with app.run_test() as pilot:
            app.start_turn("go")
            await app.workers.wait_for_complete()
            await pilot.pause()
            blocks = app.controller.committed_blocks_snapshot()
            thinking = [b for b in blocks if "thinking" in b]
            sub = [b for b in blocks if "subagent" in b]
            # Exactly ONE thinking line AND exactly ONE subagent line.
            assert len(thinking) == 1, thinking
            assert len(sub) == 1, sub
            # They are SEPARATE snapshot entries (no shared block).
            assert thinking[0] != sub[0], (thinking, sub)
            # Each carries its own correct text — neither clobbered the other.
            assert "planning the subtask" in thinking[0], thinking
            assert "research-subtask" in sub[0], sub
            assert "subagent" not in thinking[0], thinking
            assert "thinking" not in sub[0], sub

    asyncio.run(_run())


def test_child_failed_shows_failed_status_and_error_reason() -> None:
    async def _run() -> None:
        app = _make_app(
            [
                _child_event("child_started", "sub-x"),
                _child_event("child_failed", "sub-x", errorMessage="boom"),
            ]
        )
        async with app.run_test() as pilot:
            app.start_turn("go")
            await app.workers.wait_for_complete()
            await pilot.pause()
            blocks = app.controller.committed_blocks_snapshot()
            sub = [b for b in blocks if "subagent" in b]
            assert len(sub) == 1, sub
            assert "failed" in sub[0], sub
            # The failure CAUSE is surfaced on the line (not just "failed").
            assert "boom" in sub[0], sub

    asyncio.run(_run())


def test_child_progress_shows_detail() -> None:
    async def _run() -> None:
        app = _make_app(
            [
                _child_event("child_started", "sub-p"),
                _child_event("child_progress", "sub-p", detail="halfway there"),
            ]
        )
        async with app.run_test() as pilot:
            app.start_turn("go")
            await app.workers.wait_for_complete()
            await pilot.pause()
            blocks = app.controller.committed_blocks_snapshot()
            sub = [b for b in blocks if "subagent" in b]
            assert len(sub) == 1, sub
            # The progress detail is surfaced so distinct progress isn't
            # indistinguishable from a bare "running".
            assert "halfway there" in sub[0], sub

    asyncio.run(_run())


def test_child_cancelled_shows_reason() -> None:
    async def _run() -> None:
        app = _make_app(
            [
                _child_event("child_started", "sub-c"),
                _child_event("child_cancelled", "sub-c", reason="user abort"),
            ]
        )
        async with app.run_test() as pilot:
            app.start_turn("go")
            await app.workers.wait_for_complete()
            await pilot.pause()
            blocks = app.controller.committed_blocks_snapshot()
            sub = [b for b in blocks if "subagent" in b]
            assert len(sub) == 1, sub
            assert "cancelled" in sub[0], sub
            assert "user abort" in sub[0], sub

    asyncio.run(_run())


def test_interleaved_child_tool_child_keeps_one_subagent_line() -> None:
    """Index-stability invariant: a tool block committing BETWEEN two child
    events for the same task must NOT cause a duplicate subagent line, and the
    second child event must patch the ORIGINAL subagent line in place — never
    overwrite the intervening tool block.
    """

    async def _run() -> None:
        app = _make_app(
            [
                _child_event("child_started", "sub-1"),
                _bash_start("echo hi"),
                _bash_end("hi\n"),
                _child_event("child_completed", "sub-1"),
            ],
            renderers=build_tool_renderers(),
        )
        async with app.run_test() as pilot:
            app.start_turn("go")
            await app.workers.wait_for_complete()
            await pilot.pause()
            blocks = app.controller.committed_blocks_snapshot()

            # Exactly ONE subagent block, coalesced to the latest status.
            sub = [b for b in blocks if "subagent" in b]
            assert len(sub) == 1, sub
            assert "completed" in sub[0], sub

            # The tool block is PRESENT and UNAFFECTED.
            assert any("$ echo hi" in b for b in blocks), blocks
            assert any("hi" in b and "subagent" not in b for b in blocks), blocks
            # And the tool entry was never mutated into a subagent line.
            assert not any("subagent" in b and "echo hi" in b for b in blocks), blocks

    asyncio.run(_run())


def test_subagent_lines_reset_between_turns() -> None:
    """A new turn starts fresh — a same-named task in turn 2 is a new line,
    not an in-place update of turn 1's (committed) line."""

    async def _run() -> None:
        app = _make_app([_child_event("child_started", "sub-1")])
        async with app.run_test() as pilot:
            app.start_turn("first")
            await app.workers.wait_for_complete()
            await pilot.pause()
            # Swap the engine for a second turn emitting the same task id again.
            app._engine = _ChildEngine([_child_event("child_started", "sub-1")])
            app.start_turn("second")
            await app.workers.wait_for_complete()
            await pilot.pause()
            blocks = app.controller.committed_blocks_snapshot()
            sub = [b for b in blocks if "subagent" in b]
            # Two committed subagent lines — one per turn (registry reset).
            assert len(sub) == 2, sub

    asyncio.run(_run())
