"""Headless throughput benchmark for the streaming-transcript spike (PR-E1).

Feeds a simulated long stream through :class:`TranscriptController` under
Textual's headless test harness and measures throughput + per-flush cost. Proves
the render strategy (finalized-blocks-in-RichLog + single coalesced live widget)
sustains 5k-10k lines without re-parsing the whole transcript per chunk.

Run directly to print numbers::

    python -m magi_agent.cli.tui._bench --lines 10000
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from time import perf_counter

from magi_agent.cli.tui.transcript import (
    DEFAULT_FLUSH_INTERVAL,
    TranscriptApp,
)

__all__ = ["BenchResult", "run_bench", "main"]


@dataclass
class BenchResult:
    """Measured numbers from one benchmark run."""

    lines: int
    flush_count: int
    live_render_count: int
    committed_block_count: int
    elapsed_sec: float
    lines_per_sec: float
    ms_per_flush: float

    def summary(self) -> str:
        return (
            f"lines={self.lines} elapsed={self.elapsed_sec * 1000:.1f}ms "
            f"throughput={self.lines_per_sec:,.0f} lines/sec "
            f"flushes={self.flush_count} "
            f"live_renders={self.live_render_count} "
            f"committed_blocks={self.committed_block_count} "
            f"per_flush={self.ms_per_flush:.3f}ms"
        )


async def run_bench(
    *,
    lines: int = 10_000,
    flush_interval: float = DEFAULT_FLUSH_INTERVAL,
    chunk_chars: int = 0,
    markdown_live: bool = False,
) -> BenchResult:
    """Stream ``lines`` simulated lines through the transcript, headlessly.

    ``chunk_chars`` (>0) splits each line into multiple sub-chunks to model a
    token-level stream; 0 means one chunk per line. Throughput counts *lines*.
    ``markdown_live`` (OQ1) renders the live block as Rich Markdown each flush.
    """

    app = TranscriptApp(flush_interval=flush_interval)
    async with app.run_test():
        controller = app.controller
        controller.markdown_live = markdown_live
        controller.begin_live()

        start = perf_counter()
        for i in range(lines):
            line = f"line {i}: the quick brown fox jumps over the lazy dog\n"
            if chunk_chars > 0:
                for off in range(0, len(line), chunk_chars):
                    controller.append_delta(line[off : off + chunk_chars])
            else:
                controller.append_delta(line)
            # Periodically let the coalescing timer drain so memory stays bounded
            # and we exercise the real batched-flush path rather than one giant
            # terminal flush.
            if i % 256 == 0:
                await controller.flush_now()
        await controller.flush_now()
        controller.finalize_live()
        elapsed = perf_counter() - start

    lines_per_sec = lines / elapsed if elapsed > 0 else 0.0
    ms_per_flush = (
        (elapsed * 1000.0) / controller.flush_count
        if controller.flush_count
        else 0.0
    )
    return BenchResult(
        lines=lines,
        flush_count=controller.flush_count,
        live_render_count=controller.live_render_count,
        committed_block_count=controller.committed_block_count,
        elapsed_sec=elapsed,
        lines_per_sec=lines_per_sec,
        ms_per_flush=ms_per_flush,
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Transcript render benchmark")
    parser.add_argument("--lines", type=int, default=10_000)
    parser.add_argument("--flush-interval", type=float, default=DEFAULT_FLUSH_INTERVAL)
    parser.add_argument(
        "--chunk-chars",
        type=int,
        default=0,
        help="split each line into N-char sub-chunks (0 = one chunk per line)",
    )
    parser.add_argument(
        "--markdown-live",
        action="store_true",
        help="render the live block as Rich Markdown each flush (OQ1)",
    )
    args = parser.parse_args(argv)
    result = asyncio.run(
        run_bench(
            lines=args.lines,
            flush_interval=args.flush_interval,
            chunk_chars=args.chunk_chars,
            markdown_live=args.markdown_live,
        )
    )
    print(result.summary())


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    main()
