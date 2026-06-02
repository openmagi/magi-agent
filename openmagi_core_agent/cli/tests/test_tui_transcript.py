"""Tests for the PR-E1 streaming-transcript spike.

Style note: this package has no ``pytest-asyncio`` configured, so every async
test is a SYNC function that drives the coroutine via ``asyncio.run(...)`` —
matching the ``test_engine.py`` / ``test_headless.py`` convention. Textual's
own harness (``App.run_test()``) is an async context manager, so the bodies
live in nested ``async def _run()`` helpers.

These tests simulate all data; they never touch a model or the engine.
"""

from __future__ import annotations

import asyncio

from openmagi_core_agent.cli.tui.transcript import (
    TranscriptApp,
    TranscriptController,
)


# ---------------------------------------------------------------------------
# 1. A long simulated stream renders without error under run_test()
# ---------------------------------------------------------------------------
def test_long_stream_renders_without_error() -> None:
    async def _run() -> None:
        app = TranscriptApp(flush_interval=0.01)
        async with app.run_test() as pilot:
            controller = app.controller
            controller.begin_live()
            for i in range(5000):
                controller.append_delta(f"line {i}\n")
            # Flush any coalesced tail and commit the block.
            await controller.flush_now()
            controller.finalize_live()
            await pilot.pause()
        assert controller.committed_block_count == 1

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 2. Coalescing actually batches: N chunks within an interval cause < N flushes
# ---------------------------------------------------------------------------
def test_chunk_coalescing_batches_renders() -> None:
    async def _run() -> None:
        # A long interval so all chunks land inside ONE coalescing window; the
        # interval timer should not get a chance to fire mid-burst.
        app = TranscriptApp(flush_interval=10.0)
        async with app.run_test():
            controller = app.controller
            controller.begin_live()
            chunk_count = 200
            for i in range(chunk_count):
                controller.append_delta(f"chunk-{i} ")
            # One explicit flush coalesces the whole burst into a single render.
            await controller.flush_now()
            assert controller.flush_count >= 1
            assert controller.flush_count < chunk_count
            # The live widget reflects ALL the coalesced text after one flush.
            assert controller.live_render_count < chunk_count

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 3. Finalized blocks are immutable after commit
# ---------------------------------------------------------------------------
def test_finalized_blocks_are_immutable() -> None:
    async def _run() -> None:
        app = TranscriptApp(flush_interval=0.01)
        async with app.run_test():
            controller = app.controller
            controller.begin_live()
            controller.append_delta("hello world")
            await controller.flush_now()
            controller.finalize_live()
            committed = controller.committed_blocks_snapshot()
            assert committed == ("hello world",)

            # Advancing the stream after finalize must NOT mutate the committed
            # block — it belongs to the next live block.
            controller.begin_live()
            controller.append_delta("second block")
            await controller.flush_now()
            controller.finalize_live()

            committed_after = controller.committed_blocks_snapshot()
            # First block is byte-identical; a new second block was appended.
            assert committed_after[0] == "hello world"
            assert committed_after == ("hello world", "second block")

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 4. The live widget re-renders, finalized blocks do NOT re-render
# ---------------------------------------------------------------------------
def test_finalized_block_rendered_once_only() -> None:
    async def _run() -> None:
        app = TranscriptApp(flush_interval=0.01)
        async with app.run_test():
            controller = app.controller
            controller.begin_live()
            controller.append_delta("a")
            await controller.flush_now()
            controller.append_delta("b")
            await controller.flush_now()
            # Two live re-renders so far (the growing block).
            live_before = controller.live_render_count
            assert live_before >= 2

            controller.finalize_live()
            # Commit writes the finalized block to the RichLog exactly once.
            assert controller.committed_block_count == 1

            # Subsequent live activity on a NEW block must not re-render the
            # already-committed one (commit count stays 1 per finalize).
            controller.begin_live()
            controller.append_delta("c")
            await controller.flush_now()
            assert controller.committed_block_count == 1

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 5. Empty live block finalize is a no-op (no spurious committed block)
# ---------------------------------------------------------------------------
def test_finalize_empty_live_is_noop() -> None:
    async def _run() -> None:
        app = TranscriptApp(flush_interval=0.01)
        async with app.run_test():
            controller = app.controller
            controller.begin_live()
            controller.finalize_live()
            assert controller.committed_block_count == 0

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 6. Benchmark harness runs headlessly and reports plausible numbers
# ---------------------------------------------------------------------------
def test_bench_runs_and_reports() -> None:
    from openmagi_core_agent.cli.tui._bench import run_bench

    result = asyncio.run(run_bench(lines=2000, flush_interval=0.01))
    assert result.lines == 2000
    assert result.committed_block_count == 1
    assert result.flush_count >= 1
    # Coalescing: far fewer flushes than lines.
    assert result.flush_count < result.lines
    assert result.lines_per_sec > 0.0
