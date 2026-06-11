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

from dataclasses import dataclass

from textual.app import App, ComposeResult
from textual.widgets import RichLog, Static

__all__ = ["TranscriptController", "TranscriptApp", "DEFAULT_FLUSH_INTERVAL"]


@dataclass
class _CoalesceHandle:
    """Update handle for a coalesced one-line block.

    Returned by :meth:`TranscriptController.commit_coalesced`; carries the
    backing widget (``None`` on the legacy ``RichLog`` backing) and the snapshot
    index so streaming deltas update the same line in place. Generic across
    consumers — thinking (PR4.2) and subagent (PR4.3) rendering both use it.
    """

    controller: "TranscriptController"
    index: int
    widget: object | None

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

    def __init__(
        self,
        *,
        live: Static,
        log: "RichLog | None" = None,
        view: "object | None" = None,
    ) -> None:
        if log is None and view is None:  # pragma: no cover - construction guard
            raise ValueError("TranscriptController needs a `log` or a `view`")
        self._log = log
        # TranscriptView when on the widget-list backing (PR0.3). Typed as
        # ``object`` to avoid importing the leaf ``widgets`` package here (it
        # imports lazily inside ``_emit`` to dodge a circular import).
        self._view = view
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
        # OQ1: when True, the coalesced live block is rendered as a Rich
        # Markdown renderable (headings/lists/fenced code) instead of plain
        # text. Default False so the bench/legacy paths stay text-only.
        self.markdown_live: bool = False
        # Last renderable handed to the live widget. Test-observation seam:
        # Textual's ``Static`` doesn't expose the last renderable post-update, so
        # tests read this to assert OQ1 markdown parity. Not cruft — keep it.
        self.last_live_renderable: object | None = None

    # -- live block lifecycle ------------------------------------------------
    def begin_live(self) -> None:
        """Open a fresh in-flight block. Resets the live widget."""

        self._pending.clear()
        self._live_text = ""
        self._live_active = True
        self.last_live_renderable = None
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
        renderable = self._live_renderable(self._live_text)
        self.last_live_renderable = renderable
        self._live.update(renderable)
        self.flush_count += 1
        self.live_render_count += 1
        return True

    def _live_renderable(self, text: str) -> object:
        """The live-block renderable for ``text``.

        Markdown when ``markdown_live`` is on (OQ1: Rich renderable in the live
        block, re-rendered cleanly each coalesced flush), else plain text. The
        import is lazy so the headless bench has no markdown dependency on the
        hot path unless it opts in.
        """

        if not self.markdown_live:
            return text
        from magi_agent.cli.tui.render.markdown import render_markdown  # noqa: PLC0415

        return render_markdown(text)

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
    def _emit(self, widget_or_renderable: object, *, as_status: bool) -> None:
        """Append a finalized item to the active backing.

        On the ``TranscriptView`` backing (PR0.3), wrap a plain string in a
        ``StatusLine`` and a Rich renderable in a ``Static``, then ``add_block``.
        On the legacy ``RichLog`` backing, ``write`` it directly. Imports are
        lazy so ``transcript`` stays free of a hard ``widgets`` dependency (the
        ``widgets`` package is a leaf; importing it at module scope would risk a
        circular import).
        """

        if self._view is not None:
            from textual.widgets import Static  # noqa: PLC0415

            from magi_agent.cli.tui.widgets.message import (  # noqa: PLC0415
                StatusLine,
            )

            # Phase note: user/assistant blocks are currently wrapped as
            # generic ``StatusLine``/``Static`` here. Type-aware mapping onto the
            # dedicated ``UserMessage``/``AssistantMessage`` widgets (already in
            # the ``widgets.message`` module) lands in a later phase — those two
            # classes are NOT dead code, just not wired into this seam yet.
            if as_status and isinstance(widget_or_renderable, str):
                widget = StatusLine(widget_or_renderable)
            else:
                widget = Static(widget_or_renderable)
            self._view.add_block(widget)
            return
        assert self._log is not None
        self._log.write(widget_or_renderable)

    def commit_block(self, text: str) -> None:
        """Append an immutable finalized block (renders once).

        Routes through :meth:`_emit`: a ``StatusLine`` on the widget-list
        backing, ``RichLog.write`` on legacy. Public API + counters unchanged.
        """

        self._committed.append(text)
        self.committed_block_count += 1
        self._emit(text, as_status=True)

    def commit_rich(self, renderable: object, *, text: str = "") -> None:
        """Append a Rich renderable as a finalized block (renders once).

        Mirrors :meth:`commit_block`'s finalize-first ordering contract: callers
        finalize the in-flight live block BEFORE committing a tool render so
        streamed assistant text lands first. The plain ``text`` fallback (the
        displayed ``RenderNode.text``) is recorded in the committed snapshot so
        the search-fidelity / parity assertions can see exactly what was shown.
        Routes through :meth:`_emit`: a ``Static`` on the widget-list backing,
        ``RichLog.write`` on legacy.
        """

        self._committed.append(text)
        self.committed_block_count += 1
        self._emit(renderable, as_status=False)

    def commit_coalesced(self, renderable: object, *, text: str = "") -> object:
        """Commit a DIM one-line block and return an update handle.

        Generic in-place one-line coalescing seam (nothing thinking-specific):
        thinking (PR4.2) and subagent (PR4.3) rendering both drive it. Mirrors
        :meth:`commit_rich` (finalized Rich renderable + recorded search text)
        but returns the backing widget AND the snapshot index so streaming
        deltas can be COALESCED via :meth:`update_coalesced` rather than
        flooding the transcript with one block per delta. On the legacy
        ``RichLog`` backing (no in-place widget update) the handle widget is
        ``None`` and a later :meth:`update_coalesced` no-ops on the widget while
        still patching the recorded snapshot text for search fidelity.
        """

        index = len(self._committed)
        self._committed.append(text)
        self.committed_block_count += 1
        widget: object | None = None
        if self._view is not None:
            from textual.widgets import Static  # noqa: PLC0415

            widget = Static(renderable)
            self._view.add_block(widget)
        else:
            assert self._log is not None
            self._log.write(renderable)
        return _CoalesceHandle(controller=self, index=index, widget=widget)

    def update_coalesced(
        self, handle: object, renderable: object, *, text: str = ""
    ) -> None:
        """Update a previously :meth:`commit_coalesced` block in place.

        Patches both the live widget (so the single line repaints with the
        latest preview) and the recorded snapshot entry (so search fidelity
        reflects what is shown). Unknown/legacy handles update only the snapshot
        text.
        """

        if not isinstance(handle, _CoalesceHandle):
            return
        if 0 <= handle.index < len(self._committed):
            self._committed[handle.index] = text
        widget = handle.widget
        if widget is not None:
            update = getattr(widget, "update", None)
            if callable(update):
                update(renderable)

    def commit_tool(self, card: object, *, text: str = "") -> None:
        """Append a ``ToolCard`` (Collapsible) as a finalized block (PR0.4).

        Mounts the card into the ``TranscriptView`` (the widget-list backing).
        On the legacy ``RichLog`` backing — which cannot host a ``Collapsible`` —
        the header ``text`` is written as a plain block so the seam degrades
        gracefully. Either way the displayed ``text`` is recorded in the
        committed snapshot for search fidelity.
        """

        self._committed.append(text)
        self.committed_block_count += 1
        # NB: this deliberately does NOT route through ``_emit`` like
        # ``commit_block``/``commit_rich`` do — a ``ToolCard`` is already a widget,
        # and wrapping it in a ``Static`` (as ``_emit`` does) would break the
        # ``Collapsible``. Hence this third, widget-aware mount call-site.
        if self._view is not None:
            self._view.add_block(card)
            return
        assert self._log is not None
        self._log.write(text)

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
