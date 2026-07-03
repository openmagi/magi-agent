"""Parity tests for the workspace/memory path-guard single homes (B4, N-05).

Follows the S-03 identity precedent. Family B (sensitive-path / glob / read
offset) moves to the tools/_workspace_path_guards leaf; Family A (memory-mode
read half) joins the existing tools/memory_mode_guard write-half home. All moves
are byte-identical, proven by object identity plus a behavior golden matrix.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.firstparty.packs.gates_policy_default import impl as pack_impl
from magi_agent.gates import gate1a_readonly_tools as gate1a
from magi_agent.gates import gate5b_full_toolhost as gate5b
from magi_agent.tools import _workspace_path_guards as guards
from magi_agent.tools import core_toolhost
from magi_agent.tools import local_readonly
from magi_agent.tools import memory_mode_guard


def test_family_b_leaf_identity() -> None:
    assert (
        gate1a._glob_pattern_matches
        is gate5b._glob_pattern_matches
        is local_readonly._glob_pattern_matches
        is guards.glob_pattern_matches
    )
    assert gate5b._read_offset is local_readonly._read_offset is guards.read_offset
    assert (
        gate1a._SENSITIVE_PATH_PART_RE
        is gate5b._SENSITIVE_PATH_PART_RE
        is guards.SENSITIVE_PATH_PART_RE
    )


def test_family_a_read_half_identity() -> None:
    assert (
        core_toolhost._PROTECTED_GLOB_SENTINELS
        is gate5b._PROTECTED_GLOB_SENTINELS
        is memory_mode_guard.PROTECTED_GLOB_SENTINELS
    )
    assert (
        core_toolhost._grep_glob_may_include_protected_memory
        is gate5b._grep_glob_may_include_protected_memory
        is memory_mode_guard.grep_glob_may_include_protected_memory
    )
    assert (
        core_toolhost._memory_mode_read_target_paths
        is gate5b._memory_read_target_paths
        is memory_mode_guard.memory_read_target_paths
    )
    assert (
        core_toolhost._MEMORY_READ_TOOL_NAMES
        is gate5b._MEMORY_READ_TOOL_NAMES
        is pack_impl._MEMORY_READ_TOOL_NAMES
        is memory_mode_guard.MEMORY_READ_TOOL_NAMES
    )
    assert (
        core_toolhost._MEMORY_WRITE_TOOL_NAMES
        is pack_impl._MEMORY_WRITE_TOOL_NAMES
        is memory_mode_guard.MEMORY_WRITE_TOOL_NAMES
    )


def test_local_readonly_sensitive_path_part_re_stays_separate() -> None:
    # Intentionally broader, deliberately not unified into the leaf.
    assert local_readonly._SENSITIVE_PATH_PART_RE is not guards.SENSITIVE_PATH_PART_RE


@pytest.mark.parametrize(
    "relative,pattern,expected",
    [
        ("MEMORY.md", "**", True),
        ("MEMORY.md", "**/*", True),
        ("memory/x.md", "**/x.md", True),
        ("a/b.md", "memory/*", False),
        ("MEMORY.md", "MEMORY.md", True),
        ("a/b", "b", False),
        ("x.md", "*.md", True),
    ],
)
def test_glob_pattern_matches_golden(relative: str, pattern: str, expected: bool) -> None:
    assert guards.glob_pattern_matches(relative, pattern) is expected


@pytest.mark.parametrize(
    "pattern,expected",
    [
        ("", "*"),
        (".", "*"),
        ("/abs", None),
        ("~x", None),
        ("a/../b", None),
        ("a//b", "a/b"),
        ("**/*", "**/*"),
    ],
)
def test_normalize_memory_glob_golden(pattern: str, expected: str | None) -> None:
    assert memory_mode_guard.normalize_memory_glob(pattern) == expected


@pytest.mark.parametrize(
    "value,expected",
    [(True, 1), (0, 1), (3, 3), ("7", 7), ("x", 1), (None, 1), (1, 1)],
)
def test_read_offset_golden(value: object, expected: int) -> None:
    assert guards.read_offset(value) == expected


@pytest.mark.parametrize(
    "path,expected",
    [
        (".ssh/x", True),
        ("a/.env", True),
        ("secret.txt", True),
        ("normal/file.md", False),
        ("a/config.yaml", True),
        ("x/../y", True),
    ],
)
def test_is_sensitive_workspace_path_golden(path: str, expected: bool) -> None:
    assert guards.is_sensitive_workspace_path(Path(path)) is expected


@pytest.mark.parametrize(
    "tool_name,arguments,expected",
    [
        ("FileRead", {"path": "MEMORY.md"}, ("MEMORY.md",)),
        ("Glob", {"pattern": "**/*"}, ("**/*",)),
        ("Grep", {"glob": "memory/*", "path": "x"}, ("memory/*", "x")),
        ("FileRead", {"file": "a", "filePath": "b"}, ("a", "b")),
        ("Other", {}, ()),
    ],
)
def test_memory_read_target_paths_golden(
    tool_name: str, arguments: dict[str, object], expected: tuple[str, ...]
) -> None:
    assert memory_mode_guard.memory_read_target_paths(tool_name, arguments) == expected


@pytest.mark.parametrize(
    "arguments,expected",
    [
        ({"glob": "**/*"}, True),
        ({"glob": "src/*.py"}, False),
        ({"path": "MEMORY.md"}, True),
        ({}, True),
    ],
)
def test_grep_glob_may_include_protected_memory_golden(
    arguments: dict[str, object], expected: bool
) -> None:
    assert memory_mode_guard.grep_glob_may_include_protected_memory(arguments) is expected


def test_filter_protected_memory_matches_golden() -> None:
    output = {"matches": [{"path": "MEMORY.md"}, {"path": "ok.md"}]}
    filtered = memory_mode_guard.filter_protected_memory_matches(output)
    assert filtered == {"matches": [{"path": "ok.md"}]}
    # No-change case returns the same object.
    clean = {"matches": [{"path": "ok.md"}]}
    assert memory_mode_guard.filter_protected_memory_matches(clean) is clean


def test_firstparty_pack_memory_helpers_come_from_single_home() -> None:
    # The memory-mode read-half helpers must resolve to memory_mode_guard, not a
    # gate5b private import. (The unrelated permission-preflight policy still
    # imports gate5b legacy-manifest privates; that is out of B4 scope.)
    assert (
        pack_impl._filter_protected_memory_matches
        is memory_mode_guard.filter_protected_memory_matches
    )
    assert (
        pack_impl._grep_glob_may_include_protected_memory
        is memory_mode_guard.grep_glob_may_include_protected_memory
    )
    assert (
        pack_impl._memory_read_target_paths
        is memory_mode_guard.memory_read_target_paths
    )

    import inspect

    memory_mode_src = inspect.getsource(pack_impl.memory_mode_policy)
    assert "gate5b_full_toolhost import" not in memory_mode_src
