"""Bottom status footer for the Magi TUI (PR3.1).

``StatusFooter`` is a one-line ``Static`` docked below the prompt that shows the
DYNAMIC per-turn status — model · cwd · turn state · token usage · elapsed — so
the topbar can stay a static identity row (``● Magi  model  cwd  [mode]``) that
never re-renders. The model/cwd fields are repeated here for a self-contained
status line, but the live fields (state/tokens/elapsed) exist ONLY in the footer.

The widget exposes small imperative setters (``set_state`` / ``set_tokens`` /
``set_elapsed``) backed by Textual ``reactive`` attributes; any setter triggers a
single ``refresh`` of just this widget. The App owns *when* to call them (folded
from engine events); the widget owns *how* the line looks.
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
    """One-line dynamic status footer: model · cwd · state · tokens · elapsed."""

    state: reactive[str] = reactive("idle")
    tokens: reactive[int] = reactive(0)
    elapsed: reactive[float] = reactive(0.0)

    def __init__(
        self,
        *,
        model: str | None = None,
        cwd: str = "",
        id: str | None = None,  # noqa: A002 - Textual widget convention
    ) -> None:
        super().__init__("", id=id)
        self._model = model or "no model"
        self._cwd = cwd

    def on_mount(self) -> None:
        self._repaint()

    # -- imperative setters (called by the App from folded events) ----------
    def set_model(self, model: str | None) -> None:
        self._model = model or "no model"
        self._repaint()

    def set_cwd(self, cwd: str) -> None:
        self._cwd = cwd
        self._repaint()

    def set_state(self, state: str) -> None:
        self.state = state  # reactive assignment -> watch_state -> repaint

    def set_tokens(self, tokens: int) -> None:
        self.tokens = max(0, int(tokens))

    def set_elapsed(self, seconds: float) -> None:
        self.elapsed = max(0.0, float(seconds))

    # -- reactive watchers ---------------------------------------------------
    def watch_state(self, _old: str, _new: str) -> None:
        self._repaint()

    def watch_tokens(self, _old: int, _new: int) -> None:
        self._repaint()

    def watch_elapsed(self, _old: float, _new: float) -> None:
        self._repaint()

    # -- rendering -----------------------------------------------------------
    def status_text(self) -> str:
        """The exact text the footer displays (asserted by tests)."""

        return (
            f"{self._model}   {self._cwd}   "
            f"{self.state}   {self.tokens:,} tok   {int(self.elapsed)}s"
        )

    def _repaint(self) -> None:
        # ``update`` is a cheap single-widget refresh (Textual only re-renders
        # this Static, never the transcript).
        self.update(self.status_text())
