"""Session list dialog for the Magi TUI (PR2.4).

A ``ModalScreen[str]`` listing prior sessions; dismisses with the chosen session
ref (or ``None`` on escape). Resume policy (OQ3): **metadata + a fresh turn** —
the app starts a NEW turn whose prompt references the resumed session, NOT a
transcript replay. Session entries are supplied by the caller, sourced from the
``session_history.py`` substrate (``cli/session_log.py`` DAG) when a controller
is wired on ``ctx.runtime``; absent a controller the list is empty.

The dialog shares the ``OptionListModal`` base with
:class:`~magi_agent.cli.tui.dialogs.model.ModelPickerDialog`: ``ModalScreen`` +
``OptionList`` + an ``escape`` binding that cancels + select -> dismiss(ref) +
focus-on-mount. The empty case shows a ``Static`` placeholder ("No prior
sessions.") and an empty ``OptionList`` so the caller can always
``query_one(OptionList)`` without a crash.
"""

from __future__ import annotations

from dataclasses import dataclass

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from magi_agent.cli.tui.dialogs._option_modal import OptionListModal

__all__ = ["SessionEntry", "SessionListDialog", "session_entries"]


@dataclass(frozen=True)
class SessionEntry:
    """One resumable session row: stable ref + display label + updated stamp."""

    ref: str
    label: str
    updated: str = ""


def session_entries(runtime: object | None) -> list[SessionEntry]:
    """Best-effort prior-session list from a wired controller on ``runtime``.

    Looks for ``runtime.session_lister`` exposing ``recent() ->
    list[SessionEntry-like]`` (objects with ``ref`` / ``label`` / ``updated``).
    Absent (default-off, no controller wired) returns an empty list — matching
    the ``session_history.py`` "scaffold + gate" posture. Never raises; a buggy
    or unwired controller yields an empty list, so the dialog degrades to its
    empty-state placeholder rather than crashing.
    """

    if runtime is None:
        return []
    lister = getattr(runtime, "session_lister", None)
    recent = getattr(lister, "recent", None)
    if not callable(recent):
        return []
    try:
        rows = recent()
    except Exception:
        return []
    out: list[SessionEntry] = []
    for row in rows or []:
        ref = getattr(row, "ref", None)
        if not isinstance(ref, str) or not ref:
            continue
        out.append(
            SessionEntry(
                ref=ref,
                label=str(getattr(row, "label", "") or ref),
                updated=str(getattr(row, "updated", "") or ""),
            )
        )
    return out


class SessionListDialog(OptionListModal):
    """List prior sessions; dismiss with the chosen ref (or None on escape).

    Inherits the shared ``OptionList`` modal skeleton (escape -> cancel, select
    -> dismiss(ref), focus-on-mount) from :class:`OptionListModal`; only the
    per-dialog ``compose`` + empty-state placeholder live here.
    """

    CSS = """
    SessionListDialog { align: center middle; }
    #session-dialog {
        width: 72;
        height: auto;
        max-height: 80%;
        padding: 1 2;
        background: $panel;
        border: thick $accent;
    }
    #session-dialog OptionList { height: auto; max-height: 18; }
    #session-empty { color: $text-muted; }
    """

    def __init__(self, *, sessions: list[SessionEntry]) -> None:
        super().__init__()
        self._sessions = list(sessions)

    def compose(self) -> ComposeResult:
        with Vertical(id="session-dialog"):
            yield Static("Resume a session", id="session-title")
            if not self._sessions:
                # Empty state: a placeholder line + an EMPTY OptionList so
                # callers can always query_one(OptionList) (option_count == 0).
                yield Static("No prior sessions.", id="session-empty")
                yield OptionList(id="session-options")
            else:
                options = [
                    Option(self._row_text(s), id=s.ref) for s in self._sessions
                ]
                yield OptionList(*options, id="session-options")

    @staticmethod
    def _row_text(entry: SessionEntry) -> str:
        """Human-readable row: label, then the updated stamp when present."""

        stamp = f"  ({entry.updated})" if entry.updated else ""
        return f"{entry.label}{stamp}"

