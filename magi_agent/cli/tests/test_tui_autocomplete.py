"""Tests for the PR-E2 prefix autocomplete router.

Pure-logic tests (no event loop): the router is unit-testable without Textual.
We inject a FAKE ``CommandRegistry`` and fake file/channel providers.
"""

from __future__ import annotations

from magi_agent.cli.contracts import (
    Command,
    CommandSurface,
    LocalCommand,
)
from magi_agent.cli.tui.autocomplete import (
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


def test_module_docstring_does_not_advertise_channels() -> None:
    """The local CLI has no channel concept; the docstring must not promise one."""
    import magi_agent.cli.tui.autocomplete as ac

    doc = ac.__doc__ or ""
    assert "-> channels" not in doc
    # The seam is retained but documented as intentionally unbacked locally.
    assert "unbacked" in doc


def test_non_trigger_token_yields_no_results() -> None:
    registry = FakeRegistry(["compact"])
    router = AutocompleteRouter(commands=registry)
    assert router.route("hello world").results == []


# ---------------------------------------------------------------------------
# WorkspaceFileProvider — stdlib-only bounded scan for @-mentions
# ---------------------------------------------------------------------------
def test_workspace_provider_lists_relative_posix_paths(tmp_path) -> None:
    from magi_agent.cli.tui.file_provider import WorkspaceFileProvider

    (tmp_path / "readme.md").write_text("x", encoding="utf-8")
    sub = tmp_path / "src"
    sub.mkdir()
    (sub / "main.py").write_text("x", encoding="utf-8")

    candidates = list(WorkspaceFileProvider(str(tmp_path)).candidates(""))
    assert "readme.md" in candidates
    # Nested paths use forward slashes (repo-relative POSIX), never backslashes.
    assert "src/main.py" in candidates
    assert all("\\" not in c for c in candidates)


def test_workspace_provider_excludes_noise_dirs(tmp_path) -> None:
    from magi_agent.cli.tui.file_provider import WorkspaceFileProvider

    for noise in (".git", "node_modules", ".venv", "__pycache__", ".hidden"):
        d = tmp_path / noise
        d.mkdir()
        (d / "junk.txt").write_text("x", encoding="utf-8")
    (tmp_path / "keep.py").write_text("x", encoding="utf-8")

    candidates = list(WorkspaceFileProvider(str(tmp_path)).candidates(""))
    assert "keep.py" in candidates
    assert not any("junk.txt" in c for c in candidates)


def test_workspace_provider_respects_file_cap(tmp_path) -> None:
    from magi_agent.cli.tui.file_provider import WorkspaceFileProvider

    provider = WorkspaceFileProvider(str(tmp_path), file_cap=5)
    for i in range(20):
        (tmp_path / f"f{i}.txt").write_text("x", encoding="utf-8")
    assert len(provider.candidates("")) <= 5


def test_workspace_provider_swallows_permission_error(tmp_path) -> None:
    import os

    from magi_agent.cli.tui.file_provider import WorkspaceFileProvider

    (tmp_path / "keep.py").write_text("x", encoding="utf-8")
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "secret.txt").write_text("x", encoding="utf-8")
    os.chmod(locked, 0o000)
    try:
        candidates = list(WorkspaceFileProvider(str(tmp_path)).candidates(""))
        # Did not raise; the readable file still surfaces.
        assert "keep.py" in candidates
    finally:
        os.chmod(locked, 0o755)


def test_workspace_provider_does_not_follow_symlinks(tmp_path) -> None:
    import os

    from magi_agent.cli.tui.file_provider import WorkspaceFileProvider

    real = tmp_path / "real"
    real.mkdir()
    (real / "inside.txt").write_text("x", encoding="utf-8")
    link = tmp_path / "loop"
    try:
        os.symlink(tmp_path, link)
    except (OSError, NotImplementedError):
        return  # platform without symlink support — nothing to assert
    # Must not hang / recurse into the symlinked dir.
    candidates = list(WorkspaceFileProvider(str(tmp_path)).candidates(""))
    assert "real/inside.txt" in candidates
    assert not any(c.startswith("loop/") for c in candidates)
