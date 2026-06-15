"""Bottom status footer for the Magi TUI (PR3.1).

``StatusFooter`` is a one-line ``Static`` docked below the prompt that shows the
DYNAMIC per-turn status — model · cwd · turn state · token usage · elapsed — so
the topbar can stay a static identity row (``● Magi  model  cwd  [mode]``) that
never re-renders. The model/cwd fields are repeated here for a self-contained
status line, but the live fields (state/tokens/elapsed) exist ONLY in the footer.

The widget exposes small imperative setters (``set_model`` / ``set_cwd`` /
``set_state`` / ``set_tokens`` / ``set_elapsed``) that JUST assign Textual
``reactive`` attributes; the corresponding ``watch_<field>`` is what repaints
just this widget. The App owns *when* to call the setters (folded from engine
events); the widget owns *how* the line looks. ``elapsed`` repaints only when its
whole-second value changes (the line renders elapsed at 1s granularity), so a
25Hz elapsed tick doesn't repaint ~24/25 identical frames.
"""

from __future__ import annotations

from textual.reactive import reactive
from textual.widgets import Static

__all__ = ["StatusFooter", "TurnState"]

# The turn states the footer surfaces. The finished states mirror
# ``EngineResult.terminal`` values (``completed`` / ``aborted`` / ``error`` /
# ``max_turns``); ``idle`` is the pre-turn state and ``running`` is in-flight.
TurnState = str  # one of: "idle" | "running" | "aborted" | "completed" | "error"


class StatusFooter(Static):
    """One-line dynamic status footer: model · cwd · state · tokens · elapsed.

    All five display fields are Textual ``reactive`` attributes; each has a
    ``watch_<field>`` that repaints just this widget. The public setters assign
    the reactive and let the watcher do the repaint — they never call
    ``_repaint`` directly (one repaint pattern, not two).
    """

    model: reactive[str] = reactive("no model")
    cwd: reactive[str] = reactive("")
    state: reactive[str] = reactive("idle")
    tokens: reactive[int] = reactive(0)
    elapsed: reactive[float] = reactive(0.0)
    # The current-activity word (e.g. "Bash" or "Bash · no output 12s"). The App
    # composes the FULLY-rendered string (tool name + optional stall suffix at
    # integer-second granularity) and assigns it via ``set_activity``; the footer
    # is a dumb renderer that only appends it while ``state == "running"``.
    activity: reactive[str] = reactive("")

    def __init__(
        self,
        *,
        model: str | None = None,
        cwd: str = "",
        id: str | None = None,  # noqa: A002 - Textual widget convention
    ) -> None:
        super().__init__("", id=id)
        # Seed the reactives (no watcher fires pre-mount; on_mount paints once).
        self.set_reactive(StatusFooter.model, model or "no model")
        self.set_reactive(StatusFooter.cwd, cwd)

    def on_mount(self) -> None:
        self._repaint()

    # -- imperative setters: assign the reactive; the watcher repaints -------
    def set_model(self, model: str | None) -> None:
        self.model = model or "no model"

    def set_cwd(self, cwd: str) -> None:
        self.cwd = cwd

    def set_state(self, state: str) -> None:
        self.state = state

    def set_tokens(self, tokens: int) -> None:
        self.tokens = max(0, int(tokens))

    def set_elapsed(self, seconds: float) -> None:
        self.elapsed = max(0.0, float(seconds))

    def set_activity(self, text: str) -> None:
        # Assign only — NEVER call ``_repaint`` here; ``watch_activity`` repaints
        # (one repaint pattern, not two). This relies on Textual's reactive
        # equality short-circuit: assigning the SAME composed string does not
        # fire ``watch_activity``, so the ~25Hz flush tick that re-asserts an
        # unchanged integer-second activity string repaints at most once/second.
        self.activity = text

    # -- reactive watchers (the ONE repaint path) ---------------------------
    def watch_model(self, _old: str, _new: str) -> None:
        self._repaint()

    def watch_cwd(self, _old: str, _new: str) -> None:
        self._repaint()

    def watch_state(self, _old: str, _new: str) -> None:
        self._repaint()

    def watch_tokens(self, _old: int, _new: int) -> None:
        self._repaint()

    def watch_elapsed(self, _old: float, _new: float) -> None:
        # The line renders elapsed at 1-second granularity, so only repaint when
        # the whole-second value changes. The reactive value still updates every
        # tick (cheap), but the ~24/25 sub-second ticks no longer repaint.
        if int(_old) != int(_new):
            self._repaint()

    def watch_activity(self, _old: str, _new: str) -> None:
        # Repaints only when the composed activity string actually changes —
        # Textual short-circuits ``watch_`` for equal new == old, which is the
        # load-bearing throttle that collapses identical integer-second stall
        # strings re-asserted on every flush tick into a single repaint/second.
        self._repaint()

    # -- rendering -----------------------------------------------------------
    def status_text(self) -> str:
        """The exact text the footer displays (asserted by tests)."""

        # Append the current-activity word ONLY while running and non-empty, so
        # idle/terminal text stays byte-identical to the historical five-field
        # format (no stray ` · ` ever leaks in). The App composes ``activity``
        # (tool name + optional stall suffix); the footer just renders it.
        state = self.state
        if state == "running" and self.activity:
            state = f"{state} · {self.activity}"
        return (
            f"{self.model}   {self.cwd}   "
            f"{state}   {self.tokens:,} tok   {int(self.elapsed)}s"
        )

    def _repaint(self) -> None:
        # ``update`` is a cheap single-widget refresh (Textual only re-renders
        # this Static, never the transcript).
        self.update(self.status_text())
