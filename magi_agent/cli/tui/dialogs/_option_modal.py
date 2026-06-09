"""Shared ``OptionList`` modal base for the Magi TUI dialogs (PR2.5 refactor).

The model picker (PR2.3) and session list (PR2.4) dialogs are near-identical
``ModalScreen[str]`` + ``OptionList`` screens: escape cancels, selecting an
option dismisses with its id, and the list is focused on mount so Up/Down +
Enter work without a click. Per the PR2.3/2.4 reviewer directive (rule of
three), that common skeleton is extracted here once the third dialog lands.

Subclasses keep their own ``compose`` (title + options + per-dialog empty
state) and may override :meth:`_after_mount` for per-dialog mount behavior
(e.g. pre-highlighting the current row). Only the shared skeleton lives here;
no public behavior changes for the existing dialogs.
"""

from __future__ import annotations

from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import OptionList

__all__ = ["OptionListModal"]


class OptionListModal(ModalScreen[str]):
    """Base for ``OptionList``-backed modals: escape cancels, select dismisses.

    Owns the common ``BINDINGS`` (escape -> cancel), the
    ``on_option_list_option_selected`` -> ``dismiss(option.id)`` handler, the
    ``action_cancel`` -> ``dismiss(None)`` handler, and focus-on-mount. The
    concrete dialog supplies ``compose`` and, if needed, ``_after_mount``.
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def on_mount(self) -> None:
        # Focus the list so Up/Down + Enter work without a click. (An empty
        # OptionList is still focusable; Enter on it is a no-op.)
        self.query_one(OptionList).focus()
        self._after_mount()

    def _after_mount(self) -> None:
        """Hook for per-dialog mount behavior (default: nothing)."""

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        event.stop()
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)
