"""Model picker dialog for the Magi TUI (PR2.3).

A ``ModalScreen[str]`` listing candidate models in an ``OptionList``. Dismisses
with the chosen model id (the option ``id``), or ``None`` on escape. The model
list is supplied by the caller, sourced from ``cli/providers.py`` defaults via
:func:`model_choices`.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

__all__ = ["ModelPickerDialog", "model_choices"]


def model_choices(current: str | None = None) -> list[str]:
    """Candidate model ids from providers.py defaults, current first if known.

    Reads the per-provider default models; the currently active model (if any)
    is placed first so it is pre-highlighted. No network, no key required.
    """

    from magi_agent.cli.providers import (  # noqa: PLC0415
        SUPPORTED_PROVIDERS,
        _DEFAULT_MODEL,
    )

    ids: list[str] = []
    if current:
        ids.append(current)
    for provider in SUPPORTED_PROVIDERS:
        model = _DEFAULT_MODEL[provider]
        if model not in ids:
            ids.append(model)
    return ids


class ModelPickerDialog(ModalScreen[str]):
    """Pick a model; dismiss with its id (or None on escape)."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    CSS = """
    ModelPickerDialog { align: center middle; }
    #model-dialog {
        width: 60;
        height: auto;
        max-height: 80%;
        padding: 1 2;
        background: $panel;
        border: thick $accent;
    }
    #model-dialog OptionList { height: auto; max-height: 16; }
    """

    def __init__(self, *, models: list[str], current: str | None = None) -> None:
        super().__init__()
        self._models = list(models)
        self._current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="model-dialog"):
            yield Static("Select a model", id="model-title")
            options = [Option(m, id=m) for m in self._models]
            yield OptionList(*options, id="model-options")

    def on_mount(self) -> None:
        option_list = self.query_one(OptionList)
        if self._current in self._models:
            option_list.highlighted = self._models.index(self._current)
        option_list.focus()

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        event.stop()
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)
