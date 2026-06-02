"""Tests for the PR-E2 prefix autocomplete router.

Pure-logic tests (no event loop): the router is unit-testable without Textual.
We inject a FAKE ``CommandRegistry`` and fake file/channel providers.
"""

from __future__ import annotations

from openmagi_core_agent.cli.contracts import (
    Command,
    CommandSurface,
    LocalCommand,
)
from openmagi_core_agent.cli.tui.autocomplete import (
    AutocompleteRouter,
    CallableProvider,
    precursor_token,
)

TUI = CommandSurface(tui=True, headless=False)


class FakeRegistry:
    """A minimal ``CommandRegistry`` returning a fixed command list for TUI."""

    def __init__(self, names: list[str]) -> None:
        self._commands: list[Command] = [
            LocalCommand(name=name, surface=TUI) for name in names
        ]

    def lookup(self, name: str) -> Command | None:
        for command in self._commands:
            if getattr(command, "name", None) == name:
                return command
        return None

    def list_for(self, surface: CommandSurface) -> list[Command]:
        _ = surface
        return list(self._commands)


# ---------------------------------------------------------------------------
# precursor_token
# ---------------------------------------------------------------------------
def test_precursor_token_extracts_active_token() -> None:
    assert precursor_token("hello /comp") == "/comp"
    assert precursor_token("/comp") == "/comp"
    assert precursor_token("hello ") == ""
    assert precursor_token("") == ""
    assert precursor_token("@fi") == "@fi"


# ---------------------------------------------------------------------------
# slash -> commands from the registry
# ---------------------------------------------------------------------------
def test_slash_lists_commands_from_registry() -> None:
    registry = FakeRegistry(["compact", "reset", "status"])
    router = AutocompleteRouter(commands=registry)
    request = router.route("/")
    assert request.trigger == "/"
    values = [c.value for c in request.results]
    assert "/compact" in values
    assert "/reset" in values
    assert "/status" in values


def test_slash_fuzzy_ranks_and_filters() -> None:
    registry = FakeRegistry(["compact", "reset", "status", "superpowers"])
    router = AutocompleteRouter(commands=registry)
    request = router.route("/comp")
    # The closest match ranks first.
    assert request.results[0].value == "/compact"
    # Ghost text is the un-typed suffix.
    assert request.results[0].ghost == "act"


def test_result_cap_is_enforced() -> None:
    registry = FakeRegistry([f"cmd{i}" for i in range(50)])
    router = AutocompleteRouter(commands=registry, result_cap=15)
    request = router.route("/cmd")
    assert len(request.results) <= 15


# ---------------------------------------------------------------------------
# staleness guard
# ---------------------------------------------------------------------------
def test_staleness_guard_discards_stale_request() -> None:
    registry = FakeRegistry(["compact"])
    router = AutocompleteRouter(commands=registry)
    stale = router.new_request("/co")
    # A newer request supersedes the older one.
    fresh = router.new_request("/com")
    assert router.is_current(fresh)
    assert not router.is_current(stale)


# ---------------------------------------------------------------------------
# @ files / # channels providers
# ---------------------------------------------------------------------------
def test_at_routes_to_file_provider() -> None:
    registry = FakeRegistry([])
    files = CallableProvider(lambda frag: ["readme.md", "main.py", "agent.py"])
    router = AutocompleteRouter(commands=registry, file_provider=files)
    request = router.route("@age")
    assert request.trigger == "@"
    assert request.results[0].value == "@agent.py"


def test_hash_routes_to_channel_provider() -> None:
    registry = FakeRegistry([])
    channels = CallableProvider(lambda frag: ["general", "random", "dev"])
    router = AutocompleteRouter(commands=registry, channel_provider=channels)
    request = router.route("#gen")
    assert request.trigger == "#"
    assert request.results[0].value == "#general"


def test_missing_provider_yields_no_results() -> None:
    registry = FakeRegistry([])
    router = AutocompleteRouter(commands=registry)  # no @ / # providers
    assert router.route("@foo").results == []
    assert router.route("#foo").results == []


def test_non_trigger_token_yields_no_results() -> None:
    registry = FakeRegistry(["compact"])
    router = AutocompleteRouter(commands=registry)
    assert router.route("hello world").results == []
