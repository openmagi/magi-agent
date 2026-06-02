"""Prefix autocomplete router for the Magi TUI input (PR-E2).

A small, framework-light router over the *pre-cursor* text slice. The first
character of the active "token" (the run of non-space chars ending at the
cursor) selects a completion *source*:

* ``/`` -> slash commands, sourced from the injected
  :class:`~magi_agent.cli.contracts.CommandRegistry` filtered to the TUI
  surface (ghost text).
* ``@`` -> files / agents (injectable provider).
* ``#`` -> channels (injectable provider).

Ranking uses ``rapidfuzz`` (the ONLY place ``rapidfuzz`` is imported), results
are capped (~15), and a monotonically increasing *request token* implements a
staleness guard: a slow async completion computed for an out-of-date input slice
is discarded by :meth:`AutocompleteRouter.is_current`. The Textual overlay that
renders the menu lives in :mod:`app`; this module is pure logic so it is unit
testable without an event loop.

The completion *sources* are abstracted behind small callables / providers
(:class:`CompletionProvider`) so tests inject fakes — no filesystem scan is
hardcoded as the only path.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from rapidfuzz import fuzz

from magi_agent.cli.contracts import (
    Command,
    CommandRegistry,
    CommandSurface,
)

__all__ = [
    "Completion",
    "CompletionProvider",
    "CallableProvider",
    "AutocompleteRequest",
    "AutocompleteRouter",
    "DEFAULT_RESULT_CAP",
    "TUI_SURFACE",
    "precursor_token",
]

# Cap on how many ranked results are surfaced to the overlay (design §7: ~15).
DEFAULT_RESULT_CAP = 15

# The surface this router queries the CommandRegistry with.
TUI_SURFACE = CommandSurface(tui=True, headless=False)


@dataclass(frozen=True)
class Completion:
    """A single ranked completion candidate.

    ``value`` is the full token to substitute (e.g. ``"/compact"``); ``ghost`` is
    the dim suffix appended after the user's current input to preview the
    completion (``value`` minus the already-typed prefix). ``label`` is the menu
    display text (defaults to ``value``).
    """

    value: str
    ghost: str = ""
    label: str = ""
    score: float = 0.0

    def __post_init__(self) -> None:
        if not self.label:
            object.__setattr__(self, "label", self.value)


@runtime_checkable
class CompletionProvider(Protocol):
    """Supplies raw candidate *values* for a given query fragment.

    The router fuzzy-ranks whatever this returns; a provider may itself ignore
    the fragment and return its full universe (the router still ranks/caps).
    """

    def candidates(self, fragment: str) -> Sequence[str]: ...


@dataclass
class CallableProvider:
    """Adapts a plain ``Callable[[str], Iterable[str]]`` into a provider."""

    fn: Callable[[str], Iterable[str]]

    def candidates(self, fragment: str) -> Sequence[str]:
        return list(self.fn(fragment))


@dataclass
class AutocompleteRequest:
    """An in-flight completion computation tagged with a staleness token."""

    token: int
    trigger: str
    fragment: str
    precursor: str = ""
    results: list[Completion] = field(default_factory=list)


def precursor_token(precursor: str) -> str:
    """Return the active token: the run of non-space chars ending at the cursor.

    The cursor is the END of ``precursor`` (the text left of the caret). The
    active token is everything back to the last whitespace. An empty string
    means there is no token to complete.
    """

    if not precursor:
        return ""
    tail = precursor.rsplit(None, 1)
    # ``rsplit(None, 1)`` collapses the trailing token; if ``precursor`` ends in
    # whitespace the token is empty.
    if precursor[-1].isspace():
        return ""
    return tail[-1]


class AutocompleteRouter:
    """Routes a pre-cursor slice to a completion source and ranks results.

    Parameters
    ----------
    commands:
        The injected :class:`CommandRegistry`; ``/`` completions come from
        ``commands.list_for(TUI_SURFACE)``.
    file_provider / channel_provider:
        Optional :class:`CompletionProvider`s (or bare callables) for ``@`` and
        ``#``. When absent that trigger yields no completions (no hardcoded
        filesystem scan).
    result_cap:
        Maximum number of ranked completions returned.
    """

    def __init__(
        self,
        *,
        commands: CommandRegistry,
        file_provider: CompletionProvider | Callable[[str], Iterable[str]] | None = None,
        channel_provider: (
            CompletionProvider | Callable[[str], Iterable[str]] | None
        ) = None,
        result_cap: int = DEFAULT_RESULT_CAP,
    ) -> None:
        self._commands = commands
        self._file_provider = _coerce_provider(file_provider)
        self._channel_provider = _coerce_provider(channel_provider)
        self._cap = max(1, int(result_cap))
        # Monotonic request token: each new input slice bumps it; a completion
        # tagged with an older token is stale (see is_current / new_request).
        self._token = 0

    # -- staleness guard ----------------------------------------------------
    def new_request(self, precursor: str) -> AutocompleteRequest:
        """Open a request for ``precursor``, bumping the staleness token."""

        self._token += 1
        token = precursor_token(precursor)
        trigger = token[:1]
        fragment = token[1:] if token else ""
        return AutocompleteRequest(
            token=self._token,
            trigger=trigger,
            fragment=fragment,
            precursor=precursor,
        )

    def is_current(self, request: AutocompleteRequest) -> bool:
        """True iff ``request`` is the most recent one opened."""

        return request.token == self._token

    # -- routing ------------------------------------------------------------
    def route(self, precursor: str) -> AutocompleteRequest:
        """Compute completions for ``precursor`` synchronously.

        Returns a fresh :class:`AutocompleteRequest` carrying the ranked,
        capped ``results``. Callers running this off the UI thread should
        re-check :meth:`is_current` before applying the results.
        """

        request = self.new_request(precursor)
        request.results = self._complete(request.trigger, request.fragment)
        return request

    def _complete(self, trigger: str, fragment: str) -> list[Completion]:
        if trigger == "/":
            return self._rank(self._command_values(), trigger, fragment)
        if trigger == "@":
            return self._rank(
                self._provider_values(self._file_provider, fragment),
                trigger,
                fragment,
            )
        if trigger == "#":
            return self._rank(
                self._provider_values(self._channel_provider, fragment),
                trigger,
                fragment,
            )
        return []

    # -- sources ------------------------------------------------------------
    def _command_values(self) -> list[str]:
        commands: list[Command] = self._commands.list_for(TUI_SURFACE)
        values: list[str] = []
        for command in commands:
            name = getattr(command, "name", None)
            if isinstance(name, str) and name:
                values.append(name)
        return values

    @staticmethod
    def _provider_values(
        provider: CompletionProvider | None, fragment: str
    ) -> list[str]:
        if provider is None:
            return []
        return [str(value) for value in provider.candidates(fragment)]

    # -- ranking ------------------------------------------------------------
    def _rank(
        self, values: Iterable[str], trigger: str, fragment: str
    ) -> list[Completion]:
        scored: list[tuple[float, str]] = []
        for value in values:
            score = 100.0 if not fragment else fuzz.WRatio(fragment, value)
            if fragment and score <= 0.0:
                continue
            scored.append((score, value))
        # Highest score first; ties broken by lexicographic order for stability.
        scored.sort(key=lambda item: (-item[0], item[1]))
        out: list[Completion] = []
        for score, value in scored[: self._cap]:
            full = f"{trigger}{value}"
            typed = f"{trigger}{fragment}"
            ghost = full[len(typed):] if full.startswith(typed) else full
            out.append(
                Completion(value=full, ghost=ghost, label=full, score=float(score))
            )
        return out


def _coerce_provider(
    provider: CompletionProvider | Callable[[str], Iterable[str]] | None,
) -> CompletionProvider | None:
    if provider is None:
        return None
    if isinstance(provider, CompletionProvider):
        return provider
    return CallableProvider(provider)
