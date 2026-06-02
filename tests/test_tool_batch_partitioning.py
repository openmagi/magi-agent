"""Tests for the tool batch partitioning algorithm.

Verifies that ``partition_tool_calls`` correctly groups consecutive
concurrent-safe tool calls into a single ``ToolBatch(is_concurrent=True)``
and forces workspace-mutating (or unknown) tools into exclusive batches.

Critical rule: after a tool with ``mutates_workspace=True`` the very next
tool must be in an exclusive batch even when its own ``parallel_safety``
would otherwise permit concurrency.
"""
from __future__ import annotations

import pytest

from openmagi_core_agent.tools.catalog import register_core_tool_manifests
from openmagi_core_agent.tools.concurrency import (
    ConcurrencyConfig,
    ToolBatch,
    ToolCall,
    partition_tool_calls,
)
from openmagi_core_agent.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    register_core_tool_manifests(registry)
    return registry


def _call(name: str, idx: int = 0) -> ToolCall:
    """Create a minimal ToolCall for the given tool name."""
    return ToolCall(name=name, arguments={}, tool_use_id=f"call_{name}_{idx}")


# ---------------------------------------------------------------------------
# ConcurrencyConfig tests
# ---------------------------------------------------------------------------


def test_concurrency_config_defaults() -> None:
    config = ConcurrencyConfig()
    assert config.max_concurrency == 8
    assert config.enabled is False


def test_concurrency_config_custom_values() -> None:
    config = ConcurrencyConfig(max_concurrency=4, enabled=True)
    assert config.max_concurrency == 4
    assert config.enabled is True


def test_concurrency_config_is_frozen() -> None:
    config = ConcurrencyConfig()
    with pytest.raises(Exception):
        config.max_concurrency = 1  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 1. Empty input
# ---------------------------------------------------------------------------


def test_empty_input_returns_empty_output() -> None:
    registry = _registry()
    result = partition_tool_calls((), registry)
    assert result == ()


# ---------------------------------------------------------------------------
# 2. Single concurrent-safe tool → single concurrent batch
# ---------------------------------------------------------------------------


def test_single_readonly_tool_produces_concurrent_batch() -> None:
    registry = _registry()
    call = _call("FileRead")
    result = partition_tool_calls((call,), registry)
    assert len(result) == 1
    assert result[0].is_concurrent is True
    assert result[0].calls == (call,)


# ---------------------------------------------------------------------------
# 3. Single unsafe tool → single exclusive batch
# ---------------------------------------------------------------------------


def test_single_unsafe_tool_produces_exclusive_batch() -> None:
    registry = _registry()
    call = _call("FileWrite")
    result = partition_tool_calls((call,), registry)
    assert len(result) == 1
    assert result[0].is_concurrent is False
    assert result[0].calls == (call,)


# ---------------------------------------------------------------------------
# 4. All concurrent-safe tools → one concurrent batch
# ---------------------------------------------------------------------------


def test_all_readonly_tools_produce_one_concurrent_batch() -> None:
    registry = _registry()
    calls = (
        _call("FileRead", 0),
        _call("Grep", 1),
        _call("Glob", 2),
        _call("GitDiff", 3),
    )
    result = partition_tool_calls(calls, registry)
    assert len(result) == 1
    assert result[0].is_concurrent is True
    assert result[0].calls == calls


# ---------------------------------------------------------------------------
# 5. All unsafe tools → N exclusive batches
# ---------------------------------------------------------------------------


def test_all_unsafe_tools_produce_n_exclusive_batches() -> None:
    registry = _registry()
    calls = (
        _call("FileWrite", 0),
        _call("FileEdit", 1),
        _call("Bash", 2),
    )
    result = partition_tool_calls(calls, registry)
    assert len(result) == 3
    for i, batch in enumerate(result):
        assert batch.is_concurrent is False
        assert len(batch.calls) == 1
        assert batch.calls[0] == calls[i]


# ---------------------------------------------------------------------------
# 6. Mixed: [Read, Grep, Edit, Read]
#    → [concurrent(Read, Grep), exclusive(Edit), exclusive(Read)]
#    Edit mutates workspace → next Read must be exclusive
# ---------------------------------------------------------------------------


def test_read_grep_edit_read_produces_three_batches() -> None:
    registry = _registry()
    read1 = _call("FileRead", 0)
    grep = _call("Grep", 1)
    edit = _call("FileEdit", 2)
    read2 = _call("FileRead", 3)

    result = partition_tool_calls((read1, grep, edit, read2), registry)

    assert len(result) == 3

    # First batch: concurrent reads
    assert result[0].is_concurrent is True
    assert result[0].calls == (read1, grep)

    # Second batch: exclusive edit (mutates workspace)
    assert result[1].is_concurrent is False
    assert result[1].calls == (edit,)

    # Third batch: exclusive read — forced exclusive because Edit mutated workspace
    assert result[2].is_concurrent is False
    assert result[2].calls == (read2,)


# ---------------------------------------------------------------------------
# 7. Mixed: [Read, Grep, Bash, Read, Glob]
#    → [concurrent(Read,Grep), exclusive(Bash), exclusive(Read), concurrent(Glob)]
#    Bash mutates → Read after Bash is exclusive; Glob after that is concurrent
# ---------------------------------------------------------------------------


def test_read_grep_bash_read_glob_produces_four_batches() -> None:
    registry = _registry()
    read1 = _call("FileRead", 0)
    grep = _call("Grep", 1)
    bash = _call("Bash", 2)
    read2 = _call("FileRead", 3)
    glob = _call("Glob", 4)

    result = partition_tool_calls((read1, grep, bash, read2, glob), registry)

    assert len(result) == 4

    assert result[0].is_concurrent is True
    assert result[0].calls == (read1, grep)

    assert result[1].is_concurrent is False
    assert result[1].calls == (bash,)

    # Read immediately after mutating Bash → exclusive
    assert result[2].is_concurrent is False
    assert result[2].calls == (read2,)

    # Glob after the forced-exclusive Read — Read doesn't mutate workspace,
    # so the guard is consumed and Glob is free to be concurrent
    assert result[3].is_concurrent is True
    assert result[3].calls == (glob,)


# ---------------------------------------------------------------------------
# 8. Post-mutation exclusivity: [FileEdit, FileRead]
#    → [exclusive(FileEdit), exclusive(FileRead)]
# ---------------------------------------------------------------------------


def test_post_mutation_forces_next_tool_exclusive() -> None:
    registry = _registry()
    edit = _call("FileEdit", 0)
    read = _call("FileRead", 1)

    result = partition_tool_calls((edit, read), registry)

    assert len(result) == 2
    assert result[0].is_concurrent is False
    assert result[0].calls == (edit,)
    assert result[1].is_concurrent is False
    assert result[1].calls == (read,)


# ---------------------------------------------------------------------------
# 9. Non-mutating unsafe then safe: [AskUserQuestion, FileRead, Grep]
#    → [exclusive(AskUser), concurrent(FileRead, Grep)]
#    AskUser doesn't mutate workspace → next Read is NOT forced exclusive
# ---------------------------------------------------------------------------


def test_non_mutating_unsafe_does_not_force_next_exclusive() -> None:
    registry = _registry()
    ask = _call("AskUserQuestion", 0)
    read = _call("FileRead", 1)
    grep = _call("Grep", 2)

    result = partition_tool_calls((ask, read, grep), registry)

    assert len(result) == 2

    assert result[0].is_concurrent is False
    assert result[0].calls == (ask,)

    # FileRead and Grep should be batched together — AskUser doesn't mutate
    assert result[1].is_concurrent is True
    assert result[1].calls == (read, grep)


# ---------------------------------------------------------------------------
# 10. Unknown tool → treated as exclusive
# ---------------------------------------------------------------------------


def test_unknown_tool_is_treated_as_exclusive() -> None:
    registry = _registry()
    unknown = ToolCall(name="NonExistentTool", arguments={}, tool_use_id="call_unknown")
    read = _call("FileRead", 0)
    grep = _call("Grep", 1)

    result = partition_tool_calls((unknown, read, grep), registry)

    # Unknown tool is exclusive
    assert result[0].is_concurrent is False
    assert result[0].calls == (unknown,)

    # FileRead + Grep can be concurrent (unknown tool doesn't mutate workspace
    # since manifest is None — treated the same as non-mutating unsafe)
    assert result[1].is_concurrent is True
    assert result[1].calls == (read, grep)


def test_unknown_tool_sandwiched_between_safe_tools() -> None:
    registry = _registry()
    read1 = _call("FileRead", 0)
    unknown = ToolCall(name="Ghost", arguments={}, tool_use_id="call_ghost")
    read2 = _call("Grep", 1)

    result = partition_tool_calls((read1, unknown, read2), registry)

    assert len(result) == 3

    assert result[0].is_concurrent is True
    assert result[0].calls == (read1,)

    assert result[1].is_concurrent is False
    assert result[1].calls == (unknown,)

    # Unknown tool has no manifest → mutates_workspace is treated as False
    # so the guard is NOT set; Grep can be concurrent
    assert result[2].is_concurrent is True
    assert result[2].calls == (read2,)


# ---------------------------------------------------------------------------
# 11. Deterministic: same input → same output
# ---------------------------------------------------------------------------


def test_partition_is_deterministic() -> None:
    registry = _registry()
    calls = (
        _call("FileRead", 0),
        _call("Grep", 1),
        _call("FileEdit", 2),
        _call("Glob", 3),
    )

    result1 = partition_tool_calls(calls, registry)
    result2 = partition_tool_calls(calls, registry)

    assert result1 == result2


# ---------------------------------------------------------------------------
# 12. Tool call order is preserved across batches
# ---------------------------------------------------------------------------


def test_tool_call_order_preserved() -> None:
    registry = _registry()
    calls = (
        _call("FileRead", 0),
        _call("Grep", 1),
        _call("Glob", 2),
        _call("FileWrite", 3),  # breaks concurrent streak
        _call("FileRead", 4),   # forced exclusive (post-mutation)
        _call("Grep", 5),       # concurrent again
        _call("GitDiff", 6),
    )

    result = partition_tool_calls(calls, registry)

    # Reconstruct ordered flat list from batches
    flat = [call for batch in result for call in batch.calls]
    assert tuple(flat) == calls


# ---------------------------------------------------------------------------
# Additional edge cases
# ---------------------------------------------------------------------------


def test_two_consecutive_mutating_tools() -> None:
    """FileWrite → FileEdit: both exclusive, no cross-contamination."""
    registry = _registry()
    write = _call("FileWrite", 0)
    edit = _call("FileEdit", 1)
    read = _call("FileRead", 2)

    result = partition_tool_calls((write, edit, read), registry)

    # FileWrite exclusive, FileEdit exclusive (forced by FileWrite mutation),
    # FileRead exclusive (forced by FileEdit mutation)
    assert len(result) == 3
    assert all(not b.is_concurrent for b in result)


def test_readonly_tools_at_start_then_unsafe_then_more_readonly() -> None:
    """Concurrent streak, then unsafe, then new concurrent streak."""
    registry = _registry()
    clocks = tuple(_call("Clock", i) for i in range(3))
    ask = _call("AskUserQuestion", 3)  # unsafe but no mutation
    calcs = tuple(_call("Calculation", i + 4) for i in range(2))

    all_calls = clocks + (ask,) + calcs
    result = partition_tool_calls(all_calls, registry)

    assert len(result) == 3

    assert result[0].is_concurrent is True
    assert result[0].calls == clocks

    assert result[1].is_concurrent is False
    assert result[1].calls == (ask,)

    # AskUserQuestion doesn't mutate → Calculation tools are concurrent
    assert result[2].is_concurrent is True
    assert result[2].calls == calcs


def test_toolbatch_is_frozen() -> None:
    call = _call("FileRead")
    batch = ToolBatch(is_concurrent=True, calls=(call,))
    with pytest.raises(Exception):
        batch.is_concurrent = False  # type: ignore[misc]


def test_toolcall_is_frozen() -> None:
    call = ToolCall(name="FileRead", arguments={}, tool_use_id="x")
    with pytest.raises(Exception):
        call.name = "FileWrite"  # type: ignore[misc]
