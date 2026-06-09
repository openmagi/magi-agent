from __future__ import annotations

from magi_agent.runtime.session_identity import MemoryMode
from magi_agent.tools.memory_mode_guard import (
    command_may_write_protected_memory,
    command_mentions_protected_memory,
    is_incognito_memory_mode,
    is_long_term_memory_write_disabled,
    is_protected_memory_path,
    normalize_memory_mode,
    protected_memory_error,
)


def test_normalize_memory_mode_maps_known_values() -> None:
    assert normalize_memory_mode("read_only") == "read_only"
    assert normalize_memory_mode("incognito") == "incognito"
    assert normalize_memory_mode("normal") == "normal"
    assert normalize_memory_mode(MemoryMode.READ_ONLY) == "read_only"
    assert normalize_memory_mode(MemoryMode.INCOGNITO) == "incognito"
    assert normalize_memory_mode(None) == "normal"
    assert normalize_memory_mode("bogus") == "normal"


def test_memory_mode_truth_tables() -> None:
    assert is_incognito_memory_mode("incognito") is True
    assert is_incognito_memory_mode(MemoryMode.INCOGNITO) is True
    assert is_incognito_memory_mode("read_only") is False
    assert is_incognito_memory_mode("normal") is False

    assert is_long_term_memory_write_disabled("incognito") is True
    assert is_long_term_memory_write_disabled("read_only") is True
    assert is_long_term_memory_write_disabled(MemoryMode.READ_ONLY) is True
    assert is_long_term_memory_write_disabled("normal") is False
    assert is_long_term_memory_write_disabled(None) is False


def test_is_protected_memory_path_true_cases() -> None:
    assert is_protected_memory_path("MEMORY.md") is True
    assert is_protected_memory_path("SCRATCHPAD.md") is True
    assert is_protected_memory_path("WORKING.md") is True
    assert is_protected_memory_path("TASK-QUEUE.md") is True
    assert is_protected_memory_path("memory") is True
    assert is_protected_memory_path("memory/foo.md") is True
    assert is_protected_memory_path("./memory/x") is True
    assert is_protected_memory_path("/memory/x") is True
    assert is_protected_memory_path("./MEMORY.md") is True
    assert is_protected_memory_path("memory\\daily\\2026.md") is True


def test_is_protected_memory_path_false_cases() -> None:
    assert is_protected_memory_path("src/app.py") is False
    assert is_protected_memory_path("") is False
    assert is_protected_memory_path(".") is False
    assert is_protected_memory_path(None) is False
    # Only TOP-LEVEL protected files count: nested copies are not protected.
    assert is_protected_memory_path("notes/MEMORY.md") is False
    assert is_protected_memory_path("docs/memory.md") is False
    assert is_protected_memory_path("memoryless.md") is False


def test_command_mentions_protected_memory() -> None:
    assert command_mentions_protected_memory("cat memory/x") is True
    assert command_mentions_protected_memory("ls memory") is True
    assert command_mentions_protected_memory("echo hi >> MEMORY.md") is True
    assert command_mentions_protected_memory("cat SCRATCHPAD.md") is True
    assert command_mentions_protected_memory("ls") is False
    assert command_mentions_protected_memory("cat src/app.py") is False
    assert command_mentions_protected_memory("") is False
    assert command_mentions_protected_memory(None) is False


def test_command_may_write_protected_memory() -> None:
    # Mentions only — no mutation/redirection.
    assert command_may_write_protected_memory("cat memory/x") is False
    assert command_may_write_protected_memory("ls memory") is False
    # Mutating binaries against memory.
    assert command_may_write_protected_memory("rm -rf memory/") is True
    assert command_may_write_protected_memory("mv memory/a memory/b") is True
    assert command_may_write_protected_memory("touch MEMORY.md") is True
    # Redirection into a protected file.
    assert command_may_write_protected_memory("echo hi >> MEMORY.md") is True
    assert command_may_write_protected_memory("echo hi > memory/x") is True
    # Neither memory nor write.
    assert command_may_write_protected_memory("ls") is False
    assert command_may_write_protected_memory("rm -rf build/") is False


def test_protected_memory_error() -> None:
    assert protected_memory_error() == "memory mode blocks access to memory state"
    assert (
        protected_memory_error("MEMORY.md")
        == "memory mode blocks access to MEMORY.md"
    )
