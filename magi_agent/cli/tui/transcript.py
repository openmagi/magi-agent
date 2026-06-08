"""Streaming-transcript widget — the one architectural risk of the TUI stream.

Strategy (per design §6 item 1 / Ink-teardown §risks): avoid the classic Textual
trap of re-parsing the whole markdown transcript every frame. Two regions:

* **Finalized blocks** are appended to a Textual ``RichLog`` exactly ONCE on
  commit. They are immutable thereafter — never re-parsed, never re-rendered.
  ``RichLog`` owns native append-only scroll.
* **A single mutable live widget** (a ``Static``) holds the in-flight assistant
  block. Only this small, growing block re-renders as text deltas arrive. On
  finalize it is committed to the ``RichLog`` and the live widget is reset.

**Chunk coalescing.** Incoming deltas are buffered and rendered on a ~40ms
cadence (configurable) rather than once per chunk, so a burst of N chunks costs
one render. The cadence runs on a Textual ``set_interval`` timer (no blocking
sleeps); ``flush_now`` forces an immediate coalesced render for finalize/tests.

PR-E2 will fold real ``RuntimeEvent``s into this surface: ``token`` events ->
``append_delta`` on the live block; ``status``/``tool`` events -> a finalized
``commit_block``. PR-E1 proves the render strategy with simulated data only.
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.widgets import RichLog, Static

__all__ = ["TranscriptController", "TranscriptApp", "DEFAULT_FLUSH_INTERVAL"]

# Coalescing cadence: render batched deltas at most this often. 40ms sits in the
# 30-60ms band the design calls for (≈25 fps) — fast enough to feel live, slow
# enough that a token burst collapses into one render.
DEFAULT_FLUSH_INTERVAL = 0.04


class TranscriptController:
    """Owns the two render regions and the coalescing buffer.

    Separated from the ``App`` so the render strategy is unit-testable and so
    PR-E2 can drive it from the engine drain without an ``App`` subclass. The
    controller never imports the engine; its public surface is deltas in,
    committed blocks out.
    """

    def __init__(self, *, log: RichLog, live: Static) -> None:
        self._log = log
        self._live = live
        # Buffer of un-rendered deltas for the current live block.
        self._pending: list[str] = []
        # Full text of the current live block (already-rendered + pending).
        self._live_text: str = ""
        self._live_active: bool = False
        # Finalized, immutable block texts in commit order.
        self._committed: list[str] = []
        # Instrumentation (asserted by tests / reported by the bench).
        self.flush_count: int = 0
        self.live_render_count: int = 0
        self.committed_block_count: int = 0

    # -- live block lifecycle ------------------------------------------------
    def begin_live(self) -> None:
        """Open a fresh in-flight block. Resets the live widget."""

        self._pending.clear()
        self._live_text = ""
        self._live_active = True
        self._live.update("")

    def append_delta(self, text: str) -> None:
        """Queue a stream chunk. Does NOT render — coalesced by the flush."""

        if not self._live_active:
            self.begin_live()
        if text:
            self._pending.append(text)

    def flush(self) -> bool:
        """Render all buffered deltas into the live widget in ONE update.

        Returns ``True`` if a render happened (there was pending text). This is
        what the interval timer calls; ``flush_now`` is the async wrapper.
        """

        if not self._pending:
            return False
        self._live_text += "".join(self._pending)
        self._pending.clear()
        # Single re-render of the small live block — finalized blocks untouched.
        self._live.update(self._live_text)
        self.flush_count += 1
        self.live_render_count += 1
        return True

    async def flush_now(self) -> bool:
        """Async wrapper around :meth:`flush` for finalize/test call sites.

        Lets callers ``await`` a coalesced render at a deterministic point
        (e.g. just before finalize) without waiting on the interval timer.
        """

        return self.flush()

    def finalize_live(self) -> None:
        """Commit the live block to the immutable log and reset the live widget.

        Any un-flushed tail is folded in first. An empty block is a no-op.
        """

        self.flush()
        text = self._live_text
        self._live_active = False
        self._live_text = ""
        self._pending.clear()
        self._live.update("")
        if text:
            self.commit_block(text)

    @property
    def live_text(self) -> str:
        """The full text of the current live block (already-rendered + tail)."""

        return self._live_text + "".join(self._pending)

    def discard_live(self) -> None:
        """Reset the live block WITHOUT committing it.

        Used by callers (e.g. ``app._finalize_assistant_markdown``) that read
        ``live_text`` and commit a custom renderable themselves. ``finalize_live``
        remains the plain-text path; this is the "I'll commit it myself" path.
        """

        self._live_active = False
        self._live_text = ""
        self._pending.clear()
        self._live.update("")

    # -- finalized blocks ----------------------------------------------------
    def commit_block(self, text: str) -> None:
        """Append an immutable finalized block to the ``RichLog`` (renders once)."""

        self._committed.append(text)
        self.committed_block_count += 1
        self._log.write(text)

    def commit_rich(self, renderable: object, *, text: str = "") -> None:
        """Append a Rich renderable to the ``RichLog`` (renders once).

        Mirrors :meth:`commit_block`'s finalize-first ordering contract: callers
        finalize the in-flight live block BEFORE committing a tool render so
        streamed assistant text lands first. The plain ``text`` fallback (the
        displayed ``RenderNode.text``) is recorded in the committed snapshot so
        the search-fidelity / parity assertions can see exactly what was shown.
        """

        self._committed.append(text)
        self.committed_block_count += 1
        self._log.write(renderable)

    def committed_blocks_snapshot(self) -> tuple[str, ...]:
        """Immutable view of finalized block texts in commit order."""

        return tuple(self._committed)


class TranscriptApp(App[None]):
    """Minimal Textual app hosting the transcript regions (RichLog + live Static).

    Created for the spike + headless benchmark; the production REPL (Stream F)
    will embed the same two widgets in the full layout.
    """

    CSS = """
    RichLog { height: 1fr; }
    #live { height: auto; }
    """

    def __init__(self, *, flush_interval: float = DEFAULT_FLUSH_INTERVAL) -> None:
        super().__init__()
        self._flush_interval = max(0.0, float(flush_interval))
        self._log: RichLog | None = None
        self._live: Static | None = None
        self._controller: TranscriptController | None = None

    def compose(self) -> ComposeResult:
        self._log = RichLog(wrap=True, markup=False, id="transcript")
        self._live = Static("", id="live")
        yield self._log
        yield self._live

    def on_mount(self) -> None:
        assert self._log is not None and self._live is not None
        self._controller = TranscriptController(log=self._log, live=self._live)
        # Coalescing timer: drain the buffer on a fixed cadence off the UI loop.
        self.set_interval(self._flush_interval, self._on_flush_tick)

    def _on_flush_tick(self) -> None:
        if self._controller is not None:
            self._controller.flush()

    @property
    def controller(self) -> TranscriptController:
        if self._controller is None:  # pragma: no cover - guarded by on_mount
            raise RuntimeError("controller not ready; app not mounted")
        return self._controller
